import pandas as pd
import pytest

from analyze_strategy_performance import (
    aggregate_execution_packages,
    aggregate_realized_trades,
    apply_expired_option_settlement_checks,
    build_cash_balance_curve,
    build_account_kpi_cards,
    build_data_quality_warnings,
    build_futures_statement_settlements,
    build_open_position_audit,
    build_settlement_coverage,
    build_strategy_decision_board,
    build_strategy_top_summary,
    cash_balance_start_timestamp,
    calculate_strategy_position_sizing,
    calculate_buy_hold_benchmark_summary,
    calculate_cash_balance_summary,
    add_strategy_quality_columns,
    build_strategy_equity_curves,
    build_strategy_pnl_events,
    build_strategy_trade_ledgers,
    filter_realized_trades,
    benchmark_file_for_symbol,
    find_latest_account_statement,
    statement_file_names_from_trades,
    get_open_positions,
    parse_statement_position_summary,
    recalculate_account_columns,
    reconcile_open_futures_positions_to_statement,
)


def make_trade(
    exec_time,
    side,
    pos_effect,
    symbol,
    expiration,
    strike,
    option_type,
    net_pnl,
):
    return {
        "Strategy_Name": "Test Strategy",
        "Exec Time": exec_time,
        "timestamp": pd.to_datetime(exec_time, format="%m/%d/%y %H:%M:%S"),
        "Spread": "SINGLE",
        "Side": side,
        "Qty": -1 if side == "SELL" else 1,
        "Pos Effect": pos_effect,
        "Symbol": symbol,
        "Exp": expiration,
        "Strike": strike,
        "Type": option_type,
        "trade_pnl": net_pnl,
        "fees": 0,
        "net_pnl": net_pnl,
        "margin_requirement": 100,
        "return_on_margin": net_pnl / 100,
        "starting_equity": 1000,
        "log_return_on_margin": 0.0 if net_pnl <= -100 else 0.01,
    }


def make_future_trade(
    exec_time,
    side,
    pos_effect,
    symbol,
    qty,
    net_pnl,
):
    return {
        "Strategy_Name": "Discretionary",
        "Exec Time": exec_time,
        "timestamp": pd.to_datetime(exec_time, format="%m/%d/%y %H:%M:%S"),
        "Spread": "FUTURE",
        "Side": side,
        "Qty": -qty if side == "SELL" else qty,
        "Pos Effect": pos_effect,
        "Symbol": symbol,
        "Exp": "MAY 26",
        "Strike": None,
        "Type": "FUTURE",
        "trade_pnl": net_pnl,
        "fees": 0,
        "net_pnl": net_pnl,
        "margin_requirement": 100,
        "return_on_margin": net_pnl / 100,
        "starting_equity": 1000,
        "log_return_on_margin": 0.0,
    }


def test_find_latest_account_statement_uses_filename_date(tmp_path):
    older = tmp_path / "2026-05-31-AccountStatement.csv"
    newer = tmp_path / "2026-06-02-AccountStatement.csv"
    ignored = tmp_path / "trades.csv"
    older.write_text("")
    newer.write_text("")
    ignored.write_text("")

    assert find_latest_account_statement(tmp_path) == str(newer)


def test_find_latest_account_statement_returns_none_when_missing(tmp_path):
    (tmp_path / "trades.csv").write_text("")

    assert find_latest_account_statement(tmp_path) is None


def test_statement_file_names_from_trades_returns_statement_sources():
    cleaned_trades = pd.DataFrame({
        "statement_file": [
            "trades.csv",
            "2026-06-02-AccountStatement.csv",
            "/statements/2026-06-06-AccountStatement.csv",
            "",
            None,
        ]
    })

    assert statement_file_names_from_trades(cleaned_trades) == [
        "2026-06-02-AccountStatement.csv",
        "2026-06-06-AccountStatement.csv",
    ]


def test_cash_balance_start_timestamp_uses_analyzed_year_start():
    cleaned_trades = pd.DataFrame({
        "timestamp": [
            pd.Timestamp("2026-03-15 10:00:00"),
            pd.Timestamp("2026-06-05 15:00:00"),
        ]
    })

    assert cash_balance_start_timestamp(cleaned_trades) == pd.Timestamp("2026-01-01")


def test_find_latest_account_statement_prefers_trade_source_file(tmp_path):
    repo_data = tmp_path / "data"
    statement_dir = tmp_path / "statements"
    repo_data.mkdir()
    statement_dir.mkdir()
    stale = repo_data / "2026-06-02-AccountStatement.csv"
    current = statement_dir / "2026-06-06-AccountStatement.csv"
    stale.write_text("")
    current.write_text("")

    assert find_latest_account_statement(
        [repo_data, statement_dir],
        preferred_filenames=["2026-06-06-AccountStatement.csv"],
    ) == str(current)


