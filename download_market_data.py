import argparse
import base64
import csv
import gzip
import getpass
import json
import os
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_DIR = Path("data/market_data")
DEFAULT_PROVIDER = "csv"
SUPPORTED_FREQUENCIES = {"daily", "5min", "60min"}
REVIEWED_BARS_FILE = "reviewed_bars.csv"
QUALITY_DIR = "quality"
INTEGRITY_REPORT_FILE = "market_data_integrity.csv"
DAILY_INTRADAY_REPORT_FILE = "daily_intraday_quality.csv"
DAILY_INTRADAY_FIX_FILE = "daily_intraday_fix_candidates.csv"
QUALITY_SUMMARY_FILE = "quality_summary.csv"
SCHWAB_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_REDIRECT_URI = "https://developer.schwab.com/oauth2-redirect.html"
SCHWAB_MAX_DAILY_YEARS = 20
SCHWAB_MAX_INTRADAY_DAYS = 10
DEFAULT_SCHWAB_TOKEN_FILE = Path("data/market_data/schwab_tokens.json")


FUTURES_PRODUCTS = {
    "/ES": {
        "name": "E-mini S&P 500",
        "exchange": "CME",
        "category": "equity_index",
    },
    "/NQ": {
        "name": "E-mini Nasdaq-100",
        "exchange": "CME",
        "category": "equity_index",
    },
    "/RTY": {
        "name": "E-mini Russell 2000",
        "exchange": "CME",
        "category": "equity_index",
    },
    "/YM": {
        "name": "E-mini Dow",
        "exchange": "CBOT",
        "category": "equity_index",
    },
    "/EMD": {
        "name": "E-mini S&P MidCap 400",
        "exchange": "CME",
        "category": "equity_index",
    },
    "/NKD": {
        "name": "Nikkei 225 (USD)",
        "exchange": "CME",
        "category": "equity_index",
    },
    "/VX": {
        "name": "CBOE Volatility Index futures",
        "exchange": "CFE",
        "category": "equity_index",
    },
    "/ZB": {
        "name": "30-Year U.S. Treasury Bond",
        "exchange": "CBOT",
        "category": "rates",
    },
    "/ZN": {
        "name": "10-Year T-Note",
        "exchange": "CBOT",
        "category": "rates",
    },
    "/ZF": {
        "name": "5-Year T-Note",
        "exchange": "CBOT",
        "category": "rates",
    },
    "/ZT": {
        "name": "2-Year T-Note",
        "exchange": "CBOT",
        "category": "rates",
    },
    "/UB": {
        "name": "Ultra U.S. Treasury Bond",
        "exchange": "CBOT",
        "category": "rates",
    },
    "/GE": {
        "name": "Three-Month Eurodollar (retired)",
        "exchange": "CME",
        "category": "rates",
    },
    "/6E": {
        "name": "Euro FX",
        "exchange": "CME",
        "category": "currency",
    },
    "/6J": {
        "name": "Japanese Yen",
        "exchange": "CME",
        "category": "currency",
    },
    "/6B": {
        "name": "British Pound",
        "exchange": "CME",
        "category": "currency",
    },
    "/6A": {
        "name": "Australian Dollar",
        "exchange": "CME",
        "category": "currency",
    },
    "/6C": {
        "name": "Canadian Dollar",
        "exchange": "CME",
        "category": "currency",
    },
    "/6S": {
        "name": "Swiss Franc",
        "exchange": "CME",
        "category": "currency",
    },
    "/6M": {
        "name": "Mexican Peso",
        "exchange": "CME",
        "category": "currency",
    },
    "/6N": {
        "name": "New Zealand Dollar",
        "exchange": "CME",
        "category": "currency",
    },
    "/GC": {
        "name": "Gold",
        "exchange": "COMEX",
        "category": "metal",
    },
    "/SI": {
        "name": "Silver",
        "exchange": "COMEX",
        "category": "metal",
    },
    "/HG": {
        "name": "Copper",
        "exchange": "COMEX",
        "category": "metal",
    },
    "/PL": {
        "name": "Platinum",
        "exchange": "NYMEX",
        "category": "metal",
    },
    "/PA": {
        "name": "Palladium",
        "exchange": "NYMEX",
        "category": "metal",
    },
    "/CL": {
        "name": "WTI Crude Oil",
        "exchange": "NYMEX",
        "category": "energy",
    },
    "/NG": {
        "name": "Henry Hub Natural Gas",
        "exchange": "NYMEX",
        "category": "energy",
    },
    "/RB": {
        "name": "RBOB Gasoline",
        "exchange": "NYMEX",
        "category": "energy",
    },
    "/HO": {
        "name": "NY Harbor ULSD",
        "exchange": "NYMEX",
        "category": "energy",
    },
    "/BZ": {
        "name": "Brent Crude Oil",
        "exchange": "NYMEX",
        "category": "energy",
    },
    "/BTC": {
        "name": "Bitcoin",
        "exchange": "CME",
        "category": "crypto",
    },
    "/ETH": {
        "name": "Ether",
        "exchange": "CME",
        "category": "crypto",
    },
    "/MBT": {
        "name": "Micro Bitcoin",
        "exchange": "CME",
        "category": "crypto",
    },
    "/MET": {
        "name": "Micro Ether",
        "exchange": "CME",
        "category": "crypto",
    },
    "/SOL": {
        "name": "Solana",
        "exchange": "CME",
        "category": "crypto",
    },
    "/MSL": {
        "name": "Micro SOL",
        "exchange": "CME",
        "category": "crypto",
    },
    "/XRP": {
        "name": "XRP",
        "exchange": "CME",
        "category": "crypto",
    },
    "/MXP": {
        "name": "Micro XRP",
        "exchange": "CME",
        "category": "crypto",
    },
    "/MCA": {
        "name": "Micro ADA",
        "exchange": "CME",
        "category": "crypto",
    },
    "/ZC": {
        "name": "Corn",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/ZS": {
        "name": "Soybeans",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/ZM": {
        "name": "Soybean Meal",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/ZL": {
        "name": "Soybean Oil",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/ZW": {
        "name": "Chicago SRW Wheat",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/LE": {
        "name": "Live Cattle",
        "exchange": "CME",
        "category": "agriculture",
    },
    "/HE": {
        "name": "Lean Hogs",
        "exchange": "CME",
        "category": "agriculture",
    },
    "/GF": {
        "name": "Feeder Cattle",
        "exchange": "CME",
        "category": "agriculture",
    },
    "/KE": {
        "name": "KC Hard Red Winter Wheat",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/ZO": {
        "name": "Oats",
        "exchange": "CBOT",
        "category": "agriculture",
    },
    "/KC": {
        "name": "Coffee",
        "exchange": "ICE",
        "category": "soft",
    },
    "/SB": {
        "name": "Sugar No. 11",
        "exchange": "ICE",
        "category": "soft",
    },
    "/CT": {
        "name": "Cotton No. 2",
        "exchange": "ICE",
        "category": "soft",
    },
    "/CC": {
        "name": "Cocoa",
        "exchange": "ICE",
        "category": "soft",
    },
}
EQUITY_PRODUCTS = {
    "SPY": {
        "name": "SPDR S&P 500 ETF",
        "exchange": "NYSE Arca",
        "category": "equity_index",
    },
}
LEGACY_PRODUCTS = {
    "AUDUSD": {
        "name": "Australian Dollar / U.S. Dollar spot FX",
        "category": "forex",
        "schwab_symbol": "AUD/USD",
    },
    "EURJPY": {
        "name": "Euro / Japanese Yen spot FX",
        "category": "forex",
        "schwab_symbol": "EUR/JPY",
    },
    "EURUSD": {
        "name": "Euro / U.S. Dollar spot FX",
        "category": "forex",
        "schwab_symbol": "EUR/USD",
    },
    "GBPUSD": {
        "name": "British Pound / U.S. Dollar spot FX",
        "category": "forex",
        "schwab_symbol": "GBP/USD",
    },
    "LB___CCB": {
        "name": "Random Length Lumber (retired)",
        "category": "agriculture",
        "schwab_symbol": "/LBS",
    },
    "NZDUSD": {
        "name": "New Zealand Dollar / U.S. Dollar spot FX",
        "category": "forex",
        "schwab_symbol": "NZD/USD",
    },
    "RF___CCB": {
        "name": "Mini Russell 1000 (legacy ICE contract)",
        "category": "equity_index",
        "schwab_symbol": "/RF",
    },
    "USDCAD": {
        "name": "U.S. Dollar / Canadian Dollar spot FX",
        "category": "forex",
        "schwab_symbol": "USD/CAD",
    },
    "USDCHF": {
        "name": "U.S. Dollar / Swiss Franc spot FX",
        "category": "forex",
        "schwab_symbol": "USD/CHF",
    },
    "USDJPY": {
        "name": "U.S. Dollar / Japanese Yen spot FX",
        "category": "forex",
        "schwab_symbol": "USD/JPY",
    },
}
DEFAULT_SYMBOLS = [
    *FUTURES_PRODUCTS.keys(),
    *EQUITY_PRODUCTS.keys(),
    *LEGACY_PRODUCTS.keys(),
]


