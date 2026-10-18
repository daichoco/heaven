import hashlib
import html
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ARTICLE_URL_RE = re.compile(r"^https?://okinilove\.com/blog-entry-\d+", re.IGNORECASE)
STAR_RE = re.compile(r"[★☆]{1,5}")
ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from okinilove.shop_filters import is_chubby_shop
from html_format import write_html
from okinilove.scrape_okinilove import profile_check_url
from web_common import BACK_NAV, BACK_TO_TOP, HEAD_COMMON

RAW_HTML_PATH = ROOT_DIR / "docs" / "okinilove_remove_unknown.html"
# 人物ページから分離したレビュー本文 (顔・スタイル・サービス・本文)。HTML と同じフォルダに置く。
REVIEW_BODIES_NAME = "okinilove_review_bodies.json"
PROFILE_IMAGE_WORKERS = 8
PROFILE_IMAGE_HTML_LIMIT = 512 * 1024
SHOP_INDUSTRIES = {
    "ソープランド": "ソープランド",
    "デリバリーヘルス": "デリヘル",
    "デリヘル": "デリヘル",
    "ヘルス（箱ヘル）": "ヘルス",
    "ホテルヘルス": "ヘルス",
    "メンズエステ": "エステ",
    "風俗エステ": "エステ",
    "その他": "その他",
}
SHOP_INDEX_INDUSTRIES = {
    "70": "デリヘル",
    "71": "ヘルス",
    "72": "ソープランド",
}
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
        alt = image.get("alt", "")
        image_urls = [
            image.get(attribute)
            for attribute in ("src", "data-src", "data-original", "data-lazy-src", "data-echo")
            if image.get(attribute)
        ]
        for attribute in ("srcset", "data-srcset"):
            image_urls.extend(
                candidate.strip().split(" ", 1)[0]
                for candidate in image.get(attribute, "").split(",")
                if candidate.strip()
            )
        in_mitayo_widget = bool(
            image.find_parent(id="mitayoguid")
            or image.find_parent(class_="mitayoimg")
        )
        has_profile_image_path = any(
            "/img/girls/" in urljoin(page_url, image_url.strip()).lower()
            for image_url in image_urls
        )
        if in_mitayo_widget and not has_profile_image_path:
            continue
        for image_url in image_urls:
            add_candidate(image_url, alt)

    for score, image_url in sorted(candidates, key=lambda item: item[0], reverse=True):
        if score >= 20 and urlparse(image_url).scheme in {"http", "https"}:
            return image_url
    return None


def _fetch_profile_image(profile_url, person_name):
    try:
        response = requests.get(profile_check_url(profile_url), headers=HEADERS, timeout=(5, 15))
        if not 200 <= response.status_code < 400:
            print(
                f"プロフィール画像取得失敗 ({profile_url}): "
                f"HTTP {response.status_code}"
            )
            return None
        page_html = response.content[:PROFILE_IMAGE_HTML_LIMIT].decode(
            response.encoding or "utf-8",
            errors="replace",
        )
        image_url = extract_profile_image_url(page_html, response.url, person_name)
        if not image_url:
            print(f"プロフィール画像が見つかりません ({profile_url})")
        return image_url
    except requests.RequestException as error:
        print(f"プロフィール画像取得失敗 ({profile_url}): {error}")
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


def _normalize_shop_name(name):
    return re.sub(r"[\s　・（）()【】［］\[\]「」『』＆&!！]", "", name or "").casefold()


def _load_shop_industries():
    """既存の店舗カテゴリとレポート履歴から、業種が一意に判明する店舗を返す。"""
    industries_by_shop = {}

    index_path = ROOT_DIR / "data" / "okinilove_shop_index.json"
    if index_path.exists():
        shop_index = json.loads(index_path.read_text(encoding="utf-8"))
        index_industries = shop_index.get("industries", {})
        if index_industries:
            for shop, industry in index_industries.items():
                normalized_shop = _normalize_shop_name(shop)
                if normalized_shop:
                    industries_by_shop.setdefault(normalized_shop, set()).add(industry)
            for alias, shop in shop_index.get("aliases", {}).items():
                industry = index_industries.get(shop)
                normalized_alias = _normalize_shop_name(alias)
                if industry and normalized_alias:
                    industries_by_shop.setdefault(normalized_alias, set()).add(industry)
        else:
            for category in shop_index.get("categories", []):
                industry = SHOP_INDEX_INDUSTRIES.get(str(category))
                if not industry:
                    continue
                for shop in shop_index.get("shops", {}):
                    normalized_shop = _normalize_shop_name(shop)
                    if normalized_shop:
                        industries_by_shop.setdefault(normalized_shop, set()).add(industry)

    history_path = ROOT_DIR / "data" / "tokai_fuzoku_report_history.jsonl"
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            industry = SHOP_INDUSTRIES.get(row.get("shop_type", ""))
            shop = _normalize_shop_name(row.get("shop_name", ""))
            if industry and shop:
                industries_by_shop.setdefault(shop, set()).add(industry)

    return {
        shop: next(iter(industries))
        for shop, industries in industries_by_shop.items()
        if len(industries) == 1
    }


