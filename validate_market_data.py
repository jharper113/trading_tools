import argparse
import bisect
import json
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from download_market_data import (
    CANONICAL_COLUMNS,
    DEFAULT_SYMBOLS,
    REVIEWED_COLUMNS,
    empty_bars_frame,
    load_reviewed_bars,
    normalize_bar_frame,
    normalize_frequency,
    normalize_symbol,
    output_file_for,
    reviewed_bars_file_for,
)


DEFAULT_SOURCE_DIR = Path("data/market_data")
DEFAULT_OUTPUT_DIR = Path("output/market_data_validation")
DEFAULT_THRESHOLD_PCT = 0.25
DASHBOARD_FILE = "validation_dashboard.html"
DEFAULT_DASHBOARD_MAX_ROWS = 1000
DEFAULT_REFERENCE_FREQUENCIES = ["daily"]
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
PRICE_COLUMNS = ["open", "high", "low", "close"]
DIFFERENCE_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "field",
    "local_value",
    "reference_value",
    "abs_diff",
    "pct_diff",
    "threshold_pct",
    "approved",
]
MISSING_COLUMNS = ["symbol", "frequency", "comparison_key", "missing_from"]
AUTO_REVIEW_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "approved",
    "max_pct_diff",
    "auto_decision",
    "auto_reason",
    "local_neighbor_distance",
    "reference_neighbor_distance",
    "previous_confirmed_key",
    "next_confirmed_key",
    "local_open",
    "reference_open",
    "local_high",
    "reference_high",
    "local_low",
    "reference_low",
    "local_close",
    "reference_close",
]
APPLIED_AUTO_REVIEW_COLUMNS = AUTO_REVIEW_COLUMNS + ["decision"]
TIMEZONE_ALIGNMENT_COLUMNS = [
    "symbol",
    "frequency",
    "local_bars",
    "reference_bars",
    "exact_utc_matches",
    "best_shift_minutes",
    "best_shift_matches",
    "status",
    "detail",
]
DUPLICATE_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
]


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def eta_text(start_time, completed, total):
    if completed <= 0 or total <= 0:
        return "estimating"

    elapsed = time.monotonic() - start_time
    remaining = max(0, total - completed)
    seconds_remaining = elapsed / completed * remaining

    return elapsed_text(seconds_remaining)


def log_progress(message):
    print(message, flush=True)


YAHOO_FUTURES_SYMBOLS = {
    "/ES": "ES=F",
    "/NQ": "NQ=F",
    "/RTY": "RTY=F",
    "/YM": "YM=F",
    "/ZB": "ZB=F",
    "/ZN": "ZN=F",
    "/ZF": "ZF=F",
    "/ZT": "ZT=F",
    "/6E": "6E=F",
    "/6J": "6J=F",
    "/6B": "6B=F",
    "/6A": "6A=F",
    "/6C": "6C=F",
    "/6S": "6S=F",
    "/GC": "GC=F",
    "/SI": "SI=F",
    "/HG": "HG=F",
    "/PL": "PL=F",
    "/CL": "CL=F",
    "/NG": "NG=F",
    "/RB": "RB=F",
    "/HO": "HO=F",
    "/ZC": "ZC=F",
    "/ZS": "ZS=F",
    "/ZM": "ZM=F",
    "/ZL": "ZL=F",
    "/ZW": "ZW=F",
    "/LE": "LE=F",
    "/HE": "HE=F",
    "/KC": "KC=F",
    "/SB": "SB=F",
    "/CT": "CT=F",
    "/CC": "CC=F",
}


def yahoo_symbol_for(symbol):
    symbol = normalize_symbol(symbol)

    if symbol in YAHOO_FUTURES_SYMBOLS:
        return YAHOO_FUTURES_SYMBOLS[symbol]

    if symbol.startswith("/"):
        return f"{symbol.lstrip('/')}=F"

    return symbol


def yahoo_interval_for(frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        return "1d"

    if frequency == "5min":
        return "5m"

    if frequency == "60min":
        return "60m"

    raise ValueError(f"Unsupported Yahoo validation frequency: {frequency}")


def default_yahoo_range_for(frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        return "5y"

    if frequency == "5min":
        return "1mo"

    return "730d"


def date_to_epoch_seconds(value):
    if not value:
        return None

    timestamp = pd.to_datetime(value, utc=True, errors="raise")

    return int(timestamp.timestamp())


def yahoo_chart_params(frequency, start=None, end=None):
    params = {
        "interval": yahoo_interval_for(frequency),
        "includePrePost": "true",
        "events": "history",
    }
    period1 = date_to_epoch_seconds(start)
    period2 = date_to_epoch_seconds(end)

    if period1 is not None or period2 is not None:
        params["period1"] = period1 or 0
        params["period2"] = period2 or int(
            datetime.now(timezone.utc).timestamp()
        )
    else:
        params["range"] = default_yahoo_range_for(frequency)

    return params


def fetch_yahoo_bars(symbol, frequency, start=None, end=None):
    yahoo_symbol = yahoo_symbol_for(symbol)
    params = yahoo_chart_params(
        frequency=frequency,
        start=start,
        end=end,
    )
    url = f"{YAHOO_CHART_URL}/{yahoo_symbol}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "trading-tools-market-data-validator/1.0",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Yahoo request failed for {symbol} {frequency}: "
            f"HTTP {error.code} {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Yahoo request failed for {symbol} {frequency}: {error.reason}"
        ) from error

    result = payload.get("chart", {}).get("result") or []

    if not result:
        return pd.DataFrame(columns=[])

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", []),
        }
    )

    return normalize_bar_frame(
        bars,
        symbol=symbol,
        frequency=frequency,
        source="yahoo",
    )


def load_local_bars(source_dir, symbol, frequency):
    path = output_file_for(source_dir, symbol, frequency)

    if not path.exists():
        return pd.DataFrame(columns=[])

    return pd.read_csv(path)


def normalize_for_comparison(bars, frequency):
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=DUPLICATE_COLUMNS)

    working = bars.copy()
    working["timestamp"] = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )
    working = working[working["timestamp"].notna()].copy()
    working["date"] = working["timestamp"].dt.date.astype(str)

    for column in PRICE_COLUMNS:
        working[column] = pd.to_numeric(
            working.get(column),
            errors="coerce",
        )

    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        working["comparison_key"] = working["date"]
    else:
        working["comparison_key"] = working["timestamp"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    return working


def find_duplicate_bars(bars, symbol, frequency):
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=[])

    working = normalize_for_comparison(bars, frequency)
    duplicates = working[
        working.duplicated("comparison_key", keep=False)
    ].copy()

    if len(duplicates) == 0:
        return pd.DataFrame(columns=DUPLICATE_COLUMNS)

    duplicates["symbol"] = normalize_symbol(symbol)
    duplicates["frequency"] = normalize_frequency(frequency)

    return duplicates[DUPLICATE_COLUMNS].copy()


def compare_market_data(local_bars, reference_bars, symbol, frequency, threshold_pct):
    local = normalize_for_comparison(local_bars, frequency)
    reference = normalize_for_comparison(reference_bars, frequency)
    symbol = normalize_symbol(symbol)
    frequency = normalize_frequency(frequency)

    if len(local) == 0 or len(reference) == 0:
        return empty_comparison_outputs(symbol, frequency)

    merged = pd.merge(
        local[["comparison_key"] + PRICE_COLUMNS],
        reference[["comparison_key"] + PRICE_COLUMNS],
        on="comparison_key",
        how="outer",
        suffixes=("_local", "_reference"),
        indicator=True,
    )
    matched = merged[merged["_merge"] == "both"].copy()

    difference_rows = []

    for _, row in matched.iterrows():
        row_max_pct_diff = 0.0
        row_approved = True

        for column in PRICE_COLUMNS:
            local_value = row[f"{column}_local"]
            reference_value = row[f"{column}_reference"]

            if pd.isna(local_value) or pd.isna(reference_value):
                abs_diff = np.nan
                pct_diff = np.nan
                approved = False
            else:
                abs_diff = local_value - reference_value
                denominator = abs(reference_value)
                pct_diff = (
                    abs(abs_diff) / denominator * 100
                    if denominator != 0
                    else np.nan
                )
                approved = bool(
                    not pd.isna(pct_diff) and pct_diff <= threshold_pct
                )

            if not approved:
                row_approved = False

            if not pd.isna(pct_diff):
                row_max_pct_diff = max(row_max_pct_diff, pct_diff)

            difference_rows.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "comparison_key": row["comparison_key"],
                    "field": column,
                    "local_value": local_value,
                    "reference_value": reference_value,
                    "abs_diff": abs_diff,
                    "pct_diff": pct_diff,
                    "threshold_pct": threshold_pct,
                    "approved": approved,
                }
            )

        difference_rows.append(
            {
                "symbol": symbol,
                "frequency": frequency,
                "comparison_key": row["comparison_key"],
                "field": "row_max",
                "local_value": np.nan,
                "reference_value": np.nan,
                "abs_diff": np.nan,
                "pct_diff": row_max_pct_diff,
                "threshold_pct": threshold_pct,
                "approved": row_approved,
            }
        )

    differences = pd.DataFrame(difference_rows, columns=DIFFERENCE_COLUMNS)
    missing = build_missing_bars_report(merged, symbol, frequency)
    summary = summarize_comparison(
        differences=differences,
        missing=missing,
        symbol=symbol,
        frequency=frequency,
        local_count=len(local),
        reference_count=len(reference),
        threshold_pct=threshold_pct,
    )

    return differences, missing, summary


