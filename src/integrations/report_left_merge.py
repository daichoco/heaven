#%%
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
  sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl, write_jsonl_atomic
from html_format import write_html
from okinilove.okinilove_left_merge import merge_okinilove_data
from ns_senmon.ns_left_merge import merge_ns_reports
from ns_senmon.match_cityheaven import resolve_urls
from cityheaven.exists_merge import (
  PROFILE_DATA_PATH,
  main as scrape_cityheaven_profiles,
)
from web_common import BACK_NAV, BACK_TO_TOP, HEAD_COMMON
from tokai_fuzoku.report_store import canonical_article_url

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


def enrich_with_cityheaven_urls(df):
    """店名・姫名から CityHeaven URL を補完する（Yahoo検索は使わず既知データと店舗ページ検索で突合）。"""
    missing = df["cityheaven_url"].isna() | (df["cityheaven_url"].astype("string").str.strip() == "")
    if not missing.any():
        return df
    pairs = list(zip(
        df.loc[missing, "shop_name"].fillna("").astype(str),
        df.loc[missing, "girl_name"].fillna("").astype(str),
    ))
    resolved = resolve_urls(pairs)
    df.loc[missing, "cityheaven_url"] = [resolved[pair] for pair in pairs]
    return df


def report_identity_key(row):
    # 東海風俗は1記事=1レポートのため記事URLを優先する（同一人物の別レポートも履歴に残す）。
    # cityheaven_url は検索で後から補完されるため、新規判定のキーには使わない
    article_key = canonical_article_url(row.get("report_url"))
    if article_key:
        return article_key
    for column in ("report_url", "girl_url"):
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
    is_new = current_keys.notna() & ~current_keys.isin(history_keys) & ~current_keys.duplicated()
    new_reports = current_reports.loc[is_new].copy()
    if new_reports.empty:
        return history, 0

    new_reports = enrich_with_cityheaven_urls(new_reports)

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


def extract_report_final_df(parser="lxml"):
    html = load_html(DOCS_DIR / "tokai_final_report.html")
    soup = BeautifulSoup(html, parser)
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


def _log_elapsed(label, start):
    print(f"[timing] {label}: {time.perf_counter() - start:.2f}s")


def main():
    start = time.perf_counter()
    asyncio.run(scrape_cityheaven_profiles())
    _log_elapsed("scrape_cityheaven_profiles", start)
    try:
        start = time.perf_counter()
        df1_current = extract_report_final_df()
        _log_elapsed("extract_report_final_df", start)
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
    start = time.perf_counter()
    merged = merge_okinilove_data(
      merged,
      normalize_url=normalize_url,
      enrich_with_cityheaven_urls=enrich_with_cityheaven_urls,
    )
    _log_elapsed("merge_okinilove_data", start)
    start = time.perf_counter()
    merged = merge_ns_reports(merged, normalize_url=normalize_url)
    _log_elapsed("merge_ns_reports", start)
    return merged


