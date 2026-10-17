import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from integrations import report_left_merge as integration
from okinilove import okinilove_left_merge as okinilove
from tokai_fuzoku.html_render import generate_html

PROFILE = "https://www.cityheaven.net/a/shop/girlid-123/"
OTHER_PROFILE = "https://www.cityheaven.net/a/shop/girlid-456/"


def tokai_report(number, profile=PROFILE):
    return {
        "shop_name": "shop", "shop_type": "type", "girl_name": "person",
        "girl_url": profile, "cityheaven_url": profile, "age": 25,
        "report_url": f"https://m.fuzoku-tokai.com/2026/01/01/post-{number}/",
        "evaluations": {"score": "same comment"},
    }


def okinilove_report(number, profile=PROFILE):
    return {
        "shop_name": "shop", "girl_name": "person", "girl_url": profile,
        "cityheaven_url": profile,
        "report_url": f"https://okinilove.com/blog-entry-1.html#review{number}",
        "evaluations": {"score": "same comment"}, "report_text": f"body {number}",
    }


def calendar_profile(profile=PROFILE):
    return {
        "URL": profile, "名前": "person", "店名": "shop", "画像URL": "image.jpg",
        "10/17(土)": "10:00~18:00",
    }


class ReportMergeTests(unittest.TestCase):
    def test_reported_examples_have_distinct_article_bodies_in_published_page(self):
        history = integration.load_report_history()
        soup = BeautifulSoup((ROOT / "docs" / "calendar_report.html").read_text(encoding="utf-8"),
                             "html.parser")
        cases = [
            [
                "https://m.fuzoku-tokai.com/2023/11/30/post-14492/",
                "https://m.fuzoku-tokai.com/2025/09/08/post-15653/",
                "https://m.fuzoku-tokai.com/2021/03/23/post-11637/",
            ],
            [
                "https://m.fuzoku-tokai.com/2023/05/28/post-13973/",
                "https://m.fuzoku-tokai.com/2023/05/19/post-13937/",
                "https://m.fuzoku-tokai.com/2022/09/25/post-13321/",
                "https://m.fuzoku-tokai.com/2022/07/07/post-13058/",
            ],
        ]
        for urls in cases:
            with self.subTest(first_article=urls[0]):
                records = history.loc[history["report_url"].isin(urls)]
                self.assertEqual(len(records), len(urls))
                profile_keys = records["cityheaven_url"].map(integration.normalize_url)
                self.assertEqual(profile_keys.nunique(), 1)
                links = soup.find_all("a", href=urls[0])
                self.assertEqual(len(links), 1)
                review = links[0].find_parent("tr")
                self.assertEqual([a["href"] for a in review.select("a")], urls)
                self.assertEqual(len({ul.get_text() for ul in review.select("ul")}), len(urls))
                heading = review.find_previous_sibling("tr", attrs={"data-report": True})
                self.assertEqual(
                    integration.normalize_url(heading.select_one(".shop-cell > a")["href"]),
                    profile_keys.iloc[0],
                )
                for block in review.select("ul"):
                    url = block.find("a")["href"]
                    record = records.loc[records["report_url"] == url].iloc[0]
                    expected = [
                        " ".join(f"{key}: {value}".split())
                        for key, value in record["evaluations"].items()
                    ]
                    actual = [" ".join(li.get_text().split()) for li in block.find_all("li")[:-1]]
                    self.assertEqual(actual, expected)
        ids = [tag["id"] for tag in soup.select("[id]")]
        self.assertEqual(len(ids), len(set(ids)))

    def merge(self, reports, profiles, reviews):
        calendar = integration.merge_tokai_reports(
            pd.DataFrame(reports, columns=integration.REPORT_HISTORY_COLUMNS),
            pd.DataFrame(profiles),
        )
        with (
            patch.object(okinilove, "_read_all_columns", return_value=pd.DataFrame(
                reviews, columns=okinilove.OKINILOVE_COLUMNS,
            )),
            patch.object(okinilove, "fill_cityheaven_urls_from_calendar",
                         side_effect=lambda data, *_: (data, 0)),
            patch.object(okinilove, "write_jsonl_atomic",
                         side_effect=AssertionError("Unexpected data write")),
        ):
            return okinilove.merge_okinilove_data(
                calendar, normalize_url=integration.normalize_url,
                enrich_with_cityheaven_urls=None,
                check_unavailable=False, search_enrich=False,
            )

    def test_multiple_sources_do_not_multiply_or_discard_reports(self):
        reports = [tokai_report(i) for i in range(2)]
        reviews = [okinilove_report(i) for i in range(3)]
        merged = self.merge(reports, [calendar_profile(PROFILE + "?of=y")], reviews)
        self.assertEqual(len(merged), 2)  # Not 2 * 3 rows.
        self.assertEqual(merged["okinilove_reports"].map(len).tolist(), [3, 3])
        with patch("ns_senmon.ns_left_merge.load_ns_reports", return_value={}):
            merged = integration.merge_ns_reports(merged, normalize_url=integration.normalize_url)
        soup = BeautifulSoup(integration.build_calendar_html(merged), "html.parser")
        self.assertEqual(len(soup.select("tr[data-report]")), 1)
        self.assertEqual(
            [a["href"] for a in soup.select(".review-merged a")],
            [r["report_url"] for r in reports],
        )
        self.assertEqual(
            [a["href"] for a in soup.select(".review-okinilove a")],
            [r["report_url"] for r in reviews],
        )
        self.assertIn("merged 2件", soup.get_text())
        self.assertIn("okinilove 3件", soup.get_text())
        for review in reviews:
            self.assertEqual(soup.get_text().count(review["report_text"]), 1)

    def test_missing_keys_never_match_and_unmatched_profiles_survive(self):
        merged = self.merge(
            [tokai_report(1, None), tokai_report(2)],
            [calendar_profile(None), calendar_profile(OTHER_PROFILE), calendar_profile()],
            [okinilove_report(1, None), okinilove_report(2)],
        )
        self.assertEqual(len(merged), 3)
        unmatched = merged.loc[merged["URL"] != PROFILE]
        self.assertTrue(unmatched["report_url_merged"].isna().all())
        self.assertTrue(unmatched["okinilove_reports"].isna().all())
        self.assertEqual(merged.loc[merged["URL"] == PROFILE, "report_url_merged"].item(),
                         tokai_report(2)["report_url"])
        merged["ns_reports"] = None
        soup = BeautifulSoup(integration.build_calendar_html(merged), "html.parser")
        self.assertEqual(len(soup.select("tr[data-report]")), 3)
        self.assertEqual(len(soup.select("tr[data-report='true']")), 1)

    def test_empty_sources_keep_calendar_rows(self):
        merged = self.merge([], [calendar_profile()], [])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged["report_url_merged"].isna().all())
        self.assertTrue(merged["okinilove_reports"].isna().all())

    def test_duplicate_profile_keys_raise_instead_of_hiding_rows(self):
        with self.assertRaises(pd.errors.MergeError):
            integration.merge_tokai_reports(
                pd.DataFrame([tokai_report(1)]),
                pd.DataFrame([calendar_profile(), calendar_profile(PROFILE + "?of=y")]),
            )


