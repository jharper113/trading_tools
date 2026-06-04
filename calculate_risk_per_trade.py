import argparse
import math
import os
import re

import numpy as np
import pandas as pd


INPUT_FILE = "./output/master_cleaned_tos_data.csv"
OUTPUT_DIR = "./output/risk_per_trade"
SUMMARY_FILE = f"{OUTPUT_DIR}/risk_per_trade_summary.csv"

DEFAULT_SIMULATIONS = 1000
DEFAULT_SAFE_F_INCREMENT = 0.01
DEFAULT_SAFE_F_START = 1.0
DEFAULT_BANKROLL = 100000
DEFAULT_DRAWDOWN_LIMIT = -0.30
DEFAULT_PCT_ABOVE_DD_LIMIT = 0.95
DEFAULT_PERIODS_PER_YEAR = 252
RISK_RETURN_COLUMN = "return_on_margin"


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


def prepare_strategy_returns(
    trades,
    return_column=RISK_RETURN_COLUMN,
    last_n_trades=0,
):
    if return_column not in trades.columns and return_column == "return_on_margin":
        if "log_return_on_margin" in trades.columns:
            trades = trades.copy()
            trades[return_column] = np.exp(
                pd.to_numeric(
                    trades["log_return_on_margin"],
                    errors="coerce",
                )
            ) - 1

    required_columns = {
        "Strategy_Name",
        return_column,
    }
    missing = required_columns - set(trades.columns)

    if missing:
        raise ValueError(
            f"Missing required columns for risk-per-trade calculation: {missing}"
        )

    working = trades.copy()

    if "timestamp" in working.columns:
        working = working.sort_values(
            ["Strategy_Name", "timestamp"],
            na_position="last",
        )
    elif "Exec Time" in working.columns:
        working["_timestamp"] = pd.to_datetime(
            working["Exec Time"],
            format="%m/%d/%y %H:%M:%S",
            errors="coerce",
        )
        working = working.sort_values(
            ["Strategy_Name", "_timestamp"],
            na_position="last",
        )

    working[return_column] = pd.to_numeric(
        working[return_column],
        errors="coerce",
    )
    working = working[
        working[return_column].notna()
    ].copy()

    if last_n_trades and last_n_trades > 0:
        working = (
            working.groupby("Strategy_Name", group_keys=False)
            .tail(last_n_trades)
            .copy()
        )

    return working


def resample_returns(log_returns, simulations, random_seed=None):
    returns = np.asarray(log_returns, dtype=float)

    if len(returns) == 0:
        return np.empty((0, simulations))

    rng = np.random.default_rng(random_seed)
    sample_indices = rng.integers(
        0,
        len(returns),
        size=(len(returns), simulations),
    )

    return returns[sample_indices]


def cumulative_log_returns(scaled_returns):
    return np.cumsum(
        scaled_returns,
        axis=0,
    )


def simple_returns_to_log_returns(simple_returns):
    return np.log(
        np.clip(
            1 + simple_returns,
            1e-12,
            None,
        )
    )


def drawdowns_from_cumulative_log_returns(cumulative_returns):
    equity = np.exp(cumulative_returns)
    peak = np.maximum.accumulate(
        equity,
        axis=0,
    )

    return equity / peak - 1


def apply_kill_switch(cumulative_returns, drawdowns, drawdown_limit):
    modified_returns = cumulative_returns.copy()

    for column_index in range(modified_returns.shape[1]):
        breaches = np.flatnonzero(
            drawdowns[:, column_index] <= drawdown_limit
        )

        if len(breaches) == 0:
            continue

        first_breach = breaches[0]
        modified_returns[
            first_breach:,
            column_index,
        ] = modified_returns[
            first_breach,
            column_index,
        ]

    modified_drawdowns = drawdowns_from_cumulative_log_returns(
        modified_returns
    )

    return modified_returns, modified_drawdowns


def passes_drawdown_test(
    sampled_returns,
    safe_f,
    drawdown_limit=DEFAULT_DRAWDOWN_LIMIT,
    threshold=DEFAULT_PCT_ABOVE_DD_LIMIT,
):
    scaled_returns = sampled_returns * safe_f
    scaled_log_returns = simple_returns_to_log_returns(scaled_returns)
    cumulative_returns = cumulative_log_returns(scaled_log_returns)
    drawdowns = drawdowns_from_cumulative_log_returns(cumulative_returns)
    _, modified_drawdowns = apply_kill_switch(
        cumulative_returns,
        drawdowns,
        drawdown_limit,
    )
    final_drawdowns = modified_drawdowns[-1, :]

    return np.mean(final_drawdowns >= drawdown_limit) >= threshold


