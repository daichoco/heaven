from pathlib import Path
import sys

import pandas as pd
from bs4 import BeautifulSoup

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl, write_jsonl_atomic
from okinilove.scrape_okinilove import find_unavailable_profile_urls, refresh_okinilove_data

ROOT_DIR = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT_DIR / "docs"
DATA_DIR = ROOT_DIR / "data"
OKINILOVE_JSONL = DATA_DIR / "okinilove_reviews.jsonl"
OKINILOVE_COLUMNS = [
    "shop_name",
    "girl_name",
    "girl_url",
    "report_url",
    "evaluations",
    "report_text",
    "cityheaven_url",
]


def extract_okinilove_data():
    html_path = DOCS_DIR / "okinilove_reconstructed.html"
    with html_path.open("r", encoding="utf-8") as html_file:
        soup = BeautifulSoup(html_file.read(), "html.parser")

    container = soup.find("div", class_="card-container")
    reports = []
    if container:
        for card in container.find_all("div", class_="card"):
            heading = card.find("h3")
            name_tag = heading.find("a") if heading else None
            detail = card.find("details")
            summary = detail.find("summary") if detail else None
            report_link = summary.find("a") if summary else None
            report_text = detail.find("pre") if detail else None
            girl_url = name_tag.get("href") if name_tag else None

            reports.append(
                {
                    "shop_name": heading.get_text(" ", strip=True).split("-", 1)[0].strip()
                    if heading
                    else None,
                    "girl_name": name_tag.get_text(strip=True) if name_tag else None,
                    "girl_url": girl_url,
                    "report_url": report_link.get("href") if report_link else None,
                    "evaluations": summary.get_text(" ", strip=True) if summary else None,
                    "report_text": report_text.get_text(strip=True) if report_text else None,
                    "cityheaven_url": girl_url
                    if girl_url and "cityheaven.net" in girl_url
                    else None,
                }
            )

    return pd.DataFrame(reports, columns=OKINILOVE_COLUMNS)


def remove_unavailable_profile_rows(data, now=None):
    """404/410 のプロフィールURLを持つ行だけデータから除外する。"""
    if "girl_url" not in data.columns or data.empty:
        return data.copy(), 0

    unavailable_urls = find_unavailable_profile_urls(
        data["girl_url"].dropna().astype(str).unique(),
        now=now,
    )
    if not unavailable_urls:
        return data.copy(), 0

    keep = ~data["girl_url"].astype("string").isin(unavailable_urls)
    removed = int((~keep).sum())
    return data.loc[keep].copy().reset_index(drop=True), removed


def merge_okinilove_data(
    calendar_data,
    *,
    normalize_url,
    build_driver,
    enrich_with_cityheaven_urls,
):
    """オキニラブ新規データを保存し、出勤データへ右結合する。"""
    refresh_okinilove_data()

    if not OKINILOVE_JSONL.exists():
        raise FileNotFoundError(f"オキニラブ既存JSONLがありません: {OKINILOVE_JSONL}")

    existing = read_jsonl(OKINILOVE_JSONL, columns=OKINILOVE_COLUMNS)
    existing, removed_count = remove_unavailable_profile_rows(existing)
    if removed_count:
        write_jsonl_atomic(existing, OKINILOVE_JSONL, columns=OKINILOVE_COLUMNS)
        print(f"公開終了プロフィールのデータを削除: {removed_count}件")

    new_data = extract_okinilove_data()
    new_data, stale_new_count = remove_unavailable_profile_rows(new_data)
    if stale_new_count:
        print(f"公開終了プロフィールの新規候補を除外: {stale_new_count}件")

    key_columns = ["shop_name", "girl_name"]
    existing_keys = existing[key_columns].drop_duplicates()

    new_data = new_data.merge(
        existing_keys,
        on=key_columns,
        how="left",
        indicator=True,
    )
    new_data = new_data.loc[new_data["_merge"] == "left_only"].drop(columns="_merge")

    if not new_data.empty:
        driver = build_driver()
        try:
            new_data = enrich_with_cityheaven_urls(new_data, driver, source="yahoo")
        finally:
            driver.quit()

        existing = pd.concat([existing, new_data], ignore_index=True)
        existing = existing.drop_duplicates(subset=key_columns, keep="first")
        write_jsonl_atomic(existing, OKINILOVE_JSONL, columns=OKINILOVE_COLUMNS)

    existing["url_key"] = existing["cityheaven_url"].apply(normalize_url)
    return pd.merge(
        existing,
        calendar_data,
        how="right",
        on="url_key",
        suffixes=("_okinilove", "_merged"),
    )
