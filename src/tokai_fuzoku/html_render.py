# html_renderer.py
import math
import re
import sys
from html import escape
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from web_common import BACK_NAV, BACK_TO_TOP, FILTER_TOGGLE_SCRIPT, HEAD_COMMON
from ns_senmon.profile_images import classify_price_class, load_cache, normalize_profile_url
from tokai_fuzoku.report_store import is_missing, person_key

SCORE_FIELDS = (
    ("total_point", "total", "総合"),
    ("play_score", "play", "プレイ"),
    ("face_score", "face", "顔"),
    ("style_score", "style", "スタイル"),
    ("panel_point", "panel", "パネル差"),
)

REPORT_DETAIL_FIELDS = (
    ("play_time", "total_cost", "プレイ時間"),
    ("face_score", "face_feature", "顔"),
    ("style_score", "style_comment", "スタイル"),
    ("cup", "pie_comment", "カップ"),
    ("panel_point", "panel_gap", "パネル差"),
    ("play_score", "play_comment", "プレイ"),
    ("total_point", "total_comment", "総合"),
    ("repeat", "repeat_comment", "リピート"),
)


def sort_attributes(row):
    attributes = {}
    for name, column in (
        ("total", "total_point"), ("play", "play_score"),
        ("face", "face_score"), ("style", "style_score"),
    ):
        value = pd.to_numeric(row.get(column), errors="coerce")
        attributes[f"data-{name}"] = (
            f"{value:g}" if pd.notna(value) and math.isfinite(value) else ""
        )
    date = pd.to_datetime(row.get("date"), errors="coerce")
    attributes["data-date"] = date.strftime("%Y%m%d") if pd.notna(date) else ""
    return attributes


def render_score_detail(score, detail, label):
    if is_missing(score):
        return ""
    return f'''
        <div class="score-item">
            <h3 class="score-label">{escape(str(label))}: {escape(str(score))}</h3>
            <div class="report-comment">
                <p>{escape("" if is_missing(detail) else str(detail))}</p>
            </div>
        </div>
    '''


def _text(row, key):
    value = row.get(key, "")
    return "" if is_missing(value) else str(value)


def _finite_number(value):
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) and math.isfinite(float(number)) else None


def _date_value(value):
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed if pd.notna(parsed) else None


def _report_sort_key(item):
    parsed = _date_value(item[1].get("date"))
    return parsed is not None, parsed.value if parsed is not None else -1, -item[0]


def group_reports(df):
    """Group only rows with the same normalized profile URL; URL-less rows stay separate."""
    groups = {}
    for position, (_, row) in enumerate(df.iterrows()):
        record = row.to_dict()
        key = person_key(record)
        group_key = ("profile", key) if key is not None else ("article", position)
        group = groups.setdefault(group_key, {"first_index": position, "reports": []})
        group["reports"].append((position, record))

    people = []
    for group in groups.values():
        reports = sorted(group["reports"], key=_report_sort_key, reverse=True)
        latest = {}
        for key in ("name", "shop_name", "type", "predict_age", "girl_url", "profile_image_url"):
            latest[key] = next(
                (record.get(key) for _, record in reports if not is_missing(record.get(key))),
                None,
            )
        scores = {}
        score_counts = {}
        for field, sort_key, label in SCORE_FIELDS:
            values = [
                number
                for _, record in reports
                if (number := _finite_number(record.get(field))) is not None
            ]
            scores[sort_key] = sum(values) / len(values) if values else None
            score_counts[sort_key] = len(values)
        dates = [
            parsed
            for _, record in reports
            if (parsed := _date_value(record.get("date"))) is not None
        ]
        people.append({
            **group,
            "reports": reports,
            "latest": latest,
            "scores": scores,
            "score_counts": score_counts,
            "latest_date": max(dates) if dates else None,
            "search": " ".join(
                _text(record, field)
                for _, record in reports
                for field in ("shop_name", "type", "name")
            ).casefold(),
        })
    return people


