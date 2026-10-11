import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DOCS_DIR = ROOT_DIR / "docs"
SHOP_URLS_PATH = DATA_DIR / "okinilove_shop_urls.json"
RAW_HTML_PATH = DOCS_DIR / "okinilove_remove_unknown.html"
RECONSTRUCTED_HTML_PATH = DOCS_DIR / "okinilove_reconstructed.html"
PROFILE_STATUS_CACHE_PATH = DATA_DIR / "okinilove_profile_status.json"
PROFILE_STATUS_CACHE_TTL = timedelta(hours=24)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Encoding": "identity",
}
ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
PUBLICATION_RE = re.compile(r"\[公開日\d{4}/\d{2}/\d{2}\]")


def load_shop_urls():
    with SHOP_URLS_PATH.open("r", encoding="utf-8") as source:
        shop_records = json.load(source)
    return {
        shop: url
        for shop_record in shop_records
        for shop, url in shop_record.items()
        if url
    }


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


def _fetch_profile_status(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=(5, 12),
            allow_redirects=True,
            stream=True,
        )
        try:
            return response.status_code
        finally:
            response.close()
    except requests.RequestException as error:
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


def remove_unavailable_profiles_from_html():
    """掲載終了プロフィールを一覧HTMLと再構成HTMLの両方から削除する。"""
    with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
        raw_soup = BeautifulSoup(source.read(), "html.parser")
    with RECONSTRUCTED_HTML_PATH.open("r", encoding="utf-8") as source:
        reconstructed_soup = BeautifulSoup(source.read(), "html.parser")

    raw_rows = raw_soup.select("tbody tr")
    cards = reconstructed_soup.select(".card-container > .card")
    urls = set()
    for row in raw_rows:
        profile_link = profile_link_in_row(row)
        if profile_link:
            urls.add(profile_link["href"])
    for card in cards:
        heading = card.find("h3")
        profile_link = heading.find("a", href=True) if heading else None
        if profile_link:
            urls.add(profile_link["href"])

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

    removed_cards = 0
    for card in cards:
        heading = card.find("h3")
        profile_link = heading.find("a", href=True) if heading else None
        if profile_link and profile_link["href"] in unavailable_urls:
            card.decompose()
            removed_cards += 1

    if removed_rows:
        RAW_HTML_PATH.write_text(str(raw_soup), encoding="utf-8")
    if removed_cards:
        RECONSTRUCTED_HTML_PATH.write_text(str(reconstructed_soup), encoding="utf-8")

    print(
        f"掲載終了プロフィールを削除: URL{len(unavailable_urls)}件、"
        f"一覧行{removed_rows}件、再構成カード{removed_cards}件"
    )
    return removed_rows, removed_cards


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

    return {
        "shop": shop_name,
        "url": article_url,
        "girl_url": girl_url,
        "name": girl_name,
        "gap": gap or "",
        "star": stars,
    }


def scrape_new_records(session, shop_urls, known_article_urls):
    new_records = []
    for shop_name, history_url in shop_urls.items():
        try:
            article_urls = fetch_article_urls(session, history_url)
        except requests.RequestException as error:
            print(f"店舗履歴を取得できません: {shop_name}: {error}")
            continue

        pending = sorted(article_urls - known_article_urls)
        print(f"{shop_name}: 新規候補 {len(pending)}件")
        for article_url in pending:
            try:
                record = fetch_article_record(session, shop_name, article_url)
                if record:
                    new_records.append(record)
                    known_article_urls.add(article_url)
            except requests.RequestException as error:
                print(f"記事を取得できません: {article_url}: {error}")
            time.sleep(0.5)
        time.sleep(0.5)
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


def render_reconstructed_html(records, session):
    with RECONSTRUCTED_HTML_PATH.open("r", encoding="utf-8") as source:
        soup = BeautifulSoup(source.read(), "html.parser")
    container = soup.find("div", class_="card-container")
    if container is None:
        raise ValueError(f"再構成HTMLにcard-containerがありません: {RECONSTRUCTED_HTML_PATH}")

    shop_filter = soup.find("select", id="shopFilter")
    if shop_filter is not None:
        shops = {
            card.get("data-shop", "")
            for card in container.find_all("div", class_="card", recursive=False)
        }
        shops.update(record["shop"] for record in records)
        shop_filter.clear()
        all_shops = soup.new_tag("option", value="")
        all_shops.string = "全ショップ"
        shop_filter.append(all_shops)
        for shop in sorted(shops):
            option = soup.new_tag("option", value=shop)
            option.string = shop
            shop_filter.append(option)

    seen = {
        (card.get("data-shop"), (card.find("h3").find("a").get("href") if card.find("h3") and card.find("h3").find("a") else None), card.find("summary").get_text(" ", strip=True) if card.find("summary") else "")
        for card in container.find_all("div", class_="card", recursive=False)
    }
    added = 0
    for record in records:
        detail = extract_report_detail(session, record["url"]) or record["gap"]
        card = soup.new_tag("div", attrs={"class": "card", "data-maxstar": str(record["star"].count("★")), "data-shop": record["shop"]})
        heading = soup.new_tag("h3")
        heading.append(record["shop"] + " - ")
        if record["girl_url"]:
            profile_link = soup.new_tag("a", href=record["girl_url"], target="_blank")
            profile_link.string = record["name"]
            heading.append(profile_link)
        else:
            heading.append(record["name"])
        card.append(heading)

        details = soup.new_tag("details")
        summary = soup.new_tag("summary")
        summary.append(f"評価 {record['star']} → ")
        report_link = soup.new_tag("a", href=record["url"], target="_blank")
        report_link.string = "記事リンク"
        summary.append(report_link)
        details.append(summary)
        pre = soup.new_tag("pre")
        pre.string = detail
        details.append(pre)
        card.append(details)

        summary_text = summary.get_text(" ", strip=True)
        key = (record["shop"], record["girl_url"], summary_text)
        if key in seen:
            continue
        container.append(card)
        seen.add(key)
        added += 1

    if added:
        RECONSTRUCTED_HTML_PATH.write_text(str(soup), encoding="utf-8")
    return added


def refresh_okinilove_data():
    remove_unavailable_profiles_from_html()
    shop_urls = load_shop_urls()
    with requests.Session() as session:
        with RAW_HTML_PATH.open("r", encoding="utf-8") as source:
            current_soup = BeautifulSoup(source.read(), "html.parser")
        known_urls = existing_article_urls(current_soup)
        new_records = scrape_new_records(session, shop_urls, known_urls)
        raw_added = append_records_to_raw_html(new_records)
        reconstructed_added = render_reconstructed_html(new_records, session) if new_records else 0
        source_dir = str(ROOT_DIR / "src")
        if source_dir not in sys.path:
            sys.path.insert(0, source_dir)
        from okinilove.render_people_summary import render_people_summary_file

        people_count = render_people_summary_file(RAW_HTML_PATH)

    print(
        f"Okinilove更新: 取得{len(new_records)}件、一覧追加{raw_added}件、"
        f"再構成追加{reconstructed_added}件、人物別表示{people_count}人"
    )
    return raw_added, reconstructed_added


if __name__ == "__main__":
    refresh_okinilove_data()
