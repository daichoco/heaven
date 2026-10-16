"""東海風俗レポートの記事単位保存と、収集・HTML生成で共有する解析ヘルパー。

`data/tokai_fuzoku_reports.jsonl` は 1記事URL = 1行。人物の関連付けは保存せず、
`person_key()`（正規化したプロフィールURL）で実行時に求める。
"""
import math
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl_records, write_jsonl_records_atomic

REPORTS_JSONL = ROOT_DIR / "data" / "tokai_fuzoku_reports.jsonl"

ARTICLE_HOSTS = {"fuzoku-tokai.com", "m.fuzoku-tokai.com", "www.fuzoku-tokai.com"}
ARTICLE_PATH_RE = re.compile(
    r"^/(?P<year>20\d{2})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>post-\d+)/?$"
)

BASE_COLUMNS = [
    "delete?",
    "date",
    "type",
    "shop_name",
    "name",
    "predict_age",
    "face_score",
    "face_feature",
    "cup",
    "repeat",
    "girl_url",
    "url",
]
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
DETAIL_COLUMNS = [*SCORE_COLUMNS, *COMMENT_COLUMNS, *PLAY_TIME_COLUMNS]
# fetched_at: 記事本文を解析して詳細列を保存した日時。旧データには無く、補完対象の目印になる。
# profile_status: 収集時のプロフィール確認結果 published / unavailable / unknown。
STATUS_COLUMNS = ["profile_status", "profile_checked_at", "fetched_at"]
REPORT_COLUMNS = BASE_COLUMNS + DETAIL_COLUMNS + STATUS_COLUMNS

# 再取得で上書きしない手動列と、取得のたびに更新するシステム列
MANUAL_COLUMNS = {"delete?"}
SYSTEM_COLUMNS = {"fetched_at"}
PROFILE_STATUSES = {"published", "unavailable", "unknown"}

CITYHEAVEN_GIRL_RE = re.compile(r"^(?P<prefix>.*/girlid-\d+)(?:/.*)?$")
TRACKING_QUERY_KEYS = {"fbclid", "gclid"}


def canonical_article_url(url):
    """記事URLを https://m.fuzoku-tokai.com/YYYY/MM/DD/post-N/ に正規化する。対象外は None。"""
    if url is None:
        return None
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if (parsed.hostname or "").lower() not in ARTICLE_HOSTS:
        return None
    match = ARTICLE_PATH_RE.match(parsed.path)
    if not match:
        return None
    try:
        date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None
    return (
        f"https://m.fuzoku-tokai.com/{match.group('year')}/{match.group('month')}/"
        f"{match.group('day')}/{match.group('slug')}/"
    )


def article_date_from_url(url):
    canonical = canonical_article_url(url)
    if canonical is None:
        return None
    year, month, day = canonical.split("/")[3:6]
    return date(int(year), int(month), int(day))


def normalize_profile_url(url):
    """人物の関連付け用にプロフィールURLを正規化する。

    スキーム・ホストの大文字小文字・末尾スラッシュ・フラグメント・追跡用クエリだけを除き、
    人物を識別しうる他のクエリ（`?id=` 等）は残す。CityHeaven は girlid までを使う。
    """
    if is_missing(url):
        return None
    text = str(url).strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.lower()
    if port and port not in {80, 443}:
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if host.endswith("cityheaven.net"):
        match = CITYHEAVEN_GIRL_RE.match(path)
        if match:
            return f"https://{host}{match.group('prefix')}/"

    path = path.rstrip("/") or ""
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
        )
    )
    return f"https://{host}{path}/" + (f"?{query}" if query else "")


def person_key(record):
    """人物キー（正規化プロフィールURL）。URLが無い行は人物を特定しない（店名・名前で統合しない）。"""
    return normalize_profile_url(record.get("girl_url"))


def is_missing(value):
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def extract_comment(soup, label):
    tag = soup.find("p", string=label)
    if tag:
        next_tag = tag.find_next_sibling("p")
        return next_tag.get_text(strip=True) if next_tag else None
    return None


def extract_score(soup, label):
    for paragraph in soup.find_all("p"):
        if label in paragraph.get_text(strip=True):
            font = paragraph.find("font")
            if font:
                match = re.search(r"\d+", font.get_text(strip=True))
                if match:
                    return int(match.group())
    return None


