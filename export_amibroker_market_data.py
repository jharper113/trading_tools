#!/usr/bin/env python3
"""Create AmiBroker-ready daily and intraday CSV files."""

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


DEFAULT_MARKET_DATA_DIR = Path("data/market_data")
DEFAULT_TIMEZONE = "America/Detroit"
DEFAULT_INSTRUMENT_SETTINGS = (
    Path(__file__).resolve().parent
    / "amibroker_import"
    / "instrument_settings.csv"
)
PRICE_COLUMNS = ["open", "high", "low", "close"]
DAILY_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
]
INTRADAY_COLUMNS = [
    "ticker",
    "date",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
]
INSTRUMENT_SETTING_COLUMNS = [
    "ticker",
    "full_name",
    "point_value",
    "tick_size",
    "margin_deposit",
    "margin_as_of",
    "round_lot_size",
    "currency",
    "notes",
]


def _read_source(path):
    frame = pd.read_csv(path)
    required = {"timestamp", *PRICE_COLUMNS}
    missing = sorted(required - set(frame.columns))

    if missing:
        raise ValueError(
            f"{path} is missing required column(s): {', '.join(missing)}"
        )

    return frame


def _numeric_prices(frame):
    working = frame.copy()

    for column in [*PRICE_COLUMNS, "volume", "open_interest"]:
        if column not in working.columns:
            working[column] = pd.NA

        working[column] = pd.to_numeric(working[column], errors="coerce")

    working["volume"] = working["volume"].fillna(0)
    working["open_interest"] = working["open_interest"].fillna(0)
    return working


def _daily_rows(path):
    source = _read_source(path)
    working = _numeric_prices(source)
    source_dates = (
        working["date"]
        if "date" in working.columns
        else working["timestamp"]
    )
    dates = pd.to_datetime(source_dates, errors="coerce")
    valid = dates.notna() & working[PRICE_COLUMNS].notna().all(axis=1)
    exported = working.loc[valid, [*PRICE_COLUMNS, "volume", "open_interest"]].copy()
    exported.insert(0, "date", dates.loc[valid].dt.strftime("%Y-%m-%d"))
    exported.insert(0, "ticker", path.stem.upper())
    return exported[DAILY_COLUMNS], len(source)


def _intraday_rows(path, target_timezone):
    source = _read_source(path)
    working = _numeric_prices(source)
    timestamps = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    valid = timestamps.notna() & working[PRICE_COLUMNS].notna().all(axis=1)
    local_timestamps = timestamps.loc[valid].dt.tz_convert(target_timezone)
    exported = working.loc[valid, [*PRICE_COLUMNS, "volume", "open_interest"]].copy()
    exported.insert(0, "time", local_timestamps.dt.strftime("%H:%M:%S"))
    exported.insert(0, "date", local_timestamps.dt.strftime("%Y-%m-%d"))
    exported.insert(0, "ticker", path.stem.upper())
    return exported[INTRADAY_COLUMNS], len(source)


def _daily_chunk(source, ticker):
    working = _numeric_prices(source)
    source_dates = working["date"] if "date" in working.columns else working["timestamp"]
    dates = pd.to_datetime(source_dates, errors="coerce")
    valid = dates.notna() & working[PRICE_COLUMNS].notna().all(axis=1)
    exported = working.loc[valid, [*PRICE_COLUMNS, "volume", "open_interest"]].copy()
    exported.insert(0, "date", dates.loc[valid].dt.strftime("%Y-%m-%d"))
    exported.insert(0, "ticker", ticker)
    return exported[DAILY_COLUMNS]


def _intraday_chunk(source, ticker, target_timezone):
    working = _numeric_prices(source)
    timestamps = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    valid = timestamps.notna() & working[PRICE_COLUMNS].notna().all(axis=1)
    local_timestamps = timestamps.loc[valid].dt.tz_convert(target_timezone)
    exported = working.loc[valid, [*PRICE_COLUMNS, "volume", "open_interest"]].copy()
    exported.insert(0, "time", local_timestamps.dt.strftime("%H:%M:%S"))
    exported.insert(0, "date", local_timestamps.dt.strftime("%Y-%m-%d"))
    exported.insert(0, "ticker", ticker)
    return exported[INTRADAY_COLUMNS]