def test_benchmark_file_for_symbol_finds_daily_csv(tmp_path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    benchmark_file = daily_dir / "SPY.csv"
    benchmark_file.write_text("timestamp,close\n2026-01-01,100\n")

    assert benchmark_file_for_symbol("SPY", tmp_path) == str(benchmark_file)


def test_calculate_buy_hold_benchmark_summary_matches_account_period(tmp_path):
    benchmark_file = tmp_path / "SPY.csv"
    benchmark_file.write_text(
        "\n".join([
            "timestamp,close",
            "2025-12-31T00:00:00Z,90",
            "2026-01-01T00:00:00Z,100",
            "2026-01-02T00:00:00Z,110",
            "2026-01-03T00:00:00Z,105",
        ])
    )
    account_curve = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-01-01 10:00:00"),
            "starting_equity": 1000,
            "ending_equity": 1000,
        },
        {
            "timestamp": pd.Timestamp("2026-01-03 10:00:00"),
            "starting_equity": 1000,
            "ending_equity": 1200,
        },
    ])

    summary = calculate_buy_hold_benchmark_summary(
        account_curve,
        benchmark_file,
        "SPY",
    )
    values = {
        row["metric"]: row["value"]
        for _, row in summary.iterrows()
    }

    assert values["benchmark_symbol"] == "SPY"
    assert values["benchmark_start_date"] == "2026-01-01"
    assert values["benchmark_end_date"] == "2026-01-03"
    assert values["benchmark_total_pnl"] == 50
    assert values["benchmark_total_return"] == 0.05
    assert values["account_vs_benchmark_ending_equity"] == 150


def test_cash_balance_curve_tracks_raw_and_sweep_adjusted_drawdown():
    cash_ledger = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-01-01 17:00:00"),
            "type": "BAL",
            "balance": 1000,
            "cash_flow": 0,
        },
        {
            "timestamp": pd.Timestamp("2026-01-02 09:00:00"),
            "type": "FSWP",
            "balance": 700,
            "cash_flow": -300,
        },
        {
            "timestamp": pd.Timestamp("2026-01-02 17:00:00"),
            "type": "BAL",
            "balance": 700,
            "cash_flow": 0,
        },
        {
            "timestamp": pd.Timestamp("2026-01-03 09:00:00"),
            "type": "TRD",
            "balance": 750,
            "cash_flow": 50,
        },
        {
            "timestamp": pd.Timestamp("2026-01-03 17:00:00"),
            "type": "BAL",
            "balance": 750,
            "cash_flow": 0,
        },
    ])

    curve = build_cash_balance_curve(cash_ledger)
    summary = calculate_cash_balance_summary(curve)
    values = {
        row["metric"]: row["value"]
        for _, row in summary.iterrows()
    }

    assert values["cash_balance_max_drawdown"] == pytest.approx(-0.3)
    assert values["cash_balance_ex_futures_sweeps_max_drawdown"] == pytest.approx(0)
    assert curve["cash_balance_ex_futures_sweeps"].tolist() == [
        1000,
        1000,
        1050,
    ]


def test_account_kpi_cards_use_realized_drawdown_not_cash_diagnostic():
    account_summary = pd.DataFrame([
        {"metric": "total_pnl", "value": 1000},
        {"metric": "cagr", "value": 0.10},
        {"metric": "max_drawdown", "value": -0.20},
        {"metric": "mar_ratio", "value": 0.50},
        {"metric": "profit_factor", "value": 1.20},
        {
            "metric": "cash_balance_ex_futures_sweeps_max_drawdown",
            "value": -1.17,
        },
    ])

    rendered = build_account_kpi_cards(account_summary)

    assert "Realized Max DD" in rendered
    assert "Realized MAR" in rendered
    assert "-20.00%" in rendered
    assert "Cash Max DD Ex Sweeps" not in rendered
    assert "-117.00%" not in rendered


def test_strategy_top_summary_orders_strategies_alphabetically():
    summary = pd.DataFrame([
        {
            "Strategy_Name": "Zulu",
            "strategy_status": "Healthy",
            "total_pnl": 1000,
            "total_return": 0.1,
            "cagr": 0.2,
            "max_drawdown": -0.05,
            "mar_ratio": 4,
            "profit_factor": 2,
            "trade_count": 10,
        },
        {
            "Strategy_Name": "Alpha",
            "strategy_status": "Pause",
            "total_pnl": -100,
            "total_return": -0.01,
            "cagr": -0.02,
            "max_drawdown": -0.10,
            "mar_ratio": -0.2,
            "profit_factor": 0.8,
            "trade_count": 5,
        },
    ])

    rendered = build_strategy_top_summary(
        summary,
        pd.DataFrame(),
    )

    assert rendered.index("Alpha") < rendered.index("Zulu")


