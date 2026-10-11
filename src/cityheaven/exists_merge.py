#%%
import asyncio
import json
import time
import re
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
PROFILE_DATA_PATH = ROOT_DIR / "data" / "cityheaven_profiles.json"
PROFILE_COLUMNS = ["名前", "URL", "店名", "画像URL"]


def write_profiles_json(data, date_labels, output_path=PROFILE_DATA_PATH):
    columns = PROFILE_COLUMNS + list(date_labels)
    records = []
    for name, info in data.items():
        records.append(
            {
                "名前": name,
                "URL": info["url"],
                "店名": info["shop"].replace("<br>", "\n"),
                "画像URL": info["image"],
                **{label: info["schedule"].get(label, "") for label in date_labels},
            }
        )

    payload = {"columns": columns, "records": records}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
#%%
# -------------------------
# 既存データ読み込み
# -------------------------
def load_existing_data(calendar_path):
    existing_data = {}
    if os.path.exists(calendar_path):
        with open(calendar_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        rows = soup.select("tr[data-report]")
        for row in rows:
            name_tag = row.select_one("td.shop-cell a")
            name = name_tag.text.strip() if name_tag else "不明"

            shop_info_tags = row.select("td.shop-cell .shop-info")
            shop = shop_info_tags[0].text.strip() if shop_info_tags else "不明"
            shop = " ".join(shop.split())

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


# -------------------------
# Playwright Async 実行
# -------------------------
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            # headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        # 1. 最初からスマホ版 context を作成
        sp_context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
        )

        page = await sp_context.new_page()

        start_url = "https://www.cityheaven.net/tt/community/ABMyAlbumShukkin/?lo=1"
        response = await page.goto(
            start_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        if response:
            print(f"CityHeaven response: {response.status} {response.url}")
            for name in ("server", "via", "cf-ray", "x-cache", "content-type", "retry-after"):
                value = response.headers.get(name)
                if value:
                    print(f"{name}: {value}")

            if response.status >= 400:
                body = await response.text()
                print("Response body:", " ".join(body.split())[:1500])
        if response and response.status >= 400:
            raise RuntimeError(f"Cityheaven page request failed: HTTP {response.status}")

        # 正しいログインフォーム（スマホ版は name="login_form"）
        form = await page.query_selector("form[name='login_form']")
        if form is None:
            raise RuntimeError("Cityheaven login form was not found")

        # 入力
        await page.fill("input[name='user']", os.environ["CITYHEAVEN_USER"])
        await page.fill("input[name='pass']", os.environ["CITYHEAVEN_PASS"])

        # onclick バグを無視して直接 submit
        await form.evaluate("form => form.submit()")

        # ログイン完了待ち
        await page.wait_for_load_state("domcontentloaded")

        # ここから先はスマホ版で scraping 開始

        # -------------------------
        # autopager 無限スクロール（堅牢版）
        # -------------------------
        async def safe_evaluate(page, script):
            for _ in range(5):
                try:
                    return await page.evaluate(script)
                except Exception:
                    # コンテキスト破壊 → ページが再ロードされた可能性
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(0.5)
            raise RuntimeError("evaluate failed after retries")

        while True:
            prev_height = await safe_evaluate(page, "document.body.scrollHeight")

            # スマホ版は autopager が無いので、手動スクロール
            await page.mouse.wheel(0, 20000)

            await asyncio.sleep(2)

            new_height = await safe_evaluate(page, "document.body.scrollHeight")

            if new_height == prev_height:
                break

        # -------------------------
        # recommend-block の解析
        # -------------------------
        docs_dir = ROOT_DIR / "docs"
        calendar_path = docs_dir / "calendar_report.html"

        existing_data = load_existing_data(calendar_path)

        blocks = await page.query_selector_all("div.recommend-block")
        data = {}

        for block in blocks:
            try:
                name_el = await block.query_selector(".recommend-block-top-name")
                name = (await name_el.inner_text()).strip() if name_el else ""

                shop_el = await block.query_selector(".recommend-block-top-shop")
                shop = (await shop_el.inner_text()).strip() if shop_el else ""

                place_el = await block.query_selector(".recommend-block-top-place")
                place = (await place_el.inner_text()).strip() if place_el else ""

                key = (name, shop)

                # スケジュール
                schedule = {}
                items = await block.query_selector_all("li.girls-work-item")

                for item in items:
                    # 日付
                    date_el = await item.query_selector(".girls-work-date")
                    date = (await date_el.inner_text()).strip() if date_el else ""

                    # ステータス
                    no_el = await item.query_selector(".girls-work-no-schedule")
                    if no_el:
                        status = (await no_el.inner_text()).strip()
                    else:
                        sched_el = await item.query_selector(".girls-work-schedule")
                        if sched_el:
                            status = (await sched_el.inner_text()).replace("\n", " ").strip()
                        else:
                            status = ""

                    schedule[date] = status

                # 画像
                img = await block.query_selector(".recommend-block-img img")
                img_url = urljoin("https://www.cityheaven.net", await img.get_attribute("src"))

                # URL
                if key in existing_data:
                    girl_url = existing_data[key]["url"]
                else:
                    link = await block.query_selector("a.recommend-block-top-link")
                    href = await link.get_attribute("href")
                    full_url = urljoin("https://www.cityheaven.net", href)

                    # リダイレクト先URLが分かれば十分なので load 完了までは待たない
                    new_page = await sp_context.new_page()
                    girl_url = full_url
                    try:
                        await new_page.goto(full_url, wait_until="commit", timeout=15000)
                        girl_url = new_page.url
                    except Exception as e:
                        print(f"URL解決に失敗、元URLを使用: {name} → {type(e).__name__}")
                    finally:
                        await new_page.close()

                data[name] = {
                    # generate_html は 1列目に shop を置く → 店名をここに入れる
                    "shop": shop + ("<br>" + place if place else ""),

                    # generate_html は 2列目に url を置く → 名前リンクに使われる
                    "url": girl_url,

                    "schedule": schedule,
                    "image": img_url,
                    "has_review": existing_data.get(key, {}).get("has_review", False)
                }

            except Exception as e:
                print("エラー:", e)
                continue

        # -------------------------
        # 日付ラベル付け
        # -------------------------
        today = datetime.now()
        today_str = today.strftime("%m/%d").lstrip("0").replace("/0", "/")
        today_label = f"{today_str}({['月','火','水','木','金','土','日'][today.weekday()]})"

        current_year = today.year

        for name, info in data.items():
            labeled = {}
            for date_str, status in info["schedule"].items():
                try:
                    month, day = map(int, date_str.split('/'))
                    year = current_year + (1 if month < today.month else 0)
                    dt = datetime(year, month, day)
                    weekday = ["月","火","水","木","金","土","日"][dt.weekday()]
                    labeled[f"{date_str}({weekday})"] = status
                except:
                    continue
            info["schedule"] = labeled

        # 全日付ソート
        all_labels = set()
        for info in data.values():
            all_labels.update(info["schedule"].keys())

        def parse_date(label):
            date_str = label.split("(")[0]
            m, d = map(int, date_str.split("/"))
            y = current_year + (1 if m < today.month else 0)
            return datetime(y, m, d)

        sorted_labels = sorted(all_labels, key=parse_date)

        output_path = write_profiles_json(data, sorted_labels)
        print(f"プロフィール取得結果を保存: {output_path} ({len(data)}人)")
        await browser.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


# %%