def _validate_source_columns(frame, path):
    missing = sorted({"timestamp", *PRICE_COLUMNS} - set(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")


def _stream_frequency(source_dir, frequency, temporary_path, timezone_name, chunk_size):
    columns = DAILY_COLUMNS if frequency == "daily" else INTRADAY_COLUMNS
    target_timezone = ZoneInfo(timezone_name)
    pd.DataFrame(columns=columns).to_csv(temporary_path, index=False)
    source_files = source_rows = exported_rows = 0
    market_tickers = set()
    symbol_stats = {}
    es_research_sessions = {"winter": False, "summer": False}

    for path in sorted(Path(source_dir).glob("*.csv")):
        source_files += 1
        ticker = path.stem.upper()
        reader = pd.read_csv(path, chunksize=chunk_size)
        saw_chunk = False
        for source in reader:
            saw_chunk = True
            _validate_source_columns(source, path)
            source_rows += len(source)
            if frequency == "daily":
                exported = _daily_chunk(source, ticker)
                keys = ["ticker", "date"]
            elif frequency == "5min":
                exported = _intraday_chunk(source, ticker, target_timezone)
                keys = ["ticker", "date", "time"]
            else:
                raise ValueError("AmiBroker export supports only daily and 5min")
            exported = exported.sort_values(keys, kind="stable").drop_duplicates(keys, keep="last")
            if len(exported):
                exported.to_csv(temporary_path, mode="a", header=False, index=False)
                exported_rows += len(exported)
                market_tickers.add(ticker)
                if frequency == "daily":
                    keys_for_range = exported["date"].astype(str)
                else:
                    keys_for_range = exported["date"].astype(str) + " " + exported["time"].astype(str)
                current = symbol_stats.setdefault(
                    ticker, {"ticker": ticker, "rows": 0, "first": None, "last": None}
                )
                current["rows"] += len(exported)
                chunk_first = keys_for_range.min()
                chunk_last = keys_for_range.max()
                current["first"] = chunk_first if current["first"] is None else min(current["first"], chunk_first)
                current["last"] = chunk_last if current["last"] is None else max(current["last"], chunk_last)
                if frequency == "5min" and ticker == "ES":
                    research_dates = pd.to_datetime(exported["date"], errors="coerce")
                    research_dates = research_dates[
                        (research_dates >= pd.Timestamp("2009-01-01"))
                        & (research_dates < pd.Timestamp("2019-01-01"))
                    ]
                    es_research_sessions["winter"] = bool(
                        es_research_sessions["winter"]
                        or research_dates.dt.month.isin([12, 1, 2]).any()
                    )
                    es_research_sessions["summer"] = bool(
                        es_research_sessions["summer"]
                        or research_dates.dt.month.isin([6, 7, 8]).any()
                    )
        if not saw_chunk:
            header = pd.read_csv(path, nrows=0)
            _validate_source_columns(header, path)

    stats = {
        "source_files": source_files,
        "source_rows": source_rows,
        "exported_rows": exported_rows,
        "skipped_rows": source_rows - exported_rows,
        "symbols": [symbol_stats[ticker] for ticker in sorted(symbol_stats)],
    }
    if frequency == "5min":
        stats["es_research_sessions"] = es_research_sessions
    return stats, market_tickers


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_amibroker_frame(source_dir, frequency, timezone_name=DEFAULT_TIMEZONE):
    source_dir = Path(source_dir)
    target_timezone = ZoneInfo(timezone_name)
    columns = DAILY_COLUMNS if frequency == "daily" else INTRADAY_COLUMNS
    frames = []
    source_rows = 0
    source_files = 0

    for path in sorted(source_dir.glob("*.csv")):
        source_files += 1

        if frequency == "daily":
            exported, row_count = _daily_rows(path)
        elif frequency == "5min":
            exported, row_count = _intraday_rows(path, target_timezone)
        else:
            raise ValueError("AmiBroker export supports only daily and 5min")

        source_rows += row_count

        if not exported.empty:
            frames.append(exported)

    combined = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=columns)
    )
    sort_columns = ["ticker", "date"]

    if frequency == "5min":
        sort_columns.append("time")

    combined = combined.sort_values(sort_columns, kind="stable")
    combined = combined.drop_duplicates(subset=sort_columns, keep="last")
    combined = combined.reset_index(drop=True)
    return combined[columns], {
        "source_files": source_files,
        "source_rows": source_rows,
        "exported_rows": len(combined),
        "skipped_rows": source_rows - len(combined),
    }


