"""Horizon-matched WFA benchmarks for monitoring live strategy degradation."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from wfa_performance_simulation import (
    DEFAULT_WFA_BLOCK_LENGTH,
    DEFAULT_WFA_SIMULATIONS,
    simulation_metrics,
    stationary_bootstrap_indices,
)


DEFAULT_LIVE_MONITOR_SIMULATIONS = DEFAULT_WFA_SIMULATIONS
DEFAULT_LIVE_MONITOR_MIN_TRADES = 5

HORIZONS = (
    {
        "key": "3m",
        "label": "Rolling 3 Months",
        "months": 3,
        "minimum_live_trades": DEFAULT_LIVE_MONITOR_MIN_TRADES,
        "informational": True,
    },
    {
        "key": "6m",
        "label": "Rolling 6 Months",
        "months": 6,
        "minimum_live_trades": DEFAULT_LIVE_MONITOR_MIN_TRADES,
        "informational": False,
    },
    {
        "key": "12m",
        "label": "Rolling 12 Months",
        "months": 12,
        "minimum_live_trades": DEFAULT_LIVE_MONITOR_MIN_TRADES,
        "informational": False,
    },
    {
        "key": "10t",
        "label": "Last 10 Trades",
        "trade_count": 10,
        "minimum_live_trades": 10,
        "informational": False,
    },
)

METRICS = (
    ("sample_trades", "Trade Count", "trades", "context"),
    ("total_r", "Total Return", "R", "higher"),
    ("max_drawdown_r", "Maximum Drawdown", "R", "higher"),
    ("expectancy_r", "Expectancy", "R / trade", "higher"),
    ("profit_factor", "Profit Factor", "ratio", "higher"),
    ("win_rate", "Win Rate", "percent", "higher"),
    (
        "longest_losing_streak",
        "Longest Losing Streak",
        "trades",
        "lower",
    ),
)

PRIMARY_STATUS_METRICS = ("total_r", "max_drawdown_r")


def _clean_strategy_name(value):
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def _initial_risk_returns(trades):
    direct = pd.to_numeric(
        trades.get(
            "WFA_Return_On_Initial_Risk",
            pd.Series(index=trades.index, dtype=float),
        ),
        errors="coerce",
    )
    pnl = pd.to_numeric(trades.get("net_pnl"), errors="coerce")
    total_risk = pd.to_numeric(
        trades.get(
            "WFA_Total_Initial_Risk",
            pd.Series(index=trades.index, dtype=float),
        ),
        errors="coerce",
    ).abs()
    calculated = pnl / total_risk.where(total_risk > 0)
    return calculated.where(calculated.notna(), direct)


def load_wfa_monitor_baselines(library_dir):
    baseline_root = Path(library_dir).expanduser() / "_wfa_baselines"
    baselines = []

    for metadata_file in sorted(baseline_root.glob("*/metadata.json")):
        strategy_dir = metadata_file.parent
        trades_file = strategy_dir / "trades.csv"
        if not trades_file.exists():
            continue

        try:
            metadata = json.loads(metadata_file.read_text())
            trades = pd.read_csv(trades_file)
        except (OSError, json.JSONDecodeError, pd.errors.ParserError):
            continue

        strategy_name = _clean_strategy_name(metadata.get("strategy_name"))
        if not strategy_name or trades.empty:
            continue

        trades["timestamp"] = pd.to_datetime(
            trades.get("timestamp"),
            errors="coerce",
        )
        trades["initial_risk_r"] = _initial_risk_returns(trades)
        trades = trades.dropna(subset=["timestamp", "initial_risk_r"])
        if trades.empty:
            continue

        baselines.append({
            "strategy_name": strategy_name,
            "metadata": metadata,
            "trades": trades.sort_values("timestamp"),
        })

    return baselines


def _calendar_window_counts(timestamps, months):
    timestamps = pd.Series(pd.to_datetime(timestamps)).dropna().sort_values()
    if timestamps.empty:
        return np.array([], dtype=int)

    first = timestamps.min()
    last = timestamps.max()
    first_full_window = first + pd.DateOffset(months=months)
    month_ends = pd.date_range(
        start=first + pd.offsets.MonthEnd(0),
        end=last,
        freq="ME",
    )
    counts = [
        int(
            (
                (timestamps > month_end - pd.DateOffset(months=months))
                & (timestamps <= month_end)
            ).sum()
        )
        for month_end in month_ends
        if month_end >= first_full_window
    ]
    return np.asarray(counts, dtype=int)


def _empty_metric_frame(simulations, sample_count=0):
    return pd.DataFrame({
        "sample_trades": np.full(simulations, sample_count, dtype=int),
        "total_r": np.zeros(simulations),
        "max_drawdown_r": np.zeros(simulations),
        "expectancy_r": np.full(simulations, np.nan),
        "profit_factor": np.full(simulations, np.nan),
        "win_rate": np.full(simulations, np.nan),
        "longest_losing_streak": np.zeros(simulations, dtype=int),
    })


def _simulate_horizon(
    source_r,
    sample_counts,
    simulations,
    mean_block_length,
    random_seed,
):
    source_r = np.asarray(source_r, dtype=float)
    sample_counts = np.asarray(sample_counts, dtype=int)
    if source_r.size == 0 or sample_counts.size == 0:
        return pd.DataFrame()

    rng = np.random.default_rng(random_seed)
    selected_counts = rng.choice(sample_counts, size=simulations, replace=True)
    frames = []
    for sample_count in sorted(np.unique(selected_counts)):
        path_count = int((selected_counts == sample_count).sum())
        if sample_count <= 0:
            frames.append(_empty_metric_frame(path_count))
            continue

        indices = stationary_bootstrap_indices(
            len(source_r),
            int(sample_count),
            path_count,
            mean_block_length=mean_block_length,
            random_seed=random_seed + int(sample_count) * 101,
        )
        metrics = simulation_metrics(
            source_r[indices],
            risk_fraction=0.01,
            elapsed_years=1.0,
        )
        frames.append(metrics[[metric[0] for metric in METRICS]])

    result = pd.concat(frames, ignore_index=True)
    return result.sample(
        frac=1,
        random_state=random_seed,
    ).reset_index(drop=True)


def _observed_metrics(values):
    values = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy()
    if values.size == 0:
        return _empty_metric_frame(1).iloc[0].to_dict()

    metrics = simulation_metrics(
        values.reshape(-1, 1),
        risk_fraction=0.01,
        elapsed_years=1.0,
    )
    return metrics.iloc[0].to_dict()


def _metric_status(observed, row):
    if observed is None or pd.isna(observed):
        return "Insufficient Data"

    direction = row["Adverse Direction"]
    if direction == "higher":
        if observed < row["Q05"]:
            return "Critical"
        if observed < row["Q25"]:
            return "Watch"
        return "Normal"
    if direction == "lower":
        if observed > row["Q95"]:
            return "Critical"
        if observed > row["Q75"]:
            return "Watch"
        return "Normal"
    return "Informational"


def _summarize_horizon(simulated, observed):
    rows = []
    for key, label, unit, direction in METRICS:
        values = pd.to_numeric(simulated.get(key), errors="coerce")
        values = values.replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            continue
        q05, q25, median, q75, q95 = values.quantile(
            [0.05, 0.25, 0.50, 0.75, 0.95]
        )
        row = {
            "metric_key": key,
            "Metric": label,
            "Unit": unit,
            "Adverse Direction": direction,
            "Q05": float(q05),
            "Q25": float(q25),
            "Median": float(median),
            "Q75": float(q75),
            "Q95": float(q95),
            "IQR": float(q75 - q25),
            "Observed Live": observed.get(key),
        }
        row["Metric Status"] = _metric_status(
            row["Observed Live"],
            row,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _allocation_risk(capital_allocation, strategy_name, metadata):
    metadata_risk = pd.to_numeric(
        pd.Series([metadata.get("allocated_risk_per_trade_dollars")]),
        errors="coerce",
    ).iloc[0]
    if pd.notna(metadata_risk) and metadata_risk > 0:
        return float(metadata_risk), "WFA deployment allocated risk"

    if capital_allocation is None or capital_allocation.empty:
        return None, "initial stop risk unavailable"
    matches = capital_allocation[
        capital_allocation["Strategy_Name"].astype(str) == strategy_name
    ]
    if matches.empty:
        return None, "initial stop risk unavailable"
    row = matches.iloc[0]
    for column, label in (
        ("allocated_risk_per_trade_dollars", "current allocated risk"),
        ("risk_budget_dollars", "current risk budget"),
        ("risk_per_trade_dollars", "current Safe-F risk budget"),
    ):
        value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
        if pd.notna(value) and value > 0:
            return float(value), label
    return None, "initial stop risk unavailable"


def _live_r_values(live_trades, strategy_name, metadata, capital_allocation):
    strategy_trades = live_trades[
        live_trades["Strategy_Name"].astype(str) == strategy_name
    ].copy()
    strategy_trades["timestamp"] = pd.to_datetime(
        strategy_trades.get("timestamp"),
        errors="coerce",
    )
    strategy_trades = strategy_trades.dropna(subset=["timestamp"])
    if strategy_trades.empty:
        strategy_trades["live_r"] = pd.Series(dtype=float)
        return strategy_trades, "awaiting live trades"

    direct_r = _initial_risk_returns(strategy_trades)
    if direct_r.notna().all():
        strategy_trades["live_r"] = direct_r
        return strategy_trades, "recorded initial stop risk"

    risk_dollars, basis = _allocation_risk(
        capital_allocation,
        strategy_name,
        metadata,
    )
    if risk_dollars is None:
        strategy_trades["live_r"] = np.nan
        return strategy_trades, basis

    strategy_trades["live_r"] = (
        pd.to_numeric(strategy_trades.get("net_pnl"), errors="coerce")
        / risk_dollars
    )
    return strategy_trades, f"estimated from {basis} (${risk_dollars:,.2f})"


def build_live_degradation_monitor(
    live_trades,
    library_dir,
    capital_allocation=None,
    simulations=DEFAULT_LIVE_MONITOR_SIMULATIONS,
    mean_block_length=DEFAULT_WFA_BLOCK_LENGTH,
    random_seed=42,
    as_of_date=None,
):
    if simulations <= 0:
        raise ValueError("Live degradation simulations must be positive.")

    baselines = load_wfa_monitor_baselines(library_dir)
    as_of = pd.Timestamp(as_of_date or pd.Timestamp.now()).tz_localize(None)
    horizon_rows = []
    metric_frames = []
    strategy_rows = []

    for baseline_index, baseline in enumerate(baselines):
        strategy_name = baseline["strategy_name"]
        baseline_trades = baseline["trades"]
        source_r = baseline_trades["initial_risk_r"].to_numpy(dtype=float)
        live_strategy, r_basis = _live_r_values(
            live_trades,
            strategy_name,
            baseline["metadata"],
            capital_allocation,
        )
        live_strategy = live_strategy[live_strategy["timestamp"] <= as_of]
        strategy_statuses = []

        for horizon_index, horizon in enumerate(HORIZONS):
            if "months" in horizon:
                sample_counts = _calendar_window_counts(
                    baseline_trades["timestamp"],
                    horizon["months"],
                )
                window_start = as_of - pd.DateOffset(months=horizon["months"])
                observed_trades = live_strategy[
                    live_strategy["timestamp"] > window_start
                ]
            else:
                sample_counts = np.asarray([horizon["trade_count"]], dtype=int)
                observed_trades = live_strategy.tail(horizon["trade_count"])

            observed = _observed_metrics(observed_trades.get("live_r"))
            horizon_seed = random_seed + baseline_index * 1000 + horizon_index * 100
            simulated = _simulate_horizon(
                source_r,
                sample_counts,
                simulations,
                mean_block_length,
                horizon_seed,
            )
            if simulated.empty:
                continue
            summary = _summarize_horizon(simulated, observed)
            summary.insert(0, "Horizon", horizon["label"])
            summary.insert(0, "horizon_key", horizon["key"])
            summary.insert(0, "Strategy_Name", strategy_name)
            metric_frames.append(summary)

            observed_count = int(len(observed_trades))
            live_r_available = observed_trades.get(
                "live_r",
                pd.Series(dtype=float),
            ).notna().sum()
            if live_strategy.empty:
                status = "Awaiting Live Trades"
            elif live_r_available < horizon["minimum_live_trades"]:
                status = "Insufficient Data"
            elif horizon["informational"]:
                status = "Informational"
            else:
                primary = summary[
                    summary["metric_key"].isin(PRIMARY_STATUS_METRICS)
                ]["Metric Status"].tolist()
                status = (
                    "Critical"
                    if "Critical" in primary
                    else "Watch"
                    if "Watch" in primary
                    else "Normal"
                )
                strategy_statuses.append(status)

            indexed = summary.set_index("metric_key")
            count_band = indexed.loc["sample_trades"]
            total_band = indexed.loc["total_r"]
            drawdown_band = indexed.loc["max_drawdown_r"]
            horizon_rows.append({
                "Strategy_Name": strategy_name,
                "horizon_key": horizon["key"],
                "Horizon": horizon["label"],
                "Status": status,
                "Informational": bool(horizon["informational"]),
                "Observed Trades": observed_count,
                "Expected Trades Q25": count_band["Q25"],
                "Expected Trades Q75": count_band["Q75"],
                "Observed Total R": total_band["Observed Live"],
                "Total R Q05": total_band["Q05"],
                "Total R Q25": total_band["Q25"],
                "Total R Median": total_band["Median"],
                "Total R Q75": total_band["Q75"],
                "Observed Max Drawdown R": drawdown_band["Observed Live"],
                "Max Drawdown R Q05": drawdown_band["Q05"],
                "Max Drawdown R Q25": drawdown_band["Q25"],
                "Max Drawdown R Median": drawdown_band["Median"],
                "Max Drawdown R Q75": drawdown_band["Q75"],
                "R Basis": r_basis,
                "Simulations": int(simulations),
            })

        if live_strategy.empty:
            overall = "Awaiting Live Trades"
        elif "Critical" in strategy_statuses:
            overall = "Critical"
        elif "Watch" in strategy_statuses:
            overall = "Watch"
        elif "Normal" in strategy_statuses:
            overall = "Normal"
        else:
            overall = "Insufficient Data"
        strategy_rows.append({
            "Strategy_Name": strategy_name,
            "Status": overall,
            "Live Trades": int(len(live_strategy)),
            "R Basis": r_basis,
            "WFA Trades": int(len(baseline_trades)),
            "WFA Start": baseline_trades["timestamp"].min().date().isoformat(),
            "WFA End": baseline_trades["timestamp"].max().date().isoformat(),
        })

    return {
        "strategies": pd.DataFrame(strategy_rows),
        "horizons": pd.DataFrame(horizon_rows),
        "metric_bands": (
            pd.concat(metric_frames, ignore_index=True)
            if metric_frames
            else pd.DataFrame()
        ),
        "simulations": int(simulations),
        "library_dir": str(Path(library_dir).expanduser()),
    }
