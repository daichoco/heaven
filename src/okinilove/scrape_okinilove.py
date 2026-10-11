import json
import re
import sys
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

DATA_DIR = ROOT_DIR / "data"
DOCS_DIR = ROOT_DIR / "docs"
SHOP_INDEX_PATH = DATA_DIR / "okinilove_shop_index.json"
RAW_HTML_PATH = DOCS_DIR / "okinilove_remove_unknown.html"
REVIEWS_JSONL_PATH = DATA_DIR / "okinilove_reviews.jsonl"
PROFILE_STATUS_CACHE_PATH = DATA_DIR / "okinilove_profile_status.json"
PROFILE_STATUS_CACHE_TTL = timedelta(hours=24)
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
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Encoding": "identity",
}
ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
PUBLICATION_RE = re.compile(r"\[公開日\d{4}/\d{2}/\d{2}\]")


SHOP_INDEX_TTL = timedelta(days=7)
# 全投稿履歴の一覧カテゴリ。まずソープのみ（70=デリヘル, 71=ヘルス, 73=番外編は未対応）
SHOP_INDEX_CATEGORIES = [72]
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
    return display, aliases


def fetch_shop_index(session):
    """カテゴリページを巡回して ({表示名: 全投稿履歴URL}, {別名: 表示名}) を作る。"""
    shops = {}
    aliases = {}
    for category in SHOP_INDEX_CATEGORIES:
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
                for name in names:
                    aliases.setdefault(name, display)
            page += 1
            time.sleep(0.5)
    return shops, aliases


def load_shop_urls(session):
    """店舗索引をキャッシュ(7日)から読み、古ければ再取得する。(shops, aliases)を返す。"""
    if SHOP_INDEX_PATH.exists():
        cached = json.loads(SHOP_INDEX_PATH.read_text(encoding="utf-8"))
        try:
            fresh = datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"]) < SHOP_INDEX_TTL
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh and cached.get("categories") == SHOP_INDEX_CATEGORIES and cached.get("shops") and "aliases" in cached:
            return cached["shops"], cached["aliases"]

    shops, aliases = fetch_shop_index(session)
    SHOP_INDEX_PATH.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "categories": SHOP_INDEX_CATEGORIES,
                "shops": shops,
                "aliases": aliases,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"店舗索引を更新: {len(shops)}店")
    return shops, aliases


