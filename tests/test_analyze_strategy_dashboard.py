import pandas as pd

import analyze_strategy_performance as asp


def test_statement_end_of_day_uses_statement_filename_date():
    as_of_date = asp.statement_end_of_day(
        {
            "statement_file": (
                "/statements/2026-01-15-AccountStatement.csv"
            )
        }
    )

    assert as_of_date == pd.Timestamp("2026-01-15 23:59:59")


def test_ytd_statement_report_uses_all_ytd_trade_history_fees(tmp_path):
    reports = asp.build_ytd_statement_reports(
        {
            "statement_year": 2026,
            "statement_closed_net_ytd_pnl": 85.0,
            "statement_total_ytd_commissions_and_fees": 15.0,
        },
        pd.DataFrame(),
        pd.DataFrame(
            [
                {"Exec Time": "1/2/26 10:00:00", "fees": 5.0},
                {"Exec Time": "1/3/26 10:00:00", "fees": 10.0},
            ]
        ),
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-02 10:30:00",
                    "Strategy_Name": "Test Strategy",
                    "Symbol": "SPX",
                    "trade_pnl": 100.0,
                    "fees": 5.0,
                    "net_pnl": 95.0,
                }
            ]
        ),
        pd.DataFrame(),
        tmp_path / "master_cleaned_tos_data.csv",
    )

    row = reports["summary"].iloc[0]
    assert row["script_realized_closed_gross_pnl"] == 100.0
    assert row["script_realized_closed_fees"] == 15.0
    assert row["script_realized_closed_net_pnl"] == 85.0


def test_ytd_statement_report_applies_trade_history_bridge(tmp_path):
    reports = asp.build_ytd_statement_reports(
        {
            "statement_year": 2026,
            "statement_closed_gross_ytd_pnl": 97.50,
            "statement_closed_net_ytd_pnl": 89.42,
            "statement_total_ytd_commissions_and_fees": 8.08,
        },
        pd.DataFrame([
            {
                "Symbol": "/MESH26",
                "statement_open_pnl": 157.0,
                "statement_ytd_pnl": 97.5,
                "statement_closed_gross_pnl": -59.5,
            }
        ]),
        pd.DataFrame([
            {
                "Exec Time": "1/2/26 14:17:49",
                "Symbol": "/MESH26 1/5 9 JAN 26 (Wk2)",
                "Pos Effect": "TO OPEN",
                "trade_pnl": 127.0,
                "fees": 8.0,
                "net_pnl": 119.0,
            }
        ]),
        pd.DataFrame([
            {
                "timestamp": "2026-01-02 14:17:49",
                "Strategy_Name": "Discretionary",
                "Symbol": "/MESH26 1/5 9 JAN 26 (Wk2)",
                "trade_pnl": 127.0,
                "fees": 8.0,
                "net_pnl": 119.0,
            }
        ]),
        pd.DataFrame(),
        tmp_path / "master_cleaned_tos_data.csv",
    )

    row = reports["summary"].iloc[0]
    assert row["realized_trade_closed_net_pnl"] == 119.0
    assert row["trade_history_closed_net_ytd_pnl"] == 119.0
    assert round(row["open_trade_exclusion"], 2) == -127.0
    assert round(row["ytd_bridge_adjustment"], 2) == 97.42
    assert round(row["trade_history_adjusted_closed_net_ytd_pnl"], 2) == 89.42
    assert round(row["closed_net_delta_statement_minus_trade_history"], 2) == 0.0
    assert round(row["closed_net_delta_statement_minus_script"], 2) == 0.0


