import pandas as pd
import pytest

from extract_trade_history_v2 import (
    add_candidate_review_columns,
    adjustment_review_rows,
    account_trade_history_lines,
    build_ytd_bridge_adjustments,
    dashboard_html,
    dashboard_statement_totals,
    parse_args,
    reconciliation_dashboard_rows,
    reset_output_files,
    run_v2,
    save_strategy_updates,
    v2_output_paths,
    ytd_reconciliation_rows,
)


def test_dashboard_statement_totals_drop_statement_prefix():
    statement_summary = {
        "statement_file": "2026-06-13-AccountStatement.csv",
        "statement_year": 2026,
        "statement_gross_ytd_pnl": 23334.89,
        "statement_open_position_pnl": 1868.54,
        "statement_closed_net_ytd_pnl": 16741.33,
    }
    ytd_reconciliation = pd.DataFrame([
        {
            "metric": "closed_net_ytd_pnl",
            "ytd_value": 16741.33,
            "trade_history_value": 16740.00,
            "open_trade_exclusion": -10.0,
            "ytd_bridge_adjustment": 11.33,
            "bridge_adjustment": 1.33,
            "adjusted_trade_history_value": 16741.33,
            "difference": 1.33,
            "difference_after_bridge": 0.0,
        }
    ])
    correction_candidates = pd.DataFrame([
        {
            "net_pnl_delta": 2.0,
            "is_saved": False,
        },
        {
            "net_pnl_delta": 5.0,
            "is_saved": True,
        },
    ])

    totals = dashboard_statement_totals(
        statement_summary,
        ytd_reconciliation,
        correction_candidates,
    )

    assert "gross_ytd_pnl" in totals.columns
    assert "open_position_pnl" in totals.columns
    assert "closed_gross_ytd_pnl" in totals.columns
    assert "total_ytd_commissions_and_fees" in totals.columns
    assert "statement_closed_net_ytd_pnl" in totals.columns
    assert "trade_history_closed_net_ytd_pnl" in totals.columns
    assert "open_trade_exclusion" in totals.columns
    assert "ytd_bridge_adjustment" in totals.columns
    assert "total_closed_pnl_adjustment" in totals.columns
    assert "trade_history_adjusted_closed_net_ytd_pnl" in totals.columns
    assert "difference_before_adjustments" in totals.columns
    assert "difference_after_adjustments" in totals.columns
    assert "difference_before_ytd_bridge" in totals.columns
    assert "difference_after_ytd_bridge" in totals.columns
    assert "pending_suggested_correction_delta" in totals.columns
    assert "trade_history_adjusted_trade_history_closed_net_ytd_pnl" in totals.columns
    assert "difference" in totals.columns
    assert "statement_gross_ytd_pnl" not in totals.columns
    assert totals.loc[0, "statement_closed_net_ytd_pnl"] == 16741.33
    assert totals.loc[0, "trade_history_closed_net_ytd_pnl"] == 16740.00
    assert totals.loc[0, "open_trade_exclusion"] == -10.0
    assert totals.loc[0, "ytd_bridge_adjustment"] == 11.33
    assert totals.loc[0, "total_closed_pnl_adjustment"] == 1.33
    assert totals.loc[0, "trade_history_adjusted_closed_net_ytd_pnl"] == 16741.33
    assert totals.loc[0, "difference_before_adjustments"] == 1.33
    assert totals.loc[0, "difference_after_adjustments"] == 0.0
    assert totals.loc[0, "pending_suggested_correction_delta"] == 2.0
    assert (
        totals.loc[
            0,
            "trade_history_adjusted_trade_history_closed_net_ytd_pnl",
        ]
        == 16741.33
    )
    assert totals.loc[0, "difference"] == 0.0


