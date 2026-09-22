"""Strict readers and metadata for purchased Kibot futures data."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal
import zipfile
from zoneinfo import ZoneInfo

import pandas as pd

from download_market_data import CANONICAL_COLUMNS


KIBOT_TIMEZONE = ZoneInfo("America/Detroit")
KIBOT_SYMBOL_MAP = {
    "AD": "6A",
    "BP": "6B",
    "CD": "6C",
    "EU": "6E",
    "JY": "6J",
    "SF": "6S",
    "C": "ZC",
    "S": "ZS",
    "W": "ZW",
    "FV": "ZF",
    "TY": "ZN",
    "TU": "ZT",
    "US": "ZB",
    "RP": "RP",
}
KIBOT_REQUIRED_SYMBOLS = {
    "AD",
    "BP",
    "C",
    "CD",
    "CL",
    "ES",
    "EU",
    "FV",
    "GC",
    "HG",
    "JY",
    "NG",
    "NQ",
    "PA",
    "PL",
    "RP",
    "S",
    "SF",
    "SI",
    "TU",
    "TY",
    "US",
    "W",
    "YM",
}


class KibotDataError(ValueError):
    """Raised when a purchased archive cannot be normalized safely."""


@dataclass(frozen=True)
class KibotArchiveMetadata:
    path: Path
    frequency: str
    sha256: str
    size: int
    member_count: int
    vendor_symbols: tuple[str, ...]
    members: tuple[str, ...]


@dataclass(frozen=True)
class MergeResult:
    rows: pd.DataFrame
    conflicts: pd.DataFrame
    rejections: pd.DataFrame
    summary: dict[str, object]


CONFLICT_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "selected_source",
    "other_source",
    "selected_open",
    "selected_high",
    "selected_low",
    "selected_close",
    "selected_volume",
    "other_open",
    "other_high",
    "other_low",
    "other_close",
    "other_volume",
    "open_abs_diff",
    "high_abs_diff",
    "low_abs_diff",
    "close_abs_diff",
    "volume_abs_diff",
]
REJECTION_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "source",
    "reason",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_frequency(frequency: str) -> Literal["daily", "5min"]:
    if frequency not in {"daily", "5min"}:
        raise KibotDataError(
            f"Kibot import supports only daily and 5min, got {frequency!r}"
        )
    return frequency


def _vendor_symbol(member: str) -> str:
    return PurePosixPath(member).stem.upper()


def inventory_kibot_zip(path, frequency: str) -> KibotArchiveMetadata:
    path = Path(path)
    frequency = _validate_frequency(frequency)
    try:
        with zipfile.ZipFile(path) as archive:
            members = tuple(
                sorted(
                    item.filename
                    for item in archive.infolist()
                    if not item.is_dir() and item.filename.lower().endswith(".txt")
                )
            )
    except (OSError, zipfile.BadZipFile) as error:
        raise KibotDataError(f"Unable to read Kibot archive {path}: {error}") from error

    symbols = tuple(sorted(_vendor_symbol(member) for member in members))
    duplicates = sorted(
        symbol for symbol in set(symbols) if symbols.count(symbol) > 1
    )
    if duplicates:
        raise KibotDataError(
            f"Kibot archive {path} contains duplicate members for: "
            f"{', '.join(duplicates)}"
        )
    missing = sorted(KIBOT_REQUIRED_SYMBOLS - set(symbols))
    unknown = sorted(set(symbols) - KIBOT_REQUIRED_SYMBOLS)
    if missing:
        raise KibotDataError(
            f"Kibot archive {path} is missing required symbols: {', '.join(missing)}"
        )
    if unknown:
        raise KibotDataError(
            f"Kibot archive {path} contains unknown symbols: {', '.join(unknown)}"
        )

    return KibotArchiveMetadata(
        path=path,
        frequency=frequency,
        sha256=_sha256(path),
        size=path.stat().st_size,
        member_count=len(members),
        vendor_symbols=symbols,
        members=members,
    )


def _row_error(path: Path, member: str, row_number: int, message: str):
    raise KibotDataError(f"{path}: {member}: row {row_number}: {message}")


def _parse_number(
    value: str,
    path: Path,
    member: str,
    row_number: int,
    label: str,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _row_error(path, member, row_number, f"{label} is not numeric: {value!r}")
    if not math.isfinite(number):
        _row_error(path, member, row_number, f"{label} is not finite: {value!r}")
    return number


def _strict_eastern_to_utc(
    value: str,
    path: Path,
    member: str,
    row_number: int,
) -> datetime:
    try:
        naive = datetime.strptime(value, "%m/%d/%Y %H:%M")
    except ValueError:
        _row_error(path, member, row_number, f"invalid date/time: {value!r}")

    candidates = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=KIBOT_TIMEZONE, fold=fold)
        utc_value = aware.astimezone(timezone.utc)
        if utc_value.astimezone(KIBOT_TIMEZONE).replace(tzinfo=None) == naive:
            candidates[utc_value] = aware.utcoffset()

    if not candidates:
        _row_error(path, member, row_number, f"nonexistent Eastern time: {value}")
    if len(candidates) > 1:
        _row_error(path, member, row_number, f"ambiguous Eastern time: {value}")
    return next(iter(candidates))


def _parse_daily_date(
    value: str,
    path: Path,
    member: str,
    row_number: int,
) -> datetime:
    try:
        parsed = datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        _row_error(path, member, row_number, f"invalid date: {value!r}")
    return parsed.replace(tzinfo=timezone.utc)


def _canonical_symbol(path: Path, member: str) -> str:
    vendor = _vendor_symbol(member)
    if vendor not in KIBOT_REQUIRED_SYMBOLS:
        raise KibotDataError(f"{path}: {member}: unknown Kibot symbol {vendor}")
    return f"/{KIBOT_SYMBOL_MAP.get(vendor, vendor)}"


def read_kibot_member(
    path,
    member: str,
    frequency: str,
    acquired_at: str,
) -> pd.DataFrame:
    path = Path(path)
    frequency = _validate_frequency(frequency)
    symbol = _canonical_symbol(path, member)
    expected_columns = 6 if frequency == "daily" else 7
    records = []

    try:
        archive = zipfile.ZipFile(path)
        source = archive.open(member)
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise KibotDataError(f"Unable to read {member} from {path}: {error}") from error

    try:
        with archive, source, io.TextIOWrapper(source, encoding="utf-8-sig", newline="") as text:
            for row_number, fields in enumerate(csv.reader(text), start=1):
                if not fields or all(not field.strip() for field in fields):
                    continue
                if len(fields) != expected_columns:
                    _row_error(
                        path,
                        member,
                        row_number,
                        f"expected {expected_columns} columns, found {len(fields)}",
                    )

                if frequency == "daily":
                    timestamp = _parse_daily_date(fields[0], path, member, row_number)
                    price_fields = fields[1:5]
                    volume_field = fields[5]
                else:
                    timestamp = _strict_eastern_to_utc(
                        f"{fields[0]} {fields[1]}", path, member, row_number
                    )
                    if timestamp.astimezone(KIBOT_TIMEZONE).minute % 5:
                        _row_error(
                            path,
                            member,
                            row_number,
                            "timestamp is not aligned to the five-minute grid",
                        )
                    price_fields = fields[2:6]
                    volume_field = fields[6]

                open_, high, low, close = [
                    _parse_number(value, path, member, row_number, label)
                    for value, label in zip(
                        price_fields, ("open", "high", "low", "close")
                    )
                ]
                volume = _parse_number(
                    volume_field, path, member, row_number, "volume"
                )
                if low > min(open_, close) or high < max(open_, close) or low > high:
                    _row_error(path, member, row_number, "invalid OHLC envelope")

                records.append(
                    {
                        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "date": timestamp.date().isoformat(),
                        "symbol": symbol,
                        "frequency": frequency,
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                        "open_interest": pd.NA,
                        "source": "kibot",
                        "retrieved_at": acquired_at,
                        "_row_number": row_number,
                    }
                )
    except UnicodeError as error:
        raise KibotDataError(f"Unable to decode {member} from {path}: {error}") from error

    if not records:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    working = pd.DataFrame.from_records(records)
    keys = ["symbol", "frequency", "timestamp"]
    value_columns = ["open", "high", "low", "close", "volume"]
    for _, group in working.groupby(keys, sort=False):
        if len(group) > 1 and len(group[value_columns].drop_duplicates()) > 1:
            row_number = int(group.iloc[-1]["_row_number"])
            _row_error(path, member, row_number, "conflicting duplicate timestamp")

    working = working.drop_duplicates(subset=keys, keep="first")
    working = working.sort_values("timestamp").reset_index(drop=True)
    return working[CANONICAL_COLUMNS].copy()


def source_priority(source) -> int:
    normalized = str(source or "").strip().lower()
    if normalized == "schwab":
        return 300
    if normalized == "kibot":
        return 200
    return 100


def _comparison_key(row) -> str:
    frequency = str(row.get("frequency", "")).lower()
    if frequency == "daily":
        date_value = str(row.get("date", "")).strip()
        if date_value and date_value.lower() != "nan":
            parsed_date = pd.to_datetime(date_value, errors="coerce")
            if pd.notna(parsed_date):
                return parsed_date.date().isoformat()
    timestamp = pd.to_datetime(row.get("timestamp"), utc=True, errors="coerce")
    if pd.isna(timestamp):
        return ""
    if frequency == "daily":
        return timestamp.date().isoformat()
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_price_rows(frame):
    if frame is None or len(frame) == 0:
        return (
            pd.DataFrame(columns=CANONICAL_COLUMNS),
            pd.DataFrame(columns=REJECTION_COLUMNS),
        )

    accepted = []
    rejected = []
    for _, original in pd.DataFrame(frame).iterrows():
        row = original.copy()
        key = _comparison_key(row)
        reason = None
        if not key:
            reason = "invalid_timestamp"
        prices = pd.to_numeric(
            pd.Series([row.get(name) for name in ("open", "high", "low", "close")]),
            errors="coerce",
        )
        if reason is None and (prices.isna().any() or not all(math.isfinite(value) for value in prices)):
            reason = "invalid_ohlc_numeric"
        if reason is None:
            open_, high, low, close = prices.tolist()
            if low > min(open_, close) or high < max(open_, close) or low > high:
                reason = "invalid_ohlc_envelope"

        if reason is not None:
            rejected.append(
                {
                    "symbol": row.get("symbol"),
                    "frequency": row.get("frequency"),
                    "comparison_key": key,
                    "source": row.get("source"),
                    "reason": reason,
                }
            )
            continue

        timestamp = pd.to_datetime(row.get("timestamp"), utc=True)
        row["timestamp"] = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        row["date"] = key if str(row.get("frequency")).lower() == "daily" else timestamp.date().isoformat()
        accepted.append({column: row.get(column, pd.NA) for column in CANONICAL_COLUMNS})

    return (
        pd.DataFrame(accepted, columns=CANONICAL_COLUMNS),
        pd.DataFrame(rejected, columns=REJECTION_COLUMNS),
    )


def _reviewed_choices(reviewed_bars):
    choices = {}
    if reviewed_bars is None or len(reviewed_bars) == 0:
        return choices
    for _, row in pd.DataFrame(reviewed_bars).iterrows():
        choices[
            (
                str(row.get("symbol")),
                str(row.get("frequency")),
                str(row.get("comparison_key")),
            )
        ] = str(row.get("selected_source", ""))
    return choices


def _different(left, right) -> bool:
    columns = ["open", "high", "low", "close", "volume"]
    for column in columns:
        left_value = left.get(column)
        right_value = right.get(column)
        if pd.isna(left_value) and pd.isna(right_value):
            continue
        if pd.isna(left_value) != pd.isna(right_value) or left_value != right_value:
            return True
    return False


def _absolute_difference(left, right):
    if pd.isna(left) or pd.isna(right):
        return pd.NA
    return abs(float(left) - float(right))


def merge_market_data_sources(frames, reviewed_bars=None) -> MergeResult:
    accepted_frames = []
    rejection_frames = []
    for frame_order, frame in enumerate(frames):
        accepted, rejected = validate_price_rows(frame)
        if len(accepted):
            accepted = accepted.copy()
            accepted["_frame_order"] = frame_order
            accepted["_row_order"] = range(len(accepted))
            accepted["_comparison_key"] = accepted.apply(_comparison_key, axis=1)
            accepted["_priority"] = accepted["source"].map(source_priority)
            accepted_frames.append(accepted)
        if len(rejected):
            rejection_frames.append(rejected)

    rejections = (
        pd.concat(rejection_frames, ignore_index=True)
        if rejection_frames
        else pd.DataFrame(columns=REJECTION_COLUMNS)
    )
    if not accepted_frames:
        return MergeResult(
            rows=pd.DataFrame(columns=CANONICAL_COLUMNS),
            conflicts=pd.DataFrame(columns=CONFLICT_COLUMNS),
            rejections=rejections,
            summary={
                "selected_rows": 0,
                "conflicts": 0,
                "rejections": len(rejections),
                "reviewed_selections": 0,
            },
        )

    candidates = pd.concat(accepted_frames, ignore_index=True)
    reviewed = _reviewed_choices(reviewed_bars)
    selections = []
    conflicts = []
    reviewed_count = 0
    group_columns = ["symbol", "frequency", "_comparison_key"]
    for group_key, group in candidates.groupby(group_columns, sort=False, dropna=False):
        reviewed_source = reviewed.get(tuple(str(value) for value in group_key))
        if reviewed_source is not None:
            matching = group[group["source"].astype(str) == reviewed_source]
            selected = (
                matching.sort_values(["_frame_order", "_row_order"]).iloc[0]
                if len(matching)
                else group.sort_values(["_frame_order", "_row_order"]).iloc[0]
            )
            reviewed_count += 1
        else:
            ranked = group.copy()
            ranked["_retrieved_sort"] = pd.to_datetime(
                ranked["retrieved_at"], utc=True, errors="coerce"
            )
            selected = ranked.sort_values(
                ["_priority", "_retrieved_sort", "_frame_order", "_row_order"],
                na_position="first",
            ).iloc[-1]
        selections.append(selected)

        for _, other in group.iterrows():
            if int(other["_frame_order"]) == int(selected["_frame_order"]) and int(other["_row_order"]) == int(selected["_row_order"]):
                continue
            if not _different(selected, other):
                continue
            conflict = {
                "symbol": selected["symbol"],
                "frequency": selected["frequency"],
                "comparison_key": selected["_comparison_key"],
                "selected_source": selected["source"],
                "other_source": other["source"],
            }
            for column in ["open", "high", "low", "close", "volume"]:
                conflict[f"selected_{column}"] = selected[column]
                conflict[f"other_{column}"] = other[column]
                conflict[f"{column}_abs_diff"] = _absolute_difference(
                    selected[column], other[column]
                )
            conflicts.append(conflict)

    rows = pd.DataFrame(selections)
    rows = rows.sort_values(["symbol", "frequency", "timestamp"])
    rows = rows[CANONICAL_COLUMNS].reset_index(drop=True)
    conflict_frame = pd.DataFrame(conflicts, columns=CONFLICT_COLUMNS)
    return MergeResult(
        rows=rows,
        conflicts=conflict_frame,
        rejections=rejections,
        summary={
            "selected_rows": len(rows),
            "conflicts": len(conflict_frame),
            "rejections": len(rejections),
            "reviewed_selections": reviewed_count,
        },
    )
