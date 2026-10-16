"""プロフィールページの掲載判定（収集と HTML 生成で共通）。

unavailable は明確な不在の証拠があるときだけ返す:
  - HTTP 404 / 410
  - 別ページ（店舗トップ・一覧・別IDなど）への転送
  - サイト別の空プロフィールテンプレート（名前欄が空など）
CityHeaven の girlid ページは HTTP 200 かつ同一ページなら名前照合せずに published。
それ以外で判断できないときは None を返し、呼び出し側が名前照合する（不一致でも unavailable にはしない）。
"""
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from report_store import normalize_profile_url

HOST_PREFIXES = ("www.", "sp.", "m.", "s.")
META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


def decode_html(content, declared_encoding=None):
    """HTMLバイト列を文字列にする。charset 未指定時の requests 既定値（ISO-8859-1）は信用せず meta charset / UTF-8 を使う。"""
    encodings = []
    if declared_encoding and declared_encoding.lower() not in {"iso-8859-1", "latin-1"}:
        encodings.append(declared_encoding)
    match = META_CHARSET_RE.search(content[:4096])
    if match:
        encodings.append(match.group(1).decode("ascii", errors="ignore"))
    encodings.append("utf-8")
    for encoding in encodings:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")
CITYHEAVEN_PROFILE_RE = re.compile(r"/girlid-\d+/?$")
SHABOOLE_EMPTY_TITLE_RE = re.compile(r"[｜|]\s*\[\s*\]\s*プロフィール")


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _strip_host_prefix(url):
    parts = urlsplit(url)
    host = parts.netloc
    for prefix in HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def same_profile_page(requested_url, final_url):
    """転送後も同じプロフィールか（http/https、末尾スラッシュ、www./sp. などの違いは同一扱い）。"""
    if not final_url:
        return True
    requested = normalize_profile_url(requested_url)
    final = normalize_profile_url(final_url)
    if requested is None or final is None:
        return requested == final
    return _strip_host_prefix(requested) == _strip_host_prefix(final)


def is_cityheaven_profile(url):
    host = _host(url)
    return (host == "cityheaven.net" or host.endswith(".cityheaven.net")) and bool(
        CITYHEAVEN_PROFILE_RE.search(urlsplit(url).path)
    )


def empty_profile_reason(url, html):
    """サイト別の空プロフィールテンプレートなら理由を返す。"""
    host = _host(url)
    if host.endswith("shaboole.com"):
        soup = BeautifulSoup(html or "", "html.parser")
        title = soup.title.get_text() if soup.title else ""
        if SHABOOLE_EMPTY_TITLE_RE.search(title):
            return "名前が空のプロフィールテンプレート"
    if host.endswith("stylish-bach.com") and "girldetail" in urlsplit(url).path:
        soup = BeautifulSoup(html or "", "html.parser")
        if soup.body is not None and not soup.select(".profilename, .element-girl-detail"):
            return "プロフィール欄の無い女の子詳細テンプレート"
    return None


def classify_profile_page(requested_url, final_url, status_code, html):
    """(status, reason) を返す。status は published / unavailable / unknown / None（名前照合が必要）。"""
    if status_code in {404, 410}:
        return "unavailable", f"HTTP {status_code}"
    if not 200 <= status_code < 300:
        return "unknown", f"HTTP {status_code}"
    if not same_profile_page(requested_url, final_url):
        return "unavailable", f"別ページへ転送: {final_url}"
    reason = empty_profile_reason(final_url or requested_url, html)
    if reason:
        return "unavailable", reason
    if is_cityheaven_profile(requested_url):
        return "published", "CityHeaven プロフィール（HTTP 200・同一URL）"
    return None, None
