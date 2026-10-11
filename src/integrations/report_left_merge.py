#%%
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from ddgs import DDGS
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
  sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl, write_jsonl_atomic
from okinilove.okinilove_left_merge import merge_okinilove_data
from cityheaven.exists_merge import (
  PROFILE_DATA_PATH,
  main as scrape_cityheaven_profiles,
)

DOCS_DIR = ROOT_DIR / "docs"
DATA_DIR = ROOT_DIR / "data"
REPORT_HISTORY_PATH = DATA_DIR / "tokai_fuzoku_report_history.jsonl"
REPORT_HISTORY_COLUMNS = [
  "shop_name",
  "shop_type",
  "girl_name",
  "girl_url",
  "age",
  "report_url",
  "evaluations",
  "cityheaven_url",
]


def load_html(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def normalize_url(url):
    if pd.isna(url) or not str(url).strip():
        return None

    normalized = str(url).split("?")[0].rstrip("/")
    match = re.search(r"(https://www\.cityheaven\.net/.+?/girlid-\d+)", normalized)
    return match.group(1) if match else normalized


def human_delay():
    """人間っぽい待機を入れる"""
    delay = random.uniform(2, 7)
    if random.random() < 0.1:
        delay += random.uniform(10, 20)
    time.sleep(delay)


def build_driver():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/121.0",
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) Chrome/119.0 Mobile Safari/537.36",
    ]
    options = Options()
    options.add_argument(f"user-agent={random.choice(user_agents)}")
    return webdriver.Chrome(options=options)


def search_cityheaven_url(driver, shop_name, girl_name, source="yahoo", max_results=5):
    query = f"{shop_name} {girl_name} site:cityheaven.net"

    if source == "duckduckgo":
        with DDGS() as ddgs:
            results = ddgs.text(
                query,
                region="jp-jp",
                safesearch="off",
                timelimit=None,
                max_results=max_results,
            )
            for r in results:
                url = r["href"]
                if "cityheaven.net" in url and "girlid" in url and "attend" not in url:
                    print(f"[DDG] {shop_name} {girl_name}\n{url}")
                    return url
    else:
        try:
            driver.get(f"https://search.yahoo.co.jp/search?p={query}")
            human_delay()

            if random.random() < 0.2:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(2, 5))

            results = driver.find_elements(By.CSS_SELECTOR, "a.sw-Card__titleInner")
            for r in results:
                url = r.get_attribute("href")
                if url and "cityheaven.net" in url and "girlid" in url and "attend" not in url:
                    print(f"[Yahoo] {shop_name} {girl_name}\n{url}")
                    return url
        except Exception as e:
            print(f"[Yahoo] 検索失敗: {shop_name} {girl_name} → {e}")
    return None


def enrich_with_cityheaven_urls(df, driver, source="yahoo"):
    missing = df["cityheaven_url"].isna()
    for idx, row in df[missing].iterrows():
        url = search_cityheaven_url(driver, row["shop_name"], row["girl_name"], source=source)
        df.loc[idx, "cityheaven_url"] = url
        human_delay()
    return df


def report_identity_key(row):
    # cityheaven_url は検索で後から補完されるため、新規判定のキーには使わない
    for column in ("girl_url", "report_url"):
        key = normalize_url(row.get(column))
        if key:
            return key
    shop = str(row.get("shop_name", "")).strip().casefold()
    name = str(row.get("girl_name", "")).strip().casefold()
    return f"{shop}::{name}" if shop or name else None


def load_report_history(path=REPORT_HISTORY_PATH):
    if not path.exists():
        raise FileNotFoundError(f"レポート履歴JSONLがありません: {path}")
    return read_jsonl(path, columns=REPORT_HISTORY_COLUMNS)


def save_report_history(history, path=REPORT_HISTORY_PATH):
    write_jsonl_atomic(history, path, columns=REPORT_HISTORY_COLUMNS)


