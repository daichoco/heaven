import html
import json
import re
from collections import Counter
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
        if "detail-row" in (row.get("class") or []):
            continue
        cells = row.find_all("td", recursive=False)
        new_layout = "person-row" in (row.get("class") or [])
        if len(cells) < (4 if new_layout else 5):
            continue

        profile_link = cells[2].find("a", href=True) if len(cells) > 2 else None
        detail_row = row.find_next_sibling("tr") if new_layout else None
        review_items = (detail_row or row).select(".review-item")
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
        profile_image = profile_image or (cells[2].find("img") if len(cells) > 2 else None)
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


def _person_key(record):
    match = re.search(r"(?:[?&]ii=|/girl/)(\d+)", record["profile_url"] or "")
    return ("yoasobi", match.group(1)) if match else (record["shop"], record["name"])


def _person_groups(records):
    people, by_name = {}, {}
    for record in records:
        key = _person_key(record)
        name_key = (record["shop"], record["name"])
        if key not in people and name_key in by_name:
            key = by_name[name_key]
        by_name.setdefault(name_key, key)
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
        if "st_item.cfm" in (person["profile_url"] or "") and "st_item.cfm" not in (record["profile_url"] or ""):
            person["profile_url"] = record["profile_url"]
        if not person["profile_image_url"] and record.get("profile_image_url"):
            person["profile_image_url"] = record["profile_image_url"]
        def norm(url):
            return url.replace("http://", "https://", 1)

        known_urls = {norm(report["url"]) for report in person["reports"] if report["url"]}
        for report in record["reports"]:
            if report["url"] and norm(report["url"]) in known_urls:
                continue
            known_urls.add(norm(report["url"]))
            person["reports"].append(report)
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


def _load_review_details():
    """JSONLから記事URL別の顔・本文、プロフィールURL別の年齢を読む。"""
    path = ROOT_DIR / "data" / "okinilove_reviews.jsonl"
    by_report, ages = {}, {}
    if not path.exists():
        return by_report, ages
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        by_report[row.get("report_url") or ""] = row
        if row.get("age") and row.get("girl_url"):
            ages[row["girl_url"]] = row["age"]
    return by_report, ages


POSITIVE = {
    "face": {"かわいい", "キレイ", "きれい", "幼い"},
    "gap": {"写真より良い", "そのまま変りない", "そのまま変わりない"},
}
NEGATIVE = {
    "face": {"ブサイク", "老けてる"},
    "gap": {"写真より落ちる", "まったくの別人", "全くの別人"},
}


def _tone(kind, value):
    if value in POSITIVE[kind]:
        return "pos"
    if value in NEGATIVE[kind]:
        return "neg"
    return ""


def _clean_gap(text):
    text = (text or "").strip()
    if "■" in text or text == "ギャップ評価なし":
        return ""
    return text


def _face_tokens(text):
    return [token for token in re.split(r"[\s　]+", text or "") if token and "■" not in token]


