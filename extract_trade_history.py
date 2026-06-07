import argparse
import os
import csv
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path

os.environ.setdefault(
    "MPLCONFIGDIR",
    "/tmp/matplotlib",
)

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.enrich import (
    add_pnl_columns,
    add_log_return_columns,
    add_margin_return_columns,
    build_equity_curve,
    calculate_summary_statistics,
    calculate_margin_requirements,
    is_futures_option_symbol,
    is_futures_trade,
    is_multileg_continuation,
    lookup_fees,
    parse_number,
)


INPUT_FILE = "./data/trades.csv"
OUTPUT_DIR = "./output"
OUTPUT_FILE = f"{OUTPUT_DIR}/cleaned_tos_data.csv"
MASTER_OUTPUT_FILE = f"{OUTPUT_DIR}/master_cleaned_tos_data.csv"
PNL_PLOT_FILE = f"{OUTPUT_DIR}/pnl_chart.png"
EQUITY_CURVE_FILE = f"{OUTPUT_DIR}/equity_curve.csv"
SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/summary_statistics.csv"
CASH_RECONCILIATION_FILE = f"{OUTPUT_DIR}/cash_balance_reconciliation.csv"
CASH_RECONCILIATION_SUMMARY_FILE = (
    f"{OUTPUT_DIR}/cash_balance_reconciliation_summary.csv"
)
CASH_RECONCILIATION_DASHBOARD_FILE = (
    f"{OUTPUT_DIR}/cash_balance_reconciliation_dashboard.html"
)
FEE_CORRECTION_SUGGESTIONS_FILE = (
    f"{OUTPUT_DIR}/fee_correction_suggestions.csv"
)
CASH_TRADE_CORRECTIONS_FILE = f"{OUTPUT_DIR}/cash_trade_corrections.csv"
CASH_TRADE_CORRECTION_CANDIDATES_FILE = (
    f"{OUTPUT_DIR}/cash_trade_correction_candidates.csv"
)
CASH_RECONCILIATION_REVIEWS_FILE = (
    f"{OUTPUT_DIR}/cash_reconciliation_group_reviews.csv"
)
CASH_DASHBOARD_FILE_NAME = "cash_balance_reconciliation_dashboard.html"

CALCULATED_COLUMNS = {
    "Strategy_Name",
    "fees",
    "margin_requirement",
    "trade_pnl",
    "net_pnl",
    "cumulative_pnl",
    "starting_equity",
    "ending_equity",
    "log_return",
    "cumulative_log_return",
    "return_on_margin",
    "log_return_on_margin",
    "cumulative_log_return_on_margin",
    "cash_correction_applied",
    "cash_correction_status",
    "cash_correction_source",
    "statement_cash_flow",
}

CASH_CORRECTION_COLUMNS = [
    "statement_file",
    "statement_trade_row",
    "date",
    "account_bucket",
    "event_sequence",
    "event_leg_sequence",
    "correction_status",
    "correction_source",
    "ledger_timestamp",
    "ledger_description",
    "ledger_amount",
    "ledger_cash_flow",
    "ledger_misc_fees",
    "ledger_commissions_fees",
    "original_trade_pnl",
    "original_fees",
    "original_net_pnl",
    "corrected_trade_pnl",
    "corrected_fees",
    "corrected_net_pnl",
]

CASH_RECONCILIATION_REVIEW_COLUMNS = [
    "date",
    "account_bucket",
    "statement_trade_rows",
    "extracted_trade_count",
    "statement_trade_cash_flow",
    "extracted_net_pnl",
    "unreconciled_delta",
    "review_status",
]

VALID_MANUAL_STRATEGY_NAMES = {
    "Discretionary",
    "Opt025-TradeBusters-7DTE-Naked-Puts",
    "Opt026-60m0DTE-PutSpread",
    "Opt021-1DTE-ShortStraddle-Mon-Thurs",
}

AUTO_CASH_LEDGER_STRATEGIES = {
    "Opt026-60m0DTE-PutSpread",
}

AUTO_CASH_LEDGER_ACCOUNT_BUCKETS = {
    "futures",
}