def append_new_reports(history, current_reports):
    history_keys = {
        key for key in history.apply(report_identity_key, axis=1) if key
    }
    current_keys = current_reports.apply(report_identity_key, axis=1)
    new_reports = current_reports.loc[
        current_keys.notna() & ~current_keys.isin(history_keys)
    ].copy()
    if new_reports.empty:
        return history, 0

    driver = build_driver()
    try:
        new_reports = enrich_with_cityheaven_urls(new_reports, driver, source="yahoo")
    finally:
        driver.quit()

    updated_history = pd.concat(
        [history, new_reports.loc[:, REPORT_HISTORY_COLUMNS]],
        ignore_index=True,
    )
    return updated_history, len(new_reports)


def _has_value(value):
    if isinstance(value, dict):
        return bool(value)
    return bool(pd.notna(value))


def _evaluation_dict(value):
    if isinstance(value, dict):
        return value
    return ast.literal_eval(value) if isinstance(value, str) else {}


def extract_report_final_df():
    html = load_html(DOCS_DIR / "report_final.html")
    soup = BeautifulSoup(html, "html.parser")
    reports = []

    for detail in soup.find_all("details"):
        if detail.find_parent("details"):
            continue

        summary = detail.find("summary")
        if not summary:
            continue

        shop_text = summary.contents[0].strip() if summary.contents else None
        name_tag = summary.find("a")
        girl_name = name_tag.get_text(strip=True) if name_tag else None
        girl_url_tag = summary.find("a", string=girl_name)
        girl_url = girl_url_tag["href"] if girl_url_tag else None
        report_link_tag = summary.find("a", string="レポート")
        report_link = report_link_tag["href"] if report_link_tag else None

        age_match = re.search(r"(\d{2})才", summary.get_text())
        age = int(age_match.group(1)) if age_match else None

        evaluations = {}
        score_items = detail.select(".score-item")
        for score_item in score_items:
          label_tag = score_item.select_one(".score-label")
          value_tag = score_item.select_one(".report-comment p")
          key = label_tag.get_text(strip=True) if label_tag else None
          value = value_tag.get_text(strip=True) if value_tag else None
          if key:
            evaluations[key] = value

        if not score_items:
          inner_details = detail.find("div")
        else:
          inner_details = None
        if inner_details:
            for sub_detail in inner_details.find_all("details", recursive=False):
                sub_summary = sub_detail.find("summary")
                key = sub_summary.get_text(strip=True) if sub_summary else None
                value_tag = sub_detail.find("p")
                value = value_tag.get_text(strip=True) if value_tag else None
                if key:
                    evaluations[key] = value

        match = re.search(r"\((.*?)\)", shop_text or "")
        if match:
            shop_info = match.group(1)
            parts = re.split(r"[･・]", shop_info)
            shop_name = parts[0].strip() if len(parts) > 0 else None
            shop_type = parts[1].strip() if len(parts) > 1 else None
        else:
            shop_name = None
            shop_type = None

        reports.append(
            {
                "shop_name": shop_name,
                "shop_type": shop_type,
                "girl_name": girl_name,
                "girl_url": girl_url,
                "age": age,
                "report_url": report_link,
                "evaluations": evaluations,
            }
        )

    df = pd.DataFrame(reports)
    df.loc[df["girl_url"].str.contains("cityheaven.net", na=False), "cityheaven_url"] = df["girl_url"]
    return df


def extract_cityheaven_profiles_df():
    payload = json.loads(PROFILE_DATA_PATH.read_text(encoding="utf-8"))
    return pd.DataFrame(payload["records"], columns=payload["columns"])


def main():
    asyncio.run(scrape_cityheaven_profiles())
    try:
        df1_current = extract_report_final_df()
        df2 = extract_cityheaven_profiles_df()
    finally:
        PROFILE_DATA_PATH.unlink(missing_ok=True)

    df1 = load_report_history()
    df2["url_key"] = df2["URL"].apply(normalize_url)
    df1, added_count = append_new_reports(df1, df1_current)
    save_report_history(df1)
    if added_count:
      print(f"レポート履歴に新規追加: {added_count}件")

    df1["url_key"] = df1["cityheaven_url"].apply(normalize_url)

    merged = pd.merge(df1, df2, how="right", on="url_key", suffixes=("_df1", "_df2"))
    return merge_okinilove_data(
      merged,
      normalize_url=normalize_url,
      build_driver=build_driver,
      enrich_with_cityheaven_urls=enrich_with_cityheaven_urls,
    )


