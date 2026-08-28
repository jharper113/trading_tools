"""Walk-forward Monte Carlo benchmarks and drawdown-constrained sizing."""

import numpy as np
import pandas as pd


DEFAULT_WFA_SIMULATIONS = 5000
DEFAULT_WFA_BLOCK_LENGTH = 3.0
DEFAULT_WFA_DRAWDOWN_LIMIT = -0.20
DEFAULT_WFA_MAX_DRAWDOWN_BREACH_PROBABILITY = 0.05
DEFAULT_WFA_MAX_RISK_FRACTION = 1.0
DEFAULT_WFA_CAGR_QUANTILE = 0.25


METRIC_SPECS = (
    ("cagr", "CAGR", "percent", "higher"),
    ("profit_factor", "Profit Factor", "ratio", "higher"),
    ("max_drawdown", "Maximum Drawdown", "percent", "higher"),
    ("expectancy_r", "Expectancy", "R / trade", "higher"),
    ("total_r", "Total Return", "R", "higher"),
    ("max_drawdown_r", "Maximum Drawdown", "R", "higher"),
    ("win_rate", "Win Rate", "percent", "higher"),
    (
        "longest_losing_streak",
        "Longest Losing Streak",
        "trades",
        "lower",
    ),
)


def stationary_bootstrap_indices(
    source_length,
    sample_length,
    simulations,
    mean_block_length=DEFAULT_WFA_BLOCK_LENGTH,
    random_seed=42,
):
    """Return circular stationary-bootstrap indexes.

    A new block starts with probability 1 / mean_block_length. Otherwise the
    next consecutive source trade is selected, preserving short trade clusters.
    """
    if source_length <= 0:
        raise ValueError("Stationary bootstrap requires at least one trade.")
    if sample_length <= 0:
        raise ValueError("Stationary bootstrap sample length must be positive.")
    if simulations <= 0:
        raise ValueError("WFA simulation count must be positive.")
    if mean_block_length < 1:
        raise ValueError("WFA mean block length must be at least 1 trade.")

    rng = np.random.default_rng(random_seed)
    restart_probability = 1.0 / min(float(mean_block_length), source_length)
    indices = np.empty((sample_length, simulations), dtype=np.int64)
    indices[0] = rng.integers(0, source_length, size=simulations)

    for row_index in range(1, sample_length):
        restart = rng.random(simulations) < restart_probability
        random_starts = rng.integers(0, source_length, size=simulations)
        continued = (indices[row_index - 1] + 1) % source_length
        indices[row_index] = np.where(restart, random_starts, continued)

    return indices


def _equity_path_metrics(sampled_r, risk_fraction, elapsed_years):
    period_factors = 1.0 + sampled_r * float(risk_fraction)
    ruined = np.any(period_factors <= 0, axis=0)
    safe_factors = np.where(period_factors > 0, period_factors, 0.0)
    equity = np.cumprod(safe_factors, axis=0)
    equity_with_start = np.vstack([
        np.ones((1, equity.shape[1])),
        equity,
    ])
    peaks = np.maximum.accumulate(equity_with_start, axis=0)
    drawdowns = np.divide(
        equity_with_start,
        peaks,
        out=np.zeros_like(equity_with_start),
        where=peaks > 0,
    ) - 1.0
    max_drawdown = np.min(drawdowns, axis=0)
    ending_equity = equity[-1]
    years = max(float(elapsed_years), 1 / 365.25)
    cagr = np.full(ending_equity.shape, -1.0)
    survived = (~ruined) & (ending_equity > 0)
    cagr[survived] = np.exp(
        np.log(ending_equity[survived]) / years
    ) - 1.0

    return ending_equity, cagr, max_drawdown


def _longest_losing_streaks(sampled_r):
    streaks = np.zeros(sampled_r.shape[1], dtype=int)
    current = np.zeros(sampled_r.shape[1], dtype=int)

    for row in sampled_r:
        current = np.where(row < 0, current + 1, 0)
        streaks = np.maximum(streaks, current)

    return streaks


