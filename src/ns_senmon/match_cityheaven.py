"""NS専門レポートに、他レポートの既知データから cityheaven_url を付与する。

突合の優先順位:
  1. manual : data/ns_senmon_manual_map.json（記事URL -> cityheaven_url）
  2. exact  : 店名(正規化)と姫名(正規化)が一致
  3. shop   : 手順2で判明した店舗slug（または ns_senmon_shop_map.json）内で姫名が一致
  4. search : 店舗が特定できる場合、店舗ページの姫名検索(girllist/?sgnm=)で一致（掲載なしは not_listed）
  5. name   : 岐阜県の候補の中で姫名が一意に一致（確定せず cityheaven_url_candidate に保存）
一致しなかったものは data/ns_senmon_unmatched.json に出力する。
"""
import json
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
NS_PATH = DATA_DIR / "ns_senmon_reports.jsonl"
SOURCE_PATHS = [
    DATA_DIR / "tokai_fuzoku_report_history.jsonl",
    DATA_DIR / "tokai_fuzoku_reports.jsonl",
    DATA_DIR / "okinilove_reviews.jsonl",
]
MANUAL_MAP_PATH = DATA_DIR / "ns_senmon_manual_map.json"
SHOP_MAP_PATH = DATA_DIR / "ns_senmon_shop_map.json"
UNMATCHED_PATH = DATA_DIR / "ns_senmon_unmatched.json"
SEARCH_CACHE_PATH = DATA_DIR / "ns_senmon_shop_search_cache.json"
SEARCH_RETRY_DAYS = 30
SEARCH_INTERVAL_SECONDS = 0.5
SEARCH_HEADERS = {"User-Agent": "Mozilla/5.0", "Cookie": "mensesCookie=1"}
GIRL_LINK_RE = re.compile(r'href="(/[^"]*?girlid-\d+/)"[^>]*>(.*?)</a>', re.S)
IMG_ALT_RE = re.compile(r'<img[^>]*\balt="([^"]+)"')


def anchor_label(body):
    alt = IMG_ALT_RE.search(body)
    if alt:
        return alt.group(1).strip()
    return re.sub(r"<[^>]+>|\s+", " ", body).strip()
SHOP_LINK_RE = re.compile(r'href="(/gifu/A\d+/A\d+/[a-z0-9_]+/)"')


def normalize_slug(value):
    # URL形式・コード形式のどちらで書かれていても店舗コードにそろえる
    value = (value or "").strip().replace("https://www.cityheaven.net/", "")
    return value.strip("/")

CITYHEAVEN_RE = re.compile(r"https?://www\.cityheaven\.net/([^/]+)/([^/]+)/([^/]+)/([^/]+)/([^/?#]+)")
GIRL_ID_RE = re.compile(r"girlid-(\d+)", re.IGNORECASE)
BRACKET_RE = re.compile(r"[（(]([^）)]*)[）)]")


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def clean(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s･・·\-_'´`’]", "", text)


def shop_variants(name):
    # 括弧の中身・外側・全体を別候補にして、表記ゆれを吸収する
    name = unicodedata.normalize("NFKC", name or "")
    variants = {clean(name), clean(BRACKET_RE.sub("", name))}
    variants.update(clean(inner) for inner in BRACKET_RE.findall(name))
    return {v for v in variants if len(v) >= 2}


def girl_key(name):
    # 「なみ🔐」のような絵文字・記号の装飾を除く
    text = "".join(
        ch for ch in unicodedata.normalize("NFKC", name or "")
        if unicodedata.category(ch)[0] not in "SC" and ch != "\ufe0f"
    )
    text = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)
    return clean(re.sub(r"(さん|ちゃん)$", "", text))


LABEL_SPLIT_RE = re.compile(r"[「【(（\[~〜♡❤♥☆★…\s]")


def label_keys(label):
    """一覧ラベルから照合キーを作る。「東出 あさひ」は空白を詰めた形でも照合する。"""
    label = unicodedata.normalize("NFKC", label or "").strip()
    head = LABEL_SPLIT_RE.split(label.replace("\u3000", " "), 1)[0]
    tokens = label.replace("\u3000", " ").split()
    keys = girl_keys(head)
    if len(tokens) >= 2:
        keys |= girl_keys(tokens[0] + tokens[1])
    return keys


