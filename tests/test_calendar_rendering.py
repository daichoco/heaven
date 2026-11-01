import sys
import unittest
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from integrations.report_left_merge import build_calendar_html


class CalendarRenderingTests(unittest.TestCase):
    def test_mobile_schedule_and_desktop_output(self):
        cases = [
            ("10:00~18:00", "10:00<br>~<br>18:00", True),
            ("9:00 ~ 1:30", "9:00<br>~<br>1:30", True),
            (" 10:00  ~  18:00 ", "10:00<br>~<br>18:00", True),
            ("\t10:00\t~\t18:00\n", "10:00<br>~<br>18:00", True),
            ("-", "-", False),
            ("休み", "休み", False),
            (" 要確認 ", " 要確認 ", False),
            ("", "", False),
            (None, "-", False),
            (float("nan"), "-", False),
            ("10:00-18:00", "10:00-18:00", False),
            ("10:00~18:00 受付終了", "10:00~18:00 受付終了", False),
        ]
        for status, expected_mobile, is_time in cases:
            with self.subTest(status=status):
                merged = pd.DataFrame([{
                    "url_key": "https://example.com/profile",
                    "名前": "test",
                    "店名": "shop",
                    "画像URL": "https://example.com/image.jpg",
                    "URL": "https://example.com/profile",
                    "evaluations_merged": None,
                    "report_url_merged": None,
                    "okinilove_reports": [],
                    "ns_reports": [],
                    "10/17(土)": status,
                }])
                generated = build_calendar_html(merged)
                page = BeautifulSoup(generated, "html.parser")
                mobile_day = page.select_one("li[data-day='0']")
                box_class = "on" if is_time else "off"
                self.assertEqual(mobile_day.select_one(".m-date").get_text(), "10/17")
                self.assertEqual(mobile_day.select_one(".m-box").get("class"), ["m-box", box_class])
                self.assertEqual(
                    mobile_day.select_one(".m-box").get_text(separator="<br>"),
                    expected_mobile,
                )
                expected_desktop = status if pd.notna(status) else "-"
                desktop_day = page.select_one("td[data-label='10/17(土)']")
                self.assertEqual(desktop_day.get("data-on") == "1", is_time)
                self.assertIn("saturday", desktop_day.get("class", []))
                self.assertEqual(desktop_day.select_one(".cell").get_text(), expected_desktop)


if __name__ == "__main__":
    unittest.main()
