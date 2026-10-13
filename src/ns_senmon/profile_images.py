"""CityHeaven のプロフィール写真 URL を取得し、data/ns_senmon_profile_image_cache.json にキャッシュする。

キャッシュ: cityheaven_url -> {"checked_at": ISO8601, "status": "ok"|"not_found"|"no_image"|"error", "image_url": str}
  - ok        : 写真URLを取得済み。IMAGE_REFRESH_DAYS 経過後に取り直す。
  - not_found : HTTP 404/410。掲載終了とみなし再取得しない。
  - no_image / error : 写真なし・通信失敗。RETRY_DAYS 経過後に再試行する。
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from okinilove.render_people_summary import HEADERS, PROFILE_IMAGE_HTML_LIMIT, extract_profile_image_url

CACHE_PATH = ROOT_DIR / "data" / "ns_senmon_profile_image_cache.json"
RETRY_DAYS = 7
IMAGE_REFRESH_DAYS = 90
WORKERS = 4
TIME_BUDGET_SECONDS = 120
REQUEST_HEADERS = {**HEADERS, "Cookie": "mensesCookie=1"}


def _now():
    return datetime.now(timezone.utc)


def _needs_fetch(entry, now):
    if not entry:
        return True
    status = entry.get("status")
    if status == "not_found":
        return False
    try:
        checked = datetime.fromisoformat(entry["checked_at"])
    except (KeyError, TypeError, ValueError):
        return True
    days = IMAGE_REFRESH_DAYS if status == "ok" else RETRY_DAYS
    return now - checked >= timedelta(days=days)


def _fetch(url, name):
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=(5, 15))
    except requests.RequestException as error:
        print(f"プロフィール画像取得失敗 ({url}): {error}")
        return {"status": "error", "image_url": ""}
    if response.status_code in (404, 410):
        return {"status": "not_found", "image_url": ""}
    if not 200 <= response.status_code < 400:
        print(f"プロフィール画像取得失敗 ({url}): HTTP {response.status_code}")
        return {"status": "error", "image_url": ""}
    page_html = response.content[:PROFILE_IMAGE_HTML_LIMIT].decode(
        response.encoding or "utf-8", errors="replace"
    )
    image_url = extract_profile_image_url(page_html, response.url, name)
    return {"status": "ok", "image_url": image_url} if image_url else {"status": "no_image", "image_url": ""}


def load_cache(path=CACHE_PATH):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def fetch_profile_images(people, path=CACHE_PATH, time_budget=TIME_BUDGET_SECONDS):
    """people: {cityheaven_url: 名前}。{cityheaven_url: 画像URL} を返す（取得できたものだけ）。

    失敗しても例外は出さず、時間切れの分は次回に回す。古いキャッシュの画像URLは取り直しに失敗しても使い続ける。
    """
    cache = load_cache(path)
    now = _now()
    pending = {url: name for url, name in people.items() if url and _needs_fetch(cache.get(url), now)}
    if pending:
        deadline = time.monotonic() + time_budget
        executor = ThreadPoolExecutor(max_workers=WORKERS)
        futures = {executor.submit(_fetch, url, name): url for url, name in pending.items()}
        done, not_done = wait(futures, timeout=max(0, deadline - time.monotonic()))
        for future in not_done:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        checked_at = _now().isoformat()
        for future in done:
            url = futures[future]
            try:
                result = future.result()
            except Exception as error:  # 画像取得の失敗でHTML生成を止めない
                print(f"プロフィール画像取得失敗 ({url}): {error}")
                result = {"status": "error", "image_url": ""}
            old = cache.get(url) or {}
            if result["status"] == "error" and old.get("image_url"):
                result["image_url"] = old["image_url"]
            cache[url] = {"checked_at": checked_at, **result}
        found = sum(1 for f in done if cache[futures[f]].get("image_url"))
        print(f"プロフィール画像: {found} / {len(pending)}件取得" + (f"（時間切れ {len(not_done)}件は次回）" if not_done else ""))
        try:
            path.write_text(json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as error:
            print(f"プロフィール画像キャッシュの保存に失敗: {error}")
    return {url: cache[url]["image_url"] for url in people if (cache.get(url) or {}).get("image_url")}
