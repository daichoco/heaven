"""予約困難に関する明示的な記述を3つのレポートDBから抽出する。"""
import argparse
import json
import math
import re
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

import pandas as pd
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl_records
from html_format import write_html
from tokai_fuzoku.report_store import (
    latest_profile_status_by_person,
    normalize_profile_url,
    person_key,
)
from tokai_fuzoku.html_render import group_reports, render_person_entry
from okinilove.render_people_summary import build_people_summary
from okinilove.scrape_okinilove import (
    PROFILE_STATUS_CACHE_TTL,
    profile_check_url,
)
from ns_senmon.profile_images import CACHE_PATH as NS_PROFILE_IMAGE_CACHE_PATH
from ns_senmon.generate_ns_senmon_report import TOTAL_KEY, number
from shop_prices import attach_prices, refresh_saved_prices

DATASETS = (
    ("NS専門", "ns_senmon_reports.jsonl", "ns_senmon", "ns_senmon_report.html"),
    ("オキニラブ", "okinilove_reviews.jsonl", "okinilove", "okinilove_remove_unknown.html"),
    ("東海風俗", "tokai_fuzoku_reports.jsonl", "tokai_fuzoku", "tokai_final_report.html"),
)
OUTPUT_HTML = ROOT_DIR / "docs" / "reservation_difficulty.html"
OKINILOVE_PROFILE_STATUS_PATH = ROOT_DIR / "data" / "okinilove_profile_status.json"
NS_UNMATCHED_PATH = ROOT_DIR / "data" / "ns_senmon_unmatched.json"
CATEGORY_LABELS = {"difficulty": "予約困難", "soldout": "完売の記述あり", "popular": "人気の記述あり"}
SCORE_PRIOR_WEIGHT = 3
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
    r"とは言えない|というほどではない|"
    r"(?:気配|様子|兆し)(?:が|は|も)?\s*"
    r"(?:(?:全く|まったく|全然|ぜんぜん|ほとんど)\s*)?"
    r"(?:ない|無い|なく|無く|なかった|無かった|ありません|なさそう)|"
    r"(?:わけ|訳|というわけ|という訳|こと)(?:では|じゃ|は)"
    r"(?:ない|無い|なく|無く|なかった|ありません))"
)
CONTRADICTORY_CONTEXT = re.compile(
    r"予約困難.{0,35}(?:ではない|じゃない|でない|では無い)|"
    r"(?:昔は|以前は).{0,80}予約困難.{0,80}(?:今は|現在は).{0,40}(?:空いて|余裕)|"
    r"予約困難.{0,80}(?:今は|現在は).{0,40}(?:空いて|余裕)",
    re.DOTALL,
)
LOCAL_NEGATION = re.compile(
    r"^(?:嬢|女性|者)?(?:続き)?(?:が|は|も|の|に)?"
    r"(?:(?:全く|まったく|全然|ほとんど|あまり)\s*)?"
    r"(?:ない|無い|なく|無く|なかった|ありません|いない|いなく|いません|"
    r"ではな|では無|じゃな|じゃ無|でな|にならな|ならな|"
    r"出ない|出なかった|が出ない|は出ない|していない|してない|しない)"
)
FUTURE_WORDING = re.compile(
    r"^(?:嬢|女性|者)?(?:が|は|も|の)?"
    r"(?:出そう|でそう|出るかも|出るはず|でてくるかも|"
    r"に?(?:なる|なりそう|なってほし)|しそう|そう|かもしれ|"
    r"あるようになる|が出そう|は出そう)"
)


def sentence_context(text, match):
    start = max(text.rfind(char, 0, match.start()) for char in "。\n！？") + 1
    ends = [text.find(char, match.end()) for char in "。\n！？"]
    end = min((position for position in ends if position >= 0), default=len(text))
    return text[start:end], text[start:match.start()], text[match.end():end]


def rejected_wording(text, match):
    sentence, before, after = sentence_context(text, match)
    if NEGATED_DIFFICULTY.match(after) or LOCAL_NEGATION.match(after) or FUTURE_WORDING.match(after):
        return True
    if "人気" in match.group():
        if before.endswith(("恋", "不")) or re.search(r"一人\s*$", text[:match.start() + 1]):
            return True
        if re.match(r"(?:大衆)?店|の(?:お店|店|ホテル|コース|プレイ|メニュー)", after):
            return True
        if re.search(r"(?:出る|でる|出てくる).{0,8}(?:そう|かも|だろ|でしょう|はず)", after[:28]):
            return True
    if (
        re.search(r"(?:別店|他店|他の店|別の嬢|以前入った)", sentence)
        and re.search(r"(?:当欠|仕方なく|外れ|他に|選んだ|指名しました)", sentence)
        and not re.search(r"(?:移籍|移って|移った)", sentence)
    ):
        return True
    if re.search(r"(?:他にお目当て|狙ってた嬢|お目当てだった).{0,30}$", before):
        return True
    if re.search(r"(?:もし|書いたら|書くと|紹介すると|おすすめして).{0,30}$", before):
        return True
    return "予約" in match.group() and bool(CONTRADICTORY_CONTEXT.search(sentence))


def booking_matches(text):
    """予約困難を肯定する一致だけを返す。抽出と強調表示で共用する。"""
    if not text:
        return []
    matches = []
    for match in BOOKING_DIFFICULTY.finditer(text):
        if rejected_wording(text, match):
            continue
        matches.append(match)
    return matches


def booking_evidence(text):
    evidence = []
    for match in booking_matches(text):
        start = max(0, match.start() - 115)
        end = min(len(text), match.end() + 145)
        excerpt = " ".join(text[start:end].split())
        if excerpt and excerpt not in evidence:
            evidence.append(excerpt)
    return evidence


