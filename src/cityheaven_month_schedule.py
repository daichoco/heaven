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
from selenium.common.exceptions import TimeoutException  # 追加インポート

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
max_attempts = 50  # 最大試行回数（必要に応じて調整）
attempts = 0

def wait_for_height_change(driver, initial_height, timeout=10):
    """ページの高さが変わるまで待機"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.body.scrollHeight") > initial_height
        )
        return True
    except TimeoutException:
        return False

while attempts < max_attempts:
    prev_height = driver.execute_script("return document.body.scrollHeight")
    driver.execute_script("$.autopager.load();")

    # 動的待機: 高さが変わるまで待つ（最大10秒）
    if not wait_for_height_change(driver, prev_height, timeout=10):
        break  # タイムアウトしたら終了

    new_height = driver.execute_script("return document.body.scrollHeight")
    if new_height == prev_height:
        break  # 高さが変わらなければ終了
    attempts += 1
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
# 1. calendar_dict 整形
import ast
calendar_df["calendar_dict"] = calendar_df["calendar"].apply(
    lambda x: x if isinstance(x, dict) else ast.literal_eval(x)
)
from datetime import datetime

# 全日付を集める
all_dates = set()
for cal in calendar_df["calendar_dict"]:
    all_dates.update(cal.keys())

# "11/16(日)" → datetime に変換
def parse_date(d):
    try:
        md = d.split("(")[0]
        m, day = map(int, md.split("/"))
        return datetime(datetime.now().year, m, day)
    except:
        return None

# 土日判定
def is_weekend(date_str):
    dt = parse_date(date_str)
    if not dt:
        return False
    return dt.weekday() >= 5  # 5=土, 6=日

# 土日だけ抽出
weekend_dates = sorted([d for d in all_dates if is_weekend(d)], key=parse_date)
weekend_map = {d: [] for d in weekend_dates}

time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")

for _, row in calendar_df.iterrows():
    name = row["name"]
    shop = row["shop"]
    image = row["image"]
    cal = row["calendar_dict"]

    for date in weekend_dates:

        # その日付にデータがない場合はスキップ
        if date not in cal:
            continue

        status = cal[date]

        # ★ 時刻形式でなければ弾く（ここが重要）
        if not time_pattern.match(status):
            continue

        # ★ 時刻形式だけ weekend_map に追加
        weekend_map[date].append({
            "name": name,
            "shop": shop,
            "image": image,
            "status": status,
            "url": row["url"]
        })
html = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>土日出勤カレンダー</title>
<style>
  body { font-family: sans-serif; padding: 20px; background: #f9f9f9;      padding: 20px;
      max-width: 960px;
      margin: auto; }
  h1 { text-align: center; margin-bottom: 30px; }

  .calendar-grid {
    display: grid;
    grid-template-columns: 1fr 1fr; /* 土・日 */
    gap: 16px;
  }

  .day-card {
    background: white;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 12px;
  }

  .day-card h2 {
    margin: 0 0 10px;
    font-size: 1.1em;
    border-bottom: 1px solid #eee;
    padding-bottom: 4px;
  }

  .member {
    display: flex;
    align-items: center;
    margin-bottom: 10px;
  }

  .member img {
    width: 50px;
    height: 50px;
    object-fit: cover;
    border-radius: 4px;
    margin-right: 10px;
  }

  .member-name {
    font-weight: bold;
  }

  .status {
    font-size: 0.9em;
    color: #555;
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

<h1>土日出勤カレンダー</h1>

<div class="calendar-grid">
"""

for date in weekend_dates:
    html += f'<div class="day-card">'
    html += f'<h2>{date}</h2>'

    members = weekend_map[date]
    if not members:
        html += "<p>出勤なし</p>"
    else:
        for m in members:
            html += f"""
            <div class="member">
              <img src="{m['image']}" alt="{m['name']}">
              <div>
                <div class="member-name">
                  <a href="{m['url']}" target="_blank">{m['name']}</a>（{m['shop']}）
                </div>
                <div class="status">{m['status']}</div>
              </div>
            </div>
            """

    html += "</div>"

html += """
</div>
<p><a href="index.html">index.htmlに戻る</a></p>
</body>
</html>
"""

with open("../docs/index.html", "w", encoding="utf-8") as f:
    f.write(html)

# %%