def label_name(label):
    """CityHeavenの一覧ラベル（例: 'ハル【高級】' 'なみ(マーベラスコース)'）から名前部分を取り出す。"""
    label = unicodedata.normalize("NFKC", label or "").strip()
    return LABEL_SPLIT_RE.split(label, 1)[0]


def parse_cityheaven_url(url):
    m = CITYHEAVEN_RE.match(url or "")
    if not m:
        return None
    pref, area, sub, slug, girl = m.groups()
    return {"pref": pref, "slug": f"{pref}/{area}/{sub}/{slug}", "girl": girl}


def canonical_url(url):
    return (url or "").split("?")[0]


def girl_keys(name):
    # 「葵（あおい）」「Berry/ベリー」のような表記を、各部分でも照合できるようにする
    name = unicodedata.normalize("NFKC", name or "")
    parts = [name, BRACKET_RE.sub("", name)] + BRACKET_RE.findall(name)
    parts += [p for part in list(parts) for p in re.split(r"[/／]", part)]
    return {girl_key(p) for p in parts if girl_key(p)}


def load_candidates():
    rows = []
    for path in SOURCE_PATHS:
        for row in read_jsonl(path):
            row["_name"] = row.get("girl_name") or row.get("name") or ""
            row["_source"] = path.name
            rows.append(row)

    # 店名 -> CityHeaven店舗コード（CityHeaven URLを持つ行から学習）
    shop_to_slug = defaultdict(set)
    for row in rows:
        info = parse_cityheaven_url(canonical_url(row.get("cityheaven_url") or row.get("girl_url") or ""))
        if info:
            for v in shop_variants(row.get("shop_name")):
                shop_to_slug[v].add(info["slug"])

    candidates = []
    seen = set()
    for row in rows:
        url = canonical_url(row.get("cityheaven_url") or row.get("girl_url") or "")
        info = parse_cityheaven_url(url)
        if info and not GIRL_ID_RE.search(info["girl"]):
            continue
        if not info:
            # 店公式サイトのgirl_idはCityHeavenのgirlIdと一致するため、URLを組み立てる
            m = re.search(r"girl_id=(\d+)", row.get("girl_url") or "")
            slugs = set()
            for v in shop_variants(row.get("shop_name")):
                slugs |= shop_to_slug.get(v, set())
            if not m or len(slugs) != 1:
                continue
            slug = next(iter(slugs))
            url = f"https://www.cityheaven.net/{slug}/girlid-{m.group(1)}/"
            info = parse_cityheaven_url(url)
        key = (url, row["_name"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "url": url,
            "slug": info["slug"],
            "pref": info["pref"],
            "shop": row.get("shop_name") or "",
            "girls": girl_keys(row["_name"]),
            "source": row["_source"],
        })
    return candidates

def name_queries(name):
    name = unicodedata.normalize("NFKC", name or "")
    parts = [BRACKET_RE.sub("", name)] + BRACKET_RE.findall(name)
    queries = []
    for part in parts:
        for leaf in re.split(r"[/／]", part):
            leaf = leaf.strip()
            if leaf and leaf not in queries:
                queries.append(leaf)
    # 店側は「東出 あさひ」のように姓名を空白で区切るため、漢字部分・かな部分でも検索する
    for leaf in list(queries):
        for piece in re.findall(r"[一-龥々]+|[ぁ-んァ-ヶー]+", leaf):
            if len(piece) >= 2 and piece != leaf and piece not in queries:
                queries.append(piece)
    return queries


