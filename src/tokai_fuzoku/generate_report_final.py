"""東海風俗レポートHTMLを生成する。

既定（オンライン）: プロフィール掲載確認・プロフィール画像取得を行い、詳細未保存の旧記事だけ本文を取得する。
--offline: ネットに接続せず、JSONLに保存済みの詳細と収集時のプロフィール確認結果だけで生成する。
"""
import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl_records
from html_format import write_html
from html_render import generate_html, PAGE_TEMPLATE
from report_store import (
    COMMENT_COLUMNS,
    DETAIL_COLUMNS,
    PLAY_TIME_COLUMNS,
    REPORTS_JSONL,
    SCORE_COLUMNS,
    is_missing,
    latest_profile_status_by_person,
    normalize_profile_url,
    parse_report_details,
    person_key,
)
from profile_signals import classify_profile_page, decode_html
from wait_for_name_parts import name_in_text
from shop_prices import refresh_saved_prices

INPUT_JSONL = REPORTS_JSONL
OUTPUT_HTML = ROOT_DIR / "docs" / "tokai_final_report.html"
LOGIN_URL = "https://m.fuzoku-tokai.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}
PROFILE_HTML_LIMIT = 512 * 1024
AQUA_API_ROOT = "https://www.k-aqua-jpn.com/shop/api"
AQUA_ROSTER_API = f"{AQUA_API_ROOT}/parts/pc/25/1"

DETAIL_LABELS = {**COMMENT_COLUMNS, **SCORE_COLUMNS, **PLAY_TIME_COLUMNS}


def load_input_data():
    """記事単位のJSONLを読む。旧データ（詳細列・確認結果なし）も読めるよう欠損列は空で補う。"""
    if not INPUT_JSONL.exists():
        raise FileNotFoundError(f"JSON Linesデータがありません: {INPUT_JSONL}")
    records = read_jsonl_records(INPUT_JSONL)
    df = pd.DataFrame(records)
    required_columns = {"delete?", "predict_age", "url", "shop_name", "name", "girl_url"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"入力JSONLに必要な列がありません: {missing}")
    for column in [*DETAIL_COLUMNS, "profile_status", "profile_checked_at", "fetched_at"]:
        if column not in df.columns:
            df[column] = None

    df = df.loc[df["delete?"].eq(0)].copy()
    # 収集時の確認で掲載終了と確定した人物は、その人物の全レポートを除く（最新の確定結果を採用）
    statuses = latest_profile_status_by_person(records)
    unavailable = df.apply(lambda row: statuses.get(person_key(row)) == "unavailable", axis=1)
    if len(df) and unavailable.any():
        tqdm.write(f"収集時に掲載終了と確認された人物のレポートを除外: {int(unavailable.sum())}件")
        df = df.loc[~unavailable].copy()
    ages = pd.to_numeric(
        df["predict_age"].astype("string").str.extract(r"(\d+)")[0],
        errors="coerce",
    )
    return df.loc[ages.le(30)].copy().reset_index(drop=True)



