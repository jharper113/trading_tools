import argparse
import csv
import fnmatch
import html
import json
import math
import mimetypes
import os
import re
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

os.environ.setdefault(
    "MPLCONFIGDIR",
    "/tmp/matplotlib",
)

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from calculate_risk_per_trade import (
    DEFAULT_BANKROLL,
    DEFAULT_DRAWDOWN_LIMIT,
    DEFAULT_PCT_ABOVE_DD_LIMIT,
    DEFAULT_SAFE_F_INCREMENT,
    DEFAULT_SAFE_F_START,
    DEFAULT_SIMULATIONS,
    calculate_risk_per_trade_by_strategy,
)
from extract_trade_history import (
    extracted_trade_cash_by_day,
    parse_cash_ledger,
    parse_statement_ytd_summary,
    statement_trade_cash_by_day,
    trade_account_bucket,
)
from extract_trade_history_v2 import (
    build_ytd_bridge_adjustments,
    ytd_reconciliation_rows,
)
from src.enrich import (
    FUTURES_CONTRACT_PATTERN,
    get_contract_multiplier,
    normalize_root_symbol,
)
from src.lookup import FUTURES_OPTIONS, INDEX_OPTIONS


INPUT_FILE = "./output/master_cleaned_tos_data.csv"
DATA_DIR = "./data"
MARKET_DATA_DIR = "./data/market_data"
OUTPUT_DIR = "./output/strategy_performance"
CHART_DIR = f"{OUTPUT_DIR}/charts"
STRATEGY_TRADES_DIR = f"{OUTPUT_DIR}/strategy_trades"
RISK_PER_TRADE_DIR = f"{OUTPUT_DIR}/risk_per_trade"

EQUITY_CURVES_FILE = f"{OUTPUT_DIR}/strategy_equity_curves.csv"
SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/strategy_summary_statistics.csv"
ACCOUNT_EQUITY_CURVE_FILE = f"{OUTPUT_DIR}/account_equity_curve.csv"
ACCOUNT_CASH_BALANCE_CURVE_FILE = f"{OUTPUT_DIR}/account_cash_balance_curve.csv"
ACCOUNT_SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/account_summary_statistics.csv"
BENCHMARK_SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/benchmark_summary_statistics.csv"
REALIZED_TRADES_FILE = f"{OUTPUT_DIR}/realized_trades.csv"
EXPIRED_OPTION_SETTLEMENT_FILE = f"{OUTPUT_DIR}/expired_option_settlement_check.csv"
OPEN_POSITIONS_FILE = f"{OUTPUT_DIR}/open_positions.csv"
OPEN_POSITION_AUDIT_FILE = f"{OUTPUT_DIR}/open_position_audit.csv"
OPEN_POSITIONS_REVIEW_FILE = f"{OUTPUT_DIR}/open_positions_review.csv"
OPEN_POSITIONS_DIAGNOSIS_FILE = f"{OUTPUT_DIR}/open_positions_diagnosis.csv"
DATA_QUALITY_FILE = f"{OUTPUT_DIR}/data_quality_warnings.csv"
SETTLEMENT_COVERAGE_FILE = f"{OUTPUT_DIR}/settlement_coverage.csv"
YTD_STATEMENT_SUMMARY_FILE = f"{OUTPUT_DIR}/ytd_statement_summary.csv"
YTD_POSITION_RECONCILIATION_FILE = (
    f"{OUTPUT_DIR}/ytd_position_pnl_reconciliation.csv"
)
YTD_FEE_RECONCILIATION_FILE = f"{OUTPUT_DIR}/ytd_fee_reconciliation.csv"
YTD_CLOSED_PNL_RECONCILIATION_FILE = (
    f"{OUTPUT_DIR}/ytd_closed_pnl_reconciliation.csv"
)
YTD_TRADE_REVIEW_FILE = f"{OUTPUT_DIR}/ytd_trade_adjustment_review.csv"
YTD_STRATEGY_IMPACT_FILE = f"{OUTPUT_DIR}/ytd_strategy_adjustment_impact.csv"
TRADE_HISTORY_VALIDATION_SUMMARY_FILE = (
    f"{OUTPUT_DIR}/trade_history_validation_summary.csv"
)
TRADE_HISTORY_VALIDATION_ISSUES_FILE = (
    f"{OUTPUT_DIR}/trade_history_validation_issues.csv"
)
STRATEGY_DECISION_FILE = f"{OUTPUT_DIR}/strategy_decision_board.csv"
CAPITAL_ALLOCATION_FILE = f"{OUTPUT_DIR}/capital_allocation.csv"
PNL_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_pnl_correlation.csv"
DRAWDOWN_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_drawdown_correlation.csv"
DRAWDOWN_OVERLAP_FILE = f"{OUTPUT_DIR}/strategy_drawdown_overlap.csv"
DASHBOARD_FILE = f"{OUTPUT_DIR}/strategy_dashboard.html"
OPTION_TYPES = {
    "CALL",
    "PUT",
}
HIGH_CORRELATION_THRESHOLD = 0.7
ACCOUNT_STATEMENT_PATTERNS = (
    "*AccountStatement*.csv",
    "*Statement*.csv",
)
ACCOUNT_STATEMENT_DIRS = (
    DATA_DIR,
    os.path.expanduser(
        "~/Dropbox/HarpFolders/02_Trading/thinkorswim/TOS_Account_Statements"
    ),
)
FUTURES_OPTION_SETTLEMENT_UNDERLYINGS = {
    "/MES": "/ES",
}
SETTLEMENT_SYMBOL_FILE_ALIASES = {
    "SPX": [
        "SPX",
        "^SPX",
        "$SPX",
    ],
    "XSP": [
        "XSP",
        "^XSP",
        "$XSP",
    ],
}
FUTURES_MONTH_CODES = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}


def format_duration(seconds):
    seconds = max(
        0,
        int(round(seconds)),
    )

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def progress(message):
    print(
        message,
        flush=True,
    )


def finish_progress(message, start_time):
    progress(
        f"{message} in {format_duration(time.monotonic() - start_time)}."
    )


def clean_strategy_name(value):
    if value is None or pd.isna(value):
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).strip(),
    )


def safe_filename(value):
    filename = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        clean_strategy_name(value),
    ).strip("._-")

    return filename or "strategy"


def safe_ratio(numerator, denominator):
    if denominator in {0, None} or pd.isna(denominator):
        return None

    return numerator / denominator


def statement_file_sort_key(path):
    basename = os.path.basename(str(path))
    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})",
        basename,
    )

    if match:
        return (
            1,
            pd.Timestamp("-".join(match.groups())),
            basename,
        )

    try:
        modified_time = pd.Timestamp.fromtimestamp(
            os.path.getmtime(path)
        )
    except OSError:
        modified_time = pd.Timestamp.min

    return (
        0,
        modified_time,
        basename,
    )


def statement_file_names_from_trades(cleaned_trades):
    if (
        cleaned_trades is None
        or cleaned_trades.empty
        or "statement_file" not in cleaned_trades.columns
    ):
        return []

    names = []
    for value in cleaned_trades["statement_file"].dropna():
        basename = os.path.basename(str(value).strip())
        if not basename:
            continue

        if any(
            fnmatch.fnmatch(
                basename.lower(),
                pattern.lower(),
            )
            for pattern in ACCOUNT_STATEMENT_PATTERNS
        ):
            names.append(basename)

    return sorted(
        set(names),
        key=statement_file_sort_key,
    )


def cash_balance_start_timestamp(cleaned_trades):
    if cleaned_trades is None or cleaned_trades.empty:
        return None

    timestamps = pd.to_datetime(
        cleaned_trades.get("timestamp"),
        errors="coerce",
    ).dropna()
    if timestamps.empty:
        return None

    first_timestamp = timestamps.min()
    return pd.Timestamp(
        year=first_timestamp.year,
        month=1,
        day=1,
    )


def find_latest_account_statement(
    data_dir=DATA_DIR,
    preferred_filenames=None,
):
    search_dirs = []
    if data_dir is None:
        search_dirs.extend(ACCOUNT_STATEMENT_DIRS)
    elif isinstance(data_dir, (list, tuple, set)):
        search_dirs.extend(data_dir)
    else:
        search_dirs.append(data_dir)

    candidates = []

    for search_dir in search_dirs:
        if not search_dir:
            continue

        search_dir = os.path.expanduser(str(search_dir))
        if not os.path.isdir(search_dir):
            continue

        for pattern in ACCOUNT_STATEMENT_PATTERNS:
            candidates.extend(
                os.path.join(
                    search_dir,
                    filename,
                )
                for filename in os.listdir(search_dir)
                if fnmatch.fnmatch(
                    filename.lower(),
                    pattern.lower(),
                )
            )

    candidates = [
        path
        for path in {
            path
            for path in candidates
            if os.path.isfile(path)
        }
    ]

    preferred = {
        os.path.basename(str(filename).strip())
        for filename in (preferred_filenames or [])
        if str(filename).strip()
    }
    if preferred:
        preferred_candidates = [
            path
            for path in candidates
            if os.path.basename(path) in preferred
        ]
        if preferred_candidates:
            candidates = preferred_candidates

    candidates = sorted(
        candidates,
        key=statement_file_sort_key,
    )

    if not candidates:
        return None

    return candidates[-1]


def benchmark_file_for_symbol(symbol, market_data_dir=MARKET_DATA_DIR):
    clean_symbol = str(symbol).strip().lstrip("/")

    if not clean_symbol:
        return None

    path = os.path.join(
        market_data_dir,
        "daily",
        f"{clean_symbol.upper()}.csv",
    )

    if os.path.isfile(path):
        return path

    return None


def parse_number(value):
    if value is None or pd.isna(value):
        return None

    value = str(value).strip()

    if value == "":
        return None

    value = (
        value
        .replace("$", "")
        .replace(",", "")
        .replace("+", "")
    )

    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]

    try:
        return float(value)
    except ValueError:
        return None


def load_cleaned_trades(path):
    df = pd.read_csv(path)

    required_columns = {
        "Strategy_Name",
        "Exec Time",
        "net_pnl",
        "margin_requirement",
        "return_on_margin",
        "log_return_on_margin",
        "starting_equity",
    }
    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required columns in cleaned trade history: {missing}"
        )

    df = df.copy()
    df["Strategy_Name"] = df["Strategy_Name"].apply(clean_strategy_name)
    df["timestamp"] = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    df["net_pnl"] = pd.to_numeric(
        df["net_pnl"],
        errors="coerce",
    ).fillna(0.0)
    df["margin_requirement"] = pd.to_numeric(
        df["margin_requirement"],
        errors="coerce",
    ).fillna(0.0)
    df["return_on_margin"] = pd.to_numeric(
        df["return_on_margin"],
        errors="coerce",
    )
    df["log_return_on_margin"] = pd.to_numeric(
        df["log_return_on_margin"],
        errors="coerce",
    )

    return df


def is_option_trade(row):
    return str(row.get("Type", "")).upper() in OPTION_TYPES


def parse_option_expiration(row):
    candidates = [
        row.get("Exp"),
        row.get("Symbol"),
    ]

    for candidate in candidates:
        if pd.isna(candidate):
            continue

        match = re.search(
            r"\b(\d{1,2}\s+[A-Z]{3}\s+\d{2})\b",
            str(candidate).upper(),
        )

        if match is None:
            continue

        expiration = pd.to_datetime(
            match.group(1),
            format="%d %b %y",
            errors="coerce",
        )

        if not pd.isna(expiration):
            return expiration.normalize()

    return None


def option_expiration_close(row):
    expiration = parse_option_expiration(row)

    if expiration is None:
        return None

    return expiration + pd.Timedelta(hours=16)


def parse_futures_contract_month(symbol):
    if symbol is None or pd.isna(symbol):
        return None

    match = re.match(
        f"^{FUTURES_CONTRACT_PATTERN}$",
        str(symbol).strip().upper(),
    )
    if match is None:
        return None

    contract = str(symbol).strip().upper()
    month_code = contract[-3]
    year_suffix = contract[-2:]
    month = FUTURES_MONTH_CODES.get(month_code)
    if month is None:
        return None

    try:
        year = 2000 + int(year_suffix)
    except ValueError:
        return None

    return pd.Timestamp(year=year, month=month, day=1)


def futures_contract_review_timestamp(symbol):
    contract_month = parse_futures_contract_month(symbol)

    if contract_month is None:
        return None

    return contract_month + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23)


def default_as_of_timestamp(df):
    timestamps = pd.to_datetime(
        df.get("timestamp"),
        errors="coerce",
    ).dropna()

    if timestamps.empty:
        return pd.Timestamp.today()

    return timestamps.max()


def key_value(value):
    if pd.isna(value):
        return ""

    return str(value).strip()


def normalized_spread(row):
    spread = key_value(row.get("Spread")).upper()

    if spread:
        return spread

    strategy_name = key_value(row.get("Strategy_Name")).upper()

    if "STRADDLE" in strategy_name:
        return "STRADDLE"

    if "VERTICAL" in strategy_name or "SPREAD" in strategy_name:
        return "VERTICAL"

    return spread


def position_key(row, include_strategy=True):
    spread = normalized_spread(row)
    strategy_key = (
        (key_value(row.get("Strategy_Name")),)
        if include_strategy
        else ()
    )

    if is_option_trade(row):
        if spread == "STRADDLE":
            return strategy_key + (
                key_value(row.get("Symbol")),
                key_value(row.get("Exp")),
                key_value(row.get("Strike")),
                spread,
            )

        if spread == "VERTICAL":
            return strategy_key + (
                key_value(row.get("Symbol")),
                key_value(row.get("Exp")),
                key_value(row.get("Type")),
                spread,
            )

        return strategy_key + (
            key_value(row.get("Symbol")),
            key_value(row.get("Exp")),
            key_value(row.get("Strike")),
            key_value(row.get("Type")),
            spread,
        )

    return strategy_key + (
        key_value(row.get("Symbol")),
        spread,
    )


def find_open_lots(open_lots, row, side, require_opposite_side=False):
    key = position_key(row)
    lots = open_lots.get(key)

    if lots:
        return lots

    fallback_key = position_key(row, include_strategy=False)
    matches = []

    for candidate_lots in open_lots.values():
        if not candidate_lots:
            continue

        if require_opposite_side and candidate_lots[0]["side"] == side:
            continue

        candidate_row = candidate_lots[0].get("row")

        if candidate_row is None:
            continue

        if position_key(
            candidate_row,
            include_strategy=False,
        ) == fallback_key:
            matches.append(candidate_lots)

    if len(matches) == 1:
        return matches[0]

    return None


def latest_option_expirations(df):
    latest_expirations = {}

    for _, row in df.iterrows():
        if not is_option_trade(row):
            continue

        if str(row.get("Pos Effect", "")).upper() != "TO OPEN":
            continue

        expiration = option_expiration_close(row)

        if expiration is None:
            continue

        strategy_name = key_value(row.get("Strategy_Name"))
        current_expiration = latest_expirations.get(strategy_name)

        if current_expiration is None or expiration > current_expiration:
            latest_expirations[strategy_name] = expiration

    return latest_expirations


def is_expired_option_lot(
    row,
    as_of_date,
    latest_expirations=None,
):
    if not is_option_trade(row):
        return False

    if str(row.get("Pos Effect", "")).upper() != "TO OPEN":
        return False

    expiration = option_expiration_close(row)

    if expiration is None:
        return False

    if expiration <= as_of_date:
        return True

    if latest_expirations is None:
        return False

    latest_expiration = latest_expirations.get(
        key_value(row.get("Strategy_Name"))
    )

    return (
        latest_expiration is not None
        and expiration < latest_expiration
    )


def match_open_lots(
    lots,
    side,
    qty,
    drop_same_side=True,
):
    remaining_qty = qty
    matched_qty = 0.0
    open_matches = []

    while remaining_qty > 0 and lots:
        lot = lots[0]

        if lot["side"] == side:
            if drop_same_side:
                lots.popleft()
                continue

            break

        qty_to_match = min(
            remaining_qty,
            lot["remaining_qty"],
        )
        remaining_qty -= qty_to_match
        matched_qty += qty_to_match
        lot["remaining_qty"] -= qty_to_match
        open_matches.append({
            **lot,
            "qty": qty_to_match,
        })

        if lot["remaining_qty"] <= 0:
            lots.popleft()

    return open_matches, matched_qty, remaining_qty


