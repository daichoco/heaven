"""関東レポート収集。--preflight は本番データを書かず全一覧の網羅性を確認する。"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from tokai_fuzoku import collect_fuzoku_tokai as shared
from kanto_fuzoku.report_store import (
    BASE_URL, REPORTS_JSONL, ReportStore, canonical_article_url,
    parse_report_details, parse_report_page,
)

OUTPUT_JSONL = REPORTS_JSONL
STATE_JSON = ROOT_DIR / "data" / "kanto_fuzoku_collect_state.json"
HEADERS = shared.HEADERS
LoginRequiredError = shared.LoginRequiredError
COLLECTION_BOUNDARY_URL = BASE_URL + "2016/08/15/hello-world/"


def validate_article(response, url):
    if shared.is_login_page(response.text):
        raise LoginRequiredError("関東サイトのログイン画面が返されました")
    if canonical_article_url(response.url) != canonical_article_url(url):
        raise ValueError("関東の記事が別URLへ転送されました")
    soup = BeautifulSoup(response.text, "lxml")
    if not shared.is_article_page(soup):
        raise ValueError("関東の記事本文を確認できません")


def fetch_listing_html(session, page_number):
    url = BASE_URL if page_number == 1 else f"{BASE_URL}page/{page_number}/"
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    if shared.is_login_page(response.text):
        raise LoginRequiredError("関東一覧のログイン画面が返されました")
    expected = urlsplit(url)
    actual = urlsplit(response.url)
    if actual.hostname != expected.hostname or actual.path.rstrip("/") != expected.path.rstrip("/"):
        raise ValueError(f"一覧ページ {page_number} が別ページへ転送されました")
    return response.text


def extract_article_urls(soup):
    cards = (
        [soup] if soup.name == "article" and "entry-card" in soup.get("class", [])
        else soup.select("article.entry-card")
    )
    if not cards:
        raise ValueError("関東一覧の記事カードが見つかりません")
    urls = set()
    for card in cards:
        links = {
            canonical_article_url(urljoin(BASE_URL, a["href"]))
            for a in card.find_all("a", href=True) if "#" not in a["href"]
        } - {None}
        if len(links) != 1:
            raise ValueError("関東一覧の記事カードから記事URLを一意に取得できません")
        urls.update(links)
    return urls


def listing_page_count(soup):
    last = soup.select_one("ul.pagination li.last a[href]")
    if last is None:
        raise ValueError("関東一覧の最終ページリンクが見つかりません")
    match = re.search(r"/page/([1-9]\d*)/?$", last["href"])
    if match is None:
        raise ValueError("関東一覧の最終ページ番号を解析できません")
    return int(match.group(1))


def get_listing_page_count(session, max_pages):
    soup = BeautifulSoup(fetch_listing_html(session, 1), "lxml")
    return min(listing_page_count(soup), max_pages)


def fetch_listing_urls(session, page_number):
    soup = BeautifulSoup(fetch_listing_html(session, page_number), "lxml")
    return collection_urls(soup)[0]


def collection_urls(soup):
    """ユーザー指定により Hello world! 本体と、それより後の一覧記事は収集しない。"""
    urls = set()
    for card in soup.select("article.entry-card"):
        card_urls = extract_article_urls(card)
        if COLLECTION_BOUNDARY_URL in card_urls:
            return urls, True
        urls.update(card_urls)
    if not urls:
        raise ValueError("関東一覧の収集対象記事カードが見つかりません")
    return urls, False


class CollectionListings:
    def __init__(self):
        self.finished = False

    def fetch_urls(self, session, page_number):
        soup = BeautifulSoup(fetch_listing_html(session, page_number), "lxml")
        urls, self.finished = collection_urls(soup)
        return urls


def login(session, password):
    response = session.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    form = BeautifulSoup(response.text, "lxml").select_one("form#loginform")
    if form is None:
        if shared.is_login_page(response.text):
            raise LoginRequiredError("関東サイトの認証フォームを解析できません")
        extract_article_urls(BeautifulSoup(response.text, "lxml"))
        return
    action = urljoin(BASE_URL, form.get("action", ""))
    if urlsplit(action).scheme != "https" or urlsplit(action).netloc != urlsplit(BASE_URL).netloc:
        raise LoginRequiredError("関東サイトの認証フォーム送信先が不正です")
    payload = {
        tag["name"]: tag.get("value", "")
        for tag in form.select("input[type=hidden][name]")
    }
    payload["password_protected_pwd"] = password
    payload["wp-submit"] = "ログイン"
    response = session.post(
        action, data=payload, headers=HEADERS, timeout=30, allow_redirects=False,
    )
    if response.status_code in {301, 302, 303, 307, 308}:
        target = urljoin(action, response.headers.get("Location", ""))
        if urlsplit(target).scheme != "https" or urlsplit(target).netloc != urlsplit(BASE_URL).netloc:
            raise LoginRequiredError("関東サイトの認証後転送先が不正です")
        response = session.get(target, headers=HEADERS, timeout=30)
    response.raise_for_status()
    if shared.is_login_page(response.text):
        raise LoginRequiredError("既存パスワードで関東サイトにログインできません")
    extract_article_urls(BeautifulSoup(fetch_listing_html(session, 1), "lxml"))
    print("関東サイトの認証後一覧を確認しました")


def preflight(session, max_pages=shared.MAX_LISTING_PAGES, *, sleep=time.sleep):
    """全一覧を読み、記事本文は最終ページの到達確認に必要な分だけ取得する。"""
    first = BeautifulSoup(fetch_listing_html(session, 1), "lxml")
    final_page = listing_page_count(first)
    seen_pages = set()
    all_urls = set()
    report_candidates = set()
    collection_articles = set()
    boundary_page = None
    counts = []
    last_urls = set()
    for number in range(1, final_page + 1):
        soup = first if number == 1 else BeautifulSoup(fetch_listing_html(session, number), "lxml")
        if listing_page_count(soup) != final_page:
            raise ValueError("事前確認中に一覧の最終ページ数が変わりました。再確認してください")
        urls = extract_article_urls(soup)
        if boundary_page is None:
            eligible_urls, reached_boundary = collection_urls(soup)
            collection_articles.update(eligible_urls)
            if reached_boundary:
                boundary_page = number
            for card in soup.select("article.entry-card"):
                heading = card.find("h2")
                card_urls = extract_article_urls(card)
                if heading and "姫" in heading.get_text() and card_urls <= eligible_urls:
                    report_candidates.update(card_urls)
        fingerprint = frozenset(urls)
        if fingerprint in seen_pages:
            raise ValueError(f"一覧 {number} が既取得ページと同じ記事群を返しました")
        seen_pages.add(fingerprint)
        counts.append(len(urls))
        all_urls.update(urls)
        last_urls = urls
        print(f"事前確認 一覧 {number}/{final_page}: {len(urls)}記事、累計一意URL {len(all_urls)}件")
        sleep(shared.REQUEST_INTERVAL_SECONDS)

    last_reports = []
    last_non_reports = []
    for url in sorted(last_urls):
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        validate_article(response, url)
        soup = BeautifulSoup(response.text, "lxml")
        report = parse_report_page(soup, url)
        if report is None:
            last_non_reports.append(url)
        else:
            details = parse_report_details(soup)
            last_reports.append({
                "url": url, "has_profile_url": bool(report["girl_url"]),
                "has_face_score": report["face_score"] is not None,
                "stored_detail_fields": sum(value is not None for value in details.values()),
            })
        sleep(shared.REQUEST_INTERVAL_SECONDS)
    historical_report = None
    if not last_reports:
        if not report_candidates:
            raise ValueError("最終ページには実レポートがなく、過去の実レポート候補も確認できません")
        url = min(report_candidates)
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        validate_article(response, url)
        soup = BeautifulSoup(response.text, "lxml")
        report = parse_report_page(soup, url)
        if report is None:
            raise ValueError("過去の実レポート候補をレポートとして解析できません")
        details = parse_report_details(soup)
        historical_report = {
            "url": url, "has_profile_url": bool(report["girl_url"]),
            "has_face_score": report["face_score"] is not None,
            "stored_detail_fields": sum(value is not None for value in details.values()),
        }
    return {
        "checked_at": shared.now_iso(),
        "final_page": final_page,
        "pages_checked": len(counts),
        "max_pages": max_pages,
        "covers_all_pages": final_page <= max_pages,
        "unique_article_urls": len(all_urls),
        "report_title_candidates": len(report_candidates),
        "collection_article_urls": len(collection_articles),
        "collection_boundary_page": boundary_page,
        "collection_boundary_url": COLLECTION_BOUNDARY_URL if boundary_page is not None else None,
        "excluded_article_urls": len(all_urls - collection_articles),
        "per_page_counts": counts,
        "report_count_note": (
            "総記事URL数はお知らせ・用語集等を含みます。タイトルに「姫」を含む候補数は見込みで、"
            "レポート総数は全本文解析後に確定します。"
        ),
        "last_page_reports": last_reports,
        "last_page_non_reports": last_non_reports,
        "historical_report": historical_report,
    }


GROUP_CATEGORIES = [
    ("yoshiwara-soap", "吉原ソープ格安店"),
    ("yoshihara-mediumclass", "吉原ソープ大衆店"),
    ("yw-chukyu", "吉原ソープ中級店"),
    ("yoshihara-highclass", "吉原ソープ高級店"),
    ("shinjuku-soap", "新宿ソープ"),
    ("ikebukuro-soap", "池袋ソープ"),
    ("gotanda-saop", "五反田ソープ"),
    ("horinouti-soap", "川崎ソープ"),
    ("yokohama-soap", "横浜ソープ"),
    ("chiba-soap", "千葉ソープ"),
    ("omiya-soap", "大宮ソープ"),
    ("nishikawagutisaop", "西川口ソープ"),
    ("warabi-soap", "蕨ソープ"),
    ("mito-soap", "水戸ソープ"),
    ("tutiura-soap", "土浦ソープ"),
    ("yamanashi-soap", "山梨ソープ"),
    ("deli-heal", "デリヘル"),
    ("hotel-health", "ホテヘル"),
    ("newhalf-d", "ニューハーフデリヘル"),
    ("health", "ヘルス（箱ヘル）"),
    ("seikan", "性感・風俗エステ"),
    ("mens-esthe", "メンズエステ"),
    ("imecl", "イメクラ"),
    ("onaclu", "オナクラ"),
    ("sm-club", "SMクラブ"),
]
GROUP_NAMES = [name for _, name in GROUP_CATEGORIES]


def fetch_category_urls(session, slug, page_number):
    """カテゴリ一覧ページの記事URL。ページが存在しなければ None。"""
    path = f"category/{slug}/" if page_number == 1 else f"category/{slug}/page/{page_number}/"
    response = session.get(BASE_URL + path, headers=HEADERS, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    if shared.is_login_page(response.text):
        raise LoginRequiredError("関東カテゴリ一覧のログイン画面が返されました")
    soup = BeautifulSoup(response.text, "lxml")
    if not soup.select("article.entry-card"):
        return None
    return extract_article_urls(soup)


def run_fill_groups(session, store, *, full=False, sleep=0.5):
    """カテゴリ一覧を辿り、記事に価格帯・エリア(shop_group)を付与する。

    既存値は上書きしない。通常は、ページ内の保存済み記事がすべて付与済みになった時点で
    そのカテゴリの走査を終える（新着は一覧の先頭に並ぶため）。full=True で全ページ走査。
    """
    assigned = 0
    for slug, group in GROUP_CATEGORIES:
        page = 1
        while True:
            urls = fetch_category_urls(session, slug, page)
            if urls is None:
                break
            known = [store.get(u) for u in urls if u in store]
            pending = 0
            for record in known:
                if not record.get("shop_group"):
                    record["shop_group"] = group
                    store.dirty = True
                    assigned += 1
                    pending += 1
            if not full and known and not pending:
                break
            page += 1
            time.sleep(sleep)
        print(f"{group}: 走査 {page}ページ")
    ungrouped = sum(1 for r in store.records if not r.get("shop_group"))
    print(f"価格帯・エリアを付与: {assigned}件 / 未分類: {ungrouped}件")
    return assigned


def parse_args(argv):
    parser = argparse.ArgumentParser(description="関東風俗レポートを記事単位で収集する")
    mode = parser.add_mutually_exclusive_group()
    for flag, help_text in (
        ("backfill", "過去の記事を Hello world! の直前またはページ上限まで補完する"),
        ("fill-details", "詳細未保存の既存記事を補完する（欠損列のみ）"),
        ("recheck-profiles", "人物単位で掲載状態を再確認する（認証不要）"),
        ("fill-groups", "カテゴリ一覧から記事の価格帯・エリア(shop_group)を付与する"),
        ("preflight", "本番データを書かず全一覧の到達・件数・収集境界を確認する"),
    ):
        mode.add_argument(f"--{flag}", action="store_true", help=help_text)
    parser.add_argument("--resume", action="store_true", help="--backfill の中断ページから再開する")
    parser.add_argument("--all-pages", action="store_true", help="--fill-groups で全ページを走査する（初回用）")
    parser.add_argument(
        "--status", action="append", choices=shared.RECHECK_STATUSES,
        help="--recheck-profiles の対象状態（複数指定可、既定: unavailable）",
    )
    parser.add_argument("--max-pages", type=int, help="一覧の最大ページ数（FUZOKU_KANTO_MAX_PAGES または既定75）")
    parser.add_argument(
        "--overlap-days", type=int, default=shared.DEFAULT_OVERLAP_DAYS,
        help="通常収集の重複確認期間（既定3日）",
    )
    parser.add_argument("--limit", type=int, help="処理する記事数の上限（プロフィール再確認では人物数）")
    args = parser.parse_args(argv)
    if args.resume and not args.backfill:
        parser.error("--resume は --backfill と併用してください")
    if args.status and not args.recheck_profiles:
        parser.error("--status は --recheck-profiles と併用してください")
    if args.preflight and args.limit is not None:
        parser.error("--preflight は全ページ確認のため --limit を指定できません")
    if args.overlap_days < 0 or (args.limit is not None and args.limit < 1):
        parser.error("--overlap-days は0以上、--limit は1以上を指定してください")
    args.status = list(dict.fromkeys(args.status or ["unavailable"]))
    return args


def resolve_max_pages(value):
    pages = value if value is not None else int(
        os.environ.get("FUZOKU_KANTO_MAX_PAGES", shared.MAX_LISTING_PAGES)
    )
    if pages < 1:
        raise ValueError("FUZOKU_KANTO_MAX_PAGES / --max-pages は1以上を指定してください")
    return pages


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    max_pages = resolve_max_pages(args.max_pages)
    password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
    if not password and not args.recheck_profiles:
        raise RuntimeError("関東の認証にも FUZOKU_TOKAI_PASSWORD 環境変数が必要です")
    with requests.Session() as session:
        shared.configure_retries(session)
        if not args.recheck_profiles:
            login(session, password)
        if args.preflight:
            result = preflight(session, max_pages)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["covers_all_pages"]:
                raise RuntimeError("指定上限では全一覧ページを網羅できません")
            print("事前確認のみ完了。実データ収集は開始していません。")
            return
        store = ReportStore(OUTPUT_JSONL)
        if args.fill_groups:
            run_fill_groups(session, store, full=args.all_pages)
            store.save()
            return
        if store.dropped_duplicates:
            print(f"記事URLの重複行を {store.dropped_duplicates}件除きます（先頭行を保持）")
        state = shared.CollectState(STATE_JSON)
        checker = shared.ProfileChecker(session)
        listings = CollectionListings()
        collector = shared.Collector(
            session, store, state, checker, parse_article=parse_report_page,
            parse_details=parse_report_details, listing_count=get_listing_page_count,
            listing_urls=listings.fetch_urls, listing_finished=lambda: listings.finished,
        )
        try:
            if args.recheck_profiles:
                shared.run_recheck_profiles(collector, args.status, args.limit)
            elif args.fill_details:
                shared.run_fill_details(collector, args.limit)
            elif args.backfill:
                shared.run_backfill(collector, max_pages, args.resume, args.limit)
            else:
                shared.run_incremental(collector, max_pages, args.overlap_days, args.limit)
        finally:
            checker.close()
            collector.checkpoint()
            collector.report()
        if collector.counts["failed"] or state.data["failed_urls"]:
            raise RuntimeError("関東の記事取得失敗が残っています。収集状態を確認し再試行してください")


if __name__ == "__main__":
    main()