def simulation_metrics(sampled_r, risk_fraction, elapsed_years):
    """Calculate one row of performance statistics for each simulated path."""
    sampled_r = np.asarray(sampled_r, dtype=float)
    if sampled_r.ndim != 2 or sampled_r.shape[0] == 0:
        return pd.DataFrame()

    ending_equity, cagr, max_drawdown = _equity_path_metrics(
        sampled_r,
        risk_fraction,
        elapsed_years,
    )
    gross_profit = np.where(sampled_r > 0, sampled_r, 0).sum(axis=0)
    gross_loss = np.abs(np.where(sampled_r < 0, sampled_r, 0).sum(axis=0))
    profit_factor = np.divide(
        gross_profit,
        gross_loss,
        out=np.full(sampled_r.shape[1], np.nan),
        where=gross_loss > 0,
    )
    cumulative_r = np.cumsum(sampled_r, axis=0)
    cumulative_r = np.vstack([
        np.zeros((1, cumulative_r.shape[1])),
        cumulative_r,
    ])
    peak_r = np.maximum.accumulate(cumulative_r, axis=0)

    return pd.DataFrame({
        "simulation": np.arange(1, sampled_r.shape[1] + 1),
        "sample_trades": sampled_r.shape[0],
        "risk_fraction": float(risk_fraction),
        "cagr": cagr,
        "ending_return": ending_equity - 1.0,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "expectancy_r": sampled_r.mean(axis=0),
        "total_r": sampled_r.sum(axis=0),
        "max_drawdown_r": np.min(cumulative_r - peak_r, axis=0),
        "win_rate": (sampled_r > 0).mean(axis=0),
        "longest_losing_streak": _longest_losing_streaks(sampled_r),
    })


def _candidate_statistics(
    sampled_r,
    risk_fraction,
    elapsed_years,
    drawdown_limit,
    cagr_quantile,
):
    _, cagr, max_drawdown = _equity_path_metrics(
        sampled_r,
        risk_fraction,
        elapsed_years,
    )
    return {
        "risk_fraction": float(risk_fraction),
        "drawdown_breach_probability": float(
            np.mean(max_drawdown < drawdown_limit)
        ),
        "cagr_objective": float(np.quantile(cagr, cagr_quantile)),
        "median_cagr": float(np.median(cagr)),
        "drawdown_q05": float(np.quantile(max_drawdown, 0.05)),
    }


def optimize_risk_fraction(
    sampled_r,
    elapsed_years,
    drawdown_limit=DEFAULT_WFA_DRAWDOWN_LIMIT,
    max_breach_probability=DEFAULT_WFA_MAX_DRAWDOWN_BREACH_PROBABILITY,
    max_risk_fraction=DEFAULT_WFA_MAX_RISK_FRACTION,
    cagr_quantile=DEFAULT_WFA_CAGR_QUANTILE,
    tolerance=0.0001,
):
    """Maximize a conservative CAGR percentile under a drawdown constraint."""
    sampled_r = np.asarray(sampled_r, dtype=float)
    if sampled_r.ndim != 2 or sampled_r.shape[0] == 0:
        raise ValueError("WFA risk optimization requires simulated R returns.")
    if not -1 < drawdown_limit < 0:
        raise ValueError("WFA drawdown limit must be between -1 and 0.")
    if not 0 <= max_breach_probability < 1:
        raise ValueError("WFA maximum drawdown breach probability is invalid.")
    if max_risk_fraction <= 0:
        raise ValueError("WFA maximum risk fraction must be positive.")
    if not 0 < cagr_quantile <= 0.5:
        raise ValueError("WFA CAGR objective quantile must be in (0, 0.5].")

    evaluated = {}

    def evaluate(risk_fraction):
        key = round(float(risk_fraction), 10)
        if key not in evaluated:
            evaluated[key] = _candidate_statistics(
                sampled_r,
                key,
                elapsed_years,
                drawdown_limit,
                cagr_quantile,
            )
        return evaluated[key]

    low = 0.0
    high = float(max_risk_fraction)
    if evaluate(high)["drawdown_breach_probability"] <= max_breach_probability:
        risk_ceiling = high
    else:
        while high - low > tolerance:
            midpoint = (low + high) / 2.0
            if (
                evaluate(midpoint)["drawdown_breach_probability"]
                <= max_breach_probability
            ):
                low = midpoint
            else:
                high = midpoint
        risk_ceiling = low

    search_left = 0.0
    search_right = risk_ceiling
    best = evaluate(0.0)

    # A coarse search followed by local refinements handles the concave growth
    # curve without assuming that the drawdown boundary is the growth optimum.
    for _ in range(3):
        candidates = np.linspace(search_left, search_right, 61)
        feasible = [
            evaluate(candidate)
            for candidate in candidates
            if (
                evaluate(candidate)["drawdown_breach_probability"]
                <= max_breach_probability
            )
        ]
        if not feasible:
            break
        best = max(
            feasible,
            key=lambda row: (row["cagr_objective"], -row["risk_fraction"]),
        )
        spacing = (
            (search_right - search_left) / max(len(candidates) - 1, 1)
        )
        search_left = max(0.0, best["risk_fraction"] - spacing)
        search_right = min(
            risk_ceiling,
            best["risk_fraction"] + spacing,
        )

    search = pd.DataFrame(evaluated.values()).sort_values("risk_fraction")
    result = dict(best)
    result.update({
        "risk_ceiling_fraction": float(risk_ceiling),
        "drawdown_limit": float(drawdown_limit),
        "max_drawdown_breach_probability": float(max_breach_probability),
        "cagr_objective_quantile": float(cagr_quantile),
    })
    return result, search.reset_index(drop=True)


