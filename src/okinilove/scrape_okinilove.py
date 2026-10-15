import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl, write_jsonl_atomic
from okinilove.shop_filters import is_chubby_shop

DATA_DIR = ROOT_DIR / "data"
DOCS_DIR = ROOT_DIR / "docs"
SHOP_INDEX_PATH = DATA_DIR / "okinilove_shop_index.json"
RAW_HTML_PATH = DOCS_DIR / "okinilove_remove_unknown.html"
REVIEWS_JSONL_PATH = DATA_DIR / "okinilove_reviews.jsonl"
PROFILE_STATUS_CACHE_PATH = DATA_DIR / "okinilove_profile_status.json"
PROFILE_STATUS_CACHE_TTL = timedelta(days=7)
SKIPPED_ARTICLES_PATH = DATA_DIR / "okinilove_skipped_articles.json"
SKIPPED_ARTICLE_RETRY = timedelta(days=30)
REVIEW_COLUMNS = [
    "shop_name",
    "girl_name",
    "girl_url",
    "report_url",
    "evaluations",
    "report_text",
    "cityheaven_url",
    "face",
    "service",
    "age",
]
_LEGACY_COLUMNS = REVIEW_COLUMNS[:7]


def read_reviews():
    """詳細列が無い旧形式のJSONLも読み込めるようにする。"""
    reviews = read_jsonl(REVIEWS_JSONL_PATH, columns=_LEGACY_COLUMNS)
    full = read_jsonl(REVIEWS_JSONL_PATH)
    for column in ("face", "service", "age"):
        reviews[column] = full[column] if column in full.columns else None
    return reviews
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Encoding": "identity",
}
ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
PUBLICATION_RE = re.compile(r"\[公開日\d{4}/\d{2}/\d{2}\]")


SHOP_INDEX_TTL = timedelta(days=7)
SHOP_INDEX_SCHEMA_VERSION = 2
# 全投稿履歴の一覧カテゴリ（70=デリヘル, 71=ヘルス, 72=ソープ）
SHOP_INDEX_CATEGORIES = [70, 71, 72]
SHOP_INDEX_INDUSTRIES = {
    70: "デリヘル",
    71: "ヘルス",
    72: "ソープランド",
}
SHOP_EXCLUSION_TERMS = (
    "熟女",
    "熟母",
    "熟妻",
    "熟年",
    "マダム",
    "ミセス",
    "シニア",
    "おばさん",
    "美魔女",
    "アラフォー",
    "アラフィフ",
    "40代",
    "50代",
    "60代",
    "年上",
)
HISTORY_ENTRY_RE = re.compile(
    r'<a href="https?://okinilove\.com/(blog-entry-\d+\.html)"[^>]*>\s*([^<]*【全投稿履歴】)', re.S
)


def shop_names_from_title(title):
    """`店名-エリア・業種【全投稿履歴】` から (表示名, 別名の集合) を返す。

    `Sabbat（サバト）` のように英字名＋読みの店は読みを表示名にする。
    """
    title = re.sub(r"\s+", " ", title.replace("【全投稿履歴】", "")).strip()
    raw, sep, _ = title.rpartition("-")
    raw = raw.strip() if sep else title
    aliases = {raw}
    display = raw
    match = re.match(r"^([A-Za-z0-9 .\-+&'!]+)（([^）]+)）(.*)$", raw)
    if match:
        latin, reading, rest = match.groups()
        display = f"{reading}{rest}".strip()
        aliases.update({latin.strip(), f"{latin.strip()}{rest}".strip(), reading, display})
    parenthetical = re.match(r"^(.+?)（([^）]+)）(.*)$", raw)
    if parenthetical:
        base, alternate, rest = parenthetical.groups()
        aliases.update({f"{base}{rest}".strip(), f"{alternate}{rest}".strip()})
    return display, aliases


def shop_industry_from_title(title, category):
    """店舗履歴タイトルの業種を優先し、見つからなければカテゴリから返す。"""
    title = title.replace("【全投稿履歴】", "")
    suffix = title.rpartition("-")[2]
    if "ソープ" in suffix:
        return "ソープランド"
    if "デリヘル" in suffix or "デリバリーヘルス" in suffix:
        return "デリヘル"
    if "ホテルヘルス" in suffix or "ヘルス" in suffix:
        return "ヘルス"
    if "エステ" in suffix:
        return "エステ"
    return SHOP_INDEX_INDUSTRIES.get(category)


def shop_industry_label_from_title(title):
    """店舗履歴タイトルから、店名より後ろにある業種表示を返す。"""
    title = title.replace("【全投稿履歴】", "").strip()
    return title.rpartition("-")[2].strip()


