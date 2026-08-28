#!/usr/bin/env python3
"""Convert normalized AmiBroker WFA trades into analyzer execution rows."""

import argparse
import math
import sys
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = [
    "Ticker",
    "Trade",
    "Entry date",
    "Entry price",
    "Exit date",
    "Exit price",
    "Profit",
    "Shares",
    "Pos. value",
]


def numeric_series(series):
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("(", "-", regex=False)
        .str.replace(")", "", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def validate_source(frame):
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            "AmiBroker WFA trade export is missing required column(s): "
            + ", ".join(missing)
        )


def fully_blank_rows(frame):
    stripped_empty = frame.astype(str).apply(
        lambda column: column.str.strip().eq("")
    )
    return (frame.isna() | stripped_empty).all(axis=1)


def trade_direction(value):
    text = str(value).strip().lower()
    if text.startswith("long"):
        return "LONG"
    if text.startswith("short"):
        return "SHORT"
    raise ValueError(f"Unrecognized AmiBroker Trade value: {value!r}")


def formatted_exec_time(value, hour, minute):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError(f"Could not parse trade date: {value!r}")

    return (
        f"{timestamp.month}/{timestamp.day}/{timestamp.strftime('%y')} "
        f"{hour:02d}:{minute:02d}:00"
    )


def log_return(simple_return):
    if simple_return is None or pd.isna(simple_return):
        return None
    if 1 + simple_return <= 0:
        return None
    return math.log1p(simple_return)


def execution_row(
    source_row,
    source_name,
    source_row_number,
    strategy_name,
    starting_equity,
    is_entry,
):
    direction = trade_direction(source_row["Trade"])
    is_long = direction == "LONG"
    qty = abs(float(source_row["_shares"]))
    position_value = abs(float(source_row["_position_value"]))
    risk_per_contract = source_row.get("_risk_per_contract")
    total_initial_risk = source_row.get("_total_initial_risk")
    has_stop_risk = (
        risk_per_contract is not None
        and not pd.isna(risk_per_contract)
        and float(risk_per_contract) > 0
        and total_initial_risk is not None
        and not pd.isna(total_initial_risk)
        and float(total_initial_risk) > 0
    )
    margin = float(total_initial_risk) if has_stop_risk else position_value
    pnl = 0.0 if is_entry else float(source_row["_profit"])
    simple_return = None if margin == 0 else pnl / margin
    side = (
        "BUY"
        if (is_entry and is_long) or (not is_entry and not is_long)
        else "SELL"
    )
    date_column = "Entry date" if is_entry else "Exit date"
    price_column = "Entry price" if is_entry else "Exit price"
    hour, minute = (9, 30) if is_entry else (16, 0)

    row = {
        "Exec Time": formatted_exec_time(
            source_row[date_column],
            hour,
            minute,
        ),
        "Spread": f"WFA_TRADE_{source_row_number:05d}",
        "Side": side,
        "Qty": qty,
        "Pos Effect": "TO OPEN" if is_entry else "TO CLOSE",
        "Symbol": str(source_row["Ticker"]).strip(),
        "Exp": "",
        "Strike": "",
        "Type": "FUTURE",
        "Price": source_row[price_column],
        "Net Price": source_row[price_column],
        "Order Type": "WFA SYNTHETIC",
        "Order ID": f"WFA-{source_row_number:05d}-{'OPEN' if is_entry else 'CLOSE'}",
        "statement_file": source_name,
        "statement_trade_row": source_row_number,
        "Strategy_Name": strategy_name,
        "fees": 0.0,
        "margin_requirement": margin,
        "trade_pnl": pnl,
        "net_pnl": pnl,
        "starting_equity": starting_equity,
        "return_on_margin": simple_return,
        "log_return_on_margin": log_return(simple_return),
        "WFA_Source_Row": source_row_number,
        "WFA_Execution_Type": "ENTRY" if is_entry else "EXIT",
        "WFA_Trade_Direction": direction,
        "WFA_Risk_Per_Contract": (
            float(risk_per_contract) if has_stop_risk else None
        ),
        "WFA_Total_Initial_Risk": (
            float(total_initial_risk) if has_stop_risk else None
        ),
        "WFA_Return_On_Initial_Risk": simple_return,
    }

    for column, value in source_row.items():
        if not str(column).startswith("_"):
            row.setdefault(column, value)

    return row


