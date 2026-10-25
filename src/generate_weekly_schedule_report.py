"""3レポート横断の7日間予定。保存予定を使い、不足画像は自動取得。--offline で通信なし。"""
import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from data_store import read_jsonl_records
from generate_booking_difficulty_report import (
    DATASETS,
    candidate_person_key,
    filter_unavailable_records,
    relative_scores,
    rating_value,
    record_fields,
    record_url_key,
    safe_http_url,
    unavailable_article_markers,
)
from html_format import write_html
from okinilove.render_people_summary import extract_profile_image_url
from ns_senmon.profile_images import fetch_profile_images
from tokai_fuzoku.report_store import normalize_profile_url
from tokai_fuzoku.html_render import REPORT_DETAIL_FIELDS
from web_common import BACK_NAV, BACK_TO_TOP, HEAD_COMMON
import shop_prices

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "weekly_schedule.html"
CACHE = ROOT / "data" / "cityheaven_weekly_schedules.json"
IMAGE_CACHE = ROOT / "data" / "cityheaven_weekly_images.json"
JST = timezone(timedelta(hours=9))
PAGE_SIZE = 50
ASSET_DIRECTORY = "weekly_schedule_assets"
STATE_LABELS = {
    "working": "出勤予定",
    "off": "休み",
    "undecided": "未定",
    "unpublished": "未公開",
    "unknown": "要確認",
    "error": "取得エラー",
    "not_fetched": "未取得",
    "no_profile": "CityHeaven URL未突合",
}