if __name__ == "__main__":
    merged = main()


# %%
import pandas as pd
import ast
import re
from bs4 import BeautifulSoup
from datetime import datetime

# merged は DataFrame と仮定
# 出勤日カラムを抽出
calendar_cols = [col for col in merged.columns if re.match(r"\d{1,2}/\d{1,2}", col)]

# 日本語の曜日リスト
weekdays = ["月", "火", "水", "木", "金", "土", "日"]

# 今日の日付を取得
today = datetime.now()
today_label = f"{today.month}/{str(today.day).zfill(2)}({weekdays[today.weekday()]})"

# カレンダー列を翌年考慮でソート
def parse_date_with_year_adjustment(date_str, current_year, today_month):
    # date_str は "1/1(月)" の形式
    date_part = date_str.split('(')[0]  # "1/1"
    month, day = map(int, date_part.split('/'))
    year = current_year
    if month < today_month:  # 翌年を考慮
        year += 1
    return datetime(year, month, day)

current_year = today.year
calendar_cols = sorted(
    calendar_cols,
    key=lambda x: parse_date_with_year_adjustment(x, current_year, today.month)
)

# ★ 店名からユニーク地域抽出 ★
yyy_set = set()
for shop in merged["店名"]:
    matches = re.findall(r"\(([^()]*?/[^()]*)\)", str(shop))
    for inside in matches:
        yyy = inside.split("/")[0]
        yyy_set.add(yyy)
yyy_list = sorted(yyy_set)

