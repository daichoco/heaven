import contextlib
import io
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kanto_fuzoku import collect_fuzoku_kanto as collect
from kanto_fuzoku import generate_report_final as generate
from kanto_fuzoku import report_store as store
from tokai_fuzoku import collect_fuzoku_tokai as shared
from tokai_fuzoku import generate_report_final as tokai_generate
from tokai_fuzoku import report_store as tokai_store
from tokai_fuzoku.html_render import generate_html

BASE = store.BASE_URL
PROFILE = "https://example.com/profile.html?uid=person1"


def url(number):
    return f"{BASE}2026/01/02/post-{number}/"


def article(*, repeat="返す（リピートする）", age="35才", link=True, score_tag="span"):
    name = f'<a href="{PROFILE}">テスト姫</a>' if link else "テスト姫"
    score = f'<{score_tag} style="color:red">顔80点</{score_tag}>'
    return f"""<html><time class="entry-date" datetime="2026-01-02T10:00:00+09:00"></time>
<a href="/category/test/" rel="category tag">架空店舗</a>
<div id="the-content" class="entry-content">
<p><strong>◆出撃店舗名：</strong> <a href="https://example.com/">架空店舗</a></p>
<p><strong>◆店舗ジャンル：</strong> ソープランド</p>
<p><strong>◆姫の名前：</strong> {name}</p>
<p><strong>◆姫の推定年齢：</strong> {age}</p>
<p><strong>◆姫の顔（点数）： {score}</strong></p>
<p><strong>◆姫の顔（コメント）：</strong></p><p>架空コメント</p>
<p><strong>◆姫の胸（推定カップ）：</strong> Cカップ</p>
<p><strong>◆姫のスタイル・肌（点数）： <span>スタイル70点</span></strong></p>
<p><strong>◆姫のスタイル（コメント）：</strong></p><p>架空の段落1</p><p>架空の段落2</p>
<p><strong>◆パネマジ度（点数）： <span>パネマジ20点</span></strong></p>
<p><strong>◆プレイ内容（点数）： <span>プレイ90点</span></strong></p>
<p><strong>◆総合満足度（点数）： <span>満足度85点</span></strong></p>
<p><strong>◆総合満足度（コメント）：</strong></p><p>架空の総評</p>
<p><strong>◆プレイ時間：</strong> 90分</p><p><strong>◆総額：</strong> 30000円</p>
<p><strong>◆裏を返しますか（この姫をリピートしますか）：</strong></p><p>{repeat}</p>
<p><strong>◆裏を返す返さないの具体的な理由：</strong></p><p>架空の理由</p>
</div><aside><p>◆総合満足度（点数）：<font>99点</font></p></aside></html>"""


def listing(urls, last_page=3):
    cards = "".join(
        f'<article class="entry-card"><h2><a href="{value}">架空記事</a></h2>'
        f'<a href="{value}#comment">コメント</a></article>' for value in urls
    )
    return (
        cards + f'<ul class="pagination"><li class="last"><a href="{BASE}page/{last_page}/">Last</a></li></ul>'
        + f'<aside><a href="{url(999)}">サイドバー</a></aside>'
    )


