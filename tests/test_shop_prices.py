import copy
import io
import json
import sys
import unittest
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "tokai_fuzoku"))
import shop_prices as prices
from okinilove.render_people_summary import _extract_rows

SHOP = "https://www.cityheaven.net/aichi/A2301/A230103/airabugakuen/"
PROFILE = SHOP + "girlid-123/"
FIXTURE = ROOT / "tests" / "fixtures" / "shop_prices_airabugakuen.html"


def table(rows, title="通常コース"):
    body = "".join(
        f'<tr><td class="feename">{label}</td><td class="feename2">{amount}</td></tr>'
        for label, amount in rows
    )
    return f'<table class="feesmart"><tr><td class="feetitle">{title}</td></tr>{body}</table>'


def parse(rows, title="通常コース", extra=""):
    return prices.parse_system_html(f'<div id="shopcontent">{table(rows, title)}{extra}</div>')


def sample_cache():
    entry = prices.parse_system_html(FIXTURE.read_text(encoding="utf-8"))
    entry.update(source_url=SHOP + "system/", checked_at="2026-10-25T00:00:00+09:00")
    return {"version": 1, "shops": {SHOP: entry}}


class PriceParserTests(unittest.TestCase):
    def test_cached_five_shop_evidence_and_supported_dom_rows_agree(self):
        fixtures = json.loads((FIXTURE.parent / "shop_prices_cached_rows.json").read_text(encoding="utf-8"))
        expected = {
            "alamode": (90, 14000, 14000 * 60 / 90),
            "alicejogakuin_n": (100, 29000, 17400),
            "alicejogakuin_nayabashi": (90, 27000, 18000),
        }
        for slug, fixture in fixtures.items():
            with self.subTest(shop=slug):
                entry = prices.empty_result("unknown", "old parser")
                entry.update(checked_at="2026-10-25T00:00:00+00:00", source_kind="http")
                entry["candidates"] = [
                    {key: fixture[key] for key in ("course_name", "section", "conditions")}
                    | {"label": label, "price_text": amount, "minutes": None, "yen": None,
                       "eligible": False, "reason": "old parser"}
                    for label, amount in fixture["rows"]
                ]
                before = copy.deepcopy(entry)
                result = prices.reprocess_cached_entry(entry)
                self.assertEqual(entry, before)
                self.assertEqual(result["checked_at"], entry["checked_at"])
                self.assertIn("reprocessed_at", result)
                if slug in expected:
                    minutes, yen, converted = expected[slug]
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["selected"]["minutes"], minutes)
                    self.assertEqual(result["selected"]["yen"], yen)
                    self.assertAlmostEqual(result["selected"]["yen_per_60"], converted)
                    self.assertEqual(result["tax"], "unknown")
                    self.assertIn(fixture["conditions"], [fee["text"] for fee in result["additional_fees"]])
                    markup = table(fixture["rows"], escape(fixture["course_name"])).replace(
                        "</table>", '<tr><td colspan="2">' + escape(fixture["conditions"]) + "</td></tr></table>",
                    )
                    parsed = prices.parse_system_html('<div id="shopcontent">' + markup + '</div>')
                    self.assertEqual(parsed["status"], "ok")
                    self.assertEqual(parsed["selected"]["yen_per_60"], result["selected"]["yen_per_60"])
                else:
                    self.assertEqual(result["status"], "unknown")
                    self.assertIsNone(result["selected"])
                    self.assertEqual([c["price_text"] for c in result["candidates"]],
                                     [amount for _, amount in fixture["rows"]])

    def test_fee_notes_do_not_authorize_promotions_special_courses_or_ranges(self):
        for title, label, amount, conditions in [
            ("システム料金 初回限定", "90分", "1,000円", ""),
            ("通常料金", "90分", "1,000円", "WEB予約限定"),
            ("基本料金", "80分 ★オススメ 新人限定", "1,000円", ""),
            ("通常料金", "90分", "10,000円〜", "別途指名料"),
            ("システム料金", "90分", "基本料金 12000円 入場料金 2000円 14000円", ""),
            ("通常料金", "90分", "1,000円", "団体割をご利用の場合"),
        ]:
            with self.subTest(title=title, label=label, conditions=conditions):
                markup = table([(label, amount)], title).replace(
                    "</table>", '<tr><td colspan="2">' + conditions + "</td></tr></table>",
                )
                self.assertIsNone(prices.parse_system_html('<div id="shopcontent">' + markup + "</div>")["selected"])
        for title in ("延長", "特殊コース", "クーポン", "広告"):
            result = parse([("90分", "27,000円")], extra=table([("90分", "1,000円")], title))
            self.assertEqual(result["selected"]["yen"], 27000)

    def test_provided_menu_only_normal_courses_and_separate_fees(self):
        result = sample_cache()["shops"][SHOP]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selected"]["minutes"], 90)
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertEqual(result["selected"]["yen_per_60"], 18000)
        self.assertEqual(result["tax"], "included")
        eligible = [item for item in result["candidates"] if item["eligible"]]
        self.assertEqual([item["minutes"] for item in eligible], [60, 75, 90, 120, 150, 180])
        self.assertEqual([item["yen"] for item in eligible], [18000, 22000, 27000, 36000, 47000, 56000])
        self.assertTrue(any(item["text"] == "2,000円" for item in result["additional_fees"]))
        self.assertTrue(any(item["label"] == "1,000円エリア" for item in result["additional_fees"]))
        self.assertFalse(any(item["eligible"] for item in result["candidates"] if item["price_text"] in {"4,000ptまで", "21,000円", "7,000円"}))

    def test_nearest_duration_longer_tie_and_unrounded_filter_amount(self):
        result = parse([("80分", "20,000円"), ("100分", "30,000円")])
        self.assertEqual(result["selected"]["minutes"], 100)
        self.assertEqual(result["selected"]["yen_per_60"], 18000)
        self.assertEqual(parse([("75分", "22,000円")])["selected"]["yen_per_60"], 17600)
        self.assertAlmostEqual(parse([("70分", "20,000円")])["selected"]["yen_per_60"], 17142.85714285714)

    def test_ambiguities_and_unknown_formats_never_become_cheap(self):
        cases = [
            parse([("90分", "20,000〜30,000円")]),
            parse([("90分", "20,000円〜")]),
            parse([("90分", "20,000円"), ("90分", "30,000円")]),
            parse([("90分", "20,000円")], extra=table([("120分", "30,000円")], "基本料金")),
            parse([("90分", "20,000円")], extra="<p>税込と税別</p>"),
            parse([("90分", "20,000円")], title="初回限定 通常コース"),
            prices.parse_system_html('<div id="shopcontent"><img src="menu.png" alt="90分 27000円"></div>'),
            prices.parse_system_html("<table><tr><td>90分</td><td>27000円</td></tr></table>"),
        ]
        for result in cases:
            with self.subTest(reason=result["reason"]):
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["selected"])
                self.assertTrue(result["reason"])
        for title in ["通常コース 初回限定", "通常料金 クーポン", "基本料金 VIP", "通常コース 延長"]:
            self.assertIsNone(parse([("90分", "1000円")], title=title)["selected"])

    def test_tax_not_silently_added(self):
        result = parse([("90分", "27,000円")], extra="<p>税別</p>")
        self.assertEqual(result["tax"], "excluded")
        self.assertEqual(result["selected"]["yen_per_60"], 18000)
        self.assertEqual(parse([("90分", "27,000円")])["tax"], "unknown")
        markup = table([("90分", "27,000円")]).replace(
            "</table>", '<tr><td colspan="2">ホテル代は別途必須です</td></tr></table>',
        )
        result = prices.parse_system_html(f'<div id="shopcontent">{markup}</div>')
        self.assertEqual(result["selected"]["yen_per_60"], 18000)
        self.assertIn("ホテル代", result["additional_fees"][0]["text"])

    def test_safe_normalization(self):
        for url in [SHOP, SHOP + "system/?lo=1", PROFILE, PROFILE + "diary/?lo=1",
                    PROFILE.replace("https://www.", "http://")]:
            self.assertEqual(prices.shop_url(url), SHOP)
        for url in ["https://evil.invalid/aichi/A1/A2/shop/", "https://www.cityheaven.net/",
                    "https://www.cityheaven.net:8080/aichi/A1/A2/shop/",
                    "https://user@www.cityheaven.net/aichi/A1/A2/shop/", None, "https://[bad"]:
            self.assertEqual(prices.shop_url(url), "")

    def test_redirect_restricted_and_failures_are_explicit(self):
        for status, expected in [(302, "redirect"), (403, "restricted"), (429, "restricted"),
                                 (404, "not_found"), (410, "not_found")]:
            session = MagicMock()
            session.get.return_value.__enter__.return_value = Mock(status_code=status)
            result = prices.fetch_price(session, SHOP + "system/")
            self.assertEqual(result["status"], expected)
            self.assertIsNone(result["selected"])
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        session = Mock()
        session.get.side_effect = prices.requests.Timeout("timeout")
        self.assertEqual(prices.fetch_price(session, SHOP + "system/")["status"], "error")

    def test_cache_roundtrip_and_import_preserve_other_shops_without_network(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            original = sample_cache()
            other = SHOP.replace("airabugakuen", "other")
            original["shops"][other] = prices.empty_result("not_found", "HTTP 410")
            prices.save_cache(original, path)
            self.assertEqual(prices.load_cache(path), original)
            with (
                patch.object(prices, "load_cache", return_value=copy.deepcopy(original)),
                patch.object(prices, "save_cache") as save,
                patch.object(prices, "fetch_price") as fetch,
            ):
                self.assertEqual(prices.main(["--shop-url", SHOP, "--html-file", str(FIXTURE), "--refresh"]), 0)
                fetch.assert_not_called()
                self.assertEqual(save.call_args.args[0]["shops"][SHOP]["selected"]["yen"], 27000)
                self.assertEqual(save.call_args.args[0]["shops"][SHOP]["source_kind"], "saved_html")
                self.assertEqual(save.call_args.args[0]["shops"][other], original["shops"][other])
            self.assertFalse(path.with_name(".prices.json.tmp").exists())

    def test_existing_failed_cache_is_not_automatically_retried(self):
        cache = {"version": 1, "shops": {SHOP: prices.empty_result("not_found", "HTTP 410")}}
        with (
            patch.object(prices, "load_cache", return_value=cache),
            patch.object(prices, "fetch_price") as fetch,
            patch.object(prices, "save_cache") as save,
        ):
            self.assertEqual(prices.main(["--shop-url", SHOP]), 0)
            fetch.assert_not_called()
            save.assert_not_called()

    def test_explicit_cache_reprocessing_preserves_source_cache_and_other_states(self):
        entry = prices.empty_result("unknown", "old parser")
        entry.update(
            checked_at="2026-10-25T00:00:00+00:00", source_kind="http",
            source_url=SHOP + "system/",
            candidates=[{
                "course_name": "システム料金", "section": "", "conditions": "別途サービス料",
                "label": "90分", "price_text": "14,000円",
            }, {
                "course_name": "システム料金", "section": "", "conditions": "別途サービス料",
                "label": "70分", "price_text": "11,000円",
            }],
        )
        other = SHOP.replace("airabugakuen", "other")
        current = {"version": 1, "shops": {other: prices.empty_result("not_found", "HTTP 410")}}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "user.json"
            prices.save_cache({"version": 1, "shops": {SHOP: entry}}, path)
            original = path.read_bytes()
            with (
                patch.object(prices, "CACHE", Path(directory) / "out.json"),
                patch.object(prices, "load_cache", side_effect=[copy.deepcopy(current),
                    {"version": 1, "shops": {SHOP: entry}}]),
                patch.object(prices, "save_cache") as save,
                patch.object(prices, "fetch_price") as fetch,
            ):
                self.assertEqual(prices.main(["--reprocess-cache", str(path), "--shop-url", SHOP]), 0)
                fetch.assert_not_called()
                result = save.call_args.args[0]["shops"]
                self.assertEqual(result[other], current["shops"][other])
                self.assertEqual(result[SHOP]["checked_at"], entry["checked_at"])
                self.assertEqual(result[SHOP]["selected"]["yen"], 14000)
            self.assertEqual(path.read_bytes(), original)
        for status in ("not_found", "restricted", "error", "redirect"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                prices.reprocess_cached_entry(dict(entry, status=status))

    def test_deduplication_and_limit_apply_to_shops_not_people(self):
        another = SHOP.replace("airabugakuen", "another")
        with (
            patch.object(prices, "load_cache", return_value={"version": 1, "shops": {}}),
            patch.object(prices, "save_cache"),
            patch.object(prices, "fetch_price", return_value=sample_cache()["shops"][SHOP]) as fetch,
        ):
            self.assertEqual(prices.main(["--shop-url", PROFILE, "--shop-url", SHOP + "system/",
                                          "--shop-url", another, "--limit", "1"]), 0)
            self.assertEqual(fetch.call_count, 1)


class PriceDecorationTests(unittest.TestCase):
    def test_url_lookup_evidence_unknown_and_idempotence(self):
        source = f"""<html><head></head><body><h1>Test</h1><div id="toolbar"></div><table>
<tr class="person-row"><td>Shop</td><td><a href="{PROFILE}">Person</a></td></tr>
<tr class="detail-row"><td>Body</td></tr>
<tr class="person-row"><td>Unknown</td><td><a href="https://other.invalid/person">Other</a></td></tr>
</table></body></html>"""
        page = BeautifulSoup(prices.attach_prices(source, sample_cache()), "html.parser")
        rows = page.select(".person-row")
        self.assertEqual(rows[0]["data-shop-price"], "18000.0")
        self.assertEqual(rows[1]["data-shop-price"], "")
        self.assertIn("27,000円", rows[0].get_text())
        self.assertIn("税込表記", rows[0].get_text())
        self.assertIn("料金不明", rows[1].get_text())
        self.assertEqual(len(page.select("#shopPriceFilters")), 1)
        self.assertEqual(len(BeautifulSoup(prices.attach_prices(str(page), sample_cache()), "html.parser").select("#shopPriceFilters")), 1)

    def test_reparsed_okinilove_identity_never_contains_price_text(self):
        source = f"""<html><head></head><body><h1>Test</h1><table id="girlsTable"><tbody>
<tr><td>Shop</td><td><a href="https://okinilove.com/blog-entry-1.html">Article</a></td>
<td><a href="{PROFILE}">Person</a></td><td>写真通り</td><td>★★★★★</td></tr>
</tbody></table></body></html>"""
        from okinilove.render_people_summary import build_people_summary
        with patch.object(prices, "load_cache", return_value=sample_cache()):
            page, _ = build_people_summary(source, fetch_missing_industries=False)
            records = _extract_rows(page)
            self.assertEqual(records[0]["shop"], "Shop")
            self.assertEqual(records[0]["profile_url"], PROFILE)
            self.assertEqual(records[0]["name"], "Person")

    def test_explicit_offline_window_keeps_dates_and_never_writes_user_caches(self):
        import generate_weekly_schedule_report as weekly
        cache = {"version": 2, "windows": {"2026-10-19": {}}}
        with (
            patch.object(weekly, "load_ranking", return_value=([], {})),
            patch.object(weekly, "load_cache", return_value=cache),
            patch.object(weekly, "saved_images", return_value={}),
            patch.object(weekly, "save_cache") as save,
            patch.object(weekly, "fetch_profile_images") as fetch,
            patch.object(weekly, "write_html") as write,
        ):
            self.assertEqual(weekly.main(["--offline", "--window-start", "2026-10-19"]), 0)
            page = BeautifulSoup(write.call_args.args[1], "html.parser")
            self.assertEqual(page.select_one("table[data-dates]")["data-dates"],
                             "2026-10-19,2026-10-20,2026-10-21,2026-10-22,2026-10-23,2026-10-24,2026-10-25")
            save.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual(cache, {"version": 2, "windows": {"2026-10-19": {}}})

    def test_invalid_or_missing_offline_window_fails_without_writing(self):
        import generate_weekly_schedule_report as weekly
        with (
            patch.object(weekly, "load_ranking", return_value=([], {})),
            patch.object(weekly, "load_cache", return_value={"version": 2, "windows": {}}),
            patch.object(weekly, "write_html") as write,
            patch.object(weekly, "save_cache") as save,
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            for arguments in [
                ["--window-start", "2026-10-19"],
                ["--offline", "--window-start", "2026-99-19"],
                ["--offline", "--window-start", "2026-10-19"],
            ]:
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                    weekly.main(arguments)
                self.assertEqual(error.exception.code, 2)
            write.assert_not_called()
            save.assert_not_called()

    def test_calendar_offline_refresh_preserves_schedule_and_review_dom(self):
        from integrations import report_left_merge as calendar
        import pandas as pd
        original = calendar.build_calendar_html(pd.DataFrame([{
            "url_key": PROFILE, "URL": PROFILE, "名前": "Person", "店名": "Shop",
            "画像URL": "https://image.invalid/saved.jpg",
            "evaluations_merged": {"score": "saved body"}, "report_url_merged": "https://report.invalid/1",
            "okinilove_reports": [], "ns_reports": [], "10/25(日)": "10:00~18:00",
        }]))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.html"
            path.write_text(original, encoding="utf-8")
            with patch.object(prices, "load_cache", return_value=sample_cache()):
                calendar.refresh_saved_calendar(path)
            refreshed = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(len(refreshed.select("#shopPriceFilters")), 1)
            self.assertEqual(refreshed.select_one("td[data-day]").get_text(), "10:00~18:00")
            self.assertIn("saved body", refreshed.select_one(".review-merged").get_text())
            self.assertEqual(refreshed.select_one("tr[data-report]")["data-shop-price"], "18000.0")

    def test_tokai_price_only_refresh_preserves_published_population_and_is_repeatable(self):
        from tokai_fuzoku import generate_report_final as tokai
        from tokai_fuzoku.html_render import generate_html
        import pandas as pd
        original = generate_html(pd.DataFrame([{
            "name": "Person", "shop_name": "Shop", "girl_url": PROFILE,
            "profile_image_url": "https://image.invalid/saved.jpg", "total_point": 80,
        }]))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tokai.html"
            path.write_text(original, encoding="utf-8")
            with (
                patch.object(tokai, "OUTPUT_HTML", path),
                patch.object(tokai, "load_input_data") as load,
                patch.object(prices, "load_cache", return_value=sample_cache()),
            ):
                tokai.main(["--offline", "--refresh-prices"])
                tokai.main(["--offline", "--refresh-prices"])
                load.assert_not_called()
            page = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            self.assertEqual(len(page.select("details.report-entry")), 1)
            self.assertEqual(len(page.select(".shop-price")), 1)
            self.assertEqual(len(page.select(".shop-price-brief")), 1)
            self.assertEqual(page.select_one("img")["src"], "https://image.invalid/saved.jpg")


if __name__ == "__main__":
    unittest.main()