def build_people_summary_html(source_html, profile_images=None):
    source_soup = BeautifulSoup(source_html, "html.parser")
    people = list(_person_groups(_extract_rows(source_soup)))
    profile_images = profile_images or {}
    details_by_report, ages = _load_review_details()
    details_path = ROOT_DIR / "data" / "okinilove_report_details.json"
    cache = json.loads(details_path.read_text(encoding="utf-8")) if details_path.exists() else {}
    shops = sorted({person["shop"] for person in people if person["shop"]})
    rows_html = []
    all_gaps, all_faces = Counter(), Counter()
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
        gap_counts, face_counts = Counter(), Counter()
        for report in person["reports"]:
            report["gap"] = _clean_gap(report["gap"])
            if report["gap"]:
                gap_counts[report["gap"]] += 1
            article_link = (
                f'<a href="{html.escape(report["url"], quote=True)}" target="_blank" rel="noopener noreferrer">記事を読む</a>'
                if report["url"]
                else "記事URLなし"
            )
            detail = {**details_by_report.get(report["url"], {}), **cache.get(report["url"], {})}
            face_counts.update(set(_face_tokens(detail.get("face"))))
            if not detail.get("report_text"):
                detail["report_text"] = details_by_report.get(report["url"], {}).get("report_text", "")
            face_html = "".join(
                f"<span class='chip {_tone('face', token)}'>{html.escape(token)}</span>"
                for token in _face_tokens(detail.get("face"))
            )
            params_html = "".join(
                f"<p class='review-param'><span class='review-label'>{label}</span>{html.escape(detail[key])}</p>"
                for label, key in (("スタイル", "style"),)
                if detail.get(key)
            )
            if face_html:
                params_html = f"<p class='review-param'><span class='review-label'>顔</span>{face_html}</p>" + params_html
            body_html = (
                f"<p class='review-text'>{html.escape(detail['report_text'])}</p>"
                if detail.get("report_text")
                else ""
            )
            report_items.append(
                "<li class='review-item'>"
                f"<div class='review-params'>{params_html}"
                f"<p class='review-param'><span class='review-label'>ギャップ</span><span class='review-gap chip {_tone("gap", report["gap"])}'>{html.escape(report['gap']) or 'ギャップ評価なし'}</span></p></div>"
                "<details class='review-body'>"
                f"<summary class='review-rating'>{html.escape(report['rating']) or '評価なし'}</summary>"
                f"<div class='review-detail'>{body_html}<div class='review-meta'>{article_link}</div></div></details></li>"
            )
        reports_html = f"<ol class='report-list'>{''.join(report_items)}</ol>"
        search_text = " ".join((person["shop"], person["name"])).casefold()
        age = ages.get(person["profile_url"], "")
        age_html = f"<span class='age'>{html.escape(str(age))}歳</span>" if age else ""
        def chips(counts, kind):
            return "".join(
                f"<span class='chip {_tone(kind, key)}'>{html.escape(key)}{f' ×{n}' if n > 1 else ''}</span>"
                for key, n in counts.most_common()
            )

        all_gaps.update(gap_counts)
        all_faces.update(face_counts)
        neg_total = sum(n for k, n in face_counts.items() if _tone("face", k) == "neg") + sum(
            n for k, n in gap_counts.items() if _tone("gap", k) == "neg"
        )
        value_total = sum(face_counts.values()) + sum(gap_counts.values())
        neg_ratio = neg_total / value_total if value_total else None
        neg_html = (
            f"<div><span class='review-label'>ネガ率</span><strong class='neg-rate'>{neg_ratio * 100:.0f}%</strong> "
            f"<span class='age'>({neg_total}/{value_total})</span></div>"
            if neg_ratio is not None
            else ""
        )
        data_gaps = "|".join(gap_counts)
        data_faces = "|".join(face_counts)
        rows_html.append(
            f'<tr class="person-row" data-shop="{html.escape(person["shop"], quote=True)}" '
            f'data-search="{html.escape(search_text, quote=True)}" '
            f'data-gaps="{html.escape(data_gaps, quote=True)}" data-faces="{html.escape(data_faces, quote=True)}" '
            f'data-neg="{neg_ratio if neg_ratio is not None else 2:.4f}" '
            f'data-negn="{neg_total}" data-vals="{value_total}" data-reports="{len(person["reports"])}" '
            f'data-average="{average if average is not None else 0:.3f}">'
            f"<td>{html.escape(person['shop'])}</td><td>{profile_link}{age_html}</td>"
            f"<td class='average-cell'><strong>{average_text}</strong></td>"
            f"<td class='tally-cell'><div><span class='review-label'>顔</span>{chips(face_counts, 'face') or '-'}</div>"
            f"<div><span class='review-label'>ギャップ</span>{chips(gap_counts, 'gap') or '-'}</div>{neg_html}</td>"
            f"<td>{rated_count} / {len(person['reports'])} <span class='toggle'>▼</span></td></tr>"
            f'<tr class="detail-row" hidden><td colspan="5">{reports_html}</td></tr>'
        )

    shop_options = "".join(
        f'<option value="{html.escape(shop, quote=True)}">{html.escape(shop)}</option>'
        for shop in shops
    )
    gap_options = "".join(
        f'<option value="{html.escape(key, quote=True)}">{html.escape(key)} ({n}人)</option>'
        for key, n in all_gaps.most_common()
    )
    face_options = "".join(
        f'<option value="{html.escape(key, quote=True)}">{html.escape(key)} ({n}人)</option>'
        for key, n in all_faces.most_common()
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
.filters input {{ flex: 1 1 180px; min-width: 150px; }}
.filters select {{ flex: 0 1 auto; max-width: 200px; }}
.table-wrap {{ max-height: calc(100vh - 150px); overflow: auto; border: 1px solid #cbd2d8; background: white; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #e0e4e7; text-align: left; vertical-align: top; }}
thead th {{ position: sticky; top: 0; z-index: 1; background: #e9eef1; }}
tbody tr:hover {{ background: #f6f8f9; }}
.average-cell {{ min-width: 100px; white-space: nowrap; }}
.profile-link {{ display: inline-flex; align-items: center; gap: 9px; min-width: 150px; }}
.profile-photo {{ width: 42px; height: 56px; flex: none; object-fit: cover; border: 1px solid #cbd2d8; border-radius: 3px; background: #f2f4f5; }}
.person-row {{ cursor: pointer; }}
.person-row.open {{ background: #eef4f6; }}
.toggle {{ color: #17617a; font-size: 0.8rem; }}
.tally-cell div {{ margin-bottom: 4px; }}
.neg-rate {{ color: #1e6b36; }}
.chip {{ display: inline-block; margin: 0 4px 2px 0; padding: 1px 7px; border-radius: 10px; border: 1px solid transparent; background: #eceff1; color: #56616a; font-size: 0.82rem; white-space: nowrap; }}
.chip.pos {{ background: #e0f2e5; border-color: #9ccfaa; color: #1e6b36; font-weight: 600; }}
.chip.neg {{ background: #fdeaea; border-color: #e7a3a3; color: #b3261e; text-decoration: underline wavy #e7a3a3; text-underline-offset: 3px; }}
.tally-cell .review-label {{ background: none; padding: 0; margin-right: 6px; }}
.detail-row > td {{ background: #fafbfb; }}
.report-list {{ max-width: 700px; margin: 0; padding-left: 0; list-style: none; }}
.review-item {{ padding: 6px 0; border-top: 1px solid #e5e8ea; }}
.review-item:first-child {{ border-top: 0; }}
.review-body > summary {{ margin-top: 4px; padding: 4px 0; cursor: pointer; }}
.review-detail {{ padding: 0 0 10px 4px; }}
.review-meta {{ margin-top: 8px; }}
.review-rating {{ color: #865019; white-space: nowrap; }}
.review-param {{ margin: 4px 0 0; color: #38434b; white-space: pre-wrap; }}
.review-gap {{ color: #56616a; }}
.review-label {{ display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 3px; background: #e9eef1; color: #56616a; font-size: 0.8rem; }}
.review-text {{ margin: 8px 0 0; padding: 8px 10px; border-left: 3px solid #cbd2d8; background: #f8f9fa; white-space: pre-wrap; color: #38434b; }}
.age {{ display: block; margin-top: 2px; color: #68747d; font-size: 0.85rem; }}
a {{ color: #17617a; }}
.empty {{ padding: 18px; color: #5a646d; }}
@media (max-width: 700px) {{
body {{ padding: 12px; }}
h1 {{ font-size: 1.25rem; }}
.filters {{ display: grid; grid-template-columns: 1fr; gap: 8px; }}
.filters input, .filters select {{ width: 100%; min-height: 44px; }}
.table-wrap {{ max-height: none; overflow: visible; border: 0; background: transparent; }}
table, tbody, tr, td {{ display: block; width: 100%; }}
thead {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
#reviews tbody tr {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin: 0 0 10px; padding: 12px; border: 1px solid #cbd2d8; background: white; }}
#reviews tbody tr.detail-row {{ display: block; margin: -10px 0 10px; padding: 8px 12px; border: 1px solid #cbd2d8; border-top: 0; }}
#reviews tbody tr[hidden] {{ display: none !important; }}
#reviews td {{ min-width: 0; padding: 4px 0; border: 0; overflow-wrap: anywhere; }}
#reviews td:nth-child(1), #reviews td:nth-child(2), #reviews td:nth-child(4) {{ grid-column: 1 / -1; }}
#reviews td:nth-child(1) {{ font-weight: 600; color: #56616a; }}
#reviews td:nth-child(3)::before {{ content: "平均評価"; display: block; color: #68747d; font-size: 0.8rem; }}
#reviews td:nth-child(5)::before {{ content: "評価数"; display: block; color: #68747d; font-size: 0.8rem; }}
.average-cell {{ min-width: 0; }}
.profile-link {{ min-width: 0; }}
.report-list {{ max-width: 100%; }}
.review-meta {{ margin-top: 8px; }}
}}
</style></head><body>
<h1>女の子別 評価・レポート</h1>
<div class="filters"><input id="search" type="search" placeholder="名前・店舗で検索" aria-label="名前・店舗で検索">
<select id="shop"><option value="">全店舗</option>{shop_options}</select>
<select id="minimum"><option value="0">全評価</option><option value="3">平均3点以上</option><option value="4">平均4点以上</option><option value="5">平均5点</option></select>
<select id="face"><option value="">顔: すべて</option>{face_options}</select>
<select id="gap"><option value="">ギャップ: すべて</option>{gap_options}</select>
<select id="negmax"><option value="">ネガ率: すべて</option><option value="0">ネガ率 0%のみ</option><option value="0.1">ネガ率 10%以下</option><option value="0.2">ネガ率 20%以下</option><option value="0.3">ネガ率 30%以下</option></select>
<select id="reports"><option value="0">評価数: すべて</option><option value="2">2件以上</option><option value="3">3件以上</option><option value="5">5件以上</option></select>
<select id="sort"><option value="average">並び: 平均評価順</option><option value="neg">並び: ネガ率が低い順</option><option value="adj">並び: 補正ネガ率が低い順(評価数考慮)</option></select></div>
<div class="table-wrap"><table id="reviews"><thead><tr><th>店舗</th><th>女の子</th><th>平均評価</th><th>顔 / ギャップ集計</th><th>評価数</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table>
<p id="empty" class="empty" hidden>該当する人はいません</p></div>
<script>
const searchInput=document.getElementById('search'), shopFilter=document.getElementById('shop'), minFilter=document.getElementById('minimum'), faceFilter=document.getElementById('face'), gapFilter=document.getElementById('gap'), negFilter=document.getElementById('negmax'), sortSelect=document.getElementById('sort');
const tbody=document.querySelector('#reviews tbody'), originalPairs=[...tbody.querySelectorAll('tr.person-row')].map(r=>[r,r.nextElementSibling]);
const totN=originalPairs.reduce((s,p)=>s+Number(p[0].dataset.negn),0), totV=originalPairs.reduce((s,p)=>s+Number(p[0].dataset.vals),0), prior=totV?totN/totV:0;
const adj=r=>(Number(r.dataset.negn)+5*prior)/(Number(r.dataset.vals)+5);
const reportsFilter=document.getElementById('reports');reportsFilter.addEventListener('change',applyFilters);
function applySort() {{ const pairs=originalPairs.slice(); if(sortSelect.value==='adj') pairs.sort((a,b)=>adj(a[0])-adj(b[0])||Number(b[0].dataset.average)-Number(a[0].dataset.average)); if(sortSelect.value==='neg') pairs.sort((a,b)=>Number(a[0].dataset.neg)-Number(b[0].dataset.neg)||Number(b[0].dataset.average)-Number(a[0].dataset.average)); pairs.forEach(p=>{{tbody.appendChild(p[0]);tbody.appendChild(p[1]);}}); }}
sortSelect.addEventListener('change',applySort);negFilter.addEventListener('change',applyFilters);
function applyFilters() {{ let shown=0; document.querySelectorAll('#reviews tbody tr.person-row').forEach(row=>{{const match=row.dataset.search.includes(searchInput.value.trim().toLocaleLowerCase())&&(!shopFilter.value||row.dataset.shop===shopFilter.value)&&Number(row.dataset.average)>=Number(minFilter.value)&&(!faceFilter.value||row.dataset.faces.split('|').includes(faceFilter.value))&&(!gapFilter.value||row.dataset.gaps.split('|').includes(gapFilter.value))&&(negFilter.value===''||Number(row.dataset.neg)<=Number(negFilter.value))&&Number(row.dataset.reports)>=Number(reportsFilter.value);row.hidden=!match;row.nextElementSibling.hidden=!match||!row.classList.contains('open');if(match)shown++;}});document.getElementById('empty').hidden=shown!==0; }}
document.querySelectorAll('#reviews tr.person-row').forEach(row=>row.addEventListener('click',event=>{{if(event.target.closest('a'))return;row.classList.toggle('open');row.nextElementSibling.hidden=!row.classList.contains('open');}}));
searchInput.addEventListener('input',applyFilters);shopFilter.addEventListener('change',applyFilters);minFilter.addEventListener('change',applyFilters);faceFilter.addEventListener('change',applyFilters);gapFilter.addEventListener('change',applyFilters);
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