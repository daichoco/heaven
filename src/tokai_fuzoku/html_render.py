# html_renderer.py
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


def render_score_detail(score, detail, label):
    if pd.isna(score):
        return ""
    return f'''
        <div class="score-item">
            <h3 class="score-label">{label}: {score}</h3>
            <div class="report-comment">
                <p>{detail}</p>
            </div>
        </div>
    '''

def render_report_entry(row, i, shop_price_class=""):
    detail_columns = {
        "play_time":"total_cost",
        "face_score": "face_feature",
        "style_score": "style_comment",
        "cup": "pie_comment",
        "panel_point": "panel_gap",
        "play_score": "play_comment",
        "total_point": "total_comment",
        "repeat": "repeat_comment"
    }

    score_labels = {
        "play_time":"プレイ時間",
        "face_score": "顔",
        "style_score": "スタイル",
        "cup": "カップ",
        "panel_point": "パネル差",
        "play_score": "プレイ",
        "total_point": "総合",
        "repeat": "リピート"
    }
    overview_keys = ["face_score", "style_score", "panel_point", "play_score", "total_point"]

    shop = row.get("shop_name", "")
    typ = row.get("type", "")
    name = row.get("name", "")
    age = row.get("predict_age", "")
    age_str = ""
    if pd.notna(age):
        match = re.search(r"\d+", str(age))
        if match:
            age_str = f"{match.group()}才"

    url = row.get("url", "")
    girl_url = row.get("girl_url", "")
    image_url = row.get("profile_image_url", "")
    repeat_value = row.get("repeat", "")
    repeat_value = "" if pd.isna(repeat_value) else str(repeat_value)
    repeat_status = (
        "not-return" if "返さない" in repeat_value
        else "return" if "返す" in repeat_value
        else "other" if repeat_value
        else "unknown"
    )
    total_score = pd.to_numeric(row.get("total_point"), errors="coerce")
    total_attr = f"{total_score:g}" if pd.notna(total_score) else ""

    shop_type = f"{shop}・{typ}" if shop or typ else ""
    report_link = f'<a href="{url}" target="_blank" class="link-button">レポート</a>' if url else ""
    girl_link = f'<a href="{girl_url}" target="_blank" class="link-button">{name}</a>' if girl_url else ""
    profile_image = (
        f'<img class="profile-thumbnail" src="{escape(str(image_url), quote=True)}" '
        f'alt="{escape(str(name), quote=True)}のプロフィール画像" width="48" height="48" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
        if pd.notna(image_url) and image_url
        else ""
    )
    summary = f'({shop_type}) {profile_image} {girl_link} {age_str}（{report_link}）'

    score_overview = "".join(
        f'<span class="score-chip">{score_labels[key]} {row[key]:g}</span>'
        for key in overview_keys
        if pd.notna(row.get(key)) and str(row.get(key)).replace(".", "", 1).isdigit()
    )
    overview_html = f'<span class="score-overview">{score_overview}</span>' if score_overview else ""
    search_text = f"{shop} {typ} {name}".casefold()

    html = (
        f'<details class="report-entry" data-shop="{escape(str(shop), quote=True)}" '
        f'data-type="{escape(str(typ), quote=True)}" data-shop-type="{shop_price_class}" '
        f'data-repeat="{repeat_status}" '
        f'data-total="{total_attr}" data-search="{escape(search_text, quote=True)}">'
        f'<summary class="report-heading">{summary}{overview_html}</summary>'
        f'<div class="report-details">'
    )
    for key, detail_column in detail_columns.items():
        score = row.get(key, "")
        detail = row.get(detail_column, "")
        label = score_labels.get(key, key)
        html += render_score_detail(score, detail, label)
    html += '</div></details>'
    return html


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
    entries = "".join(
        render_report_entry(
            row,
            i,
            prices_by_url.get(normalize_profile_url(row.get("girl_url")), ""),
        )
        for i, row in df.iterrows()
    )
    return render_page(option_tags(shops), option_tags(types), entries)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
""" + HEAD_COMMON + """
<title>東海風俗レポート</title>
<style>
    .profile-thumbnail { width: 48px; height: 48px; margin: 0 6px; object-fit: cover; border: 1px solid var(--border); border-radius: 4px; vertical-align: middle; }
    @media (max-width: 600px) {
        .profile-thumbnail { width: 40px; height: 40px; margin: 0; }
    }
</style>
<script>
    function collapseAll() {
        document.querySelectorAll("details.report-entry").forEach(entry => entry.open = false);
    }
    function applyFilters() {
        const query = document.getElementById("reportSearch").value.trim().toLocaleLowerCase();
        const shop = document.getElementById("shopFilter").value;
        const type = document.getElementById("typeFilter").value;
        const shopType = document.getElementById("shopTypeFilter").value;
        const repeat = document.getElementById("repeatFilter").value;
        const minScore = Number(document.getElementById("scoreFilter").value);
        let visible = 0;

        document.querySelectorAll(".report-entry").forEach(entry => {
            const total = Number(entry.dataset.total);
            const matches = entry.dataset.search.includes(query)
                && (!shop || entry.dataset.shop === shop)
                && (!type || entry.dataset.type === type)
                && (!shopType || entry.dataset.shopType === shopType)
                && (!repeat || entry.dataset.repeat === repeat)
                && (!minScore || (Number.isFinite(total) && total >= minScore));
            entry.hidden = !matches;
            if (matches) visible += 1;
        });
        document.getElementById("filterCount").textContent = `${visible} / ${document.querySelectorAll(".report-entry").length} 件`;
        document.getElementById("noResults").hidden = visible !== 0;
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
        document.querySelectorAll(".sticky-toolbar input, .sticky-toolbar select").forEach(control => {
            control.addEventListener(control.tagName === "INPUT" ? "input" : "change", applyFilters);
        });
        applyFilters();
    });
</script>
</head>
<body class="report-page">
""" + BACK_NAV + """
<h1>東海風俗レポート</h1>
<div class="sticky-toolbar" id="toolbar">
    <div class="toolbar-row">
        <input id="reportSearch" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
        <button type="button" class="filter-toggle" id="filterToggle" aria-expanded="false" aria-controls="filterBar">絞り込み</button>
    </div>
    <div class="filter-bar" id="filterBar">
        <select id="shopFilter" data-shop-filter aria-label="店舗"><option value="">全店舗</option>SHOP_OPTIONS</select>
        <select id="typeFilter" aria-label="種別"><option value="">全種別</option>TYPE_OPTIONS</select>
        <select id="shopTypeFilter" aria-label="店舗種別"><option value="">店舗種別すべて</option><option value="luxury">高級</option><option value="other">高級以外</option></select>
        <select id="repeatFilter" aria-label="リピート状態"><option value="">全リピート状態</option><option value="return">返す</option><option value="not-return">返さない</option><option value="other">その他の記載</option><option value="unknown">未記載</option></select>
        <select id="scoreFilter" aria-label="総合点"><option value="0">総合点すべて</option><option value="50">50点以上</option><option value="60">60点以上</option><option value="70">70点以上</option><option value="80">80点以上</option><option value="90">90点以上</option><option value="100">100点</option></select>
        <button type="button" onclick="resetFilters()">条件をリセット</button>
    </div>
    <p id="filterCount" class="filter-count"></p>
</div>
<p id="noResults" class="no-results" hidden>条件に一致するレポートはありません。</p>
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