CANONICAL_COLUMNS = [
    "timestamp",
    "date",
    "symbol",
    "frequency",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "source",
    "retrieved_at",
]
REVIEWED_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "selected_source",
    "reviewed_at",
]
INTEGRITY_COLUMNS = [
    "symbol",
    "frequency",
    "timestamp",
    "date",
    "issue_type",
    "field",
    "value",
    "expected",
    "severity",
    "auto_fix",
]
DAILY_INTRADAY_COLUMNS = [
    "symbol",
    "date",
    "field",
    "daily_value",
    "intraday_value",
    "abs_diff",
    "pct_diff",
    "threshold_pct",
    "intraday_bars",
    "severity",
    "auto_fix",
]
DAILY_FIX_CANDIDATE_COLUMNS = CANONICAL_COLUMNS + ["intraday_bars"]


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def send_desktop_notification(
    title,
    message,
    urgency="normal",
    notifier_path=None,
    runner=None,
    environment=None,
):
    notifier_path = notifier_path or shutil.which("notify-send")

    if not notifier_path:
        return False

    notification_environment = dict(
        os.environ if environment is None else environment
    )
    runtime_dir = f"/run/user/{os.getuid()}"
    notification_environment.setdefault("DISPLAY", ":0")
    notification_environment.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    notification_environment.setdefault(
        "DBUS_SESSION_BUS_ADDRESS",
        f"unix:path={runtime_dir}/bus",
    )
    runner = runner or subprocess.run

    try:
        result = runner(
            [
                notifier_path,
                f"--urgency={urgency}",
                title,
                message,
            ],
            env=notification_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def notify_best_effort(notifier, **notification):
    try:
        notifier(**notification)
    except Exception:
        return False

    return True


def eta_text(start_time, completed, total):
    if completed <= 0 or total <= 0:
        return "estimating"

    elapsed = time.monotonic() - start_time
    remaining = max(0, total - completed)
    seconds_remaining = elapsed / completed * remaining

    return elapsed_text(seconds_remaining)


def normalize_symbol(symbol):
    symbol = str(symbol).strip().upper()

    if not symbol:
        raise ValueError("Symbol cannot be blank")

    if symbol.startswith("/"):
        return symbol

    futures_symbol = f"/{symbol}"

    if futures_symbol in FUTURES_PRODUCTS:
        return futures_symbol

    return symbol


def safe_symbol_filename(symbol):
    return normalize_symbol(symbol).replace("/", "").replace(" ", "_")


def schwab_provider_symbol(symbol):
    symbol = normalize_symbol(symbol)
    product = LEGACY_PRODUCTS.get(symbol)

    if product is not None:
        return product["schwab_symbol"]

    return symbol


def normalize_frequency(frequency):
    frequency = str(frequency).strip().lower()

    aliases = {
        "day": "daily",
        "1d": "daily",
        "1day": "daily",
        "5m": "5min",
        "5-min": "5min",
        "5-minute": "5min",
        "5minute": "5min",
        "60m": "60min",
        "60-min": "60min",
        "60-minute": "60min",
        "60minute": "60min",
        "1h": "60min",
        "1hr": "60min",
        "hour": "60min",
        "hourly": "60min",
    }
    frequency = aliases.get(frequency, frequency)

    if frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError(
            f"Unsupported frequency '{frequency}'. "
            f"Choose one of: {sorted(SUPPORTED_FREQUENCIES)}"
        )

    return frequency


def output_file_for(output_dir, symbol, frequency):
    return (
        Path(output_dir)
        / normalize_frequency(frequency)
        / f"{safe_symbol_filename(symbol)}.csv"
    )


def empty_bars_frame():
    return pd.DataFrame(columns=CANONICAL_COLUMNS)


def reviewed_bars_file_for(output_dir):
    return Path(output_dir) / REVIEWED_BARS_FILE


def empty_reviewed_bars_frame():
    return pd.DataFrame(columns=REVIEWED_COLUMNS)


def load_reviewed_bars(output_dir):
    path = reviewed_bars_file_for(output_dir)

    if not path.exists():
        return empty_reviewed_bars_frame()

    reviewed = pd.read_csv(path)

    for column in REVIEWED_COLUMNS:
        if column not in reviewed.columns:
            reviewed[column] = pd.NA

    reviewed["symbol"] = reviewed["symbol"].map(normalize_symbol)
    reviewed["frequency"] = reviewed["frequency"].map(normalize_frequency)
    reviewed["comparison_key"] = reviewed["comparison_key"].astype(str)

    return reviewed[REVIEWED_COLUMNS].copy()


def comparison_keys_for_bars(bars):
    if bars is None or len(bars) == 0:
        return pd.Series(dtype=object)

    timestamps = pd.to_datetime(
        bars["timestamp"],
        utc=True,
        errors="coerce",
    )
    frequencies = bars["frequency"].astype(str).str.lower()
    daily_keys = timestamps.dt.date.astype(str)
    intraday_keys = timestamps.dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return pd.Series(
        np.where(frequencies == "daily", daily_keys, intraday_keys),
        index=bars.index,
    )


def reviewed_key_set(reviewed_bars):
    if reviewed_bars is None or len(reviewed_bars) == 0:
        return set()

    reviewed = reviewed_bars.copy()
    reviewed["symbol"] = reviewed["symbol"].map(normalize_symbol)
    reviewed["frequency"] = reviewed["frequency"].map(normalize_frequency)
    reviewed["comparison_key"] = reviewed["comparison_key"].astype(str)

    return set(
        zip(
            reviewed["symbol"],
            reviewed["frequency"],
            reviewed["comparison_key"],
        )
    )


def normalize_bar_frame(
    bars,
    symbol,
    frequency,
    source,
    retrieved_at=None,
):
    if bars is None or len(bars) == 0:
        return empty_bars_frame()

    working = pd.DataFrame(bars).copy()
    column_map = {
        "datetime": "timestamp",
        "dateTime": "timestamp",
        "time": "timestamp",
        "symbol": "symbol",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "openInterest": "open_interest",
        "open_interest": "open_interest",
    }
    working = working.rename(
        columns={
            column: column_map[column]
            for column in working.columns
            if column in column_map
        }
    )

    if "timestamp" not in working.columns:
        raise ValueError("Market data is missing a timestamp/datetime column")

    timestamp = working["timestamp"]

    if pd.api.types.is_numeric_dtype(timestamp):
        max_value = pd.to_numeric(timestamp, errors="coerce").max()
        unit = "ms" if max_value and max_value > 10_000_000_000 else "s"
        working["timestamp"] = pd.to_datetime(
            timestamp,
            unit=unit,
            utc=True,
            errors="coerce",
        )
    else:
        working["timestamp"] = pd.to_datetime(
            timestamp,
            utc=True,
            errors="coerce",
        )

    working = working[working["timestamp"].notna()].copy()
    working["date"] = working["timestamp"].dt.date.astype(str)
    working["timestamp"] = working["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    working["symbol"] = normalize_symbol(symbol)
    working["frequency"] = normalize_frequency(frequency)
    working["source"] = source
    working["retrieved_at"] = (
        retrieved_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    for column in ["open", "high", "low", "close", "volume", "open_interest"]:
        if column not in working.columns:
            working[column] = pd.NA

        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        )

    return working[CANONICAL_COLUMNS].copy()


def append_market_data(existing, incoming, reviewed_bars=None):
    # Import lazily to avoid a module cycle: Kibot normalization reuses the
    # canonical schema defined in this module.
    from kibot_market_data import merge_market_data_sources

    return merge_market_data_sources(
        [existing, incoming],
        reviewed_bars=reviewed_bars,
    ).rows


def build_integrity_report(bars):
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=INTEGRITY_COLUMNS)

    working = bars.copy()
    timestamps = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )
    rows = []

    for index, row in working.iterrows():
        symbol = normalize_symbol(row.get("symbol"))
        frequency = normalize_frequency(row.get("frequency"))
        timestamp = (
            timestamps.loc[index].strftime("%Y-%m-%dT%H:%M:%SZ")
            if not pd.isna(timestamps.loc[index])
            else ""
        )
        date = str(row.get("date", ""))[:10]
        prices = {
            field: pd.to_numeric(row.get(field), errors="coerce")
            for field in ["open", "high", "low", "close"]
        }

        for field, value in prices.items():
            if pd.isna(value):
                rows.append(
                    {
                        "symbol": symbol,
                        "frequency": frequency,
                        "timestamp": timestamp,
                        "date": date,
                        "issue_type": "missing_ohlc",
                        "field": field,
                        "value": value,
                        "expected": "numeric OHLC value",
                        "severity": "error",
                        "auto_fix": "drop_bar",
                    }
                )
            elif value <= 0:
                rows.append(
                    {
                        "symbol": symbol,
                        "frequency": frequency,
                        "timestamp": timestamp,
                        "date": date,
                        "issue_type": "non_positive_price",
                        "field": field,
                        "value": value,
                        "expected": "> 0",
                        "severity": "error",
                        "auto_fix": "drop_bar",
                    }
                )

        clean_prices = [
            value
            for value in prices.values()
            if pd.notna(value) and value > 0
        ]

        if len(clean_prices) != 4:
            continue

        expected_high = max(clean_prices)
        expected_low = min(clean_prices)

        if prices["high"] < expected_high:
            rows.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "timestamp": timestamp,
                    "date": date,
                    "issue_type": "high_below_ohlc",
                    "field": "high",
                    "value": prices["high"],
                    "expected": expected_high,
                    "severity": "error",
                    "auto_fix": "set_high_to_max_ohlc",
                }
            )

        if prices["low"] > expected_low:
            rows.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "timestamp": timestamp,
                    "date": date,
                    "issue_type": "low_above_ohlc",
                    "field": "low",
                    "value": prices["low"],
                    "expected": expected_low,
                    "severity": "error",
                    "auto_fix": "set_low_to_min_ohlc",
                }
            )

    if not rows:
        return pd.DataFrame(columns=INTEGRITY_COLUMNS)

    return pd.DataFrame(rows).reindex(columns=INTEGRITY_COLUMNS)


