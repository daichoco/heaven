#%%
from bs4 import BeautifulSoup
import pandas as pd
#%%
url = "../docs/report_final.html"
with open(url, "r", encoding="utf-8") as f:
    html = f.read()
soup = BeautifulSoup(html, "html.parser")

# %%
reports = []

for detail in soup.find_all("details"):
    if detail.find_parent("details"):  # 子ならスキップ
        continue

    summary = detail.find("summary")
    if not summary:
        continue

    shop_text = summary.contents[0].strip() if summary.contents else None
    name_tag = summary.find("a")
    girl_name = name_tag.get_text(strip=True) if name_tag else None
    girl_url_tag = summary.find("a",string=girl_name)
    girl_url = girl_url_tag["href"] if girl_url_tag else None
    report_link_tag = summary.find("a", string="レポート")
    report_link = report_link_tag["href"] if report_link_tag else None

    import re
    age_match = re.search(r"(\d{2})才", summary.get_text())
    age = int(age_match.group(1)) if age_match else None

    evaluations = {}
    inner_details = detail.find("div")
    if inner_details:
        for sub_detail in inner_details.find_all("details", recursive=False):
            sub_summary = sub_detail.find("summary")
            key = sub_summary.get_text(strip=True) if sub_summary else None
            value_tag = sub_detail.find("p")
            value = value_tag.get_text(strip=True) if value_tag else None
            if key:
                evaluations[key] = value

    match = re.search(r"\((.*?)\)", shop_text)
    if match:
        shop_info = match.group(1)
        parts = re.split(r"[･・]", shop_info)
        shop_name = parts[0].strip() if len(parts) > 0 else None
        shop_type = parts[1].strip() if len(parts) > 1 else None
    else:
        shop_name = None
        shop_type = None


    reports.append({
        "shop_name" :shop_name,
        "shop_type":shop_type,
        "girl_name": girl_name,
        "girl_url":girl_url,
        "age": age,
        "report_url": report_link,
        "evaluations": evaluations
    })

df1 = pd.DataFrame(reports)
print(df1[["girl_name","shop_name"]])
df1.loc[df1["girl_url"].str.contains("cityheaven.net", na=False), "cityheaven_url"] = df1["girl_url"]
# print(df1.columns)
# %%
url = "../docs/cityheaven_profiles.html"
with open(url, "r", encoding="utf-8") as f:
    html = f.read()
soup = BeautifulSoup(html, 'html.parser')
table = soup.find('table')

# ヘッダー取得（3列目以降は日付）
headers = [th.text.strip() for th in table.find_all('th')]
date_headers = headers[3:]

# データ抽出
rows = table.find_all('tr')[1:]  # skip header
data = []

for row in rows:
    cols = row.find_all('td')
    name_tag = cols[0].find('a')
    name = name_tag.text.strip()
    url = name_tag['href']
    shop = cols[1].text.strip()
    img_tag = cols[2].find('img')
    img_url = img_tag['src'] if img_tag else None
    schedule = [col.text.strip() for col in cols[3:]]
    data.append([name, url, shop, img_url] + schedule)

# DataFrame化
columns = ['名前', 'URL', '店名', '画像URL'] + date_headers
df2 = pd.DataFrame(data, columns=columns)

# %%
import pandas as pd
import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from ddgs import DDGS

# 複数のユーザーエージェントを用意
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/121.0",
    "Mozilla/5.0 (Linux; Android 11; Pixel 5) Chrome/119.0 Mobile Safari/537.36",
]

# ランダムにUAを選択
ua = random.choice(user_agents)
options = Options()
options.add_argument(f"user-agent={ua}")
# options.add_argument("--headless")  # 必要なら有効化
driver = webdriver.Chrome(options=options)

def human_delay():
    """人間っぽい待機を入れる"""
    delay = random.uniform(2, 7)
    if random.random() < 0.1:  # 10%の確率で長めに休む
        delay += random.uniform(10, 20)
    time.sleep(delay)

def search_cityheaven_url(shop_name, girl_name, source="yahoo", max_results=5):
    query = f"{shop_name} {girl_name} site:cityheaven.net"

    if source == "duckduckgo":
        with DDGS() as ddgs:
            results = ddgs.text(query, region="jp-jp", safesearch="off",
                                timelimit=None, max_results=max_results)
            for r in results:
                url = r["href"]
                if "cityheaven.net" in url and "girlid" in url and "attend" not in url:
                    print(f"[DDG] {shop_name} {girl_name}\n{url}")
                    return url
    else:  # Yahoo検索
        try:
            driver.get(f"https://search.yahoo.co.jp/search?p={query}")
            human_delay()

            # 時々スクロールして人間っぽさを出す
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

def enrich_with_cityheaven_urls(df1, source="yahoo"):
    for idx, row in df1[df1["cityheaven_url"].isna()].iterrows():
        shop = row["shop_name"]
        girl = row["girl_name"]
        url = search_cityheaven_url(shop, girl, source=source)
        df1.loc[idx, "cityheaven_url"] = url
        human_delay()  # bot対策
    return df1