def extract_profile_image_url(page_html, page_url, girl_name=""):
    soup = BeautifulSoup(page_html, "html.parser")
    candidates = []
    rejected_terms = (
        "favicon",
        "/icon/",
        "logo",
        "banner",
        "noimage",
        "no_image",
        "placeholder",
        "profile_circle",
    )

    for meta in soup.find_all("meta"):
        if meta.get("property") == "og:image" or meta.get("name", "").lower() in {
            "twitter:image",
            "twitter:image:src",
        }:
            image_url = meta.get("content")
            if image_url:
                candidates.append((100, image_url, ""))

    normalized_name = re.sub(r"\s+", "", girl_name).casefold()
    for image in soup.find_all("img"):
        if image.find_parent(id="mitayoguid") or image.find_parent(class_="mitayoimg"):
            continue
        image_url = (
            image.get("src") or image.get("data-src") or image.get("data-original")
            or image.get("data-echo")
        )
        if not image_url and image.get("srcset"):
            image_url = image["srcset"].split(",", 1)[0].strip().split()[0]
        if not image_url:
            continue

        alt = image.get("alt", "")
        source = urljoin(page_url, image_url.strip())
        lowered = source.lower()
        if any(term in lowered for term in rejected_terms):
            continue

        score = 0
        if "/photos/" in lowered:
            score += 80
        if "/img/girls/" in lowered:
            score += 45
        if "grpb" in lowered:
            score += 60
        elif "grpe" in lowered:
            score += 50
        elif "grdr" in lowered:
            score += 25
        if any(term in lowered for term in ("cast", "profile", "girl")):
            score += 25
        if normalized_name and normalized_name in re.sub(r"\s+", "", alt).casefold():
            score += 50
        width = image.get("width")
        height = image.get("height")
        if width and height and width.isdigit() and height.isdigit():
            if int(width) >= 120 and int(height) >= 120:
                score += 10
        candidates.append((score, source, alt))

    for _, candidate, _ in sorted(candidates, key=lambda item: item[0], reverse=True):
        resolved = urljoin(page_url, candidate.strip())
        if urlparse(resolved).scheme in {"http", "https"}:
            if any(term in resolved.lower() for term in rejected_terms):
                continue
            return resolved
    return None


def _api_response_markup(response):
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return payload if isinstance(payload, str) else ""


def _aqua_roster_status(session):
    headers = {**HEADERS, "Referer": "https://www.k-aqua-jpn.com/girls"}
    try:
        response = session.get(AQUA_ROSTER_API, headers=headers, timeout=15)
        if not 200 <= response.status_code < 400:
            return False, set()
        markup = _api_response_markup(response)
        profile_ids = list(dict.fromkeys(re.findall(r"girls_detail\?id=(\d+)", markup)))
        if not profile_ids:
            return False, set()

        control_response = session.get(
            f"{AQUA_API_ROOT}/girls_detail/pc/{profile_ids[0]}",
            headers=headers,
            timeout=15,
        )
        if not 200 <= control_response.status_code < 400:
            return False, set()
        control_soup = BeautifulSoup(_api_response_markup(control_response), "html.parser")
        return bool(control_soup.select_one("section.girlprofile")), set(profile_ids)
    except requests.RequestException as exc:
        tqdm.write(f"aQua現役プロフィール確認失敗のため終了判定を保留: {exc}")
        return False, set()


def is_unavailable_aqua_profile(url, session, roster_state):
    parsed = urlparse(url)
    if parsed.netloc.lower() != "www.k-aqua-jpn.com" or parsed.path != "/girls_detail":
        return False
    profile_ids = parse_qs(parsed.query).get("id", [])
    if not profile_ids or not profile_ids[0].isdigit():
        return False

    profile_id = profile_ids[0]
    headers = {**HEADERS, "Referer": url}
    try:
        response = session.get(
            f"{AQUA_API_ROOT}/girls_detail/pc/{profile_id}",
            headers=headers,
            timeout=15,
        )
        if response.status_code in {404, 410}:
            return True
        if not 200 <= response.status_code < 400:
            return False

        profile_soup = BeautifulSoup(_api_response_markup(response), "html.parser")
        if profile_soup.select_one("section.girlprofile"):
            return False
        message = profile_soup.select_one(".widget-area.maintenance .message")
        if not message or "データ取得ができませんでした" not in message.get_text(" ", strip=True):
            return False

        if "status" not in roster_state:
            roster_state["status"], roster_state["profile_ids"] = _aqua_roster_status(session)
        return roster_state["status"] and profile_id not in roster_state["profile_ids"]
    except requests.RequestException as exc:
        tqdm.write(f"aQuaプロフィールAPI確認失敗のため保持: {url} ({exc})")
        return False