class ShopSearcher:
    """CityHeavenの店舗ページで姫名を検索する。結果は日付付きでキャッシュする。"""

    def resolve_shop(self, shop_name):
        """店舗一覧(gifu/shop-list)の店名検索で、店舗コードを一意に特定する。"""
        names = {}
        for query in sorted(shop_variants(shop_name) | {BRACKET_RE.sub("", shop_name or "").strip()}, key=len, reverse=True):
            if len(query) < 2:
                continue
            key = f"shop|{query}"
            entry = self.cache.get(key)
            if entry and (entry["shops"] or self._fresh({"girls": entry["shops"], "checked_at": entry["checked_at"]})):
                shops = entry["shops"]
            else:
                try:
                    response = self.session.get(
                        "https://www.cityheaven.net/gifu/shop-list/",
                        params={"shopname_search": query, "keyword_search": "all-list", "pref": "gifu"},
                        timeout=30,
                    )
                    response.raise_for_status()
                    response.encoding = "utf-8"
                except requests.RequestException as exc:
                    print(f"店舗検索失敗: {query}: {exc}")
                    continue
                self.requests_made += 1
                time.sleep(SEARCH_INTERVAL_SECONDS)
                shops = []
                for slug in dict.fromkeys(SHOP_LINK_RE.findall(response.text)):
                    m = re.search(re.escape(slug) + r'"[^>]*>(.{0,300}?)</a>', response.text, re.S)
                    label = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""
                    shops.append({"slug": slug.strip("/"), "label": label})
                self.cache[key] = {"checked_at": datetime.now(timezone.utc).isoformat(), "shops": shops}
            for shop in shops:
                if shop_variants(shop["label"]) & shop_variants(shop_name):
                    names[shop["slug"]] = shop["label"]
            if len(names) == 1:
                return next(iter(names))
        return None

    def __init__(self, cache):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers.update(SEARCH_HEADERS)
        self.requests_made = 0

    def _fresh(self, entry):
        if any(not g.get("label") for g in entry["girls"]):
            return False
        if entry["girls"]:
            return True
        checked = datetime.fromisoformat(entry["checked_at"])
        return datetime.now(timezone.utc) - checked < timedelta(days=SEARCH_RETRY_DAYS)

    def search(self, slug, query):
        key = f"{slug}|{query}"
        entry = self.cache.get(key)
        if entry and self._fresh(entry):
            return entry["girls"]
        url = f"https://www.cityheaven.net/{slug}/girllist/?sgnm={quote(query)}"
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            response.encoding = "utf-8"
        except requests.RequestException as exc:
            print(f"検索失敗（次回再試行）: {slug} {query}: {exc}")
            return None
        self.requests_made += 1
        time.sleep(SEARCH_INTERVAL_SECONDS)
        girls = []
        for href, body in GIRL_LINK_RE.findall(response.text):
            label = anchor_label(body)
            full = f"https://www.cityheaven.net{href}"
            if all(g["url"] != full for g in girls):
                girls.append({"url": full, "label": label})
        self.cache[key] = {"checked_at": datetime.now(timezone.utc).isoformat(), "girls": girls}
        return girls

    def find(self, slug, name):
        """(url, 'found') / ('', 'not_listed') / (None, 'error')"""
        wanted = girl_keys(name)
        errored = False
        for query in name_queries(name):
            girls = self.search(slug, query)
            if girls is None:
                errored = True
                continue
            hits = {
                g["url"] for g in girls
                if label_keys(g["label"]) & wanted
            }
            if len(hits) == 1:
                return next(iter(hits)), "found"
        return (None, "error") if errored else ("", "not_listed")

def match_reports(rows, candidates, manual_map, shop_map, searcher=None):
    by_girl = defaultdict(list)
    shop_slugs = defaultdict(set)  # 正規化店名 -> slug群（他レポートの店名から）
    for c in candidates:
        for g in c["girls"]:
            by_girl[g].append(c)
        for v in shop_variants(c["shop"]):
            shop_slugs[v].add(c["slug"])

    def lookup(keys):
        return [c for k in keys for c in by_girl.get(k, [])]

    def slugs_for(ns_shop):
        found = set()
        for v in shop_variants(ns_shop):
            found |= shop_slugs.get(v, set())
        return found

    learned = defaultdict(set)  # NS店名 -> slug群（exact一致から学習）
    results = {}

    for row in rows:
        url = row["url"]
        if url in manual_map:
            results[url] = (manual_map[url], "manual")
            continue
        girl = girl_keys(row.get("name"))
        shop_slug_set = slugs_for(row.get("shop_name"))
        hits = {c["url"] for c in lookup(girl) if c["slug"] in shop_slug_set}
        if len(hits) == 1:
            hit = next(iter(hits))
            results[url] = (hit, "exact")
            learned[row.get("shop_name")].add(parse_cityheaven_url(hit)["slug"])

    for row in rows:
        url = row["url"]
        if url in results:
            continue
        girl = girl_keys(row.get("name"))
        slugs = set(learned.get(row.get("shop_name"), set())) | slugs_for(row.get("shop_name"))
        mapped = shop_map.get(row.get("shop_name"), [])
        mapped = {mapped} if isinstance(mapped, str) else set(mapped)
        slugs |= {normalize_slug(s) for s in mapped if s}
        if not slugs and searcher:
            resolved = searcher.resolve_shop(row.get("shop_name"))
            if resolved:
                slugs.add(resolved)
        hits = {c["url"] for c in lookup(girl) if c["slug"] in slugs}
        if len(hits) == 1:
            results[url] = (next(iter(hits)), "shop")
            continue
        if slugs and searcher:
            for slug in sorted(slugs):
                found, state = searcher.find(slug, row.get("name"))
                if state == "found":
                    results[url] = (found, "search")
                    break
            else:
                if state == "not_listed":
                    results[url] = ("", "not_listed")
            if url in results:
                continue
        owned = {s for shop, ss in learned.items() if shop != row.get("shop_name") for s in ss}
        gifu = {c["url"] for c in lookup(girl) if c["pref"] == "gifu" and c["slug"] not in owned}
        if len(gifu) == 1:
            results[url] = (next(iter(gifu)), "name")

    return results


