import asyncio
import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate_weekly_schedule_report as weekly
from generate_booking_difficulty_report import TOTAL_KEY


def profile(number):
    return f"https://www.cityheaven.net/gifu/A2101/shop/girlid-{number}/"


def tokai(number, point=90, **extra):
    return {
        "url": f"https://example.invalid/article-{number}",
        "girl_url": profile(number),
        "name": f"人物{number}",
        "shop_name": "店舗",
        "total_point": point,
        **extra,
    }


def datasets(ns=(), oki=(), tokai_rows=()):
    return [
        ("NS専門", "ns_senmon", list(ns)),
        ("オキニラブ", "okinilove", list(oki)),
        ("東海風俗", "tokai_fuzoku", list(tokai_rows)),
    ]


def calendar_html(rows):
    return '<div id="syukin_month">' + "".join(
        f'<div class="girlitem_waku"><span class="girlitem_waku_left">{date}</span>'
        f'<span class="girlitem_waku_right">{status}</span></div>'
        for date, status in rows
    ) + "</div>"


class RankingTests(unittest.TestCase):
    def test_all_reviews_not_just_booking_candidates_and_equal_source_weight(self):
        one = tokai(1, 100)
        duplicate = {**one, "url": one["url"].replace("https:", "http:"),
                     "girl_url": profile(1) + "diary/?lo=1#top"}
        ns = {"url": "https://ns.invalid/1", "cityheaven_url": profile(1),
              "details": {TOTAL_KEY: "80点"}, "name": "別名"}
        oki = {"report_url": "https://oki.invalid/1#review", "girl_url": profile(1),
               "evaluations": "★★★★★", "girl_name": "人物1"}
        people = weekly.rank_people(datasets(
            ns=[ns], oki=[oki],
            tokai_rows=[one, duplicate, tokai(2, 0), tokai(3, None)],
        ))
        self.assertEqual(len(people), 2)
        first = people[0]
        self.assertEqual(first["profile_url"], profile(1))
        self.assertEqual(first["source_count"], 3)
        self.assertEqual(first["review_count"], 3)
        self.assertFalse(first["provisional"])
        self.assertAlmostEqual(first["score"], (62.5 + 50 + 50) / 3)
        self.assertEqual(set(first["names"]), {"人物1", "別名"})
        self.assertEqual(people[1]["score"], 37.5)
        self.assertTrue(people[1]["provisional"])

    def test_missing_sources_are_not_zero_and_review_fragments_remain_distinct(self):
        rows = [
            tokai(1, 100, url="http://example.invalid/a#one"),
            tokai(1, 100, url="https://example.invalid/a#two"),
            tokai(2, 50),
        ]
        first = weekly.rank_people(datasets(tokai_rows=rows))[0]
        self.assertEqual(first["review_count"], 2)
        self.assertEqual(first["score"], 70)
        self.assertEqual(first["tier"], "S")

    def test_only_cityheaven_profiles_merge_not_names_or_external_urls(self):
        rows = [
            tokai(1, girl_url="https://other.invalid/person"),
            tokai(2, girl_url="https://other.invalid/person", name="人物1"),
            tokai(3, girl_url=""),
            tokai(4, girl_url="", name="人物3"),
        ]
        people = weekly.rank_people(datasets(tokai_rows=rows))
        self.assertEqual(len(people), 4)
        self.assertTrue(all(not person["profile_url"] for person in people))
        self.assertEqual(weekly.selected_people(people), [])

    def test_unmatched_duplicate_articles_still_count_once(self):
        row = tokai(1, girl_url="")
        people = weekly.rank_people(datasets(tokai_rows=[
            row, {**row, "url": row["url"].replace("https:", "http:")},
        ]))
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["review_count"], 1)

    def test_cityheaven_normalization_and_host_validation(self):
        for value in [
            profile(1) + "diary/?lo=1#top",
            profile(1).replace("https://www.", "http://WWW."),
            profile(1).replace("www.cityheaven", "cityheaven"),
        ]:
            self.assertEqual(weekly.cityheaven_profile_url(value), profile(1))
        for value in [
            "https://evilcityheaven.net/x/girlid-1/",
            "https://www.cityheaven.net.evil.invalid/x/girlid-1/",
            "https://www.cityheaven.net/shop/", "javascript:alert(1)",
            "https://user:pass@www.cityheaven.net/x/girlid-1/",
            "https://www.cityheaven.net:8080/x/girlid-1/",
            "https://[broken", None,
        ]:
            self.assertEqual(weekly.cityheaven_profile_url(value), "")

    def test_percentile_boundaries_and_small_populations(self):
        for size, expected_s, expected_a in [
            (0, 0, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0),
            (4, 1, 0), (5, 1, 1), (10, 1, 2), (20, 2, 3), (21, 3, 3),
        ]:
            with self.subTest(size=size):
                people = weekly.rank_people(datasets(
                    tokai_rows=[tokai(i, i) for i in range(size)]
                ))
                self.assertEqual(sum(p["tier"] == "S" for p in people), expected_s)
                self.assertEqual(sum(p["tier"] == "A" for p in people), expected_a)
                self.assertEqual(len(weekly.selected_people(people)), expected_s)
                self.assertEqual(len(weekly.selected_people(people, True)), expected_s + expected_a)

    def test_boundary_ties_use_competition_rank_and_expand_upper_tier(self):
        rows = [tokai(i, value) for i, value in enumerate(
            [100, 100, 90, 80, 80, 80, 70, 60, 50, 40, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3]
        )]
        people = weekly.rank_people(datasets(tokai_rows=rows))
        self.assertEqual([p["rank"] for p in people[:7]], [1, 1, 3, 4, 4, 4, 7])
        self.assertEqual([p["tier"] for p in people[:7]], ["S", "S", "A", "A", "A", "A", "対象外"])
        all_tied = weekly.rank_people(datasets(tokai_rows=[tokai(i) for i in range(10)]))
        self.assertTrue(all(p["tier"] == "S" for p in all_tied))

    def test_load_ranking_reuses_unavailability_rules(self):
        rows = [
            tokai(1, profile_status="unavailable", profile_checked_at="2026-10-17"),
            tokai(2, profile_status="unknown"),
        ]
        with patch.object(weekly, "read_jsonl_records", side_effect=[[], [], rows]):
            people, removed = weekly.load_ranking()
        self.assertEqual([p["profile_url"] for p in people], [profile(2)])
        self.assertEqual(removed["東海風俗"], 1)


