"""Render the archive to something readable."""

from __future__ import annotations

import html
import json
import sqlite3
from typing import Iterable, Sequence

from .store import Store

CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
        --bubble:#f3f4f6; --recall:#fef2f2; --recall-bd:#dc2626; --line:#e5e7eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --fg:#e6e6e6; --muted:#9ca3af;
          --bubble:#1b1f27; --recall:#2a1414; --recall-bd:#f87171; --line:#242832; }
}
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
.wrap { max-width:820px; margin:0 auto; padding:32px 20px 64px; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:24px; }
.msg { margin:14px 0; padding:10px 14px; background:var(--bubble);
       border-radius:10px; border-left:3px solid transparent; }
.msg.recalled { background:var(--recall); border-left-color:var(--recall-bd); }
.msg.system { background:transparent; text-align:center; color:var(--muted); font-size:13px; }
.meta { font-size:12px; color:var(--muted); margin-bottom:3px;
        display:flex; gap:8px; flex-wrap:wrap; align-items:baseline; }
.sender { font-weight:600; color:var(--fg); }
.tag { font-size:11px; padding:1px 7px; border-radius:99px;
       border:1px solid var(--recall-bd); color:var(--recall-bd); }
.body { white-space:pre-wrap; word-break:break-word; }
hr { border:0; border-top:1px solid var(--line); margin:28px 0; }
"""


def _rows(store: Store, chat: str | None, recalled_only: bool, limit: int) -> list[sqlite3.Row]:
    rows = store.query(chat=chat, recalled_only=recalled_only, limit=limit)
    return list(reversed(rows))  # oldest first for reading


def to_html(store: Store, *, chat: str | None = None, recalled_only: bool = False, limit: int = 5000) -> str:
    rows = _rows(store, chat, recalled_only, limit)
    title = chat or "全部会话"
    parts = [
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{html.escape(title)} · wxrecall</title><style>{CSS}</style></head><body><div class='wrap'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<div class='sub'>{len(rows)} 条记录"
        f"{' · 仅显示撤回' if recalled_only else ''}</div>",
    ]
    for r in rows:
        classes = ["msg"]
        if r["recalled"]:
            classes.append("recalled")
        if r["msg_type"] == "system":
            classes.append("system")
        meta = [f"<span class='sender'>{html.escape(r['sender'])}</span>",
                f"<span>{html.escape(r['seen_at'])}</span>"]
        if r["msg_type"] not in ("text", "system"):
            meta.append(f"<span>{html.escape(r['msg_type'])}</span>")
        if r["recalled"]:
            meta.append("<span class='tag'>已撤回</span>")
        if r["vanished"] and not r["recalled"]:
            meta.append("<span class='tag'>消失</span>")
        parts.append(
            f"<div class='{' '.join(classes)}'><div class='meta'>{''.join(meta)}</div>"
            f"<div class='body'>{html.escape(r['content'])}</div></div>"
        )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def to_markdown(store: Store, *, chat: str | None = None, recalled_only: bool = False, limit: int = 5000) -> str:
    rows = _rows(store, chat, recalled_only, limit)
    lines = [f"# {chat or '全部会话'}", "", f"共 {len(rows)} 条", ""]
    for r in rows:
        flag = " **[已撤回]**" if r["recalled"] else ""
        lines.append(f"- `{r['seen_at']}` **{r['sender']}**{flag}: {r['content']}")
    return "\n".join(lines) + "\n"


def to_jsonl(store: Store, *, chat: str | None = None, recalled_only: bool = False, limit: int = 100000) -> str:
    rows = _rows(store, chat, recalled_only, limit)
    return "\n".join(json.dumps(dict(r), ensure_ascii=False) for r in rows) + "\n"


RENDERERS = {"html": to_html, "md": to_markdown, "markdown": to_markdown, "jsonl": to_jsonl}


def render(store: Store, fmt: str, **kwargs) -> str:
    try:
        return RENDERERS[fmt](store, **kwargs)
    except KeyError:
        raise ValueError(f"unknown format {fmt!r} (expected one of {', '.join(sorted(RENDERERS))})")
