import re

VISIT_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月")


def visit_month(details, fallback=""):
    """NS専門の「突撃年月」を YYYY-MM で返す。無ければ fallback(投稿日)。"""
    m = VISIT_RE.fullmatch(str((details or {}).get("突撃年月") or "").strip())
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else fallback
