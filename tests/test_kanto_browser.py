"""関東レポートのオフラインブラウザ確認。外部リソースは読み込まない。"""
import sys
import unittest
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tokai_fuzoku.html_render import generate_html


class KantoBrowserTests(unittest.TestCase):
    def test_generated_report_has_working_filters_and_grouped_reviews(self):
        reports = [
            {
                "girl_url": "https://profiles.invalid/profile.html?uid=a",
                "shop_name": "Alpha", "type": "type-a", "name": "Person A",
                "predict_age": "35才", "date": "2026-01-01", "total_point": 90,
            },
            {
                "girl_url": "https://profiles.invalid/profile.html?uid=a",
                "shop_name": "Alpha", "type": "type-a", "name": "Person A",
                "predict_age": "35才", "date": "2026-02-01", "total_point": 70,
            },
            {
                "girl_url": "https://profiles.invalid/profile.html?uid=b",
                "shop_name": "Beta", "type": "type-b", "name": "Person B",
                "predict_age": None, "date": "2026-03-01", "total_point": 85,
            },
        ]
        source = generate_html(pd.DataFrame(reports), title="関東風俗レポート")
        resources = {
            "https://report.invalid/kanto_final_report.html": ("text/html", source),
            "https://report.invalid/common.css": ("text/css", (ROOT / "docs" / "common.css").read_text(encoding="utf-8")),
            "https://report.invalid/common.js": ("text/javascript", (ROOT / "docs" / "common.js").read_text(encoding="utf-8")),
        }
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with getattr(playwright, engine).launch() as browser:
                    context = browser.new_context(viewport={"width": 390, "height": 844})

                    def respond(route):
                        if route.request.url in resources:
                            mime, body = resources[route.request.url]
                            route.fulfill(content_type=mime, body=body)
                        else:
                            route.abort()

                    context.route("**/*", respond)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto("https://report.invalid/kanto_final_report.html")
                    expect(page).to_have_title("関東風俗レポート")
                    expect(page.locator(".report-entry")).to_have_count(2)
                    expect(page.locator(".article-entry")).to_have_count(3)
                    expect(page.locator(".report-entry").first).to_contain_text("Person B")
                    page.locator("#reportSearch").fill("Person A")
                    expect(page.locator(".report-entry:visible")).to_have_count(1)
                    expect(page.locator(".report-entry:visible")).to_contain_text("35")
                    page.locator("#reportSearch").fill("no matching person")
                    expect(page.locator("#noResults")).to_be_visible()
                    self.assertEqual(errors, [], engine)
                    context.close()

    def test_published_link_and_report_are_consistent(self):
        index = BeautifulSoup((ROOT / "docs" / "index.html").read_text(encoding="utf-8"), "lxml")
        link = index.select_one('.reports a[href="kanto_final_report.html"]')
        self.assertIsNotNone(link)
        soup = BeautifulSoup((ROOT / "docs" / link["href"]).read_text(encoding="utf-8"), "lxml")
        self.assertEqual(soup.title.get_text(), "関東風俗レポート")
        self.assertGreater(len(soup.select(".report-entry")), 0)
        self.assertGreater(len(soup.select(".article-entry")), 0)


if __name__ == "__main__":
    unittest.main()
