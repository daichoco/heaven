import asyncio
import os
import re
import ast
import pandas as pd
from datetime import datetime
from urllib.parse import urljoin
from playwright.async_api import async_playwright

async def scrape_cityheaven():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )

        # --- スマホ版 context ---
        context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 "
                       "Mobile/15E148 Safari/604.1"
        )

        page = await context.new_page()
        start_url = "https://www.cityheaven.net/tt/community/ABMyAlbumShukkin/?lo=1"
        await page.goto(start_url)

        # --- ログイン ---
        form = await page.query_selector("form[name='login_form']")
        await page.fill("input[name='user']", os.environ["CITYHEAVEN_USER"])
        await page.fill("input[name='pass']", os.environ["CITYHEAVEN_PASS"])
        await form.evaluate("form => form.submit()")
        await page.wait_for_load_state("networkidle")

        # --- 無限スクロール ---
        while True:
            prev_height = await page.evaluate("document.body.scrollHeight")
            await page.mouse.wheel(0, 20000)
            await asyncio.sleep(2)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break

        # --- recommend-block 取得 ---
        blocks = await page.query_selector_all("div.recommend-block")
        data = {}

        for block in blocks:
            try:
                # 名前
                name_el = await block.query_selector(".recommend-block-top-name")
                name = (await name_el.inner_text()).strip()

                # 店
                shop_el = await block.query_selector(".recommend-block-top-shop")
                shop = (await shop_el.inner_text()).strip()

                # 画像
                img = await block.query_selector(".recommend-block-img img")
                img_url = urljoin("https://www.cityheaven.net", await img.get_attribute("src"))

                # プロフィール URL
                link = await block.query_selector("a.recommend-block-top-link")
                href = await link.get_attribute("href")
                full_url = urljoin("https://www.cityheaven.net", href)

                # 新規タブで開く
                new_page = await context.new_page()
                await new_page.goto(full_url)
                girl_url = new_page.url

                # 出勤カレンダー
                calendar = {}
                try:
                    syukin = await new_page.wait_for_selector("#syukin_month", timeout=5000)
                    rows = await syukin.query_selector_all("div.girlitem_waku")
                    for row in rows:
                        date_el = await row.query_selector("span.girlitem_waku_left")
                        status_el = await row.query_selector("span.girlitem_waku_right")
                        date = (await date_el.inner_text()).strip()
                        status = (await status_el.inner_text()).strip()
                        calendar[date] = status
                except:
                    pass

                await new_page.close()

                # --- Selenium と同じ data 構造 ---
                data[name] = {
                    "shop": shop,
                    "url": girl_url,
                    "image": img_url,
                    "calendar": calendar
                }

            except Exception as e:
                print("エラー:", e)
                continue

        # --- pandas DataFrame 化 ---
        calendar_df = pd.DataFrame.from_dict(data, orient="index").reset_index()
        calendar_df = calendar_df.rename(columns={"index": "name"})

        # --- calendar_dict を dict に統一 ---
        calendar_df["calendar_dict"] = calendar_df["calendar"].apply(
            lambda x: x if isinstance(x, dict) else ast.literal_eval(x)
        )

        # --- 全日付を集める ---
        all_dates = set()
        for cal in calendar_df["calendar_dict"]:
            all_dates.update(cal.keys())

        # --- 日付パース ---
        def parse_date(d):
            try:
                md = d.split("(")[0]
                m, day = map(int, md.split("/"))
                return datetime(datetime.now().year, m, day)
            except:
                return None

        # --- 土日判定 ---
        def is_weekend(date_str):
            dt = parse_date(date_str)
            if not dt:
                return False
            return dt.weekday() >= 5

        # --- 土日だけ抽出 ---
        weekend_dates = sorted([d for d in all_dates if is_weekend(d)], key=parse_date)
        weekend_map = {d: [] for d in weekend_dates}

        # --- 時刻形式 ---
        time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*[-~]\s*\d{1,2}:\d{2}$")

        # --- weekend_map 格納（HTML と完全互換） ---
        for _, row in calendar_df.iterrows():
            name = row["name"]
            shop = row["shop"]
            image = row["image"]
            url = row["url"]
            cal = row["calendar_dict"]

            for date in weekend_dates:
                if date not in cal:
                    continue

                status = cal[date]

                if not time_pattern.match(status):
                    continue

                weekend_map[date].append({
                    "name": name,
                    "shop": shop,
                    "image": image,
                    "status": status,
                    "url": url
                })

        return weekend_map


# --- 実行 ---
if __name__ == "__main__":
    weekend_map = asyncio.run(scrape_cityheaven())
    print("weekend_map:", weekend_map)
    html = """<!DOCTYPE html>
    <html lang="ja">
    <head>
    <meta charset="UTF-8">
    <title>土日出勤カレンダー</title>
    <style>
    body { font-family: sans-serif; padding: 20px; background: #f9f9f9; max-width: 960px; margin: auto; }
    h1 { text-align: center; margin-bottom: 30px; }
    .calendar-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .day-card { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 12px; }
    .day-card h2 { margin: 0 0 10px; font-size: 1.1em; border-bottom: 1px solid #eee; padding-bottom: 4px; }
    .member { display: flex; align-items: center; margin-bottom: 10px; }
    .member img { width: 50px; height: 50px; object-fit: cover; border-radius: 4px; margin-right: 10px; }
    .member-name { font-weight: bold; }
    .status { font-size: 0.9em; color: #555; }
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

    <h1>土日出勤カレンダー</h1>
    <div class="calendar-grid">
    """


    for date, members in weekend_map.items():
        html += f'<div class="day-card">'
        html += f'<h2>{date}</h2>'

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
    file_path = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html)

# %%
