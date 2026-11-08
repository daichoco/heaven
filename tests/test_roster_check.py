import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokai_fuzoku import roster_check
from tokai_fuzoku.roster_check import RosterChecker
from tokai_fuzoku.wait_for_name_parts import name_in_text

ROSTER = "https://www.y-lupinus.com/companion.php"
PROFILE = "https://www.y-lupinus.com/profile.php?id={}"


class Response:
    def __init__(self, html, status=200):
        self.content = html.encode("utf-8")
        self.encoding = "utf-8"
        self.status_code = status
        self.url = ROSTER


class Session:
    def __init__(self, html, status=200):
        self.response = Response(html, status)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.response


def roster_html(count, extra=""):
    links = "".join(f'<a href="profile.php?id=g{i}"><img alt="girl{i}"></a>' for i in range(count))
    return f"<html><body>{links}{extra}</body></html>"


class RosterCheckTests(unittest.TestCase):
    def checker(self, html, status=200):
        return RosterChecker(Session(html, status), {}, name_in_text)

    def test_profile_link_in_roster_is_published(self):
        status, _ = self.checker(roster_html(25)).check(PROFILE.format("g3"), "あずさ")
        self.assertEqual(status, "published")

    def test_name_in_roster_is_published(self):
        checker = self.checker(roster_html(25, "<p>あずさ</p>"))
        self.assertEqual(checker.check(PROFILE.format("zzz"), "あずさ")[0], "published")

    def test_absent_from_full_roster_is_unavailable(self):
        self.assertEqual(self.checker(roster_html(25)).check(PROFILE.format("zzz"), "ゆず")[0], "unavailable")

    def test_inconclusive_cases_stay_undecided(self):
        self.assertIsNone(self.checker(roster_html(5)).check(PROFILE.format("zzz"), "ゆず")[0])
        self.assertIsNone(self.checker(roster_html(25, '<a href="?page=2">2</a>')).check(PROFILE.format("zzz"), "ゆず")[0])
        self.assertIsNone(self.checker(roster_html(25), status=503).check(PROFILE.format("zzz"), "ゆず")[0])
        self.assertIsNone(self.checker(roster_html(25)).check("https://unlisted.example.com/p?id=1", "ゆず")[0])

    def test_roster_is_fetched_once(self):
        checker = self.checker(roster_html(25))
        checker.check(PROFILE.format("a"), "あ")
        checker.check(PROFILE.format("b"), "い")
        self.assertEqual(checker.session.calls, 1)
        self.assertIn("y-lupinus.com", roster_check.ROSTER_URLS)


if __name__ == "__main__":
    unittest.main()
