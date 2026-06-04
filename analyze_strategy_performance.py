import argparse
import html
import math
import os
import re
import webbrowser
from collections import deque

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


INPUT_FILE = "./output/master_cleaned_tos_data.csv"
OUTPUT_DIR = "./output/strategy_performance"
CHART_DIR = f"{OUTPUT_DIR}/charts"
STRATEGY_TRADES_DIR = f"{OUTPUT_DIR}/strategy_trades"
RISK_PER_TRADE_DIR = f"{OUTPUT_DIR}/risk_per_trade"

EQUITY_CURVES_FILE = f"{OUTPUT_DIR}/strategy_equity_curves.csv"
SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/strategy_summary_statistics.csv"
ACCOUNT_EQUITY_CURVE_FILE = f"{OUTPUT_DIR}/account_equity_curve.csv"
ACCOUNT_SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/account_summary_statistics.csv"
REALIZED_TRADES_FILE = f"{OUTPUT_DIR}/realized_trades.csv"
OPEN_POSITIONS_FILE = f"{OUTPUT_DIR}/open_positions.csv"
DATA_QUALITY_FILE = f"{OUTPUT_DIR}/data_quality_warnings.csv"
PNL_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_pnl_correlation.csv"
DRAWDOWN_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_drawdown_correlation.csv"
DRAWDOWN_OVERLAP_FILE = f"{OUTPUT_DIR}/strategy_drawdown_overlap.csv"
DASHBOARD_FILE = f"{OUTPUT_DIR}/strategy_dashboard.html"
OPTION_TYPES = {
    "CALL",
    "PUT",
}
HIGH_CORRELATION_THRESHOLD = 0.7


def clean_strategy_name(value):
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


def position_key(row):
    spread = normalized_spread(row)

    if is_option_trade(row):
        if spread == "STRADDLE":
            return (
                key_value(row.get("Strategy_Name")),
                key_value(row.get("Symbol")),
                key_value(row.get("Exp")),
                key_value(row.get("Strike")),
                spread,
            )

        if spread == "VERTICAL":
            return (
                key_value(row.get("Strategy_Name")),
                key_value(row.get("Symbol")),
                key_value(row.get("Exp")),
                key_value(row.get("Type")),
                spread,
            )

        return (
            key_value(row.get("Strategy_Name")),
            key_value(row.get("Symbol")),
            key_value(row.get("Exp")),
            key_value(row.get("Strike")),
            key_value(row.get("Type")),
            spread,
        )

    return (
        key_value(row.get("Strategy_Name")),
        key_value(row.get("Symbol")),
        spread,
    )


def latest_short_option_expirations(df):
    latest_expirations = {}

    for _, row in df.iterrows():
        if not is_option_trade(row):
            continue

        if str(row.get("Pos Effect", "")).upper() != "TO OPEN":
            continue

        if str(row.get("Side", "")).upper() != "SELL":
            continue

        expiration = option_expiration_close(row)

        if expiration is None:
            continue

        strategy_name = key_value(row.get("Strategy_Name"))
        current_expiration = latest_expirations.get(strategy_name)

        if current_expiration is None or expiration > current_expiration:
            latest_expirations[strategy_name] = expiration

    return latest_expirations


