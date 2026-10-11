import html
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl

INPUT_JSONL = ROOT_DIR / "data" / "ns_senmon_reports.jsonl"
OUTPUT_HTML = ROOT_DIR / "docs" / "ns_senmon_report.html"

PLAY_KEY = "プレイ満足度（点数）"
TOTAL_KEY = "総合満足度（点数）"
REPEAT_OPTIONS = ["初めて", "2回目", "3回目", "4回以上"]
AGE_OPTIONS = [
    ("10代", 0, 19),
    ("20〜24歳", 20, 24),
    ("25〜29歳", 25, 29),
    ("30〜34歳", 30, 34),
    ("35歳以上", 35, 200),
]
# 展開時に表示する項目（年齢・おっぱい・リピート・点数は見出しに出すため除く）
DETAIL_PREFIXES = [
    "推定身長",
    "中〇しした回数",
    "プレイコメント",
    "総合コメント",
]


def number(details, key):
    """`顔65点` や `90点` から数値を取り出す。"""
    match = re.search(r"\d+", str(details.get(key, "")))
    return int(match.group()) if match else None


def field_value(details, prefix):
    for key, value in details.items():
        if key.startswith(prefix):
            return value
    return ""


def person_key(record):
    return record.get("cityheaven_url") or f"{record['shop_name']}|{record['name']}"


def group_records(records):
    """同一人物（CityHeaven URL、なければ店舗+名前）でまとめる。日付降順を維持。"""
    groups = {}
    for record in records:
        groups.setdefault(person_key(record), []).append(record)
    return list(groups.values())


def chips_for(record):
    details = record["details"]
    repeat = field_value(details, "姫のリピート")
    bust = field_value(details, "おっぱい（印象）").replace("\n", " ")
    play = number(details, PLAY_KEY)
    total = number(details, TOTAL_KEY)
    chips = []
    if repeat:
        chips.append(f"リピート {repeat}")
    if bust:
        chips.append(f"おっぱい {bust}")
    if play is not None:
        chips.append(f"プレイ {play}")
    if total is not None:
        chips.append(f"総合 {total}")
    return "".join(f'<span class="score-chip">{html.escape(c)}</span>' for c in chips)


def detail_items(record):
    items = ""
    for prefix in DETAIL_PREFIXES:
        value = field_value(record["details"], prefix)
        if not value:
            continue
        items += (
            '<div class="score-item">'
            f'<h3 class="score-label">{html.escape(prefix)}</h3>'
            f'<div class="report-comment"><p>{html.escape(value)}</p></div></div>'
        )
    return items


def render_entry(group):
    record = group[0]
    details = record["details"]
    shop = record["shop_name"]
    name = record["name"]
    repeat = field_value(details, "姫のリピート")
    age = number(details, "推定年齢")
    plays = [p for p in (number(r["details"], PLAY_KEY) for r in group) if p is not None]
    totals = [t for t in (number(r["details"], TOTAL_KEY) for r in group) if t is not None]
    play = max(plays) if plays else None
    total = max(totals) if totals else None
    chips_html = chips_for(record)
    if len(group) > 1:
        chips_html = f'<span class="score-chip">レポート {len(group)}件</span>' + chips_html

    age_str = f"{age}才" if age is not None else ""
    ch_url = record.get("cityheaven_url") or ""
    if ch_url:
        ch_link = (
            f' <a href="{html.escape(ch_url, quote=True)}" target="_blank" rel="noopener" '
            'class="link-button">CityHeaven</a>'
        )
    elif record.get("match_confidence") == "not_listed":
        ch_link = ' <span class="score-chip">掲載終了?</span>'
    else:
        ch_link = ""
    heading = (
        f"({html.escape(shop)}) "
        f'<a href="{html.escape(record["url"], quote=True)}" target="_blank" rel="noopener" '
        f'class="link-button">{html.escape(name)}</a> {age_str}'
        f'<span class="entry-date">{html.escape(str(record["date"]))}</span>'
        f'{ch_link}'
        f'<span class="score-overview">{chips_html}</span>'
    )

    if len(group) == 1:
        items = detail_items(record)
    else:
        items = ""
        for r in group:
            items += (
                '<div class="sub-report">'
                f'<div class="sub-heading"><a href="{html.escape(r["url"], quote=True)}" target="_blank" '
                f'rel="noopener" class="link-button">{html.escape(str(r["date"]))}</a>'
                f'<span class="score-overview">{chips_for(r)}</span></div>'
                f'{detail_items(r)}</div>'
            )

    search_text = " ".join([shop, name] + [r["title"] for r in group]).casefold()
    return (
        f'<details class="report-entry" data-shop="{html.escape(shop, quote=True)}" '
        f'data-repeat="{html.escape(repeat, quote=True)}" data-age="{age if age is not None else ""}" '
        f'data-play="{play if play is not None else ""}" data-total="{total if total is not None else ""}" '
        f'data-date="{html.escape(str(record["date"]))}" '
        f'data-search="{html.escape(search_text, quote=True)}">'
        f'<summary class="report-heading">{heading}</summary>'
        f'<div class="report-details">{items}</div></details>'
    )