def _load_chubby_shop_names():
    index_path = ROOT_DIR / "data" / "okinilove_shop_index.json"
    if not index_path.exists():
        return set()

    shop_index = json.loads(index_path.read_text(encoding="utf-8"))
    industry_labels = shop_index.get("industry_labels", {})
    chubby_shops = set()
    for shop, labels_by_category in industry_labels.items():
        labels = [
            label
            for category_labels in labels_by_category.values()
            for label in category_labels
        ]
        if is_chubby_shop(shop, labels):
            chubby_shops.add(_normalize_shop_name(shop))
            chubby_shops.update(
                _normalize_shop_name(alias)
                for alias, canonical in shop_index.get("aliases", {}).items()
                if canonical == shop
            )
    return chubby_shops


def _industry_from_profile_html(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    shop_name = soup.select_one(".shopname")
    texts = [shop_name.get_text(" ", strip=True)] if shop_name else []
    if soup.title:
        texts.append(soup.title.get_text(" ", strip=True))
    if not texts:
        return None

    for shop_text in texts:
        if "ソープ" in shop_text:
            return "ソープランド"
        if "デリヘル" in shop_text or "デリバリーヘルス" in shop_text:
            return "デリヘル"
        if "ヘルス" in shop_text:
            return "ヘルス"
        if "エステ" in shop_text:
            return "エステ"
    return None


def _fetch_profile_shop_industry(profile_urls):
    for profile_url in profile_urls:
        hostname = (urlparse(profile_url).hostname or "").casefold()
        if not hostname.endswith(("cityheaven.net", "yoasobi.co.jp")):
            continue
        try:
            response = requests.get(profile_check_url(profile_url), headers=HEADERS, timeout=(5, 15))
            response.raise_for_status()
        except requests.RequestException as error:
            print(f"掲載URLから業種を取得できません ({profile_url}): {error}")
            continue
        industry = _industry_from_profile_html(response.content)
        if industry:
            return industry
    return None


def _load_profile_shop_industries(people, known_industries):
    profile_urls_by_shop = {}
    for person in people:
        shop_key = _normalize_shop_name(person["shop"])
        profile_url = person["profile_url"]
        if shop_key not in known_industries and profile_url:
            profile_urls_by_shop.setdefault(shop_key, set()).add(profile_url)

    if not profile_urls_by_shop:
        return {}

    resolved = {}
    with ThreadPoolExecutor(max_workers=PROFILE_IMAGE_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_profile_shop_industry, sorted(urls)): shop_key
            for shop_key, urls in profile_urls_by_shop.items()
        }
        for future in as_completed(futures):
            industry = future.result()
            if industry:
                resolved[futures[future]] = industry

    print(f"掲載URLから業種を取得: {len(resolved)} / {len(profile_urls_by_shop)}店舗")
    return resolved


