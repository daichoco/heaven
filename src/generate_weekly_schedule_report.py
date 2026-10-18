"""3レポート全体の横断順位と、上位者の本日から7日間のCityHeaven予定。既定はオフライン。"""
import argparse
import asyncio
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from data_store import read_jsonl_records
from generate_booking_difficulty_report import (
    DATASETS,
    candidate_person_key,
    filter_unavailable_records,
    relative_scores,
    unavailable_article_markers,
)
from html_format import write_html
from tokai_fuzoku.report_store import normalize_profile_url
from web_common import BACK_NAV, BACK_TO_TOP, HEAD_COMMON

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "weekly_schedule.html"
CACHE = ROOT / "data" / "cityheaven_weekly_schedules.json"
JST = timezone(timedelta(hours=9))
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


def rank_people(datasets):
    people = {}
    for label, dataset, records in datasets:
        prepared = []
        for record in records:
            profile = next((
                url for value in (record.get("cityheaven_url"), record.get("girl_url"))
                if (url := cityheaven_profile_url(value))
            ), "")
            # External/missing profiles remain article-specific, never name-matched.
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
            })
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
    try:
        response = await page.goto(person["profile_url"], wait_until="domcontentloaded")
        if response is None or response.status >= 400:
            raise ValueError(f"HTTP {response.status if response else '応答なし'}")
        if cityheaven_profile_url(page.url) != person["profile_url"]:
            raise ValueError("別ページに転送されました（認証・掲載状況を確認）")
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
    }


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


def render_html(people, dates, schedules, removed, now=None):
    now = now or datetime.now(JST)
    labels = [
        f'{day}（{"月火水木金土日"[datetime.fromisoformat(day).weekday()]}）'
        for day in dates
    ]
    day_classes = [
        {5: "saturday", 6: "sunday"}.get(datetime.fromisoformat(day).weekday(), "")
        for day in dates
    ]
    rows = []
    visible_count = 0
    for person in people:
        name = escape(" / ".join(person["names"]) or "名前不明")
        shop = escape(" / ".join(person["shops"]) or "店舗不明")
        profile = escape(person["profile_url"], quote=True)
        identity = (
            f'<a href="{profile}" target="_blank" rel="noopener">{name}</a>'
            f'<br><small>{profile}</small>' if profile else f"{name}<br>CityHeaven URL未突合"
        )
        rating_items = "".join(
            f'<li>{escape(label)}: {rating["score"]:.2f} '
            f'（原評価平均 {rating["mean"]:.2f} / {rating["count"]}件）</li>'
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
        provisional = " / 暫定" if person["provisional"] else ""
        hidden = "" if working_days else " hidden"
        visible_count += bool(working_days)
        rows.append(
            f'<tr class="person-row" data-working="{",".join(working_days)}"{hidden}>'
            f'<td class="shop-cell">{identity}<br><span class="shop-info">{shop}</span>'
            f'<p class="rank-info">{person["rank"]}位 / {person["score"]:.2f}点 / '
            f'{person["tier"]}{provisional}<br>{person["source_count"]}元 / {person["review_count"]}件</p>'
            f'<details><summary>評価内訳・取得状況</summary><ul>{rating_items}</ul>'
            f'<p>取得日時: {escape(result["fetched_at"]) or "未取得"}</p>{error}</details>'
            f'<div class="m-cal"><ul>{"".join(mobile_days)}</ul></div></td>'
            f'{"".join(cells)}</tr>'
        )
    removed_text = " / ".join(f"{label}: {count}件" for label, count in removed.items())
    headers = "".join(
        f'<th class="day-col {day_classes[index]}" data-day="{index}">{label}</th>'
        for index, label in enumerate(labels)
    )
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8">{HEAD_COMMON}
<title>3レポート横断・7日間出勤カレンダー</title>
<link rel="stylesheet" href="weekly_schedule.css">
<script src="weekly_schedule.js" defer></script></head>
<body>{BACK_NAV}
<h1>3レポート横断・7日間出勤カレンダー</h1>
<p>対象期間: {labels[0]}〜{labels[-1]} 日本時間（生成時の本日から7日間）。レポート生成日時:
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
<div class="sticky-toolbar" id="toolbar">
<div class="day-picker"><label for="scheduleFilter">表示条件</label>
<select id="scheduleFilter">
<option value="working" selected>予定ありのみ（7日間）</option>
<option value="today">本日出勤</option>
<option value="weekend">土日出勤</option>
<option value="all">全表示（未取得・休み等も含む）</option>
</select></div>
<p class="filter-count" id="dayCount" aria-live="polite">予定ありのみ（7日間）: 表示 {visible_count}名 / 母集団 {len(people)}名（順位・S/Aは固定）</p>
</div>
<p class="no-results" id="noResults" {"" if visible_count == 0 else "hidden"} role="status">条件に合う出勤予定はありません。
未取得・未公開・取得エラーは出勤なしを意味しません。「全表示」で取得状況を確認してください。</p>
<p id="staleNotice" hidden role="status">本日は保存期間に含まれません。ページを再生成・再取得してください。</p>
<noscript><p>JavaScriptが無効です。生成時の7日間に予定がある人物のみ表示しています。
セレクタを使うにはJavaScriptを有効にしてください。</p></noscript>
<div class="table-scroll" role="region" aria-label="横断順位と週間予定" tabindex="0">
<table data-dates="{",".join(dates)}"><thead><tr><th>氏名・店舗／順位・評価</th>{headers}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>{BACK_TO_TOP}</body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Sの公開CityHeaven週間予定を取得")
    parser.add_argument("--include-a", action="store_true", help="--collect でSに続いてAも取得")
    args = parser.parse_args(argv)
    if args.include_a and not args.collect:
        parser.error("--include-a は --collect と併用してください")
    now = datetime.now(JST)
    dates = schedule_dates(now)
    people, removed = load_ranking()
    cache = load_cache()
    schedules = cache["windows"].get(dates[0], {})
    failures = 0
    if args.collect:
        results = asyncio.run(collect_schedules(selected_people(people, args.include_a), dates))
        schedules.update(results)
        cache["windows"][dates[0]] = schedules
        save_cache(cache)
        failures = sum(bool(result["error"]) for result in results.values())
    write_html(OUTPUT, render_html(people, dates, schedules, removed, now))
    print(f"出力: {OUTPUT} / {len(people)}人 / 取得エラー {failures}件")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