def test_candidate_review_columns_identify_fee_only_corrections():
    candidates = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 10,
            "date": "2026-06-08",
            "account_bucket": "cash",
            "event_sequence": 1,
            "event_leg_sequence": 1,
            "ledger_timestamp": "2026-06-08 12:00:00",
            "ledger_description": "SOLD SPX",
            "ledger_cash_flow": 97.0,
            "original_trade_pnl": 100.0,
            "original_fees": 1.0,
            "original_net_pnl": 99.0,
            "corrected_trade_pnl": 100.0,
            "corrected_fees": 3.0,
            "corrected_net_pnl": 97.0,
        }
    ])

    reviewed = add_candidate_review_columns(candidates)

    assert reviewed.loc[0, "is_fee_only"]
    assert reviewed.loc[0, "trade_pnl_delta"] == 0.0
    assert reviewed.loc[0, "fee_delta"] == 2.0
    assert reviewed.loc[0, "likely_cause"] == (
        "Estimated fees differ from broker fees"
    )


def test_adjustment_review_rows_show_actual_adjustment_and_trade_detail():
    candidates = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 10,
            "date": "2026-06-08",
            "account_bucket": "cash",
            "event_sequence": 1,
            "event_leg_sequence": 1,
            "ledger_timestamp": "2026-06-08 12:00:00",
            "ledger_description": "SOLD SPX",
            "ledger_cash_flow": 97.0,
            "original_trade_pnl": 100.0,
            "original_fees": 1.0,
            "original_net_pnl": 99.0,
            "corrected_trade_pnl": 100.0,
            "corrected_fees": 3.0,
            "corrected_net_pnl": 97.0,
        }
    ])
    trades = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 10,
            "Exec Time": "6/8/26 12:00:00",
            "Strategy_Name": "Discretionary",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "8 JUN 26",
            "Strike": 7000,
            "Type": "PUT",
        }
    ])
    candidates = add_candidate_review_columns(candidates)

    rows = adjustment_review_rows(candidates, trades)

    assert rows.loc[0, "actual"] == 97.0
    assert rows.loc[0, "current"] == 99.0
    assert rows.loc[0, "adjustment"] == -2.0
    assert rows.loc[0, "adjusted"] == 97.0
    assert rows.loc[0, "timestamp"] == "2026-06-08 12:00:00"
    assert "Discretionary" in rows.loc[0, "trade_detail"]
    assert "SPX" in rows.loc[0, "trade_detail"]


def test_reconciliation_dashboard_rows_show_combined_and_likely_cause():
    reconciliation = pd.DataFrame([
        {
            "date": "2026-06-08",
            "account_bucket": "cash",
            "status": "unreconciled",
            "statement_trade_rows": 1,
            "statement_trade_cash_flow": 97.0,
            "non_trade_cash_flow": 0.0,
            "starting_balance": 1000.0,
            "ending_balance": 1097.0,
            "balance_delta": 97.0,
            "balance_residual": 0.0,
            "extracted_trade_count": 1,
            "extracted_net_pnl": 99.0,
            "unreconciled_delta": 2.0,
            "tolerance": 1.0,
        }
    ])
    candidates = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 10,
            "date": "2026-06-08",
            "account_bucket": "cash",
            "event_sequence": 1,
            "event_leg_sequence": 1,
            "ledger_timestamp": "2026-06-08 12:00:00",
            "ledger_description": "SOLD SPX",
            "ledger_cash_flow": 97.0,
            "original_trade_pnl": 100.0,
            "original_fees": 1.0,
            "original_net_pnl": 99.0,
            "corrected_trade_pnl": 100.0,
            "corrected_fees": 3.0,
            "corrected_net_pnl": 97.0,
        }
    ])
    candidates = add_candidate_review_columns(candidates)

    rows = reconciliation_dashboard_rows(
        reconciliation,
        candidates,
        tolerance=1.0,
    )

    assert "all_accounts" in rows["account_bucket"].tolist()
    cash_row = rows[rows["account_bucket"] == "cash"].iloc[0]
    assert cash_row["difference"] == 2.0
    assert cash_row["suggested_difference"] == 0.0
    assert cash_row["likely_cause"] == (
        "Estimated fees differ from broker fees"
    )


