import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_store import read_jsonl, write_jsonl_atomic
from ns_senmon.match_cityheaven import (
    girl_keys,
    label_keys,
    load_candidates,
    parse_cityheaven_url,
    shop_variants,
)
from okinilove.scrape_okinilove import find_unavailable_profile_urls

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
OKINILOVE_JSONL = DATA_DIR / "okinilove_reviews.jsonl"
ENRICH_CACHE_PATH = DATA_DIR / "okinilove_cityheaven_enrich_cache.json"
# 補完できなかった (店名, 姫名) は、この期間が過ぎるまで再検索しない
ENRICH_RETRY = timedelta(days=7)
# 店舗ページ検索による補完は1回の実行でこの秒数を超えたら次回へ回す
ENRICH_TIME_BUDGET_SECONDS = 20
ENRICH_BATCH_SIZE = 5
OKINILOVE_COLUMNS = [
    "shop_name",
    "girl_name",
    "girl_url",
    "report_url",
    "evaluations",
    "report_text",
    "cityheaven_url",
]


def extract_okinilove_data():
    return read_jsonl(OKINILOVE_JSONL, columns=OKINILOVE_COLUMNS)


def _read_all_columns():
    """face/service/age などの詳細列も保持したまま読み込む。"""
    data = read_jsonl(OKINILOVE_JSONL)
    missing = set(OKINILOVE_COLUMNS).difference(data.columns)
    if missing:
        raise ValueError(f"JSON Linesデータに必要な列がありません: {', '.join(sorted(missing))}")
    extra = [column for column in data.columns if column not in OKINILOVE_COLUMNS]
    return data.loc[:, OKINILOVE_COLUMNS + extra]


def _blank(series):
    return series.isna() | (series.astype("string").str.strip() == "")


def remove_unavailable_profile_rows(data, now=None, protected=None):
    """404/410 のプロフィールURLを持つ行だけデータから除外する。

    protected が True の行（カレンダーに掲載中の人）は掲載終了確認の対象にしない。
    同じ girl_url を持つ行が1件でも保護されていれば、そのURLは確認しない。
    """
    if "girl_url" not in data.columns or data.empty:
        return data.copy(), 0

    start = time.perf_counter()
    if protected is None:
        protected = pd.Series(False, index=data.index)
    girl_urls = data["girl_url"].astype("string")
    protected_urls = set(girl_urls[protected].dropna())
    all_urls = set(girl_urls.dropna())
    target_urls = all_urls - protected_urls
    print(
        f"掲載終了確認: 対象URL {len(target_urls)}件"
        f"（カレンダー掲載のため除外 {len(all_urls & protected_urls)}件 / 全{len(all_urls)}件）"
    )
    unavailable_urls = set(find_unavailable_profile_urls(sorted(target_urls), now=now)) - protected_urls
    print(f"[timing] 掲載終了確認: {time.perf_counter() - start:.2f}s")
    if not unavailable_urls:
        return data.copy(), 0

    remove = girl_urls.isin(unavailable_urls).fillna(False).astype(bool) & ~protected
    removed = int(remove.sum())
    return data.loc[~remove].copy().reset_index(drop=True), removed


def _shop_slug_index(data, calendar_data, normalize_url):
    """店名の表記ゆれ -> CityHeaven 店舗コード の対応を既知データから作る（通信なし）。"""
    index = {}

    def add(shop_name, url):
        info = parse_cityheaven_url(f"{normalize_url(url) or ''}/")
        if not info:
            return
        for variant in shop_variants(shop_name):
            index.setdefault(variant, set()).add(info["slug"])

    for shop_name, url in zip(data["shop_name"], data["cityheaven_url"]):
        if isinstance(shop_name, str) and isinstance(url, str):
            add(shop_name, url)
    for candidate in load_candidates():
        add(candidate["shop"], candidate["url"])
    if "店名" in calendar_data.columns:
        for shop_text, key in zip(calendar_data["店名"], calendar_data["url_key"]):
            if isinstance(shop_text, str) and isinstance(key, str):
                for line in shop_text.replace("<br>", "\n").splitlines():
                    add(line, key)
    return index