class WeeklyCalendarTests(unittest.TestCase):
    def setUp(self):
        self.dates = weekly.schedule_dates(datetime(2026, 10, 19, 19, tzinfo=weekly.JST))

    def test_today_jst_including_monday_and_year_rollover(self):
        self.assertEqual(self.dates, [f"2026-10-{day}" for day in range(19, 26)])
        sunday = weekly.schedule_dates(datetime(2026, 10, 18, tzinfo=weekly.JST))
        self.assertEqual(sunday, [f"2026-10-{day}" for day in range(18, 25)])
        # Sunday UTC already Monday in Japan.
        self.assertEqual(weekly.schedule_dates(datetime(2026, 10, 18, 15, tzinfo=timezone.utc)), self.dates)
        self.assertEqual(weekly.schedule_dates(datetime(2026, 12, 28, tzinfo=weekly.JST))[-1], "2027-01-03")
        with self.assertRaises(ValueError):
            weekly.schedule_dates(datetime(2026, 10, 18))

    def test_explicit_daily_states_and_weekdays_are_preserved(self):
        days = weekly.parse_month_calendar(calendar_html([
            ("10/19(月)", "10:00 - 18:00"), ("10/20(火)", "お休み"),
            ("10/21(水)", "未定"), ("10/22(木)", "未公開"),
            ("10/23(金)", "電話確認"), ("10/24(土)", ""),
            ("10/26(月)", "10:00 - 18:00"),
        ]), self.dates)
        self.assertEqual([day["state"] for day in days],
                         ["working", "off", "undecided", "unpublished", "unknown",
                          "unpublished", "unpublished"])
        self.assertEqual(days[0]["text"], "10:00 - 18:00")
        self.assertEqual(days[-1]["date"], "2026-10-25")
        self.assertEqual(weekly.schedule_state("-"), "unknown")
        self.assertEqual(weekly.schedule_state("未定 10:00 - 18:00"), "undecided")
        self.assertEqual(weekly.schedule_state("22:00 ～ 翌3:00"), "unknown")
        self.assertEqual(weekly.schedule_state("22:00 ～ LAST"), "working")

    def test_month_and_year_boundary_no_inferred_shifts(self):
        dates = weekly.schedule_dates(datetime(2026, 12, 28, tzinfo=weekly.JST))
        days = weekly.parse_month_calendar(calendar_html([
            ("12/28(月)", "10:00 - 18:00"), ("1/1(金)", "休み"),
            ("2026/1/2", "10:00 - 18:00"), ("2027年1月3日", "調整中"),
        ]), dates)
        self.assertEqual(days[0]["state"], "working")
        self.assertEqual(days[4]["state"], "off")
        self.assertEqual(days[5]["state"], "unpublished")
        self.assertEqual(days[6]["state"], "undecided")

    def test_missing_or_changed_calendar_is_an_error_not_days_off(self):
        for html in [
            "<html>ログイン</html>", '<div id="syukin_month"></div>',
            '<div id="syukin_month"><div class="girlitem_waku"></div></div>',
            calendar_html([("明日", "休み")]),
            calendar_html([("13/40", "休み")]),
            calendar_html([("10/19", "休み"), ("10/19", "10:00 - 18:00")]),
        ]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                weekly.parse_month_calendar(html, self.dates)

    def test_cached_data_rendering_escaping_and_atomic_persistence(self):
        people = weekly.rank_people(datasets(tokai_rows=[
            tokai(1, name="<script>name</script>", shop_name="<店>")
        ]))
        schedules = {profile(1): {
            "fetched_at": "2026-10-18T19:00:00+09:00", "error": "<error>",
            "days": weekly.daily_states(self.dates, "error"),
        }}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            cache = weekly.load_cache(path)
            cache["windows"][self.dates[0]] = schedules
            weekly.save_cache(cache, path)
            loaded = weekly.load_cache(path)
            self.assertEqual(loaded["windows"][self.dates[0]], schedules)
            self.assertFalse(path.with_name(".cache.json.tmp").exists())
            path.write_text('{"version": 2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                weekly.load_cache(path)
        page = BeautifulSoup(weekly.render_html(people, self.dates, schedules, {}), "html.parser")
        self.assertEqual(len(page.select(".m-cal li")), 7)
        self.assertFalse(page.select("td script"))
        self.assertIn("<script>name</script>", page.get_text())
        self.assertIn("2026-10-18T19:00:00+09:00", page.get_text())
        self.assertEqual(page.select_one('[role="alert"]').get_text(), "<error>")
        self.assertIn("暫定", page.get_text())

    def test_unfetched_and_unmatched_are_not_unpublished_or_errors(self):
        person = weekly.rank_people(datasets(tokai_rows=[tokai(1)]))[0]
        self.assertEqual(weekly.person_schedule(person, self.dates, {})["days"][0]["state"],
                         "not_fetched")
        person["profile_url"] = ""
        self.assertEqual(weekly.person_schedule(person, self.dates, {})["days"][0]["state"],
                         "no_profile")
        with self.assertRaises(ValueError):
            weekly.person_schedule(person, self.dates, {"": {"days": []}})

    def test_legacy_cache_migration_preserves_exact_dates_not_shifted(self):
        result = {
            "fetched_at": "2026-10-18T19:00:00+09:00", "error": "",
            "days": weekly.daily_states(self.dates, "working", "10:00 - 18:00"),
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            legacy = {"version": 1, "weeks": {self.dates[0]: {profile(1): result}}}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = weekly.load_cache(path)
            self.assertEqual(migrated, {"version": 2, "windows": legacy["weeks"]})
            # Offline loading must not rewrite the legacy file.
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)
            sunday = weekly.schedule_dates(datetime(2026, 10, 18, tzinfo=weekly.JST))
            self.assertEqual(migrated["windows"].get(sunday[0], {}), {})
            weekly.save_cache(migrated, path)
            self.assertEqual(weekly.load_cache(path), migrated)
        person = weekly.rank_people(datasets(tokai_rows=[tokai(1)]))[0]
        self.assertEqual(weekly.person_schedule(person, self.dates, {profile(1): result}), result)

    def test_cli_offline_has_no_collection_and_ignores_other_windows(self):
        people = weekly.rank_people(datasets(tokai_rows=[tokai(1)]))
        with (
            patch.object(weekly, "load_ranking", return_value=(people, {})),
            patch.object(weekly, "load_cache", return_value={"version": 2, "windows": {
                "1999-01-01": {"unexpected": "must not be displayed"}
            }}),
            patch.object(weekly, "collect_schedules", new_callable=AsyncMock) as collect,
            patch.object(weekly, "write_html") as write,
            patch.object(weekly, "save_cache") as save,
        ):
            self.assertEqual(weekly.main([]), 0)
            collect.assert_not_called()
            save.assert_not_called()
            self.assertNotIn("must not be displayed", write.call_args.args[1])
            self.assertIn("未取得", write.call_args.args[1])
        with self.assertRaises(SystemExit):
            weekly.main(["--include-a"])

    def test_cli_collect_persists_errors_and_a_follows_s(self):
        people = weekly.rank_people(datasets(tokai_rows=[tokai(i, i) for i in range(20)]))
        dates = weekly.schedule_dates()
        failed_url = people[0]["profile_url"]
        results = {failed_url: {
            "profile_url": failed_url, "fetched_at": "2026-10-18T19:00:00+09:00",
            "days": weekly.daily_states(dates, "error"), "error": "HTTP 403",
        }}
        unselected = {"retained": "unselected profile"}
        cache = {"version": 2, "windows": {
            "1999-01-01": {"past": "retained"},
            dates[0]: {"unselected": unselected},
        }}
        with (
            patch.object(weekly, "load_ranking", return_value=(people, {})),
            patch.object(weekly, "load_cache", return_value=cache),
            patch.object(weekly, "collect_schedules", new_callable=AsyncMock,
                         return_value=results) as collect,
            patch.object(weekly, "write_html") as write,
            patch.object(weekly, "save_cache") as save,
        ):
            self.assertEqual(weekly.main(["--collect", "--include-a"]), 1)
            self.assertEqual([p["tier"] for p in collect.call_args.args[0]],
                             ["S", "S", "A", "A", "A"])
            self.assertEqual(save.call_args.args[0]["windows"][dates[0]],
                             {**results, "unselected": unselected})
            self.assertEqual(save.call_args.args[0]["windows"]["1999-01-01"], {"past": "retained"})
            self.assertIn("HTTP 403", write.call_args.args[1])

    def test_redirect_to_login_is_explicit_error(self):
        page = Mock(
            url="https://www.cityheaven.net/login/",
            goto=AsyncMock(return_value=Mock(status=200)),
            close=AsyncMock(),
            content=AsyncMock(),
        )
        context = Mock(new_page=AsyncMock(return_value=page))
        result = asyncio.run(weekly.fetch_person(context, {"profile_url": profile(1)}, self.dates))
        self.assertIn("転送", result["error"])
        self.assertTrue(all(day["state"] == "error" for day in result["days"]))
        page.content.assert_not_called()
        page.close.assert_awaited_once()

    def test_report_navigation_template_preserves_weekend_content(self):
        from cityheaven.cityheaven_month_schedule import render_index_html

        html = render_index_html({"10/24(土)": []})
        self.assertIn('href="weekly_schedule.html"', html)
        self.assertIn("土日出勤カレンダー", html)
        self.assertIn("10/24(土)", html)


class BrowserFixtureTests(unittest.TestCase):
    def test_calendar_filters_layout_and_zero_states_preserve_ranking(self):
        async def run():
            now = datetime(2026, 10, 21, 0, 30, tzinfo=weekly.JST)
            dates = weekly.schedule_dates(now)
            people = weekly.rank_people(datasets(tokai_rows=[
                tokai(i, 100 - i) for i in range(1, 8)
            ]))
            original = copy.deepcopy(people)
            schedules = {}
            for i, person in enumerate(people):
                days = weekly.daily_states(dates, "unpublished")
                if i < 3:
                    day_index = [0, 3, 1][i]  # today (Wed), Saturday, Thursday
                    days[day_index] = {"date": dates[day_index], "state": "working",
                                       "text": "10:00 - 18:00"}
                elif i == 3:
                    days = weekly.daily_states(dates, "off")
                elif i == 4:
                    days = weekly.daily_states(dates, "undecided", "未定 10:00 - 18:00")
                elif i == 5:
                    days = weekly.daily_states(dates, "error")
                else:
                    continue  # not_fetched
                schedules[person["profile_url"]] = {
                    "fetched_at": now.isoformat(), "days": days,
                    "error": "HTTP 403" if i == 5 else "",
                }
            html = weekly.render_html(people, dates, schedules, {}, now)
            self.assertEqual(people, original)
            soup = BeautifulSoup(html, "html.parser")
            self.assertIn("2026-10-21（水）", soup.get_text())
            self.assertIn("2026-10-27（火）", soup.get_text())
            self.assertEqual(len(soup.select(".person-row:not([hidden])")), 3)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    for width in [390, 820, 1440]:
                        context = await browser.new_context(
                            viewport={"width": width, "height": 900},
                            timezone_id="Pacific/Honolulu",
                        )
                        page = await context.new_page()
                        await page.clock.install(time=now)
                        async def route_handler(route):
                            filename = route.request.url.rsplit("/", 1)[-1]
                            if filename in {"weekly_schedule.js", "weekly_schedule.css", "common.js", "common.css"}:
                                await route.fulfill(path=ROOT / "docs" / filename)
                            else:
                                await route.fulfill(content_type="text/html; charset=utf-8", body=html)
                        await context.route("**/*", route_handler)
                        await page.goto("https://report.invalid/weekly_schedule.html")
                        rows = page.locator(".person-row")
                        before = await rows.locator(".rank-info").all_text_contents()
                        self.assertEqual(await page.locator(".person-row:visible").count(), 3)
                        await page.select_option("#scheduleFilter", "today")
                        self.assertEqual(await page.locator(".person-row:visible").count(), 1)
                        self.assertFalse(await page.locator("#staleNotice").is_visible())
                        await page.select_option("#scheduleFilter", "weekend")
                        self.assertEqual(await page.locator(".person-row:visible").count(), 1)
                        visible_days = await page.locator(".person-row:visible .m-cal li:not([hidden])").count()
                        self.assertEqual(visible_days, 2)
                        if width < 1100:
                            self.assertEqual(await page.locator(".person-row:visible td[data-day]:visible").count(), 0)
                            self.assertTrue(await page.locator(".person-row:visible .m-cal").is_visible())
                            self.assertTrue(await page.evaluate(
                                "document.documentElement.scrollWidth <= window.innerWidth"
                            ))
                        else:
                            self.assertEqual(await page.locator(".person-row:visible td[data-day]:visible").count(), 2)
                        await page.select_option("#scheduleFilter", "all")
                        self.assertEqual(await page.locator(".person-row:visible").count(), 7)
                        self.assertEqual(await rows.locator(".rank-info").all_text_contents(), before)
                        # A saved snapshot viewed after its date range must not call day 0 'today'.
                        await page.clock.set_fixed_time(datetime(2026, 11, 1, tzinfo=weekly.JST))
                        await page.select_option("#scheduleFilter", "today")
                        self.assertEqual(await page.locator(".person-row:visible").count(), 0)
                        self.assertTrue(await page.locator("#noResults").is_visible())
                        self.assertTrue(await page.locator("#staleNotice").is_visible())
                        self.assertIn("全表示", await page.locator("#noResults").inner_text())
                        # With only unknown/off/error data, the default must show an explicit empty state.
                        await page.evaluate("""() => {
                            document.querySelectorAll('.person-row').forEach(row => row.dataset.working = '');
                        }""")
                        await page.select_option("#scheduleFilter", "working")
                        self.assertEqual(await page.locator(".person-row:visible").count(), 0)
                        self.assertTrue(await page.locator("#noResults").is_visible())
                        await context.close()
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_real_browser_dom_collection_and_failure_states_without_network(self):
        async def run():
            dates = weekly.schedule_dates(datetime(2026, 10, 19, tzinfo=weekly.JST))
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context = await browser.new_context()
                    context.set_default_timeout(200)
                    async def route_handler(route):
                        if "girlid-2/" in route.request.url:
                            await route.fulfill(status=403, body="Forbidden")
                        elif "girlid-3/" in route.request.url:
                            await route.fulfill(body="<html>Missing calendar</html>")
                        else:
                            await route.fulfill(
                                content_type="text/html; charset=utf-8",
                                body=calendar_html([("10/19(月)", "10:00 - 18:00"),
                                                    ("10/20(火)", "休み")]),
                            )
                    await context.route("**/*", route_handler)
                    for number in [1, 2, 3]:
                        result = await weekly.fetch_person(
                            context, {"profile_url": profile(number)}, dates
                        )
                        self.assertEqual(len(result["days"]), 7)
                        if number == 1:
                            self.assertEqual(result["days"][0]["state"], "working")
                            self.assertEqual(result["days"][1]["state"], "off")
                            self.assertEqual(result["days"][6]["state"], "unpublished")
                            self.assertEqual(result["error"], "")
                        else:
                            self.assertTrue(result["error"])
                            self.assertTrue(all(day["state"] == "error" for day in result["days"]))
                        self.assertTrue(result["fetched_at"].endswith("+09:00"))
                        self.assertEqual(context.pages, [])
                    await context.close()
                finally:
                    await browser.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
