"""東海風俗レポートを 1記事URL = 1行で `data/tokai_fuzoku_reports.jsonl` に収集する。

実行モード（--recheck-profiles 以外は環境変数 FUZOKU_TOKAI_PASSWORD が必要）:
  引数なし                   通常の差分収集（最新日から --overlap-days 日さかのぼって未取得記事を取り込む）
  --backfill [--resume]      過去分補完（一覧を max_pages までページ送りし、未取得記事だけを取り込む）
  --fill-details             既存記事のうち詳細（点数・コメント・時間・費用）未保存の行を補完する
  --recheck-profiles [--status S ...]
                             保存済み profile_status を人物単位で再判定する（既定は unavailable の人物）
"""
import argparse
import json
import os
import re
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import chromedriver_autoinstaller
import requests
import urllib3
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from wait_for_name_parts import name_in_text, normalize as normalize_name, wait_for_name_parts

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from report_store import (
    REPORTS_JSONL,
    ReportStore,
    article_date_from_url,
    canonical_article_url,
    is_missing,
    latest_profile_status_by_person,
    normalize_profile_url,
    parse_report_details,
    person_key,
)
from profile_signals import classify_profile_page, decode_html

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OUTPUT_JSONL = REPORTS_JSONL
STATE_JSON = ROOT_DIR / "data" / "tokai_fuzoku_collect_state.json"
BASE_URL = "https://m.fuzoku-tokai.com/"
MAX_LISTING_PAGES = 75
DEFAULT_OVERLAP_DAYS = 3
SAVE_EVERY = 10
REQUEST_INTERVAL_SECONDS = 1
PAGE_LOAD_TIMEOUT_SECONDS = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}
REPEAT_RE = re.compile(r"返す（リピートする）|返さない（リピートしない）")


class LoginRequiredError(RuntimeError):
    """ログインページが返された（パスワード誤り・セッション切れ）。"""


def is_login_page(html):
    return "password_protected_pwd" in (html or "")


def listing_page_url(page_number):
    return BASE_URL if page_number == 1 else f"{BASE_URL}page/{page_number}/"