# HTML組み立て開始
html = """<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>出勤カレンダー一覧</title>
    <style>
      * { box-sizing: border-box; }
      body { max-width: 1400px; margin: 0 auto; padding: 16px; font: 15px/1.5 sans-serif; color: #20272d; }
      h1 { margin: 0 0 16px; font-size: 1.4rem; }
      thead th {
        position: sticky;
        top: 0;
        background: #f9f9f9;
        z-index: 1;
      }
      th.saturday { color: blue; }
      th.sunday { color: red; }
      body > div[style*="overflow-x: auto"] { max-height: calc(100vh - 220px); overflow: auto; border: 1px solid #ccd2d8; background: white; }
      table { border-collapse: collapse; width: 100%; min-width: 900px; font-size: 14px; }
      th, td { border: 1px solid #d7dde1; padding: 8px; text-align: center; vertical-align: top; }
      th:first-child { position: sticky; left: 0; z-index: 2; min-width: 220px; background: #f9f9f9; }
      td.shop-cell { min-width: 220px; text-align: left; }
      img { width: 64px; height: 64px; object-fit: cover; border-radius: 4px; }
      details { text-align: left; margin-top: 5px; }
      summary { cursor: pointer; font-weight: bold; }
      .filter-group { margin-bottom: 14px; line-height: 1.8; }
      .filter-group label { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; }
      input { min-height: 22px; }
      .shop-cell button {
        min-height: 40px;
        padding: 8px 12px;
        border-radius: 4px;
        background-color: #f0f0f0;
        border: 1px solid #ccc;
        cursor: pointer;
        width: 100%;
      }
      .shop-cell button:active {
        background-color: #e0e0e0;
      }
      @media (max-width: 700px) {
        body { padding: 12px; }
        h1 { font-size: 1.2rem; }
        .filter-group { margin-bottom: 16px; }
        .filter-group label { min-height: 36px; margin-right: 8px; }
        input[type="radio"], input[type="checkbox"] { width: 18px; height: 18px; }
        input[type="date"] { min-height: 42px; }
        body > div[style*="overflow-x: auto"] { max-height: calc(100vh - 260px); overflow: auto; }
        body > div[style*="overflow-x: auto"] table, body > div[style*="overflow-x: auto"] tbody { display: block; width: 100%; min-width: 0; }
        body > div[style*="overflow-x: auto"] thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
        body > div[style*="overflow-x: auto"] tbody tr:not(.review-row) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 8px; margin: 0 0 10px; padding: 10px; border: 1px solid #ccd2d8; background: white; }
        body > div[style*="overflow-x: auto"] td.shop-cell { grid-column: 1 / -1; min-width: 0; padding: 4px 0 8px; border: 0; }
        body > div[style*="overflow-x: auto"] td:nth-child(2) { display: none; }
        body > div[style*="overflow-x: auto"] td[data-label] { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: 6px; border: 1px solid #e0e4e7; text-align: left; overflow-wrap: anywhere; }
        body > div[style*="overflow-x: auto"] td[data-label]::before { content: attr(data-label); color: #58636d; font-size: 0.8rem; font-weight: 600; }
        body > div[style*="overflow-x: auto"] .review-row td { display: block; width: 100%; }
        body > div[style*="overflow-x: auto"] .review-row pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
      }
    </style>
    <script>
      function applyCombinedFilter() {
        resetAllReviews();
        const reportSelected = document.querySelector('input[name="reportFilter"]:checked').value;
        const dateSelected = document.querySelector('input[name="filter"]:checked').value;
        const inputDate = document.getElementById("dateInput").value;
        const today = new Date();
        const weekdays = ["日", "月", "火", "水", "木", "金", "土"];
        const mm = today.getMonth() + 1;
        const dd = today.getDate();
        const dayOfWeek = weekdays[today.getDay()];
        const todayLabel = `${mm}/${dd}(${dayOfWeek})`;

        const rows = document.querySelectorAll('tbody tr:not(.review-row)');

        rows.forEach(row => {
          let showByDate = false;
          let showByReport = false;
          let showByPlace = false;

          // 日付フィルタ
          if (dateSelected === "all") {
            showByDate = true;
          } else if (dateSelected === "today") {
            const cell = row.querySelector(`[data-label="${todayLabel}"]`);
            const text = cell ? cell.textContent.trim() : "";
            showByDate = /^\\d{1,2}:\\d{2}\\s*~\\s*\\d{1,2}:\\d{2}$/.test(text);
          } else if (dateSelected === "weekend") {
            row.querySelectorAll('td[data-label]').forEach(cell => {
              const label = cell.getAttribute('data-label');
              if (label && (label.includes("(土)") || label.includes("(日)"))) {
                const text = cell.textContent.trim();
                if (/^\\d{1,2}:\\d{2}\\s*~\\s*\\d{1,2}:\\d{2}$/.test(text)) {
                  showByDate = true;
                }
              }
            });
          } else if (dateSelected === "date") {
            if (inputDate) {
              const date = new Date(inputDate);
              const label = `${date.getMonth() + 1}/${date.getDate()}(${weekdays[date.getDay()]})`;
              row.querySelectorAll('td[data-label]').forEach(cell => {
                const cellLabel = cell.getAttribute('data-label');
                if (cellLabel === label) {
                  const text = cell.textContent.trim();
                  if (/^\\d{1,2}:\\d{2}\\s*~\\s*\\d{1,2}:\\d{2}$/.test(text)) {
                    showByDate = true;
                  }
                }
              });
            } else {
              showByDate = true;
            }
          }

          // レポートフィルタ
          const hasReport = row.getAttribute("data-report");
          if (reportSelected === "all") {
            showByReport = true;
          } else if (reportSelected === "has") {
            showByReport = hasReport === "true";
          } else if (reportSelected === "none") {
            showByReport = hasReport === "false";
          }

          // 地域フィルタ
          const checkedPlaces = Array.from(document.querySelectorAll('input[name="placeFilter"]:checked'))
                                     .map(cb => cb.value);
          if (checkedPlaces.includes("all")) {
            showByPlace = true;
          } else {
            const shopCell = row.querySelector("td.shop-cell");
            const text = shopCell ? shopCell.textContent : "";
            showByPlace = checkedPlaces.some(val => text.includes("(" + val + "/"));
          }

          // AND条件で表示制御
          row.style.display = (showByDate && showByReport && showByPlace) ? "" : "none";
        });
        // --- 日付列の表示制御 ---
        const allHeaders = document.querySelectorAll("thead th");
        const allCells = document.querySelectorAll("tbody td[data-label]");

        // まず全て表示
        allHeaders.forEach(th => th.style.display = "");
        allCells.forEach(td => td.style.display = "");

        if (dateSelected === "today") {
          allHeaders.forEach(th => {
            const label = th.textContent.trim();
            if (label !== todayLabel && label !== "名前＋レビュー+店名" && label !== "画像") {
              th.style.display = "none";
            }
          });
          allCells.forEach(td => {
            const label = td.getAttribute("data-label");
            if (label !== todayLabel) td.style.display = "none";
          });
        }

        if (dateSelected === "date" && inputDate) {
          const date = new Date(inputDate);
          const label = `${date.getMonth() + 1}/${date.getDate()}(${weekdays[date.getDay()]})`;

          allHeaders.forEach(th => {
            const text = th.textContent.trim();
            if (text !== label && text !== "名前＋レビュー+店名" && text !== "画像") {
              th.style.display = "none";
            }
          });
          allCells.forEach(td => {
            const cellLabel = td.getAttribute("data-label");
            if (cellLabel !== label) td.style.display = "none";
          });
        }

        if (dateSelected === "weekend") {
          allHeaders.forEach(th => {
            const text = th.textContent.trim();
            if (!(text.includes("(土)") || text.includes("(日)") ||
                  text === "名前＋レビュー+店名" || text === "画像")) {
              th.style.display = "none";
            }
          });
          allCells.forEach(td => {
            const label = td.getAttribute("data-label");
            if (!(label.includes("(土)") || label.includes("(日)"))) {
              td.style.display = "none";
            }
          });
        }
      }

      function applyDateFilter() {
        document.querySelector('input[name="filter"][value="date"]').checked = true;
        applyCombinedFilter();
      }

      function toggleReviewRow(id) {
        const row = document.getElementById(id);
        if (row.style.display === "none") {
          row.style.display = window.matchMedia("(max-width: 700px)").matches ? "block" : "table-row";
        } else {
          row.style.display = "none";
        }
      }

      function resetAllReviews() {
        document.querySelectorAll('.review-row').forEach(row => {
          row.style.display = "none";
        });
      }
    </script>
  </head>
  <body onload="applyCombinedFilter()">
    <h1>出勤カレンダーとレビュー一覧</h1>

    <div class="filter-group">
      <strong>日付条件：</strong><br>
      <label><input type="radio" name="filter" value="all" checked onchange="applyCombinedFilter()"> 全表示</label>
      <label><input type="radio" name="filter" value="today" onchange="applyCombinedFilter()"> 本日出勤のみ</label>
      <label><input type="radio" name="filter" value="weekend" onchange="applyCombinedFilter()"> 土日出勤のみ</label>
      <label><input type="radio" name="filter" value="date" onchange="applyCombinedFilter()"> 日付指定</label>
      <label>日付で絞り込み: <input type="date" id="dateInput" onchange="applyDateFilter()"></label>
    </div>

    <div class="filter-group">
      <strong>レビュー条件：</strong><br>
      <label><input type="radio" name="reportFilter" value="all" checked onchange="applyCombinedFilter()"> 全表示</label>
      <label><input type="radio" name="reportFilter" value="has" onchange="applyCombinedFilter()"> レポートありのみ</label>
      <label><input type="radio" name="reportFilter" value="none" onchange="applyCombinedFilter()"> レポートなしのみ</label>
    </div>

    <div class="filter-group">
      <strong>地域条件：</strong><br>
      <label><input type="checkbox" name="placeFilter" value="all" checked onchange="applyCombinedFilter()"> 全地域</label><br>
"""

