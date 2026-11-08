import sys
import unittest
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kanto_fuzoku import collect_fuzoku_kanto as collect
from kanto_fuzoku import report_store as store
from tokai_fuzoku.html_render import generate_html

BASE = store.BASE_URL


def url(number):
    return f"{BASE}2026/01/02/post-{number}/"


def category_page(urls):
    return "".join(
        f'<article class="entry-card"><a href="{value}">記事</a></article>' for value in urls
    )


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


class FillGroupsTests(unittest.TestCase):
    def make_store(self, tmp, records):
        path = Path(tmp) / "r.jsonl"
        item = store.ReportStore(path)
        for number, extra in records:
            item.upsert({"url": url(number), "name": f"n{number}", **extra})
        return item

    def run_fill(self, item, pages, **kwargs):
        session = CategorySession(pages)
        original = collect.GROUP_CATEGORIES
        collect.GROUP_CATEGORIES = [("high", "高級店"), ("mid", "大衆店")]
        try:
            collect.run_fill_groups(session, item, sleep=0, **kwargs)
        finally:
            collect.GROUP_CATEGORIES = original
        return session

    def test_assigns_group_without_overwriting_and_stops_early(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_store(tmp, [(1, {}), (2, {}), (3, {"shop_group": "手動"})])
            pages = {
                BASE + "category/high/": category_page([url(1), url(3)]),
                BASE + "category/high/page/2/": category_page([url(2)]),
                BASE + "category/mid/": category_page([url(2)]),
            }
            session = self.run_fill(item, pages)
            self.assertEqual(item.get(url(1))["shop_group"], "高級店")
            self.assertEqual(item.get(url(3))["shop_group"], "手動")
            self.assertEqual(item.get(url(2))["shop_group"], "高級店")
            self.assertTrue(item.dirty)
            self.assertNotIn(BASE + "category/high/page/4/", session.calls)

    def test_incremental_stops_when_page_already_grouped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            item = self.make_store(tmp, [(1, {"shop_group": "高級店"}), (2, {})])
            pages = {
                BASE + "category/high/": category_page([url(1)]),
                BASE + "category/high/page/2/": category_page([url(2)]),
            }
            session = self.run_fill(item, pages)
            self.assertNotIn(BASE + "category/high/page/2/", session.calls)
            self.assertIsNone(item.get(url(2)).get("shop_group"))
            self.run_fill(item, pages, full=True)
            self.assertEqual(item.get(url(2))["shop_group"], "高級店")


class GroupFilterRenderTests(unittest.TestCase):
    def frame(self):
        rows = [
            {"name": "A", "shop_name": "S1", "type": "ソープランド", "url": url(1), "shop_group": "高級店"},
            {"name": "B", "shop_name": "S2", "type": "ソープランド", "url": url(2), "shop_group": "大衆店"},
        ]
        return pd.DataFrame(rows)

    def test_group_filter_rendered_only_when_requested(self):
        html = generate_html(self.frame(), title="t", include_age_filter=True, group_options=["高級店", "大衆店"])
        soup = BeautifulSoup(html, "html.parser")
        select = soup.select_one("#groupFilter")
        self.assertIsNotNone(select)
        self.assertEqual(
            [o.get_text() for o in select.find_all("option")],
            ["価格帯・エリアすべて", "高級店", "大衆店", "閉店・移籍など(未分類)"],
        )
        self.assertIn("__none__", html)
        self.assertEqual(
            sorted(a["data-group"] for a in soup.select(".article-entry")), ["大衆店", "高級店"],
        )
        self.assertIn("article.dataset.group === group", html)
        self.assertNotIn("GROUP_FILTER", html)

    def test_default_output_has_no_group_ui(self):
        html = generate_html(self.frame(), title="t")
        self.assertNotIn("groupFilter", html)
        self.assertNotIn("GROUP_FILTER", html)


if __name__ == "__main__":
    unittest.main()