def summarize_simulations(simulations, observed=None):
    rows = []
    observed = observed or {}

    for key, label, unit, adverse_direction in METRIC_SPECS:
        if key not in simulations.columns:
            continue
        values = pd.to_numeric(simulations[key], errors="coerce")
        values = values.replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            continue
        q05, q25, median, q75, q95 = values.quantile(
            [0.05, 0.25, 0.50, 0.75, 0.95]
        )
        rows.append({
            "metric_key": key,
            "Metric": label,
            "Unit": unit,
            "Adverse Direction": adverse_direction,
            "Q05": q05,
            "Q25": q25,
            "Median": median,
            "Q75": q75,
            "Q95": q95,
            "IQR": q75 - q25,
            "Observed WFA": observed.get(key),
        })

    return pd.DataFrame(rows)


def build_wfa_simulation(
    trade_r,
    elapsed_years,
    strategy_name,
    simulations=DEFAULT_WFA_SIMULATIONS,
    mean_block_length=DEFAULT_WFA_BLOCK_LENGTH,
    drawdown_limit=DEFAULT_WFA_DRAWDOWN_LIMIT,
    max_breach_probability=DEFAULT_WFA_MAX_DRAWDOWN_BREACH_PROBABILITY,
    max_risk_fraction=DEFAULT_WFA_MAX_RISK_FRACTION,
    cagr_quantile=DEFAULT_WFA_CAGR_QUANTILE,
    random_seed=42,
):
    values = pd.to_numeric(pd.Series(trade_r), errors="coerce").dropna().to_numpy()
    if len(values) == 0:
        raise ValueError("WFA simulation requires valid initial-risk returns.")

    indices = stationary_bootstrap_indices(
        len(values),
        len(values),
        simulations,
        mean_block_length,
        random_seed,
    )
    sampled_r = values[indices]
    optimization, risk_search = optimize_risk_fraction(
        sampled_r,
        elapsed_years,
        drawdown_limit,
        max_breach_probability,
        max_risk_fraction,
        cagr_quantile,
    )
    risk_fraction = optimization["risk_fraction"]
    results = simulation_metrics(sampled_r, risk_fraction, elapsed_years)
    results.insert(0, "Strategy_Name", strategy_name)
    observed_frame = simulation_metrics(
        values.reshape(-1, 1),
        risk_fraction,
        elapsed_years,
    )
    observed = (
        observed_frame.iloc[0].to_dict()
        if not observed_frame.empty
        else {}
    )
    summary = summarize_simulations(results, observed)

    optimization.update({
        "simulations": int(simulations),
        "source_trades": int(len(values)),
        "elapsed_years": float(elapsed_years),
        "mean_block_length": float(mean_block_length),
        "strategy_name": strategy_name,
    })
    return {
        "results": results,
        "summary": summary,
        "optimization": optimization,
        "risk_search": risk_search,
        "observed": observed,
    }
