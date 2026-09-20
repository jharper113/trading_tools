#!/usr/bin/env python3
"""Create corrected daily and 15-minute AmiBroker WFA .ABS presets.

This patcher targets the BROKSSE2 fixed-layout format used by the supplied
AmiBroker settings file. It validates the source signature and known WFA date
fields before changing documented offsets.
"""

from __future__ import annotations

import argparse
import struct
from datetime import date
from pathlib import Path


OFFSETS = {
    "initial_equity": 0x008,
    "trade_flags": 0x00C,
    "commission_mode": 0x038,
    "commission_value": 0x03C,
    "periodicity": 0x078,
    "futures_mode": 0x088,  # AmiBroker PointsOnlyTest / futures mode
    "round_lot_size": 0x114,
    "reverse_signal_forces_exit": 0x120,
    "allow_same_bar_exit": 0x128,
    "min_shares": 0x144,
    "use_prev_bar_equity": 0x1BA,
    "chart_interval": 0x2D2,
    "is_enabled": 0x2DA,
    "is_start": 0x2DE,
    "is_end": 0x2E6,
    "is_last": 0x2EE,
    "is_step": 0x2F6,
    "is_step_unit": 0x2FA,
    "is_anchored": 0x2FE,
    "is_last_uses_today": 0x302,
    "os_enabled": 0x306,
    "os_start": 0x30A,
    "os_end": 0x312,
    "os_last": 0x31A,
    "os_step": 0x322,
    "os_step_unit": 0x326,
    "os_anchored": 0x32A,
    "os_last_uses_today": 0x32E,
}

# AmiBroker date-only bitset: year[63:52], month[51:48], day[47:43].
# The supplied file uses this sentinel for the lower time-related bits.
DATE_TIME_SENTINEL = 0x7FFFFFFFFC0


def encode_ab_date(value: date) -> int:
    return (
        (value.year << 52)
        | (value.month << 48)
        | (value.day << 43)
        | DATE_TIME_SENTINEL
    )


def decode_ab_date(value: int) -> date:
    year = (value >> 52) & 0xFFF
    month = (value >> 48) & 0xF
    day = (value >> 43) & 0x1F
    return date(year, month, day)


def read_i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def read_f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _put_i32(data: bytearray, field: str, value: int) -> None:
    struct.pack_into("<i", data, OFFSETS[field], int(value))


def _put_f32(data: bytearray, field: str, value: float) -> None:
    struct.pack_into("<f", data, OFFSETS[field], float(value))


def _put_date(data: bytearray, field: str, value: date) -> None:
    struct.pack_into("<Q", data, OFFSETS[field], encode_ab_date(value))


def validate_source(source: bytes) -> None:
    if len(source) != 1278:
        raise ValueError(
            f"Expected the supplied 1278-byte BROKSSE2 format; got {len(source)} bytes"
        )
    if source[:8] != b"BROKSSE2":
        raise ValueError("Not a supported AmiBroker BROKSSE2 .ABS file")
    # Guard against applying offsets to an unrelated layout.
    if decode_ab_date(struct.unpack_from("<Q", source, OFFSETS["is_start"])[0]) != date(2014, 1, 1):
        raise ValueError("Unexpected source IS date layout; refusing binary patch")
    if decode_ab_date(struct.unpack_from("<Q", source, OFFSETS["os_end"])[0]) != date(2020, 1, 1):
        raise ValueError("Unexpected source OS date layout; refusing binary patch")
    if source[0x332 : 0x339] != b"CAR/MDD":
        raise ValueError("Expected CAR/MDD optimization target was not found")


def patch_abs(source: bytes, *, periodicity: int, chart_interval: int) -> bytes:
    validate_source(source)
    data = bytearray(source)

    _put_f32(data, "initial_equity", 100000)
    _put_i32(data, "trade_flags", 3)  # long and short
    _put_i32(data, "commission_mode", 3)  # per contract/share
    _put_f32(data, "commission_value", 3.76)
    _put_i32(data, "periodicity", periodicity)
    _put_i32(data, "futures_mode", 1)
    _put_i32(data, "round_lot_size", 1)
    _put_i32(data, "reverse_signal_forces_exit", 0)
    _put_i32(data, "allow_same_bar_exit", 1)
    _put_f32(data, "min_shares", 1)
    _put_i32(data, "use_prev_bar_equity", 1)
    _put_i32(data, "chart_interval", chart_interval)

    _put_i32(data, "is_enabled", 1)
    _put_date(data, "is_start", date(2009, 9, 28))
    _put_date(data, "is_end", date(2014, 9, 27))
    _put_date(data, "is_last", date(2023, 9, 27))
    _put_i32(data, "is_step", 12)
    _put_i32(data, "is_step_unit", 2)  # months
    _put_i32(data, "is_anchored", 0)  # rolling
    _put_i32(data, "is_last_uses_today", 0)

    _put_i32(data, "os_enabled", 1)
    _put_date(data, "os_start", date(2014, 9, 28))
    _put_date(data, "os_end", date(2015, 9, 27))
    _put_date(data, "os_last", date(2024, 9, 27))
    _put_i32(data, "os_step", 12)
    _put_i32(data, "os_step_unit", 2)  # months
    _put_i32(data, "os_anchored", 0)
    _put_i32(data, "os_last_uses_today", 0)

    return bytes(data)


def build(source_path: Path, output_dir: Path) -> list[Path]:
    source = source_path.read_bytes()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        (
            output_dir / "2026_Tools_Daily_WFA_Settings.ABS",
            patch_abs(source, periodicity=0, chart_interval=86400),
        ),
        (
            output_dir / "2026_Tools_Intraday_15m_WFA_Settings.ABS",
            patch_abs(source, periodicity=8, chart_interval=900),
        ),
    ]
    for path, content in outputs:
        path.write_bytes(content)
    return [path for path, _ in outputs]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_abs", type=Path, help="The supplied daily BROKSSE2 .ABS template")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ABS_Settings"),
        help="Destination directory (default: ABS_Settings)",
    )
    args = parser.parse_args(argv)
    for output in build(args.source_abs, args.output_dir):
        print(output)


if __name__ == "__main__":
    main()