def fetch_listing_html(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    if is_login_page(response.text):
        raise LoginRequiredError(f"ログインページが返されました: {url}")
    return response.text


def extract_article_urls(soup, page_url=BASE_URL):
    """記事一覧から本文記事URLだけを正規化して抽出する。"""
    urls = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "#comment" in href:
            continue
        canonical = canonical_article_url(urljoin(page_url, href))
        if canonical is None:
            continue

        sidebar_heading = anchor.find_parent("ul")
        if sidebar_heading:
            sidebar_heading = sidebar_heading.find_previous_sibling(
                "h3", class_="widget_title sidebar_widget_title"
            )
        if sidebar_heading:
            continue
        urls.add(canonical)
    return urls


def get_listing_page_count(session, max_pages):
    soup = BeautifulSoup(fetch_listing_html(session, BASE_URL), "lxml")

    last_page = 1
    pagination = soup.find("ul", class_="pagination")
    if pagination:
        last_link = pagination.find("li", class_="last")
        anchor = last_link.find("a", href=True) if last_link else None
        match = re.search(r"/page/(\d+)/", anchor["href"]) if anchor else None
        if match:
            last_page = int(match.group(1))
    return min(last_page, max_pages)


def fetch_listing_urls(session, page_number):
    url = listing_page_url(page_number)
    return extract_article_urls(BeautifulSoup(fetch_listing_html(session, url), "lxml"), url)


def text_after_strong(soup, label):
    tag = soup.find("strong", string=lambda value: value and label in value)
    if not tag or tag.next_sibling is None:
        return None
    sibling = tag.next_sibling
    return sibling.strip() if isinstance(sibling, str) else sibling.get_text(strip=True)


def is_article_page(soup):
    date_tag = soup.find("time", class_="entry-date")
    return bool(date_tag and date_tag.get("datetime"))


def parse_report_page(soup, report_url):
    """レポート記事の基本項目。返す/返さないの両方を返す。レポート形式でなければ None。"""
    repeat_tag = next(
        (tag for tag in soup.find_all("p") if REPEAT_RE.search(tag.get_text(" ", strip=True))),
        None,
    )
    if repeat_tag is None:
        return None
    repeat = repeat_tag.get_text(strip=True)

    shop_tag = soup.find("a", rel=lambda value: value and "category tag" in value)
    shop_name = shop_tag.get_text(strip=True) if shop_tag else None
    type_tag = soup.find("strong", string=lambda value: value and "店舗ジャンル" in value)
    shop_type = text_after_strong(soup, "店舗ジャンル") if type_tag else None

    name_tag = soup.find("strong", string=lambda value: value and "姫の名前" in value)
    name_link = name_tag.find_parent("p").find("a", href=True) if name_tag and name_tag.find_parent("p") else None
    if not name_link:
        return None
    name = re.sub(r"姫$", "", name_link.get_text(strip=True))
    girl_url = name_link["href"]

    date_tag = soup.find("time", class_="entry-date")
    if not date_tag or not date_tag.get("datetime"):
        return None
    report_date = datetime.fromisoformat(date_tag["datetime"]).date()

    face_score = None
    for tag in soup.find_all(["p", "strong"]):
        if "◆姫の顔（点数）：" in tag.get_text():
            font_tag = tag.find("font")
            match = re.search(r"\d+", font_tag.get_text(strip=True)) if font_tag else None
            face_score = int(match.group()) if match else None
            break

    face_comment_tag = next(
        (
            tag
            for tag in soup.find_all("p")
            if tag.find("strong") and "姫の顔（コメント）" in tag.get_text()
        ),
        None,
    )
    face_comment = face_comment_tag.find_next_sibling("p") if face_comment_tag else None

    return {
        "delete?": 0,
        "date": report_date.isoformat(),
        "type": shop_type,
        "shop_name": shop_name,
        "name": name,
        "predict_age": text_after_strong(soup, "姫の推定年齢"),
        "face_score": face_score,
        "face_feature": face_comment.get_text() if face_comment else None,
        "cup": text_after_strong(soup, "姫の胸（推定カップ）"),
        "repeat": repeat,
        "girl_url": girl_url,
        "url": canonical_article_url(report_url) or report_url,
    }


def build_driver():
    driver_path = chromedriver_autoinstaller.install()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.page_load_strategy = "eager"
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(driver_path), options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
    return driver


def safe_quit(driver):
    try:
        driver.quit()
    except (WebDriverException, urllib3.exceptions.HTTPError):
        pass


class ProfileChecker:
    """プロフィール掲載確認を人物（正規化プロフィールURL）単位でキャッシュする。

    結果は published / unavailable / unknown。unavailable は profile_signals の明確な不在シグナル
    （404・410、別ページへの転送、サイト別の空テンプレート）だけ。CityHeaven は同一URLで200なら published。
    それ以外は名前照合（タグ・装飾・読みを緩和）し、一致しなければブラウザ描画後に再照合して、
    それでも名前が無い・通信失敗・403/429/5xx・ブラウザ失敗は unknown（掲載終了と断定しない）。
    """

    def __init__(self, session, driver_factory=build_driver):
        self.session = session
        self.driver_factory = driver_factory
        self.driver = None
        self.driver_failed = False
        self.pages = {}
        self.results = {}
        self.page_requests = 0

    def _page(self, url, key):
        """(status, html)。status が None なら名前照合が必要。"""
        if key in self.pages:
            return self.pages[key]
        self.page_requests += 1
        try:
            response = self.session.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException as exc:
            print(f"プロフィール取得失敗のため掲載状況不明: {url} ({exc})")
            page = ("unknown", None)
        else:
            content = getattr(response, "content", None)
            html = (
                decode_html(content, getattr(response, "encoding", None))
                if isinstance(content, bytes)
                else response.text
            )
            status, reason = classify_profile_page(url, response.url, response.status_code, html)
            if status == "unavailable":
                print(f"プロフィール掲載終了（{reason}）: {url}")
            elif status == "unknown":
                print(f"プロフィール取得 {reason} のため掲載状況不明: {url}")
            page = (status, html)
        self.pages[key] = page
        return page

    def check(self, url, name):
        key = normalize_profile_url(url)
        if key is None or is_missing(name):
            return "unknown"
        cache_key = (key, normalize_name(str(name)))
        if cache_key not in self.results:
            status, html = self._page(url, key)
            if status is None:
                text = BeautifulSoup(html, "lxml").get_text(" ")
                status = "published" if name_in_text(text, str(name)) else self._check_with_browser(url, str(name))
            self.results[cache_key] = status
        return self.results[cache_key]

    def _check_with_browser(self, url, name):
        if self.driver_failed:
            return "unknown"
        if self.driver is None:
            try:
                self.driver = self.driver_factory()
            except Exception as exc:  # chromedriver の取得・起動失敗
                print(f"ブラウザを起動できないため掲載状況不明: {type(exc).__name__}")
                self.driver_failed = True
                return "unknown"
        try:
            self.driver.get(url)
        except (WebDriverException, urllib3.exceptions.HTTPError) as exc:
            print(f"ブラウザでの読み込み失敗のため掲載状況不明: {url} ({type(exc).__name__})")
            self.close()
            return "unknown"
        try:
            wait_for_name_parts(self.driver, name)
            return "published"
        except TimeoutException:
            print(f"プロフィールに名前を確認できないため掲載状況不明（不在の証拠なし）: {url}")
            return "unknown"
        except (WebDriverException, urllib3.exceptions.HTTPError) as exc:
            print(f"ブラウザでの確認失敗のため掲載状況不明: {url} ({type(exc).__name__})")
            self.close()
            return "unknown"

    def close(self):
        if self.driver is not None:
            safe_quit(self.driver)
            self.driver = None


class CollectState:
    """途中保存・再開用の状態（data/tokai_fuzoku_collect_state.json、Git管理外）。"""

    def __init__(self, path=STATE_JSON):
        self.path = Path(path)
        data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = {
            "non_report_urls": data.get("non_report_urls", {}),
            "failed_urls": data.get("failed_urls", {}),
            "backfill": data.get("backfill", {}),
        }

    def is_non_report(self, url):
        return url in self.data["non_report_urls"]

    def mark_non_report(self, url, checked_at):
        self.data["non_report_urls"][url] = checked_at
        self.data["failed_urls"].pop(url, None)

    def mark_failed(self, url, reason, failed_at):
        self.data["failed_urls"][url] = {"reason": str(reason)[:300], "failed_at": failed_at}

    def clear_failure(self, url):
        self.data["failed_urls"].pop(url, None)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.tmp")
        temporary_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )
        temporary_path.replace(self.path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Collector:
    def __init__(self, session, store, state, checker, *, clock=now_iso, sleep=time.sleep):
        self.session = session
        self.store = store
        self.state = state
        self.checker = checker
        self.clock = clock
        self.sleep = sleep
        self.counts = Counter()
        self.fetches = 0

    def should_fetch(self, url):
        return url not in self.store and not self.state.is_non_report(url)

    def _fail(self, url, reason):
        print(f"取得失敗: {url} ({reason})")
        self.state.mark_failed(url, reason, self.clock())
        return "failed"

    def _fetch_article(self, url):
        """(soup, None) か (None, 'failed') を返す。ログインページなら例外。"""
        try:
            response = self.session.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            return None, self._fail(url, exc)
        if response.status_code != 200:
            return None, self._fail(url, f"HTTP {response.status_code}")
        if is_login_page(response.text):
            raise LoginRequiredError(f"ログインページが返されました: {url}")
        if canonical_article_url(response.url) != url:
            return None, self._fail(url, f"別URLへ転送: {response.url}")
        return BeautifulSoup(response.text, "lxml"), None

    def process_article(self, url):
        """新規記事を取り込む。'added'/'updated'/'unchanged'/'non_report'/'failed'。"""
        soup, failure = self._fetch_article(url)
        if failure:
            return failure
        try:
            record = parse_report_page(soup, url)
        except ValueError as exc:
            return self._fail(url, exc)
        if record is None:
            if is_article_page(soup):
                self.state.mark_non_report(url, self.clock())
                return "non_report"
            return self._fail(url, "記事として解析できません")

        checked_at = self.clock()
        record.update(parse_report_details(soup))
        record["fetched_at"] = checked_at
        record["profile_status"] = self.checker.check(record["girl_url"], record["name"])
        record["profile_checked_at"] = checked_at
        self.counts[f"profile_{record['profile_status']}"] += 1
        outcome = self.store.upsert(record)
        self.state.clear_failure(url)
        return outcome

    def fill_article_details(self, url):
        """既存記事の欠損列だけを補完する（既存値・delete? は変更しない）。"""
        soup, failure = self._fetch_article(url)
        if failure:
            return failure
        if not is_article_page(soup):
            return self._fail(url, "記事として解析できません")
        try:
            fresh = parse_report_page(soup, url) or {}
        except ValueError:
            fresh = {}
        fresh.pop("delete?", None)
        fresh.update(parse_report_details(soup))
        fresh["url"] = url
        fresh["fetched_at"] = self.clock()
        outcome = self.store.upsert(fresh)
        self.state.clear_failure(url)
        return outcome

    def process_many(self, urls, limit=None, handler=None):
        """記事を順に処理する。--limit に達したら False。"""
        handler = handler or self.process_article
        for index, url in enumerate(urls, start=1):
            if limit is not None and self.fetches >= limit:
                return False
            print(f"記事 {index}/{len(urls)}: {url}")
            self.counts[handler(url)] += 1
            self.fetches += 1
            if self.fetches % SAVE_EVERY == 0:
                self.checkpoint()
            self.sleep(REQUEST_INTERVAL_SECONDS)
        return True

    def checkpoint(self):
        if self.store.save():
            print(f"JSONLを保存しました: {self.store.path}")
        self.state.save()

    def report(self):
        counts = self.counts
        print(
            "結果: "
            f"追加 {counts['added']}件 / 更新 {counts['updated']}件 / 変更なし {counts['unchanged']}件 / "
            f"レポート以外 {counts['non_report']}件 / 失敗 {counts['failed']}件"
        )
        if counts["profile_published"] or counts["profile_unavailable"] or counts["profile_unknown"]:
            print(
                "プロフィール確認: "
                f"掲載中 {counts['profile_published']}件 / 掲載終了 {counts['profile_unavailable']}件 / "
                f"不明 {counts['profile_unknown']}件（プロフィール取得 {self.checker.page_requests}回、人物単位で再利用）"
            )


def run_incremental(collector, max_pages, overlap_days, limit=None):
    """通常の差分収集。最新日の overlap_days 日前までの一覧を見て、未取得URLだけを取り込む。

    掲載日がこの範囲より古い遅延追加記事は対象外。過去分は --backfill で補完する。
    """
    latest_date = collector.store.latest_date()
    cutoff = latest_date - timedelta(days=overlap_days) if latest_date else None
    if cutoff:
        print(f"既存データの最新日付: {latest_date}（{cutoff} 以降の一覧を再確認）")

    page_count = get_listing_page_count(collector.session, max_pages)
    pending = []
    seen = set()
    for page_number in range(1, page_count + 1):
        page_urls = fetch_listing_urls(collector.session, page_number)
        in_window = {
            url for url in page_urls if cutoff is None or article_date_from_url(url) >= cutoff
        }
        pending.extend(sorted(url for url in in_window - seen if collector.should_fetch(url)))
        seen |= in_window
        print(f"一覧 {page_number}/{page_count}: {len(page_urls)}件、未取得累計{len(pending)}件")
        if cutoff is not None and page_urls and not in_window:
            print(f"再確認範囲の下限 {cutoff} より前の一覧に到達しました")
            break
        collector.sleep(REQUEST_INTERVAL_SECONDS)

    pending.sort(key=lambda url: (article_date_from_url(url), url))
    print(f"新規確認対象: {len(pending)}件")
    if not collector.process_many(pending, limit):
        print("--limit に達したため中断しました。残りは次回の実行で取り込みます")


def run_backfill(collector, max_pages, resume=False, limit=None):
    """過去分補完。最新日で打ち切らず一覧をページ送りし、未取得記事だけを取り込む。"""
    backfill = collector.state.data["backfill"]
    start_page = 1
    if resume:
        if backfill.get("completed"):
            print("前回のバックフィルは完了済みです。最初から再走査する場合は --resume なしで実行してください")
            return
        start_page = int(backfill.get("resume_page") or 1)

    page_count = get_listing_page_count(collector.session, max_pages)
    if start_page > page_count:
        print(f"再開ページ {start_page} が対象ページ数 {page_count} を超えています（--max-pages を確認）")
        return
    backfill.update({"completed": False, "page_count": page_count, "updated_at": collector.clock()})
    print(f"バックフィル: 一覧 {start_page}〜{page_count} ページ（max_pages={max_pages}）")

    for page_number in range(start_page, page_count + 1):
        # 中断時・完了後ともにこのページから再開する（一覧のずれによる取りこぼし防止）
        backfill["resume_page"] = page_number
        page_urls = fetch_listing_urls(collector.session, page_number)
        pending = sorted(url for url in page_urls if collector.should_fetch(url))
        print(f"一覧 {page_number}/{page_count}: {len(page_urls)}件中 未取得 {len(pending)}件")
        finished = collector.process_many(pending, limit)
        backfill["updated_at"] = collector.clock()
        collector.checkpoint()
        if not finished:
            print(f"--limit に達したため中断しました。--backfill --resume でページ {page_number} から再開できます")
            return
        collector.sleep(REQUEST_INTERVAL_SECONDS)

    backfill.update({"completed": True, "resume_page": None, "completed_at": collector.clock()})
    print(f"バックフィル完了（{page_count}ページ）。max_pages より後のページは走査していません")
    if collector.state.data["failed_urls"]:
        print(f"失敗記事 {len(collector.state.data['failed_urls'])}件は次回の --backfill で再試行されます")


def run_fill_details(collector, limit=None):
    targets = [
        canonical_article_url(record.get("url"))
        for record in collector.store.records
        if canonical_article_url(record.get("url")) and is_missing(record.get("fetched_at"))
    ]
    print(f"詳細未保存の既存記事: {len(targets)}件")
    if not collector.process_many(targets, limit, handler=collector.fill_article_details):
        print("--limit に達したため中断しました。再実行すると残りを補完します")


RECHECK_STATUSES = ("unavailable", "unknown", "published")


def recheck_targets(records, statuses):
    """再確認する人物を {人物キー: [記事行...]} で返す。対象は profile_status 保存済みの行のみ。

    人物の現在の状態は最新の確定結果（published/unavailable）、確定結果が無ければ unknown。
    """
    latest = latest_profile_status_by_person(records)
    groups = {}
    for record in records:
        key = person_key(record)
        if key is None or is_missing(record.get("profile_status")) or not canonical_article_url(record.get("url")):
            continue
        groups.setdefault(key, []).append(record)
    return {key: rows for key, rows in groups.items() if latest.get(key, "unknown") in statuses}


def run_recheck_profiles(collector, statuses=("unavailable",), limit=None):
    """保存済みの profile_status を人物単位で再判定する（記事本文は取得しない）。

    確定結果（published/unavailable）が出た人物だけ、その人物の状態保存済み行を更新する。
    unknown（通信失敗・制限など）のときは既存の状態を変更しない。
    """
    targets = recheck_targets(collector.store.records, set(statuses))
    print(f"プロフィール再判定の対象: {len(targets)}人（状態: {', '.join(statuses)}）")
    latest = latest_profile_status_by_person(collector.store.records)
    for index, (key, rows) in enumerate(targets.items(), start=1):
        if limit is not None and index > limit:
            print("--limit に達したため中断しました。再実行すると残りを再判定します")
            break
        rows = sorted(rows, key=lambda row: str(row.get("date") or ""), reverse=True)
        names = list(dict.fromkeys(str(row.get("name")) for row in rows if not is_missing(row.get("name"))))
        results = []
        for name in names:
            results.append(collector.checker.check(rows[0]["girl_url"], name))
            if results[-1] == "published":
                break
        if "published" in results:
            status = "published"
        elif "unavailable" in results:
            status = "unavailable"
        else:
            status = "unknown"
        previous = latest.get(key, "unknown")
        print(f"再判定 {index}/{len(targets)}: {key} {previous} -> {status}")
        collector.counts[f"profile_{status}"] += 1
        if status != "unknown":
            checked_at = collector.clock()
            for row in rows:
                collector.store.upsert(
                    {"url": row["url"], "profile_status": status, "profile_checked_at": checked_at}
                )
            if status != previous:
                collector.counts["status_changed"] += 1
        if index % SAVE_EVERY == 0:
            collector.checkpoint()
        collector.sleep(REQUEST_INTERVAL_SECONDS)
    print(f"状態が変わった人物: {collector.counts['status_changed']}人（unknown の人物は変更していません）")


def configure_retries(session):
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)


