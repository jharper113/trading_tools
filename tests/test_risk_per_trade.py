import pandas as pd
import pytest

from calculate_risk_per_trade import (
    calculate_risk_per_trade_by_strategy,
    calculate_strategy_risk,
    load_risk_input,
    prepare_strategy_returns,
)


def test_prepare_strategy_returns_uses_last_n_trades_by_strategy():
    trades = pd.DataFrame([
        {
            "Strategy_Name": "A",
            "Exec Time": "1/1/26 09:30:00",
            "return_on_margin": 0.01,
        },
        {
            "Strategy_Name": "A",
            "Exec Time": "1/2/26 09:30:00",
            "return_on_margin": 0.02,
        },
        {
            "Strategy_Name": "B",
            "Exec Time": "1/1/26 09:30:00",
            "return_on_margin": -0.01,
        },
        {
            "Strategy_Name": "B",
            "Exec Time": "1/2/26 09:30:00",
            "return_on_margin": 0.03,
        },
    ])

    prepared = prepare_strategy_returns(
        trades,
        last_n_trades=1,
    )

    assert prepared["Strategy_Name"].tolist() == [
        "A",
        "B",
    ]
    assert prepared["return_on_margin"].tolist() == [
        0.02,
        0.03,
    ]


def test_load_risk_input_accepts_legacy_r_trade_set(tmp_path):
    input_file = tmp_path / "legacy_trade_set.csv"
    pd.DataFrame([
        {
            "strat_name": "Legacy Strategy",
            "date": "2024-01-02",
            "time": "15:00:00",
            "log_return": 0.01,
        },
    ]).to_csv(
        input_file,
        index=False,
    )

    loaded = load_risk_input(input_file)

    assert loaded["Strategy_Name"].tolist() == [
        "Legacy Strategy",
    ]
    assert loaded["return_on_margin"].tolist() == [
        pytest.approx(0.010050167),
    ]
    assert loaded["Exec Time"].tolist() == [
        "01/02/24 15:00:00",
    ]


def test_prepare_strategy_returns_keeps_losses_larger_than_margin():
    trades = pd.DataFrame([
        {
            "Strategy_Name": "A",
            "Exec Time": "1/1/26 09:30:00",
            "return_on_margin": -1.25,
        },
    ])

    prepared = prepare_strategy_returns(trades)

    assert prepared["return_on_margin"].tolist() == [
        -1.25,
    ]


def test_calculate_strategy_risk_returns_safe_f_and_car25():
    summary, quantiles, performance = calculate_strategy_risk(
        "Test Strategy",
        [0.01, -0.005, 0.012, 0.004, -0.003],
        simulations=50,
        safe_f_increment=0.25,
        random_seed=42,
    )

    assert summary["Strategy_Name"] == "Test Strategy"
    assert summary["trade_count"] == 5
    assert 0 <= summary["safe_f"] <= 1
    assert "CAR25" in summary
    assert quantiles["Metric"].tolist() == [
        "cagr",
        "annualized_volatility",
        "sharpe_ratio",
        "max_drawdown",
        "profit_factor",
        "mar_ratio",
    ]
    assert performance["Strategy_Name"].unique().tolist() == [
        "Test Strategy",
    ]
    assert len(performance) == 50


def test_calculate_risk_per_trade_by_strategy_writes_reports(tmp_path):
    trades = pd.DataFrame([
        {
            "Strategy_Name": "A Strategy",
            "Exec Time": "1/1/26 09:30:00",
            "return_on_margin": 0.01,
        },
        {
            "Strategy_Name": "A Strategy",
            "Exec Time": "1/2/26 09:30:00",
            "return_on_margin": -0.005,
        },
        {
            "Strategy_Name": "B Strategy",
            "Exec Time": "1/1/26 09:30:00",
            "return_on_margin": 0.02,
        },
        {
            "Strategy_Name": "B Strategy",
            "Exec Time": "1/2/26 09:30:00",
            "return_on_margin": 0.01,
        },
    ])

    summary, report_files, performance = calculate_risk_per_trade_by_strategy(
        trades,
        output_dir=tmp_path,
        simulations=20,
        safe_f_increment=0.5,
        random_seed=42,
    )

    assert summary["Strategy_Name"].tolist() == [
        "A Strategy",
        "B Strategy",
    ]
    assert len(report_files) == 4
    assert len(performance) == 40
    assert (tmp_path / "risk_per_trade_summary.csv").exists()
    assert (tmp_path / "risk_per_trade_simulations.csv").exists()
    assert (tmp_path / "A_Strategy_risk_per_trade.txt").exists()
    assert (tmp_path / "B_Strategy_risk_metric_quantiles.csv").exists()
