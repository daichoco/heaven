import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from okinilove.render_people_summary import NEGATIVE, NEUTRAL, _tone, build_people_summary


# Execute the real sorting/filtering code with a small DOM, without a browser or network.
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const {script, data, mode, filters} = JSON.parse(fs.readFileSync(0, 'utf8'));
const rows = data.map((dataset, index) => ({
  dataset, index, hidden: false,
  cells: [{}, {querySelector: () => ({textContent: dataset.age || ''})}],
  classList: {contains: () => false},
  nextElementSibling: {detail: index, hidden: true},
}));
const elements = {};
const element = id => elements[id] ||= {
  value: '', checked: false, addEventListener() {},
};
element('sort').value = mode || 'score';
element('minimum').value = '0';
element('reports').value = '0';
for (const [id, value] of Object.entries(filters || {})) {
  if (id === 'include-chubby') element(id).checked = value;
  else element(id).value = value;
}
let appended = [];
const tbody = {
  querySelectorAll: () => rows,
  appendChild: row => appended.push(row),
};
const context = vm.createContext({
  document: {
    getElementById: element,
    querySelector: () => tbody,
    querySelectorAll: () => rows,
  },
});
vm.runInContext(script.slice(0, script.indexOf('let bodiesPromise=')), context);
vm.runInContext('applySort();applyFilters();', context);
const scores = vm.runInContext(`originalPairs.map(([r]) => ({
  star: correctedStar(r) / 10,
  neg: correctedRate(r, 'negn', priorNeg),
  pos: correctedRate(r, 'posn', priorPos),
}))`, context);
const result = {
  order: appended.filter(r => !('detail' in r)).map(r => r.index),
  pairs: appended.map(r => 'detail' in r ? `detail-${r.detail}` : r.index),
  visible: rows.filter(r => !r.hidden).map(r => r.index),
  scores,
};
// Compare with the previous three-review correction using the same real comparator.
appended = [];
const oldScript = script.slice(0, script.indexOf('let bodiesPromise='))
  .replace('(rated(r)*stars(r)+priorStar)/(rated(r)+1)',
           '(rated(r)*stars(r)+3*priorStar)/(rated(r)+3)')
  .replace('(Number(r.dataset[key])+baseline)/(count(r)+1)',
           '(Number(r.dataset[key])+3*baseline)/(count(r)+3)');
