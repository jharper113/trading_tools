import argparse
import csv
import errno
import json
import os
import re
import threading
import webbrowser
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pandas as pd

from extract_trade_history import (
    CASH_CORRECTION_COLUMNS,
    END_SECTIONS,
    VALID_MANUAL_STRATEGY_NAMES,
    add_trade_identity_columns,
    apply_cash_trade_corrections,
    build_cash_trade_corrections as build_base_cash_trade_corrections,
    build_fee_correction_suggestions,
    build_dedupe_key,
    combine_cash_trade_corrections,
    correction_key,
    current_statement_approved_corrections,
    drop_existing_replaced_real_trades,
    filter_by_exec_date,
    filter_cash_ledger_by_date,
    fill_missing_execution_times,
    get_dedupe_columns,
    get_master_starting_equity,
    json_safe_records,
    load_cash_trade_corrections,
    lookup_starting_equity,
    output_paths,
    parse_cash_ledger,
    parse_filter_date,
    parse_statement_ytd_summary,
    preserve_existing_strategy_names,
    preserve_master_strategy_names,
    recalculate_cleaned_trade_columns,
    reconcile_cash_balances,
    reset_cash_correction_columns,
    save_cash_trade_corrections,
    save_pnl_chart,
    summarize_cash_reconciliation,
    STATEMENT_IDENTITY_COLUMNS,
    SYNTHETIC_CASH_SETTLEMENT_SOURCE,
    update_master_cleaned_trades,
    restore_synthetic_cash_settlement_values,
)
from src.enrich import (
    add_log_return_columns,
    add_margin_return_columns,
    add_pnl_columns,
    build_equity_curve,
    calculate_margin_requirements,
    calculate_summary_statistics,
    is_futures_trade,
    lookup_fees,
    normalize_root_symbol,
    parse_number,
)


INPUT_FILE = "./data/trades.csv"
OUTPUT_DIR = "./output_v2"
REBUILD_OUTPUT_DIR = "./output_v2_rebuild"
STRATEGY_SOURCE_MASTER = "./output/master_cleaned_tos_data.csv"
DASHBOARD_FILE_NAME = "trade_history_reconciliation_dashboard.html"
TRADE_HISTORY_END_SECTIONS = {
    *END_SECTIONS,
    "Futures Options",
    "Forex Account Summary",
    "Crypto Account Summary",
    "Account Summary",
    "Profits and Losses",
}


class NoNewTradesAfterOverlap(ValueError):
    def __init__(self, skipped_overlap_trades):
        self.skipped_overlap_trades = skipped_overlap_trades
        super().__init__(
            "No new trades remain after skipping overlaps."
        )


@dataclass
class V2RunContext:
    input_file: str
    output_dir: str
    paths: dict
    lines: list
    base_trades: pd.DataFrame
    cash_ledger: pd.DataFrame
    statement_ytd_summary: dict
    statement_ytd_positions: pd.DataFrame
    starting_equity: float
    master_starting_equity: float
    start_date: Optional[pd.Timestamp]
    cash_reconciliation_start_date: pd.Timestamp
    cash_reconciliation_end_date: pd.Timestamp
    cash_corrections_path: str
    cash_validation_tolerance: float
    skipped_overlap_trades: int = 0
    strict_daily_import: bool = False


def v2_output_paths(output_dir):
    paths = output_paths(output_dir)
    paths["dashboard"] = f"{output_dir}/{DASHBOARD_FILE_NAME}"
    paths["reconciliation_detail"] = (
        f"{output_dir}/trade_history_reconciliation_detail.csv"
    )
    paths["ytd_reconciliation"] = f"{output_dir}/ytd_reconciliation.csv"
    paths["ytd_bridge_adjustments"] = f"{output_dir}/ytd_bridge_adjustments.csv"
    paths["statement_ytd_history"] = f"{output_dir}/statement_ytd_history.csv"
    return paths


def reset_output_files(output_dir):
    paths = v2_output_paths(output_dir)

    for path in sorted(set(paths.values())):
        Path(path).unlink(missing_ok=True)


def read_statement_lines(input_file):
    with open(input_file, "r", encoding="utf-8") as handle:
        return handle.readlines()


def account_trade_history_lines(lines):
    start_idx = None
    end_idx = None

    for index, line in enumerate(lines):
        first_col = line.split(",")[0].strip().strip('"')

        if first_col == "Account Trade History":
            start_idx = index
            continue

        if first_col in TRADE_HISTORY_END_SECTIONS and start_idx is not None:
            end_idx = index
            break

    if start_idx is None:
        raise ValueError("Could not find Account Trade History")

    if end_idx is None:
        raise ValueError("Could not find ending section after Account Trade History")

    cleaned_lines = []

    for line in lines[start_idx:end_idx]:
        if "Account Trade History" in line:
            continue

        if line.strip() == "":
            continue

        cleaned_lines.append(line)

    return cleaned_lines, start_idx


def load_statement_trade_rows(input_file, start_date=None):
    lines = read_statement_lines(input_file)
    cleaned_lines, trade_section_start = account_trade_history_lines(lines)
    df = pd.read_csv(StringIO("".join(cleaned_lines))).dropna(how="all")
    df = add_trade_identity_columns(df, input_file)
    df = fill_missing_execution_times(df)
    df = filter_by_exec_date(df, start_date)

    if df.empty:
        raise ValueError("No trades remain after applying the date filters.")

    return lines, df, trade_section_start


def overlap_dedupe_columns(existing_trades, new_trades):
    preview = pd.concat(
        [
            existing_trades,
            new_trades,
        ],
        ignore_index=True,
        sort=False,
    )
    statement_identity = set(STATEMENT_IDENTITY_COLUMNS) | {"statement_file"}

    return [
        column
        for column in get_dedupe_columns(preview)
        if column not in statement_identity
    ]


def overlap_aggregate_group_columns(existing_trades, new_trades):
    candidates = [
        "Trade Minute",
        "Spread",
        "Side",
        "Pos Effect",
        "Symbol",
        "Exp",
        "Strike",
        "Type",
        "Exp.1",
        "Settlement Date",
        "Type.1",
    ]

    return [
        column
        for column in candidates
        if column in existing_trades.columns
        and column in new_trades.columns
    ]


