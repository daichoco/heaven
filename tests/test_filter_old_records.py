import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generate_booking_difficulty_report import REPORT_MIN_DATE, filter_old_records


class FilterOldRecordsTest(unittest.TestCase):
    def test_cutoff_is_2023(self):
        self.assertEqual(REPORT_MIN_DATE, "2023-01-01")

    def test_filters_per_dataset_date_field_and_keeps_undated(self):
        datasets = [
            ("東海風俗", "tokai_fuzoku", [
                {"date": "2022-12-31"}, {"date": "2023-01-01"}, {"date": "保存日付なし"}, {},
            ]),
            ("NS専門", "ns_senmon", [{"date": "2019-01-01"}, {"date": "2026-11-07"}]),
            ("オキニラブ", "okinilove", [
                {"published_date": "2022-06-01"}, {"published_date": "2024-02-02"},
                {"published_date": ""}, {"published_date": None},
            ]),
        ]
        filtered, removed = filter_old_records(datasets)
        kept = {label: records for label, _, records in filtered}
        self.assertEqual(
            kept["東海風俗"], [{"date": "2023-01-01"}, {"date": "保存日付なし"}, {}]
        )
        self.assertEqual(kept["NS専門"], [{"date": "2026-11-07"}])
        self.assertEqual(
            kept["オキニラブ"],
            [{"published_date": "2024-02-02"}, {"published_date": ""}, {"published_date": None}],
        )
        self.assertEqual(removed, {"東海風俗": 1, "NS専門": 1, "オキニラブ": 1})

    def test_person_with_any_recent_report_stays(self):
        datasets = [("オキニラブ", "okinilove", [
            {"girl_name": "A", "published_date": "2020-01-01"},
            {"girl_name": "A", "published_date": "2025-01-01"},
        ])]
        filtered, _ = filter_old_records(datasets)
        self.assertEqual([r["published_date"] for r in filtered[0][2]], ["2025-01-01"])


if __name__ == "__main__":
    unittest.main()
