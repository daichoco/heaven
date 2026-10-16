import re
import unicodedata

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


MIN_BASE_NAME_LENGTH = 2
# 【PLATINUM】［新人］[可愛い系]〔…〕《…》〈…〉 などのランク・タグ表記（NFKC後の形）
TAG_PATTERN = re.compile(r"【[^】]*】|\[[^\]]*\]|〔[^〕]*〕|《[^》]*》|〈[^〉]*〉")
READING_PATTERN = re.compile(r"^(.*?)\(([^)]*)\)")


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def _strip_symbols(text):
    # ♡☆★♪❤ や絵文字などの装飾記号（Unicode カテゴリ So）を除く
    return "".join(ch for ch in text if unicodedata.category(ch) != "So")


def _comparable(text):
    return _strip_symbols(normalize(text)).casefold()


def base_name(name):
    """照合用の基本名。タグ・装飾記号・空白を除き、「名前（読み）」は名前部分だけを返す。"""
    text = TAG_PATTERN.sub("", normalize(name))
    match = READING_PATTERN.match(text)
    if match and match.group(1):
        text = match.group(1)
    return _comparable(text)


def name_in_text(text, name):
    """ページ本文にレポートの姫名が含まれるか。

    【PLATINUM】などのタグや♡☆等の装飾は無視し、「名前（読み）」は名前だけ一致すればよい。
    基本名が1文字以下になる場合は誤一致を避けるため、従来どおり元の表記全体（読みも含む）で照合する。
    """
    body_text = _comparable(text)
    base = base_name(name)
    if len(base) >= MIN_BASE_NAME_LENGTH:
        return base in body_text
    match = READING_PATTERN.match(normalize(name))
    if not match:
        return bool(_comparable(name)) and _comparable(name) in body_text
    main, reading = _comparable(match.group(1)), _comparable(match.group(2))
    return bool(main) and main in body_text and reading in body_text


def wait_for_name_parts(driver, name, expected_url=None, timeout=5):
    def name_present_and_url_ok(current_driver):
        body_text = current_driver.find_element(By.TAG_NAME, "body").text
        return name_in_text(body_text, name) and (
            expected_url is None or current_driver.current_url == expected_url
        )

    return WebDriverWait(driver, timeout).until(name_present_and_url_ok)