def test_cash_balance_curve_resets_sweep_adjustment_at_start_timestamp():
    cash_ledger = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2025-12-31 09:00:00"),
            "type": "FSWP",
            "balance": 500,
            "cash_flow": -500,
        },
        {
            "timestamp": pd.Timestamp("2025-12-31 17:00:00"),
            "type": "BAL",
            "balance": 500,
            "cash_flow": 0,
        },
        {
            "timestamp": pd.Timestamp("2026-01-01 17:00:00"),
            "type": "BAL",
            "balance": 1000,
            "cash_flow": 0,
        },
        {
            "timestamp": pd.Timestamp("2026-01-02 09:00:00"),
            "type": "FSWP",
            "balance": 700,
            "cash_flow": -300,
        },
        {
            "timestamp": pd.Timestamp("2026-01-02 17:00:00"),
            "type": "BAL",
            "balance": 700,
            "cash_flow": 0,
        },
    ])

    curve = build_cash_balance_curve(
        cash_ledger,
        start_timestamp=pd.Timestamp("2026-01-01"),
    )

    assert curve["cash_balance_ex_futures_sweeps"].tolist() == [
        1000,
        1000,
    ]


def execution_duplicate_frame(statement_files):
    return pd.DataFrame([
        {
            "Strategy_Name": "Discretionary",
            "Exec Time": "1/2/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-02 09:30:00"),
            "Spread": "SINGLE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "16 JAN 26",
            "Strike": 6800,
            "Type": "PUT",
            "Price": 1.25,
            "Net Price": 1.25,
            "statement_file": statement_file,
        }
        for statement_file in statement_files
    ])


def test_data_quality_ignores_repeated_fills_within_one_statement_source():
    cleaned_trades = execution_duplicate_frame([
        "2026-06-06-AccountStatement.csv",
        "2026-06-06-AccountStatement.csv",
    ])
    realized_trades = pd.DataFrame([
        {
            "margin_requirement": 1000,
            "return_on_margin": 0.01,
        }
    ])

    warnings = build_data_quality_warnings(
        cleaned_trades,
        realized_trades,
        pd.DataFrame(),
    )

    assert "duplicate_execution_keys" not in set(warnings["check"])


def test_data_quality_flags_duplicate_executions_across_statement_sources():
    cleaned_trades = execution_duplicate_frame([
        "2026-06-02-AccountStatement.csv",
        "2026-06-06-AccountStatement.csv",
    ])
    realized_trades = pd.DataFrame([
        {
            "margin_requirement": 1000,
            "return_on_margin": 0.01,
        }
    ])

    warnings = build_data_quality_warnings(
        cleaned_trades,
        realized_trades,
        pd.DataFrame(),
    )
    duplicate_warning = warnings[
        warnings["check"] == "duplicate_execution_keys"
    ].iloc[0]

    assert duplicate_warning["count"] == 2


def test_build_open_position_audit_classifies_stale_and_fractional_positions():
    open_positions = pd.DataFrame([
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "3/3/26 06:17:40",
            "Symbol": "/MBTH26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "MAR 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "6/5/26 12:04:05",
            "Symbol": "/MBTM26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "JUN 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "4/16/26 11:10:57",
            "Symbol": "AAPL",
            "Spread": "STOCK",
            "Side": "BUY",
            "remaining_qty": 0.0038,
            "Exp": None,
            "expiration_close": None,
            "Strike": None,
            "Type": "STOCK",
        },
    ])

    audit = build_open_position_audit(
        open_positions,
        pd.Timestamp("2026-06-06"),
    )

    status_by_symbol = dict(
        zip(
            audit["Symbol"],
            audit["open_position_status"],
        )
    )
    assert status_by_symbol["/MBTH26"] == "stale_missing_close"
    assert status_by_symbol["/MBTM26"] == "current_position"
    assert status_by_symbol["AAPL"] == "fractional_residual"