def _atomic_csv_write(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary_path, index=False)
    os.replace(temporary_path, path)


def _atomic_json_write(payload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(temporary_path, path)


def build_instrument_property_frames(
    market_data_dir,
    settings_path,
    market_tickers=None,
):
    market_data_dir = Path(market_data_dir)
    settings_path = Path(settings_path)
    settings = pd.read_csv(settings_path, dtype={"ticker": str})
    missing_columns = sorted(
        set(INSTRUMENT_SETTING_COLUMNS) - set(settings.columns)
    )

    if missing_columns:
        raise ValueError(
            f"{settings_path} is missing required column(s): "
            f"{', '.join(missing_columns)}"
        )

    settings = settings[INSTRUMENT_SETTING_COLUMNS].copy()
    settings["ticker"] = settings["ticker"].str.strip().str.upper()
    duplicate_tickers = sorted(
        settings.loc[settings["ticker"].duplicated(), "ticker"].unique()
    )

    if duplicate_tickers:
        raise ValueError(
            "duplicate AmiBroker instrument settings: "
            + ", ".join(duplicate_tickers)
        )

    if market_tickers is None:
        market_tickers = {
            path.stem.upper()
            for frequency in ("daily", "5min")
            for path in (market_data_dir / frequency).glob("*.csv")
            if path.stat().st_size > 0
        }

    market_tickers = sorted(set(market_tickers))
    missing_tickers = sorted(set(market_tickers) - set(settings["ticker"]))

    if missing_tickers:
        raise ValueError(
            "missing AmiBroker instrument settings: "
            + ", ".join(missing_tickers)
        )

    selected = settings.sort_values("ticker", kind="stable").reset_index(drop=True)
    details = selected[
        ["ticker", "full_name", "round_lot_size", "currency"]
    ].copy()
    point_values = selected.loc[
        pd.to_numeric(selected["point_value"], errors="coerce").notna(),
        ["ticker", "point_value"],
    ].copy()
    tick_sizes = selected.loc[
        pd.to_numeric(selected["tick_size"], errors="coerce").notna(),
        ["ticker", "tick_size"],
    ].copy()
    margin_amount_text = (
        selected["margin_deposit"].fillna("").astype(str).str.strip()
    )
    numeric_margin = pd.to_numeric(selected["margin_deposit"], errors="coerce")
    invalid_margin = margin_amount_text.ne("") & numeric_margin.isna()

    if invalid_margin.any():
        raise ValueError(
            "margin_deposit must be numeric: "
            + ", ".join(selected.loc[invalid_margin, "ticker"])
        )

    margin_amount_present = numeric_margin.notna()
    margin_date_present = (
        selected["margin_as_of"].fillna("").astype(str).str.strip().ne("")
    )
    incomplete_margin = margin_amount_present ^ margin_date_present

    if incomplete_margin.any():
        raise ValueError(
            "margin_deposit and margin_as_of must be supplied together: "
            + ", ".join(selected.loc[incomplete_margin, "ticker"])
        )

    valid_margin = margin_amount_present & margin_date_present
    margins = selected.loc[
        valid_margin,
        ["ticker", "margin_deposit", "margin_as_of"],
    ].copy()

    for frame, numeric_column in (
        (point_values, "point_value"),
        (tick_sizes, "tick_size"),
        (margins, "margin_deposit"),
    ):
        frame[numeric_column] = pd.to_numeric(frame[numeric_column])

    margin_dates = sorted(margins["margin_as_of"].astype(str).unique())
    return details, point_values, tick_sizes, margins, {
        "margin_as_of": margin_dates[0] if len(margin_dates) == 1 else None,
    }


def export_amibroker_market_data(
    market_data_dir=DEFAULT_MARKET_DATA_DIR,
    output_dir=None,
    timezone_name=DEFAULT_TIMEZONE,
    instrument_settings_path=DEFAULT_INSTRUMENT_SETTINGS,
    chunk_size=100_000,
):
    market_data_dir = Path(market_data_dir)
    output_dir = Path(output_dir or market_data_dir / "amibroker")
    daily_path = output_dir / "daily.csv"
    intraday_path = output_dir / "5min.csv"
    instrument_details_path = output_dir / "instrument_details.csv"
    point_values_path = output_dir / "point_values.csv"
    tick_sizes_path = output_dir / "tick_sizes.csv"
    margins_path = output_dir / "margins.csv"
    manifest_path = output_dir / "export_complete.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.unlink(missing_ok=True)
    final_paths = [daily_path, intraday_path, instrument_details_path, point_values_path, tick_sizes_path, margins_path]
    temporary_paths = {
        path: path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        for path in final_paths
    }

    try:
        daily_stats, daily_tickers = _stream_frequency(
            market_data_dir / "daily", "daily", temporary_paths[daily_path], timezone_name, chunk_size
        )
        intraday_stats, intraday_tickers = _stream_frequency(
            market_data_dir / "5min", "5min", temporary_paths[intraday_path], timezone_name, chunk_size
        )
        (
            instrument_details,
            point_values,
            tick_sizes,
            margins,
            instrument_stats,
        ) = build_instrument_property_frames(
            market_data_dir,
            instrument_settings_path,
            market_tickers=daily_tickers | intraday_tickers,
        )
        for frame, path in (
            (instrument_details, instrument_details_path),
            (point_values, point_values_path),
            (tick_sizes, tick_sizes_path),
            (margins, margins_path),
        ):
            frame.to_csv(temporary_paths[path], index=False)
        for path in final_paths:
            os.replace(temporary_paths[path], path)
    except Exception:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        raise

    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "timezone": timezone_name,
        "daily": {"file": daily_path.name, "sha256": _sha256(daily_path), **daily_stats},
        "intraday": {"file": intraday_path.name, "sha256": _sha256(intraday_path), **intraday_stats},
        "instrument_properties": {
            "details_file": instrument_details_path.name,
            "details_rows": len(instrument_details),
            "point_values_file": point_values_path.name,
            "point_values_rows": len(point_values),
            "tick_sizes_file": tick_sizes_path.name,
            "tick_sizes_rows": len(tick_sizes),
            "margins_file": margins_path.name,
            "margins_rows": len(margins),
            **instrument_stats,
        },
        "files": {
            path.name: _sha256(path)
            for path in final_paths
        },
    }
    _atomic_json_write(manifest, manifest_path)
    return {
        "daily_path": daily_path,
        "intraday_path": intraday_path,
        "instrument_details_path": instrument_details_path,
        "point_values_path": point_values_path,
        "tick_sizes_path": tick_sizes_path,
        "margins_path": margins_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create combined AmiBroker import files from normalized daily "
            "and 5-minute market data."
        )
    )
    parser.add_argument(
        "--market-data-dir",
        type=Path,
        default=DEFAULT_MARKET_DATA_DIR,
        help="Normalized market-data root. Default: data/market_data",
    )
    parser.add_argument(
        "--instrument-settings",
        type=Path,
        default=DEFAULT_INSTRUMENT_SETTINGS,
        help=(
            "Maintained instrument property table. Default: "
            "amibroker_import/instrument_settings.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Default: <market-data-dir>/amibroker",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone for intraday output. Default: America/Detroit",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    result = export_amibroker_market_data(
        market_data_dir=args.market_data_dir,
        output_dir=args.output_dir,
        timezone_name=args.timezone,
        instrument_settings_path=args.instrument_settings,
    )
    manifest = result["manifest"]
    print(
        f"Wrote {manifest['daily']['exported_rows']:,} daily rows to "
        f"{result['daily_path']}"
    )
    print(
        f"Wrote {manifest['intraday']['exported_rows']:,} 5-minute rows "
        f"in {manifest['timezone']} to {result['intraday_path']}"
    )
    properties = manifest["instrument_properties"]
    print(
        "Wrote AmiBroker instrument properties: "
        f"{properties['details_rows']} details, "
        f"{properties['point_values_rows']} point values, "
        f"{properties['tick_sizes_rows']} tick sizes, and "
        f"{properties['margins_rows']} dated margins."
    )
    print(f"Wrote completion manifest: {result['manifest_path']}")


if __name__ == "__main__":
    main()
