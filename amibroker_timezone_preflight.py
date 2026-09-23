"""Validate AmiBroker's live intraday timestamp diagnostic export."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


REQUIRED = {"DateTime", "TimeNum", "TimeShiftSeconds", "IntervalSeconds"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_timezone_export(csv_path: Path, database: str) -> dict:
    csv_path = Path(csv_path)
    frame = pd.read_csv(csv_path)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Timezone export is missing: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("Timezone preflight exploration returned no ES rows")
    dates = pd.to_datetime(frame["DateTime"], errors="coerce", format="mixed")
    if dates.isna().any():
        raise ValueError("Timezone export contains an invalid date")
    time_num = pd.to_numeric(frame["TimeNum"], errors="coerce")
    shifts = pd.to_numeric(frame["TimeShiftSeconds"], errors="coerce")
    intervals = pd.to_numeric(frame["IntervalSeconds"], errors="coerce")
    if time_num.isna().any() or shifts.isna().any() or intervals.isna().any():
        raise ValueError("Timezone export contains a malformed numeric value")
    normalized = pd.DataFrame(
        {"DateTime": dates, "TimeNum": time_num.astype(int)}
    )
    if normalized.duplicated(["DateTime", "TimeNum"]).any():
        raise ValueError("Timezone export contains duplicate rows")
    if not shifts.eq(0).all():
        raise ValueError("AmiBroker database time shift is not zero")
    if not intervals.eq(300).all():
        raise ValueError("AmiBroker intraday database is not 5-minute")

    session_dates = normalized["DateTime"].dt.normalize()
    session_times = normalized.groupby(session_dates)["TimeNum"].agg(set)
    complete = session_times.map(
        lambda values: any(93000 <= value < 93500 for value in values)
        and any(155500 <= value < 160000 for value in values)
    )
    complete_dates = session_times.index[complete]
    complete_dates = complete_dates[complete_dates.dayofweek < 5]
    winter = sorted(str(value.date()) for value in complete_dates if value.month == 1)
    summer = sorted(str(value.date()) for value in complete_dates if value.month == 7)
    if not winter:
        raise ValueError("Timezone export has no complete winter 09:30-09:34/15:55-15:59 session")
    if not summer:
        raise ValueError("Timezone export has no complete summer 09:30-09:34/15:55-15:59 session")
    return {
        "schema_version": 1,
        "status": "PASS",
        "timezone": "America/Detroit",
        "database": str(database),
        "timeshift_seconds": 0,
        "interval_seconds": 300,
        "winter_sessions": winter,
        "summer_sessions": summer,
        "csv_path": str(csv_path.resolve()),
        "csv_sha256": _sha256(csv_path),
        "rows": int(len(frame)),
    }