def o_ren_unavailable_reason(url, final_url, page_html, girl_name):
    parsed = urlparse(url)
    final = urlparse(final_url)
    is_o_ren_host = parsed.netloc.lower() in {"o-ren-group.com", "www.o-ren-group.com"}
    is_profile_path = re.search(r"/castid-\d+/?$", parsed.path) is not None
    if not (is_o_ren_host and is_profile_path):
        return None
    is_roster_redirect = (
        final.netloc.lower() in {"o-ren-group.com", "www.o-ren-group.com"}
        and final.path.rstrip("/") == "/cast"
    )
    if is_roster_redirect:
        return f"店舗キャスト一覧へ転送: {final_url}"
    page_text = BeautifulSoup(page_html or "", "html.parser").get_text(" ")
    if is_missing(girl_name) or not name_in_text(page_text, str(girl_name)):
        return "個人プロフィール上で名前を確認できない"
    return None


def filter_unavailable_girl_urls(df, session):
    """プロフィールURLを確認し、終了ページを除外して画像URLも付与する。

    除外は明確な不在シグナル（404/410、別ページへの転送、サイト別の空テンプレート、
    aQua の在籍API確認）のときだけ。取得失敗や名前の不一致では除外しない。
    """
    unavailable_urls = set()
    profile_images = {}
    aqua_roster_state = {}

    # 同じ人物（正規化プロフィールURL）の複数レポートでも確認は1回だけ行う
    profile_keys = df["girl_url"].map(
        lambda url: normalize_profile_url(url) or (None if is_missing(url) else str(url))
    ) if len(df) else pd.Series(dtype=object)
    representative_urls = {}
    for key, url, name in zip(profile_keys, df.get("girl_url", []), df.get("name", [])):
        if key is not None and key not in representative_urls:
            representative_urls[key] = (url, name)

    for key, (url, girl_name) in tqdm(representative_urls.items(), desc="Checking profile URLs"):
        try:
            with session.get(
                url,
                headers=HEADERS,
                timeout=15,
                allow_redirects=True,
                stream=True,
            ) as response:
                if response.status_code in {404, 410}:
                    unavailable_urls.add(key)
                    continue
                if 200 <= response.status_code < 400:
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        remaining = PROFILE_HTML_LIMIT - size
                        chunks.append(chunk[:remaining])
                        size += min(len(chunk), remaining)
                        if size >= PROFILE_HTML_LIMIT:
                            break
                    page_html = decode_html(b"".join(chunks), response.encoding)
                    o_ren_reason = o_ren_unavailable_reason(url, response.url, page_html, girl_name)
                    if o_ren_reason:
                        tqdm.write(f"プロフィール掲載終了（{o_ren_reason}）: {url}")
                        unavailable_urls.add(key)
                        continue
                    status, reason = classify_profile_page(url, response.url, response.status_code, page_html)
                    if status == "unavailable":
                        tqdm.write(f"プロフィール掲載終了（{reason}）: {url}")
                        unavailable_urls.add(key)
                        continue
                    if is_unavailable_aqua_profile(url, session, aqua_roster_state):
                        unavailable_urls.add(key)
                        continue
                    profile_images[key] = extract_profile_image_url(page_html, response.url, str(girl_name))
        except requests.RequestException as exc:
            tqdm.write(f"URL確認失敗のため保持: {url} ({exc})")

    filtered = df.copy()
    filtered["_profile_key"] = profile_keys
    if unavailable_urls:
        removed = filtered["_profile_key"].isin(unavailable_urls)
        tqdm.write(f"公開終了URLを持つレコードを除外: {int(removed.sum())}件")
        filtered = filtered.loc[~removed].copy()
    filtered["profile_image_url"] = filtered["_profile_key"].map(profile_images)
    return filtered.drop(columns="_profile_key").reset_index(drop=True)


def login(session, password):
    payload = {
        "password_protected_pwd": password,
        "wp-submit": "ログイン",
        "password_protected_cookie_test": "1",
        "password-protected": "login",
        "redirect_to": "",
    }
    response = session.post(LOGIN_URL, data=payload, headers=HEADERS, timeout=30)
    response.raise_for_status()
    tqdm.write(f"Status: {response.status_code}")
    tqdm.write(f"After login: {response.url}")