def filter_realized_trades(df, as_of_date=None):
    infer_latest_expiration = as_of_date is None
    as_of_date = (
        default_as_of_timestamp(df)
        if as_of_date is None
        else pd.Timestamp(as_of_date)
    )
    working = df.sort_values(
        ["timestamp"],
        na_position="last",
    ).copy()
    open_lots = {}
    realized_indices = set()
    latest_expirations = (
        latest_option_expirations(working)
        if infer_latest_expiration
        else None
    )

    for index, row in working.iterrows():
        pos_effect = str(row.get("Pos Effect", "")).upper()
        side = str(row.get("Side", "")).upper()
        qty = abs(parse_number(row.get("Qty")) or 0.0)

        if qty == 0:
            continue

        key = position_key(row)

        if pos_effect == "TO OPEN":
            lots = find_open_lots(
                open_lots,
                row,
                side,
                require_opposite_side=True,
            )

            if lots and lots[0]["side"] != side:
                open_matches, matched_qty, remaining_qty = match_open_lots(
                    lots,
                    side,
                    qty,
                    drop_same_side=False,
                )

                if matched_qty > 0:
                    realized_indices.add(index)

                    for match in open_matches:
                        realized_indices.add(match["index"])

                if remaining_qty <= 0:
                    continue

                qty = remaining_qty

            if is_expired_option_lot(
                row,
                as_of_date,
                latest_expirations,
            ):
                realized_indices.add(index)

            open_lots.setdefault(
                key,
                deque(),
            ).append({
                "remaining_qty": qty,
                "side": side,
                "index": index,
                "row": row,
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        lots = find_open_lots(
            open_lots,
            row,
            side,
            require_opposite_side=True,
        )

        if not lots:
            realized_indices.add(index)
            continue

        open_matches, matched_qty, _ = match_open_lots(
            lots,
            side,
            qty,
        )

        if matched_qty > 0:
            realized_indices.add(index)

            for match in open_matches:
                realized_indices.add(match["index"])

    return working.loc[
        working.index.isin(realized_indices)
    ].copy()


def formatted_exec_time(timestamp):
    return (
        f"{timestamp.month}/"
        f"{timestamp.day}/"
        f"{timestamp.strftime('%y')} "
        f"{timestamp.strftime('%H:%M:%S')}"
    )


def prorated_value(row, column, qty):
    total_qty = abs(parse_number(row.get("Qty")) or 0.0)
    value = parse_number(row.get(column))

    if total_qty == 0 or value is None:
        return 0.0

    return value * qty / total_qty


def aggregate_values(rows, column, mode="sum"):
    values = [
        parse_number(row.get(column))
        for row in rows
    ]
    values = [
        value
        for value in values
        if value is not None
    ]

    if not values:
        return None

    if mode == "max":
        return max(values)

    return sum(values)


def first_nonblank(rows, column):
    for row in rows:
        value = row.get(column)

        if key_value(value):
            return value

    return None


def primary_package_row(rows):
    for row in rows:
        if key_value(row.get("Spread")):
            return row

    return rows[0]


def source_row_ids_from_value(value):
    if value is None:
        return []

    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        values = re.split(r"[;,]\s*", cleaned)
    else:
        values = [value]

    row_ids = []
    for item in values:
        if item is None or item == "":
            continue

        try:
            row_ids.append(int(item))
        except (TypeError, ValueError):
            continue

    return row_ids


def source_row_ids_from_row(row):
    return source_row_ids_from_value(row.get("source_row_ids"))


def package_instrument_key(row):
    return (
        key_value(row.get("Symbol")),
        key_value(row.get("Exp")),
        key_value(row.get("Strike")),
        key_value(row.get("Type")),
    )


def package_quantity(rows):
    qty_by_instrument = {}

    for row in rows:
        qty = abs(parse_number(row.get("Qty")) or 0.0)
        key = package_instrument_key(row)
        qty_by_instrument[key] = qty_by_instrument.get(key, 0.0) + qty

    if not qty_by_instrument:
        return 0.0

    return max(qty_by_instrument.values())


def execution_group_key(row):
    spread = normalized_spread(row)
    base = [
        key_value(row.get("Strategy_Name")),
        key_value(row.get("Exec Time")),
        key_value(row.get("Pos Effect")),
        key_value(row.get("Symbol")),
        key_value(row.get("Exp")),
        spread,
    ]

    if spread == "STRADDLE":
        base.append(key_value(row.get("Strike")))
    elif spread == "VERTICAL":
        base.append(key_value(row.get("Type")))
    else:
        base.append(key_value(row.get("Side")))
        base.extend([
            key_value(row.get("Strike")),
            key_value(row.get("Type")),
        ])

    return tuple(base)


def aggregate_execution_packages(df):
    packages = []

    for _, group in df.groupby(
        df.apply(execution_group_key, axis=1),
        sort=False,
    ):
        rows = group.to_dict("records")
        source_row = primary_package_row(rows)
        qty = package_quantity(rows)

        package = {
            **source_row,
            "Spread": normalized_spread(source_row) or source_row.get("Spread"),
            "Qty": qty,
            "Type": first_nonblank(rows, "Type"),
            "Strike": first_nonblank(rows, "Strike"),
            "trade_pnl": aggregate_values(rows, "trade_pnl") or 0.0,
            "fees": aggregate_values(rows, "fees") or 0.0,
            "net_pnl": aggregate_values(rows, "net_pnl") or 0.0,
            "margin_requirement": (
                aggregate_values(rows, "margin_requirement", mode="max") or 0.0
            ),
            "execution_leg_count": len(rows),
            "execution_symbols": "; ".join(
                key_value(row.get("Type")) or key_value(row.get("Symbol"))
                for row in rows
            ),
            "source_row_ids": [
                int(index)
                for index in group.index
            ],
        }

        margin_requirement = package["margin_requirement"]

        if margin_requirement > 0:
            package["return_on_margin"] = (
                package["net_pnl"]
                / margin_requirement
            )
            package["log_return_on_margin"] = (
                None
                if 1 + package["return_on_margin"] <= 0
                else math.log(1 + package["return_on_margin"])
            )
        else:
            package["return_on_margin"] = None
            package["log_return_on_margin"] = None

        packages.append(package)

    if not packages:
        return pd.DataFrame()

    return pd.DataFrame(packages)


def build_realized_trade_record(
    open_matches,
    close_row=None,
    close_qty=0.0,
    expiration=None,
):
    open_rows = [
        match["row"]
        for match in open_matches
    ]
    source_row = open_rows[0] if open_rows else close_row

    if source_row is None:
        return None

    if close_row is not None:
        timestamp = close_row["timestamp"]
        exec_time = close_row["Exec Time"]
        close_exec_time = close_row["Exec Time"]
        status = "CLOSED"
    else:
        timestamp = expiration + pd.Timedelta(hours=16)
        exec_time = formatted_exec_time(timestamp)
        close_exec_time = exec_time
        status = "EXPIRED"

    open_net_pnl = sum(
        prorated_value(match["row"], "net_pnl", match["qty"])
        for match in open_matches
    )
    close_net_pnl = (
        prorated_value(close_row, "net_pnl", close_qty)
        if close_row is not None
        else 0.0
    )
    open_trade_pnl = sum(
        prorated_value(match["row"], "trade_pnl", match["qty"])
        for match in open_matches
    )
    close_trade_pnl = (
        prorated_value(close_row, "trade_pnl", close_qty)
        if close_row is not None
        else 0.0
    )
    open_fees = sum(
        prorated_value(match["row"], "fees", match["qty"])
        for match in open_matches
    )
    close_fees = (
        prorated_value(close_row, "fees", close_qty)
        if close_row is not None
        else 0.0
    )
    margin_requirement = sum(
        prorated_value(match["row"], "margin_requirement", match["qty"])
        for match in open_matches
    )

    if margin_requirement == 0 and close_row is not None:
        margin_requirement = prorated_value(
            close_row,
            "margin_requirement",
            close_qty,
        )

    qty = sum(
        match["qty"]
        for match in open_matches
    ) or close_qty
    net_pnl = open_net_pnl + close_net_pnl
    trade_pnl = open_trade_pnl + close_trade_pnl

    if margin_requirement > 0:
        return_on_margin = net_pnl / margin_requirement
        log_return_on_margin = (
            None
            if 1 + return_on_margin <= 0
            else math.log(1 + return_on_margin)
        )
    else:
        return_on_margin = None
        log_return_on_margin = None

    open_exec_times = "; ".join(
        str(row["Exec Time"])
        for row in open_rows
    )
    source_row_ids = []
    for row in open_rows:
        source_row_ids.extend(source_row_ids_from_row(row))

    if close_row is not None:
        source_row_ids.extend(source_row_ids_from_row(close_row))

    source_row_ids = sorted(set(source_row_ids))

    return {
        "Exec Time": exec_time,
        "timestamp": timestamp,
        "Strategy_Name": source_row.get("Strategy_Name"),
        "Symbol": source_row.get("Symbol"),
        "Spread": source_row.get("Spread"),
        "Side": source_row.get("Side"),
        "Qty": qty,
        "Pos Effect": status,
        "Exp": source_row.get("Exp"),
        "Strike": source_row.get("Strike"),
        "Type": source_row.get("Type"),
        "open_exec_time": open_exec_times,
        "close_exec_time": close_exec_time,
        "realized_status": status,
        "execution_leg_count": source_row.get("execution_leg_count", 1),
        "execution_symbols": source_row.get("execution_symbols"),
        "source_row_ids": source_row_ids,
        "open_net_pnl": open_net_pnl,
        "close_net_pnl": close_net_pnl,
        "trade_pnl": trade_pnl,
        "fees": open_fees + close_fees,
        "net_pnl": net_pnl,
        "margin_requirement": margin_requirement,
        "return_on_margin": return_on_margin,
        "log_return_on_margin": log_return_on_margin,
        "starting_equity": source_row.get("starting_equity"),
    }


def aggregate_realized_trades(df, as_of_date=None):
    infer_latest_expiration = as_of_date is None
    as_of_date = (
        default_as_of_timestamp(df)
        if as_of_date is None
        else pd.Timestamp(as_of_date)
    )
    working = df.sort_values(
        ["timestamp"],
        na_position="last",
    ).copy()
    working = aggregate_execution_packages(working)
    open_lots = {}
    realized_trades = []
    latest_expirations = (
        latest_option_expirations(working)
        if infer_latest_expiration
        else None
    )

    for _, row in working.iterrows():
        pos_effect = str(row.get("Pos Effect", "")).upper()
        side = str(row.get("Side", "")).upper()
        qty = abs(parse_number(row.get("Qty")) or 0.0)

        if qty == 0:
            continue

        key = position_key(row)

        if pos_effect == "TO OPEN":
            lots = find_open_lots(
                open_lots,
                row,
                side,
                require_opposite_side=True,
            )

            if lots and lots[0]["side"] != side:
                open_matches, matched_qty, remaining_qty = match_open_lots(
                    lots,
                    side,
                    qty,
                    drop_same_side=False,
                )

                if open_matches:
                    realized_trades.append(
                        build_realized_trade_record(
                            open_matches,
                            close_row=row,
                            close_qty=matched_qty,
                        )
                    )

                if remaining_qty <= 0:
                    continue

                qty = remaining_qty

            open_lots.setdefault(
                key,
                deque(),
            ).append({
                "remaining_qty": qty,
                "side": side,
                "row": row,
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        lots = find_open_lots(
            open_lots,
            row,
            side,
            require_opposite_side=True,
        )

        open_matches, matched_qty, _ = match_open_lots(
            lots,
            side,
            qty,
        )

        if open_matches:
            realized_trades.append(
                build_realized_trade_record(
                    open_matches,
                    close_row=row,
                    close_qty=matched_qty,
                )
            )
        elif qty > 0:
            realized_trades.append(
                build_realized_trade_record(
                    [],
                    close_row=row,
                    close_qty=qty,
                )
            )

    for lots in open_lots.values():
        for lot in lots:
            row = lot["row"]

            if not is_expired_option_lot(
                row,
                as_of_date,
                latest_expirations,
            ):
                continue

            expiration = parse_option_expiration(row)
            realized_trades.append(
                build_realized_trade_record(
                    [
                        {
                            "row": row,
                            "qty": lot["remaining_qty"],
                        }
                    ],
                    expiration=expiration,
                )
            )

    realized_trades = [
        trade
        for trade in realized_trades
        if trade is not None
    ]

    if not realized_trades:
        return pd.DataFrame()

    return recalculate_account_columns(
        pd.DataFrame(realized_trades)
    )


def get_open_positions(df, as_of_date=None):
    infer_latest_expiration = as_of_date is None
    as_of_date = (
        default_as_of_timestamp(df)
        if as_of_date is None
        else pd.Timestamp(as_of_date)
    )
    working = df.sort_values(
        ["timestamp"],
        na_position="last",
    ).copy()
    working = aggregate_execution_packages(working)
    open_lots = {}
    latest_expirations = (
        latest_option_expirations(working)
        if infer_latest_expiration
        else None
    )

    for _, row in working.iterrows():
        pos_effect = str(row.get("Pos Effect", "")).upper()
        side = str(row.get("Side", "")).upper()
        qty = abs(parse_number(row.get("Qty")) or 0.0)

        if qty == 0:
            continue

        key = position_key(row)

        if pos_effect == "TO OPEN":
            lots = find_open_lots(
                open_lots,
                row,
                side,
                require_opposite_side=True,
            )

            if lots and lots[0]["side"] != side:
                _, _, remaining_qty = match_open_lots(
                    lots,
                    side,
                    qty,
                    drop_same_side=False,
                )

                if remaining_qty <= 0:
                    continue

                qty = remaining_qty

            open_lots.setdefault(
                key,
                deque(),
            ).append({
                "remaining_qty": qty,
                "side": side,
                "row": row,
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        lots = find_open_lots(
            open_lots,
            row,
            side,
            require_opposite_side=True,
        )
        match_open_lots(
            lots,
            side,
            qty,
        )

    rows = []

    for lots in open_lots.values():
        for lot in lots:
            row = lot["row"]

            if is_expired_option_lot(
                row,
                as_of_date,
                latest_expirations,
            ):
                continue

            expiration_close = option_expiration_close(row)
            rows.append({
                "Strategy_Name": row.get("Strategy_Name"),
                "open_exec_time": row.get("Exec Time"),
                "Symbol": row.get("Symbol"),
                "Spread": row.get("Spread"),
                "Side": row.get("Side"),
                "remaining_qty": lot["remaining_qty"],
                "Exp": row.get("Exp"),
                "expiration_close": expiration_close,
                "Strike": row.get("Strike"),
                "Type": row.get("Type"),
                "margin_requirement": row.get("margin_requirement"),
                "return_on_margin": row.get("return_on_margin"),
                "status": "OPEN_NOT_COUNTED",
            })

    if not rows:
        return pd.DataFrame(columns=[
            "Strategy_Name",
            "open_exec_time",
            "Symbol",
            "Spread",
            "Side",
            "remaining_qty",
            "Exp",
            "expiration_close",
            "Strike",
            "Type",
            "margin_requirement",
            "return_on_margin",
            "status",
        ])

    return pd.DataFrame(rows).sort_values(
        [
            "Strategy_Name",
            "open_exec_time",
        ],
        na_position="last",
    )


def parse_statement_timestamp(date_value, time_value):
    timestamp = pd.to_datetime(
        f"{date_value} {time_value}",
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    if pd.isna(timestamp):
        return None

    return timestamp


def futures_symbol_from_description(description):
    match = re.search(r"(/[A-Z0-9]+):", str(description))

    if match is not None:
        return match.group(1)

    match = re.search(r"\b(/[A-Z0-9]+)\b", str(description))

    if match is not None:
        return match.group(1)

    return ""


def parse_futures_statement_rows(statement_file):
    rows = []
    in_futures_statement = False

    with open(statement_file, newline="", errors="replace") as file:
        for row in csv.reader(file):
            if not row:
                continue

            first_col = str(row[0]).strip().strip('"').lstrip("\ufeff")

            if first_col == "Futures Statements":
                in_futures_statement = True
                continue

            if in_futures_statement and first_col == "Account Trade History":
                break

            if not in_futures_statement or len(row) < 10:
                continue

            row_type = str(row[3]).strip()

            if row_type not in {"TRD", "ADJ"}:
                continue

            timestamp = parse_statement_timestamp(
                row[0],
                row[2],
            )

            if timestamp is None:
                continue

            symbol = futures_symbol_from_description(row[5])

            if not symbol:
                continue

            amount = parse_number(row[8]) or 0.0
            misc_fees = parse_number(row[6]) or 0.0
            commissions_fees = parse_number(row[7]) or 0.0

            rows.append({
                "date": timestamp.date().isoformat(),
                "timestamp": timestamp,
                "type": row_type,
                "symbol": symbol,
                "description": row[5],
                "amount": amount,
                "misc_fees": misc_fees,
                "commissions_fees": commissions_fees,
                "cash_flow": amount + misc_fees + commissions_fees,
                "balance": parse_number(row[9]),
            })

    return pd.DataFrame(rows)


def parse_statement_position_summary(statement_file):
    rows = []
    quantity_by_symbol = {}
    in_position_quantities = False
    in_positions = False

    with open(statement_file, newline="", errors="replace") as file:
        for row in csv.reader(file):
            if not row:
                if in_position_quantities:
                    in_position_quantities = False
                    continue
                if in_positions:
                    break
                continue

            first_col = str(row[0]).strip().strip('"').lstrip("\ufeff")

            if (
                first_col == "Symbol"
                and len(row) >= 8
                and row[1] == "Description"
                and row[4] == "Qty"
            ):
                in_position_quantities = True
                in_positions = False
                continue

            if (
                first_col == "Symbol"
                and len(row) >= 7
                and row[1] == "P/L Open"
                and row[4] == "Mark Value"
                and row[5] == "P/L YTD"
            ):
                in_positions = True
                in_position_quantities = False
                continue

            if in_position_quantities:
                symbol = key_value(row[0])

                if symbol.startswith("/") and len(row) > 4:
                    quantity_by_symbol[symbol] = parse_number(row[4])

                continue

            if not in_positions or len(row) < 7:
                continue

            symbol = key_value(row[0])

            if not symbol.startswith("/"):
                continue

            rows.append({
                "symbol": symbol,
                "open_pnl": parse_number(row[1]) or 0.0,
                "day_pnl": parse_number(row[3]) or 0.0,
                "mark_value": parse_number(row[4]) or 0.0,
                "ytd_pnl": parse_number(row[5]) or 0.0,
                "statement_qty": quantity_by_symbol.get(symbol),
                "description": row[6],
            })

    return pd.DataFrame(rows)


def latest_statement_ytd(statement_file):
    if not statement_file:
        return {}, pd.DataFrame()

    with open(statement_file, errors="replace") as file:
        return parse_statement_ytd_summary(
            file.readlines(),
            statement_file,
        )


def statement_end_of_day(statement_ytd_summary):
    statement_file = statement_ytd_summary.get("statement_file")

    if not statement_file:
        return None

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})",
        os.path.basename(str(statement_file)),
    )

    if not match:
        return None

    statement_date = pd.Timestamp(match.group(1))
    return statement_date + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def numeric_column(df, column):
    if df is None or df.empty or column not in df.columns:
        return pd.Series(dtype=float)

    return pd.to_numeric(
        df[column],
        errors="coerce",
    ).fillna(0.0)


def zero_small_money(value, tolerance=0.005):
    number = parse_number(value)

    if number is None:
        return value

    return 0.0 if abs(number) <= tolerance else value


def ytd_filter(df, timestamp_column, year):
    if df is None or df.empty or year is None or timestamp_column not in df.columns:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()

    if timestamp_column == "Exec Time":
        timestamps = pd.to_datetime(
            df[timestamp_column],
            format="%m/%d/%y %H:%M:%S",
            errors="coerce",
        )
    else:
        timestamps = pd.to_datetime(
            df[timestamp_column],
            errors="coerce",
        )

    return df.loc[
        timestamps.dt.year == int(year)
    ].copy()


def fee_bucket_for_statement(bucket):
    bucket = str(bucket).strip().lower()

    if bucket == "cash":
        return "equity"

    return bucket


def script_fee_reconciliation(cleaned_trades, statement_summary):
    if not statement_summary:
        return pd.DataFrame()

    year = statement_summary.get("statement_year")
    working = ytd_filter(
        cleaned_trades,
        "timestamp",
        year,
    )

    if working.empty:
        script_by_bucket = pd.Series(dtype=float)
    else:
        working = working.copy()
        working["fee_bucket"] = working.apply(
            lambda row: fee_bucket_for_statement(trade_account_bucket(row)),
            axis=1,
        )
        working["fees"] = numeric_column(working, "fees")
        script_by_bucket = working.groupby("fee_bucket")["fees"].sum()

    rows = [
        {
            "fee_bucket": "equity",
            "statement_fees": statement_summary.get(
                "equity_commissions_fees_ytd",
                0.0,
            ),
            "script_fees": script_by_bucket.get("equity", 0.0),
            "likely_cause": (
                "Static/index-option fee model or approved fee corrections "
                "do not exactly match broker YTD equity fees."
            ),
        },
        {
            "fee_bucket": "futures",
            "statement_fees": statement_summary.get(
                "futures_commissions_fees_ytd",
                0.0,
            ),
            "script_fees": script_by_bucket.get("futures", 0.0),
            "likely_cause": (
                "Static futures/futures-option fee model differs from "
                "broker-reported YTD futures fees."
            ),
        },
        {
            "fee_bucket": "crypto",
            "statement_fees": statement_summary.get(
                "crypto_trading_fees_ytd",
                0.0,
            ),
            "script_fees": script_by_bucket.get("crypto", 0.0),
            "likely_cause": (
                "Crypto trading fees are reported in the account summary, "
                "but lookup_fees currently stores zero for crypto rows."
            ),
        },
        {
            "fee_bucket": "forex",
            "statement_fees": statement_summary.get(
                "forex_commissions_ytd",
                0.0,
            ),
            "script_fees": script_by_bucket.get("forex", 0.0),
            "likely_cause": "Forex fees are compared directly to forex rows.",
        },
    ]
    result = pd.DataFrame(rows)
    result["fee_delta_statement_minus_script"] = (
        result["statement_fees"] - result["script_fees"]
    )

    return result


def script_open_pnl_by_symbol(open_positions):
    if open_positions is None or open_positions.empty:
        return pd.DataFrame(columns=[
            "Symbol",
            "script_open_pnl",
            "script_open_position_rows",
            "script_open_remaining_qty",
        ])

    working = open_positions.copy()
    working["margin_requirement"] = numeric_column(
        working,
        "margin_requirement",
    )
    working["return_on_margin"] = numeric_column(
        working,
        "return_on_margin",
    )
    working["remaining_qty"] = numeric_column(
        working,
        "remaining_qty",
    )
    working["script_open_pnl"] = (
        working["margin_requirement"]
        * working["return_on_margin"]
    )

    return (
        working.groupby("Symbol", dropna=False)
        .agg(
            script_open_pnl=("script_open_pnl", "sum"),
            script_open_position_rows=("Symbol", "size"),
            script_open_remaining_qty=("remaining_qty", "sum"),
        )
        .reset_index()
    )


def open_pnl_reconciliation(statement_positions, open_positions):
    if statement_positions is None or statement_positions.empty:
        return pd.DataFrame()

    statement = statement_positions[
        [
            "Symbol",
            "statement_open_pnl",
            "statement_ytd_pnl",
            "statement_closed_gross_pnl",
        ]
    ].copy()
    script_open = script_open_pnl_by_symbol(open_positions)
    result = pd.merge(
        statement,
        script_open,
        on="Symbol",
        how="outer",
    ).fillna(0.0)
    result["open_pnl_delta_statement_minus_script"] = (
        result["statement_open_pnl"] - result["script_open_pnl"]
    )
    result["likely_cause"] = result.apply(
        lambda row: open_pnl_difference_cause(row),
        axis=1,
    )

    return result.sort_values(
        "open_pnl_delta_statement_minus_script",
        key=lambda series: series.abs(),
        ascending=False,
    )


def open_pnl_difference_cause(row, tolerance=1.0):
    statement_open = row.get("statement_open_pnl", 0.0)
    script_open = row.get("script_open_pnl", 0.0)

    if abs(statement_open - script_open) <= tolerance:
        return "matches_statement_open_pnl"

    if script_open and not statement_open:
        return "script_has_open_position_not_in_statement_pnl_table"

    if statement_open and not script_open:
        return "statement_has_mark_to_market_open_pnl_not_valued_by_script"

    return (
        "script_open_pnl_uses_opening_cash_flow_or_margin_return_not "
        "statement mark-to-market open PnL"
    )


def closed_pnl_reconciliation(statement_positions, realized_trades):
    if statement_positions is None or statement_positions.empty:
        return pd.DataFrame()

    statement = statement_positions.copy()
    statement["statement_root_symbol"] = statement["Symbol"].apply(
        normalize_root_symbol,
    )

    script = realized_trades.copy() if realized_trades is not None else pd.DataFrame()

    if script.empty:
        script_by_root = pd.DataFrame(columns=[
            "statement_root_symbol",
            "script_realized_gross_pnl",
            "script_realized_fees",
            "script_realized_net_pnl",
            "script_realized_trade_count",
        ])
    else:
        script["statement_root_symbol"] = script["Symbol"].apply(
            normalize_root_symbol,
        )
        script["trade_pnl"] = numeric_column(script, "trade_pnl")
        script["fees"] = numeric_column(script, "fees")
        script["net_pnl"] = numeric_column(script, "net_pnl")
        script_by_root = (
            script.groupby("statement_root_symbol", dropna=False)
            .agg(
                script_realized_gross_pnl=("trade_pnl", "sum"),
                script_realized_fees=("fees", "sum"),
                script_realized_net_pnl=("net_pnl", "sum"),
                script_realized_trade_count=("Symbol", "size"),
            )
            .reset_index()
        )

    statement_by_root = (
        statement.groupby("statement_root_symbol", dropna=False)
        .agg(
            statement_symbols=("Symbol", lambda values: "; ".join(sorted(set(values)))),
            statement_open_pnl=("statement_open_pnl", "sum"),
            statement_ytd_pnl=("statement_ytd_pnl", "sum"),
            statement_closed_gross_pnl=("statement_closed_gross_pnl", "sum"),
        )
        .reset_index()
    )
    result = pd.merge(
        statement_by_root,
        script_by_root,
        on="statement_root_symbol",
        how="outer",
    ).fillna(0.0)
    result["closed_gross_delta_statement_minus_script"] = (
        result["statement_closed_gross_pnl"]
        - result["script_realized_gross_pnl"]
    )
    result["likely_cause"] = result.apply(
        lambda row: closed_pnl_difference_cause(row),
        axis=1,
    )

    return result.sort_values(
        "closed_gross_delta_statement_minus_script",
        key=lambda series: series.abs(),
        ascending=False,
    )


def closed_pnl_difference_cause(row, tolerance=1.0):
    delta = row.get("closed_gross_delta_statement_minus_script", 0.0)
    symbol = str(row.get("statement_root_symbol", ""))

    if abs(delta) <= tolerance:
        return "matches_statement_closed_gross_pnl"

    if symbol.startswith("/MCL") or symbol.startswith("/MGC"):
        return (
            "futures cash-ledger corrections used settlement cash flows; "
            "review futures mark-to-market ADJ handling for this root."
        )

    if symbol in {"SPX", "XSP"}:
        return (
            "expired index option settlement or grouped option-package "
            "matching differs from statement P/L YTD."
        )

    if symbol.startswith("/"):
        return (
            "futures/futures-option contract grouping differs from statement "
            "root-level P/L YTD."
        )

    return "review symbol-level open/close matching and missing executions"


def load_cash_trade_correction_review(input_file, corrections_file=None):
    if corrections_file is None:
        corrections_file = os.path.join(
            os.path.dirname(os.path.dirname(REALIZED_TRADES_FILE)),
            "cash_trade_corrections.csv",
        )

    if not os.path.isfile(corrections_file):
        return pd.DataFrame()

    corrections = pd.read_csv(corrections_file)

    if corrections.empty:
        return pd.DataFrame()

    try:
        master = pd.read_csv(input_file)
    except FileNotFoundError:
        master = pd.DataFrame()

    join_columns = [
        "statement_file",
        "statement_trade_row",
    ]

    if not master.empty and set(join_columns).issubset(master.columns):
        master_rows = master[
            join_columns
            + [
                column
                for column in [
                    "Exec Time",
                    "Strategy_Name",
                    "Symbol",
                    "Spread",
                    "Side",
                    "Qty",
                    "Pos Effect",
                    "Price",
                ]
                if column in master.columns
            ]
        ]
        corrections = pd.merge(
            corrections,
            master_rows,
            on=join_columns,
            how="left",
        )

    for column in [
        "original_trade_pnl",
        "corrected_trade_pnl",
        "original_net_pnl",
        "corrected_net_pnl",
        "original_fees",
        "corrected_fees",
    ]:
        corrections[column] = numeric_column(corrections, column)

    futures_mask = corrections.apply(
        lambda row: str(row.get("Spread", "")).upper() == "FUTURE"
        or str(row.get("Type", "")).upper() == "FUTURE",
        axis=1,
    )
    corrections.loc[
        futures_mask,
        "corrected_trade_pnl",
    ] = corrections.loc[
        futures_mask,
        "original_trade_pnl",
    ]
    corrections.loc[
        futures_mask,
        "corrected_net_pnl",
    ] = (
        corrections.loc[futures_mask, "corrected_trade_pnl"]
        - corrections.loc[futures_mask, "corrected_fees"]
    )
    corrections["trade_pnl_correction_delta"] = (
        corrections["corrected_trade_pnl"]
        - corrections["original_trade_pnl"]
    )
    corrections["net_pnl_correction_delta"] = (
        corrections["corrected_net_pnl"]
        - corrections["original_net_pnl"]
    )
    corrections["review_recommendation"] = corrections.apply(
        lambda row: trade_review_recommendation(row),
        axis=1,
    )

    return corrections[
        corrections["trade_pnl_correction_delta"].abs() > 1.0
    ].sort_values(
        "trade_pnl_correction_delta",
        key=lambda series: series.abs(),
        ascending=False,
    )


def trade_review_recommendation(row):
    symbol = str(row.get("Symbol", ""))
    bucket = trade_account_bucket(row)

    if bucket == "futures":
        return (
            "Review before applying: futures trade cash-flow corrections can "
            "represent daily settlement, not open-to-close PnL."
        )

    if symbol in {"SPX", "XSP"}:
        return (
            "Review expired option settlement and package matching against "
            "statement P/L YTD."
        )

    return "Review cash-ledger correction against the source statement row."


def strategy_ytd_adjustment_impact(realized_trades, trade_review):
    if realized_trades is None or realized_trades.empty:
        return pd.DataFrame()

    current = realized_trades.copy()
    current["net_pnl"] = numeric_column(current, "net_pnl")
    current_summary = (
        current.groupby("Strategy_Name", dropna=False)
        .agg(
            current_realized_net_pnl=("net_pnl", "sum"),
            realized_trade_count=("Strategy_Name", "size"),
        )
        .reset_index()
    )

    if (
        trade_review is None
        or trade_review.empty
        or "Strategy_Name" not in trade_review.columns
    ):
        current_summary["review_adjustment_if_reverted_to_formula_pnl"] = 0.0
    else:
        review = trade_review.copy()
        review["net_pnl_review_adjustment"] = (
            review["original_net_pnl"] - review["corrected_net_pnl"]
        )
        suspect = review[
            review["review_recommendation"]
            .astype(str)
            .str.contains("futures trade cash-flow", na=False)
        ]
        if suspect.empty:
            current_summary["review_adjustment_if_reverted_to_formula_pnl"] = 0.0
        else:
            adjustment = (
                suspect.groupby("Strategy_Name", dropna=False)
                .agg(
                    review_adjustment_if_reverted_to_formula_pnl=(
                        "net_pnl_review_adjustment",
                        "sum",
                    ),
                    review_rows=("Strategy_Name", "size"),
                )
                .reset_index()
            )
            current_summary = pd.merge(
                current_summary,
                adjustment,
                on="Strategy_Name",
                how="left",
            )
            current_summary["review_adjustment_if_reverted_to_formula_pnl"] = (
                current_summary["review_adjustment_if_reverted_to_formula_pnl"]
                .fillna(0.0)
            )

    current_summary["projected_net_pnl_after_review_adjustment"] = (
        current_summary["current_realized_net_pnl"]
        + current_summary["review_adjustment_if_reverted_to_formula_pnl"]
    )

    return current_summary


def build_ytd_statement_reports(
    statement_summary,
    statement_positions,
    cleaned_trades,
    realized_trades,
    open_positions,
    input_file,
    corrections_file=None,
):
    if not statement_summary:
        return {
            "summary": pd.DataFrame(),
            "open_pnl": pd.DataFrame(),
            "fees": pd.DataFrame(),
            "closed_pnl": pd.DataFrame(),
            "trade_review": pd.DataFrame(),
            "strategy_impact": pd.DataFrame(),
        }

    summary = pd.DataFrame([statement_summary]).copy()
    year = statement_summary.get("statement_year")
    realized_ytd = ytd_filter(
        realized_trades,
        "timestamp",
        year,
    )
    cleaned_ytd = ytd_filter(
        cleaned_trades,
        "Exec Time",
        year,
    )
    realized_closed_gross = numeric_column(
        realized_ytd,
        "trade_pnl",
    ).sum()
    script_closed_fees = numeric_column(
        cleaned_ytd,
        "fees",
    ).sum()
    realized_closed_net = realized_closed_gross - script_closed_fees
    has_trade_history_pnl = {
        "trade_pnl",
        "net_pnl",
    }.issubset(cleaned_ytd.columns)
    trade_history_closed_gross = (
        numeric_column(cleaned_ytd, "trade_pnl").sum()
        if has_trade_history_pnl
        else realized_closed_gross
    )
    trade_history_closed_net = (
        numeric_column(cleaned_ytd, "net_pnl").sum()
        if has_trade_history_pnl
        else realized_closed_net
    )
    ytd_bridge_adjustments = (
        build_ytd_bridge_adjustments(
            statement_summary,
            statement_positions,
            cleaned_trades,
        )
        if has_trade_history_pnl
        else pd.DataFrame()
    )
    ytd_reconciliation = (
        ytd_reconciliation_rows(
            statement_summary,
            cleaned_trades,
            ytd_bridge_adjustments,
        )
        if has_trade_history_pnl
        else pd.DataFrame()
    )
    closed_net_reconciliation = pd.DataFrame()
    closed_gross_reconciliation = pd.DataFrame()

    if ytd_reconciliation is not None and not ytd_reconciliation.empty:
        closed_net_reconciliation = ytd_reconciliation[
            ytd_reconciliation["metric"].eq("closed_net_ytd_pnl")
        ]
        closed_gross_reconciliation = ytd_reconciliation[
            ytd_reconciliation["metric"].eq("closed_gross_ytd_pnl")
        ]

    adjusted_closed_net = trade_history_closed_net
    adjusted_closed_gross = trade_history_closed_gross
    statement_closed_net = parse_number(
        statement_summary.get("statement_closed_net_ytd_pnl")
    )
    statement_closed_gross = parse_number(
        statement_summary.get("statement_closed_gross_ytd_pnl")
    )
    closed_net_delta = (
        statement_closed_net - adjusted_closed_net
        if statement_closed_net is not None
        else None
    )
    closed_gross_delta = (
        statement_closed_gross - adjusted_closed_gross
        if statement_closed_gross is not None
        else None
    )
    open_trade_exclusion = 0.0
    ytd_bridge_adjustment = 0.0
    total_closed_pnl_adjustment = 0.0

    if not closed_net_reconciliation.empty:
        row = closed_net_reconciliation.iloc[0]
        trade_history_closed_net = row.get(
            "trade_history_value",
            trade_history_closed_net,
        )
        adjusted_closed_net = row.get(
            "adjusted_trade_history_value",
            adjusted_closed_net,
        )
        closed_net_delta = row.get(
            "difference_after_bridge",
            closed_net_delta,
        )
        open_trade_exclusion = row.get("open_trade_exclusion", 0.0)
        ytd_bridge_adjustment = row.get("ytd_bridge_adjustment", 0.0)
        total_closed_pnl_adjustment = row.get("bridge_adjustment", 0.0)

    if not closed_gross_reconciliation.empty:
        row = closed_gross_reconciliation.iloc[0]
        trade_history_closed_gross = row.get(
            "trade_history_value",
            trade_history_closed_gross,
        )
        adjusted_closed_gross = row.get(
            "adjusted_trade_history_value",
            adjusted_closed_gross,
        )
        closed_gross_delta = row.get(
            "difference_after_bridge",
            closed_gross_delta,
        )

    closed_net_delta = zero_small_money(closed_net_delta)
    closed_gross_delta = zero_small_money(closed_gross_delta)
    adjusted_closed_net = zero_small_money(adjusted_closed_net)
    adjusted_closed_gross = zero_small_money(adjusted_closed_gross)
    summary["realized_trade_closed_gross_pnl"] = realized_closed_gross
    summary["realized_trade_closed_net_pnl"] = realized_closed_net
    summary["trade_history_closed_gross_ytd_pnl"] = trade_history_closed_gross
    summary["trade_history_closed_net_ytd_pnl"] = trade_history_closed_net
    summary["trade_history_adjusted_closed_gross_ytd_pnl"] = adjusted_closed_gross
    summary["trade_history_adjusted_closed_net_ytd_pnl"] = adjusted_closed_net
    summary["open_trade_exclusion"] = open_trade_exclusion
    summary["ytd_bridge_adjustment"] = ytd_bridge_adjustment
    summary["total_closed_pnl_adjustment"] = total_closed_pnl_adjustment
    summary["closed_net_delta_statement_minus_trade_history"] = closed_net_delta
    summary["closed_gross_delta_statement_minus_trade_history"] = (
        closed_gross_delta
    )
    summary["script_realized_closed_gross_pnl"] = adjusted_closed_gross
    summary["script_realized_closed_fees"] = script_closed_fees
    summary["script_realized_closed_net_pnl"] = adjusted_closed_net
    summary["closed_net_delta_statement_minus_script"] = closed_net_delta
    open_reconciliation = open_pnl_reconciliation(
        statement_positions,
        open_positions,
    )
    fee_reconciliation = script_fee_reconciliation(
        cleaned_trades,
        statement_summary,
    )
    closed_reconciliation = closed_pnl_reconciliation(
        statement_positions,
        realized_ytd,
    )
    trade_review = load_cash_trade_correction_review(
        input_file,
        corrections_file=corrections_file,
    )
    strategy_impact = strategy_ytd_adjustment_impact(
        realized_ytd,
        trade_review,
    )

    return {
        "summary": summary,
        "open_pnl": open_reconciliation,
        "fees": fee_reconciliation,
        "closed_pnl": closed_reconciliation,
        "trade_review": trade_review,
        "strategy_impact": strategy_impact,
    }


def cash_ledger_daily_reconciliation(cleaned_trades, cash_ledger, tolerance=1.0):
    if cash_ledger is None or cash_ledger.empty:
        return pd.DataFrame()

    working_ledger = cash_ledger.copy()
    ledger_timestamps = pd.to_datetime(
        working_ledger["timestamp"],
        errors="coerce",
    )
    working_ledger = working_ledger.loc[
        ledger_timestamps.notna()
    ].copy()

    if working_ledger.empty:
        return pd.DataFrame()

    start_date = ledger_timestamps.min().normalize()
    end_date = ledger_timestamps.max().normalize()
    trade_timestamps = pd.to_datetime(
        cleaned_trades["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    filtered_trades = cleaned_trades.loc[
        (trade_timestamps >= start_date)
        & (trade_timestamps < end_date + pd.Timedelta(days=1))
    ].copy()
    statement = statement_trade_cash_by_day(working_ledger)
    extracted = extracted_trade_cash_by_day(filtered_trades)
    available_buckets = set(working_ledger["account_bucket"])
    extracted = extracted[
        extracted["account_bucket"].isin(available_buckets)
    ].copy()
    reconciliation = pd.merge(
        statement,
        extracted,
        on=[
            "date",
            "account_bucket",
        ],
        how="outer",
    )

    numeric_columns = [
        "statement_trade_rows",
        "statement_trade_cash_flow",
        "extracted_trade_count",
        "extracted_net_pnl",
    ]

    for column in numeric_columns:
        if column not in reconciliation.columns:
            reconciliation[column] = 0.0

        reconciliation[column] = pd.to_numeric(
            reconciliation[column],
            errors="coerce",
        ).fillna(0.0)

    reconciliation["cash_flow_delta"] = (
        reconciliation["extracted_net_pnl"]
        - reconciliation["statement_trade_cash_flow"]
    )
    reconciliation["validation_status"] = reconciliation[
        "cash_flow_delta"
    ].apply(
        lambda value: "PASS" if abs(value) <= tolerance else "REVIEW"
    )

    return reconciliation.sort_values(
        [
            "validation_status",
            "cash_flow_delta",
            "date",
            "account_bucket",
        ],
        ascending=[False, False, True, True],
    )


def validation_status(delta, tolerance=1.0):
    if delta is None or pd.isna(delta):
        return "MISSING"

    return "PASS" if abs(delta) <= tolerance else "REVIEW"


def build_trade_history_validation_summary(
    ytd_reports,
    cash_reconciliation,
    tolerance=1.0,
):
    rows = []
    ytd_summary = ytd_reports.get("summary", pd.DataFrame())

    if ytd_summary is not None and not ytd_summary.empty:
        summary = ytd_summary.iloc[0]
        closed_net_delta = parse_number(
            summary.get("closed_net_delta_statement_minus_script")
        )
        closed_gross_delta = (
            parse_number(summary.get("statement_closed_gross_ytd_pnl"))
            - parse_number(summary.get("script_realized_closed_gross_pnl"))
        )
        fee_delta = (
            parse_number(
                summary.get("statement_total_ytd_commissions_and_fees")
            )
            - parse_number(summary.get("script_realized_closed_fees"))
        )
        rows.extend([
            {
                "metric": "closed_net_pnl",
                "trade_history_value": summary.get(
                    "script_realized_closed_net_pnl"
                ),
                "cash_ledger_statement_value": "",
                "ytd_statement_value": summary.get(
                    "statement_closed_net_ytd_pnl"
                ),
                "delta_statement_minus_trade_history": closed_net_delta,
                "validation_status": validation_status(
                    closed_net_delta,
                    tolerance,
                ),
                "validation_note": (
                    "Closed net PnL should match YTD gross PnL minus open "
                    "PnL minus all statement YTD fees."
                ),
            },
            {
                "metric": "closed_gross_pnl",
                "trade_history_value": summary.get(
                    "script_realized_closed_gross_pnl"
                ),
                "cash_ledger_statement_value": "",
                "ytd_statement_value": summary.get(
                    "statement_closed_gross_ytd_pnl"
                ),
                "delta_statement_minus_trade_history": closed_gross_delta,
                "validation_status": validation_status(
                    closed_gross_delta,
                    tolerance,
                ),
                "validation_note": (
                    "Gross closed PnL is compared before commissions and "
                    "fees."
                ),
            },
            {
                "metric": "fees",
                "trade_history_value": summary.get(
                    "script_realized_closed_fees"
                ),
                "cash_ledger_statement_value": "",
                "ytd_statement_value": summary.get(
                    "statement_total_ytd_commissions_and_fees"
                ),
                "delta_statement_minus_trade_history": fee_delta,
                "validation_status": validation_status(
                    fee_delta,
                    tolerance,
                ),
                "validation_note": (
                    "Fees are validated against statement YTD equity, "
                    "futures, forex, and crypto fee totals."
                ),
            },
        ])

    open_pnl = ytd_reports.get("open_pnl", pd.DataFrame())
    if open_pnl is not None and not open_pnl.empty:
        statement_open = numeric_column(open_pnl, "statement_open_pnl").sum()
        script_open = numeric_column(open_pnl, "script_open_pnl").sum()
        open_delta = statement_open - script_open
        rows.append({
            "metric": "open_pnl",
            "trade_history_value": script_open,
            "cash_ledger_statement_value": "",
            "ytd_statement_value": statement_open,
            "delta_statement_minus_trade_history": open_delta,
            "validation_status": validation_status(
                open_delta,
                tolerance,
            ),
            "validation_note": (
                "Open PnL is mark-to-market in the statement. Trade history "
                "can only match after open positions are valued from "
                "statement or market marks."
            ),
        })

    if cash_reconciliation is not None and not cash_reconciliation.empty:
        cash_delta = numeric_column(cash_reconciliation, "cash_flow_delta").sum()
        unreconciled = int(
            cash_reconciliation["validation_status"].eq("REVIEW").sum()
        )
        rows.append({
            "metric": "cash_ledger_daily_trade_cash_flow",
            "trade_history_value": numeric_column(
                cash_reconciliation,
                "extracted_net_pnl",
            ).sum(),
            "cash_ledger_statement_value": numeric_column(
                cash_reconciliation,
                "statement_trade_cash_flow",
            ).sum(),
            "ytd_statement_value": "",
            "delta_statement_minus_trade_history": -cash_delta,
            "validation_status": "PASS" if unreconciled == 0 else "REVIEW",
            "validation_note": (
                "Cash-ledger validation is daily/event cash-flow validation, "
                "not closed-PnL validation when open positions exist. "
                f"{unreconciled} daily/account groups need review."
            ),
        })

    return pd.DataFrame(rows)


def build_trade_history_validation_issues(
    ytd_reports,
    cash_reconciliation,
    tolerance=1.0,
):
    rows = []

    issue_specs = [
        (
            "open_pnl",
            ytd_reports.get("open_pnl", pd.DataFrame()),
            "Symbol",
            "open_pnl_delta_statement_minus_script",
        ),
        (
            "fees",
            ytd_reports.get("fees", pd.DataFrame()),
            "fee_bucket",
            "fee_delta_statement_minus_script",
        ),
        (
            "closed_gross_pnl",
            ytd_reports.get("closed_pnl", pd.DataFrame()),
            "statement_root_symbol",
            "closed_gross_delta_statement_minus_script",
        ),
    ]

    for issue_type, frame, key_column, delta_column in issue_specs:
        if frame is None or frame.empty or delta_column not in frame.columns:
            continue

        review_rows = frame[
            numeric_column(frame, delta_column).abs() > tolerance
        ].copy()
        review_rows = review_rows.sort_values(
            delta_column,
            key=lambda series: series.abs(),
            ascending=False,
        )

        for _, row in review_rows.iterrows():
            rows.append({
                "issue_type": issue_type,
                "review_key": row.get(key_column, ""),
                "delta": row.get(delta_column),
                "likely_cause": row.get("likely_cause", ""),
                "recommended_action": validation_recommended_action(
                    issue_type,
                    row,
                ),
            })

    if cash_reconciliation is not None and not cash_reconciliation.empty:
        review_rows = cash_reconciliation[
            cash_reconciliation["validation_status"] == "REVIEW"
        ].copy()
        review_rows = review_rows.sort_values(
            "cash_flow_delta",
            key=lambda series: series.abs(),
            ascending=False,
        )

        for _, row in review_rows.iterrows():
            rows.append({
                "issue_type": "cash_ledger_daily_trade_cash_flow",
                "review_key": (
                    f"{row.get('date', '')}|"
                    f"{row.get('account_bucket', '')}"
                ),
                "delta": row.get("cash_flow_delta"),
                "likely_cause": (
                    "Extracted trade cash flow does not match the statement "
                    "cash ledger for this day/account bucket."
                ),
                "recommended_action": (
                    "Review extracted rows and statement TRD/ADJ rows for "
                    "this date before approving corrections."
                ),
            })

    return pd.DataFrame(rows)


def validation_recommended_action(issue_type, row):
    if issue_type == "open_pnl":
        return (
            "Use statement P/L Open or market data to value open lots; do "
            "not treat opening cash flow as open PnL."
        )

    if issue_type == "fees":
        return (
            "Prefer statement YTD fee totals for validation and review "
            "bucket-level fee model assumptions."
        )

    if issue_type == "closed_gross_pnl":
        return (
            "Review the root/symbol trade rows and any cash-ledger PnL "
            "corrections before changing trade history."
        )

    return "Review the validation row."


def settled_futures_symbols(position_summary, tolerance=0.01):
    if position_summary.empty:
        return set()

    settled = position_summary[
        (position_summary["open_pnl"].abs() <= tolerance)
        & (position_summary["day_pnl"].abs() <= tolerance)
        & (position_summary["mark_value"].abs() <= tolerance)
    ]

    return set(settled["symbol"])


def build_futures_statement_settlements(
    realized_trades,
    open_positions,
    statement_rows,
    position_summary,
):
    if (
        realized_trades.empty
        or open_positions.empty
        or statement_rows.empty
        or position_summary.empty
    ):
        return pd.DataFrame(columns=realized_trades.columns)

    futures_open = open_positions[
        open_positions["Type"].astype(str).str.upper() == "FUTURE"
    ].copy()

    if futures_open.empty:
        return pd.DataFrame(columns=realized_trades.columns)

    settled_symbols = settled_futures_symbols(position_summary)
    futures_open = futures_open[
        futures_open["Symbol"].isin(settled_symbols)
    ]

    if futures_open.empty:
        return pd.DataFrame(columns=realized_trades.columns)

    rows = []
    starting_equity = realized_trades["starting_equity"].dropna().iloc[0]

    for symbol, open_group in futures_open.groupby("Symbol", sort=True):
        ledger_group = statement_rows[
            statement_rows["symbol"] == symbol
        ].sort_values("timestamp")

        if ledger_group.empty:
            continue

        statement_cash_flow = float(ledger_group["cash_flow"].sum())
        realized_symbol_pnl = float(
            pd.to_numeric(
                realized_trades.loc[
                    realized_trades["Symbol"] == symbol,
                    "net_pnl",
                ],
                errors="coerce",
            ).fillna(0.0).sum()
        )
        settlement_net_pnl = statement_cash_flow - realized_symbol_pnl
        margin_requirement = float(
            pd.to_numeric(
                open_group["margin_requirement"],
                errors="coerce",
            ).fillna(0.0).sum()
        )
        timestamp = ledger_group["timestamp"].max()
        qty = float(
            pd.to_numeric(
                open_group["remaining_qty"],
                errors="coerce",
            ).fillna(0.0).sum()
        )
        return_on_margin = (
            settlement_net_pnl / margin_requirement
            if margin_requirement > 0
            else None
        )
        log_return_on_margin = (
            math.log(1 + return_on_margin)
            if return_on_margin is not None and 1 + return_on_margin > 0
            else None
        )

        rows.append({
            "Exec Time": formatted_exec_time(timestamp),
            "timestamp": timestamp,
            "Strategy_Name": open_group["Strategy_Name"].iloc[0],
            "Symbol": symbol,
            "Spread": "FUTURE",
            "Side": open_group["Side"].iloc[0],
            "Qty": qty,
            "Pos Effect": "FUTURES_SETTLED",
            "Exp": open_group["Exp"].iloc[0],
            "Strike": None,
            "Type": "FUTURE",
            "open_exec_time": "; ".join(
                open_group["open_exec_time"].astype(str)
            ),
            "close_exec_time": formatted_exec_time(timestamp),
            "realized_status": "FUTURES_SETTLED",
            "execution_leg_count": int(len(open_group)),
            "execution_symbols": symbol,
            "open_net_pnl": 0.0,
            "close_net_pnl": settlement_net_pnl,
            "trade_pnl": settlement_net_pnl,
            "fees": 0.0,
            "net_pnl": settlement_net_pnl,
            "margin_requirement": margin_requirement,
            "return_on_margin": return_on_margin,
            "log_return_on_margin": log_return_on_margin,
            "starting_equity": starting_equity,
            "futures_statement_cash_flow": statement_cash_flow,
            "futures_statement_realized_net_pnl": realized_symbol_pnl,
            "futures_statement_source": "futures_statement_cash_flow",
        })

    if not rows:
        return pd.DataFrame(columns=realized_trades.columns)

    return pd.DataFrame(rows)


def daily_market_data_file(symbol, market_data_dir=MARKET_DATA_DIR):
    if not symbol:
        return None

    clean_symbol = str(symbol).strip().lstrip("/").upper()
    candidates = SETTLEMENT_SYMBOL_FILE_ALIASES.get(
        clean_symbol,
        [clean_symbol],
    )

    for candidate in candidates:
        path = os.path.join(
            market_data_dir,
            "daily",
            f"{candidate}.csv",
        )

        if os.path.isfile(path):
            return path

    return None


def expired_option_underlying_symbol(row):
    root = normalize_root_symbol(row.get("Symbol"))

    if root in FUTURES_OPTIONS:
        return FUTURES_OPTION_SETTLEMENT_UNDERLYINGS.get(root, root)

    if root in INDEX_OPTIONS:
        return root

    return None


def load_daily_close_series(symbol, market_data_dir=MARKET_DATA_DIR):
    path = daily_market_data_file(
        symbol,
        market_data_dir,
    )

    if path is None:
        return None

    prices = pd.read_csv(path)

    if "close" not in prices.columns:
        return None

    if "date" in prices.columns:
        dates = pd.to_datetime(
            prices["date"],
            errors="coerce",
        )
    elif "timestamp" in prices.columns:
        dates = pd.to_datetime(
            prices["timestamp"],
            errors="coerce",
            utc=True,
        )
    else:
        return None

    prices = prices.copy()
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_convert(None)
    prices["date"] = dates.dt.normalize()
    prices["close"] = pd.to_numeric(
        prices["close"],
        errors="coerce",
    )
    prices = prices.dropna(
        subset=[
            "date",
            "close",
        ]
    )

    if prices.empty:
        return None

    return prices.sort_values("date").drop_duplicates(
        "date",
        keep="last",
    ).set_index("date")["close"]


def option_intrinsic_value(option_type, strike, underlying_close):
    option_type = str(option_type).upper()

    if option_type == "CALL":
        return max(
            0.0,
            underlying_close - strike,
        )

    if option_type == "PUT":
        return max(
            0.0,
            strike - underlying_close,
        )

    return None


def apply_expired_option_settlement_checks(
    realized_trades,
    market_data_dir=MARKET_DATA_DIR,
):
    if realized_trades.empty:
        return realized_trades, pd.DataFrame()

    trades = realized_trades.copy()
    reports = []
    close_cache = {}

    settlement_columns = {
        "expired_option_original_net_pnl": None,
        "expired_option_settlement_adjustment": 0.0,
        "expired_option_adjusted": False,
        "expired_option_settlement_status": "",
        "expired_option_underlying_symbol": "",
        "expired_option_underlying_close": None,
        "expired_option_intrinsic_points": None,
        "expired_option_intrinsic_value": None,
    }

    for column, default in settlement_columns.items():
        if column not in trades.columns:
            trades[column] = default

    expired_mask = (
        trades.get("realized_status", "")
        .astype(str)
        .str.upper()
        == "EXPIRED"
    ) & trades.apply(is_option_trade, axis=1)

    for index, row in trades[expired_mask].iterrows():
        status = "unchecked"
        detail = ""
        underlying_symbol = expired_option_underlying_symbol(row)
        expiration = parse_option_expiration(row)
        strike = parse_number(row.get("Strike"))
        option_type = str(row.get("Type", "")).upper()
        qty = abs(parse_number(row.get("Qty")) or 0.0)
        multiplier = get_contract_multiplier(row)
        underlying_close = None
        intrinsic_points = None
        intrinsic_value = None
        adjustment = 0.0

        if not underlying_symbol:
            status = "unsupported_symbol"
            detail = "No settlement underlying mapping is available."
        elif expiration is None:
            status = "missing_expiration"
            detail = "Could not parse option expiration."
        elif strike is None:
            status = "missing_strike"
            detail = "Could not parse option strike."
        elif option_type not in OPTION_TYPES:
            status = "missing_option_type"
            detail = "Could not parse option type."
        elif qty == 0:
            status = "missing_quantity"
            detail = "Could not parse option quantity."
        else:
            if underlying_symbol not in close_cache:
                close_cache[underlying_symbol] = load_daily_close_series(
                    underlying_symbol,
                    market_data_dir,
                )

            closes = close_cache[underlying_symbol]

            if closes is None:
                status = "missing_market_data"
                detail = (
                    "No local daily market data file found for "
                    f"{underlying_symbol}."
                )
            elif expiration not in closes.index:
                status = "missing_expiration_close"
                detail = (
                    "No local daily close found on expiration date "
                    f"{expiration.date().isoformat()}."
                )
            else:
                underlying_close = float(closes.loc[expiration])
                intrinsic_points = option_intrinsic_value(
                    option_type,
                    strike,
                    underlying_close,
                )
                intrinsic_value = intrinsic_points * multiplier * qty

                if intrinsic_value == 0:
                    status = "verified_otm"
                    detail = "Expired option appears OTM by local daily close."
                else:
                    side = str(row.get("Side", "")).upper()
                    adjustment = (
                        -intrinsic_value
                        if side == "SELL"
                        else intrinsic_value
                    )
                    status = "adjusted_itm"
                    detail = (
                        "Expired option appears ITM by local daily close; "
                        "net PnL adjusted by intrinsic value."
                    )

        original_net_pnl = parse_number(row.get("net_pnl")) or 0.0
        original_trade_pnl = parse_number(row.get("trade_pnl")) or 0.0
        original_close_net_pnl = parse_number(row.get("close_net_pnl")) or 0.0
        original_close_trade_pnl = (
            parse_number(row.get("close_trade_pnl")) or 0.0
        )

        if adjustment:
            adjusted_net_pnl = original_net_pnl + adjustment
            adjusted_trade_pnl = original_trade_pnl + adjustment
            trades.at[index, "close_net_pnl"] = (
                original_close_net_pnl + adjustment
            )
            trades.at[index, "close_trade_pnl"] = (
                original_close_trade_pnl + adjustment
            )
            trades.at[index, "net_pnl"] = adjusted_net_pnl
            trades.at[index, "trade_pnl"] = adjusted_trade_pnl

            margin_requirement = parse_number(row.get("margin_requirement")) or 0.0
            if margin_requirement > 0:
                return_on_margin = adjusted_net_pnl / margin_requirement
                trades.at[index, "return_on_margin"] = return_on_margin
                trades.at[index, "log_return_on_margin"] = (
                    None
                    if 1 + return_on_margin <= 0
                    else math.log(1 + return_on_margin)
                )

        trades.at[index, "expired_option_original_net_pnl"] = original_net_pnl
        trades.at[index, "expired_option_settlement_adjustment"] = adjustment
        trades.at[index, "expired_option_adjusted"] = bool(adjustment)
        trades.at[index, "expired_option_settlement_status"] = status
        trades.at[index, "expired_option_underlying_symbol"] = (
            underlying_symbol or ""
        )
        trades.at[index, "expired_option_underlying_close"] = underlying_close
        trades.at[index, "expired_option_intrinsic_points"] = intrinsic_points
        trades.at[index, "expired_option_intrinsic_value"] = intrinsic_value

        reports.append({
            "Strategy_Name": row.get("Strategy_Name"),
            "Exec Time": row.get("Exec Time"),
            "open_exec_time": row.get("open_exec_time"),
            "Symbol": row.get("Symbol"),
            "Side": row.get("Side"),
            "Qty": qty,
            "Exp": row.get("Exp"),
            "expiration_date": (
                expiration.date().isoformat()
                if expiration is not None
                else ""
            ),
            "Strike": row.get("Strike"),
            "Type": row.get("Type"),
            "underlying_symbol": underlying_symbol or "",
            "underlying_close": underlying_close,
            "multiplier": multiplier,
            "intrinsic_points": intrinsic_points,
            "intrinsic_value": intrinsic_value,
            "original_net_pnl": original_net_pnl,
            "settlement_adjustment": adjustment,
            "adjusted_net_pnl": original_net_pnl + adjustment,
            "settlement_status": status,
            "detail": detail,
        })

    if reports:
        report = pd.DataFrame(reports).sort_values(
            [
                "Strategy_Name",
                "expiration_date",
                "open_exec_time",
            ],
            na_position="last",
        )
    else:
        report = pd.DataFrame(columns=[
            "Strategy_Name",
            "Exec Time",
            "open_exec_time",
            "Symbol",
            "Side",
            "Qty",
            "Exp",
            "expiration_date",
            "Strike",
            "Type",
            "underlying_symbol",
            "underlying_close",
            "multiplier",
            "intrinsic_points",
            "intrinsic_value",
            "original_net_pnl",
            "settlement_adjustment",
            "adjusted_net_pnl",
            "settlement_status",
            "detail",
        ])

    return recalculate_account_columns(trades), report


def remove_settled_futures_open_positions(open_positions, settlements):
    if open_positions.empty or settlements.empty:
        return open_positions

    settled_symbols = set(settlements["Symbol"])
    settled_mask = (
        (open_positions["Type"].astype(str).str.upper() == "FUTURE")
        & open_positions["Symbol"].isin(settled_symbols)
    )

    return open_positions.loc[
        ~settled_mask
    ].copy()


def statement_futures_quantity(position_summary, symbol):
    if position_summary is None or position_summary.empty:
        return None

    symbol_summary = position_summary[
        position_summary["symbol"] == symbol
    ]

    if symbol_summary.empty:
        return None

    if "statement_qty" in symbol_summary.columns:
        quantities = pd.to_numeric(
            symbol_summary["statement_qty"],
            errors="coerce",
        ).dropna()

        if not quantities.empty:
            return abs(float(quantities.iloc[-1]))

    if symbol in settled_futures_symbols(position_summary):
        return 0.0

    return None


def trim_futures_open_group_to_quantity(group, target_qty):
    if target_qty <= 0:
        return group.iloc[0:0].copy()

    working = group.copy()
    working["_open_timestamp"] = pd.to_datetime(
        working["open_exec_time"],
        errors="coerce",
    )
    working = working.sort_values(
        ["_open_timestamp"],
        ascending=False,
        na_position="last",
    )
    remaining_to_keep = target_qty
    kept_rows = []

    for _, row in working.iterrows():
        row_qty = abs(parse_number(row.get("remaining_qty")) or 0.0)

        if row_qty <= 0 or remaining_to_keep <= 0:
            continue

        kept = row.copy()
        qty_to_keep = min(row_qty, remaining_to_keep)
        kept["remaining_qty"] = qty_to_keep
        remaining_to_keep -= qty_to_keep
        kept_rows.append(kept)

    if not kept_rows:
        return group.iloc[0:0].copy()

    trimmed = pd.DataFrame(kept_rows).sort_values(
        ["_open_timestamp"],
        na_position="last",
    )

    return trimmed.drop(columns=["_open_timestamp"])


def reconcile_open_futures_positions_to_statement(
    open_positions,
    position_summary,
    tolerance=0.000001,
):
    if (
        open_positions is None
        or open_positions.empty
        or position_summary is None
        or position_summary.empty
    ):
        return open_positions

    working = open_positions.copy()

    if "statement_qty" not in working.columns:
        working["statement_qty"] = pd.NA
    if "statement_qty_delta" not in working.columns:
        working["statement_qty_delta"] = pd.NA
    if "statement_position_status" not in working.columns:
        working["statement_position_status"] = ""

    futures_mask = working["Type"].astype(str).str.upper() == "FUTURE"
    non_futures = working.loc[~futures_mask].copy()
    reconciled_groups = []

    for symbol, group in working.loc[futures_mask].groupby(
        "Symbol",
        sort=False,
    ):
        target_qty = statement_futures_quantity(
            position_summary,
            symbol,
        )

        if target_qty is None:
            group = group.copy()
            group["statement_position_status"] = "not_in_statement_summary"
            reconciled_groups.append(group)
            continue

        current_qty = float(
            pd.to_numeric(
                group["remaining_qty"],
                errors="coerce",
            ).fillna(0.0).abs().sum()
        )
        group = group.copy()
        group["statement_qty"] = target_qty
        group["statement_qty_delta"] = current_qty - target_qty

        if current_qty <= target_qty + tolerance:
            group["statement_position_status"] = "matches_statement_qty"
            reconciled_groups.append(group)
            continue

        trimmed = trim_futures_open_group_to_quantity(
            group,
            target_qty,
        )

        if trimmed.empty:
            continue

        trimmed["statement_qty"] = target_qty
        trimmed["statement_qty_delta"] = current_qty - target_qty
        trimmed["statement_position_status"] = "trimmed_to_statement_qty"
        reconciled_groups.append(trimmed)

    frames = [non_futures] + reconciled_groups
    frames = [
        frame
        for frame in frames
        if frame is not None and not frame.empty
    ]

    if not frames:
        return working.iloc[0:0].copy()

    return pd.concat(
        frames,
        ignore_index=True,
        sort=False,
    )


def build_data_quality_warnings(
    cleaned_trades,
    realized_trades,
    open_positions,
    expired_option_settlement=None,
    open_position_audit=None,
    settlement_coverage=None,
):
    rows = []

    def add_warning(check_name, severity, count, detail):
        if count:
            rows.append({
                "check": check_name,
                "severity": severity,
                "count": int(count),
                "detail": detail,
            })

    strategy_names = cleaned_trades.get("Strategy_Name")

    if strategy_names is not None:
        blank_strategy = (
            strategy_names.isna()
            | (strategy_names.astype(str).str.strip() == "")
        )
        add_warning(
            "missing_strategy_name",
            "high",
            blank_strategy.sum(),
            "Rows without Strategy_Name cannot be attributed to a strategy.",
        )

    timestamps = pd.to_datetime(
        cleaned_trades.get("timestamp"),
        errors="coerce",
    )
    add_warning(
        "missing_or_invalid_exec_time",
        "high",
        timestamps.isna().sum(),
        "Rows with invalid Exec Time may sort incorrectly or be excluded.",
    )

    dedupe_columns = [
        column
        for column in [
            "Exec Time",
            "Spread",
            "Side",
            "Qty",
            "Pos Effect",
            "Symbol",
            "Exp",
            "Strike",
            "Type",
            "Price",
            "Net Price",
            "Order Type",
            "Order ID",
            "Exp.1",
            "Settlement Date",
        ]
        if column in cleaned_trades.columns
    ]

    if dedupe_columns:
        duplicate_keys = cleaned_trades.apply(
            lambda row: "|".join(
                str(row.get(column, "")).strip()
                for column in dedupe_columns
            ),
            axis=1,
        )
        duplicate_frame = cleaned_trades.copy()
        duplicate_frame["_execution_key"] = duplicate_keys

        if "statement_file" in duplicate_frame.columns:
            duplicate_group_sizes = duplicate_frame.groupby(
                "_execution_key"
            )["statement_file"].nunique()
            duplicate_source_keys = set(
                duplicate_group_sizes[
                    duplicate_group_sizes > 1
                ].index
            )
            duplicate_count = duplicate_frame[
                duplicate_frame["_execution_key"].isin(duplicate_source_keys)
            ].shape[0]
        else:
            duplicate_count = duplicate_keys.duplicated(keep=False).sum()

        add_warning(
            "duplicate_execution_keys",
            "high",
            duplicate_count,
            (
                "Execution keys repeated across statement sources may "
                "double-count PnL. Repeated fills within one source are not "
                "flagged by this check."
            ),
        )

    margin = pd.to_numeric(
        realized_trades.get("margin_requirement"),
        errors="coerce",
    )
    add_warning(
        "zero_or_missing_realized_margin",
        "medium",
        ((margin.isna()) | (margin <= 0)).sum(),
        "Return-on-margin and position sizing need positive margin.",
    )

    return_on_margin = pd.to_numeric(
        realized_trades.get("return_on_margin"),
        errors="coerce",
    )
    add_warning(
        "return_on_margin_loss_exceeds_100_percent",
        "medium",
        (return_on_margin <= -1).sum(),
        "These trades are included in Safe-F using simple margin returns.",
    )
    add_warning(
        "open_positions_excluded_from_realized_stats",
        "info",
        len(open_positions),
        "Open positions are shown separately and excluded from realized stats.",
    )

    if open_position_audit is not None and not open_position_audit.empty:
        statuses = open_position_audit["open_position_status"].astype(str)
        add_warning(
            "open_positions_need_review",
            "high",
            statuses.isin(
                [
                    "stale_missing_close",
                    "expired_needs_settlement",
                    "needs_review",
                ]
            ).sum(),
            (
                "Open positions that appear stale or unclassified may cause "
                f"realized PnL to be understated. See {OPEN_POSITION_AUDIT_FILE}."
            ),
        )
        add_warning(
            "fractional_open_position_residuals",
            "medium",
            statuses.eq("fractional_residual").sum(),
            (
                "Tiny stock/crypto residuals are excluded from realized "
                f"stats. See {OPEN_POSITION_AUDIT_FILE}."
            ),
        )

    if expired_option_settlement is not None and not expired_option_settlement.empty:
        statuses = expired_option_settlement["settlement_status"].astype(str)
        add_warning(
            "expired_options_adjusted_for_intrinsic_value",
            "high",
            (statuses == "adjusted_itm").sum(),
            (
                "Expired option PnL was adjusted by estimated intrinsic value "
                f"from local daily market data. See {EXPIRED_OPTION_SETTLEMENT_FILE}."
            ),
        )
        add_warning(
            "expired_options_missing_settlement_data",
            "medium",
            statuses.str.startswith("missing").sum(),
            (
                "Expired option PnL could not be checked because required "
                f"settlement inputs were missing. See {EXPIRED_OPTION_SETTLEMENT_FILE}."
            ),
        )
        add_warning(
            "expired_options_verified_otm",
            "info",
            (statuses == "verified_otm").sum(),
            (
                "Expired option PnL was checked against local daily market "
                "data and appeared OTM."
            ),
        )

    if settlement_coverage is not None and not settlement_coverage.empty:
        missing = pd.to_numeric(
            settlement_coverage.get("settlement_missing_count"),
            errors="coerce",
        ).fillna(0)
        add_warning(
            "strategies_with_missing_settlement_coverage",
            "medium",
            (missing > 0).sum(),
            (
                "Some strategies have expired options that could not be "
                f"settlement-checked. See {SETTLEMENT_COVERAGE_FILE}."
            ),
        )

    if not rows:
        rows.append({
            "check": "no_data_quality_warnings",
            "severity": "info",
            "count": 0,
            "detail": "No monitored data-quality issues were found.",
        })

    return pd.DataFrame(rows)


def recalculate_account_columns(df):
    if df.empty:
        return df

    df = df.sort_values(
        ["timestamp"],
        na_position="last",
    ).copy()
    starting_equity = float(
        pd.to_numeric(
            df["starting_equity"],
            errors="coerce",
        ).dropna().iloc[0]
    )
    df["net_pnl"] = pd.to_numeric(
        df["net_pnl"],
        errors="coerce",
    ).fillna(0.0)
    df["cumulative_pnl"] = df["net_pnl"].cumsum()
    df["ending_equity"] = starting_equity + df["cumulative_pnl"]

    previous_equity = starting_equity
    log_returns = []
    cumulative_log_returns = []

    for ending_equity in df["ending_equity"]:
        if previous_equity <= 0 or ending_equity <= 0:
            log_return = None
        else:
            log_return = math.log(
                ending_equity / previous_equity
            )

        log_returns.append(log_return)
        cumulative_log_returns.append(
            None
            if ending_equity <= 0
            else math.log(ending_equity / starting_equity)
        )
        previous_equity = ending_equity

    df["log_return"] = log_returns
    df["cumulative_log_return"] = cumulative_log_returns
    df["cumulative_log_return_on_margin"] = (
        df["log_return_on_margin"]
        .fillna(0.0)
        .cumsum()
    )

    return df


def build_strategy_trade_ledgers(realized_trades):
    if realized_trades.empty:
        return realized_trades

    ledgers = []

    for strategy_name, group in realized_trades.groupby(
        "Strategy_Name",
        sort=True,
    ):
        group = group.sort_values(
            ["timestamp"],
            na_position="last",
        ).copy()
        group["strategy_trade_number"] = range(1, len(group) + 1)
        group["strategy_pnl"] = pd.to_numeric(
            group["net_pnl"],
            errors="coerce",
        ).fillna(0.0)
        group["strategy_cumulative_pnl"] = group["strategy_pnl"].cumsum()
        ledgers.append(group)

    return pd.concat(
        ledgers,
        ignore_index=True,
    )


def filter_strategies(df, strategies):
    if not strategies:
        return df

    requested = {
        clean_strategy_name(strategy).lower()
        for strategy in strategies
    }

    filtered = df[
        df["Strategy_Name"]
        .str.lower()
        .isin(requested)
    ].copy()

    if filtered.empty:
        available = "\n".join(
            sorted(df["Strategy_Name"].unique())
        )
        raise ValueError(
            "No rows matched the requested strategy filter. "
            f"Available strategies:\n{available}"
        )

    return filtered


def parse_exec_timestamp(value):
    if pd.isna(value):
        return pd.NaT

    first_value = str(value).split(";")[0].strip()

    return pd.to_datetime(
        first_value,
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )


def is_short_option_realized_trade(row):
    return (
        is_option_trade(row)
        and str(row.get("Side", "")).upper() == "SELL"
    )


def margin_log_return(pnl, margin_requirement):
    margin_requirement = parse_number(margin_requirement) or 0.0

    if margin_requirement <= 0:
        return None

    margin_return = pnl / margin_requirement

    if 1 + margin_return <= 0:
        return None

    return math.log(1 + margin_return)


def build_strategy_pnl_events(realized_trades):
    events = []

    for trade_number, (_, row) in enumerate(
        realized_trades.iterrows(),
        start=1,
    ):
        base = row.to_dict()
        margin_requirement = parse_number(
            row.get("margin_requirement")
        ) or 0.0

        if is_short_option_realized_trade(row):
            open_pnl = parse_number(row.get("open_net_pnl")) or 0.0
            close_pnl = parse_number(row.get("close_net_pnl")) or 0.0

            if open_pnl != 0:
                open_event = {
                    **base,
                    "realized_trade_number": trade_number,
                    "pnl_event_type": "OPEN",
                    "Exec Time": row.get("open_exec_time"),
                    "timestamp": parse_exec_timestamp(row.get("open_exec_time")),
                    "net_pnl": open_pnl,
                    "log_return_on_margin": margin_log_return(
                        open_pnl,
                        margin_requirement,
                    ),
                }
                events.append(open_event)

            if close_pnl != 0:
                close_event = {
                    **base,
                    "realized_trade_number": trade_number,
                    "pnl_event_type": "CLOSE",
                    "Exec Time": row.get("close_exec_time"),
                    "timestamp": parse_exec_timestamp(row.get("close_exec_time")),
                    "net_pnl": close_pnl,
                    "log_return_on_margin": margin_log_return(
                        close_pnl,
                        margin_requirement,
                    ),
                }
                events.append(close_event)

            continue

        event = {
            **base,
            "realized_trade_number": trade_number,
            "pnl_event_type": row.get("realized_status", "REALIZED"),
            "net_pnl": row.get("net_pnl"),
        }
        events.append(event)

    if not events:
        return pd.DataFrame()

    return pd.DataFrame(events)


def build_strategy_equity_curves(df):
    starting_equity = float(
        pd.to_numeric(
            df["starting_equity"],
            errors="coerce",
        ).dropna().iloc[0]
    )

    curves = []

    for strategy_name, group in df.groupby("Strategy_Name", sort=True):
        group = group.sort_values(
            ["timestamp"],
            na_position="last",
        ).copy()

        group["strategy_trade_number"] = range(1, len(group) + 1)
        group["strategy_pnl"] = group["net_pnl"]
        group["CMPNL"] = group["strategy_pnl"].cumsum()
        group["strategy_equity"] = starting_equity + group["CMPNL"]
        group["strategy_peak_equity"] = group["strategy_equity"].cummax()
        group["strategy_drawdown"] = (
            group["strategy_equity"]
            / group["strategy_peak_equity"]
            - 1
        )
        group["strategy_drawdown_dollars"] = (
            group["strategy_equity"]
            - group["strategy_peak_equity"]
        )
        group["strategy_log_return"] = (
            group["strategy_equity"]
            / group["strategy_equity"].shift(1).fillna(starting_equity)
        ).apply(
            lambda value: None if value <= 0 else math.log(value)
        )
        group["cm_returns"] = (
            group["log_return_on_margin"]
            .fillna(0.0)
            .cumsum()
        )

        curves.append(group)

    return pd.concat(
        curves,
        ignore_index=True,
    )


def save_strategy_trade_ledgers(strategy_trade_ledgers):
    os.makedirs(
        STRATEGY_TRADES_DIR,
        exist_ok=True,
    )

    output_files = []
    preferred_columns = [
        "strategy_trade_number",
        "timestamp",
        "Exec Time",
        "Strategy_Name",
        "Symbol",
        "Spread",
        "Side",
        "Qty",
        "Pos Effect",
        "Exp",
        "Strike",
        "Type",
        "realized_status",
        "execution_leg_count",
        "execution_symbols",
        "open_exec_time",
        "close_exec_time",
        "open_net_pnl",
        "close_net_pnl",
        "strategy_pnl",
        "strategy_cumulative_pnl",
        "trade_pnl",
        "fees",
        "net_pnl",
        "margin_requirement",
        "return_on_margin",
        "log_return_on_margin",
    ]

    for strategy_name, group in strategy_trade_ledgers.groupby(
        "Strategy_Name",
        sort=True,
    ):
        output_file = (
            f"{STRATEGY_TRADES_DIR}/"
            f"{safe_filename(strategy_name)}_trades.csv"
        )
        columns = [
            column
            for column in preferred_columns
            if column in group.columns
        ]
        group[columns].to_csv(
            output_file,
            index=False,
        )
        output_files.append(output_file)

    return output_files


def build_account_equity_curve(df):
    required_columns = {
        "net_pnl",
        "cumulative_pnl",
        "ending_equity",
        "log_return",
        "cumulative_log_return",
    }
    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Cannot build account equity curve without columns: {missing}"
        )

    account_curve = df.copy()
    account_curve["account_peak_equity"] = (
        account_curve["ending_equity"]
        .cummax()
    )
    account_curve["account_drawdown"] = (
        account_curve["ending_equity"]
        / account_curve["account_peak_equity"]
        - 1
    )
    account_curve["account_drawdown_dollars"] = (
        account_curve["ending_equity"]
        - account_curve["account_peak_equity"]
    )

    columns = [
        "timestamp",
        "Exec Time",
        "Strategy_Name",
        "Symbol",
        "Spread",
        "Side",
        "Qty",
        "realized_status",
        "execution_leg_count",
        "execution_symbols",
        "open_exec_time",
        "close_exec_time",
        "open_net_pnl",
        "close_net_pnl",
        "net_pnl",
        "cumulative_pnl",
        "starting_equity",
        "ending_equity",
        "account_peak_equity",
        "account_drawdown",
        "account_drawdown_dollars",
        "log_return",
        "cumulative_log_return",
        "return_on_margin",
        "log_return_on_margin",
        "cumulative_log_return_on_margin",
    ]

    return account_curve[
        [column for column in columns if column in account_curve.columns]
    ]


def build_cash_balance_curve(
    cash_ledger,
    start_timestamp=None,
    end_timestamp=None,
):
    if cash_ledger is None or cash_ledger.empty:
        return pd.DataFrame()

    required_columns = {
        "timestamp",
        "type",
        "balance",
        "cash_flow",
    }

    if not required_columns.issubset(cash_ledger.columns):
        return pd.DataFrame()

    curve = cash_ledger.copy()
    curve["timestamp"] = pd.to_datetime(
        curve["timestamp"],
        errors="coerce",
    )
    curve["balance"] = pd.to_numeric(
        curve["balance"],
        errors="coerce",
    )
    curve["cash_flow"] = pd.to_numeric(
        curve["cash_flow"],
        errors="coerce",
    ).fillna(0.0)
    curve = curve.sort_values("timestamp")
    if start_timestamp is not None:
        start_timestamp = pd.Timestamp(start_timestamp)
        curve = curve[
            curve["timestamp"] >= start_timestamp
        ].copy()
    if end_timestamp is not None:
        end_timestamp = pd.Timestamp(end_timestamp)
        curve = curve[
            curve["timestamp"] <= end_timestamp
        ].copy()

    if curve.empty:
        return pd.DataFrame()

    curve["futures_sweep_cash_flow"] = curve["cash_flow"].where(
        curve["type"].astype(str).str.upper() == "FSWP",
        0.0,
    )
    curve["cumulative_futures_sweeps"] = (
        curve["futures_sweep_cash_flow"].cumsum()
    )
    balance_curve = curve[
        curve["type"].astype(str).str.upper() == "BAL"
    ].copy()
    balance_curve = balance_curve[
        balance_curve["timestamp"].notna()
        & balance_curve["balance"].notna()
    ].copy()

    if balance_curve.empty:
        return pd.DataFrame()

    balance_curve["cash_balance"] = balance_curve["balance"]
    balance_curve["cash_balance_peak"] = (
        balance_curve["cash_balance"].cummax()
    )
    balance_curve["cash_balance_drawdown"] = (
        balance_curve["cash_balance"]
        / balance_curve["cash_balance_peak"]
        - 1
    )
    balance_curve["cash_balance_drawdown_dollars"] = (
        balance_curve["cash_balance"]
        - balance_curve["cash_balance_peak"]
    )
    balance_curve["cash_balance_ex_futures_sweeps"] = (
        balance_curve["cash_balance"]
        - balance_curve["cumulative_futures_sweeps"]
    )
    balance_curve["cash_balance_ex_futures_sweeps_peak"] = (
        balance_curve["cash_balance_ex_futures_sweeps"].cummax()
    )
    balance_curve["cash_balance_ex_futures_sweeps_drawdown"] = (
        balance_curve["cash_balance_ex_futures_sweeps"]
        / balance_curve["cash_balance_ex_futures_sweeps_peak"]
        - 1
    )
    balance_curve["cash_balance_ex_futures_sweeps_drawdown_dollars"] = (
        balance_curve["cash_balance_ex_futures_sweeps"]
        - balance_curve["cash_balance_ex_futures_sweeps_peak"]
    )

    columns = [
        "timestamp",
        "cash_balance",
        "cash_balance_peak",
        "cash_balance_drawdown",
        "cash_balance_drawdown_dollars",
        "cumulative_futures_sweeps",
        "cash_balance_ex_futures_sweeps",
        "cash_balance_ex_futures_sweeps_peak",
        "cash_balance_ex_futures_sweeps_drawdown",
        "cash_balance_ex_futures_sweeps_drawdown_dollars",
    ]

    return balance_curve[columns]


def annualized_return(total_return, start_timestamp, end_timestamp):
    if total_return is None or pd.isna(total_return):
        return None

    if start_timestamp is None or end_timestamp is None:
        return None

    elapsed_days = max(
        (end_timestamp - start_timestamp).days,
        1,
    )

    return (1 + total_return) ** (365 / elapsed_days) - 1


def calculate_cash_balance_summary(cash_balance_curve):
    if cash_balance_curve is None or cash_balance_curve.empty:
        return pd.DataFrame(columns=["metric", "value"])

    start_timestamp = cash_balance_curve["timestamp"].iloc[0]
    end_timestamp = cash_balance_curve["timestamp"].iloc[-1]
    start_cash = cash_balance_curve["cash_balance"].iloc[0]
    end_cash = cash_balance_curve["cash_balance"].iloc[-1]
    total_return = safe_ratio(
        end_cash - start_cash,
        start_cash,
    )
    start_adjusted_cash = (
        cash_balance_curve["cash_balance_ex_futures_sweeps"].iloc[0]
    )
    end_adjusted_cash = (
        cash_balance_curve["cash_balance_ex_futures_sweeps"].iloc[-1]
    )
    adjusted_total_return = safe_ratio(
        end_adjusted_cash - start_adjusted_cash,
        start_adjusted_cash,
    )
    stats = {
        "cash_balance_start": start_cash,
        "cash_balance_end": end_cash,
        "cash_balance_total_change": end_cash - start_cash,
        "cash_balance_total_return": total_return,
        "cash_balance_cagr": annualized_return(
            total_return,
            start_timestamp,
            end_timestamp,
        ),
        "cash_balance_max_drawdown": (
            cash_balance_curve["cash_balance_drawdown"].min()
        ),
        "cash_balance_max_drawdown_dollars": (
            cash_balance_curve["cash_balance_drawdown_dollars"].min()
        ),
        "cash_balance_ex_futures_sweeps_start": start_adjusted_cash,
        "cash_balance_ex_futures_sweeps_end": end_adjusted_cash,
        "cash_balance_ex_futures_sweeps_total_change": (
            end_adjusted_cash - start_adjusted_cash
        ),
        "cash_balance_ex_futures_sweeps_total_return": (
            adjusted_total_return
        ),
        "cash_balance_ex_futures_sweeps_cagr": annualized_return(
            adjusted_total_return,
            start_timestamp,
            end_timestamp,
        ),
        "cash_balance_ex_futures_sweeps_max_drawdown": (
            cash_balance_curve[
                "cash_balance_ex_futures_sweeps_drawdown"
            ].min()
        ),
        "cash_balance_ex_futures_sweeps_max_drawdown_dollars": (
            cash_balance_curve[
                "cash_balance_ex_futures_sweeps_drawdown_dollars"
            ].min()
        ),
    }

    return pd.DataFrame([
        {
            "metric": metric,
            "value": value,
        }
        for metric, value in stats.items()
    ])


def build_settlement_coverage(expired_option_settlement):
    columns = [
        "Strategy_Name",
        "expired_option_count",
        "settlement_checked_count",
        "settlement_missing_count",
        "settlement_adjusted_count",
        "settlement_verified_otm_count",
        "settlement_coverage_ratio",
    ]

    if expired_option_settlement is None or expired_option_settlement.empty:
        return pd.DataFrame(columns=columns)

    working = expired_option_settlement.copy()
    working["settlement_status"] = (
        working["settlement_status"].astype(str)
    )
    rows = []

    for strategy_name, group in working.groupby("Strategy_Name", sort=True):
        statuses = group["settlement_status"]
        missing = statuses.str.startswith("missing")
        checked = ~missing
        rows.append({
            "Strategy_Name": strategy_name,
            "expired_option_count": len(group),
            "settlement_checked_count": int(checked.sum()),
            "settlement_missing_count": int(missing.sum()),
            "settlement_adjusted_count": int(
                statuses.eq("adjusted_itm").sum()
            ),
            "settlement_verified_otm_count": int(
                statuses.eq("verified_otm").sum()
            ),
            "settlement_coverage_ratio": safe_ratio(
                int(checked.sum()),
                len(group),
            ),
        })

    return pd.DataFrame(rows, columns=columns)


def classify_open_position(row, as_of_date):
    position_type = str(row.get("Type", "")).upper()
    symbol = str(row.get("Symbol", "")).strip()
    remaining_qty = abs(parse_number(row.get("remaining_qty")) or 0.0)

    if position_type in {"STOCK", "CRYPTO"} and remaining_qty < 0.01:
        return (
            "fractional_residual",
            "Review or ignore tiny fractional remainder.",
        )

    if position_type in OPTION_TYPES:
        expiration = pd.to_datetime(
            row.get("expiration_close"),
            errors="coerce",
        )
        if not pd.isna(expiration) and expiration <= as_of_date:
            return (
                "expired_needs_settlement",
                "Expired option remains open; verify settlement or close row.",
            )
        return (
            "current_position",
            "Current option position excluded from realized stats.",
        )

    if position_type == "FUTURE":
        review_timestamp = futures_contract_review_timestamp(symbol)
        if review_timestamp is None:
            return (
                "needs_review",
                "Could not parse futures contract month.",
            )
        if review_timestamp <= as_of_date:
            return (
                "stale_missing_close",
                "Futures contract month is past; look for settlement or missing close.",
            )
        return (
            "current_position",
            "Current futures position excluded from realized stats.",
        )

    if position_type in {"STOCK", "CRYPTO"}:
        return (
            "current_position",
            "Current position excluded from realized stats.",
        )

    return (
        "needs_review",
        "Open position type is not classified.",
    )


def build_open_position_audit(open_positions, as_of_date):
    if open_positions is None or open_positions.empty:
        return pd.DataFrame(columns=[
            "Strategy_Name",
            "open_exec_time",
            "Symbol",
            "Type",
            "remaining_qty",
            "statement_qty",
            "statement_qty_delta",
            "statement_position_status",
            "open_position_status",
            "recommended_review_action",
        ])

    audit = open_positions.copy()
    as_of_date = pd.Timestamp(as_of_date)
    classifications = audit.apply(
        lambda row: classify_open_position(row, as_of_date),
        axis=1,
    )
    audit["open_position_status"] = [
        classification
        for classification, _ in classifications
    ]
    audit["recommended_review_action"] = [
        action
        for _, action in classifications
    ]

    priority = {
        "stale_missing_close": 0,
        "expired_needs_settlement": 1,
        "needs_review": 2,
        "fractional_residual": 3,
        "current_position": 4,
    }
    audit["_priority"] = audit["open_position_status"].map(
        priority,
    ).fillna(99)

    columns = [
        "Strategy_Name",
        "open_exec_time",
        "Symbol",
        "Spread",
        "Side",
        "remaining_qty",
        "Exp",
        "expiration_close",
        "Strike",
        "Type",
        "margin_requirement",
        "return_on_margin",
        "statement_qty",
        "statement_qty_delta",
        "statement_position_status",
        "status",
        "open_position_status",
        "recommended_review_action",
    ]

    columns = [
        column
        for column in columns
        if column in audit.columns
    ]

    return audit.sort_values(
        [
            "_priority",
            "Strategy_Name",
            "open_exec_time",
        ],
        na_position="last",
    )[[column for column in columns if column in audit.columns]].reset_index(
        drop=True,
    )


def summarize_open_position_audit(open_position_audit):
    if open_position_audit is None or open_position_audit.empty:
        return pd.DataFrame(columns=[
            "Strategy_Name",
            "open_position_count",
            "current_open_position_count",
            "stale_open_position_count",
            "fractional_residual_count",
            "needs_review_open_position_count",
        ])

    grouped = open_position_audit.groupby("Strategy_Name", sort=True)
    rows = []

    for strategy_name, group in grouped:
        statuses = group["open_position_status"].astype(str)
        stale_mask = statuses.isin(
            [
                "stale_missing_close",
                "expired_needs_settlement",
                "needs_review",
            ]
        )
        rows.append({
            "Strategy_Name": strategy_name,
            "open_position_count": len(group),
            "current_open_position_count": int(
                statuses.eq("current_position").sum()
            ),
            "stale_open_position_count": int(
                statuses.eq("stale_missing_close").sum()
                + statuses.eq("expired_needs_settlement").sum()
            ),
            "fractional_residual_count": int(
                statuses.eq("fractional_residual").sum()
            ),
            "needs_review_open_position_count": int(stale_mask.sum()),
        })

    return pd.DataFrame(rows)


def add_strategy_quality_columns(
    summary,
    settlement_coverage,
    open_position_audit,
):
    if summary is None or summary.empty:
        return summary

    enhanced = summary.copy()
    open_summary = summarize_open_position_audit(open_position_audit)

    for frame in [settlement_coverage, open_summary]:
        if frame is not None and not frame.empty:
            enhanced = pd.merge(
                enhanced,
                frame,
                on="Strategy_Name",
                how="left",
            )

    fill_zero_columns = [
        "expired_option_count",
        "settlement_checked_count",
        "settlement_missing_count",
        "settlement_adjusted_count",
        "settlement_verified_otm_count",
        "open_position_count",
        "current_open_position_count",
        "stale_open_position_count",
        "fractional_residual_count",
        "needs_review_open_position_count",
        "zero_or_missing_margin_count",
        "extreme_margin_loss_count",
    ]
    for column in fill_zero_columns:
        if column not in enhanced.columns:
            enhanced[column] = 0
        enhanced[column] = pd.to_numeric(
            enhanced[column],
            errors="coerce",
        ).fillna(0)

    if "settlement_coverage_ratio" not in enhanced.columns:
        enhanced["settlement_coverage_ratio"] = 1.0
    enhanced["settlement_coverage_ratio"] = enhanced[
        "settlement_coverage_ratio"
    ].fillna(1.0)

    def confidence(row):
        if row["settlement_missing_count"] > 0:
            return "Low"
        if row["needs_review_open_position_count"] > 0:
            return "Low"
        if row["zero_or_missing_margin_count"] > max(
            0,
            row["trade_count"] * 0.25,
        ):
            return "Low"
        if row["fractional_residual_count"] > 0:
            return "Medium"
        if row["zero_or_missing_margin_count"] > 0:
            return "Medium"
        if row["extreme_margin_loss_count"] > 0:
            return "Medium"
        if row["expired_option_count"] == 0:
            return "Medium"
        return "High"

    def confidence_reason(row):
        reasons = []
        if row["settlement_missing_count"] > 0:
            reasons.append(
                f"{int(row['settlement_missing_count'])} missing settlement checks"
            )
        if row["needs_review_open_position_count"] > 0:
            reasons.append(
                f"{int(row['needs_review_open_position_count'])} open positions need review"
            )
        if row["fractional_residual_count"] > 0:
            reasons.append(
                f"{int(row['fractional_residual_count'])} fractional residuals"
            )
        if row["zero_or_missing_margin_count"] > 0:
            reasons.append(
                f"{int(row['zero_or_missing_margin_count'])} missing margin rows"
            )
        if row["extreme_margin_loss_count"] > 0:
            reasons.append(
                f"{int(row['extreme_margin_loss_count'])} margin losses <= -100%"
            )
        if not reasons:
            reasons.append("No major data-quality blockers")
        return "; ".join(reasons)

    enhanced["data_confidence"] = enhanced.apply(
        confidence,
        axis=1,
    )
    enhanced["data_confidence_reason"] = enhanced.apply(
        confidence_reason,
        axis=1,
    )

    return enhanced


def calculate_account_summary(account_curve):
    net_pnl = account_curve["net_pnl"]
    wins = net_pnl[net_pnl > 0]
    losses = net_pnl[net_pnl < 0]
    log_returns = account_curve["log_return"].dropna()
    starting_equity = account_curve["starting_equity"].dropna().iloc[0]
    ending_equity = account_curve["ending_equity"].iloc[-1]
    total_pnl = net_pnl.sum()
    total_return = safe_ratio(
        ending_equity - starting_equity,
        starting_equity,
    )
    timestamps = account_curve["timestamp"].dropna()

    if len(timestamps) >= 2:
        elapsed_days = max(
            (timestamps.max() - timestamps.min()).days,
            1,
        )
    else:
        elapsed_days = None

    if total_return is None or elapsed_days is None:
        cagr = None
    else:
        cagr = (
            (1 + total_return)
            ** (365 / elapsed_days)
            - 1
        )

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    max_drawdown = account_curve["account_drawdown"].min()

    if len(log_returns) > 1 and log_returns.std(ddof=1) != 0:
        sharpe_ratio = (
            log_returns.mean()
            / log_returns.std(ddof=1)
            * (len(log_returns) ** 0.5)
        )
    else:
        sharpe_ratio = None

    stats = {
        "trade_count": len(account_curve),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": safe_ratio(len(wins), len(account_curve)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": safe_ratio(gross_profit, gross_loss),
        "total_pnl": total_pnl,
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "max_drawdown_dollars": account_curve["account_drawdown_dollars"].min(),
        "mar_ratio": (
            safe_ratio(cagr, abs(max_drawdown))
            if cagr is not None
            else None
        ),
        "average_trade_pnl": net_pnl.mean(),
        "median_trade_pnl": net_pnl.median(),
        "average_win": wins.mean() if len(wins) else None,
        "average_loss": losses.mean() if len(losses) else None,
        "largest_win": net_pnl.max(),
        "largest_loss": net_pnl.min(),
        "average_return_on_margin": account_curve["return_on_margin"].mean(),
        "cumulative_log_return_on_margin": (
            account_curve["cumulative_log_return_on_margin"].iloc[-1]
            if "cumulative_log_return_on_margin" in account_curve.columns
            else None
        ),
    }

    return pd.DataFrame([
        {
            "metric": metric,
            "value": value,
        }
        for metric, value in stats.items()
    ])


def load_benchmark_prices(benchmark_file):
    prices = pd.read_csv(benchmark_file)

    timestamp_column = (
        "timestamp"
        if "timestamp" in prices.columns
        else "date"
        if "date" in prices.columns
        else None
    )

    if timestamp_column is None or "close" not in prices.columns:
        raise ValueError(
            f"Benchmark file {benchmark_file} must include timestamp/date and close columns."
        )

    prices = prices.copy()
    prices["timestamp"] = pd.to_datetime(
        prices[timestamp_column],
        errors="coerce",
        utc=True,
    ).dt.tz_localize(None)
    prices["close"] = pd.to_numeric(
        prices["close"],
        errors="coerce",
    )
    prices = prices.dropna(
        subset=[
            "timestamp",
            "close",
        ]
    ).sort_values("timestamp")

    return prices


def calculate_buy_hold_benchmark_summary(
    account_curve,
    benchmark_file,
    benchmark_symbol="SPY",
):
    prices = load_benchmark_prices(benchmark_file)

    if prices.empty:
        return pd.DataFrame()

    account_timestamps = pd.to_datetime(
        account_curve["timestamp"],
        errors="coerce",
    ).dropna()

    if account_timestamps.empty:
        return pd.DataFrame()

    start_timestamp = account_timestamps.min()
    end_timestamp = account_timestamps.max()
    window = prices[
        (prices["timestamp"] >= start_timestamp.normalize())
        & (prices["timestamp"] <= end_timestamp.normalize())
    ].copy()

    if len(window) < 2:
        return pd.DataFrame()

    starting_equity = float(
        pd.to_numeric(
            account_curve["starting_equity"],
            errors="coerce",
        ).dropna().iloc[0]
    )
    start_price = float(window["close"].iloc[0])
    shares = starting_equity / start_price
    window["benchmark_equity"] = shares * window["close"]
    window["benchmark_peak_equity"] = window["benchmark_equity"].cummax()
    window["benchmark_drawdown"] = (
        window["benchmark_equity"]
        / window["benchmark_peak_equity"]
        - 1
    )
    ending_equity = float(window["benchmark_equity"].iloc[-1])
    total_return = safe_ratio(
        ending_equity - starting_equity,
        starting_equity,
    )
    elapsed_days = max(
        (window["timestamp"].iloc[-1] - window["timestamp"].iloc[0]).days,
        1,
    )
    cagr = (
        (1 + total_return)
        ** (365 / elapsed_days)
        - 1
        if total_return is not None
        else None
    )
    max_drawdown = window["benchmark_drawdown"].min()
    account_ending_equity = float(account_curve["ending_equity"].iloc[-1])
    account_total_return = safe_ratio(
        account_ending_equity - starting_equity,
        starting_equity,
    )

    stats = {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_file": benchmark_file,
        "benchmark_start_date": window["timestamp"].iloc[0].date().isoformat(),
        "benchmark_end_date": window["timestamp"].iloc[-1].date().isoformat(),
        "benchmark_start_price": start_price,
        "benchmark_end_price": float(window["close"].iloc[-1]),
        "benchmark_shares": shares,
        "benchmark_starting_equity": starting_equity,
        "benchmark_ending_equity": ending_equity,
        "benchmark_total_pnl": ending_equity - starting_equity,
        "benchmark_total_return": total_return,
        "benchmark_cagr": cagr,
        "benchmark_max_drawdown": max_drawdown,
        "benchmark_mar_ratio": (
            safe_ratio(cagr, abs(max_drawdown))
            if cagr is not None
            else None
        ),
        "account_vs_benchmark_ending_equity": (
            account_ending_equity - ending_equity
        ),
        "account_vs_benchmark_total_return": (
            account_total_return - total_return
            if account_total_return is not None and total_return is not None
            else None
        ),
    }

    return pd.DataFrame([
        {
            "metric": metric,
            "value": value,
        }
        for metric, value in stats.items()
    ])


def calculate_strategy_summary(realized_trades, equity_curves):
    rows = []

    for strategy_name, group in realized_trades.groupby("Strategy_Name", sort=True):
        group = group.sort_values(
            ["timestamp"],
            na_position="last",
        )
        net_pnl = group["net_pnl"]
        margin_requirement = pd.to_numeric(
            group["margin_requirement"],
            errors="coerce",
        )
        return_on_margin = pd.to_numeric(
            group["return_on_margin"],
            errors="coerce",
        )
        wins = net_pnl[net_pnl > 0]
        losses = net_pnl[net_pnl < 0]
        curve_group = equity_curves[
            equity_curves["Strategy_Name"] == strategy_name
        ].sort_values(
            ["timestamp"],
            na_position="last",
        )

        starting_equity = (
            curve_group["strategy_equity"].iloc[0]
            - curve_group["strategy_pnl"].iloc[0]
        )
        ending_equity = curve_group["strategy_equity"].iloc[-1]
        total_pnl = net_pnl.sum()
        total_return = safe_ratio(
            ending_equity - starting_equity,
            starting_equity,
        )

        timestamps = group["timestamp"].dropna()

        if len(timestamps) >= 2:
            elapsed_days = max(
                (timestamps.max() - timestamps.min()).days,
                1,
            )
        else:
            elapsed_days = None

        if total_return is None or elapsed_days is None:
            cagr = None
        else:
            cagr = (
                (1 + total_return)
                ** (365 / elapsed_days)
                - 1
            )

        max_drawdown = curve_group["strategy_drawdown"].min()
        max_drawdown_dollars = curve_group["strategy_drawdown_dollars"].min()
        gross_profit = wins.sum()
        gross_loss = abs(losses.sum())
        profit_factor = safe_ratio(
            gross_profit,
            gross_loss,
        )
        loss_rate = (
            1 - safe_ratio(len(wins), len(group))
            if safe_ratio(len(wins), len(group)) is not None
            else None
        )
        average_win = wins.mean() if len(wins) else None
        average_loss = losses.mean() if len(losses) else None
        expectancy = None

        if average_win is not None and average_loss is not None:
            expectancy = (
                safe_ratio(len(wins), len(group)) * average_win
                + loss_rate * average_loss
            )

        log_returns = curve_group["strategy_log_return"].dropna()

        if len(log_returns) > 1 and log_returns.std(ddof=1) != 0:
            sharpe_ratio = (
                log_returns.mean()
                / log_returns.std(ddof=1)
                * (len(log_returns) ** 0.5)
            )
        else:
            sharpe_ratio = None

        margin_returns = group["log_return_on_margin"].dropna()

        if len(margin_returns) > 1 and margin_returns.std(ddof=1) != 0:
            margin_sharpe_ratio = (
                margin_returns.mean()
                / margin_returns.std(ddof=1)
                * (len(margin_returns) ** 0.5)
            )
        else:
            margin_sharpe_ratio = None

        mar_ratio = (
            safe_ratio(cagr, abs(max_drawdown))
            if cagr is not None
            else None
        )
        recent_group = group.tail(20)
        recent_net_pnl = recent_group["net_pnl"]
        recent_wins = recent_net_pnl[recent_net_pnl > 0]
        recent_losses = recent_net_pnl[recent_net_pnl < 0]
        last_10_trade_pnl = group["net_pnl"].tail(10).sum()
        last_20_profit_factor = safe_ratio(
            recent_wins.sum(),
            abs(recent_losses.sum()),
        )
        rolling_20_win_rate = safe_ratio(
            len(recent_wins),
            len(recent_group),
        )
        rolling_20_average_return_on_margin = recent_group[
            "return_on_margin"
        ].mean()
        recent_pnl_curve = recent_net_pnl.cumsum()
        recent_equity_curve = starting_equity + recent_pnl_curve
        recent_peak_equity = recent_equity_curve.cummax()
        rolling_20_drawdown = (
            recent_equity_curve
            / recent_peak_equity
            - 1
        )
        rolling_20_max_drawdown = (
            rolling_20_drawdown.min()
            if not rolling_20_drawdown.empty
            else None
        )
        watch_flags = 0

        for value in [
            cagr,
            profit_factor - 1 if profit_factor is not None else None,
            expectancy,
            last_10_trade_pnl,
        ]:
            if value is not None and value < 0:
                watch_flags += 1

        if max_drawdown is not None and max_drawdown <= -0.30:
            watch_flags += 1

        if watch_flags >= 3:
            strategy_status = "Pause"
        elif watch_flags >= 1:
            strategy_status = "Watch"
        else:
            strategy_status = "Healthy"

        rows.append({
            "Strategy_Name": strategy_name,
            "strategy_status": strategy_status,
            "trade_count": len(group),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": safe_ratio(len(wins), len(group)),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "starting_equity": starting_equity,
            "ending_equity": ending_equity,
            "total_return": total_return,
            "cagr": cagr,
            "sharpe_ratio": sharpe_ratio,
            "margin_sharpe_ratio": margin_sharpe_ratio,
            "max_drawdown": max_drawdown,
            "max_drawdown_dollars": max_drawdown_dollars,
            "mar_ratio": mar_ratio,
            "expectancy": expectancy,
            "last_10_trade_pnl": last_10_trade_pnl,
            "last_20_profit_factor": last_20_profit_factor,
            "rolling_20_max_drawdown": rolling_20_max_drawdown,
            "rolling_20_win_rate": rolling_20_win_rate,
            "rolling_20_average_return_on_margin": (
                rolling_20_average_return_on_margin
            ),
            "average_trade_pnl": net_pnl.mean(),
            "median_trade_pnl": net_pnl.median(),
            "average_win": average_win,
            "average_loss": average_loss,
            "largest_win": net_pnl.max(),
            "largest_loss": net_pnl.min(),
            "zero_or_missing_margin_count": int(
                (margin_requirement.isna() | (margin_requirement <= 0)).sum()
            ),
            "extreme_margin_loss_count": int(
                (return_on_margin <= -1).sum()
            ),
            "average_return_on_margin": group["return_on_margin"].mean(),
            "cumulative_log_return_on_margin": (
                curve_group["cm_returns"].iloc[-1]
            ),
        })

    return pd.DataFrame(rows)


def calculate_correlation_outputs(equity_curves):
    working = equity_curves[
        equity_curves["timestamp"].notna()
    ].copy()

    working["date"] = working["timestamp"].dt.date

    daily_pnl = working.pivot_table(
        index="date",
        columns="Strategy_Name",
        values="strategy_pnl",
        aggfunc="sum",
        fill_value=0.0,
    )
    pnl_correlation = daily_pnl.corr()

    daily_drawdown = working.pivot_table(
        index="date",
        columns="Strategy_Name",
        values="strategy_drawdown",
        aggfunc="last",
    ).ffill()
    drawdown_correlation = daily_drawdown.corr()

    overlap_rows = []
    strategies = list(daily_drawdown.columns)

    for left_index, left_strategy in enumerate(strategies):
        for right_strategy in strategies[left_index + 1:]:
            left_drawdown = daily_drawdown[left_strategy] < 0
            right_drawdown = daily_drawdown[right_strategy] < 0
            either_drawdown = left_drawdown | right_drawdown
            both_drawdown = left_drawdown & right_drawdown

            overlap_rows.append({
                "strategy_a": left_strategy,
                "strategy_b": right_strategy,
                "both_drawdown_days": int(both_drawdown.sum()),
                "either_drawdown_days": int(either_drawdown.sum()),
                "drawdown_overlap_ratio": safe_ratio(
                    both_drawdown.sum(),
                    either_drawdown.sum(),
                ),
                "daily_pnl_correlation": pnl_correlation.loc[
                    left_strategy,
                    right_strategy,
                ],
                "drawdown_correlation": drawdown_correlation.loc[
                    left_strategy,
                    right_strategy,
                ],
            })

    return (
        daily_pnl,
        pnl_correlation,
        drawdown_correlation,
        pd.DataFrame(overlap_rows),
    )


def save_strategy_equity_chart(equity_curves):
    fig, ax = plt.subplots(
        figsize=(12, 7)
    )
    starting_equities = []

    for strategy_name, group in equity_curves.groupby("Strategy_Name", sort=True):
        group = group[
            group["timestamp"].notna()
        ]

        if group.empty:
            continue

        starting_equity = (
            group["strategy_equity"].iloc[0]
            - group["CMPNL"].iloc[0]
        )
        starting_equities.append(starting_equity)

        ax.plot(
            group["timestamp"],
            group["strategy_equity"],
            linewidth=1.6,
            label=strategy_name,
        )

    if starting_equities:
        baseline = min(starting_equities)
        ax.axhline(
            baseline,
            color="#444444",
            linewidth=0.9,
            linestyle="--",
            label="Starting equity",
        )
        y_min = min(
            baseline,
            equity_curves["strategy_equity"].min(),
        )
        y_max = equity_curves["strategy_equity"].max()
        y_range = y_max - y_min
        padding = max(
            y_range * 0.08,
            1,
        )
        ax.set_ylim(
            y_min - padding,
            y_max + padding,
        )

    ax.set_title("Strategy Equity Curves")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.grid(
        True,
        axis="y",
        alpha=0.25,
    )
    ax.legend(
        fontsize=8,
        ncol=2,
    )
    fig.autofmt_xdate()
    fig.tight_layout()
    output_file = f"{CHART_DIR}/strategy_equity_curves.png"
    fig.savefig(
        output_file,
        dpi=150,
    )
    plt.close(fig)

    return output_file


def save_account_equity_chart(account_curve):
    plot_df = account_curve[
        account_curve["timestamp"].notna()
    ]

    fig, ax = plt.subplots(
        figsize=(12, 6)
    )
    ax.plot(
        plot_df["timestamp"],
        plot_df["ending_equity"],
        color="#1565c0",
        linewidth=2,
        label="Account equity",
    )
    ax.set_title("Account Equity Curve")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.grid(
        True,
        axis="y",
        alpha=0.25,
    )

    ax2 = ax.twinx()
    ax2.fill_between(
        plot_df["timestamp"],
        plot_df["account_drawdown"],
        0,
        color="#c62828",
        alpha=0.18,
        label="Drawdown",
    )
    ax2.set_ylabel("Drawdown")

    handles, labels = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(
        handles + handles2,
        labels + labels2,
    )

    fig.autofmt_xdate()
    fig.tight_layout()
    output_file = f"{CHART_DIR}/account_equity_curve.png"
    fig.savefig(
        output_file,
        dpi=150,
    )
    plt.close(fig)

    return output_file


def save_recent_trade_pnl_chart(realized_trades, lookback_days=14):
    if realized_trades is None or realized_trades.empty:
        return None

    plot_df = realized_trades.copy()
    plot_df["timestamp"] = pd.to_datetime(
        plot_df["timestamp"],
        errors="coerce",
    )
    plot_df["net_pnl"] = pd.to_numeric(
        plot_df["net_pnl"],
        errors="coerce",
    )
    plot_df = plot_df.dropna(
        subset=[
            "timestamp",
            "net_pnl",
            "Strategy_Name",
        ]
    )

    if plot_df.empty:
        return None

    end_time = plot_df["timestamp"].max()
    start_time = end_time - pd.Timedelta(days=lookback_days)
    plot_df = plot_df[
        plot_df["timestamp"] >= start_time
    ].copy()

    if plot_df.empty:
        return None

    strategies = sorted(plot_df["Strategy_Name"].astype(str).unique())
    fig, axes = plt.subplots(
        len(strategies),
        1,
        figsize=(12, max(4, 2.8 * len(strategies))),
        sharex=True,
        squeeze=False,
    )

    for axis, strategy_name in zip(axes.flatten(), strategies):
        group = plot_df[
            plot_df["Strategy_Name"].astype(str) == strategy_name
        ].sort_values("timestamp")
        colors = group["net_pnl"].apply(
            lambda value: "#167647" if value >= 0 else "#b42336"
        )
        axis.scatter(
            group["timestamp"],
            group["net_pnl"],
            c=colors,
            s=44,
            alpha=0.85,
            edgecolors="#17202a",
            linewidths=0.35,
        )
        axis.axhline(
            0,
            color="#475569",
            linewidth=0.8,
        )
        axis.set_ylabel("PnL ($)")
        axis.set_title(
            strategy_name,
            loc="left",
            fontsize=10,
            fontweight="bold",
        )
        axis.grid(
            True,
            axis="y",
            alpha=0.25,
        )

    axes.flatten()[-1].set_xlabel("Trade date")
    fig.suptitle(
        f"Trade PnL By Strategy - Last {lookback_days} Days",
        fontsize=14,
        fontweight="bold",
    )
    fig.autofmt_xdate()
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    output_file = f"{CHART_DIR}/recent_trade_pnl_by_strategy.png"
    fig.savefig(
        output_file,
        dpi=150,
    )
    plt.close(fig)

    return output_file


def save_summary_bar_charts(summary):
    chart_files = []
    metrics = [
        "total_pnl",
        "profit_factor",
        "expectancy",
        "last_10_trade_pnl",
        "last_20_profit_factor",
        "rolling_20_max_drawdown",
        "rolling_20_win_rate",
        "sharpe_ratio",
        "margin_sharpe_ratio",
        "cagr",
        "max_drawdown",
        "mar_ratio",
        "win_rate",
        "average_return_on_margin",
    ]

    for metric in metrics:
        if metric not in summary.columns:
            continue

        chart_df = summary[
            ["Strategy_Name", metric]
        ].dropna()

        if chart_df.empty:
            continue

        chart_df = chart_df.sort_values(metric)

        fig, ax = plt.subplots(
            figsize=(11, max(5, 0.45 * len(chart_df)))
        )
        colors = chart_df[metric].apply(
            lambda value: "#2e7d32" if value >= 0 else "#c62828"
        )

        ax.barh(
            chart_df["Strategy_Name"],
            chart_df[metric],
            color=colors,
            alpha=0.85,
        )
        ax.axvline(
            0,
            color="#444444",
            linewidth=0.8,
        )
        ax.set_title(
            metric.replace("_", " ").title()
        )
        ax.grid(
            True,
            axis="x",
            alpha=0.2,
        )
        fig.tight_layout()

        output_file = f"{CHART_DIR}/{metric}.png"
        fig.savefig(
            output_file,
            dpi=150,
        )
        plt.close(fig)
        chart_files.append(output_file)

    return chart_files


def save_correlation_heatmap(correlation, title, filename):
    if correlation.empty:
        return None

    plot_df = correlation.astype(float)
    strategy_count = len(plot_df.columns)
    fig_size = max(6, 1.35 * strategy_count)
    fig, ax = plt.subplots(
        figsize=(fig_size, fig_size),
    )

    cmap = plt.get_cmap("RdYlGn").copy()
    cmap.set_bad("#e5e7eb")

    image = ax.imshow(
        plot_df.to_numpy(),
        cmap=cmap,
        vmin=-1,
        vmax=1,
    )

    ax.set_title(title)
    ax.set_xticks(range(len(plot_df.columns)))
    ax.set_xticklabels(
        plot_df.columns,
        rotation=45,
        ha="right",
        fontsize=8,
    )
    ax.set_yticks(range(len(plot_df.index)))
    ax.set_yticklabels(
        plot_df.index,
        fontsize=8,
    )

    for row_index, row_label in enumerate(plot_df.index):
        for column_index, column_label in enumerate(plot_df.columns):
            value = plot_df.loc[
                row_label,
                column_label,
            ]

            if pd.isna(value):
                label = "NA"
                color = "#4b5563"
            else:
                label = f"{value:.2f}"
                color = "white" if abs(value) >= 0.65 else "#111827"

            ax.text(
                column_index,
                row_index,
                label,
                ha="center",
                va="center",
                color=color,
                fontsize=9,
                fontweight="bold",
            )

    fig.colorbar(
        image,
        ax=ax,
        fraction=0.046,
        pad=0.04,
        label="Correlation",
    )
    fig.tight_layout()

    output_file = f"{CHART_DIR}/{filename}"
    fig.savefig(
        output_file,
        dpi=150,
    )
    plt.close(fig)

    return output_file


def build_strategy_dashboard_highlights(summary, risk_summary):
    columns = [
        "Strategy_Name",
        "strategy_status",
        "total_pnl",
        "profit_factor",
        "cagr",
        "expectancy",
        "last_10_trade_pnl",
        "last_20_profit_factor",
    ]
    highlights = summary[
        [column for column in columns if column in summary.columns]
    ].copy()
    highlights = highlights.rename(columns={
        "total_pnl": "strategy_pnl",
        "profit_factor": "strategy_profit_factor",
        "cagr": "strategy_cagr",
    })

    if not risk_summary.empty:
        risk_columns = [
            "Strategy_Name",
            "risk_per_trade_dollars",
            "sizing_basis",
            "estimated_max_risk_per_contract_or_share",
            "estimated_margin_per_contract_or_share",
            "contracts_or_shares_to_trade",
            "safe_f",
            "CAR25",
        ]
        highlights = highlights.merge(
            risk_summary[
                [
                    column
                    for column in risk_columns
                    if column in risk_summary.columns
                ]
            ],
            on="Strategy_Name",
            how="left",
        )

    ordered_columns = [
        "Strategy_Name",
        "strategy_status",
        "risk_per_trade_dollars",
        "sizing_basis",
        "estimated_max_risk_per_contract_or_share",
        "estimated_margin_per_contract_or_share",
        "contracts_or_shares_to_trade",
        "safe_f",
        "strategy_pnl",
        "strategy_profit_factor",
        "strategy_cagr",
        "expectancy",
        "last_10_trade_pnl",
        "last_20_profit_factor",
        "CAR25",
    ]

    return highlights[
        [
            column
            for column in ordered_columns
            if column in highlights.columns
        ]
    ].sort_values(
        "Strategy_Name",
    )


def calculate_strategy_position_sizing(risk_summary, realized_trades):
    if risk_summary.empty:
        return risk_summary

    sized = risk_summary.copy()
    sized["sizing_basis"] = None
    sized["estimated_max_risk_per_contract_or_share"] = None
    sized["estimated_margin_per_contract_or_share"] = None
    sized["contracts_or_shares_to_trade"] = None

    for index, row in sized.iterrows():
        strategy_name = row.get("Strategy_Name")

        if str(strategy_name).strip().lower() == "discretionary":
            sized.at[
                index,
                "contracts_or_shares_to_trade",
            ] = "N/A"
            sized.at[
                index,
                "sizing_basis",
            ] = "N/A"
            continue

        group = realized_trades[
            realized_trades["Strategy_Name"] == strategy_name
        ].copy()

        if group.empty:
            sized.at[
                index,
                "contracts_or_shares_to_trade",
            ] = "N/A"
            sized.at[
                index,
                "sizing_basis",
            ] = "N/A"
            continue

        qty = pd.to_numeric(
            group["Qty"],
            errors="coerce",
        ).abs()
        margin = pd.to_numeric(
            group["margin_requirement"],
            errors="coerce",
        ).abs()
        unit_margin = (
            margin / qty
        ).replace(
            [float("inf"), -float("inf")],
            pd.NA,
        ).dropna()
        unit_margin = unit_margin[
            unit_margin > 0
        ]

        if unit_margin.empty:
            sized.at[
                index,
                "contracts_or_shares_to_trade",
            ] = "N/A"
            sized.at[
                index,
                "sizing_basis",
            ] = "N/A"
            continue

        estimated_unit_margin = float(unit_margin.median())
        risk_dollars = parse_number(row.get("risk_per_trade_dollars")) or 0.0
        spread_values = {
            str(value).upper()
            for value in group.get("Spread", pd.Series(dtype=object)).dropna()
        }
        strategy_upper = str(strategy_name).upper()
        is_defined_risk_spread = (
            "VERTICAL" in spread_values
            or "SPREAD" in strategy_upper
        )
        sizing_basis = (
            "defined_max_loss"
            if is_defined_risk_spread
            else "margin_requirement"
        )
        estimated_max_risk = estimated_unit_margin
        quantity = int(risk_dollars // estimated_max_risk)

        sized.at[
            index,
            "sizing_basis",
        ] = sizing_basis
        sized.at[
            index,
            "estimated_max_risk_per_contract_or_share",
        ] = estimated_max_risk
        sized.at[
            index,
            "estimated_margin_per_contract_or_share",
        ] = estimated_unit_margin
        sized.at[
            index,
            "contracts_or_shares_to_trade",
        ] = quantity

    return sized


def save_risk_simulation_boxplots(risk_simulations):
    if risk_simulations.empty:
        return []

    chart_files = []
    metrics = [
        ("cagr", "Safe-F Simulation CAGR"),
        ("max_drawdown", "Safe-F Simulation Max Drawdown"),
        ("profit_factor", "Safe-F Simulation Profit Factor"),
    ]

    for metric, title in metrics:
        if metric not in risk_simulations.columns:
            continue

        plot_df = risk_simulations[
            [
                "Strategy_Name",
                metric,
            ]
        ].dropna()

        if plot_df.empty:
            continue

        strategy_names = sorted(plot_df["Strategy_Name"].unique())
        values = [
            plot_df.loc[
                plot_df["Strategy_Name"] == strategy_name,
                metric,
            ].to_numpy()
            for strategy_name in strategy_names
        ]

        fig, ax = plt.subplots(
            figsize=(12, max(5, 0.7 * len(strategy_names)))
        )
        ax.boxplot(
            values,
            vert=False,
            labels=strategy_names,
            patch_artist=True,
            boxprops={
                "facecolor": "#90caf9",
                "color": "#1565c0",
            },
            medianprops={
                "color": "#0d47a1",
                "linewidth": 1.6,
            },
            whiskerprops={
                "color": "#1565c0",
            },
            capprops={
                "color": "#1565c0",
            },
        )
        ax.axvline(
            0,
            color="#444444",
            linewidth=0.8,
        )
        ax.set_title(title)
        ax.grid(
            True,
            axis="x",
            alpha=0.2,
        )
        fig.tight_layout()

        output_file = f"{CHART_DIR}/risk_simulation_{metric}_boxplot.png"
        fig.savefig(
            output_file,
            dpi=150,
        )
        plt.close(fig)
        chart_files.append(output_file)

    return chart_files


def risk_summary_for_merge(risk_summary):
    if risk_summary is None or risk_summary.empty:
        return pd.DataFrame(columns=["Strategy_Name"])

    columns = [
        column
        for column in [
            "Strategy_Name",
            "safe_f",
            "risk_per_trade_dollars",
            "CAR25",
            "profit_factor_Q25",
            "sizing_basis",
            "estimated_max_risk_per_contract_or_share",
            "estimated_margin_per_contract_or_share",
            "contracts_or_shares_to_trade",
        ]
        if column in risk_summary.columns
    ]
    return risk_summary[columns].copy()


def action_for_strategy(row):
    if str(row.get("data_confidence", "")).lower() == "low":
        return "Needs Data"

    status = str(row.get("strategy_status", "")).lower()
    if status == "pause":
        return "Pause"

    if status == "watch":
        return "Watch"

    profit_factor = parse_number(row.get("profit_factor"))
    total_return = parse_number(row.get("total_return"))
    max_drawdown = parse_number(row.get("max_drawdown"))

    if (
        profit_factor is not None
        and profit_factor >= 1.10
        and total_return is not None
        and total_return > 0
        and max_drawdown is not None
        and max_drawdown > -0.20
    ):
        return "Allocate"

    return "Watch"


def action_reason_for_strategy(row):
    if str(row.get("data_confidence", "")).lower() == "low":
        return row.get("data_confidence_reason", "Data needs review")

    status = str(row.get("strategy_status", ""))
    if status == "Pause":
        return "Strategy status is Pause from recent performance/risk flags."

    profit_factor = parse_number(row.get("profit_factor"))
    total_return = parse_number(row.get("total_return"))
    max_drawdown = parse_number(row.get("max_drawdown"))

    reasons = []
    if total_return is not None:
        reasons.append(f"total return {total_return * 100:.1f}%")
    if profit_factor is not None:
        reasons.append(f"profit factor {profit_factor:.2f}")
    if max_drawdown is not None:
        reasons.append(f"max DD {max_drawdown * 100:.1f}%")

    return "; ".join(reasons) if reasons else "Review strategy metrics."


def build_strategy_decision_board(
    summary,
    risk_summary,
    pnl_correlation,
):
    if summary is None or summary.empty:
        return pd.DataFrame(columns=[])

    decision = pd.merge(
        summary.copy(),
        risk_summary_for_merge(risk_summary),
        on="Strategy_Name",
        how="left",
    )
    decision["correlation_notice"] = decision["Strategy_Name"].apply(
        lambda strategy_name: correlation_alert_for_strategy(
            strategy_name,
            pnl_correlation,
        )
    )
    decision["suggested_action"] = decision.apply(
        action_for_strategy,
        axis=1,
    )
    decision["decision_reason"] = decision.apply(
        action_reason_for_strategy,
        axis=1,
    )

    action_order = {
        "Allocate": 0,
        "Watch": 1,
        "Needs Data": 2,
        "Pause": 3,
    }
    decision["_action_order"] = decision["suggested_action"].map(
        action_order
    ).fillna(99)

    columns = [
        "Strategy_Name",
        "suggested_action",
        "strategy_status",
        "data_confidence",
        "data_confidence_reason",
        "total_pnl",
        "total_return",
        "cagr",
        "max_drawdown",
        "profit_factor",
        "safe_f",
        "risk_per_trade_dollars",
        "contracts_or_shares_to_trade",
        "settlement_coverage_ratio",
        "settlement_missing_count",
        "needs_review_open_position_count",
        "zero_or_missing_margin_count",
        "extreme_margin_loss_count",
        "correlation_notice",
        "decision_reason",
    ]

    return decision.sort_values(
        [
            "_action_order",
            "total_pnl",
        ],
        ascending=[
            True,
            False,
        ],
    )[[column for column in columns if column in decision.columns]].reset_index(
        drop=True,
    )


def build_capital_allocation_table(strategy_decision):
    if strategy_decision is None or strategy_decision.empty:
        return pd.DataFrame(columns=[])

    allocation = strategy_decision.copy()

    def allocation_note(row):
        action = str(row.get("suggested_action", ""))
        if action == "Allocate":
            contracts = row.get("contracts_or_shares_to_trade")
            return f"Candidate size: {contracts} contracts/shares at Safe-F risk."
        if action == "Needs Data":
            return "Resolve data-confidence blockers before allocating more capital."
        if action == "Pause":
            return "Do not add capital until status improves."
        return "Keep small or unchanged until metrics/data improve."

    allocation["allocation_note"] = allocation.apply(
        allocation_note,
        axis=1,
    )
    columns = [
        "Strategy_Name",
        "suggested_action",
        "data_confidence",
        "safe_f",
        "risk_per_trade_dollars",
        "contracts_or_shares_to_trade",
        "total_return",
        "max_drawdown",
        "profit_factor",
        "correlation_notice",
        "allocation_note",
    ]

    return allocation[
        [column for column in columns if column in allocation.columns]
    ].copy()


def build_compact_capital_allocation_table(capital_allocation):
    if capital_allocation is None or capital_allocation.empty:
        return pd.DataFrame(columns=[])

    compact = capital_allocation.copy()
    columns = [
        "Strategy_Name",
        "contracts_or_shares_to_trade",
        "risk_per_trade_dollars",
    ]

    return compact[
        [column for column in columns if column in compact.columns]
    ].copy()


def metric_lookup(df):
    if df is None or df.empty or not {"metric", "value"}.issubset(df.columns):
        return {}

    return {
        row["metric"]: row["value"]
        for _, row in df.iterrows()
    }


def format_dashboard_metric(metric, value):
    if pd.isna(value):
        return "n/a"

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    if metric in {
        "total_return",
        "cagr",
        "cash_balance_total_return",
        "cash_balance_cagr",
        "cash_balance_max_drawdown",
        "cash_balance_ex_futures_sweeps_total_return",
        "cash_balance_ex_futures_sweeps_cagr",
        "cash_balance_ex_futures_sweeps_max_drawdown",
        "benchmark_total_return",
        "benchmark_cagr",
        "benchmark_max_drawdown",
        "max_drawdown",
        "safe_f",
        "settlement_coverage_ratio",
        "win_rate",
        "rolling_20_max_drawdown",
        "rolling_20_win_rate",
        "average_return_on_margin",
    }:
        return f"{numeric_value * 100:,.2f}%"

    if metric in {
        "total_pnl",
        "cash_balance_start",
        "cash_balance_end",
        "cash_balance_total_change",
        "cash_balance_max_drawdown_dollars",
        "cash_balance_ex_futures_sweeps_start",
        "cash_balance_ex_futures_sweeps_end",
        "cash_balance_ex_futures_sweeps_total_change",
        "cash_balance_ex_futures_sweeps_max_drawdown_dollars",
        "benchmark_total_pnl",
        "benchmark_ending_equity",
        "account_vs_benchmark_ending_equity",
        "gross_profit",
        "gross_loss",
        "max_drawdown_dollars",
        "average_trade_pnl",
        "average_win",
        "average_loss",
        "largest_win",
        "largest_loss",
        "risk_per_trade_dollars",
        "estimated_max_risk_per_contract_or_share",
        "estimated_margin_per_contract_or_share",
        "statement_gross_ytd_pnl",
        "statement_open_position_pnl",
        "statement_closed_gross_ytd_pnl",
        "statement_total_ytd_commissions_and_fees",
        "statement_closed_net_ytd_pnl",
        "realized_trade_closed_gross_pnl",
        "realized_trade_closed_net_pnl",
        "trade_history_closed_gross_ytd_pnl",
        "trade_history_closed_net_ytd_pnl",
        "trade_history_adjusted_closed_gross_ytd_pnl",
        "trade_history_adjusted_closed_net_ytd_pnl",
        "open_trade_exclusion",
        "ytd_bridge_adjustment",
        "total_closed_pnl_adjustment",
        "script_realized_closed_gross_pnl",
        "script_realized_closed_fees",
        "script_realized_closed_net_pnl",
        "closed_net_delta_statement_minus_script",
        "closed_net_delta_statement_minus_trade_history",
        "closed_gross_delta_statement_minus_trade_history",
        "delta_statement_minus_trade_history",
        "trade_history_value",
        "cash_ledger_statement_value",
        "ytd_statement_value",
        "statement_open_pnl",
        "script_open_pnl",
    }:
        return f"${numeric_value:,.2f}"

    if metric in {
        "profit_factor",
        "mar_ratio",
        "benchmark_mar_ratio",
        "sharpe_ratio",
        "margin_sharpe_ratio",
        "expectancy",
    }:
        return f"{numeric_value:,.2f}"

    if metric in {
        "trade_count",
        "winning_trades",
        "losing_trades",
        "settlement_missing_count",
        "needs_review_open_position_count",
        "zero_or_missing_margin_count",
        "extreme_margin_loss_count",
    }:
        return f"{numeric_value:,.0f}"

    return f"{numeric_value:,.4f}"


def status_badge_class(status):
    normalized = str(status).strip().lower()

    if normalized == "healthy":
        return "status-healthy"

    if normalized == "watch":
        return "status-watch"

    if normalized == "pause":
        return "status-pause"

    return "status-neutral"


def action_badge_class(action):
    normalized = str(action).strip().lower()

    if normalized == "allocate":
        return "status-healthy"

    if normalized == "watch":
        return "status-watch"

    if normalized in {"pause", "needs data"}:
        return "status-pause"

    return "status-neutral"


def confidence_badge_class(confidence):
    normalized = str(confidence).strip().lower()

    if normalized == "high":
        return "status-healthy"

    if normalized == "medium":
        return "status-watch"

    if normalized == "low":
        return "status-pause"

    return "status-neutral"


def benchmark_caption(benchmark, metric):
    if not benchmark:
        return None

    benchmark_metric = {
        "total_pnl": "benchmark_total_pnl",
        "cagr": "benchmark_cagr",
        "max_drawdown": "benchmark_max_drawdown",
        "mar_ratio": "benchmark_mar_ratio",
    }.get(metric)

    if benchmark_metric is None:
        return None

    value = benchmark.get(benchmark_metric)
    if value is None or pd.isna(value):
        return None

    symbol = benchmark.get("benchmark_symbol", "Benchmark")
    return (
        f"{symbol} buy-and-hold: "
        f"{format_dashboard_metric(benchmark_metric, value)}"
    )


def render_account_kpi_grid(cards, heading=None):
    rendered_cards = []

    for label, metric, value, caption in cards:
        value_class = summary_value_class(metric, value)
        rendered_cards.append(
            '<article class="kpi-card">'
            f'<div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value {value_class}">{format_dashboard_metric(metric, value)}</div>'
            f'<div class="kpi-caption">{html.escape(caption)}</div>'
            "</article>"
        )

    heading_html = f"<h3>{html.escape(heading)}</h3>" if heading else ""

    return (
        '<div class="kpi-section">'
        f"{heading_html}"
        '<div class="kpi-grid">'
        f"{''.join(rendered_cards)}"
        "</div>"
        "</div>"
    )


def build_realized_performance_cards(account, benchmark):
    cards = []

    for label, metric, caption in [
        (
            "Realized Trade PNL",
            "total_pnl",
            "Closed-trade PnL used for strategy attribution.",
        ),
        (
            "Annualized Real Return",
            "cagr",
            "Annualized return from the realized-trade equity curve.",
        ),
        (
            "Realized Max DD",
            "max_drawdown",
            "Closed-trade equity curve drawdown used for performance decisions.",
        ),
        (
            "Realized MAR",
            "mar_ratio",
            "Annualized realized return divided by absolute realized max drawdown.",
        ),
        (
            "Realized Profit Factor",
            "profit_factor",
            "Gross realized profit divided by gross realized loss.",
        ),
    ]:
        comparison = benchmark_caption(
            benchmark,
            metric,
        )
        cards.append(
            (
                label,
                metric,
                account.get(metric),
                comparison if comparison is not None else caption,
            )
        )

    return cards


def build_account_kpi_cards(
    account_summary,
    benchmark_summary=None,
    ytd_reports=None,
):
    account = metric_lookup(account_summary)
    benchmark = metric_lookup(benchmark_summary)
    sections = []
    ytd_summary = (
        ytd_reports.get("summary", pd.DataFrame())
        if ytd_reports
        else pd.DataFrame()
    )

    if ytd_summary is not None and not ytd_summary.empty:
        ytd = ytd_summary.iloc[0]
        trade_history_net_metric = (
            "trade_history_adjusted_closed_net_ytd_pnl"
            if "trade_history_adjusted_closed_net_ytd_pnl" in ytd
            else "script_realized_closed_net_pnl"
        )
        trade_history_net_value = ytd.get(
            "trade_history_adjusted_closed_net_ytd_pnl",
            ytd.get("script_realized_closed_net_pnl"),
        )
        discrepancy_metric = (
            "closed_net_delta_statement_minus_trade_history"
            if "closed_net_delta_statement_minus_trade_history" in ytd
            else "closed_net_delta_statement_minus_script"
        )
        discrepancy_value = ytd.get(
            "closed_net_delta_statement_minus_trade_history",
            ytd.get("closed_net_delta_statement_minus_script"),
        )
        sections.append(
            render_account_kpi_grid(
                [
                    (
                        "Net YTD PNL",
                        "statement_closed_net_ytd_pnl",
                        ytd.get("statement_closed_net_ytd_pnl"),
                        "Closed net YTD PnL from the account statement.",
                    ),
                    (
                        "Trade History Net PNL",
                        trade_history_net_metric,
                        trade_history_net_value,
                        (
                            "Closed net YTD PnL from imported trade history "
                            "after open-position and YTD bridge adjustments."
                        ),
                    ),
                    (
                        "Discrepancy",
                        discrepancy_metric,
                        discrepancy_value,
                        "Statement minus trade history. This should be near zero.",
                    ),
                ],
                heading="YTD Reconciliation",
            )
        )

    sections.append(
        render_account_kpi_grid(
            build_realized_performance_cards(account, benchmark),
            heading="Realized Performance",
        )
    )

    return "".join(sections)


def correlation_alert_for_strategy(strategy_name, pnl_correlation):
    if pnl_correlation.empty or strategy_name not in pnl_correlation.index:
        return ""

    correlations = pnl_correlation.loc[strategy_name].drop(
        labels=[strategy_name],
        errors="ignore",
    ).dropna()

    if correlations.empty:
        return ""

    high_correlations = correlations[
        correlations.abs() >= HIGH_CORRELATION_THRESHOLD
    ].sort_values(
        key=lambda series: series.abs(),
        ascending=False,
    )

    if high_correlations.empty:
        return ""

    return "; ".join(
        f"{other}: {value:.2f}"
        for other, value in high_correlations.items()
    )


def build_strategy_top_summary(summary, pnl_correlation):
    if summary.empty:
        return '<div class="empty-state">No strategy summary rows were created.</div>'

    columns = [
        ("Strategy", "Strategy_Name"),
        ("Status", "strategy_status"),
        ("PNL", "total_pnl"),
        ("Total Return", "total_return"),
        ("Ann. Return", "cagr"),
        ("Max DD", "max_drawdown"),
        ("MAR", "mar_ratio"),
        ("Profit Factor", "profit_factor"),
        ("Trades", "trade_count"),
        ("Correlation Notice", "correlation_notice"),
    ]
    display = summary.copy()
    display["correlation_notice"] = display["Strategy_Name"].apply(
        lambda strategy_name: correlation_alert_for_strategy(
            strategy_name,
            pnl_correlation,
        )
    )
    display = display.sort_values(
        "Strategy_Name",
        key=lambda series: series.fillna("").astype(str).str.lower(),
    )

    headers = "".join(
        f"<th>{html.escape(label)}</th>"
        for label, _ in columns
    )
    rows = []

    for _, row in display.iterrows():
        cells = []

        for _, column in columns:
            value = row.get(column)

            if column == "strategy_status":
                status = html.escape(str(value))
                cells.append(
                    "<td>"
                    f'<span class="status-badge {status_badge_class(value)}">{status}</span>'
                    "</td>"
                )
                continue

            if column == "correlation_notice":
                if value:
                    cells.append(
                        '<td class="correlation-alert">'
                        f"High daily PNL correlation with {html.escape(str(value))}"
                        "</td>"
                    )
                else:
                    cells.append('<td class="muted-text">No high correlation</td>')
                continue

            if column == "Strategy_Name":
                cells.append(
                    f'<td class="strategy-name">{strategy_trade_link(value)}</td>'
                )
                continue

            class_name = summary_value_class(column, value)
            class_attr = f' class="{class_name}"' if class_name else ""
            cells.append(
                f"<td{class_attr}>{format_dashboard_metric(column, value)}</td>"
            )

        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<table class="data-table strategy-summary-table">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def dashboard_json_payload(payload):
    return json.dumps(
        payload,
        allow_nan=False,
    ).replace(
        "</",
        "<\\/",
    )


def dashboard_json_value(value):
    if value is None or pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, float):
        return float(value)

    if isinstance(value, int):
        return int(value)

    return str(value)


def apply_strategy_name_updates_to_master(input_file, updates):
    if not isinstance(updates, list):
        raise ValueError("Strategy updates must be a list")

    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Could not find {input_path}")

    master = pd.read_csv(input_path)
    if "Strategy_Name" not in master.columns:
        master["Strategy_Name"] = ""

    changed_rows = set()
    skipped_updates = 0

    for update in updates:
        if not isinstance(update, dict):
            skipped_updates += 1
            continue

        strategy_name = clean_strategy_name(update.get("strategy_name", ""))
        row_ids = source_row_ids_from_value(
            update.get(
                "source_row_ids",
                update.get("sourceRowIds"),
            )
        )

        if not strategy_name or not row_ids:
            skipped_updates += 1
            continue

        valid_row_ids = [
            row_id
            for row_id in row_ids
            if 0 <= row_id < len(master)
        ]

        if not valid_row_ids:
            skipped_updates += 1
            continue

        for row_id in valid_row_ids:
            current_strategy = clean_strategy_name(
                master.at[row_id, "Strategy_Name"]
            )
            if current_strategy != strategy_name:
                master.at[row_id, "Strategy_Name"] = strategy_name
                changed_rows.add(row_id)

    if changed_rows:
        master.to_csv(input_path, index=False)

    return {
        "saved_rows": len(changed_rows),
        "skipped_updates": skipped_updates,
        "input_file": str(input_path),
    }


def dashboard_trade_date(value, date_only=False):
    if value is None or pd.isna(value):
        return ""

    timestamp = pd.to_datetime(
        value,
        errors="coerce",
    )

    if pd.isna(timestamp):
        return str(value)

    if date_only:
        return timestamp.strftime("%Y-%m-%d")

    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def strategy_trade_browser_payload(realized_trades, summary=None):
    strategy_names = []

    if summary is not None and not summary.empty and "Strategy_Name" in summary.columns:
        strategy_names.extend(
            summary["Strategy_Name"]
            .dropna()
            .astype(str)
            .tolist()
        )

    trades = []
    if realized_trades is not None and not realized_trades.empty:
        working = realized_trades.copy()
        sort_column = "timestamp" if "timestamp" in working.columns else "Exec Time"
        if sort_column in working.columns:
            working["_dashboard_sort"] = pd.to_datetime(
                working[sort_column],
                errors="coerce",
            )
            working = working.sort_values(
                "_dashboard_sort",
                ascending=False,
                na_position="last",
            )

        for _, row in working.iterrows():
            strategy_name = str(row.get("Strategy_Name", "") or "")
            if strategy_name:
                strategy_names.append(strategy_name)

            timestamp = row.get("timestamp", row.get("Exec Time"))
            trades.append({
                "strategy": strategy_name,
                "sourceRowIds": source_row_ids_from_row(row),
                "date": dashboard_trade_date(timestamp, date_only=True),
                "symbol": dashboard_json_value(row.get("Symbol")),
                "spread": dashboard_json_value(row.get("Spread")),
                "side": dashboard_json_value(row.get("Side")),
                "qty": dashboard_json_value(row.get("Qty")),
                "status": dashboard_json_value(row.get("realized_status")),
                "entry": dashboard_json_value(row.get("open_exec_time")),
                "exit": dashboard_json_value(row.get("close_exec_time")),
                "grossPnl": dashboard_json_value(row.get("trade_pnl")),
                "fees": dashboard_json_value(row.get("fees")),
                "netPnl": dashboard_json_value(row.get("net_pnl")),
            })

    strategy_options = sorted(
        {
            strategy
            for strategy in strategy_names
            if str(strategy).strip()
        },
        key=lambda value: value.lower(),
    )

    return {
        "strategies": strategy_options,
        "trades": trades,
    }


def strategy_trade_browser_html(realized_trades, summary=None, server_enabled=False):
    payload = strategy_trade_browser_payload(
        realized_trades,
        summary,
    )
    payload["serverEnabled"] = bool(server_enabled)
    payload_json = dashboard_json_payload(payload)

    return f"""
  <section>
    <h2>Strategy Trades</h2>
    <div class="strategy-trade-browser">
      <label for="strategy-trade-select">Strategy</label>
      <select id="strategy-trade-select"></select>
      <button class="dashboard-button" type="button" id="save-strategy-edits">
        Save strategy changes
      </button>
      <button class="dashboard-button secondary" type="button" id="exit-dashboard" hidden>
        Exit dashboard
      </button>
      <span class="strategy-save-status" id="strategy-save-status"></span>
    </div>
    <div class="table-scroll">
      <table class="data-table strategy-trade-browser-table" id="strategy-trade-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Date</th>
            <th>Symbol</th>
            <th>Spread</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Status</th>
            <th>Entry Date</th>
            <th>Exit Date</th>
            <th>Gross PNL</th>
            <th>Fees</th>
            <th>Net PNL</th>
          </tr>
        </thead>
        <tbody id="strategy-trade-table-body"></tbody>
      </table>
    </div>
    <div class="empty-state strategy-trade-empty" id="strategy-trade-empty" hidden>
      No realized trades for this strategy.
    </div>
    <script type="application/json" id="strategy-trade-data">{payload_json}</script>
  </section>
"""


def strategy_trade_browser_script():
    return """
  <script>
    (() => {
      const payloadEl = document.getElementById("strategy-trade-data");
      const selectEl = document.getElementById("strategy-trade-select");
      const bodyEl = document.getElementById("strategy-trade-table-body");
      const emptyEl = document.getElementById("strategy-trade-empty");
      const saveEl = document.getElementById("save-strategy-edits");
      const exitEl = document.getElementById("exit-dashboard");
      const statusEl = document.getElementById("strategy-save-status");

      if (!payloadEl || !selectEl || !bodyEl || !emptyEl || !saveEl || !statusEl) {
        return;
      }

      const payload = JSON.parse(payloadEl.textContent || "{}");
      const serverEnabled = Boolean(payload.serverEnabled);
      const strategies = [...new Set(payload.strategies || [])].sort((a, b) => a.localeCompare(b));
      const trades = (payload.trades || []).map((trade) => ({
        ...trade,
        savedStrategy: trade.strategy || "",
        pendingStrategy: trade.strategy || "",
      }));
      const dirtyUpdates = new Map();
      const currencyFormatter = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      });

      const sourceKey = (trade) => (trade.sourceRowIds || []).join(",");

      const setStatus = (message, tone = "") => {
        statusEl.textContent = message;
        statusEl.className = "strategy-save-status";
        if (tone === "good") {
          statusEl.classList.add("good-value");
        } else if (tone === "bad") {
          statusEl.classList.add("bad-value");
        }
      };

      const refreshSaveState = () => {
        saveEl.disabled = !serverEnabled || dirtyUpdates.size === 0;
        if (!serverEnabled) {
          setStatus("Read-only");
        } else if (dirtyUpdates.size === 0) {
          setStatus("");
        } else {
          setStatus(`${dirtyUpdates.size} trade edit(s) ready to save.`);
        }
      };

      const textCell = (value) => {
        const td = document.createElement("td");
        td.textContent = value === null || value === undefined || value === "" ? "" : value;
        return td;
      };

      const moneyCell = (value) => {
        const td = document.createElement("td");
        const numeric = Number(value);
        if (Number.isFinite(numeric)) {
          td.textContent = currencyFormatter.format(numeric);
          if (numeric > 0) {
            td.className = "good-value";
          } else if (numeric < 0) {
            td.className = "bad-value";
          }
        } else {
          td.textContent = "";
        }
        return td;
      };

      const strategyOptions = (selectedStrategy) => strategies.map((strategy) => {
        const option = document.createElement("option");
        option.value = strategy;
        option.textContent = strategy;
        option.selected = strategy === selectedStrategy;
        return option;
      });

      const strategyCell = (trade) => {
        const td = document.createElement("td");
        const strategySelect = document.createElement("select");
        const key = sourceKey(trade);
        strategySelect.className = "strategy-edit-select";
        strategyOptions(trade.pendingStrategy).forEach((option) => {
          strategySelect.appendChild(option);
        });
        strategySelect.disabled = !serverEnabled || !key;
        strategySelect.addEventListener("change", () => {
          trade.pendingStrategy = strategySelect.value;
          if (trade.pendingStrategy && trade.pendingStrategy !== trade.savedStrategy) {
            dirtyUpdates.set(key, {
              source_row_ids: trade.sourceRowIds || [],
              strategy_name: trade.pendingStrategy,
            });
          } else {
            dirtyUpdates.delete(key);
          }
          refreshSaveState();
        });
        td.appendChild(strategySelect);
        return td;
      };

      const render = () => {
        const selected = selectEl.value;
        const rows = trades.filter((trade) => trade.strategy === selected);
        bodyEl.replaceChildren();
        emptyEl.hidden = rows.length > 0;

        rows.forEach((trade) => {
          const tr = document.createElement("tr");
          [
            strategyCell(trade),
            textCell(trade.date),
            textCell(trade.symbol),
            textCell(trade.spread),
            textCell(trade.side),
            textCell(trade.qty),
            textCell(trade.status),
            textCell(trade.entry),
            textCell(trade.exit),
            moneyCell(trade.grossPnl),
            moneyCell(trade.fees),
            moneyCell(trade.netPnl),
          ].forEach((cell) => tr.appendChild(cell));
          bodyEl.appendChild(tr);
        });
      };

      strategies.forEach((strategy) => {
        const option = document.createElement("option");
        option.value = strategy;
        option.textContent = strategy;
        selectEl.appendChild(option);
      });

      selectEl.disabled = strategies.length === 0;
      selectEl.addEventListener("change", render);
      saveEl.addEventListener("click", async () => {
        const updates = Array.from(dirtyUpdates.values());
        if (!serverEnabled) {
          setStatus("Start the dashboard with --serve-dashboard to save.", "bad");
          return;
        }
        if (updates.length === 0) {
          refreshSaveState();
          return;
        }

        saveEl.disabled = true;
        setStatus("Saving...");
        try {
          const response = await fetch("/api/strategy-updates", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({updates}),
          });
          const result = await response.json();
          if (!response.ok) {
            throw new Error(result.error || response.statusText);
          }

          updates.forEach((update) => {
            const key = (update.source_row_ids || []).join(",");
            trades.forEach((trade) => {
              if (sourceKey(trade) === key) {
                trade.savedStrategy = update.strategy_name;
                trade.pendingStrategy = update.strategy_name;
                trade.strategy = update.strategy_name;
              }
            });
          });
          dirtyUpdates.clear();
          setStatus(`Saved ${result.saved_rows} source row(s). Rerun analysis to refresh summaries.`, "good");
          render();
        } catch (error) {
          setStatus(`Save failed: ${error.message || error}`, "bad");
        } finally {
          saveEl.disabled = !serverEnabled || dirtyUpdates.size === 0;
        }
      });

      if (exitEl) {
        exitEl.hidden = !serverEnabled;
        exitEl.addEventListener("click", async () => {
          if (dirtyUpdates.size && !confirm("You have unsaved strategy changes. Close anyway?")) {
            return;
          }

          try {
            await fetch("/api/shutdown", {method: "POST"});
            setStatus("Dashboard server stopped.", "good");
            window.close();
          } catch (error) {
            setStatus(`Exit failed: ${error.message || error}`, "bad");
          }
        });
      }

      render();
      refreshSaveState();
    })();
  </script>
"""


GOOD_WHEN_POSITIVE = {
    "gross_profit",
    "total_pnl",
    "total_return",
    "cagr",
    "sharpe_ratio",
    "margin_sharpe_ratio",
    "mar_ratio",
    "average_trade_pnl",
    "median_trade_pnl",
    "average_win",
    "largest_win",
    "average_return_on_margin",
    "cumulative_log_return_on_margin",
    "risk_per_trade_dollars",
    "safe_f",
    "strategy_pnl",
    "strategy_cagr",
    "CAR25",
    "expectancy",
    "last_10_trade_pnl",
}

BAD_WHEN_POSITIVE = {
    "gross_loss",
    "losing_trades",
    "settlement_missing_count",
    "needs_review_open_position_count",
    "zero_or_missing_margin_count",
    "extreme_margin_loss_count",
}

GOOD_ABOVE_ONE = {
    "profit_factor",
    "strategy_profit_factor",
    "last_20_profit_factor",
    "profit_factor_Q25",
}

GOOD_ABOVE_HALF = {
    "win_rate",
    "settlement_coverage_ratio",
}

BAD_WHEN_NEGATIVE = {
    "average_loss",
    "largest_loss",
    "max_drawdown",
    "max_drawdown_dollars",
    "rolling_20_max_drawdown",
}


def format_html_value(value):
    if pd.isna(value):
        return ""

    if isinstance(value, float):
        return f"{value:,.4f}"

    return html.escape(str(value))


def strategy_trade_link(strategy_name):
    label = html.escape(str(strategy_name))
    href = html.escape(
        f"strategy_trades/{safe_filename(strategy_name)}_trades.csv"
    )

    return (
        f'<a class="strategy-trade-link" href="{href}" '
        f'target="_blank" rel="noopener">{label}</a>'
    )


def summary_value_class(metric, value):
    if pd.isna(value):
        return ""

    if metric == "strategy_status":
        status = str(value).strip().lower()

        if status == "healthy":
            return "good-value"

        if status in {"watch", "pause"}:
            return "bad-value"

        return ""

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return ""

    if metric in {
        "closed_net_delta_statement_minus_script",
        "delta_statement_minus_trade_history",
    }:
        if abs(numeric_value) <= 1:
            return "good-value"
        return "bad-value"

    if metric in GOOD_WHEN_POSITIVE:
        if numeric_value > 0:
            return "good-value"
        if numeric_value < 0:
            return "bad-value"

    if metric in BAD_WHEN_POSITIVE and numeric_value > 0:
        return "bad-value"

    if metric in GOOD_ABOVE_ONE:
        if numeric_value > 1:
            return "good-value"
        if numeric_value < 1:
            return "bad-value"

    if metric in GOOD_ABOVE_HALF:
        if numeric_value >= 0.5:
            return "good-value"
        return "bad-value"

    if metric in BAD_WHEN_NEGATIVE:
        if numeric_value < 0:
            return "bad-value"
        if numeric_value == 0:
            return "good-value"

    return ""


def correlation_value_class(value):
    if pd.isna(value):
        return ""

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return ""

    if numeric_value >= HIGH_CORRELATION_THRESHOLD:
        return "correlation-positive"

    if numeric_value <= -HIGH_CORRELATION_THRESHOLD:
        return "correlation-negative"

    return ""


def dataframe_to_html_table(
    df,
    max_rows=None,
    highlight_summary=False,
    highlight_correlation=False,
    correlation_columns_only=False,
):
    display_df = df.copy()

    if max_rows is not None:
        display_df = display_df.head(max_rows)

    if highlight_summary:
        return summary_dataframe_to_html_table(display_df)

    if highlight_correlation:
        return correlation_dataframe_to_html_table(
            display_df,
            columns_only=correlation_columns_only,
        )

    return display_df.to_html(
        index=False,
        classes="data-table",
        float_format=lambda value: f"{value:,.4f}",
        border=0,
    )


def decision_dataframe_to_html_table(df, max_rows=None):
    if df is None or df.empty:
        return '<div class="empty-state">No decision rows were created.</div>'

    display_df = df.copy()
    if max_rows is not None:
        display_df = display_df.head(max_rows)

    column_labels = {
        "Strategy_Name": "Strategy",
        "suggested_action": "Action",
        "strategy_status": "Status",
        "data_confidence": "Data",
        "total_pnl": "PNL",
        "total_return": "Total Return",
        "cagr": "Ann. Return",
        "max_drawdown": "Max DD",
        "profit_factor": "PF",
        "safe_f": "Safe-F",
        "risk_per_trade_dollars": "Risk $",
        "contracts_or_shares_to_trade": "Size",
        "settlement_coverage_ratio": "Settlement",
        "settlement_missing_count": "Missing Settle",
        "needs_review_open_position_count": "Open Review",
        "zero_or_missing_margin_count": "Missing Margin",
        "extreme_margin_loss_count": "Extreme Margin",
        "correlation_notice": "Correlation",
        "decision_reason": "Why",
        "allocation_note": "Allocation Note",
    }
    headers = "".join(
        f"<th>{html.escape(column_labels.get(column, str(column)))}</th>"
        for column in display_df.columns
    )
    rows = []

    for _, row in display_df.iterrows():
        cells = []

        for column in display_df.columns:
            value = row.get(column)

            if column == "suggested_action":
                cells.append(
                    "<td>"
                    f'<span class="status-badge {action_badge_class(value)}">{html.escape(str(value))}</span>'
                    "</td>"
                )
                continue

            if column == "strategy_status":
                cells.append(
                    "<td>"
                    f'<span class="status-badge {status_badge_class(value)}">{html.escape(str(value))}</span>'
                    "</td>"
                )
                continue

            if column == "data_confidence":
                cells.append(
                    "<td>"
                    f'<span class="status-badge {confidence_badge_class(value)}">{html.escape(str(value))}</span>'
                    "</td>"
                )
                continue

            if column == "Strategy_Name":
                cells.append(
                    f'<td class="strategy-name">{strategy_trade_link(value)}</td>'
                )
                continue

            if column in {
                "correlation_notice",
                "decision_reason",
                "allocation_note",
                "data_confidence_reason",
                "contracts_or_shares_to_trade",
            }:
                text = str(value) if not pd.isna(value) else ""
                class_name = (
                    "correlation-alert"
                    if column == "correlation_notice" and text
                    else "muted-text"
                )
                cells.append(
                    f'<td class="{class_name}">{html.escape(text or "None")}</td>'
                )
                continue

            class_name = summary_value_class(column, value)
            class_attr = f' class="{class_name}"' if class_name else ""
            cells.append(
                f"<td{class_attr}>{format_dashboard_metric(column, value)}</td>"
            )

        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<table class="data-table strategy-summary-table">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def correlation_dataframe_to_html_table(df, columns_only=False):
    headers = "".join(
        f"<th>{html.escape(str(column))}</th>"
        for column in df.columns
    )
    rows = []
    label_column = df.columns[0] if len(df.columns) else None

    for _, row in df.iterrows():
        cells = []
        row_label = key_value(row[label_column]) if label_column else ""

        for column in df.columns:
            class_name = ""

            if column != label_column:
                is_diagonal = row_label == key_value(column)
                is_correlation_column = "correlation" in str(column).lower()

                if columns_only and is_correlation_column:
                    class_name = correlation_value_class(row[column])
                elif not columns_only and not is_diagonal:
                    class_name = correlation_value_class(row[column])

            class_attr = f' class="{class_name}"' if class_name else ""
            cells.append(
                f"<td{class_attr}>{format_html_value(row[column])}</td>"
            )

        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<table class="data-table">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def summary_dataframe_to_html_table(df):
    headers = "".join(
        f"<th>{html.escape(str(column))}</th>"
        for column in df.columns
    )
    rows = []
    account_summary_shape = set(df.columns) == {"metric", "value"}

    for _, row in df.iterrows():
        cells = []
        row_metric = row["metric"] if account_summary_shape else None

        for column in df.columns:
            metric = row_metric if account_summary_shape else column
            class_name = summary_value_class(
                metric,
                row[column],
            )
            class_attr = f' class="{class_name}"' if class_name else ""
            cells.append(
                f"<td{class_attr}>{format_html_value(row[column])}</td>"
            )

        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<table class="data-table">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


PRIMARY_DASHBOARD_CHARTS = {
    "account_equity_curve.png",
    "strategy_equity_curves.png",
    "recent_trade_pnl_by_strategy.png",
    "daily_pnl_correlation_heatmap.png",
    "drawdown_correlation_heatmap.png",
    "risk_simulation_cagr_boxplot.png",
    "risk_simulation_max_drawdown_boxplot.png",
}


DASHBOARD_CHART_TITLES = {
    "account_equity_curve.png": "Account Equity Curve",
    "strategy_equity_curves.png": "Strategy Equity Curves",
    "recent_trade_pnl_by_strategy.png": "Recent Trade PNL By Strategy",
    "daily_pnl_correlation_heatmap.png": "Daily PNL Correlation",
    "drawdown_correlation_heatmap.png": "Drawdown Correlation",
    "risk_simulation_cagr_boxplot.png": "Simulation CAGR",
    "risk_simulation_max_drawdown_boxplot.png": "Simulation Max Drawdown",
}


def dashboard_chart_title(path):
    mapped_title = DASHBOARD_CHART_TITLES.get(os.path.basename(path))

    if mapped_title:
        return mapped_title

    title = os.path.basename(path).replace("_", " ").replace(".png", "")
    return title.title()


def dashboard_chart_sections(paths):
    return "\n".join(
        f'<section><h2>{html.escape(dashboard_chart_title(path))}</h2>'
        f'<img src="{html.escape(os.path.relpath(path, OUTPUT_DIR))}" '
        f'alt="{html.escape(path)}"></section>'
        for path in paths
    )


def split_dashboard_chart_files(chart_files, bottom_chart_files=None):
    primary_chart_files = []
    detail_chart_files = []

    for path in list(chart_files or []) + list(bottom_chart_files or []):
        if os.path.basename(path) in PRIMARY_DASHBOARD_CHARTS:
            primary_chart_files.append(path)
        else:
            detail_chart_files.append(path)

    return primary_chart_files, detail_chart_files


def save_dashboard(
    account_summary,
    benchmark_summary,
    strategy_highlights,
    summary,
    strategy_decision,
    capital_allocation,
    open_positions,
    open_position_audit,
    data_quality_warnings,
    pnl_correlation,
    drawdown_correlation,
    drawdown_overlap,
    chart_files,
    bottom_chart_files=None,
    ytd_reports=None,
    realized_trades=None,
    server_enabled=False,
):
    primary_chart_files, detail_chart_files = split_dashboard_chart_files(
        chart_files,
        bottom_chart_files,
    )
    ytd_details = ytd_reports_dashboard_section(ytd_reports)
    chart_tags = dashboard_chart_sections(primary_chart_files)
    detail_chart_tags = dashboard_chart_sections(detail_chart_files)
    account_kpi_cards = build_account_kpi_cards(
        account_summary,
        benchmark_summary,
        ytd_reports,
    )
    strategy_top_summary = build_strategy_top_summary(
        summary,
        pnl_correlation,
    )
    strategy_trade_browser = strategy_trade_browser_html(
        realized_trades,
        summary,
        server_enabled=server_enabled,
    )
    strategy_decision_display = strategy_decision
    if strategy_decision is not None and not strategy_decision.empty:
        strategy_decision_display = strategy_decision[
            [
                column
                for column in [
                    "Strategy_Name",
                    "suggested_action",
                    "strategy_status",
                    "data_confidence",
                    "total_pnl",
                    "total_return",
                    "max_drawdown",
                    "profit_factor",
                    "safe_f",
                    "risk_per_trade_dollars",
                    "settlement_coverage_ratio",
                    "settlement_missing_count",
                    "needs_review_open_position_count",
                    "zero_or_missing_margin_count",
                    "extreme_margin_loss_count",
                    "decision_reason",
                ]
                if column in strategy_decision.columns
            ]
        ]
    strategy_decision_table = decision_dataframe_to_html_table(
        strategy_decision_display,
    )
    compact_capital_allocation = build_compact_capital_allocation_table(
        capital_allocation,
    )
    capital_allocation_table = decision_dataframe_to_html_table(
        compact_capital_allocation,
    )
    detail_capital_allocation_table = decision_dataframe_to_html_table(
        capital_allocation,
    )

    dashboard = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strategy Performance Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17202a;
      --muted: #687382;
      --line: #d9e0e8;
      --surface: #ffffff;
      --surface-2: #f3f6f9;
      --navy: #102033;
      --teal: #0f766e;
      --amber: #b45309;
      --red: #b42336;
      --green: #167647;
      --blue: #1d4ed8;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--surface-2);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    .page-header {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(16, 32, 51, 0.97);
      color: #f8fafc;
      border-bottom: 1px solid rgba(255, 255, 255, 0.12);
      padding: 18px 28px;
      backdrop-filter: blur(10px);
    }}
    .page-header h1 {{
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }}
    .page-header p {{
      margin: 4px 0 0;
      color: #cbd5e1;
    }}
    main {{
      padding: 22px 28px 34px;
      max-width: 1680px;
      margin: 0 auto;
    }}
    h1, h2 {{
      margin-bottom: 8px;
    }}
    section {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 18px;
      box-shadow: 0 10px 28px rgba(16, 32, 51, 0.06);
    }}
    section h2 {{
      margin: 0 0 14px;
      font-size: 17px;
      letter-spacing: 0;
    }}
    .top-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 18px;
      margin-bottom: 18px;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .kpi-section + .kpi-section {{
      margin-top: 18px;
    }}
    .kpi-section h3 {{
      margin: 0 0 10px;
      color: #334155;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    .kpi-card {{
      min-height: 132px;
      background: #fbfcfe;
      border: 1px solid var(--line);
      border-top: 4px solid var(--teal);
      border-radius: 8px;
      padding: 14px;
    }}
    .kpi-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .kpi-value {{
      margin-top: 8px;
      font-size: clamp(23px, 2.2vw, 34px);
      font-weight: 800;
      letter-spacing: 0;
      color: var(--ink);
      overflow-wrap: anywhere;
    }}
    .kpi-caption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    img {{
      max-width: 100%;
      height: auto;
      display: block;
    }}
    .data-table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}
    .data-table th, .data-table td {{
      border-bottom: 1px solid #e6ebf1;
      padding: 8px 10px;
      text-align: right;
      vertical-align: top;
    }}
    .data-table th:first-child, .data-table td:first-child {{
      text-align: left;
    }}
    .data-table th {{
      background: #f8fafc;
      color: #475569;
      font-size: 11px;
      font-weight: 750;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .data-table tbody tr:hover {{
      background: #f8fafc;
    }}
    .strategy-summary-table td {{
      white-space: nowrap;
    }}
    .strategy-summary-table .strategy-name,
    .strategy-summary-table .correlation-alert,
    .strategy-summary-table .muted-text {{
      white-space: normal;
    }}
    .strategy-name {{
      min-width: 220px;
      font-weight: 700;
    }}
    .strategy-trade-link {{
      color: #0f766e;
      text-decoration: none;
      border-bottom: 1px solid rgba(15, 118, 110, 0.35);
    }}
    .strategy-trade-link:hover {{
      color: #134e4a;
      border-bottom-color: #134e4a;
    }}
    .status-badge {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 12px;
      font-weight: 800;
    }}
    .status-healthy {{
      color: #14532d;
      background: #dcfce7;
      border: 1px solid #86efac;
    }}
    .status-watch {{
      color: #7c2d12;
      background: #ffedd5;
      border: 1px solid #fdba74;
    }}
    .status-pause {{
      color: #7f1d1d;
      background: #fee2e2;
      border: 1px solid #fca5a5;
    }}
    .status-neutral {{
      color: #334155;
      background: #e2e8f0;
      border: 1px solid #cbd5e1;
    }}
    .correlation-alert {{
      color: #7c2d12;
      background: #fff7ed;
      font-weight: 700;
    }}
    .muted-text {{
      color: var(--muted);
    }}
    .empty-state {{
      color: var(--muted);
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fbfcfe;
    }}
    .good-value {{
      color: var(--green);
      font-weight: 700;
    }}
    .bad-value {{
      color: var(--red);
      font-weight: 700;
    }}
    .correlation-positive {{
      color: var(--green);
      font-weight: 700;
    }}
    .correlation-negative {{
      color: var(--red);
      font-weight: 700;
    }}
    .table-scroll {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .table-scroll .data-table th,
    .table-scroll .data-table td {{
      border-bottom: 1px solid #e6ebf1;
    }}
    .strategy-trade-browser {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }}
    .strategy-trade-browser label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .strategy-trade-browser select {{
      min-width: min(100%, 360px);
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      padding: 7px 10px;
      font: inherit;
    }}
    .dashboard-button {{
      min-height: 38px;
      border: 1px solid var(--teal);
      border-radius: 8px;
      background: var(--teal);
      color: #ffffff;
      padding: 7px 12px;
      font: inherit;
      font-weight: 750;
      cursor: pointer;
    }}
    .dashboard-button.secondary {{
      border-color: var(--line);
      background: #ffffff;
      color: var(--ink);
    }}
    .dashboard-button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}
    .strategy-save-status {{
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .strategy-save-status.good-value,
    .strategy-save-status.bad-value {{
      font-weight: 800;
    }}
    .strategy-edit-select {{
      width: min(100%, 260px);
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      padding: 6px 8px;
      font: inherit;
    }}
    .strategy-trade-browser-table td:nth-child(3),
    .strategy-trade-browser-table td:nth-child(4),
    .strategy-trade-browser-table td:nth-child(8),
    .strategy-trade-browser-table td:nth-child(9) {{
      white-space: normal;
    }}
    .strategy-trade-empty[hidden] {{
      display: none;
    }}
    .supporting-details {{
      background: transparent;
      border: 0;
      box-shadow: none;
      padding: 0;
      margin: 6px 0 0;
    }}
    .supporting-details > summary {{
      cursor: pointer;
      list-style: none;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      font-size: 16px;
      font-weight: 800;
      color: var(--navy);
    }}
    .supporting-details > summary::-webkit-details-marker {{
      display: none;
    }}
    .supporting-details > summary::after {{
      content: "+";
      float: right;
      color: var(--muted);
    }}
    .supporting-details[open] > summary::after {{
      content: "-";
    }}
    .supporting-details-content {{
      margin-top: 18px;
    }}
    @media (max-width: 1200px) {{
      .kpi-grid {{
        grid-template-columns: repeat(2, minmax(180px, 1fr));
      }}
    }}
    @media (max-width: 720px) {{
      .page-header, main {{
        padding-left: 16px;
        padding-right: 16px;
      }}
      .kpi-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header class="page-header">
    <h1>Strategy Performance Dashboard</h1>
    <p>Compact account reconciliation, strategy performance, allocation, and correlation risk.</p>
  </header>
  <main>
  <div class="top-grid">
    <section>
      <h2>Account Summary</h2>
      {account_kpi_cards}
    </section>
    <section>
      <h2>Strategy Summary</h2>
      <div class="table-scroll">
        {strategy_top_summary}
      </div>
    </section>
    <section>
      <h2>Capital Allocation</h2>
      <div class="table-scroll">
        {capital_allocation_table}
      </div>
    </section>
    {strategy_trade_browser}
  </div>
  {chart_tags}
  <details class="supporting-details">
    <summary>Supporting Details</summary>
    <div class="supporting-details-content">
      <section>
        <h2>Decision Board</h2>
        <div class="table-scroll">
          {strategy_decision_table}
        </div>
      </section>
      <section>
        <h2>Detailed Capital Allocation</h2>
        <div class="table-scroll">
          {detail_capital_allocation_table}
        </div>
      </section>
      {ytd_details}
      <section>
        <h2>Open Position Audit</h2>
        <div class="table-scroll">{dataframe_to_html_table(open_position_audit, max_rows=80)}</div>
      </section>
      <section>
        <h2>Open Positions Not Counted Yet</h2>
        <div class="table-scroll">{dataframe_to_html_table(open_positions, max_rows=80)}</div>
      </section>
      <section>
        <h2>Data Quality Warnings</h2>
        <div class="table-scroll">{dataframe_to_html_table(data_quality_warnings, max_rows=80)}</div>
      </section>
      <section>
        <h2>Daily PnL Correlation Table</h2>
        <div class="table-scroll">{dataframe_to_html_table(pnl_correlation.reset_index(), highlight_correlation=True)}</div>
      </section>
      <section>
        <h2>Drawdown Correlation Table</h2>
        <div class="table-scroll">{dataframe_to_html_table(drawdown_correlation.reset_index(), highlight_correlation=True)}</div>
      </section>
      <section>
        <h2>Drawdown Overlap</h2>
        <div class="table-scroll">{dataframe_to_html_table(drawdown_overlap.sort_values("drawdown_overlap_ratio", ascending=False), max_rows=40, highlight_correlation=True, correlation_columns_only=True)}</div>
      </section>
      {detail_chart_tags}
    </div>
  </details>
  </main>
  {strategy_trade_browser_script()}
</body>
</html>
"""

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as file:
        file.write(dashboard)


def open_dashboard(path):
    dashboard_url = f"file://{os.path.abspath(path)}"

    try:
        return webbrowser.open(
            dashboard_url,
            new=2,
        )
    except Exception as error:
        print(f"Could not open dashboard automatically: {error}")
        return False


def shutdown_strategy_dashboard_server(server):
    thread = threading.Thread(
        target=server.shutdown,
        daemon=True,
    )
    thread.start()
    return thread


class StrategyDashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}

        return json.loads(
            self.rfile.read(length).decode("utf-8")
        )

    def do_GET(self):
        parsed = urlparse(self.path)
        request_path = unquote(parsed.path or "/")

        if request_path in {"", "/"}:
            file_path = Path(self.server.dashboard_path)
        else:
            file_path = (Path(self.server.web_root) / request_path.lstrip("/"))

        try:
            resolved_file = file_path.resolve()
            resolved_root = Path(self.server.web_root).resolve()
            if (
                resolved_root != resolved_file
                and resolved_root not in resolved_file.parents
            ):
                self.send_error(403)
                return

            if not resolved_file.exists() or not resolved_file.is_file():
                self.send_error(404)
                return

            body = resolved_file.read_bytes()
            content_type = (
                mimetypes.guess_type(str(resolved_file))[0]
                or "application/octet-stream"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError as error:
            self.send_error(500, str(error))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/shutdown":
            self.send_json(200, {"message": "Dashboard server is stopping."})
            shutdown_strategy_dashboard_server(self.server)
            return

        if path != "/api/strategy-updates":
            self.send_json(404, {"error": "Unknown endpoint"})
            return

        try:
            payload = self.read_json_body()
            result = apply_strategy_name_updates_to_master(
                self.server.input_file,
                payload.get("updates", []),
            )
            self.send_json(200, result)
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_strategy_dashboard(
    dashboard_path,
    input_file,
    host="127.0.0.1",
    port=8780,
    open_browser=True,
):
    dashboard_path = Path(dashboard_path).resolve()
    server = HTTPServer((host, port), StrategyDashboardHandler)
    server.dashboard_path = str(dashboard_path)
    server.web_root = str(dashboard_path.parent)
    server.input_file = str(input_file)
    url = f"http://{host}:{port}/{dashboard_path.name}"

    print(f"Serving strategy dashboard at {url}")
    if open_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception as error:
            print(f"Could not open dashboard automatically: {error}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Strategy dashboard server stopped.")
    finally:
        server.server_close()


def ytd_reports_dashboard_section(ytd_reports):
    if not ytd_reports or ytd_reports.get("summary", pd.DataFrame()).empty:
        return """
  <section>
    <h2>Statement YTD Reconciliation</h2>
    <div class="empty-state">No statement YTD values were available.</div>
  </section>
"""

    return f"""
  <section>
    <h2>Statement YTD Reconciliation</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["summary"], max_rows=1)}</div>
  </section>
  <section>
    <h2>Trade History Validation Summary</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports.get("validation_summary", pd.DataFrame()), max_rows=20)}</div>
  </section>
  <section>
    <h2>Trade History Validation Issues</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports.get("validation_issues", pd.DataFrame()), max_rows=40)}</div>
  </section>
  <section>
    <h2>Open PnL Reconciliation</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["open_pnl"], max_rows=20)}</div>
  </section>
  <section>
    <h2>YTD Fee Reconciliation</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["fees"], max_rows=20)}</div>
  </section>
  <section>
    <h2>Closed YTD PnL Reconciliation</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["closed_pnl"], max_rows=25)}</div>
  </section>
  <section>
    <h2>Trade Rows To Review Before Adjustment</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["trade_review"], max_rows=30)}</div>
  </section>
  <section>
    <h2>Hypothetical Strategy Impact</h2>
    <div class="table-scroll">{dataframe_to_html_table(ytd_reports["strategy_impact"], max_rows=20)}</div>
  </section>
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze strategy performance from cleaned TOS trade history."
    )
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to master_cleaned_tos_data.csv.",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        help="Strategy_Name to include. Repeat for multiple strategies.",
    )
    parser.add_argument(
        "--no-open-dashboard",
        action="store_true",
        help="Do not open the dashboard in a browser after the script completes.",
    )
    parser.add_argument(
        "--serve-dashboard",
        action="store_true",
        help="Serve the dashboard locally so strategy-name edits can be saved.",
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8780,
        help="Port for --serve-dashboard.",
    )
    parser.add_argument(
        "--as-of-date",
        help=(
            "Timestamp used to decide whether unmatched option positions have "
            "expired. Defaults to the account statement date when one is "
            "available; otherwise defaults to the current timestamp."
        ),
    )
    parser.add_argument(
        "--futures-statement-file",
        help=(
            "Raw Thinkorswim account statement CSV. Defaults to the latest "
            "matching statement file found in data/."
        ),
    )
    parser.add_argument(
        "--no-auto-futures-settlement",
        action="store_true",
        help=(
            "Do not auto-detect a statement file in data/ for stale futures "
            "settlement. Explicit --futures-statement-file still applies."
        ),
    )
    parser.add_argument(
        "--benchmark-symbol",
        default="SPY",
        help=(
            "Buy-and-hold benchmark symbol. Default: SPY. The analyzer looks "
            "for a matching daily CSV under data/market_data/daily/."
        ),
    )
    parser.add_argument(
        "--benchmark-file",
        help=(
            "Daily OHLC benchmark CSV with timestamp/date and close columns. "
            "Overrides --benchmark-symbol file discovery."
        ),
    )
    parser.add_argument(
        "--no-benchmark",
        action="store_true",
        help="Skip buy-and-hold benchmark comparison.",
    )
    parser.add_argument(
        "--no-expired-option-settlement-check",
        action="store_true",
        help=(
            "Skip estimated intrinsic-value checks for expired options. "
            "By default, expired options are checked against local daily "
            "market data when available."
        ),
    )
    parser.add_argument(
        "--risk-simulations",
        type=int,
        default=DEFAULT_SIMULATIONS,
        help="Number of bootstrap equity curves for risk-per-trade calculations.",
    )
    parser.add_argument(
        "--risk-bankroll",
        type=float,
        default=DEFAULT_BANKROLL,
        help="Account equity used for risk-per-trade dollar sizing.",
    )
    parser.add_argument(
        "--risk-drawdown-limit",
        type=float,
        default=DEFAULT_DRAWDOWN_LIMIT,
        help="Drawdown limit used by the Safe-F risk test.",
    )
    parser.add_argument(
        "--risk-pct-above-dd-limit",
        type=float,
        default=DEFAULT_PCT_ABOVE_DD_LIMIT,
        help="Required percentage of simulations above the drawdown limit.",
    )
    parser.add_argument(
        "--risk-safe-f-increment",
        type=float,
        default=DEFAULT_SAFE_F_INCREMENT,
        help="Step size used when searching for Safe-F.",
    )
    parser.add_argument(
        "--risk-safe-f-start",
        type=float,
        default=DEFAULT_SAFE_F_START,
        help="Starting value used when searching for Safe-F.",
    )
    parser.add_argument(
        "--risk-last-n-trades",
        type=int,
        default=0,
        help="Use only the most recent N realized trades per strategy. 0 uses all.",
    )
    parser.add_argument(
        "--risk-random-seed",
        type=int,
        default=42,
        help="Random seed for risk-per-trade bootstrap sampling.",
    )

    return parser.parse_args()


