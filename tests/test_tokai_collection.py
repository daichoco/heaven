import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "tokai_fuzoku"))

import collect_fuzoku_tokai as collect
import report_store
from integrations import report_left_merge
from tokai_fuzoku import generate_report_final as tokai

BASE = "https://m.fuzoku-tokai.com/"
GIRL_A = "https://www.cityheaven.net/gifu/A2101/A210101/shop/girlid-111/?of=y"
GIRL_B = "https://www.cityheaven.net/gifu/A2101/A210101/shop/girlid-222/"


def article_url(day, post_id):
    return f"{BASE}2026/01/{day:02d}/post-{post_id}/"


def article_html(
    date="2026-01-02",
    *,
    name="あや",
    girl_url=GIRL_A,
    repeat="返す（リピートする）",
    total="80",
    style=True,
    shop="店A",
):
    style_html = (
        "<p>◆姫のスタイル・肌（点数）：<font>70点</font></p>"
        "<p>◆姫のスタイル（コメント）：</p><p>細い</p>"
        if style
        else ""
    )
    total_html = f"<p>◆総合満足度（点数）：<font>{total}点</font></p>" if total is not None else ""
    return f"""<html><body>
<time class="entry-date" datetime="{date}T10:00:00+09:00"></time>
<a rel="category tag" href="/category/x/">{shop}</a>
<p><strong>店舗ジャンル：</strong>ソープランド</p>
<p><strong>姫の名前：</strong><a href="{girl_url}">{name}姫</a></p>
<p><strong>姫の推定年齢：</strong>25才</p>
<p>◆姫の顔（点数）：<font>75点</font></p>
<p><strong>◆姫の顔（コメント）：</strong></p><p>かわいい</p>
<p><strong>姫の胸（推定カップ）：</strong>Cカップ</p>
{total_html}
<p>◆総合満足度（コメント）：</p><p>良かった</p>
{style_html}
<p>◆プレイ時間：90分</p><p>◆総額：30000円</p>
<p>{repeat}</p>
<p>◆裏を返す返さないの具体的な理由：</p><p>理由</p>
</body></html>"""


def listing_html(urls, last_page=None):
    links = "".join(f'<a href="{url}">x</a>' for url in urls)
    pagination = (
        f'<ul class="pagination"><li class="last"><a href="{BASE}page/{last_page}/">last</a></li></ul>'
        if last_page
        else ""
    )
    sidebar = (
        '<h3 class="widget_title sidebar_widget_title">人気</h3>'
        f'<ul><li><a href="{BASE}2020/01/01/post-1/">old</a></li></ul>'
    )
    return f"<html><body>{links}{pagination}{sidebar}</body></html>"


class FakeResponse:
    def __init__(self, url, text="", status_code=200, final_url=None):
        self.url = final_url or url
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return FakeResponse(url, "", 404)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, FakeResponse):
            return page
        return FakeResponse(url, page)


def write_records(path, records):
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def legacy_record(url, *, name="あや", girl_url=GIRL_A, date="2026-01-02", **extra):
    record = {
        "delete?": 0,
        "date": date,
        "type": "ソープランド",
        "shop_name": "店A",
        "name": name,
        "predict_age": "25才",
        "face_score": 75.0,
        "face_feature": "かわいい",
        "cup": "Cカップ",
        "repeat": "返す（リピートする）",
        "girl_url": girl_url,
        "url": url,
    }
    record.update(extra)
    return record


class UrlNormalizationTests(unittest.TestCase):
    def test_article_url_variants_are_canonical(self):
        expected = f"{BASE}2026/01/02/post-10/"
        for url in (
            "https://m.fuzoku-tokai.com/2026/01/02/post-10/",
            "http://fuzoku-tokai.com/2026/01/02/post-10",
            "https://WWW.fuzoku-tokai.com/2026/01/02/post-10/?amp=1#respond",
        ):
            self.assertEqual(report_store.canonical_article_url(url), expected)
        self.assertIsNone(report_store.canonical_article_url("https://m.fuzoku-tokai.com/2026/02/30/post-10/"))
        self.assertIsNone(report_store.canonical_article_url("https://example.com/2026/01/02/post-10/"))
        self.assertIsNone(report_store.canonical_article_url(f"{BASE}page/2/"))

    def test_profile_url_normalization_keeps_identity(self):
        normalize = report_store.normalize_profile_url
        self.assertEqual(normalize(GIRL_A), normalize(GIRL_A.replace("?of=y", "").upper().replace("/GIFU/A2101/A210101/SHOP/GIRLID-111/", "/gifu/A2101/A210101/shop/girlid-111")))
        self.assertEqual(
            normalize("http://Example.com/girls_detail?id=1&utm_source=x"),
            normalize("https://example.com/girls_detail/?id=1"),
        )
        self.assertNotEqual(
            normalize("https://www.k-aqua-jpn.com/girls_detail?id=1"),
            normalize("https://www.k-aqua-jpn.com/girls_detail?id=2"),
        )
        self.assertNotEqual(normalize("https://www.example.com/p/1"), normalize("https://example.com/p/1"))
        self.assertIsNone(normalize(None))
        self.assertIsNone(normalize(float("nan")))

    def test_person_key_never_merges_by_shop_and_name(self):
        first = legacy_record(article_url(1, 1), girl_url=GIRL_A)
        second = legacy_record(article_url(2, 2), girl_url=GIRL_B)
        without_url = legacy_record(article_url(3, 3), girl_url=None)
        self.assertNotEqual(report_store.person_key(first), report_store.person_key(second))
        self.assertIsNone(report_store.person_key(without_url))


