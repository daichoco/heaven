import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "tokai_fuzoku"))

from tokai_fuzoku.html_render import generate_html, group_reports, sort_attributes
from tokai_fuzoku import generate_report_final as tokai


HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const {script, data, controls, actions} = JSON.parse(fs.readFileSync(0, "utf8"));
const rows = data.map((item, index) => {
    const dataset = item.person || item;
    const articleData = item.articles || [dataset];
    const articles = articleData.map(articleDataset =>
        ({dataset: articleDataset, hidden: false, open: index === 0})
    );
    return {
        dataset, index, hidden: false, open: index === 0, articles,
        querySelectorAll(selector) {
            if (selector !== ".article-entry") throw new Error(`Unknown nested selector ${selector}`);
            return this.articles;
        },
        querySelector(selector) {
            if (selector !== ".person-filter-status") throw new Error(`Unknown nested element ${selector}`);
            return this.status;
        },
        status: {textContent: ""},
    };
});
const elements = Object.fromEntries(Object.entries(controls).map(([id, value]) =>
    [id, {value, listeners: {}, addEventListener(event, handler) {this.listeners[event] = handler;}}]
));
let order = [...rows];
elements.entries = {appendChild(fragment) {order = [...fragment.children];}};
let ready;
const context = vm.createContext({
    document: {
        getElementById(id) {
            if (!elements[id]) throw new Error(`Unknown element ${id}`);
            return elements[id];
        },
        querySelectorAll(selector) {
            if (selector === ".report-entry" || selector === "details.report-entry") return order;
            if (selector === "details.report-entry, details.article-entry")
                return [...order, ...order.flatMap(row => row.articles)];
            if (selector === ".article-entry") return order.flatMap(row => row.articles);
            if (selector === ".sticky-toolbar input, .sticky-toolbar select:not([data-sort])")
                return Object.entries(elements).filter(([id]) =>
                    id === "reportSearch" || id.endsWith("Filter")
                ).map(([id, control]) => ({...control, tagName: id === "reportSearch" ? "INPUT" : "SELECT",
                    addEventListener(event, handler) {elements[id].listeners[event] = handler;}
                }));
            throw new Error(`Unknown selector ${selector}`);
        },
        createDocumentFragment() {
            return {children: [], appendChild(entry) {this.children.push(entry);}};
        },
        addEventListener(event, handler) {
            if (event !== "DOMContentLoaded") throw new Error(`Unexpected event ${event}`);
            ready = handler;
        },
    },
    window: {},
    URLSearchParams,
    location: {search: ""},
});
vm.runInContext(script, context);
ready();
function snapshot() {
    return {
        order: order.map(row => row.index),
        visible: order.filter(row => !row.hidden).map(row => row.index),
        visibleArticles: order.map(row => row.articles.filter(article => !article.hidden).length),
        open: rows.map(row => row.open),
        articleOpen: rows.map(row => row.articles.map(article => article.open)),
        count: elements.filterCount.textContent,
        noResults: elements.noResults.hidden,
        sort: elements.reportSort.value,
    };
}
const results = [snapshot()];
for (const action of actions || []) {
    if (action.call) vm.runInContext(`${action.call}();`, context);
    else {
        const control = elements[action.id];
        control.value = action.value;
        control.listeners[action.id === "reportSearch" ? "input" : "change"]();
    }
    results.push(snapshot());
}
process.stdout.write(JSON.stringify(results));
"""


def report(**extra):
    return {
        "total": "80", "play": "70", "face": "60", "style": "50",
        "date": "20250101", "search": "sample shop", "shop": "shop",
        "type": "type", "shopType": "luxury", "repeat": "return", **extra,
    }


class TokaiSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise RuntimeError("Tokai sorting tests require Node.js on PATH.")
        with patch("tokai_fuzoku.html_render.load_cache", return_value={}):
            generated = generate_html(pd.DataFrame([{
                "date": "2025-12-20", "shop_name": "shop", "type": "type",
                "name": "name", "total_point": 0, "play_score": 80,
                "face_score": 90, "style_score": 70,
            }]))
        cls.pages = {
            "generated": BeautifulSoup(generated, "html.parser"),
            "published": BeautifulSoup(
                (ROOT / "docs" / "tokai_final_report.html").read_text(encoding="utf-8"),
                "html.parser",
            ),
        }

    def run_scripts(self, data, check, actions=None):
        for name, page in self.pages.items():
            with self.subTest(page=name):
                controls = {
                    control["id"]: (
                        control.select_one("option").get("value", "")
                        if control.name == "select" else ""
                    )
                    for control in page.select(".sticky-toolbar input, .sticky-toolbar select")
                }
                controls.update(filterCount="", noResults="")
                script = next(
                    s.string for s in page.find_all("script")
                    if "function applySort()" in (s.string or "")
                )
                result = subprocess.run(
                    [self.node, "-e", HARNESS],
                    input=json.dumps({
                        "script": script, "data": data, "controls": controls,
                        "actions": actions or [],
                    }),
                    encoding="utf-8", capture_output=True, check=True,
                )
                check(json.loads(result.stdout))

    def test_generated_metadata_and_scripts_match_published(self):
        entry = self.pages["generated"].select_one(".report-entry")
        for key, value in {
            "total": "0", "play": "80", "face": "90", "style": "70", "date": "20251220",
        }.items():
            self.assertEqual(entry[f"data-{key}"], value)
        scripts = []
        for page in self.pages.values():
            scripts.append(next(
                s.string for s in page.find_all("script")
                if "function applySort()" in (s.string or "")
            ))
            select = page.select_one("#reportSort")
            self.assertEqual(
                [o["value"] for o in select.select("option")],
                ["recommended", "total", "play", "face", "style", "date"],
            )
            self.assertTrue(select.has_attr("data-sort"))
            self.assertNotEqual(select.parent.get("id"), "filterBar")
            self.assertEqual(page.select_one('label[for="reportSort"]').text, "並び順")
            self.assertIn("リピート・パネル差は順位に含めません", page.select_one("#sortHelp").text)
            for row in page.select(".report-entry"):
                for key in ("total", "play", "face", "style", "date"):
                    self.assertIn(f"data-{key}", row.attrs)
        self.assertEqual(*scripts)

    def test_metadata_missing_zero_invalid_and_date(self):
        self.assertEqual(sort_attributes({
            "total_point": 0, "play_score": "bad", "face_score": float("inf"),
            "style_score": float("nan"), "date": "invalid",
        }), {
            "data-total": "0", "data-play": "", "data-face": "",
            "data-style": "", "data-date": "",
        })
        self.assertEqual(sort_attributes({})["data-date"], "")

    def test_recommended_priority_and_deterministic_ties(self):
        data = [
            report(style="49", date="20260101"),
            report(date="20250101"), report(date="20260101"),
            report(date="20260101"), report(face="61"),
            report(play="71"), report(total="81", play="0"),
        ]
        def check(results):
            for result in (results[0], results[-1]):
                self.assertEqual(result["order"], [6, 5, 4, 2, 3, 1, 0])
                self.assertEqual(result["open"], [True] + [False] * 6)
        self.run_scripts(data, check, actions=[
            {"id": "reportSort", "value": "date"},
            {"id": "reportSort", "value": "recommended"},
        ])

    def test_missing_values_come_after_zero_in_each_score_mode(self):
        for field in ("total", "play", "face", "style"):
            data = [
                report(**{field: "", "date": "20261231"}),
                report(**{field: "0"}),
                report(**{field: "NaN"}),
                report(**{field: "10"}),
                report(**{field: " "}),
                report(**{field: "Infinity"}),
            ]
            data.append(report())
            del data[-1][field]
            actions = [{"id": "reportSort", "value": field}]
            self.run_scripts(data, lambda r: self.assertEqual(
                r[-1]["order"], [3, 1, 0, 2, 4, 5, 6]
            ), actions=actions)
            self.run_scripts(data, lambda r: self.assertEqual(
                r[0]["order"], [3, 1, 0, 2, 4, 5, 6]
            ))

    def test_score_modes_and_newest_ties(self):
        data = [
            report(total="99", play="1", face="1", style="1", date=""),
            report(total="1", play="99", face="2", style="2", date="20240101"),
            report(total="2", play="2", face="99", style="3", date="20250101"),
            report(total="3", play="3", face="3", style="99", date="20260101"),
        ]
        for mode, order in {
            "total": [0, 3, 2, 1], "play": [1, 3, 2, 0],
            "face": [2, 3, 1, 0], "style": [3, 2, 1, 0],
            "date": [3, 2, 1, 0],
        }.items():
            self.run_scripts(data, lambda r, order=order: self.assertEqual(
                r[-1]["order"], order
            ), actions=[{"id": "reportSort", "value": mode}])
        self.run_scripts([report(date=""), report(date="20260101"), report(date="20260101")],
                         lambda r: self.assertEqual(r[-1]["order"], [1, 2, 0]),
                         actions=[{"id": "reportSort", "value": "total"}])

    def test_filters_sort_switch_reset_and_empty_results(self):
        data = [report(total="70"), report(total="90", play="1"), report(total=""),
                report(total="95", shop="different", search="different")]
        actions = [
            {"id": "shopFilter", "value": "shop"},
            {"id": "scoreFilter", "value": "80"},
            {"id": "reportSort", "value": "play"},
            {"id": "reportSearch", "value": " NO MATCH "},
            {"call": "resetFilters"},
        ]
        def check(results):
            self.assertEqual(results[0]["order"], [3, 1, 0, 2])
            self.assertEqual(results[1]["visible"], [1, 0, 2])
            self.assertEqual(results[2]["visible"], [1])
            self.assertEqual(results[3]["visible"], [1])
            self.assertEqual(results[3]["count"], "1 人 / 1 件（全 4 人 / 4 件）")
            self.assertEqual(results[4]["visible"], [])
            self.assertFalse(results[4]["noResults"])
            self.assertEqual(results[5]["visible"], [0, 2, 3, 1])
            self.assertEqual(results[5]["sort"], "play")
            self.assertTrue(results[5]["noResults"])
        self.run_scripts(data, check, actions=actions)
        for control in ("shopFilter", "typeFilter", "shopTypeFilter", "repeatFilter"):
            self.run_scripts(data, lambda r: self.assertEqual(r[-1]["visible"], []),
                             actions=[{"id": control, "value": "absent"}])
        self.run_scripts(data, lambda r: self.assertEqual(r[-1]["visible"], [1, 0, 2]),
                         actions=[{"id": "reportSearch", "value": " SHOP "}])
        self.run_scripts(data, lambda r: self.assertEqual(r[-1]["visible"], [1]), actions=[
            {"id": "reportSearch", "value": " SAMPLE "},
            {"id": "shopFilter", "value": "shop"},
            {"id": "typeFilter", "value": "type"},
            {"id": "shopTypeFilter", "value": "luxury"},
            {"id": "repeatFilter", "value": "return"},
            {"id": "scoreFilter", "value": "80"},
            {"id": "reportSort", "value": "face"},
        ])

    def test_published_reports_initial_order(self):
        data = [
            {key.removeprefix("data-").replace("-type", "Type"): value
             for key, value in row.attrs.items() if key.startswith("data-")}
            for row in self.pages["published"].select(".report-entry")
        ]
        self.assertTrue(data)
        def descending(row, key):
            return -float(row[key]) if row.get(key) else float("inf")
        expected = sorted(range(len(data)), key=lambda index: (
            *(descending(data[index], key) for key in ("total", "play", "face", "style", "date")),
            index,
        ))
        self.run_scripts(data, lambda r: self.assertEqual(r[0]["order"], expected))

    def test_repeat_and_panel_do_not_affect_order_and_empty_data(self):
        self.run_scripts(
            [report(repeat="not-return", panel="0"), report(repeat="return", panel="100")],
            lambda r: self.assertEqual(r[0]["order"], [0, 1]),
        )
        self.run_scripts([], lambda r: self.assertEqual(
            r[0]["count"], "0 人 / 0 件（全 0 人 / 0 件）"
        ))

    def test_multiple_filters_must_match_the_same_article(self):
        data = [
            {
                "person": report(total="80"),
                "articles": [
                    report(shop="shop", repeat="not-return", total="90"),
                    report(shop="different", repeat="return", total="50"),
                ],
            },
            {
                "person": report(total="90"),
                "articles": [report(shop="shop", repeat="return", total="80")],
            },
        ]
        self.run_scripts(data, lambda results: (
            self.assertEqual(results[-1]["visible"], [1]),
            self.assertEqual(results[-1]["visibleArticles"], [1, 0]),
            self.assertEqual(results[-1]["count"], "1 人 / 1 件（全 2 人 / 3 件）"),
        ), actions=[
            {"id": "shopFilter", "value": "shop"},
            {"id": "repeatFilter", "value": "return"},
            {"id": "scoreFilter", "value": "80"},
        ])

    def test_collapse_all_closes_people_and_individual_reports(self):
        self.run_scripts(
            [{"person": report(), "articles": [report(), report(date="20240101")]}],
            lambda results: (
                self.assertEqual(results[-1]["open"], [False]),
                self.assertEqual(results[-1]["articleOpen"], [[False, False]]),
            ),
            actions=[{"call": "collapseAll"}],
        )


class TokaiPersonAggregationTests(unittest.TestCase):
    def test_url_identity_missing_scores_latest_metadata_and_article_preservation(self):
        common = "https://profiles.example/girl?id=17"
        frame = pd.DataFrame([
            {
                "date": "2025-01-01", "girl_url": common + "&utm_source=old",
                "name": "旧名", "shop_name": "旧店", "type": "旧種別",
                "total_point": 0, "play_score": None, "face_score": float("nan"),
                "style_score": 0, "panel_point": 5, "url": "https://reports.example/old",
                "profile_image_url": "https://images.example/old.jpg",
                "total_comment": "旧コメント",
            },
            {
                "date": "2025-02-01", "girl_url": "https://PROFILES.example/girl/?utm_medium=x&id=17#bio",
                "name": "新名", "shop_name": "新店", "type": "新種別",
                "total_point": 80, "play_score": 100, "face_score": None,
                "style_score": 20, "panel_point": None, "url": "https://reports.example/new",
                "profile_image_url": "https://images.example/new.jpg",
                "total_comment": "<script>残るコメント</script>",
            },
            {
                "date": "2025-03-01", "girl_url": "https://profiles.example/other/",
                "name": "新名", "shop_name": "別店", "total_point": None,
                "url": "https://reports.example/other",
            },
            {
                "date": "2025-04-01", "girl_url": None, "name": "未連携",
                "shop_name": "未連携店", "url": "https://reports.example/no-profile-1",
            },
            {
                "date": "2025-05-01", "girl_url": None, "name": "未連携",
                "shop_name": "未連携店", "url": "https://reports.example/no-profile-2",
            },
        ])
        people = group_reports(frame)
        self.assertEqual(len(people), 4)
        merged = next(person for person in people if len(person["reports"]) == 2)
        self.assertEqual(merged["latest"]["name"], "新名")
        self.assertEqual(merged["latest"]["shop_name"], "新店")
        self.assertEqual(merged["latest"]["profile_image_url"], "https://images.example/new.jpg")
        self.assertEqual(merged["scores"]["total"], 40)
        self.assertEqual(merged["score_counts"]["total"], 2)
        self.assertEqual(merged["scores"]["play"], 100)
        self.assertEqual(merged["score_counts"]["play"], 1)
        self.assertEqual(merged["scores"]["style"], 10)
        self.assertEqual(merged["scores"]["panel"], 5)
        self.assertEqual(
            [record["url"] for _, record in merged["reports"]],
            ["https://reports.example/new", "https://reports.example/old"],
        )

        with patch("tokai_fuzoku.html_render.load_cache", return_value={}):
            page = BeautifulSoup(generate_html(frame), "html.parser")
        self.assertEqual(len(page.select(".report-entry")), 4)
        self.assertEqual(len(page.select(".article-entry")), len(frame))
        merged_card = next(
            card for card in page.select(".report-entry")
            if card.select_one(".person-identity a").get_text() == "新名"
            and len(card.select(".article-entry")) == 2
        )
        self.assertEqual(
            [merged_card[f"data-{key}"] for key in ("total", "play", "face", "style", "date")],
            ["40", "100", "", "10", "20250201"],
        )
        self.assertIn("パネル差 5.0（n=1）", merged_card.get_text())
        self.assertIn("未評価", next(
            card for card in page.select(".report-entry") if card is not merged_card
        ).get_text())
        self.assertEqual(
            {a["href"] for a in page.select(".article-entry a[href]")},
            set(frame["url"]),
        )
        self.assertEqual(
            page.select_one('.report-entry img.profile-thumbnail')["src"],
            "https://images.example/new.jpg",
        )
        self.assertIn("n=2", page.select_one(".report-entry .score-overview").text)
        self.assertIn("<script>", page.select_one(".article-entry .report-details").text)
        self.assertNotIn(
            "<script>", str(page.select_one(".article-entry .report-details"))
        )

    def test_generated_real_dataset_keeps_every_article_and_groups_by_url(self):
        df = tokai.load_input_data()
        people = group_reports(df)
        with patch("tokai_fuzoku.html_render.load_cache", return_value={}):
            page = BeautifulSoup(generate_html(df), "html.parser")
        self.assertEqual(len(page.select(".report-entry")), len(people))
        self.assertEqual(len(page.select(".article-entry")), len(df))
        article_links = {
            link["href"] for link in page.select(".article-entry a[href]")
            if link.get_text(strip=True) == "レポート"
        }
        self.assertEqual(article_links, set(df["url"].dropna().astype(str)))


if __name__ == "__main__":
    unittest.main()
