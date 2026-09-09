"""Snapshot diffing: turn a sequence of window reads into message events.

The UI gives us no message IDs — every poll is just an ordered list of bubbles
currently rendered. So state is inferred by aligning consecutive snapshots:

  * bubbles that appear at the end  -> new messages
  * bubbles that appear at the head -> scrollback (history loaded above)
  * bubbles that vanish at the head -> the client trimmed the viewport
  * bubbles that vanish elsewhere   -> withdrawn, if a recall notice replaced them

That last rule is the whole point of the tool. WeChat swaps the bubble for a
grey "X withdrew a message" line in the same slot, so a deletion paired with a
freshly appeared recall notice is a recall; an unexplained deletion is reported
separately as `vanished` rather than silently upgraded to a recall.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

from .models import Message, MsgType, is_recall_notice, utcnow


@dataclass
class Recall:
    message: Message
    notice: Optional[Message]
    detected_at: str = field(default_factory=utcnow)


@dataclass
class Changes:
    new: list[Message] = field(default_factory=list)
    recalled: list[Recall] = field(default_factory=list)
    vanished: list[Message] = field(default_factory=list)
    scrollback: list[Message] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.new or self.recalled or self.vanished or self.scrollback)


def _keys(messages: Sequence[Message]) -> list[tuple[str, str, str]]:
    return [m.key for m in messages]


def diff_snapshots(prev: Sequence[Message], curr: Sequence[Message]) -> Changes:
    """Classify what changed between two reads of the same chat window."""
    changes = Changes()
    if not prev:
        changes.new.extend(curr)
        return changes

    matcher = SequenceMatcher(a=_keys(prev), b=_keys(curr), autojunk=False)
    opcodes = matcher.get_opcodes()

    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            continue

        gone = list(prev[i1:i2])
        arrived = list(curr[j1:j2])
        at_head = idx == 0
        at_tail = idx == len(opcodes) - 1

        notices = [m for m in arrived if is_recall_notice(m.content)]
        others = [m for m in arrived if not is_recall_notice(m.content)]

        # A viewport trim: the oldest bubbles fell off the top and nothing
        # replaced them. Not a recall, and not worth an event.
        if gone and at_head and not notices:
            pass
        elif gone:
            for offset, msg in enumerate(gone):
                notice = notices[offset] if offset < len(notices) else (notices[-1] if notices else None)
                if notice is not None:
                    changes.recalled.append(Recall(message=msg, notice=notice))
                else:
                    changes.vanished.append(msg)

        # Bubbles inserted above everything we knew about are history being
        # paged in by scrolling up, not traffic arriving.
        if at_head and not at_tail and not gone:
            changes.scrollback.extend(others)
        else:
            changes.new.extend(others)

        # A recall notice with nothing deleted alongside it still matters: it
        # means a message was withdrawn before we ever managed to read it.
        unpaired = notices[len(gone):] if gone else notices
        changes.new.extend(unpaired)

    return changes


class ChatWatcher:
    """Per-chat state machine driven by successive snapshots.

    Note what `previous` is and is not: it holds the last *snapshot* — the
    bubbles rendered a moment ago — never the stored log. Diffing a snapshot
    against a full archive would read every message that has scrolled out of
    the viewport as a disappearance. Stored history is only ever passed to
    `bootstrap` as `known`, where it serves to suppress re-inserting a backlog.
    """

    def __init__(self, chat: str) -> None:
        self.chat = chat
        self._prev: list[Message] = []
        self._bootstrapped = False

    @property
    def previous(self) -> list[Message]:
        return list(self._prev)

    @property
    def bootstrapped(self) -> bool:
        return self._bootstrapped

    def bootstrap(self, snapshot: Sequence[Message], known: Iterable[Message] = ()) -> Changes:
        """Seed from the first read of this chat after start-up.

        Everything already on screen was sent before we were watching, so it is
        reported as scrollback rather than as new traffic — and the part of it
        already sitting in `known` is dropped, so a restart does not re-insert
        the visible backlog.
        """
        self._prev = list(snapshot)
        self._bootstrapped = True
        return Changes(scrollback=dedupe_against(list(known), list(snapshot)))

    def feed(self, snapshot: Sequence[Message], known: Iterable[Message] = ()) -> Changes:
        if not self._bootstrapped:
            return self.bootstrap(snapshot, known)
        changes = diff_snapshots(self._prev, snapshot)
        self._prev = list(snapshot)
        return changes


def dedupe_against(known: Sequence[Message], snapshot: Sequence[Message]) -> list[Message]:
    """Return the tail of `snapshot` that is not already covered by `known`.

    Used on restart so a fresh process does not re-insert the backlog that is
    still sitting in the database from the previous run.
    """
    if not known:
        return list(snapshot)
    matcher = SequenceMatcher(a=_keys(known), b=_keys(snapshot), autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    if not blocks:
        return list(snapshot)
    last = blocks[-1]
    return list(snapshot[last.b + last.size:])


__all__ = [
    "Changes",
    "ChatWatcher",
    "Recall",
    "dedupe_against",
    "diff_snapshots",
    "MsgType",
]
