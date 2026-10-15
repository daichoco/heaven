import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from okinilove import scrape_okinilove


class NumberedArticleDiscoveryTests(unittest.TestCase):
    def test_discovers_numbered_profiles_per_review_section(self):
        soup = BeautifulSoup(
            """
            <div>
              <h3><a name="sakuma">aQua（アクア）すい体験レポ</a></h3>
              <h4>風俗店名</h4>AQUA
              <h4>女の子の名前</h4>
              <a href="https://www3.yoasobi.co.jp/tokai/girl/1312162">すい</a>
              <h4>利用者満足度</h4>★★★★★
              <h3 id="ordinary"><a name="ordinary">aQua（アクア）ももえ体験レポ</a></h3>
              <h4>風俗店名</h4>AQUA
              <h4>女の子の名前</h4>
              <a href="https://www12.yoasobi.co.jp/tokai/girl/1312163">ももえ</a>
              <h4>利用者満足度</h4>★★★★★
            </div>
            """,
            "html.parser",
        )
        candidates = scrape_okinilove.numbered_profile_candidates(
            soup,
            "https://okinilove.com/blog-entry-2811.html",
            {"AQUA": "アクア"},
        )

        self.assertEqual(
            candidates,
            [
                {
                    "shop": "アクア",
                    "name": "すい",
                    "profile_url": "https://www3.yoasobi.co.jp/tokai/girl/1312162",
                    "article_url": "https://okinilove.com/blog-entry-2811.html#sakuma",
                },
                {
                    "shop": "アクア",
                    "name": "ももえ",
                    "profile_url": "https://www12.yoasobi.co.jp/tokai/girl/1312163",
                    "article_url": "https://okinilove.com/blog-entry-2811.html#ordinary",
                },
            ],
        )


class ProfileCheckUrlTests(unittest.TestCase):
    def test_normalizes_any_numbered_www_host(self):
        for numbered_host in ("www3.yoasobi.co.jp", "www4.example.com", "www12.example.com"):
            with self.subTest(host=numbered_host):
                self.assertEqual(
                    scrape_okinilove.profile_check_url(
                        f"https://{numbered_host}/girl/123?shop=abc"
                    ),
                    f"https://www.{numbered_host.split('.', 1)[1]}/girl/123?shop=abc",
                )

    def test_leaves_other_hosts_unchanged(self):
        for url in (
            "https://www.yoasobi.co.jp/girl/123",
            "https://shop.example.com/girl/123",
        ):
            with self.subTest(url=url):
                self.assertEqual(scrape_okinilove.profile_check_url(url), url)

    def test_profile_status_checks_the_normalized_host(self):
        response = Mock(status_code=200, url="https://www.yoasobi.co.jp/girl/123456")
        response.close = Mock()
        with (
            patch.object(scrape_okinilove.requests, "get", return_value=response) as get,
            patch.object(scrape_okinilove, "is_personal_profile", return_value=True),
        ):
            self.assertEqual(
                scrape_okinilove._fetch_profile_status(
                    "https://www3.yoasobi.co.jp/girl/123456"
                ),
                200,
            )
        self.assertEqual(get.call_args.args[0], "https://www.yoasobi.co.jp/girl/123456")

    def test_numbered_host_ignores_cached_unavailable_status(self):
        now = datetime.now(timezone.utc)
        source_url = "https://www12.example.com/girl/123456"
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "profile-status.json"
            cache_path.write_text(
                json.dumps(
                    {
                        source_url: {
                            "checked_at": (now - timedelta(days=1)).isoformat(),
                            "status": 410,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(scrape_okinilove, "PROFILE_STATUS_CACHE_PATH", cache_path),
                patch.object(scrape_okinilove, "_fetch_profile_status", return_value=200) as fetch,
            ):
                self.assertEqual(
                    scrape_okinilove.find_unavailable_profile_urls([source_url], now=now),
                    set(),
                )
        fetch.assert_called_once_with(source_url)

    def test_article_scraper_adds_person_found_on_normalized_host(self):
        article_response = Mock(text="<html></html>")
        article_response.raise_for_status = Mock()
        profile_response = Mock(
            status_code=200,
            url="https://www.yoasobi.co.jp/girl/123456",
            content="<p>年齢 25歳</p>".encode(),
            encoding="utf-8",
            apparent_encoding="utf-8",
        )
        profile_response.raise_for_status = Mock()
        session = Mock()
        session.get.side_effect = [article_response, profile_response]
        girl_link = BeautifulSoup(
            '<a href="https://www3.yoasobi.co.jp/girl/123456">りん</a>',
            "html.parser",
        ).a
        fields = [
            ("女の子の名前", "りん", girl_link),
            ("利用者満足度", "★★★★★", None),
        ]

        with (
            patch.object(scrape_okinilove, "article_section_fields", return_value=fields),
            patch.object(scrape_okinilove, "extract_report_detail", return_value="レポート本文"),
        ):
            record = scrape_okinilove.fetch_article_record(
                session,
                "店舗",
                "https://okinilove.com/blog-entry-123.html#review",
            )

        self.assertIsNotNone(record)
        self.assertEqual(record["girl_url"], "https://www.yoasobi.co.jp/girl/123456")
        self.assertEqual(record["age"], "25")
        self.assertEqual(
            session.get.call_args_list[1].args[0],
            "https://www.yoasobi.co.jp/girl/123456",
        )


if __name__ == "__main__":
    unittest.main()
