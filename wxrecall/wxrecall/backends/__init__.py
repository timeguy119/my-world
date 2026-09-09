"""Sources of chat snapshots.

A backend's whole job is: given a chat title, return the bubbles currently
rendered in that window. Everything else — diffing, recall detection, storage —
is backend-agnostic, which is what lets the core be tested without Windows.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from ..models import Message


@runtime_checkable
class Backend(Protocol):
    def list_chats(self) -> Sequence[str]:
        """Chat titles currently reachable, best effort."""

    def current_chat(self) -> Optional[str]:
        """Title of the chat currently open in the window, if any."""

    def snapshot(self, chat: str) -> Sequence[Message]:
        """Bubbles rendered in `chat` right now, oldest first."""

    def capture(self, chat: str) -> Optional[bytes]:
        """PNG of the message area, or None if this backend cannot screenshot."""

    def close(self) -> None:
        ...


def load(name: str, **kwargs) -> Backend:
    if name == "uia":
        from .uia import UIABackend

        return UIABackend(**kwargs)
    if name == "replay":
        from .replay import ReplayBackend

        return ReplayBackend(**kwargs)
    raise ValueError(f"unknown backend: {name!r} (expected 'uia' or 'replay')")


__all__ = ["Backend", "load"]
