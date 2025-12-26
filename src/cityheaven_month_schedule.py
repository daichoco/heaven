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
#%%
driver.quit()
# %%
import re
from datetime import datetime

# 時間帯の正規表現（例: "10:30 - 22:00"）
time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")

def count_work_days_all(cal_dict):
    """
    カレンダー辞書（"MM/DD(曜)" → ステータス）から
    年越しを考慮しつつ、全期間の出勤日数を返す。

    出勤日数の定義:
    - 時間帯表記（例: "10:30 - 22:00"）のみを出勤扱い
    - 「明日出勤です♪」「休み」「-」などはカウントしない
    """

    # 1. 月日だけでソート（年はまだ付けない）
    def md_key(date_str):
        md = date_str.split("(")[0]
        m, d = map(int, md.split("/"))
        return (m, d)

    items = sorted(cal_dict.items(), key=lambda x: md_key(x[0]))

    # 2. 年を推定しながら datetime を生成
    current_year = datetime.now().year
    last_month = None
    dated_items = []

    for date_str, status in items:
        md = date_str.split("(")[0]
        m, d = map(int, md.split("/"))

        # 月が小さくなったら年越しと判断
        if last_month is not None and m < last_month:
            current_year += 1

        last_month = m
        dated_items.append((datetime(current_year, m, d), status))

    # 3. 全期間の出勤日数をカウント
    count = 0
    for dt, status in dated_items:
        if isinstance(status, str) and time_pattern.match(status.strip()):
            count += 1

    return count


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
import ast
import re
import json
import pandas as pd
from datetime import datetime

# -------------------------
# 1. calendar_dict を最初に作る
# -------------------------
calendar_df["calendar_dict"] = calendar_df["calendar"].apply(
    lambda x: ast.literal_eval(x) if isinstance(x, str) else x
)

# -------------------------
# 2. 時間帯の正規表現
# -------------------------
time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")

# -------------------------
# 3. 全期間の出勤日数カウント（年越し対応）
# -------------------------
def count_work_days_all(cal_dict):
    def md_tuple(date_str):
        md = date_str.split("(")[0]
        m, d = map(int, md.split("/"))
        return m, d

    items = sorted(cal_dict.items(), key=lambda x: md_tuple(x[0]))

    current_year = datetime.now().year
    last_month = None
    dated_items = []

    for date_str, status in items:
        m, d = md_tuple(date_str)
        if last_month is not None and m < last_month:
            current_year += 1
        last_month = m
        dated_items.append((datetime(current_year, m, d), status))

    count = 0
    for dt, status in dated_items:
        if isinstance(status, str) and time_pattern.match(status.strip()):
            count += 1

    return count

calendar_df["work_days"] = calendar_df["calendar_dict"].apply(count_work_days_all)

# -------------------------
# 4. ソート（0 は最後）
# -------------------------
df_positive = calendar_df[calendar_df["work_days"] > 0].sort_values("work_days", ascending=True)
df_zero = calendar_df[calendar_df["work_days"] == 0]
calendar_df = pd.concat([df_positive, df_zero], ignore_index=True)

# -------------------------
# 5. index.html を生成
# -------------------------
html = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>メンバー一覧</title>
  <style>
    body {
      font-family: sans-serif;
      background-color: #f9f9f9;
      padding: 20px;
      max-width: 960px;
      margin: auto;
    }
    h1 { text-align: center; margin-bottom: 40px; }
    h2 { margin-top: 40px; margin-bottom: 20px; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
    .members { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; }
    .members li { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 10px; text-align: center; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05); }
    .members li img { width: 100px; height: 100px; object-fit: cover; border-radius: 4px; margin-bottom: 8px; }
    .member-card { display: flex; flex-direction: column; align-items: center; }
    .name-link { margin-top: 8px; font-weight: bold; text-decoration: none; color: #333; }
    .name-link:hover { color: #007acc; }
  </style>
</head>
<body>
  <h1>公開ページ</h1>

  <section class="member-list">
    <h2>👤 メンバー一覧（出勤日数順）</h2>
    <ul class="members">
"""

for _, row in calendar_df.iterrows():
    html += f"""
      <li>
        <div class="member-card">
          <a href="{row['url']}" target="_blank" rel="noopener noreferrer">
            <img src="{row['image']}" alt="{row['name']}">
          </a>
          <a href="calendar.html?name={row['name']}" class="name-link">
            {row['name']}
          </a>
          <div>出勤日数: {row['work_days']}</div>
          <div>{row['shop']}</div>
        </div>
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