def find_safe_f(
    sampled_returns,
    safe_f_start=DEFAULT_SAFE_F_START,
    safe_f_increment=DEFAULT_SAFE_F_INCREMENT,
    drawdown_limit=DEFAULT_DRAWDOWN_LIMIT,
    threshold=DEFAULT_PCT_ABOVE_DD_LIMIT,
):
    safe_f = safe_f_start

    while safe_f > 0:
        if passes_drawdown_test(
            sampled_returns,
            safe_f,
            drawdown_limit,
            threshold,
        ):
            return round(
                safe_f,
                10,
            )

        safe_f -= safe_f_increment

    return 0.0


def profit_factor_from_cumulative_log_return(cumulative_return):
    equity = np.exp(cumulative_return)
    pnl = np.diff(
        equity,
        prepend=1.0,
    )
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(
        pnl[pnl < 0].sum()
    )

    if gross_loss == 0:
        return None

    return gross_profit / gross_loss


def performance_summary(
    cumulative_returns,
    periods_per_year=DEFAULT_PERIODS_PER_YEAR,
):
    if cumulative_returns.size == 0:
        return pd.DataFrame()

    n_periods = cumulative_returns.shape[0]
    rows = []

    for column_index in range(cumulative_returns.shape[1]):
        log_return = cumulative_returns[:, column_index]
        equity = np.exp(log_return)
        period_returns = np.diff(
            log_return,
            prepend=0.0,
        )
        annualized_vol = (
            float(np.std(period_returns, ddof=1) * math.sqrt(periods_per_year))
            if len(period_returns) > 1
            else None
        )
        cagr = math.exp(
            log_return[-1] * periods_per_year / n_periods
        ) - 1
        drawdown = equity / np.maximum.accumulate(equity) - 1
        max_drawdown = float(np.min(drawdown))
        profit_factor = profit_factor_from_cumulative_log_return(log_return)
        sharpe_ratio = (
            None
            if not annualized_vol
            else cagr / annualized_vol
        )
        mar_ratio = (
            None
            if max_drawdown == 0
            else cagr / abs(max_drawdown)
        )

        rows.append({
            "simulation": column_index + 1,
            "cagr": cagr,
            "annualized_volatility": annualized_vol,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor,
            "mar_ratio": mar_ratio,
        })

    return pd.DataFrame(rows)


def quantile_summary(performance):
    rows = []

    for metric in [
        "cagr",
        "annualized_volatility",
        "sharpe_ratio",
        "max_drawdown",
        "profit_factor",
        "mar_ratio",
    ]:
        values = pd.to_numeric(
            performance[metric],
            errors="coerce",
        ).dropna()

        if values.empty:
            rows.append({
                "Metric": metric,
                "Q25": None,
                "Median": None,
                "Q75": None,
            })
            continue

        rows.append({
            "Metric": metric,
            "Q25": values.quantile(0.25),
            "Median": values.quantile(0.50),
            "Q75": values.quantile(0.75),
        })

    return pd.DataFrame(rows)