def auto_fix_integrity_issues(bars):
    if bars is None or len(bars) == 0:
        return empty_bars_frame(), pd.DataFrame(columns=INTEGRITY_COLUMNS)

    fixed = bars.copy()
    before_report = build_integrity_report(fixed)

    for column in ["open", "high", "low", "close"]:
        fixed[column] = pd.to_numeric(fixed[column], errors="coerce")

    invalid_mask = fixed[["open", "high", "low", "close"]].isna().any(axis=1)
    invalid_mask = invalid_mask | (fixed[["open", "high", "low", "close"]] <= 0).any(axis=1)
    fixed = fixed[~invalid_mask].copy()

    if len(fixed) > 0:
        fixed["high"] = fixed[["open", "high", "low", "close"]].max(axis=1)
        fixed["low"] = fixed[["open", "high", "low", "close"]].min(axis=1)

    return fixed[CANONICAL_COLUMNS].reset_index(drop=True), before_report


def aggregate_intraday_to_daily(intraday_bars):
    if intraday_bars is None or len(intraday_bars) == 0:
        return pd.DataFrame(columns=[
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "intraday_bars",
        ])

    working = intraday_bars.copy()
    working["timestamp"] = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )
    working = working[working["timestamp"].notna()].copy()
    working = working.sort_values(["symbol", "timestamp"])

    for column in ["open", "high", "low", "close"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")

    working = working.dropna(subset=["open", "high", "low", "close"])
    working["symbol"] = working["symbol"].map(normalize_symbol)
    working["date"] = working["timestamp"].dt.date.astype(str)

    if len(working) == 0:
        return pd.DataFrame(columns=[
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "intraday_bars",
        ])

    aggregated = working.groupby(["symbol", "date"], as_index=False).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        intraday_bars=("close", "size"),
    )

    return aggregated