class ReportExtractionTests(unittest.TestCase):
    def test_grouped_html_extracts_each_article_with_its_own_evaluations(self):
        records = [{
            "url": tokai_report(i)["report_url"], "girl_url": PROFILE,
            "name": "person", "shop_name": f"shop{i}", "type": "type",
            "date": f"2026-01-0{i}", "predict_age": 25,
            "total_point": 80, "total_comment": f"comment {i}",
        } for i in (1, 2)]
        with patch("tokai_fuzoku.html_render.load_cache", return_value={}):
            html = generate_html(pd.DataFrame(records))
        with patch.object(integration, "load_html", return_value=html):
            extracted = integration.extract_report_final_df()
        self.assertEqual(len(extracted), 2)
        by_article = extracted.set_index("report_url")
        for record in records:
            row = by_article.loc[record["url"]]
            self.assertEqual(row["shop_name"], record["shop_name"])
            self.assertEqual(row["shop_type"], "type")
            self.assertEqual(row["girl_name"], "person")
            self.assertEqual(row["girl_url"], PROFILE)
            self.assertEqual(row["cityheaven_url"], PROFILE)
            self.assertEqual(row["age"], 25)
            self.assertEqual(row["evaluations"], {"総合: 80": record["total_comment"]})
        empty = pd.DataFrame(columns=integration.REPORT_HISTORY_COLUMNS)
        with patch.object(integration, "enrich_with_cityheaven_urls", side_effect=lambda df: df):
            history, added = integration.append_new_reports(empty, extracted)
            again, added_again = integration.append_new_reports(history, extracted)
        self.assertEqual((added, added_again, len(again)), (2, 0, 2))

    def test_legacy_html_is_still_supported(self):
        html = f"""<details><summary>(shop・type) <a href="{PROFILE}">person</a>
        25才 (<a href="{tokai_report(1)['report_url']}">レポート</a>)</summary>
        <div><details><summary>score</summary><p>comment</p></details></div></details>"""
        with patch.object(integration, "load_html", return_value=html):
            extracted = integration.extract_report_final_df()
        self.assertEqual(len(extracted), 1)
        row = extracted.iloc[0]
        self.assertEqual((row["shop_name"], row["shop_type"], row["girl_name"]),
                         ("shop", "type", "person"))
        self.assertEqual(row["evaluations"], {"score": "comment"})
        self.assertEqual(row["report_url"], tokai_report(1)["report_url"])

    def test_empty_html_has_expected_columns(self):
        with patch.object(integration, "load_html", return_value="<html></html>"):
            extracted = integration.extract_report_final_df()
        self.assertTrue(extracted.empty)
        self.assertEqual(list(extracted.columns), integration.REPORT_HISTORY_COLUMNS)


if __name__ == "__main__":
    unittest.main()
