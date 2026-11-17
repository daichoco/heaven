"""ページの★で作った通知対象Issueを取り込み、weekend_alerts_targets.txt に保存する。

ラベル notify-targets の最新のopen Issueの本文からプロフィールURLを抜き出す。
Issueが無い、または取得に失敗したときは既存ファイルを変更しない。
"""
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekend_alerts

API = "https://api.github.com/repos/daichoco/heaven/issues"


def extract_urls(body):
    return list(dict.fromkeys(re.findall(r"https?://[^\s<>\"')]+", body or "")))


def latest_targets(issues):
    for issue in issues:
        if "pull_request" in issue:
            continue
        urls = extract_urls(issue.get("body"))
        if urls:
            return urls
    return []


def main():
    response = requests.get(
        API, params={"labels": "notify-targets", "state": "open", "sort": "created", "direction": "desc", "per_page": 5},
        headers={"Accept": "application/vnd.github+json"}, timeout=30,
    )
    response.raise_for_status()
    urls = latest_targets(response.json())
    if not urls:
        print("通知対象Issueなし: 既存の通知対象を維持")
        return
    weekend_alerts.TARGETS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"通知対象 {len(urls)}名を取り込みました")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"通知対象の取り込みに失敗: {error}")