PREFERRED_DEDUPE_COLUMNS = [
    "statement_trade_row",
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

NUMERIC_DEDUPE_COLUMNS = {
    "Qty",
    "Strike",
    "Price",
    "Net Price",
    "statement_trade_row",
}

STATEMENT_IDENTITY_COLUMNS = [
    "statement_trade_row",
]


END_SECTIONS = {
    "Equities",
    "Options",
    "Futures",
    "Forex",
    "Crypto",
}


def parse_statement_datetime(date_value, time_value):

    if not date_value or not time_value:
        return None

    parsed = pd.to_datetime(
        f"{date_value} {time_value}",
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    if pd.isna(parsed):
        return None

    return parsed


def balance_account_key(description, row):

    description = str(description)

    if "Futures cash balance" in description:
        return "futures"

    if "Crypto Cash balance" in description:
        return "crypto"

    if row and row[0].strip() == "":
        return "forex"

    if "Cash balance" in description:
        return "cash"

    return None


def cash_section_account_key(section_name):

    section_name = str(section_name).strip().lower()

    if section_name == "futures statements":
        return "futures"

    if section_name == "forex statements":
        return "forex"

    if "crypto" in section_name and "statements" in section_name:
        return "crypto"

    if section_name in {
        "cash balance",
        "cash & sweep vehicle",
        "cash and sweep vehicle",
    }:
        return "cash"

    if "futures" in section_name and "cash" in section_name:
        return "futures"

    if "forex" in section_name and "cash" in section_name:
        return "forex"

    if "crypto" in section_name and "cash" in section_name:
        return "crypto"

    return None


def shifted_cash_ledger_row_parts(row, account_key):

    if len(row) < 10:
        return None

    row_type = str(row[3]).strip()

    if row_type in {"", "TYPE"}:
        return None

    if account_key == "futures":
        return {
            "date": row[1],
            "time": row[2],
            "type": row_type,
            "description": row[5],
            "misc_fees": row[6],
            "commissions_fees": row[7],
            "amount": row[8],
            "balance": row[9],
        }

    amount = row[8] if len(row) > 8 else ""
    balance = row[9] if len(row) > 9 else ""

    return {
        "date": row[1],
        "time": row[2],
        "type": row_type,
        "description": row[5],
        "misc_fees": "",
        "commissions_fees": row[6],
        "amount": amount,
        "balance": balance,
    }


def cash_ledger_row_parts(row, account_key=None):

    if (
        account_key in {"futures", "forex", "crypto"}
        and len(row) > 3
        and str(row[3]).strip()
    ):
        shifted_parts = shifted_cash_ledger_row_parts(row, account_key)

        if shifted_parts is not None:
            return shifted_parts

    if len(row) < 9:
        return None

    if row[2] in {"", "TYPE"}:
        return None

    return {
        "date": row[0],
        "time": row[1],
        "type": row[2],
        "description": row[4] if len(row) > 4 else "",
        "misc_fees": row[5] if len(row) > 5 else "",
        "commissions_fees": row[6] if len(row) > 6 else "",
        "amount": row[7] if len(row) > 7 else "",
        "balance": row[8] if len(row) > 8 else "",
    }


def parse_cash_ledger(lines):

    rows = []
    current_account = None
    stop_ledger_sections = {
        "Account Order History",
        "Equities",
        "Options",
        "Futures",
        "Forex",
        "Crypto",
    }

    for row in csv.reader(lines):
        if not row:
            continue

        first_col = str(row[0]).strip().strip('"').lstrip("\ufeff")

        if first_col == "Account Trade History":
            break

        if first_col in stop_ledger_sections:
            current_account = None
            continue

        account_key = cash_section_account_key(first_col)

        if account_key is not None:
            current_account = account_key
            continue

        if current_account is None:
            continue

        parts = cash_ledger_row_parts(row, current_account)

        if parts is None:
            continue

        timestamp = parse_statement_datetime(
            parts["date"],
            parts["time"],
        )

        if timestamp is None:
            continue

        amount = parse_number(parts["amount"]) or 0.0
        misc_fees = parse_number(parts["misc_fees"]) or 0.0
        commissions_fees = parse_number(parts["commissions_fees"]) or 0.0
        balance = parse_number(parts["balance"])

        rows.append(
            {
                "date": timestamp.date().isoformat(),
                "timestamp": timestamp,
                "account_bucket": current_account,
                "type": str(parts["type"]).strip(),
                "description": parts["description"],
                "amount": amount,
                "misc_fees": misc_fees,
                "commissions_fees": commissions_fees,
                "cash_flow": amount + misc_fees + commissions_fees,
                "balance": balance,
            }
        )

    return pd.DataFrame(rows)


def parse_filter_date(value, label):
    if value is None:
        return None

    parsed = pd.to_datetime(
        value,
        errors="coerce",
    )

    if pd.isna(parsed):
        raise ValueError(
            f"Could not parse {label}: {value}"
        )

    return parsed.normalize()


def filter_by_exec_date(df, start_date=None):
    if start_date is None or df.empty:
        return df

    timestamps = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    return df.loc[
        timestamps >= start_date
    ].copy()


def filter_cash_ledger_by_date(cash_ledger, start_date=None, end_date=None):
    if (
        start_date is None
        and end_date is None
    ) or cash_ledger is None or cash_ledger.empty:
        return cash_ledger

    timestamps = pd.to_datetime(
        cash_ledger["timestamp"],
        errors="coerce",
    )
    keep = pd.Series(
        True,
        index=cash_ledger.index,
    )

    if start_date is not None:
        keep &= timestamps >= start_date

    if end_date is not None:
        keep &= timestamps < end_date + pd.Timedelta(days=1)

    return cash_ledger.loc[keep].copy()


def current_statement_approved_corrections(candidates, approved_corrections):
    approved_keys = approved_correction_keys(approved_corrections)

    if not approved_keys:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    return filter_corrections_to_keys(
        candidates,
        approved_keys,
    )


def auto_approved_cash_trade_corrections(candidates, trades):
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    if trades is None or trades.empty:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    if not {
        "statement_file",
        "statement_trade_row",
    }.issubset(candidates.columns) or not {
        "statement_file",
        "statement_trade_row",
        "Strategy_Name",
    }.issubset(trades.columns):
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    trade_strategies = trades[
        [
            "statement_file",
            "statement_trade_row",
            "Strategy_Name",
        ]
    ].copy()
    trade_strategies["statement_trade_row"] = pd.to_numeric(
        trade_strategies["statement_trade_row"],
        errors="coerce",
    )
    working = candidates.copy()
    working["statement_trade_row"] = pd.to_numeric(
        working["statement_trade_row"],
        errors="coerce",
    )
    working = working.merge(
        trade_strategies,
        on=[
            "statement_file",
            "statement_trade_row",
        ],
        how="left",
    )
    strategy = working["Strategy_Name"].fillna("").astype(str)
    account_bucket = working["account_bucket"].fillna("").astype(str)
    auto_mask = (
        strategy.isin(AUTO_CASH_LEDGER_STRATEGIES)
        | account_bucket.isin(AUTO_CASH_LEDGER_ACCOUNT_BUCKETS)
    )

    return working.loc[
        auto_mask,
        CASH_CORRECTION_COLUMNS,
    ].copy()


def combine_cash_trade_corrections(*correction_frames):
    frames = [
        frame
        for frame in correction_frames
        if frame is not None and not frame.empty
    ]

    if not frames:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    combined = pd.concat(
        frames,
        ignore_index=True,
        sort=False,
    ).reindex(columns=CASH_CORRECTION_COLUMNS)

    if {
        "statement_file",
        "statement_trade_row",
    }.issubset(combined.columns):
        combined = combined.drop_duplicates(
            subset=[
                "statement_file",
                "statement_trade_row",
            ],
            keep="last",
        )

    return combined


def balance_row_parts(row):

    if len(row) > 8 and row[2] == "BAL":
        return row[0], row[1], row[4], row[8]

    if len(row) > 9 and row[3] == "BAL":
        balance = row[9]

        if balance in {"", "--"} and len(row) > 8:
            balance = row[8]

        return row[1], row[2], row[5], balance

    return None


def lookup_starting_equity(lines, first_trade_time):

    balances = {}

    for row in csv.reader(lines):
        parts = balance_row_parts(row)

        if parts is None:
            continue

        date_value, time_value, description, balance_value = parts
        balance_time = parse_statement_datetime(
            date_value,
            time_value,
        )

        if balance_time is None or balance_time > first_trade_time:
            continue

        account_key = balance_account_key(
            description,
            row,
        )

        balance = parse_number(balance_value)

        if account_key is None or balance is None:
            continue

        balances[account_key] = balance

    if not balances:
        raise ValueError(
            "Could not find starting account balance before first trade"
        )

    return sum(balances.values())


def trade_account_bucket(row):

    symbol = str(row.get("Symbol", "")).strip().upper()
    spread = str(row.get("Spread", "")).strip().upper()
    type_value = str(row.get("Type", "")).strip().upper()

    if symbol.startswith("BTC/") or symbol.startswith("ETH/"):
        return "crypto"

    if spread == "CRYPTO" or type_value == "CRYPTO":
        return "crypto"

    if is_futures_trade(row) or is_futures_option_symbol(symbol):
        return "futures"

    if spread == "FOREX" or type_value == "FOREX":
        return "forex"

    if (
        "/" in symbol
        and not symbol.startswith("/")
        and not symbol.startswith("SPX")
        and not symbol.startswith("XSP")
    ):
        return "forex"

    return "cash"


def extracted_trade_cash_by_day(df):

    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "account_bucket",
                "extracted_trade_count",
                "extracted_net_pnl",
            ]
        )

    working = df.copy()
    working["_trade_time"] = pd.to_datetime(
        working["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    working = working[
        working["_trade_time"].notna()
    ].copy()
    working["date"] = working["_trade_time"].dt.date.astype(str)
    working["account_bucket"] = working.apply(
        trade_account_bucket,
        axis=1,
    )
    working["net_pnl"] = (
        working["net_pnl"]
        .apply(parse_number)
        .fillna(0.0)
    )

    return (
        working.groupby(
            ["date", "account_bucket"],
            as_index=False,
        )
        .agg(
            extracted_trade_count=("net_pnl", "size"),
            extracted_net_pnl=("net_pnl", "sum"),
        )
    )


def add_trade_identity_columns(df, input_file):

    df = df.copy()

    if "statement_file" not in df.columns:
        df["statement_file"] = Path(input_file).name

    if "statement_trade_row" not in df.columns:
        df["statement_trade_row"] = range(1, len(df) + 1)

    return df


def trade_event_rows(trades):

    if trades is None or trades.empty:
        return []

    working = trades.copy()
    working["_trade_time"] = pd.to_datetime(
        working["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    working = working[
        working["_trade_time"].notna()
    ].copy()
    records = working.to_dict("records")
    events = []
    current_event = None
    previous_row = None

    for row in records:
        continuation = is_multileg_continuation(
            row,
            previous_row,
        )

        if not continuation or current_event is None:
            current_event = {
                "date": row["_trade_time"].date().isoformat(),
                "account_bucket": trade_account_bucket(row),
                "timestamp": row["_trade_time"],
                "rows": [],
            }
            events.append(current_event)

        current_event["rows"].append(row)
        previous_row = row

    for event in events:
        event["trade_pnl"] = sum(
            parse_number(row.get("trade_pnl")) or 0.0
            for row in event["rows"]
        )
        event["fees"] = sum(
            parse_number(row.get("fees")) or 0.0
            for row in event["rows"]
        )
        event["net_pnl"] = sum(
            parse_number(row.get("net_pnl")) or 0.0
            for row in event["rows"]
        )

    return events


def normalized_match_term(value):
    value = str(value).strip().upper()

    if not value or value in {"NAN", "NONE"}:
        return ""

    return value


def normalized_number_terms(value):
    number = parse_number(value)

    if number is None:
        return []

    if float(number).is_integer():
        return [str(int(number))]

    terms = [
        f"{number:g}",
        f"{number:.2f}",
    ]

    return sorted(set(terms))


def event_description_terms(event, column):

    terms = []

    for row in event.get("rows", []):
        value = normalized_match_term(row.get(column, ""))

        if value:
            terms.append(value)

    return sorted(set(terms))


def event_numeric_terms(event, column):

    terms = []

    for row in event.get("rows", []):
        terms.extend(normalized_number_terms(row.get(column, "")))

    return sorted(set(terms))


def event_futures_price_terms(event):

    terms = []

    for row in event.get("rows", []):
        spread = normalized_match_term(row.get("Spread", ""))

        if spread != "FUTURE":
            continue

        terms.extend(normalized_number_terms(row.get("Price", "")))

    return sorted(set(terms))


def description_has_term(description, term):

    if term.startswith("/"):
        term = term.split()[0]

    return bool(term and term in description)


def ledger_matches_event(ledger, event):

    ledger_time = pd.to_datetime(
        ledger.get("timestamp"),
        errors="coerce",
    )

    if pd.isna(ledger_time):
        return False

    if abs((ledger_time - event["timestamp"]).total_seconds()) > 2:
        return False

    description = str(ledger.get("description", "")).upper()
    symbol_terms = event_description_terms(event, "Symbol")
    expiration_terms = event_description_terms(event, "Exp")
    type_terms = event_description_terms(event, "Type")
    strike_terms = event_numeric_terms(event, "Strike")
    futures_price_terms = event_futures_price_terms(event)

    if symbol_terms and not any(
        description_has_term(description, term)
        for term in symbol_terms
    ):
        return False

    if strike_terms and not all(
        description_has_term(description, term)
        for term in strike_terms
    ):
        return False

    if futures_price_terms and not any(
        description_has_term(description, term)
        for term in futures_price_terms
    ):
        return False

    if strike_terms and expiration_terms and not any(
        description_has_term(description, term)
        for term in expiration_terms
    ):
        return False

    if strike_terms and type_terms and not any(
        description_has_term(description, term)
        for term in type_terms
    ):
        return False

    return bool(
        symbol_terms
        or strike_terms
        or futures_price_terms
        or expiration_terms
        or type_terms
    )


def aggregate_ledger_matches(ledger_rows):

    if len(ledger_rows) == 1:
        return ledger_rows.iloc[0]

    ledger = ledger_rows.iloc[0].copy()

    for column in [
        "amount",
        "misc_fees",
        "commissions_fees",
        "cash_flow",
    ]:
        ledger[column] = sum(
            parse_number(value) or 0.0
            for value in ledger_rows[column]
        )

    descriptions = [
        str(description)
        for description in ledger_rows["description"].tolist()
    ]
    unique_descriptions = list(dict.fromkeys(descriptions))

    ledger["description"] = " | ".join(unique_descriptions)

    return ledger


def correction_rows_for_event(
    event,
    ledger,
    event_sequence,
):

    ledger_amount = parse_number(ledger.get("amount")) or 0.0
    ledger_cash_flow = parse_number(ledger.get("cash_flow")) or 0.0
    ledger_misc_fees = parse_number(ledger.get("misc_fees")) or 0.0
    ledger_commissions_fees = (
        parse_number(ledger.get("commissions_fees")) or 0.0
    )
    corrected_event_fees = ledger_amount - ledger_cash_flow
    rows = []

    for leg_sequence, row in enumerate(event["rows"], start=1):
        first_leg = leg_sequence == 1
        original_trade_pnl = (
            parse_number(row.get("trade_pnl")) or 0.0
        )
        original_fees = parse_number(row.get("fees")) or 0.0
        original_net_pnl = parse_number(row.get("net_pnl")) or 0.0

        rows.append(
            {
                "statement_file": row.get("statement_file"),
                "statement_trade_row": row.get("statement_trade_row"),
                "date": event["date"],
                "account_bucket": event["account_bucket"],
                "event_sequence": event_sequence,
                "event_leg_sequence": leg_sequence,
                "correction_status": "cash_ledger_applied",
                "correction_source": "cash_ledger",
                "ledger_timestamp": ledger.get("timestamp"),
                "ledger_description": ledger.get("description"),
                "ledger_amount": ledger_amount,
                "ledger_cash_flow": ledger_cash_flow,
                "ledger_misc_fees": ledger_misc_fees,
                "ledger_commissions_fees": ledger_commissions_fees,
                "original_trade_pnl": original_trade_pnl,
                "original_fees": original_fees,
                "original_net_pnl": original_net_pnl,
                "corrected_trade_pnl": (
                    ledger_amount if first_leg else 0.0
                ),
                "corrected_fees": (
                    corrected_event_fees if first_leg else 0.0
                ),
                "corrected_net_pnl": (
                    ledger_cash_flow if first_leg else 0.0
                ),
            }
        )

    return rows


def build_cash_trade_corrections(
    cash_ledger,
    trades,
):

    if cash_ledger is None or cash_ledger.empty or trades is None or trades.empty:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    events = trade_event_rows(trades)
    ledger_trades = cash_review_ledger_rows(cash_ledger)

    if ledger_trades.empty:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    ledger_trades = ledger_trades[
        ledger_trades["type"] == "TRD"
    ].copy()
    event_frame = pd.DataFrame(
        [
            {
                "date": event["date"],
                "account_bucket": event["account_bucket"],
                "timestamp": event["timestamp"],
                "event": event,
                "matched_by_exact_event": False,
            }
            for event in events
        ]
    )

    if event_frame.empty:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    corrections = []
    matched_ledger_indices = set()
    event_frame = event_frame.sort_values("timestamp").copy()
    event_frame["event_sequence"] = (
        event_frame.groupby(
            ["date", "account_bucket"],
            sort=False,
        )
        .cumcount()
        .add(1)
    )

    for event_index, event_row in event_frame.iterrows():
        candidate_ledgers = ledger_trades[
            (ledger_trades["date"] == event_row["date"])
            & (
                ledger_trades["account_bucket"]
                == event_row["account_bucket"]
            )
            & ~ledger_trades.index.isin(matched_ledger_indices)
        ]
        matches = [
            ledger_index
            for ledger_index, ledger in candidate_ledgers.iterrows()
            if ledger_matches_event(ledger, event_row["event"])
        ]

        if not matches:
            continue

        ledger = aggregate_ledger_matches(
            ledger_trades.loc[matches]
        )
        matched_ledger_indices.update(matches)
        event_frame.at[event_index, "matched_by_exact_event"] = True
        corrections.extend(
            correction_rows_for_event(
                event_row["event"],
                ledger,
                int(event_row["event_sequence"]),
            )
        )

    group_keys = sorted(
        set(
            zip(
                event_frame["date"],
                event_frame["account_bucket"],
            )
        )
        & set(
            zip(
                ledger_trades["date"],
                ledger_trades["account_bucket"],
            )
        )
    )

    for date, account_bucket in group_keys:
        event_group = event_frame[
            (event_frame["date"] == date)
            & (event_frame["account_bucket"] == account_bucket)
            & ~event_frame["matched_by_exact_event"].astype(bool)
        ].sort_values("timestamp")
        ledger_group = ledger_trades[
            (ledger_trades["date"] == date)
            & (ledger_trades["account_bucket"] == account_bucket)
            & ~ledger_trades.index.isin(matched_ledger_indices)
        ].sort_values("timestamp")

        if len(event_group) != len(ledger_group):
            continue

        for event_sequence, ((_, event_row), (_, ledger)) in enumerate(
            zip(event_group.iterrows(), ledger_group.iterrows()),
            start=1,
        ):
            event = event_row["event"]
            corrections.extend(
                correction_rows_for_event(
                    event,
                    ledger,
                    event_sequence,
                )
            )

    if not corrections:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    return pd.DataFrame(corrections).reindex(
        columns=CASH_CORRECTION_COLUMNS
    )


def load_cash_trade_corrections(path):

    path = Path(path)

    if not path.exists():
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    return pd.read_csv(path)


def save_cash_trade_corrections(path, corrections):

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    corrections = (
        corrections
        if corrections is not None and not corrections.empty
        else pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)
    )
    corrections.to_csv(path, index=False)


def load_cash_reconciliation_reviews(path):

    path = Path(path)

    if not path.exists():
        return pd.DataFrame(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)

    try:
        return pd.read_csv(path).reindex(
            columns=CASH_RECONCILIATION_REVIEW_COLUMNS
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)


def save_cash_reconciliation_reviews(path, reviews):

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reviews = (
        reviews
        if reviews is not None and not reviews.empty
        else pd.DataFrame(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)
    )
    reviews = reviews.reindex(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)
    reviews.to_csv(path, index=False)


def group_review_key(row):
    def money_part(value):
        number = parse_number(value)

        if number is None:
            return ""

        return f"{number:.2f}"

    def count_part(value):
        number = parse_number(value)

        if number is None:
            return "0"

        return str(int(round(number)))

    return "|".join(
        [
            str(row.get("date", "")),
            str(row.get("account_bucket", "")),
            count_part(row.get("statement_trade_rows")),
            count_part(row.get("extracted_trade_count")),
            money_part(row.get("statement_trade_cash_flow")),
            money_part(row.get("extracted_net_pnl")),
            money_part(row.get("unreconciled_delta")),
        ]
    )


def reviewed_group_keys(reviews):
    if reviews is None or reviews.empty:
        return set()

    return {
        group_review_key(row)
        for row in reviews.to_dict("records")
        if str(row.get("review_status", "")).strip() != ""
    }


def filter_reviewed_reconciliation_groups(groups, reviews):
    if groups is None or groups.empty:
        return groups

    keys = reviewed_group_keys(reviews)

    if not keys:
        return groups

    return groups.loc[
        ~groups.apply(
            lambda row: group_review_key(row) in keys,
            axis=1,
        )
    ].copy()


def correction_key(row):
    def key_part(value):
        if pd.isna(value):
            return ""

        number = parse_number(value)

        if number is not None and float(number).is_integer():
            return str(int(number))

        return str(value)

    return "|".join(
        [
            key_part(row.get("date", "")),
            key_part(row.get("account_bucket", "")),
            key_part(row.get("event_leg_sequence", "")),
            key_part(row.get("ledger_timestamp", "")),
            key_part(row.get("ledger_description", "")),
        ]
    )


def approved_correction_keys(corrections):

    if corrections is None or corrections.empty:
        return set()

    return {
        correction_key(row)
        for row in corrections.to_dict("records")
    }


def filter_corrections_to_keys(corrections, keys):

    if corrections is None or corrections.empty or not keys:
        return pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    working = corrections.copy()
    mask = working.apply(
        lambda row: correction_key(row) in keys,
        axis=1,
    )

    return working[mask].copy().reindex(columns=CASH_CORRECTION_COLUMNS)


def apply_cash_trade_corrections(trades, corrections):

    if trades is None or trades.empty:
        return trades

    if corrections is None or corrections.empty:
        return trades

    working = trades.copy()
    if "cash_correction_applied" not in working.columns:
        working["cash_correction_applied"] = False
    else:
        working["cash_correction_applied"] = (
            working["cash_correction_applied"]
            .fillna(False)
            .astype(bool)
        )

    corrections = corrections.copy()
    corrections["statement_trade_row"] = pd.to_numeric(
        corrections["statement_trade_row"],
        errors="coerce",
    )
    working["statement_trade_row"] = pd.to_numeric(
        working["statement_trade_row"],
        errors="coerce",
    )
    correction_lookup = corrections.set_index(
        ["statement_file", "statement_trade_row"]
    )
    applied = 0

    for index, row in working.iterrows():
        key = (
            row.get("statement_file"),
            row.get("statement_trade_row"),
        )

        if key not in correction_lookup.index:
            continue

        correction = correction_lookup.loc[key]

        if isinstance(correction, pd.DataFrame):
            correction = correction.iloc[-1]

        working.at[index, "trade_pnl"] = correction["corrected_trade_pnl"]
        working.at[index, "fees"] = correction["corrected_fees"]
        working.at[index, "net_pnl"] = correction["corrected_net_pnl"]
        working.at[index, "cash_correction_applied"] = True
        working.at[index, "cash_correction_status"] = correction[
            "correction_status"
        ]
        working.at[index, "cash_correction_source"] = correction[
            "correction_source"
        ]
        working.at[index, "statement_cash_flow"] = correction[
            "ledger_cash_flow"
        ]
        applied += 1

    working["cumulative_pnl"] = (
        working["net_pnl"]
        .apply(parse_number)
        .fillna(0.0)
        .cumsum()
    )
    working.attrs["cash_corrections_applied"] = applied

    return working


def statement_trade_cash_by_day(cash_ledger):

    columns = [
        "date",
        "account_bucket",
        "statement_trade_rows",
        "statement_trade_cash_flow",
        "non_trade_cash_flow",
        "starting_balance",
        "ending_balance",
        "balance_delta",
        "balance_residual",
    ]

    if cash_ledger is None or cash_ledger.empty:
        return pd.DataFrame(columns=columns)

    rows = []

    for (date, account_bucket), group in cash_ledger.groupby(
        ["date", "account_bucket"],
    ):
        group = group.sort_values("timestamp")
        trade_rows = group[group["type"] == "TRD"]
        balance_rows = group[group["balance"].notna()]
        statement_trade_cash_flow = float(trade_rows["cash_flow"].sum())
        non_trade_cash_flow = float(
            group.loc[
                ~group["type"].isin(["BAL", "TRD"]),
                "cash_flow",
            ].sum()
        )
        starting_balance = (
            float(balance_rows["balance"].iloc[0])
            if not balance_rows.empty
            else 0.0
        )
        ending_balance = (
            float(balance_rows["balance"].iloc[-1])
            if not balance_rows.empty
            else 0.0
        )
        balance_delta = ending_balance - starting_balance
        balance_residual = (
            balance_delta
            - statement_trade_cash_flow
            - non_trade_cash_flow
        )

        rows.append(
            {
                "date": date,
                "account_bucket": account_bucket,
                "statement_trade_rows": len(trade_rows),
                "statement_trade_cash_flow": statement_trade_cash_flow,
                "non_trade_cash_flow": non_trade_cash_flow,
                "starting_balance": starting_balance,
                "ending_balance": ending_balance,
                "balance_delta": balance_delta,
                "balance_residual": balance_residual,
            }
        )

    return pd.DataFrame(rows, columns=columns)


def reconcile_cash_balances(
    lines,
    trades,
    tolerance=1.0,
    start_date=None,
    end_date=None,
):

    cash_ledger = parse_cash_ledger(lines)
    cash_ledger = filter_cash_ledger_by_date(
        cash_ledger,
        start_date,
        end_date,
    )
    statement = statement_trade_cash_by_day(cash_ledger)
    extracted = extracted_trade_cash_by_day(trades)

    available_buckets = (
        set(cash_ledger["account_bucket"])
        if cash_ledger is not None and not cash_ledger.empty
        else set()
    )
    extracted = extracted[
        extracted["account_bucket"].isin(available_buckets)
    ].copy()
    reconciliation = pd.merge(
        statement,
        extracted,
        on=["date", "account_bucket"],
        how="outer",
    )

    if reconciliation.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "account_bucket",
                "status",
                "extracted_trade_count",
                "statement_trade_rows",
                "statement_trade_cash_flow",
                "extracted_net_pnl",
                "unreconciled_delta",
                "tolerance",
            ]
        )

    numeric_columns = [
        "statement_trade_rows",
        "statement_trade_cash_flow",
        "non_trade_cash_flow",
        "starting_balance",
        "ending_balance",
        "balance_delta",
        "balance_residual",
        "extracted_trade_count",
        "extracted_net_pnl",
    ]

    for column in numeric_columns:
        if column not in reconciliation.columns:
            reconciliation[column] = 0.0

        reconciliation[column] = reconciliation[column].fillna(0.0)

    reconciliation["unreconciled_delta"] = (
        reconciliation["extracted_net_pnl"]
        - reconciliation["statement_trade_cash_flow"]
    )
    reconciliation["tolerance"] = tolerance
    reconciliation["status"] = reconciliation["unreconciled_delta"].apply(
        lambda value: (
            "reconciled"
            if abs(value) <= tolerance
            else "unreconciled"
        )
    )

    return reconciliation.sort_values(
        ["status", "date", "account_bucket"],
        ascending=[False, True, True],
    ).reset_index(drop=True)


