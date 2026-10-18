"""CityHeaven のプロフィールページから写真 URL と店舗種別を取得し、data/ns_senmon_profile_image_cache.json にキャッシュする。

キャッシュ: cityheaven_url -> {"checked_at": ISO8601, "status": "ok"|"not_found"|"no_image"|"error", "image_url": str,
                              "shop_kind": "ソープ(高級/金津園)" など, "price_class": "高級"|"スタンダード"|"激安"|""}
  shop_kind / price_class は `.shopname` の h1 直後のテキスト「業種(価格帯/地域)」から取る。
  ページを読めたが `.shopname` が無い場合はキーを持たない（種別不明）。
  - ok        : 写真URLを取得済み。IMAGE_REFRESH_DAYS 経過後に取り直す。
  - not_found : HTTP 404/410。掲載終了とみなし再取得しない。
  - no_image / error : 写真なし・通信失敗。RETRY_DAYS 経過後に再試行する。
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
SHOP_KIND_RE = re.compile(r"^\s*([^\s(（]+)\s*[(（]([^)）]*)[)）]")
PRICE_WORDS = ("高級", "スタンダード", "激安", "格安", "大衆")


def parse_shop_kind(page_html):
    """`<div class="shopname"><h1>店名</h1> ソープ(高級/金津園)<br>...` から (業種表記, 価格帯) を返す。

    `.shopname` が無ければ None。価格帯の表記が無い場合は空文字。
    """
    shopname = BeautifulSoup(page_html, "html.parser").select_one(".shopname")
    heading = shopname.find("h1") if shopname else None
    if not heading:
        return None
    text = ""
    for node in heading.next_siblings:
        if getattr(node, "name", None) == "br":
            break
        text += node.get_text() if hasattr(node, "get_text") else str(node)
    match = SHOP_KIND_RE.match(text)
    if not match:
        return None
    kind = match.group(0).strip()
    parts = re.split(r"[/／]", match.group(2))
    price = next((p.strip() for p in parts if any(w in p for w in PRICE_WORDS)), "")
    return kind, price


def classify_price_class(entry):
    """Return luxury, other, or unknown based only on an explicit cached price label."""
    if not entry or "price_class" not in entry:
        return ""
    price_class = str(entry.get("price_class", "")).strip()
    if "高級" in price_class:
        return "luxury"
    if any(label in price_class for label in PRICE_WORDS if label != "高級"):
        return "other"
    return ""


def normalize_profile_url(url):
    """Normalize a CityHeaven profile URL for cache lookup."""
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(str(url or ""))
    if not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    match = re.search(r"(/girlid-\d+)$", path)
    if match:
        path = path[: match.start()] + match.group(1)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path + "/", "", ""))


def _now():
    return datetime.now(timezone.utc)


def _needs_fetch(entry, now):
    if not entry:
        return True
    status = entry.get("status")
    if status == "not_found":
        return False
    if status in ("ok", "no_image") and "shop_checked" not in entry:
        return True  # 店舗種別を持たない古いエントリは取り直す
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
    if normalize_profile_url(response.url).split("://")[-1] != normalize_profile_url(url).split("://")[-1]:
        message = "別ページに転送されました（認証・掲載状況を確認）"
        print(f"プロフィール画像取得失敗 ({url}): {message}")
        return {"status": "error", "image_url": "", "error": message}
    # Content-Type に charset が無いと requests は ISO-8859-1 とみなすため、UTF-8 で読む
    encoding = response.encoding if "charset" in response.headers.get("content-type", "").lower() else "utf-8"
    page_html = response.content[:PROFILE_IMAGE_HTML_LIMIT].decode(encoding, errors="replace")
    image_url = extract_profile_image_url(page_html, response.url, name)
    result = {"status": "ok" if image_url else "no_image", "image_url": image_url or "", "shop_checked": True}
    parsed = parse_shop_kind(page_html)
    if parsed:
        result["shop_kind"], result["price_class"] = parsed
    return result


def load_cache(path=CACHE_PATH):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def fetch_profile_images(people, path=CACHE_PATH, time_budget=TIME_BUDGET_SECONDS):
    """people: {cityheaven_url: 名前}。{cityheaven_url: キャッシュエントリ} を返す（キャッシュにあるものだけ）。

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
            if result["status"] == "error":
                # 取り直しに失敗したときは、前回までに分かっている写真・店舗種別を使い続ける
                result = {**{k: v for k, v in old.items() if k != "checked_at"}, **result,
                          "image_url": old.get("image_url", "")}
            cache[url] = {"checked_at": checked_at, **result}
        found = sum(1 for f in done if cache[futures[f]].get("image_url"))
        print(f"プロフィール画像: {found} / {len(pending)}件取得" + (f"（時間切れ {len(not_done)}件は次回）" if not_done else ""))
        try:
            path.write_text(json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as error:
            print(f"プロフィール画像キャッシュの保存に失敗: {error}")
    return {url: cache[url] for url in people if cache.get(url)}