def build_daily_intraday_quality_report(
    daily_bars,
    intraday_bars,
    threshold_pct=0.25,
):
    if daily_bars is None or len(daily_bars) == 0:
        return pd.DataFrame(columns=DAILY_INTRADAY_COLUMNS)

    intraday_daily = aggregate_intraday_to_daily(intraday_bars)

    if len(intraday_daily) == 0:
        return pd.DataFrame(columns=DAILY_INTRADAY_COLUMNS)

    daily = daily_bars.copy()
    daily["symbol"] = daily["symbol"].map(normalize_symbol)
    daily["date"] = pd.to_datetime(
        daily["timestamp"],
        utc=True,
        errors="coerce",
    ).dt.date.astype(str)

    for column in ["open", "high", "low", "close"]:
        daily[column] = pd.to_numeric(daily[column], errors="coerce")

    merged = pd.merge(
        daily[["symbol", "date", "open", "high", "low", "close"]],
        intraday_daily,
        on=["symbol", "date"],
        how="inner",
        suffixes=("_daily", "_intraday"),
    )
    rows = []

    for _, row in merged.iterrows():
        for field in ["open", "high", "low", "close"]:
            daily_value = row[f"{field}_daily"]
            intraday_value = row[f"{field}_intraday"]

            if pd.isna(daily_value) or pd.isna(intraday_value):
                pct_diff = np.nan
                abs_diff = np.nan
            else:
                abs_diff = daily_value - intraday_value
                denominator = abs(intraday_value)
                pct_diff = (
                    abs(abs_diff) / denominator * 100
                    if denominator != 0
                    else np.nan
                )

            if pd.isna(pct_diff) or pct_diff <= threshold_pct:
                continue

            rows.append(
                {
                    "symbol": row["symbol"],
                    "date": row["date"],
                    "field": field,
                    "daily_value": daily_value,
                    "intraday_value": intraday_value,
                    "abs_diff": abs_diff,
                    "pct_diff": pct_diff,
                    "threshold_pct": threshold_pct,
                    "intraday_bars": row["intraday_bars"],
                    "severity": "warning",
                    "auto_fix": "candidate_daily_from_intraday",
                }
            )

    if not rows:
        return pd.DataFrame(columns=DAILY_INTRADAY_COLUMNS)

    return pd.DataFrame(rows).reindex(columns=DAILY_INTRADAY_COLUMNS)


def build_daily_intraday_fix_candidates(daily_bars, intraday_bars, threshold_pct=0.25):
    report = build_daily_intraday_quality_report(
        daily_bars=daily_bars,
        intraday_bars=intraday_bars,
        threshold_pct=threshold_pct,
    )

    if len(report) == 0:
        return pd.DataFrame(columns=DAILY_FIX_CANDIDATE_COLUMNS)

    candidate_keys = set(zip(report["symbol"], report["date"]))
    intraday_daily = aggregate_intraday_to_daily(intraday_bars)
    candidates = intraday_daily[
        [
            (row["symbol"], row["date"]) in candidate_keys
            for _, row in intraday_daily.iterrows()
        ]
    ].copy()

    if len(candidates) == 0:
        return pd.DataFrame(columns=DAILY_FIX_CANDIDATE_COLUMNS)

    candidates["timestamp"] = pd.to_datetime(
        candidates["date"],
        utc=True,
    ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    candidates["frequency"] = "daily"
    candidates["volume"] = pd.NA
    candidates["open_interest"] = pd.NA
    candidates["source"] = "candidate_from_5min"
    candidates["retrieved_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return candidates[DAILY_FIX_CANDIDATE_COLUMNS].reset_index(drop=True)


def apply_daily_intraday_fix_candidates(
    output_dir,
    candidates,
    min_intraday_bars=50,
):
    if candidates is None or len(candidates) == 0:
        return {"updated_bars": 0, "updated_files": 0}

    output_dir = Path(output_dir)
    working = candidates.copy()
    working["intraday_bars"] = pd.to_numeric(
        working.get("intraday_bars"),
        errors="coerce",
    ).fillna(0)
    working = working[working["intraday_bars"] >= min_intraday_bars].copy()

    if len(working) == 0:
        return {"updated_bars": 0, "updated_files": 0}

    updated_bars = 0
    updated_files = 0

    for symbol, group in working.groupby("symbol"):
        output_path = output_file_for(output_dir, symbol, "daily")

        if not output_path.exists():
            continue

        existing = pd.read_csv(output_path)
        existing["timestamp"] = pd.to_datetime(
            existing["timestamp"],
            utc=True,
            errors="coerce",
        )
        existing = existing[existing["timestamp"].notna()].copy()
        existing["_date"] = existing["timestamp"].dt.date.astype(str)
        group = group.copy()
        group["_date"] = pd.to_datetime(
            group["timestamp"],
            utc=True,
            errors="coerce",
        ).dt.date.astype(str)
        fix_dates = set(group["_date"])
        existing = existing[~existing["_date"].isin(fix_dates)].drop(
            columns=["_date"]
        )
        replacement = group[CANONICAL_COLUMNS]
        combined = append_market_data(
            existing,
            replacement,
            reviewed_bars=load_reviewed_bars(output_dir),
        )
        combined.to_csv(output_path, index=False)
        updated_bars += len(replacement)
        updated_files += 1

    return {
        "updated_bars": updated_bars,
        "updated_files": updated_files,
    }


def save_market_data(output_dir, symbol, frequency, bars):
    output_path = output_file_for(output_dir, symbol, frequency)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing = (
        pd.read_csv(output_path)
        if output_path.exists()
        else empty_bars_frame()
    )
    # Preserve the historical repair behavior for lower-priority local and
    # CSV sources. Preferred vendor rows remain unmodified so an invalid
    # Schwab row can fall back to Kibot instead of being manufactured into a
    # valid-looking replacement.
    repaired_inputs = []
    for frame in (existing, bars):
        if frame is None or len(frame) == 0:
            repaired_inputs.append(frame)
            continue
        frame = frame.copy()
        preferred = frame["source"].astype(str).str.lower().isin(
            {"schwab", "kibot"}
        )
        lower_priority, _ = auto_fix_integrity_issues(frame[~preferred])
        repaired_inputs.append(
            pd.concat([frame[preferred], lower_priority], ignore_index=True)
        )
    existing, bars = repaired_inputs
    reviewed_bars = load_reviewed_bars(output_dir)
    combined = append_market_data(existing, bars, reviewed_bars=reviewed_bars)
    combined, _ = auto_fix_integrity_issues(combined)
    combined.to_csv(output_path, index=False)

    return output_path, len(combined)


def write_symbol_manifest(output_dir, symbols):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "symbols.csv"

    with manifest_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "symbol",
                "name",
                "exchange",
                "category",
            ],
        )
        writer.writeheader()

        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            product = (
                FUTURES_PRODUCTS.get(normalized)
                or EQUITY_PRODUCTS.get(normalized)
                or {}
            )
            writer.writerow(
                {
                    "symbol": normalized,
                    "name": product.get("name", ""),
                    "exchange": product.get("exchange", ""),
                    "category": product.get("category", ""),
                }
            )

    return manifest_path


def load_saved_market_data(output_dir, symbol, frequency):
    path = output_file_for(output_dir, symbol, frequency)

    if not path.exists():
        return empty_bars_frame()

    return pd.read_csv(path)