def test_strategy_quality_and_decision_board_use_data_confidence():
    summary = pd.DataFrame([
        {
            "Strategy_Name": "Needs Data Strategy",
            "strategy_status": "Healthy",
            "trade_count": 10,
            "total_pnl": 1000,
            "total_return": 0.10,
            "cagr": 0.25,
            "max_drawdown": -0.05,
            "profit_factor": 1.4,
        },
        {
            "Strategy_Name": "Allocate Strategy",
            "strategy_status": "Healthy",
            "trade_count": 10,
            "total_pnl": 1000,
            "total_return": 0.10,
            "cagr": 0.25,
            "max_drawdown": -0.05,
            "profit_factor": 1.4,
        },
    ])
    settlement = pd.DataFrame([
        {
            "Strategy_Name": "Needs Data Strategy",
            "expired_option_count": 3,
            "settlement_checked_count": 2,
            "settlement_missing_count": 1,
            "settlement_adjusted_count": 0,
            "settlement_verified_otm_count": 2,
            "settlement_coverage_ratio": 2 / 3,
        },
        {
            "Strategy_Name": "Allocate Strategy",
            "expired_option_count": 3,
            "settlement_checked_count": 3,
            "settlement_missing_count": 0,
            "settlement_adjusted_count": 0,
            "settlement_verified_otm_count": 3,
            "settlement_coverage_ratio": 1.0,
        },
    ])
    risk = pd.DataFrame([
        {
            "Strategy_Name": "Needs Data Strategy",
            "safe_f": 0.05,
            "risk_per_trade_dollars": 5000,
        },
        {
            "Strategy_Name": "Allocate Strategy",
            "safe_f": 0.05,
            "risk_per_trade_dollars": 5000,
        },
    ])

    enhanced = add_strategy_quality_columns(
        summary,
        settlement,
        pd.DataFrame(),
    )
    decision = build_strategy_decision_board(
        enhanced,
        risk,
        pd.DataFrame(),
    )
    action_by_strategy = dict(
        zip(
            decision["Strategy_Name"],
            decision["suggested_action"],
        )
    )

    assert action_by_strategy["Needs Data Strategy"] == "Needs Data"
    assert action_by_strategy["Allocate Strategy"] == "Allocate"


def test_build_settlement_coverage_counts_missing_and_checked_rows():
    settlement_report = pd.DataFrame([
        {
            "Strategy_Name": "Test",
            "settlement_status": "missing_market_data",
        },
        {
            "Strategy_Name": "Test",
            "settlement_status": "verified_otm",
        },
        {
            "Strategy_Name": "Test",
            "settlement_status": "adjusted_itm",
        },
    ])

    coverage = build_settlement_coverage(settlement_report).iloc[0]

    assert coverage["expired_option_count"] == 3
    assert coverage["settlement_missing_count"] == 1
    assert coverage["settlement_checked_count"] == 2
    assert coverage["settlement_coverage_ratio"] == pytest.approx(2 / 3)


def test_aggregate_execution_packages_sums_split_futures_fills():
    df = pd.DataFrame([
        make_future_trade(
            "4/8/26 09:57:01",
            "SELL",
            "TO CLOSE",
            "/MCLK26",
            3,
            -100,
        ),
        make_future_trade(
            "4/8/26 09:57:01",
            "SELL",
            "TO CLOSE",
            "/MCLK26",
            1,
            -50,
        ),
        make_future_trade(
            "4/8/26 09:57:01",
            "SELL",
            "TO CLOSE",
            "/MCLK26",
            2,
            -75,
        ),
    ])

    packages = aggregate_execution_packages(df)

    assert len(packages) == 1
    assert packages.loc[0, "Qty"] == 6
    assert packages.loc[0, "net_pnl"] == -225


def test_build_futures_statement_settlements_uses_statement_cash_flow_delta():
    realized_trades = pd.DataFrame([
        {
            "Exec Time": "2/23/26 20:17:18",
            "timestamp": pd.Timestamp("2026-02-23 20:17:18"),
            "Strategy_Name": "Discretionary",
            "Symbol": "/MBTG26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "Qty": 2,
            "Pos Effect": "CLOSED",
            "Exp": "FEB 26",
            "Strike": None,
            "Type": "FUTURE",
            "open_exec_time": "2/5/26 15:56:27",
            "close_exec_time": "2/23/26 20:17:18",
            "realized_status": "CLOSED",
            "execution_leg_count": 1,
            "execution_symbols": "/MBTG26",
            "open_net_pnl": -6,
            "close_net_pnl": -94,
            "trade_pnl": -100,
            "fees": 0,
            "net_pnl": -100,
            "margin_requirement": 1000,
            "return_on_margin": -0.1,
            "log_return_on_margin": -0.105,
            "starting_equity": 1000,
        }
    ])
    open_positions = pd.DataFrame([
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "2/26/26 09:47:10",
            "Symbol": "/MBTG26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "FEB 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
            "margin_requirement": 500,
            "return_on_margin": None,
            "status": "OPEN_NOT_COUNTED",
        }
    ])
    statement_rows = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-02-26 17:00:00"),
            "symbol": "/MBTG26",
            "cash_flow": -250,
        }
    ])
    position_summary = pd.DataFrame([
        {
            "symbol": "/MBTG26",
            "open_pnl": 0,
            "day_pnl": 0,
            "mark_value": 0,
            "ytd_pnl": -250,
        }
    ])

    settlements = build_futures_statement_settlements(
        realized_trades,
        open_positions,
        statement_rows,
        position_summary,
    )

    assert len(settlements) == 1
    assert settlements.loc[0, "realized_status"] == "FUTURES_SETTLED"
    assert settlements.loc[0, "net_pnl"] == -150


