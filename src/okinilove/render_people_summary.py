import html
import re
from pathlib import Path

from bs4 import BeautifulSoup

ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
STAR_RE = re.compile(r"[★☆]{1,5}")
ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_HTML_PATH = ROOT_DIR / "docs" / "okinilove_remove_unknown.html"


def _cell_segments(cell):
    segments = []
    current = []
    for child in cell.contents:
        if getattr(child, "name", None) == "br":
            text = " ".join("".join(current).split()).strip()
            if text:
                segments.append(text)
            current = []
        elif getattr(child, "name", None):
            current.append(child.get_text(" ", strip=True))
        else:
            current.append(str(child))
    text = " ".join("".join(current).split()).strip()
    if text:
        segments.append(text)
    return segments


def _extract_rows(source_soup):
    records = []
    for row in source_soup.select("#girlsTable tbody tr, #reviews tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue

        profile_link = cells[2].find("a", href=True)
        review_items = row.select(".review-item")
        if review_items:
            profile_link = next(
                (
                    anchor
                    for anchor in row.find_all("a", href=True)
                    if not ARTICLE_URL_RE.match(anchor["href"])
                    and not anchor.find_parent(class_="review-item")
                ),
                None,
            )
        name = profile_link.get_text(" ", strip=True) if profile_link else cells[2].get_text(" ", strip=True)
        profile_url = profile_link.get("href", "") if profile_link else ""
        reports = []

        if review_items:
            name = profile_link.get_text(" ", strip=True) if profile_link else cells[1].get_text(" ", strip=True)
            for item in review_items:
                link = item.find("a", href=True)
                rating = item.select_one(".review-rating")
                gap = item.select_one(".review-gap")
                reports.append(
                    {
                        "url": link.get("href", "") if link else "",
                        "rating": rating.get_text(" ", strip=True) if rating else "",
                        "gap": gap.get_text(" ", strip=True) if gap else "",
                    }
                )
        else:
            report_links = [
                anchor.get("href", "")
                for anchor in cells[1].find_all("a", href=True)
                if ARTICLE_URL_RE.match(anchor.get("href", ""))
            ]
            ratings = _cell_segments(cells[4])
            gaps = _cell_segments(cells[3])
            count = max(len(report_links), len(ratings), len(gaps), 1)
            reports = [
                {
                    "url": report_links[index] if index < len(report_links) else "",
                    "rating": ratings[index] if index < len(ratings) else "",
                    "gap": gaps[index] if index < len(gaps) else "",
                }
                for index in range(count)
            ]

        records.append(
            {
                "shop": cells[0].get_text(" ", strip=True),
                "name": name,
                "profile_url": profile_url,
                "reports": reports,
            }
        )
    return records


def _person_groups(records):
    people = {}
    for record in records:
        key = record["profile_url"] or (record["shop"], record["name"])
        person = people.setdefault(
            key,
            {
                "shop": record["shop"],
                "name": record["name"],
                "profile_url": record["profile_url"],
                "reports": [],
            },
        )
        known_urls = {report["url"] for report in person["reports"] if report["url"]}
        person["reports"].extend(
            report for report in record["reports"]
            if not report["url"] or report["url"] not in known_urls
        )
    return people.values()


def _average_rating(reports):
    scores = []
    for report in reports:
        match = STAR_RE.search(report["rating"])
        if match:
            scores.append(match.group().count("★"))
    return (sum(scores) / len(scores), len(scores)) if scores else (None, 0)


def build_people_summary_html(source_html):
    source_soup = BeautifulSoup(source_html, "html.parser")
    people = list(_person_groups(_extract_rows(source_soup)))
    shops = sorted({person["shop"] for person in people if person["shop"]})
    rows_html = []

    for person in sorted(
        people,
        key=lambda item: (
            -(_average_rating(item["reports"])[0] or 0),
            item["shop"].casefold(),
            item["name"].casefold(),
        ),
    ):
        average, rated_count = _average_rating(person["reports"])
        average_text = f"{average:.1f} / 5" if average is not None else "評価なし"
        profile_link = (
            f'<a href="{html.escape(person["profile_url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(person["name"])}</a>'
            if person["profile_url"]
            else html.escape(person["name"])
        )
        report_items = []
        for report in person["reports"]:
            article_link = (
                f'<a href="{html.escape(report["url"], quote=True)}" target="_blank" rel="noopener noreferrer">記事を読む</a>'
                if report["url"]
                else "記事URLなし"
            )
            report_items.append(
                "<li class='review-item'>"
                f"<div class='review-meta'><span class='review-rating'>{html.escape(report['rating'])}</span>{article_link}</div>"
                f"<p class='review-gap'>{html.escape(report['gap']) or 'ギャップ評価なし'}</p></li>"
            )
        reports_html = (
            f"<details class='person-reports'><summary>レポート {len(person['reports'])}件</summary>"
            f"<ol>{''.join(report_items)}</ol></details>"
        )
        search_text = " ".join((person["shop"], person["name"])).casefold()
        rows_html.append(
            f'<tr data-shop="{html.escape(person["shop"], quote=True)}" '
            f'data-search="{html.escape(search_text, quote=True)}" '
            f'data-average="{average if average is not None else 0:.3f}">'
            f"<td>{html.escape(person['shop'])}</td><td>{profile_link}</td>"
            f"<td class='average-cell'><strong>{average_text}</strong></td>"
            f"<td>{rated_count} / {len(person['reports'])}</td><td>{reports_html}</td></tr>"
        )

    shop_options = "".join(
        f'<option value="{html.escape(shop, quote=True)}">{html.escape(shop)}</option>'
        for shop in shops
    )
    page = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>オキニラブ 人物別レビュー</title>
<style>
:root {{ color-scheme: light; font-family: "Yu Gothic UI", Meiryo, sans-serif; color: #20272d; background: #f2f4f5; }}
* {{ box-sizing: border-box; }}
body {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
h1 {{ margin: 0 0 16px; font-size: 1.4rem; }}
.filters {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }}
.filters input, .filters select {{ min-height: 38px; padding: 7px 10px; border: 1px solid #b9c2c9; border-radius: 4px; background: white; }}
.filters input {{ flex: 1 1 260px; }}
.table-wrap {{ max-height: calc(100vh - 150px); overflow: auto; border: 1px solid #cbd2d8; background: white; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e0e4e7; text-align: left; vertical-align: top; }}
thead th {{ position: sticky; top: 0; z-index: 1; background: #e9eef1; }}
tbody tr:hover {{ background: #f6f8f9; }}
.average-cell {{ min-width: 100px; white-space: nowrap; }}
.person-reports summary {{ cursor: pointer; color: #17617a; }}
.person-reports ol {{ min-width: 250px; max-width: 600px; margin: 8px 0 0; padding-left: 22px; }}
.review-item {{ padding: 8px 0; border-top: 1px solid #e5e8ea; }}
.review-meta {{ display: flex; justify-content: space-between; gap: 14px; }}
.review-rating {{ color: #865019; white-space: nowrap; }}
.review-gap {{ margin: 4px 0 0; color: #56616a; white-space: pre-wrap; }}
a {{ color: #17617a; }}
.empty {{ padding: 18px; color: #5a646d; }}
@media (max-width: 700px) {{ body {{ padding: 12px; }} th, td {{ padding: 8px; }} .table-wrap {{ max-height: none; }} }}
</style></head><body>
<h1>女の子別 評価・レポート</h1>
<div class="filters"><input id="search" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
<select id="shop"><option value="">全店舗</option>{shop_options}</select>
<select id="minimum"><option value="0">全評価</option><option value="3">平均3点以上</option><option value="4">平均4点以上</option><option value="5">平均5点</option></select></div>
<div class="table-wrap"><table id="reviews"><thead><tr><th>店舗</th><th>女の子</th><th>平均評価</th><th>評価数</th><th>レポート</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table>
<p id="empty" class="empty" hidden>該当する人はいません</p></div>
<script>
const searchInput=document.getElementById('search'), shopFilter=document.getElementById('shop'), minFilter=document.getElementById('minimum');
function applyFilters() {{ let shown=0; document.querySelectorAll('#reviews tbody tr').forEach(row=>{{const match=row.dataset.search.includes(searchInput.value.trim().toLocaleLowerCase())&&(!shopFilter.value||row.dataset.shop===shopFilter.value)&&Number(row.dataset.average)>=Number(minFilter.value);row.hidden=!match;if(match)shown++;}});document.getElementById('empty').hidden=shown!==0; }}
searchInput.addEventListener('input',applyFilters);shopFilter.addEventListener('change',applyFilters);minFilter.addEventListener('change',applyFilters);
</script></body></html>'''
    return BeautifulSoup(page, "html.parser")


def render_people_summary_file(path):
    path = Path(path)
    raw_html = path.read_text(encoding="utf-8")
    page = build_people_summary_html(raw_html)
    path.write_text(str(page), encoding="utf-8")
    return len(page.select("#reviews tbody tr"))


def main():
    summary_count = render_people_summary_file(RAW_HTML_PATH)
    print(f"人物別UIを生成: {summary_count}人")


if __name__ == "__main__":
    main()