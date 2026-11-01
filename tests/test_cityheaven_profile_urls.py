import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cityheaven.profile_urls import canonical_profile_url, same_profile


class ProfileUrlTests(unittest.TestCase):
    ORIGINAL = "https://www.cityheaven.net/aichi/A2301/A230113/alamode/girlid-31291565/"
    RELOCATED = ORIGINAL.replace("A230113", "A230101")

    def test_reported_area_relocations(self):
        for shop, girl, old_area, new_area in [
            ("alamode", "31291565", "A230113", "A230101"),
            ("imperial-h", "12065159", "A230113", "A230101"),
            ("aitokkyu2006", "10798162", "A230103", "A230104"),
        ]:
            old = f"https://www.cityheaven.net/aichi/A2301/{old_area}/{shop}/girlid-{girl}/"
            new = old.replace(old_area, new_area)
            with self.subTest(shop=shop):
                self.assertTrue(same_profile(old, new))
                self.assertEqual(canonical_profile_url(new), new)
        self.assertTrue(same_profile(
            self.ORIGINAL, self.RELOCATED.replace("/aichi/A2301/A230101/", "/gifu/A2101/")
        ))

    def test_normalization_and_requested_subpage(self):
        for value in [
            self.RELOCATED.rstrip("/"), self.RELOCATED + "?of=y#top",
            self.RELOCATED.replace("https://www.", "http://WWW."),
            self.RELOCATED.replace("www.", ""),
            self.RELOCATED.replace("www.", "sp."),
        ]:
            with self.subTest(value=value):
                self.assertEqual(canonical_profile_url(value), self.RELOCATED)
                self.assertTrue(same_profile(self.ORIGINAL + "diary/?lo=1", value))

    def test_non_profiles_and_different_people_are_rejected(self):
        for value in [
            self.RELOCATED.replace("alamode", "different-shop"),
            self.RELOCATED.replace("31291565", "999"),
            self.RELOCATED + "diary/",
            self.RELOCATED.replace("www.cityheaven.net", "evilcityheaven.net"),
            self.RELOCATED.replace("www.cityheaven.net", "www.cityheaven.net.evil.invalid"),
            self.RELOCATED.replace("www.cityheaven.net", "img.cityheaven.net"),
            self.RELOCATED.replace("www.cityheaven.net", "www.cityheaven.net:8080"),
            self.RELOCATED.replace("www.cityheaven.net", "user:pass@www.cityheaven.net"),
            self.RELOCATED.replace("https:", "ftp:"),
            "https://www.cityheaven.net/login/?next=" + self.RELOCATED,
            "https://www.cityheaven.net/login/girlid-31291565/",
            "https://www.cityheaven.net/cs/nenrei/girlid-31291565/",
            "https://www.cityheaven.net/aichi/A2301/A230101/alamode/",
            "https://www.cityheaven.net/aichi/A2301/A230101/alamode/girlid-31291565/login/",
            "https://[broken", None, "",
        ]:
            with self.subTest(value=value):
                self.assertFalse(same_profile(self.ORIGINAL, value))
        self.assertFalse(same_profile("", ""))
        self.assertFalse(same_profile("https://www.cityheaven.net/login/", self.RELOCATED))
