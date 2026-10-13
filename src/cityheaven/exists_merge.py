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
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[2]
PROFILE_DATA_PATH = ROOT_DIR / "data" / "cityheaven_profiles.json"
PROFILE_COLUMNS = ["名前", "URL", "店名", "画像URL"]

BASE_URL = "https://www.cityheaven.net"
# 高さが変わらなければこの時間で打ち切る（従来の固定 sleep(2) と同じ上限）
SCROLL_WAIT_TIMEOUT_MS = 2000
URL_RESOLVE_CONCURRENCY = 3
BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}

# recommend-block を1回の evaluate でまとめて取得する。
# テキストは従来の inner_text() と同じ innerText を使い、整形は Python 側で行う。
EXTRACT_BLOCKS_JS = """
() => Array.from(document.querySelectorAll("div.recommend-block")).map((block) => {
  const text = (root, selector) => {
    const el = root.querySelector(selector);
    return el ? el.innerText : null;
  };
  const img = block.querySelector(".recommend-block-img img");
  const link = block.querySelector("a.recommend-block-top-link");
  return {
    name: text(block, ".recommend-block-top-name"),
    shop: text(block, ".recommend-block-top-shop"),
    place: text(block, ".recommend-block-top-place"),
    items: Array.from(block.querySelectorAll("li.girls-work-item")).map((item) => ({
      date: text(item, ".girls-work-date"),
      no_schedule: text(item, ".girls-work-no-schedule"),
      schedule: text(item, ".girls-work-schedule"),
    })),
    has_img: img !== null,
    img_src: img ? img.getAttribute("src") : null,
    has_link: link !== null,
    href: link ? link.getAttribute("href") : null,
  };
})
"""


def _env_flag(name):
    """未設定なら None、それ以外は真偽値を返す。"""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() not in ("0", "false", "no", "off")


def _log_elapsed(label, start):
    print(f"[timing] {label}: {time.perf_counter() - start:.2f}s")


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
# Playwright ヘルパー
# -------------------------
async def safe_evaluate(page, script, arg=None):
    for _ in range(5):
        try:
            return await page.evaluate(script, arg)
        except Exception:
            # コンテキスト破壊 → ページが再ロードされた可能性
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(0.5)
    raise RuntimeError("evaluate failed after retries")


async def scroll_to_bottom(page, timeout_ms=SCROLL_WAIT_TIMEOUT_MS):
    """高さが変わったらすぐ次へ。timeout_ms 以内に変わらなければ終了。"""
    rounds = 0
    while True:
        prev_height = await safe_evaluate(page, "document.body.scrollHeight")

        # スマホ版は autopager が無いので、手動スクロール
        await page.mouse.wheel(0, 20000)
        rounds += 1

        try:
            await page.wait_for_function(
                "prev => document.body.scrollHeight !== prev",
                arg=prev_height,
                timeout=timeout_ms,
                polling=100,
            )
        except PlaywrightTimeoutError:
            break
        except Exception:
            # 再ロード等で待機が中断された場合は従来通り高さを比較する
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(0.5)
            new_height = await safe_evaluate(page, "document.body.scrollHeight")
            if new_height == prev_height:
                break
    return rounds


def _clean_text(value):
    return value.strip() if value is not None else ""


def build_profile_entries(raw_blocks, existing_data):
    """evaluate で取得した生データを従来と同じ形に整形する。

    URL 解決が必要なものは entry["full_url"] に解決前の URL を持つ（既存データ再利用時は None）。
    """
    entries = []
    for block in raw_blocks:
        try:
            name = _clean_text(block["name"])
            shop = _clean_text(block["shop"])
            place = _clean_text(block["place"])
            key = (name, shop)

            schedule = {}
            for item in block["items"]:
                date = _clean_text(item["date"])
                if item["no_schedule"] is not None:
                    status = item["no_schedule"].strip()
                elif item["schedule"] is not None:
                    status = item["schedule"].replace("\n", " ").strip()
                else:
                    status = ""
                schedule[date] = status

            if not block["has_img"]:
                raise ValueError("画像要素が見つかりません")
            img_url = urljoin(BASE_URL, block["img_src"])

            if key in existing_data:
                girl_url = existing_data[key]["url"]
                full_url = None
            else:
                if not block["has_link"]:
                    raise ValueError("リンク要素が見つかりません")
                girl_url = None
                full_url = urljoin(BASE_URL, block["href"])

            entries.append(
                {
                    "name": name,
                    "key": key,
                    "shop": shop,
                    "place": place,
                    "schedule": schedule,
                    "image": img_url,
                    "url": girl_url,
                    "full_url": full_url,
                }
            )
        except Exception as e:
            print("エラー:", e)
            continue
    return entries


