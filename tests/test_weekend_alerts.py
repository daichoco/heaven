import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import weekend_alerts as alerts


def person(url, name="A"):
    return {"profile_url": url, "names": [name], "shops": ["S"]}


def schedule(url, *days):
    return {url: {"days": [{"date": d, "state": s, "text": "20:00 - 4:00"} for d, s in days]}}


class WeekendAlertTests(unittest.TestCase):
    def test_first_run_seeds_then_only_new_weekend_workings_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            state, outbox = Path(folder, "s.json"), Path(folder, "o.txt")
            today = date(2026, 11, 16)  # Monday
            url = "https://example/g/1/"
            people = [person(url), person("https://example/g/2/", "B")]
            targets = Path(folder, "t.txt")
            targets.write_text(url + "\n", encoding="utf-8")
            record = alerts.record_alerts
            alerts.record_alerts = lambda *a: record(*a, targets_path=targets)
            self.addCleanup(setattr, alerts, "record_alerts", record)
            first = schedule(url, ("2026-11-21", "working"), ("2026-11-17", "working"))
            self.assertEqual(alerts.record_alerts(people, first, today, state, outbox), [])
            self.assertFalse(outbox.exists())
            second = schedule(url, ("2026-11-21", "working"), ("2026-11-22", "working"), ("2026-11-23", "off"))
            lines = alerts.record_alerts(people, second, today, state, outbox)
            self.assertEqual(len(lines), 1)
            self.assertIn("11/22(日) S A", lines[0])
            self.assertEqual(alerts.record_alerts(people, second, today, state, outbox), [])
            self.assertEqual(outbox.read_text(encoding="utf-8").count("11/22"), 1)


if __name__ == "__main__":
    unittest.main()