class ParseTests(unittest.TestCase):
    def test_parse_keeps_not_return_reports_and_details(self):
        soup = BeautifulSoup(article_html(repeat="返さない（リピートしない）", style=False), "lxml")
        record = collect.parse_report_page(soup, "http://fuzoku-tokai.com/2026/01/02/post-5")
        self.assertEqual(record["repeat"], "返さない（リピートしない）")
        self.assertEqual(record["url"], f"{BASE}2026/01/02/post-5/")
        self.assertEqual(record["date"], "2026-01-02")
        self.assertEqual(record["name"], "あや")
        details = report_store.parse_report_details(soup)
        self.assertEqual(details["total_point"], 80)
        self.assertEqual(details["total_comment"], "良かった")
        self.assertIsNone(details["style_score"])
        self.assertIsNone(details["style_comment"])
        self.assertEqual(details["play_time"], "◆プレイ時間：90分")
        self.assertEqual(details["total_cost"], "◆総額：30000円")
        self.assertEqual(details["repeat_comment"], "理由")

    def test_listing_extracts_canonical_urls_without_sidebar_or_comments(self):
        html = listing_html(
            [
                "http://fuzoku-tokai.com/2026/01/02/post-10",
                f"{BASE}2026/01/02/post-10/#comments",
                f"{BASE}2026/01/02/post-11/",
            ]
        )
        urls = collect.extract_article_urls(BeautifulSoup(html, "lxml"))
        self.assertEqual(urls, {f"{BASE}2026/01/02/post-10/", f"{BASE}2026/01/02/post-11/"})


class ReportStoreTests(unittest.TestCase):
    def test_unchanged_legacy_file_is_not_rewritten_and_upsert_merges_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.jsonl"
            legacy = legacy_record(article_url(2, 10), **{"delete?": 1, "custom": "keep"})
            legacy["face_score"] = 60.0
            write_records(path, [legacy])
            original = path.read_bytes()

            store = report_store.ReportStore(path)
            self.assertFalse(store.save())
            self.assertEqual(path.read_bytes(), original)

            fresh = legacy_record("http://fuzoku-tokai.com/2026/01/02/post-10", **{"delete?": 0})
            fresh.update({"face_score": 99, "total_point": 80, "style_score": None, "fetched_at": "t1"})
            self.assertEqual(store.upsert(fresh), "updated")
            self.assertEqual(len(store), 1)
            row = store.get(article_url(2, 10))
            self.assertEqual(row["delete?"], 1)
            self.assertEqual(row["face_score"], 60.0)
            self.assertEqual(row["custom"], "keep")
            self.assertEqual(row["total_point"], 80)
            self.assertNotIn("style_score", row)
            self.assertEqual(row["fetched_at"], "t1")
            self.assertEqual(store.upsert(fresh), "unchanged")

            store.save()
            saved = report_store.read_jsonl_records(path)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["delete?"], 1)

    def test_duplicate_urls_in_file_are_collapsed_to_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.jsonl"
            write_records(
                path,
                [
                    legacy_record(article_url(2, 10), name="first"),
                    legacy_record("http://fuzoku-tokai.com/2026/01/02/post-10", name="second"),
                ],
            )
            store = report_store.ReportStore(path)
            self.assertEqual(store.dropped_duplicates, 1)
            self.assertEqual([r["name"] for r in store.records], ["first"])