def fetch_shop_index(session, categories=None):
    """カテゴリページを巡回して店舗URL、別名、業種を収集する。"""
    shops = {}
    aliases = {}
    industries = {}
    industry_labels = {}
    for category in categories or SHOP_INDEX_CATEGORIES:
        seen = set()
        page = 0
        while True:
            suffix = f"-{page}" if page else ""
            url = f"https://okinilove.com/blog-category-{category}{suffix}.html"
            response = session.get(url, headers=HEADERS, timeout=20)
            if response.status_code != 200:
                break
            found = {m.group(1): m.group(2) for m in HISTORY_ENTRY_RE.finditer(response.text)}
            new = {k: v for k, v in found.items() if k not in seen}
            if not new:
                break
            seen.update(new)
            for entry, title in new.items():
                display, names = shop_names_from_title(title)
                shops[display] = f"https://okinilove.com/{entry}"
                industry = shop_industry_from_title(title, category)
                if industry:
                    industries.setdefault(display, set()).add(industry)
                industry_label = shop_industry_label_from_title(title)
                category_labels = industry_labels.setdefault(display, {}).setdefault(
                    str(category), set()
                )
                if industry_label:
                    category_labels.add(industry_label)
                for name in names:
                    aliases.setdefault(name, display)
            page += 1
            time.sleep(0.5)
    return (
        shops,
        aliases,
        {
            shop: next(iter(values))
            for shop, values in industries.items()
            if len(values) == 1
        },
        {
            shop: {
                category: sorted(labels)
                for category, labels in category_labels.items()
            }
            for shop, category_labels in industry_labels.items()
        },
    )


def filter_shop_urls_by_categories(shops, industry_labels, categories=None):
    """熟女系のカテゴリ70店舗と、ぽっちゃり系店舗を収集対象から除外する。"""
    selected_categories = set(categories or SHOP_INDEX_CATEGORIES)
    filtered = {}
    for shop, url in shops.items():
        labels_by_category = industry_labels.get(shop, {})
        shop_categories = {int(category) for category in labels_by_category}
        if selected_categories and not shop_categories.intersection(selected_categories):
            continue
        selected_labels = [
            label
            for category in selected_categories
            for label in labels_by_category.get(str(category), [])
        ]
        if is_chubby_shop(shop, selected_labels):
            continue
        category_70_labels = labels_by_category.get("70", [])
        if 70 in selected_categories and any(
            term in text.casefold()
            for text in (shop, *category_70_labels)
            for term in SHOP_EXCLUSION_TERMS
        ):
            continue
        filtered[shop] = url
    return filtered


