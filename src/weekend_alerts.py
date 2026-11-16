"""土日の出勤予定を新たに確認したとき、LINE通知用の文面を溜める。

週間カレンダーの取得直後に呼ぶ。通知済みの (プロフィールURL, 日付) は状態ファイルに保存し、
同じ出勤を二度通知しない。状態ファイルが無い初回は、既存分を通知せず記録だけ行う。
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "weekend_alerts_state.json"
OUTBOX = ROOT / "data" / "weekend_alerts_outbox.txt"
TARGETS = ROOT / "data" / "weekend_alerts_targets.txt"
WEEKDAYS = "月火水木金土日"


def weekend_workings(people, schedules, today):
    """今日以降の土日で state が working の (key, 表示行) を返す。"""
    found = []
    for person in people:
        url = person.get("profile_url")
        for day in (schedules.get(url) or {}).get("days", []):
            if day.get("state") != "working":
                continue
            when = date.fromisoformat(day["date"])
            if when < today or when.weekday() < 5:
                continue
            shop = (person.get("shops") or [""])[0]
            name = (person.get("names") or [""])[0]
            line = f"{when.month}/{when.day}({WEEKDAYS[when.weekday()]}) {shop} {name} {day.get('text', '')}".rstrip()
            found.append((f"{url}|{day['date']}", f"{line}\n  {url}"))
    return found


def load_targets(path=TARGETS):
    """通知対象のプロフィールURL。ページの★で選んだ一覧を1行1URLで保存したもの。"""
    path = Path(path)
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return {line.strip() for line in lines if line.strip().startswith("http")}


def record_alerts(people, schedules, today, state_path=STATE, outbox_path=OUTBOX, targets_path=TARGETS):
    state_path, outbox_path = Path(state_path), Path(outbox_path)
    targets = load_targets(targets_path)
    if not targets:
        return []
    people = [person for person in people if person.get("profile_url") in targets]
    first_run = not state_path.exists()
    notified = set(json.loads(state_path.read_text(encoding="utf-8"))) if not first_run else set()
    found = weekend_workings(people, schedules, today)
    new = [(key, line) for key, line in found if key not in notified]
    # 過去日は不要なので、今日以降のキーだけ残す。
    keep = {key for key in notified if key.rsplit("|", 1)[-1] >= today.isoformat()} | {key for key, _ in found}
    state_path.write_text(json.dumps(sorted(keep), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if new and not first_run:
        with outbox_path.open("a", encoding="utf-8") as file:
            file.write("\n".join(line for _, line in new) + "\n")
    return [] if first_run else [line for _, line in new]
