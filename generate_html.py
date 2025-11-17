from bs4 import BeautifulSoup

def generate_html(data, sorted_labels, today_label):
    html = f"""<html><head><meta charset='utf-8'>
<style>
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #ccc; padding: 5px; text-align: center; }}
img {{ max-width: 100px; }}
th.saturday {{ color: blue; }}
th.sunday {{ color: red; }}
.radio-group {{ margin-bottom: 10px; }}
</style>
<script>
function applyRadioFilter() {{
  const selected = document.querySelector('input[name="filter"]:checked').value;
  const rows = document.querySelectorAll('tbody tr');

  rows.forEach(row => {{
    let show = false;

    if (selected === "today") {{
      const cell = row.querySelector('[data-label="{today_label}"]');
      const text = cell ? cell.textContent.trim() : "";
      show = /^\\d{{1,2}}:\\d{{2}}\\s*~\\s*\\d{{1,2}}:\\d{{2}}$/.test(text);
    }} else if (selected === "weekend") {{
      row.querySelectorAll('td').forEach(cell => {{
        const label = cell.getAttribute('data-label');
        if (label && (label.includes('(土)') || label.includes('(日)'))) {{
          const text = cell.textContent.trim();
          if (/^\\d{{1,2}}:\\d{{2}}\\s*~\\s*\\d{{1,2}}:\\d{{2}}$/.test(text)) {{
            show = true;
          }}
        }}
      }});
    }} else {{
      show = true;
    }}

    row.style.display = show ? "" : "none";
  }});
}}

function applyDateFilter() {{
  const inputDate = document.getElementById("dateInput").value;
  if (!inputDate) return;

  const date = new Date(inputDate);
  const weekdays = ["日", "月", "火", "水", "木", "金", "土"];
  const label = `${{date.getMonth() + 1}}/${{date.getDate()}}(${{weekdays[date.getDay()]}})`;

  const rows = document.querySelectorAll('tbody tr');
  rows.forEach(row => {{
    let show = false;
    row.querySelectorAll('td').forEach(cell => {{
      const cellLabel = cell.getAttribute('data-label');
      if (cellLabel === label) {{
        const text = cell.textContent.trim();
        if (/^\\d{{1,2}}:\\d{{2}}\\s*~\\s*\\d{{1,2}}:\\d{{2}}$/.test(text)) {{
          show = true;
        }}
      }}
    }});
    row.style.display = show ? "" : "none";
  }});
}}
</script>
</head><body>
<h2>1週間のスケジュール一覧</h2>
<div class="radio-group">
  <label><input type="radio" name="filter" value="today" onchange="applyRadioFilter()"> 本日出勤のみ</label>
  <label><input type="radio" name="filter" value="weekend" onchange="applyRadioFilter()"> 土日出勤のみ</label>
  <label><input type="radio" name="filter" value="all" onchange="applyRadioFilter()" checked> 全表示</label>
</div>
<div class="date-filter">
  <label>日付で絞り込み: <input type="date" id="dateInput" onchange="applyDateFilter()"></label>
</div>
<table id='scheduleTable'>
<thead><tr>
<th>店</th><th>名前とURL</th><th>画像</th>"""

    for label in sorted_labels:
        if "(土)" in label:
            html += f"<th class='saturday'>{label}</th>"
        elif "(日)" in label:
            html += f"<th class='sunday'>{label}</th>"
        else:
            html += f"<th>{label}</th>"
    html += "</tr></thead><tbody>"

    for name, info in data.items():
        img_path = info["image"].replace("\\", "/")
        shop = info["shop"]
        html += "<tr>"
        html += f"<td><a href='{info['url']}' target='_blank'>{name}</a></td>"
        html += f"<td>{shop}</td>"
        html += f"<td><img src='{img_path}'></td>"
        for label in sorted_labels:
            val = info["schedule"].get(label, "-")
            html += f"<td data-label='{label}'>{val}</td>"
        html += "</tr>"

    html += "</tbody></table></body></html>"
    return BeautifulSoup(html, "html.parser").prettify()