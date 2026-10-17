"""Offline real-browser regressions; install Chromium and WebKit with Playwright."""

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tokai_fuzoku.html_render import generate_html


class TokaiBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browsers = {}
        for engine in ("chromium", "webkit"):
            browser = getattr(cls.playwright, engine).launch()
            cls.addClassCleanup(browser.close)
            cls.browsers[engine] = browser
        reports = [
            {
                "girl_url": "https://profiles.invalid/a", "shop_name": "Alpha",
                "type": "type-a", "name": "Person A", "date": "2026-01-01",
                "total_point": 90, "play_score": 80, "face_score": 70,
                "style_score": 60, "repeat": "返す",
            },
            {
                "girl_url": "https://profiles.invalid/a", "shop_name": "Beta",
                "type": "type-b", "name": "Person A", "date": "2026-02-01",
                "total_point": 50, "play_score": 60, "face_score": 50,
                "style_score": 40, "repeat": "返さない",
            },
            {
                "girl_url": "https://profiles.invalid/b", "shop_name": "Beta",
                "type": "type-b", "name": "Person B", "date": "2026-03-01",
                "total_point": 85, "play_score": 90, "face_score": 90,
                "style_score": 90, "repeat": "返す",
            },
            {
                "girl_url": "https://profiles.invalid/c", "shop_name": "Gamma",
                "type": "type-c", "name": "Person C", "date": None,
                "total_point": None,
            },
        ]
        with patch("tokai_fuzoku.html_render.load_cache", return_value={}):
            generated = generate_html(pd.DataFrame(reports))
        cls.pages = {
            "generated": generated,
            "published": (ROOT / "docs" / "tokai_final_report.html").read_text(encoding="utf-8"),
        }

    def open_page(self, browser, source, width=390, height=844, common_js="normal"):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            is_mobile=width <= 844, has_touch=width <= 844,
        )
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        resources = {
            "https://report.invalid/tokai_final_report.html": ("text/html", source),
            "https://report.invalid/away.html": ("text/html", "<p>History destination</p>"),
        }
        for name, mime in (("common.css", "text/css"), ("common.js", "text/javascript")):
            resources[f"https://report.invalid/{name}"] = (
                mime, (ROOT / "docs" / name).read_text(encoding="utf-8"),
            )
        pending_scripts = []

        def respond(route):
            url = route.request.url
            if url == "https://report.invalid/common.js":
                if common_js == "blocked":
                    route.abort()
                    return
                if common_js == "delayed":
                    pending_scripts.append(route)
                    return
            if url in resources:
                mime, body = resources[url]
                route.fulfill(content_type=mime, body=body)
            else:
                route.abort()

        context.route("**/*", respond)
        page.goto(
            "https://report.invalid/tokai_final_report.html",
            wait_until="commit" if common_js == "delayed" else "load",
        )
        return page, errors, pending_scripts

    def expected_order(self, source, mode="recommended"):
        keys = (
            ("total", "play", "face", "style", "date") if mode == "recommended"
            else ("date",) if mode == "date" else (mode, "date")
        )
        entries = BeautifulSoup(source, "html.parser").select(".report-entry")

        def rank(entry):
            values = []
            for key in keys:
                raw = entry.get(f"data-{key}", "")
                value = float(raw) if raw.strip() else math.nan
                values.append(-value if math.isfinite(value) else math.inf)
            return tuple(values)

        return [entry["data-search"] for entry in sorted(entries, key=rank)]

    def assert_toolbar_accessible(self, page, mobile=True):
        for selector in ("#reportSearch", "#reportSort", "#filterToggle") if mobile else (
            "#reportSearch", "#reportSort", "#shopFilter",
        ):
            control = page.locator(selector)
            expect(control).to_be_visible()
            expect(control).to_be_enabled()
            self.assertTrue(control.evaluate("""element => {
                const rect = element.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                return x >= 0 && x < innerWidth && y >= 0 && y < innerHeight
                    && element.contains(document.elementFromPoint(x, y));
            }"""), selector)

    def assert_initial_results(self, page, source):
        soup = BeautifulSoup(source, "html.parser")
        people = len(soup.select(".report-entry"))
        articles = len(soup.select(".article-entry"))
        expect(page.locator("#filterCount")).to_have_text(
            f"{people} 人 / {articles} 件（全 {people} 人 / {articles} 件）"
        )
        self.assertEqual(page.locator(".report-entry:not([hidden])").count(), people)
        self.assertEqual(page.locator(".article-entry:not([hidden])").count(), articles)
        self.assertEqual(page.locator(".report-entry").evaluate_all(
            "entries => entries.map(entry => entry.dataset.search)"
        ), self.expected_order(source))
        expect(page.locator("#noResults")).to_be_hidden()

    def test_mobile_initial_toggle_empty_search_filter_reset(self):
        for engine, browser in self.browsers.items():
            for name, source in self.pages.items():
                with self.subTest(engine=engine, page=name):
                    page, errors, _ = self.open_page(browser, source)
                    self.assert_toolbar_accessible(page)
                    self.assert_initial_results(page, source)
                    expect(page.locator("#filterBar")).to_be_visible()
                    for selector in (
                        "#shopFilter", "#typeFilter", "#shopTypeFilter",
                        "#repeatFilter", "#scoreFilter",
                    ):
                        expect(page.locator(selector)).to_be_visible()
                        expect(page.locator(selector)).to_be_enabled()
                    expect(page.locator("#filterToggle")).to_have_attribute("aria-expanded", "true")
                    for _ in range(2):
                        page.locator("#filterToggle").click()
                        expect(page.locator("#filterToggle")).to_have_attribute("aria-expanded", "false")
                        expect(page.locator("#filterBar")).to_be_hidden()
                        self.assert_toolbar_accessible(page)
                        page.locator("#filterToggle").click()
                        expect(page.locator("#filterToggle")).to_have_attribute("aria-expanded", "true")
                        expect(page.locator("#shopFilter")).to_be_visible()
                    page.locator("#reportSearch").fill(" ")
                    page.locator("#reportSearch").fill("")
                    self.assert_initial_results(page, source)
                    page.locator(".shop-filter-search").fill(" ")
                    page.locator(".shop-filter-search").fill("")
                    self.assert_initial_results(page, source)
                    first_article = BeautifulSoup(source, "html.parser").select_one(".article-entry")
                    shop = first_article["data-shop"]
                    page.locator("#shopFilter").select_option(shop)
                    expect(page.locator("#filterToggle")).to_have_text("絞り込み (1)")
                    expected_articles = BeautifulSoup(source, "html.parser").select(".article-entry")
                    expected_matches = sum(a["data-shop"] == shop for a in expected_articles)
                    self.assertEqual(
                        page.locator(".article-entry:not([hidden])").count(), expected_matches,
                    )
                    page.locator("#filterToggle").click()
                    page.locator("#reportSort").select_option("play")
                    expect(page.locator("#shopFilter")).to_have_value(shop)
                    self.assertEqual(
                        page.locator(".article-entry:not([hidden])").count(), expected_matches,
                    )
                    page.locator("#reportSearch").fill("nonexistent-unique-person")
                    expect(page.locator("#noResults")).to_be_visible()
                    expect(page.locator(".report-entry:not([hidden])")).to_have_count(0)
                    page.locator("#filterToggle").click()
                    page.get_by_role("button", name="条件をリセット").click()
                    expect(page.locator("#reportSort")).to_have_value("play")
                    page.locator("#reportSort").select_option("recommended")
                    self.assert_initial_results(page, source)
                    expect(page.locator("#filterToggle")).to_have_text("絞り込み")
                    self.assertEqual(errors, [])

    def test_desktop_initial_sort_and_expanded_state(self):
        for engine, browser in self.browsers.items():
            for name, source in self.pages.items():
                with self.subTest(engine=engine, page=name):
                    page, errors, _ = self.open_page(browser, source, 1280, 900)
                    self.assert_toolbar_accessible(page, mobile=False)
                    self.assert_initial_results(page, source)
                    expect(page.locator("#filterBar")).to_be_visible()
                    person = page.locator(".report-entry").first
                    person.locator(".person-report-count").click()
                    expect(person).to_have_attribute("open", "")
                    person.locator(".article-date").first.click()
                    page.evaluate("""() => {
                        window.testPerson = document.querySelector(".report-entry");
                        window.testArticle = testPerson.querySelector(".article-entry");
                    }""")
                    for mode in ("total", "play", "face", "style", "date", "recommended"):
                        page.locator("#reportSort").select_option(mode)
                        self.assertEqual(page.locator(".report-entry").evaluate_all(
                            "entries => entries.map(entry => entry.dataset.search)"
                        ), self.expected_order(source, mode))
                        self.assertTrue(page.evaluate("testPerson.open && testArticle.open"))
                    self.assertEqual(errors, [])

    def test_mobile_scroll_reload_and_history(self):
        source = self.pages["published"]
        for engine, browser in self.browsers.items():
            with self.subTest(engine=engine):
                page, errors, _ = self.open_page(browser, source)
                for action in ("scroll", "reload", "history"):
                    page.evaluate("scrollTo(0, 2500)")
                    if action == "reload":
                        page.reload()
                    elif action == "history":
                        page.goto("https://report.invalid/away.html")
                        page.go_back()
                    self.assert_toolbar_accessible(page)
                    self.assert_initial_results(page, source)
                self.assertEqual(errors, [])

    def test_blocked_and_delayed_common_script(self):
        source = self.pages["published"]
        for engine, browser in self.browsers.items():
            for mode in ("blocked", "delayed"):
                with self.subTest(engine=engine, script=mode):
                    page, errors, pending = self.open_page(browser, source, common_js=mode)
                    expect(page.locator("#filterToggle")).to_be_visible()
                    self.assert_toolbar_accessible(page)
                    if mode == "delayed":
                        # The page's initializer currently waits for deferred common.js.
                        expect(page.locator("#filterCount")).to_have_text("")
                        page.locator("#filterToggle").click()
                        expect(page.locator("#filterBar")).to_be_hidden()
                        page.locator("#filterToggle").click()
                        expect(page.locator("#filterBar")).to_be_visible()
                        self.assertEqual(len(pending), 1)
                        pending[0].fulfill(
                            content_type="text/javascript",
                            body=(ROOT / "docs" / "common.js").read_text(encoding="utf-8"),
                        )
                        page.wait_for_load_state("load")
                    self.assert_initial_results(page, source)
                    self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