CALENDAR_PAGE_HEAD = """<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8">
    __HEAD_COMMON__
    <title>出勤カレンダー一覧</title>
    <style>
      :root { --page-pad: 16px; --sat: blue; --sun: red; --row-sep: #f5f6f7; --review-merged: #f9f9f9; --review-ns: #fff8f0; --review-okinilove: #f0f9ff; }
      @media (prefers-color-scheme: dark) {
        :root { --sat: #6cb6ff; --sun: #ff7b72; --row-sep: #0f1214; --review-merged: #1d2125; --review-ns: #2a2219; --review-okinilove: #172431; }
      }
      body { max-width: 1400px; font: 15px/1.5 sans-serif; }
      h1 { margin: 0 0 16px; font-size: 1.4rem; }
      thead th {
        background: var(--surface);
      }
      th.saturday { color: var(--sat); }
      th.sunday { color: var(--sun); }
      body > div[style*="overflow-x: auto"] { overflow-x: auto; overflow-y: visible; border: 1px solid var(--border); background: var(--surface-alt); }
      table { border-collapse: collapse; width: 100%; font-size: 14px; }
      th, td { border: 1px solid var(--border-soft); padding: 8px; text-align: center; vertical-align: top; }
      th:first-child { position: sticky; left: 0; z-index: 2; min-width: 220px; background: var(--surface); }
      td.shop-cell { min-width: 220px; text-align: left; }
      .m-cal { display: none; }
      img { width: 64px; height: 64px; object-fit: cover; border-radius: 4px; }
      details { text-align: left; margin-top: 5px; }
      summary { cursor: pointer; font-weight: bold; }
      .filter-group { margin-bottom: 14px; line-height: 1.8; }
      .filter-group label { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; }
      .calendar-shop-filter { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 320px); align-items: center; gap: 10px; max-width: 600px; margin: 0 0 14px; }
      .calendar-shop-filter select { width: 100%; min-width: 0; min-height: 40px; padding: 6px 9px; border: 1px solid var(--input-border); border-radius: 4px; background: var(--input-bg); color: var(--fg); }
      .day-picker { display: flex; align-items: center; gap: 8px; }
      .day-picker select { flex: 1; min-width: 0; max-width: 320px; min-height: 40px; padding: 6px 9px; border: 1px solid var(--input-border); border-radius: 4px; background: var(--input-bg); color: var(--fg); font-weight: 700; }
      .day-picker button { flex: none; min-height: 40px; padding: 6px 12px; border: 1px solid var(--border); border-radius: 4px; background: var(--chip); color: var(--fg); font-size: 15px; cursor: pointer; }
      .day-picker select.inactive { font-weight: normal; color: var(--muted); }
      .day-picker button:disabled { opacity: 0.4; cursor: default; }
      th.day-col { min-width: 140px; }
      td[data-on="1"] .cell { display: inline-block; padding: 2px 8px; border-radius: 4px; background: var(--surface-open); color: var(--link); font-weight: 700; }
      .no-results { padding: 16px; color: var(--muted); }
      .review-row.review-merged { background-color: var(--review-merged); }
      .review-row.review-ns { background-color: var(--review-ns); }
      .review-row.review-okinilove { background-color: var(--review-okinilove); }
      .shop-cell button {
        min-height: 44px;
        padding: 8px 12px;
        border-radius: 4px;
        color: var(--fg);
        background-color: var(--chip);
        border: 1px solid var(--border);
        cursor: pointer;
        width: 100%;
      }
      .shop-cell button:active {
        background-color: var(--border-soft);
      }
      @media (max-width: 700px) {
        :root { --page-pad: 12px; }
        h1 { font-size: 1.2rem; }
        .filter-group { margin-bottom: 16px; }
        .filter-group label { min-height: 44px; margin-right: 8px; }
        .calendar-shop-filter { grid-template-columns: minmax(0, 1fr); gap: 4px; }
        .calendar-shop-filter select { min-height: 44px; }
        body > div[style*="overflow-x: auto"] { max-height: none; overflow-x: auto; overflow-y: visible; }
        body > div[style*="overflow-x: auto"] table, body > div[style*="overflow-x: auto"] tbody { display: block; width: 100%; min-width: 0; }
        body > div[style*="overflow-x: auto"] thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
        body > div[style*="overflow-x: auto"] tbody tr:not(.review-row) { display: grid; grid-template-columns: 97px minmax(0, 1fr); column-gap: 12px; align-items: start; margin: 0; padding: 12px 12px; border: 0; border-bottom: 4px solid var(--row-sep); background: var(--surface-alt); color: var(--fg-soft); font-size: 13px; }
        body > div[style*="overflow-x: auto"] td { border: 0; padding: 0; }
        body > div[style*="overflow-x: auto"] td.shop-cell { grid-column: 2; grid-row: 1; min-width: 0; text-align: left; }
        body > div[style*="overflow-x: auto"] td.shop-cell > a { display: block; font-size: 16px; font-weight: 700; line-height: 1.3; color: var(--muted); text-decoration: none; }
        body > div[style*="overflow-x: auto"] td.shop-cell > br { display: none; }
        body > div[style*="overflow-x: auto"] td.shop-cell .shop-info { display: block; font-size: 10px; line-height: 15px; color: var(--muted); }
        body > div[style*="overflow-x: auto"] td.img-cell { grid-column: 1; grid-row: 1; }
        body > div[style*="overflow-x: auto"] td.img-cell img { display: block; width: 97px; height: 97px; border-radius: 5px; object-fit: cover; }
        body > div[style*="overflow-x: auto"] td[data-label] { display: none; }
        body > div[style*="overflow-x: auto"] .m-cal { display: block; margin: 5px 0 0; overflow-x: scroll; overflow-y: hidden; -webkit-overflow-scrolling: touch; }
        body > div[style*="overflow-x: auto"] .m-cal ul { display: flex; margin: 0; padding: 0; list-style: none; width: max-content; }
        body > div[style*="overflow-x: auto"] .m-cal li { width: 65px; margin: 4px; color: var(--muted); }
        body > div[style*="overflow-x: auto"] .m-date { margin: 0 0 2px; font-size: 13px; line-height: 18px; text-align: center; color: var(--muted); }
        body > div[style*="overflow-x: auto"] .m-date.saturday { color: var(--sat); }
        body > div[style*="overflow-x: auto"] .m-date.sunday { color: var(--sun); }
        body > div[style*="overflow-x: auto"] .m-box { height: 50px; border-radius: 5px; box-shadow: 0 0 4px var(--shadow); overflow: hidden; text-align: center; font-size: 11px; line-height: 15.4px; padding: 3px 0 0; }
        body > div[style*="overflow-x: auto"] .m-box.on { font-size: 13px; line-height: 13px; padding: 5px 0; }
        body > div[style*="overflow-x: auto"] .m-box.off { background: linear-gradient(var(--surface-alt) calc(100% - 8px), var(--border) 0); }
        body > div[style*="overflow-x: auto"] .review-row td { display: block; width: 100%; }
        body > div[style*="overflow-x: auto"] .review-row pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
      }
    </style>
    <script>
      const TIME_RE = /^\\d{1,2}:\\d{2}\\s*~\\s*\\d{1,2}:\\d{2}$/;

      function initialDayIndex(select) {
        const today = new Date();
        const options = [...select.options];
        const index = options.findIndex(option => {
          const [month, day] = option.dataset.date.split("/").map(Number);
          return month === today.getMonth() + 1 && day === today.getDate();
        });
        return index >= 0 ? index : 0;
      }

      function shiftDay(delta) {
        const select = document.getElementById("daySelect");
        const next = select.selectedIndex + delta;
        if (next < 0 || next >= select.options.length) return;
        select.selectedIndex = next;
        selectDay();
      }

      // セレクタや前日/翌日を操作したら日付指定モードに戻す
      function selectDay() {
        document.querySelector('input[name="dateMode"][value="day"]').checked = true;
        applyCombinedFilter();
      }

      function applyCombinedFilter() {
        resetAllReviews();
        const select = document.getElementById("daySelect");
        if (!select || !select.options.length) return;
        const weekendMode = document.querySelector('input[name="dateMode"]:checked').value === "weekend";
        // 表示対象の列番号（日付指定なら選択日、土日モードなら土日すべて）
        const targetDays = new Set(weekendMode
          ? [...select.options].filter(o => /\((土|日)\)/.test(o.dataset.label)).map(o => o.value)
          : [select.value]);
        const workingOnly = document.getElementById("workingOnly").checked;
        const reportSelected = document.querySelector('input[name="reportFilter"]:checked').value;
        const selectedShop = document.getElementById("shopFilter").value;
        const workingCounts = new Array(select.options.length).fill(0);
        let working = 0;
        let shown = 0;

        document.querySelectorAll('tbody tr:not(.review-row)').forEach(row => {
          const shopMatches = !selectedShop || row.dataset.shops.split("|").includes(selectedShop);
          const hasReport = row.getAttribute("data-report");
          const showByReport = shopMatches && (reportSelected === "all"
            || (reportSelected === "has" && hasReport === "true")
            || (reportSelected === "none" && hasReport === "false"));
          let isWorking = false;
          row.querySelectorAll('td[data-day]').forEach(cell => {
            const on = cell.dataset.on === "1" || TIME_RE.test(cell.textContent.trim());
            if (on && showByReport) workingCounts[Number(cell.dataset.day)] += 1;
            if (targetDays.has(cell.dataset.day) && on) isWorking = true;
          });
          if (isWorking && showByReport) working += 1;
          const visible = showByReport && (isWorking || !workingOnly);
          if (visible) shown += 1;
          row.style.display = visible ? "" : "none";
        });

        // 対象日以外の列（PC）とカード（スマホ）を隠す
        document.querySelectorAll('[data-day]').forEach(el => {
          el.style.display = targetDays.has(el.dataset.day) ? "" : "none";
        });

        [...select.options].forEach((option, i) => {
          option.textContent = `${option.dataset.label}  出勤${workingCounts[i]}名`;
        });
        document.getElementById("prevDay").disabled = select.selectedIndex <= 0;
        document.getElementById("nextDay").disabled = select.selectedIndex >= select.options.length - 1;
        select.classList.toggle("inactive", weekendMode);
        const label = weekendMode ? "土日" : select.options[select.selectedIndex].dataset.label;
        document.getElementById("dayCount").textContent = workingOnly
          ? `${label} の出勤 ${working}名`
          : `${label} の出勤 ${working}名 / 表示 ${shown}名`;
        document.getElementById("noResults").hidden = shown > 0;
      }

      function initCalendar() {
        const select = document.getElementById("daySelect");
        if (select && select.options.length) select.selectedIndex = initialDayIndex(select);
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
  <body onload="initCalendar()">
    __BACK_NAV__
    <h1>出勤カレンダーとレビュー一覧</h1>

    <div class="filter-group">
      <strong>表示条件：</strong><br>
      <label><input type="radio" name="dateMode" value="day" checked onchange="applyCombinedFilter()"> 日付指定</label>
      <label><input type="radio" name="dateMode" value="weekend" onchange="applyCombinedFilter()"> 土日出勤のみ</label>
      <label><input type="checkbox" id="workingOnly" checked onchange="applyCombinedFilter()"> 出勤の人のみ</label>
    </div>

    <div class="filter-group">
      <strong>レビュー条件：</strong><br>
      <label><input type="radio" name="reportFilter" value="all" checked onchange="applyCombinedFilter()"> 全表示</label>
      <label><input type="radio" name="reportFilter" value="has" onchange="applyCombinedFilter()"> レポートありのみ</label>
      <label><input type="radio" name="reportFilter" value="none" onchange="applyCombinedFilter()"> レポートなしのみ</label>
    </div>

    <label class="calendar-shop-filter"><span>店舗</span><select id="shopFilter" data-shop-filter aria-label="店舗" onchange="applyCombinedFilter()"><option value="">全店舗</option>__SHOP_OPTIONS__</select></label>

    <div class="sticky-toolbar" id="toolbar">
      <div class="day-picker">
        <button type="button" id="prevDay" onclick="shiftDay(-1)" aria-label="前日">‹ 前日</button>
        <select id="daySelect" aria-label="表示する日付" onchange="selectDay()">__DAY_OPTIONS__</select>
        <button type="button" id="nextDay" onclick="shiftDay(1)" aria-label="翌日">翌日 ›</button>
      </div>
      <p class="filter-count" id="dayCount" aria-live="polite"></p>
    </div>

"""
CALENDAR_PAGE_HEAD = (
    CALENDAR_PAGE_HEAD
    .replace("__HEAD_COMMON__", HEAD_COMMON.replace("\n", "\n    "))
    .replace("__BACK_NAV__", BACK_NAV)
)


