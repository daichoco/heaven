import os
import re
import time
from datetime import datetime
from pathlib import Path

import chromedriver_autoinstaller
import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from wait_for_name_parts import wait_for_name_parts

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_CSV = ROOT_DIR / "data" / "results_2025-11-09_webdriver1.csv"
BASE_URL = "https://m.fuzoku-tokai.com/"
MAX_LISTING_PAGES = 75
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}
ARTICLE_URL_RE = re.compile(
    r"^https://(?:m\.)?fuzoku-tokai\.com/202\d+/\d{2}/\d{2}/post-\d+/?$"
)
REPEAT_RE = re.compile(r"返す（リピートする）|返さない（リピートしない）")
CSV_COLUMNS = [
    "delete?",
    "date",
    "type",
    "shop_name",
    "name",
    "predict_age",
    "face_score",
    "face_feature",
    "cup",
    "repeat",
    "girl_url",
    "url",
]


def extract_article_urls(soup):
    """記事一覧から本文記事URLだけを抽出する。"""
    urls = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not ARTICLE_URL_RE.match(href) or "#comment" in href:
            continue

        sidebar_heading = anchor.find_parent("ul")
        if sidebar_heading:
            sidebar_heading = sidebar_heading.find_previous_sibling(
                "h3", class_="widget_title sidebar_widget_title"
            )
        if sidebar_heading:
            continue
        urls.add(href)
    return urls


def get_listing_page_count(session, max_pages):
    response = session.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    last_page = 1
    pagination = soup.find("ul", class_="pagination")
    if pagination:
        last_link = pagination.find("li", class_="last")
        anchor = last_link.find("a", href=True) if last_link else None
        match = re.search(r"/page/(\d+)/", anchor["href"]) if anchor else None
        if match:
            last_page = int(match.group(1))
    return min(last_page, max_pages)


def collect_article_urls(session, max_pages):
    page_count = get_listing_page_count(session, max_pages)
    article_urls = set()

    for page_number in range(1, page_count + 1):
        page_url = BASE_URL if page_number == 1 else f"{BASE_URL}page/{page_number}/"
        response = session.get(page_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        page_urls = extract_article_urls(BeautifulSoup(response.text, "lxml"))
        article_urls.update(page_urls)
        print(f"一覧 {page_number}/{page_count}: {len(page_urls)}件、累計{len(article_urls)}件")
        time.sleep(1)

    return article_urls


def text_after_strong(soup, label):
    tag = soup.find("strong", string=lambda value: value and label in value)
    if not tag or tag.next_sibling is None:
        return None
    sibling = tag.next_sibling
    return sibling.strip() if isinstance(sibling, str) else sibling.get_text(strip=True)


def parse_report_page(soup, report_url):
    repeat_tag = next(
        (tag for tag in soup.find_all("p") if REPEAT_RE.search(tag.get_text(" ", strip=True))),
        None,
    )
    if repeat_tag is None:
        return None

    repeat = repeat_tag.get_text(strip=True)
    if "返さない" in repeat:
        return None

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
        "date": report_date,
        "type": shop_type,
        "shop_name": shop_name,
        "name": name,
        "predict_age": text_after_strong(soup, "姫の推定年齢"),
        "face_score": face_score,
        "face_feature": face_comment.get_text() if face_comment else None,
        "cup": text_after_strong(soup, "姫の胸（推定カップ）"),
        "repeat": repeat,
        "girl_url": girl_url,
        "url": report_url,
    }


def build_driver():
    driver_path = chromedriver_autoinstaller.install()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(service=Service(driver_path), options=options)


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


def load_existing_data():
    if not OUTPUT_CSV.exists():
        return pd.DataFrame(columns=CSV_COLUMNS)
    return pd.read_csv(OUTPUT_CSV, encoding="utf-8-sig")


def append_new_data(existing, records):
    if not records:
        print("追加対象の記事はありません")
        return existing

    combined = pd.concat([existing, pd.DataFrame(records)], ignore_index=True)
    combined = combined.drop_duplicates(subset=["shop_name", "name"], keep="first")
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OUTPUT_CSV.with_suffix(".csv.tmp")
    combined.to_csv(temp_path, index=False, encoding="utf-8-sig")
    temp_path.replace(OUTPUT_CSV)
    print(f"CSVを更新しました: {OUTPUT_CSV} ({len(records)}件追加)")
    return combined


def main():
    password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
    if not password:
        raise RuntimeError("FUZOKU_TOKAI_PASSWORD 環境変数を設定してください")

    max_pages = int(os.environ.get("FUZOKU_TOKAI_MAX_PAGES", MAX_LISTING_PAGES))
    if max_pages < 1:
        raise ValueError("FUZOKU_TOKAI_MAX_PAGES は1以上を指定してください")

    existing = load_existing_data()
    existing_urls = set(existing.get("url", pd.Series(dtype=str)).dropna())

    with requests.Session() as session:
        login(session, password)
        article_urls = collect_article_urls(session, max_pages)
        pending_urls = sorted(article_urls - existing_urls)
        print(f"新規確認対象: {len(pending_urls)}件")
        if not pending_urls:
            return

        driver = build_driver()
        records = []
        known_girls = set(
            zip(existing.get("shop_name", []), existing.get("name", []))
        )
        try:
            for index, report_url in enumerate(pending_urls, start=1):
                print(f"記事 {index}/{len(pending_urls)}: {report_url}")
                try:
                    response = session.get(report_url, headers=HEADERS, timeout=30)
                    if response.status_code != 200:
                        print(f"記事取得失敗: HTTP {response.status_code}")
                        continue

                    record = parse_report_page(
                        BeautifulSoup(response.text, "lxml"), report_url
                    )
                    if record is None:
                        continue
                    girl_key = (record["shop_name"], record["name"])
                    if girl_key in known_girls:
                        continue

                    profile_response = session.get(
                        record["girl_url"], headers=HEADERS, timeout=15
                    )
                    if profile_response.status_code in {404, 410} or profile_response.url != record["girl_url"]:
                        print(f"プロフィールが公開されていないため除外: {record['girl_url']}")
                        continue
                    profile_response.raise_for_status()

                    driver.get(record["girl_url"])
                    wait_for_name_parts(driver, record["name"])
                    records.append(record)
                    known_girls.add(girl_key)
                except (requests.RequestException, WebDriverException, ValueError) as exc:
                    print(f"取得失敗: {report_url} ({exc})")
                time.sleep(1)
        finally:
            driver.quit()

    append_new_data(existing, records)


if __name__ == "__main__":
    main()