def candidate_matches(text):
    matches = [(match, "difficulty") for match in booking_matches(text)]
    for match in re.finditer(r"完売|(?<!恋)(?<!不)(?<!一)人気", text or ""):
        if rejected_wording(text, match):
            continue
        matches.append((match, "soldout" if match.group() == "完売" else "popular"))
    return matches


def highlight_booking_text(fragment, entry):
    for paragraph in entry.select(".review-text, .review-param, .report-comment p"):
        for node in list(paragraph.find_all(string=True)):
            if node.parent.name == "mark":
                continue
            text = str(node)
            matches = []
            for match, category in sorted(
                candidate_matches(text), key=lambda item: (item[0].start(), -item[0].end())
            ):
                if matches and match.start() < matches[-1][0].end():
                    continue
                matches.append((match, category))
            if not matches:
                continue
            position = 0
            for match, category in matches:
                if match.start() > position:
                    node.insert_before(text[position:match.start()])
                mark = fragment.new_tag("mark", attrs={
                    "class": f"booking-highlight highlight-{category}",
                    "title": CATEGORY_LABELS[category],
                })
                mark.string = match.group()
                node.insert_before(mark)
                position = match.end()
            if position < len(text):
                node.insert_before(text[position:])
            node.extract()


def record_text(dataset, record):
    return "\n".join(value for _, value in record_fields(dataset, record))


def record_fields(dataset, record):
    if dataset == "ns_senmon":
        values = record.get("details", {})
        return [(key, str(value)) for key, value in values.items() if value] if isinstance(values, dict) else []
    if dataset == "okinilove":
        keys = ("report_text", "evaluations")
    else:
        keys = ("total_comment", "repeat_comment", "play_comment", "style_comment")
    return [(key, str(record[key])) for key in keys if record.get(key)]


def review_reason(text, match, name, field):
    sentence, before, after = sentence_context(text, match)
    if field.startswith(("待合室", "プレイルーム")):
        return "設備・店舗の説明欄にある記述のため、人物への言及か要確認"
    if (
        re.search(r"(?:他の|他店|別の|別店|お目当て|いつもの|比べ|と違)", sentence)
        and not re.search(r"(?:移籍|移って|移った)", sentence)
    ):
        return "別人物・店舗との比較を含むため、言及対象を要確認"
    if re.search(r"(?:予感|ありそう|そうだ|はず|だと思った|今後|だろう|でしょう)", sentence):
        return "推測・予測を含む記述のため、実績か要確認"
    names = re.findall(r"([ぁ-んァ-ヶ一-龥a-zA-Z]{2,12})(?:さん|ちゃん|嬢)", before[-35:])
    if names and name and not any(name in mentioned or mentioned in name for mentioned in names):
        return "本文中の呼称と保存された人物名の対応を要確認"
    if re.match(r"(?:嬢|女性)?(?:になる|が出る|出る|があっても)", after):
        return "人気の実績ではなく予測・期待の可能性があるため要確認"
    return ""


def analyze_record(dataset, record):
    name = record.get("name") or record.get("girl_name") or ""
    fields = []
    for label, text in record_fields(dataset, record):
        matches = [
            {"start": match.start(), "end": match.end(), "category": category,
             "reason": review_reason(text, match, name, label)}
            for match, category in candidate_matches(text)
        ]
        if matches:
            fields.append({"label": label, "text": text, "matches": matches})
    return fields


def safe_http_url(value):
    try:
        return value if urlsplit(value).scheme.lower() in {"http", "https"} else ""
    except (TypeError, ValueError):
        return ""


def candidate_person_key(dataset, record, row_number):
    profile = record.get("cityheaven_url") or record.get("girl_url") or ""
    source = record.get("url") or record.get("report_url") or ""
    return normalize_profile_url(profile) or f"{dataset}:{source or row_number}"


def rating_value(dataset, record):
    if dataset == "ns_senmon":
        value = number(record.get("details") or {}, TOTAL_KEY)
        maximum = 100
    elif dataset == "okinilove":
        stars = re.search(r"[★☆]{5}", record.get("evaluations") or "")
        return stars.group().count("★") if stars else None
    else:
        value = record.get("total_point")
        maximum = 100
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= maximum else None


def relative_scores(dataset, records):
    """Rank all rated people in one DB, then shrink toward its mean percentile."""
    groups = defaultdict(list)
    seen = set()
    for row_number, record in enumerate(records, start=1):
        key = candidate_person_key(dataset, record, row_number)
        source = record.get("url") or record.get("report_url") or ""
        identity = (key, record_url_key(source)) if source else (key, row_number)
        if identity in seen:
            continue
        value = rating_value(dataset, record)
        if value is not None:
            seen.add(identity)
            groups[key].append(value)
    means = {key: sum(values) / len(values) for key, values in groups.items()}
    ordered = sorted(means.values())
    results = {}
    for key, raw_mean in means.items():
        count = len(groups[key])
        percentile = (
            100 * (bisect_left(ordered, raw_mean) + bisect_right(ordered, raw_mean) - 1)
            / (2 * (len(ordered) - 1))
            if len(ordered) > 1 else 50
        )
        score = (count * percentile + SCORE_PRIOR_WEIGHT * 50) / (count + SCORE_PRIOR_WEIGHT)
        results[key] = {
            "score": score, "percentile": percentile,
            "mean": raw_mean, "count": count,
        }
    return results