def login(session, password):
    payload = {
        "password_protected_pwd": password,
        "wp-submit": "ログイン",
        "password_protected_cookie_test": "1",
        "password-protected": "login",
        "redirect_to": "",
    }
    response = session.post(BASE_URL, data=payload, headers=HEADERS, timeout=30)
    response.raise_for_status()
    print(f"ログイン応答: {response.status_code} {response.url}")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="東海風俗レポートを記事単位で収集する")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true", help="過去分を一覧の max_pages まで走査して未取得記事を取り込む")
    mode.add_argument("--fill-details", action="store_true", help="詳細未保存の既存記事を補完する（欠損列のみ）")
    mode.add_argument(
        "--recheck-profiles",
        action="store_true",
        help="保存済みの profile_status を人物単位で再判定する（ログイン不要。--limit は人物数）",
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=RECHECK_STATUSES,
        help="--recheck-profiles の対象となる現在の状態（複数指定可。既定: unavailable）",
    )
    parser.add_argument("--resume", action="store_true", help="--backfill の中断ページから再開する")
    parser.add_argument("--max-pages", type=int, help="一覧の最大ページ数（既定: FUZOKU_TOKAI_MAX_PAGES または 75）")
    parser.add_argument("--overlap-days", type=int, default=DEFAULT_OVERLAP_DAYS, help="通常収集で最新日からさかのぼる日数")
    parser.add_argument("--limit", type=int, help="この実行で取得する記事数の上限")
    args = parser.parse_args(argv)
    if args.resume and not args.backfill:
        parser.error("--resume は --backfill と併用してください")
    if args.status and not args.recheck_profiles:
        parser.error("--status は --recheck-profiles と併用してください")
    args.status = list(dict.fromkeys(args.status or ["unavailable"]))
    if args.overlap_days < 0:
        parser.error("--overlap-days は0以上を指定してください")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit は1以上を指定してください")
    return args