def write_quality_reports(
    output_dir,
    symbols,
    frequencies=None,
    threshold_pct=0.25,
    apply_daily_fixes=False,
    min_intraday_bars=50,
):
    output_dir = Path(output_dir)
    frequencies = frequencies or ["daily", "5min"]
    quality_dir = output_dir / QUALITY_DIR
    quality_dir.mkdir(parents=True, exist_ok=True)
    integrity_reports = []
    daily_intraday_reports = []
    fix_candidates = []
    summary_rows = []

    for symbol in symbols:
        normalized = normalize_symbol(symbol)
        daily = load_saved_market_data(output_dir, normalized, "daily")
        intraday = load_saved_market_data(output_dir, normalized, "5min")

        for frequency in frequencies:
            bars = load_saved_market_data(output_dir, normalized, frequency)
            integrity = build_integrity_report(bars)
            integrity_reports.append(integrity)
            summary_rows.append(
                {
                    "symbol": normalized,
                    "frequency": frequency,
                    "check": "ohlc_integrity",
                    "bars_checked": len(bars),
                    "issues": len(integrity),
                    "status": "passed" if len(integrity) == 0 else "failed",
                }
            )

        daily_intraday = build_daily_intraday_quality_report(
            daily_bars=daily,
            intraday_bars=intraday,
            threshold_pct=threshold_pct,
        )
        candidates = build_daily_intraday_fix_candidates(
            daily_bars=daily,
            intraday_bars=intraday,
            threshold_pct=threshold_pct,
        )
        daily_intraday_reports.append(daily_intraday)
        fix_candidates.append(candidates)
        summary_rows.append(
            {
                "symbol": normalized,
                "frequency": "daily_vs_5min",
                "check": "daily_intraday_consistency",
                "bars_checked": len(daily),
                "issues": len(daily_intraday),
                "status": (
                    "passed" if len(daily_intraday) == 0 else "review_candidates"
                ),
            }
        )

    integrity_report = (
        pd.concat(integrity_reports, ignore_index=True)
        if integrity_reports
        else pd.DataFrame(columns=INTEGRITY_COLUMNS)
    )
    daily_intraday_report = (
        pd.concat(daily_intraday_reports, ignore_index=True)
        if daily_intraday_reports
        else pd.DataFrame(columns=DAILY_INTRADAY_COLUMNS)
    )
    daily_fix_candidates = (
        pd.concat(fix_candidates, ignore_index=True)
        if fix_candidates
        else pd.DataFrame(columns=DAILY_FIX_CANDIDATE_COLUMNS)
    )
    apply_result = {}

    if apply_daily_fixes:
        apply_result = apply_daily_intraday_fix_candidates(
            output_dir=output_dir,
            candidates=daily_fix_candidates,
            min_intraday_bars=min_intraday_bars,
        )

        if apply_result.get("updated_bars", 0) > 0:
            refreshed = write_quality_reports(
                output_dir=output_dir,
                symbols=symbols,
                frequencies=frequencies,
                threshold_pct=threshold_pct,
                apply_daily_fixes=False,
                min_intraday_bars=min_intraday_bars,
            )
            refreshed["apply_result"] = apply_result
            return refreshed

    summary = pd.DataFrame(summary_rows)

    integrity_path = quality_dir / INTEGRITY_REPORT_FILE
    daily_intraday_path = quality_dir / DAILY_INTRADAY_REPORT_FILE
    fix_candidates_path = quality_dir / DAILY_INTRADAY_FIX_FILE
    summary_path = quality_dir / QUALITY_SUMMARY_FILE

    integrity_report.to_csv(integrity_path, index=False)
    daily_intraday_report.to_csv(daily_intraday_path, index=False)
    daily_fix_candidates.to_csv(fix_candidates_path, index=False)
    summary.to_csv(summary_path, index=False)

    return {
        "summary": summary,
        "integrity": integrity_report,
        "daily_intraday": daily_intraday_report,
        "daily_fix_candidates": daily_fix_candidates,
        "apply_result": apply_result,
        "summary_path": summary_path,
        "integrity_path": integrity_path,
        "daily_intraday_path": daily_intraday_path,
        "fix_candidates_path": fix_candidates_path,
    }


def repair_saved_integrity(output_dir, symbols, frequencies=None):
    output_dir = Path(output_dir)
    frequencies = frequencies or ["daily", "5min"]
    repaired_bars = 0
    repaired_files = 0
    issue_reports = []

    for symbol in symbols:
        for frequency in frequencies:
            path = output_file_for(output_dir, symbol, frequency)

            if not path.exists():
                continue

            bars = pd.read_csv(path)
            fixed, report = auto_fix_integrity_issues(bars)
            issue_reports.append(report)

            if len(report) == 0:
                continue

            fixed.to_csv(path, index=False)
            repaired_bars += len(report)
            repaired_files += 1

    issue_report = (
        pd.concat(issue_reports, ignore_index=True)
        if issue_reports
        else pd.DataFrame(columns=INTEGRITY_COLUMNS)
    )

    return {
        "repaired_issue_rows": repaired_bars,
        "repaired_files": repaired_files,
        "issues": issue_report,
    }


class CsvProvider:
    def __init__(self, input_dir):
        if not input_dir:
            raise ValueError("--input-dir is required when provider=csv")

        self.input_dir = Path(input_dir)

    def fetch_bars(self, symbol, frequency, start=None, end=None):
        candidates = [
            self.input_dir
            / normalize_frequency(frequency)
            / f"{safe_symbol_filename(symbol)}.csv",
            self.input_dir
            / f"{safe_symbol_filename(symbol)}_{normalize_frequency(frequency)}.csv",
            self.input_dir / f"{safe_symbol_filename(symbol)}.csv",
        ]
        input_path = next(
            (candidate for candidate in candidates if candidate.exists()),
            None,
        )

        if input_path is None:
            return empty_bars_frame()

        bars = normalize_bar_frame(
            pd.read_csv(input_path),
            symbol=symbol,
            frequency=frequency,
            source="csv",
        )

        return filter_date_range(bars, start=start, end=end)


