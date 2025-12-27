#%%
import time
import re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
from urllib.parse import urljoin
import pandas as pd
#%%
chromedriver_autoinstaller.install()
options = Options()
# options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1")

driver = webdriver.Chrome(options=options)
driver.get("https://www.cityheaven.net/tt/community/ABMyAlbumShukkin/?lo=1")
WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "user")))

driver.find_element(By.NAME, "user").send_keys("daichoco08050214@icloud.com")
driver.find_element(By.NAME, "pass").send_keys("Itochoco")
driver.find_element(By.NAME, "login").click()
WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "js-contentsWrap")))
driver.get("https://www.cityheaven.net/tt/community/ABMyAlbumShukkin/?lo=1")

# スクロール処理（最下部まで）
last_height = driver.execute_script("return document.body.scrollHeight")

while True:
    # 一番下までスクロール
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)  # 読み込み待ち

    # 新しい高さを取得
    new_height = driver.execute_script("return document.body.scrollHeight")
    if new_height == last_height:
        break  # 高さが変わらなければ終了
    last_height = new_height

# 最下部までスクロール後のHTMLを取得
# %%

from tqdm import tqdm

data = {}

blocks = driver.find_elements(By.CSS_SELECTOR, "div.recommend-block")

for block in tqdm(blocks, desc="進捗", unit="人"):
    try:
        # 名前の取得
        name_tag = block.find_element(By.CSS_SELECTOR, ".recommend-block-top-name")
        name = name_tag.text.strip() if name_tag else "不明"
        shop_tag = block.find_element(By.CSS_SELECTOR,".recommend-block-top-shop")
        shop = shop_tag.text.strip() if shop_tag else "不明"

        # 画像URLの取得と保存
        img_tag = block.find_element(By.CSS_SELECTOR, ".recommend-block-img img")
        img_url = urljoin("https://www.cityheaven.net", img_tag.get_attribute("src"))

        # プロフィールリンクの href を取得して新規タブで開く
        link_tag = block.find_element(By.CSS_SELECTOR, "a.recommend-block-top-link")
        href = link_tag.get_attribute("href")
        driver.execute_script("window.open(arguments[0], '_blank');", href)
        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        driver.switch_to.window(driver.window_handles[1])
        girl_url = driver.current_url

        # 出勤情報の取得
        calendar = {}
        try:
            syukin_block = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#syukin_month"))
            )
            rows = syukin_block.find_elements(By.CSS_SELECTOR, "div.girlitem_waku")
            for row in rows:
                try:
                    date = row.find_element(By.CSS_SELECTOR, "span.girlitem_waku_left").text.strip()
                    status = row.find_element(By.CSS_SELECTOR, "span.girlitem_waku_right").text.strip()
                    calendar[date] = status
                except:
                    continue
            # print(f"{name}取得完了")
        except:
            calendar = {}
            print(f"{name}取得できず")

        driver.close()
        driver.switch_to.window(driver.window_handles[0])

        # 情報保存
        data[name] = {
            "shop": shop,
            "url": girl_url,
            "image": img_url,
            "calendar": calendar
        }

    except Exception as e:
        print(f"エラー: {e}")
        continue


calendar_df = pd.DataFrame.from_dict(data, orient="index").reset_index()
calendar_df = calendar_df.rename(columns={"index": "name"})

driver.quit()
# %%
# 出勤日数を計算
time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")
calendar_df["working_days"] = calendar_df["calendar"].apply(
    lambda cal: sum(1 for status in cal.values() if time_pattern.match(status))
)

# 出勤日数が多い順にソート
calendar_df = calendar_df.sort_values(by="working_days", ascending=False)
html = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>メンバー一覧</title>
  <style>
    /* 共通 */
    body {
      font-family: sans-serif;
      background-color: #f9f9f9;
      padding: 20px;
      max-width: 960px;
      margin: auto;
    }

    h1 {
      text-align: center;
      margin-bottom: 40px;
    }

    h2 {
      margin-top: 40px;
      margin-bottom: 20px;
      color: #2c3e50;
      border-bottom: 1px solid #ddd;
      padding-bottom: 5px;
    }

    /* レポート一覧 */
    .reports {
      list-style: none;
      padding: 0;
    }

    .reports li {
      margin-bottom: 10px;
    }

    .reports a {
      text-decoration: none;
      color: #007acc;
      font-weight: bold;
    }

    .reports a:hover {
      text-decoration: underline;
    }

    /* メンバー一覧 */
    .members {
      list-style: none;
      padding: 0;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 16px;
    }

    .members li {
      background: white;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 10px;
      text-align: center;
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    }

    .members li img {
      width: 100px;
      height: 100px;
      object-fit: cover;
      border-radius: 4px;
      margin-bottom: 8px;
    }

    .members li a {
      display: block;
      text-decoration: none;
      color: #333;
      font-weight: bold;
    }

    .members li a:hover {
      color: #007acc;
    }
  </style>
