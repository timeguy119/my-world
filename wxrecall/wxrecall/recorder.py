"""The polling loop that ties a backend to the store."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .backends import Backend
from .models import Message, utcnow
from .store import Store
from .watcher import ChatWatcher, Changes

log = logging.getLogger("wxrecall")


@dataclass
class RecorderConfig:
    interval: float = 1.0
    chats: Optional[Sequence[str]] = None       # None = whatever chat is open
    keep_scrollback: bool = True
    screenshots_dir: Optional[Path] = None
    history_window: int = 200                   # rows consulted for restart dedupe


class Recorder:
    def __init__(self, backend: Backend, store: Store, config: Optional[RecorderConfig] = None) -> None:
        self.backend = backend
        self.store = store
        self.config = config or RecorderConfig()
        self._watchers: dict[str, ChatWatcher] = {}
        self._history: dict[str, list[Message]] = {}
        # One frame of lag: the useful screenshot for a recalled image is the
        # one taken *before* the bubble disappeared.
        self._last_capture: dict[str, bytes] = {}
        self.counts = {"new": 0, "recalled": 0, "vanished": 0, "scrollback": 0}

    def _watcher_for(self, chat: str) -> ChatWatcher:
        watcher = self._watchers.get(chat)
        if watcher is None:
            watcher = ChatWatcher(chat)
            self._watchers[chat] = watcher
            self._history[chat] = self.store.recent(chat, self.config.history_window)
        return watcher

    def _wanted(self, chat: Optional[str]) -> bool:
        if not chat:
            return False
        if not self.config.chats:
            return True
        return chat in self.config.chats

    def poll_once(self) -> tuple[Optional[str], Changes]:
        """Read the open chat once and persist whatever changed."""
        chat = self.backend.current_chat()
        if not self._wanted(chat):
            return chat, Changes()

        snapshot = list(self.backend.snapshot(chat))
        watcher = self._watcher_for(chat)
        # On the first sight of a chat the stored log is handed over so the
        # backlog already on screen is not inserted a second time; afterwards
        # it is irrelevant and the snapshot-to-snapshot diff takes over.
        changes = watcher.feed(snapshot, known=self._history.pop(chat, ()))

        applied = self.store.apply(
            changes, keep_scrollback=self.config.keep_scrollback
        )
        for k, v in applied.items():
            self.counts[k] += v

        if changes.recalled:
            self._save_capture(chat, changes)
        if self.config.screenshots_dir:
            shot = self.backend.capture(chat)
            if shot:
                self._last_capture[chat] = shot

        return chat, changes

    def _save_capture(self, chat: str, changes: Changes) -> None:
        blob = self._last_capture.get(chat)
        if not blob or not self.config.screenshots_dir:
            return
        directory = Path(self.config.screenshots_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().replace(":", "-")
        safe = "".join(c for c in chat if c.isalnum() or c in "-_") or "chat"
        path = directory / f"{safe}_{stamp}.png"
        path.write_bytes(blob)
        log.info("saved pre-recall screenshot: %s", path)

    def run(
        self,
        *,
        max_polls: Optional[int] = None,
        on_change: Optional[Callable[[str, Changes], None]] = None,
        stop: Optional[Callable[[], bool]] = None,
    ) -> dict:
        run_id = self.store.start_run(note=f"interval={self.config.interval}s")
        polls = 0
        try:
            while True:
                if stop and stop():
                    break
                if max_polls is not None and polls >= max_polls:
                    break
                try:
                    chat, changes = self.poll_once()
                except Exception:
                    # A transient UI read (window minimised, chat switching)
                    # must not take the recorder down; the next poll retries.
                    log.exception("poll failed; continuing")
                    chat, changes = None, Changes()
                polls += 1
                if changes and chat and on_change:
                    on_change(chat, changes)
                if max_polls is None or polls < max_polls:
                    time.sleep(self.config.interval)
        finally:
            self.store.stop_run(run_id)
        return dict(self.counts)
