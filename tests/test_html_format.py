import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "tokai_fuzoku"))

from html_format import format_html, write_html
from cityheaven.cityheaven_month_schedule import write_index_html
from integrations.report_left_merge import write_calendar_html
from ns_senmon import generate_ns_senmon_report as ns
from okinilove.render_people_summary import render_people_summary_file
from tokai_fuzoku import generate_report_final as tokai


class HtmlFormatTests(unittest.TestCase):
    def test_preserves_sensitive_content_and_attributes(self):
        source = (
            '<!DOCTYPE html><html lang="ja"><head><style>\n'
            '.review-param { white-space: pre-wrap; }\n</style></head><body>'
            '<p class="review-param"> first  line\n  second &amp; third <span>inline</span> end </p>'
            '<pre>  pre\n text</pre><textarea>  input\n value</textarea>'
            '<a href="page.html?a=1&amp;b=2" data-search="a  b" onclick="go(\' a  b \')">one</a>'
            '<a href="#x">two</a> <span id="x">three</span>'
            '<script>\nconst text = ` first\n  second `;\n</script></body></html>'
        )
        formatted = format_html(source)
        before, after = (BeautifulSoup(s, "html.parser") for s in (source, formatted))
        self.assertEqual(
            [(e.name, e.attrs) for e in before.find_all()],
            [(e.name, e.attrs) for e in after.find_all()],
        )
        for selector in ("p", "pre", "textarea", "script", "style"):
            self.assertEqual(
                before.select_one(selector).decode_contents(),
                after.select_one(selector).decode_contents(),
            )
        self.assertIn('</a><a href="#x">two</a> <span', formatted)
        self.assertEqual(formatted, format_html(formatted))

    def test_all_published_html_is_idempotent(self):
        files = sorted((ROOT / "docs").rglob("*.html"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(page=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertEqual(source, format_html(source))
                self.assertNotIn(b"\r", path.read_bytes())

    def test_formatter_failure_does_not_overwrite_html(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text("original", encoding="utf-8")
            with patch("html_format.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Node.js"):
                    write_html(path, "<html></html>")
            failure = subprocess.CompletedProcess([], 1, "", "formatter error")
            with patch("html_format.subprocess.run", return_value=failure):
                with self.assertRaisesRegex(RuntimeError, "formatter error"):
                    write_html(path, "<html></html>")
            self.assertEqual(path.read_text(encoding="utf-8"), "original")


class GeneratedHtmlFormatTests(unittest.TestCase):
    def assert_formatted(self, path):
        source = path.read_text(encoding="utf-8")
        self.assertEqual(source, format_html(source))
        self.assertNotIn(b"\r", path.read_bytes())
        self.assertIn("\n  <head>", source)

    def test_index_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            write_index_html({"test day": []}, path)
            self.assert_formatted(path)
            self.assertIn("tokai_final_report.html", path.read_text(encoding="utf-8"))

    def test_calendar_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calendar_report.html"
            write_calendar_html(pd.DataFrame(), path)
            self.assert_formatted(path)

    def test_ns_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ns_senmon_report.html"
            with (
                patch.object(ns, "OUTPUT_HTML", path),
                patch.object(ns, "read_jsonl", return_value=pd.DataFrame(columns=["date", "post_id"])),
                patch.object(ns, "load_profiles", return_value={}),
            ):
                ns.main()
            self.assert_formatted(path)

    def test_tokai_generator_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokai_final_report.html"
            data = pd.DataFrame()
            with (
                patch.object(tokai, "OUTPUT_HTML", path),
                patch.dict(os.environ, {"FUZOKU_TOKAI_PASSWORD": "offline-test"}),
                patch.object(tokai, "load_input_data", return_value=data),
                patch.object(tokai.requests, "Session"),
                patch.object(tokai, "login"),
                patch.object(tokai, "filter_unavailable_girl_urls", return_value=data),
                patch.object(tokai, "enrich_report_data", return_value=data),
            ):
                tokai.main()
            self.assert_formatted(path)

    def test_people_summary_writer_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "okinilove_remove_unknown.html"
            path.write_text("<html><body><table><tbody></tbody></table></body></html>", encoding="utf-8")
            count = render_people_summary_file(
                path, profile_images={}, fetch_missing_images=False, fetch_missing_industries=False
            )
            self.assertEqual(count, 0)
            self.assert_formatted(path)
            self.assertEqual(
                json.loads((path.parent / "okinilove_review_bodies.json").read_text(encoding="utf-8")), []
            )


if __name__ == "__main__":
    unittest.main()
