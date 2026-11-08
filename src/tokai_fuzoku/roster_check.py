"""店舗キャスト一覧ページとの照合（プロフィールページに名前が文字で出ないサイト向け）。

ROSTER_URLS に登録した店舗だけが対象。一覧に本人のプロフィールリンクか名前があれば published、
一覧が十分に読めている（同じ形式のプロフィールリンクが多数・ページ送りなし）のに本人がいなければ unavailable。
読み込み失敗・リンクが少ない・ページ送りがある場合は None（判定しない）。
"""
import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from tokai_fuzoku.profile_signals import decode_html

ROSTER_URLS = {
    "y-lupinus.com": ["https://www.y-lupinus.com/companion.php"],
    "soap-fourseason.com": ["https://soap-fourseason.com/therapist/"],
    "sweet-k.com": ["https://www.sweet-k.com/cast.php"],
    "y-loveboat.com": ["https://www.y-loveboat.com/girls.php"],
    "lions-net.com": ["https://www.lions-net.com/companion.php"],
    "oh-oku.com": ["https://www.oh-oku.com/companion.php"],
    "y-ichiriki.com": ["https://www.y-ichiriki.com/companion.php"],
    "pretty-girl.net": ["https://www.pretty-girl.net/companion.php"],
    "talent-club.com": ["https://www.talent-club.com/pc/companion.php"],
    "parfait.y-lupinus.com": ["https://parfait.y-lupinus.com/companion.php"],
    "yoshiwara-soap.jp": ["https://yoshiwara-soap.jp/cast/"],
    "n-fortune.jp": ["https://n-fortune.jp/cast/"],
    "mutekikawasaki.jp": ["https://mutekikawasaki.jp/cast.html"],
    "t-classy.com": ["https://www.t-classy.com/profile/"],
}
MIN_PROFILE_LINKS = 20
PAGER_RE = re.compile(r"[?&]page=\d|/page/\d|次のページ|次へ|NEXT\s*>|>>", re.I)


def _base_host(url):
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _profile_key(url):
    parts = urlsplit(url)
    return parts.path, parts.query


def _shape(path):
    """個人ごとにパスが変わる形式（/cast/a012.html、/profile/01180/）を同じ形にそろえる。"""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return path
    stem, dot, ext = segments[-1].partition(".")
    if re.search(r"\d", stem):
        return "/" + "/".join(segments[:-1] + ["*" + dot + ext])
    return path


class RosterChecker:
    def __init__(self, session, headers, name_matcher):
        self.session = session
        self.headers = headers
        self.name_matcher = name_matcher
        self.cache = {}

    def _load(self, roster_url):
        if roster_url in self.cache:
            return self.cache[roster_url]
        try:
            response = self.session.get(roster_url, headers=self.headers, timeout=20)
            if response.status_code != 200:
                raise requests.RequestException(f"HTTP {response.status_code}")
            html = decode_html(response.content, response.encoding)
            soup = BeautifulSoup(html, "lxml")
            links = [urljoin(response.url, a["href"]) for a in soup.find_all("a", href=True)]
            alts = " ".join(f"{t.get('alt') or ''} {t.get('title') or ''}" for t in soup.find_all(True))
            data = {"links": links, "text": soup.get_text(" ") + " " + alts, "html": html}
        except requests.RequestException as exc:
            print(f"キャスト一覧を取得できないため照合を保留: {roster_url} ({exc})")
            data = None
        self.cache[roster_url] = data
        return data

    def check(self, profile_url, name):
        urls = ROSTER_URLS.get(_base_host(profile_url))
        if not urls:
            return None, None
        path, query = _profile_key(profile_url)
        shape = _shape(path)
        profile_links = 0
        pager = False
        for roster_url in urls:
            data = self._load(roster_url)
            if data is None:
                return None, None
            if any(self._same_profile(link, path, query) for link in data["links"]):
                return "published", f"キャスト一覧にプロフィールリンクあり: {roster_url}"
            if name and self.name_matcher(data["text"], str(name)):
                return "published", f"キャスト一覧に名前あり: {roster_url}"
            profile_links += sum(1 for link in data["links"] if _shape(urlsplit(link).path) == shape)
            pager = pager or bool(PAGER_RE.search(data["html"]))
        if profile_links >= MIN_PROFILE_LINKS and not pager:
            return "unavailable", f"キャスト一覧に存在しない（{profile_links}件中）: {urls[0]}"
        return None, None

    @staticmethod
    def _same_profile(link, path, query):
        parts = urlsplit(link)
        return parts.path == path and (parts.query == query or query in parts.query.split("&"))