def _slugs_for(shop_name, slug_index):
    slugs = set()
    for variant in shop_variants(shop_name):
        slugs |= slug_index.get(variant, set())
    return slugs


def _calendar_people(calendar_data):
    """カレンダー掲載者を 店舗コード -> [(url_key, 名前キー)] にまとめる。"""
    people = {}
    if "名前" not in calendar_data.columns:
        return people
    seen = set()
    for name, key in zip(calendar_data["名前"], calendar_data["url_key"]):
        if not isinstance(key, str) or key in seen:
            continue
        info = parse_cityheaven_url(f"{key}/")
        if not info:
            continue
        seen.add(key)
        people.setdefault(info["slug"], []).append((key, label_keys(name)))
    return people


def fill_cityheaven_urls_from_calendar(data, calendar_data, normalize_url, slug_index=None):
    """cityheaven_url が空の行を、カレンダー掲載者（同じ店舗・同じ名前）と照合して埋める。"""
    missing = _blank(data["cityheaven_url"])
    if not missing.any() or "url_key" not in calendar_data.columns:
        return data, 0
    people = _calendar_people(calendar_data)
    if not people:
        return data, 0
    slug_index = slug_index if slug_index is not None else _shop_slug_index(data, calendar_data, normalize_url)

    resolved = {}
    for shop_name, girl_name in data.loc[missing, ["shop_name", "girl_name"]].drop_duplicates().itertuples(index=False):
        wanted = girl_keys(girl_name if isinstance(girl_name, str) else "")
        if not wanted:
            continue
        hits = {
            key
            for slug in _slugs_for(shop_name, slug_index)
            for key, keys in people.get(slug, [])
            if keys & wanted
        }
        if len(hits) == 1:
            resolved[(shop_name, girl_name)] = f"{next(iter(hits))}/"

    if not resolved:
        return data, 0
    data = data.copy()
    pairs = list(zip(data["shop_name"], data["girl_name"]))
    filled = 0
    for position, (pair, is_missing) in enumerate(zip(pairs, missing)):
        if is_missing and pair in resolved:
            data.iat[position, data.columns.get_loc("cityheaven_url")] = resolved[pair]
            filled += 1
    return data, filled


