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