# 地域フィルタチェックボックス
for y in yyy_list:
    html += f"<label><input type='checkbox' name='placeFilter' value='{y}' onchange='applyCombinedFilter()'> {y}</label><br>"
html += "</div>"

# テーブル開始
html += "<div style='height: calc(100vh - 100px); overflow-y: auto; overflow-x: auto;'>"
html += "<table><thead><tr><th>名前＋レビュー+店名</th><th>画像</th>"

# 日付ヘッダー
for day in calendar_cols:
    if "(土)" in day:
        html += f"<th class='saturday'>{day}</th>"
    elif "(日)" in day:
        html += f"<th class='sunday'>{day}</th>"
    else:
        html += f"<th>{day}</th>"

html += "</tr></thead><tbody>"

for idx, row in merged.iterrows():
    name = row["名前"]
    shop = row["店名"]
    shop = [s.strip() for s in shop.split("\n") if s.strip()]
    image_url = row["画像URL"]
    profile_url = row["URL"]

    eval_merged = row["evaluations_merged"]
    eval_okinilove = row["evaluations_okinilove"]
    report_url_merged = row["report_url_merged"]
    report_url_okinilove = row["report_url_okinilove"]
    report_text_okinilove = row["report_text"]

    has_report = "true" if (
      _has_value(eval_merged)
      or _has_value(eval_okinilove)
      or _has_value(report_text_okinilove)
    ) else "false"
    row_id_merged = f"review-merged-{idx}"
    row_id_okinilove = f"review-okinilove-{idx}"

    # メイン行
    html += f"<tr data-report='{has_report}'>"

    # ★ 名前＋レビュー＋店名（統合セル）
    html += f"<td class='shop-cell'>"
    html += f"<a href='{profile_url}' target='_blank'>{name}</a>"

    # レビューボタン
    if _has_value(eval_merged):
        html += f"<br><button onclick=\"toggleReviewRow('{row_id_merged}')\">レビュー(merged)</button>"
    if _has_value(eval_okinilove) or _has_value(report_text_okinilove):
        html += f"<br><button onclick=\"toggleReviewRow('{row_id_okinilove}')\">レビュー(okinilove)</button>"

    # 店名（名前の下に表示）
    if len(shop) >= 1:
        html += f"<br><span class='shop-info'>{shop[0]}</span>"
    if len(shop) >= 2:
        html += f"<br><span class='shop-info'>{shop[1]}</span>"

    html += "</td>"

    # 画像セル
    html += f"<td><img src='{image_url}' alt='{name}'></td>"

    # 出勤日
    for day in calendar_cols:
        status = row[day] if pd.notna(row[day]) else "-"
        html += f"<td data-label='{day}'>{status}</td>"

    html += "</tr>"

    # mergedレビュー行
    if pd.notna(eval_merged):
        try:
            eval_dict = _evaluation_dict(eval_merged)
            html += f"<tr id='{row_id_merged}' class='review-row' style='display:none; background-color:#f9f9f9;'>"
            html += f"<td colspan='{2 + len(calendar_cols)}' style='text-align: left;'><ul>"
            for k, v in eval_dict.items():
                html += f"<li><strong>{k}:</strong> {v}</li>"
            if pd.notna(report_url_merged):
                html += f"<li><a href='{report_url_merged}' target='_blank'>レポートリンク (merged)</a></li>"
            html += "</ul></td></tr>"
        except Exception:
            html += f"<tr id='{row_id_merged}' class='review-row' style='display:none;'><td colspan='{2 + len(calendar_cols)}'>レビュー解析失敗 (merged)</td></tr>"

    # okiniloveレビュー行
    if pd.notna(eval_okinilove) or pd.notna(report_text_okinilove):
        html += f"<tr id='{row_id_okinilove}' class='review-row' style='display:none; background-color:#f0f9ff;'>"
        html += f"<td colspan='{2 + len(calendar_cols)}' style='text-align: left;'>"
        if _has_value(eval_okinilove):
          if isinstance(eval_okinilove, dict):
            html += "<ul>"
            for key, value in eval_okinilove.items():
              html += f"<li><strong>{key}:</strong> {value}</li>"
            html += "</ul>"
          else:
            html += f"<p><strong>評価 (okinilove):</strong> {eval_okinilove}</p>"
        if _has_value(report_text_okinilove):
            html += f"<pre>{report_text_okinilove}</pre>"
        if pd.notna(report_url_okinilove):
            html += f"<br><a href='{report_url_okinilove}' target='_blank'>レポートリンク (okinilove)</a>"
        html += "</td></tr>"



html += "</tbody></table></div><p><a href=\"index.html\">index.htmlに戻る</a></p></body></html>"

# 整形
soup = BeautifulSoup(html, "html.parser")
# formatted_html = soup.prettify()
# html = formatted_html
output_path = DOCS_DIR / "calendar_report.html"
with output_path.open("w", encoding="utf-8") as f:
    f.write(html)
# %%
