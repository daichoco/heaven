"""3レポート（東海・NS専門・オキニラブ）の追加・削除件数と高得点の追加分を要約する。

Git の HEAD と作業ツリーのデータファイルを比較し、LINE 通知用のテキストを標準出力へ出す。
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
MAX_HIGHLIGHTS = 10
PAGES_BASE = "https://daichoco.github.io/heaven/"
TOKAI_MIN_TOTAL = 80
NS_MIN_TOTAL = 90
OKINILOVE_MIN_STARS = 4
NS_TOTAL_KEY = "総合満足度（点数）"


def tokai_key(r):
    return (r.get("url"), r.get("shop_name"), r.get("name"))


def ns_key(r):
    return (r.get("post_id"), r.get("name"))


def okinilove_key(r):
    return (r.get("report_url"), r.get("girl_name"))


def stars(r):
    match = re.search(r"[★☆]+", str(r.get("evaluations", "")))
    return match.group().count("★") if match else 0


def tokai_high(r):
    total = r.get("total_point")
    return (
        isinstance(total, (int, float))
        and total >= TOKAI_MIN_TOTAL
        and str(r.get("repeat", "")).startswith("返す")
    )


def ns_high(r):
    match = re.search(r"\d+", str(r.get("details", {}).get(NS_TOTAL_KEY, "")))
    return bool(match) and int(match.group()) >= NS_MIN_TOTAL


def okinilove_high(r):
    return stars(r) >= OKINILOVE_MIN_STARS


def person_link(page, shop, name):
    """レポートページ内でその人物だけに絞り込むリンク。"""
    shop, name = str(shop or "").strip(), str(name or "").strip()
    if not name:
        return ""
    return f"{PAGES_BASE}{page}#search={quote(name)}"


def with_link(label, page, shop, name):
    link = person_link(page, shop, name)
    return f"{label}\n  レポート: {link}" if link else label


def tokai_label(r):
    label = f"{r.get('shop_name', '')} {r.get('name', '')} 総合{r.get('total_point')}点"
    return with_link(label, "tokai_final_report.html", r.get("shop_name"), r.get("name"))


def ns_label(r):
    label = f"{r.get('shop_name', '')} {r.get('name', '')} 総合{r.get('details', {}).get(NS_TOTAL_KEY, '')}"
    return with_link(label, "ns_senmon_report.html", r.get("shop_name"), r.get("name"))


def okinilove_label(r):
    label = f"{r.get('shop_name', '')} {r.get('girl_name', '')} ★{stars(r)}"
    return with_link(label, "okinilove_remove_unknown.html", r.get("shop_name"), r.get("girl_name"))


REPORTS = [
    ("東海", "data/tokai_fuzoku_reports.jsonl", tokai_key, tokai_high, tokai_label),
    ("NS専門", "data/ns_senmon_reports.jsonl", ns_key, ns_high, ns_label),
    ("オキニラブ", "data/okinilove_reviews.jsonl", okinilove_key, okinilove_high, okinilove_label),
]


def parse(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_head(path, root=ROOT):
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{path}"],
        capture_output=True,
    )
    return parse(result.stdout.decode("utf-8")) if result.returncode == 0 else []


def read_work(path, root=ROOT):
    file = Path(root) / path
    return parse(file.read_text(encoding="utf-8")) if file.exists() else []


def compare(old, new, key, is_high):
    old_keys = {key(r) for r in old}
    new_keys = {key(r) for r in new}
    added = [r for r in new if key(r) not in old_keys]
    deleted = [r for r in old if key(r) not in new_keys]
    return added, deleted, [r for r in added if is_high(r)]


def summarize(results):
    lines = []
    highlights = []
    for name, (added, deleted, high, label) in results.items():
        lines.append(f"{name}: 追加{len(added)}件 / 削除{len(deleted)}件")
        highlights.extend(f"[{name}] {label(r)}" for r in high)
    if highlights:
        lines.append("")
        lines.append(f"高得点の追加 {len(highlights)}件")
        lines.extend(highlights[:MAX_HIGHLIGHTS])
        if len(highlights) > MAX_HIGHLIGHTS:
            lines.append(f"…ほか{len(highlights) - MAX_HIGHLIGHTS}件")
    return "\n".join(lines)


def main():
    results = {}
    for name, path, key, is_high, label in REPORTS:
        added, deleted, high = compare(read_head(path), read_work(path), key, is_high)
        results[name] = (added, deleted, high, label)
    sys.stdout.reconfigure(encoding="utf-8")
    print(summarize(results))


if __name__ == "__main__":
    main()