def _read_optional_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def unavailable_article_markers(datasets, *, now=None, okinilove_cache=None,
                                ns_profile_cache=None, ns_unmatched=None):
    """Return saved, affirmative profile-unavailability markers for each report DB."""
    now = now or datetime.now(timezone.utc)
    records_by_dataset = {dataset: rows for _, dataset, rows in datasets}
    tokai_statuses = latest_profile_status_by_person(records_by_dataset["tokai_fuzoku"])
    tokai_people = {
        key for key, status in tokai_statuses.items() if status == "unavailable"
    }

    if okinilove_cache is None:
        okinilove_cache = _read_optional_json(OKINILOVE_PROFILE_STATUS_PATH, {})
    unavailable_okinilove_profiles = set()
    for url, entry in okinilove_cache.items():
        if not isinstance(entry, dict) or str(entry.get("status")) not in {"404", "410"}:
            continue
        try:
            checked_at = datetime.fromisoformat(entry["checked_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if now - checked_at < PROFILE_STATUS_CACHE_TTL:
            unavailable_okinilove_profiles.add(profile_check_url(url))

    if ns_profile_cache is None:
        ns_profile_cache = _read_optional_json(NS_PROFILE_IMAGE_CACHE_PATH, {})
    unavailable_ns_profiles = {
        normalize_profile_url(url)
        for url, entry in ns_profile_cache.items()
        if isinstance(entry, dict) and entry.get("status") == "not_found"
    }
    if ns_unmatched is None:
        ns_unmatched = _read_optional_json(NS_UNMATCHED_PATH, [])
    unavailable_ns_articles = {
        row.get("url")
        for row in ns_unmatched
        if row.get("status") == "not_listed" and row.get("url")
    }
    unavailable_ns_articles.update(
        row.get("url")
        for row in records_by_dataset["ns_senmon"]
        if row.get("match_confidence") == "not_listed" and row.get("url")
    )
    return {
        "tokai_people": tokai_people,
        "okinilove_profiles": unavailable_okinilove_profiles,
        "ns_profiles": {url for url in unavailable_ns_profiles if url},
        "ns_articles": unavailable_ns_articles,
    }


def filter_unavailable_records(datasets, markers):
    filtered = []
    removed_counts = {}
    for label, dataset, records in datasets:
        retained = []
        for record in records:
            if dataset == "tokai_fuzoku":
                unavailable = person_key(record) in markers["tokai_people"]
            elif dataset == "okinilove":
                unavailable = profile_check_url(record.get("girl_url") or "") in markers[
                    "okinilove_profiles"
                ]
            else:
                profile = normalize_profile_url(record.get("cityheaven_url"))
                unavailable = (
                    record.get("url") in markers["ns_articles"]
                    or profile in markers["ns_profiles"]
                )
            if not unavailable:
                retained.append(record)
        filtered.append((label, dataset, retained))
        removed_counts[label] = len(records) - len(retained)
    return filtered, removed_counts


def extract_people():
    people = {}
    source_counts = defaultdict(int)
    scores_by_database = {}
    datasets = [
        (label, dataset, read_jsonl_records(ROOT_DIR / "data" / filename))
        for label, filename, dataset, _ in DATASETS
    ]
    markers = unavailable_article_markers(datasets)
    datasets, removed_counts = filter_unavailable_records(datasets, markers)
    removed_summary = " / ".join(f"{label} {count}件" for label, count in removed_counts.items())
    print(f"掲載終了扱いの記事を候補対象外にしました: {removed_summary}")
    shop_names = {
        record["shop_name"] for _, _, rows in datasets for record in rows if record.get("shop_name")
    }

    for label, dataset, rows in datasets:
        scores_by_database[label] = relative_scores(dataset, rows)
        for row_number, record in enumerate(rows, start=1):
            fields = analyze_record(dataset, record)
            categories = sorted({
                match["category"] for field in fields for match in field["matches"]
            })
            if not categories:
                continue

            source_url = record.get("url") or record.get("report_url") or ""
            profile_url = (
                record.get("cityheaven_url")
                or record.get("girl_url")
                or ""
            )
            key = candidate_person_key(dataset, record, row_number)
            name = record.get("name") or record.get("girl_name") or ""
            identity_issues = []
            if not name or name in {"名前不明", "不明", "退店・移籍嬢"} or name in shop_names:
                identity_issues.append("人物情報要確認：保存された名前が店舗名・不明値の可能性")
                key = f"{dataset}:identity:{source_url or row_number}"
            person = people.setdefault(
                key,
                {
                    "name": record.get("name") or record.get("girl_name") or "名前不明",
                    "names": [],
                    "shops": [],
                    "profile_url": profile_url,
                    "sources": [],
                    "key": key,
                },
            )
            name = record.get("name") or record.get("girl_name")
            shop = record.get("shop_name")
            if name and name not in person["names"]:
                person["names"].append(name)
            if shop and shop not in person["shops"]:
                person["shops"].append(shop)

            evidence = {
                "database": label,
                "source_url": source_url,
                "record": record,
                "categories": categories,
                "fields": fields,
                "review_reasons": sorted({
                    match["reason"] for field in fields for match in field["matches"] if match["reason"]
                } | set(identity_issues)),
                "needs_review": bool(identity_issues) or all(
                    match["reason"] for field in fields for match in field["matches"]
                ),
            }
            if evidence not in person["sources"]:
                person["sources"].append(evidence)
            source_counts[label] += 1

    for key, person in people.items():
        person["needs_review"] = all(source["needs_review"] for source in person["sources"])
        person["identity_issues"] = sorted({
            reason for source in person["sources"] for reason in source["review_reasons"]
            if reason.startswith("人物情報要確認")
        })
        person["ratings"] = {
            label: scores[key] for label, scores in scores_by_database.items() if key in scores
        }
        ratings = list(person["ratings"].values())
        person["score"] = sum(rating["score"] for rating in ratings) / len(ratings) if ratings else None
        person["rated_count"] = sum(rating["count"] for rating in ratings)

    ordered = sorted(
        people.values(),
        key=lambda person: (
            -len(person["sources"]),
            person["name"].casefold(),
        ),
    )
    return ordered, source_counts


def record_url_key(value):
    """Match saved article links across HTTP/HTTPS while preserving review fragments."""
    url = urlsplit(value)
    return url.netloc.casefold(), url.path, url.query, url.fragment


class ReportViews:
    def __init__(self, docs_dir):
        self.indexes = {}
        self.tokai_profile_images = {}
        for label, _, dataset, filename in DATASETS:
            page = BeautifulSoup((docs_dir / filename).read_text(encoding="utf-8"), "html.parser")
            selector = (
                ".article-entry > summary a.link-button"
                if dataset == "tokai_fuzoku" else ".review-meta a"
            )
            index = {}
            for link in page.select(selector):
                index[record_url_key(link["href"])] = link
            self.indexes[label] = index
            if dataset == "tokai_fuzoku":
                for person in page.select("details.report-entry"):
                    profile_link = person.select_one(".person-heading a.link-button")
                    image = person.select_one(".profile-thumbnail")
                    if profile_link and image and image.get("src"):
                        profile_key = normalize_profile_url(profile_link.get("href"))
                        if profile_key:
                            self.tokai_profile_images[profile_key] = image["src"]
        self.okinilove_bodies = json.loads(
            (docs_dir / "okinilove_review_bodies.json").read_text(encoding="utf-8")
        )

    def fill_okinilove_bodies(self, fragment, bodies=None):
        bodies = self.okinilove_bodies if bodies is None else bodies
        for item in fragment.select(".review-item[data-rid]"):
            body = bodies[int(item["data-rid"])]
            params = item.select_one(".review-params")
            for key, label in (("f", "顔"), ("s", "スタイル"), ("v", "サービス")):
                if not body.get(key):
                    continue
                param = fragment.new_tag("p", attrs={"class": "review-param"})
                title = fragment.new_tag("span", attrs={"class": "review-label"})
                title.string = label
                param.append(title)
                if isinstance(body[key], str):
                    param.append(body[key])
                else:
                    for text, tone in body[key]:
                        chip = fragment.new_tag("span", attrs={"class": f"chip {tone}"})
                        chip.string = text
                        param.append(chip)
                params.select_one(".review-gap").parent.insert_before(param)
            if body.get("t"):
                text = fragment.new_tag("p", attrs={"class": "review-text"})
                text.string = body["t"]
                item.select_one(".review-detail").insert(0, text)
            del item["data-rid"]

    def render_missing_okinilove(self, records):
        rows = []
        for record in records:
            profile = safe_http_url(record.get("girl_url") or record.get("cityheaven_url") or "")
            rows.append(
                f'<tr><td>{escape(record.get("shop_name") or "")}</td>'
                f'<td><a href="{escape(record["report_url"], quote=True)}">記事</a></td>'
                f'<td><a href="{escape(profile, quote=True)}">{escape(record.get("girl_name") or "")}</a></td>'
                f'<td></td><td>{escape(record.get("evaluations") or "")}</td></tr>'
            )
        page, bodies = build_people_summary(
            f'<table id="girlsTable"><tbody>{"".join(rows)}</tbody></table>',
            fetch_missing_industries=False,
        )
        fragment = BeautifulSoup(
            f'<table class="person-table">{page.select_one("#reviews").decode_contents()}</table>',
            "html.parser",
        )
        self.fill_okinilove_bodies(fragment, json.loads(bodies))
        return fragment

    def append_saved_evidence(self, fragment, entry, sources):
        original_text = "".join(entry.get_text().split())
        is_tokai_entry = "article-entry" in entry.get("class", [])
        displayed_comments = {}
        if is_tokai_entry:
            for paragraph in entry.select(".report-comment p"):
                displayed_comments.setdefault("".join(paragraph.get_text().split()), []).append(paragraph)

        def add_highlights(paragraph, field):
            text = paragraph.get_text()
            normalized_text = "".join(text.split())
            normalized_field = "".join(field["text"].split())
            if normalized_text != normalized_field or paragraph.find(True):
                return False
            paragraph.clear()
            text_positions = [index for index, char in enumerate(text) if not char.isspace()]
            field_positions = [index for index, char in enumerate(field["text"]) if not char.isspace()]
            spans = []
            for match in sorted(field["matches"], key=lambda item: (item["start"], -item["end"])):
                start = sum(index < match["start"] for index in field_positions)
                end = sum(index < match["end"] for index in field_positions)
                spans.append((text_positions[start], text_positions[end - 1] + 1, match))
            position = 0
            for start, end, match in spans:
                if start < position:
                    continue
                if start > position:
                    paragraph.append(text[position:start])
                category = match["category"]
                mark = fragment.new_tag("mark", attrs={
                    "class": f"booking-highlight highlight-{category}",
                    "title": CATEGORY_LABELS[category] + (
                        f'（要確認：{match["reason"]}）' if match["reason"] else ""
                    ),
                })
                mark.string = text[start:end]
                paragraph.append(mark)
                position = end
            paragraph.append(text[position:])
            return True

        fields = []
        missing_labels = set()
        for source in sources:
            for field in source.get("fields", []):
                normalized_text = "".join(field["text"].split())
                paragraphs = displayed_comments.get(normalized_text, []) if is_tokai_entry else []
                if paragraphs:
                    paragraph = next(
                        (paragraph for paragraph in paragraphs if not paragraph.select_one("mark.booking-highlight")),
                        None,
                    )
                    if paragraph is not None and add_highlights(paragraph, field):
                        continue
                    if paragraph is None:
                        continue
                fields.append(field)
                if normalized_text not in original_text:
                    missing_labels.add(field["label"])

        if not fields:
            return
        block = fragment.new_tag("div", attrs={"class": "saved-evidence"})
        heading = fragment.new_tag("h4")
        heading.string = "抽出に使用した保存本文"
        block.append(heading)
        if missing_labels:
            notice = fragment.new_tag("p", attrs={"class": "evidence-discrepancy"})
            notice.string = (
                "既存docsの表示内に同一本文を確認できません（"
                + " / ".join(sorted(missing_labels))
                + "）。抽出根拠は以下の保存本文です。表示省略・保存時点の違いに注意してください。"
            )
            block.append(notice)
        seen_fields = set()
        for field in fields:
            key = (field["label"], field["text"])
            if key in seen_fields:
                continue
            seen_fields.add(key)
            section = fragment.new_tag("section", attrs={"class": "evidence-field"})
            title = fragment.new_tag("h5")
            title.string = field["label"]
            section.append(title)
            paragraph = fragment.new_tag("p", attrs={"class": "review-text saved-evidence-text"})
            text = field["text"]
            position = 0
            for match in sorted(field["matches"], key=lambda item: (item["start"], -item["end"])):
                if match["start"] < position:
                    continue
                paragraph.append(text[position:match["start"]])
                category = match["category"]
                mark = fragment.new_tag("mark", attrs={
                    "class": f"booking-highlight highlight-{category}",
                    "title": CATEGORY_LABELS[category] + (
                        f'（要確認：{match["reason"]}）' if match["reason"] else ""
                    ),
                })
                mark.string = text[match["start"]:match["end"]]
                paragraph.append(mark)
                position = match["end"]
            paragraph.append(text[position:])
            section.append(paragraph)
            block.append(section)
        target = entry.select_one(".review-detail, .report-details")
        target.insert(0, block)

    def render_missing_tokai(self, records):
        records = [dict(record) for record in records]
        for record in records:
            profile_key = normalize_profile_url(record.get("girl_url"))
            image_url = self.tokai_profile_images.get(profile_key)
            if image_url:
                record["profile_image_url"] = image_url
        fragments = {}
        for group in group_reports(pd.DataFrame(records)):
            markup = render_person_entry(group, {})
            fragments[group["first_index"]] = BeautifulSoup(markup, "html.parser")
        return fragments

    def render(self, person):
        blocks = []
        for label, _, dataset, filename in DATASETS:
            sources = [source for source in person["sources"] if source["database"] == label]
            if not sources:
                continue
            fragments = {}
            missing = []
            for source in sources:
                target = self.indexes[label].get(record_url_key(source["source_url"]))
                if target is None:
                    if dataset not in {"tokai_fuzoku", "okinilove"}:
                        raise ValueError(f"{filename}: matching review not found: {source['source_url']}")
                    missing.append(source["record"])
                    continue
                container = target.find_parent(
                    class_="report-entry" if dataset == "tokai_fuzoku" else "detail-row"
                )
                if id(container) in fragments:
                    continue
                if dataset == "tokai_fuzoku":
                    markup = str(container)
                else:
                    row = container.find_previous_sibling("tr")
                    markup = (
                        '<table class="person-table"><thead><tr>'
                        '<th>店舗</th><th>人物</th><th>評価</th><th>評価内訳</th><th>詳細</th>'
                        f'</tr></thead><tbody>{row}{container}</tbody></table>'
                    )
                fragments[id(container)] = BeautifulSoup(markup, "html.parser")
            if missing:
                if dataset == "okinilove":
                    fragments["missing"] = self.render_missing_okinilove(missing)
                else:
                    fragments.update({
                        ("missing", index): fragment
                        for index, fragment in self.render_missing_tokai(missing).items()
                    })
            keys = defaultdict(list)
            for source in sources:
                keys[record_url_key(source["source_url"])].append(source)
            for fragment in fragments.values():
                if dataset == "okinilove":
                    self.fill_okinilove_bodies(fragment)
                for button in fragment.select(".person-row .toggle"):
                    button.name = "button"
                    button["type"] = "button"
                    button["aria-expanded"] = "false"
                    button.string = "詳細を表示"
                for link in fragment.select(".review-meta a, .article-entry > summary a.link-button"):
                    linked_sources = keys.get(record_url_key(link["href"]))
                    if not linked_sources:
                        continue
                    source = linked_sources[0]
                    entry = link.find_parent(class_="review-item") or link.find_parent(class_="article-entry")
                    entry["class"] = [*entry.get("class", []), "evidence-record"]
                    entry["data-source-url"] = source["source_url"]
                    disclosure = entry.select_one("details.review-body")
                    (disclosure if disclosure is not None else entry)["open"] = ""
                    marker = fragment.new_tag("strong", attrs={"class": "booking-marker"})
                    categories = sorted({
                        category for item in linked_sources
                        for category in item.get("categories", ["difficulty"])
                    })
                    marker.string = " / ".join(CATEGORY_LABELS[category] for category in categories)
                    reasons = sorted({
                        reason for item in linked_sources for reason in item.get("review_reasons", [])
                    })
                    if reasons:
                        marker.string += "（要確認：" + " / ".join(reasons) + "）"
                    if disclosure is not None:
                        entry.insert(0, marker)
                    else:
                        entry.select_one("summary").insert_after(marker)
                    self.append_saved_evidence(fragment, entry, linked_sources)
                blocks.append(
                    f'<div class="source-report report-page" data-database="{escape(label)}">'
                    f'<h3>{escape(label)}</h3>{fragment}</div>'
                )
        return "".join(blocks)


def render_person(person, views):
    categories = sorted({category for source in person["sources"] for category in source["categories"]})
    candidate_categories = sorted({
        match["category"] for source in person["sources"] for field in source["fields"]
        for match in field["matches"] if not match["reason"] and not person["identity_issues"]
    })
    category_badges = " ".join(
        f'<span class="database category-{category}">{CATEGORY_LABELS[category]}'
        f'{"（要確認）" if category not in candidate_categories else ""}</span>'
        for category in categories
    )
    review_badge = (
        '<strong class="review-notice">要確認（通常の候補と分離）</strong>'
        if person["needs_review"] else ""
    )
    identity_notice = " / ".join(escape(reason) for reason in person["identity_issues"])
    score = person["score"]
    score_attribute = "" if score is None else f"{score:.8f}"
    score_text = "未評価" if score is None else f"総合相対スコア {score:.1f} / 100"
    rating_details = " / ".join(
        f'{escape(label)}: 元評価 {rating["mean"]:.1f}'
        f'{" / 5" if label == "オキニラブ" else " / 100"}'
        f'（{rating["count"]}件）、相対 {rating["score"]:.1f}'
        for label, rating in person["ratings"].items()
    )
    mention_count = len({
        (source["database"], record_url_key(source["source_url"]))
        for source in person["sources"]
    })
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
            f'<li class="stored-evidence"{source_attribute}>'
            f'<span class="database">{database}</span> {source} {docs_link}'
            f'<details class="stored-record">'
            f'<summary>DBに保存された元データを全文表示（JSON）</summary>'
            f'<pre>{record_json}</pre></details></li>'
        )

    return f"""
<section class="person" data-review="{"review" if person["needs_review"] else "candidate"}" data-candidate-categories="{" ".join(candidate_categories)}" data-categories="{" ".join(categories)}" data-score="{score_attribute}" data-rated-count="{person["rated_count"]}" data-mentions="{mention_count}">
  <h2>{heading}<span class="evidence-count">{len(person["sources"])}件の根拠</span></h2>
  {review_badge}<p class="review-notice">{identity_notice}</p>
  <p class="relative-score"><strong>{score_text}</strong>
    <span class="summary">評価{person["rated_count"]}件・{len(person["ratings"])}DB</span>
  </p>
  <p class="summary">{rating_details}</p>
  <p class="candidate-categories">{category_badges}</p>
  {views.render(person)}
  <details class="raw-records"><summary>保存データ（JSON）・元記事リンク</summary>
    <ul class="evidence-list">{"".join(source_items)}</ul>
  </details>
</section>
"""


def render_html(people, source_counts, views=None):
    views = views if views is not None else ReportViews(ROOT_DIR / "docs")
    database_total = sum(source_counts.values())
    people_html = "".join(render_person(person, views) for person in people)
    counts_html = " / ".join(
        f"{escape(label)} {source_counts.get(label, 0)}件"
        for label, _, _, _ in DATASETS
    )
    return attach_prices(f"""<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="color-scheme" content="light dark">
    <link rel="stylesheet" href="common.css">
    <script src="common.js" defer></script>
    <title>予約困難・完売・人気の人物候補</title>
    <style>
body {{ max-width: 1280px; font-family: sans-serif; line-height: 1.65; }}
h1 {{ font-size: 1.5rem; }}
.summary {{ color: var(--muted); }}
.search {{ width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--input-border); border-radius: 5px; background: var(--input-bg); color: var(--fg); }}
.age-filter {{ display: flex; align-items: center; gap: 10px; margin-top: 12px; }}
.age-filter select {{ min-height: 44px; padding: 8px 10px; background: var(--input-bg); color: var(--fg); border: 1px solid var(--input-border); border-radius: 5px; }}
.person {{ margin: 10px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }}
.person h2 {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; font-size: 1.1rem; }}
.shop, .evidence-count {{ color: var(--muted); font-size: 0.9rem; font-weight: normal; }}
.evidence-count {{ margin-left: auto; }}
.evidence-list {{ margin-bottom: 0; padding-left: 1.2rem; }}
.evidence-list > li {{ margin: 12px 0; }}
.source-report {{ margin: 12px 0; }}
.source-report h3 {{ font-size: 1rem; }}
.source-report table {{ width: 100%; border-collapse: collapse; }}
.source-report th, .source-report td {{ padding: 10px; border-bottom: 1px solid var(--border-soft); text-align: left; vertical-align: top; }}
.source-report th {{ background: var(--surface-alt); }}
.source-report .person-row {{ cursor: pointer; }}
.source-report .person-row.open, .source-report .detail-row {{ background: var(--surface-alt); }}
.source-report .profile-link {{ display: inline-flex; align-items: center; gap: 9px; }}
.source-report .profile-photo {{ width: 42px; height: 56px; object-fit: cover; border-radius: 3px; }}
.source-report .age, .source-report .latest, .source-report .shop-kind {{ display: block; color: var(--muted); font-size: 0.85rem; }}
.source-report .chip {{ display: inline-block; margin: 2px; padding: 1px 7px; border-radius: 10px; background: var(--chip); color: var(--fg-soft); font-size: 0.85rem; }}
.source-report .chip.pos {{ border: 1px solid #56946a; }}
.source-report .chip.neg {{ border: 1px solid #bc6666; text-decoration: underline wavy; }}
.source-report .report-list {{ margin: 0; padding: 0; list-style: none; }}
.source-report .review-item {{ padding: 10px 0; border-bottom: 1px solid var(--border-soft); }}
.source-report .review-param, .source-report .review-text {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
.source-report .review-label {{ display: inline-block; margin-right: 8px; color: var(--muted); }}
.source-report .review-text {{ padding: 10px; border-left: 3px solid var(--border); }}
.source-report summary, .raw-records > summary {{ min-height: 44px; cursor: pointer; }}
.source-report .toggle {{ min-height: 44px; padding: 6px 10px; color: var(--link); background: var(--surface); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; }}
.booking-marker {{ display: block; color: var(--fg); border-left: 3px solid var(--link); padding-left: 8px; margin: 6px 0; font-size: 0.85rem; }}
.saved-evidence {{ border: 1px solid var(--border); padding: 10px; margin: 8px 0; background: var(--surface); }}
.saved-evidence h4, .saved-evidence h5 {{ margin: 6px 0; overflow-wrap: anywhere; }}
.review-notice {{ color: var(--muted); font-size: 0.9rem; }}
.booking-highlight {{ background: #ffe080; color: #3b2900; padding: 1px 3px; border-radius: 3px; font-weight: 700; }}
.highlight-popular, .category-popular {{ background: #cce9ff; color: #143c59; }}
.highlight-soldout, .category-soldout {{ background: #ffd7bb; color: #572a09; }}
@media (prefers-color-scheme: dark) {{
  .booking-highlight {{ background: #725500; color: #fff1b8; }}
  .highlight-popular, .category-popular {{ background: #24455e; color: #d4eeff; }}
  .highlight-soldout, .category-soldout {{ background: #623a20; color: #ffe3cc; }}
}}
.source-report .evidence-record {{ scroll-margin-top: 20px; }}
.source-report [hidden] {{ display: none !important; }}
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
  .source-report table, .source-report tbody, .source-report tr, .source-report td {{ display: block; width: 100%; }}
  .source-report thead {{ display: none; }}
  .source-report td {{ padding: 6px 0; border: 0; }}
  .source-report .person-row {{ padding: 8px 0; }}
  .source-report .detail-row {{ padding: 8px; }}
}}
    </style>
  </head>
  <body>
    <nav class="page-nav"><a href="index.html">← 一覧</a> / <a href="weekly_schedule.html">3レポート横断・7日間出勤カレンダー</a></nav>
    <h1>予約困難・完売・人気の人物候補</h1>
    <p class="summary">該当する記述が見つかった人物: {len(people)}人（根拠レコード {database_total}件）</p>
    <p class="summary">{counts_html}</p>
    <label for="personSearch">名前・店舗・根拠を検索</label>
    <input class="search" id="personSearch" type="search" autocomplete="off">
    <label class="age-filter" for="sortOrder">並び順
      <select id="sortOrder" aria-label="並び順">
        <option value="score">総合相対スコア順</option>
        <option value="mentions">人気・完売・予約困難の言及件数順</option>
        <option value="original">抽出順</option>
      </select>
    </label>
    <p class="summary">総合相対スコアは各DB内の評価順位を0〜100に換算し、少数レビューを平均方向に補正した比較用の値です。共通基準の絶対評価ではありません。人気・完売の言及件数はスコアに加算しません。</p>
    <label class="age-filter" for="categoryFilter">分類
      <select id="categoryFilter" aria-label="候補の分類">
        <option value="booking">予約困難・完売</option>
        <option value="">すべて（人気候補を含む）</option>
        <option value="difficulty">予約困難</option>
        <option value="soldout">完売の記述あり</option>
        <option value="popular">人気の記述あり</option>
      </select>
    </label>
    <p class="summary">分類は本文の記述に基づく候補です。「人気」は予約しやすい人物も含みます。別の人物への言及や一時的な完売の可能性もあるため、ハイライト前後の本文を確認してください。</p>
    <label class="age-filter" for="reviewFilter">確認区分
      <select id="reviewFilter" aria-label="確認区分">
        <option value="candidate">通常候補</option>
        <option value="">すべて（要確認を含む）</option>
        <option value="review">要確認のみ</option>
      </select>
    </label>
    <p class="summary">対象人物が曖昧な記述・推測・人物名の異常は要確認として分離します。ハイライトは「抽出に使用した保存本文」の正確な一致位置に表示し、既存docsの本文は比較用にそのまま残します。通常候補も自動判定であり、正しさを保証するものではありません。</p>
    <label class="age-filter" for="ageFilter">年齢
      <select id="ageFilter" aria-label="年齢区分">
        <option value="">指定なし</option>
        <option value="under30">30歳未満</option>
        <option value="30plus">30歳以上</option>
        <option value="unknown">年齢不明</option>
      </select>
    </label>
    <p class="summary" id="visibleCount" aria-live="polite"></p>
    <p class="no-results" id="noResults" hidden>該当する人物はいません。</p>
    <section id="people">{people_html}</section>
    <aside class="method">
      <strong>抽出方法と注意</strong>
      <p>対象は data/ns_senmon_reports.jsonl、data/okinilove_reviews.jsonl、data/tokai_fuzoku_reports.jsonl です。既存docsの人物欄・評価・詳細をこのページに直接掲載しています。人物の詳細を開くとレビュー本文を読め、候補の根拠となったレビューには分類を表示し、該当箇所を強調しています。予約困難・完売・人気は重複することがあります。人物欄の集計にはこれらに言及しないレビューも含まれます。既存一覧にないデータも保存レコードから同じHTML形式で表示します。JSONは補足として残しています。否定文や「予約が取れなくなる前」などの将来の懸念は除外しました。</p>
      <p>3つのデータベースには元ページのHTMLソース自体は保存されていないため、表示するのは取得・保存済みのレコードデータです。これは保存済みレビューの文言検索結果であり、現在の出勤状況や予約可能性を確認したものではありません。記述時期・レビュアーの主観・本文が別の人物に言及している可能性があるため、各元記事で確認してください。人物の統合はプロフィールURLが一致する場合のみ行い、プロフィールURLのないレコードは個別に扱っています。</p>
      <p>生成日: {date.today().isoformat()}</p>
      <p>スコアは候補以外も含む各DBの全保存データで計算します。NS専門・東海は総合点、オキニラブは星評価を使用し、同じ人物・記事URL（HTTP/HTTPS違いを含む）の評価は1件とします。人物の平均評価をDB内で順位化し、同点は平均順位、1人のみなら50とします。相対スコアは（評価件数 × 順位点 ＋ {SCORE_PRIOR_WEIGHT} × 50）÷（評価件数 ＋ {SCORE_PRIOR_WEIGHT}）。複数DBの評価はDBごとに均等平均します。未評価DBは平均に含めず、全DB未評価なら末尾に表示します。表示中の人物欄とDB内の集計単位・対象範囲が異なる場合があります。</p>
    </aside>
    <script>
      const people = [...document.querySelectorAll(".person")];
      const search = document.getElementById("personSearch");
      const ageFilter = document.getElementById("ageFilter");
      const categoryFilter = document.getElementById("categoryFilter");
      const reviewFilter = document.getElementById("reviewFilter");
      const sortOrder = document.getElementById("sortOrder");
      const originalOrder = new Map(people.map((person, index) => [person, index]));
      function sortPeople() {{
        const mode = sortOrder.value;
        const ordered = [...people].sort((a, b) => {{
          if (mode === "score") {{
            const missing = Number(a.dataset.score === "") - Number(b.dataset.score === "");
            if (missing) return missing;
            const difference = Number(b.dataset.score) - Number(a.dataset.score);
            if (difference) return difference;
            const countDifference = Number(b.dataset.ratedCount) - Number(a.dataset.ratedCount);
            if (countDifference) return countDifference;
          }} else if (mode === "mentions") {{
            const difference = Number(b.dataset.mentions) - Number(a.dataset.mentions);
            if (difference) return difference;
          }}
          return originalOrder.get(a) - originalOrder.get(b);
        }});
        document.getElementById("people").append(...ordered);
      }}
      sortOrder.addEventListener("change", sortPeople);
      sortPeople();
      const personAges = new Map(people.map(person => {{
        const ages = [...person.querySelectorAll(".person-row td:nth-child(2) .age, .person-identity")]
          .map(node => Number(node.textContent.match(/([0-9]+)\\s*[歳才]/)?.[1] || 0))
          .filter(age => age > 0);
        return [person, ages];
      }}));
      document.querySelectorAll(".source-report .person-row").forEach(row => {{
        const detail = row.nextElementSibling;
        const button = row.querySelector("button.toggle");
        row.addEventListener("click", event => {{
          if (event.target.closest("a, .shop-price")) return;
          const open = !row.classList.contains("open");
          row.classList.toggle("open", open);
          detail.hidden = !open;
          button.setAttribute("aria-expanded", String(open));
          button.textContent = open ? "詳細を閉じる" : "詳細を表示";
        }});
      }});
      const linkedRecord = new URLSearchParams(location.search).get("record");
      if (linkedRecord) {{
        const normalizeRecordUrl = value => {{
          const url = new URL(value, location.href);
          if (url.protocol === "http:") url.protocol = "https:";
          return url.href;
        }};
        const linkedUrl = normalizeRecordUrl(linkedRecord);
        const evidence = [...document.querySelectorAll(".evidence-record[data-source-url]")]
          .find(item => normalizeRecordUrl(item.dataset.sourceUrl) === linkedUrl);
        if (evidence) {{
          categoryFilter.value = "";
          reviewFilter.value = "";
          const detail = evidence.closest(".detail-row");
          if (detail) detail.previousElementSibling.querySelector("button.toggle").click();
          for (let node = evidence; node; node = node.parentElement) {{
            if (node.tagName === "DETAILS") node.open = true;
          }}
          const body = evidence.querySelector("details.review-body");
          if (body) body.open = true;
          evidence.scrollIntoView({{block: "center"}});
        }}
      }}
      function filterPeople() {{
        const query = search.value.trim().toLocaleLowerCase();
        let visible = 0;
        people.forEach(person => {{
          const ages = personAges.get(person);
          const categories = (reviewFilter.value === "candidate"
            ? person.dataset.candidateCategories : person.dataset.categories).split(" ");
          const reviewMatch = !reviewFilter.value || person.dataset.review === reviewFilter.value;
          const categoryMatch = !categoryFilter.value || (categoryFilter.value === "booking"
            ? categories.includes("difficulty") || categories.includes("soldout")
            : categories.includes(categoryFilter.value));
          const ageMatch = !ageFilter.value || (ageFilter.value === "unknown"
            ? ages.length === 0
            : ages.some(age => ageFilter.value === "under30" ? age < 30 : age >= 30));
          const matches = reviewMatch && categoryMatch && ageMatch
            && (typeof shopPriceMatches !== "function" || shopPriceMatches(person))
            && person.textContent.toLocaleLowerCase().includes(query);
          person.hidden = !matches;
          if (matches) visible += 1;
        }});
        document.getElementById("visibleCount").textContent = `${{visible}} / ${{people.length}} 人`;
        document.getElementById("noResults").hidden = visible !== 0;
      }}
      search.addEventListener("input", filterPeople);
      ageFilter.addEventListener("change", filterPeople);
      categoryFilter.addEventListener("change", filterPeople);
      reviewFilter.addEventListener("change", filterPeople);
      filterPeople();
    </script>
  </body>
</html>
""")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-prices", action="store_true", help="保存HTMLの人物・根拠を保持して料金とフィルタだけ更新")
    args = parser.parse_args(argv)
    if args.refresh_prices:
        write_html(OUTPUT_HTML, refresh_saved_prices(
            OUTPUT_HTML.read_text(encoding="utf-8"), render_html([], {}), "function filterPeople()",
        ))
        print(f"保存HTMLの人物・根拠を保持して料金を更新しました: {OUTPUT_HTML}")
        return
    people, source_counts = extract_people()
    write_html(OUTPUT_HTML, render_html(people, source_counts))
    print(
        f"予約困難レポートを生成: {OUTPUT_HTML} "
        f"({len(people)}人、根拠{sum(source_counts.values())}件)"
    )


if __name__ == "__main__":
    main()