def calculate_strategy_risk(
    strategy_name,
    log_returns,
    simulations=DEFAULT_SIMULATIONS,
    safe_f_increment=DEFAULT_SAFE_F_INCREMENT,
    safe_f_start=DEFAULT_SAFE_F_START,
    bankroll=DEFAULT_BANKROLL,
    drawdown_limit=DEFAULT_DRAWDOWN_LIMIT,
    pct_above_dd_limit=DEFAULT_PCT_ABOVE_DD_LIMIT,
    periods_per_year=DEFAULT_PERIODS_PER_YEAR,
    random_seed=None,
):
    returns = pd.Series(log_returns).dropna().astype(float).to_numpy()

    if len(returns) == 0:
        return None, pd.DataFrame(), pd.DataFrame()

    sampled_returns = resample_returns(
        returns,
        simulations,
        random_seed,
    )
    safe_f = find_safe_f(
        sampled_returns,
        safe_f_start,
        safe_f_increment,
        drawdown_limit,
        pct_above_dd_limit,
    )
    scaled_returns = sampled_returns * safe_f
    scaled_log_returns = simple_returns_to_log_returns(scaled_returns)
    cumulative_returns = cumulative_log_returns(scaled_log_returns)
    drawdowns = drawdowns_from_cumulative_log_returns(cumulative_returns)
    modified_cumulative_returns, _ = apply_kill_switch(
        cumulative_returns,
        drawdowns,
        drawdown_limit,
    )
    performance = performance_summary(
        modified_cumulative_returns,
        periods_per_year,
    )
    performance["Strategy_Name"] = strategy_name
    performance = performance[
        [
            "Strategy_Name",
            "simulation",
            "cagr",
            "annualized_volatility",
            "sharpe_ratio",
            "max_drawdown",
            "profit_factor",
            "mar_ratio",
        ]
    ]
    quantiles = quantile_summary(performance)
    car25 = quantiles.loc[
        quantiles["Metric"] == "cagr",
        "Q25",
    ].iloc[0]
    profit_factor_q25 = quantiles.loc[
        quantiles["Metric"] == "profit_factor",
        "Q25",
    ].iloc[0]
    risk_per_trade_dollars = bankroll * safe_f

    summary = {
        "Strategy_Name": strategy_name,
        "trade_count": len(returns),
        "simulations": simulations,
        "safe_f": safe_f,
        "risk_per_trade_dollars": risk_per_trade_dollars,
        "CAR25": car25,
        "profit_factor_Q25": profit_factor_q25,
        "bankroll": bankroll,
        "drawdown_limit": drawdown_limit,
        "pct_above_drawdown_limit": pct_above_dd_limit,
        "safe_f_increment": safe_f_increment,
        "last_n_trades": None,
    }

    return summary, quantiles, performance


def write_strategy_report(summary, quantiles, output_dir):
    os.makedirs(
        output_dir,
        exist_ok=True,
    )
    strategy_name = summary["Strategy_Name"]
    base = safe_filename(strategy_name)
    text_file = f"{output_dir}/{base}_risk_per_trade.txt"
    quantile_file = f"{output_dir}/{base}_risk_metric_quantiles.csv"

    quantiles.to_csv(
        quantile_file,
        index=False,
    )

    with open(text_file, "w", encoding="utf-8") as file:
        file.write(f"Strategy Name: {strategy_name}\n")
        file.write(f"CAR25: {summary['CAR25']:.4f}\n")
        file.write(
            "Risk Per Trade (dollars): "
            f"${summary['risk_per_trade_dollars']:,.2f}\n"
        )
        file.write(
            "Safe F - percentage of account to risk with this strategy: "
            f"{summary['safe_f']:.2%}\n"
        )
        file.write(f"Trade count: {summary['trade_count']}\n")
        file.write(f"Simulations: {summary['simulations']}\n")
        file.write(f"Drawdown limit: {summary['drawdown_limit']:.2%}\n")
        file.write(
            "Required simulations above drawdown limit: "
            f"{summary['pct_above_drawdown_limit']:.2%}\n"
        )
        file.write("\nPerformance Metrics:\n")
        file.write(
            quantiles.to_string(
                index=False,
                float_format=lambda value: f"{value:.4f}",
            )
        )
        file.write("\n")

    return text_file, quantile_file


