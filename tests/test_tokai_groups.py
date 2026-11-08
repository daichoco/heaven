import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokai_fuzoku import collect_fuzoku_tokai as collect
from tokai_fuzoku import report_store as store
from tokai_fuzoku.html_render import generate_html

BASE = collect.BASE_URL


def url(number):
    return f"{BASE}2026/01/02/post-{number}/"


def category_page(urls):
    return "".join(f'<article class="entry-card"><a href="{value}">記事</a></article>' for value in urls)


class Response:
    def __init__(self, url, text, status=200):
        self.url, self.text, self.status_code = url, text, status

    def raise_for_status(self):
        pass


class CategorySession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.pages:
            return Response(url, "", 404)
        return Response(url, self.pages[url])


class TokaiGroupTests(unittest.TestCase):
    def test_group_categories_cover_price_tiers(self):
        names = collect.GROUP_NAMES
        for tier in ("金津園ソープ格安店", "金津園ソープ大衆店", "金津園ソープ中級店", "金津園ソープ高級店"):
            self.assertIn(tier, names)

    def test_fill_groups_assigns_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = store.ReportStore(Path(tmp) / "r.jsonl")
            for number, extra in ((1, {}), (2, {}), (3, {"shop_group": "手動"})):
                item.upsert({"url": url(number), "name": f"n{number}", **extra})
            pages = {
                BASE + "category/high/": category_page([url(1), url(3)]),
                BASE + "category/high/page/2/": category_page([url(2)]),
            }
            session = CategorySession(pages)
            collect.fill_groups(session, item, [("high", "高級店")], collect_fetch(), sleep=0)
            self.assertEqual(item.get(url(1))["shop_group"], "高級店")
            self.assertEqual(item.get(url(2))["shop_group"], "高級店")
            self.assertEqual(item.get(url(3))["shop_group"], "手動")

    def test_login_page_raises(self):
        session = CategorySession({BASE + "category/high/": "password_protected_pwd"})
        with self.assertRaises(collect.LoginRequiredError):
            collect.fetch_category_urls(session, "high", 1)

    def test_filter_rendered_with_options(self):
        frame = pd.DataFrame([
            {"name": "A", "shop_name": "S1", "type": "ソープランド", "url": url(1), "shop_group": "金津園ソープ高級店"},
        ])
        html = generate_html(frame, group_options=["金津園ソープ高級店"])
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNotNone(soup.select_one("#groupFilter"))
        self.assertEqual(soup.select_one(".article-entry")["data-group"], "金津園ソープ高級店")


def collect_fetch():
    return collect.fetch_category_urls


if __name__ == "__main__":
    unittest.main()
