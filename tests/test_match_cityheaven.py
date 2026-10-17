import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from integrations import report_left_merge
from ns_senmon import match_cityheaven as matcher


class MissingNameTests(unittest.TestCase):
    def test_name_helpers_treat_missing_values_as_empty(self):
        for missing in (None, float("nan"), pd.NA, ""):
            for helper, expected in (
                (matcher.clean, ""),
                (matcher.shop_variants, set()),
                (matcher.girl_key, ""),
                (matcher.girl_keys, set()),
                (matcher.label_keys, set()),
                (matcher.label_name, ""),
                (matcher.name_queries, []),
            ):
                with self.subTest(value=missing, helper=helper.__name__):
                    self.assertEqual(helper(missing), expected)

    def test_existing_name_normalization(self):
        self.assertEqual(matcher.shop_variants("ＡＢＣ（アクア）"), {"abc", "アクア", "abc(アクア)"})
        self.assertEqual(matcher.girl_keys("葵（あおい）"), {"葵(あおい)", "葵", "あおい"})

    def test_missing_shop_does_not_trigger_network_search(self):
        searcher = matcher.ShopSearcher({})
        with patch.object(searcher.session, "get") as get:
            for missing in (None, float("nan"), pd.NA, ""):
                self.assertIsNone(searcher.resolve_shop(missing))
        get.assert_not_called()

    def test_missing_girl_is_not_marked_not_listed(self):
        searcher = Mock()
        for missing in (None, float("nan"), pd.NA, ""):
            result = matcher.match_reports(
                [{"url": "article", "shop_name": "shop", "name": missing}],
                [], {}, {"shop": "gifu/A1/A2/shop"}, searcher,
            )
            self.assertEqual(result, {})
        searcher.resolve_shop.assert_not_called()
        searcher.find.assert_not_called()

    def test_resolver_handles_missing_names_without_accepting_name_only_matches(self):
        slug = "gifu/A1/A2/shop"
        url = f"https://www.cityheaven.net/{slug}/girlid-123/"
        candidates = [{
            "url": url, "slug": slug, "pref": "gifu",
            "shop": "shop", "girls": {"あおい"},
        }]
        pairs = [("shop", "あおい")]
        for missing in (None, float("nan"), pd.NA):
            pairs.extend([(missing, "あおい"), ("shop", missing)])
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(matcher, "SEARCH_CACHE_PATH", Path(directory) / "cache.json"),
                patch.object(matcher, "read_json", return_value={}),
                patch.object(matcher, "load_candidates", return_value=candidates),
                patch.object(matcher.requests.Session, "get") as get,
            ):
                resolved = matcher.resolve_urls(pairs)
        self.assertEqual(resolved[("shop", "あおい")], url)
        for pair in pairs[1:]:
            self.assertIsNone(resolved[pair])
        get.assert_not_called()

    def test_enrichment_does_not_stringify_missing_names(self):
        frame = pd.DataFrame([
            {"shop_name": float("nan"), "girl_name": "あおい", "cityheaven_url": None},
            {"shop_name": "shop", "girl_name": pd.NA, "cityheaven_url": ""},
            {"shop_name": None, "girl_name": None, "cityheaven_url": None},
            {"shop_name": "shop", "girl_name": "あおい", "cityheaven_url": "existing"},
        ])
        with patch.object(
            report_left_merge, "resolve_urls",
            return_value={("", "あおい"): None, ("shop", ""): None, ("", ""): None},
        ) as resolve:
            result = report_left_merge.enrich_with_cityheaven_urls(frame)
        resolve.assert_called_once_with([("", "あおい"), ("shop", ""), ("", "")])
        self.assertTrue(result.loc[:2, "cityheaven_url"].isna().all())
        self.assertEqual(result.loc[3, "cityheaven_url"], "existing")


if __name__ == "__main__":
    unittest.main()
