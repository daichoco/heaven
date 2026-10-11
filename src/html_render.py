# html_renderer.py
import pandas as pd
import re


def render_score_accordion(score, detail, label, modal_id):
    if pd.isna(score):
        return ""
    return f'''
        <details open style="margin-bottom: 0.5em;">
            <summary>{label}: {score}</summary>
            <div style="margin-left: 1em; margin-top: 5px;">
                <p>{detail}</p>
            </div>
        </details>
    '''

def render_accordion_row(row, i):
    modal_map = {
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

    shop_type = f"{shop}・{typ}" if shop or typ else ""
    report_link = f'<a href="{url}" target="_blank" class="link-button">レポート</a>' if url else ""
    girl_link = f'<a href="{girl_url}" target="_blank" class="link-button">{name}</a>' if girl_url else ""
    summary = f'({shop_type}) {girl_link} {age_str}（{report_link}）'

    html = f'<details><summary>{summary}</summary><div style="margin-left:1em; margin-top:5px;">'
    for key in modal_map:
        score = row.get(key, "")
        detail = row.get(modal_map[key], "")
        label = score_labels.get(key, key)
        modal_id = f"{key}_{i}"
        html += render_score_accordion(score, detail, label, modal_id)
    html += '</div></details>'
    return html

def generate_html(df):
    html = """
    <html>
    <head>
    <meta charset="UTF-8">
    <style>
        body { font-family: sans-serif; padding: 20px; }
        details { margin-bottom: 1em; }
        summary { font-weight: bold; cursor: pointer; }
        .link-button {
            color: #007BFF;
            text-decoration: underline;
        }
        details {
        margin-bottom: 1em;
        border: 1px solid #ccc;
        border-radius: 6px;
        padding: 0.5em;
        background-color: #f9f9f9;
        transition: background-color 0.3s ease;
        }
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
        details[open] {
            background-color: #eef6ff;
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

        details > div {
            margin-top: 0.5em;
            padding-left: 1em;
            font-size: 0.95em;
            line-height: 1.5;
            color: #333;
        }
    </style>
    <script>
    function expandAll() {
    document.querySelectorAll("details").forEach(el => el.setAttribute("open", true));
    }
    function collapseAll() {
    document.querySelectorAll("details").forEach(el => el.removeAttribute("open"));
    }
    </script>
    </head>
    <body>
    <div style="margin-bottom: 1em;">
        <button onclick="expandAll()">全部展開</button>
        <button onclick="collapseAll()">全部閉じる</button>
    </div>

    """
    for i, row in df.iterrows():
        html += render_accordion_row(row, i)
    html += "</body></html>"
    return html