def build_timezone_alignment_report(local_bars, reference_bars, symbol, frequency):
    symbol = normalize_symbol(symbol)
    frequency = normalize_frequency(frequency)
    local = normalize_for_comparison(local_bars, frequency)
    reference = normalize_for_comparison(reference_bars, frequency)

    if frequency == "daily":
        exact_matches = len(
            set(local.get("comparison_key", []))
            & set(reference.get("comparison_key", []))
        )
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "local_bars": len(local),
                    "reference_bars": len(reference),
                    "exact_utc_matches": exact_matches,
                    "best_shift_minutes": 0,
                    "best_shift_matches": exact_matches,
                    "status": "date_aligned",
                    "detail": "Daily bars are compared by UTC-normalized calendar date.",
                }
            ],
            columns=TIMEZONE_ALIGNMENT_COLUMNS,
        )

    if len(local) == 0 or len(reference) == 0:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "local_bars": len(local),
                    "reference_bars": len(reference),
                    "exact_utc_matches": 0,
                    "best_shift_minutes": np.nan,
                    "best_shift_matches": 0,
                    "status": "insufficient_data",
                    "detail": "Cannot check timestamp alignment without both local and reference bars.",
                }
            ],
            columns=TIMEZONE_ALIGNMENT_COLUMNS,
        )

    local_times = set(pd.to_datetime(local["timestamp"], utc=True).array.asi8)
    reference_times = pd.to_datetime(reference["timestamp"], utc=True)
    reference_ns = reference_times.array.asi8
    exact_matches = len(local_times & set(reference_ns))
    offsets = [
        -720,
        -660,
        -600,
        -540,
        -480,
        -420,
        -360,
        -300,
        -240,
        -180,
        -120,
        -60,
        0,
        60,
        120,
        180,
        240,
        300,
        360,
        420,
        480,
        540,
        600,
        660,
        720,
    ]
    match_counts = {}

    for offset_minutes in offsets:
        shifted = reference_ns + pd.Timedelta(minutes=offset_minutes).value
        match_counts[offset_minutes] = len(local_times & set(shifted))

    best_shift = max(match_counts, key=match_counts.get)
    best_matches = match_counts[best_shift]

    if best_shift != 0 and best_matches > exact_matches:
        status = "possible_timezone_shift"
        detail = (
            f"Shifting Yahoo/reference timestamps by {best_shift} minutes "
            f"matches {best_matches} bars vs {exact_matches} exact UTC matches."
        )
    else:
        status = "utc_aligned"
        detail = "Exact UTC timestamp matching is as good as or better than common timezone shifts."

    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "frequency": frequency,
                "local_bars": len(local),
                "reference_bars": len(reference),
                "exact_utc_matches": exact_matches,
                "best_shift_minutes": best_shift,
                "best_shift_matches": best_matches,
                "status": status,
                "detail": detail,
            }
        ],
        columns=TIMEZONE_ALIGNMENT_COLUMNS,
    )


def empty_comparison_outputs(symbol, frequency):
    differences = pd.DataFrame(columns=DIFFERENCE_COLUMNS)
    missing = pd.DataFrame(columns=MISSING_COLUMNS)
    summary = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "frequency": frequency,
                "local_bars": 0,
                "reference_bars": 0,
                "matched_bars": 0,
                "missing_local_bars": 0,
                "missing_reference_bars": 0,
                "max_pct_diff": np.nan,
                "mean_pct_diff": np.nan,
                "approved_bars": 0,
                "failed_bars": 0,
                "threshold_pct": np.nan,
                "approved": False,
            }
        ]
    )

    return differences, missing, summary


def build_missing_bars_report(merged, symbol, frequency):
    missing_rows = []

    for _, row in merged[merged["_merge"] != "both"].iterrows():
        missing_from = (
            "local" if row["_merge"] == "right_only" else "reference"
        )
        missing_rows.append(
            {
                "symbol": symbol,
                "frequency": frequency,
                "comparison_key": row["comparison_key"],
                "missing_from": missing_from,
            }
        )

    return pd.DataFrame(missing_rows, columns=MISSING_COLUMNS)


def summarize_comparison(
    differences,
    missing,
    symbol,
    frequency,
    local_count,
    reference_count,
    threshold_pct,
):
    row_max = differences[differences["field"] == "row_max"].copy()
    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)].copy()
    failed_bars = int((~row_max["approved"].fillna(False)).sum())
    approved_bars = int(row_max["approved"].fillna(False).sum())
    missing_from = (
        missing["missing_from"]
        if "missing_from" in missing.columns
        else pd.Series(dtype=object)
    )
    missing_local = int((missing_from == "local").sum())
    missing_reference = int((missing_from == "reference").sum())
    max_pct_diff = price_diffs["pct_diff"].max()
    mean_pct_diff = price_diffs["pct_diff"].mean()

    approved = (
        failed_bars == 0
        and missing_local == 0
        and missing_reference == 0
        and len(row_max) > 0
    )

    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "frequency": frequency,
                "local_bars": local_count,
                "reference_bars": reference_count,
                "matched_bars": len(row_max),
                "missing_local_bars": missing_local,
                "missing_reference_bars": missing_reference,
                "max_pct_diff": max_pct_diff,
                "mean_pct_diff": mean_pct_diff,
                "approved_bars": approved_bars,
                "failed_bars": failed_bars,
                "threshold_pct": threshold_pct,
                "approved": approved,
            }
        ]
    )


