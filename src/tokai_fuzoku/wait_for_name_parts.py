import re
import unicodedata

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def wait_for_name_parts(driver, name, expected_url=None, timeout=5):
    match = re.match(r"^(.*?)\s*[（(](.*?)[）)]", name)
    if not match:
        def name_present_and_url_ok(current_driver):
            body_text = normalize(current_driver.find_element(By.TAG_NAME, "body").text)
            return normalize(name) in body_text and (
                expected_url is None or current_driver.current_url == expected_url
            )

        return WebDriverWait(driver, timeout).until(name_present_and_url_ok)

    prefix, inner = normalize(match.group(1)), normalize(match.group(2))

    def both_parts_and_url_ok(current_driver):
        body_text = normalize(current_driver.find_element(By.TAG_NAME, "body").text)
        return (
            prefix in body_text
            and inner in body_text
            and (expected_url is None or current_driver.current_url == expected_url)
        )

    return WebDriverWait(driver, timeout).until(both_parts_and_url_ok)