def test_realized_trades_keep_source_row_ids_for_strategy_edits():
    raw_trades = pd.DataFrame(
        [
            {
                "Strategy_Name": "Old Strategy",
                "Exec Time": "1/2/26 10:00:00",
                "timestamp": pd.Timestamp("2026-01-02 10:00:00"),
                "Spread": "STOCK",
                "Side": "BUY",
                "Qty": 10,
                "Pos Effect": "TO OPEN",
                "Symbol": "AAPL",
                "Exp": "",
                "Strike": "",
                "Type": "",
                "trade_pnl": 0.0,
                "fees": 0.0,
                "net_pnl": 0.0,
                "margin_requirement": 0.0,
                "return_on_margin": None,
                "log_return_on_margin": None,
                "starting_equity": 100000.0,
            },
            {
                "Strategy_Name": "Old Strategy",
                "Exec Time": "1/2/26 11:00:00",
                "timestamp": pd.Timestamp("2026-01-02 11:00:00"),
                "Spread": "STOCK",
                "Side": "SELL",
                "Qty": -10,
                "Pos Effect": "TO CLOSE",
                "Symbol": "AAPL",
                "Exp": "",
                "Strike": "",
                "Type": "",
                "trade_pnl": 50.0,
                "fees": 1.0,
                "net_pnl": 49.0,
                "margin_requirement": 0.0,
                "return_on_margin": None,
                "log_return_on_margin": None,
                "starting_equity": 100000.0,
            },
        ],
        index=[7, 8],
    )

    realized = asp.aggregate_realized_trades(raw_trades)

    assert realized.iloc[0]["source_row_ids"] == [7, 8]


def test_apply_strategy_name_updates_to_master_updates_selected_source_rows(tmp_path):
    input_file = tmp_path / "master_cleaned_tos_data.csv"
    pd.DataFrame(
        [
            {"Strategy_Name": "Old Strategy", "Symbol": "AAPL"},
            {"Strategy_Name": "Keep Strategy", "Symbol": "MSFT"},
            {"Strategy_Name": "Old Strategy", "Symbol": "SPX"},
        ]
    ).to_csv(input_file, index=False)

    result = asp.apply_strategy_name_updates_to_master(
        input_file,
        [
            {
                "source_row_ids": [0, 2],
                "strategy_name": "Discretionary",
            }
        ],
    )

    updated = pd.read_csv(input_file)
    assert result["saved_rows"] == 2
    assert updated["Strategy_Name"].tolist() == [
        "Discretionary",
        "Keep Strategy",
        "Discretionary",
    ]