def _load_enrich_cache():
    if not ENRICH_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(ENRICH_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_enrich_cache(cache):
    ENRICH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = ENRICH_CACHE_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary_path.replace(ENRICH_CACHE_PATH)


def _enrich_cache_key(shop_name, girl_name):
    return f"{shop_name}\t{girl_name}"


def enrich_missing_cityheaven_urls(
    data,
    enrich_with_cityheaven_urls,
    *,
    calendar_slugs=frozenset(),
    slug_index=None,
    now=None,
    time_budget=ENRICH_TIME_BUDGET_SECONDS,
):
    """空の cityheaven_url を店舗ページ検索で補完する（件数・時間を絞り、失敗は期限付きで再試行）。

    カレンダーに載っている店舗の人を優先し、次に店舗コードが分かっている人、最後にその他を試す。
    """
    missing = _blank(data["cityheaven_url"])
    if not missing.any():
        return data, 0
    now = now or datetime.now(timezone.utc)
    cache = _load_enrich_cache()
    slug_index = slug_index or {}

    resolved = {}
    pending = []
    for shop_name, girl_name in data.loc[missing, ["shop_name", "girl_name"]].drop_duplicates().itertuples(index=False):
        if not isinstance(shop_name, str) or not isinstance(girl_name, str):
            continue
        entry = cache.get(_enrich_cache_key(shop_name, girl_name))
        if entry and entry.get("url"):
            resolved[(shop_name, girl_name)] = entry["url"]
            continue
        if entry:
            try:
                if now - datetime.fromisoformat(entry["checked_at"]) < ENRICH_RETRY:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        slugs = _slugs_for(shop_name, slug_index)
        priority = 0 if slugs & calendar_slugs else 1 if slugs else 2
        pending.append((priority, shop_name, girl_name))
    pending.sort(key=lambda item: item[0])

    start = time.perf_counter()
    attempted = 0
    try:
        for offset in range(0, len(pending), ENRICH_BATCH_SIZE):
            if time.perf_counter() - start >= time_budget:
                break
            batch = pending[offset:offset + ENRICH_BATCH_SIZE]
            frame = pd.DataFrame(
                {
                    "shop_name": [shop for _, shop, _ in batch],
                    "girl_name": [girl for _, _, girl in batch],
                    "cityheaven_url": [None] * len(batch),
                }
            )
            frame = enrich_with_cityheaven_urls(frame)
            checked_at = now.isoformat()
            for shop_name, girl_name, url in zip(frame["shop_name"], frame["girl_name"], frame["cityheaven_url"]):
                url = url if isinstance(url, str) and url.strip() else None
                cache[_enrich_cache_key(shop_name, girl_name)] = {"checked_at": checked_at, "url": url}
                if url:
                    resolved[(shop_name, girl_name)] = url
            attempted += len(batch)
    finally:
        _save_enrich_cache(cache)
    print(
        f"cityheaven_url 再補完(検索): 試行{attempted}件 / 待機{len(pending)}件"
        f"（残り{len(pending) - attempted}件は次回以降）, {time.perf_counter() - start:.2f}s"
    )

    if not resolved:
        return data, 0
    data = data.copy()
    column = data.columns.get_loc("cityheaven_url")
    filled = 0
    for position, (pair, is_missing) in enumerate(zip(zip(data["shop_name"], data["girl_name"]), missing)):
        if is_missing and pair in resolved:
            data.iat[position, column] = resolved[pair]
            filled += 1
    return data, filled


def merge_okinilove_data(
    calendar_data,
    *,
    normalize_url,
    enrich_with_cityheaven_urls,
):
    """既存のオキニラブデータを突合し、出勤データへ右結合する（サイトの再取得はしない）。"""
    if not OKINILOVE_JSONL.exists():
        raise FileNotFoundError(f"オキニラブ既存JSONLがありません: {OKINILOVE_JSONL}")

    existing = _read_all_columns()
    changed = False

    start = time.perf_counter()
    slug_index = _shop_slug_index(existing, calendar_data, normalize_url)
    existing, calendar_filled = fill_cityheaven_urls_from_calendar(
        existing, calendar_data, normalize_url, slug_index=slug_index
    )
    print(
        f"cityheaven_url 再補完(カレンダー照合): {calendar_filled}件, "
        f"{time.perf_counter() - start:.2f}s"
    )
    changed |= bool(calendar_filled)

    # カレンダーに表示されている人は掲載終了していないため、掲載終了確認の対象外にする
    calendar_keys = set(calendar_data["url_key"].dropna()) if "url_key" in calendar_data.columns else set()
    on_calendar = existing["cityheaven_url"].apply(normalize_url).isin(calendar_keys)
    existing, removed_count = remove_unavailable_profile_rows(existing, protected=on_calendar)
    if removed_count:
        print(f"公開終了プロフィールのデータを削除: {removed_count}件")
        changed = True

    calendar_slugs = frozenset(
        info["slug"]
        for info in (parse_cityheaven_url(f"{key}/") for key in calendar_keys)
        if info
    )
    existing, searched_filled = enrich_missing_cityheaven_urls(
        existing,
        enrich_with_cityheaven_urls,
        calendar_slugs=calendar_slugs,
        slug_index=slug_index,
    )
    if searched_filled:
        print(f"cityheaven_url 再補完(検索): {searched_filled}行")
        changed = True

    if changed:
        write_jsonl_atomic(existing, OKINILOVE_JSONL)

    existing = existing.loc[:, OKINILOVE_COLUMNS].copy()
    existing["url_key"] = existing["cityheaven_url"].apply(normalize_url)
    return pd.merge(
        existing,
        calendar_data,
        how="right",
        on="url_key",
        suffixes=("_okinilove", "_merged"),
    )