def save_heatmap(summary, output_dir):
    if len(summary) == 0:
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    heatmap_path = output_dir / "market_data_difference_heatmap.png"
    working = summary.copy()
    working["symbol_frequency"] = (
        working["symbol"] + " " + working["frequency"]
    )
    heatmap = working.pivot_table(
        index="symbol_frequency",
        values="max_pct_diff",
        aggfunc="max",
    ).sort_index()

    if len(heatmap) == 0:
        return None

    values = heatmap[["max_pct_diff"]].to_numpy(dtype=float)
    fig_height = max(4, len(heatmap) * 0.45)
    fig, ax = plt.subplots(figsize=(7, fig_height))
    image = ax.imshow(
        values,
        aspect="auto",
        cmap="RdYlGn_r",
        interpolation="nearest",
    )
    ax.set_xticks([0])
    ax.set_xticklabels(["Max % Difference"])
    ax.set_yticks(np.arange(len(heatmap.index)))
    ax.set_yticklabels(heatmap.index)
    ax.set_title("Market Data Validation vs Yahoo")

    for row_index, value in enumerate(values[:, 0]):
        label = "N/A" if np.isnan(value) else f"{value:.3f}%"
        ax.text(
            0,
            row_index,
            label,
            ha="center",
            va="center",
            color="black",
            fontsize=9,
        )

    fig.colorbar(image, ax=ax, label="Max % Difference")
    fig.tight_layout()
    fig.savefig(heatmap_path, dpi=160)
    plt.close(fig)

    return heatmap_path


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_for_comparison_key(comparison_key, frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        timestamp = pd.to_datetime(
            str(comparison_key),
            utc=True,
            errors="raise",
        )
    else:
        timestamp = pd.to_datetime(
            str(comparison_key),
            utc=True,
            errors="raise",
        )

    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def selected_value(decision, field, selected_source):
    prefix = "reference" if selected_source == "yahoo" else "local"
    value = decision.get(f"{prefix}_{field}")

    return pd.to_numeric(value, errors="coerce")


def decision_to_bar(decision, reviewed_at):
    selected_source = str(decision.get("decision", "")).strip().lower()

    if selected_source not in {"local", "yahoo"}:
        return None

    symbol = normalize_symbol(decision["symbol"])
    frequency = normalize_frequency(decision["frequency"])

    return {
        "timestamp": timestamp_for_comparison_key(
            decision["comparison_key"],
            frequency,
        ),
        "date": str(decision["comparison_key"])[:10],
        "symbol": symbol,
        "frequency": frequency,
        "open": selected_value(decision, "open", selected_source),
        "high": selected_value(decision, "high", selected_source),
        "low": selected_value(decision, "low", selected_source),
        "close": selected_value(decision, "close", selected_source),
        "volume": pd.NA,
        "open_interest": pd.NA,
        "source": f"reviewed_{selected_source}",
        "retrieved_at": reviewed_at,
    }


def comparison_key_for_row(row):
    timestamp = pd.to_datetime(
        row["timestamp"],
        utc=True,
        errors="coerce",
    )
    frequency = normalize_frequency(row["frequency"])

    if pd.isna(timestamp):
        return ""

    if frequency == "daily":
        return str(timestamp.date())

    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_reconciliation_decisions(source_dir, decisions):
    source_dir = Path(source_dir)
    reviewed_at = utc_now_text()
    selected_bars = []
    reviewed_rows = []

    for decision in decisions:
        selected_source = str(decision.get("decision", "")).strip().lower()

        if selected_source not in {"local", "yahoo"}:
            continue

        bar = decision_to_bar(decision, reviewed_at=reviewed_at)

        if bar is None:
            continue

        selected_bars.append(bar)
        reviewed_rows.append(
            {
                "symbol": bar["symbol"],
                "frequency": bar["frequency"],
                "comparison_key": str(decision["comparison_key"]),
                "selected_source": selected_source,
                "reviewed_at": reviewed_at,
            }
        )

    if not selected_bars:
        return {"updated_bars": 0, "reviewed_bars": 0}

    selected = pd.DataFrame(selected_bars, columns=CANONICAL_COLUMNS)
    selected["timestamp"] = pd.to_datetime(
        selected["timestamp"],
        utc=True,
        errors="coerce",
    )
    selected["timestamp"] = selected["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    for (symbol, frequency), group in selected.groupby(["symbol", "frequency"]):
        output_path = output_file_for(source_dir, symbol, frequency)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            pd.read_csv(output_path)
            if output_path.exists()
            else empty_bars_frame()
        )

        if len(existing) > 0:
            existing = existing.copy()
            existing["symbol"] = existing["symbol"].map(normalize_symbol)
            existing["frequency"] = existing["frequency"].map(normalize_frequency)
            existing["_comparison_key"] = existing.apply(
                comparison_key_for_row,
                axis=1,
            )
            selected_keys = set(group["timestamp"].map(
                lambda value: comparison_key_for_row(
                    {
                        "timestamp": value,
                        "frequency": frequency,
                    }
                )
            ))
            existing = existing[
                ~existing["_comparison_key"].isin(selected_keys)
            ].drop(columns=["_comparison_key"])

        combined = pd.concat([existing, group], ignore_index=True)
        combined["timestamp"] = pd.to_datetime(
            combined["timestamp"],
            utc=True,
            errors="coerce",
        )
        combined = combined[combined["timestamp"].notna()].copy()
        combined = combined.sort_values(["symbol", "frequency", "timestamp"])
        combined["date"] = combined["timestamp"].dt.date.astype(str)
        combined["timestamp"] = combined["timestamp"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        combined = combined[CANONICAL_COLUMNS].reset_index(drop=True)
        combined.to_csv(output_path, index=False)

    reviewed_existing = load_reviewed_bars(source_dir)
    reviewed_new = pd.DataFrame(reviewed_rows, columns=REVIEWED_COLUMNS)
    reviewed = pd.concat(
        [reviewed_existing, reviewed_new],
        ignore_index=True,
    )
    reviewed = reviewed.drop_duplicates(
        subset=["symbol", "frequency", "comparison_key"],
        keep="last",
    )
    reviewed_path = reviewed_bars_file_for(source_dir)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed[REVIEWED_COLUMNS].to_csv(reviewed_path, index=False)

    return {
        "updated_bars": len(selected),
        "reviewed_bars": len(reviewed_new),
        "reviewed_file": str(reviewed_path),
    }


def apply_auto_review_decisions(source_dir, auto_review):
    if auto_review is None or len(auto_review) == 0:
        return {"updated_bars": 0, "reviewed_bars": 0}

    decisions = to_json_records(
        auto_review[auto_review["decision"].isin(["local", "yahoo"])]
    )

    return apply_reconciliation_decisions(
        source_dir=source_dir,
        decisions=decisions,
    )


def reviewed_key_set(source_dir):
    reviewed = load_reviewed_bars(source_dir)

    if reviewed is None or len(reviewed) == 0:
        return set()

    return set(
        zip(
            reviewed["symbol"].map(normalize_symbol),
            reviewed["frequency"].map(normalize_frequency),
            reviewed["comparison_key"].astype(str),
        )
    )


def filter_reviewed_auto_review(auto_review, reviewed_keys):
    if (
        auto_review is None
        or len(auto_review) == 0
        or not reviewed_keys
    ):
        return auto_review

    working = auto_review.copy()
    keys = list(
        zip(
            working["symbol"].map(normalize_symbol),
            working["frequency"].map(normalize_frequency),
            working["comparison_key"].astype(str),
        )
    )

    return working[
        [key not in reviewed_keys for key in keys]
    ].reset_index(drop=True)


def unreviewed_local_bars(local_bars, symbol, frequency, reviewed_keys):
    local = normalize_for_comparison(local_bars, frequency)

    if len(local) == 0 or not reviewed_keys:
        return local

    symbol = normalize_symbol(symbol)
    frequency = normalize_frequency(frequency)
    keys = list(
        zip(
            [symbol] * len(local),
            [frequency] * len(local),
            local["comparison_key"].astype(str),
        )
    )

    return local[
        [key not in reviewed_keys for key in keys]
    ].reset_index(drop=True)


def review_new_only_padding(frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        return pd.Timedelta(days=7)

    if frequency == "5min":
        return pd.Timedelta(days=1)

    return pd.Timedelta(days=3)


def bounded_timestamp(value, fallback=None):
    if value is None:
        return fallback

    timestamp = pd.to_datetime(
        value,
        utc=True,
        errors="coerce",
    )

    if pd.isna(timestamp):
        return fallback

    return timestamp


def format_fetch_timestamp(timestamp):
    if timestamp is None or pd.isna(timestamp):
        return None

    return pd.Timestamp(timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")


def review_new_only_fetch_window(
    local_bars,
    symbol,
    frequency,
    reviewed_keys,
    start=None,
    end=None,
):
    unreviewed = unreviewed_local_bars(
        local_bars,
        symbol,
        frequency,
        reviewed_keys,
    )

    if len(unreviewed) == 0:
        return {
            "skip_reference_fetch": True,
            "start": None,
            "end": None,
            "unreviewed_bars": 0,
        }

    timestamps = pd.to_datetime(
        unreviewed["timestamp"],
        utc=True,
        errors="coerce",
    ).dropna()

    if timestamps.empty:
        return {
            "skip_reference_fetch": False,
            "start": start,
            "end": end,
            "unreviewed_bars": len(unreviewed),
        }

    padding = review_new_only_padding(frequency)
    fetch_start = timestamps.min() - padding
    fetch_end = timestamps.max() + padding
    user_start = bounded_timestamp(start)
    user_end = bounded_timestamp(end)

    if user_start is not None:
        fetch_start = max(fetch_start, user_start)

    if user_end is not None:
        fetch_end = min(fetch_end, user_end)

    return {
        "skip_reference_fetch": False,
        "start": format_fetch_timestamp(fetch_start),
        "end": format_fetch_timestamp(fetch_end),
        "unreviewed_bars": len(unreviewed),
    }


def skipped_review_new_only_summary(symbol, frequency, local_bars, threshold_pct):
    local = normalize_for_comparison(local_bars, frequency)

    return pd.DataFrame(
        [
            {
                "symbol": normalize_symbol(symbol),
                "frequency": normalize_frequency(frequency),
                "local_bars": len(local),
                "reference_bars": 0,
                "matched_bars": 0,
                "missing_local_bars": 0,
                "missing_reference_bars": 0,
                "max_pct_diff": np.nan,
                "mean_pct_diff": np.nan,
                "approved_bars": 0,
                "failed_bars": 0,
                "threshold_pct": threshold_pct,
                "approved": True,
            }
        ]
    )


def skipped_reference_summary(symbol, frequency, local_bars, threshold_pct):
    local = normalize_for_comparison(local_bars, frequency)

    return pd.DataFrame(
        [
            {
                "symbol": normalize_symbol(symbol),
                "frequency": normalize_frequency(frequency),
                "local_bars": len(local),
                "reference_bars": 0,
                "matched_bars": 0,
                "missing_local_bars": 0,
                "missing_reference_bars": 0,
                "max_pct_diff": np.nan,
                "mean_pct_diff": np.nan,
                "approved_bars": 0,
                "failed_bars": 0,
                "threshold_pct": threshold_pct,
                "approved": True,
            }
        ]
    )


def actionable_auto_review(auto_review):
    if auto_review is None or len(auto_review) == 0:
        return pd.DataFrame(columns=APPLIED_AUTO_REVIEW_COLUMNS)

    return auto_review[
        auto_review["decision"].isin(["local", "yahoo"])
    ].copy().reindex(columns=APPLIED_AUTO_REVIEW_COLUMNS)


def to_json_records(frame):
    if frame is None or len(frame) == 0:
        return []

    safe = frame.replace([np.inf, -np.inf], np.nan).copy()
    safe = safe.astype(object).where(pd.notna(safe), None)

    return safe.to_dict(orient="records")


def json_safe_value(value):
    if isinstance(value, dict):
        return {
            key: json_safe_value(nested_value)
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [json_safe_value(item) for item in value]

    if isinstance(value, tuple):
        return [json_safe_value(item) for item in value]

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, float) and not np.isfinite(value):
        return None

    if pd.isna(value):
        return None

    return value


def dashboard_reconciliation_rows(differences, max_rows=DEFAULT_DASHBOARD_MAX_ROWS):
    if differences is None or len(differences) == 0:
        return []

    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)].copy()
    all_price_diffs = price_diffs.copy()
    row_max = differences[differences["field"] == "row_max"].copy()

    if len(price_diffs) == 0 or len(row_max) == 0:
        return []

    selected_keys = sorted_dashboard_keys(row_max, max_rows=max_rows)

    if selected_keys is not None:
        selected_key_set = set(selected_keys)
        price_diffs = price_diffs[
            [
                key in selected_key_set
                for key in zip(
                    price_diffs["symbol"],
                    price_diffs["frequency"],
                    price_diffs["comparison_key"],
                )
            ]
        ].copy()

    rows = []
    confirmed_lookup = build_confirmed_price_lookup(all_price_diffs, row_max)

    for key, group in price_diffs.groupby(
        ["symbol", "frequency", "comparison_key"],
        dropna=False,
    ):
        row = {
            "symbol": key[0],
            "frequency": key[1],
            "comparison_key": key[2],
            "threshold_pct": group["threshold_pct"].max(),
            "approved": bool(group["approved"].fillna(False).all()),
            "max_pct_diff": group["pct_diff"].max(),
        }

        for _, diff in group.iterrows():
            field = diff["field"]
            row[f"local_{field}"] = diff["local_value"]
            row[f"reference_{field}"] = diff["reference_value"]
            row[f"pct_diff_{field}"] = diff["pct_diff"]

        row.update(
            confirmed_neighbor_context(
                confirmed_lookup,
                symbol=key[0],
                frequency=key[1],
                comparison_key=key[2],
            )
        )
        row.update(auto_review_decision(row))

        rows.append(row)

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            bool(row.get("approved", False)),
            -float(row.get("max_pct_diff") or 0),
            str(row.get("symbol", "")),
            str(row.get("frequency", "")),
            str(row.get("comparison_key", "")),
        ),
    )

    if max_rows and max_rows > 0:
        return sorted_rows[:max_rows]

    return sorted_rows


