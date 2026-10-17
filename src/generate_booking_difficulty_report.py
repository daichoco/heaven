"""予約困難に関する明示的な記述を3つのレポートDBから抽出する。"""
import json
import re
import sys
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl_records
from html_format import write_html
from tokai_fuzoku.report_store import normalize_profile_url

DATASETS = (
    ("NS専門", "ns_senmon_reports.jsonl", "ns_senmon", "ns_senmon_report.html"),
    ("オキニラブ", "okinilove_reviews.jsonl", "okinilove", "okinilove_remove_unknown.html"),
    ("東海風俗", "tokai_fuzoku_reports.jsonl", "tokai_fuzoku", "tokai_final_report.html"),
)
OUTPUT_HTML = ROOT_DIR / "docs" / "reservation_difficulty.html"
BOOKING_DIFFICULTY = re.compile(
    r"超予約困難|予約困難|"
    r"予約(?:が|は|の|を)?(?:全然|なかなか|あまり)?"
    r"(?:取れな(?:い|く|かった|くて)|とれな(?:い|く|かった|くて)|取れず|"
    r"取りにくい|取りづらい|難しい|厳しい|大変)|"
    r"なかなか予約(?:が)?(?:取れな(?:い|く|かった)|とれな(?:い|く|かった)|取りにくい|取りづらい)|"
    r"予約(?:が)?(?:いっぱい|完売)|予約(?:が)?(?:すぐ|即|瞬間で)?埋ま(?:る|って|り)|"
    r"予約開始.{0,30}(?:数分|\d+分|数秒|\d+秒).{0,20}(?:完売|埋まる)|"
    r"(?:出勤|予約枠).{0,15}(?:数分|\d+分|数秒|\d+秒)で(?:埋まる|満枠)|"
    r"予約の取れなさ",
    re.IGNORECASE,
)
NEGATED_DIFFICULTY = re.compile(
    r"^(?:ではない|じゃない|でない|では無い|じゃ無い|ではなく|"
    r"とは言えない|というほどではない)"
)
CONTRADICTORY_CONTEXT = re.compile(
    r"予約困難.{0,35}(?:ではない|じゃない|でない|では無い)|"
    r"(?:昔は|以前は).{0,80}予約困難.{0,80}(?:今は|現在は).{0,40}(?:空いて|余裕)|"
    r"予約困難.{0,80}(?:今は|現在は).{0,40}(?:空いて|余裕)",
    re.DOTALL,
)


def booking_evidence(text):
    """予約困難を肯定する記述だけを返す。将来の懸念や否定文は除外する。"""
    if not text or CONTRADICTORY_CONTEXT.search(text):
        return []

    evidence = []
    for match in BOOKING_DIFFICULTY.finditer(text):
        after = text[match.end():match.end() + 24]
        if NEGATED_DIFFICULTY.match(after):
            continue
        if re.match(r"(?:なる|なりそう|なる前|なる(?:と|前に))", after):
            continue
        start = max(0, match.start() - 115)
        end = min(len(text), match.end() + 145)
        excerpt = " ".join(text[start:end].split())
        if excerpt and excerpt not in evidence:
            evidence.append(excerpt)
    return evidence


def record_text(dataset, record):
    if dataset == "ns_senmon":
        values = record.get("details", {})
        return " ".join(str(value) for value in values.values()) if isinstance(values, dict) else ""
    if dataset == "okinilove":
        return " ".join(str(record.get(key) or "") for key in ("report_text", "evaluations"))
    return " ".join(
        str(record.get(key) or "")
        for key in ("total_comment", "repeat_comment", "play_comment", "style_comment")
    )


def safe_http_url(value):
    try:
        return value if urlsplit(value).scheme.lower() in {"http", "https"} else ""
    except (TypeError, ValueError):
        return ""