def build_html(records):
    shops = sorted({r["shop_name"] for r in records if r["shop_name"]})
    shop_options = "".join(
        f'<option value="{html.escape(s, quote=True)}">{html.escape(s)}</option>' for s in shops
    )
    repeat_options = "".join(
        f'<option value="{html.escape(o, quote=True)}">{html.escape(o)}</option>' for o in REPEAT_OPTIONS
    )
    age_options = "".join(
        f'<option value="{low}-{high}">{label}</option>' for label, low, high in AGE_OPTIONS
    )
    entries = "\n".join(render_entry(group) for group in group_records(records))
    return (
        TEMPLATE.replace("SHOP_OPTIONS", shop_options)
        .replace("REPEAT_OPTIONS", repeat_options)
        .replace("AGE_OPTIONS", age_options)
        .replace("ENTRIES", entries)
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NS専門 レポート（仮）</title>
<style>
    * { box-sizing: border-box; }
    body { font-family: sans-serif; max-width: 980px; margin: 0 auto; padding: 24px; color: #222; line-height: 1.65; }
    .link-button { color: #007BFF; text-decoration: underline; }
    .report-entry { margin-bottom: 1em; border: 1px solid #ccc; border-radius: 6px; padding: 0.75em; background-color: #f9f9f9; transition: background-color 0.2s ease; }
    .report-entry[open] { background-color: #eef6ff; }
    .report-heading { cursor: pointer; font-weight: bold; padding: 0.3em; outline: none; margin: 0 0 0.5em; font-size: 1.05em; line-height: 1.5; }
    .report-heading:hover { color: #007BFF; }
    .entry-date { margin-left: 0.5em; font-weight: normal; color: #58636d; font-size: 0.85em; }
    .score-overview { display: inline-flex; flex-wrap: wrap; gap: 6px; margin-left: 0.5em; vertical-align: middle; }
    .score-chip { padding: 2px 8px; border-radius: 4px; background: #e8eef4; font-size: 0.9em; font-weight: normal; }
    .sub-report { margin: 0.6em 0; padding: 0.5em 0.75em; border-left: 3px solid #9bbbe0; background: #fff; }
    .sub-heading { font-weight: bold; }
    .score-item { margin: 0.35em 0; }
    .score-label { margin: 0.25em 0; font-size: 1em; }
    .report-details { margin: 0.5em 0 0 0.75em; }
    .report-comment { max-width: 72ch; line-height: 1.8; color: #333; overflow-wrap: anywhere; }
    .report-comment p { margin: 0.35em 0 0.8em; white-space: pre-wrap; }
    button { margin-right: 10px; padding: 6px 12px; font-size: 14px; background-color: #007BFF; color: white; border: none; border-radius: 4px; cursor: pointer; }
    button:hover { background-color: #0056b3; }
    .filter-bar { display: grid; grid-template-columns: minmax(220px, 1fr) repeat(4, minmax(135px, auto)); gap: 8px; margin: 0 0 14px; }
    .filter-bar input, .filter-bar select { min-height: 38px; padding: 6px 9px; border: 1px solid #b9c2c9; border-radius: 4px; background: #fff; color: #222; }
    .filter-count { margin: 0 0 10px; color: #58636d; font-size: 0.9em; }
    .no-results { padding: 16px; color: #58636d; }
    @media (max-width: 760px) { .filter-bar { grid-template-columns: 1fr 1fr; } .filter-bar input { grid-column: 1 / -1; } }
    @media (max-width: 600px) {
        body { padding: 12px; }
        h1 { font-size: 1.35rem; }
        .filter-bar { grid-template-columns: 1fr; }
        .filter-bar input { grid-column: auto; }
        .filter-bar input, .filter-bar select, button { min-height: 44px; }
        .entry-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .entry-controls button { margin: 0; }
        .report-entry { padding: 10px; }
        .report-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; overflow-wrap: anywhere; }
        .score-overview { flex-basis: 100%; margin: 4px 0 0; }
        .report-details { margin-left: 0; }
    }
</style>
<script>
    function expandAll() { document.querySelectorAll("details.report-entry").forEach(e => e.open = true); }
    function collapseAll() { document.querySelectorAll("details.report-entry").forEach(e => e.open = false); }
    function applyFilters() {
        const query = document.getElementById("reportSearch").value.trim().toLocaleLowerCase();
        const shop = document.getElementById("shopFilter").value;
        const repeat = document.getElementById("repeatFilter").value;
        const ageRange = document.getElementById("ageFilter").value;
        let visible = 0;
        document.querySelectorAll(".report-entry").forEach(entry => {
            let ageOk = true;
            if (ageRange) {
                const [low, high] = ageRange.split("-").map(Number);
                const age = entry.dataset.age === "" ? NaN : Number(entry.dataset.age);
                ageOk = Number.isFinite(age) && age >= low && age <= high;
            }
            const matches = entry.dataset.search.includes(query)
                && (!shop || entry.dataset.shop === shop)
                && (!repeat || entry.dataset.repeat === repeat)
                && ageOk;
            entry.hidden = !matches;
            if (matches) visible += 1;
        });
        document.getElementById("filterCount").textContent = `${visible} / ${document.querySelectorAll(".report-entry").length} 件`;
        document.getElementById("noResults").hidden = visible !== 0;
    }
    function sortEntries() {
        const key = document.getElementById("sortKey").value;
        const container = document.getElementById("entries");
        const value = entry => {
            const v = entry.dataset[key];
            return key === "date" ? v : (v === "" ? -1 : Number(v));
        };
        [...container.children]
            .sort((a, b) => {
                const va = value(a), vb = value(b);
                return va < vb ? 1 : va > vb ? -1 : 0;
            })
            .forEach(entry => container.appendChild(entry));
    }
    function resetFilters() {
        ["reportSearch", "shopFilter", "repeatFilter", "ageFilter"].forEach(id => document.getElementById(id).value = "");
        document.getElementById("sortKey").value = "date";
        sortEntries();
        applyFilters();
    }
    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".filter-bar input, .filter-bar select").forEach(control => {
            const handler = control.id === "sortKey" ? sortEntries : applyFilters;
            control.addEventListener(control.tagName === "INPUT" ? "input" : "change", handler);
        });
        applyFilters();
    });
</script>
</head>
<body>
<h1>NS専門 レポート（仮）</h1>
<div class="filter-bar">
    <input id="reportSearch" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
    <select id="shopFilter"><option value="">全店舗</option>SHOP_OPTIONS</select>
    <select id="repeatFilter"><option value="">全リピート</option>REPEAT_OPTIONS</select>
    <select id="ageFilter"><option value="">全年齢</option>AGE_OPTIONS</select>
    <select id="sortKey"><option value="date">新しい順</option><option value="total">総合点が高い順</option><option value="play">プレイ点が高い順</option></select>
    <button type="button" onclick="resetFilters()">条件をリセット</button>
</div>
<p id="filterCount" class="filter-count"></p>
<p id="noResults" class="no-results" hidden>条件に一致するレポートはありません。</p>
<div class="entry-controls">
    <button type="button" onclick="expandAll()">全部展開</button>
    <button type="button" onclick="collapseAll()">全部閉じる</button>
</div>
<div id="entries">
ENTRIES
</div>
</body>
</html>
"""


def main():
    data = read_jsonl(INPUT_JSONL)
    records = data.sort_values(["date", "post_id"], ascending=False).to_dict("records")
    for record in records:
        if not isinstance(record["details"], dict):
            record["details"] = json.loads(record["details"])
    OUTPUT_HTML.write_text(build_html(records), encoding="utf-8")
    print(f"HTMLを生成しました: {OUTPUT_HTML} ({len(records)}件)")


if __name__ == "__main__":
    main()