async def resolve_redirect_urls(context, entries, concurrency=URL_RESOLVE_CONCURRENCY):
    """新規の人の転送先 URL を少数並列で解決する（失敗時は元 URL）。"""
    targets = list(dict.fromkeys(e["full_url"] for e in entries if e["full_url"] is not None))
    if not targets:
        return {}

    semaphore = asyncio.Semaphore(concurrency)
    names = {}
    for e in entries:
        if e["full_url"] is not None:
            names.setdefault(e["full_url"], e["name"])

    async def resolve(full_url):
        async with semaphore:
            new_page = await context.new_page()
            try:
                # リダイレクト先URLが分かれば十分なので load 完了までは待たない
                await new_page.goto(full_url, wait_until="commit", timeout=15000)
                return new_page.url
            except Exception as e:
                print(f"URL解決に失敗、元URLを使用: {names[full_url]} → {type(e).__name__}")
                return full_url
            finally:
                await new_page.close()

    resolved = await asyncio.gather(*(resolve(url) for url in targets))
    return dict(zip(targets, resolved))


async def extract_profile_data(page, context, existing_data):
    """表示中ページの recommend-block から data 辞書（日付ラベル付け前）を作る。"""
    start = time.perf_counter()
    raw_blocks = await safe_evaluate(page, EXTRACT_BLOCKS_JS)
    entries = build_profile_entries(raw_blocks, existing_data)
    _log_elapsed(f"ブロック抽出 ({len(raw_blocks)}件)", start)

    start = time.perf_counter()
    resolved = await resolve_redirect_urls(context, entries)
    _log_elapsed(f"URL解決 ({len(resolved)}件)", start)

    data = {}
    for e in entries:
        girl_url = e["url"] if e["full_url"] is None else resolved[e["full_url"]]
        data[e["name"]] = {
            # generate_html は 1列目に shop を置く → 店名をここに入れる
            "shop": e["shop"] + ("<br>" + e["place"] if e["place"] else ""),

            # generate_html は 2列目に url を置く → 名前リンクに使われる
            "url": girl_url,

            "schedule": e["schedule"],
            "image": e["image"],
            "has_review": existing_data.get(e["key"], {}).get("has_review", False)
        }
    return data, len(raw_blocks)


async def _block_heavy_resources(route):
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.fallback()


def label_schedules(data, today=None):
    """スケジュールの日付に曜日ラベルを付け、全日付ラベルを日付順で返す。"""
    today = today or datetime.now()
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

    return sorted(all_labels, key=parse_date)


# -------------------------
# Playwright Async 実行
# -------------------------
async def main():
    total_start = time.perf_counter()
    # 未設定なら Playwright の既定（headless）。CITYHEAVEN_HEADLESS=0 で画面表示
    headless = _env_flag("CITYHEAVEN_HEADLESS")
    block_resources = _env_flag("CITYHEAVEN_BLOCK_RESOURCES") is not False
    print(
        f"[config] headless={'default' if headless is None else headless}, "
        f"block_resources={block_resources}"
    )

    async with async_playwright() as p:
        launch_kwargs = {"args": ["--disable-blink-features=AutomationControlled"]}
        if headless is not None:
            launch_kwargs["headless"] = headless
        browser = await p.chromium.launch(**launch_kwargs)

        # 1. 最初からスマホ版 context を作成
        sp_context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
        )
        if block_resources:
            # 画像・フォント・動画は読み込まない（img の src 属性は DOM から取得できる）
            await sp_context.route("**/*", _block_heavy_resources)

        page = await sp_context.new_page()

        start = time.perf_counter()
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
        _log_elapsed("ページ読み込み・ログイン", start)

        # ここから先はスマホ版で scraping 開始
        start = time.perf_counter()
        rounds = await scroll_to_bottom(page)
        _log_elapsed(f"無限スクロール ({rounds}回)", start)

        # -------------------------
        # recommend-block の解析
        # -------------------------
        docs_dir = ROOT_DIR / "docs"
        calendar_path = docs_dir / "calendar_report.html"

        existing_data = load_existing_data(calendar_path)

        data, block_count = await extract_profile_data(page, sp_context, existing_data)
        print(
            f"[count] blocks={block_count}, data={len(data)} "
            f"(block_resources={block_resources})"
        )

        # -------------------------
        # 日付ラベル付け
        # -------------------------
        sorted_labels = label_schedules(data)

        output_path = write_profiles_json(data, sorted_labels)
        print(f"プロフィール取得結果を保存: {output_path} ({len(data)}人)")
        await browser.close()
    _log_elapsed("CityHeaven スクレイピング合計", total_start)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


# %%
