"""オキニラブデータの保守処理を単独で実行する（report_left_merge では既定で行わない時間のかかる処理）。

  python okinilove/maintain_okinilove.py --check-unavailable   # 掲載終了確認（404/410 の行を削除）
  python okinilove/maintain_okinilove.py --enrich              # 空の cityheaven_url を店舗ページ検索で補完
  python okinilove/maintain_okinilove.py --enrich --time-budget 300

data/cityheaven_profiles.json（出勤カレンダー）がある場合は、掲載中の人を掲載終了確認から除外し、
その店舗の人を検索補完で優先する。
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import write_jsonl_atomic
from integrations.report_left_merge import enrich_with_cityheaven_urls, normalize_url
from okinilove.okinilove_left_merge import (
    ENRICH_TIME_BUDGET_SECONDS,
    OKINILOVE_JSONL,
    _read_all_columns,
    fill_cityheaven_urls_from_calendar,
    maintain_okinilove_data,
)

PROFILE_DATA_PATH = SRC_DIR.parent / "data" / "cityheaven_profiles.json"


def load_calendar_data(path=PROFILE_DATA_PATH):
    if not path.exists():
        print(f"出勤カレンダーなし（全件を対象に実行）: {path}")
        return pd.DataFrame(columns=["名前", "URL", "店名", "url_key"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    calendar = pd.DataFrame(payload["records"], columns=payload["columns"])
    calendar["url_key"] = calendar["URL"].apply(normalize_url)
    return calendar


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-unavailable", action="store_true", help="掲載終了確認を行う")
    parser.add_argument("--enrich", action="store_true", help="店舗ページ検索で cityheaven_url を補完する")
    parser.add_argument(
        "--time-budget",
        type=float,
        default=ENRICH_TIME_BUDGET_SECONDS,
        help=f"検索補完に使う秒数の上限（既定 {ENRICH_TIME_BUDGET_SECONDS}）",
    )
    args = parser.parse_args(argv)
    if not (args.check_unavailable or args.enrich):
        parser.error("--check-unavailable と --enrich の少なくとも一方を指定してください")

    start = time.perf_counter()
    calendar = load_calendar_data()
    data = _read_all_columns()
    data, calendar_filled = fill_cityheaven_urls_from_calendar(data, calendar, normalize_url)
    if calendar_filled:
        print(f"cityheaven_url 再補完(カレンダー照合): {calendar_filled}件")
    data, changed = maintain_okinilove_data(
        data,
        calendar,
        normalize_url=normalize_url,
        enrich_with_cityheaven_urls=enrich_with_cityheaven_urls,
        check_unavailable=args.check_unavailable,
        search_enrich=args.enrich,
        time_budget=args.time_budget,
    )
    if changed or calendar_filled:
        write_jsonl_atomic(data, OKINILOVE_JSONL)
        print(f"保存: {OKINILOVE_JSONL}")
    print(f"[timing] maintain_okinilove: {time.perf_counter() - start:.2f}s")


if __name__ == "__main__":
    main()