def test_strategy_dashboard_uses_compact_summary_layout(tmp_path, monkeypatch):
    output_dir = tmp_path / "strategy_performance"
    chart_dir = output_dir / "charts"
    chart_dir.mkdir(parents=True)
    monkeypatch.setattr(asp, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(
        asp,
        "DASHBOARD_FILE",
        str(output_dir / "strategy_dashboard.html"),
    )

    account_summary = pd.DataFrame(
        [
            {"metric": "total_pnl", "value": 2233.27},
            {"metric": "max_drawdown", "value": -0.02},
        ]
    )
    ytd_reports = {
        "summary": pd.DataFrame(
            [
                {
                    "statement_closed_net_ytd_pnl": 419.89,
                    "script_realized_closed_net_pnl": 2233.27,
                    "closed_net_delta_statement_minus_script": -1813.38,
                }
            ]
        ),
        "validation_summary": pd.DataFrame(),
        "validation_issues": pd.DataFrame(),
        "open_pnl": pd.DataFrame(),
        "fees": pd.DataFrame(),
        "closed_pnl": pd.DataFrame(),
        "trade_review": pd.DataFrame(),
        "strategy_impact": pd.DataFrame(),
    }
    summary = pd.DataFrame(
        [
            {
                "Strategy_Name": "Discretionary",
                "strategy_status": "Watch",
                "total_pnl": 100.0,
                "total_return": 0.01,
                "cagr": 0.12,
                "max_drawdown": -0.02,
                "mar_ratio": 6.0,
                "profit_factor": 1.5,
                "trade_count": 3,
            }
        ]
    )
    strategy_decision = pd.DataFrame(
        [
            {
                "Strategy_Name": "Discretionary",
                "suggested_action": "Watch",
                "strategy_status": "Watch",
                "data_confidence": "High",
                "total_pnl": 100.0,
                "total_return": 0.01,
                "max_drawdown": -0.02,
                "profit_factor": 1.5,
                "safe_f": 0.2,
                "risk_per_trade_dollars": 20000.0,
                "contracts_or_shares_to_trade": 2,
                "decision_reason": "Small sample.",
            }
        ]
    )
    capital_allocation = pd.DataFrame(
        [
            {
                "Strategy_Name": "Discretionary",
                "suggested_action": "Watch",
                "data_confidence": "High",
                "safe_f": 0.2,
                "risk_per_trade_dollars": 20000.0,
                "contracts_or_shares_to_trade": 2,
                "allocation_note": "Keep small.",
            }
        ]
    )
    correlation = pd.DataFrame(
        [[1.0]],
        index=["Discretionary"],
        columns=["Discretionary"],
    )
    chart_files = [
        str(chart_dir / "account_equity_curve.png"),
        str(chart_dir / "strategy_equity_curves.png"),
        str(chart_dir / "recent_trade_pnl_by_strategy.png"),
        str(chart_dir / "daily_pnl_correlation_heatmap.png"),
        str(chart_dir / "drawdown_correlation_heatmap.png"),
        str(chart_dir / "profit_factor.png"),
    ]
    realized_trades = pd.DataFrame(
        [
            {
                "timestamp": "2026-01-02 11:00:00",
                "Strategy_Name": "Discretionary",
                "Symbol": "/ESH26",
                "Spread": "FUTURE",
                "Side": "SELL",
                "Qty": 1,
                "realized_status": "CLOSED",
                "open_exec_time": "1/2/26 10:00:00",
                "close_exec_time": "1/2/26 11:00:00",
                "trade_pnl": 125.0,
                "fees": 6.4,
                "net_pnl": 118.6,
                "source_row_ids": [4, 5],
            }
        ]
    )

    asp.save_dashboard(
        account_summary,
        pd.DataFrame(),
        {},
        summary,
        strategy_decision,
        capital_allocation,
        pd.DataFrame(columns=["Strategy_Name"]),
        pd.DataFrame(columns=["Strategy_Name"]),
        pd.DataFrame(columns=["check"]),
        correlation,
        correlation,
        pd.DataFrame(columns=["drawdown_overlap_ratio"]),
        chart_files,
        [
            str(chart_dir / "risk_simulation_cagr_boxplot.png"),
            str(chart_dir / "risk_simulation_max_drawdown_boxplot.png"),
            str(chart_dir / "risk_simulation_profit_factor_boxplot.png"),
        ],
        ytd_reports,
        realized_trades=realized_trades,
        server_enabled=True,
    )

    html = (output_dir / "strategy_dashboard.html").read_text()
    support_start = html.index("<summary>Supporting Details</summary>")
    strategy_summary_start = html.index("<h2>Strategy Summary</h2>")
    capital_start = html.index("<h2>Capital Allocation</h2>")
    strategy_trades_start = html.index("<h2>Strategy Trades</h2>")
    capital_section = html[capital_start:support_start]

    assert "Net YTD PNL" in html
    assert "Trade History Net PNL" in html
    assert "Discrepancy" in html
    assert "Realized Trade PNL" in html
    assert "Annualized Real Return" in html
    assert "Realized Max DD" in html
    assert "Realized MAR" in html
    assert "Realized Profit Factor" in html
    assert "$419.89" in html
    assert "$2,233.27" in html
    assert "$-1,813.38" in html
    assert "Size" in capital_section
    assert "Risk $" in capital_section
    assert "Action" not in capital_section
    assert "<h2>Strategy Trades</h2>" in html
    assert strategy_summary_start < capital_start < strategy_trades_start
    assert 'id="strategy-trade-select"' in html
    assert 'id="save-strategy-edits"' in html
    assert "/api/strategy-updates" in html
    assert '"serverEnabled": true' in html
    assert '"strategy": "Discretionary"' in html
    assert '"sourceRowIds": [4, 5]' in html
    assert '"entry": "1/2/26 10:00:00"' in html
    assert '"exit": "1/2/26 11:00:00"' in html
    assert '"netPnl": 118.6' in html
    assert html.index("Account Equity Curve") < support_start
    assert html.index("Daily PNL Correlation") < support_start
    assert html.index("Simulation CAGR") < support_start
    assert html.index("Simulation Max Drawdown") < support_start
    assert html.index("Decision Board") > support_start
    assert html.index("<h2>Profit Factor</h2>") > support_start
    assert html.index("Risk Simulation Profit Factor Boxplot") > support_start