def load_shop_urls(session):
    """店舗索引をキャッシュ(7日)から読み、古ければ再取得する。(shops, aliases)を返す。"""
    if SHOP_INDEX_PATH.exists():
        cached = json.loads(SHOP_INDEX_PATH.read_text(encoding="utf-8"))
        try:
            fresh = datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"]) < SHOP_INDEX_TTL
        except (KeyError, TypeError, ValueError):
            fresh = False
        if (
            fresh
            and cached.get("schema_version") == SHOP_INDEX_SCHEMA_VERSION
            and cached.get("categories") == SHOP_INDEX_CATEGORIES
            and cached.get("shops")
            and "aliases" in cached
            and "industries" in cached
            and "industry_labels" in cached
        ):
            return (
                filter_shop_urls_by_categories(
                    cached["shops"], cached["industry_labels"]
                ),
                cached["aliases"],
            )

    shops, aliases, industries, industry_labels = fetch_shop_index(session)
    SHOP_INDEX_PATH.write_text(
        json.dumps(
            {
                "schema_version": SHOP_INDEX_SCHEMA_VERSION,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "categories": SHOP_INDEX_CATEGORIES,
                "shops": shops,
                "aliases": aliases,
                "industries": industries,
                "industry_labels": industry_labels,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"店舗索引を更新: {len(shops)}店")
    return filter_shop_urls_by_categories(shops, industry_labels), aliases


def normalize_existing_shop_names(aliases):
    """既存レビューの店名を索引の表示名に揃える（JSONLと一覧HTML）。"""
    existing = read_reviews()
    renamed = existing["shop_name"].map(lambda name: aliases.get(name, name))
    changed = int((renamed != existing["shop_name"]).sum())
    if changed:
        existing["shop_name"] = renamed
        write_jsonl_atomic(existing, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)

    soup = BeautifulSoup(RAW_HTML_PATH.read_text(encoding="utf-8"), "html.parser")
    html_changed = 0
    for row in soup.select("tbody tr:not(.detail-row)"):
        cell = row.find("td")
        name = cell.get_text(strip=True) if cell else ""
        if cell and not cell.find("a") and aliases.get(name, name) != name:
            cell.string = aliases[name]
            html_changed += 1
    if html_changed:
        RAW_HTML_PATH.write_text(str(soup), encoding="utf-8")
    if changed or html_changed:
        print(f"店名を統一: JSONL {changed}件、一覧HTML {html_changed}件")


def existing_article_urls(soup):
    return {
        anchor.get("href")
        for anchor in soup.select("tbody a[href]")
        if ARTICLE_URL_RE.match(anchor["href"])
    }


def profile_link_in_row(row):
    return next(
        (
            anchor
            for anchor in row.find_all("a", href=True)
            if not ARTICLE_URL_RE.match(anchor["href"])
            and not anchor.find_parent(class_="review-item")
        ),
        None,
    )


_UNREACHABLE_HOSTS = set()
# 同一ホストへのタイムアウトがこの回数連続したら、以降の確認をスキップする
_TIMEOUT_SKIP_THRESHOLD = 3
_HOST_TIMEOUT_COUNTS = {}
_HOST_STATE_LOCK = threading.Lock()
# 閉鎖済みで存在しないホストは掲載終了として扱う
_DEAD_HOSTS = {"www.dolce-vita-e.com"}


def profile_check_url(url):
    """数字付き www のプロフィールURLは通常の www ホストで確認する。"""
    parsed = urlparse(url)
    if not parsed.hostname or not re.match(
        r"^www\d+\.", parsed.hostname, re.IGNORECASE
    ):
        return url
    netloc = re.sub(
        r"^www\d+\.", "www.", parsed.netloc, count=1, flags=re.IGNORECASE
    )
    return parsed._replace(netloc=netloc).geturl()


def _fetch_profile_status(url):
    check_url = profile_check_url(url)
    host = urlparse(check_url).netloc.lower()
    if host in _DEAD_HOSTS:
        return 410
    if host in _UNREACHABLE_HOSTS:
        return None
    try:
        response = requests.get(
            check_url,
            headers=HEADERS,
            timeout=(5, 12),
            allow_redirects=True,
            stream=True,
        )
        with _HOST_STATE_LOCK:
            _HOST_TIMEOUT_COUNTS.pop(host, None)
        try:
            if (
                response.status_code == 400
                and urlparse(check_url).netloc.lower() == "www.yoasobi.co.jp"
            ):
                body = next(response.iter_content(chunk_size=64 * 1024), b"")
                page_text = BeautifulSoup(
                    body.decode(response.encoding or "utf-8", errors="replace"),
                    "html.parser",
                ).get_text(" ", strip=True)
                if "ページが見つかりません" in page_text and "ページが削除されています" in page_text:
                    return 410
            if 200 <= response.status_code < 400 and not is_personal_profile(url, response.url):
                return 410
            return response.status_code
        finally:
            response.close()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as error:
        with _HOST_STATE_LOCK:
            count = _HOST_TIMEOUT_COUNTS.get(host, 0) + 1
            _HOST_TIMEOUT_COUNTS[host] = count
            newly_skipped = count >= _TIMEOUT_SKIP_THRESHOLD and host not in _UNREACHABLE_HOSTS
            if newly_skipped:
                _UNREACHABLE_HOSTS.add(host)
        if newly_skipped:
            print(
                f"{host} へのタイムアウトが{count}回連続したため保持（以降スキップ）: {url}"
            )
        else:
            print(f"プロフィール確認タイムアウトのため保持: {url} ({error})")
        return None
    except requests.RequestException as error:
        if "NameResolutionError" in str(error):
            # ホスト名自体が存在しない場合は、同じホストを何度も確認しない
            with _HOST_STATE_LOCK:
                _UNREACHABLE_HOSTS.add(host)
            print(f"ホスト名を解決できないため保持（以降スキップ）: {host} 例: {url}")
        else:
            print(f"プロフィール確認失敗のため保持: {url} ({error})")
        return None


def find_unavailable_profile_urls(urls, now=None):
    """URLの状態をキャッシュし、404/410 のURLだけを返す。"""
    now = now or datetime.now(timezone.utc)
    cache_path = PROFILE_STATUS_CACHE_PATH
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    else:
        cache = {}

    statuses = {}
    pending = []
    for url in set(urls):
        cached = cache.get(url)
        check_url = profile_check_url(url)
        if urlparse(check_url).netloc.lower() in _DEAD_HOSTS:
            pending.append(url)
            continue
        if isinstance(cached, dict) and (
            cached.get("check_url") == check_url
            or (check_url == url and "check_url" not in cached)
        ):
            try:
                checked_at = datetime.fromisoformat(cached["checked_at"])
                if now - checked_at < PROFILE_STATUS_CACHE_TTL:
                    statuses[url] = cached["status"]
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        pending.append(url)

    print(f"プロフィール状態確認: キャッシュ利用{len(statuses)}件 / 新規確認{len(pending)}件")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_profile_status, url): url for url in pending}
        for future in as_completed(futures):
            url = futures[future]
            status = future.result()
            statuses[url] = status
            if status is not None and (200 <= status < 400 or status in {404, 410}):
                cache[url] = {
                    "checked_at": now.isoformat(),
                    "status": status,
                    "check_url": profile_check_url(url),
                }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(cache_path)
    failed = sum(1 for url in pending if statuses.get(url) is None)
    if failed:
        print(f"プロフィール状態確認失敗のため保持: {failed}件")
    return {url for url, status in statuses.items() if status in {404, 410}}