</head>
<body>
  <h1>公開ページ</h1>
  <section class="report-list">
    <h2>📄 レポート一覧</h2>
    <ul class="reports">
      <li><a href="calendar_report.html" target="_blank">カレンダー形式レポート</a></li>
      <li><a href="cityheaven_profiles.html" target="_blank">プロフィール一覧</a></li>
      <li><a href="report_final.html" target="_blank">最終レポート</a></li>
      <li><a href="okinilove_remove_unknown.html" target="_blank">オキニラブレポート</a></li>
      <li><a href="okinilove_reconstructed.html" target="_blank">オキニラブレポート再構成</a></li>
    </ul>
  </section>

  <section class="member-list">
    <h2>👤 メンバー一覧</h2>
    <ul class="members">
"""

for _, row in calendar_df.iterrows():
    html += f"""
      <li>
        <a href="{row['url']}" target="_blank" rel="noopener noreferrer">
          <img src="{row['image']}" alt="{row['name']}">
        </a>
        <a href="calendar.html?name={row['name']}">
          {row['name']}
        </a>
        {row['shop']}
        <br>出勤日数: {row['working_days']}日
      </li>
    """

html += """
    </ul>
  </section>
</body>
</html>
"""

with open("../docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

import json

calendar_df["calendar_dict"] = calendar_df["calendar"].apply(
    lambda x: ast.literal_eval(x) if isinstance(x, str) else x
)
# JSON化（日本語保持）
calendars = {row["name"]: row["calendar_dict"] for _, row in calendar_df.iterrows()}
calendars_json = json.dumps(calendars, ensure_ascii=False, indent=2)
import ast
from datetime import datetime


# 時間形式の正規表現（例：12:00 - 14:00）
time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")

# calendar列を辞書に変換（必要に応じて）

# 全日付を抽出
all_dates = set()
for cal in calendar_df["calendar_dict"]:
    all_dates.update(cal.keys())

# 日付を datetime に変換して昇順に並べる
def parse_date(d):
    try:
        md = d.split("(")[0]
        m, day = map(int, md.split("/"))
        return datetime(datetime.now().year, m, day)
    except:
        return None

sorted_dates = sorted([d for d in all_dates if parse_date(d)], key=parse_date)
date_labels = [d for d in sorted_dates]  # 元の文字列形式（例：11/16(日)）

# 日付ラベル（取得済みのものを使用）
date_labels_json = json.dumps(date_labels, ensure_ascii=False, indent=2)

html = f"""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>出勤カレンダー</title>
  <style>
    body {{ font-family: sans-serif; }}
    .calendar-container {{
      display: grid;
      grid-template-columns: repeat(7, 1fr); /* 横7列（日〜土） */
      gap: 8px;
    }}
    .date-card {{
      border: 1px solid #ccc;
      border-radius: 6px;
      padding: 6px;
      text-align: center;
      background-color: #fafafa;
      min-height: 90px;
    }}
    .date-card h2 {{
      margin: 0 0 4px;
      font-size: 0.9em;
    }}
    .saturday {{ background-color: #e6f0ff; }}
    .sunday {{ background-color: #ffe6e6; }}
    .today {{ border: 2px solid orange; }}
  </style>
</head>
<body>
  <h1 id="title"></h1>
  <div id="calendar-container" class="calendar-container"></div>

  <script>
    const calendars = {calendars_json};
    const dateLabels = {date_labels_json};

    const params = new URLSearchParams(window.location.search);
    const selectedName = params.get("name");

    function pad(n) {{ return n < 10 ? "0" + n : "" + n; }}

    function stripParen(dateStr) {{
      const idx = dateStr.indexOf("(");
      return idx >= 0 ? dateStr.slice(0, idx) : dateStr;
    }}

    if (selectedName && calendars[selectedName]) {{
      document.getElementById("title").innerText = selectedName + "の出勤カレンダー";
      const container = document.getElementById("calendar-container");
      const cal = calendars[selectedName];

      const today = new Date();
      const todayStr = (today.getMonth() + 1) + "/" + today.getDate();
      const todayStrPadded = pad(today.getMonth() + 1) + "/" + pad(today.getDate());

      for (let i = 0; i < dateLabels.length; i++) {{
        const date = dateLabels[i];
        const status = cal[date] ? cal[date] : "-";

        let weekdayClass = "";
        if (date.indexOf("(土)") !== -1) weekdayClass = "saturday";
        if (date.indexOf("(日)") !== -1) weekdayClass = "sunday";

        const dateOnly = stripParen(date);
        const isToday = (dateOnly === todayStr) || (dateOnly === todayStrPadded);

        container.innerHTML += `
          <div class="date-card ${{weekdayClass}} ${{isToday ? "today" : ""}}">
            <h2>${{date}}</h2>
            <p>${{status}}</p>
          </div>
        `;
      }}
    }} else {{
      document.getElementById("calendar-container").innerHTML = "<p>該当するメンバーが見つかりません</p>";
    }}
  </script>
</body>
</html>
"""


with open("../docs/calendar.html", "w", encoding="utf-8") as f:
    f.write(html)
# %%