def main():
    run_started_at = time.monotonic()
    args = parse_args()

    progress("Starting strategy performance analysis...")
    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True,
    )
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )
    os.makedirs(
        STRATEGY_TRADES_DIR,
        exist_ok=True,
    )
    os.makedirs(
        RISK_PER_TRADE_DIR,
        exist_ok=True,
    )

    stage_started_at = time.monotonic()
    progress(f"Loading cleaned trades from {args.input}...")
    cleaned_trades = load_cleaned_trades(args.input)
    cleaned_trades = filter_strategies(
        cleaned_trades,
        args.strategy,
    )
    original_execution_count = len(cleaned_trades)
    finish_progress(
        f"Loaded {original_execution_count} execution rows",
        stage_started_at,
    )

    futures_statement_file = args.futures_statement_file
    if (
        futures_statement_file is None
        and not args.no_auto_futures_settlement
    ):
        futures_statement_file = find_latest_account_statement(
            data_dir=None,
            preferred_filenames=statement_file_names_from_trades(
                cleaned_trades,
            ),
        )

    cash_balance_curve = pd.DataFrame()
    statement_cash_ledger = pd.DataFrame()
    statement_ytd_summary = {}
    statement_ytd_positions = pd.DataFrame()
    if futures_statement_file:
        progress(
            f"Using futures statement settlement file: {futures_statement_file}"
        )
        statement_ytd_summary, statement_ytd_positions = latest_statement_ytd(
            futures_statement_file
        )

    as_of_date = args.as_of_date
    if as_of_date is None:
        statement_as_of_date = statement_end_of_day(
            statement_ytd_summary,
        )
        if statement_as_of_date is not None:
            as_of_date = statement_as_of_date
            progress(
                "Using statement date as analysis cutoff: "
                f"{as_of_date}"
            )
        else:
            as_of_date = pd.Timestamp.now()

    stage_started_at = time.monotonic()
    progress("Aggregating realized trades and open positions...")
    trades = aggregate_realized_trades(
        cleaned_trades,
        as_of_date=as_of_date,
    )
    open_positions = get_open_positions(
        cleaned_trades,
        as_of_date=as_of_date,
    )
    finish_progress(
        (
            f"Built {len(trades)} realized trades and "
            f"{len(open_positions)} open positions"
        ),
        stage_started_at,
    )
    if futures_statement_file:
        stage_started_at = time.monotonic()
        progress("Reconciling stale futures positions against statement cash flows...")
        futures_statement_rows = parse_futures_statement_rows(
            futures_statement_file
        )
        position_summary = parse_statement_position_summary(
            futures_statement_file
        )
        futures_settlements = build_futures_statement_settlements(
            trades,
            open_positions,
            futures_statement_rows,
            position_summary,
        )

        if not futures_settlements.empty:
            trades = recalculate_account_columns(
                pd.concat(
                    [
                        trades,
                        futures_settlements,
                    ],
                    ignore_index=True,
                    sort=False,
                )
            )
            open_positions = remove_settled_futures_open_positions(
                open_positions,
                futures_settlements,
            )

        open_position_count_before_statement_reconciliation = len(
            open_positions
        )
        open_positions = reconcile_open_futures_positions_to_statement(
            open_positions,
            position_summary,
        )
        finish_progress(
            (
                "Finished futures settlement reconciliation "
                f"({len(futures_settlements)} synthetic settlement rows, "
                f"{open_position_count_before_statement_reconciliation - len(open_positions)} "
                "statement-closed open positions)"
            ),
            stage_started_at,
        )

        stage_started_at = time.monotonic()
        progress("Building account cash-balance diagnostic curve...")
        try:
            with open(
                futures_statement_file,
                errors="replace",
            ) as statement:
                statement_cash_ledger = parse_cash_ledger(
                    statement.readlines()
                )
            cash_balance_curve = build_cash_balance_curve(
                statement_cash_ledger,
                start_timestamp=cash_balance_start_timestamp(cleaned_trades),
            )
            finish_progress(
                (
                    "Built account cash-balance diagnostic curve "
                    f"({len(cash_balance_curve)} balance rows)"
                ),
                stage_started_at,
            )
        except Exception as error:
            cash_balance_curve = pd.DataFrame()
            finish_progress(
                (
                    "Skipped account cash-balance diagnostic curve "
                    f"({error})"
                ),
                stage_started_at,
            )

    if trades.empty:
        raise ValueError(
            "No closed trades or expired short options were found."
        )

    expired_option_settlement = pd.DataFrame()
    if not args.no_expired_option_settlement_check:
        stage_started_at = time.monotonic()
        progress("Checking expired option settlement values...")
        trades, expired_option_settlement = apply_expired_option_settlement_checks(
            trades,
            MARKET_DATA_DIR,
        )
        adjusted_count = (
            expired_option_settlement["settlement_status"].eq("adjusted_itm").sum()
            if not expired_option_settlement.empty
            else 0
        )
        missing_count = (
            expired_option_settlement["settlement_status"]
            .astype(str)
            .str.startswith("missing")
            .sum()
            if not expired_option_settlement.empty
            else 0
        )
        finish_progress(
            (
                "Checked expired option settlement values "
                f"({adjusted_count} adjusted, {missing_count} missing data)"
            ),
            stage_started_at,
        )

    stage_started_at = time.monotonic()
    progress("Calculating account, benchmark, strategy, and correlation summaries...")
    open_position_audit = build_open_position_audit(
        open_positions,
        as_of_date,
    )
    settlement_coverage = build_settlement_coverage(
        expired_option_settlement,
    )
    account_curve = build_account_equity_curve(trades)
    account_summary = calculate_account_summary(account_curve)
    cash_balance_summary = calculate_cash_balance_summary(
        cash_balance_curve,
    )
    if not cash_balance_summary.empty:
        account_summary = pd.concat(
            [
                account_summary,
                cash_balance_summary,
            ],
            ignore_index=True,
            sort=False,
        )
    benchmark_summary = pd.DataFrame()
    benchmark_file = args.benchmark_file
    if benchmark_file is None and not args.no_benchmark:
        benchmark_file = benchmark_file_for_symbol(args.benchmark_symbol)

    if benchmark_file and not args.no_benchmark:
        benchmark_summary = calculate_buy_hold_benchmark_summary(
            account_curve,
            benchmark_file,
            args.benchmark_symbol,
        )
        if benchmark_summary.empty:
            progress(
                "Benchmark comparison skipped: "
                f"{benchmark_file} did not have enough same-period data."
            )
        else:
            progress(
                "Using buy-and-hold benchmark file: "
                f"{benchmark_file}"
            )
    elif not args.no_benchmark:
        progress(
            "Benchmark comparison skipped: "
            f"no daily market data file found for {args.benchmark_symbol}."
        )
    strategy_trade_ledgers = build_strategy_trade_ledgers(trades)
    strategy_pnl_events = build_strategy_pnl_events(trades)
    equity_curves = build_strategy_equity_curves(strategy_pnl_events)
    summary = calculate_strategy_summary(
        trades,
        equity_curves,
    )
    summary = add_strategy_quality_columns(
        summary,
        settlement_coverage,
        open_position_audit,
    )
    (
        daily_pnl,
        pnl_correlation,
        drawdown_correlation,
        drawdown_overlap,
    ) = calculate_correlation_outputs(equity_curves)
    data_quality_warnings = build_data_quality_warnings(
        cleaned_trades,
        trades,
        open_positions,
        expired_option_settlement,
        open_position_audit,
        settlement_coverage,
    )
    ytd_reports = build_ytd_statement_reports(
        statement_ytd_summary,
        statement_ytd_positions,
        cleaned_trades,
        trades,
        open_positions,
        args.input,
    )
    statement_cash_reconciliation = cash_ledger_daily_reconciliation(
        cleaned_trades,
        statement_cash_ledger,
    )
    ytd_reports["validation_summary"] = build_trade_history_validation_summary(
        ytd_reports,
        statement_cash_reconciliation,
    )
    ytd_reports["validation_issues"] = build_trade_history_validation_issues(
        ytd_reports,
        statement_cash_reconciliation,
    )
    finish_progress(
        f"Calculated summaries for {len(summary)} strategies",
        stage_started_at,
    )

    stage_started_at = time.monotonic()
    progress("Writing CSV outputs...")
    equity_curves.to_csv(
        EQUITY_CURVES_FILE,
        index=False,
    )
    trades.to_csv(
        REALIZED_TRADES_FILE,
        index=False,
    )
    expired_option_settlement.to_csv(
        EXPIRED_OPTION_SETTLEMENT_FILE,
        index=False,
    )
    open_positions.to_csv(
        OPEN_POSITIONS_FILE,
        index=False,
    )
    open_position_audit.to_csv(
        OPEN_POSITION_AUDIT_FILE,
        index=False,
    )
    open_position_audit.to_csv(
        OPEN_POSITIONS_REVIEW_FILE,
        index=False,
    )
    open_position_audit.to_csv(
        OPEN_POSITIONS_DIAGNOSIS_FILE,
        index=False,
    )
    settlement_coverage.to_csv(
        SETTLEMENT_COVERAGE_FILE,
        index=False,
    )
    data_quality_warnings.to_csv(
        DATA_QUALITY_FILE,
        index=False,
    )
    ytd_reports["summary"].to_csv(
        YTD_STATEMENT_SUMMARY_FILE,
        index=False,
    )
    ytd_reports["open_pnl"].to_csv(
        YTD_POSITION_RECONCILIATION_FILE,
        index=False,
    )
    ytd_reports["fees"].to_csv(
        YTD_FEE_RECONCILIATION_FILE,
        index=False,
    )
    ytd_reports["closed_pnl"].to_csv(
        YTD_CLOSED_PNL_RECONCILIATION_FILE,
        index=False,
    )
    ytd_reports["trade_review"].to_csv(
        YTD_TRADE_REVIEW_FILE,
        index=False,
    )
    ytd_reports["strategy_impact"].to_csv(
        YTD_STRATEGY_IMPACT_FILE,
        index=False,
    )
    ytd_reports["validation_summary"].to_csv(
        TRADE_HISTORY_VALIDATION_SUMMARY_FILE,
        index=False,
    )
    ytd_reports["validation_issues"].to_csv(
        TRADE_HISTORY_VALIDATION_ISSUES_FILE,
        index=False,
    )
    account_curve.to_csv(
        ACCOUNT_EQUITY_CURVE_FILE,
        index=False,
    )
    cash_balance_curve.to_csv(
        ACCOUNT_CASH_BALANCE_CURVE_FILE,
        index=False,
    )
    account_summary.to_csv(
        ACCOUNT_SUMMARY_STATS_FILE,
        index=False,
    )
    benchmark_summary.to_csv(
        BENCHMARK_SUMMARY_STATS_FILE,
        index=False,
    )
    summary.to_csv(
        SUMMARY_STATS_FILE,
        index=False,
    )
    daily_pnl.to_csv(
        f"{OUTPUT_DIR}/strategy_daily_pnl.csv",
    )
    pnl_correlation.to_csv(
        PNL_CORRELATION_FILE,
    )
    drawdown_correlation.to_csv(
        DRAWDOWN_CORRELATION_FILE,
    )
    drawdown_overlap.to_csv(
        DRAWDOWN_OVERLAP_FILE,
        index=False,
    )
    finish_progress("Wrote CSV outputs", stage_started_at)

    stage_started_at = time.monotonic()
    progress("Saving per-strategy trade ledgers...")
    strategy_trade_files = save_strategy_trade_ledgers(strategy_trade_ledgers)
    finish_progress(
        f"Saved {len(strategy_trade_files)} strategy trade files",
        stage_started_at,
    )

    stage_started_at = time.monotonic()
    progress(
        "Running risk-per-trade simulations "
        f"({args.risk_simulations} simulations per strategy)..."
    )
    (
        risk_summary,
        risk_report_files,
        risk_simulations,
    ) = calculate_risk_per_trade_by_strategy(
        trades,
        output_dir=RISK_PER_TRADE_DIR,
        simulations=args.risk_simulations,
        safe_f_increment=args.risk_safe_f_increment,
        safe_f_start=args.risk_safe_f_start,
        bankroll=args.risk_bankroll,
        drawdown_limit=args.risk_drawdown_limit,
        pct_above_dd_limit=args.risk_pct_above_dd_limit,
        last_n_trades=args.risk_last_n_trades,
        random_seed=args.risk_random_seed,
    )
    risk_summary = calculate_strategy_position_sizing(
        risk_summary,
        trades,
    )
    strategy_decision = build_strategy_decision_board(
        summary,
        risk_summary,
        pnl_correlation,
    )
    capital_allocation = build_capital_allocation_table(
        strategy_decision,
    )
    risk_summary.to_csv(
        f"{RISK_PER_TRADE_DIR}/risk_per_trade_summary.csv",
        index=False,
    )
    strategy_decision.to_csv(
        STRATEGY_DECISION_FILE,
        index=False,
    )
    capital_allocation.to_csv(
        CAPITAL_ALLOCATION_FILE,
        index=False,
    )
    finish_progress(
        (
            "Finished risk-per-trade simulations "
            f"({len(risk_report_files)} report files)"
        ),
        stage_started_at,
    )

    stage_started_at = time.monotonic()
    progress("Building dashboard highlights and charts...")
    strategy_highlights = build_strategy_dashboard_highlights(
        summary,
        risk_summary,
    )
    risk_boxplot_files = save_risk_simulation_boxplots(risk_simulations)

    chart_files = [
        save_account_equity_chart(account_curve),
        save_strategy_equity_chart(equity_curves),
        save_recent_trade_pnl_chart(trades),
        save_correlation_heatmap(
            pnl_correlation,
            "Daily PnL Correlation Heat Map",
            "daily_pnl_correlation_heatmap.png",
        ),
        save_correlation_heatmap(
            drawdown_correlation,
            "Drawdown Correlation Heat Map",
            "drawdown_correlation_heatmap.png",
        ),
        *save_summary_bar_charts(summary),
    ]
    chart_files = [
        path
        for path in chart_files
        if path is not None
    ]
    finish_progress(
        (
            f"Built {len(chart_files)} dashboard charts and "
            f"{len(risk_boxplot_files)} risk charts"
        ),
        stage_started_at,
    )

    stage_started_at = time.monotonic()
    progress("Writing strategy dashboard HTML...")
    save_dashboard(
        account_summary,
        benchmark_summary,
        strategy_highlights,
        summary,
        strategy_decision,
        capital_allocation,
        open_positions,
        open_position_audit,
        data_quality_warnings,
        pnl_correlation,
        drawdown_correlation,
        drawdown_overlap,
        chart_files,
        risk_boxplot_files,
        ytd_reports,
        realized_trades=trades,
        server_enabled=args.serve_dashboard,
    )
    finish_progress("Wrote strategy dashboard HTML", stage_started_at)

    print(f"Saved account equity curve to {ACCOUNT_EQUITY_CURVE_FILE}")
    print(f"Saved account cash-balance curve to {ACCOUNT_CASH_BALANCE_CURVE_FILE}")
    print(f"Saved account summary statistics to {ACCOUNT_SUMMARY_STATS_FILE}")
    print(f"Saved benchmark summary statistics to {BENCHMARK_SUMMARY_STATS_FILE}")
    print(f"Saved realized trades to {REALIZED_TRADES_FILE}")
    print(f"Saved expired option settlement check to {EXPIRED_OPTION_SETTLEMENT_FILE}")
    print(f"Saved open positions to {OPEN_POSITIONS_FILE}")
    print(f"Saved open position audit to {OPEN_POSITION_AUDIT_FILE}")
    print(f"Saved settlement coverage to {SETTLEMENT_COVERAGE_FILE}")
    print(f"Saved data quality warnings to {DATA_QUALITY_FILE}")
    print(f"Saved YTD statement summary to {YTD_STATEMENT_SUMMARY_FILE}")
    print(f"Saved YTD open PnL reconciliation to {YTD_POSITION_RECONCILIATION_FILE}")
    print(f"Saved YTD fee reconciliation to {YTD_FEE_RECONCILIATION_FILE}")
    print(f"Saved YTD closed PnL reconciliation to {YTD_CLOSED_PNL_RECONCILIATION_FILE}")
    print(f"Saved YTD trade review to {YTD_TRADE_REVIEW_FILE}")
    print(f"Saved YTD strategy impact to {YTD_STRATEGY_IMPACT_FILE}")
    print(f"Saved trade history validation summary to {TRADE_HISTORY_VALIDATION_SUMMARY_FILE}")
    print(f"Saved trade history validation issues to {TRADE_HISTORY_VALIDATION_ISSUES_FILE}")
    print(f"Saved strategy decision board to {STRATEGY_DECISION_FILE}")
    print(f"Saved capital allocation table to {CAPITAL_ALLOCATION_FILE}")
    print(f"Saved strategy trade files to {STRATEGY_TRADES_DIR}")
    print(f"Saved risk-per-trade files to {RISK_PER_TRADE_DIR}")
    print(f"Saved strategy equity curves to {EQUITY_CURVES_FILE}")
    print(f"Saved strategy summary statistics to {SUMMARY_STATS_FILE}")
    print(f"Saved strategy dashboard to {DASHBOARD_FILE}")
    print(
        "Aggregated "
        f"{original_execution_count} execution rows into "
        f"{len(trades)} realized trades."
    )
    print(
        "Saved "
        f"{len(strategy_trade_files)} strategy trade files."
    )
    print(
        "Saved "
        f"{len(risk_report_files)} risk-per-trade report files for "
        f"{len(risk_summary)} strategies."
    )
    print(
        f"Completed strategy performance analysis in "
        f"{format_duration(time.monotonic() - run_started_at)}."
    )

    if args.serve_dashboard:
        serve_strategy_dashboard(
            DASHBOARD_FILE,
            args.input,
            host=args.dashboard_host,
            port=args.dashboard_port,
            open_browser=not args.no_open_dashboard,
        )
    elif not args.no_open_dashboard:
        if open_dashboard(DASHBOARD_FILE):
            print("Opened strategy dashboard in your browser.")
        else:
            print(
                "Dashboard was created, but no browser reported that it opened."
            )


if __name__ == "__main__":
    main()