def render_report_entry(row, i, shop_price_class=""):
    score_labels = {key: label for key, _, label in REPORT_DETAIL_FIELDS}
    overview_keys = ["face_score", "style_score", "panel_point", "play_score", "total_point"]

    shop = _text(row, "shop_name")
    typ = _text(row, "type")
    name = _text(row, "name")
    age = row.get("predict_age", "")
    age_str = ""
    if not is_missing(age):
        match = re.search(r"\d+", str(age))
        if match:
            age_str = f"{match.group()}才"

    url = _text(row, "url")
    repeat_value = _text(row, "repeat")
    repeat_status = (
        "not-return" if "返さない" in repeat_value
        else "return" if "返す" in repeat_value
        else "other" if repeat_value
        else "unknown"
    )
    sort_attrs = " ".join(
        f'{key}="{value}"' for key, value in sort_attributes(row).items()
    )

    shop_type = "・".join(part for part in (shop, typ) if part)
    report_link = (
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        'class="link-button">レポート</a>'
        if url else ""
    )
    date = _text(row, "date")
    summary = (
        f'<span class="article-date">{escape(date)}</span> '
        f'<span>{escape(shop_type)}</span> {escape(name)} {escape(age_str)}（{report_link}）'
    )

    score_overview = "".join(
        f'<span class="score-chip">{score_labels[key]} {escape(str(row[key]))}</span>'
        for key in overview_keys
        if _finite_number(row.get(key)) is not None
    )
    overview_html = f'<span class="score-overview">{score_overview}</span>' if score_overview else ""
    search_text = f"{shop} {typ} {name}".casefold()

    html = (
        f'<details class="article-entry" data-shop="{escape(shop, quote=True)}" '
        f'data-type="{escape(typ, quote=True)}" data-shop-type="{escape(shop_price_class, quote=True)}" '
        f'data-repeat="{repeat_status}" '
        f'{sort_attrs} data-search="{escape(search_text, quote=True)}">'
        f'<summary class="report-heading">{summary}{overview_html}</summary>'
        f'<div class="report-details">'
    )
    for key, detail_column, label in REPORT_DETAIL_FIELDS:
        score = row.get(key, "")
        detail = row.get(detail_column, "")
        html += render_score_detail(score, detail, label)
    html += '</div></details>'
    return html


def render_person_entry(person, prices_by_url):
    latest = person["latest"]
    name = "" if is_missing(latest["name"]) else str(latest["name"])
    shop = "" if is_missing(latest["shop_name"]) else str(latest["shop_name"])
    typ = "" if is_missing(latest["type"]) else str(latest["type"])
    age = latest["predict_age"]
    age_str = ""
    if not is_missing(age):
        match = re.search(r"\d+", str(age))
        if match:
            age_str = f"{match.group()}才"
    girl_url = "" if is_missing(latest["girl_url"]) else str(latest["girl_url"])
    image_url = "" if is_missing(latest["profile_image_url"]) else str(latest["profile_image_url"])
    profile_image = (
        f'<img class="profile-thumbnail" src="{escape(image_url, quote=True)}" '
        f'alt="{escape(name, quote=True)}のプロフィール画像" width="48" height="48" loading="lazy" '
        'decoding="async" referrerpolicy="no-referrer">'
        if image_url else ""
    )
    girl_link = (
        f'<a href="{escape(girl_url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'class="link-button">{escape(name)}</a>'
        if girl_url else escape(name)
    )
    identity = "・".join(part for part in (shop, typ) if part)
    average_chips = "".join(
        (
            f'<span class="score-chip">{label} '
            f'{person["scores"][sort_key]:.1f}（n={person["score_counts"][sort_key]}）</span>'
            if person["scores"][sort_key] is not None
            else f'<span class="score-chip">{label} 未評価</span>'
        )
        for _, sort_key, label in SCORE_FIELDS
    )
    latest_date = (
        person["latest_date"].strftime("%Y-%m-%d")
        if person["latest_date"] is not None else "未記載"
    )
    sort_values = {
        "data-total": person["scores"]["total"],
        "data-play": person["scores"]["play"],
        "data-face": person["scores"]["face"],
        "data-style": person["scores"]["style"],
        "data-date": person["latest_date"].strftime("%Y%m%d") if person["latest_date"] else "",
    }
    sort_attrs_list = []
    for key, value in sort_values.items():
        formatted = "" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))
        sort_attrs_list.append(f'{key}="{escape(formatted, quote=True)}"')
    sort_attrs = " ".join(sort_attrs_list)
    reports = "".join(
        render_report_entry(
            pd.Series(record),
            report_index,
            prices_by_url.get(normalize_profile_url(record.get("girl_url")), ""),
        )
        for report_index, record in person["reports"]
    )
    search = " ".join((identity, name, person["search"])).casefold()
    return (
        f'<details class="report-entry" {sort_attrs} data-search="{escape(search, quote=True)}">'
        f'<summary class="person-heading">{profile_image} <span class="person-identity">'
        f'({escape(identity)}) {girl_link} {escape(age_str)}</span> '
        f'<span class="person-report-count">レポート {len(person["reports"])}件</span> '
        f'<span class="person-latest-date">最新 {latest_date}</span>'
        f'<span class="score-overview">{average_chips}</span></summary>'
        f'<div class="person-details"><h2>個別レポート（{len(person["reports"])}件）</h2>'
        f'<p class="person-filter-status" aria-live="polite"></p>{reports}</div></details>'
    )


def option_tags(values):
    return "".join(
        f'<option value="{escape(value, quote=True)}">{escape(value)}</option>' for value in values
    )