def resolve_max_pages(value):
    max_pages = value if value is not None else int(os.environ.get("FUZOKU_TOKAI_MAX_PAGES", MAX_LISTING_PAGES))
    if max_pages < 1:
        raise ValueError("FUZOKU_TOKAI_MAX_PAGES / --max-pages は1以上を指定してください")
    return max_pages


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
    if not password and not args.recheck_profiles:
        raise RuntimeError("FUZOKU_TOKAI_PASSWORD 環境変数を設定してください")
    max_pages = resolve_max_pages(args.max_pages)

    store = ReportStore(OUTPUT_JSONL)
    if store.dropped_duplicates:
        print(f"重複した記事URLの行を {store.dropped_duplicates}件 除きます（先頭行を保持）")
    state = CollectState(STATE_JSON)

    with requests.Session() as session:
        configure_retries(session)
        if not args.recheck_profiles:
            login(session, password)
        checker = ProfileChecker(session)
        collector = Collector(session, store, state, checker)
        try:
            if args.recheck_profiles:
                run_recheck_profiles(collector, args.status, args.limit)
            elif args.fill_details:
                run_fill_details(collector, args.limit)
            elif args.backfill:
                run_backfill(collector, max_pages, args.resume, args.limit)
            else:
                run_incremental(collector, max_pages, args.overlap_days, args.limit)
        finally:
            checker.close()
            collector.checkpoint()
            collector.report()


if __name__ == "__main__":
    main()