def is_expired_short_option(
    row,
    as_of_date,
    latest_expirations=None,
):
    if not is_option_trade(row):
        return False

    if str(row.get("Pos Effect", "")).upper() != "TO OPEN":
        return False

    if str(row.get("Side", "")).upper() != "SELL":
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
        latest_short_option_expirations(working)
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
            if is_expired_short_option(
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
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        lots = open_lots.get(key)

        if not lots:
            realized_indices.add(index)
            continue

        remaining_qty = qty
        matched_close = False

        while remaining_qty > 0 and lots:
            lot = lots[0]

            if lot["side"] == side:
                lots.popleft()
                continue

            matched_qty = min(
                remaining_qty,
                lot["remaining_qty"],
            )
            remaining_qty -= matched_qty
            lot["remaining_qty"] -= matched_qty
            matched_close = True
            realized_indices.add(lot["index"])

            if lot["remaining_qty"] <= 0:
                lots.popleft()

        if matched_close:
            realized_indices.add(index)

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
        qty_values = [
            abs(parse_number(row.get("Qty")) or 0.0)
            for row in rows
        ]
        qty = max(qty_values) if qty_values else 0.0

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
        latest_short_option_expirations(working)
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

        lots = open_lots.get(key)
        remaining_qty = qty
        matched_qty = 0.0
        open_matches = []

        while remaining_qty > 0 and lots:
            lot = lots[0]

            if lot["side"] == side:
                lots.popleft()
                continue

            qty_to_match = min(
                remaining_qty,
                lot["remaining_qty"],
            )
            remaining_qty -= qty_to_match
            matched_qty += qty_to_match
            lot["remaining_qty"] -= qty_to_match
            open_matches.append({
                "row": lot["row"],
                "qty": qty_to_match,
            })

            if lot["remaining_qty"] <= 0:
                lots.popleft()

        if open_matches:
            realized_trades.append(
                build_realized_trade_record(
                    open_matches,
                    close_row=row,
                    close_qty=qty,
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

            if not is_expired_short_option(
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
        latest_short_option_expirations(working)
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

        lots = open_lots.get(key)
        remaining_qty = qty

        while remaining_qty > 0 and lots:
            lot = lots[0]

            if lot["side"] == side:
                lots.popleft()
                continue

            qty_to_match = min(
                remaining_qty,
                lot["remaining_qty"],
            )
            remaining_qty -= qty_to_match
            lot["remaining_qty"] -= qty_to_match

            if lot["remaining_qty"] <= 0:
                lots.popleft()

    rows = []

    for lots in open_lots.values():
        for lot in lots:
            row = lot["row"]

            if is_expired_short_option(
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


def build_data_quality_warnings(cleaned_trades, realized_trades, open_positions):
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
        duplicate_keys = (
            cleaned_trades[dedupe_columns]
            .fillna("")
            .astype(str)
            .agg("|".join, axis=1)
        )
        add_warning(
            "duplicate_execution_keys",
            "high",
            duplicate_keys.duplicated(keep=False).sum(),
            "Duplicate execution keys may double-count PnL.",
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


def calculate_strategy_summary(realized_trades, equity_curves):
    rows = []

    for strategy_name, group in realized_trades.groupby("Strategy_Name", sort=True):
        group = group.sort_values(
            ["timestamp"],
            na_position="last",
        )
        net_pnl = group["net_pnl"]
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
        ncols=2,
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
            tick_labels=strategy_names,
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
}

GOOD_ABOVE_ONE = {
    "profit_factor",
    "strategy_profit_factor",
    "last_20_profit_factor",
    "profit_factor_Q25",
}

GOOD_ABOVE_HALF = {
    "win_rate",
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


def save_dashboard(
    account_summary,
    strategy_highlights,
    summary,
    open_positions,
    data_quality_warnings,
    pnl_correlation,
    drawdown_correlation,
    drawdown_overlap,
    chart_files,
    bottom_chart_files=None,
):
    bottom_chart_files = bottom_chart_files or []
    chart_tags = "\n".join(
        f'<section><h2>{html.escape(os.path.basename(path).replace("_", " ").replace(".png", "").title())}</h2>'
        f'<img src="{html.escape(os.path.relpath(path, OUTPUT_DIR))}" alt="{html.escape(path)}"></section>'
        for path in chart_files
    )
    bottom_chart_tags = "\n".join(
        f'<section><h2>{html.escape(os.path.basename(path).replace("_", " ").replace(".png", "").title())}</h2>'
        f'<img src="{html.escape(os.path.relpath(path, OUTPUT_DIR))}" alt="{html.escape(path)}"></section>'
        for path in bottom_chart_files
    )

    dashboard = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Strategy Performance Dashboard</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 24px;
      color: #1f2933;
      background: #f7f8fa;
    }}
    h1, h2 {{
      margin-bottom: 8px;
    }}
    section {{
      background: #ffffff;
      border: 1px solid #d8dde6;
      border-radius: 6px;
      padding: 16px;
      margin-bottom: 20px;
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
      border-bottom: 1px solid #e2e8f0;
      padding: 6px 8px;
      text-align: right;
    }}
    .data-table th:first-child, .data-table td:first-child {{
      text-align: left;
    }}
    .good-value {{
      color: #1b5e20;
      font-weight: 700;
    }}
    .bad-value {{
      color: #b71c1c;
      font-weight: 700;
    }}
    .correlation-positive {{
      color: #1b5e20;
      font-weight: 700;
    }}
    .correlation-negative {{
      color: #b71c1c;
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <h1>Strategy Performance Dashboard</h1>
  <section>
    <h2>Strategy Highlights</h2>
    {dataframe_to_html_table(strategy_highlights, highlight_summary=True)}
  </section>
  <section>
    <h2>Account Summary Statistics</h2>
    {dataframe_to_html_table(account_summary, highlight_summary=True)}
  </section>
  <section>
    <h2>Strategy Summary Statistics</h2>
    {dataframe_to_html_table(summary.sort_values("total_pnl", ascending=False), highlight_summary=True)}
  </section>
  <section>
    <h2>Open Positions Not Counted Yet</h2>
    {dataframe_to_html_table(open_positions, max_rows=80)}
  </section>
  <section>
    <h2>Data Quality Warnings</h2>
    {dataframe_to_html_table(data_quality_warnings, max_rows=80)}
  </section>
  {chart_tags}
  <section>
    <h2>Daily PnL Correlation</h2>
    {dataframe_to_html_table(pnl_correlation.reset_index(), highlight_correlation=True)}
  </section>
  <section>
    <h2>Drawdown Correlation</h2>
    {dataframe_to_html_table(drawdown_correlation.reset_index(), highlight_correlation=True)}
  </section>
  <section>
    <h2>Drawdown Overlap</h2>
    {dataframe_to_html_table(drawdown_overlap.sort_values("drawdown_overlap_ratio", ascending=False), max_rows=40, highlight_correlation=True, correlation_columns_only=True)}
  </section>
  {bottom_chart_tags}
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
    args = parse_args()

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

    cleaned_trades = load_cleaned_trades(args.input)
    cleaned_trades = filter_strategies(
        cleaned_trades,
        args.strategy,
    )
    original_execution_count = len(cleaned_trades)
    open_positions = get_open_positions(cleaned_trades)
    trades = aggregate_realized_trades(cleaned_trades)

    if trades.empty:
        raise ValueError(
            "No closed trades or expired short options were found."
        )

    data_quality_warnings = build_data_quality_warnings(
        cleaned_trades,
        trades,
        open_positions,
    )
    account_curve = build_account_equity_curve(trades)
    account_summary = calculate_account_summary(account_curve)
    strategy_trade_ledgers = build_strategy_trade_ledgers(trades)
    strategy_pnl_events = build_strategy_pnl_events(trades)
    equity_curves = build_strategy_equity_curves(strategy_pnl_events)
    summary = calculate_strategy_summary(
        trades,
        equity_curves,
    )
    (
        daily_pnl,
        pnl_correlation,
        drawdown_correlation,
        drawdown_overlap,
    ) = calculate_correlation_outputs(equity_curves)

    equity_curves.to_csv(
        EQUITY_CURVES_FILE,
        index=False,
    )
    trades.to_csv(
        REALIZED_TRADES_FILE,
        index=False,
    )
    open_positions.to_csv(
        OPEN_POSITIONS_FILE,
        index=False,
    )
    data_quality_warnings.to_csv(
        DATA_QUALITY_FILE,
        index=False,
    )
    account_curve.to_csv(
        ACCOUNT_EQUITY_CURVE_FILE,
        index=False,
    )
    account_summary.to_csv(
        ACCOUNT_SUMMARY_STATS_FILE,
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
    strategy_trade_files = save_strategy_trade_ledgers(strategy_trade_ledgers)
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
    risk_summary.to_csv(
        f"{RISK_PER_TRADE_DIR}/risk_per_trade_summary.csv",
        index=False,
    )
    strategy_highlights = build_strategy_dashboard_highlights(
        summary,
        risk_summary,
    )
    risk_boxplot_files = save_risk_simulation_boxplots(risk_simulations)

    chart_files = [
        save_account_equity_chart(account_curve),
        save_strategy_equity_chart(equity_curves),
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
    save_dashboard(
        account_summary,
        strategy_highlights,
        summary,
        open_positions,
        data_quality_warnings,
        pnl_correlation,
        drawdown_correlation,
        drawdown_overlap,
        chart_files,
        risk_boxplot_files,
    )

    print(f"Saved account equity curve to {ACCOUNT_EQUITY_CURVE_FILE}")
    print(f"Saved account summary statistics to {ACCOUNT_SUMMARY_STATS_FILE}")
    print(f"Saved realized trades to {REALIZED_TRADES_FILE}")
    print(f"Saved open positions to {OPEN_POSITIONS_FILE}")
    print(f"Saved data quality warnings to {DATA_QUALITY_FILE}")
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

    if not args.no_open_dashboard:
        if open_dashboard(DASHBOARD_FILE):
            print("Opened strategy dashboard in your browser.")
        else:
            print(
                "Dashboard was created, but no browser reported that it opened."
            )


if __name__ == "__main__":
    main()
