import os
import csv
from io import StringIO

os.environ.setdefault(
    "MPLCONFIGDIR",
    "/tmp/matplotlib",
)

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.enrich import (
    add_strategy_names,
    add_pnl_columns,
    add_log_return_columns,
    add_margin_return_columns,
    build_equity_curve,
    calculate_summary_statistics,
    calculate_margin_requirements,
    lookup_fees,
    parse_number,
)


INPUT_FILE = "./data/trades.csv"
OUTPUT_DIR = "./output"
OUTPUT_FILE = f"{OUTPUT_DIR}/cleaned_tos_data.csv"
PNL_PLOT_FILE = f"{OUTPUT_DIR}/pnl_chart.png"
EQUITY_CURVE_FILE = f"{OUTPUT_DIR}/equity_curve.csv"
SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/summary_statistics.csv"


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


def main():

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True,
    )

    #
    # Read raw file
    #
    with open(INPUT_FILE, "r") as f:
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

    df = add_strategy_names(df)

    #
    # Add enrichment columns
    #
    df["fees"] = df.apply(
        lookup_fees,
        axis=1
    )

    df["margin_requirement"] = calculate_margin_requirements(df)

    df = add_pnl_columns(df)

    first_trade_time = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    ).dropna().min()

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

    equity_curve = build_equity_curve(df)
    summary_statistics = calculate_summary_statistics(df)

    #
    # Save output
    #
    df.to_csv(
        OUTPUT_FILE,
        index=False
    )

    save_pnl_chart(df)

    equity_curve.to_csv(
        EQUITY_CURVE_FILE,
        index=False,
    )

    summary_statistics.to_csv(
        SUMMARY_STATS_FILE,
        index=False,
    )

    print(
        f"Saved enriched trade data to {OUTPUT_FILE}"
    )

    print(
        f"Saved PnL chart to {PNL_PLOT_FILE}"
    )

    print(
        f"Saved equity curve to {EQUITY_CURVE_FILE}"
    )

    print(
        f"Saved summary statistics to {SUMMARY_STATS_FILE}"
    )


if __name__ == "__main__":
    main()