def resolve_urls(pairs):
    """(店名, 姫名) の組から CityHeaven URL を割り出す。見つからなければ None。

    既知データ(各レポートの店名・姫名・URL)との突合 → 店舗ページの姫名検索 の順で試し、Yahoo等の外部検索は使わない。
    """
    pairs = list(dict.fromkeys(pairs))
    rows = [{"url": f"pair-{i}", "shop_name": shop, "name": girl} for i, (shop, girl) in enumerate(pairs)]
    cache = read_json(SEARCH_CACHE_PATH)
    searcher = ShopSearcher(cache)
    try:
        results = match_reports(rows, load_candidates(), {}, read_json(SHOP_MAP_PATH), searcher)
    finally:
        SEARCH_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    resolved = {}
    for row, pair in zip(rows, pairs):
        url, status = results.get(row["url"], ("", "none"))
        if status == "name":  # 店舗が裏付けられない姓名のみの一致は採用しない
            url = ""
        resolved[pair] = url or None
        print(f"[突合:{status}] {pair[0]} {pair[1]} {url or '-'}")
    return resolved


def main():
    rows = read_jsonl(NS_PATH)
    if not rows:
        print("NS専門のデータがありません")
        return

    candidates = load_candidates()
    manual_map = read_json(MANUAL_MAP_PATH)
    shop_map = read_json(SHOP_MAP_PATH)
    cache = read_json(SEARCH_CACHE_PATH)
    searcher = ShopSearcher(cache)
    try:
        results = match_reports(rows, candidates, manual_map, shop_map, searcher)
    finally:
        SEARCH_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = defaultdict(int)
    unmatched = []
    for row in rows:
        hit = results.get(row["url"])
        row.pop("cityheaven_url_candidate", None)
        if hit and hit[1] == "name":
            # 店舗不一致の可能性があるため確定せず、候補としてのみ保存する
            row["cityheaven_url"], row["match_confidence"] = "", "name"
            row["cityheaven_url_candidate"] = hit[0]
            unmatched.append({"status": "name", "shop_name": row.get("shop_name"), "name": row.get("name"),
                              "url": row["url"], "candidate": hit[0]})
        elif hit:
            row["cityheaven_url"], row["match_confidence"] = hit
            if hit[1] == "not_listed":
                unmatched.append({"status": "not_listed", "shop_name": row.get("shop_name"), "name": row.get("name"),
                                  "url": row["url"], "note": "店舗ページに掲載なし（掲載終了の可能性）"})
        else:
            row["cityheaven_url"], row["match_confidence"] = "", "none"
            unmatched.append({"status": "none", "shop_name": row.get("shop_name"), "name": row.get("name"),
                              "url": row["url"], "note": "店の CityHeaven 店舗コードが不明（shop_map に追加すると検索可能）"})
        counts[row["match_confidence"]] += 1

    tmp = NS_PATH.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(NS_PATH)
    order = {"none": 0, "name": 1, "not_listed": 2}
    unmatched.sort(key=lambda u: (order[u["status"]], u["shop_name"] or "", u["name"] or ""))
    UNMATCHED_PATH.write_text(json.dumps(unmatched, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"候補: {len(candidates)}件 / NS専門: {len(rows)}件")
    for key in ("manual", "exact", "shop", "search", "not_listed", "name", "none"):
        print(f"  {key}: {counts.get(key, 0)}")
    print(f"店舗検索リクエスト: {searcher.requests_made}件")
    print(f"未突合を保存: {UNMATCHED_PATH}")


if __name__ == "__main__":
    main()
