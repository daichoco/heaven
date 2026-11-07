import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import report_change_summary as rcs


class ReportChangeSummaryTest(unittest.TestCase):
    def test_tokai_requires_high_total_and_repeat(self):
        self.assertTrue(rcs.tokai_high({"total_point": 80, "repeat": "返す（リピートする）"}))
        self.assertFalse(rcs.tokai_high({"total_point": 85, "repeat": "返さない（リピートしない）"}))
        self.assertFalse(rcs.tokai_high({"total_point": 79, "repeat": "返す（リピートする）"}))

    def test_ns_total_threshold(self):
        self.assertTrue(rcs.ns_high({"details": {rcs.NS_TOTAL_KEY: "90点"}}))
        self.assertFalse(rcs.ns_high({"details": {rcs.NS_TOTAL_KEY: "85点"}}))
        self.assertFalse(rcs.ns_high({"details": {}}))

    def test_okinilove_stars(self):
        self.assertTrue(rcs.okinilove_high({"evaluations": "評価 ★★★★☆ →記事リンク"}))
        self.assertTrue(rcs.okinilove_high({"evaluations": "最高評価 ★★★★★ →記事リンク"}))
        self.assertFalse(rcs.okinilove_high({"evaluations": "評価 ★★★☆☆ →記事リンク"}))

    def test_compare_counts_added_and_deleted(self):
        old = [{"post_id": 1, "name": "a"}, {"post_id": 2, "name": "b"}]
        new = [{"post_id": 2, "name": "b"}, {"post_id": 3, "name": "c", "details": {rcs.NS_TOTAL_KEY: "95点"}}]
        added, deleted, high = rcs.compare(old, new, rcs.ns_key, rcs.ns_high)
        self.assertEqual([r["post_id"] for r in added], [3])
        self.assertEqual([r["post_id"] for r in deleted], [1])
        self.assertEqual(len(high), 1)

    def test_summary_text(self):
        results = {"東海": ([], [{}], [], rcs.tokai_label)}
        self.assertEqual(rcs.summarize(results), "東海: 追加0件 / 削除1件")


if __name__ == "__main__":
    unittest.main()
