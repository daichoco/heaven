import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from generate_booking_difficulty_report import (
    DATASETS, ReportViews, booking_matches, candidate_matches, candidate_person_key,
    analyze_record, extract_people, highlight_booking_text,
    rating_value, record_url_key, relative_scores, render_html,
)


class BookingDifficultyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.people, cls.counts = extract_people()
        cls.html = render_html(cls.people, cls.counts)
        cls.page = BeautifulSoup(cls.html, "html.parser")

    def test_every_person_has_inline_matching_reviews(self):
        cards = self.page.select("#people > .person")
        self.assertEqual(len(cards), len(self.people))
        for person, card in zip(self.people, cards):
            expected = {
                (source["database"], record_url_key(source["source_url"]))
                for source in person["sources"]
            }
            actual = {
                (entry.find_parent(class_="source-report")["data-database"],
                 record_url_key(entry["data-source-url"]))
                for entry in card.select(".source-report .evidence-record")
            }
            self.assertEqual(actual, expected, person["name"])
            self.assertTrue(card.select(".person-row, .person-heading"), person["name"])
            for entry in card.select(".evidence-record"):
                self.assertIsNotNone(entry.select_one(".booking-marker"))
                disclosure = entry.select_one(".review-body") or entry
                self.assertTrue(disclosure.has_attr("open"))

    def test_person_cells_are_reused_without_losing_photos_or_ratings(self):
        for label, _, dataset, filename in DATASETS:
            if dataset == "tokai_fuzoku":
                continue
            source = BeautifulSoup((ROOT / "docs" / filename).read_text(encoding="utf-8"), "html.parser")
            source_rows = {
                tuple(str(cell) for cell in row.find_all("td", recursive=False)[:-1]): row
                for row in source.select("tr.person-row")
            }
            for block in self.page.select(".source-report"):
                if block["data-database"] != label:
                    continue
                for row in block.select(".person-row"):
                    row_key = tuple(str(cell) for cell in row.find_all("td", recursive=False)[:-1])
                    if row_key not in source_rows:
                        self.assertEqual(block["data-database"], "オキニラブ")
                        self.assertIsNotNone(block.select_one(".review-text"))
                        continue
                    original = source_rows[row_key]
                    cells = row.find_all("td", recursive=False)
                    originals = original.find_all("td", recursive=False)
                    self.assertEqual(len(cells), len(originals))
                    for actual, expected in zip(cells[:-1], originals[:-1]):
                        self.assertEqual(str(actual), str(expected))

    def test_okinilove_bodies_are_inline_and_json_is_secondary(self):
        bodies = json.loads((ROOT / "docs" / "okinilove_review_bodies.json").read_text(encoding="utf-8"))
        source = BeautifulSoup(
            (ROOT / "docs" / "okinilove_remove_unknown.html").read_text(encoding="utf-8"),
            "html.parser",
        )
        texts = {}
        for row in source.select(".person-row"):
            key = tuple(
                link["href"] for link in row.find_next_sibling("tr").select(".review-meta a")
            )
            texts[key] = [
                bodies[int(item["data-rid"])].get("t") if item.has_attr("data-rid") else None
                for item in row.find_next_sibling("tr").select(".review-item")
            ]
        self.assertFalse(self.page.select("[data-rid]"))
        for block in self.page.select('.source-report[data-database="オキニラブ"]'):
            key = tuple(link["href"] for link in block.select(".review-meta a"))
            if key not in texts:
                self.assertIsNotNone(block.select_one(".review-text"))
                continue
            for item, expected in zip(block.select(".review-item"), texts[key]):
                if expected:
                    self.assertEqual(
                        item.select_one(".review-text:not(.saved-evidence-text)").get_text(), expected
                    )
        self.assertNotIn("fetch(", self.html)
        raw_records = self.page.select(".raw-records .stored-record pre")
        expected_records = [source["record"] for person in self.people for source in person["sources"]]
        self.assertEqual([json.loads(node.get_text()) for node in raw_records], expected_records)

    def test_missing_tokai_record_is_rendered_not_linked_to_json(self):
        with TemporaryDirectory() as directory:
            docs = Path(directory)
            for _, _, _, filename in DATASETS:
                (docs / filename).write_text("<html><body></body></html>", encoding="utf-8")
            (docs / "okinilove_review_bodies.json").write_text("[]", encoding="utf-8")
            record = {
                "name": "Example", "shop_name": "Shop", "predict_age": "45",
                "url": "https://example.invalid/article", "girl_url": "https://example.invalid/person",
                "total_point": 90, "total_comment": "<script>not markup</script> 予約困難",
            }
            person = {"sources": [{"database": "東海風俗", "source_url": record["url"], "record": record}]}
            fragment = BeautifulSoup(ReportViews(docs).render(person), "html.parser")
            self.assertEqual(fragment.select_one(".person-identity").get_text().strip(), "(Shop) Example 45才")
            self.assertEqual(fragment.select_one(".report-comment p").get_text(), record["total_comment"])
            self.assertIsNotNone(fragment.select_one(".evidence-record"))
            self.assertFalse(fragment.select("script, pre"))
            person["sources"][0]["database"] = "NS専門"
            with self.assertRaisesRegex(ValueError, "matching review not found"):
                ReportViews(docs).render(person)

    def test_highlighting_preserves_text_and_ignores_negated_difficulty(self):
        fragment = BeautifulSoup(
            '<div><p class="review-text">人気で予約が取れない。超予約困難です。'
            '&lt;script&gt;表示のみ&lt;/script&gt;</p>'
            '<p class="review-param">予約困難ではない。</p>'
            '<p class="review-param">予約困難になる前に。</p></div>',
            "html.parser",
        )
        original = fragment.get_text()
        highlight_booking_text(fragment, fragment.div)
        self.assertEqual(fragment.get_text(), original)
        self.assertEqual(
            [mark.get_text() for mark in fragment.select("mark.booking-highlight")],
            ["人気", "予約が取れない", "超予約困難"],
        )
        self.assertFalse(fragment.select("script"))
        for entry in self.page.select(".source-report .evidence-record"):
            for paragraph in entry.select(".saved-evidence-text"):
                if "超予約困難" in paragraph.get_text():
                    self.assertIsNotNone(paragraph.select_one("mark.booking-highlight"))
        self.assertGreater(len(self.page.select("mark.booking-highlight")), 100)

    def test_unfilled_booking_context_is_not_evidence_or_highlighted(self):
        for text in (
            "しかし当日まで予約が埋まる気配が全くないのが少し気にかかる。",
            "予約が埋まる気配がない。",
            "予約が埋まる様子はほとんどありません。",
            "予約が埋まるわけではない。",
            "予約困難というわけではない。",
        ):
            with self.subTest(text=text):
                self.assertEqual(booking_matches(text), [])
                fragment = BeautifulSoup('<div><p class="review-text"></p></div>', "html.parser")
                fragment.p.string = text
                highlight_booking_text(fragment, fragment.div)
                self.assertEqual(fragment.p.get_text(), text)
                self.assertFalse(fragment.select("mark"))
        for text in (
            "予約が埋まる気配がある。",
            "予約が埋まるので早めに連絡した。",
            "予約が埋まる気配が全くない。翌月は予約困難だった。",
        ):
            with self.subTest(text=text):
                self.assertEqual(len(booking_matches(text)), 1)

    def test_popular_and_soldout_categories_keep_easy_booking_candidates(self):
        for text, expected in (
            ("人気嬢の割に予約が取りやすい。", {"popular"}),
            ("人気嬢だが予約困難ではない。", {"popular"}),
            ("予約困難ではないが人気です。", {"popular"}),
            ("出勤前に完売している事が多い。", {"soldout"}),
            ("連日完売の人気嬢。", {"soldout", "popular"}),
            ("予約困難な人気嬢。", {"difficulty", "popular"}),
            ("人気のない子。", set()),
            ("完売ではない。", set()),
            ("人気になる前に。", set()),
            ("予約が埋まる気配が全くない。", set()),
            ("お風呂でゆっくり恋人気分を楽しみました。", set()),
            ("トークで恋人気分にさせてくれる。", set()),
            ("不人気ではない。", set()),
            ("恋人気分を楽しめる人気嬢。", {"popular"}),
        ):
            with self.subTest(text=text):
                self.assertEqual({category for _, category in candidate_matches(text)}, expected)
        for person, card in zip(self.people, self.page.select("#people > .person")):
            expected = {category for source in person["sources"] for category in source["categories"]}
            self.assertEqual(set(card["data-categories"].split()), expected)

    def test_popularity_highlight_does_not_split_lover_compounds(self):
        fragment = BeautifulSoup('<div><p class="review-text"></p></div>', "html.parser")
        text = "恋人気分を楽しめる。不人気ではない。人気嬢の割に予約が取りやすい。"
        fragment.p.string = text
        highlight_booking_text(fragment, fragment.div)
        self.assertEqual(fragment.p.get_text(), text)
        marks = fragment.select("mark.highlight-popular")
        self.assertEqual([mark.get_text() for mark in marks], ["人気"])
        for mark in self.page.select("mark.highlight-popular"):
            previous = str(mark.previous_sibling or "")
            self.assertFalse(previous.endswith(("恋", "不")), previous)

    def test_audited_negations_predictions_and_other_subjects_are_excluded(self):
        for text in (
            "普段から全然予約が埋まっていないので不安。",
            "予約が埋まって無い。",
            "人気嬢ではない為予約完売もなく。",
            "他にお目当ての嬢がいたのですが、完売していたため、まおみさんを指名しました。",
            "元々別店の人気嬢に当欠をくらったので、適当に入った。",
            "お目当ての他店の人気嬢が、どの嬢も完売もしくは休みだったので、仕方なく選んだ。",
            "初週は完売しそう。",
            "今だに即完売にならない。",
            "なぜこの娘が完売続きにならないのか不思議。",
            "人気が出そうなので、ほっといても予約が埋まりそうです。",
            "もう一人気になっていた当日出勤予定だったしょうこ。",
            "金津園で人気店。",
            "金津園の人気大衆店。",
            "あまり人気は出ないかもしれない。",
        ):
            with self.subTest(text=text):
                self.assertEqual(candidate_matches(text), [])
        for text in (
            "他店の人気嬢がこの店に移籍して来た。",
            "人気嬢の割に予約が取りやすい。",
            "人気の割には空いてる不思議な娘。",
            "以前は予約困難ではない。今は予約困難です。",
        ):
            with self.subTest(text=text):
                fields = analyze_record("okinilove", {"girl_name": "例", "report_text": text})
                self.assertTrue(fields)
                self.assertTrue(any(not match["reason"] for field in fields for match in field["matches"]))

    def test_ambiguous_evidence_is_reviewed_without_hiding_valid_support(self):
        fields = analyze_record("okinilove", {
            "girl_name": "例", "report_text": "他店の人気嬢と比べると静か。予約困難です。"
        })
        matches = fields[0]["matches"]
        self.assertTrue(next(match for match in matches if match["category"] == "popular")["reason"])
        self.assertFalse(next(match for match in matches if match["category"] == "difficulty")["reason"])
        equipment = analyze_record("ns_senmon", {
            "name": "例", "details": {"待合室について（コメント）": "お店は人気があるようです。"}
        })
        self.assertTrue(equipment[0]["matches"][0]["reason"])
        malformed = [person for person in self.people if person["name"] == "スタイリッシュバッハ"]
        self.assertTrue(malformed)
        self.assertTrue(all(person["needs_review"] and person["identity_issues"] for person in malformed))
        retained = [
            source for person in self.people for source in person["sources"]
            if source["source_url"] == "https://okinilove.com/blog-entry-2664.html#keniti"
        ]
        self.assertEqual(len(retained), 1)
        self.assertFalse(retained[0]["needs_review"])

    def test_saved_evidence_is_exact_safe_and_covers_every_entry(self):
        for person, card in zip(self.people, self.page.select("#people > .person")):
            for entry in card.select(".evidence-record"):
                expected = {
                    (field["label"], field["text"])
                    for source in person["sources"]
                    if source["database"] == entry.find_parent(class_="source-report")["data-database"]
                    and record_url_key(source["source_url"]) == record_url_key(entry["data-source-url"])
                    for field in source["fields"]
                }
                actual = {
                    (field.h5.get_text(), field.select_one(".saved-evidence-text").get_text())
                    for field in entry.select(".evidence-field")
                }
                self.assertEqual(actual, expected, person["name"])
                self.assertIsNotNone(entry.select_one(".saved-evidence mark"), person["name"])
        fragment = BeautifulSoup('<div><div class="review-detail"></div></div>', "html.parser")
        record = {"girl_name": "例", "report_text": '<script>alert("x")</script>人気です。'}
        sources = [{"fields": analyze_record("okinilove", record)}]
        views = ReportViews.__new__(ReportViews)
        views.append_saved_evidence(fragment, fragment.div, sources)
        self.assertFalse(fragment.select("script"))
        self.assertEqual(fragment.select_one(".saved-evidence-text").get_text(), record["report_text"])
        self.assertIsNotNone(fragment.select_one(".evidence-discrepancy"))

    def test_relative_scores_use_ties_deduplication_and_small_sample_correction(self):
        records = [
            {"girl_url": "https://example.invalid/a", "url": "http://example.invalid/1", "total_point": 100},
            {"girl_url": "https://example.invalid/a", "url": "https://example.invalid/1", "total_point": 100},
            {"girl_url": "https://example.invalid/b", "url": "https://example.invalid/2", "total_point": 100},
            {"girl_url": "https://example.invalid/c", "url": "https://example.invalid/3", "total_point": 0},
            {"girl_url": "https://example.invalid/d", "url": "https://example.invalid/4", "total_point": None},
        ]
        scores = relative_scores("tokai_fuzoku", records)
        self.assertEqual(len(scores), 3)
        keys = [
            candidate_person_key("tokai_fuzoku", {"girl_url": f"https://example.invalid/{key}"}, 1)
            for key in "abc"
        ]
        a, b, c = [scores[key] for key in keys]
        self.assertEqual(a["count"], 1)
        self.assertEqual(a["percentile"], 75)
        self.assertEqual(a["score"], 56.25)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(c["percentile"], 0)
        self.assertEqual(c["score"], 37.5)
        singleton = relative_scores("tokai_fuzoku", records[:1])
        self.assertEqual(next(iter(singleton.values()))["score"], 50)
        records.extend(
            {"girl_url": "https://example.invalid/b", "url": f"https://example.invalid/more-{i}", "total_point": 100}
            for i in range(3)
        )
        scores = relative_scores("tokai_fuzoku", records)
        self.assertGreater(scores[keys[1]]["score"], a["score"])
        self.assertTrue(all(0 <= rating["score"] <= 100 for rating in scores.values()))
        self.assertEqual(rating_value("okinilove", {"evaluations": "評価 ★★★★☆"}), 4)
        self.assertEqual(
            rating_value("ns_senmon", {"details": {"総合満足度（点数）": "90点"}}), 90
        )
        for invalid in (None, "", "unknown", float("nan"), float("inf"), -1, 101, True):
            self.assertIsNone(rating_value("tokai_fuzoku", {"total_point": invalid}))

    def test_combined_scores_weight_databases_equally_and_keep_missing_unrated(self):
        for person, card in zip(self.people, self.page.select("#people > .person")):
            ratings = list(person["ratings"].values())
            self.assertEqual(person["rated_count"], sum(rating["count"] for rating in ratings))
            if ratings:
                expected = sum(rating["score"] for rating in ratings) / len(ratings)
                self.assertAlmostEqual(person["score"], expected)
                self.assertAlmostEqual(float(card["data-score"]), expected, places=7)
            else:
                self.assertIsNone(person["score"])
                self.assertEqual(card["data-score"], "")
                self.assertIn("未評価", card.select_one(".relative-score").get_text())

    def test_browser_interactions_and_mobile_layout(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (390, 1280):
                with self.subTest(width=width):
                    context = browser.new_context(viewport={"width": width, "height": 844})
                    page = context.new_page()
                    errors, requests = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: requests.append(request.url))

                    def respond(route):
                        parsed = urlsplit(route.request.url)
                        if parsed.netloc != "report.invalid":
                            route.abort()
                        elif parsed.path == "/reservation_difficulty.html":
                            route.fulfill(content_type="text/html", body=self.html)
                        elif parsed.path in ("/common.css", "/common.js"):
                            path = ROOT / "docs" / parsed.path.lstrip("/")
                            route.fulfill(
                                content_type="text/css" if path.suffix == ".css" else "text/javascript",
                                body=path.read_text(encoding="utf-8"),
                            )
                        else:
                            route.abort()

                    context.route("**/*", respond)
                    page.goto("https://report.invalid/reservation_difficulty.html")
                    expect(page.locator(".person")).to_have_count(len(self.people))
                    category_filter = page.locator("#categoryFilter")
                    expect(category_filter).to_have_value("booking")
                    review_filter = page.locator("#reviewFilter")
                    expect(review_filter).to_have_value("candidate")
                    expected_default = sum(
                        card["data-review"] == "candidate"
                        and bool({"difficulty", "soldout"} & set(card["data-candidate-categories"].split()))
                        for card in self.page.select("#people > .person")
                    )
                    expect(page.locator(".person:visible")).to_have_count(expected_default)
                    category_filter.select_option("")
                    for review in ("review", "candidate", ""):
                        review_filter.select_option(review)
                        expected_count = sum(
                            not review or card["data-review"] == review
                            for card in self.page.select("#people > .person")
                        )
                        expect(page.locator(".person:visible")).to_have_count(expected_count)
                    for category in ("booking", "difficulty", "soldout", "popular", ""):
                        category_filter.select_option(category)
                        expected_count = sum(
                            not category or (
                                bool({"difficulty", "soldout"} & set(card["data-categories"].split()))
                                if category == "booking"
                                else category in card["data-categories"].split()
                            )
                            for card in self.page.select("#people > .person")
                        )
                        expect(page.locator(".person:visible")).to_have_count(expected_count)
                    category_filter.select_option("")
                    sort_order = page.locator("#sortOrder")
                    expect(sort_order).to_have_value("score")
                    for mode in ("score", "mentions", "original"):
                        sort_order.select_option(mode)
                        actual = page.locator("#people > .person").evaluate_all(
                            "items => items.map(item => ({score:item.dataset.score, count:Number(item.dataset.ratedCount), mentions:Number(item.dataset.mentions), name:item.querySelector('h2').textContent}))"
                        )
                        if mode == "score":
                            values = [
                                (float(row["score"]) if row["score"] else -1, row["count"])
                                for row in actual
                            ]
                            self.assertEqual(values, sorted(values, reverse=True))
                        elif mode == "mentions":
                            values = [row["mentions"] for row in actual]
                            self.assertEqual(values, sorted(values, reverse=True))
                        else:
                            self.assertEqual(
                                [row["name"] for row in actual],
                                [card.h2.get_text() for card in self.page.select("#people > .person")],
                            )
                    row = page.locator(".person-row:visible").first
                    button = row.locator("button.toggle")
                    expect(button).to_have_attribute("aria-expanded", "false")
                    button.click()
                    expect(button).to_have_attribute("aria-expanded", "true")
                    expect(row.locator("xpath=following-sibling::tr[1]")).to_be_visible()
                    button.focus()
                    page.keyboard.press("Space")
                    expect(button).to_have_attribute("aria-expanded", "false")
                    page.locator("#personSearch").fill("__no_person_matches_this__")
                    expect(page.locator("#noResults")).to_be_visible()
                    page.locator("#personSearch").fill("")
                    expect(page.locator(".person:visible")).to_have_count(len(self.people))
                    age_filter = page.locator("#ageFilter")
                    expect(age_filter.locator("option")).to_have_text(
                        ["指定なし", "30歳未満", "30歳以上", "年齢不明"]
                    )
                    for category in ("under30", "30plus", "unknown"):
                        age_filter.select_option(category)
                        expected_count = page.evaluate("""category => [...document.querySelectorAll('.person')]
                          .filter(person => {
                            const ages = [...person.querySelectorAll('.person-row td:nth-child(2) .age, .person-identity')]
                              .map(node => Number(node.textContent.match(/([0-9]+)\\s*[歳才]/)?.[1] || 0))
                              .filter(age => age > 0);
                            return category === 'unknown' ? !ages.length
                              : ages.some(age => category === 'under30' ? age < 30 : age >= 30);
                          }).length""", category)
                        expect(page.locator(".person:visible")).to_have_count(expected_count)
                        expect(page.locator("#visibleCount")).to_have_text(
                            f"{expected_count} / {len(self.people)} 人"
                        )
                        page.locator("#personSearch").fill("__no_person_matches_this__")
                        expect(page.locator(".person:visible")).to_have_count(0)
                        page.locator("#personSearch").fill("")
                    age_filter.select_option("")
                    expect(page.locator(".person:visible")).to_have_count(len(self.people))
                    self.assertLessEqual(
                        page.evaluate("document.documentElement.scrollWidth"), width,
                        "page must not scroll horizontally on mobile",
                    )
                    linked = self.page.select_one(
                        '.person[data-review="review"] .source-report[data-database="オキニラブ"] .evidence-record'
                    )["data-source-url"]
                    page.goto(
                        "https://report.invalid/reservation_difficulty.html?record="
                        + quote(linked, safe="")
                    )
                    matching = page.locator(".evidence-record").evaluate_all(
                        "(items, url) => items.findIndex(item => item.dataset.sourceUrl === url)", linked
                    )
                    target = page.locator(".evidence-record").nth(matching)
                    expect(target).to_be_visible()
                    expect(review_filter).to_have_value("")
                    expect(target.locator(".review-body")).to_have_attribute("open", "")
                    self.assertFalse(any("review_bodies" in url for url in requests))
                    self.assertEqual(errors, [])
                    context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