def extract_play_time(soup, label):
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(strip=True)
        if label in text:
            return text
    return None


def parse_report_details(soup):
    """点数・コメント・時間・費用。記載が無い項目は None（0点にしない）。"""
    details = {}
    for column, label in SCORE_COLUMNS.items():
        details[column] = extract_score(soup, label)
    for column, label in COMMENT_COLUMNS.items():
        details[column] = extract_comment(soup, label)
    for column, label in PLAY_TIME_COLUMNS.items():
        details[column] = extract_play_time(soup, label)
    return details


def merge_report(existing, fresh):
    """既存行に再取得結果を安全に反映する。(結果, 変更した非システム列) を返す。

    既存の値（手動補正・補完値を含む）は上書きせず、欠損している列だけを埋める。
    `delete?` は既存行では変更しない。fetched_at は取得のたびに更新する。
    profile_status は確定結果（published/unavailable）のときだけ更新する。
    """
    merged = dict(existing)
    changed = []
    for key, value in fresh.items():
        if is_missing(value):
            continue
        if key in MANUAL_COLUMNS and key in existing:
            continue
        if key in SYSTEM_COLUMNS:
            merged[key] = value
            continue
        if key in {"profile_status", "profile_checked_at"}:
            continue
        if is_missing(existing.get(key)):
            merged[key] = value
            changed.append(key)

    status = fresh.get("profile_status")
    if status in {"published", "unavailable"}:
        if existing.get("profile_status") != status:
            changed.append("profile_status")
        merged["profile_status"] = status
        merged["profile_checked_at"] = fresh.get("profile_checked_at")
    elif status == "unknown" and is_missing(existing.get("profile_status")):
        merged["profile_status"] = status
        merged["profile_checked_at"] = fresh.get("profile_checked_at")
    return merged, changed


class ReportStore:
    """記事URLをキーにした JSONL ストア。既存行のキー順・未知の列・並び順を保持する。"""

    def __init__(self, path=REPORTS_JSONL):
        self.path = Path(path)
        self.records = []
        self._index = {}
        self.dirty = False
        self.dropped_duplicates = 0
        for record in read_jsonl_records(self.path):
            key = canonical_article_url(record.get("url"))
            if key is not None and key in self._index:
                self.dropped_duplicates += 1
                self.dirty = True
                continue
            if key is not None:
                self._index[key] = len(self.records)
            self.records.append(record)

    def __contains__(self, url):
        key = canonical_article_url(url)
        return key is not None and key in self._index

    def __len__(self):
        return len(self.records)

    def get(self, url):
        key = canonical_article_url(url)
        return self.records[self._index[key]] if key in self._index else None

    def latest_date(self):
        dates = []
        for record in self.records:
            value = record.get("date")
            try:
                dates.append(date.fromisoformat(str(value)[:10]))
            except (TypeError, ValueError):
                continue
        return max(dates) if dates else None

    def upsert(self, record):
        """'added' / 'updated' / 'unchanged' を返す。"""
        key = canonical_article_url(record.get("url"))
        if key is None:
            raise ValueError(f"記事URLとして扱えません: {record.get('url')}")
        record = {**record, "url": key}
        if key not in self._index:
            self._index[key] = len(self.records)
            new_record = {column: record.get(column) for column in REPORT_COLUMNS}
            new_record.update({k: v for k, v in record.items() if k not in new_record})
            self.records.append(new_record)
            self.dirty = True
            return "added"

        position = self._index[key]
        merged, changed = merge_report(self.records[position], record)
        if merged != self.records[position]:
            self.records[position] = merged
            self.dirty = True
        return "updated" if changed else "unchanged"

    def save(self):
        if not self.dirty:
            return False
        write_jsonl_records_atomic(self.records, self.path)
        self.dirty = False
        return True


def latest_profile_status_by_person(records):
    """人物キーごとに、最も新しい確定的なプロフィール確認結果を返す。"""
    latest = {}
    for record in records:
        status = record.get("profile_status")
        if status not in {"published", "unavailable"}:
            continue
        key = person_key(record)
        if key is None:
            continue
        checked_at = "" if is_missing(record.get("profile_checked_at")) else str(record.get("profile_checked_at"))
        if key not in latest or checked_at >= latest[key][0]:
            latest[key] = (checked_at, status)
    return {key: status for key, (_, status) in latest.items()}
