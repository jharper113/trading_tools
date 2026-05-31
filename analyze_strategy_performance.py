import argparse
import html
import math
import os
import re

os.environ.setdefault(
    "MPLCONFIGDIR",
    "/tmp/matplotlib",
)

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


INPUT_FILE = "./output/cleaned_tos_data.csv"
OUTPUT_DIR = "./output/strategy_performance"
CHART_DIR = f"{OUTPUT_DIR}/charts"

EQUITY_CURVES_FILE = f"{OUTPUT_DIR}/strategy_equity_curves.csv"
SUMMARY_STATS_FILE = f"{OUTPUT_DIR}/strategy_summary_statistics.csv"
PNL_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_pnl_correlation.csv"
DRAWDOWN_CORRELATION_FILE = f"{OUTPUT_DIR}/strategy_drawdown_correlation.csv"
DRAWDOWN_OVERLAP_FILE = f"{OUTPUT_DIR}/strategy_drawdown_overlap.csv"
DASHBOARD_FILE = f"{OUTPUT_DIR}/strategy_dashboard.html"


def clean_strategy_name(value):
    return re.sub(
        r"\s+",
        " ",
        str(value).strip(),
    )


def safe_ratio(numerator, denominator):
    if denominator in {0, None} or pd.isna(denominator):
        return None

    return numerator / denominator


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


def calculate_strategy_summary(equity_curves):
    rows = []

    for strategy_name, group in equity_curves.groupby("Strategy_Name", sort=True):
        group = group.sort_values(
            ["timestamp"],
            na_position="last",
        )
        net_pnl = group["strategy_pnl"]
        wins = net_pnl[net_pnl > 0]
        losses = net_pnl[net_pnl < 0]

        starting_equity = (
            group["strategy_equity"].iloc[0]
            - group["strategy_pnl"].iloc[0]
        )
        ending_equity = group["strategy_equity"].iloc[-1]
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

        max_drawdown = group["strategy_drawdown"].min()
        max_drawdown_dollars = group["strategy_drawdown_dollars"].min()
        gross_profit = wins.sum()
        gross_loss = abs(losses.sum())
        profit_factor = safe_ratio(
            gross_profit,
            gross_loss,
        )
        log_returns = group["strategy_log_return"].dropna()

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

        rows.append({
            "Strategy_Name": strategy_name,
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
            "average_trade_pnl": net_pnl.mean(),
            "median_trade_pnl": net_pnl.median(),
            "average_win": wins.mean() if len(wins) else None,
            "average_loss": losses.mean() if len(losses) else None,
            "largest_win": net_pnl.max(),
            "largest_loss": net_pnl.min(),
            "average_return_on_margin": group["return_on_margin"].mean(),
            "cumulative_log_return_on_margin": group["cm_returns"].iloc[-1],
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

    for strategy_name, group in equity_curves.groupby("Strategy_Name", sort=True):
        group = group[
            group["timestamp"].notna()
        ]

        if group.empty:
            continue

        ax.plot(
            group["timestamp"],
            group["CMPNL"],
            linewidth=1.6,
            label=strategy_name,
        )

    ax.axhline(
        0,
        color="#444444",
        linewidth=0.8,
    )
    ax.set_title("Cumulative PnL By Strategy")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative PnL ($)")
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


def save_summary_bar_charts(summary):
    chart_files = []
    metrics = [
        "total_pnl",
        "profit_factor",
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


def dataframe_to_html_table(df, max_rows=None):
    display_df = df.copy()

    if max_rows is not None:
        display_df = display_df.head(max_rows)

    return display_df.to_html(
        index=False,
        classes="data-table",
        float_format=lambda value: f"{value:,.4f}",
        border=0,
    )


def save_dashboard(
    summary,
    pnl_correlation,
    drawdown_correlation,
    drawdown_overlap,
    chart_files,
):
    chart_tags = "\n".join(
        f'<section><h2>{html.escape(os.path.basename(path).replace("_", " ").replace(".png", "").title())}</h2>'
        f'<img src="{html.escape(os.path.relpath(path, OUTPUT_DIR))}" alt="{html.escape(path)}"></section>'
        for path in chart_files
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
  </style>
</head>
<body>
  <h1>Strategy Performance Dashboard</h1>
  <section>
    <h2>Summary Statistics</h2>
    {dataframe_to_html_table(summary.sort_values("total_pnl", ascending=False))}
  </section>
  {chart_tags}
  <section>
    <h2>Daily PnL Correlation</h2>
    {dataframe_to_html_table(pnl_correlation.reset_index())}
  </section>
  <section>
    <h2>Drawdown Correlation</h2>
    {dataframe_to_html_table(drawdown_correlation.reset_index())}
  </section>
  <section>
    <h2>Drawdown Overlap</h2>
    {dataframe_to_html_table(drawdown_overlap.sort_values("drawdown_overlap_ratio", ascending=False), max_rows=40)}
  </section>
</body>
</html>
"""

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as file:
        file.write(dashboard)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze strategy performance from cleaned TOS trade history."
    )
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to cleaned_tos_data.csv.",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        help="Strategy_Name to include. Repeat for multiple strategies.",
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

    trades = load_cleaned_trades(args.input)
    trades = filter_strategies(
        trades,
        args.strategy,
    )

    equity_curves = build_strategy_equity_curves(trades)
    summary = calculate_strategy_summary(equity_curves)
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

    chart_files = [
        save_strategy_equity_chart(equity_curves),
        *save_summary_bar_charts(summary),
    ]
    save_dashboard(
        summary,
        pnl_correlation,
        drawdown_correlation,
        drawdown_overlap,
        chart_files,
    )

    print(f"Saved strategy equity curves to {EQUITY_CURVES_FILE}")
    print(f"Saved strategy summary statistics to {SUMMARY_STATS_FILE}")
    print(f"Saved strategy dashboard to {DASHBOARD_FILE}")


if __name__ == "__main__":
    main()