def has_stored_details(df):
    if "fetched_at" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["fetched_at"].map(lambda value: not is_missing(value))


def enrich_report_data(df, session):
    """詳細未保存の旧記事だけ本文を取得して詳細列を埋める（JSONLには書き戻さない）。

    収集時に保存済みの詳細はそのまま使う。保存するには collect_fuzoku_tokai.py --fill-details。
    """
    df = df.copy()
    for column in DETAIL_LABELS:
        if column not in df.columns:
            df[column] = None
        df[column] = df[column].astype(object)
    targets = df.index[~has_stored_details(df)]
    for index in tqdm(targets, total=len(targets), desc="Scraping progress"):
        row = df.loc[index]
        response = session.get(row["url"], headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tqdm.write(f"{row['shop_name']}:{row['name']}")
        for column, value in parse_report_details(soup).items():
            if value is None:
                tqdm.write(f"{DETAIL_LABELS[column]} に関する記述なし")
            df.at[index, column] = value
    return df


def prepare_offline_data(df):
    """ネットなしで、保存済みの詳細だけからHTMLを作る。画像・掲載確認は行わない。"""
    df = df.copy()
    for column in DETAIL_LABELS:
        if column not in df.columns:
            df[column] = None
    if "profile_image_url" not in df.columns:
        df["profile_image_url"] = None
    if OUTPUT_HTML.exists() and "girl_url" in df.columns:
        saved_page = BeautifulSoup(OUTPUT_HTML.read_text(encoding="utf-8"), "lxml")
        saved_images = {}
        for person in saved_page.select(".report-entry"):
            link = person.select_one(".person-heading a.link-button")
            image = person.select_one(".profile-thumbnail[src]")
            if link and image:
                saved_images[normalize_profile_url(link["href"])] = image["src"]
        missing_images = df["profile_image_url"].isna() | df["profile_image_url"].eq("")
        df.loc[missing_images, "profile_image_url"] = df.loc[missing_images, "girl_url"].map(
            lambda value: saved_images.get(normalize_profile_url(value))
        )
    missing = int((~has_stored_details(df)).sum()) if len(df) else 0
    if missing:
        tqdm.write(
            f"詳細未保存の記事 {missing}件は基本項目のみ表示します"
            "（collect_fuzoku_tokai.py --fill-details で補完できます）"
        )
    return df


def parse_args(argv):
    parser = argparse.ArgumentParser(description="東海風俗レポートHTMLを生成する")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ネット接続なしで保存済みデータから生成する（プロフィール再確認・画像取得なし）",
    )
    parser.add_argument(
        "--refresh-prices", action="store_true",
        help="--offline専用。保存HTMLの人物・記事・掲載確認を保持して料金とフィルタだけ更新",
    )
    args = parser.parse_args(argv)
    if args.refresh_prices and not args.offline:
        parser.error("--refresh-prices は --offline と併用してください")
    return args


def main(argv=()):
    args = parse_args(list(argv))
    if args.refresh_prices:
        write_html(OUTPUT_HTML, refresh_saved_prices(
            OUTPUT_HTML.read_text(encoding="utf-8"), PAGE_TEMPLATE, "function applySort()",
        ))
        print(f"保存HTMLの人物・記事を保持して料金を更新しました: {OUTPUT_HTML}")
        return
    df = load_input_data()
    if args.offline:
        df = prepare_offline_data(df)
    else:
        password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
        if not password:
            raise RuntimeError(
                "FUZOKU_TOKAI_PASSWORD 環境変数を設定してください（ネットなしで生成する場合は --offline）"
            )
        with requests.Session() as session:
            login(session, password)
            df = filter_unavailable_girl_urls(df, session)
            df = enrich_report_data(df, session)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    write_html(OUTPUT_HTML, generate_html(df))
    print(f"生成しました: {OUTPUT_HTML}")


if __name__ == "__main__":
    main(sys.argv[1:])
