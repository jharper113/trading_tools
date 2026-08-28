import pandas as pd
import pytest

from analyze_strategy_performance import (
    attach_wfa_deployment_recommendation,
    apply_wfa_validation_decision,
    build_wfa_validation,
    calculate_strategy_position_sizing,
    save_wfa_simulation_boxplots,
    wfa_validation_panel_html,
)


def failing_wfa_executions():
    rows = []
    pnl_by_segment = {
        "2019": [1000, -900],
        "2020": [500, -1200],
        "2021": [800, -700],
        "2022": [400, -1000],
    }

    for year, values in pnl_by_segment.items():
        for index, pnl in enumerate(values, start=1):
            rows.append({
                "WFA_01_Segment": year,
                "WFA_Execution_Type": "EXIT",
                "Pos Effect": "TO CLOSE",
                "net_pnl": pnl,
                "starting_equity": 100000,
                "timestamp": pd.Timestamp(
                    year=int(year),
                    month=index,
                    day=15,
                ),
                "Qty": 1.5,
                "Symbol": "/ES",
                "return_on_margin": pnl / 500,
            })

    return pd.DataFrame(rows)


def test_wfa_validation_builds_failing_pass_gate_panel():
    validation = build_wfa_validation(
        failing_wfa_executions(),
        simulations=200,
        round_trip_cost=5,
        cost_stress_multiple=2,
        random_seed=7,
        as_of_date="2026-06-19",
    )
    gates = {gate["key"]: gate for gate in validation["gates"]}

    assert validation["verdict"] == "FAIL"
    assert len(validation["segments"]) == 4
    assert gates["aggregate_profit_factor"]["status"] == "bad"
    assert gates["trade_coverage"]["status"] == "bad"
    assert gates["position_sizing_integrity"]["status"] == "bad"
    assert gates["cross_market_robustness"]["status"] == "na"
    assert gates["cross_market_robustness"]["blocking"] is False
    assert gates["recent_untouched_data"]["status"] == "bad"

    html = wfa_validation_panel_html(validation)
    assert "Walk-Forward Pass Gates" in html
    assert 'class="wfa-verdict fail">FAIL' in html
    assert "OOS Segment Detail" in html
    assert "Uses an additional $10.00 cost" in html


def test_wfa_panel_is_absent_without_opt_in_validation():
    assert wfa_validation_panel_html(None) == ""


def test_wfa_mixed_segments_produce_pilot_verdict_with_sparse_coverage():
    rows = []
    positive_segments = {"2019", "2021", "2024", "2025"}

    for year in range(2019, 2026):
        segment = str(year)
        pnl_values = (
            [1000] * 6 + [-100] * 2
            if segment in positive_segments
            else [1000] * 3 + [-800] * 5
        )
        for index, pnl in enumerate(pnl_values, start=1):
            rows.append({
                "WFA_01_Segment": segment,
                "WFA_Execution_Type": "EXIT",
                "Pos Effect": "TO CLOSE",
                "net_pnl": pnl,
                "starting_equity": 100000,
                "timestamp": pd.Timestamp(year=year, month=index, day=15),
                "Qty": 1,
                "Symbol": "/ES",
                "return_on_margin": pnl / 5000,
                "WFA_Risk_Per_Contract": 5000,
                "WFA_13_DollarsPerPt": 50,
                "WFA_14_StopDist": 100,
            })

    validation = build_wfa_validation(
        pd.DataFrame(rows),
        simulations=500,
        random_seed=7,
    )
    gates = {gate["key"]: gate for gate in validation["gates"]}

    assert validation["verdict"] == "PILOT"
    assert gates["positive_segments"]["status"] == "review"
    assert gates["trade_coverage"]["status"] == "good"
    assert gates["position_sizing_integrity"]["status"] == "good"
    assert gates["recent_untouched_data"]["status"] == "good"
    assert gates["bandy_safe_f"]["status"] == "good"
    assert "2025: 8 trades" in gates["recent_untouched_data"]["display_value"]
    assert len(validation["simulation"]["results"]) == 500
    assert "IQR" in validation["simulation"]["summary"].columns

    html = wfa_validation_panel_html(validation)
    assert "WFA Simulation And Position Sizing" in html
    assert "Simulation IQR Values" in html
    assert "Optimized risk per trade" in html


def test_wfa_deployment_panel_identifies_strategy_market_and_live_contract():
    validation = {
        "strategy_name": "0001_2RSI_Daily_LongAndShort_ES",
        "tested_symbols": ["/ES"],
        "verdict": "PILOT",
        "gates": [],
        "segments": pd.DataFrame([{
            "Segment": "2025",
            "Trades": 8,
            "Net PNL": 1000.0,
            "Profit Factor": 1.5,
            "Win Rate": 0.625,
            "Positive": True,
        }]),
        "simulation": None,
    }
    capital_allocation = pd.DataFrame([{
        "Strategy_Name": "0001_2RSI_Daily_LongAndShort_ES",
        "suggested_action": "Pilot",
        "recommended_instrument": "/MES",
        "contracts_or_shares_to_trade": 4,
        "wfa_risk_per_contract_dollars": 1098.654,
        "risk_budget_dollars": 4473.88,
        "allocated_risk_per_trade_dollars": 4394.616,
    }])

    updated = attach_wfa_deployment_recommendation(
        validation,
        capital_allocation,
    )
    panel = wfa_validation_panel_html(updated)

    assert updated["deployment"]["recommended_instrument"] == "/MES"
    assert updated["deployment"]["contracts"] == 4
    assert "0001_2RSI_Daily_LongAndShort_ES" in panel
    assert "Tested On" in panel
    assert "/ES" in panel
    assert "Live Instrument" in panel
    assert "/MES" in panel
    assert "4 contracts" in panel
    assert "$1,098.65" in panel


