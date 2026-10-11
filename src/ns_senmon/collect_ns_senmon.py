import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
for path in (SRC_DIR, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from data_store import read_jsonl, write_jsonl_atomic
from ns_senmon_login import BASE_URL, TIMEOUT_SECONDS, create_logged_in_session

OUTPUT_JSONL = ROOT_DIR / "data" / "ns_senmon_reports.jsonl"
LISTING_URL = f"{BASE_URL}/?cat=20"
MAX_LISTING_PAGES = 200
REQUEST_INTERVAL_SECONDS = 1.0
DATA_COLUMNS = [
    "date",
    "post_id",
    "shop_name",
    "name",
    "title",
    "url",
    "details",
]
POST_ID_RE = re.compile(r"[?&]p=(\d+)")
FIELD_RE = re.compile(r"^●(.+?)[:：]\s*$")


def configure_retries(session):
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


def parse_listing_page(soup):
    """一覧ページから (記事URL, 投稿日, タイトル) を返す。"""
    items = []
    for card in soup.select("a.entry-card-wrap"):
        href = card.get("href", "")
        match = POST_ID_RE.search(href)
        date_tag = card.select_one(".entry-date")
        if not match or not date_tag:
            continue
        title_tag = card.select_one(".entry-card-title")
        items.append(
            {
                "post_id": int(match.group(1)),
                "url": href,
                "date": date_tag.get_text(strip=True).replace(".", "-"),
                "title": title_tag.get_text(strip=True) if title_tag else "",
            }
        )
    return items


def collect_listing_items(session, max_pages, latest_date=None, known_urls=frozenset()):
    """最新日付以降の記事だけを集める。一覧は新しい順なので、古い記事だけのページで止める。"""
    items = []
    for page in range(1, max_pages + 1):
        url = LISTING_URL if page == 1 else f"{LISTING_URL}&paged={page}"
        response = session.get(url, timeout=TIMEOUT_SECONDS)
        if response.status_code == 404:
            break
        response.raise_for_status()
        page_items = parse_listing_page(BeautifulSoup(response.text, "lxml"))
        if not page_items:
            break

        # 同日の別記事を取りこぼさないよう、最新日付と同じ日付も対象にする
        fresh = [
            item
            for item in page_items
            if latest_date is None or item["date"] >= latest_date
        ]
        items.extend(item for item in fresh if item["url"] not in known_urls)
        print(f"一覧 {page}: {len(page_items)}件中 対象{len(fresh)}件、累計{len(items)}件")
        if latest_date is not None and not fresh:
            break
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return items


def parse_details(content):
    """本文の `●項目:` 形式を {項目: 値} にする。末尾の「→」以降（閲覧期限など）は除く。"""
    details = {}
    label = None
    values = []
    for line in content.get_text("\n", strip=True).splitlines():
        if line.startswith("→"):
            break
        match = FIELD_RE.match(line)
        if match:
            if label is not None:
                details[label] = "\n".join(values)
            label, values = match.group(1).strip(), []
        elif label is not None:
            values.append(line)
    if label is not None:
        details[label] = "\n".join(values)
    return details


def parse_breadcrumb(soup):
    texts = [
        item.get_text(strip=True)
        for item in soup.select(".breadcrumb [itemprop='name'], .breadcrumb a")
    ]
    names = []
    for text in texts:
        if text and text not in names:
            names.append(text)
    # ホーム / エリア / 店名 / 姫名 の順
    shop = names[-2].lstrip("●") if len(names) >= 2 else ""
    girl = names[-1].lstrip("･・") if names else ""
    return shop, girl


def parse_report_page(soup, item):
    content = soup.select_one(".entry-content")
    if content is None:
        return None
    details = parse_details(content)
    if not details:
        return None
    shop, girl = parse_breadcrumb(soup)
    return {**item, "shop_name": shop, "name": girl, "details": details}


def load_existing_data():
    if not OUTPUT_JSONL.exists():
        return pd.DataFrame(columns=DATA_COLUMNS)
    return read_jsonl(OUTPUT_JSONL, columns=DATA_COLUMNS)


def append_new_data(existing, records):
    if not records:
        print("追加対象の記事はありません")
        return existing
    combined = pd.concat([existing, pd.DataFrame(records)], ignore_index=True)
    combined = combined.drop_duplicates(subset=["url"], keep="first")
    combined = combined.sort_values(["date", "post_id"], ascending=False)
    write_jsonl_atomic(combined, OUTPUT_JSONL, columns=DATA_COLUMNS)
    print(f"JSONLを更新しました: {OUTPUT_JSONL} ({len(records)}件追加)")
    return combined


def main():
    existing = load_existing_data()
    latest_date = None
    full_mode = os.environ.get("NS_SENMON_FULL") == "1"
    if full_mode:
        print("全件モード: 日付の起点を使わず、未取得の記事をすべて取得します")
    elif not existing.empty:
        latest_date = str(existing["date"].astype(str).max())[:10]
        print(f"既存データの最新日付: {latest_date}")
    known_urls = frozenset(existing["url"].dropna())

    session = create_logged_in_session()
    configure_retries(session)
    max_pages = int(os.environ.get("NS_SENMON_MAX_PAGES", MAX_LISTING_PAGES))
    print(f"一覧の最大ページ数: {max_pages}")

    items = collect_listing_items(session, max_pages, latest_date, known_urls)
    print(f"取得対象の記事: {len(items)}件")

    records = []
    try:
        for index, item in enumerate(items, start=1):
            print(f"記事 {index}/{len(items)}: {item['url']}")
            try:
                response = session.get(item["url"], timeout=TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"取得に失敗したためスキップ: {type(exc).__name__}")
                continue
            record = parse_report_page(BeautifulSoup(response.text, "lxml"), item)
            if record is None:
                print("レポート本文ではないためスキップ（移籍・改名のお知らせ等）")
                continue
            records.append(record)
            time.sleep(REQUEST_INTERVAL_SECONDS)
    finally:
        append_new_data(existing, records)


if __name__ == "__main__":
    main()
