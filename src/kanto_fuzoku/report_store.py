"""関東の記事URL・本文解析。保存と人物同定は東海と共有する。"""
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from tokai_fuzoku import report_store as shared

ROOT_DIR = Path(__file__).resolve().parents[2]
BASE_URL = "https://m.fuzoku-kanto.net/"
REPORTS_JSONL = ROOT_DIR / "data" / "kanto_fuzoku_reports.jsonl"
ARTICLE_HOSTS = {"fuzoku-kanto.net", "m.fuzoku-kanto.net", "www.fuzoku-kanto.net"}
ARTICLE_PATH_RE = re.compile(
    r"^/(?P<year>20\d{2})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<slug>post-\d+|hello-world)/?$"
)
REPEAT_RE = re.compile(r"^返す（リピートする）$|^返さない（リピートしない）$")


def canonical_article_url(url):
    return shared.canonical_article_url(
        url, hosts=ARTICLE_HOSTS, base_url=BASE_URL, path_pattern=ARTICLE_PATH_RE,
    )


class ReportStore(shared.ReportStore):
    def __init__(self, path=REPORTS_JSONL):
        super().__init__(path, canonicalize=canonical_article_url)


def report_body(soup):
    return soup.select_one("#the-content.entry-content") or soup.select_one(".entry-content") or soup


def label_paragraph(soup, label):
    for paragraph in report_body(soup).find_all("p"):
        heading = paragraph.find("strong")
        text = (heading or paragraph).get_text(" ", strip=True)
        if text.startswith(label):
            return paragraph
    return None


def label_value(soup, label):
    paragraph = label_paragraph(soup, label)
    if paragraph is None:
        return None
    text = paragraph.get_text(" ", strip=True)
    value = text[len(label):].strip()
    return value or None


def score_value(soup, label):
    value = label_value(soup, label)
    if value is None and label == "◆姫のスタイル・肌（点数）：":
        value = label_value(soup, "◆姫のスタイル､肌（点数）：")
    match = re.search(r"(-?\d+(?:\.\d*)?)\s*点", value or "")
    if match is None:
        return None
    number = float(match.group(1))
    if not 0 <= number <= 100:
        raise ValueError(f"点数が0〜100の範囲外です: {label}")
    return int(number) if number.is_integer() else number


def comment_value(soup, label):
    paragraph = label_paragraph(soup, label)
    if paragraph is None:
        return None
    inline = label_value(soup, label)
    if inline:
        return inline
    parts = []
    for sibling in paragraph.find_next_siblings():
        text = sibling.get_text(" ", strip=True)
        if text.startswith("◆") or sibling.name not in {"p", "ul", "ol"}:
            break
        if text:
            parts.append(text)
    return "\n".join(parts) or None


def parse_report_details(soup):
    details = {key: score_value(soup, label) for key, label in shared.SCORE_COLUMNS.items()}
    details.update({key: comment_value(soup, label) for key, label in shared.COMMENT_COLUMNS.items()})
    for key, label in shared.PLAY_TIME_COLUMNS.items():
        value = label_value(soup, label)
        details[key] = f"{label}{value}" if value else None
    return details


def parse_report_page(soup, url):
    body = report_body(soup)
    repeat = next(
        (p.get_text(strip=True) for p in body.find_all("p") if REPEAT_RE.fullmatch(p.get_text(strip=True))),
        None,
    )
    name_paragraph = label_paragraph(soup, "◆姫の名前：")
    if repeat is None and name_paragraph is None:
        if any(label_paragraph(soup, label) is not None for label in shared.SCORE_COLUMNS.values()):
            raise ValueError("点数がある記事の人物名・リピート回答を解析できません")
        return None
    if repeat is None or name_paragraph is None:
        raise ValueError("レポートの人物名またはリピート回答を解析できません")
    link = name_paragraph.find("a", href=True)
    name = link.get_text(" ", strip=True) if link else label_value(soup, "◆姫の名前：")
    name = re.sub(r"姫$", "", name or "").strip()
    if not name:
        raise ValueError("レポートの人物名がありません")
    time_tag = soup.select_one("time.entry-date[datetime]")
    if time_tag is None:
        raise ValueError("レポートの公開日時がありません")
    category = soup.find("a", rel=lambda value: value and "category tag" in value)
    canonical = canonical_article_url(url)
    if canonical is None:
        raise ValueError("関東の記事URLではありません")
    return {
        "delete?": 0,
        "date": datetime.fromisoformat(time_tag["datetime"]).date().isoformat(),
        "type": label_value(soup, "◆店舗ジャンル："),
        "shop_name": label_value(soup, "◆出撃店舗名：") or (
            category.get_text(strip=True) if category else None
        ),
        "name": name,
        "predict_age": label_value(soup, "◆姫の推定年齢："),
        "face_score": score_value(soup, "◆姫の顔（点数）："),
        "face_feature": comment_value(soup, "◆姫の顔（コメント）："),
        "cup": label_value(soup, "◆姫の胸（推定カップ）："),
        "repeat": repeat,
        "girl_url": urljoin(url, link["href"]) if link else None,
        "url": canonical,
    }
