import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if value is None:
        return None

    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def read_jsonl(path, *, columns=None):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JSON Linesデータがありません: {path}")
    if path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)

    records = pd.read_json(path, orient="records", lines=True, dtype=False)
    if columns is not None:
        missing = set(columns).difference(records.columns)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"JSON Linesデータに必要な列がありません: {names}")
        records = records.loc[:, columns]
    return records


def write_jsonl_atomic(records, path, *, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is not None:
        missing = set(columns).difference(records.columns)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"JSON Lines出力に必要な列がありません: {names}")
        records = records.loc[:, columns]

    temporary_path = path.with_name(f".{path.name}.tmp")
    serialized = "".join(
        json.dumps(_json_value(record), ensure_ascii=False, allow_nan=False) + "\n"
        for record in records.to_dict(orient="records")
    )
    temporary_path.write_text(serialized, encoding="utf-8", newline="")
    temporary_path.replace(path)