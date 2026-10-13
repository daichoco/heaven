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

from data_store import read_jsonl
from html_render import generate_html

INPUT_JSONL = ROOT_DIR / "data" / "tokai_fuzoku_reports.jsonl"
OUTPUT_HTML = ROOT_DIR / "docs" / "report_final.html"
LOGIN_URL = "https://m.fuzoku-tokai.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}
PROFILE_HTML_LIMIT = 512 * 1024
AQUA_API_ROOT = "https://www.k-aqua-jpn.com/shop/api"
AQUA_ROSTER_API = f"{AQUA_API_ROOT}/parts/pc/25/1"

COMMENT_COLUMNS = {
    "style_comment": "◆姫のスタイル（コメント）：",
    "panel_gap": "◆パネマジ度（コメント）：",
    "pie_comment": "◆姫の胸（コメント）：",
    "play_comment": "◆プレイ内容（コメント）：",
    "total_comment": "◆総合満足度（コメント）：",
    "repeat_comment": "◆裏を返す返さないの具体的な理由：",
}
SCORE_COLUMNS = {
    "total_point": "◆総合満足度（点数）：",
    "style_score": "◆姫のスタイル・肌（点数）：",
    "panel_point": "◆パネマジ度（点数）：",
    "play_score": "◆プレイ内容（点数）：",
}
PLAY_TIME_COLUMNS = {
    "play_time": "◆プレイ時間：",
    "total_cost": "◆総額：",
}


def extract_comment(soup, label):
    tag = soup.find("p", string=label)
    if tag:
        next_tag = tag.find_next_sibling("p")
        return next_tag.get_text(strip=True) if next_tag else None
    tqdm.write(f"{label} に関する記述なし")
    return None


def extract_score(soup, label):
    import re

    for paragraph in soup.find_all("p"):
        if label in paragraph.get_text(strip=True):
            font = paragraph.find("font")
            if font:
                match = re.search(r"\d+", font.get_text(strip=True))
                if match:
                    return int(match.group())
    tqdm.write(f"{label} に関する記述なし")
    return None


def extract_play_time(soup, label):
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(strip=True)
        if label in text:
            return text
    tqdm.write(f"{label} に関する記述なし")
    return None


def load_input_data():
    df = read_jsonl(INPUT_JSONL)
    required_columns = {"delete?", "predict_age", "url", "shop_name", "name", "girl_url"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"入力JSONLに必要な列がありません: {missing}")

    df = df.loc[df["delete?"].eq(0)].copy()
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
        image_url = image.get("src") or image.get("data-src") or image.get("data-original")
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


def is_unavailable_o_ren_profile(url, final_url):
    parsed = urlparse(url)
    final = urlparse(final_url)
    is_o_ren_host = parsed.netloc.lower() in {"o-ren-group.com", "www.o-ren-group.com"}
    is_profile_path = re.search(r"/castid-\d+/?$", parsed.path) is not None
    is_roster_redirect = (
        final.netloc.lower() in {"o-ren-group.com", "www.o-ren-group.com"}
        and final.path.rstrip("/") == "/cast"
    )
    return is_o_ren_host and is_profile_path and is_roster_redirect


def filter_unavailable_girl_urls(df, session):
    """プロフィールURLを確認し、終了ページを除外して画像URLも付与する。"""
    unavailable_urls = set()
    profile_images = {}
    aqua_roster_state = {}

    for url in tqdm(df["girl_url"].dropna().unique(), desc="Checking profile URLs"):
        try:
            with session.get(
                url,
                headers=HEADERS,
                timeout=15,
                allow_redirects=True,
                stream=True,
            ) as response:
                if response.status_code in {404, 410}:
                    unavailable_urls.add(url)
                    continue
                if 200 <= response.status_code < 400:
                    if is_unavailable_o_ren_profile(url, response.url):
                        unavailable_urls.add(url)
                        continue
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
                    page_html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    if is_unavailable_aqua_profile(url, session, aqua_roster_state):
                        unavailable_urls.add(url)
                        continue
                    girl_name = str(df.loc[df["girl_url"].eq(url), "name"].iloc[0])
                    profile_images[url] = extract_profile_image_url(page_html, response.url, girl_name)
        except requests.RequestException as exc:
            tqdm.write(f"URL確認失敗のため保持: {url} ({exc})")

    filtered = df.copy()
    if unavailable_urls:
        removed = filtered["girl_url"].isin(unavailable_urls)
        tqdm.write(f"公開終了URLを持つレコードを除外: {int(removed.sum())}件")
        filtered = filtered.loc[~removed].copy()
    filtered["profile_image_url"] = filtered["girl_url"].map(profile_images)
    return filtered.reset_index(drop=True)


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


def enrich_report_data(df, session):
    columns = {**COMMENT_COLUMNS, **SCORE_COLUMNS, **PLAY_TIME_COLUMNS}
    values = {column: [] for column in columns}

    for _, row in tqdm(df.iterrows(), total=len(df), desc="Scraping progress"):
        response = session.get(row["url"], headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tqdm.write(f"{row['shop_name']}:{row['name']}")

        for column, label in COMMENT_COLUMNS.items():
            values[column].append(extract_comment(soup, label))
        for column, label in SCORE_COLUMNS.items():
            values[column].append(extract_score(soup, label))
        for column, label in PLAY_TIME_COLUMNS.items():
            values[column].append(extract_play_time(soup, label))

    for column, column_values in values.items():
        df[column] = column_values
    return df


def main():
    password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
    if not password:
        raise RuntimeError("FUZOKU_TOKAI_PASSWORD 環境変数を設定してください")

    df = load_input_data()
    with requests.Session() as session:
        login(session, password)
        df = filter_unavailable_girl_urls(df, session)
        df = enrich_report_data(df, session)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(generate_html(df), encoding="utf-8")
    print(f"生成しました: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