class ProfileSignalTests(unittest.TestCase):
    SABBAT = "https://www.cityheaven.net/gifu/A2101/A210101/sabbat/girlid-57814696/"
    SHABOOLE = "https://www.shaboole.com/profile/01722/"
    BACH = "https://www.stylish-bach.com/top/girls/girldetail/?girl_id=64733072"
    RGROUP = "https://r-group.jp/profile?lid=9"
    SHABOOLE_EMPTY = "<html><head><title>名古屋愛知風俗デリヘル シャブール｜[]プロフィール</title></head><body>名前 年齢 才</body></html>"
    SHABOOLE_OK = "<html><head><title>名古屋愛知風俗デリヘル シャブール｜なな[可愛い系]プロフィール</title></head><body>なな</body></html>"
    BACH_EMPTY = '<html><body><div class="profile">10月 5日 入店 愛桜/まお</div></body></html>'
    BACH_OK = '<html><body><div class="element-girl-detail"><div class="profilename">りら/LILA (21歳)</div></div></body></html>'

    def test_classify(self):
        import profile_signals

        classify = profile_signals.classify_profile_page
        self.assertEqual(classify(self.SABBAT, self.SABBAT, 200, "<html>るな♡【PREMIUM】</html>")[0], "published")
        self.assertEqual(classify(self.SABBAT, self.SABBAT + "?of=y", 200, "<html></html>")[0], "published")
        self.assertEqual(
            classify(self.SABBAT, "https://www.cityheaven.net/gifu/A2101/A210101/sabbat/", 200, "")[0], "unavailable"
        )
        self.assertEqual(classify(self.SABBAT, self.SABBAT, 404, "")[0], "unavailable")
        self.assertEqual(classify(self.SABBAT, self.SABBAT, 503, "")[0], "unknown")
        self.assertEqual(classify(self.SHABOOLE, self.SHABOOLE, 200, self.SHABOOLE_EMPTY)[0], "unavailable")
        self.assertIsNone(classify(self.SHABOOLE, self.SHABOOLE, 200, self.SHABOOLE_OK)[0])
        self.assertEqual(classify(self.BACH, self.BACH, 200, self.BACH_EMPTY)[0], "unavailable")
        self.assertIsNone(classify(self.BACH, self.BACH, 200, self.BACH_OK)[0])
        self.assertIsNone(classify(self.RGROUP, self.RGROUP, 200, "<title>橘　真央プロフィール</title>")[0])
        self.assertIsNone(
            classify("http://example.com/girl/1", "https://sp.example.com/girl/1/", 200, "<html></html>")[0]
        )
        self.assertEqual(
            classify("https://example.com/girl?id=1", "https://example.com/girl?id=2", 200, "")[0], "unavailable"
        )

    def test_decode_ignores_requests_latin1_default(self):
        import profile_signals

        raw = self.SHABOOLE_EMPTY.encode("utf-8")
        self.assertEqual(profile_signals.decode_html(raw, "ISO-8859-1"), self.SHABOOLE_EMPTY)
        sjis = '<meta charset="Shift_JIS"><title>なな</title>'.encode("shift_jis")
        self.assertIn("なな", profile_signals.decode_html(sjis, None))

        class BytesResponse(FakeResponse):
            encoding = "ISO-8859-1"

            @property
            def content(self):
                return self.text.encode("utf-8")

        session = FakeSession({self.SHABOOLE: BytesResponse(self.SHABOOLE, self.SHABOOLE_EMPTY)})
        checker = collect.ProfileChecker(session, driver_factory=lambda: self.fail("browser not needed"))
        self.assertEqual(checker.check(self.SHABOOLE, "みく"), "unavailable")

    def test_checker_uses_signals_and_name_mismatch_is_unknown(self):
        session = FakeSession(
            {
                self.SABBAT: "<html><title>「るな♡【PREMIUM】」Sabbat</title></html>",
                self.SHABOOLE: self.SHABOOLE_EMPTY,
                self.BACH: self.BACH_EMPTY,
                self.RGROUP: "<html><title>橘　真央プロフィール</title>橘　真央</html>",
            }
        )
        checker = collect.ProfileChecker(session, driver_factory=lambda: (_ for _ in ()).throw(RuntimeError("no chrome")))
        self.assertEqual(checker.check(self.SABBAT, "全然違う名前"), "published")
        self.assertEqual(checker.check(self.SHABOOLE, "みく"), "unavailable")
        self.assertEqual(checker.check(self.BACH, "あず"), "unavailable")
        self.assertEqual(checker.check(self.RGROUP, "橘　奈央"), "unknown")
        self.assertEqual(checker.check(self.RGROUP, "橘真央"), "published")

    def test_generator_filter_matches_policy(self):
        class StreamResponse(FakeResponse):
            encoding = "utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_content(self, chunk_size):
                yield self.text.encode("utf-8")

        pages = {
            self.SABBAT: StreamResponse(self.SABBAT, "<html>るな♡【PREMIUM】</html>"),
            self.SHABOOLE: StreamResponse(self.SHABOOLE, self.SHABOOLE_EMPTY),
            self.BACH: StreamResponse(self.BACH, self.BACH_EMPTY),
            self.RGROUP: StreamResponse(self.RGROUP, "<html>橘　真央</html>"),
            "https://example.com/girl/9": StreamResponse(
                "https://example.com/girl/9", "<html>list</html>", final_url="https://example.com/girls/"
            ),
            "https://example.com/girl/8": StreamResponse("https://example.com/girl/8", "", 429),
        }

        class StreamSession(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append(url)
                return pages[url]

        frame = pd.DataFrame(
            [
                legacy_record(article_url(1, 1), name="るな【PLATINUM】", girl_url=self.SABBAT),
                legacy_record(article_url(2, 2), name="みく", girl_url=self.SHABOOLE),
                legacy_record(article_url(3, 3), name="あず", girl_url=self.BACH),
                legacy_record(article_url(4, 4), name="橘　奈央", girl_url=self.RGROUP),
                legacy_record(article_url(5, 5), name="えま", girl_url="https://example.com/girl/9"),
                legacy_record(article_url(6, 6), name="りこ", girl_url="https://example.com/girl/8"),
            ]
        )
        filtered = tokai.filter_unavailable_girl_urls(frame, StreamSession({}))
        self.assertEqual(
            list(filtered["url"]), [article_url(1, 1), article_url(4, 4), article_url(6, 6)]
        )

    def test_o_ren_cast_url_without_person_profile_is_unavailable(self):
        class StreamResponse(FakeResponse):
            encoding = "utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_content(self, chunk_size):
                yield self.text.encode("utf-8")

        ended_url = "https://www.o-ren-group.com/kanazuen/vivi/castid-5001486101/"
        active_url = "https://www.o-ren-group.com/kanazuen/vivi/castid-5001486102/"

        class StreamSession(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append(url)
                return StreamResponse(
                    url,
                    "<html><body>キャスト一覧</body></html>"
                    if url == ended_url
                    else "<html><body><h1>るか</h1>プロフィール</body></html>",
                )

        frame = pd.DataFrame(
            [
                legacy_record(article_url(1, 1), name="るか", girl_url=ended_url),
                legacy_record(article_url(2, 2), name="るか", girl_url=active_url),
            ]
        )
        filtered = tokai.filter_unavailable_girl_urls(frame, StreamSession({}))
        self.assertEqual(list(filtered["girl_url"]), [active_url])


class NameMatchTests(unittest.TestCase):
    def test_rank_tags_decorations_and_readings(self):
        from wait_for_name_parts import base_name, name_in_text

        self.assertEqual(base_name("るな【PLATINUM】"), "るな")
        self.assertEqual(base_name("TSUKASA（ツカサ）"), "tsukasa")
        self.assertTrue(name_in_text("プロフィール るな♡【PREMIUM】 22歳", "るな【PLATINUM】"))
        self.assertTrue(name_in_text("ＴＳＵＫＡＳＡ プロフィール", "TSUKASA（ツカサ）"))
        self.assertTrue(name_in_text("桃花 プロフィール", "桃花（ももか）"))
        self.assertTrue(name_in_text("★ゆ☆め♪ 21歳", "ゆめ［新人］"))
        self.assertTrue(name_in_text("橘 奈央", "橘　奈央"))
        self.assertFalse(name_in_text("橘 真央", "橘　奈央"))
        self.assertFalse(name_in_text("プロフィール ちゃん[才カップ]", "みく"))

    def test_short_base_names_keep_strict_matching(self):
        from wait_for_name_parts import name_in_text

        self.assertFalse(name_in_text("みなみ 在籍", "み【新人】"))
        self.assertTrue(name_in_text("み【新人】 在籍", "み【新人】"))
        self.assertFalse(name_in_text("A 在籍", "A（エー）"))
        self.assertTrue(name_in_text("A（エー）", "A（エー）"))


class FakeDriver:
    def __init__(self, error=None):
        self.error = error
        self.urls = []
        self.quit_called = False

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error

    def quit(self):
        self.quit_called = True


class ProfileCheckerTests(unittest.TestCase):
    def test_failures_and_limits_are_not_unavailable(self):
        session = FakeSession(
            {
                "https://a.example/rate": FakeResponse("https://a.example/rate", "", 429),
                "https://a.example/forbidden": FakeResponse("https://a.example/forbidden", "", 403),
                "https://a.example/error": requests.ConnectionError("down"),
                "https://a.example/redirect": FakeResponse(
                    "https://a.example/redirect", "あや", 200, final_url="https://a.example/cast/"
                ),
                "https://a.example/slash": FakeResponse(
                    "https://a.example/slash", "あや", 200, final_url="https://a.example/slash/"
                ),
            }
        )
        checker = collect.ProfileChecker(session, driver_factory=lambda: self.fail("browser not needed"))
        self.assertEqual(checker.check("https://a.example/rate", "あや"), "unknown")
        self.assertEqual(checker.check("https://a.example/forbidden", "あや"), "unknown")
        self.assertEqual(checker.check("https://a.example/error", "あや"), "unknown")
        self.assertEqual(checker.check("https://a.example/missing", "あや"), "unavailable")
        self.assertEqual(checker.check("https://a.example/redirect", "あや"), "unavailable")
        self.assertEqual(checker.check("https://a.example/slash", "あや"), "published")

    def test_browser_results(self):
        session = FakeSession({"https://a.example/js": "<html>loading</html>"})
        with patch.object(collect, "wait_for_name_parts", side_effect=TimeoutException()):
            checker = collect.ProfileChecker(session, driver_factory=FakeDriver)
            self.assertEqual(checker.check("https://a.example/js", "あや"), "unknown")

        broken = FakeDriver(error=WebDriverException("crash"))
        checker = collect.ProfileChecker(session, driver_factory=lambda: broken)
        self.assertEqual(checker.check("https://a.example/js", "あや"), "unknown")
        self.assertTrue(broken.quit_called)

        def failing_factory():
            raise RuntimeError("no chrome")

        checker = collect.ProfileChecker(session, driver_factory=failing_factory)
        self.assertEqual(checker.check("https://a.example/js", "あや"), "unknown")

        with patch.object(collect, "wait_for_name_parts", return_value=True):
            checker = collect.ProfileChecker(session, driver_factory=FakeDriver)
            self.assertEqual(checker.check("https://a.example/js", "あや"), "published")


class CollectorTestBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_path = Path(self.directory.name) / "reports.jsonl"
        self.state_path = Path(self.directory.name) / "state.json"
        self.clock_ticks = 0

    def clock(self):
        self.clock_ticks += 1
        return f"2026-01-10T00:00:{self.clock_ticks:02d}+00:00"

    def make_collector(self, pages):
        session = FakeSession(pages)
        store = report_store.ReportStore(self.data_path)
        state = collect.CollectState(self.state_path)
        checker = collect.ProfileChecker(session, driver_factory=lambda: self.fail("browser not needed"))
        collector = collect.Collector(session, store, state, checker, clock=self.clock, sleep=lambda _: None)
        return collector, session


class IncrementalTests(CollectorTestBase):
    def test_same_day_posts_and_multiple_reports_per_person_are_kept(self):
        known = article_url(5, 50)
        write_records(self.data_path, [legacy_record(known, date="2026-01-05")])
        same_day = article_url(5, 51)
        second_report = article_url(4, 49)
        not_return = article_url(3, 48)
        too_old = article_url(1, 40)
        pages = {
            BASE: listing_html([known, same_day, second_report], last_page=3),
            f"{BASE}page/2/": listing_html([not_return]),
            f"{BASE}page/3/": listing_html([too_old]),
            same_day: article_html("2026-01-05", total="90"),
            second_report: article_html("2026-01-04", total=None),
            not_return: article_html("2026-01-03", name="みく", girl_url=GIRL_B, repeat="返さない（リピートしない）"),
            GIRL_A: "<html>あや</html>",
            GIRL_B.rstrip("/"): "<html>みく</html>",
            GIRL_B: "<html>みく</html>",
        }
        collector, session = self.make_collector(pages)
        collect.run_incremental(collector, max_pages=75, overlap_days=3)
        collector.checkpoint()

        self.assertEqual(collector.counts["added"], 3)
        self.assertEqual(collector.counts["failed"], 0)
        self.assertNotIn(known, session.calls)
        self.assertNotIn(too_old, session.calls)
        self.assertEqual(session.calls.count(GIRL_A), 1)

        saved = {r["url"]: r for r in report_store.read_jsonl_records(self.data_path)}
        self.assertEqual(set(saved), {known, same_day, second_report, not_return})
        self.assertEqual(saved[not_return]["repeat"], "返さない（リピートしない）")
        self.assertIsNone(saved[second_report]["total_point"])
        self.assertEqual(saved[same_day]["total_point"], 90)
        self.assertEqual(saved[same_day]["profile_status"], "published")
        self.assertEqual(
            report_store.person_key(saved[same_day]), report_store.person_key(saved[second_report])
        )

    def test_failures_are_counted_and_retried_next_run(self):
        target = article_url(6, 60)
        pages = {BASE: listing_html([target]), target: FakeResponse(target, "", 503)}
        collector, _ = self.make_collector(pages)
        collect.run_incremental(collector, max_pages=75, overlap_days=3)
        collector.checkpoint()
        self.assertEqual(collector.counts["failed"], 1)
        self.assertEqual(collector.counts["added"], 0)
        self.assertIn(target, json.loads(self.state_path.read_text(encoding="utf-8"))["failed_urls"])

        pages[target] = article_html("2026-01-06")
        pages[GIRL_A] = "<html>あや</html>"
        collector, _ = self.make_collector(pages)
        collect.run_incremental(collector, max_pages=75, overlap_days=3)
        collector.checkpoint()
        self.assertEqual(collector.counts["added"], 1)
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8"))["failed_urls"], {})

    def test_login_page_aborts_instead_of_marking_non_report(self):
        target = article_url(6, 60)
        pages = {
            BASE: listing_html([target]),
            target: '<form><input name="password_protected_pwd"></form>',
        }
        collector, _ = self.make_collector(pages)
        with self.assertRaises(collect.LoginRequiredError):
            collect.run_incremental(collector, max_pages=75, overlap_days=3)
        self.assertFalse(collector.state.is_non_report(target))


class BackfillTests(CollectorTestBase):
    def test_backfill_limit_resume_and_max_pages(self):
        a1, a2, a3, non_report, beyond = (
            article_url(9, 90),
            article_url(8, 80),
            article_url(7, 70),
            article_url(6, 60),
            article_url(1, 10),
        )
        pages = {
            BASE: listing_html([a1, non_report], last_page=4),
            f"{BASE}page/2/": listing_html([a2]),
            f"{BASE}page/3/": listing_html([a3]),
            f"{BASE}page/4/": listing_html([beyond]),
            a1: article_html("2026-01-09"),
            a2: article_html("2026-01-08", repeat="返さない（リピートしない）"),
            a3: article_html("2026-01-07"),
            non_report: '<html><time class="entry-date" datetime="2026-01-06T00:00:00+09:00"></time>お知らせ</html>',
            beyond: article_html("2026-01-01"),
            GIRL_A: "<html>あや</html>",
        }
        write_records(self.data_path, [legacy_record(a1, date="2026-01-09")])

        collector, session = self.make_collector(pages)
        collect.run_backfill(collector, max_pages=3, limit=2)
        collector.checkpoint()
        self.assertEqual(collector.counts["non_report"], 1)
        self.assertEqual(collector.counts["added"], 1)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["backfill"]["resume_page"], 3)
        self.assertFalse(state["backfill"]["completed"])
        self.assertNotIn(a1, session.calls)

        collector, session = self.make_collector(pages)
        collect.run_backfill(collector, max_pages=3, resume=True)
        collector.checkpoint()
        self.assertEqual(collector.counts["added"], 1)
        self.assertNotIn(BASE + "page/2/", session.calls)
        self.assertNotIn(f"{BASE}page/4/", session.calls)
        self.assertNotIn(beyond, session.calls)
        self.assertNotIn(non_report, session.calls)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(state["backfill"]["completed"])

        saved = {r["url"] for r in report_store.read_jsonl_records(self.data_path)}
        self.assertEqual(saved, {a1, a2, a3})


class RecheckProfilesTests(CollectorTestBase):
    def test_recheck_updates_only_definitive_results(self):
        sabbat = "https://www.cityheaven.net/gifu/A2101/A210101/sabbat/girlid-57814696/"
        gone = "https://example.com/girl/2"
        flaky = "https://example.com/girl/3"
        fine = "https://example.com/girl/4"
        legacy = "https://example.com/girl/5"

        def row(day, post, name, girl_url, status, checked="2026-01-05T00:00:00+00:00"):
            extra = {"profile_status": status, "profile_checked_at": checked} if status else {}
            return legacy_record(article_url(day, post), name=name, girl_url=girl_url, **extra)

        write_records(
            self.data_path,
            [
                row(1, 1, "るな", sabbat, "published", "2026-01-01T00:00:00+00:00"),
                row(2, 2, "るな【PLATINUM】", sabbat, "unavailable"),
                row(3, 3, "ゆき", gone, "unavailable"),
                row(4, 4, "まい", flaky, "unavailable"),
                row(5, 5, "えま", fine, "published"),
                row(6, 6, "りこ", legacy, None),
            ],
        )
        pages = {
            sabbat: "<html>るな♡【PREMIUM】</html>",
            flaky: FakeResponse(flaky, "", 429),
            fine: "<html>えま</html>",
        }
        collector, session = self.make_collector(pages)
        collect.run_recheck_profiles(collector, ["unavailable"])
        collector.checkpoint()

        self.assertEqual(collector.counts["status_changed"], 1)
        self.assertEqual(collector.counts["profile_unknown"], 1)
        self.assertNotIn(fine, session.calls)
        self.assertNotIn(legacy, session.calls)
        self.assertEqual(session.calls.count(sabbat), 1)
        saved = {r["url"]: r for r in report_store.read_jsonl_records(self.data_path)}
        self.assertEqual(saved[article_url(1, 1)]["profile_status"], "published")
        self.assertEqual(saved[article_url(2, 2)]["profile_status"], "published")
        self.assertEqual(saved[article_url(2, 2)]["profile_checked_at"], saved[article_url(1, 1)]["profile_checked_at"])
        self.assertEqual(saved[article_url(3, 3)]["profile_status"], "unavailable")
        self.assertNotEqual(saved[article_url(3, 3)]["profile_checked_at"], "2026-01-05T00:00:00+00:00")
        self.assertEqual(saved[article_url(4, 4)]["profile_status"], "unavailable")
        self.assertEqual(saved[article_url(4, 4)]["profile_checked_at"], "2026-01-05T00:00:00+00:00")
        self.assertNotIn("profile_status", saved[article_url(6, 6)])
        self.assertEqual(
            report_store.latest_profile_status_by_person(saved.values())[report_store.normalize_profile_url(sabbat)],
            "published",
        )

    def test_recheck_cli_needs_no_password_and_validates_status(self):
        args = collect.parse_args(["--recheck-profiles", "--status", "unknown", "--status", "unavailable"])
        self.assertEqual(args.status, ["unknown", "unavailable"])
        self.assertEqual(collect.parse_args(["--recheck-profiles"]).status, ["unavailable"])
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            collect.parse_args(["--status", "unknown"])
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            collect.parse_args(["--recheck-profiles", "--backfill"])

        with (
            patch.object(collect, "OUTPUT_JSONL", self.data_path),
            patch.object(collect, "STATE_JSON", self.state_path),
            patch.object(collect, "login", side_effect=AssertionError("login used")),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("FUZOKU_TOKAI_PASSWORD", None)
            collect.main(["--recheck-profiles"])
        self.assertFalse(self.data_path.exists())


class FillDetailsTests(CollectorTestBase):
    def test_fill_details_only_fills_missing_values(self):
        target = article_url(2, 20)
        failing = article_url(3, 30)
        legacy = legacy_record(target, **{"delete?": 1})
        legacy["face_score"] = 60.0
        write_records(self.data_path, [legacy, legacy_record(failing)])
        pages = {target: article_html("2026-01-02")}
        collector, session = self.make_collector(pages)
        collect.run_fill_details(collector)
        collector.checkpoint()

        self.assertEqual(collector.counts["updated"], 1)
        self.assertEqual(collector.counts["failed"], 1)
        self.assertNotIn(GIRL_A, session.calls)
        saved = {r["url"]: r for r in report_store.read_jsonl_records(self.data_path)}
        self.assertEqual(saved[target]["delete?"], 1)
        self.assertEqual(saved[target]["face_score"], 60.0)
        self.assertEqual(saved[target]["total_point"], 80)
        self.assertIsNotNone(saved[target]["fetched_at"])
        self.assertNotIn("fetched_at", saved[failing])


class GeneratorTests(unittest.TestCase):
    def test_cityheaven_echo_profile_image(self):
        self.assertEqual(tokai.extract_profile_image_url(
            '<meta property="og:image" content="/img/favicon.png">'
            '<img data-echo="//img2.cityheaven.net/img/girls/t/shop/la_grpb1.jpg" alt="あや">',
            GIRL_A, "あや",
        ), "https://img2.cityheaven.net/img/girls/t/shop/la_grpb1.jpg")

    def test_offline_generation_uses_stored_details_and_profile_status(self):
        records = [
            legacy_record(article_url(1, 1)),
            legacy_record(
                article_url(2, 2),
                repeat="返さない（リピートしない）",
                total_point=None,
                total_comment="微妙",
                style_score=40,
                style_comment="普通",
                fetched_at="2026-01-10T00:00:00+00:00",
                profile_status="published",
                profile_checked_at="2026-01-10T00:00:00+00:00",
            ),
            legacy_record(
                article_url(3, 3),
                name="みく",
                girl_url=GIRL_B,
                profile_status="published",
                profile_checked_at="2026-01-01T00:00:00+00:00",
            ),
            legacy_record(
                article_url(4, 4),
                name="みく",
                girl_url=GIRL_B.rstrip("/"),
                profile_status="unavailable",
                profile_checked_at="2026-01-05T00:00:00+00:00",
            ),
            legacy_record(
                article_url(5, 5),
                name="みく",
                girl_url="https://other.example/girl/1",
                profile_status="unknown",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "reports.jsonl"
            html_path = Path(directory) / "tokai.html"
            write_records(data_path, records)
            with (
                patch.object(tokai, "INPUT_JSONL", data_path),
                patch.object(tokai, "OUTPUT_HTML", html_path),
                patch.dict(os.environ, {}, clear=False),
                patch.object(tokai.requests, "Session", side_effect=AssertionError("network used")),
            ):
                os.environ.pop("FUZOKU_TOKAI_PASSWORD", None)
                frame = tokai.load_input_data()
                tokai.main(["--offline"])
            html = html_path.read_text(encoding="utf-8")

        self.assertEqual(
            list(frame["url"]), [article_url(1, 1), article_url(2, 2), article_url(5, 5)]
        )
        soup = BeautifulSoup(html, "html.parser")
        entries = soup.select("details.report-entry")
        self.assertEqual(len(entries), 2)
        articles = soup.select("details.article-entry")
        self.assertEqual(len(articles), 3)
        not_return = next(article for article in articles if article["data-repeat"] == "not-return")
        self.assertEqual(not_return["data-repeat"], "not-return")
        self.assertEqual(not_return["data-total"], "")
        self.assertNotIn("総合 0", not_return.get_text())
        self.assertIn("スタイル: 40", not_return.get_text())
        self.assertIn("普通", not_return.get_text())

    def test_online_enrich_fetches_only_rows_without_stored_details(self):
        frame = pd.DataFrame(
            [
                legacy_record(article_url(1, 1)),
                legacy_record(article_url(2, 2), total_point=70, fetched_at="t"),
            ]
        )
        session = FakeSession({article_url(1, 1): article_html("2026-01-01", style=False)})
        enriched = tokai.enrich_report_data(frame, session)
        self.assertEqual(session.calls, [article_url(1, 1)])
        self.assertEqual(enriched.loc[0, "total_point"], 80)
        self.assertIsNone(enriched.loc[0, "style_score"])
        self.assertEqual(enriched.loc[1, "total_point"], 70)

    def test_online_profile_check_once_per_person(self):
        frame = pd.DataFrame(
            [
                legacy_record(article_url(1, 1), girl_url=GIRL_A),
                legacy_record(article_url(2, 2), girl_url=GIRL_A.replace("?of=y", "")),
                legacy_record(article_url(3, 3), name="みく", girl_url=GIRL_B),
            ]
        )

        class StreamResponse(FakeResponse):
            encoding = "utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_content(self, chunk_size):
                yield self.text.encode("utf-8")

        class StreamSession(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append(url)
                if url == GIRL_B:
                    return StreamResponse(url, "", 404)
                return StreamResponse(url, '<html><meta property="og:image" content="https://img.example/a.jpg"></html>')

        session = StreamSession({})
        filtered = tokai.filter_unavailable_girl_urls(frame, session)
        self.assertEqual(session.calls, [GIRL_A, GIRL_B])
        self.assertEqual(list(filtered["url"]), [article_url(1, 1), article_url(2, 2)])
        self.assertEqual(set(filtered["profile_image_url"]), {"https://img.example/a.jpg"})


class ReportHistoryTests(unittest.TestCase):
    def test_history_keeps_each_article_once(self):
        def report(report_url, girl_url=GIRL_A):
            return {
                "shop_name": "店A",
                "shop_type": "ソープランド",
                "girl_name": "あや",
                "girl_url": girl_url,
                "age": 25,
                "report_url": report_url,
                "evaluations": {"総合: 80": "良い"},
                "cityheaven_url": girl_url,
            }

        history = pd.DataFrame([report(article_url(1, 1))])
        current = pd.DataFrame(
            [
                report("http://fuzoku-tokai.com/2026/01/01/post-1"),
                report(article_url(2, 2)),
                report(article_url(2, 2)),
            ]
        )
        with patch.object(report_left_merge, "enrich_with_cityheaven_urls", side_effect=lambda df: df):
            updated, added = report_left_merge.append_new_reports(history, current)
            self.assertEqual(added, 1)
            self.assertEqual(list(updated["report_url"]), [article_url(1, 1), article_url(2, 2)])
            again, added_again = report_left_merge.append_new_reports(updated, current)
        self.assertEqual(added_again, 0)
        self.assertEqual(len(again), 2)


if __name__ == "__main__":
    unittest.main()