def summarize_cash_reconciliation(reconciliation):

    if reconciliation is None or reconciliation.empty:
        return pd.DataFrame(
            [
                {
                    "groups": 0,
                    "unreconciled_groups": 0,
                    "reviewed_unreconciled_groups": 0,
                    "unreviewed_unreconciled_groups": 0,
                    "trades_in_unreconciled_groups": 0,
                    "trades_in_unreviewed_unreconciled_groups": 0,
                    "max_abs_unreconciled_delta": 0.0,
                }
            ]
        )

    unreconciled = reconciliation[
        reconciliation["status"] == "unreconciled"
    ]
    review_status = (
        unreconciled["review_status"]
        if "review_status" in unreconciled.columns
        else pd.Series("", index=unreconciled.index)
    )
    reviewed = unreconciled[
        review_status.astype(str).str.strip() != ""
    ]
    unreviewed = unreconciled[
        review_status.astype(str).str.strip() == ""
    ]

    return pd.DataFrame(
        [
            {
                "groups": len(reconciliation),
                "unreconciled_groups": len(unreconciled),
                "reviewed_unreconciled_groups": len(reviewed),
                "unreviewed_unreconciled_groups": len(unreviewed),
                "trades_in_unreconciled_groups": int(
                    unreconciled["extracted_trade_count"].sum()
                ),
                "trades_in_unreviewed_unreconciled_groups": int(
                    unreviewed["extracted_trade_count"].sum()
                ),
                "max_abs_unreconciled_delta": float(
                    reconciliation["unreconciled_delta"].abs().max()
                ),
            }
        ]
    )