def normalize_existing_shop_names(aliases):
    """既存レビューの店名を索引の表示名に揃える（JSONLと一覧HTML）。"""
    existing = read_jsonl(REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    renamed = existing["shop_name"].map(lambda name: aliases.get(name, name))
    changed = int((renamed != existing["shop_name"]).sum())
    if changed:
        existing["shop_name"] = renamed
        write_jsonl_atomic(existing, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)

    soup = BeautifulSoup(RAW_HTML_PATH.read_text(encoding="utf-8"), "html.parser")
    html_changed = 0
    for row in soup.select("tbody tr"):
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


def _fetch_profile_status(url):
    host = urlparse(url).netloc.lower()
    if host in _UNREACHABLE_HOSTS:
        return None
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=(5, 12),
            allow_redirects=True,
            stream=True,
        )
        try:
            if response.status_code == 400 and urlparse(url).netloc.lower() == "www.yoasobi.co.jp":
                body = next(response.iter_content(chunk_size=64 * 1024), b"")
                page_text = BeautifulSoup(
                    body.decode(response.encoding or "utf-8", errors="replace"),
                    "html.parser",
                ).get_text(" ", strip=True)
                if "ページが見つかりません" in page_text and "ページが削除されています" in page_text:
                    return 410
            return response.status_code
        finally:
            response.close()
    except requests.RequestException as error:
        if "NameResolutionError" in str(error):
            # ホスト名自体が存在しない場合は、同じホストを何度も確認しない
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
        if cached:
            try:
                checked_at = datetime.fromisoformat(cached["checked_at"])
                if now - checked_at < PROFILE_STATUS_CACHE_TTL:
                    statuses[url] = cached["status"]
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        pending.append(url)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_profile_status, url): url for url in pending}
        for future in as_completed(futures):
            url = futures[future]
            status = future.result()
            statuses[url] = status
            if status is not None and (200 <= status < 400 or status in {404, 410}):
                cache[url] = {"checked_at": now.isoformat(), "status": status}

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(cache_path)
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

    reviews = read_jsonl(REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
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


def fetch_article_record(session, shop_name, article_url):
    response = session.get(article_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    fragment = urlparse(article_url).fragment
    section_anchor = soup.find(attrs={"name": fragment}) or soup.find(id=fragment)
    if section_anchor is None:
        return None

    section = section_anchor.parent
    girl_name = None
    girl_url = None
    gap = None
    stars = None

    for sibling in section.find_next_siblings():
        if sibling.name == "h3":
            break
        if sibling.name != "h4":
            continue

        heading = sibling.get_text(" ", strip=True)
        next_sibling = sibling.find_next_sibling()
        if "女の子の名前" in heading:
            link = next_sibling.find("a", href=True) if next_sibling else None
            if link:
                girl_name = link.get_text(strip=True)
                girl_url = link.get("href")
            elif next_sibling:
                girl_name = next_sibling.get_text(" ", strip=True) or None
        elif "パネル（写真）とのギャップ印象" in heading and next_sibling:
            gap = next_sibling.get_text(" ", strip=True)
        elif "利用者満足度" in heading and next_sibling:
            candidate = next_sibling.get_text(" ", strip=True)
            stars = candidate if "★" in candidate or "☆" in candidate else None

    if not girl_name or not stars:
        return None
    if girl_url and "okinilove.com" in girl_url:
        return None

    if girl_url:
        profile = session.get(girl_url, headers=HEADERS, timeout=15)
        if profile.status_code in {400, 404, 410}:
            return None
        profile.raise_for_status()
        if profile.url != girl_url and urlparse(profile.url).netloc.endswith("okinilove.com"):
            return None

    report_text = extract_report_detail(session, article_url) or gap or ""
    return {
        "shop": shop_name,
        "url": article_url,
        "girl_url": girl_url,
        "name": girl_name,
        "gap": gap or "",
        "star": stars,
        "report_text": report_text,
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


def scrape_new_records(session, shop_urls, known_article_urls):
    new_records = []
    skipped = load_skipped_articles()
    skip_urls = recently_skipped_urls(skipped)
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
                else:
                    skipped[article_url] = datetime.now(timezone.utc).isoformat()
                time.sleep(0.5)
            time.sleep(0.5)
    finally:
        save_skipped_articles(skipped)
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
    existing = read_jsonl(REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    known_urls = set(existing["report_url"].dropna().astype(str))
    additions = []
    for record in records:
        if record["url"] in known_urls:
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
            }
        )
        known_urls.add(record["url"])

    if additions:
        updated = pd.concat(
            [existing, pd.DataFrame(additions, columns=REVIEW_COLUMNS)],
            ignore_index=True,
        )
        write_jsonl_atomic(updated, REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)
    return len(additions)


def extract_report_detail(session, article_url):
    base_url, _, fragment = article_url.partition("#")
    response = session.get(base_url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    target = soup.find(attrs={"name": fragment}) if fragment else soup
    if target is None:
        return ""

    section = target.parent if fragment else target
    for sibling in section.find_next_siblings():
        if sibling.name == "h4" and "レポート内容補足などあれば" in sibling.get_text():
            parts = []
            for item in sibling.next_siblings:
                if getattr(item, "name", None) == "h3":
                    break
                text = item.get_text(" ", strip=True) if getattr(item, "name", None) else str(item).strip()
                if not text:
                    continue
                if PUBLICATION_RE.search(text):
                    break
                parts.append("\n" if getattr(item, "name", None) == "br" else text)
            return "".join(parts)
    return ""


def refresh_okinilove_data():
    remove_unavailable_profiles()
    with requests.Session() as session:
        shop_urls, shop_aliases = load_shop_urls(session)
        normalize_existing_shop_names(shop_aliases)
        with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
            current_soup = BeautifulSoup(source.read(), "html.parser")
        known_urls = existing_article_urls(current_soup)
        known_urls.update(
            read_jsonl(REVIEWS_JSONL_PATH, columns=REVIEW_COLUMNS)["report_url"]
            .dropna()
            .astype(str)
        )
        new_records = scrape_new_records(session, shop_urls, known_urls)
        raw_added = append_records_to_raw_html(new_records)
        jsonl_added = append_records_to_jsonl(new_records)
        from okinilove.render_people_summary import render_people_summary_file

        people_count = render_people_summary_file(RAW_HTML_PATH)

    print(
        f"Okinilove更新: 取得{len(new_records)}件、一覧追加{raw_added}件、"
        f"JSONL追加{jsonl_added}件、人物別表示{people_count}人"
    )
    return raw_added, jsonl_added


if __name__ == "__main__":
    refresh_okinilove_data()
