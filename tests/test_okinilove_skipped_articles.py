import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from okinilove import scrape_okinilove as so

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)
OLD_CHECK = "2026-01-01T00:00:00+00:00"
RECENT_CHECK = "2026-11-01T00:00:00+00:00"
BASE = "http://okinilove.com/blog-entry-1.html"


class SkippedArticlesTest(unittest.TestCase):
    def urls(self, skipped):
        return so.recently_skipped_urls(skipped, now=NOW)

    def test_legacy_string_entries_keep_30_day_retry(self):
        skipped = {BASE + "#a": OLD_CHECK, BASE + "#b": RECENT_CHECK}
        self.assertEqual(self.urls(skipped), {BASE + "#b"})

    def test_old_published_date_is_never_rechecked(self):
        skipped = {
            BASE + "#a": so.skipped_entry(OLD_CHECK, "2022-12-31"),
            BASE + "#b": so.skipped_entry(OLD_CHECK, "2023-01-01"),
            BASE + "#c": so.skipped_entry(OLD_CHECK),
        }
        self.assertEqual(self.urls(skipped), {BASE + "#a"})

    def test_dict_entry_respects_retry_window(self):
        skipped = {BASE + "#a": so.skipped_entry(RECENT_CHECK, "2025-05-05")}
        self.assertEqual(self.urls(skipped), {BASE + "#a"})

    def test_non_fragment_urls_are_always_skipped(self):
        self.assertEqual(self.urls({BASE: OLD_CHECK}), {BASE})


if __name__ == "__main__":
    unittest.main()