def annotate_cash_reconciliation_reviews(reconciliation, reviews):
    if reconciliation is None or reconciliation.empty:
        return reconciliation

    working = reconciliation.copy()
    working["review_status"] = ""
    working["review_state"] = working["status"]

    keys = reviewed_group_keys(reviews)
    if keys:
        reviewed_mask = working.apply(
            lambda row: group_review_key(row) in keys,
            axis=1,
        )
        reviewed_mask = reviewed_mask & (
            working["status"].astype(str) == "unreconciled"
        )
        working.loc[reviewed_mask, "review_status"] = "reviewed"
        working.loc[reviewed_mask, "review_state"] = "reviewed_unreconciled"

    unreviewed_mask = (
        working["status"].astype(str).eq("unreconciled")
        & working["review_status"].astype(str).str.strip().eq("")
    )
    working.loc[unreviewed_mask, "review_state"] = "unreviewed_unreconciled"
    working["strict_reconciliation_failure"] = unreviewed_mask

    return working


def strict_reconciliation_failures(reconciliation):
    if reconciliation is None or reconciliation.empty:
        return pd.DataFrame(columns=[])

    if "strict_reconciliation_failure" not in reconciliation.columns:
        return reconciliation[
            reconciliation["status"].astype(str).eq("unreconciled")
        ].copy()

    return reconciliation[
        reconciliation["strict_reconciliation_failure"].astype(bool)
    ].copy()


def json_safe_records(frame):

    if frame is None or frame.empty:
        return []

    safe = frame.copy()

    for column in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[column]):
            safe[column] = safe[column].dt.strftime("%Y-%m-%d %H:%M:%S")

    safe = safe.astype(object).where(pd.notna(safe), None)

    return safe.to_dict(orient="records")


def add_review_sequence(frame, time_column):

    if frame is None or frame.empty:
        return frame

    working = frame.copy()
    working["_review_time"] = pd.to_datetime(
        working[time_column],
        errors="coerce",
    )
    working = working.sort_values(
        ["date", "account_bucket", "_review_time"],
        kind="mergesort",
    )
    working["review_sequence"] = (
        working.groupby(["date", "account_bucket"]).cumcount() + 1
    )

    return working.drop(columns=["_review_time"])