def test_parse_statement_position_summary_extracts_futures_quantity(tmp_path):
    statement = tmp_path / "statement.csv"
    statement.write_text(
        "\n".join([
            "Symbol,Description,SPC,Exp,Qty,Trade Price,Mark,P/L Day",
            '/MBTM26,"Micro Bitcoin Futures,Jun-2026, (prev. /MBTM6)",1/0.1,JUN 26,+9,59953.8889,62115,"$2,684.00"',
            "",
            "Symbol,P/L Open,P/L %,P/L Day,Mark Value,P/L YTD,Description",
            '/MBTM26,"$1,945.00",+3.60%,"$2,684.00","$1,440.00","$3,320.50","Micro Bitcoin Futures,Jun-2026, (prev. /MBTM6)"',
        ])
    )

    position_summary = parse_statement_position_summary(statement)

    assert position_summary.loc[0, "symbol"] == "/MBTM26"
    assert position_summary.loc[0, "statement_qty"] == 9


def test_reconcile_open_futures_positions_to_statement_qty_trims_extra_lots():
    open_positions = pd.DataFrame([
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "6/5/26 12:04:05",
            "Symbol": "/MBTM26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "JUN 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "6/5/26 12:05:24",
            "Symbol": "/MBTM26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "JUN 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "6/5/26 14:44:04",
            "Symbol": "/MBTM26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "JUN 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
    ])
    position_summary = pd.DataFrame([
        {
            "symbol": "/MBTM26",
            "open_pnl": 100,
            "day_pnl": 100,
            "mark_value": 100,
            "statement_qty": 2,
        }
    ])

    reconciled = reconcile_open_futures_positions_to_statement(
        open_positions,
        position_summary,
    )

    assert reconciled["remaining_qty"].sum() == 2
    assert reconciled["open_exec_time"].tolist() == [
        "6/5/26 12:05:24",
        "6/5/26 14:44:04",
    ]
    assert reconciled["statement_position_status"].tolist() == [
        "trimmed_to_statement_qty",
        "trimmed_to_statement_qty",
    ]


def test_reconcile_open_futures_positions_removes_zero_mark_settled_symbol():
    open_positions = pd.DataFrame([
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "3/3/26 06:17:40",
            "Symbol": "/MBTH26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "MAR 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
        {
            "Strategy_Name": "Discretionary",
            "open_exec_time": "6/5/26 14:44:04",
            "Symbol": "/MBTM26",
            "Spread": "FUTURE",
            "Side": "BUY",
            "remaining_qty": 1,
            "Exp": "JUN 26",
            "expiration_close": None,
            "Strike": None,
            "Type": "FUTURE",
        },
    ])
    position_summary = pd.DataFrame([
        {
            "symbol": "/MBTH26",
            "open_pnl": 0,
            "day_pnl": 0,
            "mark_value": 0,
            "statement_qty": None,
        },
        {
            "symbol": "/MBTM26",
            "open_pnl": 100,
            "day_pnl": 100,
            "mark_value": 100,
            "statement_qty": 1,
        },
    ])

    reconciled = reconcile_open_futures_positions_to_statement(
        open_positions,
        position_summary,
    )

    assert reconciled["Symbol"].tolist() == ["/MBTM26"]
    assert reconciled.loc[0, "statement_position_status"] == (
        "matches_statement_qty"
    )


def test_filter_realized_trades_keeps_closed_trades():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO CLOSE",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            150,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert realized["Pos Effect"].tolist() == [
        "TO OPEN",
        "TO CLOSE",
    ]


