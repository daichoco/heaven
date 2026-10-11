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

SCORE_FIELDS = [
    ("顔", "姫の顔（点数）"),
    ("スタイル", "スタイル（点数）"),
    ("接客", "姫の性格・接客（点数）"),
    ("胸", "おっぱい（点数）"),
    ("膣", "膣の具合（点数）"),
    ("感度", "姫の感度（点数）"),
    ("プレイ", "プレイ満足度（点数）"),
    ("総合", "総合満足度（点数）"),
]
INFO_FIELDS = ["推定年齢", "推定身長", "中〇しした回数"]
REPEAT_OPTIONS = ["初めて", "2回目", "3回目", "4回以上"]
COMMENT_FIELDS = ["プレイコメント（対面から中〇し～事後までの流れ）", "総合コメント（全体を通じて。良かった点、物足りなかった点、リピの可否、など）"]


def score(details, key):
    """`顔65点` や `90点` から数値を取り出す。"""
    match = re.search(r"\d+", str(details.get(key, "")))
    return int(match.group()) if match else None


def field_value(details, prefix):
    for key, value in details.items():
        if key.startswith(prefix):
            return value
    return ""


def render_row(record):
    details = record["details"]
    scores = {label: score(details, key) for label, key in SCORE_FIELDS}
    cells = "".join(
        f"<td class='num'>{'' if scores[label] is None else scores[label]}</td>"
        for label, _ in SCORE_FIELDS
    )
    info = " / ".join(
        f"{html.escape(prefix)}: {html.escape(field_value(details, prefix))}"
        for prefix in INFO_FIELDS
        if field_value(details, prefix)
    )
    comments = "".join(
        f"<p>{html.escape(field_value(details, prefix)).replace(chr(10), '<br>')}</p>"
        for prefix in ("プレイコメント", "総合コメント")
        if field_value(details, prefix)
    )
    search_text = html.escape(f"{record['shop_name']} {record['name']} {record['title']}")
    repeat = html.escape(field_value(details, "姫のリピート"))
    return (
        f"<tr data-search='{search_text}' data-repeat='{repeat}'>"
        f"<td>{html.escape(str(record['date']))}</td>"
        f"<td>{html.escape(record['shop_name'])}</td>"
        f"<td><a href='{html.escape(record['url'])}' target='_blank' rel='noopener'>"
        f"{html.escape(record['name'])}</a></td>"
        f"<td>{repeat}</td>"
        f"{cells}"
        f"<td><details><summary>{info or '詳細'}</summary>{comments}</details></td>"
        "</tr>"
    )


def build_html(records):
    header = "".join(f"<th data-col='{4 + i}'>{label}</th>" for i, (label, _) in enumerate(SCORE_FIELDS))
    rows = "\n".join(render_row(record) for record in records)
    repeat_options = "".join(f"<option>{html.escape(option)}</option>" for option in REPEAT_OPTIONS)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NS専門 レポート（仮）</title>
<style>
body{{font-family:sans-serif;margin:12px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #ccc;padding:4px 6px;vertical-align:top}}
th{{background:#f0f0f0;position:sticky;top:0;cursor:pointer}}
td.num{{text-align:right;white-space:nowrap}}
details p{{margin:4px 0;max-width:640px}}
input{{padding:6px;width:260px;margin-bottom:8px}}
</style></head><body>
<h1>NS専門 レポート（仮）</h1>
<input id="q" type="search" placeholder="店名・姫名で絞り込み">
<select id="repeat"><option value="">リピート: すべて</option>{repeat_options}</select>
<span id="count">{len(records)}件</span>
<table id="t"><thead><tr><th>日付</th><th>店名</th><th>姫</th><th>リピート</th>{header}<th>詳細</th></tr></thead>
<tbody>
{rows}
</tbody></table>
<script>
const body=document.querySelector('#t tbody');
function applyFilter(){{
  const q=document.getElementById('q').value.trim().toLowerCase();
  const rep=document.getElementById('repeat').value;let n=0;
  for(const tr of body.rows){{
    const hit=(!q||tr.dataset.search.toLowerCase().includes(q))&&(!rep||tr.dataset.repeat===rep);
    tr.hidden=!hit;if(hit)n++;
  }}
  document.getElementById('count').textContent=n+'件';
}}
document.getElementById('q').addEventListener('input',applyFilter);
document.getElementById('repeat').addEventListener('change',applyFilter);
document.querySelectorAll('th[data-col]').forEach(th=>th.addEventListener('click',()=>{{
  const c=+th.dataset.col;
  const val=r=>parseFloat(r.cells[c].textContent)||-1;
  [...body.rows].sort((a,b)=>val(b)-val(a)).forEach(r=>body.appendChild(r));
}}));
</script></body></html>
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
