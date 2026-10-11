import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
STAR_RE = re.compile(r"[★☆]{1,5}")
ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_HTML_PATH = ROOT_DIR / "docs" / "okinilove_remove_unknown.html"
PROFILE_IMAGE_WORKERS = 8
PROFILE_IMAGE_HTML_LIMIT = 512 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
}


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
        profile_image = profile_link.find("img") if profile_link else None
        profile_image = profile_image or cells[2].find("img")
        profile_image_url = ""
        if profile_image:
            profile_image_url = (
                profile_image.get("src")
                or profile_image.get("data-src")
                or profile_image.get("data-original")
                or ""
            )
            if profile_image_url and profile_url:
                profile_image_url = urljoin(profile_url, profile_image_url)
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
                "profile_image_url": profile_image_url,
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
                "profile_image_url": record.get("profile_image_url", ""),
                "reports": [],
            },
        )
        if not person["profile_image_url"] and record.get("profile_image_url"):
            person["profile_image_url"] = record["profile_image_url"]
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


def extract_profile_image_url(page_html, page_url, person_name=""):
    soup = BeautifulSoup(page_html, "html.parser")
    candidates = []
    rejected_terms = (
        "favicon", "/icon/", "logo", "banner", "noimage", "no_image",
        "placeholder", "profile_circle", "/campaign/", "/nav/",
    )
    normalized_name = re.sub(r"\s+", "", person_name).casefold()

    def add_candidate(image_url, alt="", score=0):
        if not image_url:
            return
        resolved = urljoin(page_url, image_url.strip())
        lowered = resolved.lower()
        text = f"{lowered} {alt.lower()}"
        if any(term in text for term in rejected_terms) or "no photo" in text:
            return
        if "/img/girls/" in lowered:
            score += 100
        if "grpb" in lowered:
            score += 45
        elif "grpe" in lowered:
            score += 40
        elif "grdr" in lowered:
            score += 25
        if "/mb/cnv/img_item_jpg/" in lowered:
            score += 90
        if "fuzoku-images.ranking-deli.jp" in lowered:
            score += 80
        if "/photos/" in lowered:
            score += 75
        normalized_alt = re.sub(r"\s+", "", alt).casefold()
        if normalized_name and normalized_name in normalized_alt:
            score += 50
        candidates.append((score, resolved))

    for meta in soup.find_all("meta"):
        if meta.get("property", "").lower() == "og:image" or meta.get("name", "").lower() in {
            "twitter:image",
            "twitter:image:src",
        }:
            add_candidate(meta.get("content"), score=80)

    for image in soup.find_all("img"):
        if image.find_parent(id="mitayoguid") or image.find_parent(class_="mitayoimg"):
            continue
        alt = image.get("alt", "")
        for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
            add_candidate(image.get(attribute), alt)
        for attribute in ("srcset", "data-srcset"):
            for candidate in image.get(attribute, "").split(","):
                add_candidate(candidate.strip().split(" ", 1)[0], alt)

    for score, image_url in sorted(candidates, key=lambda item: item[0], reverse=True):
        if score >= 20 and urlparse(image_url).scheme in {"http", "https"}:
            return image_url
    return None


def _fetch_profile_image(profile_url, person_name):
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=(5, 15))
        if not 200 <= response.status_code < 400:
            return None
        page_html = response.content[:PROFILE_IMAGE_HTML_LIMIT].decode(
            response.encoding or "utf-8",
            errors="replace",
        )
        return extract_profile_image_url(page_html, response.url, person_name)
    except requests.RequestException:
        return None


def _fetch_profile_images(people):
    images = {
        person["profile_url"]: person["profile_image_url"]
        for person in people
        if person["profile_url"] and person["profile_image_url"]
    }
    pending = {
        person["profile_url"]: person["name"]
        for person in people
        if person["profile_url"] and not person["profile_image_url"]
    }
    if not pending:
        return images

    found = 0
    with ThreadPoolExecutor(max_workers=PROFILE_IMAGE_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_profile_image, url, name): url
            for url, name in pending.items()
        }
        for future in as_completed(futures):
            image_url = future.result()
            if image_url:
                images[futures[future]] = image_url
                found += 1
    print(f"プロフィール画像を取得: {found} / {len(pending)}件")
    return images


def build_people_summary_html(source_html, profile_images=None):
    source_soup = BeautifulSoup(source_html, "html.parser")
    people = list(_person_groups(_extract_rows(source_soup)))
    profile_images = profile_images or {}
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
        image_url = person["profile_image_url"] or profile_images.get(person["profile_url"], "")
        profile_link = (
            f'<a class="profile-link" href="{html.escape(person["profile_url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            + (
                f'<img class="profile-photo" src="{html.escape(image_url, quote=True)}" '
                f'alt="{html.escape(person["name"], quote=True)}のプロフィール画像" loading="lazy" referrerpolicy="no-referrer">'
                if image_url
                else ""
            )
            + f'<span>{html.escape(person["name"])}</span></a>'
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
.profile-link {{ display: inline-flex; align-items: center; gap: 9px; min-width: 150px; }}
.profile-photo {{ width: 42px; height: 56px; flex: none; object-fit: cover; border: 1px solid #cbd2d8; border-radius: 3px; background: #f2f4f5; }}
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
    source_soup = BeautifulSoup(raw_html, "html.parser")
    people = list(_person_groups(_extract_rows(source_soup)))
    profile_images = _fetch_profile_images(people)
    page = build_people_summary_html(raw_html, profile_images)
    path.write_text(str(page), encoding="utf-8")
    return len(page.select("#reviews tbody tr"))


def main():
    summary_count = render_people_summary_file(RAW_HTML_PATH)
    print(f"人物別UIを生成: {summary_count}人")


if __name__ == "__main__":
    main()