def sorted_dashboard_keys(row_max, max_rows=DEFAULT_DASHBOARD_MAX_ROWS):
    if max_rows is None or max_rows <= 0:
        return None

    working = row_max.copy()
    working["_approved_sort"] = working["approved"].fillna(False).astype(bool)
    working["_pct_sort"] = pd.to_numeric(
        working["pct_diff"],
        errors="coerce",
    ).fillna(0)
    working = working.sort_values(
        ["_approved_sort", "_pct_sort", "symbol", "frequency", "comparison_key"],
        ascending=[True, False, True, True, True],
    ).head(max_rows)

    return list(
        zip(
            working["symbol"],
            working["frequency"],
            working["comparison_key"],
        )
    )


def build_confirmed_price_lookup(price_diffs, row_max):
    confirmed = row_max[row_max["approved"].fillna(False)].copy()

    if len(confirmed) == 0:
        return {}

    close_diffs = price_diffs[price_diffs["field"] == "close"].copy()
    confirmed_closes = pd.merge(
        confirmed[["symbol", "frequency", "comparison_key"]],
        close_diffs[
            [
                "symbol",
                "frequency",
                "comparison_key",
                "local_value",
                "reference_value",
            ]
        ],
        on=["symbol", "frequency", "comparison_key"],
        how="left",
    )
    lookup = {}

    for key, group in confirmed_closes.groupby(["symbol", "frequency"]):
        working = group.copy()
        working["_sort_key"] = pd.to_datetime(
            working["comparison_key"],
            utc=True,
            errors="coerce",
        )
        working = working[working["_sort_key"].notna()].copy()
        working = working.sort_values(
            ["_sort_key", "comparison_key"],
            na_position="last",
        )
        records = working[
            ["comparison_key", "local_value", "reference_value", "_sort_key"]
        ].to_dict(orient="records")
        sort_keys = [
            record["_sort_key"].value
            for record in records
        ]
        lookup[key] = {
            "sort_keys": sort_keys,
            "records": records,
        }

    return lookup


def confirmed_neighbor_context(confirmed_lookup, symbol, frequency, comparison_key):
    confirmed_group = confirmed_lookup.get((symbol, frequency), {})
    confirmed_rows = confirmed_group.get("records", [])
    sort_keys = confirmed_group.get("sort_keys", [])
    target_time = pd.to_datetime(
        comparison_key,
        utc=True,
        errors="coerce",
    )

    if pd.isna(target_time) or not confirmed_rows:
        previous_row = None
        next_row = None
    else:
        insertion_index = bisect.bisect_left(sort_keys, target_time.value)
        previous_row = (
            confirmed_rows[insertion_index - 1]
            if insertion_index > 0
            else None
        )
        next_index = bisect.bisect_right(sort_keys, target_time.value)
        next_row = (
            confirmed_rows[next_index]
            if next_index < len(confirmed_rows)
            else None
        )

    return {
        "previous_confirmed_key": previous_row.get("comparison_key") if previous_row else None,
        "previous_confirmed_local_close": previous_row.get("local_value") if previous_row else None,
        "previous_confirmed_reference_close": previous_row.get("reference_value") if previous_row else None,
        "next_confirmed_key": next_row.get("comparison_key") if next_row else None,
        "next_confirmed_local_close": next_row.get("local_value") if next_row else None,
        "next_confirmed_reference_close": next_row.get("reference_value") if next_row else None,
    }


def finite_values(values):
    clean = []

    for value in values:
        numeric = pd.to_numeric(value, errors="coerce")

        if pd.notna(numeric) and np.isfinite(numeric):
            clean.append(float(numeric))

    return clean


def mean_abs_distance(value, anchors):
    numeric_value = pd.to_numeric(value, errors="coerce")
    clean_anchors = finite_values(anchors)

    if pd.isna(numeric_value) or not np.isfinite(numeric_value) or not clean_anchors:
        return np.nan

    return float(
        np.mean(
            [
                abs(float(numeric_value) - anchor)
                for anchor in clean_anchors
            ]
        )
    )


def auto_review_decision(row):
    local_close = pd.to_numeric(row.get("local_close"), errors="coerce")
    reference_close = pd.to_numeric(row.get("reference_close"), errors="coerce")
    anchors = finite_values(
        [
            row.get("previous_confirmed_local_close"),
            row.get("previous_confirmed_reference_close"),
            row.get("next_confirmed_local_close"),
            row.get("next_confirmed_reference_close"),
        ]
    )
    local_distance = mean_abs_distance(local_close, anchors)
    reference_distance = mean_abs_distance(reference_close, anchors)

    if pd.notna(local_close) and pd.isna(reference_close):
        decision = "local"
        reason = "Yahoo/reference value missing; prefer local."
    elif pd.isna(local_close) and pd.notna(reference_close):
        if pd.notna(reference_distance):
            decision = "yahoo"
            reason = "Local value missing; Yahoo is near confirmed neighbor close(s)."
        else:
            decision = "review"
            reason = "Local value missing, but no confirmed neighbor close is available."
    elif pd.isna(local_close) and pd.isna(reference_close):
        decision = "review"
        reason = "Both local and Yahoo/reference values are missing."
    elif pd.notna(local_distance) and pd.notna(reference_distance) and reference_distance < local_distance:
        decision = "yahoo"
        reason = "Yahoo/reference close is closer to confirmed neighbor close(s)."
    else:
        decision = "local"
        reason = "Prefer local data unless Yahoo/reference is closer to confirmed neighbor close(s)."

    return {
        "auto_decision": decision,
        "auto_reason": reason,
        "local_neighbor_distance": local_distance,
        "reference_neighbor_distance": reference_distance,
    }


def auto_review_record(row):
    record = {column: row.get(column) for column in AUTO_REVIEW_COLUMNS}
    record["decision"] = row.get("auto_decision")

    return record


def bar_lookup_by_comparison_key(bars, frequency):
    normalized = normalize_for_comparison(bars, frequency)

    if len(normalized) == 0:
        return {}

    normalized = normalized.drop_duplicates("comparison_key", keep="last")

    return normalized.set_index("comparison_key")[PRICE_COLUMNS].to_dict(
        orient="index"
    )


def build_missing_auto_review_rows(
    missing,
    local_bars,
    reference_bars,
    confirmed_lookup,
    symbol,
    frequency,
):
    if missing is None or len(missing) == 0:
        return []

    local_lookup = bar_lookup_by_comparison_key(local_bars, frequency)
    reference_lookup = bar_lookup_by_comparison_key(reference_bars, frequency)
    rows = []

    for _, missing_row in missing.iterrows():
        comparison_key = missing_row["comparison_key"]
        row = {
            "symbol": symbol,
            "frequency": frequency,
            "comparison_key": comparison_key,
            "approved": False,
            "max_pct_diff": np.nan,
            "missing_from": missing_row.get("missing_from"),
        }

        for field in PRICE_COLUMNS:
            row[f"local_{field}"] = local_lookup.get(comparison_key, {}).get(field)
            row[f"reference_{field}"] = reference_lookup.get(comparison_key, {}).get(field)

        row.update(
            confirmed_neighbor_context(
                confirmed_lookup,
                symbol=symbol,
                frequency=frequency,
                comparison_key=comparison_key,
            )
        )
        row.update(auto_review_decision(row))
        rows.append(row)

    return rows


