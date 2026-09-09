"""A scripted backend: replays a canned conversation.

Exists so the pipeline can be exercised end to end on any OS — CI, a Mac, this
container — and so `wxrecall demo` shows real recall detection without needing
WeChat installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence

from ..models import Message, MsgType

CHAT = "演示群"

# Each frame is one poll of the window. Frame 4 is the interesting one: the
# bubble from frame 3 has been swapped for a recall notice.
DEMO_FRAMES: list[list[tuple[str, str, str]]] = [
    [
        ("小明", "明早十点开会", "text"),
        ("小红", "收到", "text"),
    ],
    [
        ("小明", "明早十点开会", "text"),
        ("小红", "收到", "text"),
        ("小明", "对了，密码是 hunter2", "text"),
    ],
    [
        ("小明", "明早十点开会", "text"),
        ("小红", "收到", "text"),
        ("system", "小明撤回了一条消息", "system"),
    ],
    [
        ("小明", "明早十点开会", "text"),
        ("小红", "收到", "text"),
        ("system", "小明撤回了一条消息", "system"),
        ("小明", "发错了，当我没说", "text"),
        ("小红", "[图片]", "image"),
    ],
    [
        ("小明", "明早十点开会", "text"),
        ("小红", "收到", "text"),
        ("system", "小明撤回了一条消息", "system"),
        ("小明", "发错了，当我没说", "text"),
        ("system", "小红撤回了一条消息", "system"),
    ],
]


class ReplayBackend:
    def __init__(self, frames: Optional[list] = None, chat: str = CHAT, script: Optional[str] = None) -> None:
        if script:
            frames = json.loads(Path(script).read_text(encoding="utf-8"))
        self.chat = chat
        self._frames = frames if frames is not None else DEMO_FRAMES
        self._i = 0

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self._frames)

    def list_chats(self) -> Sequence[str]:
        return [self.chat]

    def current_chat(self) -> Optional[str]:
        return self.chat

    def snapshot(self, chat: str) -> Sequence[Message]:
        # Hold on the final frame so long-running callers see a settled window
        # instead of the conversation resetting.
        idx = min(self._i, len(self._frames) - 1)
        self._i += 1
        return [
            Message(chat=chat, sender=s, content=c, msg_type=MsgType(t))
            for s, c, t in self._frames[idx]
        ]

    def capture(self, chat: str) -> Optional[bytes]:
        return None

    def close(self) -> None:
        pass
