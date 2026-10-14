"""NS専門レポートを人物ごとにまとめた HTML（docs/ns_senmon_report.html）を生成する。

見た目・操作はおきにラブの人物別ページ（src/okinilove/render_people_summary.py）に合わせる。
"""
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl
from ns_senmon.profile_images import classify_price_class
from web_common import BACK_NAV, BACK_TO_TOP, HEAD_COMMON


def shop_type(entry):
    """'luxury'（高級）/ 'other'（明示された高級以外）/ ''（不明）。"""
    return classify_price_class(entry)

INPUT_JSONL = ROOT_DIR / "data" / "ns_senmon_reports.jsonl"
OUTPUT_HTML = ROOT_DIR / "docs" / "ns_senmon_report.html"

PLAY_KEY = "プレイ満足度（点数）"
TOTAL_KEY = "総合満足度（点数）"
# 総合スコアで加える全体平均の件数（ベイズ平均の事前件数）
PRIOR_WEIGHT = 3
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
    """`顔65点` や `90点` から数値を取り出す（`50点以下` は 50 として扱う）。"""
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


def average(values):
    values = [v for v in values if v is not None]
    return (sum(values) / len(values), len(values)) if values else (None, 0)


def format_score(value):
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def score_text(label, group, key):
    """1件ならその点、複数件なら平均点と件数（例: 総合 平均88.3（3件））。"""
    value, count = average(number(r["details"], key) for r in group)
    if value is None:
        return value, ""
    if len(group) == 1:
        return value, f"{label} {format_score(value)}"
    return value, f"{label} 平均{format_score(value)}（{count}件）"


def adjusted_score(group, prior_mean):
    """件数補正した総合スコア（全体平均 PRIOR_WEIGHT 件分を加えた平均）。有効な総合点が無ければ None。"""
    totals = [t for t in (number(r["details"], TOTAL_KEY) for r in group) if t is not None]
    if not totals or prior_mean is None:
        return None
    return (sum(totals) + prior_mean * PRIOR_WEIGHT) / (len(totals) + PRIOR_WEIGHT)


def overall_total_mean(records):
    """全レポートの有効な総合点の平均（人物平均の平均ではない）。"""
    return average(number(r["details"], TOTAL_KEY) for r in records)[0]


def chip(text, extra=""):
    return f"<span class='chip{extra}'>{html.escape(text)}</span>"


def report_chips(record):
    details = record["details"]
    chips = []
    for label, key in (("総合", TOTAL_KEY), ("プレイ", PLAY_KEY)):
        value = number(details, key)
        if value is not None:
            chips.append(chip(f"{label} {value}", " score"))
    repeat = field_value(details, "姫のリピート")
    bust = field_value(details, "おっぱい（印象）").replace("\n", " ")
    age = number(details, "推定年齢")
    if repeat:
        chips.append(chip(f"リピート {repeat}"))
    if bust:
        chips.append(chip(f"おっぱい {bust}"))
    if age is not None:
        chips.append(chip(f"推定{age}歳"))
    return "".join(chips)


def detail_items(record):
    items = ""
    for prefix in DETAIL_PREFIXES:
        value = field_value(record["details"], prefix)
        if not value:
            continue
        items += (
            f"<p class='review-param'><span class='review-label'>{html.escape(prefix)}</span>"
            f"{html.escape(value)}</p>"
        )
    return items


def tally_chips(counts):
    return "".join(
        chip(f"{key}{f' ×{n}' if n > 1 else ''}") for key, n in counts.most_common()
    )


