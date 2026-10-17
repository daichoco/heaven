"""Offline calendar disclosure checks in Chromium and WebKit."""
import sys
import unittest
from pathlib import Path

import pandas as pd
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from integrations.report_left_merge import build_calendar_html


class CalendarBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browsers = {}
        for engine in ("chromium", "webkit"):
            browser = getattr(cls.playwright, engine).launch()
            cls.addClassCleanup(browser.close)
            cls.browsers[engine] = browser
        cls.long_text = "ReportBody" * 160
        cls.generated = build_calendar_html(pd.DataFrame([{
            "url_key": "https://profile.invalid/person",
            "URL": "https://profile.invalid/person", "名前": "Person", "店名": "Shop",
            "画像URL": "https://profile.invalid/image",
            "evaluations_merged": {"score": cls.long_text},
            "report_url_merged": "https://report.invalid/tokai",
            "okinilove_reports": [{
                "evaluations": {"score": "Okinilove"}, "report_text": cls.long_text,
                "report_url": "https://report.invalid/okinilove",
            }],
            "ns_reports": [{
                "date": "2026-01-01", "url": "https://report.invalid/ns",
                "scores": {"score": "NS"}, "comments": {"body": cls.long_text},
            }],
            **{f"10/{day}(土)": "10:00~18:00" for day in range(17, 24)},
        }]))
        cls.pages = {
            "generated": cls.generated,
            "published": (ROOT / "docs" / "calendar_report.html").read_text(encoding="utf-8"),
        }

    def open_page(self, browser, source, width, touch, common_js=True):
        context = browser.new_context(
            viewport={"width": width, "height": 900}, is_mobile=touch, has_touch=touch,
        )
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        resources = {
            "calendar.html": ("text/html", source),
            "common.css": ("text/css", (ROOT / "docs" / "common.css").read_text(encoding="utf-8")),
        }
        if common_js:
            resources["common.js"] = (
                "text/javascript", (ROOT / "docs" / "common.js").read_text(encoding="utf-8"),
            )

        def respond(route):
            resource = resources.get(route.request.url.rsplit("/", 1)[-1])
            if resource:
                mime, body = resource
                route.fulfill(content_type=mime, body=body)
            else:
                route.abort()

        context.route("**/*", respond)
        page.goto("https://calendar.invalid/calendar.html")
        page.locator("#workingOnly").uncheck()
        page.locator('input[name="dateMode"][value="weekend"]').check()
        return page, errors

    def activate(self, button, touch):
        if touch:
            button.tap()
        else:
            button.click()

    def test_disclosure_touch_click_wrapping_and_filter_reset(self):
        for engine, browser in self.browsers.items():
            for name, source in self.pages.items():
                for width, touch in ((390, True), (820, True), (1024, True), (1366, True), (1440, False)):
                    with self.subTest(engine=engine, page=name, width=width):
                        page, errors = self.open_page(browser, source, width, touch)
                        for source_name in ("merged", "okinilove", "ns"):
                            button = page.locator(
                                f'tr[data-report] button[aria-controls^="review-{source_name}-"]'
                            ).first
                            row = page.locator("#" + button.get_attribute("aria-controls"))
                            expect(row).to_be_hidden()
                            self.activate(button, touch)
                            expect(row).to_be_visible()
                            expect(button).to_have_attribute("aria-expanded", "true")
                            expected_display = "block" if width <= 1100 or touch else "table-row"
                            self.assertEqual(row.evaluate("el => getComputedStyle(el).display"),
                                             expected_display)
                            colspan = row.locator("td").get_attribute("colspan")
                            self.assertEqual(int(colspan), page.locator("thead th").count())
                            self.assertTrue(row.locator("td").evaluate("""cell => {
                                const wrapper = cell.closest('table').parentElement;
                                return cell.getBoundingClientRect().width <= wrapper.clientWidth + 2
                                    && cell.scrollWidth <= cell.clientWidth + 2;
                            }"""))
                            self.assertTrue(row.evaluate("el => el.getBoundingClientRect().height > 20"))
                            self.activate(button, touch)
                            expect(row).to_be_hidden()
                            expect(button).to_have_attribute("aria-expanded", "false")
                        self.activate(button, touch)
                        page.locator("#workingOnly").check()
                        expect(row).to_be_hidden()
                        expect(button).to_have_attribute("aria-expanded", "false")
                        self.assertEqual(errors, [])

    def test_open_review_survives_phone_tablet_resize_and_blocked_common_script(self):
        for engine, browser in self.browsers.items():
            for common_js in (True, False):
                with self.subTest(engine=engine, common_js=common_js):
                    page, errors = self.open_page(browser, self.generated, 390, True, common_js)
                    button = page.locator('button[aria-controls^="review-okinilove-"]')
                    row = page.locator("#" + button.get_attribute("aria-controls"))
                    button.tap()
                    for width in (820, 1024, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        expect(row).to_be_visible()
                        expect(button).to_have_attribute("aria-expanded", "true")
                        self.assertEqual(row.evaluate("el => getComputedStyle(el).display"), "block")
                        self.assertIn(self.long_text, row.text_content())
                    button.tap()
                    expect(row).to_be_hidden()
                    self.assertEqual(errors, [])

    def test_open_review_tracks_desktop_responsive_breakpoint(self):
        for engine, browser in self.browsers.items():
            with self.subTest(engine=engine):
                page, errors = self.open_page(browser, self.generated, 1440, False)
                button = page.locator('button[aria-controls^="review-merged-"]')
                row = page.locator("#" + button.get_attribute("aria-controls"))
                button.click()
                for width in (390, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    expect(row).to_be_visible()
                    self.assertEqual(row.evaluate("el => getComputedStyle(el).display"),
                                     "block" if width <= 1100 else "table-row")
                self.assertEqual(errors, [])

    def test_love_missile_body_is_reachable_by_touch(self):
        for engine, browser in self.browsers.items():
            for width in (390, 820, 1024, 1366):
                with self.subTest(engine=engine, width=width):
                    page, errors = self.open_page(browser, self.pages["published"], width, True)
                    heading = page.locator("tr[data-report]").filter(
                        has=page.locator('.shop-cell > a[href*="girlid-14231717"]')
                    )
                    button = heading.locator('button[aria-controls^="review-merged-"]')
                    row = page.locator("#" + button.get_attribute("aria-controls"))
                    button.tap()
                    expect(row).to_be_visible()
                    self.assertEqual(row.locator("ul").count(), 4)
                    last_comment = row.locator("li").last
                    last_comment.scroll_into_view_if_needed()
                    expect(last_comment).to_be_in_viewport()
                    self.assertTrue(last_comment.evaluate("""el => {
                        const rect = el.getBoundingClientRect();
                        return rect.left >= 0 && rect.right <= innerWidth + 1;
                    }"""))
                    button.tap()
                    expect(row).to_be_hidden()
                    self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