class SchwabProvider:
    def __init__(
        self,
        access_token=None,
        refresh_token=None,
        token_file=None,
        client_id=None,
        client_secret=None,
        redirect_uri=SCHWAB_REDIRECT_URI,
        force_reauth=False,
        max_history=False,
        base_url=SCHWAB_BASE_URL,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_history = max_history
        self.token_file = token_file_for(token_file)
        saved_tokens = load_schwab_token_file(self.token_file)
        explicit_access_token = None

        if not force_reauth:
            explicit_access_token = (
                access_token
                or os.getenv("SCHWAB_ACCESS_TOKEN")
            )
        saved_access_token = saved_tokens.get("access_token")
        self.access_token = explicit_access_token

        if (
            not self.access_token
            and not force_reauth
            and saved_access_token
            and schwab_access_token_is_current(saved_tokens)
        ):
            self.access_token = saved_access_token

        if force_reauth:
            refresh_token = None
        else:
            refresh_token = (
                refresh_token
                or os.getenv("SCHWAB_REFRESH_TOKEN")
                or saved_tokens.get("refresh_token")
            )

        if not self.access_token and refresh_token:
            try:
                client_id, client_secret = schwab_client_credentials(
                    client_id=client_id,
                    client_secret=client_secret,
                    prompt=False,
                )
            except ValueError as error:
                message = str(error)

                if saved_access_token:
                    message = (
                        "Saved Schwab access token is expired and refresh "
                        "requires --client-id/--client-secret or "
                        "SCHWAB_CLIENT_ID/SCHWAB_CLIENT_SECRET. Run an "
                        "interactive Schwab authorization again if the "
                        "refresh token has also expired."
                    )

                raise ValueError(message) from error

            token_payload = exchange_schwab_refresh_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            save_schwab_token_file(self.token_file, token_payload)
            self.access_token = token_payload["access_token"]

        if not self.access_token:
            token_payload = prompt_for_schwab_token_payload(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
            save_schwab_token_file(self.token_file, token_payload)
            self.access_token = token_payload["access_token"]

    def fetch_bars(self, symbol, frequency, start=None, end=None):
        requested_frequency = normalize_frequency(frequency)
        params = schwab_price_history_params(
            symbol=symbol,
            frequency=requested_frequency,
            start=start,
            end=end,
            max_history=self.max_history,
        )
        url = f"{self.base_url}/pricehistory?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = http_error_body_text(error)
            raise RuntimeError(
                f"Schwab pricehistory request failed for {symbol} "
                f"{frequency}: HTTP {error.code} {body}"
            ) from error
        except URLError as error:
            raise RuntimeError(
                f"Schwab pricehistory request failed for {symbol} "
                f"{frequency}: {error.reason}"
            ) from error

        candles = payload.get("candles", [])
        bars = normalize_bar_frame(
            candles,
            symbol=symbol,
            frequency=requested_frequency,
            source="schwab",
        )

        if requested_frequency == "60min":
            return aggregate_bars_to_60min(
                bars,
                symbol=symbol,
                source="schwab_30min_aggregated",
            )

        return bars


def schwab_price_history_params(
    symbol,
    frequency,
    start=None,
    end=None,
    max_history=False,
):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        params = {
            "symbol": schwab_provider_symbol(symbol),
            "periodType": "year",
            "period": SCHWAB_MAX_DAILY_YEARS if max_history else 1,
            "frequencyType": "daily",
            "frequency": 1,
        }
    elif frequency == "5min":
        params = {
            "symbol": schwab_provider_symbol(symbol),
            "periodType": "day",
            "period": SCHWAB_MAX_INTRADAY_DAYS if max_history else 1,
            "frequencyType": "minute",
            "frequency": 5,
            "needExtendedHoursData": "true",
        }
    else:
        params = {
            "symbol": schwab_provider_symbol(symbol),
            "periodType": "day",
            "period": SCHWAB_MAX_INTRADAY_DAYS if max_history else 1,
            "frequencyType": "minute",
            "frequency": 30,
            "needExtendedHoursData": "true",
        }

    start_ms = date_to_epoch_ms(start)
    end_ms = date_to_epoch_ms(end)

    if start_ms is not None:
        params["startDate"] = start_ms

    if end_ms is not None:
        params["endDate"] = end_ms

    return params


def schwab_authorization_url(client_id, redirect_uri=SCHWAB_REDIRECT_URI):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": "readonly",
        "redirect_uri": redirect_uri,
    }

    return f"{SCHWAB_AUTHORIZE_URL}?{urlencode(params)}"


def extract_authorization_code(value):
    value = str(value).strip()

    if not value:
        raise ValueError("Authorization code cannot be blank")

    parsed = urlparse(value)

    if parsed.query:
        query_values = parse_qs(parsed.query)

        if "code" in query_values and query_values["code"]:
            return query_values["code"][0]

    return value


def token_file_for(token_file=None):
    return Path(
        token_file
        or os.getenv("SCHWAB_TOKEN_FILE")
        or DEFAULT_SCHWAB_TOKEN_FILE
    )


def load_schwab_token_file(token_file):
    path = Path(token_file)

    if not path.exists():
        return {}

    with path.open() as token_handle:
        return json.load(token_handle)


def schwab_access_token_expires_at(token_payload):
    retrieved_at = token_payload.get("retrieved_at")
    expires_in = token_payload.get("expires_in")

    if not retrieved_at or not expires_in:
        return None

    try:
        retrieved = datetime.fromisoformat(
            str(retrieved_at).replace("Z", "+00:00")
        )
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None

    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=timezone.utc)

    return retrieved + timedelta(seconds=seconds)


def schwab_access_token_is_current(token_payload, leeway_seconds=60):
    if not token_payload.get("access_token"):
        return False

    expires_at = schwab_access_token_expires_at(token_payload)

    if expires_at is None:
        return False

    return (
        datetime.now(timezone.utc) + timedelta(seconds=leeway_seconds)
        < expires_at
    )


