# html_renderer.py
import pandas as pd
import re
from html import escape


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

def render_report_entry(row, i):
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
        f'alt="{escape(str(name), quote=True)}のプロフィール画像" loading="lazy" referrerpolicy="no-referrer">'
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
        f'data-type="{escape(str(typ), quote=True)}" data-repeat="{repeat_status}" '
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

def generate_html(df):
    shops = sorted(df["shop_name"].dropna().astype(str).unique()) if "shop_name" in df else []
    types = sorted(df["type"].dropna().astype(str).unique()) if "type" in df else []
    shop_options = "".join(
        f'<option value="{escape(value, quote=True)}">{escape(value)}</option>' for value in shops
    )
    type_options = "".join(
        f'<option value="{escape(value, quote=True)}">{escape(value)}</option>' for value in types
    )
    html = """
    <html lang="ja">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { font-family: sans-serif; max-width: 980px; margin: 0 auto; padding: 24px; color: #222; line-height: 1.65; }
        .link-button {
            color: #007BFF;
            text-decoration: underline;
        }
        .report-entry {
            margin-bottom: 1em;
            border: 1px solid #ccc;
            border-radius: 6px;
            padding: 0.75em;
            background-color: #f9f9f9;
            transition: background-color 0.2s ease;
        }
        .report-entry[open] { background-color: #eef6ff; }
        .report-heading { cursor: pointer; font-weight: bold; padding: 0.3em; outline: none; }
        .report-heading:hover { color: #007BFF; }
        .report-heading { margin: 0 0 0.5em; font-size: 1.05em; line-height: 1.5; }
        .score-overview { display: inline-flex; flex-wrap: wrap; gap: 6px; margin-left: 0.5em; vertical-align: middle; }
        .score-chip { padding: 2px 8px; border-radius: 4px; background: #e8eef4; font-size: 0.9em; }
        .profile-thumbnail { width: 48px; height: 48px; margin: 0 6px; object-fit: cover; border: 1px solid #ccd2d8; border-radius: 4px; vertical-align: middle; }
        .score-item { margin: 0.35em 0; }
        .score-label { margin: 0.25em 0; font-size: 1em; }
        .report-details { margin: 0.5em 0 0 0.75em; }
        .report-comment { max-width: 72ch; line-height: 1.8; color: #333; overflow-wrap: anywhere; }
        .report-comment p { margin: 0.35em 0 0.8em; white-space: pre-wrap; }
        button {
            margin-right: 10px;
            padding: 6px 12px;
            font-size: 14px;
            background-color: #007BFF;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover {
            background-color: #0056b3;
        }
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
            .profile-thumbnail { width: 40px; height: 40px; margin: 0; }
            .report-details { margin-left: 0; }
        }

        summary {
            font-weight: bold;
            cursor: pointer;
            padding: 0.3em;
            outline: none;
        }

        summary:hover {
            color: #007BFF;
        }

    </style>
    <script>
        function expandAll() {
            document.querySelectorAll("details.report-entry").forEach(entry => entry.open = true);
        }
        function collapseAll() {
            document.querySelectorAll("details.report-entry").forEach(entry => entry.open = false);
        }
        function applyFilters() {
            const query = document.getElementById("reportSearch").value.trim().toLocaleLowerCase();
            const shop = document.getElementById("shopFilter").value;
            const type = document.getElementById("typeFilter").value;
            const repeat = document.getElementById("repeatFilter").value;
            const minScore = Number(document.getElementById("scoreFilter").value);
            let visible = 0;

            document.querySelectorAll(".report-entry").forEach(entry => {
                const total = Number(entry.dataset.total);
                const matches = entry.dataset.search.includes(query)
                    && (!shop || entry.dataset.shop === shop)
                    && (!type || entry.dataset.type === type)
                    && (!repeat || entry.dataset.repeat === repeat)
                    && (!minScore || (Number.isFinite(total) && total >= minScore));
                entry.hidden = !matches;
                if (matches) visible += 1;
            });
            document.getElementById("filterCount").textContent = `${visible} / ${document.querySelectorAll(".report-entry").length} 件`;
            document.getElementById("noResults").hidden = visible !== 0;
        }
        function resetFilters() {
            document.getElementById("reportSearch").value = "";
            document.getElementById("shopFilter").value = "";
            document.getElementById("typeFilter").value = "";
            document.getElementById("repeatFilter").value = "";
            document.getElementById("scoreFilter").value = "0";
            applyFilters();
        }
        document.addEventListener("DOMContentLoaded", () => {
            document.querySelectorAll(".filter-bar input, .filter-bar select").forEach(control => {
                control.addEventListener(control.tagName === "INPUT" ? "input" : "change", applyFilters);
            });
            applyFilters();
        });
    </script>
    </head>
    <body>
    <h1>東海風俗レポート</h1>
    <div class="filter-bar">
        <input id="reportSearch" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
        <select id="shopFilter"><option value="">全店舗</option>SHOP_OPTIONS</select>
        <select id="typeFilter"><option value="">全種別</option>TYPE_OPTIONS</select>
        <select id="repeatFilter"><option value="">全リピート状態</option><option value="return">返す</option><option value="not-return">返さない</option><option value="other">その他の記載</option><option value="unknown">未記載</option></select>
        <select id="scoreFilter"><option value="0">総合点すべて</option><option value="50">50点以上</option><option value="60">60点以上</option><option value="70">70点以上</option><option value="80">80点以上</option><option value="90">90点以上</option><option value="100">100点</option></select>
        <button type="button" onclick="resetFilters()">条件をリセット</button>
    </div>
    <p id="filterCount" class="filter-count"></p>
    <p id="noResults" class="no-results" hidden>条件に一致するレポートはありません。</p>
    <div class="entry-controls">
        <button type="button" onclick="expandAll()">全部展開</button>
        <button type="button" onclick="collapseAll()">全部閉じる</button>
    </div>
    """
    for i, row in df.iterrows():
        html += render_report_entry(row, i)
    html = html.replace("SHOP_OPTIONS", shop_options).replace("TYPE_OPTIONS", type_options)
    html += "</body></html>"
    return html