def remove_unavailable_profiles():
    """掲載終了プロフィールを人物一覧HTMLとレビューJSONLから削除する。"""
    with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
        raw_soup = BeautifulSoup(source.read(), "html.parser")

    raw_rows = raw_soup.select("tbody tr")
    urls = set()
    for row in raw_rows:
        profile_link = profile_link_in_row(row)
        if profile_link:
            urls.add(profile_link["href"])

    reviews = read_reviews()
    if "girl_url" in reviews:
        urls.update(reviews["girl_url"].dropna().astype(str))

    unavailable_urls = find_unavailable_profile_urls(urls)
    if not unavailable_urls:
        print("掲載終了プロフィールは見つかりませんでした")
        return 0, 0

    removed_rows = 0
    for row in raw_rows:
        profile_link = profile_link_in_row(row)
        if profile_link and profile_link["href"] in unavailable_urls:
            detail = row.find_next_sibling("tr", class_="detail-row") if "person-row" in (row.get("class") or []) else None
            if detail:
                detail.decompose()
            row.decompose()
            removed_rows += 1

    keep_reviews = ~reviews["girl_url"].astype("string").isin(unavailable_urls)
    removed_reviews = int((~keep_reviews).sum())
    reviews = reviews.loc[keep_reviews].reset_index(drop=True)

    if removed_rows:
        RAW_HTML_PATH.write_text(str(raw_soup), encoding="utf-8")
    if removed_reviews:
        write_jsonl_atomic(reviews, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)

    print(
        f"掲載終了プロフィールを削除: URL{len(unavailable_urls)}件、"
        f"一覧行{removed_rows}件、レビュー{removed_reviews}件"
    )
    return removed_rows, removed_reviews


def fetch_article_urls(session, history_url):
    response = session.get(history_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return {
        anchor["href"]
        for container in soup.find_all("div", class_="ently_text")
        for table in container.find_all("table")
        for anchor in table.find_all("a", href=True)
        if ARTICLE_URL_RE.match(anchor["href"])
    }


def is_personal_profile(original_url, final_url):
    """転送後のURLに元の個人IDが残っていれば個人ページ。トップや店ページへの転送、アーカイブは対象外。"""
    if urlparse(original_url).netloc.endswith(("okinilove.com", "web.archive.org")):
        return False
    if urlparse(final_url).netloc.endswith(("okinilove.com", "web.archive.org")):
        return False
    ids = re.findall(r"\d{4,}", original_url.split("?aid=")[0].replace("&aid=ad_001", ""))
    return not ids or ids[-1] in final_url


def extract_section(looks_text, label):
    match = re.search(rf"【{label}】■*\s*(.*?)\s*(?:■■■|$)", looks_text or "")
    return match.group(1).strip() if match else ""


def extract_face(looks_text):
    return extract_section(looks_text, "顔")


def extract_style(looks_text):
    return extract_section(looks_text, "スタイル")


REPORT_DETAILS_PATH = DATA_DIR / "okinilove_report_details.json"
AGE_PATTERNS = (
    re.compile(r"年齢\s*[:：]?\s*(\d{2})\s*(?:歳|才)"),
    re.compile(r"名前\(年齢\)\s*\S+\s*\((\d{2})\)"),
    re.compile(r"(?<!\d)(\d{2})\s*歳"),
)


def decode_response(response):
    for encoding in ("utf-8", response.apparent_encoding):
        try:
            return response.content.decode(encoding or "utf-8")
        except (UnicodeDecodeError, LookupError):
            continue
    return response.content.decode("utf-8", errors="replace")


def extract_age(page_html):
    text = BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
    for pattern in AGE_PATTERNS:
        match = pattern.search(text)
        if match and 18 <= int(match.group(1)) <= 60:
            return match.group(1)
    return ""


def article_section_fields(soup, fragment):
    """記事内の見出しごとに (見出し, 値テキスト, 値内リンク) を返す。"""
    section_anchor = soup.find(attrs={"name": fragment}) or soup.find(id=fragment)
    if section_anchor is None:
        return None

    fields = []
    for sibling in section_anchor.parent.find_next_siblings():
        if sibling.name == "h3":
            break
        if sibling.name != "h4":
            continue

        heading = sibling.get_text(" ", strip=True)
        # 見出しから次の見出しまでが値（タグ・文字列どちらの形式にも対応）
        value_nodes = []
        for node in sibling.next_siblings:
            if getattr(node, "name", None) in {"h3", "h4"}:
                break
            value_nodes.append(node)
        value_text = " ".join(
            (node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node).strip())
            for node in value_nodes
        ).strip()
        value_link = next(
            (
                found
                for node in value_nodes
                if hasattr(node, "find_all")
                for found in ([node] if node.name == "a" and node.get("href") else node.find_all("a", href=True))
            ),
            None,
        )
        fields.append((heading, value_text, value_link))
    return fields


