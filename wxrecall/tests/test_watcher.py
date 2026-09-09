import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wxrecall.models import Message, MsgType
from wxrecall.watcher import ChatWatcher, dedupe_against, diff_snapshots

CHAT = "项目组"


def msg(sender, content, mtype=MsgType.TEXT):
    return Message(chat=CHAT, sender=sender, content=content, msg_type=mtype)


def notice(who="小明"):
    return msg("system", f"{who}撤回了一条消息", MsgType.SYSTEM)


class TestDiff(unittest.TestCase):
    def test_new_messages_at_tail(self):
        prev = [msg("A", "1"), msg("B", "2")]
        curr = prev + [msg("A", "3")]
        c = diff_snapshots(prev, curr)
        self.assertEqual([m.content for m in c.new], ["3"])
        self.assertFalse(c.recalled)
        self.assertFalse(c.vanished)

    def test_recall_detected_when_notice_replaces_bubble(self):
        prev = [msg("A", "1"), msg("小明", "口误，别看"), msg("B", "3")]
        curr = [msg("A", "1"), notice("小明"), msg("B", "3")]
        c = diff_snapshots(prev, curr)
        self.assertEqual(len(c.recalled), 1)
        self.assertEqual(c.recalled[0].message.content, "口误，别看")
        self.assertEqual(c.recalled[0].message.sender, "小明")
        self.assertIn("撤回", c.recalled[0].notice.content)
        self.assertFalse(c.vanished)

    def test_recall_of_last_message(self):
        prev = [msg("A", "1"), msg("小明", "秘密")]
        curr = [msg("A", "1"), notice("小明")]
        c = diff_snapshots(prev, curr)
        self.assertEqual(len(c.recalled), 1)
        self.assertEqual(c.recalled[0].message.content, "秘密")

    def test_recall_of_image_keeps_placeholder(self):
        prev = [msg("A", "1"), msg("小明", "[图片]", MsgType.IMAGE)]
        curr = [msg("A", "1"), notice("小明")]
        c = diff_snapshots(prev, curr)
        self.assertEqual(len(c.recalled), 1)
        self.assertEqual(c.recalled[0].message.msg_type, MsgType.IMAGE)

    def test_viewport_trim_is_not_a_recall(self):
        prev = [msg("A", str(i)) for i in range(5)]
        curr = prev[2:] + [msg("A", "5")]
        c = diff_snapshots(prev, curr)
        self.assertFalse(c.recalled)
        self.assertFalse(c.vanished)
        self.assertEqual([m.content for m in c.new], ["5"])

    def test_scrollback_is_not_new_traffic(self):
        prev = [msg("A", "3"), msg("A", "4")]
        curr = [msg("A", "1"), msg("A", "2")] + prev
        c = diff_snapshots(prev, curr)
        self.assertEqual([m.content for m in c.scrollback], ["1", "2"])
        self.assertFalse(c.new)

    def test_unexplained_disappearance_is_vanished_not_recalled(self):
        prev = [msg("A", "1"), msg("B", "2"), msg("A", "3")]
        curr = [msg("A", "1"), msg("A", "3")]
        c = diff_snapshots(prev, curr)
        self.assertFalse(c.recalled)
        self.assertEqual([m.content for m in c.vanished], ["2"])

    def test_notice_without_known_bubble_is_still_recorded(self):
        prev = [msg("A", "1")]
        curr = [msg("A", "1"), notice("小明")]
        c = diff_snapshots(prev, curr)
        self.assertFalse(c.recalled)
        self.assertEqual(len(c.new), 1)
        self.assertIn("撤回", c.new[0].content)

    def test_two_recalls_in_one_poll(self):
        prev = [msg("A", "1"), msg("小明", "x"), msg("小明", "y"), msg("B", "4")]
        curr = [msg("A", "1"), notice("小明"), notice("小明"), msg("B", "4")]
        c = diff_snapshots(prev, curr)
        self.assertEqual(sorted(r.message.content for r in c.recalled), ["x", "y"])

    def test_repeated_identical_texts_survive_alignment(self):
        prev = [msg("A", "在"), msg("A", "在"), msg("A", "在")]
        curr = [msg("A", "在"), msg("A", "在"), msg("A", "在"), msg("A", "在")]
        c = diff_snapshots(prev, curr)
        self.assertEqual(len(c.new), 1)
        self.assertFalse(c.recalled)

    def test_idle_poll_produces_nothing(self):
        prev = [msg("A", "1"), msg("B", "2")]
        c = diff_snapshots(prev, list(prev))
        self.assertFalse(c)


class TestWatcher(unittest.TestCase):
    def test_first_snapshot_is_backlog_not_new(self):
        w = ChatWatcher(CHAT)
        c = w.bootstrap([msg("A", "1"), msg("A", "2")])
        self.assertEqual(len(c.scrollback), 2)
        self.assertFalse(c.new)

    def test_feed_sequence(self):
        w = ChatWatcher(CHAT)
        w.feed([msg("A", "1")])
        c = w.feed([msg("A", "1"), msg("小明", "oops")])
        self.assertEqual([m.content for m in c.new], ["oops"])
        c = w.feed([msg("A", "1"), notice("小明")])
        self.assertEqual(len(c.recalled), 1)
        self.assertEqual(c.recalled[0].message.content, "oops")

    def test_restart_dedupe(self):
        known = [msg("A", "1"), msg("A", "2"), msg("A", "3")]
        snapshot = [msg("A", "2"), msg("A", "3"), msg("A", "4")]
        self.assertEqual([m.content for m in dedupe_against(known, snapshot)], ["4"])

    def test_restart_dedupe_with_no_overlap(self):
        known = [msg("A", "1")]
        snapshot = [msg("A", "9"), msg("A", "10")]
        self.assertEqual(len(dedupe_against(known, snapshot)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
