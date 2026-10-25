"""Structural decisions, evidence provenance, holds and UI status distinctions."""
import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import shop_prices as prices

SHOP = "https://www.cityheaven.net/aichi/A2301/A230101/e-com/"


def table(title, rows=None, note=""):
    rows = rows or [("60分", "18,000円"), ("90分", "27,000円"), ("120分", "36,000円")]
    return ('<table class="feesmart"><tr><td class="feetitle">' + title + "</td></tr>" +
            "".join(f'<tr><td class="feename">{a}</td><td class="feename2">{b}</td></tr>' for a, b in rows) +
            f'<tr><td colspan="2">{note}</td></tr></table>')


def parse(*tables):
    return prices.parse_system_html('<div id="shopcontent">' + "".join(tables) + "</div>")


def cache_entry(title="■基本コース", checked="2026-10-25T10:00:00+00:00"):
    result = parse(table(title))
    result.update(checked_at=checked, source_url=SHOP + "system/", source_kind="http")
    return result


class StructureTests(unittest.TestCase):
    def test_real_saved_ecom_context_excludes_events_and_preserves_reservation_note(self):
        result = prices.parse_system_html(
            (ROOT / "tests" / "fixtures" / "shop_prices_structural.html").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selected"]["minutes"], 90)
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertEqual(result["selected"]["yen_per_60"], 18000)
        self.assertEqual(result["selected"]["conditions"], "ネット予約をする")
        self.assertTrue(any(f["text"] == "2,000円" for f in result["additional_fees"]))
        self.assertEqual([d["decision"] for d in result["table_decisions"]],
                         ["excluded", "excluded", "eligible", "excluded"])
        replay = prices.reprocess_cached_entry(dict(result, status="unknown", selected=None))
        self.assertEqual(replay["selected"], result["selected"])

    def test_semantics_and_structure_not_exact_title_whitelist(self):
        for title in ["■基本コース", "【通常料金システム】", "通常ｺｰｽ", "✨料金システム✨",
                      "－SYSTEM－", "コース料金", "料金表", "ご利用料金", "⭐当店の基本コース！⭐"]:
            with self.subTest(title=title):
                result = parse(table(title))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["selected"]["yen"], 27000)
        # An unsupported product name alone must not make the cheapest table ordinary.
        result = parse(table("新しい謎のコース"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("名称", result["reason"])

    def test_separate_fee_notes_are_not_coupon_qualifiers(self):
        notes = [
            "別途指名料2,000円。ネット予約をする",
            "土日祝・17:00以降＋1000円 別途入場料",
            "週末は割増料金1000円。時間変更は延長扱いです",
            "基本プレイ以外はオプションとなり別途料金です",
            "プラチナメール会員登録",
        ]
        for note in notes:
            with self.subTest(note=note):
                result = parse(table("■基本コース", note=note))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["selected"]["yen_per_60"], 18000)
                self.assertEqual(result["selected"]["conditions"], note)
        for note in ["ネット予約限定", "事前予約必須", "会員様のみ", "口コミ割でこの価格", "新人限定"]:
            with self.subTest(note=note):
                self.assertIsNone(parse(table("通常料金", note=note))["selected"])

    def test_multiple_ordinary_time_and_rank_systems_stay_on_hold(self):
        cases = [
            (table("基本コース"), table("通常コース", [("90分", "1,000円")])),
            (table("コース料金"), table("未知の商品コース", [("90分", "1,000円")])),
            (table("通常料金"), table("平日10:00～17:00 通常料金", [("90分", "1,000円")])),
            (table("ヤングコース", [("90分", "1,000円")]), table("アダルトコース")),
            (table("通常コース VIPクラス"), table("通常コース レギュラークラス")),
            (table("基本料金", [("90分", "20,000円"), ("90分", "30,000円")]),),
            (table("基本料金", [("60分", "10,000円"), ("60分", "20,000円"), ("90分", "27,000円")]),),
            (table("基本料金", note="平日10:00～17:00の料金です"),),
        ]
        for tables in cases:
            with self.subTest(tables=tables):
                self.assertIsNone(parse(*tables)["selected"])

    def test_promotions_extensions_ads_and_points_never_become_base_prices(self):
        for title in ["イベント", "新人限定", "ネット予約限定", "広告", "延長料金", "特殊コース",
                      "ポイント", "会員様コース", "お任せフリー", "総額パック", "VIPコース"]:
            with self.subTest(title=title):
                result = parse(table("基本料金"), table(title, [("90分", "1,000円")]))
                self.assertEqual(result["selected"]["yen_per_60"], 18000)
        result = parse(table("基本料金", [
            ("60分", "18,000円"), ("90分", "27,000円"), ("30分延長", "1,000円"),
            ("ポイント90分", "1000pt"), ("初回限定90分", "1,000円"),
        ]))
        self.assertEqual(result["selected"]["yen"], 27000)

    def test_multiple_amounts_and_normal_discount_combinations_are_not_minimized(self):
        values = ["基本料金12,990円 入場料金2,000円 14,990円（税込）",
                  "20,000円→10,000円", "通常20,000円 会員10,000円", "20,000円～", "20,000〜30,000円"]
        for value in values:
            with self.subTest(value=value):
                result = parse(table("通常料金", [("90分", value)]))
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["selected"])
                self.assertEqual(result["candidates"][0]["price_text"], value)

    def test_missing_legacy_boundaries_do_not_authorize_structural_guess(self):
        result = parse(table(""))
        self.assertEqual(result["status"], "ok")
        for row in result["candidates"]:
            row.pop("table_index")
        replay = prices.reprocess_cached_entry(result)
        self.assertEqual(replay["status"], "unknown")
        self.assertIn("DOM", replay["reason"])
        explicit = cache_entry()
        for row in explicit["candidates"]:
            row.pop("table_index")
        self.assertEqual(prices.reprocess_cached_entry(explicit)["status"], "ok")
        explicit["candidates"] *= 2
        self.assertEqual(prices.reprocess_cached_entry(explicit)["status"], "unknown")

    def test_unknown_rows_are_retained_and_can_block_selection(self):
        result = parse(table("基本料金", [("60分", "18,000円"), ("90分応相談", "27,000円")]))
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIsNone(result["selected"])
        self.assertEqual(result["candidates"][1]["label"], "90分応相談")

    def test_tax_conflict_remains_on_hold_and_fullwidth_values_are_parsed(self):
        result = parse(table("通常ｺｰｽ", [("６０分", "１８，０００円"), ("９０分", "２７，０００円")]))
        self.assertEqual(result["selected"]["yen"], 27000)
        self.assertEqual(result["selected"]["price_text"], "２７，０００円")
        self.assertIsNone(parse(table("基本料金", note="税込と税別の記載"))["selected"])