def render_person(group, profiles, prior_mean=None):
    record = group[0]
    shop = record["shop_name"]
    name = record["name"]
    ch_url = record.get("cityheaven_url") or ""
    age = next((a for a in (number(r["details"], "推定年齢") for r in group) if a is not None), None)
    total, total_text = score_text("総合", group, TOTAL_KEY)
    play, play_text = score_text("プレイ", group, PLAY_KEY)
    score = adjusted_score(group, prior_mean)
    repeats = Counter(v for v in (field_value(r["details"], "姫のリピート") for r in group) if v)
    busts = Counter(
        v for v in (field_value(r["details"], "おっぱい（印象）").replace("\n", " ") for r in group) if v
    )

    profile = profiles.get(ch_url) or {}
    image_url = profile.get("image_url", "")
    kind = shop_type(profile) if ch_url else ""
    kind_html = (
        f"<span class='shop-kind'>{html.escape(profile['shop_kind'])}</span>" if profile.get("shop_kind") else ""
    )
    photo = (
        f'<img class="profile-photo" src="{html.escape(image_url, quote=True)}" '
        f'alt="{html.escape(name, quote=True)}のプロフィール画像" width="42" height="56" '
        'loading="lazy" decoding="async" referrerpolicy="no-referrer">'
        if image_url
        else ""
    )
    if ch_url:
        person_html = (
            f'<a class="profile-link" href="{html.escape(ch_url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{photo}<span>{html.escape(name)}</span></a>'
        )
    else:
        person_html = f"<span class='profile-link'><span>{html.escape(name)}</span></span>"
        if record.get("match_confidence") == "not_listed":
            person_html += chip("掲載終了?")
    age_html = f"<span class='age'>推定{age}歳</span>" if age is not None else ""

    score_html = "".join(
        f"<div><strong>{html.escape(text)}</strong></div>" for text in (total_text, play_text) if text
    ) or "点数なし"
    if score is not None:
        score_html += f"<div class='adjusted-score' title='件数補正した総合スコア'>スコア {score:.1f}</div>"
    tally_html = (
        f"<div><span class='review-label'>リピート</span>{tally_chips(repeats) or '-'}</div>"
        f"<div><span class='review-label'>おっぱい</span>{tally_chips(busts) or '-'}</div>"
    )

    report_items = []
    for r in group:
        total_r = number(r["details"], TOTAL_KEY)
        summary = f"{r['date']}　総合 {total_r if total_r is not None else '-'}"
        report_items.append(
            "<li class='review-item'>"
            f"<div class='review-params'>{report_chips(r)}</div>"
            "<details class='review-body'>"
            f"<summary class='review-rating'>{html.escape(summary)}</summary>"
            f"<div class='review-detail'>{detail_items(r)}"
            f"<div class='review-meta'><a href=\"{html.escape(r['url'], quote=True)}\" target=\"_blank\" "
            f"rel=\"noopener noreferrer\">レポートを読む</a></div></div></details></li>"
        )
    reports_html = f"<ol class='report-list'>{''.join(report_items)}</ol>"

    search_text = " ".join([shop, name] + [r["title"] for r in group]).casefold()
    return (
        f'<tr class="person-row" data-shop="{html.escape(shop, quote=True)}" '
        f'data-repeat="{html.escape("|".join(repeats), quote=True)}" '
        f'data-age="{age if age is not None else ""}" data-shop-type="{kind}" '
        f'data-score="{f"{score:.3f}" if score is not None else ""}" '
        f'data-total="{f"{total:.3f}" if total is not None else ""}" '
        f'data-play="{f"{play:.3f}" if play is not None else ""}" '
        f'data-date="{html.escape(str(record["date"]), quote=True)}" '
        f'data-search="{html.escape(search_text, quote=True)}">'
        f"<td>{html.escape(shop)}{kind_html}</td><td>{person_html}{age_html}</td>"
        f"<td class='average-cell'>{score_html}</td>"
        f"<td class='tally-cell'>{tally_html}</td>"
        f"<td><span class='latest'>{html.escape(str(record['date']))}</span>"
        f"{len(group)}件 <span class='toggle'>▼</span></td></tr>"
        f'<tr class="detail-row" hidden><td colspan="5">{reports_html}</td></tr>'
    )