def build_auto_review_decisions(
    differences,
    missing,
    local_bars,
    reference_bars,
    symbol,
    frequency,
):
    symbol = normalize_symbol(symbol)
    frequency = normalize_frequency(frequency)
    rows = []
    row_max = differences[differences["field"] == "row_max"].copy()
    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)].copy()
    confirmed_lookup = build_confirmed_price_lookup(price_diffs, row_max)

    rows.extend(
        [
            row
            for row in dashboard_reconciliation_rows(differences, max_rows=0)
            if not row.get("approved", False)
        ]
    )
    rows.extend(
        build_missing_auto_review_rows(
            missing=missing,
            local_bars=local_bars,
            reference_bars=reference_bars,
            confirmed_lookup=confirmed_lookup,
            symbol=symbol,
            frequency=frequency,
        )
    )

    if not rows:
        return pd.DataFrame(columns=AUTO_REVIEW_COLUMNS + ["decision"])

    records = [auto_review_record(row) for row in rows]

    return pd.DataFrame(records).reindex(
        columns=AUTO_REVIEW_COLUMNS + ["decision"]
    )


def write_validation_dashboard(
    output_dir,
    summary,
    differences,
    missing,
    duplicates,
    auto_review=None,
    timezone_alignment=None,
    auto_apply_result=None,
    heatmap_path=None,
    server_enabled=False,
    dashboard_max_rows=DEFAULT_DASHBOARD_MAX_ROWS,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = output_dir / DASHBOARD_FILE
    heatmap_src = (
        Path(heatmap_path).name
        if heatmap_path
        else ""
    )
    payload = {
        "summary": to_json_records(summary),
        "differences": dashboard_reconciliation_rows(
            differences,
            max_rows=dashboard_max_rows,
        ),
        "totalReconciliationRows": count_reconciliation_rows(differences),
        "dashboardMaxRows": dashboard_max_rows,
        "missing": to_json_records(
            missing.head(dashboard_max_rows)
            if dashboard_max_rows and dashboard_max_rows > 0
            else missing
        ),
        "totalMissingRows": len(missing),
        "duplicates": to_json_records(duplicates),
        "autoReview": to_json_records(
            auto_review.head(dashboard_max_rows)
            if auto_review is not None and dashboard_max_rows and dashboard_max_rows > 0
            else auto_review
        ),
        "totalAutoReviewRows": len(auto_review) if auto_review is not None else 0,
        "timezoneAlignment": to_json_records(timezone_alignment),
        "autoApplyResult": auto_apply_result or {},
        "heatmapSrc": heatmap_src,
        "serverEnabled": server_enabled,
    }
    html = dashboard_html(payload)
    dashboard_path.write_text(html, encoding="utf-8")

    return dashboard_path


def count_reconciliation_rows(differences):
    if differences is None or len(differences) == 0:
        return 0

    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)]

    if len(price_diffs) == 0:
        return 0

    return len(
        price_diffs.groupby(
            ["symbol", "frequency", "comparison_key"],
            dropna=False,
        )
    )


