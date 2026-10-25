"""Shared, conservative CityHeaven shop-course prices (never report-body analysis)."""
import argparse
import copy
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from data_store import read_jsonl_records

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cityheaven_shop_prices.json"
DECISION_VERSION = 4
PROMOTION = re.compile(
    r"初回|初めて|新規|新人|限定|割引|割り|クーポン|イベント|キャンペーン|"
    r"最安|お得|特価|口コミ|割(?!増)|会員様|会員価格|会員料金|フリー|おまかせ|お任せ|お試し|"
    r"モーニング|早朝|深夜割|期間|事前予約|予約必須|予約のみ|予約専用|OFF", re.I,
)
FEE_ONLY = re.compile(
    r"延長|オプション|ポイント|利用可能|指名料|入場料|入泉料|入浴料|交通費|出張料|"
    r"サービス料|入会金|ホテル代|広告", re.I,
)
TIME_CONTEXT = re.compile(r"\d{1,2}\s*[:：時]\s*\d{0,2}\s*(?:分)?\s*[~〜～\-]|平日|土日|祝日|ランク|クラス")
STATUSES = {
    "ok": "掲載コース目安あり", "unknown": "判定保留", "not_fetched": "未取得",
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


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def fee_only_heading(value):
    value = normalized(value).lstrip("■●★☆【[ ")
    return bool(
        re.match(r"(?:ご|追加)?(?:指名料|入場料|交通費|出張料|サービス料|入会金|ホテル代)", value, re.I)
        or ("延長" in value and not re.search(r"延長(?:込|含|無料)", value))
        or re.search(r"入浴料|入泉料", value)
        or re.search(r"ポイント|利用可能|広告", value)
        or ("オプション" in value and not re.search(r"オプション(?:込|付|含|無料)", value))
    )


def context_role(title, section, conditions):
    heading = normalized(title + " " + section)
    note = normalized(conditions)
    # Only fee-only headings exclude a table. Course names and eligibility conditions remain evidence.
    if fee_only_heading(title) or fee_only_heading(section):
        return "extra", "延長・追加料金・部分料金のみの表は換算対象外"
    if PROMOTION.search(heading):
        return "promotion", "限定・会員等の掲載条件を保持したコース候補"
    if TIME_CONTEXT.search(heading + " " + note) or PROMOTION.search(note):
        return "conditional", "時間帯・利用条件を保持した掲載コース候補"
    if not normalized(title):
        return "unlabeled", "見出しなしの表はDOMの表境界と複数行の確認が必要"
    return "course", "コース名称で除外せず時間と単一掲載金額の対応を確認"


def classify_candidate(candidate):
    """Re-read raw row evidence, never trust a previously inferred amount."""
    title, section = candidate["course_name"], candidate["section"]
    label = candidate["label"]
    clean_label = normalized(label)
    recommendation = r"(?:当店)?(?:超)?(?:オススメ|おすすめ|人気)(?:コース)?"
    clean_label = re.sub(rf"^[★☆!【\[\s]*{recommendation}[★☆!】\]\s]*", "", clean_label)
    clean_label = re.sub(rf"\s*[★☆!【\[\s]*{recommendation}[★☆!】\]\s]*$", "", clean_label)
    duration = None if re.search(r"[~〜～]|以上|以下|から|応相談", clean_label) else re.fullmatch(
        r"[^\d]*?(\d{1,3})\s*(?:分|minutes?|mins?)\b[^\d]*", clean_label, re.I,
    )
    # Japanese course suffixes do not form a Unicode word boundary after 分.
    if duration is None and not re.search(r"[~〜～]|以上|以下|から|応相談", clean_label):
        duration = re.fullmatch(r"[^\d]*?(\d{1,3})\s*分[^\d]*", clean_label, re.I)
    price_text = normalized(candidate["price_text"])
    amount = re.fullmatch(
        r"(?:[¥￥]\s*([1-9]\d*(?:,\d{3})*)\s*-?|([1-9]\d*(?:,\d{3})*)\s*(?:円|yen))"
        r"(?:\s*\((税込(?:み)?|\d+\s*pt)\))?"
        r"(?:\s*\+\s*(出張費|交通費|指名料|サービス料))?",
        price_text, re.I,
    )
    annotations = []
    if amount:
        if amount[3]:
            annotations.append(amount[3])
        if amount[4]:
            annotations.append("別途 " + amount[4] + "（金額未確認）")
    points_unclear = bool(amount and amount[3] and re.fullmatch(r"\d+\s*pt", amount[3], re.I))
    role, context_reason = context_role(title, section, candidate["conditions"])
    row_extra = bool(
        FEE_ONLY.search(normalized(label))
        or re.match(r"^\+\s*[1-9][\d,]*\s*(?:円|yen)", price_text, re.I)
    )
    row_information = (
        normalized(label) == price_text and not re.search(r"円|yen|[¥￥]", price_text, re.I)
        and bool(re.fullmatch(
            r"※\s*\d+\s*(?:分|mins?|minutes?)以上.*ご用意しております[。.!！]?", price_text, re.I,
        ))
    )
    eligible = bool(
        role != "extra" and not row_extra
        and duration and 1 <= int(duration[1]) <= 300 and amount and not points_unclear
    )
    return {
        **candidate,
        "minutes": int(duration[1]) if duration else None,
        "yen": int((amount[1] or amount[2]).replace(",", "")) if amount else None,
        "eligible": eligible,
        "amount_annotations": annotations,
        "context_role": role, "row_role": (
            "extra" if row_extra else "information" if row_information else "course"
        ),
        "reason": context_reason if eligible else (
            "延長・追加料金のみの行は換算対象外" if row_extra else
            context_reason if role == "extra" else
            "金額併記のptが支払条件か特定できません" if points_unclear else
            "時間と単一掲載金額の対応を特定できません（範囲・内訳・複数金額等）"
        ),
    }


def select_published_course(result, tables):
    viable = []
    unresolved = []
    decisions = []
    for table in tables:
        first = table[0]
        role, reason = context_role(first["course_name"], first["section"], first["conditions"])
        courses = [item for item in table if item["row_role"] == "course"]
        has_duration = any(item["minutes"] is not None or re.search(
            r"\d+\s*(?:分|mins?|minutes?)", normalized(item["label"]), re.I,
        ) for item in courses)
        if role == "extra":
            decision = "excluded"
            if role == "extra" and re.search(r"入浴料|入泉料", normalized(first["course_name"] + " " + first["label"])):
                reason = "入浴・入泉料のみの部分料金で独立した掲載コース料金を特定できません"
        elif not courses or not has_duration:
            decision, reason = "excluded", "時間コースではない追加情報の表"
            if any(re.search(r"入浴料|入泉料", normalized(item["label"])) for item in table):
                reason = "入浴・入泉料のみの部分料金で独立した掲載コース料金を特定できません"
        elif any(not item["eligible"] for item in courses):
            decision, reason = "hold", "表内に未対応の時間・範囲・複数金額・条件行があります"
            unresolved.append(table)
        elif role == "unlabeled" and len({item["minutes"] for item in courses}) < 2:
            decision, reason = "hold", "見出しなしの単一行では独立したコースか確認できません"
            unresolved.append(table)
        elif role == "unlabeled" and first.get("table_index") is None:
            decision, reason = "hold", "保存候補だけでは見出しなし表のDOM境界を復元できません"
            unresolved.append(table)
        elif first.get("table_index") is None and len({item["minutes"] for item in courses}) != len(courses):
            decision, reason = "hold", "旧保存候補の重複行から元の表境界を復元できません"
            unresolved.append(table)
        else:
            decision = "eligible"
            viable.append(courses)
        decisions.append({
            "table_index": first.get("table_index"), "course_name": first["course_name"],
            "section": first["section"], "role": role, "decision": decision, "reason": reason,
        })
    result["table_decisions"] = decisions
    if not viable or unresolved:
        result["reason"] = (
            "未解決の時間・金額を含む候補表があり掲載コースの代表値を特定できません"
            if viable and unresolved else
            "候補あり・判定保留: " + " / ".join(dict.fromkeys(
                item["reason"] for item in decisions if item["decision"] == "hold"
            )) if any(item["decision"] == "hold" for item in decisions) else
            "入浴・入泉料等の部分料金のみで、独立した掲載コース料金を特定できません"
            if any("部分料金" in item["reason"] for item in decisions) else
            "候補は延長・追加料金等のみで掲載コース料金を特定できません"
            if result["candidates"] else "料金候補を抽出できません（画像・未対応構造等、料金不存在ではありません）"
        )
        return result
    candidates = [candidate for table in viable for candidate in table]
    minutes = min((item["minutes"] for item in candidates), key=lambda value: (abs(value - 90), -value))
    if result["tax"] == "ambiguous":
        result["reason"] = "税込・税別の表記が混在し採用料金の税条件を特定できません"
        return result
    compared = [item for item in candidates if item["minutes"] == minutes]
    highest = max(item["yen"] for item in compared)
    alternatives = [item for item in compared if item["yen"] == highest]
    selected = alternatives[0]
    result["selected"] = {
        **selected, "yen_per_60": selected["yen"] * 60 / minutes,
        "matching_courses": alternatives,
        "compared_courses": compared,
        "selection_reason": "掲載コースから90分に最も近い時間、同距離は長い時間。同時間の掲載料金は高い方。名称や利用条件では除外しない",
    }
    result["status"] = "ok"
    result["reason"] = result["selected"]["selection_reason"]
    result["decision_version"] = DECISION_VERSION
    return result


def reprocess_cached_entry(entry):
    """Re-evaluate saved candidates without claiming a new page fetch."""
    if entry.get("evidence_conflict"):
        raise ValueError("矛盾する保存証拠は自動再判定できません")
    if entry.get("status") not in {"ok", "unknown"}:
        raise ValueError("再解析は抽出済みの ok / unknown の店舗に限ります")
    result = copy.deepcopy(entry)
    result.update(status="unknown", selected=None, candidates=[])
    groups = {}
    for raw in entry["candidates"]:
        candidate = classify_candidate(raw)
        result["candidates"].append(candidate)
        key = (candidate.get("table_index"), candidate["course_name"],
               candidate["section"], candidate["conditions"])
        groups.setdefault(key, []).append(candidate)
    for group in groups.values():
        conditions = group[0]["conditions"]
        fee = {"section": group[0]["course_name"], "label": "料金表の条件（加算なし）", "text": conditions}
        if conditions and fee not in result["additional_fees"]:
            result["additional_fees"].append(fee)
        for candidate in group:
            remember_candidate_fee(result, candidate)
    result["reprocessed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["decision_version"] = DECISION_VERSION
    return select_published_course(result, list(groups.values()))


def remember_candidate_fee(result, candidate):
    if (
        candidate["row_role"] == "extra" or any(
            note not in {"税込", "税込み"} for note in candidate["amount_annotations"]
        )
        or re.search(r"指名|姫予約|入会|交通|出張|別途|追加|ホテル|ルーム代|必須|サービス料",
                     candidate["course_name"] + " " + candidate["label"])
    ):
        fee = {"section": candidate["course_name"], "label": candidate["label"], "text": candidate["price_text"]}
        if fee not in result["additional_fees"]:
            result["additional_fees"].append(fee)


def parse_system_html(markup):
    """Support the observed feesmart table schema; unknown layouts stay unknown."""
    soup = BeautifulSoup(markup, "html.parser")
    root = soup.select_one("#shopcontent")
    diagnostics = {
        "root_found": root is not None,
        "table_count": len(root.select("table.feesmart")) if root else 0,
        "image_count": len(root.select("img")) if root else 0,
    }
    if root is None or not root.select("table.feesmart"):
        return dict(
            empty_result("unknown", "対応する #shopcontent table.feesmart がありません（画像のみ・構造変更等）"),
            extraction_diagnostics=diagnostics,
        )
    tabs = {
        tab.get("id", "")[4:]: text(tab)
        for tab in soup.select(".fee_system_menu li[id^=menu]")
    }
    result = empty_result("unknown", "時間と掲載金額が対応するコース候補がありません")
    result["extraction_diagnostics"] = diagnostics
    result["unpaired_tables"] = []
    tables = []
    for table_index, table in enumerate(root.select("table.feesmart")):
        title = text(table.select_one("label.feetitle, td.feetitle"))
        section = table.find_parent(class_="osusume")
        section_title = tabs.get(section.get("id", ""), "") if section else ""
        conditions = " / ".join(
            text(cell) for cell in table.select("td[colspan]") if not cell.select_one(".feetitle")
            and "feetitle" not in cell.get("class", [])
        )
        if conditions:
            result["additional_fees"].append({"section": title, "label": "料金表の条件（加算なし）", "text": conditions})
        table_candidates = []
        for row in table.select("tr"):
            name = row.select_one("td.feename")
            price = row.select_one("td.feename2")
            if name is None or price is None:
                continue
            label, raw_price = text(name), text(price)
            candidate = classify_candidate({
                "course_name": title, "label": label, "price_text": raw_price,
                "conditions": conditions, "section": section_title,
                "table_index": table_index,
            })
            remember_candidate_fee(result, candidate)
            result["candidates"].append(candidate)
            table_candidates.append(candidate)
        if table_candidates:
            tables.append(table_candidates)
        elif title or conditions:
            result["unpaired_tables"].append({
                "table_index": table_index, "course_name": title,
                "section": section_title, "conditions": conditions,
            })
    table_text = text(root)
    inclusive = bool(re.search(r"税込|消費税.*含", table_text))
    exclusive = bool(re.search(r"税別|税抜", table_text))
    result["tax"] = "ambiguous" if inclusive and exclusive else "included" if inclusive else "excluded" if exclusive else "unknown"
    result["decision_version"] = DECISION_VERSION
    return select_published_course(result, tables)


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


def merge_saved_evidence(current, incoming, source_digest):
    """Prefer a newer observation, but retain competing originals and their provenance."""
    merged = copy.deepcopy(current)
    conflicts = []
    for url, entry in incoming["shops"].items():
        if shop_url(url) != url:
            raise ValueError(f"保存店舗URLが正規形式ではありません: {url}")
        old = merged["shops"].get(url)
        if old == entry:
            continue
        imported = copy.deepcopy(entry)
        imported["imported_evidence_sha256"] = source_digest
        if old is None:
            merged["shops"][url] = imported
            continue
        old_time = datetime.fromisoformat(old["checked_at"])
        new_time = datetime.fromisoformat(entry["checked_at"])
        if old_time.tzinfo is None or new_time.tzinfo is None:
            raise ValueError(f"取得日時にタイムゾーンがないため証拠の新旧を判断できません: {url}")
        observation_keys = ("candidates", "tax", "additional_fees", "source_url")
        old_raw = [{key: c.get(key) for key in ("course_name", "section", "conditions", "label", "price_text", "table_index")} for c in old.get("candidates", [])]
        new_raw = [{key: c.get(key) for key in ("course_name", "section", "conditions", "label", "price_text", "table_index")} for c in entry.get("candidates", [])]
        same_evidence = old_raw == new_raw and all(old.get(key) == entry.get(key) for key in observation_keys if key != "candidates")
        if same_evidence:
            if new_time > old_time:
                imported["previous_evidence"] = old
                merged["shops"][url] = imported
            continue
        conflicts.append(url)
        if new_time > old_time:
            imported["previous_evidence"] = old
            imported["evidence_merge_reason"] = "新しい取得日時を採用し、異なる旧証拠を保持"
            merged["shops"][url] = imported
        else:
            kept = merged["shops"][url]
            alternatives = kept.setdefault("alternate_evidence", [])
            if imported not in alternatives:
                alternatives.append(imported)
            kept["evidence_merge_reason"] = "新しいローカル証拠を保持。異なる取り込み証拠も保管"
            if new_time == old_time:
                kept.update(
                    status="unknown", selected=None, evidence_conflict=True,
                    reason="同一取得日時で保存証拠が矛盾するため自動選択しません",
                )
    return merged, conflicts


def reprocess_snapshot(path=CACHE, source=None):
    """Re-evaluate an explicit snapshot once, refusing to overwrite a concurrent writer."""
    path = Path(path)
    before = path.read_bytes() if path.exists() else None
    payload = json.loads(before) if before is not None else {"version": 1, "shops": {}}
    if payload.get("version") != 1 or not isinstance(payload.get("shops"), dict):
        raise ValueError("料金キャッシュの形式が不正です")
    conflicts = []
    source_bytes = None
    if source is not None:
        source = Path(source)
        source_bytes = source.read_bytes()
        incoming = json.loads(source_bytes)
        if incoming.get("version") != 1 or not isinstance(incoming.get("shops"), dict):
            raise ValueError("取り込み元料金キャッシュの形式が不正です")
        payload, conflicts = merge_saved_evidence(
            payload, incoming, hashlib.sha256(source_bytes).hexdigest(),
        )
    transitions = {}
    for url, entry in list(payload["shops"].items()):
        if entry.get("evidence_conflict") or entry.get("status") not in {"ok", "unknown"}:
            continue
        result = reprocess_cached_entry(entry)
        payload["shops"][url] = result
        key = entry["status"] + " -> " + result["status"]
        transitions[key] = transitions.get(key, 0) + 1
    if (path.read_bytes() if path.exists() else None) != before:
        raise RuntimeError("料金キャッシュが処理中に更新されたため保存を中止しました。収集完了後に再実行してください")
    if source is not None and source.read_bytes() != source_bytes:
        raise RuntimeError("取り込み元が処理中に更新されたため保存を中止しました")
    payload["analysis_inputs"] = {
        "destination_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
        "imported_sha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes is not None else None,
    }
    save_cache(payload, path)
    return payload, transitions, conflicts


def fetch_price(session, url):
    """One request per shop; never follow access/age-check redirects."""
    try:
        with session.get(url, timeout=20, allow_redirects=False, stream=True) as response:
            if response.status_code in {404, 410}:
                return empty_result("not_found", f"HTTP {response.status_code}")
            if response.status_code in {401, 403, 429}:
                return empty_result("restricted", f"HTTP {response.status_code}（回避しません）")
            if 300 <= response.status_code < 400:
                return redirect_result(response.status_code, response.headers.get("Location", ""))
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


def redirect_result(status, location):
    location = location if isinstance(location, str) else ""
    age_gate = bool(re.match(
        r"^https?://(?:img\.|www\.)?cityheaven\.net/cs/nenrei/", location, re.I,
    ))
    return {
        **empty_result("redirect", f"HTTP {status}（{'年齢確認への転送・' if age_gate else ''}転送先へ移動しません）"),
        "http_status": status, "redirect_location": location,
        "redirect_kind": "age_verification" if age_gate else "unverified",
    }


def known_shops():
    urls = set()
    for filename in ("ns_senmon_reports.jsonl", "okinilove_reviews.jsonl", "tokai_fuzoku_reports.jsonl"):
        for record in read_jsonl_records(ROOT / "data" / filename):
            for key in ("cityheaven_url", "girl_url"):
                normalized = shop_url(record.get(key))
                if normalized:
                    urls.add(normalized)
    return sorted(urls)


def unresolved_categories(entry):
    """Evidence categories may overlap; absence of extraction is not absence of prices."""
    if entry.get("status") == "ok":
        return []
    if entry.get("status") == "redirect":
        return ["年齢確認への転送" if entry.get("redirect_kind") == "age_verification" else "転送先未確認"]
    if entry.get("status") != "unknown":
        return [STATUSES.get(entry.get("status"), "未取得")]
    if not entry.get("candidates"):
        return ["候補未抽出・原DOM不足"]
    categories = set()
    for decision in entry.get("table_decisions", []):
        if "部分料金" in decision["reason"]:
            categories.add("入浴・入泉料のみ・掲載コース不足")
    for candidate in entry["candidates"]:
        if candidate.get("context_role") == "extra":
            continue
        if candidate.get("row_role") != "course" or candidate.get("eligible"):
            continue
        value = normalized(candidate["price_text"])
        amount_count = len(re.findall(r"(?:[¥￥]\s*\d[\d,]*|\d[\d,]*\s*(?:円|yen))", value, re.I))
        if amount_count > 1:
            categories.add("複数金額・内訳・税込併記")
        elif re.search(r"[~〜～]|以上|から", value):
            categories.add("範囲・下限料金")
        elif re.search(r"\d+\s*pt", value, re.I):
            categories.add("ポイントと金額の関係不明")
        else:
            categories.add("時間・単一金額表記未対応")
    if entry.get("tax") == "ambiguous":
        categories.add("税条件の対応不明")
    if "DOM" in entry.get("reason", "") or "表境界" in entry.get("reason", ""):
        categories.add("旧候補の表境界不足")
    return sorted(categories) or ["延長・追加料金等のみ・独立コース未確認"]


PRICE_CONTROLS = """<section id="shopPriceFilters" class="shop-price-filters" aria-label="店舗料金">
<strong>掲載コースの60分換算目安（利用条件あり）</strong>
<div class="shop-price-inputs">
<label>価格帯<select id="shopPriceBand">
<option value="">すべて</option>
<option value="0-10000">1万円未満</option>
<option value="10000-15000">1万〜1.5万円未満</option>
<option value="15000-20000">1.5万〜2万円未満</option>
<option value="20000-25000">2万〜2.5万円未満</option>
<option value="25000-30000">2.5万〜3万円未満</option>
<option value="30000">3万円以上</option>
<option value="unknown">料金不明</option>
</select></label>
</div>
<p>掲載コースから90分に最も近い時間（同距離は長い方）を60分換算。同時間の掲載料金は高い方を採用します。通常・VIP・限定・会員等の名称や条件では除外しません。
選択コースの利用条件つきの目安で、実際の60分コース料金・確定支払総額ではありません。延長・追加料金のみの行は対象外。
指名料・交通費・別途必須料金は加算していません。税・追加費用・時間帯等は根拠欄を確認してください。未取得を安価とは扱いません。</p>
</section>"""


def price_info(entry, url):
    status = entry.get("status", "not_fetched")
    selected = entry.get("selected") if status == "ok" else None
    if selected:
        heading = f'条件つき目安 {selected["yen_per_60"]:,.0f}円 / 60分換算'
        evidence = (
            f'掲載コース {selected["minutes"]}分 {selected["yen"]:,}円'
            f'（{selected["course_name"]} / {selected["label"]}）'
        )
    else:
        state = (
            "候補あり・判定保留" if status == "unknown" and entry.get("candidates") else
            "候補未抽出・判定保留" if status == "unknown" else STATUSES.get(status, "判定保留")
        )
        heading = f'料金不明（{state}）'
        evidence = entry.get("reason") or "掲載コース料金は未取得です"
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
    reprocessed = (
        f' / 保存候補の再解析 {entry["reprocessed_at"]}（ページ再取得なし・税条件は元記録を保持）'
        if entry.get("reprocessed_at") else ""
    )
    redirect_evidence = (
        f'<p>観測した転送先（未追跡・店舗URLは変更しません）: {escape(entry["redirect_location"])}</p>'
        if entry.get("redirect_location") else ""
    )
    decisions = entry.get("table_decisions", [])
    matching = (selected or {}).get("compared_courses") or ([selected] if selected else [])
    conditions_html = "".join(
        f'<li>{"採用金額" if selected and item["yen"] == selected["yen"] else "比較対象"}: '
        f'{escape(item["course_name"] or "見出しなし")} / {escape(item["section"])} / '
        f'{escape(item["label"])} / {escape(item["price_text"])}'
        f' — 条件: {escape(item["conditions"] or "記載条件は出典で確認してください")}</li>'
        for item in matching
    )
    decision_html = (
        '<ul class="shop-price-decisions">' + "".join(
            f'<li>{escape(item["course_name"] or "見出しなし")} / '
            f'{escape(item["section"])}: {escape(item["reason"])}</li>' for item in decisions
        ) + "</ul>" if decisions else ""
    )
    return (
        f'<div class="shop-price" data-details-scroll="native"><span class="shop-price-value">{escape(heading)}</span>'
        f'<details class="shop-price-evidence"><summary>料金の根拠・別途費用</summary>'
        f'<p>{escape(evidence)}</p><p>{escape(tax)}</p>'
        f'<p>採用時間の掲載コース・比較料金と原条件（適用可能性は未確認）:</p><ul>{conditions_html}</ul>'
        f'<p>条件: {escape((selected or {}).get("conditions") or "詳細な適用条件は出典を確認してください")}</p>'
        f'<p>掲載金額に加算しない別情報: {escape(fee_text)}</p>'
        f'<p>{escape(entry.get("reason", ""))}</p>{decision_html}{redirect_evidence}'
        f'<p>確認日時: {escape(checked + saved + reprocessed)}</p>{origin}</details></div>'
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
    parser.add_argument("--reprocess-cache", type=Path, help="保存候補の入力JSON（通信なし・--shop-url または --reprocess-all と併用）")
    parser.add_argument("--reprocess-all", action="store_true", help="現在の全保存候補を通信なしで再判定。--reprocess-cache指定で証拠を読取統合")
    parser.add_argument("--all", action="store_true", help="3レポート保存URLの全店舗を明示的に取得")
    parser.add_argument("--limit", type=int, help="全店舗取得の上限")
    parser.add_argument("--refresh", action="store_true", help="既存の取得結果も更新（既定は未取得店舗だけ）")
    parser.add_argument("--summary", action="store_true", help="保存料金の数値分布と状態を表示")
    args = parser.parse_args(argv)
    if args.reprocess_all:
        if args.all or args.shop_url or args.html_file or args.limit or args.refresh:
            parser.error("--reprocess-all は取得・店舗選択・limit・refreshと併用できません")
        payload, transitions, conflicts = reprocess_snapshot(source=args.reprocess_cache)
        print(f"保存候補の再判定 {transitions} / 証拠の相違 {len(conflicts)}件（別証拠を保持）")
        print(f"保存店舗 {len(payload['shops'])} / 状態 " + str({
            status: sum(entry["status"] == status for entry in payload["shops"].values()) for status in STATUSES
        }))
        print("未解決理由（重複あり）: " + str(dict(Counter(
            reason for entry in payload["shops"].values() for reason in unresolved_categories(entry)
        ))))
        return int(any(entry["status"] != "ok" for entry in payload["shops"].values()))
    if args.limit is not None and args.limit < 1:
        parser.error("--limit は1以上です")
    if args.all and (args.shop_url or args.html_file or args.reprocess_cache):
        parser.error("--all と店舗指定・HTML取り込みは併用できません")
    if args.reprocess_cache and (not args.shop_url or args.html_file):
        parser.error("--reprocess-cache は --shop-url と併用し、HTML取り込みとは併用できません")
    urls = known_shops() if args.all else sorted({shop_url(url) for url in args.shop_url})
    if "" in urls or (args.html_file and len(urls) != 1):
        parser.error("有効なCityHeaven店舗URLが必要です（HTML取り込みは1店舗）")
    if not urls and not args.summary:
        parser.error("--shop-url / --all / --summary のいずれかが必要です")
    cache = load_cache()
    if args.reprocess_cache:
        evidence = load_cache(args.reprocess_cache)
        if any(
            url not in evidence["shops"] or evidence["shops"][url].get("status") != "unknown"
            or not evidence["shops"][url].get("candidates") for url in urls
        ):
            parser.error("指定店舗には候補付き unknown の保存記録が必要です（404/410等は再解析しません）")
        if any(url in cache["shops"] for url in urls) and not args.refresh:
            parser.error("既存店舗の再解析結果を保存するには --refresh が必要です")
    pending = [url for url in urls if args.refresh or url not in cache["shops"]]
    if args.limit:
        pending = pending[:args.limit]
    failures = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"})
        for index, url in enumerate(pending):
            if args.reprocess_cache:
                entry = reprocess_cached_entry(evidence["shops"][url])
            elif args.html_file:
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
            if not args.reprocess_cache:
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
    print("未解決理由（重複あり）: " + str(dict(Counter(
        reason for entry in cache["shops"].values() for reason in unresolved_categories(entry)
    ))))
    print(f"保存サンプルの60分換算分布（全店舗の代表性なし）: {values} 円")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