def calculate_risk_per_trade_by_strategy(
    trades,
    output_dir=OUTPUT_DIR,
    return_column=RISK_RETURN_COLUMN,
    simulations=DEFAULT_SIMULATIONS,
    safe_f_increment=DEFAULT_SAFE_F_INCREMENT,
    safe_f_start=DEFAULT_SAFE_F_START,
    bankroll=DEFAULT_BANKROLL,
    drawdown_limit=DEFAULT_DRAWDOWN_LIMIT,
    pct_above_dd_limit=DEFAULT_PCT_ABOVE_DD_LIMIT,
    periods_per_year=DEFAULT_PERIODS_PER_YEAR,
    last_n_trades=0,
    random_seed=None,
    write_files=True,
):
    working = prepare_strategy_returns(
        trades,
        return_column,
        last_n_trades,
    )
    summaries = []
    performances = []
    report_files = []

    for strategy_name, group in working.groupby("Strategy_Name", sort=True):
        seed = (
            None
            if random_seed is None
            else (
                sum(ord(char) for char in str(strategy_name))
                + random_seed
            )
        )
        summary, quantiles, performance = calculate_strategy_risk(
            strategy_name,
            group[return_column],
            simulations,
            safe_f_increment,
            safe_f_start,
            bankroll,
            drawdown_limit,
            pct_above_dd_limit,
            periods_per_year,
            seed,
        )

        if summary is None:
            continue

        summary["last_n_trades"] = last_n_trades
        summaries.append(summary)
        performances.append(performance)

        if write_files:
            report_files.extend(
                write_strategy_report(
                    summary,
                    quantiles,
                    output_dir,
                )
            )

    summary_df = pd.DataFrame(summaries)
    performance_df = (
        pd.concat(
            performances,
            ignore_index=True,
        )
        if performances
        else pd.DataFrame()
    )

    if write_files:
        os.makedirs(
            output_dir,
            exist_ok=True,
        )
        summary_df.to_csv(
            f"{output_dir}/risk_per_trade_summary.csv",
            index=False,
        )
        performance_df.to_csv(
            f"{output_dir}/risk_per_trade_simulations.csv",
            index=False,
        )

    return summary_df, report_files, performance_df


def load_realized_trades_from_master(path):
    from analyze_strategy_performance import (
        aggregate_realized_trades,
        load_cleaned_trades,
    )

    cleaned_trades = load_cleaned_trades(path)

    return aggregate_realized_trades(cleaned_trades)


def load_risk_input(path):
    raw = pd.read_csv(path)

    if {
        "Strategy_Name",
        RISK_RETURN_COLUMN,
    }.issubset(raw.columns):
        return raw

    if {
        "strat_name",
        "log_return",
    }.issubset(raw.columns):
        legacy = raw.rename(columns={
            "strat_name": "Strategy_Name",
            "log_return": "log_return_on_margin",
        })
        legacy[RISK_RETURN_COLUMN] = np.exp(
            pd.to_numeric(
                legacy["log_return_on_margin"],
                errors="coerce",
            )
        ) - 1

        if {
            "date",
            "time",
        }.issubset(legacy.columns):
            legacy["Exec Time"] = pd.to_datetime(
                legacy["date"].astype(str) + " " + legacy["time"].astype(str),
                errors="coerce",
            ).dt.strftime("%m/%d/%y %H:%M:%S")

        return legacy

    return load_realized_trades_from_master(path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate Safe-F risk per trade by strategy."
    )
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to master_cleaned_tos_data.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help="Directory for risk-per-trade output files.",
    )
    parser.add_argument(
        "--simulations",
        type=int,
        default=DEFAULT_SIMULATIONS,
    )
    parser.add_argument(
        "--safe-f-increment",
        type=float,
        default=DEFAULT_SAFE_F_INCREMENT,
    )
    parser.add_argument(
        "--safe-f-start",
        type=float,
        default=DEFAULT_SAFE_F_START,
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=DEFAULT_BANKROLL,
    )
    parser.add_argument(
        "--drawdown-limit",
        type=float,
        default=DEFAULT_DRAWDOWN_LIMIT,
    )
    parser.add_argument(
        "--pct-above-dd-limit",
        type=float,
        default=DEFAULT_PCT_ABOVE_DD_LIMIT,
    )
    parser.add_argument(
        "--last-n-trades",
        type=int,
        default=0,
        help="Use only the most recent N realized trades per strategy. 0 uses all.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
    )

    return parser.parse_args()


def main():
    args = parse_args()
    realized_trades = load_risk_input(args.input)

    if realized_trades.empty:
        raise ValueError(
            "No realized trades were found for risk-per-trade calculation."
        )

    summary, report_files, _ = calculate_risk_per_trade_by_strategy(
        realized_trades,
        output_dir=args.output_dir,
        simulations=args.simulations,
        safe_f_increment=args.safe_f_increment,
        safe_f_start=args.safe_f_start,
        bankroll=args.bankroll,
        drawdown_limit=args.drawdown_limit,
        pct_above_dd_limit=args.pct_above_dd_limit,
        last_n_trades=args.last_n_trades,
        random_seed=args.random_seed,
    )

    print(f"Saved risk-per-trade summary to {args.output_dir}/risk_per_trade_summary.csv")
    print(f"Saved {len(report_files)} risk-per-trade report files.")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