class Response:
    def __init__(self, url, text, status=200):
        self.url, self.text, self.status_code = url, text, status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
        self.posts = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, Response) else Response(url, value)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.pages["post"]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ParserTests(unittest.TestCase):
    def test_actual_tag_variants_and_details(self):
        for tag in ("span", "font"):
            soup = BeautifulSoup(article(score_tag=tag), "lxml")
            record = store.parse_report_page(soup, url(1))
            details = store.parse_report_details(soup)
            self.assertEqual(record["name"], "テスト")
            self.assertEqual(record["face_score"], 80)
            self.assertEqual(record["predict_age"], "35才")
            self.assertEqual(record["girl_url"], PROFILE)
            self.assertEqual(details["total_point"], 85)
            self.assertEqual(details["style_comment"], "架空の段落1\n架空の段落2")
            self.assertEqual(details["play_time"], "◆プレイ時間：90分")
            self.assertIsNone(details["pie_comment"])

    def test_missing_url_and_age_and_negative_repeat_are_kept(self):
        soup = BeautifulSoup(article(age="", link=False, repeat="返さない（リピートしない）"), "lxml")
        record = store.parse_report_page(soup, url(1))
        self.assertIsNone(record["girl_url"])
        self.assertIsNone(record["predict_age"])
        self.assertEqual(record["repeat"], "返さない（リピートしない）")

    def test_body_shop_name_takes_precedence_over_retirement_category(self):
        source = article().replace(
            'rel="category tag">架空店舗</a>', 'rel="category tag">退店・移籍嬢</a>',
        )
        record = store.parse_report_page(BeautifulSoup(source, "lxml"), url(1))
        self.assertEqual(record["shop_name"], "架空店舗")

    def test_verified_historical_score_punctuation_variants(self):
        for source in (
            article().replace("スタイル70点", "スタイル70.点"),
            article().replace("姫のスタイル・肌", "姫のスタイル､肌"),
        ):
            self.assertEqual(store.parse_report_details(BeautifulSoup(source, "lxml"))["style_score"], 70)

    def test_notice_and_malformed_report_are_distinguished(self):
        self.assertIsNone(store.parse_report_page(BeautifulSoup("<p>お知らせ</p>", "lxml"), url(1)))
        with self.assertRaises(ValueError):
            store.parse_report_page(BeautifulSoup(article(repeat=""), "lxml"), url(1))
        with self.assertRaises(ValueError):
            store.parse_report_page(BeautifulSoup(article().replace("顔80点", "顔180点"), "lxml"), url(1))
        with self.assertRaises(ValueError):
            store.parse_report_page(
                BeautifulSoup('<p><strong>◆総合満足度（点数）：</strong><span>80点</span></p>', "lxml"),
                url(1),
            )

    def test_url_normalization_and_host_isolation(self):
        self.assertEqual(store.canonical_article_url(
            "http://WWW.fuzoku-kanto.net/2026/01/02/post-1?utm=x#comment",
        ), url(1))
        self.assertIsNone(store.canonical_article_url("https://m.fuzoku-tokai.com/2026/01/02/post-1/"))
        self.assertIsNone(tokai_store.canonical_article_url(url(1)))
        self.assertIsNone(store.canonical_article_url(BASE + "2026/02/30/post-1/"))
        hello = BASE + "2016/08/15/hello-world/"
        self.assertEqual(store.canonical_article_url(hello), hello)
        self.assertEqual(collect.extract_article_urls(BeautifulSoup(listing([hello]), "lxml")), {hello})
        self.assertIsNone(tokai_store.canonical_article_url(hello))
        self.assertNotEqual(tokai_store.person_key({"girl_url": PROFILE}),
                            tokai_store.person_key({"girl_url": PROFILE.replace("person1", "person2")}))

    def test_listing_excludes_sidebar_and_detects_bad_markup(self):
        self.assertEqual(collect.extract_article_urls(BeautifulSoup(listing([url(1), url(2)]), "lxml")),
                         {url(1), url(2)})
        with self.assertRaises(ValueError):
            collect.extract_article_urls(BeautifulSoup("<p>empty</p>", "lxml"))


