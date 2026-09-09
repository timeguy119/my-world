"""Core data types shared by backends, the watcher and the store."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MsgType(str, Enum):
    """What kind of bubble this is.

    The UI layer can only tell us so much: a picture bubble has no text, so we
    record the placeholder the client renders plus (optionally) a screenshot.
    """

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    FILE = "file"
    LINK = "link"
    STICKER = "sticker"
    SYSTEM = "system"
    UNKNOWN = "unknown"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Message:
    """One rendered chat bubble.

    `key` is what alignment is done on. It deliberately excludes timestamps:
    the same snapshot re-read a second later must produce an identical key or
    every poll would look like the whole history was replaced.
    """

    chat: str
    sender: str
    content: str
    msg_type: MsgType = MsgType.TEXT
    sent_at: Optional[str] = None
    seen_at: str = field(default_factory=utcnow)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.sender, self.msg_type.value, self.content)

    @property
    def fingerprint(self) -> str:
        raw = "\x1f".join([self.chat, self.sender, self.msg_type.value, self.content])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# Patterns WeChat uses when a message is withdrawn. Covers the Simplified /
# Traditional / English clients, "you" and "someone else" variants, and the
# group form where the display name is wrapped in corner brackets.
RECALL_PATTERNS = [
    re.compile(r"^你撤回了一条消息"),
    re.compile(r"^您撤回了一則訊息"),
    re.compile(r"^「?(?P<who>.+?)」?\s*撤回了一条消息"),
    re.compile(r"^「?(?P<who>.+?)」?\s*撤回了一則訊息"),
    re.compile(r"^You recalled a message"),
    re.compile(r"^\"?(?P<who>.+?)\"?\s+recalled a message"),
]


def parse_recall_notice(text: str) -> Optional[str]:
    """Return the recaller's display name if `text` is a recall notice.

    Returns "" for the first-person forms ("you recalled a message"), which are
    still a positive match — callers must test against None, not truthiness.
    """
    stripped = text.strip()
    for pattern in RECALL_PATTERNS:
        m = pattern.match(stripped)
        if m:
            return (m.groupdict().get("who") or "").strip()
    return None


def is_recall_notice(text: str) -> bool:
    return parse_recall_notice(text) is not None
