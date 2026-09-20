import pandas as pd

from analyze_strategy_performance import (
    export_strategy_library,
    live_degradation_monitor_html,
)
from live_degradation_monitor import build_live_degradation_monitor


STRATEGY = "0001_2RSI_Daily_LongAndShort_ES"


def create_wfa_baseline(library_dir):
    timestamps = pd.date_range("2019-01-15", periods=60, freq="42D")
    trade_r = ([0.80, -0.20, 0.50, -0.10, 0.40] * 12)[:60]
    trades = pd.DataFrame({
        "timestamp": timestamps,
        "Strategy_Name": [STRATEGY] * len(timestamps),
        "Symbol": ["/ES"] * len(timestamps),
        "net_pnl": [value * 1000 for value in trade_r],
        # AmiBroker's aggregated close rows can retain a zero custom R field;
        # the monitor must derive R from PNL and total initial risk instead.
        "WFA_Return_On_Initial_Risk": [0.0] * len(timestamps),
        "WFA_Total_Initial_Risk": [1000.0] * len(timestamps),
        "strategy_pnl": [value * 1000 for value in trade_r],
    })
    trades["strategy_cumulative_pnl"] = trades["strategy_pnl"].cumsum()
    equity = trades.copy()
    equity["CMPNL"] = equity["strategy_pnl"].cumsum()
    equity["strategy_equity"] = 100000 + equity["CMPNL"]
    export_strategy_library(
        trades,
        equity,
        library_dir,
        source_input="raw_wfa.csv",
        analysis_mode="wfa",
        capital_allocation=pd.DataFrame([{
            "Strategy_Name": STRATEGY,
            "allocated_risk_per_trade_dollars": 1000.0,
        }]),
    )


def test_live_monitor_waits_for_an_undeployed_strategy(tmp_path):
    create_wfa_baseline(tmp_path)
    unrelated_live_trade = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-06-01"),
        "Strategy_Name": "Another Strategy",
        "net_pnl": 100.0,
    }])

    monitor = build_live_degradation_monitor(
        unrelated_live_trade,
        tmp_path,
        simulations=200,
        random_seed=7,
        as_of_date="2026-06-30",
    )

    strategy = monitor["strategies"].iloc[0]
    assert strategy["Status"] == "Awaiting Live Trades"
    assert strategy["Live Trades"] == 0
    assert set(monitor["horizons"]["Status"]) == {"Awaiting Live Trades"}

    panel = live_degradation_monitor_html(monitor)
    assert "Live Degradation Monitor" in panel
    assert STRATEGY in panel
    assert "Awaiting Live Trades" in panel
    assert "5,000" not in panel
    assert "200 horizon-matched WFA simulations" in panel


def test_live_monitor_flags_a_severely_adverse_live_sequence(tmp_path):
    create_wfa_baseline(tmp_path)
    timestamps = pd.date_range("2026-05-01", periods=10, freq="3D")
    live_trades = pd.DataFrame({
        "timestamp": timestamps,
        "Strategy_Name": [STRATEGY] * 10,
        "net_pnl": [-1000.0] * 10,
    })

    monitor = build_live_degradation_monitor(
        live_trades,
        tmp_path,
        simulations=500,
        random_seed=11,
        as_of_date="2026-06-30",
    )
    horizons = monitor["horizons"].set_index("horizon_key")

    assert monitor["strategies"].iloc[0]["Status"] == "Critical"
    assert horizons.loc["3m", "Status"] == "Informational"
    assert horizons.loc["6m", "Status"] == "Critical"
    assert horizons.loc["12m", "Status"] == "Critical"
    assert horizons.loc["10t", "Status"] == "Critical"
    assert horizons.loc["10t", "Observed Total R"] == -10.0
    assert "WFA deployment allocated risk" in horizons.loc["10t", "R Basis"]
