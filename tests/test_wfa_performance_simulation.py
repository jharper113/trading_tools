import numpy as np
import pytest

from wfa_performance_simulation import (
    DEFAULT_WFA_SIMULATIONS,
    build_wfa_simulation,
    optimize_risk_fraction,
    stationary_bootstrap_indices,
)


def test_stationary_bootstrap_is_deterministic_and_preserves_shape():
    first = stationary_bootstrap_indices(
        source_length=8,
        sample_length=12,
        simulations=25,
        mean_block_length=3,
        random_seed=17,
    )
    second = stationary_bootstrap_indices(
        source_length=8,
        sample_length=12,
        simulations=25,
        mean_block_length=3,
        random_seed=17,
    )

    assert first.shape == (12, 25)
    assert np.array_equal(first, second)
    assert first.min() >= 0
    assert first.max() < 8
    continued = first[1:] == ((first[:-1] + 1) % 8)
    assert continued.mean() > 0.45


def test_optimizer_selects_positive_risk_with_five_percent_drawdown_limit():
    trade_r = np.array([
        0.60,
        -0.35,
        0.45,
        -0.20,
        0.75,
        -0.50,
        0.30,
        0.55,
    ])
    indices = stationary_bootstrap_indices(
        len(trade_r),
        len(trade_r),
        800,
        mean_block_length=2,
        random_seed=11,
    )

    result, search = optimize_risk_fraction(
        trade_r[indices],
        elapsed_years=2,
        drawdown_limit=-0.20,
        max_breach_probability=0.05,
        max_risk_fraction=0.50,
        cagr_quantile=0.25,
    )

    assert result["risk_fraction"] > 0
    assert result["drawdown_breach_probability"] <= 0.05
    assert result["cagr_objective"] > 0
    assert not search.empty


def test_optimizer_reduces_non_positive_strategy_to_zero_risk():
    sampled_r = np.tile(
        np.array([-0.10, -0.20, -0.05, -0.30]).reshape(-1, 1),
        (1, 300),
    )

    result, _ = optimize_risk_fraction(
        sampled_r,
        elapsed_years=1,
        drawdown_limit=-0.20,
        max_breach_probability=0.05,
        max_risk_fraction=0.50,
        cagr_quantile=0.25,
    )

    assert result["risk_fraction"] == 0
    assert result["cagr_objective"] == 0
    assert result["drawdown_breach_probability"] == 0


def test_wfa_simulation_returns_paths_and_explicit_iqr_values():
    trade_r = [0.50, -0.30, 0.25, -0.10, 0.80, -0.45] * 3
    simulation = build_wfa_simulation(
        trade_r,
        elapsed_years=3,
        strategy_name="0001_2RSI_Daily_LongAndShort_ES",
        simulations=400,
        mean_block_length=3,
        random_seed=19,
    )

    assert DEFAULT_WFA_SIMULATIONS == 5000
    assert len(simulation["results"]) == 400
    assert simulation["results"]["sample_trades"].unique().tolist() == [18]
    assert simulation["optimization"]["strategy_name"] == (
        "0001_2RSI_Daily_LongAndShort_ES"
    )
    assert {
        "Q05",
        "Q25",
        "Median",
        "Q75",
        "Q95",
        "IQR",
        "Observed WFA",
    }.issubset(simulation["summary"].columns)
    row = simulation["summary"].set_index("metric_key").loc["expectancy_r"]
    assert row["IQR"] == pytest.approx(row["Q75"] - row["Q25"])