def test_build_strategy_trade_ledgers_calculates_cumulative_pnl_by_strategy():
    realized = pd.DataFrame([
        {
            "Strategy_Name": "Strategy B",
            "Exec Time": "1/2/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-02 09:30:00"),
            "net_pnl": 50,
        },
        {
            "Strategy_Name": "Strategy A",
            "Exec Time": "1/1/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-01 09:30:00"),
            "net_pnl": 100,
        },
        {
            "Strategy_Name": "Strategy A",
            "Exec Time": "1/3/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-03 09:30:00"),
            "net_pnl": -25,
        },
    ])

    ledgers = build_strategy_trade_ledgers(realized)
    strategy_a = ledgers[
        ledgers["Strategy_Name"] == "Strategy A"
    ]
    strategy_b = ledgers[
        ledgers["Strategy_Name"] == "Strategy B"
    ]

    assert strategy_a["strategy_trade_number"].tolist() == [
        1,
        2,
    ]
    assert strategy_a["strategy_cumulative_pnl"].tolist() == [
        100,
        75,
    ]
    assert strategy_b["strategy_trade_number"].tolist() == [
        1,
    ]
    assert strategy_b["strategy_cumulative_pnl"].tolist() == [
        50,
    ]


def test_calculate_strategy_position_sizing_uses_risk_and_unit_margin():
    risk_summary = pd.DataFrame([
        {
            "Strategy_Name": "Test Strategy",
            "risk_per_trade_dollars": 10000,
        },
        {
            "Strategy_Name": "Discretionary",
            "risk_per_trade_dollars": 10000,
        },
    ])
    realized_trades = pd.DataFrame([
        {
            "Strategy_Name": "Test Strategy",
            "Qty": 2,
            "margin_requirement": 5000,
        },
        {
            "Strategy_Name": "Test Strategy",
            "Qty": 4,
            "margin_requirement": 12000,
        },
        {
            "Strategy_Name": "Discretionary",
            "Qty": 1,
            "margin_requirement": 100,
        },
    ])

    sized = calculate_strategy_position_sizing(
        risk_summary,
        realized_trades,
    )

    test_strategy = sized[
        sized["Strategy_Name"] == "Test Strategy"
    ].iloc[0]
    discretionary = sized[
        sized["Strategy_Name"] == "Discretionary"
    ].iloc[0]

    assert test_strategy["estimated_margin_per_contract_or_share"] == 2750
    assert test_strategy["contracts_or_shares_to_trade"] == 3
    assert discretionary["contracts_or_shares_to_trade"] == "N/A"


def test_filter_realized_trades_keeps_expired_long_options():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-20",
    )

    assert realized["Symbol"].tolist() == [
        "SPX",
    ]


def test_get_open_positions_drops_expired_long_options():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
        make_trade(
            "1/2/26 09:31:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "23 JAN 26",
            6800,
            "PUT",
            -150,
        ),
    ])

    open_positions = get_open_positions(
        df,
        as_of_date="2026-01-20",
    )

    assert open_positions["Exp"].tolist() == [
        "23 JAN 26",
    ]


def test_filter_realized_trades_keeps_expired_short_options():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "/X2AF26",
            6800,
            "PUT",
            125,
        ),
        make_trade(
            "1/2/26 09:31:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            100,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-13",
    )

    assert realized["Symbol"].tolist() == [
        "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
    ]


def test_recalculate_account_columns_after_filtering():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "2 JAN 26",
            6800,
            "PUT",
            100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            50,
        ),
    ])
    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-03",
    )

    recalculated = recalculate_account_columns(realized)

    assert recalculated["cumulative_pnl"].tolist() == [
        100,
    ]
    assert recalculated["ending_equity"].tolist() == [
        1100,
    ]


def test_aggregate_realized_trades_counts_open_and_close_as_one_trade():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO CLOSE",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            150,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert len(realized) == 1
    assert realized["realized_status"].tolist() == [
        "CLOSED",
    ]
    assert realized["net_pnl"].tolist() == [
        50,
    ]
    assert realized["cumulative_pnl"].tolist() == [
        50,
    ]
    assert realized["Exec Time"].tolist() == [
        "1/3/26 09:30:00",
    ]


def test_aggregate_realized_trades_counts_expired_short_option_as_one_trade():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "/X2AF26",
            6800,
            "PUT",
            125,
        ),
        make_trade(
            "1/2/26 09:31:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13",
    )

    assert len(realized) == 1
    assert realized["realized_status"].tolist() == [
        "EXPIRED",
    ]
    assert realized["net_pnl"].tolist() == [
        125,
    ]
    assert realized["Exec Time"].tolist() == [
        "1/12/26 16:00:00",
    ]


