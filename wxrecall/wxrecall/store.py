"""Local SQLite archive. Nothing here ever leaves the machine."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .models import Message, MsgType, utcnow
from .watcher import Changes, Recall

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat         TEXT NOT NULL,
    sender       TEXT NOT NULL,
    msg_type     TEXT NOT NULL,
    content      TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    sent_at      TEXT,
    seen_at      TEXT NOT NULL,
    recalled     INTEGER NOT NULL DEFAULT 0,
    recalled_at  TEXT,
    recalled_by  TEXT,
    vanished     INTEGER NOT NULL DEFAULT 0,
    snapshot     TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat, id);
CREATE INDEX IF NOT EXISTS idx_messages_recalled ON messages(recalled) WHERE recalled = 1;
CREATE INDEX IF NOT EXISTS idx_messages_fingerprint ON messages(fingerprint);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    note       TEXT
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Chat logs are worth an fsync per commit; losing the last few seconds
        # of an archive on a power cut defeats the point of running it.
        self.conn.execute("PRAGMA synchronous=FULL")
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- writes

    def add(self, message: Message, *, snapshot: Optional[str] = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO messages
               (chat, sender, msg_type, content, fingerprint, sent_at, seen_at, snapshot)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                message.chat,
                message.sender,
                message.msg_type.value,
                message.content,
                message.fingerprint,
                message.sent_at,
                message.seen_at,
                snapshot,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_many(self, messages: Iterable[Message], *, snapshot: Optional[str] = None) -> list[int]:
        return [self.add(m, snapshot=snapshot) for m in messages]

    def mark_recalled(self, recall: Recall) -> Optional[int]:
        """Flag the stored copy of a withdrawn message.

        Matches the most recent row with the same fingerprint that is not
        already flagged, so a phrase sent and recalled twice marks two rows.
        """
        row = self.conn.execute(
            """SELECT id FROM messages
               WHERE fingerprint = ? AND recalled = 0
               ORDER BY id DESC LIMIT 1""",
            (recall.message.fingerprint,),
        ).fetchone()
        if row is None:
            # Never saw it stored (started mid-conversation): keep it anyway,
            # already flagged, so the recall is not lost.
            rowid = self.add(recall.message)
        else:
            rowid = int(row["id"])
        by = recall.notice.content if recall.notice else None
        self.conn.execute(
            "UPDATE messages SET recalled = 1, recalled_at = ?, recalled_by = ? WHERE id = ?",
            (recall.detected_at, by, rowid),
        )
        self.conn.commit()
        return rowid

    def mark_vanished(self, message: Message) -> None:
        row = self.conn.execute(
            "SELECT id FROM messages WHERE fingerprint = ? ORDER BY id DESC LIMIT 1",
            (message.fingerprint,),
        ).fetchone()
        if row is not None:
            self.conn.execute("UPDATE messages SET vanished = 1 WHERE id = ?", (int(row["id"]),))
            self.conn.commit()

    def apply(self, changes: Changes, *, snapshot: Optional[str] = None, keep_scrollback: bool = True) -> dict:
        """Persist one poll's worth of changes. Returns a small counters dict."""
        counts = {"new": 0, "recalled": 0, "vanished": 0, "scrollback": 0}
        for m in changes.new:
            self.add(m, snapshot=snapshot)
            counts["new"] += 1
        if keep_scrollback:
            for m in changes.scrollback:
                self.add(m, snapshot=snapshot)
                counts["scrollback"] += 1
        for r in changes.recalled:
            self.mark_recalled(r)
            counts["recalled"] += 1
        for m in changes.vanished:
            self.mark_vanished(m)
            counts["vanished"] += 1
        return counts

    def start_run(self, note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, note) VALUES (?, ?)", (utcnow(), note)
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def stop_run(self, run_id: int) -> None:
        self.conn.execute("UPDATE runs SET stopped_at = ? WHERE id = ?", (utcnow(), run_id))
        self.conn.commit()

    # ----------------------------------------------------------------- reads

    def _to_message(self, row: sqlite3.Row) -> Message:
        return Message(
            chat=row["chat"],
            sender=row["sender"],
            content=row["content"],
            msg_type=MsgType(row["msg_type"]),
            sent_at=row["sent_at"],
            seen_at=row["seen_at"],
        )

    def recent(self, chat: str, limit: int = 200) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE chat = ? ORDER BY id DESC LIMIT ?", (chat, limit)
        ).fetchall()
        return [self._to_message(r) for r in reversed(rows)]

    def query(
        self,
        *,
        chat: Optional[str] = None,
        recalled_only: bool = False,
        search: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM messages WHERE 1=1"
        args: list = []
        if chat:
            sql += " AND chat = ?"
            args.append(chat)
        if recalled_only:
            sql += " AND recalled = 1"
        if search:
            sql += " AND content LIKE ?"
            args.append(f"%{search}%")
        if since:
            sql += " AND seen_at >= ?"
            args.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return list(self.conn.execute(sql, args).fetchall())

    def chats(self) -> list[tuple[str, int, int]]:
        rows = self.conn.execute(
            """SELECT chat, COUNT(*) AS n, SUM(recalled) AS r
               FROM messages GROUP BY chat ORDER BY n DESC"""
        ).fetchall()
        return [(r["chat"], int(r["n"]), int(r["r"] or 0)) for r in rows]

    def stats(self) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(recalled) AS recalled,
                      SUM(vanished) AS vanished,
                      COUNT(DISTINCT chat) AS chats
               FROM messages"""
        ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "recalled": int(row["recalled"] or 0),
            "vanished": int(row["vanished"] or 0),
            "chats": int(row["chats"] or 0),
        }


__all__ = ["Store"]