def test_dashboard_has_save_and_exit_controls():
    html = dashboard_html(
        {
            "statementTotals": [],
            "ytdBridge": [],
            "strategyNames": ["Discretionary"],
            "newTrades": [],
            "untaggedTrades": [],
            "openPositions": [],
            "reconciliation": [],
            "adjustmentReview": [],
            "correctionCandidates": [],
        },
        server_enabled=True,
    )

    assert "Save selected corrections" in html
    assert "Exit" in html
    assert "Closed PnL Adjustments" in html
    assert "YTD Calculation Detail" in html
    assert "YTD Net PNL From Account Statement" in html
    assert "YTD Net PNL From Trade History" in html
    assert "'open_trade_exclusion'" in html
    assert "'total_closed_pnl_adjustment'" in html
    assert "'difference_before_adjustments'" in html
    assert "'difference_after_adjustments'" in html
    assert "'pending_suggested_correction_delta'" in html
    assert "Save strategy names" in html
    assert "Untagged Trades" in html
    assert "/api/save-corrections" in html
    assert "/api/save-strategies" in html
    assert "beforeunload" in html
    assert "renderStatementDetail()" in html
    assert "renderStrategyTable('new-trades'" in html
    assert "renderStrategyTable('untagged-trades'" in html
    assert (
        "const rows = (DATA.correctionCandidates || []).filter(row => !row.is_saved);"
        in html
    )
    assert html.index("Statement Totals") < html.index("Suggested Corrections")
    assert html.index("Suggested Corrections") < html.index("New Trades Imported")
    assert html.index("Reconciliation") < html.index("YTD Calculation Detail")
    assert (
        "'_select','net_pnl_delta','trade_pnl_delta','fee_delta',"
        in html
    )


def test_ytd_bridge_offsets_open_premium_and_prior_year_carryover():
    statement_summary = {
        "statement_year": 2026,
        "statement_closed_gross_ytd_pnl": 97.50,
        "statement_total_ytd_commissions_and_fees": 8.08,
        "statement_closed_net_ytd_pnl": 89.42,
    }
    statement_positions = pd.DataFrame([
        {
            "Symbol": "/MESH26",
            "statement_open_pnl": 157.0,
        }
    ])
    master = pd.DataFrame([
        {
            "Exec Time": "1/2/26 14:17:49",
            "Symbol": "/MESH26 1/5 9 JAN 26 (Wk2)",
            "Pos Effect": "TO OPEN",
            "trade_pnl": 127.0,
            "fees": 8.0,
            "net_pnl": 119.0,
        }
    ])

    bridge = build_ytd_bridge_adjustments(
        statement_summary,
        statement_positions,
        master,
    )
    reconciliation = ytd_reconciliation_rows(
        statement_summary,
        master,
        bridge,
    )

    assert bridge["bridge_type"].tolist() == [
        "current_open_premium_exclusion",
        "prior_year_carryover_adjustment",
        "fee_true_up",
    ]
    assert bridge["adjustment_category"].tolist() == [
        "open_trade_exclusion",
        "ytd_bridge",
        "ytd_bridge",
    ]
    assert bridge["gross_pnl_adjustment"].round(2).tolist() == [
        -127.0,
        97.5,
        0.0,
    ]
    assert bridge["fee_adjustment"].round(2).tolist() == [
        0.0,
        0.0,
        0.08,
    ]
    closed_net = reconciliation[
        reconciliation["metric"] == "closed_net_ytd_pnl"
    ].iloc[0]
    assert round(closed_net["open_trade_exclusion"], 2) == -127.0
    assert round(closed_net["ytd_bridge_adjustment"], 2) == 97.42
    assert round(closed_net["bridge_adjustment"], 2) == -29.58
    assert reconciliation["difference_after_bridge"].round(2).tolist() == [
        0.0,
        0.0,
        0.0,
    ]


