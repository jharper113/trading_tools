import json

import pandas as pd

from analyze_strategy_performance import export_strategy_library


def strategy_frames(name, pnl_values, symbol):
    timestamps = pd.bdate_range("2025-01-02", periods=len(pnl_values))
    trades = pd.DataFrame({
        "timestamp": timestamps,
        "Strategy_Name": [name] * len(pnl_values),
        "Symbol": [symbol] * len(pnl_values),
        "net_pnl": pnl_values,
        "strategy_pnl": pnl_values,
        "strategy_cumulative_pnl": pd.Series(pnl_values).cumsum(),
    })
    equity = trades.copy()
    equity["CMPNL"] = equity["strategy_pnl"].cumsum()
    equity["strategy_equity"] = 100000 + equity["CMPNL"]
    return trades, equity


def test_strategy_library_preserves_prior_exports_and_builds_correlations(
    tmp_path,
):
    strategy_a_trades, strategy_a_equity = strategy_frames(
        "Strategy A",
        [100.0, -50.0, 200.0, 25.0, -10.0] * 5,
        "/ES",
    )
    strategy_b_trades, strategy_b_equity = strategy_frames(
        "Strategy B",
        [200.0, -100.0, 400.0, 50.0, -20.0] * 5,
        "/CL",
    )

    export_strategy_library(
        strategy_a_trades,
        strategy_a_equity,
        tmp_path,
        source_input="wfa_a.csv",
        analysis_mode="wfa",
        capital_allocation=pd.DataFrame([{
            "Strategy_Name": "Strategy A",
            "allocated_risk_per_trade_dollars": 1000.0,
        }]),
    )
    result = export_strategy_library(
        strategy_b_trades,
        strategy_b_equity,
        tmp_path,
        source_input="wfa_b.csv",
        analysis_mode="wfa",
    )

    assert result["strategy_count"] == 2
    assert (tmp_path / "Strategy_A" / "trades.csv").exists()
    assert (tmp_path / "Strategy_A" / "equity_curve.csv").exists()
    assert (tmp_path / "Strategy_A" / "daily_pnl.csv").exists()
    assert (tmp_path / "Strategy_B" / "trades.csv").exists()
    assert (
        tmp_path / "_wfa_baselines" / "Strategy_A" / "trades.csv"
    ).exists()

    metadata = json.loads(
        (tmp_path / "Strategy_A" / "metadata.json").read_text()
    )
    assert metadata["strategy_name"] == "Strategy A"
    assert metadata["tested_symbols"] == ["/ES"]
    assert metadata["analysis_mode"] == "wfa"
    assert metadata["allocated_risk_per_trade_dollars"] == 1000.0

    combined = pd.read_csv(tmp_path / "combined_daily_pnl.csv")
    correlation = pd.read_csv(
        tmp_path / "pnl_correlation.csv",
        index_col=0,
    )
    overlap = pd.read_csv(
        tmp_path / "correlation_overlap_days.csv",
        index_col=0,
    )
    manifest = pd.read_csv(tmp_path / "strategy_manifest.csv")

    assert list(combined.columns) == ["date", "Strategy A", "Strategy B"]
    assert correlation.loc["Strategy A", "Strategy B"] == 1.0
    assert overlap.loc["Strategy A", "Strategy B"] == 25
    assert manifest["strategy_name"].tolist() == ["Strategy A", "Strategy B"]