def test_aggregate_realized_trades_counts_expired_long_option_as_one_trade():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-20",
    )

    assert len(realized) == 1
    assert realized["realized_status"].tolist() == [
        "EXPIRED",
    ]
    assert realized["net_pnl"].tolist() == [
        -100,
    ]
    assert realized["Exec Time"].tolist() == [
        "1/16/26 16:00:00",
    ]


def test_expired_mes_put_is_adjusted_by_intrinsic_value(tmp_path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    (daily_dir / "ES.csv").write_text(
        "\n".join([
            "timestamp,date,symbol,frequency,close",
            "2026-06-05T00:00:00Z,2026-06-05,/ES,daily,7400.5",
        ])
    )
    trade = make_trade(
        "6/1/26 09:30:00",
        "SELL",
        "TO OPEN",
        "/MESM26 1/5 5 JUN 26 (Friday) (Wk1)",
        "5 JUN 26",
        7405,
        "PUT",
        105,
    )
    trade["Qty"] = -3
    df = pd.DataFrame([trade])
    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-06-06",
    )

    adjusted, report = apply_expired_option_settlement_checks(
        realized,
        tmp_path,
    )

    assert adjusted.loc[0, "net_pnl"] == 37.5
    assert adjusted.loc[0, "expired_option_settlement_adjustment"] == -67.5
    assert adjusted.loc[0, "expired_option_settlement_status"] == "adjusted_itm"
    assert report.loc[0, "settlement_status"] == "adjusted_itm"
    assert report.loc[0, "intrinsic_value"] == 67.5


def test_expired_xsp_option_without_market_data_is_flagged_not_adjusted(tmp_path):
    trade = make_trade(
        "1/2/26 09:30:00",
        "SELL",
        "TO OPEN",
        "XSP",
        "16 JAN 26",
        700,
        "PUT",
        100,
    )
    df = pd.DataFrame([trade])
    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-20",
    )

    adjusted, report = apply_expired_option_settlement_checks(
        realized,
        tmp_path,
    )

    assert adjusted.loc[0, "net_pnl"] == 100
    assert adjusted.loc[0, "expired_option_settlement_adjustment"] == 0
    assert adjusted.loc[0, "expired_option_settlement_status"] == (
        "missing_market_data"
    )
    assert report.loc[0, "settlement_status"] == "missing_market_data"


def test_aggregate_realized_trades_does_not_expire_after_today_by_default():
    df = pd.DataFrame([
        make_trade(
            "1/14/26 15:06:22",
            "SELL",
            "TO OPEN",
            "/ESH26 1/50 15 JAN 26 (Thursday) (Wk3)",
            "/E3DF26",
            6955,
            "CALL",
            2047.5,
        ),
    ])

    realized = aggregate_realized_trades(df)

    assert realized.empty