def save_schwab_token_file(token_file, token_payload):
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_schwab_token_file(path)
    merged = {
        **existing,
        **token_payload,
        "retrieved_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }

    with path.open("w") as token_handle:
        json.dump(merged, token_handle, indent=2)
        token_handle.write("\n")

    os.chmod(path, 0o600)

    return path


def schwab_client_credentials(client_id=None, client_secret=None, prompt=True):
    if client_id is None:
        client_id = os.getenv("SCHWAB_CLIENT_ID")

    if client_secret is None:
        client_secret = os.getenv("SCHWAB_CLIENT_SECRET")

    if prompt:
        client_id = client_id or input("Schwab client_id: ").strip()
        client_secret = client_secret or getpass.getpass(
            "Schwab client_secret: "
        ).strip()

    if not client_id:
        raise ValueError("Schwab client_id is required")

    if not client_secret:
        raise ValueError("Schwab client_secret is required")

    return client_id, client_secret


def http_error_body_text(error):
    body = error.read()
    encoding = ""

    if error.headers:
        encoding = error.headers.get("Content-Encoding", "") or error.headers.get(
            "content-encoding",
            "",
        )

    if "gzip" in str(encoding).lower():
        try:
            body = gzip.decompress(body)
        except OSError:
            pass

    return body.decode("utf-8", errors="replace")


def request_schwab_token(
    client_id,
    client_secret,
    token_fields,
    token_url=SCHWAB_TOKEN_URL,
):
    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    payload = urlencode(token_fields).encode("utf-8")
    request = Request(
        token_url,
        data=payload,
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            token_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = http_error_body_text(error)
        raise RuntimeError(
            f"Schwab token request failed: HTTP {error.code} {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Schwab token request failed: {error.reason}"
        ) from error

    if "access_token" not in token_payload:
        raise RuntimeError(
            "Schwab token response did not include an access_token"
        )

    return token_payload


def exchange_schwab_authorization_code(
    client_id,
    client_secret,
    authorization_code,
    redirect_uri=SCHWAB_REDIRECT_URI,
    token_url=SCHWAB_TOKEN_URL,
):
    return request_schwab_token(
        client_id=client_id,
        client_secret=client_secret,
        token_fields={
            "grant_type": "authorization_code",
            "code": extract_authorization_code(authorization_code),
            "redirect_uri": redirect_uri,
        },
        token_url=token_url,
    )


def exchange_schwab_refresh_token(
    client_id,
    client_secret,
    refresh_token,
    token_url=SCHWAB_TOKEN_URL,
):
    return request_schwab_token(
        client_id=client_id,
        client_secret=client_secret,
        token_fields={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        token_url=token_url,
    )


def prompt_for_schwab_token_payload(
    client_id=None,
    client_secret=None,
    redirect_uri=SCHWAB_REDIRECT_URI,
    browser_opener=None,
):
    client_id, client_secret = schwab_client_credentials(
        client_id=client_id,
        client_secret=client_secret,
        prompt=True,
    )

    authorization_url = schwab_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    print("\nOpen this Schwab authorization URL in your browser:")
    print(authorization_url)
    opener = browser_opener or webbrowser.open

    try:
        opener(authorization_url)
    except webbrowser.Error:
        pass

    print(
        "\nAfter approving access, paste the full redirect URL "
        "or just the code value."
    )
    authorization_code = input("Schwab authorization code or URL: ").strip()
    token_payload = exchange_schwab_authorization_code(
        client_id=client_id,
        client_secret=client_secret,
        authorization_code=authorization_code,
        redirect_uri=redirect_uri,
    )

    if token_payload.get("refresh_token"):
        print(
            "Received access token and refresh token. "
            "This script uses the access token for this run only."
        )
    else:
        print("Received access token for this run.")

    return token_payload


def prompt_for_schwab_access_token(
    client_id=None,
    client_secret=None,
    redirect_uri=SCHWAB_REDIRECT_URI,
):
    return prompt_for_schwab_token_payload(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )["access_token"]


def date_to_epoch_ms(value):
    if not value:
        return None

    timestamp = pd.to_datetime(value, utc=True, errors="raise")

    return int(timestamp.timestamp() * 1000)


def filter_date_range(bars, start=None, end=None):
    if len(bars) == 0:
        return bars

    working = bars.copy()
    timestamps = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )

    if start:
        working = working[timestamps >= pd.to_datetime(start, utc=True)]
        timestamps = pd.to_datetime(
            working["timestamp"],
            utc=True,
            errors="coerce",
        )

    if end:
        working = working[timestamps <= pd.to_datetime(end, utc=True)]

    return working.reset_index(drop=True)


def aggregate_bars_to_60min(bars, symbol, source="schwab_30min"):
    if bars is None or len(bars) == 0:
        return empty_bars_frame()

    working = bars.copy()
    working["timestamp"] = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )
    working = working[working["timestamp"].notna()].copy()

    if len(working) == 0:
        return empty_bars_frame()

    for column in ["open", "high", "low", "close", "volume", "open_interest"]:
        working[column] = pd.to_numeric(
            working.get(column),
            errors="coerce",
        )

    working = working.sort_values("timestamp")
    working["hour"] = working["timestamp"].dt.floor("60min")
    aggregations = {
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "open_interest": ("open_interest", "last"),
    }
    aggregated = working.groupby("hour", as_index=False).agg(**aggregations)
    aggregated = aggregated.rename(columns={"hour": "timestamp"})
    aggregated["date"] = aggregated["timestamp"].dt.date.astype(str)
    aggregated["symbol"] = normalize_symbol(symbol)
    aggregated["frequency"] = "60min"
    aggregated["source"] = source
    aggregated["retrieved_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    aggregated["timestamp"] = aggregated["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return aggregated[CANONICAL_COLUMNS].reset_index(drop=True)


def create_provider(args):
    if args.provider == "csv":
        return CsvProvider(args.input_dir)

    if args.provider == "schwab":
        return SchwabProvider(
            access_token=args.access_token,
            refresh_token=args.refresh_token,
            token_file=args.token_file,
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            force_reauth=args.force_reauth,
            max_history=args.all,
        )

    raise ValueError(f"Unsupported provider: {args.provider}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Download or ingest market data and store normalized "
            "daily and 5-minute bars locally (with optional 60-minute output)."
        )
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help=(
            "Symbols to download, e.g. /ES /6E /GC or equities/ETFs "
            "such as SPY."
        ),
    )
    parser.add_argument(
        "--frequencies",
        nargs="+",
        default=["daily", "5min"],
        help="One or more frequencies: daily, 5min, 60min",
    )
    parser.add_argument(
        "--provider",
        choices=["csv", "schwab"],
        default=DEFAULT_PROVIDER,
        help=(
            "Data provider. Use csv for vendor/exported files; use schwab "
            "only if your Schwab API account supports the requested bars."
        ),
    )
    parser.add_argument(
        "--input-dir",
        help="Directory containing source CSV bars when provider=csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where normalized market data files are written",
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help=(
            "Run local integrity and daily-vs-5min quality checks against "
            "already saved market data without fetching provider data."
        ),
    )
    parser.add_argument(
        "--start",
        help="Optional inclusive start date/time, e.g. 2026-01-01",
    )
    parser.add_argument(
        "--end",
        help="Optional inclusive end date/time, e.g. 2026-06-05",
    )
    parser.add_argument(
        "--access-token",
        help="Schwab bearer token. Defaults to SCHWAB_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--refresh-token",
        help=(
            "Schwab refresh token for non-interactive token renewal. "
            "Defaults to SCHWAB_REFRESH_TOKEN or the token file."
        ),
    )
    parser.add_argument(
        "--token-file",
        help=(
            "Path to a JSON file for saved Schwab tokens. Defaults to "
            "SCHWAB_TOKEN_FILE or data/market_data/schwab_tokens.json."
        ),
    )
    parser.add_argument(
        "--client-id",
        help="Schwab OAuth client_id. Defaults to SCHWAB_CLIENT_ID or prompt.",
    )
    parser.add_argument(
        "--client-secret",
        help=(
            "Schwab OAuth client_secret. Defaults to SCHWAB_CLIENT_SECRET "
            "or hidden prompt."
        ),
    )
    parser.add_argument(
        "--redirect-uri",
        default=SCHWAB_REDIRECT_URI,
        help="Schwab OAuth redirect URI configured for your app.",
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Prepare and save Schwab tokens without downloading market data.",
    )
    parser.add_argument(
        "--force-reauth",
        action="store_true",
        help="Ignore saved Schwab tokens and start browser authorization.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue remaining symbol/frequency jobs after a provider error.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send a desktop notification when the command succeeds or fails.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Request as much Schwab price history as this script can ask for. "
            "Without this flag, Schwab mode uses the default shorter window."
        ),
    )
    parser.add_argument(
        "--export-amibroker",
        action="store_true",
        help=(
            "After quality checks, create combined daily and 5-minute "
            "AmiBroker import files under <output-dir>/amibroker."
        ),
    )
    parser.add_argument(
        "--amibroker-timezone",
        default="America/Detroit",
        help=(
            "IANA timezone used for AmiBroker intraday exports. "
            "Default: America/Detroit."
        ),
    )
    parser.add_argument(
        "-all",
        action="store_true",
        dest="all",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--quality-threshold-pct",
        type=float,
        default=0.25,
        help=(
            "Maximum allowed OHLC percentage difference when comparing daily "
            "bars to daily bars aggregated from 5-minute data."
        ),
    )
    parser.add_argument(
        "--apply-daily-intraday-fixes",
        action="store_true",
        help=(
            "Replace mismatched daily bars with OHLC aggregated from 5-minute "
            "bars when enough intraday bars are available."
        ),
    )
    parser.add_argument(
        "--min-intraday-bars-for-daily-fix",
        type=int,
        default=50,
        help=(
            "Minimum number of 5-minute bars required before applying a "
            "daily repair candidate."
        ),
    )

    args = parser.parse_args()

    if args.provider == "csv" and not args.quality_only and not args.input_dir:
        parser.error(
            "--input-dir is required when --provider csv unless "
            "--quality-only is used"
        )

    if args.provider != "schwab" and (args.auth_only or args.force_reauth):
        parser.error("--auth-only and --force-reauth require --provider schwab")

    if args.quality_only and (args.auth_only or args.force_reauth):
        parser.error(
            "--quality-only cannot be combined with --auth-only or "
            "--force-reauth"
        )

    return args


