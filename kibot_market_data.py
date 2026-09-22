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

import numpy as np
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
    *,
    reject_invalid_rows: bool = False,
) -> pd.DataFrame:
    path = Path(path)
    frequency = _validate_frequency(frequency)
    symbol = _canonical_symbol(path, member)
    expected_columns = 6 if frequency == "daily" else 7
    records = []
    rejections = []

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
                invalid_envelope = False
                if frequency == "daily":
                    if low > high:
                        invalid_envelope = True
                    elif not low <= open_ <= high:
                        open_gap = max(open_ - high, low - open_)
                        scale = max(abs(open_), abs(high), abs(low), 1.0)
                        if open_gap / scale <= 0.002:
                            high = max(high, open_)
                            low = min(low, open_)
                        else:
                            invalid_envelope = True
                elif low > min(open_, close) or high < max(open_, close) or low > high:
                    invalid_envelope = True
                if invalid_envelope:
                    if reject_invalid_rows:
                        rejections.append(
                            {
                                "symbol": symbol,
                                "frequency": frequency,
                                "comparison_key": timestamp.date().isoformat()
                                if frequency == "daily"
                                else timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "source": "kibot",
                                "reason": "vendor_invalid_daily_open"
                                if frequency == "daily"
                                else "invalid_ohlc_envelope",
                            }
                        )
                        continue
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
        empty = pd.DataFrame(columns=CANONICAL_COLUMNS)
        empty.attrs["rejections"] = rejections
        return empty

    working = pd.DataFrame.from_records(records)
    keys = ["symbol", "frequency", "timestamp"]
    value_columns = ["open", "high", "low", "close", "volume"]
    for _, group in working.groupby(keys, sort=False):
        if len(group) > 1 and len(group[value_columns].drop_duplicates()) > 1:
            row_number = int(group.iloc[-1]["_row_number"])
            _row_error(path, member, row_number, "conflicting duplicate timestamp")

    working = working.drop_duplicates(subset=keys, keep="first")
    working = working.sort_values("timestamp").reset_index(drop=True)
    result = working[CANONICAL_COLUMNS].copy()
    result.attrs["rejections"] = rejections
    return result


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

    working = pd.DataFrame(frame).copy()
    for column in CANONICAL_COLUMNS:
        if column not in working:
            working[column] = pd.NA
    frequency = working["frequency"].astype(str).str.lower()
    is_daily = frequency.eq("daily")
    timestamps = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    dates = pd.to_datetime(working["date"], errors="coerce")
    timestamp_date_keys = timestamps.dt.strftime("%Y-%m-%d")
    daily_keys = dates.dt.strftime("%Y-%m-%d").fillna(timestamp_date_keys)
    intraday_keys = timestamps.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    comparison_keys = daily_keys.where(is_daily, intraday_keys).fillna("")

    prices = working[["open", "high", "low", "close"]].apply(
        pd.to_numeric, errors="coerce"
    )
    finite = prices.notna().all(axis=1) & np.isfinite(prices).all(axis=1)
    daily_envelope = (
        prices["low"].le(prices["high"])
        & prices["open"].ge(prices["low"])
        & prices["open"].le(prices["high"])
    )
    intraday_envelope = (
        prices["low"].le(prices["high"])
        & prices["low"].le(prices[["open", "close"]].min(axis=1))
        & prices["high"].ge(prices[["open", "close"]].max(axis=1))
    )
    envelope = daily_envelope.where(is_daily, intraday_envelope)
    reasons = pd.Series(pd.NA, index=working.index, dtype="object")
    reasons.loc[comparison_keys.eq("") | timestamps.isna()] = "invalid_timestamp"
    reasons.loc[reasons.isna() & ~finite] = "invalid_ohlc_numeric"
    reasons.loc[reasons.isna() & ~envelope] = "invalid_ohlc_envelope"

    rejected_mask = reasons.notna()
    rejected = pd.DataFrame(
        {
            "symbol": working.loc[rejected_mask, "symbol"],
            "frequency": working.loc[rejected_mask, "frequency"],
            "comparison_key": comparison_keys.loc[rejected_mask],
            "source": working.loc[rejected_mask, "source"],
            "reason": reasons.loc[rejected_mask],
        },
        columns=REJECTION_COLUMNS,
    ).reset_index(drop=True)

    accepted = working.loc[~rejected_mask, CANONICAL_COLUMNS].copy()
    accepted_timestamps = timestamps.loc[~rejected_mask]
    accepted["timestamp"] = accepted_timestamps.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    accepted["date"] = comparison_keys.loc[~rejected_mask].where(
        is_daily.loc[~rejected_mask], accepted_timestamps.dt.strftime("%Y-%m-%d")
    )
    for column in ("open", "high", "low", "close"):
        accepted[column] = prices.loc[~rejected_mask, column]
    return accepted.reset_index(drop=True), rejected


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
            accepted["_comparison_key"] = accepted["timestamp"].astype(str)
            daily_mask = accepted["frequency"].astype(str).str.lower().eq("daily")
            accepted.loc[daily_mask, "_comparison_key"] = accepted.loc[
                daily_mask, "date"
            ].astype(str)
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
    group_columns = ["symbol", "frequency", "_comparison_key"]
    candidates = candidates.reset_index(drop=True)
    candidates["_candidate_id"] = range(len(candidates))
    candidates["_retrieved_sort"] = pd.to_datetime(
        candidates["retrieved_at"], utc=True, errors="coerce"
    )
    ranked = candidates.sort_values(
        [*group_columns, "_priority", "_retrieved_sort", "_frame_order", "_row_order"],
        na_position="first",
        kind="stable",
    )
    selections = ranked.drop_duplicates(group_columns, keep="last")

    reviewed = _reviewed_choices(reviewed_bars)
    reviewed_count = 0
    if reviewed:
        selection_by_key = {
            tuple(str(row[column]) for column in group_columns): index
            for index, row in selections.iterrows()
        }
        replacement_indices = {}
        for group_key, reviewed_source in reviewed.items():
            mask = pd.Series(True, index=candidates.index)
            for column, value in zip(group_columns, group_key):
                mask &= candidates[column].astype(str).eq(str(value))
            group = candidates.loc[mask].sort_values(
                ["_frame_order", "_row_order"], kind="stable"
            )
            if group.empty:
                continue
            matching = group[group["source"].astype(str).eq(reviewed_source)]
            replacement_indices[group_key] = int(
                (matching if len(matching) else group).index[0]
            )
            reviewed_count += 1
        if replacement_indices:
            selected_indices = set(selections.index)
            for key, replacement in replacement_indices.items():
                previous = selection_by_key.get(key)
                if previous is not None:
                    selected_indices.discard(previous)
                selected_indices.add(replacement)
            selections = candidates.loc[sorted(selected_indices)]

    duplicate_candidates = candidates[
        candidates.duplicated(group_columns, keep=False)
    ]
    selected_for_join = selections[
        selections.duplicated(group_columns, keep=False)
        | selections.set_index(group_columns).index.isin(
            duplicate_candidates.set_index(group_columns).index
        )
    ][
        [*group_columns, "_candidate_id", "source", "open", "high", "low", "close", "volume"]
    ].rename(
        columns={
            "_candidate_id": "_selected_id",
            "source": "selected_source",
            **{column: f"selected_{column}" for column in ("open", "high", "low", "close", "volume")},
        }
    )
    other_for_join = duplicate_candidates[
        [*group_columns, "_candidate_id", "source", "open", "high", "low", "close", "volume"]
    ].rename(
        columns={
            "_candidate_id": "_other_id",
            "source": "other_source",
            **{column: f"other_{column}" for column in ("open", "high", "low", "close", "volume")},
        }
    )
    comparisons = selected_for_join.merge(other_for_join, on=group_columns, how="inner")
    comparisons = comparisons[comparisons["_selected_id"] != comparisons["_other_id"]].copy()
    different = pd.Series(False, index=comparisons.index)
    for column in ("open", "high", "low", "close", "volume"):
        left = comparisons[f"selected_{column}"]
        right = comparisons[f"other_{column}"]
        different |= ~(left.eq(right) | (left.isna() & right.isna()))
        comparisons[f"{column}_abs_diff"] = (left - right).abs()
    comparisons = comparisons.loc[different].copy()
    comparisons = comparisons.rename(columns={"_comparison_key": "comparison_key"})
    conflict_frame = comparisons.reindex(columns=CONFLICT_COLUMNS)

    rows = selections.copy()
    rows = rows.sort_values(["symbol", "frequency", "timestamp"])
    rows = rows[CANONICAL_COLUMNS].reset_index(drop=True)
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