def dashboard_html(payload):
    payload_json = json.dumps(json_safe_value(payload), allow_nan=False)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Data Validation</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #64707d;
      --line: #d9dde2;
      --good: #146c43;
      --bad: #b42318;
      --warn: #9a5b00;
      --accent: #245b73;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 24px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 17px; letter-spacing: 0; }}
    main {{ padding: 20px 28px 36px; }}
    section {{ margin-bottom: 24px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 14px;
    }}
    .metric-label {{ color: var(--muted); font-size: 12px; }}
    .metric-value {{ font-size: 22px; font-weight: 700; margin-top: 5px; }}
    .good {{ color: var(--good); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .warn {{ color: var(--warn); font-weight: 700; }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin: 0 0 12px;
    }}
    button, select, input {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
      font-size: 13px;
    }}
    button {{ cursor: pointer; background: var(--accent); color: #fff; border-color: var(--accent); }}
    button.secondary {{ background: #fff; color: var(--ink); border-color: var(--line); }}
    .table-wrap {{ overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 9px; text-align: right; font-size: 12px; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #f0f2f3; z-index: 1; color: #38424c; }}
    td:first-child, th:first-child, td:nth-child(2), th:nth-child(2), td:nth-child(3), th:nth-child(3) {{ text-align: left; }}
    tr.failed {{ background: #fff5f3; }}
    tr.approved {{ background: #f4fbf7; }}
    .heatmap {{ background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 12px; }}
    .heatmap img {{ max-width: 100%; height: auto; display: block; }}
    .choice {{ display: inline-flex; gap: 8px; align-items: center; }}
    .choice label {{ display: inline-flex; gap: 4px; align-items: center; }}
  </style>
</head>
<body>
  <header>
    <h1>Market Data Validation</h1>
    <div class="subtle">Compare locally stored Schwab/API bars to Yahoo Finance continuous futures reference data.</div>
  </header>
  <main>
    <section>
      <div class="cards" id="cards"></div>
    </section>
    <section id="heatmap-section">
      <h2>Difference Heatmap</h2>
      <div class="heatmap" id="heatmap"></div>
    </section>
    <section>
      <h2>Summary</h2>
      <div class="table-wrap"><table id="summary-table"></table></div>
    </section>
    <section>
      <h2>Timezone Alignment</h2>
      <div class="table-wrap"><table id="timezone-table"></table></div>
    </section>
    <section>
      <h2>Reconciliation Choices</h2>
      <div class="toolbar">
        <select id="filter-status">
          <option value="all">All rows</option>
          <option value="failed" selected>Failed only</option>
          <option value="approved">Approved only</option>
        </select>
        <button class="secondary" id="choose-local">Mark visible: keep local/Schwab</button>
        <button class="secondary" id="choose-yahoo">Mark visible: use Yahoo</button>
        <button id="download-decisions">Download decisions CSV</button>
        <button id="download-selected-bars">Download selected bars CSV</button>
        <button id="apply-decisions">Apply selected changes</button>
      </div>
      <div class="subtle" id="apply-status" style="margin-bottom:10px;"></div>
      <div class="table-wrap"><table id="diff-table"></table></div>
    </section>
    <section>
      <h2>Missing Bars</h2>
      <div class="table-wrap"><table id="missing-table"></table></div>
    </section>
    <section>
      <h2>Auto Review Decisions</h2>
      <div class="table-wrap"><table id="auto-review-table"></table></div>
    </section>
    <section>
      <h2>Duplicate Local Bars</h2>
      <div class="table-wrap"><table id="duplicate-table"></table></div>
    </section>
  </main>
  <script>
    const DATA = {payload_json};
    const decisions = new Map();

    function fmt(value, digits = 4) {{
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
      return Number(value).toFixed(digits);
    }}
    function pct(value) {{ return value === null || value === undefined ? '' : `${{fmt(value, 4)}}%`; }}
    function keyFor(row) {{ return `${{row.symbol}}|${{row.frequency}}|${{row.comparison_key}}`; }}
    function sourceChoice(row) {{ return decisions.get(keyFor(row)) || row.auto_decision || row.decision || (row.approved ? 'local' : 'review'); }}
    function setChoice(row, choice) {{ decisions.set(keyFor(row), choice); }}
    function visibleDiffRows() {{
      const filter = document.getElementById('filter-status').value;
      return DATA.differences.filter(row => filter === 'all' || (filter === 'failed' && !row.approved) || (filter === 'approved' && row.approved));
    }}
    function renderCards() {{
      const summary = DATA.summary || [];
      const approved = summary.filter(row => row.approved).length;
      const failedBars = summary.reduce((sum, row) => sum + Number(row.failed_bars || 0), 0);
      const missing = summary.reduce((sum, row) => sum + Number(row.missing_local_bars || 0) + Number(row.missing_reference_bars || 0), 0);
      const maxDiff = Math.max(...summary.map(row => Number(row.max_pct_diff || 0)));
      const displayedRows = `${{DATA.differences.length}} / ${{DATA.totalReconciliationRows || DATA.differences.length}}`;
      const autoApplied = DATA.autoApplyResult && DATA.autoApplyResult.updated_bars;
      const cards = [
        ['Groups Approved', `${{approved}} / ${{summary.length}}`, approved === summary.length ? 'good' : 'bad'],
        ['Failed Bars', failedBars, failedBars === 0 ? 'good' : 'bad'],
        ['Missing Bars', missing, missing === 0 ? 'good' : 'warn'],
        ['Largest Difference', pct(maxDiff), maxDiff <= 0.25 ? 'good' : 'bad'],
        ['Review Rows Loaded', displayedRows, DATA.differences.length ? 'warn' : 'bad'],
        ['Auto Applied', autoApplied || 0, autoApplied ? 'good' : 'warn'],
      ];
      document.getElementById('cards').innerHTML = cards.map(([label, value, klass]) => `<div class="card"><div class="metric-label">${{label}}</div><div class="metric-value ${{klass}}">${{value}}</div></div>`).join('');
    }}
    function renderHeatmap() {{
      const el = document.getElementById('heatmap');
      if (!DATA.heatmapSrc) {{ el.innerHTML = '<span class="subtle">No heatmap available.</span>'; return; }}
      el.innerHTML = `<img src="${{DATA.heatmapSrc}}" alt="Market data difference heatmap">`;
    }}
    function renderSimpleTable(id, rows, columns) {{
      const table = document.getElementById(id);
      if (!rows || rows.length === 0) {{ table.innerHTML = '<tbody><tr><td>No rows.</td></tr></tbody>'; return; }}
      table.innerHTML = `<thead><tr>${{columns.map(col => `<th>${{col}}</th>`).join('')}}</tr></thead><tbody>` +
        rows.map(row => `<tr>${{columns.map(col => `<td>${{row[col] ?? ''}}</td>`).join('')}}</tr>`).join('') + '</tbody>';
    }}
    function renderSummary() {{
      const cols = ['symbol','frequency','local_bars','reference_bars','matched_bars','missing_local_bars','missing_reference_bars','max_pct_diff','mean_pct_diff','approved_bars','failed_bars','threshold_pct','approved'];
      renderSimpleTable('summary-table', DATA.summary, cols);
    }}
    function renderDifferences() {{
      const rows = visibleDiffRows();
      const table = document.getElementById('diff-table');
      const headers = ['symbol','frequency','comparison_key','max_pct_diff','auto','previous confirmed close','open','high','low','close','next confirmed close','decision'];
      table.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join('')}}</tr></thead><tbody>` + rows.map(row => {{
        const klass = row.approved ? 'approved' : 'failed';
        const choice = sourceChoice(row);
        const fields = ['open','high','low','close'].map(field => `<td>Local ${{fmt(row[`local_${{field}}`])}}<br>Yahoo ${{fmt(row[`reference_${{field}}`])}}<br><span class="${{Number(row[`pct_diff_${{field}}`] || 0) > Number(row.threshold_pct || 0) ? 'bad' : 'good'}}">${{pct(row[`pct_diff_${{field}}`])}}</span></td>`).join('');
        const previousConfirmed = `<td>${{row.previous_confirmed_key || ''}}<br>Local ${{fmt(row.previous_confirmed_local_close)}}<br>Yahoo ${{fmt(row.previous_confirmed_reference_close)}}</td>`;
        const nextConfirmed = `<td>${{row.next_confirmed_key || ''}}<br>Local ${{fmt(row.next_confirmed_local_close)}}<br>Yahoo ${{fmt(row.next_confirmed_reference_close)}}</td>`;
        const auto = `<td><span class="${{row.auto_decision === 'yahoo' ? 'warn' : row.auto_decision === 'local' ? 'good' : 'bad'}}">${{row.auto_decision || ''}}</span><br>${{row.auto_reason || ''}}</td>`;
        return `<tr class="${{klass}}"><td>${{row.symbol}}</td><td>${{row.frequency}}</td><td>${{row.comparison_key}}</td><td>${{pct(row.max_pct_diff)}}</td>${{auto}}${{previousConfirmed}}${{fields}}${{nextConfirmed}}<td><span class="choice"><label><input type="radio" name="${{keyFor(row)}}" value="local" ${{choice === 'local' ? 'checked' : ''}}>Local</label><label><input type="radio" name="${{keyFor(row)}}" value="yahoo" ${{choice === 'yahoo' ? 'checked' : ''}}>Yahoo</label><label><input type="radio" name="${{keyFor(row)}}" value="review" ${{choice === 'review' ? 'checked' : ''}}>Review</label></span></td></tr>`;
      }}).join('') + '</tbody>';
      table.querySelectorAll('input[type="radio"]').forEach(input => input.addEventListener('change', event => {{ decisions.set(event.target.name, event.target.value); }}));
    }}
    function csvEscape(value) {{
      const text = value === null || value === undefined ? '' : String(value);
      return /[",\\n]/.test(text) ? `"${{text.replaceAll('"', '""')}}"` : text;
    }}
    function downloadDecisions() {{
      const header = ['symbol','frequency','comparison_key','decision','auto_decision','auto_reason','approved','max_pct_diff','local_neighbor_distance','reference_neighbor_distance','local_open','reference_open','local_high','reference_high','local_low','reference_low','local_close','reference_close'];
      const lines = [header.join(',')];
      selectedDecisionRows().forEach(row => {{
        const values = [row.symbol,row.frequency,row.comparison_key,row.decision,row.auto_decision,row.auto_reason,row.approved,row.max_pct_diff,row.local_neighbor_distance,row.reference_neighbor_distance,row.local_open,row.reference_open,row.local_high,row.reference_high,row.local_low,row.reference_low,row.local_close,row.reference_close];
        lines.push(values.map(csvEscape).join(','));
      }});
      const blob = new Blob([lines.join('\\n')], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'market_data_reconciliation_decisions.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    function downloadSelectedBars() {{
      const header = ['symbol','frequency','comparison_key','selected_source','open','high','low','close','max_pct_diff','approved'];
      const lines = [header.join(',')];
      selectedDecisionRows().forEach(row => {{
        const choice = row.decision;
        const prefix = choice === 'yahoo' ? 'reference' : choice === 'local' ? 'local' : '';
        const values = [
          row.symbol,
          row.frequency,
          row.comparison_key,
          choice,
          prefix ? row[`${{prefix}}_open`] : '',
          prefix ? row[`${{prefix}}_high`] : '',
          prefix ? row[`${{prefix}}_low`] : '',
          prefix ? row[`${{prefix}}_close`] : '',
          row.max_pct_diff,
          row.approved,
        ];
        lines.push(values.map(csvEscape).join(','));
      }});
      const blob = new Blob([lines.join('\\n')], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'market_data_selected_bars.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    function selectedDecisionRows() {{
      const sourceRows = DATA.autoReview && DATA.autoReview.length ? DATA.autoReview : DATA.differences;
      return sourceRows.map(row => ({{ ...row, decision: sourceChoice(row) }}));
    }}
    async function applyDecisions() {{
      const status = document.getElementById('apply-status');
      if (!DATA.serverEnabled) {{
        status.innerHTML = 'Static dashboard mode: download a CSV or rerun with <code>--serve-dashboard</code> to apply changes directly.';
        return;
      }}
      const actionable = selectedDecisionRows().filter(row => row.decision === 'local' || row.decision === 'yahoo');
      status.textContent = `Applying ${{actionable.length}} reviewed bar choices...`;
      try {{
        const response = await fetch('/api/apply-decisions', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ decisions: actionable }}),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Apply failed');
        status.textContent = `Applied ${{payload.updated_bars}} bars and marked ${{payload.reviewed_bars}} bars reviewed. Reviewed file: ${{payload.reviewed_file}}`;
      }} catch (error) {{
        status.textContent = `Apply failed: ${{error.message}}`;
      }}
    }}
    document.getElementById('filter-status').addEventListener('change', renderDifferences);
    document.getElementById('choose-local').addEventListener('click', () => {{ visibleDiffRows().forEach(row => setChoice(row, 'local')); renderDifferences(); }});
    document.getElementById('choose-yahoo').addEventListener('click', () => {{ visibleDiffRows().forEach(row => setChoice(row, 'yahoo')); renderDifferences(); }});
    document.getElementById('download-decisions').addEventListener('click', downloadDecisions);
    document.getElementById('download-selected-bars').addEventListener('click', downloadSelectedBars);
    document.getElementById('apply-decisions').addEventListener('click', applyDecisions);
    document.getElementById('apply-status').innerHTML = DATA.serverEnabled ? 'Server mode: Apply selected changes will update local market data and write reviewed_bars.csv.' : 'Static dashboard mode: choices can be downloaded, but direct updates require <code>--serve-dashboard</code>.';
    if (DATA.autoApplyResult && DATA.autoApplyResult.updated_bars) {{
      document.getElementById('apply-status').innerHTML = `Auto-applied ${{DATA.autoApplyResult.updated_bars}} bars and marked ${{DATA.autoApplyResult.reviewed_bars}} bars reviewed. Reviewed file: ${{DATA.autoApplyResult.reviewed_file || ''}}`;
    }}
    if ((DATA.totalReconciliationRows || 0) > DATA.differences.length) {{
      document.getElementById('apply-status').innerHTML += ` Showing the top ${{DATA.differences.length}} review rows sorted by failed/largest difference. Increase with <code>--dashboard-max-rows</code>.`;
    }}
    renderCards();
    renderHeatmap();
    renderSummary();
    renderSimpleTable('timezone-table', DATA.timezoneAlignment, ['symbol','frequency','local_bars','reference_bars','exact_utc_matches','best_shift_minutes','best_shift_matches','status','detail']);
    renderDifferences();
    renderSimpleTable('missing-table', DATA.missing, ['symbol','frequency','comparison_key','missing_from']);
    if ((DATA.totalMissingRows || 0) > DATA.missing.length) {{
      const table = document.getElementById('missing-table');
      table.insertAdjacentHTML('beforebegin', `<div class="subtle" style="padding:0 0 8px;">Showing ${{DATA.missing.length}} / ${{DATA.totalMissingRows}} missing-bar rows. Increase with <code>--dashboard-max-rows</code>.</div>`);
    }}
    renderSimpleTable('auto-review-table', DATA.autoReview, ['symbol','frequency','comparison_key','auto_decision','auto_reason','local_neighbor_distance','reference_neighbor_distance','previous_confirmed_key','next_confirmed_key','local_close','reference_close']);
    renderSimpleTable('duplicate-table', DATA.duplicates, ['symbol','frequency','comparison_key','timestamp','open','high','low','close']);
  </script>
</body>
</html>"""


def collect_validation_data(
    source_dir,
    symbols=None,
    frequencies=None,
    reference_frequencies=None,
    threshold_pct=DEFAULT_THRESHOLD_PCT,
    start=None,
    end=None,
    reference_fetcher=fetch_yahoo_bars,
    review_new_only=False,
    reviewed_keys=None,
):
    symbols = symbols or DEFAULT_SYMBOLS
    frequencies = frequencies or ["daily", "5min", "60min"]
    reference_frequencies = {
        normalize_frequency(frequency)
        for frequency in (
            reference_frequencies
            if reference_frequencies is not None
            else DEFAULT_REFERENCE_FREQUENCIES
        )
    }
    total_jobs = len(symbols) * len(frequencies)
    completed_jobs = 0
    run_start = time.monotonic()
    all_differences = []
    all_missing = []
    all_duplicates = []
    all_summary = []
    all_auto_review = []
    all_timezone_alignment = []

    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)

        for raw_frequency in frequencies:
            job_number = completed_jobs + 1
            job_start = time.monotonic()
            frequency = normalize_frequency(raw_frequency)
            log_progress(
                f"[{job_number}/{total_jobs}] Validating {symbol} "
                f"{frequency}: loading local bars..."
            )
            local_bars = load_local_bars(source_dir, symbol, frequency)
            duplicates = find_duplicate_bars(
                local_bars,
                symbol=symbol,
                frequency=frequency,
            )

            fetch_start = start
            fetch_end = end
            reviewed_skipped = 0
            fetch_window = None
            skip_reference = frequency not in reference_frequencies

            if skip_reference:
                log_progress(
                    f"[{job_number}/{total_jobs}] Skipping Yahoo for "
                    f"{symbol} {frequency}: frequency not selected for "
                    "reference validation."
                )
                reference_bars = pd.DataFrame(columns=[])
                differences = pd.DataFrame(columns=DIFFERENCE_COLUMNS)
                missing = pd.DataFrame(columns=MISSING_COLUMNS)
                summary = skipped_reference_summary(
                    symbol=symbol,
                    frequency=frequency,
                    local_bars=local_bars,
                    threshold_pct=threshold_pct,
                )
                auto_review = pd.DataFrame(
                    columns=AUTO_REVIEW_COLUMNS + ["decision"]
                )
                timezone_alignment = build_timezone_alignment_report(
                    local_bars=pd.DataFrame(columns=[]),
                    reference_bars=pd.DataFrame(columns=[]),
                    symbol=symbol,
                    frequency=frequency,
                )
            elif review_new_only:
                fetch_window = review_new_only_fetch_window(
                    local_bars=local_bars,
                    symbol=symbol,
                    frequency=frequency,
                    reviewed_keys=reviewed_keys or set(),
                    start=start,
                    end=end,
                )

            if (
                not skip_reference
                and fetch_window is not None
                and fetch_window["skip_reference_fetch"]
            ):
                log_progress(
                    f"[{job_number}/{total_jobs}] Skipping Yahoo for "
                    f"{symbol} {frequency}: no unreviewed local bars."
                )
                reference_bars = pd.DataFrame(columns=[])
                differences = pd.DataFrame(columns=DIFFERENCE_COLUMNS)
                missing = pd.DataFrame(columns=MISSING_COLUMNS)
                summary = skipped_review_new_only_summary(
                    symbol=symbol,
                    frequency=frequency,
                    local_bars=local_bars,
                    threshold_pct=threshold_pct,
                )
                auto_review = pd.DataFrame(
                    columns=AUTO_REVIEW_COLUMNS + ["decision"]
                )
                timezone_alignment = build_timezone_alignment_report(
                    local_bars=pd.DataFrame(columns=[]),
                    reference_bars=pd.DataFrame(columns=[]),
                    symbol=symbol,
                    frequency=frequency,
                )
            elif not skip_reference:
                if fetch_window is not None:
                    fetch_start = fetch_window["start"]
                    fetch_end = fetch_window["end"]
                    log_progress(
                        f"[{job_number}/{total_jobs}] Fetching Yahoo for "
                        f"{symbol} {frequency} around "
                        f"{fetch_window['unreviewed_bars']} unreviewed "
                        "local bars..."
                    )
                else:
                    log_progress(
                        f"[{job_number}/{total_jobs}] Fetching Yahoo for "
                        f"{symbol} {frequency}..."
                    )

                reference_bars = reference_fetcher(
                    symbol=symbol,
                    frequency=frequency,
                    start=fetch_start,
                    end=fetch_end,
                )
                differences, missing, summary = compare_market_data(
                    local_bars=local_bars,
                    reference_bars=reference_bars,
                    symbol=symbol,
                    frequency=frequency,
                    threshold_pct=threshold_pct,
                )
                auto_review = build_auto_review_decisions(
                    differences=differences,
                    missing=missing,
                    local_bars=local_bars,
                    reference_bars=reference_bars,
                    symbol=symbol,
                    frequency=frequency,
                )
                auto_review_total = len(auto_review)
                if review_new_only:
                    auto_review = filter_reviewed_auto_review(
                        auto_review,
                        reviewed_keys or set(),
                    )
                reviewed_skipped = auto_review_total - len(auto_review)
                timezone_alignment = build_timezone_alignment_report(
                    local_bars=local_bars,
                    reference_bars=reference_bars,
                    symbol=symbol,
                    frequency=frequency,
                )

            all_differences.append(differences)
            all_missing.append(missing)
            all_summary.append(summary)
            all_auto_review.append(auto_review)
            all_timezone_alignment.append(timezone_alignment)

            if len(duplicates) > 0:
                all_duplicates.append(duplicates)

            completed_jobs += 1
            summary_row = summary.iloc[0]
            log_progress(
                f"[{completed_jobs}/{total_jobs}] Completed {symbol} "
                f"{frequency}: local {summary_row['local_bars']} bars, "
                f"Yahoo {summary_row['reference_bars']} bars, "
                f"failed {summary_row['failed_bars']}, "
                f"missing local {summary_row['missing_local_bars']}, "
                f"missing Yahoo {summary_row['missing_reference_bars']}, "
                f"auto-review {len(auto_review)} rows"
                f"{f' ({reviewed_skipped} reviewed skipped)' if reviewed_skipped else ''}, "
                f"step {elapsed_text(time.monotonic() - job_start)}, "
                f"ETA {eta_text(run_start, completed_jobs, total_jobs)}."
            )

    return {
        "differences": pd.concat(all_differences, ignore_index=True),
        "missing": pd.concat(all_missing, ignore_index=True),
        "summary": pd.concat(all_summary, ignore_index=True),
        "duplicates": (
            pd.concat(all_duplicates, ignore_index=True)
            if all_duplicates
            else pd.DataFrame(columns=DUPLICATE_COLUMNS)
        ),
        "auto_review": (
            pd.concat(all_auto_review, ignore_index=True)
            if all_auto_review
            else pd.DataFrame(columns=AUTO_REVIEW_COLUMNS + ["decision"])
        ),
        "timezone_alignment": (
            pd.concat(all_timezone_alignment, ignore_index=True)
            if all_timezone_alignment
            else pd.DataFrame(columns=TIMEZONE_ALIGNMENT_COLUMNS)
        ),
    }


def validate_market_data(
    source_dir=DEFAULT_SOURCE_DIR,
    output_dir=DEFAULT_OUTPUT_DIR,
    symbols=None,
    frequencies=None,
    reference_frequencies=None,
    threshold_pct=DEFAULT_THRESHOLD_PCT,
    start=None,
    end=None,
    reference_fetcher=fetch_yahoo_bars,
    dashboard_max_rows=DEFAULT_DASHBOARD_MAX_ROWS,
    auto_apply_decisions=False,
    review_new_only=False,
):
    run_start = time.monotonic()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_progress("Starting market data validation.")
    reviewed_keys = reviewed_key_set(source_dir) if review_new_only else set()
    if review_new_only:
        log_progress(
            f"Review-new-only mode: loaded {len(reviewed_keys)} reviewed "
            "symbol/frequency/bar keys."
        )
    result = collect_validation_data(
        source_dir=source_dir,
        symbols=symbols,
        frequencies=frequencies,
        reference_frequencies=reference_frequencies,
        threshold_pct=threshold_pct,
        start=start,
        end=end,
        reference_fetcher=reference_fetcher,
        review_new_only=review_new_only,
        reviewed_keys=reviewed_keys,
    )
    auto_apply_result = {}
    applied_auto_review = pd.DataFrame(columns=APPLIED_AUTO_REVIEW_COLUMNS)

    if auto_apply_decisions:
        log_progress(
            "Applying auto-review decisions marked local or Yahoo..."
        )
        applied_auto_review = actionable_auto_review(result["auto_review"])
        auto_apply_result = apply_auto_review_decisions(
            source_dir=source_dir,
            auto_review=applied_auto_review,
        )
        log_progress(
            "Auto-apply result: "
            f"{auto_apply_result.get('updated_bars', 0)} updated bars, "
            f"{auto_apply_result.get('reviewed_bars', 0)} reviewed bars."
        )

        if auto_apply_result.get("updated_bars", 0) > 0:
            log_progress(
                "Re-running validation after auto-apply so reports reflect "
                "the post-apply state."
            )
            reviewed_keys = (
                reviewed_key_set(source_dir)
                if review_new_only
                else set()
            )
            result = collect_validation_data(
                source_dir=source_dir,
                symbols=symbols,
                frequencies=frequencies,
                reference_frequencies=reference_frequencies,
                threshold_pct=threshold_pct,
                start=start,
                end=end,
                reference_fetcher=reference_fetcher,
                review_new_only=review_new_only,
                reviewed_keys=reviewed_keys,
            )

    summary = result["summary"]
    differences = result["differences"]
    missing = result["missing"]
    duplicates = result["duplicates"]
    auto_review = result["auto_review"]
    timezone_alignment = result["timezone_alignment"]
    log_progress("Writing validation heatmap, dashboard, and CSV reports...")
    heatmap_path = save_heatmap(summary, output_dir)
    dashboard_path = write_validation_dashboard(
        output_dir=output_dir,
        summary=summary,
        differences=differences,
        missing=missing,
        duplicates=duplicates,
        auto_review=auto_review,
        timezone_alignment=timezone_alignment,
        auto_apply_result=auto_apply_result,
        heatmap_path=heatmap_path,
        dashboard_max_rows=dashboard_max_rows,
    )

    summary.to_csv(output_dir / "validation_summary.csv", index=False)
    differences.to_csv(output_dir / "price_differences.csv", index=False)
    missing.to_csv(output_dir / "missing_bars.csv", index=False)
    duplicates.to_csv(output_dir / "duplicate_bars.csv", index=False)
    auto_review.to_csv(output_dir / "auto_review_decisions.csv", index=False)
    applied_auto_review.to_csv(
        output_dir / "auto_review_applied_decisions.csv",
        index=False,
    )
    timezone_alignment.to_csv(output_dir / "timezone_alignment.csv", index=False)
    log_progress(
        f"Finished market data validation in "
        f"{elapsed_text(time.monotonic() - run_start)}."
    )

    return {
        "summary": summary,
        "differences": differences,
        "missing": missing,
        "duplicates": duplicates,
        "auto_review": auto_review,
        "applied_auto_review": applied_auto_review,
        "timezone_alignment": timezone_alignment,
        "auto_apply_result": auto_apply_result,
        "heatmap_path": heatmap_path,
        "dashboard_path": dashboard_path,
    }


class ValidationDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        output_dir = self.server.output_dir
        path = self.path.split("?", 1)[0]

        if path in {"/", f"/{DASHBOARD_FILE}"}:
            file_path = output_dir / DASHBOARD_FILE
            content_type = "text/html; charset=utf-8"
        elif path == "/market_data_difference_heatmap.png":
            file_path = output_dir / "market_data_difference_heatmap.png"
            content_type = "image/png"
        else:
            self.send_error(404)
            return

        if not file_path.exists():
            self.send_error(404)
            return

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/apply-decisions":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = apply_reconciliation_decisions(
                source_dir=self.server.source_dir,
                decisions=payload.get("decisions", []),
            )
            self.send_json(200, result)
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_validation_dashboard(
    output_dir,
    source_dir,
    host="127.0.0.1",
    port=8765,
    open_browser=True,
):
    output_dir = Path(output_dir)
    source_dir = Path(source_dir)
    server = HTTPServer((host, port), ValidationDashboardHandler)
    server.output_dir = output_dir
    server.source_dir = source_dir
    url = f"http://{host}:{server.server_port}/{DASHBOARD_FILE}"

    if open_browser:
        webbrowser.open(url)

    print(f"Serving validation dashboard at {url}")
    print("Press Ctrl+C to stop the dashboard server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped validation dashboard server.")
    finally:
        server.server_close()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Validate locally stored market data against Yahoo Finance "
            "reference data."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help="Local normalized market data directory to validate.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where validation reports are written.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help=(
            "Symbols to validate, e.g. /ES /GC /CL or equities/ETFs "
            "such as SPY."
        ),
    )
    parser.add_argument(
        "--frequencies",
        nargs="+",
        default=["daily", "5min", "60min"],
        help="Frequencies to validate: daily, 5min, 60min.",
    )
    parser.add_argument(
        "--reference-frequencies",
        nargs="+",
        default=DEFAULT_REFERENCE_FREQUENCIES,
        help=(
            "Frequencies to compare against Yahoo/reference data. "
            "Default: daily. Pass daily 5min 60min to opt into "
            "Yahoo intraday validation."
        ),
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=DEFAULT_THRESHOLD_PCT,
        help="Maximum allowed OHLC percentage difference for approval.",
    )
    parser.add_argument(
        "--start",
        help="Optional inclusive validation start date/time.",
    )
    parser.add_argument(
        "--end",
        help="Optional inclusive validation end date/time.",
    )
    parser.add_argument(
        "--open-dashboard",
        action="store_true",
        help="Open the validation dashboard in your default browser.",
    )
    parser.add_argument(
        "--serve-dashboard",
        action="store_true",
        help=(
            "Serve the dashboard locally so Apply selected changes can "
            "update market data and mark bars reviewed."
        ),
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Port for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-max-rows",
        type=int,
        default=DEFAULT_DASHBOARD_MAX_ROWS,
        help=(
            "Maximum reconciliation rows embedded in the dashboard. "
            "Rows are sorted by failed/largest difference first. Use 0 "
            "to embed all rows."
        ),
    )
    parser.add_argument(
        "--auto-apply-decisions",
        action="store_true",
        help=(
            "Automatically apply auto-review decisions marked local or Yahoo "
            "and mark those bars reviewed before writing the dashboard."
        ),
    )
    parser.add_argument(
        "--review-new-only",
        action="store_true",
        help=(
            "Skip bars already listed in reviewed_bars.csv when building "
            "auto-review/apply decisions and narrow Yahoo/reference fetches "
            "to unreviewed local bars."
        ),
    )
    parser.add_argument(
        "--recheck-reviewed",
        action="store_true",
        help=(
            "Reconsider bars already listed in reviewed_bars.csv. This "
            "overrides the default new-only behavior used with "
            "--auto-apply-decisions."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()
    review_new_only = (
        args.review_new_only
        or args.auto_apply_decisions
    ) and not args.recheck_reviewed
    result = validate_market_data(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        symbols=args.symbols,
        frequencies=args.frequencies,
        reference_frequencies=args.reference_frequencies,
        threshold_pct=args.threshold_pct,
        start=args.start,
        end=args.end,
        dashboard_max_rows=args.dashboard_max_rows,
        auto_apply_decisions=args.auto_apply_decisions,
        review_new_only=review_new_only,
    )
    summary = result["summary"]
    approved_count = int(summary["approved"].fillna(False).sum())
    total_count = len(summary)
    print(
        f"Approved {approved_count}/{total_count} symbol-frequency "
        "validation groups."
    )
    print(f"Wrote: {Path(args.output_dir) / 'validation_summary.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'price_differences.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'missing_bars.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'duplicate_bars.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'auto_review_decisions.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'auto_review_applied_decisions.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'timezone_alignment.csv'}")

    if result["auto_apply_result"]:
        print(
            "Auto-applied "
            f"{result['auto_apply_result'].get('updated_bars', 0)} bars "
            "from auto-review decisions."
        )

    if result["heatmap_path"]:
        print(f"Wrote: {result['heatmap_path']}")

    if result["dashboard_path"]:
        print(f"Wrote: {result['dashboard_path']}")

        if args.serve_dashboard:
            write_validation_dashboard(
                output_dir=args.output_dir,
                summary=result["summary"],
                differences=result["differences"],
                missing=result["missing"],
                duplicates=result["duplicates"],
                auto_review=result["auto_review"],
                timezone_alignment=result["timezone_alignment"],
                auto_apply_result=result["auto_apply_result"],
                heatmap_path=result["heatmap_path"],
                server_enabled=True,
                dashboard_max_rows=args.dashboard_max_rows,
            )
            serve_validation_dashboard(
                output_dir=args.output_dir,
                source_dir=args.source_dir,
                host=args.dashboard_host,
                port=args.dashboard_port,
                open_browser=True,
            )
        elif args.open_dashboard:
            webbrowser.open(result["dashboard_path"].resolve().as_uri())


if __name__ == "__main__":
    main()