def add_overlap_aggregate_columns(trades):
    result = trades.copy()
    exec_times = pd.to_datetime(
        result.get("Exec Time"),
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    result["Trade Minute"] = exec_times.dt.strftime("%Y-%m-%d %H:%M")
    qty = pd.to_numeric(result.get("Qty"), errors="coerce").fillna(0.0)
    price = pd.to_numeric(result.get("Price"), errors="coerce").fillna(0.0)
    side = result.get(
        "Side",
        pd.Series("", index=result.index),
    ).astype(str).str.upper()
    signed_qty = qty.copy()
    signed_qty.loc[side.eq("BUY")] = qty.abs()
    signed_qty.loc[side.eq("SELL")] = -qty.abs()
    result["_overlap_signed_qty"] = signed_qty
    result["_overlap_gross_value"] = qty.abs() * price

    return result


def is_option_aggregate_group(group):
    if "Type" not in group.columns:
        return False

    return group["Type"].astype(str).str.upper().isin({"CALL", "PUT"}).any()


def aggregate_overlap_key(identity, signed_qty, gross_value):
    return (
        identity,
        round(float(signed_qty), 8),
        None if gross_value is None else round(float(gross_value), 4),
    )


def build_aggregate_overlap_keys(trades, group_columns):
    if trades.empty:
        return set()

    if not group_columns:
        return set()

    keys = set()

    for group_key, group in trades.groupby(
        group_columns,
        dropna=False,
        sort=False,
    ):
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        identity = tuple(
            "" if pd.isna(value) else str(value)
            for value in group_key
        )
        signed_qty = group["_overlap_signed_qty"].sum()
        gross_value = group["_overlap_gross_value"].sum()
        keys.add(
            aggregate_overlap_key(identity, signed_qty, gross_value)
        )
        if is_option_aggregate_group(group):
            keys.add(
                aggregate_overlap_key(identity, signed_qty, None)
            )

    return keys


def aggregate_overlap_skip_mask(incoming, exact_keep_mask, existing_trades):
    if not exact_keep_mask:
        return []

    existing_aggregate = add_overlap_aggregate_columns(existing_trades)
    incoming_aggregate = add_overlap_aggregate_columns(incoming)
    group_columns = overlap_aggregate_group_columns(
        existing_aggregate,
        incoming_aggregate,
    )

    if not group_columns:
        return exact_keep_mask

    aggregate_keys = build_aggregate_overlap_keys(
        existing_aggregate,
        group_columns,
    )
    if not aggregate_keys:
        return exact_keep_mask

    result_mask = list(exact_keep_mask)
    candidate_positions = [
        position
        for position, keep_row in enumerate(exact_keep_mask)
        if keep_row
    ]

    if not candidate_positions:
        return result_mask

    candidates = incoming_aggregate.iloc[candidate_positions].copy()
    candidates["_overlap_original_position"] = candidate_positions

    for _, group in candidates.groupby(group_columns, dropna=False, sort=False):
        identity = tuple(
            "" if pd.isna(group.iloc[0][column]) else str(group.iloc[0][column])
            for column in group_columns
        )
        signed_qty = group["_overlap_signed_qty"].sum()
        gross_value = group["_overlap_gross_value"].sum()
        possible_keys = {
            aggregate_overlap_key(identity, signed_qty, gross_value),
        }

        if is_option_aggregate_group(group):
            possible_keys.add(
                aggregate_overlap_key(identity, signed_qty, None)
            )

        if not possible_keys.intersection(aggregate_keys):
            continue

        for original_position in group["_overlap_original_position"]:
            result_mask[int(original_position)] = False

    return result_mask


def skip_existing_trade_overlaps(new_trades, master_file):
    if new_trades.empty or not os.path.exists(master_file):
        return new_trades, 0

    existing_trades = pd.read_csv(master_file)

    if existing_trades.empty:
        return new_trades, 0

    dedupe_columns = overlap_dedupe_columns(
        existing_trades,
        new_trades,
    )

    if not dedupe_columns:
        return new_trades, 0

    remaining_existing_counts = (
        build_dedupe_key(existing_trades, dedupe_columns)
        .value_counts()
        .to_dict()
    )
    incoming = new_trades.copy()
    incoming["_overlap_dedupe_key"] = build_dedupe_key(
        incoming,
        dedupe_columns,
    )
    keep_rows = []
    skipped_count = 0

    for key in incoming["_overlap_dedupe_key"]:
        existing_count = remaining_existing_counts.get(key, 0)

        if existing_count > 0:
            remaining_existing_counts[key] = existing_count - 1
            keep_rows.append(False)
            skipped_count += 1
        else:
            keep_rows.append(True)

    keep_rows = aggregate_overlap_skip_mask(
        incoming,
        keep_rows,
        existing_trades,
    )
    skipped_count = len(keep_rows) - sum(keep_rows)

    filtered = (
        incoming.loc[keep_rows]
        .drop(columns=["_overlap_dedupe_key"])
        .reset_index(drop=True)
    )

    return filtered, skipped_count


def trade_date_window(trades, start_date=None, exact_start=False):
    trade_times = pd.to_datetime(
        trades["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    ).dropna()

    if trade_times.empty:
        raise ValueError("Could not determine trade date window")

    first_trade_time = trade_times.min()
    last_trade_time = trade_times.max()

    if start_date is not None:
        reconciliation_start = max(
            start_date,
            first_trade_time.normalize(),
        )
    elif exact_start:
        reconciliation_start = first_trade_time
    else:
        reconciliation_start = first_trade_time.normalize()

    return first_trade_time, reconciliation_start, last_trade_time.normalize()


def prepare_base_trades(raw_trades, strategy_source_master=None):
    trades = raw_trades.copy()

    if strategy_source_master:
        trades = preserve_master_strategy_names(
            trades,
            strategy_source_master,
        )

    trades["fees"] = trades.apply(lookup_fees, axis=1)
    trades["margin_requirement"] = calculate_margin_requirements(trades)
    trades = add_pnl_columns(trades)
    trades = reset_cash_correction_columns(trades)

    return trades


def is_nonzero_futures_cash_settlement(row):
    if str(row.get("account_bucket", "")).strip().lower() != "futures":
        return False

    if str(row.get("type", "")).strip().upper() != "TRD":
        return False

    description = str(row.get("description", "")).strip().lower()
    if "cash settle future" not in description:
        return False

    return abs(parse_number(row.get("cash_flow")) or 0.0) > 0.000001


def parse_expired_futures_option_removal(description):
    match = re.search(
        r"expiration of\s+"
        r"(?P<symbol>/[A-Z0-9]+)\s+"
        r"\S+\s+"
        r"\d+"
        r"(?:\s+\((?P<cycle>[^)]+)\))?\s+"
        r"(?P<day>\d{1,2})\s+"
        r"(?P<month>[A-Za-z]{3})\s+"
        r"(?P<year>\d{4})\s+"
        r"(?P<strike>[0-9]+(?:\.[0-9]+)?)\s+"
        r"(?P<option_type>CALL|PUT)",
        str(description),
        flags=re.IGNORECASE,
    )

    if not match:
        return {}

    groups = match.groupdict()
    expiration = (
        f"{int(groups['day'])} "
        f"{groups['month'].upper()} "
        f"{str(groups['year'])[-2:]}"
    )
    cycle = str(groups.get("cycle") or "").strip().upper()
    symbol = f"{groups['symbol'].upper()} {expiration}"

    if cycle:
        symbol = f"{symbol} ({cycle})"

    strike = parse_number(groups.get("strike"))

    return {
        "symbol": symbol,
        "expiration": expiration,
        "strike": strike if strike is not None else groups.get("strike"),
        "option_type": groups["option_type"].upper(),
        "removal_description": str(description),
    }


def matching_expiration_removal(cash_ledger, settlement_row):
    if cash_ledger is None or cash_ledger.empty:
        return None

    timestamp = pd.to_datetime(settlement_row.get("timestamp"), errors="coerce")
    if pd.isna(timestamp):
        return None

    description = cash_ledger["description"].astype(str)
    candidates = cash_ledger[
        cash_ledger["account_bucket"].astype(str).str.lower().eq("futures")
        & cash_ledger["type"].astype(str).str.upper().eq("TRD")
        & cash_ledger["date"].astype(str).eq(str(settlement_row.get("date")))
        & description.str.contains(
            "Removal of option due to expiration",
            case=False,
            na=False,
            regex=False,
        )
    ].copy()

    if candidates.empty:
        return None

    candidates["_timestamp"] = pd.to_datetime(
        candidates["timestamp"],
        errors="coerce",
    )
    candidates = candidates[
        candidates["_timestamp"].notna()
        & (candidates["_timestamp"] <= timestamp)
    ].sort_values("_timestamp")

    if candidates.empty:
        return None

    return candidates.iloc[-1]


def format_exec_time(timestamp):
    timestamp = pd.to_datetime(timestamp, errors="coerce")

    if pd.isna(timestamp):
        return ""

    return (
        f"{timestamp.month}/{timestamp.day}/"
        f"{timestamp.strftime('%y')} {timestamp.strftime('%H:%M:%S')}"
    )


def next_synthetic_statement_row(trades, sequence):
    if "statement_trade_row" not in trades.columns or trades.empty:
        return 100000 + sequence

    statement_rows = pd.to_numeric(
        trades["statement_trade_row"],
        errors="coerce",
    ).dropna()

    if statement_rows.empty:
        return 100000 + sequence

    return int(statement_rows.max()) + 100000 + sequence


def append_futures_cash_settlement_trades(trades, cash_ledger, input_file):
    if trades is None or trades.empty or cash_ledger is None or cash_ledger.empty:
        return trades

    settlement_rows = cash_ledger[
        cash_ledger.apply(is_nonzero_futures_cash_settlement, axis=1)
    ].sort_values("timestamp")

    if settlement_rows.empty:
        return trades

    synthetic_rows = []

    for sequence, (_, settlement) in enumerate(
        settlement_rows.iterrows(),
        start=1,
    ):
        removal = matching_expiration_removal(cash_ledger, settlement)
        removal_details = (
            parse_expired_futures_option_removal(
                removal.get("description", ""),
            )
            if removal is not None
            else {}
        )
        ledger_amount = parse_number(settlement.get("amount")) or 0.0
        ledger_cash_flow = parse_number(settlement.get("cash_flow")) or 0.0
        ledger_fees = ledger_amount - ledger_cash_flow
        timestamp = pd.to_datetime(
            settlement.get("timestamp"),
            errors="coerce",
        )
        ledger_description = str(settlement.get("description", "") or "")
        removal_description = removal_details.get("removal_description", "")
        description = ledger_description

        if removal_description:
            description = f"{removal_description} | {ledger_description}"

        synthetic_rows.append(
            {
                "Exec Time": format_exec_time(timestamp),
                "Spread": "SINGLE",
                "Side": "CASH",
                "Qty": 0,
                "Pos Effect": "CASH SETTLE",
                "Symbol": removal_details.get(
                    "symbol",
                    "FUTURES CASH SETTLEMENT",
                ),
                "Exp": removal_details.get("expiration", ""),
                "Strike": removal_details.get("strike", ""),
                "Type": removal_details.get("option_type", ""),
                "Price": 0,
                "Net Price": 0,
                "Order Type": "CASH_SETTLE",
                "Order ID": (
                    f"cash-settle-"
                    f"{timestamp.strftime('%Y%m%d%H%M%S')}"
                    if not pd.isna(timestamp)
                    else f"cash-settle-{sequence}"
                ),
                "Strategy_Name": "",
                "statement_file": Path(input_file).name,
                "statement_trade_row": next_synthetic_statement_row(
                    trades,
                    sequence,
                ),
                "synthetic_cash_settlement": True,
                "synthetic_cash_settlement_description": description,
                "synthetic_trade_pnl": ledger_amount,
                "synthetic_fees": ledger_fees,
                "synthetic_net_pnl": ledger_cash_flow,
                "trade_pnl": ledger_amount,
                "fees": ledger_fees,
                "net_pnl": ledger_cash_flow,
                "cash_correction_applied": True,
                "cash_correction_status": "cash_ledger_applied",
                "cash_correction_source": SYNTHETIC_CASH_SETTLEMENT_SOURCE,
                "statement_cash_flow": ledger_cash_flow,
            }
        )

    synthetic = pd.DataFrame(synthetic_rows)
    combined = pd.concat(
        [
            trades,
            synthetic,
        ],
        ignore_index=True,
        sort=False,
    )
    combined = combined.sort_values(
        by="Exec Time",
        key=lambda values: pd.to_datetime(
            values,
            format="%m/%d/%y %H:%M:%S",
            errors="coerce",
        ),
        kind="mergesort",
    ).reset_index(drop=True)

    return restore_synthetic_cash_settlement_values(combined)


def futures_unmatched_close_keys(trades):
    if trades is None or trades.empty:
        return set()

    working = trades.copy()
    working["_trade_time"] = pd.to_datetime(
        working.get("Exec Time"),
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    working["_original_order"] = range(len(working))
    working = working.sort_values(
        ["_trade_time", "_original_order"],
        na_position="last",
    )
    positions = {}
    unmatched = set()

    for _, row in working.iterrows():
        if not is_futures_trade(row):
            continue

        root = normalize_root_symbol(row.get("Symbol"))
        if not root:
            continue

        qty = abs(parse_number(row.get("Qty")) or 0.0)
        if qty <= 0:
            continue

        side = str(row.get("Side", "")).strip().upper()
        pos_effect = str(row.get("Pos Effect", "")).strip().upper()
        direction = 1 if side == "BUY" else -1
        positions.setdefault(root, [])

        if pos_effect == "TO OPEN":
            positions[root].append({
                "direction": direction,
                "qty_remaining": qty,
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        remaining = qty
        open_direction = -direction

        for lot in positions[root]:
            if remaining <= 0:
                break

            if lot["direction"] != open_direction:
                continue

            consumed = min(remaining, lot["qty_remaining"])
            lot["qty_remaining"] -= consumed
            remaining -= consumed

        positions[root] = [
            lot
            for lot in positions[root]
            if lot["qty_remaining"] > 0.000001
        ]

        if remaining > 0.000001:
            unmatched.add(
                (
                    str(row.get("statement_file", "") or ""),
                    row.get("statement_trade_row"),
                )
            )

    return unmatched


def build_cash_trade_corrections(cash_ledger, trades):
    corrections = build_base_cash_trade_corrections(
        cash_ledger,
        trades,
    )

    if corrections is None or corrections.empty:
        return corrections

    unmatched_keys = futures_unmatched_close_keys(trades)
    if not unmatched_keys:
        return corrections

    working = corrections.copy()

    for index, correction in working.iterrows():
        key = (
            str(correction.get("statement_file", "") or ""),
            correction.get("statement_trade_row"),
        )

        if key not in unmatched_keys:
            continue

        ledger_amount = parse_number(correction.get("ledger_amount"))
        ledger_cash_flow = parse_number(correction.get("ledger_cash_flow"))

        if ledger_amount is None or ledger_cash_flow is None:
            continue

        original_trade_pnl = (
            parse_number(correction.get("original_trade_pnl")) or 0.0
        )

        if abs(ledger_amount - original_trade_pnl) <= 0.01:
            continue

        leg_sequence = parse_number(correction.get("event_leg_sequence")) or 1
        if leg_sequence > 1:
            working.at[index, "correction_source"] = (
                "cash_ledger_unmatched_futures_close"
            )
            working.at[index, "corrected_trade_pnl"] = 0.0
            working.at[index, "corrected_fees"] = 0.0
            working.at[index, "corrected_net_pnl"] = 0.0
            continue

        corrected_fees = ledger_amount - ledger_cash_flow
        working.at[index, "correction_source"] = (
            "cash_ledger_unmatched_futures_close"
        )
        working.at[index, "corrected_trade_pnl"] = ledger_amount
        working.at[index, "corrected_fees"] = corrected_fees
        working.at[index, "corrected_net_pnl"] = ledger_cash_flow

    return working.reindex(columns=CASH_CORRECTION_COLUMNS)


def finalize_trades(base_trades, cash_trade_corrections, starting_equity):
    trades = reset_cash_correction_columns(base_trades)
    trades = restore_synthetic_cash_settlement_values(trades)
    trades = apply_cash_trade_corrections(
        trades,
        cash_trade_corrections,
    )
    trades = add_log_return_columns(
        trades,
        starting_equity,
    )
    trades = add_margin_return_columns(trades)
    return trades


def current_corrections_for_candidates(candidates, saved_corrections):
    current = current_statement_approved_corrections(
        candidates,
        saved_corrections,
    )
    return combine_cash_trade_corrections(
        saved_corrections,
        current,
    )


def candidate_is_fee_only(row):
    trade_delta = (
        parse_number(row.get("corrected_trade_pnl")) or 0.0
    ) - (
        parse_number(row.get("original_trade_pnl")) or 0.0
    )
    fee_delta = (
        parse_number(row.get("corrected_fees")) or 0.0
    ) - (
        parse_number(row.get("original_fees")) or 0.0
    )
    net_delta = (
        parse_number(row.get("corrected_net_pnl")) or 0.0
    ) - (
        parse_number(row.get("original_net_pnl")) or 0.0
    )

    return (
        abs(trade_delta) <= 0.01
        and abs(fee_delta) > 0.01
        and abs(net_delta) > 0.01
    )


def add_candidate_review_columns(candidates, approved_corrections=None):
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=list(CASH_CORRECTION_COLUMNS) + [
            "correction_key",
            "is_saved",
            "is_fee_only",
            "likely_cause",
            "trade_pnl_delta",
            "fee_delta",
            "net_pnl_delta",
        ])

    approved_keys = set()

    if approved_corrections is not None and not approved_corrections.empty:
        approved_keys = {
            correction_key(row)
            for row in approved_corrections.to_dict("records")
        }

    working = candidates.copy()
    working["correction_key"] = working.apply(correction_key, axis=1)
    working["is_saved"] = working["correction_key"].isin(approved_keys)
    working["trade_pnl_delta"] = (
        working["corrected_trade_pnl"].apply(parse_number).fillna(0.0)
        - working["original_trade_pnl"].apply(parse_number).fillna(0.0)
    )
    working["fee_delta"] = (
        working["corrected_fees"].apply(parse_number).fillna(0.0)
        - working["original_fees"].apply(parse_number).fillna(0.0)
    )
    working["net_pnl_delta"] = (
        working["corrected_net_pnl"].apply(parse_number).fillna(0.0)
        - working["original_net_pnl"].apply(parse_number).fillna(0.0)
    )
    working["is_fee_only"] = working.apply(candidate_is_fee_only, axis=1)
    working["likely_cause"] = working.apply(
        lambda row: (
            "Estimated fees differ from broker fees"
            if row["is_fee_only"]
            else "Trade amount differs from cash ledger"
            if abs(row["trade_pnl_delta"]) > 0.01
            else "Already saved or no material change"
        ),
        axis=1,
    )

    return working


def normalized_trade_root(value):
    root = normalize_root_symbol(value)
    return str(root or value or "").strip().upper()


def trade_history_year_rows(master_trades, statement_year):
    trades = master_trades.copy()
    trades["_trade_time"] = pd.to_datetime(
        trades.get("Exec Time"),
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    if statement_year:
        trades = trades[
            trades["_trade_time"].dt.year == int(statement_year)
        ].copy()

    return trades


def numeric_sum(frame, column):
    if frame is None or frame.empty or column not in frame.columns:
        return 0.0

    return frame[column].apply(parse_number).fillna(0.0).sum()


def bridge_key_value(value):
    if pd.isna(value):
        return ""

    return str(value).strip()


def bridge_normalized_spread(row):
    spread = bridge_key_value(row.get("Spread")).upper()

    if spread:
        return spread

    strategy_name = bridge_key_value(row.get("Strategy_Name")).upper()

    if "STRADDLE" in strategy_name:
        return "STRADDLE"

    if "VERTICAL" in strategy_name or "SPREAD" in strategy_name:
        return "VERTICAL"

    return spread


def bridge_is_option_trade(row):
    return bridge_key_value(row.get("Type")).upper() in {"CALL", "PUT"}


def bridge_position_key(row, include_strategy=True):
    spread = bridge_normalized_spread(row)
    strategy_key = (
        (bridge_key_value(row.get("Strategy_Name")),)
        if include_strategy
        else ()
    )

    if bridge_is_option_trade(row):
        if spread == "STRADDLE":
            return strategy_key + (
                bridge_key_value(row.get("Symbol")),
                bridge_key_value(row.get("Exp")),
                bridge_key_value(row.get("Strike")),
                spread,
            )

        if spread == "VERTICAL":
            return strategy_key + (
                bridge_key_value(row.get("Symbol")),
                bridge_key_value(row.get("Exp")),
                bridge_key_value(row.get("Type")),
                spread,
            )

        return strategy_key + (
            bridge_key_value(row.get("Symbol")),
            bridge_key_value(row.get("Exp")),
            bridge_key_value(row.get("Strike")),
            bridge_key_value(row.get("Type")),
            spread,
        )

    return strategy_key + (
        bridge_key_value(row.get("Symbol")),
        spread,
    )


def bridge_find_open_lots(open_lots, row, side, require_opposite_side=False):
    key = bridge_position_key(row)
    lots = open_lots.get(key)

    if lots:
        return lots

    fallback_key = bridge_position_key(row, include_strategy=False)
    matches = []

    for candidate_lots in open_lots.values():
        if not candidate_lots:
            continue

        if require_opposite_side and candidate_lots[0]["side"] == side:
            continue

        candidate_row = candidate_lots[0].get("row")

        if candidate_row is None:
            continue

        if bridge_position_key(
            candidate_row,
            include_strategy=False,
        ) == fallback_key:
            matches.append(candidate_lots)

    if len(matches) == 1:
        return matches[0]

    return None


def bridge_match_open_lots(lots, side, qty, drop_same_side=True):
    remaining_qty = qty
    matched_qty = 0.0

    if not lots:
        return matched_qty, remaining_qty

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

        if lot["remaining_qty"] <= 0.000001:
            lots.popleft()

    return matched_qty, remaining_qty


def bridge_prorated_value(row, column, qty):
    total_qty = abs(parse_number(row.get("Qty")) or 0.0)
    value = parse_number(row.get(column))

    if total_qty == 0 or value is None:
        return 0.0

    return value * (qty / total_qty)


def current_open_trade_lots(trades):
    if trades is None or trades.empty:
        return pd.DataFrame(columns=[])

    working = trades.copy()

    if "_trade_time" not in working.columns:
        working["_trade_time"] = pd.to_datetime(
            working.get("Exec Time"),
            format="%m/%d/%y %H:%M:%S",
            errors="coerce",
        )

    working["_original_order"] = range(len(working))
    working = working.sort_values(
        ["_trade_time", "_original_order"],
        na_position="last",
    )
    open_lots = {}

    for _, row in working.iterrows():
        pos_effect = bridge_key_value(row.get("Pos Effect")).upper()
        side = bridge_key_value(row.get("Side")).upper()
        qty = abs(parse_number(row.get("Qty")) or 0.0)

        if qty <= 0:
            continue

        if pos_effect == "TO OPEN":
            lots = bridge_find_open_lots(
                open_lots,
                row,
                side,
                require_opposite_side=True,
            )

            if lots and lots[0]["side"] != side:
                _, remaining_qty = bridge_match_open_lots(
                    lots,
                    side,
                    qty,
                    drop_same_side=False,
                )

                if remaining_qty <= 0.000001:
                    continue

                qty = remaining_qty

            open_lots.setdefault(
                bridge_position_key(row),
                deque(),
            ).append({
                "remaining_qty": qty,
                "side": side,
                "row": row,
            })
            continue

        if pos_effect != "TO CLOSE":
            continue

        lots = bridge_find_open_lots(
            open_lots,
            row,
            side,
            require_opposite_side=True,
        )
        bridge_match_open_lots(
            lots,
            side,
            qty,
        )

    rows = []

    for lots in open_lots.values():
        for lot in lots:
            remaining_qty = lot["remaining_qty"]

            if remaining_qty <= 0.000001:
                continue

            row = dict(lot["row"])
            row["_remaining_qty"] = remaining_qty
            row["_open_lot_trade_pnl"] = bridge_prorated_value(
                lot["row"],
                "trade_pnl",
                remaining_qty,
            )
            row["_open_lot_fees"] = bridge_prorated_value(
                lot["row"],
                "fees",
                remaining_qty,
            )
            row["_open_lot_net_pnl"] = bridge_prorated_value(
                lot["row"],
                "net_pnl",
                remaining_qty,
            )
            rows.append(row)

    return pd.DataFrame(rows)


def build_ytd_bridge_adjustments(
    statement_ytd_summary,
    statement_ytd_positions,
    master_trades,
):
    summary = dict(statement_ytd_summary or {})
    statement_year = summary.get("statement_year")

    if not summary or master_trades is None:
        return pd.DataFrame(columns=[
            "adjustment_category",
            "bridge_type",
            "symbol",
            "normalized_root",
            "gross_pnl_adjustment",
            "fee_adjustment",
            "net_pnl_adjustment",
            "reason",
            "source",
        ])

    trades = trade_history_year_rows(master_trades, statement_year)
    rows = []
    open_exclusion_total = 0.0

    if (
        statement_ytd_positions is not None
        and not statement_ytd_positions.empty
        and not trades.empty
    ):
        trade_roots = trades.get("Symbol", pd.Series(dtype=object)).apply(
            normalized_trade_root
        )
        open_lots = current_open_trade_lots(trades)
        open_lot_roots = (
            open_lots.get("Symbol", pd.Series(dtype=object)).apply(
                normalized_trade_root
            )
            if not open_lots.empty
            else pd.Series(dtype=object)
        )
        position_rows = statement_ytd_positions.copy()
        position_rows["statement_open_pnl"] = (
            position_rows["statement_open_pnl"]
            .apply(parse_number)
            .fillna(0.0)
        )
        position_rows = position_rows[
            position_rows["statement_open_pnl"].abs() > 0.005
        ].copy()

        for _, position in position_rows.iterrows():
            symbol = str(position.get("Symbol", "")).strip()
            root = normalized_trade_root(symbol)
            root_trades = trades[trade_roots == root].copy()

            if root_trades.empty:
                continue

            root_open_lots = (
                open_lots[open_lot_roots == root].copy()
                if not open_lots.empty
                else pd.DataFrame()
            )
            gross_open_cash_pnl = numeric_sum(
                root_open_lots,
                "_open_lot_trade_pnl",
            )

            if abs(gross_open_cash_pnl) <= 0.005:
                continue

            gross_adjustment = -gross_open_cash_pnl
            open_exclusion_total += gross_adjustment
            rows.append({
                "adjustment_category": "open_trade_exclusion",
                "bridge_type": "current_open_premium_exclusion",
                "symbol": symbol,
                "normalized_root": root,
                "gross_pnl_adjustment": gross_adjustment,
                "fee_adjustment": 0.0,
                "net_pnl_adjustment": gross_adjustment,
                "reason": (
                    "Current open-position cash premium is in trade history "
                    "cash flow but is excluded from statement closed YTD PnL."
                ),
                "source": "statement_open_positions",
            })

    statement_closed_gross = summary.get("statement_closed_gross_ytd_pnl")
    trade_history_gross = numeric_sum(trades, "trade_pnl")

    if statement_closed_gross is not None:
        carryover_adjustment = (
            statement_closed_gross
            - trade_history_gross
            - open_exclusion_total
        )

        if abs(carryover_adjustment) > 0.005:
            rows.append({
                "adjustment_category": "ytd_bridge",
                "bridge_type": "prior_year_carryover_adjustment",
                "symbol": "",
                "normalized_root": "",
                "gross_pnl_adjustment": carryover_adjustment,
                "fee_adjustment": 0.0,
                "net_pnl_adjustment": carryover_adjustment,
                "reason": (
                    "Statement closed YTD PnL includes PnL from positions "
                    "opened before the rebuilt trade-history window."
                ),
                "source": "statement_ytd_residual",
            })

    statement_fees = summary.get("statement_total_ytd_commissions_and_fees")
    trade_history_fees = numeric_sum(trades, "fees")

    if statement_fees is not None:
        fee_adjustment = statement_fees - trade_history_fees

        if abs(fee_adjustment) > 0.005:
            rows.append({
                "adjustment_category": "ytd_bridge",
                "bridge_type": "fee_true_up",
                "symbol": "",
                "normalized_root": "",
                "gross_pnl_adjustment": 0.0,
                "fee_adjustment": fee_adjustment,
                "net_pnl_adjustment": -fee_adjustment,
                "reason": (
                    "Broker YTD fees differ from estimated fees in the "
                    "rebuilt trade history."
                ),
                "source": "statement_ytd_fees",
            })

    if not rows:
        return pd.DataFrame(columns=[
            "adjustment_category",
            "bridge_type",
            "symbol",
            "normalized_root",
            "gross_pnl_adjustment",
            "fee_adjustment",
            "net_pnl_adjustment",
            "reason",
            "source",
        ])

    return pd.DataFrame(rows)


def categorized_adjustment_sums(ytd_bridge_adjustments):
    empty = {
        "gross": 0.0,
        "fees": 0.0,
        "net": 0.0,
    }
    if ytd_bridge_adjustments is None or ytd_bridge_adjustments.empty:
        return {
            "open_trade_exclusion": dict(empty),
            "ytd_bridge": dict(empty),
            "total": dict(empty),
        }

    working = ytd_bridge_adjustments.copy()

    if "adjustment_category" not in working.columns:
        working["adjustment_category"] = working["bridge_type"].apply(
            lambda value: (
                "open_trade_exclusion"
                if value == "current_open_premium_exclusion"
                else "ytd_bridge"
            )
        )

    def sums_for(category=None):
        rows = (
            working
            if category is None
            else working[working["adjustment_category"] == category]
        )
        return {
            "gross": numeric_sum(rows, "gross_pnl_adjustment"),
            "fees": numeric_sum(rows, "fee_adjustment"),
            "net": numeric_sum(rows, "net_pnl_adjustment"),
        }

    return {
        "open_trade_exclusion": sums_for("open_trade_exclusion"),
        "ytd_bridge": sums_for("ytd_bridge"),
        "total": sums_for(),
    }


def ytd_bridge_sums(ytd_bridge_adjustments):
    return categorized_adjustment_sums(ytd_bridge_adjustments)["total"]


def ytd_reconciliation_rows(
    statement_ytd_summary,
    master_trades,
    ytd_bridge_adjustments=None,
):
    summary = dict(statement_ytd_summary or {})
    rows = []

    if not summary:
        return pd.DataFrame(columns=[])

    statement_year = summary.get("statement_year")
    trades = trade_history_year_rows(master_trades, statement_year)
    adjustment_sums = categorized_adjustment_sums(ytd_bridge_adjustments)
    open_exclusion = adjustment_sums["open_trade_exclusion"]
    ytd_bridge = adjustment_sums["ytd_bridge"]
    bridge = adjustment_sums["total"]

    trade_history_closed_net_pnl = numeric_sum(trades, "net_pnl")
    trade_history_gross_pnl = numeric_sum(trades, "trade_pnl")
    trade_history_fees = numeric_sum(trades, "fees")
    closed_net_target = summary.get("statement_closed_net_ytd_pnl")
    closed_gross_target = summary.get("statement_closed_gross_ytd_pnl")
    fees_target = summary.get("statement_total_ytd_commissions_and_fees")
    adjusted_closed_net = trade_history_closed_net_pnl + bridge["net"]
    adjusted_gross = trade_history_gross_pnl + bridge["gross"]
    adjusted_fees = trade_history_fees + bridge["fees"]

    rows.append({
        "metric": "closed_net_ytd_pnl",
        "ytd_value": closed_net_target,
        "trade_history_value": trade_history_closed_net_pnl,
        "open_trade_exclusion": open_exclusion["net"],
        "ytd_bridge_adjustment": ytd_bridge["net"],
        "bridge_adjustment": bridge["net"],
        "adjusted_trade_history_value": adjusted_closed_net,
        "difference": (
            closed_net_target - trade_history_closed_net_pnl
            if closed_net_target is not None
            else None
        ),
        "difference_after_bridge": (
            closed_net_target - adjusted_closed_net
            if closed_net_target is not None
            else None
        ),
    })
    rows.append({
        "metric": "closed_gross_ytd_pnl",
        "ytd_value": closed_gross_target,
        "trade_history_value": trade_history_gross_pnl,
        "open_trade_exclusion": open_exclusion["gross"],
        "ytd_bridge_adjustment": ytd_bridge["gross"],
        "bridge_adjustment": bridge["gross"],
        "adjusted_trade_history_value": adjusted_gross,
        "difference": (
            closed_gross_target - trade_history_gross_pnl
            if closed_gross_target is not None
            else None
        ),
        "difference_after_bridge": (
            closed_gross_target - adjusted_gross
            if closed_gross_target is not None
            else None
        ),
    })
    rows.append({
        "metric": "fees_ytd",
        "ytd_value": fees_target,
        "trade_history_value": trade_history_fees,
        "open_trade_exclusion": open_exclusion["fees"],
        "ytd_bridge_adjustment": ytd_bridge["fees"],
        "bridge_adjustment": bridge["fees"],
        "adjusted_trade_history_value": adjusted_fees,
        "difference": (
            fees_target - trade_history_fees
            if fees_target is not None
            else None
        ),
        "difference_after_bridge": (
            fees_target - adjusted_fees
            if fees_target is not None
            else None
        ),
    })

    return pd.DataFrame(rows)


def pending_correction_delta(correction_candidates):
    if correction_candidates is None or correction_candidates.empty:
        return 0.0

    working = correction_candidates.copy()

    if "is_saved" in working.columns:
        saved = working["is_saved"].fillna(False).astype(bool)
        working = working.loc[~saved]

    if working.empty or "net_pnl_delta" not in working.columns:
        return 0.0

    return numeric_sum(working, "net_pnl_delta")


def statement_file_basename(statement_file):
    if statement_file is None or pd.isna(statement_file):
        return ""

    return Path(str(statement_file)).name


def statement_file_date(statement_file):
    basename = statement_file_basename(statement_file)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", basename)

    if not match:
        return ""

    return match.group(1)


def statement_ytd_history_row(statement_ytd_summary):
    row = dict(statement_ytd_summary or {})
    statement_file = row.get("statement_file", "")
    row["statement_basename"] = statement_file_basename(statement_file)
    row["statement_date"] = statement_file_date(statement_file)
    return row


def statement_ytd_history_sort(history):
    if history is None or history.empty:
        return pd.DataFrame(columns=[])

    working = history.copy()

    if "statement_basename" not in working.columns:
        working["statement_basename"] = working.get(
            "statement_file",
            pd.Series("", index=working.index),
        ).apply(statement_file_basename)

    if "statement_date" not in working.columns:
        working["statement_date"] = working.get(
            "statement_file",
            pd.Series("", index=working.index),
        ).apply(statement_file_date)

    working["_statement_date"] = pd.to_datetime(
        working["statement_date"],
        errors="coerce",
    )
    working["_original_order"] = range(len(working))
    return working.sort_values(
        ["_statement_date", "_original_order"],
        na_position="last",
    ).drop(
        columns=["_statement_date", "_original_order"]
    ).reset_index(drop=True)


def statement_ytd_history_from_existing_outputs(paths):
    history = load_output_csv(paths["statement_ytd_history"])
    latest_summary = load_output_csv(paths["statement_ytd_summary"])

    frames = []
    if not history.empty:
        frames.append(history)

    if not latest_summary.empty:
        frames.append(
            pd.DataFrame([
                statement_ytd_history_row(latest_summary.iloc[0].to_dict())
            ])
        )

    if not frames:
        return pd.DataFrame(columns=[])

    combined = pd.concat(frames, ignore_index=True, sort=False)

    if "statement_basename" not in combined.columns:
        combined["statement_basename"] = combined.get(
            "statement_file",
            pd.Series("", index=combined.index),
        ).apply(statement_file_basename)

    combined = combined.drop_duplicates(
        subset=["statement_basename"],
        keep="last",
    )
    return statement_ytd_history_sort(combined)


def update_statement_ytd_history(paths, statement_ytd_summary):
    history = statement_ytd_history_from_existing_outputs(paths)
    current = pd.DataFrame([statement_ytd_history_row(statement_ytd_summary)])
    combined = pd.concat(
        [
            history,
            current,
        ],
        ignore_index=True,
        sort=False,
    )

    if "statement_basename" not in combined.columns:
        combined["statement_basename"] = combined.get(
            "statement_file",
            pd.Series("", index=combined.index),
        ).apply(statement_file_basename)

    combined = combined.drop_duplicates(
        subset=["statement_basename"],
        keep="last",
    )
    combined = statement_ytd_history_sort(combined)
    combined.to_csv(paths["statement_ytd_history"], index=False)
    return combined


def previous_statement_ytd_summary(statement_ytd_summary, ytd_history):
    if ytd_history is None or ytd_history.empty:
        return {}

    current = statement_ytd_history_row(statement_ytd_summary)
    current_basename = current.get("statement_basename", "")
    current_date = pd.to_datetime(
        current.get("statement_date", ""),
        errors="coerce",
    )
    history = statement_ytd_history_sort(ytd_history)
    dates = pd.to_datetime(history.get("statement_date"), errors="coerce")

    if not pd.isna(current_date):
        previous = history[dates < current_date]
    else:
        current_matches = history[
            history.get(
                "statement_basename",
                pd.Series("", index=history.index),
            ).astype(str).eq(str(current_basename))
        ]

        if current_matches.empty:
            previous = history
        else:
            previous = history.loc[: current_matches.index[-1]].iloc[:-1]

    if previous.empty:
        return {}

    return previous.iloc[-1].to_dict()


def statement_ytd_delta_fields(statement_ytd_summary, ytd_history):
    current = dict(statement_ytd_summary or {})
    previous = previous_statement_ytd_summary(current, ytd_history)
    current_net = parse_number(current.get("statement_closed_net_ytd_pnl"))
    previous_net = parse_number(previous.get("statement_closed_net_ytd_pnl"))
    current_gross = parse_number(current.get("statement_gross_ytd_pnl"))
    previous_gross = parse_number(previous.get("statement_gross_ytd_pnl"))

    return {
        "statement_file": current.get("statement_file"),
        "statement_date": statement_file_date(current.get("statement_file")),
        "previous_statement_file": previous.get("statement_file"),
        "previous_statement_date": previous.get("statement_date"),
        "previous_statement_closed_net_ytd_pnl": previous_net,
        "statement_closed_net_ytd_pnl_change_since_previous": (
            current_net - previous_net
            if current_net is not None and previous_net is not None
            else None
        ),
        "previous_statement_gross_ytd_pnl": previous_gross,
        "statement_gross_ytd_pnl_change_since_previous": (
            current_gross - previous_gross
            if current_gross is not None and previous_gross is not None
            else None
        ),
    }


def dashboard_statement_totals(
    statement_ytd_summary,
    ytd_reconciliation,
    correction_candidates=None,
    ytd_history=None,
):
    summary = dict(statement_ytd_summary or {})
    display = {
        "gross_ytd_pnl": summary.get("statement_gross_ytd_pnl"),
        "open_position_pnl": summary.get("statement_open_position_pnl"),
        "closed_gross_ytd_pnl": summary.get("statement_closed_gross_ytd_pnl"),
        "total_ytd_commissions_and_fees": summary.get(
            "statement_total_ytd_commissions_and_fees"
        ),
    }
    display.update(statement_ytd_delta_fields(summary, ytd_history))

    if ytd_reconciliation is not None and not ytd_reconciliation.empty:
        for _, row in ytd_reconciliation.iterrows():
            metric = row["metric"]

            if metric != "closed_net_ytd_pnl":
                continue

            display["statement_closed_net_ytd_pnl"] = row.get("ytd_value")
            display["trade_history_closed_net_ytd_pnl"] = (
                row.get("trade_history_value")
            )
            display["open_trade_exclusion"] = row.get("open_trade_exclusion")
            display["ytd_bridge_adjustment"] = row.get(
                "ytd_bridge_adjustment"
            )
            display["total_closed_pnl_adjustment"] = row.get(
                "bridge_adjustment"
            )
            display["trade_history_adjusted_closed_net_ytd_pnl"] = (
                row.get("adjusted_trade_history_value")
            )
            display["difference_before_adjustments"] = row.get("difference")
            display["difference_after_adjustments"] = (
                row.get("difference_after_bridge")
            )
            display["pending_suggested_correction_delta"] = (
                pending_correction_delta(correction_candidates)
            )

            # Keep legacy keys for older tests/reports that read the CSV-ish
            # payload directly.
            display["trade_history_adjusted_trade_history_closed_net_ytd_pnl"] = (
                row.get("adjusted_trade_history_value")
            )
            display["difference"] = row.get("difference_after_bridge")
            display["difference_before_ytd_bridge"] = row.get("difference")
            display["difference_after_ytd_bridge"] = (
                row.get("difference_after_bridge")
            )

    return pd.DataFrame([display])


def imported_trade_rows(cleaned_trades):
    columns = [
        "strategy_key",
        "row_id",
        "statement_file",
        "statement_trade_row",
        "Exec Time",
        "Strategy_Name",
        "Spread",
        "Side",
        "Qty",
        "Pos Effect",
        "Symbol",
        "Exp",
        "Strike",
        "Type",
        "trade_pnl",
        "fees",
        "net_pnl",
        "cash_correction_applied",
    ]
    trades = add_strategy_review_metadata(cleaned_trades)
    trades = trades.rename(columns={"trade_pnl": "gross_pnl"})
    columns = [
        "gross_pnl" if column == "trade_pnl" else column
        for column in columns
    ]
    return trades[[column for column in columns if column in trades.columns]]


def open_position_rows(statement_ytd_positions):
    if statement_ytd_positions is None or statement_ytd_positions.empty:
        return pd.DataFrame(columns=[
            "Symbol",
            "open_position_pnl",
            "ytd_pnl",
            "closed_gross_pnl",
            "description",
        ])

    positions = statement_ytd_positions.copy()
    positions = positions.rename(
        columns={
            "statement_open_pnl": "open_position_pnl",
            "statement_ytd_pnl": "ytd_pnl",
            "statement_closed_gross_pnl": "closed_gross_pnl",
            "statement_description": "description",
        }
    )
    positions["open_position_pnl"] = (
        positions["open_position_pnl"].apply(parse_number).fillna(0.0)
    )
    positions = positions[
        positions["open_position_pnl"].abs() > 0.005
    ].copy()
    positions["_abs_open_pnl"] = positions["open_position_pnl"].abs()
    positions = positions.sort_values(
        "_abs_open_pnl",
        ascending=False,
    ).drop(columns=["_abs_open_pnl"])

    columns = [
        "Symbol",
        "open_position_pnl",
        "ytd_pnl",
        "closed_gross_pnl",
        "description",
    ]
    return positions[[column for column in columns if column in positions.columns]]


def candidate_group_for_reconciliation_row(row, candidates):
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=[])

    date_mask = candidates["date"].astype(str) == str(row.get("date"))
    account_bucket = str(row.get("account_bucket"))

    if account_bucket == "all_accounts":
        return candidates[date_mask].copy()

    return candidates[
        date_mask
        & (
            candidates["account_bucket"].astype(str)
            == account_bucket
        )
    ].copy()


def add_suggested_reconciliation_columns(reconciliation, candidates):
    if reconciliation is None or reconciliation.empty:
        return reconciliation

    working = reconciliation.copy()
    suggested_adjustments = []
    suggested_net_pnls = []
    suggested_differences = []
    candidate_counts = []

    for _, row in working.iterrows():
        group_candidates = candidate_group_for_reconciliation_row(
            row,
            candidates,
        )

        if not group_candidates.empty and "is_saved" in group_candidates.columns:
            group_candidates = group_candidates[
                ~group_candidates["is_saved"].astype(bool)
            ].copy()

        adjustment = (
            group_candidates.get("net_pnl_delta", pd.Series(dtype=float))
            .apply(parse_number)
            .fillna(0.0)
            .sum()
        )
        extracted_net_pnl = parse_number(row.get("extracted_net_pnl")) or 0.0
        statement_cash_flow = (
            parse_number(row.get("statement_trade_cash_flow")) or 0.0
        )
        suggested_net_pnl = extracted_net_pnl + adjustment
        suggested_difference = suggested_net_pnl - statement_cash_flow

        suggested_adjustments.append(adjustment)
        suggested_net_pnls.append(suggested_net_pnl)
        suggested_differences.append(suggested_difference)
        candidate_counts.append(len(group_candidates))

    working["suggested_adjustment"] = suggested_adjustments
    working["suggested_net_pnl"] = suggested_net_pnls
    working["suggested_difference"] = suggested_differences
    working["correction_candidate_count"] = candidate_counts

    return working


def likely_reconciliation_cause(group, candidates, tolerance=1.0):
    delta = parse_number(group.get("unreconciled_delta")) or 0.0

    if str(group.get("status", "")).strip() == "explained" and bool(
        group.get("settlement_basis_explained", False)
    ):
        return "Futures settlement basis differs from trade PnL"

    if abs(delta) <= tolerance:
        return "Reconciled"

    group_candidates = candidate_group_for_reconciliation_row(
        group,
        candidates,
    )

    if not group_candidates.empty and "is_saved" in group_candidates.columns:
        group_candidates = group_candidates[
            ~group_candidates["is_saved"].astype(bool)
        ].copy()

    suggested_difference = (
        parse_number(group.get("suggested_difference"))
        if "suggested_difference" in group
        else None
    )
    trade_rows = parse_number(group.get("extracted_trade_count")) or 0.0
    ledger_rows = parse_number(group.get("statement_trade_rows")) or 0.0

    if (
        suggested_difference is not None
        and abs(suggested_difference) <= tolerance
        and not group_candidates.empty
        and group_candidates["is_fee_only"].all()
    ):
        return "Estimated fees differ from broker fees"

    if (
        suggested_difference is not None
        and abs(suggested_difference) <= tolerance
        and not group_candidates.empty
    ):
        return "Suggested cash-ledger corrections reconcile this group"

    if trade_rows != ledger_rows:
        return "Missing or extra trade event versus cash ledger"

    if not group_candidates.empty and (
        group_candidates["trade_pnl_delta"].abs() > tolerance
    ).any():
        return "Trade amount differs from cash ledger"

    if (
        suggested_difference is not None
        and abs(suggested_difference) < abs(delta)
    ):
        return "Suggested corrections only partially explain the difference"

    balance_residual = parse_number(group.get("balance_residual")) or 0.0

    if abs(balance_residual) > tolerance:
        return "Cash balance includes non-trade movement"

    return "Manual review needed"


def add_combined_reconciliation_rows(reconciliation):
    if reconciliation is None or reconciliation.empty:
        return reconciliation

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
        "unreconciled_delta",
        "tolerance",
    ]
    combined_rows = []

    for date, group in reconciliation.groupby("date", dropna=False):
        row = {"date": date, "account_bucket": "all_accounts"}

        for column in numeric_columns:
            if column in group.columns:
                row[column] = group[column].apply(parse_number).fillna(0.0).sum()

        row["status"] = (
            "reconciled"
            if abs(row.get("unreconciled_delta", 0.0))
            <= row.get("tolerance", 1.0)
            else "unreconciled"
        )
        combined_rows.append(row)

    return pd.concat(
        [
            pd.DataFrame(combined_rows),
            reconciliation,
        ],
        ignore_index=True,
        sort=False,
    )


def reconciliation_dashboard_rows(reconciliation, candidates, tolerance=1.0):
    if reconciliation is None or reconciliation.empty:
        return pd.DataFrame(columns=[])

    candidates = add_candidate_review_columns(candidates)
    working = add_combined_reconciliation_rows(reconciliation)
    working["difference"] = working["unreconciled_delta"]
    working = add_suggested_reconciliation_columns(
        working,
        candidates,
    )
    working["likely_cause"] = working.apply(
        lambda row: likely_reconciliation_cause(row, candidates, tolerance),
        axis=1,
    )
    working["abs_difference"] = (
        working["difference"].apply(parse_number).fillna(0.0).abs()
    )
    return working.sort_values(
        ["status", "abs_difference", "date", "account_bucket"],
        ascending=[False, False, True, True],
    ).drop(columns=["abs_difference"])


def adjustment_review_rows(candidates, trades):
    columns = [
        "date",
        "account_bucket",
        "timestamp",
        "trade_detail",
        "actual",
        "current",
        "adjustment",
        "adjusted",
        "likely_cause",
        "is_saved",
        "ledger_description",
    ]

    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=columns)

    working = candidates.copy()
    working["statement_trade_row"] = pd.to_numeric(
        working["statement_trade_row"],
        errors="coerce",
    )
    trade_details = pd.DataFrame(columns=[
        "statement_file",
        "statement_trade_row",
        "Exec Time",
        "Strategy_Name",
        "Spread",
        "Side",
        "Qty",
        "Pos Effect",
        "Symbol",
        "Exp",
        "Strike",
        "Type",
    ])

    if trades is not None and not trades.empty:
        trade_details = trades.copy()

        if "statement_trade_row" in trade_details.columns:
            trade_details["statement_trade_row"] = pd.to_numeric(
                trade_details["statement_trade_row"],
                errors="coerce",
            )

    merge_columns = [
        column
        for column in [
            "statement_file",
            "statement_trade_row",
            "Exec Time",
            "Strategy_Name",
            "Spread",
            "Side",
            "Qty",
            "Pos Effect",
            "Symbol",
            "Exp",
            "Strike",
            "Type",
        ]
        if column in trade_details.columns
    ]

    if {
        "statement_file",
        "statement_trade_row",
    }.issubset(trade_details.columns):
        working = pd.merge(
            working,
            trade_details[merge_columns],
            on=["statement_file", "statement_trade_row"],
            how="left",
        )

    material = (
        working["net_pnl_delta"].apply(parse_number).fillna(0.0).abs() > 0.005
    ) | (
        working["trade_pnl_delta"].apply(parse_number).fillna(0.0).abs()
        > 0.005
    ) | (
        working["fee_delta"].apply(parse_number).fillna(0.0).abs() > 0.005
    )
    working = working[material].copy()

    if working.empty:
        return pd.DataFrame(columns=columns)

    def trade_detail(row):
        parts = [
            row.get("Strategy_Name"),
            row.get("Side"),
            row.get("Qty"),
            row.get("Pos Effect"),
            row.get("Symbol"),
            row.get("Exp"),
            row.get("Strike"),
            row.get("Type"),
        ]
        return " ".join(
            str(part).strip()
            for part in parts
            if part is not None
            and not pd.isna(part)
            and str(part).strip() != ""
        )

    review = pd.DataFrame({
        "date": working["date"],
        "account_bucket": working["account_bucket"],
        "timestamp": working.get("ledger_timestamp"),
        "trade_detail": working.apply(trade_detail, axis=1),
        "actual": working["ledger_cash_flow"],
        "current": working["original_net_pnl"],
        "adjustment": working["net_pnl_delta"],
        "adjusted": working["corrected_net_pnl"],
        "likely_cause": working["likely_cause"],
        "is_saved": working["is_saved"],
        "ledger_description": working["ledger_description"],
    })

    review["_sort_time"] = pd.to_datetime(
        review["timestamp"],
        errors="coerce",
    )
    return review.sort_values(
        ["date", "account_bucket", "_sort_time"],
        na_position="last",
    ).drop(columns=["_sort_time"]).reindex(columns=columns)


def dataframe_html(frame, max_rows=None):
    if frame is None or frame.empty:
        return '<div class="empty">No rows.</div>'

    display = frame.copy()

    if max_rows is not None:
        display = display.head(max_rows)

    return display.to_html(
        index=False,
        classes="data-table",
        border=0,
        escape=True,
    )


def clean_strategy_name(value):
    if value is None or pd.isna(value):
        return ""

    return " ".join(str(value).strip().split())


def ensure_strategy_column(df):
    working = df.copy()

    if "Strategy_Name" not in working.columns:
        working["Strategy_Name"] = ""
    else:
        working["Strategy_Name"] = (
            working["Strategy_Name"]
            .astype("object")
            .where(working["Strategy_Name"].notna(), "")
        )

    return working


def strategy_key(row):
    statement_file = str(row.get("statement_file", "") or "").strip()
    statement_trade_row = row.get("statement_trade_row", "")
    row_id = row.get("row_id", "")

    if statement_file and str(statement_trade_row).strip():
        number = parse_number(statement_trade_row)
        row_part = f"{number:g}" if number is not None else str(statement_trade_row)
        return f"{statement_file}|{row_part}"

    return f"row:{row_id}"


def strategy_name_options(master_trades=None):
    names = {clean_strategy_name(value) for value in VALID_MANUAL_STRATEGY_NAMES}

    if master_trades is not None and not master_trades.empty:
        master = ensure_strategy_column(master_trades)
        names.update(
            clean_strategy_name(value)
            for value in master["Strategy_Name"].dropna().tolist()
        )

    names.discard("")
    return sorted(names, key=str.casefold)


def add_strategy_review_metadata(trades):
    if trades is None or trades.empty:
        return pd.DataFrame(columns=[
            "strategy_key",
            "row_id",
            "statement_file",
            "statement_trade_row",
            "Strategy_Name",
        ])

    working = ensure_strategy_column(trades).copy()
    working["row_id"] = working.index.astype(int)
    working["strategy_key"] = working.apply(strategy_key, axis=1)
    return working


def strategy_identity_pairs(trades):
    if trades is None or trades.empty:
        return set()

    working = add_strategy_review_metadata(trades)
    return set(working["strategy_key"].dropna().astype(str))


def untagged_trade_rows(master_trades, exclude_trades=None):
    if master_trades is None or master_trades.empty:
        return []

    working = add_strategy_review_metadata(master_trades)
    excluded = strategy_identity_pairs(exclude_trades)
    strategy_names = working["Strategy_Name"].apply(clean_strategy_name)
    working = working[
        (strategy_names == "")
        & ~working["strategy_key"].isin(excluded)
    ].copy()

    columns = [
        "strategy_key",
        "row_id",
        "statement_file",
        "statement_trade_row",
        "Strategy_Name",
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
        "net_pnl",
    ]
    return json_safe_records(
        working[[column for column in columns if column in working.columns]]
    )


def apply_strategy_updates_to_frame(frame, updates):
    if frame is None or frame.empty:
        return frame, 0

    working = ensure_strategy_column(frame)
    saved_rows = 0

    for update in updates:
        strategy_name = clean_strategy_name(update.get("strategy_name"))

        if not strategy_name:
            continue

        mask = pd.Series(False, index=working.index)
        statement_file = str(update.get("statement_file", "") or "").strip()
        statement_trade_row = parse_number(update.get("statement_trade_row"))

        if (
            statement_file
            and statement_trade_row is not None
            and {"statement_file", "statement_trade_row"}.issubset(
                working.columns
            )
        ):
            row_numbers = pd.to_numeric(
                working["statement_trade_row"],
                errors="coerce",
            )
            mask = (
                working["statement_file"].astype(str).eq(statement_file)
                & row_numbers.eq(statement_trade_row)
            )

        if not mask.any():
            try:
                row_id = int(update.get("row_id"))
            except (TypeError, ValueError):
                row_id = None

            if row_id is not None and row_id in working.index:
                mask.loc[row_id] = True

        if mask.any():
            working.loc[mask, "Strategy_Name"] = strategy_name
            saved_rows += int(mask.sum())

    return working, saved_rows


def save_strategy_updates(paths, updates):
    if not isinstance(updates, list):
        raise ValueError("Strategy updates must be a list")

    master_path = Path(paths["master"])

    if not master_path.exists():
        raise FileNotFoundError(f"Could not find {master_path}")

    master = pd.read_csv(master_path)
    master, master_saved = apply_strategy_updates_to_frame(master, updates)

    if master_saved:
        master.to_csv(master_path, index=False)
        build_equity_curve(master).to_csv(paths["equity_curve"], index=False)
        calculate_summary_statistics(master).to_csv(
            paths["summary_stats"],
            index=False,
        )

    cleaned_saved = 0
    cleaned_path = Path(paths["cleaned"])

    if cleaned_path.exists():
        cleaned = pd.read_csv(cleaned_path)
        cleaned, cleaned_saved = apply_strategy_updates_to_frame(
            cleaned,
            updates,
        )

        if cleaned_saved:
            cleaned.to_csv(cleaned_path, index=False)

    return {
        "saved_rows": master_saved,
        "cleaned_rows": cleaned_saved,
        "strategies": strategy_name_options(master),
    }


def preserve_current_import_strategy_names(cleaned, master):
    if cleaned is None or cleaned.empty:
        return cleaned

    if master is None or master.empty:
        return ensure_strategy_column(cleaned)

    preview = pd.concat(
        [
            master,
            cleaned,
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
        master,
        cleaned,
        strategy_dedupe_columns,
    )


def strategy_series(frame):
    return ensure_strategy_column(frame)["Strategy_Name"].apply(
        clean_strategy_name
    )


def backfill_missing_master_strategy_names(paths, strategy_source_master):
    if not strategy_source_master:
        return 0

    master_path = Path(paths["master"])
    source_path = Path(strategy_source_master)

    if not master_path.exists() or not source_path.exists():
        return 0

    if master_path.resolve() == source_path.resolve():
        return 0

    master = pd.read_csv(master_path)
    source = pd.read_csv(source_path)

    if master.empty or source.empty or "Strategy_Name" not in source.columns:
        return 0

    before = strategy_series(master)
    preview = pd.concat(
        [
            source,
            master,
        ],
        ignore_index=True,
        sort=False,
    )
    strategy_dedupe_columns = [
        column
        for column in get_dedupe_columns(preview)
        if column not in STATEMENT_IDENTITY_COLUMNS
    ]
    enriched = preserve_existing_strategy_names(
        source,
        master,
        strategy_dedupe_columns,
    )
    after = strategy_series(enriched)
    filled = before.eq("") & after.ne("")

    if not filled.any():
        return 0

    enriched.loc[~before.eq(""), "Strategy_Name"] = ensure_strategy_column(
        master
    ).loc[~before.eq(""), "Strategy_Name"]
    enriched.to_csv(master_path, index=False)

    cleaned_path = Path(paths["cleaned"])
    if cleaned_path.exists():
        cleaned = pd.read_csv(cleaned_path)
        cleaned = preserve_current_import_strategy_names(cleaned, enriched)
        cleaned.to_csv(cleaned_path, index=False)

    return int(filled.sum())


def frame_starting_equity(frame):
    if frame is None or frame.empty or "starting_equity" not in frame.columns:
        return None

    values = frame["starting_equity"].apply(parse_number).dropna()

    if values.empty:
        return None

    return float(values.iloc[0])


def refresh_existing_outputs_with_saved_corrections(paths, cash_corrections_path):
    master_path = Path(paths["master"])

    if not master_path.exists():
        return 0

    master = pd.read_csv(master_path)
    if master.empty:
        return 0

    starting_equity = frame_starting_equity(master)
    if starting_equity is None:
        return 0

    corrections = load_cash_trade_corrections(cash_corrections_path)
    refreshed_master = master
    applied = 0

    if not corrections.empty:
        refreshed_master = recalculate_cleaned_trade_columns(
            master,
            starting_equity,
            cash_trade_corrections=corrections,
        )
        applied = int(refreshed_master.attrs.get("cash_corrections_applied", 0))

        if applied > 0:
            refreshed_master.to_csv(master_path, index=False)
            build_equity_curve(refreshed_master).to_csv(
                paths["equity_curve"],
                index=False,
            )
            calculate_summary_statistics(refreshed_master).to_csv(
                paths["summary_stats"],
                index=False,
            )

    cleaned_path = Path(paths["cleaned"])
    if cleaned_path.exists():
        cleaned = pd.read_csv(cleaned_path)
        if statement_identity_keys(cleaned):
            refreshed_cleaned = select_current_import_rows(
                refreshed_master,
                cleaned,
            )
        else:
            cleaned_starting_equity = (
                frame_starting_equity(cleaned) or starting_equity
            )
            refreshed_cleaned = recalculate_cleaned_trade_columns(
                cleaned,
                cleaned_starting_equity,
                cash_trade_corrections=corrections,
            )
        refreshed_cleaned = preserve_current_import_strategy_names(
            refreshed_cleaned,
            refreshed_master,
        )
        refreshed_cleaned.to_csv(cleaned_path, index=False)

    statement_summary = load_output_csv(paths["statement_ytd_summary"])
    if not statement_summary.empty:
        statement_ytd_summary = statement_summary.iloc[0].to_dict()
        statement_ytd_positions = load_output_csv(
            paths["statement_ytd_positions"]
        )
        ytd_bridge_adjustments = build_ytd_bridge_adjustments(
            statement_ytd_summary,
            statement_ytd_positions,
            refreshed_master,
        )
        ytd_reconciliation = ytd_reconciliation_rows(
            statement_ytd_summary,
            refreshed_master,
            ytd_bridge_adjustments,
        )
        ytd_bridge_adjustments.to_csv(
            paths["ytd_bridge_adjustments"],
            index=False,
        )
        ytd_reconciliation.to_csv(
            paths["ytd_reconciliation"],
            index=False,
        )

    return applied


def refresh_statement_ytd_outputs(paths, input_file):
    master_path = Path(paths["master"])

    if not master_path.exists():
        return False

    master = pd.read_csv(master_path)
    if master.empty:
        return False

    lines = Path(input_file).read_text(errors="replace").splitlines(True)
    statement_ytd_summary, statement_ytd_positions = (
        parse_statement_ytd_summary(lines, input_file)
    )
    ytd_bridge_adjustments = build_ytd_bridge_adjustments(
        statement_ytd_summary,
        statement_ytd_positions,
        master,
    )
    ytd_reconciliation = ytd_reconciliation_rows(
        statement_ytd_summary,
        master,
        ytd_bridge_adjustments,
    )

    update_statement_ytd_history(paths, statement_ytd_summary)
    pd.DataFrame([statement_ytd_summary]).to_csv(
        paths["statement_ytd_summary"],
        index=False,
    )
    statement_ytd_positions.to_csv(
        paths["statement_ytd_positions"],
        index=False,
    )
    ytd_bridge_adjustments.to_csv(
        paths["ytd_bridge_adjustments"],
        index=False,
    )
    ytd_reconciliation.to_csv(
        paths["ytd_reconciliation"],
        index=False,
    )

    return True


def write_outputs(context, cash_trade_corrections):
    os.makedirs(context.output_dir, exist_ok=True)
    master = update_master_cleaned_trades(
        context.base_trades.copy(),
        context.paths["master"],
        context.master_starting_equity,
        cash_trade_corrections=cash_trade_corrections,
        start_date=None,
    )
    cleaned = select_current_import_rows(
        master,
        context.base_trades,
    )
    reconciliation_trades = cash_reconciliation_trade_frame(
        context,
        master,
        cleaned,
    )
    reconciliation = reconcile_cash_balances(
        context.lines,
        reconciliation_trades,
        tolerance=context.cash_validation_tolerance,
        start_date=context.cash_reconciliation_start_date,
        end_date=context.cash_reconciliation_end_date,
        include_futures_mtm_adjustments=False,
    )
    reconciliation = annotate_futures_settlement_basis(
        reconciliation,
        context.cash_ledger,
        reconciliation_trades,
        context.cash_validation_tolerance,
    )
    equity_curve = build_equity_curve(master)
    summary_statistics = calculate_summary_statistics(master)
    ytd_bridge_adjustments = build_ytd_bridge_adjustments(
        context.statement_ytd_summary,
        context.statement_ytd_positions,
        master,
    )
    ytd_reconciliation = ytd_reconciliation_rows(
        context.statement_ytd_summary,
        master,
        ytd_bridge_adjustments,
    )

    update_statement_ytd_history(
        context.paths,
        context.statement_ytd_summary,
    )
    cleaned.to_csv(context.paths["cleaned"], index=False)
    master.to_csv(context.paths["master"], index=False)
    equity_curve.to_csv(context.paths["equity_curve"], index=False)
    summary_statistics.to_csv(context.paths["summary_stats"], index=False)
    pd.DataFrame([context.statement_ytd_summary]).to_csv(
        context.paths["statement_ytd_summary"],
        index=False,
    )
    context.statement_ytd_positions.to_csv(
        context.paths["statement_ytd_positions"],
        index=False,
    )
    reconciliation.to_csv(context.paths["cash_reconciliation"], index=False)
    reconciliation.to_csv(
        context.paths["daily_trade_cash_reconciliation"],
        index=False,
    )
    summarize_cash_reconciliation(reconciliation).to_csv(
        context.paths["cash_reconciliation_summary"],
        index=False,
    )
    ytd_reconciliation.to_csv(
        context.paths["ytd_reconciliation"],
        index=False,
    )
    ytd_bridge_adjustments.to_csv(
        context.paths["ytd_bridge_adjustments"],
        index=False,
    )
    save_pnl_chart(master, output_file=context.paths["pnl_chart"])

    return {
        "cleaned": cleaned,
        "master": master,
        "equity_curve": equity_curve,
        "summary_statistics": summary_statistics,
        "reconciliation": reconciliation,
        "ytd_reconciliation": ytd_reconciliation,
        "ytd_bridge_adjustments": ytd_bridge_adjustments,
    }


def existing_master_for_preview(context):
    master_path = Path(context.paths["master"])

    if master_path.exists():
        return pd.read_csv(master_path)

    return pd.DataFrame(columns=context.base_trades.columns)


def normalized_statement_row(value):
    number = parse_number(value)

    if number is not None:
        return str(int(number)) if float(number).is_integer() else str(number)

    return str(value or "").strip()


def statement_identity_keys(trades):
    if (
        trades is None
        or trades.empty
        or "statement_file" not in trades.columns
        or "statement_trade_row" not in trades.columns
    ):
        return set()

    return {
        (
            str(row.get("statement_file", "") or "").strip(),
            normalized_statement_row(row.get("statement_trade_row")),
        )
        for row in trades.to_dict("records")
    }


def select_current_import_rows(master_trades, current_import_trades):
    if master_trades is None or master_trades.empty:
        return master_trades

    keys = statement_identity_keys(current_import_trades)

    if not keys:
        return master_trades.tail(len(current_import_trades)).copy()

    working = master_trades.copy()
    mask = working.apply(
        lambda row: (
            str(row.get("statement_file", "") or "").strip(),
            normalized_statement_row(row.get("statement_trade_row")),
        )
        in keys,
        axis=1,
    )

    return working.loc[mask].copy()


def trade_rows_in_reconciliation_window(trades, start_date, end_date):
    if trades is None or trades.empty:
        return trades

    working = trades.copy()
    trade_times = pd.to_datetime(
        working.get("Exec Time"),
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    keep = trade_times.notna()

    if start_date is not None:
        keep &= trade_times >= start_date

    if end_date is not None:
        keep &= trade_times < end_date + pd.Timedelta(days=1)

    return working.loc[keep].copy()


def cash_reconciliation_trade_frame(context, master, cleaned):
    if not context.skipped_overlap_trades:
        return cleaned

    window_trades = trade_rows_in_reconciliation_window(
        master,
        context.cash_reconciliation_start_date,
        context.cash_reconciliation_end_date,
    )

    if window_trades is None or window_trades.empty:
        return cleaned

    return window_trades


def master_aware_trade_frame(context, cash_trade_corrections=None):
    existing = existing_master_for_preview(context)

    if existing is None or existing.empty:
        return finalize_trades(
            context.base_trades,
            cash_trade_corrections,
            context.starting_equity,
        )

    preview = pd.concat(
        [
            existing,
            context.base_trades,
        ],
        ignore_index=True,
        sort=False,
    )

    dedupe_columns = get_dedupe_columns(preview)
    if dedupe_columns:
        strategy_dedupe_columns = [
            column
            for column in dedupe_columns
            if column not in STATEMENT_IDENTITY_COLUMNS
        ]
        base_trades = preserve_existing_strategy_names(
            existing,
            context.base_trades,
            strategy_dedupe_columns,
        )
        existing = drop_existing_replaced_real_trades(
            existing,
            base_trades,
            dedupe_columns,
        )
        existing = existing.copy()
        base_trades = base_trades.copy()
        existing["_dedupe_key"] = build_dedupe_key(
            existing,
            dedupe_columns,
        )
        base_trades["_dedupe_key"] = build_dedupe_key(
            base_trades,
            dedupe_columns,
        )
        existing = existing.loc[
            ~existing["_dedupe_key"].isin(base_trades["_dedupe_key"])
        ].drop(columns=["_dedupe_key"])
        base_trades = base_trades.drop(columns=["_dedupe_key"])
        combined = pd.concat(
            [
                existing,
                base_trades,
            ],
            ignore_index=True,
            sort=False,
        )
    else:
        combined = preview

    return recalculate_cleaned_trade_columns(
        combined,
        context.master_starting_equity,
        cash_trade_corrections=cash_trade_corrections,
    )


def master_aware_current_import_trades(context, cash_trade_corrections=None):
    master = master_aware_trade_frame(
        context,
        cash_trade_corrections,
    )
    return select_current_import_rows(
        master,
        context.base_trades,
    )


def futures_settlement_basis_by_day(cash_ledger, trades):
    candidates = build_base_cash_trade_corrections(
        cash_ledger,
        trades,
    )

    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=[
            "date",
            "account_bucket",
            "futures_settlement_basis_delta",
        ])

    working = candidates[
        candidates["account_bucket"].astype(str).eq("futures")
    ].copy()
    if working.empty:
        return pd.DataFrame(columns=[
            "date",
            "account_bucket",
            "futures_settlement_basis_delta",
        ])

    event_columns = [
        "date",
        "account_bucket",
        "event_sequence",
        "ledger_timestamp",
        "ledger_description",
    ]
    event_rows = []

    for key, event in working.groupby(event_columns, dropna=False):
        event_rows.append({
            "date": key[0],
            "account_bucket": key[1],
            "futures_settlement_basis_delta": (
                event["original_net_pnl"].apply(parse_number).fillna(0.0).sum()
                - (parse_number(event["ledger_cash_flow"].iloc[0]) or 0.0)
            ),
        })

    if not event_rows:
        return pd.DataFrame(columns=[
            "date",
            "account_bucket",
            "futures_settlement_basis_delta",
        ])

    return (
        pd.DataFrame(event_rows)
        .groupby(["date", "account_bucket"], as_index=False)[
            "futures_settlement_basis_delta"
        ]
        .sum()
    )


def annotate_futures_settlement_basis(
    reconciliation,
    cash_ledger,
    trades,
    tolerance,
):
    if reconciliation is None or reconciliation.empty:
        return reconciliation

    basis = futures_settlement_basis_by_day(
        cash_ledger,
        trades,
    )
    working = reconciliation.copy()
    working = working.merge(
        basis,
        on=["date", "account_bucket"],
        how="left",
    )
    working["futures_settlement_basis_delta"] = (
        pd.to_numeric(
            working["futures_settlement_basis_delta"],
            errors="coerce",
        )
        .fillna(0.0)
    )
    working["futures_settlement_residual"] = (
        working["unreconciled_delta"].apply(parse_number).fillna(0.0)
        - working["futures_settlement_basis_delta"]
    )
    explained_mask = (
        working["account_bucket"].astype(str).eq("futures")
        & working["status"].astype(str).eq("unreconciled")
        & working["futures_settlement_basis_delta"].abs().gt(tolerance)
        & working["futures_settlement_residual"].abs().le(tolerance)
    )
    working["settlement_basis_explained"] = False
    working.loc[explained_mask, "status"] = "explained"
    working.loc[explained_mask, "settlement_basis_explained"] = True

    return working


def write_preview_outputs(context, cash_trade_corrections):
    os.makedirs(context.output_dir, exist_ok=True)
    master = master_aware_trade_frame(
        context,
        cash_trade_corrections,
    )
    cleaned = select_current_import_rows(
        master,
        context.base_trades,
    )
    reconciliation_trades = cash_reconciliation_trade_frame(
        context,
        master,
        cleaned,
    )
    reconciliation = reconcile_cash_balances(
        context.lines,
        reconciliation_trades,
        tolerance=context.cash_validation_tolerance,
        start_date=context.cash_reconciliation_start_date,
        end_date=context.cash_reconciliation_end_date,
        include_futures_mtm_adjustments=False,
    )
    reconciliation = annotate_futures_settlement_basis(
        reconciliation,
        context.cash_ledger,
        reconciliation_trades,
        context.cash_validation_tolerance,
    )
    ytd_bridge_adjustments = build_ytd_bridge_adjustments(
        context.statement_ytd_summary,
        context.statement_ytd_positions,
        master,
    )
    ytd_reconciliation = ytd_reconciliation_rows(
        context.statement_ytd_summary,
        master,
        ytd_bridge_adjustments,
    )

    update_statement_ytd_history(
        context.paths,
        context.statement_ytd_summary,
    )
    cleaned.to_csv(context.paths["cleaned"], index=False)
    reconciliation.to_csv(context.paths["cash_reconciliation"], index=False)
    reconciliation.to_csv(
        context.paths["daily_trade_cash_reconciliation"],
        index=False,
    )
    summarize_cash_reconciliation(reconciliation).to_csv(
        context.paths["cash_reconciliation_summary"],
        index=False,
    )
    pd.DataFrame([context.statement_ytd_summary]).to_csv(
        context.paths["statement_ytd_summary"],
        index=False,
    )
    context.statement_ytd_positions.to_csv(
        context.paths["statement_ytd_positions"],
        index=False,
    )
    ytd_reconciliation.to_csv(
        context.paths["ytd_reconciliation"],
        index=False,
    )
    ytd_bridge_adjustments.to_csv(
        context.paths["ytd_bridge_adjustments"],
        index=False,
    )

    return {
        "cleaned": cleaned,
        "master": master,
        "reconciliation": reconciliation,
        "ytd_reconciliation": ytd_reconciliation,
        "ytd_bridge_adjustments": ytd_bridge_adjustments,
    }


def strict_daily_failures(reconciliation):
    if reconciliation is None or reconciliation.empty:
        return pd.DataFrame(columns=[])

    return reconciliation[
        reconciliation["status"].astype(str).eq("unreconciled")
    ].copy()


def html_payload(
    context,
    outputs,
    correction_candidates,
    saved_corrections,
):
    candidates = add_candidate_review_columns(
        correction_candidates,
        saved_corrections,
    )
    reconciliation_rows = reconciliation_dashboard_rows(
        outputs["reconciliation"],
        candidates,
        tolerance=context.cash_validation_tolerance,
    )
    statement_totals = dashboard_statement_totals(
        context.statement_ytd_summary,
        outputs["ytd_reconciliation"],
        candidates,
        load_output_csv(context.paths["statement_ytd_history"]),
    )

    return {
        "statementTotals": json_safe_records(statement_totals),
        "ytdBridge": json_safe_records(
            outputs.get("ytd_bridge_adjustments")
        ),
        "strategyNames": strategy_name_options(outputs.get("master")),
        "newTrades": json_safe_records(imported_trade_rows(outputs["cleaned"])),
        "untaggedTrades": untagged_trade_rows(outputs.get("master")),
        "openPositions": json_safe_records(
            open_position_rows(context.statement_ytd_positions)
        ),
        "reconciliation": json_safe_records(reconciliation_rows),
        "adjustmentReview": json_safe_records(
            adjustment_review_rows(
                candidates,
                outputs["cleaned"],
            )
        ),
        "correctionCandidates": json_safe_records(candidates),
        "serverEnabled": False,
    }


def load_output_csv(path, columns=None):
    path = Path(path)

    if not path.exists():
        return pd.DataFrame(columns=columns or [])

    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns or [])


def dashboard_payload_from_existing_outputs(
    paths,
    cash_validation_tolerance=1.0,
    cash_corrections_path=None,
):
    statement_summary = load_output_csv(paths["statement_ytd_summary"])
    statement_ytd_summary = (
        statement_summary.iloc[0].to_dict()
        if not statement_summary.empty
        else {}
    )
    context = SimpleNamespace(
        paths=paths,
        statement_ytd_summary=statement_ytd_summary,
        statement_ytd_positions=load_output_csv(
            paths["statement_ytd_positions"]
        ),
        cash_validation_tolerance=cash_validation_tolerance,
    )
    outputs = {
        "cleaned": load_output_csv(paths["cleaned"]),
        "master": load_output_csv(paths["master"]),
        "reconciliation": load_output_csv(paths["cash_reconciliation"]),
        "ytd_reconciliation": load_output_csv(paths["ytd_reconciliation"]),
        "ytd_bridge_adjustments": load_output_csv(
            paths["ytd_bridge_adjustments"]
        ),
    }
    outputs["cleaned"] = preserve_current_import_strategy_names(
        outputs["cleaned"],
        outputs["master"],
    )
    correction_candidates = load_output_csv(
        paths["cash_trade_correction_candidates"],
        columns=CASH_CORRECTION_COLUMNS,
    )
    saved_corrections = load_output_csv(
        cash_corrections_path or paths["cash_trade_corrections"],
        columns=CASH_CORRECTION_COLUMNS,
    )

    return html_payload(
        context,
        outputs,
        correction_candidates,
        saved_corrections,
    )


def save_corrections_to_existing_outputs(
    paths,
    submitted,
    dashboard_path,
    cash_validation_tolerance=1.0,
    cash_corrections_path=None,
):
    corrections_path = cash_corrections_path or paths["cash_trade_corrections"]
    submitted = (
        submitted
        if submitted is not None and not submitted.empty
        else pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)
    )
    submitted = submitted.reindex(columns=CASH_CORRECTION_COLUMNS)
    existing = load_cash_trade_corrections(corrections_path)
    combined = combine_cash_trade_corrections(existing, submitted)
    save_cash_trade_corrections(corrections_path, combined)

    applied = refresh_existing_outputs_with_saved_corrections(
        paths,
        corrections_path,
    )
    candidates = load_output_csv(
        paths["cash_trade_correction_candidates"],
        columns=CASH_CORRECTION_COLUMNS,
    )
    candidates = add_candidate_review_columns(candidates, combined)
    candidates.to_csv(
        paths["cash_trade_correction_candidates"],
        index=False,
    )
    refreshed_payload = dashboard_payload_from_existing_outputs(
        paths,
        cash_validation_tolerance=cash_validation_tolerance,
        cash_corrections_path=corrections_path,
    )
    write_dashboard(
        dashboard_path,
        refreshed_payload,
        server_enabled=True,
    )

    return {
        "saved_rows": len(combined),
        "submitted_rows": len(submitted),
        "master_updated": True,
        "applied_rows": applied,
        "message": (
            f"Saved {len(combined)} correction rows and refreshed existing "
            "v2 CSV outputs."
        ),
    }


def dashboard_html(payload, server_enabled=False):
    payload = dict(payload)
    payload["serverEnabled"] = bool(server_enabled)
    payload_json = json.dumps(payload, allow_nan=False)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trade History V2 Reconciliation</title>
  <style>
    :root {{
      --bg: #f3f5f6;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #657280;
      --line: #d8e0e6;
      --accent: #14606f;
      --accent-dark: #0d4954;
      --good: #137a4b;
      --good-bg: #e8f6ee;
      --bad: #b42318;
      --bad-bg: #fff1f0;
      --warn: #9a5b00;
      --warn-bg: #fff7e6;
      --soft: #f8fafb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.42;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(255, 255, 255, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 14px 20px;
      backdrop-filter: blur(8px);
    }}
    main {{
      max-width: 1840px;
      margin: 0 auto;
      padding: 16px 20px 30px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 22px; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    .header-row,
    .section-head,
    .actions {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .actions {{ justify-content: flex-start; }}
    button {{
      border: 1px solid var(--accent);
      border-radius: 7px;
      padding: 8px 11px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-weight: 750;
      font-size: 12px;
    }}
    button:hover {{ background: var(--accent-dark); }}
    button.secondary {{
      background: #fff;
      border-color: #b9c6d0;
      color: var(--ink);
    }}
    button.secondary:hover {{ background: var(--soft); }}
    button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
    select,
    input[type="text"] {{
      border: 1px solid #b9c6d0;
      border-radius: 7px;
      padding: 7px 8px;
      background: #fff;
      color: var(--ink);
      font-size: 12px;
      min-width: 170px;
    }}
    .strategy-controls {{
      display: flex;
      gap: 6px;
      align-items: center;
      min-width: 360px;
    }}
    .strategy-controls input[type="text"] {{
      min-width: 150px;
      max-width: 190px;
    }}
    .subtle {{ color: var(--muted); font-size: 12px; }}
    .banner {{
      margin-top: 10px;
      padding: 9px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      color: var(--muted);
      font-size: 13px;
    }}
    .banner.good {{ background: var(--good-bg); color: var(--good); border-color: #9bd8b5; }}
    .banner.warn {{ background: #fff7db; color: #7a4b00; border-color: #e8c86f; }}
    .banner.bad {{ background: var(--bad-bg); color: var(--bad); border-color: #f0aaa4; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      overflow: hidden;
      box-shadow: 0 8px 22px rgba(23, 32, 42, 0.05);
    }}
    .section-head {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--soft);
    }}
    .table-wrap {{
      overflow: auto;
      max-height: 460px;
    }}
    .statement-totals {{
      padding: 12px;
      display: grid;
      gap: 12px;
    }}
    .totals-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
    }}
    .total-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      padding: 10px;
    }}
    .total-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      text-transform: uppercase;
    }}
    .total-value {{
      margin-top: 4px;
      font-size: 20px;
      font-weight: 850;
    }}
    table {{
      width: 100%;
      min-width: 920px;
      border-collapse: separate;
      border-spacing: 0;
    }}
    th, td {{
      padding: 8px 9px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
      font-size: 12px;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      z-index: 3;
      background: #edf3f5;
      color: #435260;
      font-size: 11px;
      font-weight: 850;
      text-transform: uppercase;
    }}
    td:first-child, th:first-child,
    td:nth-child(2), th:nth-child(2) {{
      text-align: left;
    }}
    .empty {{ padding: 14px; color: var(--muted); }}
    .recon-list {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
      padding: 12px;
    }}
    .recon-card {{
      display: grid;
      grid-template-columns: minmax(210px, 300px) minmax(0, 1fr);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }}
    .difference {{
      padding: 13px;
      background: var(--bad-bg);
      border-right: 1px solid var(--line);
    }}
    .difference.good {{ background: var(--good-bg); }}
    .difference .value {{
      margin-top: 4px;
      font-size: 24px;
      font-weight: 850;
      color: var(--bad);
    }}
    .difference.good .value {{ color: var(--good); }}
    .recon-detail {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
      gap: 8px;
      padding: 12px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px;
      background: var(--soft);
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .metric-value {{
      margin-top: 3px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }}
    .save-grid {{
      padding: 12px;
    }}
    .checkbox-cell {{ text-align: center; }}
    .wide {{
      max-width: 420px;
      min-width: 260px;
      white-space: normal;
      text-align: left;
    }}
    .money-positive {{ color: var(--good); font-weight: 750; }}
    .money-negative {{ color: var(--bad); font-weight: 750; }}
    @media (max-width: 900px) {{
      .recon-card {{ grid-template-columns: 1fr; }}
      .difference {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      main, header {{ padding-left: 12px; padding-right: 12px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>Trade History V2 Reconciliation</h1>
        <div class="subtle">Daily trade history validation against cash and sweep, futures, forex, crypto cash balances, and YTD statement totals.</div>
      </div>
      <div class="actions">
        <button id="save-corrections">Save selected corrections</button>
        <button class="secondary" id="save-strategies">Save strategy names</button>
        <button class="secondary" id="select-fee-only">Select fee-only</button>
        <button class="secondary" id="clear-selection">Clear selection</button>
        <button class="secondary" id="exit-dashboard">Exit</button>
      </div>
    </div>
    <div class="banner" id="banner">Corrections are saved only when this dashboard is served by the v2 script.</div>
  </header>
  <main>
    <section class="panel">
      <div class="section-head">
        <h2>Statement Totals</h2>
        <div class="subtle">Closed net YTD comparison after known trade-history adjustments.</div>
      </div>
      <div id="statement-totals"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Suggested Corrections</h2>
        <div class="subtle">Fee-only corrections are selected by default; trade amount differences are left unchecked for review.</div>
      </div>
      <div class="save-grid">
        <div class="table-wrap" id="corrections"></div>
      </div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>New Trades Imported</h2>
        <div class="subtle" id="new-trade-count"></div>
      </div>
      <div class="table-wrap" id="new-trades"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Untagged Trades</h2>
        <div class="subtle" id="untagged-trade-count"></div>
      </div>
      <div class="table-wrap" id="untagged-trades"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Open Positions</h2>
        <div class="subtle">Statement positions with nonzero open PnL.</div>
      </div>
      <div class="table-wrap" id="open-positions"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Reconciliation</h2>
        <div class="subtle">Actionable adjustment rows only. Actual is the broker cash-ledger value; current is the extracted trade-history value.</div>
      </div>
      <div class="table-wrap" id="reconciliation"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>YTD Calculation Detail</h2>
        <div class="subtle">Source values and adjustments used for the Statement Totals comparison.</div>
      </div>
      <div class="table-wrap" id="statement-detail"></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>Closed PnL Adjustments</h2>
        <div class="subtle">Separates open-trade cash-flow exclusions from true YTD bridge residuals without changing cash-accurate trade rows.</div>
      </div>
      <div class="table-wrap" id="ytd-bridge"></div>
    </section>
  </main>
  <script>
    const DATA = {payload_json};
    const selected = new Set();
    const dirtyStrategyRows = new Set();
    const strategyRowsByKey = new Map();
    let dirty = false;
    let savedSinceLoad = false;
    const moneyColumns = new Set([
      'gross_ytd_pnl','open_position_pnl','closed_gross_ytd_pnl',
      'total_ytd_commissions_and_fees','closed_net_ytd_pnl',
      'trade_history_closed_net_ytd_pnl','closed_net_ytd_pnl_difference',
      'trade_history_closed_gross_ytd_pnl','closed_gross_ytd_pnl_difference',
      'trade_history_fees_ytd','fees_ytd_difference',
      'bridge_adjustment','adjusted_trade_history_value',
      'difference_after_bridge','gross_pnl_adjustment','fee_adjustment',
      'net_pnl_adjustment',
      'actual','current','adjustment','adjusted',
      'statement_closed_net_ytd_pnl',
      'trade_history_adjusted_trade_history_closed_net_ytd_pnl',
      'trade_history_closed_net_ytd_pnl',
      'open_trade_exclusion',
      'ytd_bridge_adjustment',
      'total_closed_pnl_adjustment',
      'trade_history_adjusted_closed_net_ytd_pnl',
      'difference_before_adjustments',
      'difference_after_adjustments',
      'difference_before_ytd_bridge',
      'difference_after_ytd_bridge',
      'pending_suggested_correction_delta',
      'difference',
      'previous_statement_closed_net_ytd_pnl',
      'statement_closed_net_ytd_pnl_change_since_previous',
      'previous_statement_gross_ytd_pnl',
      'statement_gross_ytd_pnl_change_since_previous',
      'gross_pnl','fees','net_pnl','open_position_pnl','ytd_pnl',
      'closed_gross_pnl','difference','statement_trade_cash_flow',
      'extracted_net_pnl','suggested_net_pnl','suggested_adjustment',
      'suggested_difference','balance_residual','ledger_cash_flow',
      'original_trade_pnl','original_fees','original_net_pnl',
      'corrected_trade_pnl','corrected_fees','corrected_net_pnl',
      'trade_pnl_delta','fee_delta','net_pnl_delta'
    ]);
    const wideColumns = new Set(['ledger_description', 'likely_cause', 'description', 'reason', 'trade_detail']);
    function isMoneyColumn(column) {{
      return moneyColumns.has(column)
        || column.endsWith('_bridge_adjustment')
        || column.startsWith('adjusted_trade_history_')
        || column.endsWith('_difference_after_bridge');
    }}

    function escapeHtml(value) {{
      const text = value === null || value === undefined ? '' : String(value);
      const map = {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }};
      return text.replace(/[&<>"']/g, char => map[char]);
    }}
    function money(value) {{
      const number = Number(value || 0);
      return number.toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    }}
    function formatValue(column, value) {{
      if (value === null || value === undefined || value === '') return '';
      if (typeof value === 'boolean') return value ? 'Yes' : 'No';
      if (isMoneyColumn(column)) return money(value);
      return escapeHtml(value);
    }}
    function cellClass(column, value) {{
      const classes = [];
      if (wideColumns.has(column)) classes.push('wide');
      if (isMoneyColumn(column)) {{
        const number = Number(value);
        if (Number.isFinite(number) && number > 0) classes.push('money-positive');
        if (Number.isFinite(number) && number < 0) classes.push('money-negative');
      }}
      return classes.length ? ` class="${{classes.join(' ')}}"` : '';
    }}
    function renderTable(targetId, rows, columns, options = {{}}) {{
      const target = document.getElementById(targetId);
      if (!rows.length) {{
        target.innerHTML = '<div class="empty">No rows.</div>';
        return;
      }}
      const header = columns.map(column => `<th>${{escapeHtml(column)}}</th>`).join('');
      const body = rows.map(row => {{
        const cells = columns.map(column => {{
          if (column === '_select') {{
            const key = row.correction_key;
            return `<td class="checkbox-cell"><input type="checkbox" data-key="${{escapeHtml(key)}}" ${{selected.has(key) ? 'checked' : ''}}></td>`;
          }}
          return `<td${{cellClass(column, row[column])}}>${{formatValue(column, row[column])}}</td>`;
        }}).join('');
        return `<tr>${{cells}}</tr>`;
      }}).join('');
      target.innerHTML = `<table><thead><tr>${{header}}</tr></thead><tbody>${{body}}</tbody></table>`;
      if (options.checkboxes) {{
        target.querySelectorAll('input[type="checkbox"]').forEach(box => {{
          box.addEventListener('change', event => {{
            const key = event.currentTarget.dataset.key;
            if (event.currentTarget.checked) selected.add(key);
            else selected.delete(key);
            dirty = true;
            updateBanner();
          }});
        }});
      }}
    }}
    function strategyOptions(selectedValue = '') {{
      const selectedValueText = String(selectedValue || '');
      const options = ['<option value="">Select strategy...</option>'];
      (DATA.strategyNames || []).forEach(strategy => {{
        const selectedAttr = strategy === selectedValueText ? ' selected' : '';
        options.push(`<option value="${{escapeHtml(strategy)}}"${{selectedAttr}}>${{escapeHtml(strategy)}}</option>`);
      }});
      return options.join('');
    }}
    function controlsForStrategyKey(attributeName, key) {{
      return Array.from(document.querySelectorAll(`[${{attributeName}}]`))
        .filter(control => control.dataset.strategySelect === key || control.dataset.strategyManual === key);
    }}
    function strategyValueForKey(key) {{
      const manual = controlsForStrategyKey('data-strategy-manual', key)
        .map(control => control.value.trim())
        .find(value => value) || '';
      const selected = controlsForStrategyKey('data-strategy-select', key)
        .map(control => control.value.trim())
        .find(value => value) || '';
      return manual || selected;
    }}
    function strategyControl(row) {{
      const key = String(row.strategy_key || '');
      return `<div class="strategy-controls">
        <select data-strategy-select="${{escapeHtml(key)}}">${{strategyOptions(row.Strategy_Name)}}</select>
        <input data-strategy-manual="${{escapeHtml(key)}}" type="text" placeholder="Manual strategy name">
      </div>`;
    }}
    function renderStrategyTable(targetId, rows, columns) {{
      const target = document.getElementById(targetId);
      if (!rows.length) {{
        target.innerHTML = '<div class="empty">No rows.</div>';
        return;
      }}
      rows.forEach(row => strategyRowsByKey.set(String(row.strategy_key || ''), row));
      const header = columns.map(column => `<th>${{escapeHtml(column === '_strategy' ? 'Strategy_Name' : column)}}</th>`).join('');
      const body = rows.map(row => {{
        const cells = columns.map(column => {{
          if (column === '_strategy') {{
            return `<td>${{strategyControl(row)}}</td>`;
          }}
          return `<td${{cellClass(column, row[column])}}>${{formatValue(column, row[column])}}</td>`;
        }}).join('');
        return `<tr>${{cells}}</tr>`;
      }}).join('');
      target.innerHTML = `<table><thead><tr>${{header}}</tr></thead><tbody>${{body}}</tbody></table>`;
      target.querySelectorAll('[data-strategy-select], [data-strategy-manual]').forEach(input => {{
        input.addEventListener('change', event => {{
          dirtyStrategyRows.add(event.currentTarget.dataset.strategySelect || event.currentTarget.dataset.strategyManual);
          updateBanner();
        }});
        input.addEventListener('input', event => {{
          dirtyStrategyRows.add(event.currentTarget.dataset.strategySelect || event.currentTarget.dataset.strategyManual);
          updateBanner();
        }});
      }});
    }}
    function renderStatementTotals() {{
      const target = document.getElementById('statement-totals');
      const row = (DATA.statementTotals || [])[0];
      if (!row) {{
        target.innerHTML = '<div class="empty">No statement totals.</div>';
        return;
      }}
      const totals = [
        ['statement_closed_net_ytd_pnl', 'YTD Net PNL From Account Statement'],
        ['statement_closed_net_ytd_pnl_change_since_previous', 'Change Since Prior Statement'],
        ['trade_history_adjusted_closed_net_ytd_pnl', 'YTD Net PNL From Trade History'],
        ['difference_after_adjustments', 'Difference'],
      ];
      const cards = fields => fields.map(([key, label]) =>
        `<div class="total-card"><div class="total-label">${{escapeHtml(label)}}</div><div class="total-value">${{formatValue(key, row[key])}}</div></div>`
      ).join('');
      target.innerHTML = `<div class="statement-totals">
        <div class="totals-grid">${{cards(totals)}}</div>
      </div>`;
    }}
    function renderStatementDetail() {{
      renderTable('statement-detail', DATA.statementTotals || [], [
        'gross_ytd_pnl',
        'open_position_pnl',
        'closed_gross_ytd_pnl',
        'total_ytd_commissions_and_fees',
        'statement_closed_net_ytd_pnl',
        'previous_statement_file',
        'previous_statement_date',
        'previous_statement_closed_net_ytd_pnl',
        'statement_closed_net_ytd_pnl_change_since_previous',
        'trade_history_closed_net_ytd_pnl',
        'open_trade_exclusion',
        'ytd_bridge_adjustment',
        'total_closed_pnl_adjustment',
        'trade_history_adjusted_closed_net_ytd_pnl',
        'difference_before_adjustments',
        'difference_after_adjustments',
        'pending_suggested_correction_delta'
      ]);
    }}
    function renderReconciliation() {{
      const target = document.getElementById('reconciliation');
      const rows = DATA.reconciliation || [];
      if (!rows.length) {{
        target.innerHTML = '<div class="empty">No reconciliation rows.</div>';
        return;
      }}
      target.innerHTML = rows.map(row => {{
        const difference = Number(row.difference || 0);
        const suggestedDifference = Number(row.suggested_difference || 0);
        const good = Math.abs(difference) <= Number(row.tolerance || 1);
        const metrics = [
          ['Date', row.date],
          ['Account', row.account_bucket],
          ['Trade history', money(row.extracted_net_pnl)],
          ['Cash balances', money(row.statement_trade_cash_flow)],
          ['Suggested trade history', money(row.suggested_net_pnl)],
          ['Suggested adjustment', money(row.suggested_adjustment)],
          ['After suggestion', money(row.suggested_difference)],
          ['Correction rows', row.correction_candidate_count],
          ['Trade rows', row.extracted_trade_count],
          ['Ledger rows', row.statement_trade_rows],
          ['Balance residual', money(row.balance_residual)],
          ['Status', row.status],
        ].map(([label, value]) => `<div class="metric"><div class="metric-label">${{escapeHtml(label)}}</div><div class="metric-value">${{escapeHtml(value)}}</div></div>`).join('');
        return `<article class="recon-card">
          <div class="difference ${{good ? 'good' : ''}}">
            <div class="metric-label">Difference</div>
            <div class="value">${{money(difference)}}</div>
            <div class="metric-label">After suggestions</div>
            <div class="value">${{money(suggestedDifference)}}</div>
            <div class="subtle">${{escapeHtml(row.likely_cause || '')}}</div>
          </div>
          <div class="recon-detail">${{metrics}}</div>
        </article>`;
      }}).join('');
    }}
    function updateBanner(message, klass = '') {{
      const banner = document.getElementById('banner');
      if (message) banner.textContent = message;
      else if (!DATA.serverEnabled) banner.textContent = 'Static dashboard mode: rerun with --serve-dashboard to save directly from this page.';
      else if (dirty && dirtyStrategyRows.size) banner.textContent = 'You have unsaved correction and strategy name changes.';
      else if (dirty) banner.textContent = 'You have selected corrections that have not been saved.';
      else if (dirtyStrategyRows.size) banner.textContent = 'You have strategy name changes that have not been saved.';
      else if (savedSinceLoad) banner.textContent = 'Corrections saved and v2 output files refreshed.';
      else banner.textContent = 'Select corrections or assign strategy names when the review looks right.';
      banner.className = `banner ${{klass}}`;
    }}
    function selectedRows() {{
      const keys = selected;
      return (DATA.correctionCandidates || []).filter(row => keys.has(row.correction_key) && correctionIsActionable(row));
    }}
    function correctionIsActionable(row) {{
      if (!row || row.is_saved) return false;
      return ['trade_pnl_delta', 'fee_delta', 'net_pnl_delta'].some(column => Math.abs(Number(row[column]) || 0) > 0.005);
    }}
    function selectFeeOnly() {{
      (DATA.correctionCandidates || []).forEach(row => {{
        if (row.is_fee_only && correctionIsActionable(row)) selected.add(row.correction_key);
      }});
      dirty = true;
      renderCorrections();
      updateBanner();
    }}
    function clearSelection() {{
      selected.clear();
      dirty = true;
      renderCorrections();
      updateBanner();
    }}
    async function saveCorrections() {{
      if (!DATA.serverEnabled) {{
        updateBanner('Static dashboard mode: rerun with --serve-dashboard to save directly from this page.', 'bad');
        return;
      }}
      const rows = selectedRows();
      if (!rows.length) {{
        updateBanner('No corrections are selected.', 'bad');
        return;
      }}
      const response = await fetch('/api/save-corrections', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ corrections: rows }}),
      }});
      const responseText = await response.text();
      let payload = {{}};
      try {{
        payload = responseText ? JSON.parse(responseText) : {{}};
      }} catch (error) {{
        payload = {{ error: responseText }};
      }}
      if (!response.ok) {{
        updateBanner(`Save failed: ${{payload.error || response.statusText}}`, 'bad');
        return;
      }}
      dirty = false;
      savedSinceLoad = true;
      selected.clear();
      if (payload.master_updated === false) {{
        updateBanner(payload.message || `Saved ${{payload.saved_rows}} correction rows. Master update is still blocked by unreconciled groups.`, 'warn');
      }} else {{
        updateBanner(payload.message || `Saved ${{payload.saved_rows}} correction rows and refreshed v2 CSV outputs.`, 'good');
      }}
      setTimeout(() => window.location.reload(), 400);
    }}
    async function saveStrategies() {{
      if (!DATA.serverEnabled) {{
        updateBanner('Static dashboard mode: rerun with --serve-dashboard to save directly from this page.', 'bad');
        return;
      }}
      const updates = Array.from(dirtyStrategyRows).map(key => {{
        const row = strategyRowsByKey.get(String(key));
        return {{
          row_id: row?.row_id,
          statement_file: row?.statement_file,
          statement_trade_row: row?.statement_trade_row,
          strategy_name: strategyValueForKey(String(key)),
        }};
      }}).filter(update => update.strategy_name);
      if (!updates.length) {{
        updateBanner('No strategy names are ready to save yet.', 'bad');
        return;
      }}
      const response = await fetch('/api/save-strategies', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ updates }}),
      }});
      const payload = await response.json();
      if (!response.ok) {{
        updateBanner(`Save failed: ${{payload.error || response.statusText}}`, 'bad');
        return;
      }}
      dirtyStrategyRows.clear();
      savedSinceLoad = true;
      updateBanner(`Saved ${{payload.saved_rows}} strategy row(s) and refreshed v2 CSV outputs.`, 'good');
      setTimeout(() => window.location.reload(), 400);
    }}
    async function stopDashboardServer() {{
      if (!DATA.serverEnabled) return true;
      try {{
        const response = await fetch('/api/shutdown', {{ method: 'POST' }});
        if (!response.ok) {{
          const payload = await response.json().catch(() => ({{}}));
          updateBanner(`Exit failed: ${{payload.error || response.statusText}}`, 'bad');
          return false;
        }}
        return true;
      }} catch (error) {{
        updateBanner(`Exit failed: ${{error.message || error}}`, 'bad');
        return false;
      }}
    }}
    async function exitDashboard() {{
      if ((dirty || dirtyStrategyRows.size) && !confirm('You have unsaved dashboard changes. Close anyway?')) return;
      const stopped = await stopDashboardServer();
      if (!stopped) return;
      updateBanner('Dashboard server stopped. The command line should be available again.', 'good');
      window.close();
    }}
    function renderCorrections() {{
      const rows = (DATA.correctionCandidates || []).filter(correctionIsActionable);
      const columns = [
        '_select','net_pnl_delta','trade_pnl_delta','fee_delta',
        'date','account_bucket','statement_trade_row','likely_cause',
        'ledger_timestamp','ledger_description','original_trade_pnl',
        'original_fees','original_net_pnl','corrected_trade_pnl',
        'corrected_fees','corrected_net_pnl','is_saved'
      ];
      renderTable('corrections', rows, columns, {{ checkboxes: true }});
    }}
    function render() {{
      renderStatementTotals();
      renderStatementDetail();
      document.getElementById('new-trade-count').textContent = `${{(DATA.newTrades || []).length}} execution rows`;
      document.getElementById('untagged-trade-count').textContent = `${{(DATA.untaggedTrades || []).length}} untagged rows`;
      renderStrategyTable('new-trades', DATA.newTrades || [], [
        '_strategy','statement_trade_row','Exec Time','Spread','Side','Qty',
        'Pos Effect','Symbol','Exp','Strike','Type','gross_pnl','fees','net_pnl',
        'cash_correction_applied'
      ]);
      renderStrategyTable('untagged-trades', DATA.untaggedTrades || [], [
        '_strategy','statement_file','statement_trade_row','Exec Time','Spread',
        'Side','Qty','Pos Effect','Symbol','Exp','Strike','Type','Price',
        'Net Price','net_pnl'
      ]);
      renderTable('open-positions', DATA.openPositions || [], [
        'Symbol','open_position_pnl','ytd_pnl','closed_gross_pnl','description'
      ]);
      renderTable('reconciliation', DATA.adjustmentReview || [], [
        'date','account_bucket','timestamp','trade_detail','actual',
        'current','adjustment','adjusted','likely_cause','is_saved',
        'ledger_description'
      ]);
      renderTable('ytd-bridge', DATA.ytdBridge || [], [
        'adjustment_category','bridge_type','symbol','normalized_root','gross_pnl_adjustment',
        'fee_adjustment','net_pnl_adjustment','reason','source'
      ]);
      selectFeeOnly();
      dirty = false;
      updateBanner();
    }}
    window.addEventListener('beforeunload', event => {{
      if (!dirty && !dirtyStrategyRows.size) return;
      event.preventDefault();
      event.returnValue = '';
    }});
    document.getElementById('save-corrections').addEventListener('click', saveCorrections);
    document.getElementById('save-strategies').addEventListener('click', saveStrategies);
    document.getElementById('select-fee-only').addEventListener('click', selectFeeOnly);
    document.getElementById('clear-selection').addEventListener('click', clearSelection);
    document.getElementById('exit-dashboard').addEventListener('click', exitDashboard);
    render();
  </script>