def write_tiny_statement(path, ledger_amount):
    path.write_text(
        "\n".join([
            "Cash Balance",
            "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
            "1/1/26,09:00:00,BAL,,Cash balance,,,,1000.00",
            (
                "1/1/26,10:00:00,TRD,,SOLD 1 XYZ,,, "
                f"{ledger_amount:.2f},{1000 + ledger_amount:.2f}"
            ).replace(", ", ","),
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            (
                "1/1/26 10:00:00,STOCK,SELL,-1,TO OPEN,XYZ,,,,"
                "100,100,MKT,abc"
            ),
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_two_day_statement(path):
    path.write_text(
        "\n".join([
            "Cash Balance",
            "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
            "1/1/26,09:00:00,BAL,,Cash balance,,,,1000.00",
            "1/1/26,10:00:00,TRD,,SOLD 1 OLD,,,95.00,1095.00",
            "1/2/26,09:00:00,BAL,,Cash balance,,,,1095.00",
            "1/2/26,10:00:00,TRD,,SOLD 1 NEW,,,50.00,1145.00",
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            "1/1/26 10:00:00,STOCK,SELL,-1,TO OPEN,OLD,,,,100,100,MKT,old",
            "1/2/26 10:00:00,STOCK,SELL,-1,TO OPEN,NEW,,,,50,50,MKT,new",
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_initial_overlap_statement(path):
    path.write_text(
        "\n".join([
            "Cash Balance",
            "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
            "1/1/26,09:00:00,BAL,,Cash balance,,,,1000.00",
            "1/1/26,09:30:00,TRD,,SOLD 1 ABC,,,25.00,1025.00",
            "1/1/26,10:00:00,TRD,,SOLD 1 XYZ,,,100.00,1125.00",
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            "1/1/26 09:30:00,STOCK,SELL,-1,TO OPEN,ABC,,,,25,25,MKT,abc",
            "1/1/26 10:00:00,STOCK,SELL,-1,TO OPEN,XYZ,,,,100,100,MKT,xyz",
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_shifted_overlap_statement(path):
    path.write_text(
        "\n".join([
            "Cash Balance",
            "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
            "1/1/26,09:00:00,BAL,,Cash balance,,,,1000.00",
            "1/1/26,10:00:00,TRD,,SOLD 1 XYZ,,,100.00,1100.00",
            "1/1/26,11:00:00,TRD,,SOLD 1 NEW,,,50.00,1150.00",
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            "1/1/26 10:00:00,STOCK,SELL,-1,TO OPEN,XYZ,,,,100,100,MKT,xyz",
            "1/1/26 11:00:00,STOCK,SELL,-1,TO OPEN,NEW,,,,50,50,MKT,new",
            "Equities",
        ]),
        encoding="utf-8",
    )


def test_strict_daily_import_blocks_master_when_cash_does_not_match(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_tiny_statement(statement, ledger_amount=95.0)

    with pytest.raises(SystemExit) as error:
        run_v2(
            input_file=statement,
            output_dir=output_dir,
            strategy_source_master=None,
            strict_daily_import=True,
        )

    paths = v2_output_paths(output_dir)
    assert error.value.code == 1
    assert not (output_dir / "master_cleaned_tos_data.csv").exists()
    assert (output_dir / "cleaned_tos_data.csv").exists()
    assert (output_dir / "trade_history_reconciliation_dashboard.html").exists()
    reconciliation = pd.read_csv(paths["cash_reconciliation"])
    assert reconciliation.loc[0, "status"] == "unreconciled"


def test_start_date_limits_strict_reconciliation_to_import_window(tmp_path):
    statement = tmp_path / "2026-01-02-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_two_day_statement(statement)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        start_date="2026-01-02",
        strict_daily_import=True,
    )

    master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    reconciliation = pd.read_csv(
        output_dir / "trade_history_reconciliation_detail.csv"
    )
    assert master["Symbol"].tolist() == ["NEW"]
    assert set(reconciliation["date"]) == {"2026-01-02"}
    assert set(reconciliation["status"]) == {"reconciled"}


def test_overlapping_statement_import_skips_existing_trade_rows(tmp_path):
    first_statement = tmp_path / "2026-01-01-AccountStatement.csv"
    second_statement = tmp_path / "2026-01-02-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(first_statement)
    write_shifted_overlap_statement(second_statement)

    run_v2(
        input_file=first_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    run_v2(
        input_file=second_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    reconciliation = pd.read_csv(
        output_dir / "trade_history_reconciliation_detail.csv"
    )
    assert master["Symbol"].tolist() == ["ABC", "XYZ", "NEW"]
    assert master["Symbol"].value_counts().to_dict() == {
        "ABC": 1,
        "XYZ": 1,
        "NEW": 1,
    }
    cash_reconciliation = reconciliation.loc[
        reconciliation["account_bucket"] == "cash"
    ]
    assert cash_reconciliation["extracted_net_pnl"].sum() == 50.0
    assert cash_reconciliation["statement_trade_cash_flow"].sum() == 50.0


def test_fully_overlapping_statement_import_is_noop_and_opens_dashboard(
    tmp_path,
    monkeypatch,
):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(statement)
    opened = []
    monkeypatch.setattr(
        "extract_trade_history_v2.webbrowser.open",
        opened.append,
    )

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    before = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")

    result = run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
        open_dashboard=True,
    )

    after = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    assert result["skipped_overlap_trades"] == 2
    assert result["dashboard_path"] == str(
        output_dir / "trade_history_reconciliation_dashboard.html"
    )
    assert opened == [
        (output_dir / "trade_history_reconciliation_dashboard.html")
        .resolve()
        .as_uri()
    ]
    assert after["Symbol"].tolist() == before["Symbol"].tolist()
    assert len(after) == 2


def test_save_strategy_updates_tags_master_and_current_import(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(statement)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    paths = v2_output_paths(output_dir)
    master_before = pd.read_csv(paths["master"])
    first_trade = master_before.iloc[0]

    result = save_strategy_updates(
        paths,
        [
            {
                "statement_file": first_trade["statement_file"],
                "statement_trade_row": first_trade["statement_trade_row"],
                "strategy_name": "Discretionary",
            }
        ],
    )

    master = pd.read_csv(paths["master"])
    cleaned = pd.read_csv(paths["cleaned"])
    assert result["saved_rows"] == 1
    assert result["cleaned_rows"] == 1
    assert master.loc[0, "Strategy_Name"] == "Discretionary"
    assert cleaned.loc[0, "Strategy_Name"] == "Discretionary"
    assert pd.isna(master.loc[1, "Strategy_Name"]) or (
        master.loc[1, "Strategy_Name"] == ""
    )


def test_start_date_import_preserves_existing_master_rows(tmp_path):
    first_statement = tmp_path / "2026-01-01-AccountStatement.csv"
    second_statement = tmp_path / "2026-01-02-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_tiny_statement(first_statement, ledger_amount=100.0)
    write_two_day_statement(second_statement)

    run_v2(
        input_file=first_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    first_master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")

    run_v2(
        input_file=second_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        start_date="2026-01-02",
        strict_daily_import=True,
    )

    master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    assert master["Symbol"].tolist() == ["XYZ", "NEW"]
    assert master["starting_equity"].tolist() == [
        first_master.loc[0, "starting_equity"],
        first_master.loc[0, "starting_equity"],
    ]


def test_strict_daily_import_writes_master_when_cash_matches(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_tiny_statement(statement, ledger_amount=100.0)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    assert len(master) == 1
    assert master.loc[0, "net_pnl"] == 100.0


def test_reset_output_files_removes_known_rebuild_artifacts(tmp_path):
    paths = v2_output_paths(tmp_path)
    master_path = tmp_path / "master_cleaned_tos_data.csv"
    dashboard_path = tmp_path / "trade_history_reconciliation_dashboard.html"
    master_path.write_text("old", encoding="utf-8")
    dashboard_path.write_text("old", encoding="utf-8")

    reset_output_files(tmp_path)

    assert not master_path.exists()
    assert not dashboard_path.exists()
    assert paths["master"].endswith("master_cleaned_tos_data.csv")


def test_account_trade_history_can_end_at_futures_options_section():
    lines = [
        "Account Trade History\n",
        "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price\n",
        "1/2/26 14:17:49,SINGLE,SELL,-4,TO OPEN,/MESH26,/EX2F26,6740,PUT,6.35,6.35\n",
        "\n",
        "Futures Options\n",
        "Symbol,Option Code,Exp,Strike,Type,Qty,Trade Price,Mark,Mark Value\n",
    ]

    trade_lines, start_idx = account_trade_history_lines(lines)

    assert start_idx == 0
    assert len(trade_lines) == 2
    assert trade_lines[0].startswith("Exec Time")


def test_cli_opens_dashboard_by_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["extract_trade_history_v2.py"])

    args = parse_args()

    assert args.open_dashboard


def test_cli_can_disable_auto_dashboard_launch(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "extract_trade_history_v2.py",
            "--no-open-dashboard",
        ],
    )

    args = parse_args()

    assert not args.open_dashboard
