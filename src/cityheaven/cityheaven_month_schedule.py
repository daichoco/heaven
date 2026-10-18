import asyncio
import os
import re
import ast
import sys
import pandas as pd
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from web_common import BACK_TO_TOP, HEAD_COMMON
from html_format import write_html


async def safe_evaluate(page, script):
    for _ in range(5):
        try:
            return await page.evaluate(script)
        except Exception as e:
            if "Execution context was destroyed" not in str(e):
                raise
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(0.5)
    raise RuntimeError("evaluate failed after retries")


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
        await page.goto(start_url, wait_until="domcontentloaded")

        # --- ログイン ---
        form = await page.query_selector("form[name='login_form']")
        await page.fill("input[name='user']", os.environ["CITYHEAVEN_USER"])
        await page.fill("input[name='pass']", os.environ["CITYHEAVEN_PASS"])
        await form.evaluate("form => form.submit()")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)

        # --- 無限スクロール ---
        while True:
            prev_height = await safe_evaluate(page, "document.body.scrollHeight")
            await page.mouse.wheel(0, 20000)
            await asyncio.sleep(2)
            new_height = await safe_evaluate(page, "document.body.scrollHeight")
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


REPORT_LINKS = [
    ("weekly_schedule.html", "3レポート横断・翌週出勤予定"),
    ("reservation_difficulty.html", "予約困難な人物"),
    ("calendar_report.html", "カレンダー形式レポート"),
    ("tokai_final_report.html", "最終レポート"),
    ("okinilove_remove_unknown.html", "オキニラブレポート"),
    ("ns_senmon_report.html", "NS専門レポート"),
]


def render_index_html(weekend_map):
    report_items = "".join(
        f'<li><a href="{href}">{label}</a></li>' for href, label in REPORT_LINKS
    )
    day_cards = ""
    for date, members in weekend_map.items():
        day_cards += f'<div class="day-card"><h2>{escape(date)}</h2>'
        if not members:
            day_cards += "<p>出勤なし</p>"
        for m in members:
            day_cards += f"""
<div class="member">
<img src="{escape(m['image'], quote=True)}" alt="{escape(m['name'], quote=True)}" width="50" height="50" loading="lazy" decoding="async">
<div>
    <div class="member-name"><a href="{escape(m['url'], quote=True)}" target="_blank" rel="noopener">{escape(m['name'])}</a>（{escape(m['shop'])}）</div>
    <div class="status">{escape(m['status'])}</div>
</div>
</div>
"""
        day_cards += "</div>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
{HEAD_COMMON}
<title>土日出勤カレンダー</title>
<style>
:root {{ --page-pad: 20px; --bg: #f9f9f9; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #15181b; }} }}
body {{ font-family: sans-serif; max-width: 960px; line-height: 1.55; }}
h1 {{ text-align: center; margin: 0 0 24px; font-size: 1.5rem; }}
.calendar-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
.day-card {{ background: var(--surface-alt); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
.day-card h2 {{ margin: 0 0 10px; font-size: 1.1em; border-bottom: 1px solid var(--border-soft); padding-bottom: 4px; }}
.member {{ display: flex; align-items: center; margin-bottom: 10px; }}
.member img {{ width: 50px; height: 50px; object-fit: cover; border-radius: 4px; margin-right: 10px; }}
.member-name {{ font-weight: bold; }}
.status {{ font-size: 0.9em; color: var(--muted); }}
.reports {{ margin: 0 0 24px; padding-left: 1.2em; }}
.reports a {{ display: inline-flex; align-items: center; min-height: 44px; }}
@media (max-width: 600px) {{
    :root {{ --page-pad: 12px; }}
    h1 {{ text-align: left; font-size: 1.25rem; margin-bottom: 18px; }}
    .calendar-grid {{ grid-template-columns: 1fr; gap: 10px; }}
    .day-card {{ padding: 10px; }}
    .member {{ align-items: flex-start; }}
    .member img {{ width: 44px; height: 44px; flex: none; }}
    .member-name {{ overflow-wrap: anywhere; }}
}}
</style>
</head>
<body>
<h1>公開ページ</h1>
<section class="report-links">
<h2>📄 レポート一覧</h2>
<ul class="reports">{report_items}</ul>
</section>

<h1>土日出勤カレンダー</h1>
<div class="calendar-grid">
{day_cards}
</div>
{BACK_TO_TOP}
</body>
</html>
"""


def write_index_html(weekend_map, path=None):
    write_html(path or ROOT_DIR / "docs" / "index.html", render_index_html(weekend_map))


# --- 実行 ---
if __name__ == "__main__":
    weekend_map = asyncio.run(scrape_cityheaven())
    print("weekend_map:", weekend_map)
    write_index_html(weekend_map)