def build_html(records, profiles=None):
    profiles = profiles or {}
    groups = group_records(records)
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
    prior_mean = overall_total_mean(records)
    rows = "".join(render_person(group, profiles, prior_mean) for group in groups)
    return (
        TEMPLATE.replace("SHOP_OPTIONS", shop_options)
        .replace("REPEAT_OPTIONS", repeat_options)
        .replace("AGE_OPTIONS", age_options)
        .replace("PEOPLE_COUNT", str(len(groups)))
        .replace("PRIOR_MEAN", format_score(round(prior_mean, 1)) if prior_mean is not None else "-")
        .replace("PRIOR_WEIGHT", str(PRIOR_WEIGHT))
        .replace("ROWS", rows)
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
""" + HEAD_COMMON + """
<title>NS専門 レポート（仮）</title>
<style>
:root { --page-pad: 24px; --bg: #f2f4f5; --fg: #20272d; --link: #17617a; --panel: #fff; --panel-border: #d7dfe4; --field-label: #56616a; --field-border: #cbd4da; --focus: #28738a; --rule: #edf0f2; --head: #e9eef1; --row-hover: #f6f8f9; --row-open: #eef4f6; --detail-bg: #fafbfb; --chip: #eceff1; --chip-fg: #56616a; --score-bg: #fdf1e3; --score-border: #e9c393; --rating: #865019; --text-soft: #38434b; --table-border: #cbd2d8; --row-border: #e0e4e7; --muted: #68747d; }
@media (prefers-color-scheme: dark) {
:root { --bg: #15181b; --fg: #e3e6e9; --link: #6cc0d8; --panel: #1d2125; --panel-border: #30363c; --field-label: #9ba6af; --field-border: #4a535b; --focus: #4fa3bd; --rule: #2b3137; --head: #262c32; --row-hover: #22282d; --row-open: #1d2c33; --detail-bg: #191d20; --chip: #2b3239; --chip-fg: #b5bec6; --score-bg: #33261a; --score-border: #7a5630; --rating: #e0a35c; --text-soft: #c9d0d6; --table-border: #3b4249; --row-border: #30363c; --muted: #9ba6af; }
}
body { max-width: 1280px; font-family: "Yu Gothic UI", Meiryo, sans-serif; }
h1 { margin: 0 0 16px; font-size: 1.4rem; }
.filters { margin-bottom: 14px; padding: 16px; border: 1px solid var(--panel-border); border-radius: 10px; background: var(--panel); box-shadow: 0 2px 8px #2637460a; }
.filter-primary { display: grid; grid-template-columns: minmax(220px, 1.6fr) minmax(180px, 1fr); gap: 12px; }
.filter-field { display: grid; min-width: 0; gap: 5px; color: var(--field-label); font-size: 0.78rem; font-weight: 600; }
.filters input, .filters select { width: 100%; min-width: 0; min-height: 40px; padding: 8px 10px; border: 1px solid var(--field-border); border-radius: 6px; background: var(--panel); color: var(--fg); font: inherit; font-size: 16px; }
.filters input:focus, .filters select:focus { border-color: var(--focus); outline: 3px solid #28738a40; }
.filter-search { position: relative; }
.filter-search::before { position: absolute; left: 11px; bottom: 11px; color: #73818a; content: "⌕"; font-size: 1.15rem; line-height: 1; }
.filters input[type="search"] { padding-left: 34px; }
.filter-section { margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--rule); }
.filter-section-title { margin-bottom: 9px; color: var(--text-soft); font-size: 0.82rem; font-weight: 700; }
.filter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 12px; }
.filter-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; }
.result-count { margin: 0; color: var(--field-label); font-size: 0.85rem; }
.result-count strong { color: var(--fg); font-size: 1rem; }
.score-note { margin: 8px 0 0; color: var(--muted); font-size: 0.8rem; }
.adjusted-score { margin-top: 2px; color: var(--muted); font-size: 0.85rem; }
.reset-filters { min-height: 36px; padding: 7px 14px; border: 1px solid var(--field-border); border-radius: 6px; background: var(--detail-bg); color: var(--text-soft); font: inherit; font-size: 0.9rem; cursor: pointer; }
.reset-filters:hover { border-color: #8b9aa3; background: var(--head); }
.table-wrap { max-height: calc(100vh - 150px); overflow: auto; border: 1px solid var(--table-border); background: var(--panel); }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--row-border); text-align: left; vertical-align: top; }
thead th { position: sticky; top: 0; z-index: 1; background: var(--head); }
tbody tr:hover { background: var(--row-hover); }
.average-cell { min-width: 150px; white-space: nowrap; }
.profile-link { display: inline-flex; align-items: center; gap: 9px; min-width: 150px; }
.profile-photo { width: 42px; height: 56px; flex: none; object-fit: cover; border: 1px solid var(--table-border); border-radius: 3px; background: var(--bg); }
.person-row { cursor: pointer; }
.person-row.open { background: var(--row-open); }
.toggle { color: var(--link); font-size: 0.8rem; }
.shop-kind { display: block; margin-top: 2px; color: var(--muted); font-size: 0.8rem; font-weight: normal; }
.latest { display: block; color: var(--muted); font-size: 0.85rem; }
#reviews th:last-child, #reviews .person-row > td:last-child { white-space: nowrap; }
.tally-cell div { margin-bottom: 4px; }
.chip { display: inline-block; margin: 0 4px 2px 0; padding: 1px 7px; border-radius: 10px; border: 1px solid transparent; background: var(--chip); color: var(--chip-fg); font-size: 0.82rem; white-space: nowrap; }
.tally-cell .chip, .review-params .chip { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
.chip.score { background: var(--score-bg); border-color: var(--score-border); color: var(--rating); font-weight: 600; }
.tally-cell .review-label { background: none; padding: 0; margin-right: 6px; }
.detail-row > td { background: var(--detail-bg); }
.report-list { max-width: 700px; margin: 0; padding-left: 0; list-style: none; }
.review-item { padding: 6px 0; border-top: 1px solid var(--row-border); }
.review-item:first-child { border-top: 0; }
.review-params { margin-top: 4px; }
.review-body > summary { display: flex; align-items: center; min-height: 44px; margin-top: 4px; padding: 4px 0; cursor: pointer; }
.review-detail { padding: 0 0 10px 4px; }
.review-meta { margin-top: 8px; }
.review-meta a { display: inline-flex; align-items: center; min-height: 44px; }
.review-rating { color: var(--rating); white-space: nowrap; }
.review-param { margin: 6px 0 0; color: var(--text-soft); white-space: pre-wrap; overflow-wrap: anywhere; }
.review-label { display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 3px; background: var(--head); color: var(--field-label); font-size: 0.8rem; }
.age { display: block; margin-top: 2px; color: var(--muted); font-size: 0.85rem; }
a { color: var(--link); }
.empty { padding: 18px; color: var(--muted); }
@media (max-width: 760px), (pointer: coarse) {
.reset-filters { min-height: 44px; }
}
@media (max-width: 700px) {
:root { --page-pad: 12px; }
h1 { font-size: 1.25rem; }
.filters { padding: 12px; }
.filter-primary { grid-template-columns: 1fr 1fr; gap: 9px; }
.filter-search { grid-column: 1 / -1; }
.filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.filters input, .filters select { min-height: 44px; }
.filter-footer { flex-wrap: wrap; }
.table-wrap { max-height: none; overflow: visible; border: 0; background: transparent; }
table, tbody, tr, td { display: block; width: 100%; }
thead { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
#reviews tbody tr { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin: 0 0 10px; padding: 12px; border: 1px solid var(--table-border); background: var(--panel); }
#reviews tbody tr.person-row.open { background: var(--row-open); }
#reviews tbody tr.detail-row { display: block; margin: -10px 0 10px; padding: 8px 12px; border: 1px solid var(--table-border); border-top: 0; }
#reviews tbody tr[hidden] { display: none !important; }
#reviews td { min-width: 0; padding: 4px 0; border: 0; overflow-wrap: anywhere; }
#reviews td:nth-child(1), #reviews td:nth-child(2), #reviews td:nth-child(4) { grid-column: 1 / -1; }
#reviews td:nth-child(1) { font-weight: 600; color: var(--field-label); }
#reviews td:nth-child(3)::before { content: "点数"; display: block; color: var(--muted); font-size: 0.8rem; }
#reviews td:nth-child(5)::before { content: "レポート"; display: block; color: var(--muted); font-size: 0.8rem; }
.average-cell { min-width: 0; white-space: normal; }
.profile-link { min-width: 0; }
.report-list { max-width: 100%; }
.review-rating { white-space: normal; }
}
</style>
</head>
<body>
""" + BACK_NAV + """
<h1>NS専門 女の子別レポート（仮）</h1>
<section class="filters" id="filters" aria-label="レポートの絞り込み">
<div class="filter-primary">
<label class="filter-field filter-search"><span>名前・店舗を検索</span><input id="search" type="search" placeholder="名前や店舗名を入力" aria-label="名前・店舗で検索"></label>
<label class="filter-field"><span>店舗</span><select id="shop" data-shop-filter><option value="">全店舗</option>SHOP_OPTIONS</select></label>
</div>
<div class="filter-section">
<div class="filter-section-title">詳細条件</div>
<div class="filter-grid">
<label class="filter-field"><span>店舗種別</span><select id="shop-type"><option value="">すべて</option><option value="luxury">高級</option><option value="other">高級以外</option></select></label>
<label class="filter-field"><span>リピート</span><select id="repeat"><option value="">すべて</option>REPEAT_OPTIONS</select></label>
<label class="filter-field"><span>推定年齢</span><select id="age"><option value="">すべて</option>AGE_OPTIONS</select></label>
<label class="filter-field"><span>並び順</span><select id="sort"><option value="score">総合スコア順</option><option value="date">新しい順</option><option value="total">総合点（平均）が高い順</option><option value="play">プレイ点（平均）が高い順</option></select></label>
</div>
</div>
<div class="filter-footer"><p class="result-count" aria-live="polite">表示 <strong id="visible-count">PEOPLE_COUNT</strong> / PEOPLE_COUNT 人</p><button class="reset-filters" id="reset-filters" type="button">条件をクリア</button></div>
<p class="score-note">総合スコア = 総合点の平均に全体平均（PRIOR_MEAN点）をPRIOR_WEIGHT件分加えて補正した値。件数が少ない人ほど全体平均に近づきます。</p>
</section>
<div class="table-wrap"><table id="reviews"><thead><tr><th>店舗</th><th>女の子</th><th>点数</th><th>リピート / おっぱい</th><th>レポート</th></tr></thead><tbody>ROWS</tbody></table>
<p id="empty" class="empty" hidden>該当する人はいません</p></div>
<script>
const $ = id => document.getElementById(id);
const tbody = document.querySelector('#reviews tbody');
const pairs = [...tbody.querySelectorAll('tr.person-row')].map(r => [r, r.nextElementSibling]);
// 点数順: 点数なしは常に最後、同点は新しい順
function applySort() {
    const key = $('sort').value;
    const byDate = (a, b) => b[0].dataset.date.localeCompare(a[0].dataset.date);
    const sorted = pairs.slice().sort((a, b) => {
        if (key === 'date') return byDate(a, b);
        const va = a[0].dataset[key], vb = b[0].dataset[key];
        if (va === '' || vb === '') return (va === '') - (vb === '') || byDate(a, b);
        return Number(vb) - Number(va) || byDate(a, b);
    });
    sorted.forEach(p => { tbody.appendChild(p[0]); tbody.appendChild(p[1]); });
}
function applyFilters() {
    const query = $('search').value.trim().toLocaleLowerCase(), shop = $('shop').value, repeat = $('repeat').value, ageRange = $('age').value, shopType = $('shop-type').value;
    let shown = 0;
    pairs.forEach(([row, detail]) => {
        let ageOk = true;
        if (ageRange) {
            const [low, high] = ageRange.split('-').map(Number);
            const age = row.dataset.age === '' ? NaN : Number(row.dataset.age);
            ageOk = Number.isFinite(age) && age >= low && age <= high;
        }
        const match = row.dataset.search.includes(query)
            && (!shop || row.dataset.shop === shop)
            && (!repeat || row.dataset.repeat.split('|').includes(repeat))
            && (!shopType || row.dataset.shopType === shopType)
            && ageOk;
        row.hidden = !match;
        detail.hidden = !match || !row.classList.contains('open');
        if (match) shown++;
    });
    $('visible-count').textContent = shown;
    $('empty').hidden = shown !== 0;
}
pairs.forEach(([row, detail]) => row.addEventListener('click', event => {
    if (event.target.closest('a')) return;
    const open = !row.classList.contains('open');
    pairs.forEach(([r, d]) => { r.classList.remove('open'); d.hidden = true; });
    if (open) { row.classList.add('open'); detail.hidden = false; }
}));
$('search').addEventListener('input', applyFilters);
['shop', 'shop-type', 'repeat', 'age'].forEach(id => $(id).addEventListener('change', applyFilters));
$('sort').addEventListener('change', applySort);
$('reset-filters').addEventListener('click', () => {
    $('filters').querySelectorAll('select').forEach(select => select.selectedIndex = 0);
    $('search').value = '';
    if (window.resetShopSearches) resetShopSearches();
    applySort();
    applyFilters();
});
applySort();
applyFilters();
</script>
""" + BACK_TO_TOP + """
</body>
</html>
"""


def load_profiles(records):
    people = {}
    for record in records:
        if record.get("cityheaven_url"):
            people.setdefault(record["cityheaven_url"], record["name"])
    try:
        from ns_senmon.profile_images import fetch_profile_images

        return fetch_profile_images(people)
    except Exception as error:  # 画像なしでも HTML は生成する
        print(f"プロフィール画像の取得をスキップ: {error}")
        return {}


def main():
    data = read_jsonl(INPUT_JSONL)
    records = data.sort_values(["date", "post_id"], ascending=False).to_dict("records")
    for record in records:
        if not isinstance(record["details"], dict):
            record["details"] = json.loads(record["details"])
        if not isinstance(record.get("cityheaven_url"), str):
            record["cityheaven_url"] = ""
    OUTPUT_HTML.write_text(build_html(records, load_profiles(records)), encoding="utf-8")
    print(f"HTMLを生成しました: {OUTPUT_HTML} ({len(records)}件 / {len(group_records(records))}人)")


if __name__ == "__main__":
    main()
