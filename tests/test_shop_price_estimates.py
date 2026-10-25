"""Published-course estimates retain conditions, not ordinary-course guarantees."""
import copy
import json
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import shop_prices as prices


def entry(rows):
    result = prices.empty_result("unknown", "saved evidence")
    result["candidates"] = [
        {"course_name": title, "label": label, "price_text": amount, "conditions": note,
         "section": "掲載コース", "table_index": index}
        for index, (title, label, amount, note) in enumerate(rows)
    ]
    return result


class PublishedEstimateTests(unittest.TestCase):
    def test_c_pari_prefers_longer_equal_distance_vip_and_retains_evidence(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "shop_prices_c_pari.json").read_text(encoding="utf-8"))
        saved = entry(fixture["rows"])
        saved.update(source_url=fixture["source_url"], checked_at=fixture["checked_at"], tax="included")
        before = copy.deepcopy(saved)
        result = prices.reprocess_cached_entry(saved)
        self.assertEqual(saved, before)
        self.assertEqual(result["checked_at"], fixture["checked_at"])
        self.assertEqual(result["source_url"], fixture["source_url"])
        selected = result["selected"]
        self.assertEqual((selected["course_name"], selected["minutes"], selected["yen"], selected["yen_per_60"]),
                         ("VIPコース", 100, 35000, 21000))
        self.assertTrue(any(c["course_name"] == "延長" and not c["eligible"] for c in result["candidates"]))
        markup = prices.price_info(result, prices.shop_url(fixture["source_url"]))
        page = BeautifulSoup(markup, "html.parser")
        for expected in ("条件つき目安", "VIPコース", "一部対象外", "入場料", "2,000円",
                         "指名料", "0円〜3,000円", fixture["checked_at"], "税込表記"):
            self.assertIn(expected, page.get_text())
        self.assertEqual(page.a["href"], fixture["source_url"])

    def test_names_and_conditions_do_not_exclude_independent_courses(self):
        for title in ("通常", "VIPコース", "プレミアム", "Aコース（ベッド限定）", "新人限定",
                      "会員限定", "早朝プラン", "総額パック", "撮影コース", "お任せフリー",
                      "コース（指名料別途）"):
            with self.subTest(title=title):
                result = prices.reprocess_cached_entry(entry([(title, "90分", "27,000円", "会員のみ・17時まで")]))
                self.assertEqual(result["selected"]["yen_per_60"], 18000)
                self.assertEqual(result["selected"]["conditions"], "会員のみ・17時まで")
        for title in ("延長", "追加オプション", "入浴料", "入泉料", "広告", "ポイント"):
            with self.subTest(title=title):
                result = prices.reprocess_cached_entry(entry([(title, "90分", "1,000円", "")]))
                self.assertIsNone(result["selected"])

    def test_competing_independent_course_amounts_choose_highest_at_selected_duration(self):
        result = prices.reprocess_cached_entry(entry([
            ("通常", "90分", "27,000円", ""), ("会員", "90分", "21,000円", "会員限定"),
        ]))
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertIn("高い方", result["reason"])
        self.assertEqual(len(result["selected"]["compared_courses"]), 2)
        self.assertIn("会員限定", prices.price_info(result, ""))
        result = prices.reprocess_cached_entry(entry([
            ("通常", "80分", "27,000円", ""), ("VIP", "100分", "35,000円", ""),
        ]))
        self.assertEqual(result["selected"]["yen_per_60"], 21000)

    def test_identical_selected_amount_preserves_all_alternative_conditions(self):
        result = prices.reprocess_cached_entry(entry([
            ("通常", "90分", "27,000円", "指名料別"), ("会員", "90分", "27,000円", "会員限定"),
        ]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["selected"]["matching_courses"]), 2)
        markup = prices.price_info(result, "")
        self.assertIn("指名料別", markup)
        self.assertIn("会員限定", markup)

    def test_partial_fees_unknown_durations_ranges_and_breakdowns_not_guessed(self):
        for label, amount in (("時間未定", "20,000円"), ("90分以上", "20,000円"),
                              ("90分", "20,000円〜"), ("90分", "基本料金20000円 入場料2000円"),
                              ("80/100分", "20,000円"), ("90分", "20,000円(15pt)")):
            with self.subTest(label=label, amount=amount):
                self.assertIsNone(prices.reprocess_cached_entry(entry([("VIP", label, amount, "")]))["selected"])


if __name__ == "__main__":
    unittest.main()
