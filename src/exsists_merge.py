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
import os
from urllib.parse import urljoin
#%%
chromedriver_autoinstaller.install()
options = Options()
# options.add_argument("--headless=new")
options.add_argument('--window-size=1920,1080')  # 広めに固定
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
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
#%%
def load_existing_data(calendar_path):
    """calendar_report.htmlを読み込み、(名前, 店名)をキーに既存データを返す"""
    from bs4 import BeautifulSoup
    import os

    existing_data = {}
    if os.path.exists(calendar_path):
        with open(calendar_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        rows = soup.select("tr[data-report]")
        for row in rows:
            name_tag = row.select_one("td a")
            shop_tag = row.select("td")[1]

            name = name_tag.text.strip() if name_tag else "不明"
            shop = shop_tag.text.strip() if shop_tag else "不明"
            key = (name, shop)

            img_tag = row.select_one("td img")
            img_url = img_tag["src"] if img_tag else None
            url = name_tag["href"] if name_tag else None

            existing_data[key] = {
                "url": url,
                "image": img_url,
                "has_review": row.get("data-report") == "true"
            }
    return existing_data

#%%
# 最下部までスクロール後のHTMLを取得
from tqdm import tqdm
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin

data = {}
blocks = driver.find_elements(By.CSS_SELECTOR, "div.recommend-block")
base_dir = os.path.dirname(os.path.abspath(__file__))
calendar_path = os.path.join(base_dir, "..", "docs", "calendar_report.html")
output_path = os.path.join(base_dir, "..", "docs", "cityheaven_profiles.html")

existing_data = load_existing_data(calendar_path)


for block in tqdm(blocks, desc="スクレイピング中", unit="人"):
    try:
        # 名前と店名の取得
        name_tag = block.find_element(By.CSS_SELECTOR, ".recommend-block-top-name")
        name = name_tag.text.strip() if name_tag else "不明"

        shop_tag = block.find_element(By.CSS_SELECTOR, ".recommend-block-top-shop")
        shop = shop_tag.text.strip() if shop_tag else "不明"

        key = (name, shop)

        # スケジュールの取得
        schedule = {}
        schedule_items = block.find_elements(By.CSS_SELECTOR, "li.girls-work-item")
        for item in schedule_items:
            try:
                date = item.find_element(By.CSS_SELECTOR, ".girls-work-date").text.strip()
                status_elem = item.find_element(By.CSS_SELECTOR, ".girls-work-no-schedule")
                status = status_elem.text.strip()
            except:
                time_elem = item.find_element(By.CSS_SELECTOR, ".girls-work-schedule")
                status = time_elem.text.strip().replace("\n", " ")
            schedule[date] = status

        # 画像URL
        img_tag = block.find_element(By.CSS_SELECTOR, ".recommend-block-img img")
        img_url = urljoin("https://www.cityheaven.net", img_tag.get_attribute("src"))

        # URLは既存データから引用、なければタブを開いて取得
        if key in existing_data:
            girl_url = existing_data[key]["url"]
            img_url = existing_data[key]["image"] or img_url
        else:
            link_tag = block.find_element(By.CSS_SELECTOR, "a.recommend-block-top-link")
            href = link_tag.get_attribute("href")
            driver.execute_script("window.open(arguments[0], '_blank');", href)
            WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
            driver.switch_to.window(driver.window_handles[1])
            girl_url = driver.current_url
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

        # 情報保存
        data[name] = {
            "shop": shop,
            "url": girl_url,
            "schedule": schedule,
            "image": img_url,
            "has_review": existing_data.get(key, {}).get("has_review", False)
        }

    except Exception as e:
        print(f"エラー: {e}")
        continue
# %%
# 正規表現：時間形式（例：9:00 ~ 17:30）
time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}$")

# 今日の日付と曜日
today = datetime.now()
today_str = today.strftime("%m/%d").lstrip("0").replace("/0", "/")
today_label = f"{today_str}({['月','火','水','木','金','土','日'][today.weekday()]})"

# スケジュールを曜日付きに変換
current_year = today.year
for name, info in data.items():
    labeled_schedule = {}
    for date_str, status in info["schedule"].items():
        try:
            dt = datetime.strptime(f"{current_year}/{date_str}", "%Y/%m/%d")
            weekday = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            label = f"{date_str}({weekday})"
            labeled_schedule[label] = status.strip()
        except:
            continue
    data[name]["schedule"] = labeled_schedule

# 全日付ラベルを収集して昇順に並べる
all_labels = set()
for info in data.values():
    all_labels.update(info["schedule"].keys())

sorted_labels = sorted(
    all_labels,
    key=lambda x: datetime.strptime(f"{current_year}/{x[:x.index('(')]}", "%Y/%m/%d")
)
from generate_html import generate_html
# HTML生成
html = generate_html(data, sorted_labels, today_label)

base_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(base_dir, "..", "docs")
output_path = os.path.join(output_dir, "cityheaven_profiles.html")

with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)
# %%
