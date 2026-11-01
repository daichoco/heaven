import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from okinilove import scrape_okinilove as scraper


def category_response(category=70, page=0, entries=(), status=200, html=None):
    url = f"https://okinilove.com/blog-category-{category}"
    url += f"-{page}.html" if page else ".html"
    if html is None:
        links = "".join(
            f'<a id="entry{entry}"></a>'
            f'<a href="https://okinilove.com/blog-entry-{entry}.html">'
            f'{name}-名古屋・デリヘル【全投稿履歴】</a>'
            for entry, name in entries
        )
        html = (
            "<html><title>デリヘル体験レビュー【全投稿履歴】</title>"
            f'<div id="center">{links}</div></html>'
        )
    return Mock(status_code=status, url=url, text=html, content=html.encode("utf-8"))


class ShopIndexTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(redirect_stderr(self.stderr))
        self.enterContext(patch.object(scraper.time, "sleep"))

    def test_success_and_normal_empty_terminal_page(self):
        session = Mock()
        session.get.side_effect = [
            category_response(entries=[(1688, "DERIDOS（でりどす）")]),
            category_response(page=1, entries=[(2000, "アクア")]),
            category_response(page=2),
        ]
        shops, aliases, industries, labels = scraper.fetch_shop_index(session, [70])
        self.assertEqual(shops["でりどす"], "https://okinilove.com/blog-entry-1688.html")
        self.assertEqual(aliases["DERIDOS"], "でりどす")
        self.assertEqual(industries["でりどす"], "デリヘル")
        self.assertEqual(labels["でりどす"], {"70": ["名古屋・デリヘル"]})
        self.assertEqual(len(shops), 2)
        self.assertIn("status=200", self.stdout.getvalue())
        self.assertIn("page=2", self.stdout.getvalue())

    def test_repeated_terminal_page_preserves_existing_behavior(self):
        session = Mock()
        session.get.side_effect = [
            category_response(entries=[(1688, "でりどす")]),
            category_response(page=1, entries=[(1688, "でりどす")]),
        ]
        self.assertEqual(len(scraper.fetch_shop_index(session, [70])[0]), 1)

    def test_invalid_first_pages_raise(self):
        for response in (
            category_response(status=403, html="<html>Forbidden</html>"),
            category_response(status=404),
            category_response(html="<html><title>Access denied</title></html>"),
            category_response(html=""),
            category_response(),
        ):
            with self.subTest(response=response):
                with self.assertRaises(RuntimeError):
                    scraper.fetch_shop_index(Mock(get=Mock(return_value=response)), [70])
        self.assertIn("本文先頭", self.stderr.getvalue())

    def test_unparsed_entries_on_later_page_are_not_terminal(self):
        session = Mock()
        session.get.side_effect = [
            category_response(entries=[(1688, "でりどす")]),
            category_response(
                page=1,
                html='<title>【全投稿履歴】</title><div id="center"><a id="entry2">Changed markup</a></div>',
            ),
        ]
        with self.assertRaises(RuntimeError):
            scraper.fetch_shop_index(session, [70])

    def test_redirect_to_error_page_even_with_matching_html_raises(self):
        response = category_response(entries=[(1688, "でりどす")])
        response.url = "https://error.fc2.com/blog3/"
        with self.assertRaises(RuntimeError):
            scraper.fetch_shop_index(Mock(get=Mock(return_value=response)), [70])
        self.assertIn("https://error.fc2.com/blog3/", self.stdout.getvalue())

    def test_network_errors_are_logged_and_propagated(self):
        session = Mock()
        session.get.side_effect = requests.Timeout("timed out")
        with self.assertRaises(requests.Timeout):
            scraper.fetch_shop_index(session, [70])
        self.assertIn("timed out", self.stderr.getvalue())

    def test_diagnosis_checks_all_first_pages_without_writing(self):
        session = Mock()
        session.get.return_value = category_response(status=403)
        with patch.object(scraper, "SHOP_INDEX_PATH") as path:
            with self.assertRaises(RuntimeError):
                scraper.diagnose_shop_index(session)
            path.write_text.assert_not_called()
        self.assertEqual(session.get.call_count, 3)

    def test_failed_or_empty_refresh_preserves_old_index_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            original = b'{"shops":{"old":"old-url"}}\n'
            for result in ("partial-page", "partial-category", "empty-result"):
                with self.subTest(result=result):
                    path.write_bytes(original)
                    session = Mock()
                    if result == "partial-page":
                        session.get.side_effect = [
                            category_response(entries=[(1688, "でりどす")]),
                            category_response(page=1, status=403),
                        ]
                    else:
                        session.get.side_effect = [
                            category_response(entries=[(1688, "でりどす")]),
                            category_response(page=1),
                            category_response(category=71, status=403),
                        ]
                    with patch.object(scraper, "SHOP_INDEX_PATH", path):
                        if result == "empty-result":
                            with patch.object(scraper, "fetch_shop_index", return_value=({}, {}, {}, {})):
                                with self.assertRaises(RuntimeError):
                                    scraper.load_shop_urls(session)
                        else:
                            with self.assertRaises(RuntimeError):
                                scraper.load_shop_urls(session)
                    self.assertEqual(path.read_bytes(), original)

    def test_successful_refresh_writes_complete_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            result = (
                {"でりどす": "https://okinilove.com/blog-entry-1688.html"},
                {"DERIDOS": "でりどす"},
                {"でりどす": "デリヘル"},
                {"でりどす": {"70": ["名古屋・デリヘル"]}},
            )
            with (
                patch.object(scraper, "SHOP_INDEX_PATH", path),
                patch.object(scraper, "fetch_shop_index", return_value=result) as fetch,
            ):
                self.assertEqual(scraper.load_shop_urls(Mock()), result[:2])
                cached = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(cached["schema_version"], scraper.SHOP_INDEX_SCHEMA_VERSION)
                self.assertEqual(cached["categories"], [70, 71, 72])
                self.assertEqual(cached["industry_labels"], result[3])
                datetime.fromisoformat(cached["fetched_at"])
                self.assertEqual(scraper.load_shop_urls(Mock()), result[:2])
                self.assertEqual(fetch.call_count, 1)

    def test_fresh_but_empty_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps({
                "schema_version": scraper.SHOP_INDEX_SCHEMA_VERSION,
                "categories": [70, 71, 72],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "shops": {},
            }), encoding="utf-8")
            with (
                patch.object(scraper, "SHOP_INDEX_PATH", path),
                patch.object(scraper, "fetch_shop_index", side_effect=RuntimeError("blocked")) as fetch,
            ):
                with self.assertRaises(RuntimeError):
                    scraper.load_shop_urls(Mock())
                fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