def render_page(shop_options, type_options, entries_html):
    """option タグとレポート要素(HTML)からページ全体を組み立てる。"""
    return (
        PAGE_TEMPLATE.replace("SHOP_OPTIONS", shop_options)
        .replace("TYPE_OPTIONS", type_options)
        .replace("ENTRIES", entries_html)
    )


def generate_html(df):
    shops = sorted(df["shop_name"].dropna().astype(str).unique()) if "shop_name" in df else []
    types = sorted(df["type"].dropna().astype(str).unique()) if "type" in df else []
    cache = load_cache()
    prices_by_url = {
        normalize_profile_url(url): classify_price_class(entry)
        for url, entry in cache.items()
    }
    entries = "".join(render_person_entry(person, prices_by_url) for person in group_reports(df))
    return render_page(option_tags(shops), option_tags(types), entries)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
""" + HEAD_COMMON + """
<title>東海風俗レポート</title>
<style>
    .profile-thumbnail { width: 48px; height: 48px; margin: 0 6px; object-fit: cover; border: 1px solid var(--border); border-radius: 4px; vertical-align: middle; }
    .sort-control { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
    .sort-help { font-size: 0.85rem; color: var(--muted); }
    .person-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
    .person-identity { font-weight: 600; }
    .person-report-count, .person-latest-date, .person-filter-status { color: var(--muted); font-size: 0.9rem; }
    .person-details { padding: 8px 12px; }
    .person-details h2 { font-size: 1rem; margin: 8px 0; }
    .article-entry { margin: 6px 0; border: 1px solid var(--border); border-radius: 6px; }
    .article-entry > summary { padding: 8px; }
    .article-entry .report-details { padding: 0 8px 8px; }
    @media (max-width: 600px) {
        .profile-thumbnail { width: 40px; height: 40px; margin: 0; }
        .person-details { padding: 6px 4px; }
        .person-heading { align-items: flex-start; }
    }
</style>
<script>
    let reportEntries = [];
    function numericValue(entry, key) {
        const raw = entry.dataset[key];
        if (raw == null || raw.trim() === "") return null;
        const value = Number(raw);
        return Number.isFinite(value) ? value : null;
    }
    function compareDescending(a, b) {
        if (a === b) return 0;
        if (a === null) return 1;
        if (b === null) return -1;
        return b - a;
    }
    function applySort() {
        const mode = document.getElementById("reportSort").value;
        const keys = mode === "recommended"
            ? ["total", "play", "face", "style", "date"]
            : mode === "date" ? ["date"] : [mode, "date"];
        const sorted = reportEntries.slice().sort((a, b) => {
            for (const key of keys) {
                const difference = compareDescending(
                    numericValue(a.entry, key), numericValue(b.entry, key)
                );
                if (difference) return difference;
            }
            return a.index - b.index;
        });
        const fragment = document.createDocumentFragment();
        sorted.forEach(({entry}) => fragment.appendChild(entry));
        document.getElementById("entries").appendChild(fragment);
    }
    function collapseAll() {
        document.querySelectorAll("details.report-entry, details.article-entry")
            .forEach(entry => entry.open = false);
    }
    function applyFilters() {
        const query = document.getElementById("reportSearch").value.trim().toLocaleLowerCase();
        const shop = document.getElementById("shopFilter").value;
        const type = document.getElementById("typeFilter").value;
        const shopType = document.getElementById("shopTypeFilter").value;
        const repeat = document.getElementById("repeatFilter").value;
        const minScore = Number(document.getElementById("scoreFilter").value);
        let visiblePeople = 0;
        let visibleArticles = 0;

        document.querySelectorAll(".report-entry").forEach(entry => {
            const articles = [...entry.querySelectorAll(".article-entry")];
            let matchedArticles = 0;
            articles.forEach(article => {
                const total = numericValue(article, "total");
                const matches = article.dataset.search.includes(query)
                    && (!shop || article.dataset.shop === shop)
                    && (!type || article.dataset.type === type)
                    && (!shopType || article.dataset.shopType === shopType)
                    && (!repeat || article.dataset.repeat === repeat)
                    && (!minScore || (total !== null && total >= minScore));
                article.hidden = !matches;
                if (matches) matchedArticles += 1;
            });
            entry.hidden = matchedArticles === 0;
            const status = entry.querySelector(".person-filter-status");
            status.textContent = `${matchedArticles} / ${articles.length} 件が条件に一致`;
            if (matchedArticles) {
                visiblePeople += 1;
                visibleArticles += matchedArticles;
            }
        });
        const allArticles = document.querySelectorAll(".article-entry").length;
        document.getElementById("filterCount").textContent =
            `${visiblePeople} 人 / ${visibleArticles} 件（全 ${reportEntries.length} 人 / ${allArticles} 件）`;
        document.getElementById("noResults").hidden = visiblePeople !== 0;
        if (window.updateFilterToggle) updateFilterToggle();
    }
    function resetFilters() {
        document.getElementById("reportSearch").value = "";
        document.getElementById("shopFilter").value = "";
        document.getElementById("typeFilter").value = "";
        document.getElementById("shopTypeFilter").value = "";
        document.getElementById("repeatFilter").value = "";
        document.getElementById("scoreFilter").value = "0";
        if (window.resetShopSearches) resetShopSearches();
        applyFilters();
    }
    document.addEventListener("DOMContentLoaded", () => {
        reportEntries = [...document.querySelectorAll(".report-entry")]
            .map((entry, index) => ({entry, index}));
        document.getElementById("reportSort").addEventListener("change", applySort);
        document.querySelectorAll(".sticky-toolbar input, .sticky-toolbar select:not([data-sort])").forEach(control => {
            control.addEventListener(control.tagName === "INPUT" ? "input" : "change", applyFilters);
        });
        applySort();
        applyFilters();
        const linkedRecord = new URLSearchParams(location.search).get("record");
        if (linkedRecord) {
            const normalizeRecordUrl = value => {
                const url = new URL(value, location.href);
                if (url.protocol === "http:") url.protocol = "https:";
                return url.href;
            };
            const target = [...document.querySelectorAll(".article-entry a.link-button")]
                .find(link => normalizeRecordUrl(link.href) === normalizeRecordUrl(linkedRecord));
            const article = target?.closest(".article-entry");
            const person = article?.closest(".report-entry");
            if (target && article && person) {
                person.open = true;
                article.open = true;
                target.scrollIntoView({block: "center"});
            } else {
                const notice = document.createElement("p");
                notice.setAttribute("role", "status");
                notice.textContent = "この記事は一覧の表示条件により掲載されていません。保存データを確認: ";
                const fallback = document.createElement("a");
                fallback.href = `reservation_difficulty.html?record=${encodeURIComponent(linkedRecord)}`;
                fallback.textContent = "予約困難レポート";
                notice.append(fallback);
                document.querySelector("#entries")?.before(notice);
            }
        }
    });
</script>
</head>
<body class="report-page">
""" + BACK_NAV + """
<h1>東海風俗レポート</h1>
<div class="sticky-toolbar filters-open" id="toolbar">
    <div class="toolbar-row">
        <input id="reportSearch" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
        <button type="button" class="filter-toggle" id="filterToggle" aria-expanded="true" aria-controls="filterBar">絞り込み</button>
    </div>
    <div class="filter-bar" id="filterBar">
        <select id="shopFilter" data-shop-filter aria-label="店舗"><option value="">全店舗</option>SHOP_OPTIONS</select>
        <select id="typeFilter" aria-label="種別"><option value="">全種別</option>TYPE_OPTIONS</select>
        <select id="shopTypeFilter" aria-label="店舗種別"><option value="">店舗種別すべて</option><option value="luxury">高級</option><option value="other">高級以外</option></select>
        <select id="repeatFilter" aria-label="リピート状態"><option value="">全リピート状態</option><option value="return">返す</option><option value="not-return">返さない</option><option value="other">その他の記載</option><option value="unknown">未記載</option></select>
        <select id="scoreFilter" aria-label="総合点"><option value="0">総合点すべて</option><option value="50">50点以上</option><option value="60">60点以上</option><option value="70">70点以上</option><option value="80">80点以上</option><option value="90">90点以上</option><option value="100">100点</option></select>
        <button type="button" onclick="resetFilters()">条件をリセット</button>
    </div>
    <div class="sort-control">
        <label for="reportSort">並び順</label>
        <select id="reportSort" data-sort aria-describedby="sortHelp">
            <option value="recommended">おすすめ順</option>
            <option value="total">総合点順</option>
            <option value="play">プレイ点順</option>
            <option value="face">顔点順</option>
            <option value="style">スタイル点順</option>
            <option value="date">新着順</option>
        </select>
    </div>
    <p id="filterCount" class="filter-count"></p>
</div>
<p id="sortHelp" class="sort-help">おすすめ順は人物ごとの総合→プレイ→顔→スタイル平均の高い順です。欠損は各項目の末尾、同点は最新投稿日順です。リピート・パネル差は順位に含めません。点数ごとの n は平均対象のレポート数です。</p>
<p id="noResults" class="no-results" hidden>条件に一致する人物・レポートはありません。</p>
<div class="entry-controls">
    <button type="button" onclick="collapseAll()">全部閉じる</button>
</div>
<div id="entries">
ENTRIES
</div>
""" + FILTER_TOGGLE_SCRIPT + "\n" + BACK_TO_TOP + """
</body>
</html>
"""