def cash_review_trade_rows(trades):

    if trades is None or trades.empty:
        return pd.DataFrame(columns=[])

    working = trades.copy()
    working["_trade_time"] = pd.to_datetime(
        working["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    working = working[
        working["_trade_time"].notna()
    ].copy()
    working["date"] = working["_trade_time"].dt.date.astype(str)
    working["account_bucket"] = working.apply(
        trade_account_bucket,
        axis=1,
    )
    working = add_review_sequence(
        working,
        "_trade_time",
    )

    columns = [
        "date",
        "account_bucket",
        "statement_file",
        "statement_trade_row",
        "review_sequence",
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
        "fees",
        "trade_pnl",
        "net_pnl",
        "cash_correction_applied",
        "Order ID",
    ]

    return working[
        [column for column in columns if column in working.columns]
    ].copy()


def cash_review_ledger_rows(cash_ledger):

    if cash_ledger is None or cash_ledger.empty:
        return pd.DataFrame(columns=[])

    working = cash_ledger.copy()
    working = add_review_sequence(
        working,
        "timestamp",
    )
    working["timestamp"] = pd.to_datetime(
        working["timestamp"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %H:%M:%S")

    columns = [
        "date",
        "account_bucket",
        "review_sequence",
        "timestamp",
        "type",
        "description",
        "amount",
        "misc_fees",
        "commissions_fees",
        "cash_flow",
        "balance",
    ]

    return working[columns].copy()


def build_fee_correction_suggestions(
    cash_ledger,
    trades,
    tolerance=1.0,
):

    trade_rows = cash_review_trade_rows(trades)
    ledger_rows = cash_review_ledger_rows(cash_ledger)

    if trade_rows.empty or ledger_rows.empty:
        return pd.DataFrame(columns=[])

    ledger_trade_rows = ledger_rows[
        ledger_rows["type"] == "TRD"
    ].copy()
    suggestions = []
    group_keys = sorted(
        set(
            zip(
                trade_rows["date"],
                trade_rows["account_bucket"],
            )
        )
        & set(
            zip(
                ledger_trade_rows["date"],
                ledger_trade_rows["account_bucket"],
            )
        )
    )

    for date, account_bucket in group_keys:
        extracted_group = trade_rows[
            (trade_rows["date"] == date)
            & (trade_rows["account_bucket"] == account_bucket)
        ].sort_values("review_sequence")
        ledger_group = ledger_trade_rows[
            (ledger_trade_rows["date"] == date)
            & (ledger_trade_rows["account_bucket"] == account_bucket)
        ].sort_values("review_sequence")

        if len(extracted_group) != len(ledger_group):
            continue

        for (_, trade), (_, ledger) in zip(
            extracted_group.iterrows(),
            ledger_group.iterrows(),
        ):
            trade_pnl = parse_number(trade.get("trade_pnl")) or 0.0
            current_fees = parse_number(trade.get("fees")) or 0.0
            current_net_pnl = parse_number(trade.get("net_pnl")) or 0.0
            statement_amount = parse_number(ledger.get("amount")) or 0.0
            statement_cash_flow = (
                parse_number(ledger.get("cash_flow")) or 0.0
            )
            broker_implied_fees = trade_pnl - statement_cash_flow
            suggested_net_pnl = statement_cash_flow
            fee_adjustment = broker_implied_fees - current_fees
            net_pnl_adjustment = suggested_net_pnl - current_net_pnl
            amount_delta = trade_pnl - statement_amount

            if abs(amount_delta) > tolerance:
                suggestion_status = "review_amount_mismatch"
                safe_to_apply = False
            elif abs(net_pnl_adjustment) <= tolerance:
                suggestion_status = "already_reconciled"
                safe_to_apply = False
            else:
                suggestion_status = "fee_only"
                safe_to_apply = True

            if suggestion_status == "already_reconciled":
                continue

            suggestions.append(
                {
                    "date": date,
                    "account_bucket": account_bucket,
                    "review_sequence": int(trade["review_sequence"]),
                    "suggestion_status": suggestion_status,
                    "safe_to_apply": safe_to_apply,
                    "exec_time": trade.get("Exec Time"),
                    "symbol": trade.get("Symbol"),
                    "side": trade.get("Side"),
                    "qty": trade.get("Qty"),
                    "order_id": trade.get("Order ID"),
                    "ledger_timestamp": ledger.get("timestamp"),
                    "ledger_description": ledger.get("description"),
                    "trade_pnl": trade_pnl,
                    "statement_amount": statement_amount,
                    "amount_delta": amount_delta,
                    "current_fees": current_fees,
                    "broker_implied_fees": broker_implied_fees,
                    "fee_adjustment": fee_adjustment,
                    "current_net_pnl": current_net_pnl,
                    "suggested_net_pnl": suggested_net_pnl,
                    "net_pnl_adjustment": net_pnl_adjustment,
                    "statement_misc_fees": (
                        parse_number(ledger.get("misc_fees")) or 0.0
                    ),
                    "statement_commissions_fees": (
                        parse_number(ledger.get("commissions_fees")) or 0.0
                    ),
                    "statement_cash_flow": statement_cash_flow,
                }
            )

    return pd.DataFrame(suggestions)


def merge_fee_suggestions_into_trade_rows(trade_rows, suggestions):

    if trade_rows is None or trade_rows.empty:
        return trade_rows

    working = trade_rows.copy()

    if suggestions is None or suggestions.empty:
        return working

    columns = [
        "date",
        "account_bucket",
        "review_sequence",
        "suggestion_status",
        "safe_to_apply",
        "broker_implied_fees",
        "fee_adjustment",
        "suggested_net_pnl",
        "net_pnl_adjustment",
        "statement_cash_flow",
    ]

    return pd.merge(
        working,
        suggestions[columns],
        on=["date", "account_bucket", "review_sequence"],
        how="left",
    )


def write_cash_reconciliation_dashboard(
    output_file,
    reconciliation,
    cash_ledger,
    trades,
    fee_suggestions=None,
    correction_candidates=None,
    approved_corrections=None,
    reviewed_groups=None,
    server_enabled=False,
):

    dashboard_path = str(output_file)
    failed = reconciliation[
        reconciliation["status"] == "unreconciled"
    ].copy()
    failed["abs_delta"] = failed["unreconciled_delta"].abs()
    failed = failed.sort_values(
        ["abs_delta", "date", "account_bucket"],
        ascending=[False, True, True],
    )
    approved_keys = approved_correction_keys(approved_corrections)
    reviewed_keys = reviewed_group_keys(reviewed_groups)
    failed = filter_reviewed_reconciliation_groups(
        failed,
        reviewed_groups,
    )

    if correction_candidates is not None and not correction_candidates.empty:
        visible_groups = []

        for _, group in failed.iterrows():
            group_candidates = correction_candidates[
                (correction_candidates["date"].astype(str) == str(group["date"]))
                & (
                    correction_candidates["account_bucket"].astype(str)
                    == str(group["account_bucket"])
                )
            ]

            if (
                not group_candidates.empty
                and all(
                    correction_key(candidate) in approved_keys
                    for candidate in group_candidates.to_dict("records")
                )
            ):
                continue

            visible_groups.append(group)

        failed = (
            pd.DataFrame(visible_groups)
            if visible_groups
            else failed.iloc[0:0].copy()
        )

    payload = {
        "groups": json_safe_records(failed),
        "trades": json_safe_records(
            merge_fee_suggestions_into_trade_rows(
                cash_review_trade_rows(trades),
                fee_suggestions,
            )
        ),
        "ledger": json_safe_records(cash_review_ledger_rows(cash_ledger)),
        "feeSuggestions": json_safe_records(fee_suggestions),
        "correctionCandidates": json_safe_records(correction_candidates),
        "approvedCorrectionKeys": list(approved_keys),
        "reviewedGroupKeys": list(reviewed_keys),
        "serverEnabled": server_enabled,
    }
    payload_json = json.dumps(payload, allow_nan=False)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cash Reconciliation Review</title>
  <style>
    :root {{
      --bg: #f6f7f8;
      --panel: #ffffff;
      --ink: #202936;
      --muted: #667085;
      --line: #d8dde4;
      --bad: #b42318;
      --good: #146c43;
      --accent: #245b73;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      padding: 18px 24px;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; letter-spacing: 0; }}
    main {{ padding: 18px 24px 28px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
    }}
    .panel-head {{
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfbfc;
    }}
    .group-list {{
      max-height: calc(100vh - 155px);
      overflow: auto;
    }}
    .group-button {{
      display: block;
      width: 100%;
      padding: 10px 12px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      text-align: left;
      cursor: pointer;
      font-size: 13px;
    }}
    .group-button.active {{ background: #e8f1f4; }}
    .group-button.approved {{ background: #ecfdf3; border-left: 6px solid var(--good); }}
    .group-button.partial {{ background: #fff8e6; border-left: 6px solid #b7791f; }}
    .delta {{ color: var(--bad); font-weight: 700; }}
    .badge {{
      display: inline-block;
      margin-top: 5px;
      padding: 3px 7px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
    }}
    .badge.approved {{ background: #d1fadf; color: var(--good); }}
    .badge.partial {{ background: #fef0c7; color: #8a4b00; }}
    .split {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px;
      background: #fff;
    }}
    .metric-label {{ color: var(--muted); font-size: 12px; }}
    .metric-value {{ margin-top: 4px; font-size: 17px; font-weight: 700; }}
    .toolbar {{ padding: 12px; display: flex; gap: 8px; flex-wrap: wrap; border-bottom: 1px solid var(--line); }}
    button {{ border: 1px solid var(--accent); border-radius: 6px; padding: 8px 10px; background: var(--accent); color: #fff; cursor: pointer; }}
    button.secondary {{ background: #fff; color: var(--ink); border-color: var(--line); }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}
    .approval-status {{
      margin: 0 12px 12px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      font-size: 13px;
      color: var(--muted);
    }}
    .approval-status.saved {{
      border-color: #75c98b;
      background: #ecfdf3;
      color: var(--good);
      font-weight: 700;
    }}
    .approval-status.error {{
      border-color: #f2a19a;
      background: #fff1f0;
      color: var(--bad);
      font-weight: 700;
    }}
    .table-wrap {{ overflow: auto; max-height: 60vh; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 760px; }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 7px 8px;
      text-align: right;
      white-space: nowrap;
      font-size: 12px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #eef1f4;
      z-index: 1;
    }}
    td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) {{
      text-align: left;
    }}
    @media (max-width: 980px) {{
      .layout, .split {{ grid-template-columns: 1fr; }}
      .group-list {{ max-height: 300px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Cash Reconciliation Review</h1>
    <div class="subtle">Manual review of extracted trade history against statement cash ledger rows. No corrections are applied from this dashboard.</div>
  </header>
  <main>
    <div class="layout">
      <aside class="panel">
        <div class="panel-head">
          <h2>Unreconciled Groups</h2>
          <div class="subtle" id="group-count"></div>
        </div>
        <div class="group-list" id="groups"></div>
      </aside>
      <section class="panel">
        <div class="panel-head">
          <h2 id="selected-title">Select a group</h2>
          <div class="subtle" id="selected-subtitle"></div>
        </div>
        <div class="metrics" id="metrics"></div>
        <div class="toolbar">
          <button id="approve-group">Approve group corrections</button>
          <button class="secondary" id="clear-group">Clear group approvals</button>
          <button id="save-approvals">Save approved corrections</button>
          <button class="secondary" id="download-approvals">Download approved corrections</button>
        </div>
        <div class="approval-status" id="approval-status">Approval changes have not been saved in this dashboard session.</div>
        <div class="split">
          <div>
            <div class="panel-head"><h2>Extracted Trade History</h2></div>
            <div class="table-wrap"><table id="trades-table"></table></div>
          </div>
          <div>
            <div class="panel-head"><h2>Statement Cash Ledger</h2></div>
            <div class="table-wrap"><table id="ledger-table"></table></div>
          </div>
        </div>
      </section>
    </div>
  </main>
  <script>
    const DATA = {payload_json};
    let selectedIndex = 0;
    const approved = new Set(DATA.approvedCorrectionKeys || []);
    const saved = new Set(DATA.approvedCorrectionKeys || []);
    const reviewedGroups = new Set(DATA.reviewedGroupKeys || []);
    const savedReviewedGroups = new Set(DATA.reviewedGroupKeys || []);

    function money(value) {{
      const number = Number(value || 0);
      return number.toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    }}
    function keyFor(row) {{ return `${{row.date}}|${{row.account_bucket}}`; }}
    function rowsFor(rows, group) {{
      const key = keyFor(group);
      return rows.filter(row => keyFor(row) === key);
    }}
    function correctionKey(row) {{
      return [
        row.date,
        row.account_bucket,
        row.event_sequence,
        row.event_leg_sequence,
        row.ledger_timestamp,
        row.ledger_description,
      ].map(value => {{
        if (value === null || value === undefined) return '';
        const number = Number(value);
        if (Number.isFinite(number) && Number.isInteger(number)) return String(number);
        return String(value);
      }}).join('|');
    }}
    function groupReviewKey(row) {{
      const moneyPart = value => {{
        const number = Number(value);
        return Number.isFinite(number) ? number.toFixed(2) : '';
      }};
      const countPart = value => {{
        const number = Number(value);
        return Number.isFinite(number) ? String(Math.round(number)) : '0';
      }};
      return [
        row.date,
        row.account_bucket,
        countPart(row.statement_trade_rows),
        countPart(row.extracted_trade_count),
        moneyPart(row.statement_trade_cash_flow),
        moneyPart(row.extracted_net_pnl),
        moneyPart(row.unreconciled_delta),
      ].map(value => value === null || value === undefined ? '' : String(value)).join('|');
    }}
    function reviewedGroupRows() {{
      return (DATA.groups || []).filter(group => reviewedGroups.has(groupReviewKey(group))).map(group => ({{
        date: group.date,
        account_bucket: group.account_bucket,
        statement_trade_rows: group.statement_trade_rows,
        extracted_trade_count: group.extracted_trade_count,
        statement_trade_cash_flow: group.statement_trade_cash_flow,
        extracted_net_pnl: group.extracted_net_pnl,
        unreconciled_delta: group.unreconciled_delta,
        review_status: 'reviewed_no_auto_correction',
      }}));
    }}
    function groupCandidateRows(group) {{ return rowsFor(DATA.correctionCandidates || [], group); }}
    function approvedRows() {{
      return (DATA.correctionCandidates || []).filter(row => approved.has(correctionKey(row)));
    }}
    function groupApprovalState(group) {{
      const rows = groupCandidateRows(group);
      const approvedCount = rows.filter(row => approved.has(correctionKey(row))).length;
      if (!rows.length) {{
        const reviewed = reviewedGroups.has(groupReviewKey(group));
        return {{ klass: reviewed ? 'approved' : '', label: reviewed ? 'Reviewed' : 'No auto corrections', approvedCount: reviewed ? 1 : 0, total: 1 }};
      }}
      if (approvedCount === 0) return {{ klass: '', label: 'Needs approval', approvedCount, total: rows.length }};
      if (approvedCount === rows.length) {{
        const savedCount = rows.filter(row => saved.has(correctionKey(row))).length;
        return {{ klass: 'approved', label: savedCount === rows.length ? 'Saved' : 'Approved', approvedCount, total: rows.length }};
      }}
      return {{ klass: 'partial', label: 'Partial', approvedCount, total: rows.length }};
    }}
    function renderTable(id, rows, columns) {{
      const table = document.getElementById(id);
      if (!rows.length) {{
        table.innerHTML = '<tbody><tr><td>No rows.</td></tr></tbody>';
        return;
      }}
      table.innerHTML = `<thead><tr>${{columns.map(col => `<th>${{col}}</th>`).join('')}}</tr></thead><tbody>` +
        rows.map(row => `<tr>${{columns.map(col => `<td>${{row[col] ?? ''}}</td>`).join('')}}</tr>`).join('') +
        '</tbody>';
    }}
    function renderGroups() {{
      document.getElementById('group-count').textContent = `${{DATA.groups.length}} groups need review`;
      document.getElementById('groups').innerHTML = DATA.groups.map((group, index) => {{
        const active = index === selectedIndex ? 'active' : '';
        const approval = groupApprovalState(group);
        const badge = approval.klass ? `<span class="badge ${{approval.klass}}">${{approval.label}} · ${{approval.approvedCount}}/${{approval.total}}</span>` : `<span class="badge partial">${{approval.label}}</span>`;
        return `<button class="group-button ${{active}} ${{approval.klass}}" data-index="${{index}}">
          <strong>${{group.date}} · ${{group.account_bucket}}</strong><br>
          <span class="delta">Delta ${{money(group.unreconciled_delta)}}</span><br>
          <span class="subtle">${{group.extracted_trade_count}} extracted trades · ${{group.statement_trade_rows}} ledger trades</span><br>
          ${{badge}}
        </button>`;
      }}).join('');
      document.querySelectorAll('.group-button').forEach(button => {{
        button.addEventListener('click', event => {{
          selectedIndex = Number(event.currentTarget.dataset.index);
          render();
        }});
      }});
    }}
    function renderSelected() {{
      const group = DATA.groups[selectedIndex];
      const approveButton = document.getElementById('approve-group');
      const clearButton = document.getElementById('clear-group');
      if (!group) {{
        document.getElementById('selected-title').textContent = 'No unreconciled groups';
        document.getElementById('selected-subtitle').textContent = '';
        document.getElementById('metrics').innerHTML = '';
        approveButton.disabled = true;
        clearButton.disabled = true;
        renderTable('trades-table', [], []);
        renderTable('ledger-table', [], []);
        return;
      }}
      const candidateRows = groupCandidateRows(group);
      const hasCandidates = candidateRows.length > 0;
      approveButton.disabled = false;
      clearButton.disabled = !hasCandidates && !reviewedGroups.has(groupReviewKey(group));
      approveButton.textContent = hasCandidates ? 'Approve group corrections' : 'Mark group reviewed';
      clearButton.textContent = hasCandidates ? 'Clear group approvals' : 'Clear reviewed mark';
      document.getElementById('selected-title').textContent = `${{group.date}} · ${{group.account_bucket}}`;
      document.getElementById('selected-subtitle').textContent = hasCandidates
        ? 'Review whether extracted trade rows should be changed to match broker cash flow.'
        : 'No automatic correction candidates are available for this group. This usually means the number of extracted trade events does not match the number of statement ledger trade rows, so manual review is needed.';
      const metrics = [
        ['Extracted net PnL', money(group.extracted_net_pnl)],
        ['Statement trade cash flow', money(group.statement_trade_cash_flow)],
        ['Suggested delta', money(group.unreconciled_delta)],
        ['Balance residual', money(group.balance_residual)],
        ['Fee suggestions', rowsFor(DATA.feeSuggestions || [], group).filter(row => row.safe_to_apply).length],
        ['Auto correction candidates', candidateRows.length],
        ['Approved corrections', candidateRows.filter(row => approved.has(correctionKey(row))).length],
        ['Reviewed group', reviewedGroups.has(groupReviewKey(group)) ? 'Yes' : 'No'],
      ];
      document.getElementById('metrics').innerHTML = metrics.map(([label, value]) =>
        `<div class="metric"><div class="metric-label">${{label}}</div><div class="metric-value">${{value}}</div></div>`
      ).join('');
      renderTable(
        'trades-table',
        rowsFor(DATA.trades, group),
        ['review_sequence','statement_trade_row','Exec Time','Spread','Side','Qty','Pos Effect','Symbol','Type','Price','Net Price','fees','broker_implied_fees','fee_adjustment','trade_pnl','net_pnl','suggested_net_pnl','net_pnl_adjustment','suggestion_status','cash_correction_applied','Order ID']
      );
      renderTable(
        'ledger-table',
        rowsFor(DATA.ledger, group).filter(row => row.type === 'TRD'),
        ['timestamp','type','description','amount','misc_fees','commissions_fees','cash_flow','balance']
      );
    }}
    function render() {{
      renderGroups();
      renderSelected();
    }}
    function csvEscape(value) {{
      const text = value === null || value === undefined ? '' : String(value);
      return /[",\\n]/.test(text) ? `"${{text.replaceAll('"', '""')}}"` : text;
    }}
    function approvalCsv(rows) {{
      const columns = ['statement_file','statement_trade_row','date','account_bucket','event_sequence','event_leg_sequence','correction_status','correction_source','ledger_timestamp','ledger_description','ledger_amount','ledger_cash_flow','ledger_misc_fees','ledger_commissions_fees','original_trade_pnl','original_fees','original_net_pnl','corrected_trade_pnl','corrected_fees','corrected_net_pnl'];
      return [columns.join(',')].concat(rows.map(row => columns.map(col => csvEscape(row[col])).join(','))).join('\\n');
    }}
    function approveCurrentGroup() {{
      const group = DATA.groups[selectedIndex];
      const rows = groupCandidateRows(group);
      const status = document.getElementById('approval-status');
      if (!rows.length) {{
        reviewedGroups.add(groupReviewKey(group));
        status.className = 'approval-status';
        status.textContent = 'Group marked reviewed. Save approvals to make this permanent.';
        render();
        return;
      }}
      rows.forEach(row => approved.add(correctionKey(row)));
      status.className = 'approval-status';
      status.textContent = `${{approved.size}} correction rows approved. Save approvals to make them permanent.`;
      render();
    }}
    function clearCurrentGroup() {{
      const group = DATA.groups[selectedIndex];
      const rows = groupCandidateRows(group);
      const status = document.getElementById('approval-status');
      if (!rows.length) {{
        reviewedGroups.delete(groupReviewKey(group));
        status.className = 'approval-status';
        status.textContent = 'Reviewed mark cleared for this group.';
        render();
        return;
      }}
      rows.forEach(row => approved.delete(correctionKey(row)));
      status.className = 'approval-status';
      status.textContent = `${{approved.size}} correction rows approved. Save approvals to make them permanent.`;
      render();
    }}
    function downloadApprovals() {{
      const blob = new Blob([approvalCsv(approvedRows())], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'cash_trade_corrections.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    async function saveApprovals() {{
      const rows = approvedRows();
      const reviewedRows = reviewedGroupRows();
      const status = document.getElementById('approval-status');
      if (!DATA.serverEnabled) {{
        status.className = 'approval-status error';
        status.textContent = 'Static dashboard mode: download approved corrections, or rerun with --serve-cash-dashboard to save directly.';
        return;
      }}
      const response = await fetch('/api/save-approved-corrections', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ corrections: rows, reviewed_groups: reviewedRows }}),
      }});
      const payload = await response.json();
      if (!response.ok) {{
        status.className = 'approval-status error';
        status.textContent = `Save failed: ${{payload.error || response.statusText}}`;
        return;
      }}
      approvedRows().forEach(row => saved.add(correctionKey(row)));
      reviewedGroupRows().forEach(row => savedReviewedGroups.add(groupReviewKey(row)));
      status.className = 'approval-status saved';
      status.textContent = `Saved ${{payload.saved_rows}} approved correction rows and ${{payload.saved_reviewed_groups}} reviewed groups. Future runs will apply or hide these reviews.`;
      render();
    }}
    document.getElementById('approve-group').addEventListener('click', approveCurrentGroup);
    document.getElementById('clear-group').addEventListener('click', clearCurrentGroup);
    document.getElementById('download-approvals').addEventListener('click', downloadApprovals);
    document.getElementById('save-approvals').addEventListener('click', saveApprovals);
    render();
  </script>
</body>
</html>"""

    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(html)

    return dashboard_path


class CashDashboardHandler(BaseHTTPRequestHandler):

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
        path = self.path.split("?", 1)[0]

        if path in {"/", f"/{CASH_DASHBOARD_FILE_NAME}"}:
            file_path = self.server.dashboard_path
            content_type = "text/html; charset=utf-8"
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
        if self.path != "/api/save-approved-corrections":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            corrections = pd.DataFrame(
                payload.get("corrections", []),
            ).reindex(columns=CASH_CORRECTION_COLUMNS)
            reviewed_groups = pd.DataFrame(
                payload.get("reviewed_groups", []),
            ).reindex(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)
            existing_corrections = load_cash_trade_corrections(
                self.server.approved_corrections_path
            )
            existing_reviews = load_cash_reconciliation_reviews(
                self.server.reviewed_groups_path
            )
            corrections = combine_cash_trade_corrections(
                existing_corrections,
                corrections,
            )
            reviewed_groups = pd.concat(
                [
                    existing_reviews,
                    reviewed_groups,
                ],
                ignore_index=True,
                sort=False,
            ).reindex(columns=CASH_RECONCILIATION_REVIEW_COLUMNS)
            if not reviewed_groups.empty:
                reviewed_groups["_review_key"] = reviewed_groups.apply(
                    group_review_key,
                    axis=1,
                )
                reviewed_groups = reviewed_groups.drop_duplicates(
                    subset=["_review_key"],
                    keep="last",
                ).drop(columns=["_review_key"])
            save_cash_trade_corrections(
                self.server.approved_corrections_path,
                corrections,
            )
            save_cash_reconciliation_reviews(
                self.server.reviewed_groups_path,
                reviewed_groups,
            )
            self.send_json(
                200,
                {
                    "saved_rows": len(corrections),
                    "path": str(self.server.approved_corrections_path),
                    "saved_reviewed_groups": len(reviewed_groups),
                    "reviewed_groups_path": str(self.server.reviewed_groups_path),
                },
            )
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_cash_dashboard(
    dashboard_path,
    approved_corrections_path,
    reviewed_groups_path,
    host="127.0.0.1",
    port=8766,
    open_browser=True,
):

    dashboard_path = Path(dashboard_path)
    server = HTTPServer((host, port), CashDashboardHandler)
    server.dashboard_path = dashboard_path
    server.approved_corrections_path = Path(approved_corrections_path)
    server.reviewed_groups_path = Path(reviewed_groups_path)
    url = f"http://{host}:{server.server_port}/{CASH_DASHBOARD_FILE_NAME}"

    if open_browser:
        webbrowser.open(url)

    print(f"Serving cash reconciliation dashboard at {url}")
    print("Press Ctrl+C to stop the dashboard server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped cash reconciliation dashboard server.")
    finally:
        server.server_close()


def save_pnl_chart(df, output_file=PNL_PLOT_FILE):

    plot_df = df.copy()

    plot_df["plot_time"] = pd.to_datetime(
        plot_df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    plot_df = plot_df[
        plot_df["plot_time"].notna()
    ].copy()

    if plot_df.empty:
        return

    fig, ax = plt.subplots(
        figsize=(12, 6)
    )

    colors = plot_df["net_pnl"].apply(
        lambda value: "#2e7d32" if value >= 0 else "#c62828"
    )

    ax.bar(
        plot_df["plot_time"],
        plot_df["net_pnl"],
        color=colors,
        alpha=0.35,
        label="Net PnL per trade",
    )

    ax.plot(
        plot_df["plot_time"],
        plot_df["cumulative_pnl"],
        color="#1565c0",
        linewidth=2,
        label="Cumulative net PnL",
    )

    ax2 = ax.twinx()

    ax2.plot(
        plot_df["plot_time"],
        plot_df["cumulative_log_return"],
        color="#6a1b9a",
        linewidth=1.5,
        linestyle="--",
        label="Cumulative log return",
    )

    ax2.set_ylabel("Cumulative log return")

    ax.axhline(
        0,
        color="#444444",
        linewidth=0.8,
    )

    ax.set_title("Trade PnL")
    ax.set_xlabel("Trade time")
    ax.set_ylabel("PnL ($)")
    handles, labels = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(
        handles + handles2,
        labels + labels2,
    )
    ax.grid(
        True,
        axis="y",
        alpha=0.25,
    )

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(
        output_file,
        dpi=150,
    )
    plt.close(fig)


def fill_missing_execution_times(df):

    if "Exec Time" not in df.columns:
        return df

    df = df.copy()
    df["Exec Time"] = (
        df["Exec Time"]
        .replace("", pd.NA)
        .ffill()
    )

    return df


def get_master_starting_equity(master_file, fallback_starting_equity):

    if not os.path.exists(master_file):
        return fallback_starting_equity

    existing = pd.read_csv(master_file)

    if "starting_equity" not in existing.columns:
        return fallback_starting_equity

    existing_starting_equity = (
        existing["starting_equity"]
        .apply(parse_number)
        .dropna()
    )

    if existing_starting_equity.empty:
        return fallback_starting_equity

    return existing_starting_equity.iloc[0]


def get_dedupe_columns(df):

    dedupe_columns = [
        column
        for column in PREFERRED_DEDUPE_COLUMNS
        if column in df.columns
    ]

    if dedupe_columns:
        return dedupe_columns

    return [
        column
        for column in df.columns
        if column not in CALCULATED_COLUMNS
    ]


def normalized_dedupe_value(column, value):

    if pd.isna(value):
        return ""

    if column in NUMERIC_DEDUPE_COLUMNS:
        number = parse_number(value)

        if number is not None:
            return f"{number:g}"

    return str(value).strip()


def build_dedupe_key(df, dedupe_columns):

    return df.apply(
        lambda row: "|".join(
            normalized_dedupe_value(
                column,
                row.get(column),
            )
            for column in dedupe_columns
        ),
        axis=1,
    )


def has_statement_identity(df):
    if not all(column in df.columns for column in STATEMENT_IDENTITY_COLUMNS):
        return pd.Series(False, index=df.index)

    if "statement_file" not in df.columns:
        return pd.Series(False, index=df.index)

    return (
        df["statement_file"].notna()
        & (df["statement_file"].astype(str).str.strip() != "")
        & df["statement_trade_row"].notna()
        & (df["statement_trade_row"].astype(str).str.strip() != "")
    )


def drop_legacy_blank_identity_duplicates(df, new_trades):
    if not all(
        column in df.columns
        for column in STATEMENT_IDENTITY_COLUMNS
    ):
        return df

    if not all(
        column in new_trades.columns
        for column in STATEMENT_IDENTITY_COLUMNS
    ):
        return df

    new_identity = has_statement_identity(new_trades)

    if not new_identity.any():
        return df

    base_dedupe_columns = [
        column
        for column in get_dedupe_columns(df)
        if column not in STATEMENT_IDENTITY_COLUMNS
    ]

    if not base_dedupe_columns:
        return df

    new_identity_keys = set(
        build_dedupe_key(
            new_trades.loc[new_identity],
            base_dedupe_columns,
        )
    )
    df = df.copy()
    df["_dedupe_no_statement_identity"] = build_dedupe_key(
        df,
        base_dedupe_columns,
    )
    blank_identity = ~has_statement_identity(df)
    keep_rows = ~(
        blank_identity
        & df["_dedupe_no_statement_identity"].isin(new_identity_keys)
    )

    return df.loc[
        keep_rows
    ].drop(
        columns=["_dedupe_no_statement_identity"],
    )


def preserve_existing_strategy_names(existing_trades, new_trades, dedupe_columns):
    if (
        "Strategy_Name" not in existing_trades.columns
        or not dedupe_columns
    ):
        return new_trades

    existing = existing_trades.copy()
    new = new_trades.copy()
    if "Strategy_Name" not in new.columns:
        new["Strategy_Name"] = ""
    existing["_dedupe_key"] = build_dedupe_key(
        existing,
        dedupe_columns,
    )
    new["_dedupe_key"] = build_dedupe_key(
        new,
        dedupe_columns,
    )
    existing_strategy_names = (
        existing["Strategy_Name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    strategy_by_key = (
        existing[
            existing_strategy_names.isin(VALID_MANUAL_STRATEGY_NAMES)
        ]
        .drop_duplicates(
            subset=["_dedupe_key"],
            keep="last",
        )
        .set_index("_dedupe_key")["Strategy_Name"]
        .to_dict()
    )
    replacement = new["_dedupe_key"].map(strategy_by_key)
    has_replacement = replacement.notna() & (
        replacement.astype(str).str.strip() != ""
    )
    new.loc[
        has_replacement,
        "Strategy_Name",
    ] = replacement.loc[has_replacement]

    return new.drop(columns=["_dedupe_key"])


def preserve_master_strategy_names(new_trades, master_file):
    if not os.path.exists(master_file):
        return new_trades

    existing_trades = pd.read_csv(master_file)

    if existing_trades.empty:
        return new_trades

    preview = pd.concat(
        [
            existing_trades,
            new_trades,
        ],
        ignore_index=True,
        sort=False,
    )
    strategy_dedupe_columns = [
        column
        for column in get_dedupe_columns(preview)
        if column not in STATEMENT_IDENTITY_COLUMNS
    ]

    return preserve_existing_strategy_names(
        existing_trades,
        new_trades,
        strategy_dedupe_columns,
    )


def drop_existing_replaced_real_trades(existing_trades, new_trades, dedupe_columns):

    if not dedupe_columns:
        return existing_trades

    real_dedupe_columns = [
        column
        for column in dedupe_columns
        if column not in STATEMENT_IDENTITY_COLUMNS
    ]

    if not real_dedupe_columns:
        return existing_trades

    existing = existing_trades.copy()
    new = new_trades.copy()
    existing["_real_trade_key"] = build_dedupe_key(
        existing,
        real_dedupe_columns,
    )
    new["_real_trade_key"] = build_dedupe_key(
        new,
        real_dedupe_columns,
    )
    replacement_keys = set(new["_real_trade_key"])

    return existing.loc[
        ~existing["_real_trade_key"].isin(replacement_keys)
    ].drop(
        columns=["_real_trade_key"],
    )


def drop_legacy_blank_time_duplicates(df):

    if "Exec Time" not in df.columns:
        return df

    dedupe_columns = [
        column
        for column in get_dedupe_columns(df)
        if column != "Exec Time"
    ]

    if not dedupe_columns:
        return df

    df = df.copy()
    blank_time = (
        df["Exec Time"]
        .isna()
        | (df["Exec Time"].astype(str).str.strip() == "")
    )
    df["_dedupe_no_exec_time"] = build_dedupe_key(
        df,
        dedupe_columns,
    )
    timestamped_keys = set(
        df.loc[
            ~blank_time,
            "_dedupe_no_exec_time",
        ]
    )
    keep_rows = ~(
        blank_time
        & df["_dedupe_no_exec_time"].isin(timestamped_keys)
    )

    return df.loc[
        keep_rows
    ].drop(
        columns=["_dedupe_no_exec_time"],
    )


def drop_invalid_strategy_duplicates(df):

    if "Strategy_Name" not in df.columns:
        return df

    dedupe_columns = [
        column
        for column in [
            "Exec Time",
            "Symbol",
            "Exp",
            "Strike",
            "Type",
        ]
        if column in df.columns
    ]

    if not dedupe_columns:
        return df

    df = df.copy()
    strategy_names = (
        df["Strategy_Name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    valid_strategy = strategy_names.isin(VALID_MANUAL_STRATEGY_NAMES)
    blank_strategy = strategy_names == ""
    df["_instrument_time_key"] = build_dedupe_key(
        df,
        dedupe_columns,
    )
    reviewed_keys = set(
        df.loc[
            valid_strategy,
            "_instrument_time_key",
        ]
    )
    blank_keys = set(
        df.loc[
            blank_strategy,
            "_instrument_time_key",
        ]
    )
    keep_rows = (
        valid_strategy
        | (
            blank_strategy
            & ~df["_instrument_time_key"].isin(reviewed_keys)
        )
        | ~df["_instrument_time_key"].isin(
            reviewed_keys | blank_keys
        )
    )
    invalid_strategy = ~valid_strategy & ~blank_strategy
    df.loc[
        invalid_strategy,
        "Strategy_Name",
    ] = ""

    return df.loc[
        keep_rows
    ].drop(
        columns=["_instrument_time_key"],
    )


def sort_cleaned_trades(df):

    df = df.copy()
    df["_sort_time"] = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    df = df.sort_values(
        by=["_sort_time"],
        kind="mergesort",
    )

    return df.drop(
        columns=["_sort_time"],
    )


def ensure_blank_strategy_column(df):

    df = df.copy()

    if "Strategy_Name" not in df.columns:
        df["Strategy_Name"] = ""

    return df


def reset_cash_correction_columns(df):

    df = df.copy()
    df["cash_correction_applied"] = False
    df["cash_correction_status"] = ""
    df["cash_correction_source"] = ""
    df["statement_cash_flow"] = ""

    return df


def recalculate_cleaned_trade_columns(
    df,
    starting_equity,
    cash_trade_corrections=None,
):

    df = sort_cleaned_trades(df)
    df = ensure_blank_strategy_column(df)
    df["fees"] = df.apply(
        lookup_fees,
        axis=1,
    )
    df["margin_requirement"] = calculate_margin_requirements(df)
    df = add_pnl_columns(df)
    df = reset_cash_correction_columns(df)
    df = apply_cash_trade_corrections(
        df,
        cash_trade_corrections,
    )
    df = add_log_return_columns(
        df,
        starting_equity,
    )
    df = add_margin_return_columns(df)

    return df


def update_master_cleaned_trades(
    new_trades,
    master_file,
    starting_equity,
    cash_trade_corrections=None,
    start_date=None,
):

    if os.path.exists(master_file):
        existing_trades = pd.read_csv(master_file)
        preview = pd.concat(
            [
                existing_trades,
                new_trades,
            ],
            ignore_index=True,
            sort=False,
        )
        dedupe_columns = get_dedupe_columns(preview)
        strategy_dedupe_columns = [
            column
            for column in dedupe_columns
            if column not in STATEMENT_IDENTITY_COLUMNS
        ]
        new_trades = preserve_existing_strategy_names(
            existing_trades,
            new_trades,
            strategy_dedupe_columns,
        )
        existing_trades = drop_existing_replaced_real_trades(
            existing_trades,
            new_trades,
            dedupe_columns,
        )
        existing_trades = existing_trades.copy()
        existing_trades["_dedupe_key"] = build_dedupe_key(
            existing_trades,
            dedupe_columns,
        )
        new_trades["_dedupe_key"] = build_dedupe_key(
            new_trades,
            dedupe_columns,
        )
        existing_trades = existing_trades.loc[
            ~existing_trades["_dedupe_key"].isin(new_trades["_dedupe_key"])
        ].drop(
            columns=["_dedupe_key"],
        )
        new_trades = new_trades.drop(
            columns=["_dedupe_key"],
        )
        combined = pd.concat(
            [
                existing_trades,
                new_trades,
            ],
            ignore_index=True,
            sort=False,
        )
    else:
        combined = new_trades.copy()

    combined = drop_legacy_blank_time_duplicates(combined)
    combined = drop_legacy_blank_identity_duplicates(
        combined,
        new_trades,
    )
    combined = drop_invalid_strategy_duplicates(combined)
    combined = filter_by_exec_date(
        combined,
        start_date,
    )

    dedupe_columns = get_dedupe_columns(combined)
    combined["_dedupe_key"] = build_dedupe_key(
        combined,
        dedupe_columns,
    )
    combined = combined.drop_duplicates(
        subset=["_dedupe_key"],
        keep="first",
    ).drop(
        columns=["_dedupe_key"],
    )

    return recalculate_cleaned_trade_columns(
        combined,
        starting_equity,
        cash_trade_corrections=cash_trade_corrections,
    )


def output_paths(output_dir):
    return {
        "cleaned": f"{output_dir}/cleaned_tos_data.csv",
        "master": f"{output_dir}/master_cleaned_tos_data.csv",
        "pnl_chart": f"{output_dir}/pnl_chart.png",
        "equity_curve": f"{output_dir}/equity_curve.csv",
        "summary_stats": f"{output_dir}/summary_statistics.csv",
        "cash_reconciliation": (
            f"{output_dir}/cash_balance_reconciliation.csv"
        ),
        "daily_trade_cash_reconciliation": (
            f"{output_dir}/daily_trade_cash_reconciliation.csv"
        ),
        "cash_reconciliation_summary": (
            f"{output_dir}/cash_balance_reconciliation_summary.csv"
        ),
        "cash_reconciliation_dashboard": (
            f"{output_dir}/cash_balance_reconciliation_dashboard.html"
        ),
        "fee_correction_suggestions": (
            f"{output_dir}/fee_correction_suggestions.csv"
        ),
        "cash_trade_corrections": (
            f"{output_dir}/cash_trade_corrections.csv"
        ),
        "cash_trade_correction_candidates": (
            f"{output_dir}/cash_trade_correction_candidates.csv"
        ),
        "cash_reconciliation_reviews": (
            f"{output_dir}/cash_reconciliation_group_reviews.csv"
        ),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract, enrich, and summarize Thinkorswim account trade "
            "history exports."
        )
    )
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to the raw Thinkorswim account statement CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help="Directory where cleaned trade outputs are written.",
    )
    parser.add_argument(
        "--start-date",
        help=(
            "Only include trades and cash reconciliation rows on or after "
            "this date, e.g. 2026-01-01."
        ),
    )
    parser.add_argument(
        "--skip-cash-validation",
        action="store_true",
        help=(
            "Skip cash-balance reconciliation reports from statement cash "
            "ledger sections."
        ),
    )
    parser.add_argument(
        "--cash-validation-tolerance",
        type=float,
        default=1.0,
        help=(
            "Allowed dollar difference between extracted trade PnL and "
            "statement trade cash flow by day/account bucket."
        ),
    )
    parser.add_argument(
        "--skip-cash-dashboard",
        action="store_true",
        help="Skip the HTML cash reconciliation review dashboard.",
    )
    parser.add_argument(
        "--open-cash-dashboard",
        action="store_true",
        help="Open the cash reconciliation review dashboard after writing it.",
    )
    parser.add_argument(
        "--serve-cash-dashboard",
        action="store_true",
        help=(
            "Serve the cash dashboard locally so approval selections can "
            "be saved to the corrections file."
        ),
    )
    parser.add_argument(
        "--cash-dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-cash-dashboard.",
    )
    parser.add_argument(
        "--cash-dashboard-port",
        type=int,
        default=8766,
        help="Port for --serve-cash-dashboard.",
    )
    parser.add_argument(
        "--apply-cash-corrections",
        action="store_true",
        help=(
            "Apply saved approved cash-ledger trade corrections before "
            "writing cleaned/master outputs."
        ),
    )
    parser.add_argument(
        "--cash-corrections-file",
        help=(
            "CSV file used to persist cash-ledger trade corrections. "
            "Default: <output-dir>/cash_trade_corrections.csv."
        ),
    )
    parser.add_argument(
        "--ignore-cash-corrections",
        action="store_true",
        help="Do not load or apply a saved cash corrections file.",
    )
    parser.add_argument(
        "--strict-reconciliation",
        action="store_true",
        help=(
            "Exit with an error if cash validation finds unreviewed "
            "unreconciled day/account groups."
        ),
    )
    return parser.parse_args()


def main(
    input_file=INPUT_FILE,
    output_dir=OUTPUT_DIR,
    validate_cash_balances=True,
    cash_validation_tolerance=1.0,
    write_cash_dashboard=True,
    open_cash_dashboard=False,
    serve_cash_dashboard_enabled=False,
    cash_dashboard_host="127.0.0.1",
    cash_dashboard_port=8766,
    apply_cash_corrections=False,
    cash_corrections_file=None,
    ignore_cash_corrections=False,
    start_date=None,
    strict_reconciliation=False,
):
    paths = output_paths(output_dir)
    start_date = parse_filter_date(
        start_date,
        "--start-date",
    )
    cash_corrections_path = (
        cash_corrections_file
        or paths["cash_trade_corrections"]
    )
    cash_reconciliation_reviews_path = paths["cash_reconciliation_reviews"]

    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    #
    # Read raw file
    #
    with open(input_file, "r") as f:
        lines = f.readlines()

    #
    # Find trade history section
    #
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):

        first_col = (
            line.split(",")[0]
            .strip()
            .strip('"')
        )

        #
        # Start section
        #
        if first_col == "Account Trade History":
            start_idx = i

        #
        # End section
        #
        elif (
            first_col in END_SECTIONS
            and start_idx is not None
        ):
            end_idx = i
            break

    #
    # Validation
    #
    if start_idx is None:
        raise ValueError(
            "Could not find Account Trade History"
        )

    if end_idx is None:
        raise ValueError(
            "Could not find ending section"
        )

    #
    # Extract lines
    #
    trade_lines = lines[start_idx:end_idx]

    #
    # Remove junk rows
    #
    cleaned_lines = []

    for line in trade_lines:

        #
        # Skip title row
        #
        if "Account Trade History" in line:
            continue

        #
        # Skip blank rows
        #
        if line.strip() == "":
            continue

        cleaned_lines.append(line)

    #
    # Load into pandas
    #
    csv_data = "".join(cleaned_lines)

    df = pd.read_csv(
        StringIO(csv_data)
    )

    #
    # Drop fully empty rows
    #
    df = df.dropna(
        how="all"
    )
    df = add_trade_identity_columns(
        df,
        input_file,
    )

    df = fill_missing_execution_times(df)

    df = filter_by_exec_date(
        df,
        start_date,
    )

    if df.empty:
        raise ValueError(
            "No trades remain after applying the date filters."
        )

    trade_times = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    ).dropna()
    first_trade_time = trade_times.min()
    last_trade_time = trade_times.max()
    cash_reconciliation_start_date = (
        max(
            start_date,
            first_trade_time.normalize(),
        )
        if start_date is not None
        else first_trade_time.normalize()
    )
    cash_reconciliation_end_date = last_trade_time.normalize()

    df = ensure_blank_strategy_column(df)
    df = preserve_master_strategy_names(
        df,
        paths["master"],
    )

    #
    # Add enrichment columns
    #
    df["fees"] = df.apply(
        lookup_fees,
        axis=1
    )

    df["margin_requirement"] = calculate_margin_requirements(df)

    df = add_pnl_columns(df)
    cash_ledger = parse_cash_ledger(lines)
    cash_ledger = filter_cash_ledger_by_date(
        cash_ledger,
        cash_reconciliation_start_date,
        cash_reconciliation_end_date,
    )
    cash_trade_correction_candidates = (
        build_cash_trade_corrections(cash_ledger, df)
        if validate_cash_balances
        else pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)
    )
    saved_cash_trade_corrections = load_cash_trade_corrections(
        cash_corrections_path
    )
    saved_cash_reconciliation_reviews = load_cash_reconciliation_reviews(
        cash_reconciliation_reviews_path
    )
    current_approved_cash_trade_corrections = (
        current_statement_approved_corrections(
            cash_trade_correction_candidates,
            saved_cash_trade_corrections,
        )
    )
    auto_cash_trade_corrections = (
        auto_approved_cash_trade_corrections(
            cash_trade_correction_candidates,
            df,
        )
        if apply_cash_corrections and not ignore_cash_corrections
        else pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)
    )
    auto_cash_trade_corrections_saved = 0

    if not auto_cash_trade_corrections.empty:
        auto_cash_trade_corrections_saved = len(
            auto_cash_trade_corrections
        )
        saved_cash_trade_corrections = combine_cash_trade_corrections(
            saved_cash_trade_corrections,
            auto_cash_trade_corrections,
        )
        save_cash_trade_corrections(
            cash_corrections_path,
            saved_cash_trade_corrections,
        )

    cash_trade_corrections = combine_cash_trade_corrections(
        saved_cash_trade_corrections,
        current_approved_cash_trade_corrections,
    )

    if ignore_cash_corrections:
        cash_trade_corrections = pd.DataFrame(
            columns=CASH_CORRECTION_COLUMNS
        )

    df = apply_cash_trade_corrections(
        df,
        cash_trade_corrections,
    )

    if pd.isna(first_trade_time):
        raise ValueError(
            "Could not determine first trade time"
        )

    starting_equity = lookup_starting_equity(
        lines[:start_idx],
        first_trade_time,
    )

    df = add_log_return_columns(
        df,
        starting_equity,
    )

    df = add_margin_return_columns(df)

    master_starting_equity = get_master_starting_equity(
        paths["master"],
        starting_equity,
    ) if start_date is None else starting_equity
    master_df = update_master_cleaned_trades(
        df,
        paths["master"],
        master_starting_equity,
        cash_trade_corrections=cash_trade_corrections,
        start_date=start_date,
    )
    equity_curve = build_equity_curve(master_df)
    summary_statistics = calculate_summary_statistics(master_df)
    cash_reconciliation = None
    cash_reconciliation_summary = None
    fee_correction_suggestions = None

    if validate_cash_balances:
        cash_reconciliation = reconcile_cash_balances(
            lines,
            df,
            tolerance=cash_validation_tolerance,
            start_date=cash_reconciliation_start_date,
            end_date=cash_reconciliation_end_date,
        )
        cash_reconciliation = annotate_cash_reconciliation_reviews(
            cash_reconciliation,
            saved_cash_reconciliation_reviews,
        )
        cash_reconciliation_summary = summarize_cash_reconciliation(
            cash_reconciliation
        )
        fee_correction_suggestions = build_fee_correction_suggestions(
            cash_ledger,
            df,
            tolerance=cash_validation_tolerance,
        )

    #
    # Save output
    #
    df.to_csv(
        paths["cleaned"],
        index=False
    )

    master_df.to_csv(
        paths["master"],
        index=False,
    )

    save_pnl_chart(master_df, output_file=paths["pnl_chart"])

    equity_curve.to_csv(
        paths["equity_curve"],
        index=False,
    )

    summary_statistics.to_csv(
        paths["summary_stats"],
        index=False,
    )

    if validate_cash_balances:
        cash_reconciliation.to_csv(
            paths["cash_reconciliation"],
            index=False,
        )
        cash_reconciliation.to_csv(
            paths["daily_trade_cash_reconciliation"],
            index=False,
        )
        cash_reconciliation_summary.to_csv(
            paths["cash_reconciliation_summary"],
            index=False,
        )
        fee_correction_suggestions.to_csv(
            paths["fee_correction_suggestions"],
            index=False,
        )
        cash_trade_correction_candidates.to_csv(
            paths["cash_trade_correction_candidates"],
            index=False,
        )
        if write_cash_dashboard:
            dashboard_path = write_cash_reconciliation_dashboard(
                paths["cash_reconciliation_dashboard"],
                cash_reconciliation,
                cash_ledger,
                df,
                fee_correction_suggestions,
                cash_trade_correction_candidates,
                saved_cash_trade_corrections,
                saved_cash_reconciliation_reviews,
                server_enabled=serve_cash_dashboard_enabled,
            )
            if open_cash_dashboard:
                webbrowser.open(
                    Path(dashboard_path).resolve().as_uri()
                )

    print(
        f"Saved enriched trade data to {paths['cleaned']}"
    )

    print(
        f"Saved master cleaned trade data to {paths['master']}"
    )

    print(
        f"Saved PnL chart to {paths['pnl_chart']}"
    )

    print(
        f"Saved equity curve to {paths['equity_curve']}"
    )

    print(
        f"Saved summary statistics to {paths['summary_stats']}"
    )

    if validate_cash_balances:
        unreconciled_trades = int(
            cash_reconciliation_summary.loc[
                0,
                "trades_in_unreconciled_groups",
            ]
        )
        unreconciled_groups = int(
            cash_reconciliation_summary.loc[
                0,
                "unreconciled_groups",
            ]
        )
        print(
            "Cash reconciliation found "
            f"{unreconciled_trades} trades in "
            f"{unreconciled_groups} unreconciled day/account groups"
        )
        print(
            f"Saved cash reconciliation to "
            f"{paths['cash_reconciliation']}"
        )
        print(
            f"Saved daily trade/cash reconciliation to "
            f"{paths['daily_trade_cash_reconciliation']}"
        )
        print(
            f"Saved cash reconciliation summary to "
            f"{paths['cash_reconciliation_summary']}"
        )
        print(
            f"Saved fee correction suggestions to "
            f"{paths['fee_correction_suggestions']}"
        )
        print(
            f"Saved cash trade correction candidates to "
            f"{paths['cash_trade_correction_candidates']}"
        )
        if apply_cash_corrections or len(cash_trade_corrections) > 0:
            print(
                f"Loaded approved cash trade corrections from "
                f"{cash_corrections_path}"
            )
            print(
                "Applied "
                f"{df.attrs.get('cash_corrections_applied', 0)} "
                "approved cash-ledger trade corrections."
            )
            if auto_cash_trade_corrections_saved:
                print(
                    "Auto-approved and saved "
                    f"{auto_cash_trade_corrections_saved} "
                    "futures/Opt026 cash-ledger corrections."
                )
        if write_cash_dashboard:
            print(
                f"Saved cash reconciliation dashboard to "
                f"{paths['cash_reconciliation_dashboard']}"
            )
        if serve_cash_dashboard_enabled and write_cash_dashboard:
            serve_cash_dashboard(
                paths["cash_reconciliation_dashboard"],
                cash_corrections_path,
                cash_reconciliation_reviews_path,
                host=cash_dashboard_host,
                port=cash_dashboard_port,
                open_browser=True,
            )

        strict_failures = strict_reconciliation_failures(
            cash_reconciliation
        )
        if strict_reconciliation and not strict_failures.empty:
            failure_preview = strict_failures[
                [
                    column
                    for column in [
                        "date",
                        "account_bucket",
                        "extracted_trade_count",
                        "statement_trade_rows",
                        "extracted_net_pnl",
                        "statement_trade_cash_flow",
                        "unreconciled_delta",
                    ]
                    if column in strict_failures.columns
                ]
            ].head(10)
            raise RuntimeError(
                "Strict reconciliation failed: "
                f"{len(strict_failures)} unreviewed unreconciled "
                "day/account groups remain. Preview:\n"
                f"{failure_preview.to_string(index=False)}"
            )

    return {
        "cleaned": df,
        "master": master_df,
        "equity_curve": equity_curve,
        "summary_statistics": summary_statistics,
        "cash_reconciliation": cash_reconciliation,
        "cash_reconciliation_summary": cash_reconciliation_summary,
        "fee_correction_suggestions": fee_correction_suggestions,
        "cash_trade_corrections": cash_trade_corrections,
        "cash_trade_correction_candidates": cash_trade_correction_candidates,
        "cash_reconciliation_reviews": saved_cash_reconciliation_reviews,
    }


if __name__ == "__main__":
    args = parse_args()
    main(
        input_file=args.input,
        output_dir=args.output_dir,
        validate_cash_balances=not args.skip_cash_validation,
        cash_validation_tolerance=args.cash_validation_tolerance,
        write_cash_dashboard=not args.skip_cash_dashboard,
        open_cash_dashboard=args.open_cash_dashboard,
        serve_cash_dashboard_enabled=args.serve_cash_dashboard,
        cash_dashboard_host=args.cash_dashboard_host,
        cash_dashboard_port=args.cash_dashboard_port,
        apply_cash_corrections=args.apply_cash_corrections,
        cash_corrections_file=args.cash_corrections_file,
        ignore_cash_corrections=args.ignore_cash_corrections,
        start_date=args.start_date,
        strict_reconciliation=args.strict_reconciliation,
    )