class SnapshotTests(unittest.TestCase):
    def test_merge_retains_newer_user_observation_and_conflicting_original(self):
        newer = cache_entry(checked="2026-10-25T12:00:00+00:00")
        older = cache_entry(checked="2026-10-25T10:00:00+00:00")
        older["candidates"][1]["price_text"] = "30,000円"
        current = {"version": 1, "shops": {SHOP: newer}}
        incoming = {"version": 1, "shops": {SHOP: older}}
        result, conflicts = prices.merge_saved_evidence(current, incoming, "source-digest")
        self.assertEqual(conflicts, [SHOP])
        self.assertEqual(current["shops"][SHOP], newer)
        self.assertEqual(result["shops"][SHOP]["checked_at"], newer["checked_at"])
        self.assertEqual(result["shops"][SHOP]["alternate_evidence"][0]["candidates"],
                         older["candidates"])
        result, _ = prices.merge_saved_evidence(incoming, current, "new-source")
        self.assertEqual(result["shops"][SHOP]["previous_evidence"], older)
        self.assertEqual(result["shops"][SHOP]["imported_evidence_sha256"], "new-source")
        older["checked_at"] = newer["checked_at"]
        result, _ = prices.merge_saved_evidence(current, incoming, "conflict")
        self.assertTrue(result["shops"][SHOP]["evidence_conflict"])
        self.assertIsNone(result["shops"][SHOP]["selected"])
        with self.assertRaisesRegex(ValueError, "矛盾"):
            prices.reprocess_cached_entry(result["shops"][SHOP])
        older["checked_at"] = "2026-10-25T10:00:00"
        with self.assertRaisesRegex(ValueError, "タイムゾーン"):
            prices.merge_saved_evidence(current, incoming, "naive")

    def test_offline_snapshot_preserves_source_times_raw_data_and_nonparser_states(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "destination.json"
            source = Path(directory) / "source.json"
            entry = dict(cache_entry(), status="unknown", selected=None)
            incoming = {"version": 1, "shops": {SHOP: entry}}
            prices.save_cache(incoming, source)
            original = source.read_bytes()
            other = SHOP.replace("e-com", "restricted")
            baseline = {"version": 1, "shops": {other: prices.empty_result("redirect", "HTTP 302")}}
            prices.save_cache(baseline, path)
            with patch.object(prices, "fetch_price") as fetch:
                result, transitions, conflicts = prices.reprocess_snapshot(path, source)
                fetch.assert_not_called()
            self.assertEqual(transitions, {"unknown -> ok": 1})
            self.assertFalse(conflicts)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(result["shops"][SHOP]["checked_at"], entry["checked_at"])
            self.assertEqual(result["shops"][SHOP]["tax"], entry["tax"])
            self.assertEqual(result["shops"][other], baseline["shops"][other])
            self.assertEqual(result["analysis_inputs"]["imported_sha256"],
                             hashlib.sha256(original).hexdigest())

    def test_concurrent_external_write_is_not_overwritten(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            prices.save_cache({"version": 1, "shops": {SHOP: cache_entry()}}, path)
            external = {"version": 1, "shops": {SHOP: cache_entry(), "new": {"status": "redirect"}}}
            original = prices.reprocess_cached_entry
            def concurrent(entry):
                prices.save_cache(external, path)
                return original(entry)
            with patch.object(prices, "reprocess_cached_entry", side_effect=concurrent):
                with self.assertRaisesRegex(RuntimeError, "処理中に更新"):
                    prices.reprocess_snapshot(path)
            self.assertEqual(prices.load_cache(path), external)

    def test_changed_source_aborts_without_overwriting_destination(self):
        with TemporaryDirectory() as directory:
            path, source = Path(directory) / "local.json", Path(directory) / "source.json"
            payload = {"version": 1, "shops": {SHOP: cache_entry()}}
            prices.save_cache(payload, path)
            prices.save_cache(payload, source)
            before = path.read_bytes()
            original = prices.reprocess_cached_entry
            def changed_source(entry):
                prices.save_cache({"version": 1, "shops": {}}, source)
                return original(entry)
            with patch.object(prices, "reprocess_cached_entry", side_effect=changed_source):
                with self.assertRaisesRegex(RuntimeError, "取り込み元"):
                    prices.reprocess_snapshot(path, source)
            self.assertEqual(path.read_bytes(), before)

    def test_ui_distinguishes_pending_unfetched_and_fetch_failures(self):
        entries = [
            ({}, "未取得"),
            (prices.empty_result("error", "timeout"), "取得失敗"),
            (prices.empty_result("restricted", "403"), "アクセス制限"),
            (prices.empty_result("redirect", "302"), "転送"),
            (prices.empty_result("unknown", "unknown structure"), "候補未抽出・判定保留"),
            (dict(cache_entry(), status="unknown", selected=None), "候補あり・判定保留"),
        ]
        for entry, expected in entries:
            with self.subTest(expected=expected):
                soup = BeautifulSoup(prices.price_info(entry, SHOP), "html.parser")
                self.assertIn(expected, soup.select_one(".shop-price-value").get_text())
                self.assertNotIn("0円", soup.select_one(".shop-price-value").get_text())


if __name__ == "__main__":
    unittest.main()