def extract_people():
    people = {}
    source_counts = defaultdict(int)

    for label, filename, dataset, _ in DATASETS:
        rows = read_jsonl_records(ROOT_DIR / "data" / filename)
        for row_number, record in enumerate(rows, start=1):
            excerpts = booking_evidence(record_text(dataset, record))
            if not excerpts:
                continue

            source_url = record.get("url") or record.get("report_url") or ""
            profile_url = (
                record.get("cityheaven_url")
                or record.get("girl_url")
                or ""
            )
            normalized_url = normalize_profile_url(profile_url)
            key = normalized_url or f"{dataset}:{source_url or row_number}"
            person = people.setdefault(
                key,
                {
                    "name": record.get("name") or record.get("girl_name") or "名前不明",
                    "names": [],
                    "shops": [],
                    "profile_url": profile_url,
                    "sources": [],
                },
            )
            name = record.get("name") or record.get("girl_name")
            shop = record.get("shop_name")
            if name and name not in person["names"]:
                person["names"].append(name)
            if shop and shop not in person["shops"]:
                person["shops"].append(shop)

            for excerpt in excerpts[:1]:
                evidence = {
                    "database": label,
                    "source_url": source_url,
                    "record": record,
                }
                if evidence not in person["sources"]:
                    person["sources"].append(evidence)
            source_counts[label] += 1

    ordered = sorted(
        people.values(),
        key=lambda person: (
            -len(person["sources"]),
            person["name"].casefold(),
        ),
    )
    return ordered, source_counts


def render_person(person):
    names = person["names"] or [person["name"]]
    shops = person["shops"]
    profile = person["profile_url"]
    name_text = " / ".join(names)
    heading = escape(name_text)
    profile = safe_http_url(profile)
    if profile:
        heading = (
            f'<a href="{escape(profile, quote=True)}" target="_blank" '
            f'rel="noopener">{heading}</a>'
        )
    if shops:
        heading += f' <span class="shop">（{escape(" / ".join(shops))}）</span>'

    source_items = []
    for item in person["sources"]:
        database = escape(item["database"])
        record_json = escape(json.dumps(item["record"], ensure_ascii=False, indent=2))
        source_url = safe_http_url(item["source_url"])
        source_attribute = (
            f' data-source-url="{escape(source_url, quote=True)}"' if source_url else ""
        )
        if source_url:
            source = (
                f'<a href="{escape(source_url, quote=True)}" '
                f'target="_blank" rel="noopener">元記事</a>'
            )
            docs_filename = next(
                filename for label, _, _, filename in DATASETS
                if label == item["database"]
            )
            docs_url = f"{docs_filename}?record={quote(source_url, safe='')}"
            docs_link = (
                f'<a href="{escape(docs_url, quote=True)}" target="_blank" '
                f'rel="noopener">該当データのdocsを表示</a>'
            )
        else:
            source = "元記事リンクなし"
            docs_link = ""
        source_items.append(
            f'<li class="evidence-record"{source_attribute}>'
            f'<span class="database">{database}</span> {source} {docs_link}'
            f'<details class="stored-record">'
            f'<summary>DBに保存された元データを全文表示（JSON）</summary>'
            f'<pre>{record_json}</pre></details></li>'
        )

    return f"""
<details class="person">
  <summary>{heading}<span class="evidence-count">{len(person["sources"])}件の根拠</span></summary>
  <ul class="evidence-list">{"".join(source_items)}</ul>
</details>
"""


