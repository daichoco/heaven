import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
NS_JSONL = ROOT_DIR / "data" / "ns_senmon_reports.jsonl"

SUMMARY_KEYS = [
    "推定年齢",
    "姫の顔（点数）",
    "スタイル（点数）",
    "おっぱい（印象）",
    "プレイ満足度（点数）",
    "総合満足度（点数）",
    "姫のリピート",
]
COMMENT_KEYS = ["プレイコメント", "総合コメント"]


def _summarize(record):
    details = record.get("details") or {}
    scores = {k: details[k] for k in SUMMARY_KEYS if details.get(k)}
    comments = {}
    for key, value in details.items():
        if value and any(key.startswith(prefix) for prefix in COMMENT_KEYS):
            comments[key.split("（")[0]] = value
    return {
        "date": record.get("date", ""),
        "url": record.get("url", ""),
        "scores": scores,
        "comments": comments,
    }


def load_ns_reports(normalize_url, path=NS_JSONL):
    """cityheaven_url の正規化キー -> 新しい順のレポート要約リスト"""
    by_key = {}
    if not Path(path).exists():
        return by_key
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            key = normalize_url(record.get("cityheaven_url"))
            if not key:
                continue
            by_key.setdefault(key, []).append(_summarize(record))
    for reports in by_key.values():
        reports.sort(key=lambda r: r["date"], reverse=True)
    return by_key


def merge_ns_reports(calendar_data, *, normalize_url):
    """出勤データに NS専門 レポート(ns_reports列)を左結合する。"""
    by_key = load_ns_reports(normalize_url)
    merged = calendar_data.copy()
    merged["ns_reports"] = merged["url_key"].map(lambda key: by_key.get(key))
    return merged