const oldContext = vm.createContext({
  document: {
    getElementById: element,
    querySelector: () => tbody,
    querySelectorAll: () => rows,
  },
});
vm.runInContext(oldScript + '\napplySort();', oldContext);
result.oldOrder = appended.filter(r => !('detail' in r)).map(r => r.index);
process.stdout.write(JSON.stringify(result));
"""


def person(average, rated, vals=None, posn=0, negn=0, **extra):
    vals = rated if vals is None else vals
    return {
        "average": str(average), "rated": str(rated), "vals": str(vals),
        "posn": str(posn), "negn": str(negn), "reports": str(rated),
        "neg": str(negn / vals if vals else 0), "search": "sample",
        "shop": "shop", "industry": "industry", "faces": "face",
        "gaps": "gap", "serviceTypes": "sp", "chubby": "false", **extra,
    }


class RecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise RuntimeError("Recommendation tests require Node.js on PATH.")
        with (
            patch("requests.get", side_effect=AssertionError("Unexpected network request")),
            patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network request")),
        ):
            generated, _ = build_people_summary(
                "<html><body></body></html>", {}, fetch_missing_industries=False
            )
        published = BeautifulSoup(
            (ROOT / "docs" / "okinilove_remove_unknown.html").read_text(encoding="utf-8"),
            "html.parser",
        )
        cls.pages = {"generated": generated, "published": published}

    def run_scripts(self, data, check, **options):
        for name, page in self.pages.items():
            with self.subTest(page=name):
                script = next(s.string for s in page.find_all("script") if "correctedStar" in (s.string or ""))
                result = subprocess.run(
                    [self.node, "-e", HARNESS],
                    input=json.dumps({"script": script, "data": data, **options}),
                    capture_output=True, encoding="utf-8", check=True,
                )
                check(json.loads(result.stdout))

    def test_explanation_and_generated_script_match_published(self):
        scripts = []
        for page in self.pages.values():
            self.assertIn("星・率は全体平均1件分で補正します。", page.get_text())
            self.assertNotIn("全体平均3件分", page.get_text())
            scripts.append(next(
                s.string for s in page.find_all("script") if "correctedStar" in (s.string or "")
            ).split("let bodiesPromise=", 1)[0])
        self.assertEqual(*scripts)

    def test_one_review_correction_values(self):
        def check(result):
            # Weighted baselines: stars=3.2, negative=0.4, positive=0.4.
            self.assertEqual(result["scores"][0]["star"], 4.1)
            self.assertAlmostEqual(result["scores"][0]["neg"], 0.2)
            self.assertAlmostEqual(result["scores"][0]["pos"], 0.7)
        self.run_scripts([person(5, 1, posn=1), person(3, 9, posn=3, negn=4)], check)

    def test_style_classification_updates_negative_rate_body_and_recommendation(self):
        self.assertNotIn("デブ", NEUTRAL["style"])
        self.assertIn("デブ", NEGATIVE["style"])
        self.assertEqual(_tone("style", "デブ"), "neg")
        for word in ["ちょいぽちゃ", "ガリガリ", "ムッチリ", "普通", "普通体型", "細い", "色白", "色黒"]:
            self.assertEqual(_tone("style", word), "", word)
        source = '<table id="girlsTable"><tbody>' + "".join(
            f'<tr><td>店舗</td><td><a href="https://okinilove.com/blog-entry-{i}.html">記事</a></td>'
            f'<td><a href="https://example.invalid/{i}">人物{i}</a></td><td></td><td>★★★★☆</td></tr>'
            for i in [1, 2]
        ) + '</tbody></table>'
        with (
            patch("okinilove.render_people_summary._load_review_details", return_value=({
                "https://okinilove.com/blog-entry-1.html": {"style": "デブ"},
                "https://okinilove.com/blog-entry-2.html": {"style": "普通"},
            }, {})),
            patch("requests.get", side_effect=AssertionError("Unexpected network request")),
        ):
            page, bodies = build_people_summary(source, {}, fetch_missing_industries=False)
        rows = page.select(".person-row")
        self.assertEqual([row["data-negn"] for row in rows], ["1", "0"])
        self.assertEqual([row["data-vals"] for row in rows], ["1", "1"])
        self.assertIn("ネガ含有率100%", rows[0].get_text("", strip=True))
        self.assertIn(["デブ", "neg"], json.loads(bodies)[0]["s"])
        data = [
            person(float(row["data-average"]), int(row["data-rated"]),
                   vals=int(row["data-vals"]), posn=int(row["data-posn"]), negn=int(row["data-negn"]))
            for row in rows
        ]
        self.run_scripts(data, lambda result: self.assertEqual(result["order"], [1, 0]))

    def test_single_review_can_overtake_established_person(self):
        def check(result):
            self.assertEqual(result["order"], [0, 1, 2])
            self.assertEqual(result["oldOrder"], [1, 0, 2])
            self.assertEqual([s["star"] for s in result["scores"][:2]], [4.4, 4.4])
            self.assertEqual(result["pairs"], [0, "detail-0", 1, "detail-1", 2, "detail-2"])
        self.run_scripts(
            [person(5, 1, posn=1), person(4.5, 10, posn=10, negn=2), person(3, 10, negn=5)],
            check,
        )

    def test_priority_and_one_decimal_rounding(self):
        cases = [
            # Stars outrank negative rate.
            ([person(5, 4, negn=4), person(3, 4)], [0, 1]),
            # Different raw stars round to the same corrected star; negative wins.
            ([person(4.01, 10, negn=2), person(4, 10, negn=1)], [1, 0]),
            # Positive rate breaks a negative-rate tie.
            ([person(4, 2, posn=1), person(4, 2, posn=2)], [1, 0]),
            # Count breaks equal corrected stars and rates.
            ([person(4, 1, posn=1), person(4, 3, posn=3)], [1, 0]),
            # A complete tie retains original order.
            ([person(4, 2), person(4, 2)], [0, 1]),
        ]
        for data, order in cases:
            self.run_scripts(data, lambda r: self.assertEqual(r["order"], order))

    def test_missing_evaluations_and_empty_data(self):
        self.run_scripts(
            [person(0, 0, vals=2, posn=2), person(0, 1, vals=0), person(0, 1, vals=1)],
            lambda r: self.assertEqual(r["order"], [2, 1, 0]),
        )
        self.run_scripts(
            [person(0, 0, vals=0), person(0, 0, vals=0)],
            lambda r: self.assertEqual(r["scores"], [{"star": 0, "neg": 0, "pos": 0}] * 2),
        )
        self.run_scripts([], lambda r: self.assertEqual(r["order"], []))

    def test_other_sort_modes_unchanged(self):
        data = [
            person(5, 1, posn=1), person(4, 4, posn=2, negn=1),
            person(3, 2, posn=1), person(0, 0, vals=0),
        ]
        for mode, order in {
            "pos": [0, 1, 2, 3], "neg": [2, 0, 1, 3],
            "average": [0, 1, 2, 3], "count": [1, 2, 0, 3],
        }.items():
            def check(result, order=order):
                self.assertEqual(result["order"], order)
                self.assertEqual(result["order"], result["oldOrder"])
            self.run_scripts(data, check, mode=mode)

    def test_filters_do_not_change_global_baseline(self):
        data = [person(5, 1, posn=1), person(3, 9, posn=3, negn=4, chubby="true")]
        def check(result):
            self.assertEqual(result["visible"], [0])
            self.assertEqual(result["scores"][0]["star"], 4.1)
        self.run_scripts(data, check)
        for field, value in {
            "search": "absent", "shop": "absent", "industry": "absent",
            "minimum": "6", "face": "absent", "gap": "absent",
            "negmax": "-1", "reports": "10", "age": "under30",
            "service-type": "super-sp",
        }.items():
            self.run_scripts(data, lambda r: self.assertEqual(r["visible"], []), filters={field: value})
        self.run_scripts(
            data, lambda r: self.assertEqual(r["visible"], [0, 1]),
            filters={"include-chubby": True},
        )


if __name__ == "__main__":
    unittest.main()
