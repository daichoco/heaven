"""Validated CityHeaven profile URLs and relocation identity."""
import re
from urllib.parse import urlsplit

PROFILE_PATH_RE = re.compile(
    r"/[a-z]+/(?:A\d+/)+(?P<shop>[a-zA-Z0-9_-]+)/(?P<girl>girlid-\d+)(?:/|$)"
)
PROFILE_HOSTS = {
    "cityheaven.net", "www.cityheaven.net", "sp.cityheaven.net",
    "m.cityheaven.net", "s.cityheaven.net",
}


def canonical_profile_url(value, *, allow_subpages=False):
    """Return a canonical URL, rejecting non-profile redirect destinations."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in PROFILE_HOSTS
            or parsed.port not in {None, 80, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        match = PROFILE_PATH_RE.match(parsed.path)
        if not match or (not allow_subpages and match.end() != len(parsed.path)):
            return ""
    except ValueError:
        return ""
    return "https://www.cityheaven.net" + match[0].rstrip("/") + "/"


def same_profile(requested_url, final_url):
    """Area/prefecture relocation is allowed only for the same shop and girlid."""
    requested = canonical_profile_url(requested_url, allow_subpages=True)
    final = canonical_profile_url(final_url)
    return bool(requested and final and requested.split("/")[-3:-1] == final.split("/")[-3:-1])