def test_aggregate_realized_trades_expires_prior_rolling_short_options():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/7/26 09:32:23",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 14 JAN 26 (Wednesday) (Wk3)",
                "/X2CF26",
                6815,
                "PUT",
                127,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
        {
            **make_trade(
                "1/8/26 09:31:05",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 15 JAN 26 (Thursday) (Wk3)",
                "/X3DF26",
                6770,
                "PUT",
                101.25,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
        {
            **make_trade(
                "1/14/26 09:32:25",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 21 JAN 26 (Wednesday) (Wk4)",
                "/X3CF26",
                6765,
                "PUT",
                149,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
    ])

    realized = aggregate_realized_trades(df)

    assert realized["Symbol"].tolist() == [
        "/MESH26 1/5 14 JAN 26 (Wednesday) (Wk3)",
        "/MESH26 1/5 15 JAN 26 (Thursday) (Wk3)",
    ]
    assert realized["realized_status"].tolist() == [
        "EXPIRED",
        "EXPIRED",
    ]


def test_aggregate_realized_trades_counts_straddle_legs_as_one_trade():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/5/26 15:00:00",
                "SELL",
                "TO OPEN",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "CALL",
                1447.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "1/5/26 15:00:00",
                "SELL",
                "TO OPEN",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "PUT",
                -2.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
        {
            **make_trade(
                "1/6/26 09:30:00",
                "BUY",
                "TO CLOSE",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "CALL",
                -1127.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "1/6/26 09:30:00",
                "BUY",
                "TO CLOSE",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "PUT",
                -2.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert len(realized) == 1
    assert realized["execution_leg_count"].tolist() == [
        2,
    ]
    assert realized["net_pnl"].tolist() == [
        315.0,
    ]


def test_mislabeled_to_open_straddle_offsets_existing_opposite_lot():
    df = pd.DataFrame([
        {
            **make_trade(
                "6/4/26 15:04:07",
                "SELL",
                "TO OPEN",
                "/ESM26 1/50 5 JUN 26 (Wk1)",
                "/EW1M26",
                7600,
                "CALL",
                1895.26,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "6/4/26 15:04:07",
                "SELL",
                "TO OPEN",
                "/ESM26 1/50 5 JUN 26 (Wk1)",
                "/EW1M26",
                7600,
                "PUT",
                0.0,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
        {
            **make_trade(
                "6/5/26 09:33:00",
                "BUY",
                "TO OPEN",
                "/ESM26 1/50 5 JUN 26 (Wk1)",
                "/EW1M26",
                7600,
                "CALL",
                -2804.74,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "6/5/26 09:33:00",
                "BUY",
                "TO OPEN",
                "/ESM26 1/50 5 JUN 26 (Wk1)",
                "/EW1M26",
                7600,
                "PUT",
                0.0,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-06-07",
    )
    open_positions = get_open_positions(
        df,
        as_of_date="2026-06-07",
    )

    assert len(realized) == 1
    assert realized.loc[0, "realized_status"] == "CLOSED"
    assert realized.loc[0, "open_net_pnl"] == pytest.approx(1895.26)
    assert realized.loc[0, "close_net_pnl"] == pytest.approx(-2804.74)
    assert realized.loc[0, "net_pnl"] == pytest.approx(-909.48)
    assert open_positions.empty


def test_aggregate_realized_trades_counts_vertical_legs_as_one_trade():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/7/26 11:45:04",
                "SELL",
                "TO OPEN",
                "SPX",
                "7 JAN 26",
                6945,
                "PUT",
                173.75,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
        {
            **make_trade(
                "1/7/26 11:45:04",
                "BUY",
                "TO OPEN",
                "SPX",
                "7 JAN 26",
                6925,
                "PUT",
                -1.25,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
        {
            **make_trade(
                "1/7/26 14:10:39",
                "BUY",
                "TO CLOSE",
                "SPX",
                "7 JAN 26",
                6945,
                "PUT",
                -1845,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
        {
            **make_trade(
                "1/7/26 14:10:39",
                "SELL",
                "TO CLOSE",
                "SPX",
                "7 JAN 26",
                6925,
                "PUT",
                -5,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-07 15:00:00",
    )

    assert len(realized) == 1
    assert realized["execution_leg_count"].tolist() == [
        2,
    ]
    assert realized["net_pnl"].tolist() == [
        -1677.5,
    ]


def test_aggregate_execution_packages_uses_explicit_spread_leg_as_primary():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/6/26 12:47:27",
                "BUY",
                "TO OPEN",
                "SPX",
                "6 JAN 26",
                6900,
                "PUT",
                -5,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
        {
            **make_trade(
                "1/6/26 12:47:27",
                "SELL",
                "TO OPEN",
                "SPX",
                "6 JAN 26",
                6920,
                "PUT",
                435,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
    ])

    packages = aggregate_execution_packages(df)

    assert packages["Side"].tolist() == [
        "SELL",
    ]
    assert packages["net_pnl"].tolist() == [
        430,
    ]


def test_strategy_pnl_events_split_short_option_open_and_close_pnl():
    df = pd.DataFrame([
        make_trade(
            "1/13/26 09:32:02",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            116,
        ),
        make_trade(
            "1/13/26 10:29:02",
            "BUY",
            "TO CLOSE",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            -253,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13 11:00:00",
    )
    events = build_strategy_pnl_events(realized)

    assert events["pnl_event_type"].tolist() == [
        "OPEN",
        "CLOSE",
    ]
    assert events["Exec Time"].tolist() == [
        "1/13/26 09:32:02",
        "1/13/26 10:29:02",
    ]
    assert events["net_pnl"].tolist() == [
        116,
        -253,
    ]


def test_strategy_equity_curve_uses_short_option_cashflow_dates():
    df = pd.DataFrame([
        make_trade(
            "1/13/26 09:32:02",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            116,
        ),
        make_trade(
            "1/13/26 10:29:02",
            "BUY",
            "TO CLOSE",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            -253,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13 11:00:00",
    )
    events = build_strategy_pnl_events(realized)
    curve = build_strategy_equity_curves(events)

    assert curve["strategy_pnl"].tolist() == [
        116,
        -253,
    ]
    assert curve["CMPNL"].tolist() == [
        116,
        -137,
    ]
    assert curve["strategy_equity"].tolist() == [
        1116,
        863,
    ]
