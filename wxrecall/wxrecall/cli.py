"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import tempfile
import sys
from pathlib import Path

from . import __version__
from .backends import load as load_backend
from .export import render
from .recorder import Recorder, RecorderConfig
from .store import Store
from .watcher import Changes

DEFAULT_DB = "wxrecall.db"

RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"


def _colour(enabled: bool):
    if enabled and sys.stdout.isatty():
        return RED, DIM, BOLD, OFF
    return "", "", "", ""


def _print_changes(chat: str, changes: Changes, colour: bool = True) -> None:
    red, dim, bold, off = _colour(colour)
    for m in changes.new:
        print(f"{dim}[{m.seen_at}]{off} {bold}{m.sender}{off} · {chat}: {m.content}")
    for r in changes.recalled:
        print(
            f"{red}[撤回]{off} {dim}{r.detected_at}{off} {bold}{r.message.sender}{off} "
            f"· {chat}: {red}{r.message.content}{off}"
        )
    for m in changes.vanished:
        print(f"{dim}[消失? {m.sender} · {chat}: {m.content}]{off}")


def cmd_watch(args) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    backend = load_backend(
        args.backend,
        **({"screenshots": bool(args.screenshots)} if args.backend == "uia" else {}),
    )
    config = RecorderConfig(
        interval=args.interval,
        chats=args.chats.split(",") if args.chats else None,
        keep_scrollback=not args.no_backlog,
        screenshots_dir=Path(args.screenshots) if args.screenshots else None,
    )
    stopping = {"flag": False}

    def _stop(signum, frame):
        stopping["flag"] = True
        print("\n正在停止…", file=sys.stderr)

    signal.signal(signal.SIGINT, _stop)

    with Store(args.db) as store:
        recorder = Recorder(backend, store, config)
        target = args.chats or "当前打开的会话"
        print(f"记录中 → {args.db}（{target}，每 {args.interval}s 一次，Ctrl-C 停止）")
        counts = recorder.run(
            max_polls=args.max_polls,
            on_change=lambda c, ch: _print_changes(c, ch, not args.no_colour),
            stop=lambda: stopping["flag"],
        )
        print(
            f"\n新增 {counts['new']} · 撤回 {counts['recalled']} · "
            f"消失 {counts['vanished']} · 回填 {counts['scrollback']}"
        )
    backend.close()
    return 0


def cmd_demo(args) -> int:
    """Run the whole pipeline against a scripted conversation."""
    if not args.db:
        # A throwaway database, recreated each run: replaying the same script
        # into a live archive would look like the conversation happened twice.
        args.db = str(Path(tempfile.gettempdir()) / "wxrecall-demo.db")
        Path(args.db).unlink(missing_ok=True)
    backend = load_backend("replay", script=args.script)
    with Store(args.db) as store:
        recorder = Recorder(backend, store, RecorderConfig(interval=0))
        counts = recorder.run(
            max_polls=args.frames,
            on_change=lambda c, ch: _print_changes(c, ch, not args.no_colour),
        )
    print(f"\n新增 {counts['new']} · 撤回 {counts['recalled']} · 回填 {counts['scrollback']}")
    print(f"数据库：{args.db}")
    return 0


def cmd_list(args) -> int:
    with Store(args.db) as store:
        rows = store.query(
            chat=args.chat, recalled_only=args.recalled, search=args.search, limit=args.limit
        )
        if not rows:
            print("没有匹配的记录。")
            return 0
        red, dim, bold, off = _colour(not args.no_colour)
        for r in reversed(rows):
            tag = f"{red}[撤回]{off} " if r["recalled"] else ""
            print(f"{dim}{r['seen_at']}{off} {tag}{bold}{r['sender']}{off} · {r['chat']}: {r['content']}")
    return 0


def cmd_chats(args) -> int:
    with Store(args.db) as store:
        rows = store.chats()
        if not rows:
            print("库里还没有会话。")
            return 0
        width = max(len(c) for c, _, _ in rows)
        for chat, n, recalled in rows:
            print(f"{chat.ljust(width)}  {n:>6} 条  {recalled:>4} 撤回")
    return 0


def cmd_stats(args) -> int:
    with Store(args.db) as store:
        s = store.stats()
    print(f"会话 {s['chats']} · 消息 {s['total']} · 撤回 {s['recalled']} · 消失 {s['vanished']}")
    return 0


def cmd_export(args) -> int:
    with Store(args.db) as store:
        out = render(store, args.format, chat=args.chat, recalled_only=args.recalled)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        sys.stdout.write(out)
    return 0


def cmd_probe(args) -> int:
    from .backends.uia import UIABackend

    backend = UIABackend(profile=args.profile)
    print(backend.probe(max_depth=args.depth))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wxrecall",
        description="本地记录微信聊天，保留被撤回的消息。只读窗口，不注入、不联网。",
    )
    p.add_argument("--version", action="version", version=f"wxrecall {__version__}")
    p.add_argument("--db", help=f"SQLite 路径（默认 {DEFAULT_DB}；demo 用一次性临时库）")
    sub = p.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watch", help="持续记录当前会话")
    w.add_argument("--backend", default="uia", choices=["uia", "replay"])
    w.add_argument("--interval", type=float, default=1.0, help="轮询间隔秒数")
    w.add_argument("--chats", help="只记录这些会话，逗号分隔；默认跟随当前打开的会话")
    w.add_argument("--screenshots", metavar="DIR", help="检测到撤回时保存撤回前的截图到该目录")
    w.add_argument("--no-backlog", action="store_true", help="不保存启动时窗口里已有的历史")
    w.add_argument("--max-polls", type=int, help="轮询这么多次后退出（调试用）")
    w.add_argument("--no-colour", action="store_true")
    w.add_argument("-v", "--verbose", action="store_true")
    w.set_defaults(func=cmd_watch)

    d = sub.add_parser("demo", help="用脚本化的假会话跑一遍完整流程（任何系统都能跑）")
    d.add_argument("--frames", type=int, default=6)
    d.add_argument("--script", help="自定义帧的 JSON 文件")
    d.add_argument("--no-colour", action="store_true")
    d.set_defaults(func=cmd_demo)

    l = sub.add_parser("list", help="查询已记录的消息")
    l.add_argument("--chat")
    l.add_argument("--recalled", action="store_true", help="只看被撤回的")
    l.add_argument("--search", help="内容包含")
    l.add_argument("--limit", type=int, default=50)
    l.add_argument("--no-colour", action="store_true")
    l.set_defaults(func=cmd_list)

    c = sub.add_parser("chats", help="列出库里的会话")
    c.set_defaults(func=cmd_chats)

    s = sub.add_parser("stats", help="总体统计")
    s.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="导出为 html / md / jsonl")
    e.add_argument("--chat")
    e.add_argument("--recalled", action="store_true")
    e.add_argument("--format", default="html", choices=["html", "md", "markdown", "jsonl"])
    e.add_argument("-o", "--output")
    e.set_defaults(func=cmd_export)

    pr = sub.add_parser("probe", help="打印微信窗口的控件树（适配新版本时用）")
    pr.add_argument("--profile", default="auto", choices=["auto", "win3", "win4"])
    pr.add_argument("--depth", type=int, default=8)
    pr.set_defaults(func=cmd_probe)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "demo":
        args.db = args.db or DEFAULT_DB
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
