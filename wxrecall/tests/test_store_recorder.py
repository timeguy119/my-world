import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wxrecall.backends.replay import ReplayBackend
from wxrecall.models import Message, MsgType
from wxrecall.recorder import Recorder, RecorderConfig
from wxrecall.store import Store
from wxrecall.watcher import Recall

CHAT = "演示群"


def msg(sender, content, chat=CHAT, mtype=MsgType.TEXT):
    return Message(chat=chat, sender=sender, content=content, msg_type=mtype)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_and_read_back(self):
        self.store.add(msg("A", "hello"))
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "hello")
        self.assertEqual(rows[0]["recalled"], 0)

    def test_mark_recalled_flags_the_stored_row(self):
        m = msg("A", "oops")
        self.store.add(m)
        self.store.mark_recalled(Recall(message=m, notice=msg("system", "A撤回了一条消息")))
        rows = self.store.query(recalled_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "oops")
        self.assertIsNotNone(rows[0]["recalled_at"])

    def test_recall_of_never_stored_message_is_still_kept(self):
        m = msg("A", "sent before we started")
        self.store.mark_recalled(Recall(message=m, notice=None))
        rows = self.store.query(recalled_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "sent before we started")

    def test_same_text_recalled_twice_marks_two_rows(self):
        m = msg("A", "在吗")
        self.store.add(m)
        self.store.add(m)
        self.store.mark_recalled(Recall(message=m, notice=None))
        self.store.mark_recalled(Recall(message=m, notice=None))
        self.assertEqual(len(self.store.query(recalled_only=True)), 2)

    def test_recent_returns_oldest_first(self):
        for i in range(5):
            self.store.add(msg("A", str(i)))
        self.assertEqual([m.content for m in self.store.recent(CHAT, 3)], ["2", "3", "4"])

    def test_search_and_stats(self):
        self.store.add(msg("A", "会议纪要"))
        self.store.add(msg("B", "午饭吃啥"))
        self.assertEqual(len(self.store.query(search="会议")), 1)
        self.assertEqual(self.store.stats()["total"], 2)


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, frames=6):
        with Store(self.db) as store:
            rec = Recorder(ReplayBackend(), store, RecorderConfig(interval=0))
            return rec.run(max_polls=frames)

    def test_end_to_end_demo_detects_both_recalls(self):
        counts = self._run()
        self.assertEqual(counts["recalled"], 2)
        with Store(self.db) as store:
            recalled = store.query(recalled_only=True)
            self.assertEqual(
                sorted(r["content"] for r in recalled), ["[图片]", "对了，密码是 hunter2"]
            )
            self.assertEqual(store.stats()["vanished"], 0)

    def test_restart_does_not_reimport_the_backlog(self):
        """Regression: stored history was once diffed as if it were a snapshot,
        which flagged the whole archive as vanished and re-inserted it."""
        self._run()
        with Store(self.db) as store:
            before = store.stats()["total"]

        with Store(self.db) as store:
            rec = Recorder(ReplayBackend(), store, RecorderConfig(interval=0))
            counts = rec.run(max_polls=1)   # a restart sees frame 0 again

        self.assertEqual(counts["scrollback"], 0, "backlog was re-inserted on restart")
        self.assertEqual(counts["vanished"], 0, "backlog was misread as disappearing")
        with Store(self.db) as store:
            self.assertEqual(store.stats()["total"], before)

    def test_only_configured_chats_are_recorded(self):
        with Store(self.db) as store:
            rec = Recorder(ReplayBackend(), store, RecorderConfig(interval=0, chats=["别的群"]))
            counts = rec.run(max_polls=6)
        self.assertEqual(counts["new"], 0)
        with Store(self.db) as store:
            self.assertEqual(store.stats()["total"], 0)

    def test_poll_survives_a_backend_error(self):
        logging.disable(logging.CRITICAL)      # the failure is logged on purpose
        self.addCleanup(logging.disable, logging.NOTSET)

        class Flaky(ReplayBackend):
            def snapshot(self, chat):
                raise RuntimeError("window disappeared")

        with Store(self.db) as store:
            rec = Recorder(Flaky(), store, RecorderConfig(interval=0))
            counts = rec.run(max_polls=3)   # must not raise
        self.assertEqual(counts["new"], 0)


class ExportTest(unittest.TestCase):
    def test_html_marks_recalled_messages(self):
        from wxrecall.export import render

        tmp = tempfile.TemporaryDirectory()
        with Store(Path(tmp.name) / "t.db") as store:
            m = msg("A", "<script>oops</script>")
            store.add(m)
            store.mark_recalled(Recall(message=m, notice=None))
            html = render(store, "html")
        self.assertIn("recalled", html)
        self.assertIn("已撤回", html)
        self.assertNotIn("<script>oops", html)   # content must be escaped
        tmp.cleanup()

    def test_unknown_format_is_rejected(self):
        from wxrecall.export import render

        tmp = tempfile.TemporaryDirectory()
        with Store(Path(tmp.name) / "t.db") as store:
            with self.assertRaises(ValueError):
                render(store, "pdf")
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
