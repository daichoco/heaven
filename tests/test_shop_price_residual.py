"""Saved-evidence representation fixes must not relax ambiguous-system safeguards."""
import copy
import json
import sys
import unittest
from html import escape
from pathlib import Path
from unittest.mock import MagicMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import shop_prices as prices


def entry(title, rows, conditions="", section=""):
    result = prices.empty_result("unknown", "saved")
    result["candidates"] = [
        {"course_name": title, "label": label, "price_text": amount, "conditions": conditions,
         "section": section, "table_index": 1} for label, amount in rows
    ]
    result.update(checked_at="2026-10-25T13:00:00+00:00", source_url="saved")
    return result


class ResidualPriceTests(unittest.TestCase):
    def test_cached_real_rows_and_live_parser_share_decisions_without_losing_evidence(self):
        fixtures = json.loads((ROOT / "tests" / "fixtures" / "shop_prices_residual_rows.json").read_text(encoding="utf-8"))
        for fixture in fixtures:
            with self.subTest(url=fixture["source_url"]):
                before = entry(fixture["course_name"], fixture["rows"], fixture["conditions"], fixture["section"])
                original = copy.deepcopy(before)
                parsed = prices.reprocess_cached_entry(before)
                self.assertEqual(before, original)
                self.assertEqual(parsed["checked_at"], before["checked_at"])
                self.assertEqual(parsed["tax"], before["tax"])
                for old, new in zip(before["candidates"], parsed["candidates"]):
                    self.assertTrue(all(new[key] == value for key, value in old.items()))
                title = escape(fixture["course_name"])
                markup = '<div id="shopcontent"><table class="feesmart"><tr><td class="feetitle">' + title + "</td></tr>"
                for label, amount in fixture["rows"]:
                    markup += f'<tr><td class="feename">{escape(label)}</td><td class="feename2">{escape(amount)}</td></tr>'
                markup += f'<tr><td colspan="2">{escape(fixture["conditions"])}</td></tr></table></div>'
                live = prices.parse_system_html(markup)
                for result in (parsed, live):
                    if fixture["expected"] is None:
                        self.assertIsNone(result["selected"])
                    else:
                        minutes, yen = fixture["expected"]
                        self.assertEqual(result["status"], "ok")
                        self.assertEqual(result["selected"]["minutes"], minutes)
                        self.assertEqual(result["selected"]["yen"], yen)
                        self.assertEqual(result["selected"]["yen_per_60"], yen * 60 / minutes)
                        self.assertEqual(result["selected"]["conditions"], fixture["conditions"])
                if any("+" in amount for _, amount in fixture["rows"]):
                    for result in (parsed, live):
                        self.assertTrue(any("+" in fee["text"] for fee in result["additional_fees"]))

    def test_recommendation_and_currency_decoration_are_narrow_not_arbitrary_trimming(self):
        for label in ("90min", "90MIN.COURSE", "９０分【オススメ】", "人気 90分", "おすすめコース 90分", "90分 ★オススメ！"):
            for value in ("27,000円", "27,000yen", "￥27,000", "¥27,000-", "27,000円(税込)"):
                with self.subTest(label=label, value=value):
                    result = prices.reprocess_cached_entry(entry("通常コース", [(label, value)]))
                    self.assertEqual(result["selected"]["yen"], 27000)
        for label in ("90min～", "90分以上", "90分会員限定", "90分(会員100分)", "A90分", "90分要予約"):
            self.assertIsNone(prices.reprocess_cached_entry(entry("通常コース", [(label, "27,000円")]))["selected"], label)
        for value in ("¥27,000～", "27,000円→20,000円", "27,000円(税込29,700円)",
                      "27,000円(15pt)", "27,000円+交通費2,000円", "27,000円〜37,000円", "27,000円 会員限定"):
            self.assertIsNone(prices.reprocess_cached_entry(entry("通常コース", [("90分", value)]))["selected"], value)

    def test_named_unquantified_addon_kept_separate_not_silently_added(self):
        result = prices.reprocess_cached_entry(entry("通常コース", [("90分", "27,000円+交通費")]))
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertEqual(result["selected"]["amount_annotations"], ["別途 交通費（金額未確認）"])
        self.assertTrue(any("27,000円+交通費" == item["text"] for item in result["additional_fees"]))
        result = prices.reprocess_cached_entry(entry("基本料金", [("90分", "27,000円"), ("追加プラン", "+3,500円～")]))
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertTrue(any("+3,500円～" == item["text"] for item in result["additional_fees"]))

    def test_only_identical_nonprice_information_rows_are_ignored(self):
        rows = [("60min", "18,000yen"), ("90min", "25,000yen"),
                ("※180min以上もご用意しております。", "※180min以上もご用意しております。")]
        self.assertEqual(prices.reprocess_cached_entry(entry("基本料金", rows))["selected"]["yen"], 25000)
        rows[-1] = ("180min", "料金はお問い合わせ下さい")
        self.assertIsNone(prices.reprocess_cached_entry(entry("基本料金", rows))["selected"])
        rows[-1] = ("90minお問い合わせ", "90minお問い合わせ")
        self.assertIsNone(prices.reprocess_cached_entry(entry("基本料金", rows))["selected"])

    def test_partial_bathing_fee_is_not_misrepresented_as_complete_base_course(self):
        result = prices.parse_system_html(
            (ROOT / "tests" / "fixtures" / "shop_prices_partial_fee.html").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("部分料金", result["reason"])
        self.assertIn("入浴・入泉料のみ・基本体系不足", prices.unresolved_categories(result))
        self.assertEqual(len(result["candidates"]), 6)
        self.assertTrue(any("別途サービス料金" in item["text"] for item in result["additional_fees"]))
        for rows in ([("ご入浴料 70分", "13,200円"), ("指名料", "3,000円")],
                     [("入浴料", "11,000円"), ("指名料", "2,000円")]):
            result = prices.reprocess_cached_entry(entry("料金システム", rows))
            self.assertIn("部分料金", result["reason"])
            self.assertIsNone(result["selected"])

    def test_redirect_location_recorded_without_following_or_reassigning_shop(self):
        session = MagicMock()
        response = Mock(status_code=302, headers={"Location": "http://img.cityheaven.net/cs/nenrei/"})
        session.get.return_value.__enter__.return_value = response
        url = "https://www.cityheaven.net/aichi/A2301/A230101/example/system/"
        result = prices.fetch_price(session, url)
        session.get.assert_called_once()
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(result["redirect_kind"], "age_verification")
        self.assertEqual(result["redirect_location"], response.headers["Location"])
        self.assertIsNone(result["selected"])
        self.assertIn("未追跡", prices.price_info(result, prices.shop_url(url)))
        self.assertEqual(prices.redirect_result(302, "/different-shop/")["redirect_kind"], "unverified")

    def test_extraction_diagnostics_do_not_invent_prices_or_parent_table_relationships(self):
        markup = ('<div id="shopcontent"><table class="feesmart"><tr><td class="feetitle">画像料金</td>'
                  '</tr><tr><td colspan="2"><img src="price.png" alt="90分1000円"></td></tr></table></div>')
        result = prices.parse_system_html(markup)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["extraction_diagnostics"], {"root_found": True, "table_count": 1, "image_count": 1})
        self.assertEqual(result["unpaired_tables"][0]["course_name"], "画像料金")
        self.assertFalse(result["candidates"])
        self.assertEqual(prices.unresolved_categories(result), ["候補未抽出・原DOM不足"])

    def test_overlapping_hold_categories_report_ambiguity_instead_of_cheapest_value(self):
        before = entry("通常コース", [("60分", "18,000円"), ("90分", "27,000円(15pt)")])
        result = prices.reprocess_cached_entry(before)
        self.assertIn("ポイントと金額の関係不明", prices.unresolved_categories(result))
        self.assertIsNone(result["selected"])
        before["candidates"][1]["price_text"] = "27,000円(税込29,700円)"
        result = prices.reprocess_cached_entry(before)
        self.assertIn("複数金額・内訳・税込併記", prices.unresolved_categories(result))


if __name__ == "__main__":
    unittest.main()