class CollectorTests(unittest.TestCase):
    def make_collector(self, directory, pages):
        session = Session(pages)
        reports = store.ReportStore(Path(directory) / "reports.jsonl")
        state = shared.CollectState(Path(directory) / "state.json")
        checker = shared.ProfileChecker(session, driver_factory=lambda: self.fail("No browser needed"))
        listings = collect.CollectionListings()
        return shared.Collector(
            session, reports, state, checker, sleep=lambda _: None,
            parse_article=store.parse_report_page, parse_details=store.parse_report_details,
            listing_count=collect.get_listing_page_count, listing_urls=listings.fetch_urls,
            listing_finished=lambda: listings.finished,
        )

    def test_backfill_resume_deduplication_and_missing_profile(self):
        pages = {
            BASE: listing([url(1)], 2), BASE + "page/2/": listing([url(2)], 2),
            url(1): article(), url(2): article(link=False, repeat="返さない（リピートしない）"),
            PROFILE: "<html>テスト</html>",
        }
        with tempfile.TemporaryDirectory() as directory:
            collector = self.make_collector(directory, pages)
            shared.run_backfill(collector, 75, limit=1)
            self.assertFalse(collector.state.data["backfill"]["completed"])
            collector = self.make_collector(directory, pages)
            shared.run_backfill(collector, 75, resume=True)
            self.assertEqual(len(collector.store), 2)
            self.assertTrue(collector.state.data["backfill"]["completed"])
            self.assertEqual(collector.store.get(url(2))["profile_status"], "unknown")
            shared.run_backfill(collector, 75)
            self.assertEqual(len(collector.store), 2)

    def test_store_preserves_manual_fields_and_rechecks_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = self.make_collector(directory, {PROFILE: "<html>テスト</html>"})
            collector.store.upsert({
                **store.parse_report_page(BeautifulSoup(article(), "lxml"), url(1)),
                "delete?": 1, "face_score": 60, "profile_status": "unavailable",
            })
            collector.store.upsert({"url": url(1), "delete?": 0, "face_score": 80, "total_point": 85})
            shared.run_recheck_profiles(collector)
            record = collector.store.get(url(1))
            self.assertEqual(record["delete?"], 1)
            self.assertEqual(record["face_score"], 60)
            self.assertEqual(record["total_point"], 85)
            self.assertEqual(record["profile_status"], "published")

    def test_incremental_and_fill_use_kanto_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = self.make_collector(directory, {
                BASE: listing([url(1)], 1), url(1): article(), PROFILE: "<html>テスト</html>",
            })
            shared.run_incremental(collector, 75, 3)
            self.assertEqual(collector.store.get(url(1))["total_point"], 85)
            collector.store.get(url(1))["fetched_at"] = None
            shared.run_fill_details(collector)
            self.assertIsNotNone(collector.store.get(url(1))["fetched_at"])

    def test_login_article_does_not_become_non_report(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = self.make_collector(directory, {url(1): '<input name="password_protected_pwd">'})
            with self.assertRaises(shared.LoginRequiredError):
                collector.process_article(url(1))
            self.assertFalse(collector.state.is_non_report(url(1)))

    def test_backfill_stops_at_user_selected_boundary(self):
        pages = {
            BASE: listing([url(1)], 3),
            BASE + "page/2/": listing([url(2), collect.COLLECTION_BOUNDARY_URL, url(3)], 3),
            url(1): article(), url(2): article(), PROFILE: "<html>テスト</html>",
        }
        with tempfile.TemporaryDirectory() as directory:
            collector = self.make_collector(directory, pages)
            shared.run_backfill(collector, 75)
            self.assertTrue(collector.state.data["backfill"]["completed"])
            self.assertEqual(collector.state.data["backfill"]["last_processed_page"], 2)
            self.assertEqual(len(collector.store), 2)
            self.assertNotIn(BASE + "page/3/", collector.session.calls)
            self.assertNotIn(collect.COLLECTION_BOUNDARY_URL, collector.session.calls)
            self.assertNotIn(url(3), collector.session.calls)


class PreflightTests(unittest.TestCase):
    def pages(self):
        return {
            BASE: listing([url(1), url(2)], 2),
            BASE + "page/2/": listing([url(2), url(3)], 2),
            url(2): article(), url(3): article(link=False),
        }

    def test_all_pages_and_last_articles_are_checked_without_writing(self):
        result = collect.preflight(Session(self.pages()), 75, sleep=lambda _: None)
        self.assertEqual(result["pages_checked"], 2)
        self.assertEqual(result["unique_article_urls"], 3)
        self.assertTrue(result["covers_all_pages"])
        self.assertEqual(len(result["last_page_reports"]), 2)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(collect, "OUTPUT_JSONL", Path(directory) / "data.jsonl"),
            patch.object(collect, "STATE_JSON", Path(directory) / "state.json"),
            patch.object(collect, "login"),
            patch.object(collect, "preflight", return_value=result),
            patch.object(shared, "configure_retries"),
            patch.object(collect.requests, "Session", return_value=Session(self.pages())),
            patch.dict(os.environ, {"FUZOKU_TOKAI_PASSWORD": "test-only"}),
        ):
            collect.main(["--preflight"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_limit_does_not_silently_claim_full_coverage(self):
        result = collect.preflight(Session(self.pages()), 1, sleep=lambda _: None)
        self.assertEqual(result["pages_checked"], 2)
        self.assertFalse(result["covers_all_pages"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            collect.parse_args(["--preflight", "--limit", "1"])

    def test_repeated_empty_and_redirected_pages_fail(self):
        pages = self.pages()
        pages[BASE + "page/2/"] = pages[BASE]
        with self.assertRaises(ValueError):
            collect.preflight(Session(pages), sleep=lambda _: None)
        pages[BASE + "page/2/"] = "<html>empty</html>"
        with self.assertRaises(ValueError):
            collect.preflight(Session(pages), sleep=lambda _: None)
        pages[BASE + "page/2/"] = Response(BASE, listing([url(3)], 2))
        with self.assertRaises(ValueError):
            collect.preflight(Session(pages), sleep=lambda _: None)

    def test_last_page_failure_prevents_success(self):
        pages = self.pages()
        pages[url(2)] = Response(url(2), "error", 503)
        with self.assertRaises(requests.HTTPError):
            collect.preflight(Session(pages), sleep=lambda _: None)

    def test_glossary_final_page_and_historical_review_are_both_verified(self):
        pages = {
            BASE: listing([url(1)], 2).replace("架空記事", "架空人物姫"),
            BASE + "page/2/": listing([url(2)], 2),
            url(1): article(),
            url(2): '<time class="entry-date" datetime="2026-01-02T10:00:00+09:00"></time>'
                    '<div id="the-content" class="entry-content"><p>架空用語の説明</p></div>',
        }
        result = collect.preflight(Session(pages), sleep=lambda _: None)
        self.assertEqual(result["last_page_reports"], [])
        self.assertEqual(result["last_page_non_reports"], [url(2)])
        self.assertEqual(result["historical_report"]["url"], url(1))
        self.assertEqual(result["report_title_candidates"], 1)

    def test_boundary_excludes_itself_and_all_later_articles_from_collection_count(self):
        pages = {
            BASE: listing([url(1), collect.COLLECTION_BOUNDARY_URL], 2).replace("架空記事", "架空人物姫"),
            BASE + "page/2/": listing([url(2)], 2),
            url(1): article(),
            url(2): '<time class="entry-date" datetime="2026-01-02T10:00:00+09:00"></time><p>用語説明</p>',
        }
        result = collect.preflight(Session(pages), sleep=lambda _: None)
        self.assertEqual(result["unique_article_urls"], 3)
        self.assertEqual(result["collection_article_urls"], 1)
        self.assertEqual(result["excluded_article_urls"], 2)
        self.assertEqual(result["collection_boundary_page"], 1)


class LoginTests(unittest.TestCase):
    def test_form_action_is_validated_before_password_post(self):
        session = Session({BASE: '<form id="loginform" action="https://untrusted.invalid/">'
                          '<input name="password_protected_pwd"></form>'})
        with self.assertRaises(shared.LoginRequiredError):
            collect.login(session, "test-only")
        self.assertEqual(session.posts, [])

    def test_http_200_login_form_is_not_success(self):
        form = f'<form id="loginform" action="{BASE}?password-protected=login">'
        form += '<input type="hidden" name="password_protected_cookie_test" value="1">'
        form += '<input name="password_protected_pwd"></form>'
        session = Session({BASE: form, "post": Response(BASE, form)})
        with self.assertRaises(shared.LoginRequiredError):
            collect.login(session, "test-only")
        self.assertEqual(session.posts[0][1]["data"]["password_protected_cookie_test"], "1")

    def test_password_post_does_not_follow_external_redirect(self):
        form = f'<form id="loginform" action="{BASE}"><input name="password_protected_pwd"></form>'
        redirect = Response(BASE, "", 307)
        redirect.headers["Location"] = "https://untrusted.invalid/"
        session = Session({BASE: form, "post": redirect})
        with self.assertRaises(shared.LoginRequiredError):
            collect.login(session, "test-only")
        self.assertFalse(session.posts[0][1]["allow_redirects"])
        self.assertEqual(session.calls, [BASE])


class GeneratorTests(unittest.TestCase):
    def test_offline_age_limit_matches_tokai_and_ended_profiles_excluded(self):
        records = []
        for number, age, status in (
            (1, "29才", "published"), (2, "30才", "unknown"),
            (3, "31才", "published"), (4, "", "published"),
            (5, "25才", "unavailable"), (6, "35才", "published"),
            (7, "不明", "unknown"),
        ):
            record = store.parse_report_page(BeautifulSoup(article(age=age), "lxml"), url(number))
            record.update(store.parse_report_details(BeautifulSoup(article(), "lxml")))
            record.update(profile_status=status, fetched_at="2026-01-03T00:00:00+00:00",
                          girl_url=PROFILE + str(number))
            records.append(record)
        records.append({**records[0], "url": url(8)})
        records.append({**records[0], "url": url(9), "predict_age": "31才"})
        with tempfile.TemporaryDirectory() as directory:
            input_path, output_path = Path(directory) / "data.jsonl", Path(directory) / "report.html"
            input_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
            with (
                patch.object(generate, "INPUT_JSONL", input_path),
                patch.object(generate, "OUTPUT_HTML", output_path),
                patch.object(generate.requests, "Session", side_effect=AssertionError("network")),
                patch.dict(os.environ, {"FUZOKU_TOKAI_PASSWORD": ""}),
            ):
                generate.main(["--offline"])
            soup = BeautifulSoup(output_path.read_text(encoding="utf-8"), "lxml")
            self.assertEqual(soup.title.get_text(), "関東風俗レポート")
            self.assertEqual(len(soup.select(".report-entry")), 2)
            self.assertEqual(len(soup.select(".article-entry")), 3)
            expected_urls = set(tokai_generate.load_input_data(input_path)["url"])
            actual_urls = {
                link["href"] for link in soup.select(".article-entry .report-heading a")
            }
            self.assertEqual(expected_urls, {url(1), url(2), url(8)})
            self.assertEqual(actual_urls, expected_urls)

    def test_title_is_escaped_without_altering_review_text(self):
        source = generate_html(pd.DataFrame(), title="<関東>")
        self.assertIn("<title>&lt;関東&gt;</title>", source)

    def test_online_detail_enrichment_rejects_login_page(self):
        record = store.parse_report_page(BeautifulSoup(article(), "lxml"), url(1))
        with self.assertRaises(shared.LoginRequiredError):
            tokai_generate.enrich_report_data(
                pd.DataFrame([record]), Session({url(1): '<input name="password_protected_pwd">'}),
                parse_details=store.parse_report_details, validate_article=collect.validate_article,
            )


if __name__ == "__main__":
    unittest.main()