def render_html(people, source_counts):
    database_total = sum(source_counts.values())
    people_html = "".join(render_person(person) for person in people)
    counts_html = " / ".join(
        f"{escape(label)} {source_counts.get(label, 0)}件"
        for label, _, _, _ in DATASETS
    )
    return f"""<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="color-scheme" content="light dark">
    <link rel="stylesheet" href="common.css">
    <script src="common.js" defer></script>
    <title>予約困難な人物</title>
    <style>
body {{ max-width: 960px; font-family: sans-serif; line-height: 1.65; }}
h1 {{ font-size: 1.5rem; }}
.summary {{ color: var(--muted); }}
.search {{ width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--input-border); border-radius: 5px; background: var(--input-bg); color: var(--fg); }}
.person {{ margin: 10px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }}
.person summary {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; cursor: pointer; font-weight: 600; }}
.shop, .evidence-count {{ color: var(--muted); font-size: 0.9rem; font-weight: normal; }}
.evidence-count {{ margin-left: auto; }}
.evidence-list {{ margin-bottom: 0; padding-left: 1.2rem; }}
.evidence-list li {{ margin: 12px 0; }}
.database {{ display: inline-block; margin-right: 6px; padding: 1px 7px; border-radius: 10px; background: var(--chip); color: var(--fg-soft); font-size: 0.82rem; }}
.stored-record {{ margin-top: 8px; }}
.stored-record > summary {{ display: inline-flex; align-items: center; min-height: 44px; cursor: pointer; color: var(--link); }}
.stored-record pre {{ max-height: 70vh; overflow: auto; margin: 4px 0; padding: 12px; border: 1px solid var(--border-soft); border-radius: 5px; background: var(--surface-alt); color: var(--fg-soft); white-space: pre-wrap; overflow-wrap: anywhere; }}
.method {{ margin-top: 22px; padding: 12px; border: 1px solid var(--border-soft); border-radius: 6px; color: var(--muted); font-size: 0.9rem; }}
.no-results {{ color: var(--muted); }}
@media (max-width: 600px) {{
  :root {{ --page-pad: 12px; }}
  h1 {{ font-size: 1.3rem; }}
  .person {{ padding: 10px; }}
  .evidence-count {{ margin-left: 0; }}
}}
    </style>
  </head>
  <body>
    <nav class="page-nav"><a href="index.html">← 一覧</a></nav>
    <h1>予約困難な人物</h1>
    <p class="summary">明示的な予約困難の記述が見つかった人物: {len(people)}人（根拠レコード {database_total}件）</p>
    <p class="summary">{counts_html}</p>
    <label for="personSearch">名前・店舗・根拠を検索</label>
    <input class="search" id="personSearch" type="search" autocomplete="off">
    <p class="summary" id="visibleCount" aria-live="polite"></p>
    <p class="no-results" id="noResults" hidden>該当する人物はいません。</p>
    <section id="people">{people_html}</section>
    <aside class="method">
      <strong>抽出方法と注意</strong>
      <p>対象は data/ns_senmon_reports.jsonl、data/okinilove_reviews.jsonl、data/tokai_fuzoku_reports.jsonl です。予約の取りづらさを明示する記述を含むレコードを抽出し、各カード内で該当レコードのJSONデータ全体を表示できます。本文や項目値は抜粋・要約せず、DBに保存された値をそのまま表示しています。否定文や「予約が取れなくなる前」などの将来の懸念は除外しました。</p>
      <p>3つのデータベースには元ページのHTMLソース自体は保存されていないため、表示するのは取得・保存済みのレコードデータです。これは保存済みレビューの文言検索結果であり、現在の出勤状況や予約可能性を確認したものではありません。記述時期・レビュアーの主観・本文が別の人物に言及している可能性があるため、各元記事で確認してください。人物の統合はプロフィールURLが一致する場合のみ行い、プロフィールURLのないレコードは個別に扱っています。</p>
      <p>生成日: {date.today().isoformat()}</p>
    </aside>
    <script>
      const people = [...document.querySelectorAll(".person")];
      const search = document.getElementById("personSearch");
      const linkedRecord = new URLSearchParams(location.search).get("record");
      if (linkedRecord) {{
        const linkedUrl = new URL(linkedRecord, location.href).href;
        const evidence = [...document.querySelectorAll(".evidence-record[data-source-url]")]
          .find(item => new URL(item.dataset.sourceUrl, location.href).href === linkedUrl);
        if (evidence) {{
          evidence.closest(".person").open = true;
          evidence.querySelector(".stored-record").open = true;
          evidence.scrollIntoView({{block: "center"}});
        }}
      }}
      function filterPeople() {{
        const query = search.value.trim().toLocaleLowerCase();
        let visible = 0;
        people.forEach(person => {{
          const matches = person.textContent.toLocaleLowerCase().includes(query);
          person.hidden = !matches;
          if (matches) visible += 1;
        }});
        document.getElementById("visibleCount").textContent = `${{visible}} / ${{people.length}} 人`;
        document.getElementById("noResults").hidden = visible !== 0;
      }}
      search.addEventListener("input", filterPeople);
      filterPeople();
    </script>
  </body>
</html>
"""


def main():
    people, source_counts = extract_people()
    write_html(OUTPUT_HTML, render_html(people, source_counts))
    print(
        f"予約困難レポートを生成: {OUTPUT_HTML} "
        f"({len(people)}人、根拠{sum(source_counts.values())}件)"
    )


if __name__ == "__main__":
    main()