# 実行例
# df1 = enrich_with_cityheaven_urls(df1, source="yahoo")
# df1.to_csv("../data/df1 - df1.csv.csv")
driver.quit()



# %%
df1 = pd.read_csv("../data/df1 - df1.csv.csv")

import re

def normalize_url(url):
    if pd.isna(url):
        return None
    url = url.split("?")[0].rstrip("/")
    match = re.search(r"(https://www\.cityheaven\.net/.+?/girlid-\d+)", url)
    return match.group(1) if match else url


# 正規化キー列を追加
df1["url_key"] = df1["cityheaven_url"].apply(normalize_url)
df2["url_key"] = df2["URL"].apply(normalize_url)

# 左結合
merged = pd.merge(df1, df2, how="right", on="url_key", suffixes=("_df1", "_df2"))
#%%
url = "../docs/okinilove_reconstructed.html"
with open(url, "r", encoding="utf-8") as f:
    html = f.read()
soup = BeautifulSoup(html, "html.parser")

# %%
reports = []
soup = soup.find("div",class_="card-container")
for card in soup.find_all("div",class_="card"):
    shop_name = card.find("h3").get_text()
    shop_name = shop_name.split("-")[0].strip()
    name_tag = card.find("a")
    girl_name = name_tag.get_text(strip=True) if name_tag else None
    girl_url = name_tag["href"] if name_tag else None

    detail = card.find_all("details")[0]

    summary = detail.find("summary")

    report_link_tag = summary.find("a")
    report_link = report_link_tag["href"] if report_link_tag else None
    eval = summary.get_text(strip=True)

    report_text = detail.find("pre").get_text(strip=True)


    reports.append({
        "shop_name" :shop_name,
        "girl_name": girl_name,
        "girl_url":girl_url,
        "report_url": report_link,
        "evaluations": eval,
        "report_text":report_text
    })

df3 = pd.DataFrame(reports)
# print(df3[["girl_name","shop_name"]])
df3.loc[df3["girl_url"].str.contains("cityheaven.net", na=False), "cityheaven_url"] = df3["girl_url"]
driver = webdriver.Chrome(options=options)
# df3 = enrich_with_cityheaven_urls(df3,source="yahoo")
driver.quit()
#%%
# df3.to_csv("../data/okinilove_heaven.csv")
#%%
df3 = pd.read_csv("../data/okinilove_heaven.csv")
df3["url_key"] = df3["cityheaven_url"].apply(normalize_url)
merged = pd.merge(df3, merged, how="right", on="url_key", suffixes=("_okinilove", "_merged"))
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
  <title>出勤カレンダー一覧</title>
  <style>
    thead th {
      position: sticky;
      top: 0;
      background: #f9f9f9;
      z-index: 1;
    }
    th.saturday { color: blue; }
    th.sunday { color: red; }
    body { font-family: sans-serif; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ccc; padding: 0.5em; text-align: center; vertical-align: top;fon-size:16px; }
    td:first-child, th:first-child {
      position: sticky;
      left: 0;
      background: #fff;
      z-index: 2;
    }
    img { max-height: 100%; border-radius: 4px; }
    details { text-align: left; margin-top: 5px; }
    summary { cursor: pointer; font-weight: bold; }
    .filter-group { margin-bottom: 15px; }
  </style>
  <script>

    function applyCombinedFilter() {
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
        if (text !== label && text !== "名前＋レビュー+店名" && text !== "画像"){
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
        row.style.display = "table-row";
      } else {
        row.style.display = "none";
      }
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

    has_report = "true" if (pd.notna(eval_merged) or pd.notna(eval_okinilove) or pd.notna(report_text_okinilove)) else "false"
    row_id_merged = f"review-merged-{idx}"
    row_id_okinilove = f"review-okinilove-{idx}"

    # メイン行
    html += f"<tr data-report='{has_report}'>"

    # ★ 名前＋レビュー＋店名（統合セル）
    html += f"<td class='shop-cell'>"
    html += f"<a href='{profile_url}' target='_blank'>{name}</a>"

    # レビューボタン
    if pd.notna(eval_merged):
        html += f"<br><button onclick=\"toggleReviewRow('{row_id_merged}')\">レビュー(merged)</button>"
    if pd.notna(eval_okinilove) or pd.notna(report_text_okinilove):
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
            eval_dict = ast.literal_eval(eval_merged)
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
        if pd.notna(eval_okinilove):
            html += f"<p><strong>評価 (okinilove):</strong> {eval_okinilove}</p>"
        if pd.notna(report_text_okinilove):
            html += f"<pre>{report_text_okinilove}</pre>"
        if pd.notna(report_url_okinilove):
            html += f"<br><a href='{report_url_okinilove}' target='_blank'>レポートリンク (okinilove)</a>"
        html += "</td></tr>"



html += "</tbody></table></div></body></html>"

# 整形
soup = BeautifulSoup(html, "html.parser")
formatted_html = soup.prettify()
html = formatted_html

with open("../docs/calendar_report.html", "w", encoding="utf-8") as f:
    f.write(html)


# %%