WORK_TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}$")


def build_calendar_html(merged):
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

    # HTML組み立て開始（日付セレクタの選択肢 value は data-day と同じ列番号）
    day_options = "".join(
        f"<option value='{i}' data-date='{day.split('(')[0]}' data-label='{day}'>{day}</option>"
        for i, day in enumerate(calendar_cols)
    )
    html = CALENDAR_PAGE_HEAD.replace("__DAY_OPTIONS__", day_options)
    shops = sorted({
        shop.strip()
        for value in merged.get("店名", [])
        if isinstance(value, str)
        for shop in value.split("\n")
        if shop.strip()
    })
    shop_options = "".join(
        f'<option value="{html_escape(shop, quote=True)}">{html_escape(shop)}</option>'
        for shop in shops
    )
    html = html.replace("__SHOP_OPTIONS__", shop_options)

    # テーブル開始
    html += "<div style='overflow-x: auto;'>"
    html += "<table><thead><tr><th>名前＋レビュー+店名</th><th>画像</th>"

    # 日付ヘッダー
    for i, day in enumerate(calendar_cols):
        day_class = " saturday" if "(土)" in day else (" sunday" if "(日)" in day else "")
        html += f"<th class='day-col{day_class}' data-day='{i}'>{day}</th>"

    html += "</tr></thead><tbody>"

    # 同じ姫に東海風俗レポートが複数ある場合は1行にまとめる
    merged_reports_by_key = {}
    for idx, row in merged.iterrows():
        key = row.get("url_key")
        if pd.isna(key):
            continue
        if _has_value(row["evaluations_merged"]):
            merged_reports_by_key.setdefault(key, []).append(
                (row["evaluations_merged"], row["report_url_merged"])
            )
    rendered_keys = set()

    for idx, row in merged.iterrows():
        key = row.get("url_key")
        if pd.notna(key):
            if key in rendered_keys:
                continue
            rendered_keys.add(key)
        merged_reports = merged_reports_by_key.get(key, []) if pd.notna(key) else (
            [(row["evaluations_merged"], row["report_url_merged"])] if _has_value(row["evaluations_merged"]) else []
        )
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

        ns_reports = row["ns_reports"] if isinstance(row["ns_reports"], list) else []
        row_id_ns = f"review-ns-{idx}"

        has_report = "true" if (
          ns_reports
          or merged_reports
          or _has_value(eval_okinilove)
          or _has_value(report_text_okinilove)
        ) else "false"
        row_id_merged = f"review-merged-{idx}"
        row_id_okinilove = f"review-okinilove-{idx}"

        # メイン行
        shop_attr = html_escape("|".join(shop), quote=True)
        html += f"<tr data-report='{has_report}' data-shops='{shop_attr}'>"

        # ★ 名前＋レビュー＋店名（統合セル）
        html += f"<td class='shop-cell'>"
        html += f"<a href='{profile_url}' target='_blank'>{name}</a>"

        # レビューボタン
        if merged_reports:
            count = f" {len(merged_reports)}件" if len(merged_reports) > 1 else ""
            html += f"<br><button onclick=\"toggleReviewRow('{row_id_merged}')\">レビュー(merged{count})</button>"
        if _has_value(eval_okinilove) or _has_value(report_text_okinilove):
            html += f"<br><button onclick=\"toggleReviewRow('{row_id_okinilove}')\">レビュー(okinilove)</button>"

        if ns_reports:
            html += f"<br><button onclick=\"toggleReviewRow('{row_id_ns}')\">レビュー(NS専門 {len(ns_reports)}件)</button>"

        # 店名（名前の下に表示）
        if len(shop) >= 1:
            html += f"<br><span class='shop-info'>{shop[0]}</span>"
        if len(shop) >= 2:
            html += f"<br><span class='shop-info'>{shop[1]}</span>"

        # スマホ用の横スクロールカレンダー（デスクトップでは非表示）
        html += "<div class='m-cal'><ul>"
        for i, day in enumerate(calendar_cols):
            status = str(row[day]) if pd.notna(row[day]) else "-"
            day_class = " saturday" if "(土)" in day else (" sunday" if "(日)" in day else "")
            is_time = WORK_TIME_RE.match(status.strip())
            if is_time:
                formatted_status = re.sub(r'\s*~\s*', '<br>~<br>', status.strip())
                box = f"<div class='m-box on'>{formatted_status}</div>"
            else:
                box = f"<div class='m-box off'>{status}</div>"
            html += f"<li data-day='{i}'><div class='m-date{day_class}'>{day.split('(')[0]}</div>{box}</li>"
        html += "</ul></div>"

        html += "</td>"

        # 画像セル
        html += f"<td class='img-cell'><img src='{image_url}' alt='{name}' width='64' height='64' loading='lazy' decoding='async'></td>"

        # 出勤日
        for i, day in enumerate(calendar_cols):
            status = row[day] if pd.notna(row[day]) else "-"
            day_class = " class='saturday'" if "(土)" in day else (" class='sunday'" if "(日)" in day else "")
            short_day = day.split("(")[0]
            on_attr = " data-on='1'" if WORK_TIME_RE.match(str(status).strip()) else ""
            html += f"<td data-label='{day}' data-date='{short_day}' data-day='{i}'{on_attr}{day_class}><span class='cell'>{status}</span></td>"

        html += "</tr>"

        # mergedレビュー行（複数レポートは縦に並べる）
        if merged_reports:
            html += f"<tr id='{row_id_merged}' class='review-row review-merged' style='display:none;'>"
            html += f"<td colspan='3' style='text-align: left;'>"
            for eval_value, report_url in merged_reports:
                try:
                    html += "<ul>"
                    for k, v in _evaluation_dict(eval_value).items():
                        html += f"<li><strong>{k}:</strong> {v}</li>"
                    if pd.notna(report_url):
                        html += f"<li><a href='{report_url}' target='_blank'>レポートリンク (merged)</a></li>"
                    html += "</ul>"
                except Exception:
                    html += "<p>レビュー解析失敗 (merged)</p>"
            html += "</td></tr>"

        # NS専門レビュー行
        if ns_reports:
            html += f"<tr id='{row_id_ns}' class='review-row review-ns' style='display:none;'>"
            html += f"<td colspan='3' style='text-align: left;'>"
            for rep in ns_reports:
                html += f"<p><a href='{rep['url']}' target='_blank'>レポートリンク (NS専門 {rep['date']})</a></p><ul>"
                for k, v in rep["scores"].items():
                    html += f"<li><strong>{k}:</strong> {html_escape(str(v))}</li>"
                html += "</ul>"
                for k, v in rep["comments"].items():
                    html += f"<p><strong>{k}</strong></p><pre>{html_escape(str(v))}</pre>"
            html += "</td></tr>"

        # okiniloveレビュー行
        if pd.notna(eval_okinilove) or pd.notna(report_text_okinilove):
            html += f"<tr id='{row_id_okinilove}' class='review-row review-okinilove' style='display:none;'>"
            html += f"<td colspan='3' style='text-align: left;'>"
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

    html += "</tbody></table></div>"
    html += "<p class='no-results' id='noResults' hidden>条件に合う人はいません。</p>"
    html += BACK_TO_TOP + "</body></html>"
    return html


def write_calendar_html(merged, path=None):
    output_path = path or DOCS_DIR / "calendar_report.html"
    write_html(output_path, build_calendar_html(merged))


if __name__ == "__main__":
    _total_start = time.perf_counter()
    write_calendar_html(main())
    _log_elapsed("report_left_merge 合計", _total_start)
