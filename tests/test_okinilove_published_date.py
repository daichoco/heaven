import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from okinilove import scrape_okinilove as so

HTML = """
<div>
<h3><a name="abc"></a>one</h3>
<h4>レポート内容補足などあれば</h4>本文A<br>[公開日2026/11/03]<br>
<h3><a name="def"></a>two</h3>
<h4>レポート内容補足などあれば</h4>本文B<br>
<h3><a name="ghi"></a>three</h3>
</div>
"""


class PublishedDateTest(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup(HTML, "html.parser")

    def test_date_is_extracted_per_fragment(self):
        self.assertEqual(
            so.extract_published_date(self.soup, "https://okinilove.com/blog-entry-1.html#abc"),
            "2026-11-03",
        )

    def test_missing_date_or_fragment_is_empty(self):
        base = "https://okinilove.com/blog-entry-1.html"
        self.assertEqual(so.extract_published_date(self.soup, base + "#def"), "")
        self.assertEqual(so.extract_published_date(self.soup, base + "#zzz"), "")
        self.assertEqual(so.extract_published_date(self.soup, base), "")

    def test_report_text_excludes_date(self):
        target = self.soup.find(attrs={"name": "abc"}).parent
        self.assertEqual(so._report_text_after(target), "本文A")


if __name__ == "__main__":
    unittest.main()