def main(args=None):
    args = args or parse_args()
    output_dir = Path(args.output_dir)
    symbols = [normalize_symbol(symbol) for symbol in args.symbols]
    frequencies = [
        normalize_frequency(frequency)
        for frequency in args.frequencies
    ]
    provider = None

    if not args.quality_only:
        provider = create_provider(args)

    if args.auth_only:
        print(
            "Schwab authorization is ready. Saved token file: "
            f"{provider.token_file}"
        )
        return

    manifest_path = write_symbol_manifest(output_dir, symbols)
    print(f"Wrote symbol manifest: {manifest_path}")

    run_start = time.monotonic()
    failed_jobs = []
    total_jobs = 0

    if provider is not None:
        total_jobs = len(symbols) * len(frequencies)
        completed_jobs = 0

        for symbol in symbols:
            for frequency in frequencies:
                job_number = completed_jobs + 1
                job_start = time.monotonic()
                fetch_note = (
                    " via 30min Schwab bars"
                    if args.provider == "schwab" and frequency == "60min"
                    else ""
                )
                print(
                    f"[{job_number}/{total_jobs}] Fetching {symbol} "
                    f"{frequency} from {args.provider}{fetch_note}..."
                )
                try:
                    bars = provider.fetch_bars(
                        symbol=symbol,
                        frequency=frequency,
                        start=args.start,
                        end=args.end,
                    )
                except (RuntimeError, ValueError) as error:
                    if not args.continue_on_error:
                        raise

                    completed_jobs += 1
                    failed_jobs.append((symbol, frequency, str(error)))
                    print(
                        f"[{completed_jobs}/{total_jobs}] Failed {symbol} "
                        f"{frequency}: {error}. Continuing; ETA "
                        f"{eta_text(run_start, completed_jobs, total_jobs)}."
                    )
                    continue

                fetch_elapsed = time.monotonic() - job_start
                save_start = time.monotonic()
                print(
                    f"[{job_number}/{total_jobs}] Fetched {len(bars)} bars "
                    f"for {symbol} {frequency} in {elapsed_text(fetch_elapsed)}; "
                    "saving..."
                )

                output_path, row_count = save_market_data(
                    output_dir=output_dir,
                    symbol=symbol,
                    frequency=frequency,
                    bars=bars,
                )
                completed_jobs += 1
                total_elapsed = time.monotonic() - job_start
                save_elapsed = time.monotonic() - save_start
                print(
                    f"[{completed_jobs}/{total_jobs}] Saved {symbol} "
                    f"{frequency}: added {len(bars)} bars; "
                    f"{row_count} total rows -> {output_path}. "
                    f"Step {elapsed_text(total_elapsed)} "
                    f"(save {elapsed_text(save_elapsed)}); "
                    f"ETA {eta_text(run_start, completed_jobs, total_jobs)}."
                )

    quality_start = time.monotonic()
    print("Running saved-data integrity repairs...")
    repair_result = repair_saved_integrity(
        output_dir=output_dir,
        symbols=symbols,
        frequencies=frequencies,
    )
    if repair_result["repaired_issue_rows"]:
        print(
            "Repaired integrity issues: "
            f"{repair_result['repaired_issue_rows']} issue rows across "
            f"{repair_result['repaired_files']} files."
        )

    print("Writing quality reports...")
    quality = write_quality_reports(
        output_dir=output_dir,
        symbols=symbols,
        frequencies=frequencies,
        threshold_pct=args.quality_threshold_pct,
        apply_daily_fixes=args.apply_daily_intraday_fixes,
        min_intraday_bars=args.min_intraday_bars_for_daily_fix,
    )
    quality_issues = int(quality["summary"]["issues"].sum())
    print(f"Wrote quality summary: {quality['summary_path']}")
    print(f"Wrote integrity report: {quality['integrity_path']}")
    print(f"Wrote daily-vs-5min report: {quality['daily_intraday_path']}")
    print(f"Wrote daily repair candidates: {quality['fix_candidates_path']}")
    if quality["apply_result"]:
        print(
            "Applied daily-vs-5min fixes: "
            f"{quality['apply_result'].get('updated_bars', 0)} bars across "
            f"{quality['apply_result'].get('updated_files', 0)} files."
        )
    print(f"Quality checks found {quality_issues} issue rows.")

    if getattr(args, "export_amibroker", False):
        from export_amibroker_market_data import export_amibroker_market_data

        export_result = export_amibroker_market_data(
            market_data_dir=output_dir,
            timezone_name=args.amibroker_timezone,
        )
        export_manifest = export_result["manifest"]
        print(
            "Wrote AmiBroker exports: "
            f"{export_manifest['daily']['exported_rows']:,} daily rows -> "
            f"{export_result['daily_path']}; "
            f"{export_manifest['intraday']['exported_rows']:,} 5-minute "
            f"rows -> {export_result['intraday_path']}."
        )

    print(
        "Finished market data run in "
        f"{elapsed_text(time.monotonic() - run_start)} "
        f"(quality {elapsed_text(time.monotonic() - quality_start)})."
    )

    if failed_jobs:
        raise RuntimeError(
            f"{len(failed_jobs)} of {total_jobs} market data jobs failed; "
            "see the failure lines above"
        )


def run_cli(args=None, run_main=None, notifier=None):
    args = args or parse_args()
    run_main = run_main or main
    notifier = notifier or send_desktop_notification

    try:
        run_main(args)
    except (RuntimeError, ValueError) as error:
        if args.notify:
            notify_best_effort(
                notifier,
                title="Market data update failed",
                message=str(error),
                urgency="critical",
            )
        raise SystemExit(f"error: {error}") from None
    except Exception as error:
        if args.notify:
            notify_best_effort(
                notifier,
                title="Market data update failed",
                message=str(error),
                urgency="critical",
            )
        raise

    if args.notify:
        notify_best_effort(
            notifier,
            title="Market data update succeeded",
            message="download_market_data.py completed successfully.",
            urgency="normal",
        )


if __name__ == "__main__":
    run_cli()