</body>
</html>"""


def write_dashboard(path, payload, server_enabled=False):
    path = Path(path)
    path.write_text(
        dashboard_html(payload, server_enabled=server_enabled),
        encoding="utf-8",
    )
    return str(path)


def shutdown_dashboard_server(server):
    thread = threading.Thread(
        target=server.shutdown,
        daemon=True,
    )
    thread.start()
    return thread


def create_dashboard_server(host, port, handler_class, port_attempts=25):
    requested_port = int(port)
    candidate_ports = (
        [0]
        if requested_port == 0
        else range(requested_port, requested_port + port_attempts)
    )
    last_error = None

    for candidate_port in candidate_ports:
        try:
            server = HTTPServer((host, candidate_port), handler_class)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise

            last_error = error
            continue

        if requested_port and server.server_port != requested_port:
            print(
                f"Dashboard port {requested_port} is in use; "
                f"using {server.server_port} instead."
            )

        return server

    try:
        server = HTTPServer((host, 0), handler_class)
    except OSError:
        if last_error is not None:
            raise last_error
        raise

    if requested_port:
        print(
            f"Dashboard ports {requested_port}-"
            f"{requested_port + port_attempts - 1} are in use; "
            f"using {server.server_port} instead."
        )

    return server


class V2DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path not in {"/", f"/{DASHBOARD_FILE_NAME}"}:
            self.send_error(404)
            return

        body = Path(self.server.dashboard_path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/shutdown":
            self.send_json(
                200,
                {"message": "Dashboard server is stopping."},
            )
            shutdown_dashboard_server(self.server)
            return

        if path == "/api/save-strategies":
            self.save_strategies()
            return

        if path != "/api/save-corrections":
            self.send_error(404)
            return

        try:
            payload = self.read_json_body()
            submitted = pd.DataFrame(payload.get("corrections", []))
            submitted = submitted.reindex(columns=CASH_CORRECTION_COLUMNS)
            existing = load_cash_trade_corrections(
                self.server.cash_corrections_path,
            )
            combined = combine_cash_trade_corrections(existing, submitted)
            save_cash_trade_corrections(
                self.server.cash_corrections_path,
                combined,
            )
            if self.server.run_context.strict_daily_import:
                preview_outputs = write_preview_outputs(
                    self.server.run_context,
                    combined,
                )
                failures = strict_daily_failures(
                    preview_outputs["reconciliation"],
                )

                if not failures.empty:
                    candidates = add_candidate_review_columns(
                        self.server.correction_candidates,
                        combined,
                    )
                    candidates.to_csv(
                        self.server.run_context.paths[
                            "cash_trade_correction_candidates"
                        ],
                        index=False,
                    )
                    refreshed_payload = html_payload(
                        self.server.run_context,
                        preview_outputs,
                        self.server.correction_candidates,
                        combined,
                    )
                    write_dashboard(
                        self.server.dashboard_path,
                        refreshed_payload,
                        server_enabled=True,
                    )
                    self.send_json(
                        200,
                        {
                            "saved_rows": len(combined),
                            "submitted_rows": len(submitted),
                            "master_updated": False,
                            "message": (
                                "Corrections saved. The rebuild master was not "
                                f"updated because {len(failures)} cash-balance "
                                "group(s) are still unreconciled."
                            ),
                            "unreconciled_groups": json_safe_records(
                                failures[
                                    [
                                        column
                                        for column in [
                                            "date",
                                            "account_bucket",
                                            "unreconciled_delta",
                                            "extracted_net_pnl",
                                            "statement_trade_cash_flow",
                                            "extracted_trade_count",
                                            "statement_trade_rows",
                                        ]
                                        if column in failures.columns
                                    ]
                                ]
                            ),
                        },
                    )
                    return

            outputs = write_outputs(self.server.run_context, combined)
            candidates = add_candidate_review_columns(
                self.server.correction_candidates,
                combined,
            )
            candidates.to_csv(
                self.server.run_context.paths["cash_trade_correction_candidates"],
                index=False,
            )
            refreshed_payload = html_payload(
                self.server.run_context,
                outputs,
                self.server.correction_candidates,
                combined,
            )
            write_dashboard(
                self.server.dashboard_path,
                refreshed_payload,
                server_enabled=True,
            )
            self.send_json(
                200,
                {
                    "saved_rows": len(combined),
                    "submitted_rows": len(submitted),
                    "master_updated": True,
                    "message": (
                        f"Saved {len(combined)} correction rows and refreshed "
                        "v2 CSV outputs."
                    ),
                    "master": self.server.run_context.paths["master"],
                    "cleaned": self.server.run_context.paths["cleaned"],
                    "dashboard": self.server.dashboard_path,
                    "reconciled_groups": int(
                        (outputs["reconciliation"]["status"] == "reconciled").sum()
                    ),
                },
            )
        except Exception as error:
            self.send_json(500, {"error": str(error)})

    def save_strategies(self):
        try:
            payload = self.read_json_body()
            result = save_strategy_updates(
                self.server.run_context.paths,
                payload.get("updates", []),
            )
            refreshed_payload = dashboard_payload_from_existing_outputs(
                self.server.run_context.paths,
                cash_validation_tolerance=(
                    self.server.run_context.cash_validation_tolerance
                ),
                cash_corrections_path=self.server.cash_corrections_path,
            )
            write_dashboard(
                self.server.dashboard_path,
                refreshed_payload,
                server_enabled=True,
            )
            self.send_json(
                200,
                {
                    **result,
                    "master": self.server.run_context.paths["master"],
                    "cleaned": self.server.run_context.paths["cleaned"],
                    "dashboard": self.server.dashboard_path,
                },
            )
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_dashboard(
    dashboard_path,
    context,
    correction_candidates,
    host="127.0.0.1",
    port=8770,
    open_browser=False,
):
    server = create_dashboard_server(host, port, V2DashboardHandler)
    server.dashboard_path = str(dashboard_path)
    server.run_context = context
    server.correction_candidates = correction_candidates
    server.cash_corrections_path = context.cash_corrections_path
    url = f"http://{host}:{server.server_port}/{DASHBOARD_FILE_NAME}"

    if open_browser:
        webbrowser.open(url)

    print(f"Serving trade history v2 dashboard at {url}")
    print("Press Ctrl+C to stop the dashboard server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped trade history v2 dashboard server.")
    finally:
        server.server_close()


class StaticDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path not in {"/", f"/{DASHBOARD_FILE_NAME}"}:
            self.send_error(404)
            return

        body = Path(self.server.dashboard_path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/shutdown":
            self.send_json(
                200,
                {"message": "Dashboard server is stopping."},
            )
            shutdown_dashboard_server(self.server)
            return

        if path == "/api/save-strategies":
            self.save_strategies()
            return

        if path == "/api/save-corrections":
            self.save_corrections()
            return

        self.send_error(404)

    def save_corrections(self):
        try:
            payload = self.read_json_body()
            submitted = pd.DataFrame(payload.get("corrections", []))
            result = save_corrections_to_existing_outputs(
                self.server.paths,
                submitted,
                self.server.dashboard_path,
                cash_validation_tolerance=self.server.cash_validation_tolerance,
                cash_corrections_path=self.server.cash_corrections_path,
            )
            self.send_json(
                200,
                {
                    **result,
                    "master": self.server.paths["master"],
                    "cleaned": self.server.paths["cleaned"],
                    "dashboard": self.server.dashboard_path,
                },
            )
        except Exception as error:
            self.send_json(500, {"error": str(error)})

    def save_strategies(self):
        try:
            payload = self.read_json_body()
            result = save_strategy_updates(
                self.server.paths,
                payload.get("updates", []),
            )
            refreshed_payload = dashboard_payload_from_existing_outputs(
                self.server.paths,
                cash_validation_tolerance=self.server.cash_validation_tolerance,
                cash_corrections_path=self.server.cash_corrections_path,
            )
            write_dashboard(
                self.server.dashboard_path,
                refreshed_payload,
                server_enabled=True,
            )
            self.send_json(
                200,
                {
                    **result,
                    "master": self.server.paths["master"],
                    "cleaned": self.server.paths["cleaned"],
                    "dashboard": self.server.dashboard_path,
                },
            )
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_existing_dashboard(
    dashboard_path,
    paths,
    host="127.0.0.1",
    port=8770,
    open_browser=False,
    cash_validation_tolerance=1.0,
    cash_corrections_path=None,
):
    server = create_dashboard_server(host, port, StaticDashboardHandler)
    server.dashboard_path = str(dashboard_path)
    server.paths = paths
    server.cash_validation_tolerance = cash_validation_tolerance
    server.cash_corrections_path = cash_corrections_path
    url = f"http://{host}:{server.server_port}/{DASHBOARD_FILE_NAME}"

    if open_browser:
        webbrowser.open(url)

    print(f"Serving existing trade history v2 dashboard at {url}")
    print("Press Ctrl+C to stop the dashboard server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped trade history v2 dashboard server.")
    finally:
        server.server_close()


def view_existing_dashboard(
    paths,
    serve=False,
    open_dashboard=False,
    dashboard_host="127.0.0.1",
    dashboard_port=8770,
    cash_validation_tolerance=1.0,
    cash_corrections_path=None,
):
    dashboard_path = paths["dashboard"]

    if not Path(dashboard_path).exists():
        print(f"No existing dashboard found at {dashboard_path}")
        return None

    print(f"Existing v2 reconciliation dashboard available at {dashboard_path}")

    payload = dashboard_payload_from_existing_outputs(
        paths,
        cash_validation_tolerance=cash_validation_tolerance,
        cash_corrections_path=cash_corrections_path,
    )
    write_dashboard(
        dashboard_path,
        payload,
        server_enabled=serve,
    )

    if serve:
        serve_existing_dashboard(
            dashboard_path,
            paths,
            host=dashboard_host,
            port=dashboard_port,
            open_browser=open_dashboard,
            cash_validation_tolerance=cash_validation_tolerance,
            cash_corrections_path=cash_corrections_path,
        )
    elif open_dashboard:
        webbrowser.open(Path(dashboard_path).resolve().as_uri())

    return dashboard_path


def build_context(
    input_file,
    output_dir=OUTPUT_DIR,
    start_date=None,
    strategy_source_master=STRATEGY_SOURCE_MASTER,
    cash_corrections_file=None,
    cash_validation_tolerance=1.0,
    strict_daily_import=False,
):
    paths = v2_output_paths(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    lines, raw_trades, trade_section_start = load_statement_trade_rows(
        input_file,
        start_date=start_date,
    )
    raw_trades, skipped_overlap_trades = skip_existing_trade_overlaps(
        raw_trades,
        paths["master"],
    )

    if raw_trades.empty:
        raise NoNewTradesAfterOverlap(skipped_overlap_trades)

    first_trade_time, reconciliation_start, reconciliation_end = trade_date_window(
        raw_trades,
        start_date=start_date,
        exact_start=skipped_overlap_trades > 0 and start_date is None,
    )
    statement_ytd_summary, statement_ytd_positions = parse_statement_ytd_summary(
        lines,
        input_file,
    )
    cash_ledger = parse_cash_ledger(lines)
    cash_ledger = filter_cash_ledger_by_date(
        cash_ledger,
        reconciliation_start,
        reconciliation_end,
    )
    base_trades = prepare_base_trades(
        raw_trades,
        strategy_source_master=strategy_source_master,
    )
    base_trades = append_futures_cash_settlement_trades(
        base_trades,
        cash_ledger,
        input_file,
    )
    starting_equity = lookup_starting_equity(
        lines[:trade_section_start],
        first_trade_time,
    )
    master_starting_equity = get_master_starting_equity(
        paths["master"],
        starting_equity,
    )

    return V2RunContext(
        input_file=str(input_file),
        output_dir=str(output_dir),
        paths=paths,
        lines=lines,
        base_trades=base_trades,
        cash_ledger=cash_ledger,
        statement_ytd_summary=statement_ytd_summary,
        statement_ytd_positions=statement_ytd_positions,
        starting_equity=starting_equity,
        master_starting_equity=master_starting_equity,
        start_date=start_date,
        cash_reconciliation_start_date=reconciliation_start,
        cash_reconciliation_end_date=reconciliation_end,
        cash_corrections_path=(
            cash_corrections_file or paths["cash_trade_corrections"]
        ),
        cash_validation_tolerance=cash_validation_tolerance,
        skipped_overlap_trades=skipped_overlap_trades,
        strict_daily_import=strict_daily_import,
    )


def run_v2(
    input_file=INPUT_FILE,
    output_dir=OUTPUT_DIR,
    start_date=None,
    strategy_source_master=STRATEGY_SOURCE_MASTER,
    cash_corrections_file=None,
    ignore_saved_corrections=False,
    cash_validation_tolerance=1.0,
    reset_master=False,
    strict_daily_import=False,
    serve=False,
    open_dashboard=False,
    dashboard_host="127.0.0.1",
    dashboard_port=8770,
):
    start_date = parse_filter_date(start_date, "--start-date")

    if reset_master:
        reset_output_files(output_dir)

    backfilled_strategy_names = backfill_missing_master_strategy_names(
        v2_output_paths(output_dir),
        strategy_source_master,
    )

    try:
        context = build_context(
            input_file,
            output_dir=output_dir,
            start_date=start_date,
            strategy_source_master=strategy_source_master,
            cash_corrections_file=cash_corrections_file,
            cash_validation_tolerance=cash_validation_tolerance,
            strict_daily_import=strict_daily_import,
        )
    except NoNewTradesAfterOverlap as error:
        paths = v2_output_paths(output_dir)
        refreshed_corrections = (
            0
            if ignore_saved_corrections
            else refresh_existing_outputs_with_saved_corrections(
                paths,
                cash_corrections_file or paths["cash_trade_corrections"],
            )
        )
        refreshed_statement_ytd = refresh_statement_ytd_outputs(
            paths,
            input_file,
        )
        print(
            "No new trades to import after skipping overlapping "
            f"already-imported trades: {error.skipped_overlap_trades}"
        )
        if refreshed_corrections:
            print(
                "Refreshed existing master with saved cash corrections: "
                f"{refreshed_corrections} row(s)"
            )
        if backfilled_strategy_names:
            print(
                "Backfilled missing Strategy_Name values from strategy source: "
                f"{backfilled_strategy_names}"
            )
        if refreshed_statement_ytd:
            print(
                "Refreshed statement YTD totals and open positions from "
                f"{Path(input_file).name}"
            )
        if refreshed_corrections:
            print(f"Updated existing master at {paths['master']}")
        else:
            print(f"Master was not updated at {paths['master']}")
        dashboard_path = view_existing_dashboard(
            paths,
            serve=serve,
            open_dashboard=open_dashboard,
            dashboard_host=dashboard_host,
            dashboard_port=dashboard_port,
            cash_validation_tolerance=cash_validation_tolerance,
            cash_corrections_path=(
                cash_corrections_file or paths["cash_trade_corrections"]
            ),
        )
        return {
            "context": None,
            "outputs": {},
            "correction_candidates": pd.DataFrame(),
            "dashboard_path": dashboard_path,
            "skipped_overlap_trades": error.skipped_overlap_trades,
        }
    correction_candidate_trades = master_aware_trade_frame(context)
    correction_candidates = build_cash_trade_corrections(
        context.cash_ledger,
        correction_candidate_trades,
    )
    saved_corrections = (
        pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)
        if ignore_saved_corrections
        else load_cash_trade_corrections(context.cash_corrections_path)
    )
    cash_trade_corrections = current_corrections_for_candidates(
        correction_candidates,
        saved_corrections,
    )
    strict_failures = pd.DataFrame(columns=[])

    if strict_daily_import:
        preview_outputs = write_preview_outputs(
            context,
            cash_trade_corrections,
        )
        strict_failures = strict_daily_failures(
            preview_outputs["reconciliation"],
        )

        if strict_failures.empty:
            outputs = write_outputs(
                context,
                cash_trade_corrections,
            )
        else:
            outputs = preview_outputs
    else:
        outputs = write_outputs(
            context,
            cash_trade_corrections,
        )
    fee_suggestions = build_fee_correction_suggestions(
        context.cash_ledger,
        outputs["cleaned"],
        tolerance=cash_validation_tolerance,
    )
    reviewed_candidates = add_candidate_review_columns(
        correction_candidates,
        cash_trade_corrections,
    )
    reviewed_candidates.to_csv(
        context.paths["cash_trade_correction_candidates"],
        index=False,
    )
    fee_suggestions.to_csv(
        context.paths["fee_correction_suggestions"],
        index=False,
    )
    reconciliation_detail = reconciliation_dashboard_rows(
        outputs["reconciliation"],
        reviewed_candidates,
        tolerance=cash_validation_tolerance,
    )
    reconciliation_detail.to_csv(
        context.paths["reconciliation_detail"],
        index=False,
    )
    payload = html_payload(
        context,
        outputs,
        correction_candidates,
        cash_trade_corrections,
    )
    dashboard_path = write_dashboard(
        context.paths["dashboard"],
        payload,
        server_enabled=serve,
    )

    print(f"Saved v2 cleaned trade data to {context.paths['cleaned']}")
    if strict_daily_import and not strict_failures.empty:
        print(
            "Strict daily import preview saved; "
            f"master was not updated at {context.paths['master']}"
        )
    else:
        print(f"Saved v2 master trade data to {context.paths['master']}")
    print(f"Saved v2 reconciliation dashboard to {dashboard_path}")
    if backfilled_strategy_names:
        print(
            "Backfilled missing Strategy_Name values from strategy source: "
            f"{backfilled_strategy_names}"
        )
    if context.skipped_overlap_trades:
        print(
            "Skipped overlapping already-imported trades: "
            f"{context.skipped_overlap_trades}"
        )
    print(
        "Cash reconciliation groups: "
        f"{len(outputs['reconciliation'])}; "
        "unreconciled: "
        f"{int((outputs['reconciliation']['status'] == 'unreconciled').sum())}"
    )
    print(
        "Suggested correction rows: "
        f"{len(reviewed_candidates)}; "
        "fee-only: "
        f"{int(reviewed_candidates.get('is_fee_only', pd.Series(dtype=bool)).sum())}"
    )

    if strict_daily_import and not strict_failures.empty:
        failure_preview = strict_failures[
            [
                column
                for column in [
                    "date",
                    "account_bucket",
                    "unreconciled_delta",
                    "extracted_net_pnl",
                    "statement_trade_cash_flow",
                    "extracted_trade_count",
                    "statement_trade_rows",
                ]
                if column in strict_failures.columns
            ]
        ]
        print(
            "Strict daily import blocked the master update. "
            "Review/save corrections in the dashboard, then rerun or save "
            "from the served dashboard."
        )
        print(failure_preview.to_string(index=False))

    if serve:
        serve_dashboard(
            dashboard_path,
            context,
            correction_candidates,
            host=dashboard_host,
            port=dashboard_port,
            open_browser=open_dashboard,
        )
    else:
        if open_dashboard:
            webbrowser.open(Path(dashboard_path).resolve().as_uri())

        if strict_daily_import and not strict_failures.empty:
            raise SystemExit(1)

    return {
        "context": context,
        "outputs": outputs,
        "correction_candidates": reviewed_candidates,
        "dashboard_path": dashboard_path,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract Thinkorswim trade history with a v2 daily reconciliation "
            "dashboard. Outputs are written separately so v1 and v2 can be "
            "compared side by side."
        )
    )
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to the raw Thinkorswim account statement CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where v2 outputs are written.",
    )
    parser.add_argument(
        "--rebuild-master",
        action="store_true",
        help=(
            "Use a separate clean rebuild output directory "
            f"({REBUILD_OUTPUT_DIR}) unless --output-dir is supplied."
        ),
    )
    parser.add_argument(
        "--reset-master",
        action="store_true",
        help=(
            "Remove known v2 output files in the selected output directory "
            "before importing this statement."
        ),
    )
    parser.add_argument(
        "--strict-daily-import",
        action="store_true",
        help=(
            "Do not update the rebuilding master unless the current statement "
            "reconciles to cash balances after saved corrections."
        ),
    )
    parser.add_argument(
        "--start-date",
        help="Only include trades and cash rows on or after this date.",
    )
    parser.add_argument(
        "--strategy-source-master",
        default=STRATEGY_SOURCE_MASTER,
        help=(
            "Existing master_cleaned_tos_data.csv used only to preserve "
            "reviewed Strategy_Name values. Set to an empty string to skip."
        ),
    )
    parser.add_argument(
        "--cash-corrections-file",
        help="CSV file used to persist v2 cash-ledger corrections.",
    )
    parser.add_argument(
        "--ignore-saved-corrections",
        action="store_true",
        help="Generate v2 outputs without applying saved correction rows.",
    )
    parser.add_argument(
        "--cash-validation-tolerance",
        type=float,
        default=1.0,
        help="Allowed dollar delta for daily cash reconciliation.",
    )
    parser.add_argument(
        "--serve-dashboard",
        action="store_true",
        help="Serve the dashboard locally so corrections can be saved.",
    )
    dashboard_open_group = parser.add_mutually_exclusive_group()
    dashboard_open_group.add_argument(
        "--open-dashboard",
        action="store_true",
        default=True,
        help="Open the dashboard after writing it. This is the default.",
    )
    dashboard_open_group.add_argument(
        "--no-open-dashboard",
        dest="open_dashboard",
        action="store_false",
        help="Do not open the dashboard after writing it.",
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8770,
        help="Port for --serve-dashboard.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = args.output_dir or (
        REBUILD_OUTPUT_DIR if args.rebuild_master else OUTPUT_DIR
    )
    run_v2(
        input_file=args.input,
        output_dir=output_dir,
        start_date=args.start_date,
        strategy_source_master=args.strategy_source_master or None,
        cash_corrections_file=args.cash_corrections_file,
        ignore_saved_corrections=args.ignore_saved_corrections,
        cash_validation_tolerance=args.cash_validation_tolerance,
        reset_master=args.reset_master,
        strict_daily_import=args.strict_daily_import,
        serve=args.serve_dashboard,
        open_dashboard=args.open_dashboard,
        dashboard_host=args.dashboard_host,
        dashboard_port=args.dashboard_port,
    )


if __name__ == "__main__":
    main()
