"""Exercise actual listing filters and price disclosures offline in both engines."""
import copy
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import shop_prices
import generate_weekly_schedule_report as weekly
from integrations.report_left_merge import build_calendar_html
from ns_senmon.generate_ns_senmon_report import build_html, TOTAL_KEY
from okinilove.render_people_summary import build_people_summary
from tokai_fuzoku.html_render import generate_html

SHOP = "https://www.cityheaven.net/aichi/A2301/A230103/airabugakuen/"
PROFILES = [SHOP + "girlid-123/", SHOP.replace("airabugakuen", "unknown") + "girlid-456/"]


def pages():
    records = [{
        "shop_name": f"Shop{index}", "name": f"Person{index}", "girl_url": profile,
        "cityheaven_url": profile, "date": "2026-10-25", "post_id": index,
        "type": "Type", "title": "Title", "url": f"https://report.invalid/{index}",
        "predict_age": 25, "profile_image_url": None, "total_point": 80,
        "details": {TOTAL_KEY: "80点", "推定年齢": "25歳"},
    } for index, profile in enumerate(PROFILES)]
    people = weekly.rank_people([("東海風俗", "tokai_fuzoku", records)])
    dates = weekly.schedule_dates()
    schedules = {
        profile: {"days": weekly.daily_states(dates, "working", "10:00 - 18:00"),
                  "error": "", "fetched_at": "saved"} for profile in PROFILES
    }
    calendar = pd.DataFrame([{
        "url_key": profile, "URL": profile, "名前": f"Person{index}", "店名": f"Shop{index}",
        "画像URL": "", "evaluations_merged": None, "report_url_merged": None,
        "okinilove_reports": [], "ns_reports": [], "10/25(日)": "10:00~18:00",
    } for index, profile in enumerate(PROFILES)])
    raw_oki = '<html><body><table id="girlsTable"><tbody>' + "".join(
        f'<tr><td>Shop{index}</td><td><a href="https://okinilove.com/blog-entry-{index}.html">Report</a></td>'
        f'<td><a href="{profile}">Person{index}</a></td><td>写真通り</td><td>★★★★★</td></tr>'
        for index, profile in enumerate(PROFILES)
    ) + "</tbody></table></body></html>"
    return {
        "weekly": weekly.render_html(people, dates, schedules, {}),
        "ns": build_html(records),
        "okinilove": str(build_people_summary(raw_oki, fetch_missing_industries=False)[0]),
        "tokai": generate_html(pd.DataFrame(records)),
        "calendar": build_calendar_html(calendar),
    }, people


class ShopPriceBrowserTests(unittest.TestCase):
    def test_native_filter_composition_unknown_empty_reset_and_disclosure(self):
        entry = shop_prices.parse_system_html(
            (ROOT / "tests" / "fixtures" / "shop_prices_airabugakuen.html").read_text(encoding="utf-8")
        )
        cache = {"version": 1, "shops": {SHOP: entry}}
        with patch.object(shop_prices, "load_cache", return_value=cache):
            generated, people = pages()
        before = copy.deepcopy(people)
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch()
                try:
                    for name, markup in generated.items():
                        for width in (1440, 390):
                            with self.subTest(engine=engine, page=name, width=width):
                                context = browser.new_context(viewport={"width": width, "height": 900})
                                page = context.new_page()
                                errors = []
                                page.on("pageerror", lambda error: errors.append(str(error)))

                                def respond(route):
                                    filename = route.request.url.rsplit("/", 1)[-1]
                                    if filename == "test.html":
                                        route.fulfill(content_type="text/html", body=markup)
                                    elif filename in {"common.css", "common.js", "shop_prices.css", "shop_prices.js",
                                                      "weekly_schedule.css", "weekly_schedule.js"}:
                                        route.fulfill(
                                            content_type="text/css" if filename.endswith(".css") else "text/javascript",
                                            body=(ROOT / "docs" / filename).read_text(encoding="utf-8"),
                                        )
                                    else:
                                        route.abort()

                                context.route("**/*", respond)
                                page.goto("https://prices.invalid/test.html")
                                rows = page.locator("[data-shop-price-status]")
                                expect(rows.locator("visible=true")).to_have_count(2)
                                page.locator("#shopPriceMin").fill("18000")
                                page.locator("#shopPriceMax").fill("18000")
                                expect(rows.locator("visible=true")).to_have_count(1)
                                known = rows.filter(has=page.locator('[href="' + PROFILES[0] + '"]')).first
                                price_details = known.locator(".shop-price-evidence").first
                                if name == "tokai":
                                    known.locator(":scope > summary").click()
                                price_details.locator("summary").click()
                                expect(price_details).to_have_attribute("open", "")
                                expect(price_details).to_contain_text("27,000円")
                                if name in {"ns", "okinilove"}:
                                    expect(known).not_to_have_class("person-row open")
                                top = price_details.locator("summary").bounding_box()["y"]
                                price_details.locator("summary").click()
                                if name == "weekly":
                                    self.assertLess(abs(price_details.locator("summary").bounding_box()["y"] - top), 2)
                                page.locator("#shopPriceMin").fill("18001")
                                expect(rows.locator("visible=true")).to_have_count(0)
                                expect(page.locator("#shopPriceError")).to_be_visible()
                                page.locator("#shopPriceMode").select_option("unknown")
                                expect(rows.locator("visible=true")).to_have_count(1)
                                expect(page.locator("#shopPriceMin")).to_be_disabled()
                                page.locator("#shopPriceMode").select_option("")
                                page.locator("#shopPriceMin").fill("")
                                page.locator("#shopPriceMax").fill("17999")
                                expect(rows.locator("visible=true")).to_have_count(0)
                                # Clearing numeric conditions restores the native population, not an independently hidden copy.
                                page.locator("#shopPriceMax").fill("")
                                expect(rows.locator("visible=true")).to_have_count(2)
                                if name == "weekly":
                                    page.locator("#ageFilter").select_option("over30")
                                    expect(rows.locator("visible=true")).to_have_count(0)
                                    page.locator("#shopPriceMin").fill("0")
                                    expect(rows.locator("visible=true")).to_have_count(0)
                                    page.locator("#ageFilter").select_option("")
                                elif name in {"ns", "okinilove"}:
                                    page.locator("#shop").select_option("Shop1")
                                    page.locator("#shopPriceMax").fill("18000")
                                    expect(rows.locator("visible=true")).to_have_count(0)
                                    page.locator("#reset-filters").click()
                                    expect(page.locator("#shopPriceMax")).to_have_value("")
                                    expect(rows.locator("visible=true")).to_have_count(2)
                                elif name in {"tokai", "calendar"}:
                                    page.locator("#shopFilter").select_option("Shop1")
                                    page.locator("#shopPriceMax").fill("18000")
                                    expect(rows.locator("visible=true")).to_have_count(0)
                                    page.locator("#shopFilter").select_option("")
                                    page.locator("#shopPriceMax").fill("")
                                self.assertFalse(errors, errors)
                                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                                artifacts = os.environ.get("SHOP_PRICE_ARTIFACTS")
                                if artifacts:
                                    path = Path(artifacts)
                                    path.mkdir(parents=True, exist_ok=True)
                                    page.screenshot(path=str(path / f"prices-{name}-{engine}-{width}.png"))
                                context.close()
                finally:
                    browser.close()
        self.assertEqual(people, before)


if __name__ == "__main__":
    unittest.main()