POSITIVE = {
    "face": {"かわいい", "キレイ", "きれい"},
    "gap": {"写真より良い", "そのまま変りない", "そのまま変わりない"},
    "style": {"ナイスボディ"},
    "service": {"フェラ上手", "キス上手", "一生懸命", "テクニシャン", "手コキ上手", "いちゃいちゃ"},
}
NEUTRAL = {
    "face": {"並", "幼い", "幼い（追）"},
    "gap": set(),
    "style": {"お絵かきあり", "ちょいぽちゃ", "ガリガリ", "ムッチリ", "普通", "普通体型", "細い", "色白", "色黒"},
    "service": {
        "濃厚ベロチュー", "二度ヌキ以上", "即プレイ", "交渉次第で…",
        "喫煙有（タバコ/アイコス）", "追銭", "撮影", "期間限定/出稼ぎ",
        "A舐め", "AF（アナルファック）", "超SP", "SP（スペシャルサービス）",
        "SP", "ぎこちない",
    },
}
NEGATIVE = {
    "face": {"ブサイク", "老けてる"},
    "gap": {"写真より落ちる", "まったくの別人", "全くの別人"},
    "style": {"デブ", "ぽっちゃり", "おなかぽっこり", "お腹ポッコリ", "お腹ぽっこり", "おなかポッコリ"},
    "service": {"事務的", "サバサバ", "手抜き", "地雷"},
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
    return build_people_summary(source_html, profile_images)[0]


def build_people_summary(source_html, profile_images=None, fetch_missing_industries=True):
    """人物別ページ (BeautifulSoup) と、レビュー本文 JSON の文字列を返す。"""
    source_soup = BeautifulSoup(source_html, "html.parser")
    review_bodies = []
    people = list(_person_groups(_extract_rows(source_soup)))
    profile_images = profile_images or {}
    details_by_report, ages = _load_review_details()
    shop_industries = _load_shop_industries()
    if fetch_missing_industries:
        shop_industries.update(_load_profile_shop_industries(people, shop_industries))
    chubby_shops = _load_chubby_shop_names()
    details_path = ROOT_DIR / "data" / "okinilove_report_details.json"
    cache = json.loads(details_path.read_text(encoding="utf-8")) if details_path.exists() else {}
    shops = sorted({person["shop"] for person in people if person["shop"]})
    rows_html = []
    all_gaps, all_faces, all_industries = Counter(), Counter(), Counter()
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
                f'alt="{html.escape(person["name"], quote=True)}のプロフィール画像" width="42" height="56" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
                if image_url
                else ""
            )
            + f'<span>{html.escape(person["name"])}</span></a>'
            if person["profile_url"]
            else html.escape(person["name"])
        )
        report_items = []
        service_types = set()
        gap_counts, face_counts = Counter(), Counter()
        pos_total = neg_total = value_total = 0
        review_classes = Counter()
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
            face_tokens = _face_tokens(detail.get("face"))
            face_counts.update(sorted(set(face_tokens)))
            style_tokens = _face_tokens(detail.get("style"))
            service_tokens = _face_tokens(detail.get("service"))
            items = (
                [("face", token) for token in face_tokens]
                + [("style", token) for token in style_tokens]
                + [("service", token) for token in service_tokens]
                + [("gap", report["gap"])]
            )
            if any(value in POSITIVE[kind] | NEGATIVE[kind] | NEUTRAL[kind] for kind, value in items):
                value_total += 1
                has_pos = any(_tone(kind, value) == "pos" for kind, value in items)
                has_neg = any(_tone(kind, value) == "neg" for kind, value in items)
                pos_total += has_pos
                neg_total += has_neg
                review_classes[(has_pos, has_neg)] += 1
            if any(token == "SP" or token.startswith("SP（") for token in service_tokens):
                service_types.add("sp")
            if "超SP" in service_tokens:
                service_types.add("super-sp")
            if not detail.get("report_text"):
                detail["report_text"] = details_by_report.get(report["url"], {}).get("report_text", "")
            # 顔・スタイル・サービス・本文は別ファイル (REVIEW_BODIES_NAME) に分け、開いたときに読み込む
            body = {}
            faces = [[token, _tone("face", token)] for token in _face_tokens(detail.get("face"))]
            if faces:
                body["f"] = faces
            if style_tokens:
                body["s"] = [[token, _tone("style", token)] for token in style_tokens]
            if service_tokens:
                body["v"] = [[token, _tone("service", token)] for token in service_tokens]
            if detail.get("report_text"):
                body["t"] = detail["report_text"]
            rid_attr = ""
            if body:
                rid_attr = f" data-rid='{len(review_bodies)}'"
                review_bodies.append(body)
            report_items.append(
                f"<li class='review-item'{rid_attr}>"
                "<div class='review-params'>"
                f"<p class='review-param'><span class='review-label'>ギャップ</span><span class='review-gap chip {_tone("gap", report["gap"])}'>{html.escape(report['gap']) or 'ギャップ評価なし'}</span></p></div>"
                "<details class='review-body'>"
                f"<summary class='review-rating'>{html.escape(report['rating']) or '評価なし'}</summary>"
                f"<div class='review-detail'><div class='review-meta'>{article_link}</div></div></details></li>"
            )
        reports_html = f"<ol class='report-list'>{''.join(report_items)}</ol>"
        search_text = " ".join((person["shop"], person["name"])).casefold()
        age = ages.get(person["profile_url"], "")
        industry = shop_industries.get(_normalize_shop_name(person["shop"]), "不明")
        is_chubby = (
            _normalize_shop_name(person["shop"]) in chubby_shops
            or is_chubby_shop(person["shop"])
        )
        all_industries[industry] += 1
        age_html = f"<span class='age'>{html.escape(str(age))}歳</span>" if age else ""
        def chips(counts, kind):
            return "".join(
                f"<span class='chip {_tone(kind, key)}'>{html.escape(key)}{f' ×{n}' if n > 1 else ''}</span>"
                for key, n in counts.most_common()
            )

        all_gaps.update(gap_counts)
        all_faces.update(face_counts)
        neg_ratio = neg_total / value_total if value_total else None
        pos_ratio = pos_total / value_total if value_total else None
        neg_html = (
            f"<div><span class='review-label'>ポジ含有率</span><strong>{pos_ratio * 100:.0f}%</strong> "
            f"<span class='age'>({pos_total}/{value_total}件)</span></div>"
            f"<div title='ネガ項目を含むレビュー数 / 判定可能レビュー数'><span class='review-label'>ネガ含有率</span><strong class='neg-rate'>{neg_ratio * 100:.0f}%</strong> "
            f"<span class='age'>({neg_total}/{value_total}件)</span></div>"
            f"<div class='age'>ポジのみ{review_classes[(True, False)]} / 両方{review_classes[(True, True)]} / "
            f"ネガのみ{review_classes[(False, True)]} / 中立のみ{review_classes[(False, False)]}</div>"
            f"<div class='age'>判定可能{value_total}件 / 全{len(person['reports'])}件</div>"
            if neg_ratio is not None
            else "<div class='age'>判定可能レビューなし</div>"
        )
        data_gaps = "|".join(gap_counts)
        data_faces = "|".join(face_counts)
        rows_html.append(
            f'<tr class="person-row" data-shop="{html.escape(person["shop"], quote=True)}" '
            f'data-search="{html.escape(search_text, quote=True)}" '
            f'data-industry="{html.escape(industry, quote=True)}" '
            f'data-chubby="{"true" if is_chubby else "false"}" '
            f'data-gaps="{html.escape(data_gaps, quote=True)}" data-faces="{html.escape(data_faces, quote=True)}" '
            f'data-service-types="{"|".join(sorted(service_types))}" '
            f'data-neg="{neg_ratio if neg_ratio is not None else 2:.4f}" '
            f'data-posn="{pos_total}" data-rated="{rated_count}" '
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
    industry_options = "".join(
        f'<option value="{html.escape(key, quote=True)}">{html.escape(key)} ({n}人)</option>'
        for key, n in all_industries.most_common()
    )
    bodies_json = json.dumps(review_bodies, ensure_ascii=False, separators=(",", ":"))
    bodies_url = f"{REVIEW_BODIES_NAME}?v={hashlib.sha256(bodies_json.encode('utf-8')).hexdigest()[:12]}"
    page = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
{HEAD_COMMON}
<title>オキニラブ 人物別レビュー</title>
<style>
:root {{ --page-pad: 24px; --bg: #f2f4f5; --fg: #20272d; --link: #17617a; --panel: #fff; --panel-border: #d7dfe4; --field-label: #56616a; --field-border: #cbd4da; --focus: #28738a; --rule: #edf0f2; --head: #e9eef1; --row-hover: #f6f8f9; --row-open: #eef4f6; --detail-bg: #fafbfb; --chip: #eceff1; --chip-fg: #56616a; --pos-bg: #e0f2e5; --pos-border: #9ccfaa; --pos-fg: #1e6b36; --neg-bg: #fdeaea; --neg-border: #e7a3a3; --neg-fg: #b3261e; --rating: #865019; --text-soft: #38434b; --quote-bg: #f8f9fa; --table-border: #cbd2d8; --row-border: #e0e4e7; --muted: #68747d; }}
@media (prefers-color-scheme: dark) {{
:root {{ --bg: #15181b; --fg: #e3e6e9; --link: #6cc0d8; --panel: #1d2125; --panel-border: #30363c; --field-label: #9ba6af; --field-border: #4a535b; --focus: #4fa3bd; --rule: #2b3137; --head: #262c32; --row-hover: #22282d; --row-open: #1d2c33; --detail-bg: #191d20; --chip: #2b3239; --chip-fg: #b5bec6; --pos-bg: #16301f; --pos-border: #2f6b40; --pos-fg: #7fd49a; --neg-bg: #3a1b1b; --neg-border: #8a3c3c; --neg-fg: #ff8a80; --rating: #e0a35c; --text-soft: #c9d0d6; --quote-bg: #1a1e22; --table-border: #3b4249; --row-border: #30363c; --muted: #9ba6af; }}
}}
body {{ max-width: 1280px; font-family: "Yu Gothic UI", Meiryo, sans-serif; }}
h1 {{ margin: 0 0 16px; font-size: 1.4rem; }}
.filters {{ margin-bottom: 14px; padding: 16px; border: 1px solid var(--panel-border); border-radius: 10px; background: var(--panel); box-shadow: 0 2px 8px #2637460a; }}
.filter-primary {{ display: grid; grid-template-columns: minmax(220px, 1.6fr) minmax(180px, 1fr) minmax(160px, 0.8fr); gap: 12px; }}
.filter-field {{ display: grid; min-width: 0; gap: 5px; color: var(--field-label); font-size: 0.78rem; font-weight: 600; }}
.filters input, .filters select {{ width: 100%; min-width: 0; min-height: 40px; padding: 8px 10px; border: 1px solid var(--field-border); border-radius: 6px; background: var(--panel); color: var(--fg); font: inherit; font-size: 16px; }}
.filters input:focus, .filters select:focus {{ border-color: var(--focus); outline: 3px solid #28738a40; }}
.filter-search {{ position: relative; }}
.filter-search::before {{ position: absolute; left: 11px; bottom: 11px; color: #73818a; content: "⌕"; font-size: 1.15rem; line-height: 1; }}
.filters input[type="search"] {{ padding-left: 34px; }}
.filter-section {{ margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--rule); }}
.filter-section-title {{ margin-bottom: 9px; color: var(--text-soft); font-size: 0.82rem; font-weight: 700; }}
.filter-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 12px; }}
.filter-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; }}
.chubby-toggle {{ display: inline-flex; align-items: center; gap: 7px; min-height: 44px; color: var(--field-label); font-size: 0.85rem; cursor: pointer; }}
.chubby-toggle input {{ width: 20px; height: 20px; min-height: 0; margin: 0; }}
.result-count {{ margin: 0; color: var(--field-label); font-size: 0.85rem; }}
.result-count strong {{ color: var(--fg); font-size: 1rem; }}
.reset-filters {{ min-height: 36px; padding: 7px 14px; border: 1px solid var(--field-border); border-radius: 6px; background: var(--detail-bg); color: var(--text-soft); font: inherit; font-size: 0.9rem; cursor: pointer; }}
.reset-filters:hover {{ border-color: #8b9aa3; background: var(--head); }}
.table-wrap {{ max-height: calc(100vh - 150px); overflow: auto; border: 1px solid var(--table-border); background: var(--panel); }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--row-border); text-align: left; vertical-align: top; }}
thead th {{ position: sticky; top: 0; z-index: 1; background: var(--head); }}
tbody tr:hover {{ background: var(--row-hover); }}
.average-cell {{ min-width: 100px; white-space: nowrap; }}
.profile-link {{ display: inline-flex; align-items: center; gap: 9px; min-width: 150px; }}
.profile-photo {{ width: 42px; height: 56px; flex: none; object-fit: cover; border: 1px solid var(--table-border); border-radius: 3px; background: var(--bg); }}
.person-row {{ cursor: pointer; }}
.person-row.open {{ background: var(--row-open); }}
.toggle {{ color: var(--link); font-size: 0.8rem; }}
.tally-cell div {{ margin-bottom: 4px; }}
.neg-rate {{ color: var(--pos-fg); }}
.chip {{ display: inline-block; margin: 0 4px 2px 0; padding: 1px 7px; border-radius: 10px; border: 1px solid transparent; background: var(--chip); color: var(--chip-fg); font-size: 0.82rem; white-space: nowrap; }}
.chip.pos {{ background: var(--pos-bg); border-color: var(--pos-border); color: var(--pos-fg); font-weight: 600; }}
.chip.neg {{ background: var(--neg-bg); border-color: var(--neg-border); color: var(--neg-fg); text-decoration: underline wavy var(--neg-border); text-underline-offset: 3px; }}
.tally-cell .review-label {{ background: none; padding: 0; margin-right: 6px; }}
.detail-row > td {{ background: var(--detail-bg); }}
.report-list {{ max-width: 700px; margin: 0; padding-left: 0; list-style: none; }}
.review-item {{ padding: 6px 0; border-top: 1px solid var(--row-border); }}
.review-item:first-child {{ border-top: 0; }}
.review-body > summary {{ display: flex; align-items: center; min-height: 44px; margin-top: 4px; padding: 4px 0; cursor: pointer; }}
.review-detail {{ padding: 0 0 10px 4px; }}
.review-meta {{ margin-top: 8px; }}
.review-meta a {{ display: inline-flex; align-items: center; min-height: 44px; }}
.review-rating {{ color: var(--rating); white-space: nowrap; }}
.review-param {{ margin: 4px 0 0; color: var(--text-soft); white-space: pre-wrap; }}
.review-gap {{ color: var(--chip-fg); }}
.review-label {{ display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 3px; background: var(--head); color: var(--field-label); font-size: 0.8rem; }}
.review-text {{ margin: 8px 0 0; padding: 8px 10px; border-left: 3px solid var(--table-border); background: var(--quote-bg); white-space: pre-wrap; color: var(--text-soft); }}
.review-loading {{ margin: 6px 0; color: var(--muted); font-size: 0.85rem; }}
.review-loading button {{ margin-left: 8px; min-height: 36px; padding: 4px 12px; border: 1px solid var(--field-border); border-radius: 6px; background: var(--panel); color: var(--fg); font: inherit; cursor: pointer; }}
.age {{ display: block; margin-top: 2px; color: var(--muted); font-size: 0.85rem; }}
a {{ color: var(--link); }}
.empty {{ padding: 18px; color: var(--muted); }}
@media (max-width: 760px), (pointer: coarse) {{
.reset-filters, .review-loading button {{ min-height: 44px; }}
}}
@media (max-width: 700px) {{
:root {{ --page-pad: 12px; }}
h1 {{ font-size: 1.25rem; }}
.filters {{ padding: 12px; }}
.filter-primary {{ grid-template-columns: 1fr 1fr; gap: 9px; }}
.filter-search {{ grid-column: 1 / -1; }}
.filter-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }}
.filters input, .filters select {{ min-height: 44px; }}
.filter-footer {{ flex-wrap: wrap; }}
.table-wrap {{ max-height: none; overflow: visible; border: 0; background: transparent; }}
table, tbody, tr, td {{ display: block; width: 100%; }}
thead {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
#reviews tbody tr {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin: 0 0 10px; padding: 12px; border: 1px solid var(--table-border); background: var(--panel); }}
#reviews tbody tr.person-row.open {{ background: var(--row-open); }}
#reviews tbody tr.detail-row {{ display: block; margin: -10px 0 10px; padding: 8px 12px; border: 1px solid var(--table-border); border-top: 0; }}
#reviews tbody tr[hidden] {{ display: none !important; }}
#reviews td {{ min-width: 0; padding: 4px 0; border: 0; overflow-wrap: anywhere; }}
#reviews td:nth-child(1), #reviews td:nth-child(2), #reviews td:nth-child(4) {{ grid-column: 1 / -1; }}
#reviews td:nth-child(1) {{ font-weight: 600; color: var(--field-label); }}
#reviews td:nth-child(3)::before {{ content: "平均評価"; display: block; color: var(--muted); font-size: 0.8rem; }}
#reviews td:nth-child(5)::before {{ content: "評価数"; display: block; color: var(--muted); font-size: 0.8rem; }}
.average-cell {{ min-width: 0; }}
.profile-link {{ min-width: 0; }}
.report-list {{ max-width: 100%; }}
.review-meta {{ margin-top: 8px; }}
}}
</style></head><body>
{BACK_NAV}
<h1>女の子別 評価・レポート</h1>
<section class="filters" id="filters" aria-label="レビューの絞り込み">
<div class="filter-primary">
<label class="filter-field filter-search"><span>名前・店舗を検索</span><input id="search" type="search" placeholder="名前や店舗名を入力" aria-label="名前・店舗で検索"></label>
<label class="filter-field"><span>店舗</span><select id="shop" data-shop-filter><option value="">全店舗</option>{shop_options}</select></label>
<label class="filter-field"><span>業種</span><select id="industry" aria-label="業種"><option value="">すべて</option>{industry_options}</select></label>
</div>
<div class="filter-section">
<div class="filter-section-title">詳細条件</div>
<div class="filter-grid">
<label class="filter-field"><span>平均評価</span><select id="minimum"><option value="0">指定なし</option><option value="3">3点以上</option><option value="4">4点以上</option><option value="5">5点</option></select></label>
<label class="filter-field"><span>顔の評価</span><select id="face"><option value="">すべて</option>{face_options}</select></label>
<label class="filter-field"><span>写真とのギャップ</span><select id="gap"><option value="">すべて</option>{gap_options}</select></label>
<label class="filter-field"><span>気になる評価を含むレビュー率</span><select id="negmax"><option value="">指定なし</option><option value="0">0%のみ</option><option value="0.1">10%以下</option><option value="0.2">20%以下</option><option value="0.3">30%以下</option></select></label>
<label class="filter-field"><span>評価数</span><select id="reports"><option value="0">指定なし</option><option value="2">2件以上</option><option value="3">3件以上</option><option value="5">5件以上</option></select></label>
<label class="filter-field"><span>年齢</span><select id="age" aria-label="年齢区分"><option value="">指定なし</option><option value="under30">30歳未満</option><option value="30plus">30歳以上</option><option value="unknown">年齢不明</option></select></label>
<label class="filter-field"><span>SPサービス</span><select id="service-type"><option value="">指定なし</option><option value="sp">SPあり</option><option value="super-sp">超SPあり</option><option value="either">SPまたは超SPあり</option></select></label>
<label class="filter-field"><span>並び順</span><select id="sort"><option value="score">おすすめ順</option><option value="pos">ポジ含有率が高い順</option><option value="neg">ネガ含有率が低い順</option><option value="average">平均星が高い順</option><option value="count">判定可能レビュー数が多い順</option></select></label>
</div>
</div>
<div class="filter-footer"><p class="result-count" aria-live="polite">表示 <strong id="visible-count">{len(people)}</strong> / {len(people)} 人</p><label class="chubby-toggle"><input id="include-chubby" type="checkbox">ぽっちゃり系店舗も表示</label><button class="reset-filters" id="reset-filters" type="button">条件をクリア</button></div>
</section>
<p class="age">ポジ・ネガ含有率は顔・写真との差・スタイル・サービスをレビュー単位で集計します。おすすめ順は補正平均星（小数1桁）が高い順 → 補正ネガ率が低い順 → 補正ポジ率が高い順 → 判定可能レビュー数が多い順です。星・率は全体平均1件分で補正します。判定不能・未分類のみのレビューは率の集計対象外です。</p>
<div class="table-wrap"><table id="reviews"><thead><tr><th>店舗</th><th>女の子</th><th>平均評価</th><th>顔 / ギャップ集計</th><th>評価数</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table>
<p id="empty" class="empty" hidden>該当する人はいません</p></div>
<script>
const searchInput=document.getElementById('search'), shopFilter=document.getElementById('shop'), industryFilter=document.getElementById('industry'), minFilter=document.getElementById('minimum'), faceFilter=document.getElementById('face'), gapFilter=document.getElementById('gap'), negFilter=document.getElementById('negmax'), ageFilter=document.getElementById('age'), serviceTypeFilter=document.getElementById('service-type'), includeChubbyFilter=document.getElementById('include-chubby'), sortSelect=document.getElementById('sort');
const tbody=document.querySelector('#reviews tbody'), originalPairs=[...tbody.querySelectorAll('tr.person-row')].map(r=>[r,r.nextElementSibling]);
const count=r=>Number(r.dataset.vals), stars=r=>Number(r.dataset.average);
const totV=originalPairs.reduce((s,p)=>s+count(p[0]),0);
const prior=key=>totV?originalPairs.reduce((s,p)=>s+Number(p[0].dataset[key]),0)/totV:0;
const priorPos=prior('posn'), priorNeg=prior('negn');
const rated=r=>Number(r.dataset.rated);
const totRated=originalPairs.reduce((s,p)=>s+rated(p[0]),0);
const priorStar=totRated?originalPairs.reduce((s,p)=>s+rated(p[0])*stars(p[0]),0)/totRated:0;
const correctedStar=r=>Math.round(10*(rated(r)*stars(r)+priorStar)/(rated(r)+1));
const correctedRate=(r,key,baseline)=>(Number(r.dataset[key])+baseline)/(count(r)+1);
const rate=(r,key)=>Number(r.dataset[key])/count(r);
const evidenceTie=(a,b)=>count(b)-count(a)||stars(b)-stars(a);
const reportsFilter=document.getElementById('reports');reportsFilter.addEventListener('change',applyFilters);ageFilter.addEventListener('change',applyFilters);industryFilter.addEventListener('change',applyFilters);
function applySort() {{
  const mode=sortSelect.value, pairs=originalPairs.slice();
  pairs.sort(([a],[b])=>{{
    if(mode==='score') {{
      const missing=Number(rated(a)===0)-Number(rated(b)===0);
      if(missing)return missing;
      const starDifference=correctedStar(b)-correctedStar(a);
      if(starDifference)return starDifference;
      const missingEvaluation=Number(count(a)===0)-Number(count(b)===0);
      if(missingEvaluation)return missingEvaluation;
      return correctedRate(a,'negn',priorNeg)-correctedRate(b,'negn',priorNeg)
        ||correctedRate(b,'posn',priorPos)-correctedRate(a,'posn',priorPos)
        ||count(b)-count(a);
    }}
    if(['pos','neg'].includes(mode)) {{
      const missing=Number(count(a)===0)-Number(count(b)===0);
      if(missing)return missing;
      if(!count(a)&&!count(b))return evidenceTie(a,b);
      const difference=mode==='pos'?rate(b,'posn')-rate(a,'posn'):rate(a,'negn')-rate(b,'negn');
      return difference||evidenceTie(a,b);
    }}
    if(mode==='average')return stars(b)-stars(a)||Number(b.dataset.rated)-Number(a.dataset.rated)||count(b)-count(a);
    return evidenceTie(a,b);
  }});
  pairs.forEach(p=>{{tbody.appendChild(p[0]);tbody.appendChild(p[1]);}});
}}
sortSelect.addEventListener('change',applySort);negFilter.addEventListener('change',applyFilters);
function applyFilters() {{ let shown=0; document.querySelectorAll('#reviews tbody tr.person-row').forEach(row=>{{const ageText=row.cells[1].querySelector('.age')?.textContent||'', age=Number(ageText.match(/\\d+/)?.[0]||0), ageMatch=!ageFilter.value||(ageFilter.value==='unknown'?!age:ageFilter.value==='under30'?age>0&&age<30:age>=30), serviceTypes=row.dataset.serviceTypes.split('|'), serviceTypeMatch=!serviceTypeFilter.value||(serviceTypeFilter.value==='either'?serviceTypes.includes('sp')||serviceTypes.includes('super-sp'):serviceTypes.includes(serviceTypeFilter.value)), match=(includeChubbyFilter.checked||row.dataset.chubby!=='true')&&row.dataset.search.includes(searchInput.value.trim().toLocaleLowerCase())&&(!shopFilter.value||row.dataset.shop===shopFilter.value)&&(!industryFilter.value||row.dataset.industry===industryFilter.value)&&Number(row.dataset.average)>=Number(minFilter.value)&&(!faceFilter.value||row.dataset.faces.split('|').includes(faceFilter.value))&&(!gapFilter.value||row.dataset.gaps.split('|').includes(gapFilter.value))&&(negFilter.value===''||Number(row.dataset.neg)<=Number(negFilter.value))&&Number(row.dataset.reports)>=Number(reportsFilter.value)&&ageMatch&&serviceTypeMatch;row.hidden=!match;row.nextElementSibling.hidden=!match||!row.classList.contains('open');if(match)shown++;}});document.getElementById('visible-count').textContent=shown;document.getElementById('empty').hidden=shown!==0; }}
let bodiesPromise=null;
const loadBodies=()=>bodiesPromise||(bodiesPromise=fetch('{bodies_url}').then(r=>{{if(!r.ok)throw new Error(r.status);return r.json();}}).catch(e=>{{bodiesPromise=null;throw e;}}));
const el=(tag,cls,text)=>{{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}};
const param=(label,content)=>{{const p=el('p','review-param');p.append(el('span','review-label',label));typeof content==='string'?p.append(content):p.append(...content);return p;}};
function fillBodies(detailRow) {{
  const items=[...detailRow.querySelectorAll('li.review-item[data-rid]:not([data-filled])')];
  if(!items.length)return;
  detailRow.querySelector('.review-loading')?.remove();
  const status=el('p','review-loading','レビュー本文を読み込み中…');
  detailRow.querySelector('td').prepend(status);
  loadBodies().then(bodies=>{{
    status.remove();
    items.forEach(li=>{{
      const b=bodies[Number(li.dataset.rid)]||{{}}, params=li.querySelector('.review-params'), gap=params.firstElementChild, nodes=[];
      if(b.f)nodes.push(param('顔',b.f.map(([t,tone])=>el('span','chip '+tone,t))));
      if(b.s)nodes.push(param('スタイル',typeof b.s==='string'?b.s:b.s.map(([t,tone])=>el('span','chip '+tone,t))));
      if(b.v)nodes.push(param('サービス',typeof b.v==='string'?b.v:b.v.map(([t,tone])=>el('span','chip '+tone,t))));
      nodes.forEach(n=>params.insertBefore(n,gap));
      if(b.t)li.querySelector('.review-detail').prepend(el('p','review-text',b.t));
      li.dataset.filled='1';
    }});
  }}).catch(()=>{{
    status.textContent='レビュー本文を読み込めませんでした。';
    const retry=el('button','', '再読み込み');retry.type='button';
    retry.addEventListener('click',event=>{{event.stopPropagation();fillBodies(detailRow);}});
    status.append(retry);
  }});
}}
document.querySelectorAll('#reviews tr.person-row').forEach(row=>row.addEventListener('click',event=>{{if(event.target.closest('a'))return;const shouldOpen=!row.classList.contains('open');originalPairs.forEach(([person,detail])=>{{person.classList.remove('open');detail.hidden=true;}});if(shouldOpen){{row.classList.add('open');row.nextElementSibling.hidden=false;fillBodies(row.nextElementSibling);}}}}));
document.querySelectorAll('#reviews details.review-body').forEach(detail=>detail.addEventListener('toggle',()=>{{if(detail.open)document.querySelectorAll('#reviews details.review-body[open]').forEach(other=>{{if(other!==detail)other.open=false;}});}}));
const linkedRecord=new URLSearchParams(location.search).get('record');
if(linkedRecord){{const normalizeRecordUrl=value=>{{const url=new URL(value,location.href);if(url.protocol==='http:')url.protocol='https:';return url.href;}};includeChubbyFilter.checked=true;document.getElementById('filters')?.querySelectorAll('select').forEach(select=>select.selectedIndex=0);searchInput.value='';applyFilters();const target=[...document.querySelectorAll('.review-item .review-meta a')].find(link=>normalizeRecordUrl(link.href)===normalizeRecordUrl(linkedRecord)),detailRow=target?.closest('.detail-row'),personRow=detailRow?.previousElementSibling;if(target&&detailRow&&personRow){{originalPairs.forEach(([person,detail])=>{{person.classList.remove('open');detail.hidden=true;}});personRow.classList.add('open');detailRow.hidden=false;fillBodies(detailRow);target.closest('details.review-body').open=true;target.scrollIntoView({{block:'center'}});}}}}
searchInput.addEventListener('input',applyFilters);shopFilter.addEventListener('change',applyFilters);minFilter.addEventListener('change',applyFilters);faceFilter.addEventListener('change',applyFilters);gapFilter.addEventListener('change',applyFilters);serviceTypeFilter.addEventListener('change',applyFilters);includeChubbyFilter.addEventListener('change',applyFilters);
document.getElementById('reset-filters').addEventListener('click',()=>{{document.getElementById('filters')?.querySelectorAll('select').forEach(select=>select.selectedIndex=0);searchInput.value='';includeChubbyFilter.checked=false;if(window.resetShopSearches)resetShopSearches();applySort();applyFilters();}});
applySort();applyFilters();
</script>
{BACK_TO_TOP}
</body></html>'''
    return BeautifulSoup(page, "html.parser"), bodies_json


def render_people_summary_file(
    path,
    profile_images=None,
    fetch_missing_images=True,
    fetch_missing_industries=True,
):
    path = Path(path)
    raw_html = path.read_text(encoding="utf-8")
    source_soup = BeautifulSoup(raw_html, "html.parser")
    people = list(_person_groups(_extract_rows(source_soup)))
    if profile_images is None:
        profile_images = _fetch_profile_images(people)
    elif fetch_missing_images:
        profile_images = {**_fetch_profile_images(people), **profile_images}
    page, bodies_json = build_people_summary(
        raw_html,
        profile_images,
        fetch_missing_industries=fetch_missing_industries,
    )
    (path.parent / REVIEW_BODIES_NAME).write_text(bodies_json, encoding="utf-8")
    write_html(path, str(page))
    return len(page.select("#reviews tbody tr.person-row"))


def main():
    summary_count = render_people_summary_file(RAW_HTML_PATH)
    print(f"人物別UIを生成: {summary_count}人")


if __name__ == "__main__":
    main()