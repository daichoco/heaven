import os
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from html_render import generate_html

ROOT_DIR = Path(__file__).resolve().parents[1]
INPUT_CSV = ROOT_DIR / "data" / "results_2025-11-09_webdriver1.csv"
OUTPUT_HTML = ROOT_DIR / "docs" / "report_final.html"
LOGIN_URL = "https://m.fuzoku-tokai.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "identity",
}

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
    df = pd.read_csv(INPUT_CSV)
    required_columns = {"delete?", "predict_age", "url", "shop_name", "name", "girl_url"}
    missing_columns = required_columns.difference(df.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"入力CSVに必要な列がありません: {missing}")

    df = df.loc[df["delete?"].eq(0)].copy()
    ages = pd.to_numeric(
        df["predict_age"].astype("string").str.extract(r"(\d+)")[0],
        errors="coerce",
    )
    return df.loc[ages.le(30)].copy().reset_index(drop=True)


def filter_unavailable_girl_urls(df, session):
    """404/410 のプロフィールURLを持つレコードを除外する。"""
    unavailable_urls = set()

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
        except requests.RequestException as exc:
            tqdm.write(f"URL確認失敗のため保持: {url} ({exc})")

    if unavailable_urls:
        removed = df["girl_url"].isin(unavailable_urls)
        tqdm.write(f"公開終了URLを持つレコードを除外: {int(removed.sum())}件")
        return df.loc[~removed].copy().reset_index(drop=True)

    return df.copy().reset_index(drop=True)


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