def fetch_article_record(session, shop_name, article_url):
    response = session.get(article_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    fragment = urlparse(article_url).fragment
    fields = article_section_fields(soup, fragment)
    if fields is None:
        return None

    girl_name = None
    girl_url = None
    gap = None
    stars = None
    face = ""
    service = ""

    for heading, value_text, value_link in fields:
        if "ルックス" in heading:
            face = extract_face(value_text)
        elif "サービスについて" in heading:
            service = " ".join(value_text.split())
        elif "女の子の名前" in heading:
            if value_link:
                girl_name = value_link.get_text(strip=True)
                girl_url = value_link.get("href")
            else:
                girl_name = value_text or None
        elif "パネル（写真）とのギャップ印象" in heading and value_text:
            gap = value_text
        elif "利用者満足度" in heading:
            stars = value_text if "★" in value_text or "☆" in value_text else None

    # 名前にリンクがない＝掲載終了として扱う
    if not girl_name or not stars or not girl_url:
        return None

    profile_url = profile_check_url(girl_url)
    profile = session.get(profile_url, headers=HEADERS, timeout=15)
    if profile.status_code in {400, 404, 410}:
        return None
    profile.raise_for_status()
    if not is_personal_profile(girl_url, profile.url):
        return None

    report_text = extract_report_detail(session, article_url) or gap or ""
    return {
        "shop": shop_name,
        "url": article_url,
        "girl_url": profile.url or profile_url,
        "name": girl_name,
        "gap": gap or "",
        "star": stars,
        "report_text": report_text,
        "face": face,
        "service": service,
        "age": extract_age(decode_response(profile)),
    }


def load_skipped_articles():
    if not SKIPPED_ARTICLES_PATH.exists():
        return {}
    try:
        return json.loads(SKIPPED_ARTICLES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_skipped_articles(skipped):
    SKIPPED_ARTICLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = SKIPPED_ARTICLES_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(skipped, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary_path.replace(SKIPPED_ARTICLES_PATH)


def recently_skipped_urls(skipped, now=None):
    """取り込み対象外と判定済みで、再確認期間内の記事URL。"""
    now = now or datetime.now(timezone.utc)
    urls = set()
    for url, checked_at in skipped.items():
        # #付きでない記事は履歴・掲示板ページで、女の子のレポートにはならないため再確認しない
        if "#" not in url:
            urls.add(url)
            continue
        try:
            if now - datetime.fromisoformat(checked_at) < SKIPPED_ARTICLE_RETRY:
                urls.add(url)
        except (TypeError, ValueError):
            continue
    return urls


def scrape_new_records(
    session,
    shop_urls,
    known_article_urls,
    batch_callback=None,
    batch_size=25,
):
    new_records = []
    skipped = load_skipped_articles()
    skip_urls = recently_skipped_urls(skipped)

    def flush_batch():
        nonlocal new_records
        if batch_callback and new_records:
            batch_callback(new_records)
            new_records = []

    try:
        for shop_name, history_url in shop_urls.items():
            try:
                article_urls = fetch_article_urls(session, history_url)
            except requests.RequestException as error:
                print(f"店舗履歴を取得できません: {shop_name}: {error}")
                continue

            pending = sorted(article_urls - known_article_urls - skip_urls)
            print(f"{shop_name}: 新規候補 {len(pending)}件")
            for article_url in pending:
                try:
                    record = fetch_article_record(session, shop_name, article_url)
                except requests.RequestException as error:
                    # 通信失敗は判定できていないので、次回また試す
                    print(f"記事を取得できません: {article_url}: {error}")
                    time.sleep(0.5)
                    continue
                if record:
                    new_records.append(record)
                    known_article_urls.add(article_url)
                    if len(new_records) >= batch_size:
                        flush_batch()
                else:
                    skipped[article_url] = datetime.now(timezone.utc).isoformat()
                time.sleep(0.5)
            time.sleep(0.5)
    finally:
        save_skipped_articles(skipped)
    flush_batch()
    return new_records


def append_records_to_raw_html(records):
    with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
        soup = BeautifulSoup(source.read(), "html.parser")
    tbody = soup.select_one("#girlsTable tbody, #reviews tbody") or soup.find("tbody")
    if tbody is None:
        raise ValueError(f"レビューHTMLにtbodyがありません: {RAW_HTML_PATH}")

    existing_urls = existing_article_urls(soup)
    added = 0
    for record in records:
        if record["url"] in existing_urls:
            continue

        row = soup.new_tag("tr")
        shop_cell = soup.new_tag("td")
        shop_cell.string = record["shop"]

        article_cell = soup.new_tag("td")
        article_link = soup.new_tag("a", href=record["url"], target="_blank", rel="noopener noreferrer")
        article_link.string = "記事リンク"
        article_cell.append(article_link)

        girl_cell = soup.new_tag("td")
        if record["girl_url"]:
            girl_link = soup.new_tag(
                "a",
                href=record["girl_url"],
                target="_blank",
                rel="noopener noreferrer",
            )
            girl_link.string = record["name"]
            girl_cell.append(girl_link)
        else:
            girl_cell.string = record["name"]

        gap_cell = soup.new_tag("td")
        gap_cell.string = record["gap"]
        star_cell = soup.new_tag("td")
        star_cell.string = record["star"]
        row.extend([shop_cell, article_cell, girl_cell, gap_cell, star_cell])
        tbody.append(row)
        existing_urls.add(record["url"])
        added += 1

    if added:
        RAW_HTML_PATH.write_text(str(soup), encoding="utf-8")
    return added


def append_records_to_jsonl(records):
    existing = read_reviews()

    known_urls = {_normalize_report_url(u) for u in existing["report_url"].dropna()}
    additions = []
    for record in records:
        normalized_url = _normalize_report_url(record["url"])
        if normalized_url in known_urls:
            continue
        additions.append(
            {
                "shop_name": record["shop"],
                "girl_name": record["name"],
                "girl_url": record["girl_url"],
                "report_url": record["url"],
                "evaluations": f"評価 {record['star']} →記事リンク",
                "report_text": record["report_text"],
                "cityheaven_url": record["girl_url"]
                if record["girl_url"] and "cityheaven.net" in record["girl_url"]
                else None,
                "face": record.get("face", ""),
                "service": record.get("service", ""),
                "age": record.get("age", ""),
            }
        )
        known_urls.add(normalized_url)

    if additions:
        updated = pd.concat(
            [existing, pd.DataFrame(additions, columns=REVIEW_COLUMNS)],
            ignore_index=True,
        )
        write_jsonl_atomic(updated, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    return len(additions)


def _normalize_report_url(url):
    return str(url).replace("http://", "https://", 1)


def extract_report_detail(session, article_url):
    base_url, _, fragment = article_url.partition("#")
    response = session.get(base_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    target = soup.find(attrs={"name": fragment}) if fragment else soup
    if target is None:
        return ""
    return _report_text_after(target.parent if fragment else target)


def _report_text_after(section):
    for sibling in section.find_next_siblings():
        if sibling.name == "h4" and "レポート内容補足などあれば" in sibling.get_text():
            parts = []
            for item in sibling.next_siblings:
                if getattr(item, "name", None) == "h3":
                    break
                if getattr(item, "name", None) == "br":
                    parts.append("\n")
                    continue
                text = item.get_text(" ", strip=True) if getattr(item, "name", None) else str(item).strip()
                if not text:
                    continue
                if PUBLICATION_RE.search(text):
                    break
                parts.append(text)
            return "".join(parts).strip()
    return ""


def _fetch_age(girl_url):
    try:
        response = requests.get(
            profile_check_url(girl_url), headers=HEADERS, timeout=(5, 15)
        )
        if not 200 <= response.status_code < 300:
            return None
        return extract_age(decode_response(response))
    except requests.RequestException:
        return None


def _fetch_face(base_url, fragment):
    try:
        response = requests.get(base_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return None
    fields = article_section_fields(BeautifulSoup(response.text, "html.parser"), fragment)
    if fields is None:
        return ""
    return next((extract_face(text) for heading, text, _ in fields if "ルックス" in heading), "")


def backfill_face_and_age():
    """face/age が未取得（null）の既存行を記事とプロフィールから補完する。取得不可は空文字で確定。"""
    reviews = read_reviews()
    missing_face = reviews[reviews["face"].isna()]
    missing_age = reviews[reviews["age"].isna()]
    if missing_face.empty and missing_age.empty:
        return 0

    faces, ages = {}, {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        face_jobs = {
            executor.submit(_fetch_face, *url.partition("#")[::2]): url
            for url in missing_face["report_url"].astype(str)
        }
        age_jobs = {
            executor.submit(_fetch_age, url): url
            for url in missing_age["girl_url"].dropna().astype(str).unique()
        }
        for jobs, target in ((face_jobs, faces), (age_jobs, ages)):
            for future in as_completed(jobs):
                target[jobs[future]] = future.result()

    reviews["face"] = [
        faces.get(str(url), face) if pd.isna(face) else face
        for url, face in zip(reviews["report_url"], reviews["face"])
    ]
    reviews["age"] = [
        ages.get(str(url), age) if pd.isna(age) else age
        for url, age in zip(reviews["girl_url"], reviews["age"])
    ]
    reviews["face"] = reviews["face"].where(reviews["face"].notna(), None)
    reviews["age"] = reviews["age"].where(reviews["age"].notna(), None)
    write_jsonl_atomic(reviews, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    print(
        f"face/age補完: face {sum(bool(v) for v in faces.values())}/{len(faces)}件、"
        f"age {sum(bool(v) for v in ages.values())}/{len(ages)}件"
    )
    return len(faces) + len(ages)


def backfill_review_service_from_cache():
    """記事詳細キャッシュのサービス内容をメインレビューJSONLへ補完する。"""
    reviews = read_reviews()
    details = load_report_details()
    details_by_url = {
        _normalize_report_url(url): detail for url, detail in details.items()
    }
    updated = 0

    for index, report_url in reviews["report_url"].items():
        detail = details_by_url.get(_normalize_report_url(report_url))
        current = reviews.at[index, "service"]
        value = detail.get("service") if detail else None
        value = " ".join(value.split()) if isinstance(value, str) else value
        if value and current != value:
            reviews.at[index, "service"] = value
            updated += 1

    if updated:
        write_jsonl_atomic(reviews, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    print(f"サービス内容を補完: {updated}件")
    return updated


def parse_report_details(soup, fragment):
    fields = article_section_fields(soup, fragment)
    if fields is None:
        return None
    looks = next((text for heading, text, _ in fields if "ルックス" in heading), "")
    service = next(
        (
            " ".join(text.split())
            for heading, text, _ in fields
            if "サービスについて" in heading
        ),
        "",
    )
    anchor = soup.find(attrs={"name": fragment}) or soup.find(id=fragment)
    return {
        "face": extract_face(looks),
        "style": extract_style(looks),
        "service": service,
        "report_text": _report_text_after(anchor.parent) if anchor else "",
    }


def load_report_details():
    if not REPORT_DETAILS_PATH.exists():
        return {}
    return json.loads(REPORT_DETAILS_PATH.read_text(encoding="utf-8"))


def backfill_report_details(report_urls):
    """記事ごとに1回取得し、顔・スタイル・サービス・本文をキャッシュする。"""
    details = load_report_details()
    details_by_url = {
        _normalize_report_url(url): detail for url, detail in details.items()
    }
    pending = {}
    for url in report_urls:
        if (
            url
            and "#" in url
            and "service" not in details_by_url.get(_normalize_report_url(url), {})
        ):
            pending.setdefault(url.partition("#")[0], []).append(url)
    if not pending:
        return 0

    def work(base_url):
        try:
            response = requests.get(base_url, headers=HEADERS, timeout=20)
            response.raise_for_status()
        except requests.RequestException as error:
            print(
                f"Failed to fetch report details for {base_url}: {error}",
                file=sys.stderr,
            )
            return {}
        soup = BeautifulSoup(response.text, "html.parser")
        return {
            url: parsed
            for url in pending[base_url]
            if (parsed := parse_report_details(soup, url.partition("#")[2])) is not None
        }

    with ThreadPoolExecutor(max_workers=6) as executor:
        for result in executor.map(work, pending):
            details.update(result)
    REPORT_DETAILS_PATH.write_text(
        json.dumps(details, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"レポート詳細を取得: {len(details)}件（記事{len(pending)}件を確認）")
    return len(details)


def refresh_okinilove_data():
    remove_unavailable_profiles()
    with requests.Session() as session:
        shop_urls, shop_aliases = load_shop_urls(session)
        normalize_existing_shop_names(shop_aliases)
        with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
            current_soup = BeautifulSoup(source.read(), "html.parser")
        known_urls = existing_article_urls(current_soup)
        known_urls.update(
            read_reviews()["report_url"]
            .dropna()
            .astype(str)
        )
        new_records = scrape_new_records(session, shop_urls, known_urls)
        raw_added = append_records_to_raw_html(new_records)
        jsonl_added = append_records_to_jsonl(new_records)
        backfill_face_and_age()
        from okinilove.render_people_summary import _extract_rows, render_people_summary_file

        report_urls = [
            report["url"]
            for row in _extract_rows(BeautifulSoup(RAW_HTML_PATH.read_text(encoding="utf-8"), "html.parser"))
            for report in row["reports"]
        ]
        backfill_report_details(report_urls)
        backfill_review_service_from_cache()
        people_count = render_people_summary_file(RAW_HTML_PATH)

    print(
        f"Okinilove更新: 取得{len(new_records)}件、一覧追加{raw_added}件、"
        f"JSONL追加{jsonl_added}件、人物別表示{people_count}人"
    )
    return raw_added, jsonl_added


def refresh_shop_category_reviews(category):
    """指定カテゴリの未取得レビューを補完し、対象外店舗を除外する。"""
    counts = {"raw": 0, "jsonl": 0}
    with requests.Session() as session:
        shops, aliases, industries, industry_labels = fetch_shop_index(
            session, categories=[category]
        )
        shop_urls = filter_shop_urls_by_categories(
            shops, industry_labels, categories=[category]
        )
        if category == 70:
            print(
                f"カテゴリ70: 店舗{len(shops)}店、対象外店舗除外"
                f"{len(shops) - len(shop_urls)}店、取得対象{len(shop_urls)}店"
            )
        else:
            print(
                f"カテゴリ{category}: 店舗{len(shops)}店、対象外店舗除外"
                f"{len(shops) - len(shop_urls)}店、取得対象{len(shop_urls)}店"
            )

        cached_index = {}
        if SHOP_INDEX_PATH.exists():
            cached_index = json.loads(
                SHOP_INDEX_PATH.read_text(encoding="utf-8")
            )
        cached_index["schema_version"] = SHOP_INDEX_SCHEMA_VERSION
        cached_index["fetched_at"] = datetime.now(timezone.utc).isoformat()
        cached_index["categories"] = SHOP_INDEX_CATEGORIES
        cached_index["shops"] = {
            **cached_index.get("shops", {}),
            **shops,
        }
        cached_index["aliases"] = {
            **cached_index.get("aliases", {}),
            **aliases,
        }
        cached_index["industries"] = {
            **cached_index.get("industries", {}),
            **industries,
        }
        merged_labels = cached_index.get("industry_labels", {})
        for shop, category_map in industry_labels.items():
            merged_labels.setdefault(shop, {}).update(category_map)
        cached_index["industry_labels"] = merged_labels
        SHOP_INDEX_PATH.write_text(
            json.dumps(cached_index, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
            current_soup = BeautifulSoup(source.read(), "html.parser")
        known_urls = existing_article_urls(current_soup)
        known_urls.update(
            read_reviews()["report_url"].dropna().astype(str)
        )

        def persist_batch(records):
            counts["jsonl"] += append_records_to_jsonl(records)
            counts["raw"] += append_records_to_raw_html(records)

        scrape_new_records(
            session,
            shop_urls,
            known_urls,
            batch_callback=persist_batch,
        )
        backfill_face_and_age()
        from okinilove.render_people_summary import (
            _extract_rows,
            render_people_summary_file,
        )

        report_urls = [
            report["url"]
            for row in _extract_rows(
                BeautifulSoup(
                    RAW_HTML_PATH.read_text(encoding="utf-8"), "html.parser"
                )
            )
            for report in row["reports"]
        ]
        backfill_report_details(report_urls)
        backfill_review_service_from_cache()
        people_count = render_people_summary_file(RAW_HTML_PATH)

    print(
        f"カテゴリ{category}更新: 対象{len(shop_urls)}店、"
        f"レビュー追加{counts['jsonl']}件、人物別表示{people_count}人"
    )
    return counts["raw"], counts["jsonl"]


if __name__ == "__main__":
    refresh_okinilove_data()