def convert_wfa_trades(frame, strategy_name, starting_equity, source_name):
    validate_source(frame)
    if starting_equity <= 0:
        raise ValueError("Starting equity must be greater than zero.")

    working = frame.loc[~fully_blank_rows(frame)].copy()
    if working.empty:
        raise ValueError("AmiBroker WFA trade export contains no populated trades.")

    working["_shares"] = numeric_series(working["Shares"])
    working["_position_value"] = numeric_series(working["Pos. value"])
    working["_profit"] = numeric_series(working["Profit"])

    for column in ["_shares", "_position_value", "_profit"]:
        if working[column].isna().any():
            bad_rows = (working.index[working[column].isna()] + 2).tolist()
            raise ValueError(
                f"Column {column.lstrip('_')!r} contains non-numeric values "
                f"at CSV row(s): {bad_rows[:10]}"
            )

    risk_per_contract = pd.Series(float("nan"), index=working.index)
    if "WFA_05_AmtRisked" in working.columns:
        risk_per_contract = numeric_series(working["WFA_05_AmtRisked"]).abs()

    if {
        "WFA_13_DollarsPerPt",
        "WFA_14_StopDist",
    }.issubset(working.columns):
        calculated_risk = (
            numeric_series(working["WFA_13_DollarsPerPt"]).abs()
            * numeric_series(working["WFA_14_StopDist"]).abs()
        )
        risk_per_contract = risk_per_contract.where(
            risk_per_contract > 0,
            calculated_risk,
        )

    working["_risk_per_contract"] = risk_per_contract.where(
        risk_per_contract > 0
    )
    working["_total_initial_risk"] = (
        working["_shares"].abs() * working["_risk_per_contract"]
    )

    rows = []
    for index, source_row in working.iterrows():
        source_row_number = int(index) + 2
        rows.append(execution_row(
            source_row,
            source_name,
            source_row_number,
            strategy_name,
            starting_equity,
            is_entry=True,
        ))
        rows.append(execution_row(
            source_row,
            source_name,
            source_row_number,
            strategy_name,
            starting_equity,
            is_entry=False,
        ))

    converted = pd.DataFrame(rows)
    converted["_timestamp"] = pd.to_datetime(
        converted["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )
    converted = converted.sort_values(
        ["_timestamp", "WFA_Source_Row", "WFA_Execution_Type"],
        kind="stable",
    ).drop(columns=["_timestamp"])
    return converted.reset_index(drop=True)


def prepare_file(input_path, output_path, strategy_name, starting_equity):
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Output path must differ from the input path.")

    frame = pd.read_csv(
        input_path,
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    converted = convert_wfa_trades(
        frame,
        strategy_name,
        starting_equity,
        input_path.name,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    converted.to_csv(output_path, index=False, encoding="utf-8-sig")
    return converted


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Convert a normalized AmiBroker WFA trade export into matched "
            "execution rows accepted by analyze_strategy_performance.py."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--strategy-name", required=True)
    parser.add_argument(
        "--starting-equity",
        required=True,
        type=float,
        help="Initial equity used by the AmiBroker WFA analysis.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        converted = prepare_file(
            args.input,
            args.output,
            args.strategy_name,
            args.starting_equity,
        )
        print(f"Input path: {args.input}")
        print(f"Output path: {args.output}")
        print(f"Strategy name: {args.strategy_name}")
        print(f"Starting equity: ${args.starting_equity:,.2f}")
        print(f"Source trades: {len(converted) // 2:,}")
        print(f"Analyzer execution rows: {len(converted):,}")
        close_returns = pd.to_numeric(
            converted.loc[
                converted["Pos Effect"] == "TO CLOSE",
                "return_on_margin",
            ],
            errors="coerce",
        )
        extreme_losses = int((close_returns <= -1).sum())
        if extreme_losses:
            print(
                "WARNING: "
                f"{extreme_losses} trade(s) lost at least 100% of exported "
                "Pos. value. Verify AmiBroker futures point value, margin, "
                "and position-sizing settings before using Safe-F results.",
                file=sys.stderr,
            )
        return 0
    except (FileNotFoundError, OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
