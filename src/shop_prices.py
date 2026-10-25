"""Shared, conservative CityHeaven shop-course prices (never report-body analysis)."""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from data_store import read_jsonl_records

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cityheaven_shop_prices.json"
NORMAL_TITLE = re.compile(r"^(?:料金システム|基本料金|通常料金|通常コース|基本コース)(?:\s|$)")
EXCLUDED = re.compile(
    r"初回|初めて|新規|限定|割|クーポン|延長|特殊|オプション|ポイント|"
    r"イベント|キャンペーン|セット|パック|VIP|プレミア|指名|交通|出張|入会|"
    r"早朝|深夜|モーニング|ネット予約割|ネット予約限定", re.I,
)
STATUSES = {
    "ok": "確認済み", "unknown": "判別不可", "not_fetched": "未取得",
    "error": "取得失敗", "restricted": "アクセス制限", "redirect": "転送",
    "not_found": "HTTP 404/410",
}


def shop_url(value):
    """Only accept the known CityHeaven shop path, never infer from shop names."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"cityheaven.net", "www.cityheaven.net"}
            or parsed.port not in {None, 80, 443}
            or parsed.username is not None or parsed.password is not None
        ):
            return ""
    except ValueError:
        return ""
    match = re.fullmatch(
        r"(/[a-z]+/A\d+/A\d+/[^/]+)(?:/(?:girlid-\d+(?:/[^?]*)?|system/?)?)?/?",
        parsed.path,
    )
    return f"https://www.cityheaven.net{match[1]}/" if match else ""


def system_url(value):
    root = shop_url(value)
    return root + "system/" if root else ""


def empty_result(status, reason):
    return {
        "status": status, "reason": reason, "candidates": [], "selected": None,
        "tax": "unknown", "additional_fees": [],
    }


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_system_html(markup):
    """Support the observed feesmart table schema; unknown layouts stay unknown."""
    soup = BeautifulSoup(markup, "html.parser")
    root = soup.select_one("#shopcontent")
    if root is None or not root.select("table.feesmart"):
        return empty_result("unknown", "対応する #shopcontent table.feesmart がありません（画像のみ・構造変更等）")
    tabs = {
        tab.get("id", "")[4:]: text(tab)
        for tab in soup.select(".fee_system_menu li[id^=menu]")
    }
    result = empty_result("unknown", "明示的な通常料金表がありません")
    normal_tables = []
    for table_index, table in enumerate(root.select("table.feesmart")):
        title = text(table.select_one("label.feetitle, td.feetitle"))
        section = table.find_parent(class_="osusume")
        section_title = tabs.get(section.get("id", ""), "") if section else ""
        normal = bool(NORMAL_TITLE.match(title)) and not EXCLUDED.search(title + " " + section_title)
        conditions = " / ".join(
            text(cell) for cell in table.select("td[colspan]") if not cell.select_one(".feetitle")
            and "feetitle" not in cell.get("class", [])
        )
        if normal and conditions:
            result["additional_fees"].append({"section": title, "label": "料金表の条件（加算なし）", "text": conditions})
        excluded_context = EXCLUDED.search(conditions)
        table_candidates = []
        invalid = False
        for row in table.select("tr"):
            name = row.select_one("td.feename")
            price = row.select_one("td.feename2")
            if name is None or price is None:
                continue
            label, raw_price = text(name), text(price)
            if re.search(r"指名|姫予約|入会|交通|出張|別途|追加|ホテル|ルーム代|必須|サービス料", title + " " + label):
                result["additional_fees"].append({
                    "section": title, "label": label, "text": raw_price,
                })
            duration = re.fullmatch(r"(\d{1,3})\s*分(?:\s*コース)?", label)
            amount = re.fullmatch(r"([1-9]\d*(?:,\d{3})*)\s*円(?:\s*[（(]税込(?:み)?[）)])?", raw_price)
            eligible = bool(normal and not excluded_context and duration and amount)
            candidate = {
                "course_name": title, "label": label, "price_text": raw_price,
                "conditions": conditions, "section": section_title,
                "minutes": int(duration[1]) if duration else None,
                "yen": int(amount[1].replace(",", "")) if amount else None,
                "eligible": eligible,
                "reason": "明示された通常料金表の時間・基本料金" if eligible else "通常コースと確認できない条件・表記",
            }
            # Retain time/price evidence, not unrelated option descriptions.
            if duration or normal:
                result["candidates"].append(candidate)
            if normal and not EXCLUDED.search(label):
                if not eligible or not 1 <= int(duration[1]) <= 300 or int(amount[1].replace(",", "")) <= 0:
                    invalid = True
                else:
                    table_candidates.append(candidate)
        if normal:
            normal_tables.append((table_index, table_candidates, invalid))
    if len(normal_tables) > 1:
        result["reason"] = "通常料金表が複数あり料金体系を一意に判別できません"
        return result
    if not normal_tables:
        return result
    _, candidates, invalid = normal_tables[0]
    if invalid or not candidates:
        result["reason"] = "通常料金表に範囲料金・条件付き料金・未対応表記があります"
        return result
    pairs = {(item["minutes"], item["yen"]) for item in candidates}
    minutes = min((item[0] for item in pairs), key=lambda value: (abs(value - 90), -value))
    if len({yen for duration, yen in pairs if duration == minutes}) != 1:
        result["reason"] = "採用時間の通常料金が複数あり一意に判別できません"
        return result
    selected = next(item for item in candidates if item["minutes"] == minutes)
    # Monetary rounding is display-only; filters use the unrounded conversion.
    result["selected"] = {
        **selected, "yen_per_60": selected["yen"] * 60 / minutes,
        "selection_reason": "通常90分を優先。なければ90分に最も近い時間、同距離は長い時間",
    }
    table_text = text(root)
    inclusive = bool(re.search(r"税込|消費税.*含", table_text))
    exclusive = bool(re.search(r"税別|税抜", table_text))
    result["tax"] = "ambiguous" if inclusive and exclusive else "included" if inclusive else "excluded" if exclusive else "unknown"
    if result["tax"] == "ambiguous":
        result["selected"] = None
        result["reason"] = "税込・税別の表記が混在し通常料金の税条件を特定できません"
        return result
    result["status"] = "ok"
    result["reason"] = result["selected"]["selection_reason"]
    return result


def load_cache(path=CACHE):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "shops": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("shops"), dict):
        raise ValueError(f"店舗料金キャッシュの形式が不正です: {path}")
    return payload


def save_cache(payload, path=CACHE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def fetch_price(session, url):
    """One request per shop; never follow access/age-check redirects."""
    try:
        with session.get(url, timeout=20, allow_redirects=False, stream=True) as response:
            if response.status_code in {404, 410}:
                return empty_result("not_found", f"HTTP {response.status_code}")
            if response.status_code in {401, 403, 429}:
                return empty_result("restricted", f"HTTP {response.status_code}（回避しません）")
            if 300 <= response.status_code < 400:
                return empty_result("redirect", f"HTTP {response.status_code}（転送先へ移動しません）")
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    return empty_result("error", "HTMLが2MiBの取得上限を超えました")
            if "html" not in response.headers.get("Content-Type", "").lower():
                return empty_result("unknown", "HTML以外の応答です")
            return parse_system_html(bytes(body).decode("utf-8", errors="replace"))
    except requests.RequestException as error:
        return empty_result("error", str(error))


def known_shops():
    urls = set()
    for filename in ("ns_senmon_reports.jsonl", "okinilove_reviews.jsonl", "tokai_fuzoku_reports.jsonl"):
        for record in read_jsonl_records(ROOT / "data" / filename):
            for key in ("cityheaven_url", "girl_url"):
                normalized = shop_url(record.get(key))
                if normalized:
                    urls.add(normalized)
    return sorted(urls)


PRICE_CONTROLS = """<section id="shopPriceFilters" class="shop-price-filters" aria-label="店舗料金">
<strong>店舗の通常料金（60分換算・参考）</strong>
<div class="shop-price-inputs">
<label>下限（円）<input id="shopPriceMin" type="number" min="0" step="any" inputmode="numeric" placeholder="指定なし"></label>
<label>上限（円）<input id="shopPriceMax" type="number" min="0" step="any" inputmode="numeric" placeholder="指定なし"></label>
<label>料金の確認状態<select id="shopPriceMode"><option value="">絞り込みなし</option><option value="unknown">料金不明のみ</option></select></label>
</div>
<p>通常90分を優先し、なければ最も近い時間（同距離は長い方）を60分換算。実際の60分コース料金・確定支払総額ではありません。
指名料・交通費・別途必須料金は加算していません。税込・税別・税不明は根拠欄を確認してください。未取得を安価とは扱いません。</p>
<p id="shopPriceError" role="alert" hidden></p>
</section>"""


def price_info(entry, url):
    status = entry.get("status", "not_fetched")
    selected = entry.get("selected") if status == "ok" else None
    if selected:
        heading = f'参考 {selected["yen_per_60"]:,.0f}円 / 60分換算'
        evidence = (
            f'通常 {selected["minutes"]}分 {selected["yen"]:,}円'
            f'（{selected["course_name"]} / {selected["label"]}）'
        )
    else:
        heading = f'料金不明（{STATUSES.get(status, "判別不可")}）'
        evidence = entry.get("reason") or "通常コース料金は未取得です"
    tax = {
        "included": "税込表記", "excluded": "税別表記（税を加算していません）",
        "unknown": "税の扱い不明", "ambiguous": "税表記が混在",
    }.get(entry.get("tax"), "税の扱い不明")
    fees = entry.get("additional_fees", [])
    fee_text = " / ".join(f'{fee["label"]}: {fee["text"]}' for fee in fees) if fees else "別途費用の詳細は未確認"
    origin = (
        f'<a href="{escape(url + "system/", quote=True)}" target="_blank" rel="noopener">料金表の出典</a>'
        if url else "CityHeaven店舗URL未突合"
    )
    checked = entry.get("checked_at") or "未取得"
    saved = (
        f' / 保存HTMLからの取り込み（保存日時 {entry.get("observed_at", "不明")}、最新性は未確認）'
        if entry.get("source_kind") == "saved_html" else ""
    )
    return (
        f'<div class="shop-price" data-details-scroll="native"><span class="shop-price-value">{escape(heading)}</span>'
        f'<details class="shop-price-evidence"><summary>料金の根拠・別途費用</summary>'
        f'<p>{escape(evidence)}</p><p>{escape(tax)}</p>'
        f'<p>条件: {escape((selected or {}).get("conditions") or "詳細な適用条件は出典を確認してください")}</p>'
        f'<p>基本料金に加算しない別情報: {escape(fee_text)}</p>'
        f'<p>{escape(entry.get("reason", ""))}</p>'
        f'<p>確認日時: {escape(checked + saved)}</p>{origin}</details></div>'
    )


def attach_prices(markup, cache=None):
    """Decorate listing rows using profile URLs only; native filters own visibility."""
    cache = cache if cache is not None else load_cache()
    soup = BeautifulSoup(markup, "lxml")
    if soup.select_one("#shopPriceFilters"):
        return str(soup)
    rows = soup.select("#people > .person") or soup.select(".person-row, tr[data-report], details.report-entry")
    if not rows:
        return markup
    for row in rows:
        identity = (
            row.find("summary", recursive=False) if row.name == "details"
            else row.find("h2", recursive=False) if row.name == "section" else row
        )
        urls = {shop_url(link.get("href")) for link in identity.select("a[href]")}
        urls.discard("")
        url = next(iter(urls)) if len(urls) == 1 else ""
        entry = cache["shops"].get(url, {})
        selected = entry.get("selected") if entry.get("status") == "ok" else None
        row["data-shop-price"] = str(selected["yen_per_60"]) if selected else ""
        row["data-shop-price-status"] = entry.get("status", "not_fetched")
        info = BeautifulSoup(price_info(entry, url), "html.parser").div
        if row.name == "section":
            identity.insert_after(info)
        elif row.name == "details":
            # Do not nest a details disclosure in a summary.
            brief = soup.new_tag("span", attrs={"class": "shop-price-brief"})
            brief.string = info.select_one(".shop-price-value").get_text()
            identity.append(brief)
            row.select_one(".person-details").insert(0, info)
        else:
            cell = row.select_one(".shop-cell") or row.find("td", recursive=False)
            anchor = cell.select_one(".shop-info")
            if anchor:
                anchor.insert_after(info)
            else:
                cell.append(info)
    controls = BeautifulSoup(PRICE_CONTROLS, "html.parser").section
    target = soup.select_one(".filters, .filter-group, #toolbar")
    if target:
        target.insert_before(controls)
    else:
        soup.select_one("h1").insert_after(controls)
    for tag, attrs in (
        ("link", {"rel": "stylesheet", "href": "shop_prices.css"}),
        ("script", {"src": "shop_prices.js"}),
    ):
        soup.head.insert(0, soup.new_tag(tag, attrs=attrs))
    return str(soup)


def refresh_saved_prices(markup, template, filter_marker):
    """Retain published people/schedules/bodies; replace only price UI and its filter."""
    soup = BeautifulSoup(markup, "lxml")
    source = BeautifulSoup(template, "html.parser")
    old_script = next((
        node for node in soup.find_all("script")
        if filter_marker in (node.string or "")
    ), None)
    new_script = next((
        node for node in source.find_all("script")
        if filter_marker in (node.string or "")
    ), None)
    if old_script is None or new_script is None:
        raise ValueError("保存HTMLとテンプレートの人物フィルタを確認できません")
    if not soup.select(".person-row, tr[data-report], details.report-entry, #people > .person"):
        raise ValueError("保存HTMLに人物行がありません")
    old_script.replace_with(new_script)
    for node in soup.select(
        "#shopPriceFilters, .shop-price, .shop-price-brief, "
        "script[src='shop_prices.js'], link[href='shop_prices.css']"
    ):
        node.decompose()
    return attach_prices(str(soup))


def main(argv=None):
    parser = argparse.ArgumentParser(description="CityHeaven店舗料金の限定取得／保存HTML取り込み（通常生成は通信なし）")
    parser.add_argument("--shop-url", action="append", default=[], help="取得対象店舗URL。複数指定可")
    parser.add_argument("--html-file", type=Path, help="1店舗の保存HTMLを取り込む（通信なし）")
    parser.add_argument("--all", action="store_true", help="3レポート保存URLの全店舗を明示的に取得")
    parser.add_argument("--limit", type=int, help="全店舗取得の上限")
    parser.add_argument("--refresh", action="store_true", help="既存の取得結果も更新（既定は未取得店舗だけ）")
    parser.add_argument("--summary", action="store_true", help="保存料金の数値分布と状態を表示")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit は1以上です")
    if args.all and (args.shop_url or args.html_file):
        parser.error("--all と店舗指定・HTML取り込みは併用できません")
    urls = known_shops() if args.all else sorted({shop_url(url) for url in args.shop_url})
    if "" in urls or (args.html_file and len(urls) != 1):
        parser.error("有効なCityHeaven店舗URLが必要です（HTML取り込みは1店舗）")
    if not urls and not args.summary:
        parser.error("--shop-url / --all / --summary のいずれかが必要です")
    cache = load_cache()
    pending = [url for url in urls if args.refresh or url not in cache["shops"]]
    if args.limit:
        pending = pending[:args.limit]
    failures = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"})
        for index, url in enumerate(pending):
            if args.html_file:
                markup = args.html_file.read_text(encoding="utf-8")
                soup = BeautifulSoup(markup, "html.parser")
                canonical = soup.select_one('link[rel="canonical"]')
                if canonical is None or system_url(canonical.get("href")) != url + "system/":
                    parser.error("保存HTMLのcanonicalが指定店舗と一致しません")
                entry = parse_system_html(markup)
                entry["source_kind"] = "saved_html"
                entry["observed_at"] = datetime.fromtimestamp(
                    args.html_file.stat().st_mtime, timezone.utc,
                ).isoformat(timespec="seconds")
            else:
                if index:
                    time.sleep(1)
                entry = fetch_price(session, url + "system/")
                entry["source_kind"] = "http"
            entry.update({
                "source_url": url + "system/",
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            cache["shops"][url] = entry
            save_cache(cache)
            failures += entry["status"] != "ok"
            print(f"{url}: {entry['status']} / {entry['reason']}")
    values = sorted(
        entry["selected"]["yen_per_60"] for entry in cache["shops"].values()
        if entry.get("status") == "ok" and entry.get("selected")
    )
    states = {status: sum(entry["status"] == status for entry in cache["shops"].values()) for status in STATUSES}
    print(f"保存店舗 {len(cache['shops'])} / 状態 {states}")
    print(f"保存サンプルの60分換算分布（全店舗の代表性なし）: {values} 円")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
