"""Pages-like HTTP delivery: global filtering, bounded DOM, and lazy review races."""
import functools
import json
import shutil
import sys
import threading
import unittest
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bs4 import BeautifulSoup
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import generate_weekly_schedule_report as weekly
import shop_prices


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class WeeklyPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.now = datetime(2026, 10, 19, 12, tzinfo=weekly.JST)
        cls.dates = weekly.schedule_dates(cls.now)
        records = [{
            "url": f"https://example.invalid/article-{i}", "name": f"Person{i:03}",
            "girl_url": f"https://www.cityheaven.net/aichi/A2301/shop/girlid-{i}/",
            "shop_name": f"Shop{i % 3}", "predict_age": 25 if i % 2 else 35,
            "total_point": 100 - i / 2, "total_comment": f"Original body {i:03}\nsecond line",
        } for i in range(125)]
        cls.people = weekly.rank_people([("東海風俗", "tokai_fuzoku", records)])
        schedules = {}
        for i, person in enumerate(cls.people):
            days = weekly.daily_states(cls.dates, "unpublished")
            days[0 if i % 2 else 5] = {
                "date": cls.dates[0 if i % 2 else 5], "state": "working", "text": "10:00 - 18:00",
            }
            schedules[person["profile_url"]] = {"days": days, "error": "", "fetched_at": "saved"}
        cls.assets = {}
        with patch.object(shop_prices, "load_cache", return_value={"shops": {}}):
            cls.markup = weekly.render_html(
                cls.people, cls.dates, schedules, {}, cls.now, assets=cls.assets,
            )
        (cls.root / "weekly_schedule.html").write_text(cls.markup, encoding="utf-8")
        weekly.write_weekly_assets(cls.assets, cls.root / "weekly_schedule.html")
        for name in ("weekly_schedule.js", "weekly_schedule.css", "common.js", "common.css",
                     "shop_prices.js", "shop_prices.css"):
            shutil.copyfile(ROOT / "docs" / name, cls.root / name)
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(cls.root)),
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/weekly_schedule.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def test_output_is_bounded_stable_and_complete(self):
        page = BeautifulSoup(self.markup, "html.parser")
        self.assertEqual(len(page.select(".person-row")), 50)
        self.assertFalse(page.select(".saved-report, .review-row"))
        self.assertNotIn("Original body", self.markup)
        index = json.loads(page.select_one("#weeklyIndex").string)["people"]
        self.assertEqual(len(index), 125)
        self.assertEqual(len({item["id"] for item in index}), 125)
        rows = {}
        for name, payload in self.assets.items():
            if name.startswith("list-"):
                rows.update(json.loads(payload)["rows"])
        self.assertEqual(list(rows), [item["id"] for item in index])
        for i, item in enumerate(index):
            row = BeautifulSoup(rows[item["id"]], "html.parser").tr
            payload = json.loads(self.assets[row["data-body-url"].split("/")[-1]])
            self.assertEqual(payload["id"], item["id"])
            self.assertEqual(payload["sources"]["2"], weekly.render_reports(self.people[i], "東海風俗"))
        reordered = {}
        with patch.object(shop_prices, "load_cache", return_value={"shops": {}}):
            markup = weekly.render_html(list(reversed(self.people)), self.dates, {}, {},
                                        self.now, assets=reordered)
        new_index = json.loads(BeautifulSoup(markup, "html.parser").select_one("#weeklyIndex").string)["people"]
        self.assertEqual({item["id"] for item in index}, {item["id"] for item in new_index})

    def test_asset_cleanup_is_scoped_to_obsolete_generator_files(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "weekly.html"
            current = {"list-" + "a" * 24 + ".json": '{"rows":{}}'}
            weekly.write_weekly_assets(current, output)
            folder = output.parent / weekly.ASSET_DIRECTORY
            obsolete = folder / ("review-" + "b" * 24 + ".json")
            obsolete.write_text("old", encoding="utf-8")
            other = folder / "user-evidence.json"
            other.write_text("keep", encoding="utf-8")
            weekly.prune_weekly_assets(current, output)
            self.assertFalse(obsolete.exists())
            self.assertTrue(other.exists())
            self.assertTrue((folder / next(iter(current))).exists())

    def test_http_global_filters_paging_lazy_loading_retry_and_races(self):
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch()
                try:
                    for width in (1440, 390):
                        with self.subTest(engine=engine, width=width):
                            context = browser.new_context(viewport={"width": width, "height": 900})
                            page = context.new_page()
                            page.clock.install(time=self.now)
                            errors = []
                            requests = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.on("request", lambda request: requests.append(request.url))
                            page.goto(self.url)
                            expect(page.locator(".person-row")).to_have_count(50)
                            self.assertFalse(any("/review-" in url or "/list-" in url for url in requests))
                            expect(page.locator(".review-row")).to_have_count(0)
                            page.locator("#weeklyNext").click()
                            expect(page.locator(".person-name").first).to_have_text("Person050")
                            expect(page.locator(".person-row")).to_have_count(50)
                            page.locator("#weeklyNext").click()
                            expect(page.locator(".person-row")).to_have_count(25)
                            expect(page.locator(".person-name").first).to_have_text("Person100")
                            page.locator("#weeklySearch").fill("Person123")
                            expect(page.locator(".person-row")).to_have_count(1)
                            expect(page.locator(".person-name")).to_have_text("Person123")
                            page.select_option("#ageFilter", "over30")
                            expect(page.locator("#noResults")).to_be_visible()
                            page.select_option("#ageFilter", "under30")
                            expect(page.locator(".person-row")).to_have_count(1)
                            page.select_option("#shopPriceBand", "15000-20000")
                            expect(page.locator(".person-row")).to_have_count(0)
                            page.select_option("#shopPriceBand", "unknown")
                            expect(page.locator(".person-row")).to_have_count(1)
                            page.select_option("#scheduleFilter", "weekend")
                            expect(page.locator(".person-row")).to_have_count(0)
                            page.select_option("#scheduleFilter", "today")
                            expect(page.locator(".person-row")).to_have_count(1)
                            page.select_option("#daySelect", self.dates[3])
                            expect(page.locator(".person-row")).to_have_count(0)
                            page.select_option("#scheduleFilter", "all")
                            expect(page.locator(".person-row")).to_have_count(1)
                            button = page.locator(".review-toggle")
                            page.route("**/review-*.json", lambda route: route.fulfill(status=503))
                            button.click()
                            expect(page.locator(".review-row [role=alert]")).to_contain_text("HTTP 503")
                            page.unroute("**/review-*.json")
                            page.get_by_role("button", name="本文を再試行").click()
                            expect(page.locator(".saved-report")).to_have_count(1)
                            expect(page.locator(".saved-report")).to_contain_text("Original body 123")
                            request_count = len([url for url in requests if "/review-" in url])
                            button.click()
                            button.click()
                            expect(page.locator(".saved-report")).to_have_count(1)
                            self.assertEqual(len([url for url in requests if "/review-" in url]), request_count)
                            page.locator("#weeklyReset").click()
                            expect(page.locator(".person-row")).to_have_count(50)
                            expect(page.locator(".review-row")).to_have_count(0)
                            expect(page.locator("#shopPriceBand")).to_have_value("")
                            expect(page.locator("#ageFilter")).to_have_value("")
                            held = []
                            page.route("**/review-*.json", lambda route: held.append(route))
                            page.locator(".review-toggle").first.click()
                            expect(page.locator(".review-row [aria-busy=true]")).to_have_count(1)
                            # A rapid close/reopen and page change cannot insert another person's body.
                            page.locator(".review-toggle").first.click()
                            page.locator(".review-toggle").first.click()
                            page.locator("#weeklyNext").click()
                            expect(page.locator(".person-name").first).to_have_text("Person050")
                            for route in held:
                                route.continue_()
                            page.unroute("**/review-*.json")
                            page.wait_for_load_state("networkidle")
                            expect(page.locator(".review-row")).to_have_count(0)
                            # A failed listing is explicit and can be retried.
                            page.goto(self.url)
                            page.route("**/list-*.json", lambda route: route.fulfill(status=503))
                            page.locator("#weeklyNext").click()
                            expect(page.locator("#weeklyLoadStatus")).to_contain_text("HTTP 503")
                            expect(page.locator(".person-row")).to_have_count(0)
                            page.unroute("**/list-*.json")
                            page.locator("#weeklyRetry").click()
                            expect(page.locator(".person-name").first).to_have_text("Person050")
                            self.assertFalse(errors)
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                            context.close()
                finally:
                    browser.close()

    def test_file_url_preserves_initial_rows_and_explains_http(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto((self.root / "weekly_schedule.html").as_uri())
                expect(page.locator("#fileNotice")).to_be_visible()
                expect(page.locator(".person-row")).to_have_count(50)
                page.locator(".review-toggle").first.click()
                expect(page.locator(".review-row [role=alert]")).to_contain_text("HTTP")
                page.locator("#weeklyNext").click()
                expect(page.locator("#weeklyLoadStatus")).to_contain_text("HTTP")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
