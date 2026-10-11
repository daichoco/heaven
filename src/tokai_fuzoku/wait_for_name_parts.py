import re
import unicodedata

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def name_in_text(text, name):
    """ページ本文に名前（「名前（補足）」形式なら両方）が含まれるか。"""
    body_text = normalize(text)
    match = re.match(r"^(.*?)\s*[（(](.*?)[）)]", name)
    if not match:
        return normalize(name) in body_text
    return normalize(match.group(1)) in body_text and normalize(match.group(2)) in body_text


def wait_for_name_parts(driver, name, expected_url=None, timeout=5):
    def name_present_and_url_ok(current_driver):
        body_text = current_driver.find_element(By.TAG_NAME, "body").text
        return name_in_text(body_text, name) and (
            expected_url is None or current_driver.current_url == expected_url
        )

    return WebDriverWait(driver, timeout).until(name_present_and_url_ok)