def test_wfa_position_sizing_uses_micro_contract_and_pilot_cap():
    risk_summary = pd.DataFrame([{
        "Strategy_Name": "WFA1_OOS",
        "safe_f": 0.06,
        "risk_per_trade_dollars": 6000,
        "bankroll": 100000,
    }])
    trades = pd.DataFrame([
        {
            "Strategy_Name": "WFA1_OOS",
            "Symbol": "/ES",
            "timestamp": pd.Timestamp("2025-01-15"),
            "WFA_Risk_Per_Contract": 12000,
        },
        {
            "Strategy_Name": "WFA1_OOS",
            "Symbol": "/ES",
            "timestamp": pd.Timestamp("2025-12-31"),
            "WFA_Risk_Per_Contract": 10986.54,
        },
    ])

    sized = calculate_strategy_position_sizing(
        risk_summary,
        trades,
        wfa_pilot_risk_pct=1.5,
    ).iloc[0]

    assert sized["recommended_instrument"] == "/MES"
    assert sized["risk_budget_dollars"] == 1500
    assert sized["estimated_max_risk_per_contract_or_share"] == pytest.approx(
        1098.654
    )
    assert sized["contracts_or_shares_to_trade"] == 1
    assert sized["allocated_risk_per_trade_dollars"] == pytest.approx(1098.654)


def test_wfa_position_sizing_uses_drawdown_constrained_optimizer():
    risk_summary = pd.DataFrame([{
        "Strategy_Name": "0001_2RSI_Daily_LongAndShort_ES",
        "safe_f": 0.06,
        "risk_per_trade_dollars": 6000,
        "CAR25": 0.03,
        "bankroll": 100000,
    }])
    trades = pd.DataFrame([{
        "Strategy_Name": "0001_2RSI_Daily_LongAndShort_ES",
        "Symbol": "/ES",
        "timestamp": pd.Timestamp("2025-12-31"),
        "WFA_Risk_Per_Contract": 10986.54,
    }])
    optimization = {
        "risk_fraction": 0.0447,
        "drawdown_breach_probability": 0.05,
        "drawdown_limit": -0.20,
        "cagr_objective": 0.027,
    }

    sized = calculate_strategy_position_sizing(
        risk_summary,
        trades,
        wfa_risk_optimization=optimization,
    ).iloc[0]

    assert sized["sizing_basis"] == "wfa_bandy_safe_f_initial_stop_risk"
    assert sized["recommended_instrument"] == "/MES"
    assert sized["safe_f"] == pytest.approx(0.0447)
    assert sized["risk_budget_dollars"] == pytest.approx(4470)
    assert sized["contracts_or_shares_to_trade"] == 4
    assert sized["allocated_risk_per_trade_dollars"] == pytest.approx(
        4 * 1098.654
    )
    assert sized["wfa_drawdown_breach_probability"] == pytest.approx(0.05)


def test_wfa_simulation_boxplots_are_written(tmp_path, monkeypatch):
    rows = []
    for year in range(2019, 2026):
        for index, pnl in enumerate([1000, -300, 700, -200, 500], start=1):
            rows.append({
                "WFA_01_Segment": str(year),
                "WFA_Execution_Type": "EXIT",
                "Pos Effect": "TO CLOSE",
                "net_pnl": pnl,
                "starting_equity": 100000,
                "timestamp": pd.Timestamp(year=year, month=index, day=15),
                "Qty": 1,
                "Symbol": "/ES",
                "Strategy_Name": "0001_2RSI_Daily_LongAndShort_ES",
                "WFA_Risk_Per_Contract": 5000,
                "WFA_Total_Initial_Risk": 5000,
                "WFA_13_DollarsPerPt": 50,
                "WFA_14_StopDist": 100,
            })

    validation = build_wfa_validation(
        pd.DataFrame(rows),
        simulations=150,
        random_seed=9,
    )
    monkeypatch.setattr(
        "analyze_strategy_performance.CHART_DIR",
        str(tmp_path),
    )
    files = save_wfa_simulation_boxplots(validation)

    assert len(files) == 5
    assert all((tmp_path / file.split("/")[-1]).exists() for file in files)
    assert validation["simulation"]["boxplot_files"] == files


def test_wfa_pilot_verdict_overrides_generic_allocate_action():
    decision = pd.DataFrame([{
        "Strategy_Name": "WFA1_OOS",
        "suggested_action": "Allocate",
        "decision_reason": "Generic metrics passed.",
    }])
    trades = pd.DataFrame([{
        "Strategy_Name": "WFA1_OOS",
        "WFA_01_Segment": "2025",
    }])

    updated = apply_wfa_validation_decision(
        decision,
        trades,
        {"verdict": "PILOT"},
    ).iloc[0]

    assert updated["suggested_action"] == "Pilot"
    assert "verdict PILOT" in updated["decision_reason"]