def cityheaven_profile_url(value):
    """Only fetch real CityHeaven person URLs; tracking/subpages share an identity."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"www.cityheaven.net", "cityheaven.net"}
            or parsed.port not in {None, 80, 443}
            or parsed.username is not None
            or parsed.password is not None
            or not re.search(r"/girlid-\d+(?:/|$)", parsed.path)
        ):
            return ""
    except ValueError:
        return ""
    normalized = normalize_profile_url(value)
    return normalized.replace("https://cityheaven.net/", "https://www.cityheaven.net/")


def schedule_dates(now=None):
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です")
    today = now.astimezone(JST).date()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(7)]


def external_person_key(value):
    try:
        parsed = urlsplit(str(value or ""))
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"www.yoasobi.co.jp", "yoasobi.co.jp"}
            and parsed.username is None and parsed.password is None
            and parsed.port in {None, 80, 443}
        ):
            if re.fullmatch(r"/tokai/[^/]+/shop/\d+/girl/\d+/?", parsed.path):
                return parsed.path.rstrip("/")
            ids = parse_qs(parsed.query).get("ii", [])
            if parsed.path == "/mb/st_item.cfm" and len(ids) == 1 and ids[0].isdigit():
                return f"/mb/st_item.cfm?ii={ids[0]}"
    except ValueError:
        return ""
    return ""


def rank_people(datasets):
    datasets = [(label, dataset, list(records)) for label, dataset, records in datasets]
    profile_aliases = {}
    for _, _, records in datasets:
        for record in records:
            alias = external_person_key(record.get("girl_url"))
            profile = cityheaven_profile_url(record.get("cityheaven_url"))
            if alias and profile:
                profile_aliases.setdefault(alias, set()).add(profile)
    people = {}
    for label, dataset, records in datasets:
        prepared = []
        for record in records:
            profile = next((
                url for value in (record.get("cityheaven_url"), record.get("girl_url"))
                if (url := cityheaven_profile_url(value))
            ), "")
            aliases = profile_aliases.get(external_person_key(record.get("girl_url")), set())
            if not profile and len(aliases) == 1:
                profile = next(iter(aliases))
            # Unresolved profiles remain article-specific, never name-matched.
            source = record.get("url") or record.get("report_url") or ""
            if source:
                parsed = urlsplit(source)
                source = parsed._replace(scheme="https", netloc=parsed.netloc.casefold()).geturl()
            prepared.append({
                **record, "cityheaven_url": profile, "girl_url": "", "url": source,
            })
        scores = relative_scores(dataset, prepared)
        for row_number, record in enumerate(prepared, start=1):
            key = candidate_person_key(dataset, record, row_number)
            if key not in scores:
                continue
            person = people.setdefault(key, {
                "key": key,
                "profile_url": record["cityheaven_url"],
                "names": [],
                "shops": [],
                "ratings": {},
                "reported_ages": [],
                "reports": [],
            })
            source = record.get("url") or record.get("report_url") or ""
            article_key = (label, record_url_key(source) if source else row_number)
            if not any(report["identity"] == article_key for report in person["reports"]):
                fields = record_fields(dataset, record)
                if dataset == "tokai_fuzoku":
                    known = {key for key, _ in fields}
                    fields += [
                        (key, str(value)) for key, value in record.items()
                        if value is not None and key not in known and key.endswith(("_comment", "_score", "_point"))
                    ]
                person["reports"].append({
                    "identity": article_key, "source": source, "database": label,
                    "date": str(record.get("date") or "保存日付なし"),
                    "score": rating_value(dataset, record), "fields": fields,
                    **({"record": record} if dataset == "tokai_fuzoku" else {}),
                })
            age_value = (
                (record.get("details") or {}).get("推定年齢")
                if dataset == "ns_senmon" else
                record.get("age") if dataset == "okinilove" else record.get("predict_age")
            )
            match = re.fullmatch(r"\s*(\d{1,2})(?:\.0)?\s*(?:歳|才)?\s*", str(age_value or ""))
            if match and 0 < int(match[1]) < 100 and int(match[1]) not in person["reported_ages"]:
                person["reported_ages"].append(int(match[1]))
            for field, value in (
                ("names", record.get("name") or record.get("girl_name")),
                ("shops", record.get("shop_name")),
            ):
                if value and value not in person[field]:
                    person[field].append(value)
            person["ratings"][label] = scores[key]

    for person in people.values():
        ratings = list(person["ratings"].values())
        person["score"] = sum(item["score"] for item in ratings) / len(ratings)
        person["source_count"] = len(ratings)
        person["review_count"] = sum(item["count"] for item in ratings)
        person["provisional"] = len(ratings) == 1
    ordered = sorted(people.values(), key=lambda person: (-person["score"], person["key"]))
    s_end = math.ceil(len(ordered) * 0.10)
    a_end = math.ceil(len(ordered) * 0.25)
    rank = 0
    previous = None
    for index, person in enumerate(ordered, start=1):
        if person["score"] != previous:
            rank = index
        previous = person["score"]
        person["rank"] = rank
        person["tier"] = "S" if rank <= s_end else "A" if rank <= a_end else "対象外"
    return ordered


def load_ranking():
    datasets = [
        (label, dataset, read_jsonl_records(ROOT / "data" / filename))
        for label, filename, dataset, _ in DATASETS
    ]
    filtered, removed = filter_unavailable_records(
        datasets, unavailable_article_markers(datasets)
    )
    return rank_people(filtered), removed


def daily_states(dates, state, text=""):
    return [{"date": day, "state": state, "text": text} for day in dates]


def schedule_state(text):
    if not text or re.search(r"未公開|公開前|非公開", text):
        return "unpublished"
    if re.search(r"未定|調整中", text):
        return "undecided"
    if re.fullmatch(r"(?:お休み|休み|休暇|休日|店休日|定休日|出勤なし|お休みです)", text):
        return "off"
    if re.search(r"\d{1,2}:\d{2}\s*[-~～〜－–]\s*(?:\d{1,2}:\d{2}|LAST|ラスト)", text, re.I):
        return "working"
    return "unknown"


def parse_month_calendar(html, dates):
    soup = BeautifulSoup(html, "html.parser")
    calendar = soup.select_one("#syukin_month")
    if calendar is None:
        raise ValueError("月間予定 #syukin_month がありません（認証・掲載状況を確認）")
    rows = calendar.select("div.girlitem_waku")
    if not rows:
        raise ValueError("月間予定の日付行がありません")
    by_day = {}
    for row in rows:
        date_node = row.select_one("span.girlitem_waku_left")
        status_node = row.select_one("span.girlitem_waku_right")
        if date_node is None or status_node is None:
            raise ValueError("月間予定の日付・予定の構造が変わっています")
        label = date_node.get_text(" ", strip=True)
        match = re.search(
            r"(?<!\d)(?:(?P<year>\d{4})[年/\-])?(?P<month>\d{1,2})[月/\-](?P<day>\d{1,2})日?(?!\d)",
            label,
        )
        if not match:
            raise ValueError(f"解釈できない予定日: {label}")
        datetime(int(match["year"] or 2000), int(match["month"]), int(match["day"]))
        text = " ".join(status_node.get_text(" ", strip=True).split())
        for day in dates:
            date = datetime.fromisoformat(day)
            if (
                (date.month, date.day) == (int(match["month"]), int(match["day"]))
                and (not match["year"] or date.year == int(match["year"]))
            ):
                item = {"date": day, "state": schedule_state(text), "text": text}
                if day in by_day and by_day[day] != item:
                    raise ValueError(f"同じ日付に異なる予定があります: {day}")
                by_day[day] = item
    return [
        by_day.get(day, {"date": day, "state": "unpublished", "text": ""})
        for day in dates
    ]


def selected_people(people, include_a=False):
    tiers = {"S", "A"} if include_a else {"S"}
    return [person for person in people if person["tier"] in tiers and person["profile_url"]]


async def fetch_person(context, person, dates):
    from playwright.async_api import Error as PlaywrightError

    page = await context.new_page()
    error = ""
    image = {"status": "error", "image_url": ""}
    try:
        response = await page.goto(person["profile_url"], wait_until="domcontentloaded")
        if response is None or response.status >= 400:
            raise ValueError(f"HTTP {response.status if response else '応答なし'}")
        if cityheaven_profile_url(page.url) != person["profile_url"]:
            raise ValueError("別ページに転送されました（認証・掲載状況を確認）")
        image_url = extract_profile_image_url(
            await page.content(), page.url, (person.get("names") or [""])[0]
        )
        image = {"status": "ok" if image_url else "no_image", "image_url": image_url or ""}
        await page.wait_for_selector("#syukin_month .girlitem_waku", state="attached")
        days = parse_month_calendar(await page.content(), dates)
    except (PlaywrightError, ValueError) as exc:
        error = str(exc)
        print(f"取得エラー {person['profile_url']}: {error}", file=sys.stderr)
        days = daily_states(dates, "error")
    finally:
        await page.close()
    return {
        "profile_url": person["profile_url"],
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "days": days,
        "error": error,
        "image": image,
    }


def saved_images(schedules):
    """Read existing profile caches without fetching or modifying schedule snapshots."""
    images = {}
    for filename in ["ns_senmon_profile_image_cache.json", IMAGE_CACHE.name]:
        path = ROOT / "data" / filename
        if path.exists():
            for url, entry in json.loads(path.read_text(encoding="utf-8")).items():
                key = cityheaven_profile_url(url)
                if key:
                    images[key] = entry
    calendar_path = ROOT / "docs" / "calendar_report.html"
    if calendar_path.exists():
        soup = BeautifulSoup(calendar_path.read_text(encoding="utf-8"), "html.parser")
        for row in soup.select("tr"):
            link = row.select_one(".shop-cell a[href]")
            img = row.select_one("img[src]")
            key = cityheaven_profile_url(link["href"]) if link else ""
            if key and img and not images.get(key, {}).get("image_url"):
                images[key] = {"status": "ok", "image_url": img["src"]}
    for url, result in schedules.items():
        image = result.get("image")
        if image and (image.get("image_url") or not images.get(url, {}).get("image_url")):
            images[url] = image
    return images


def image_html(person, images):
    image = images.get(person["profile_url"], {})
    url = image.get("image_url") or ""
    try:
        valid = urlsplit(url).scheme in {"http", "https"}
    except ValueError:
        valid = False
    status = image.get("status")
    label = (
        "画像取得失敗" if status in {"error", "not_found"}
        else "画像URL未検出" if status == "no_image" else "画像未取得"
    )
    if valid:
        return (
            '<div class="profile-photo">'
            f'<img src="{escape(url, quote=True)}" alt="{escape(" / ".join(person["names"]), quote=True)}" '
            'width="160" height="180" loading="lazy" decoding="async">'
            '<span class="image-error" hidden>画像読込失敗</span></div>'
            + ('<small>画像再取得失敗・保存画像を表示</small>' if status == "error" else "")
        )
    return f'<div class="profile-photo image-placeholder">{label}</div>'


def render_reports(person, database=None):
    def saved_text(text):
        return re.sub(
            r"[ \t]+(?=\r?$)",
            lambda match: "".join(f"&#{ord(char)};" for char in match[0]),
            escape(text),
            flags=re.MULTILINE,
        )

    reports = []
    for report in person.get("reports", []):
        if database is not None and report["database"] != database:
            continue
        score = report["score"]
        scale = "/5（星）" if report["database"] == "オキニラブ" else "/100点"
        score_text = f"{score:g}{scale}" if score is not None else "未評価"
        url = safe_http_url(report["source"])
        source = (
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">元レポート</a>'
            if url else "出典URLなし"
        )
        body = "".join(
            f'<div class="saved-field"><strong>{escape(label)}</strong>'
            f'<p>{saved_text(text)}</p></div>' for label, text in report["fields"]
        ) or "<p>保存済み本文なし（外部から追加取得していません）</p>"
        if report["database"] == "東海風俗" and "record" in report:
            items = []
            for key, comment_key, label in REPORT_DETAIL_FIELDS:
                record = report["record"]
                value = record.get(key)
                comment = record.get(comment_key)
                value = "" if value is None else str(value)
                comment = "" if comment is None else str(comment)
                if not value and not comment:
                    continue
                heading = f"{label}: {value}" if value else label
                items.append(
                    f'<li class="saved-field"><strong>{escape(heading)}:</strong> '
                    f'<p>{saved_text(comment)}</p></li>'
                )
            body = f'<ul class="tokai-evaluations">{"".join(items)}</ul>' if items else (
                "<p>保存済み本文なし（外部から追加取得していません）</p>"
            )
        reports.append(
            f'<section class="saved-report"><h3>{escape(report["database"])} / '
            f'{escape(report["date"])} / 元評価 {score_text}</h3>'
            f'{body + source if report["database"] == "東海風俗" else source + body}</section>'
        )
    return "".join(reports)


async def collect_schedules(people, dates):
    from playwright.async_api import async_playwright
    from cityheaven.exists_merge import _env_flag, _block_heavy_resources

    results = {}
    if not people:
        return results
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=_env_flag("CITYHEAVEN_HEADLESS") is not False,
        )
        try:
            context = await browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 "
                           "Mobile/15E148 Safari/604.1",
            )
            context.set_default_timeout(15000)
            if _env_flag("CITYHEAVEN_BLOCK_RESOURCES") is not False:
                await context.route("**/*", _block_heavy_resources)
            for person in people:
                results[person["profile_url"]] = await fetch_person(context, person, dates)
            await context.close()
        finally:
            await browser.close()
    return results


def load_cache(path=CACHE):
    path = Path(path)
    if not path.exists():
        return {"version": 2, "windows": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") == 1 and isinstance(payload.get("weeks"), dict):
        # Legacy Monday windows retain their exact dates, never shift to today.
        return {"version": 2, "windows": payload["weeks"]}
    if payload.get("version") != 2 or not isinstance(payload.get("windows"), dict):
        raise ValueError(f"週間予定キャッシュの形式が不正です: {path}")
    return payload


def save_cache(payload, path=CACHE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def person_schedule(person, dates, schedules):
    result = schedules.get(person["profile_url"])
    if result is not None:
        if [day["date"] for day in result["days"]] != dates:
            raise ValueError(f"保存予定の対象日が不正です: {person['profile_url']}")
        return result
    state = "not_fetched" if person["profile_url"] else "no_profile"
    return {"fetched_at": "", "days": daily_states(dates, state), "error": ""}


def json_script(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def add_asset(assets, kind, value):
    content = json_script(value)
    name = f"{kind}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]}.json"
    assets[name] = content
    return f"{ASSET_DIRECTORY}/{name}"


def write_weekly_assets(assets, output):
    directory = output.parent / ASSET_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in assets.items():
        path = directory / name
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)


def prune_weekly_assets(assets, output):
    directory = output.parent / ASSET_DIRECTORY
    for path in directory.iterdir():
        if re.fullmatch(r"(?:list|review)-[0-9a-f]{24}\.json", path.name) and path.name not in assets:
            path.unlink()


def render_html(people, dates, schedules, removed, now=None, images=None, assets=None):
    now = now or datetime.now(JST)
    images = images or {}
    assets = assets if assets is not None else {}
    prices = shop_prices.load_cache()
    period_note = (
        "生成時の本日から7日間" if dates[0] == now.astimezone(JST).date().isoformat()
        else "保存済み対象期間・本日へずらしません"
    )
    labels = [
        f'{day}（{"月火水木金土日"[datetime.fromisoformat(day).weekday()]}）'
        for day in dates
    ]
    day_classes = [
        {5: "saturday", 6: "sunday"}.get(datetime.fromisoformat(day).weekday(), "")
        for day in dates
    ]
    rows = []
    listing_index = []
    person_ids = set()
    visible_count = 0
    for person in people:
        person_id = hashlib.sha256(person["key"].encode("utf-8")).hexdigest()[:24]
        if person_id in person_ids:
            raise ValueError("週間人物IDが重複しています")
        person_ids.add(person_id)
        name = escape(" / ".join(person["names"]) or "名前不明")
        shop = escape(" / ".join(person["shops"]) or "店舗不明")
        profile = escape(person["profile_url"], quote=True)
        price_url = shop_prices.shop_url(person["profile_url"])
        price_entry = prices["shops"].get(price_url, {})
        selected_price = price_entry.get("selected") if price_entry.get("status") == "ok" else None
        price_amount = selected_price["yen_per_60"] if selected_price else ""
        identity = (
            f'<a href="{profile}" target="_blank" rel="noopener">{name}</a>'
            if profile else f"{name}<br>CityHeaven URL未突合"
        )
        rating_items = "".join(
            f'<li>{escape(label)}: 元評価平均 '
            f'{"★" if label == "オキニラブ" else ""}{rating["mean"]:.2f}'
            f'{"/5" if label == "オキニラブ" else "/100点"} '
            f'（評価 {rating["count"]}件） / 相対スコア {rating["score"]:.2f}/100</li>'
            for label, rating in person["ratings"].items()
        )
        original_ratings = "".join(
            f'<div>{escape(label)}: 元評価平均 '
            f'{"★" if label == "オキニラブ" else ""}{rating["mean"]:.2f}'
            f'{"/5" if label == "オキニラブ" else "/100点"}（{rating["count"]}件）</div>'
            for label, rating in person["ratings"].items()
        )
        result = person_schedule(person, dates, schedules)
        mobile_days = []
        cells = []
        working_days = []
        for index, day in enumerate(result["days"]):
            working = day["state"] == "working" and not result["error"]
            if working:
                working_days.append(day["date"])
            day_class = day_classes[index]
            text = f'<strong>{STATE_LABELS[day["state"]]}</strong><br>{escape(day["text"])}'
            cells.append(
                f'<td data-day="{index}" data-state="{day["state"]}" '
                f'data-on="{int(working)}" class="{day_class}"><span class="cell">{text}</span></td>'
            )
            mobile_days.append(
                f'<li data-day="{index}"><div class="m-date {day_class}">{labels[index]}</div>'
                f'<div class="m-box {"on" if working else "off"}">{text}</div></li>'
            )
        error = f'<p role="alert">{escape(result["error"])}</p>' if result["error"] else ""
        provisional = '<span class="status-badge">暫定</span>' if person["provisional"] else ""
        tier_class = {"S": "tier-s", "A": "tier-a"}.get(person["tier"], "tier-other")
        ages = person.get("reported_ages", [])
        age_text = " / ".join(f"{age}歳" for age in ages) or "不明"
        visible_count += bool(working_days)
        review_buttons = []
        bodies = {}
        for source_index, (label, _, _, _) in enumerate(DATASETS):
            count = sum(report["database"] == label for report in person.get("reports", []))
            if not count:
                continue
            row_id = f"review-{person_id}-{source_index}"
            review_buttons.append(
                f'<button type="button" class="review-toggle" aria-controls="{row_id}" '
                f'data-source="{source_index}" aria-expanded="false">レビュー（{escape(label)} {count}件）</button>'
            )
            bodies[str(source_index)] = render_reports(person, label)
        body_url = add_asset(assets, "review", {"id": person_id, "sources": bodies}) if bodies else ""
        rows.append(
            f'<tr class="person-row" data-person-id="{person_id}" data-body-url="{body_url}" '
            f'data-working="{",".join(working_days)}" '
            f'data-shop-price="{price_amount}" data-shop-price-status="{escape(price_entry.get("status", "not_fetched"), quote=True)}" '
            f'data-ages="{",".join(map(str, ages))}">'
            f'<td class="shop-cell">{image_html(person, images)}'
            f'<div class="person-identity"><div class="person-name">{identity}</div>'
            f'<div class="shop-info">{shop}</div></div>{shop_prices.price_info(price_entry, price_url)}'
            f'<div class="rank-info"><dl class="ranking-metrics">'
            f'<div><dt>横断順位</dt><dd>{person["rank"]}<small>位</small></dd></div>'
            f'<div><dt>横断相対スコア</dt><dd>{person["score"]:.2f}<small>/100</small></dd></div></dl>'
            f'<div class="ranking-badges"><span class="tier-badge {tier_class}">'
            f'区分 {person["tier"]}</span>{provisional}</div>'
            f'<dl class="rating-counts"><div><dt>評価元</dt><dd>{person["source_count"]}サイト</dd></div>'
            f'<div><dt>評価</dt><dd>{person["review_count"]}件</dd></div></dl></div>'
            f'<div class="original-ratings"><strong>元レポートの評価</strong>{original_ratings}</div>'
            f'<div class="reported-age">保存レポート年齢: {age_text}（現在年齢とは限りません）</div>'
            f'<details><summary>評価内訳・取得状況</summary><ul>{rating_items}</ul>'
            f'<p>取得日時: {escape(result["fetched_at"]) or "未取得"}</p>{error}</details>'
            f'<div class="review-buttons">{"".join(review_buttons)}</div>'
            f'<div class="m-cal"><ul>{"".join(mobile_days)}</ul></div></td>'
            f'{"".join(cells)}</tr>'
        )
        listing_index.append({
            "id": person_id, "working": working_days, "ages": ages,
            "price": price_amount, "priceStatus": price_entry.get("status", "not_fetched"),
            "search": " ".join(person["names"] + person["shops"] + [person["profile_url"]]).casefold(),
        })
    for start in range(0, len(rows), PAGE_SIZE):
        url = add_asset(assets, "list", {
            "rows": {listing_index[i]["id"]: rows[i] for i in range(start, min(start + PAGE_SIZE, len(rows)))},
        })
        for item in listing_index[start:start + PAGE_SIZE]:
            item["url"] = url
    initial = [rows[i] for i, item in enumerate(listing_index) if item["working"]][:PAGE_SIZE]
    removed_text = " / ".join(f"{label}: {count}件" for label, count in removed.items())
    headers = "".join(
        f'<th class="day-col {day_classes[index]}" data-day="{index}">{label}</th>'
        for index, label in enumerate(labels)
    )
    date_options = "".join(
        f'<option value="{day}">{label}</option>' for day, label in zip(dates, labels)
    )
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8">{HEAD_COMMON}
<title>3レポート横断・7日間出勤カレンダー</title>
<link rel="stylesheet" href="shop_prices.css">
<script src="shop_prices.js"></script>
<link rel="stylesheet" href="weekly_schedule.css">
<script src="weekly_schedule.js" defer></script></head>
<body data-details-scroll="native">{BACK_NAV}
<h1>3レポート横断・7日間出勤カレンダー</h1>
<p>対象期間: {labels[0]}〜{labels[-1]} 日本時間（{period_note}）。レポート生成日時:
{escape(now.astimezone(JST).isoformat(timespec="seconds"))}。評価あり {len(people)}人。</p>
<details class="method"><summary>ランキング・取得方針</summary>
<p>全評価人物が母集団です（予約困難・人気の文言、年齢、3件未満で除外しません）。
掲載終了扱いの保存記事は除外: {escape(removed_text)}。</p>
<p>各評価元の人物平均を同点平均順位の0〜100パーセンタイルに変換し、
（件数×パーセンタイル＋3×50）÷（件数＋3）で補正します。利用できる評価元を等重み平均し、
欠けた評価元は0点扱いしません。1評価元は暫定。件数は元ごとに同一人物・記事URLの重複を除いた評価件数です。
同じ正規化CityHeaven URLだけを統合し、未突合は記事単位で残します。無評価は順位対象外です。</p>
<p>Sは上位10%（人数切り上げ）、Aはその次から上位25%（人数切り上げ）。
同点は競技順位（1, 1, 3）とし、境界同点は全員を上位区分に含めるため割合を超える場合があります。
少人数ではSのみとなる場合があります。表示は小数2桁ですが区分判定は丸め前のスコアです。</p>
<p>予定取得はS優先、Aは追加指定。表示フィルタでは順位・母集団・S/A選定を変更しません。
未取得・未公開・未定・休み・要確認・取得エラーは区別します。空欄や欠けた日を休みと推測しません。
予定は取得日時点の公開情報であり、予約可能性を保証しません。</p></details>
{shop_prices.PRICE_CONTROLS}
<div class="sticky-toolbar" id="toolbar">
<label for="weeklySearch">名前・店舗・プロフィールURLで検索（全員対象）</label>
<input id="weeklySearch" type="search" autocomplete="off">
<div class="day-picker"><label for="scheduleFilter">表示条件</label>
<select id="scheduleFilter">
<option value="working" selected>予定ありのみ（7日間）</option>
<option value="today">本日出勤</option>
<option value="weekend">土日出勤</option>
<option value="date">日付指定</option>
<option value="all">全表示（未取得・休み等も含む）</option>
</select></div>
<div class="day-picker"><label for="daySelect">日付指定</label>
<select id="daySelect">{date_options}</select></div>
<div class="day-picker"><label for="ageFilter">保存レポート年齢</label>
<select id="ageFilter" aria-label="年齢区分">
<option value="" selected>絞り込みなし</option>
<option value="under30">30歳未満</option><option value="over30">30歳以上</option>
<option value="unknown">年齢不明</option></select></div>
<button type="button" id="weeklyReset">条件をリセット</button>
<p class="filter-count" id="dayCount" aria-live="polite">予定ありのみ（7日間）: 表示 {visible_count}名 / 母集団 {len(people)}名（順位・S/Aは固定）</p>
</div>
<p class="no-results" id="noResults" {"" if visible_count == 0 else "hidden"} role="status">条件に合う出勤予定はありません。
未取得・未公開・取得エラーは出勤なしを意味しません。「全表示」と年齢「絞り込みなし」、料金条件を確認してください。</p>
<p id="staleNotice" hidden role="status">本日は保存期間に含まれません。ページを再生成・再取得してください。</p>
<p id="fileNotice" hidden role="status">ファイル直開きでは別ファイルの取得が制限されます。
初期の一覧のみ閲覧できます。レビュー・ページ移動はGitHub Pages、またはリポジトリで
<code>py -3 -m http.server 8000 --directory docs</code> を実行し
<code>http://localhost:8000/weekly_schedule.html</code> で閲覧してください。</p>
<noscript><p>JavaScriptが無効です。予定ありの先頭50人のみ表示します。全員の検索・ページ移動・本文にはJavaScriptとHTTP配信が必要です。</p></noscript>
<nav class="weekly-pagination" aria-label="人物一覧のページ">
<button id="weeklyPrevious" type="button" disabled>前の50人</button>
<span id="weeklyPage" role="status" aria-live="polite"></span>
<button id="weeklyNext" type="button">次の50人</button>
</nav>
<p id="weeklyLoadStatus" role="status" aria-live="polite"></p>
<button id="weeklyRetry" type="button" hidden>一覧の読み込みを再試行</button>
<div class="table-scroll" role="region" aria-label="横断順位と週間予定" tabindex="0">
<table data-dates="{",".join(dates)}"><thead><tr><th>氏名・店舗／順位・評価</th>{headers}</tr></thead>
<tbody>{"".join(initial)}</tbody></table></div>
<script type="application/json" id="weeklyIndex">{json_script({"version": 1, "pageSize": PAGE_SIZE, "people": listing_index})}</script>
{BACK_TO_TOP}</body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Sの公開CityHeaven週間予定を取得")
    parser.add_argument("--collect-images", action="store_true", help="互換オプション（不足画像は既定で取得）")
    parser.add_argument("--offline", action="store_true", help="画像も予定も取得せず保存データだけで生成")
    parser.add_argument("--image-limit", type=int, help="明示的に不足画像取得対象数を制限（既定は対象全員）")
    parser.add_argument("--include-a", action="store_true", help="取得対象にSに続いてAも追加")
    parser.add_argument("--window-start", help="--offline 専用。保存済み開始日 YYYY-MM-DD の7日間を再生成")
    args = parser.parse_args(argv)
    if args.offline and (args.collect or args.collect_images):
        parser.error("--offline と収集オプションは併用できません")
    if args.image_limit is not None and args.image_limit < 1:
        parser.error("--image-limit は1以上で指定してください")
    if args.window_start and not args.offline:
        parser.error("--window-start は --offline と併用してください")
    now = datetime.now(JST)
    dates = schedule_dates(now)
    people, removed = load_ranking()
    cache = load_cache()
    if args.window_start:
        try:
            start = datetime.strptime(args.window_start, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--window-start は YYYY-MM-DD で指定してください")
        if start.isoformat() not in cache["windows"]:
            parser.error("指定した開始日の保存済み予定がありません")
        dates = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
    schedules = cache["windows"].get(dates[0], {})
    failures = 0
    if args.collect:
        results = asyncio.run(collect_schedules(selected_people(people, args.include_a), dates))
        schedules.update(results)
        cache["windows"][dates[0]] = schedules
        save_cache(cache)
        failures = sum(bool(result["error"]) for result in results.values())
    images = saved_images(schedules)
    if not args.offline:
        eligible = [
            person for person in people if person["profile_url"] and (
                person["tier"] == "S"
                or (person["tier"] == "A" and args.include_a)
                or person["profile_url"] in schedules
            )
        ]
        pending = [
            person for person in eligible
            if not images.get(person["profile_url"], {}).get("image_url")
        ]
        requested = pending[:args.image_limit] if args.image_limit is not None else pending
        fetched = fetch_profile_images(
            {person["profile_url"]: (person["names"] or [""])[0] for person in requested},
            path=IMAGE_CACHE,
        )
        images.update(fetched)
        missing = [person for person in pending if not images.get(person["profile_url"], {}).get("image_url")]
        counts = {
            state: sum(images.get(person["profile_url"], {}).get("status", "pending") == state for person in missing)
            for state in ["error", "not_found", "no_image", "pending"]
        }
        print(
            f"画像対象 {len(eligible)}人 / 既存画像 {len(eligible) - len(pending)}人 / "
            f"今回補完 {len(pending) - len(missing)}人 / 残り {len(missing)}人: {counts}"
        )
        failures += counts["error"] + counts["not_found"] + counts["pending"]
    assets = {}
    markup = render_html(people, dates, schedules, removed, now, images, assets)
    write_weekly_assets(assets, OUTPUT)
    write_html(OUTPUT, markup)
    # Remove only this generator's obsolete content-addressed artifacts after publishing HTML.
    prune_weekly_assets(assets, OUTPUT)
    print(f"出力: {OUTPUT} / {len(people)}人 / 取得エラー {failures}件")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
