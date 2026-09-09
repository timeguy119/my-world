"""Windows UI Automation backend.

Reads the WeChat desktop client the same way a screen reader does: through the
public accessibility tree. It does not attach a debugger, inject a DLL, patch
memory or touch the encrypted local database — it only sees what is already
drawn on your screen.

The trade-off is that the control tree is not a stable API. Names and class
names shift between client versions, so the selectors live in PROFILES and can
be overridden from the CLI. Run `wxrecall probe` on a machine where extraction
misbehaves: it dumps the live tree so a profile can be adjusted.

Requires: pip install uiautomation  (and pillow for --screenshots)
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..models import Message, MsgType

try:  # pragma: no cover - import guard, exercised only on Windows
    import uiautomation as auto
except ImportError:  # pragma: no cover
    auto = None


@dataclass
class Profile:
    """Selectors for one family of WeChat desktop builds."""

    name: str
    window_classes: tuple[str, ...]
    window_names: tuple[str, ...] = ("微信", "WeChat", "Weixin")
    message_list_names: tuple[str, ...] = ("消息", "訊息", "Message")
    chat_title_names: tuple[str, ...] = ("聊天信息", "Chat Info")


PROFILES: dict[str, Profile] = {
    # WeChat for Windows 3.x — the long-lived MFC client.
    "win3": Profile(name="win3", window_classes=("WeChatMainWndForPC",)),
    # WeChat for Windows 4.x — Qt-based rewrite; class name varies by build,
    # so several known spellings are tried.
    "win4": Profile(
        name="win4",
        window_classes=("mmui::MainWindow", "Qt51514QWindowIcon", "Qt5152QWindowIcon"),
    ),
}

# Placeholder text the client renders in place of non-text content.
TYPE_HINTS: list[tuple[re.Pattern, MsgType]] = [
    (re.compile(r"^\[(图片|圖片|Photo|Image)\]$"), MsgType.IMAGE),
    (re.compile(r"^\[(视频|影片|Video)\]$"), MsgType.VIDEO),
    (re.compile(r"^\[(语音|語音|Voice|Audio)\]"), MsgType.VOICE),
    (re.compile(r"^\[(文件|檔案|File)\]"), MsgType.FILE),
    (re.compile(r"^\[(链接|連結|Link)\]"), MsgType.LINK),
    (re.compile(r"^\[(动画表情|動畫表情|Animated Sticker|Sticker)\]$"), MsgType.STICKER),
]

# Rows that are chrome rather than conversation: date separators, the unread
# divider, the "load more" affordance.
CHROME_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^(查看更多消息|查看更多訊息|View more messages)$"),
    re.compile(r"^(以下为新消息|以下為新訊息)$"),
    re.compile(r"^\d{1,2}:\d{2}$"),
    re.compile(r"^(昨天|前天|今天|Yesterday|Today)\s*\d{0,2}:?\d{0,2}$"),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日"),
    re.compile(r"^\d{1,2}月\d{1,2}日"),
]

SYSTEM_PATTERNS = [
    re.compile(r"撤回了一条消息"),
    re.compile(r"撤回了一則訊息"),
    re.compile(r"recalled a message"),
    re.compile(r"^(你|您)?已添加.+为好友"),
    re.compile(r"加入了群聊"),
    re.compile(r"^消息已发出，但被对方拒收了"),
]


def classify(text: str) -> MsgType:
    for pattern, mtype in TYPE_HINTS:
        if pattern.match(text):
            return mtype
    for pattern in SYSTEM_PATTERNS:
        if pattern.search(text):
            return MsgType.SYSTEM
    return MsgType.TEXT


def is_chrome(text: str) -> bool:
    return any(p.match(text) for p in CHROME_PATTERNS)


class BackendUnavailable(RuntimeError):
    pass


class UIABackend:
    def __init__(
        self,
        profile: str = "auto",
        search_timeout: float = 2.0,
        screenshots: bool = False,
    ) -> None:
        if auto is None:
            raise BackendUnavailable(
                "the 'uiautomation' package is required for the UIA backend "
                "(Windows only): pip install uiautomation"
            )
        self.search_timeout = search_timeout
        self.screenshots = screenshots
        self._profile: Optional[Profile] = None if profile == "auto" else PROFILES[profile]
        self._window = None

    # ------------------------------------------------------------- plumbing

    def _find_window(self):
        if self._window is not None and self._window.Exists(0.2):
            return self._window
        candidates = [self._profile] if self._profile else list(PROFILES.values())
        for prof in candidates:
            for cls in prof.window_classes:
                win = auto.WindowControl(searchDepth=1, ClassName=cls)
                if win.Exists(self.search_timeout):
                    self._profile, self._window = prof, win
                    return win
        # Fall back to matching on the window title, which survives a class
        # name we have never seen before.
        for prof in candidates:
            for nm in prof.window_names:
                win = auto.WindowControl(searchDepth=1, Name=nm)
                if win.Exists(self.search_timeout):
                    self._profile, self._window = prof, win
                    return win
        raise BackendUnavailable(
            "WeChat window not found. Make sure the desktop client is running "
            "and not minimised to the tray, then retry. If it is running, use "
            "`wxrecall probe` to inspect the control tree."
        )

    def _find_message_list(self, window):
        prof = self._profile or PROFILES["win3"]
        for nm in prof.message_list_names:
            lst = window.ListControl(Name=nm)
            if lst.Exists(0.5):
                return lst
        # Unnamed in some builds: take the deepest list with the most children,
        # which is reliably the message area rather than the session sidebar.
        lists = [c for c in _walk(window, max_depth=12) if c.ControlTypeName == "ListControl"]
        if not lists:
            raise BackendUnavailable("could not locate the message list control")
        return max(lists, key=lambda c: len(c.GetChildren()))

    # ------------------------------------------------------------- protocol

    def current_chat(self) -> Optional[str]:
        window = self._find_window()
        # The chat title sits in an edit/text control above the message list;
        # the tab bar exposes it as the selected item's name in most builds.
        for ctrl in _walk(window, max_depth=10):
            if ctrl.ControlTypeName in ("EditControl", "TextControl"):
                name = (ctrl.Name or "").strip()
                if name and not is_chrome(name) and len(name) < 64:
                    return name
        return None

    def list_chats(self) -> Sequence[str]:
        window = self._find_window()
        names = []
        for ctrl in _walk(window, max_depth=8):
            if ctrl.ControlTypeName == "ListItemControl":
                nm = (ctrl.Name or "").strip()
                if nm:
                    names.append(nm)
        return names

    def snapshot(self, chat: str) -> Sequence[Message]:
        window = self._find_window()
        msg_list = self._find_message_list(window)
        out: list[Message] = []
        for item in msg_list.GetChildren():
            parsed = self._parse_item(item, chat)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item, chat: str) -> Optional[Message]:
        text = (item.Name or "").strip()
        if not text or is_chrome(text):
            return None
        mtype = classify(text)
        sender = "system" if mtype is MsgType.SYSTEM else self._sender_of(item)
        return Message(chat=chat, sender=sender, content=text, msg_type=mtype)

    def _sender_of(self, item) -> str:
        """Best-effort display name for a bubble.

        The avatar is a button whose Name is the sender's nickname; in group
        chats an extra text control repeats it above the bubble. Neither is
        guaranteed, so an unknown sender is recorded as such rather than
        guessed — a wrong attribution in an archive is worse than a blank one.
        """
        try:
            for child in _walk(item, max_depth=4):
                if child.ControlTypeName == "ButtonControl":
                    nm = (child.Name or "").strip()
                    if nm and nm not in ("发送", "Send"):
                        return nm
        except Exception:
            pass
        return "unknown"

    def capture(self, chat: str) -> Optional[bytes]:
        if not self.screenshots:
            return None
        try:
            from PIL import ImageGrab
        except ImportError:
            return None
        try:
            window = self._find_window()
            rect = self._find_message_list(window).BoundingRectangle
            img = ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def close(self) -> None:
        self._window = None

    # ------------------------------------------------------------ debugging

    def probe(self, max_depth: int = 8) -> str:
        window = self._find_window()
        lines = [f"profile: {self._profile.name if self._profile else '?'}"]
        for ctrl, depth in _walk(window, max_depth=max_depth, with_depth=True):
            name = (ctrl.Name or "")[:60].replace("\n", "\\n")
            lines.append(f"{'  ' * depth}{ctrl.ControlTypeName} name={name!r}")
        return "\n".join(lines)


def _walk(root, max_depth: int = 8, with_depth: bool = False, _depth: int = 0):
    """Depth-first walk of the control tree, defensive against transient nodes."""
    try:
        children = root.GetChildren()
    except Exception:
        return
    for child in children:
        yield (child, _depth) if with_depth else child
        if _depth + 1 < max_depth:
            yield from _walk(child, max_depth, with_depth, _depth + 1)
