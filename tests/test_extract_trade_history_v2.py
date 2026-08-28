from pathlib import Path

import pandas as pd
import pytest

from extract_trade_history_v2 import (
    add_candidate_review_columns,
    adjustment_review_rows,
    account_trade_history_lines,
    build_cash_trade_corrections,
    build_context,
    build_ytd_bridge_adjustments,
    create_dashboard_server,
    dashboard_html,
    dashboard_statement_totals,
    html_payload,
    parse_args,
    reconciliation_dashboard_rows,
    reset_output_files,
    run_v2,
    save_corrections_to_existing_outputs,
    save_strategy_updates,
    shutdown_dashboard_server,
    skip_existing_trade_overlaps,
    v2_output_paths,
    V2DashboardHandler,
    write_outputs,
    write_preview_outputs,
    ytd_reconciliation_rows,
)
from extract_trade_history import (
    CASH_CORRECTION_COLUMNS,
    apply_cash_trade_corrections,
    parse_cash_ledger,
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
        pd.DataFrame([
            {
                "statement_file": "2026-06-12-AccountStatement.csv",
                "statement_date": "2026-06-12",
                "statement_closed_net_ytd_pnl": 15000.00,
                "statement_gross_ytd_pnl": 21000.00,
            },
            {
                "statement_file": "2026-06-13-AccountStatement.csv",
                "statement_date": "2026-06-13",
                "statement_closed_net_ytd_pnl": 16741.33,
                "statement_gross_ytd_pnl": 23334.89,
            },
        ]),
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
    assert "previous_statement_file" in totals.columns
    assert "previous_statement_closed_net_ytd_pnl" in totals.columns
    assert "statement_closed_net_ytd_pnl_change_since_previous" in totals.columns
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
        totals.loc[0, "previous_statement_file"]
        == "2026-06-12-AccountStatement.csv"
    )
    assert totals.loc[0, "previous_statement_closed_net_ytd_pnl"] == 15000.00
    assert (
        round(
            totals.loc[
                0,
                "statement_closed_net_ytd_pnl_change_since_previous",
            ],
            2,
        )
        == 1741.33
    )
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
    assert "controlsForStrategyKey" in html
    assert "querySelectorAll(`[${attributeName}]`)" in html
    assert "CSS.escape" not in html
    assert "/api/save-corrections" in html
    assert "/api/save-strategies" in html
    assert "beforeunload" in html
    assert "renderStatementDetail()" in html
    assert "renderStrategyTable('new-trades'" in html
    assert "renderStrategyTable('untagged-trades'" in html
    assert "function correctionIsActionable(row)" in html
    assert "async function stopDashboardServer()" in html
    assert "fetch('/api/shutdown', { method: 'POST' })" in html
    assert "Dashboard server stopped. The command line should be available again." in html
    assert (
        "const rows = (DATA.correctionCandidates || []).filter(correctionIsActionable);"
        in html
    )
    assert (
        "keys.has(row.correction_key) && correctionIsActionable(row)"
        in html
    )
    assert html.index("Statement Totals") < html.index("Suggested Corrections")
    assert html.index("Suggested Corrections") < html.index("New Trades Imported")
    assert html.index("Reconciliation") < html.index("YTD Calculation Detail")
    assert (
        "'_select','net_pnl_delta','trade_pnl_delta','fee_delta',"
        in html
    )


def test_dashboard_shutdown_helper_stops_server():
    class FakeServer:
        stopped = False

        def shutdown(self):
            self.stopped = True

    server = FakeServer()
    thread = shutdown_dashboard_server(server)
    thread.join(timeout=2)

    assert server.stopped
    assert not thread.is_alive()


def test_dashboard_server_falls_back_when_port_is_in_use(monkeypatch):
    class FakeHTTPServer:
        def __init__(self, address, handler_class):
            _, port = address

            if port == 8770:
                raise OSError(98, "Address already in use")

            self.server_port = port
            self.handler_class = handler_class

        def server_close(self):
            pass

    monkeypatch.setattr(
        "extract_trade_history_v2.HTTPServer",
        FakeHTTPServer,
    )

    server = create_dashboard_server(
        "127.0.0.1",
        8770,
        V2DashboardHandler,
    )

    assert server.server_port == 8771
    assert server.handler_class is V2DashboardHandler


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
            "Spread": "SINGLE",
            "Side": "SELL",
            "Qty": -1,
            "Symbol": "/MESH26 1/5 9 JAN 26 (Wk2)",
            "Exp": "9 JAN 26",
            "Strike": 6000,
            "Type": "PUT",
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


def test_ytd_bridge_excludes_only_current_open_lot_premium():
    statement_summary = {
        "statement_year": 2026,
        "statement_closed_gross_ytd_pnl": -70.0,
        "statement_total_ytd_commissions_and_fees": 0.0,
        "statement_closed_net_ytd_pnl": -70.0,
    }
    statement_positions = pd.DataFrame([
        {
            "Symbol": "SPX",
            "statement_open_pnl": 15.0,
        }
    ])
    master = pd.DataFrame([
        {
            "Exec Time": "1/2/26 10:00:00",
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "2 JAN 26",
            "Strike": 5000,
            "Type": "PUT",
            "trade_pnl": 200.0,
            "fees": 0.0,
            "net_pnl": 200.0,
        },
        {
            "Exec Time": "1/2/26 11:00:00",
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "SPX",
            "Exp": "2 JAN 26",
            "Strike": 5000,
            "Type": "PUT",
            "trade_pnl": -250.0,
            "fees": 0.0,
            "net_pnl": -250.0,
        },
        {
            "Exec Time": "6/15/26 10:00:00",
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
            "Side": "SELL",
            "Qty": -2,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "16 JUN 26",
            "Strike": 7525,
            "Type": "PUT",
            "trade_pnl": 200.0,
            "fees": 0.0,
            "net_pnl": 200.0,
        },
        {
            "Exec Time": "6/15/26 12:00:00",
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "SPX",
            "Exp": "16 JUN 26",
            "Strike": 7525,
            "Type": "PUT",
            "trade_pnl": -120.0,
            "fees": 0.0,
            "net_pnl": -120.0,
        },
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
    closed_gross = reconciliation[
        reconciliation["metric"] == "closed_gross_ytd_pnl"
    ].iloc[0]

    assert bridge["bridge_type"].tolist() == [
        "current_open_premium_exclusion",
    ]
    assert bridge["gross_pnl_adjustment"].round(2).tolist() == [-100.0]
    assert round(closed_gross["trade_history_value"], 2) == 30.0
    assert round(closed_gross["open_trade_exclusion"], 2) == -100.0
    assert round(closed_gross["adjusted_trade_history_value"], 2) == -70.0
    assert round(closed_gross["difference_after_bridge"], 2) == 0.0


def test_v2_cash_corrections_use_ledger_for_unmatched_futures_closes():
    lines = [
        "Futures Statements\n",
        ",1/20/26,03:02:36,TRD,ref,SOLD -2 /ZBH26:XCBT @114'10,-0.00,-5.38,-2250.00,0\n",
        ",1/20/26,06:01:22,TRD,ref,SOLD -1 /ZBH26:XCBT @114'03,-0.00,-2.69,-781.25,0\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 100,
            "Exec Time": "1/19/26 09:59:16",
            "Spread": "FUTURE",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO OPEN",
            "Symbol": "/ZBH26",
            "Type": "FUTURE",
            "Price": "114'28",
            "fees": 2.69,
            "trade_pnl": 0.0,
            "net_pnl": -2.69,
        },
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 101,
            "Exec Time": "1/20/26 03:02:36",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -2,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/ZBH26",
            "Type": "FUTURE",
            "Price": "114'10",
            "fees": 5.38,
            "trade_pnl": -562.50,
            "net_pnl": -567.88,
        },
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 102,
            "Exec Time": "1/20/26 06:01:22",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/ZBH26",
            "Type": "FUTURE",
            "Price": "114'03",
            "fees": 2.69,
            "trade_pnl": 0.0,
            "net_pnl": -2.69,
        },
    ])

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )

    row_101 = corrections[
        corrections["statement_trade_row"] == 101
    ].iloc[0]
    row_102 = corrections[
        corrections["statement_trade_row"] == 102
    ].iloc[0]
    assert row_101["correction_source"] == (
        "cash_ledger_unmatched_futures_close"
    )
    assert row_101["corrected_trade_pnl"] == -2250.0
    assert round(row_101["corrected_fees"], 2) == 5.38
    assert round(row_101["corrected_net_pnl"], 2) == -2255.38
    assert row_102["corrected_trade_pnl"] == -781.25
    assert round(row_102["corrected_net_pnl"], 2) == -783.94
    assert round(corrected.loc[1, "net_pnl"], 2) == -2255.38
    assert round(corrected.loc[2, "net_pnl"], 2) == -783.94


def test_v2_cash_corrections_split_same_price_futures_partial_fills():
    lines = [
        "Futures Statements\n",
        ",2/1/26,22:35:43,TRD,ref,SOLD -4 /MESH26:XCME @6888.00,-1.48,-7.20,-1555.00,0\n",
        ",2/1/26,22:35:43,TRD,ref,SOLD -4 /MESH26:XCME @6888.25,-1.48,-7.20,-1550.00,0\n",
        ",2/1/26,22:35:43,TRD,ref,SOLD -1 /MESH26:XCME @6888.00,-0.37,-1.80,-388.75,0\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 249,
            "Exec Time": "2/1/26 22:35:43",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -4,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MESH26",
            "Type": "FUTURE",
            "Price": "6888.00",
            "fees": 10.0,
            "trade_pnl": 0.0,
            "net_pnl": -10.0,
        },
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 250,
            "Exec Time": "2/1/26 22:35:43",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -4,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MESH26",
            "Type": "FUTURE",
            "Price": "6888.25",
            "fees": 10.0,
            "trade_pnl": 0.0,
            "net_pnl": -10.0,
        },
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 251,
            "Exec Time": "2/1/26 22:35:43",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MESH26",
            "Type": "FUTURE",
            "Price": "6888.00",
            "fees": 2.5,
            "trade_pnl": 0.0,
            "net_pnl": -2.5,
        },
    ])

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )
    by_row = corrections.set_index("statement_trade_row")

    assert round(by_row.loc[249, "corrected_net_pnl"], 2) == -1563.68
    assert round(by_row.loc[250, "corrected_net_pnl"], 2) == -1558.68
    assert round(by_row.loc[251, "corrected_net_pnl"], 2) == -390.92
    assert round(corrected["net_pnl"].sum(), 2) == -3513.28


def test_v2_cash_corrections_keep_identical_futures_fills_separate():
    cash_ledger = pd.DataFrame([
        {
            "date": "2026-02-26",
            "timestamp": pd.Timestamp("2026-02-26 11:26:31"),
            "account_bucket": "futures",
            "type": "TRD",
            "description": "SOLD -1 /MBTG26:XCME @67495.00",
            "amount": 7.00,
            "misc_fees": -1.17,
            "commissions_fees": -1.80,
            "cash_flow": 4.03,
            "balance": 47682.79,
        },
        {
            "date": "2026-02-26",
            "timestamp": pd.Timestamp("2026-02-26 11:26:31"),
            "account_bucket": "futures",
            "type": "TRD",
            "description": "SOLD -1 /MBTG26:XCME @67495.00",
            "amount": 7.00,
            "misc_fees": -1.17,
            "commissions_fees": -1.80,
            "cash_flow": 4.03,
            "balance": 47686.82,
        },
    ])
    trades = pd.DataFrame([
        {
            "statement_file": "2026-02-28-AccountStatement.csv",
            "statement_trade_row": 452,
            "Exec Time": "2/26/26 11:26:31",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MBTG26",
            "Exp": "FEB 26",
            "Type": "FUTURE",
            "Price": "67495.00",
            "trade_pnl": 290.50,
            "fees": 3.00,
            "net_pnl": 287.50,
        },
        {
            "statement_file": "2026-02-28-AccountStatement.csv",
            "statement_trade_row": 453,
            "Exec Time": "2/26/26 11:26:31",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MBTG26",
            "Exp": "FEB 26",
            "Type": "FUTURE",
            "Price": "67495.00",
            "trade_pnl": 7.00,
            "fees": 3.00,
            "net_pnl": 4.00,
        },
    ])

    corrections = build_cash_trade_corrections(cash_ledger, trades)

    assert corrections["statement_trade_row"].tolist() == [452, 453]
    assert corrections["ledger_cash_flow"].tolist() == [4.03, 4.03]


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


def write_futures_open_statement(path):
    path.write_text(
        "\n".join([
            "Futures Statements",
            (
                "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,"
                "Misc Fees,Commissions & Fees,Amount,Balance"
            ),
            (
                "1/30/26,1/30/26,01:00:00,BAL,--,"
                "Futures cash balance at the start of business day,"
                "--,--,--,\"50,000.00\""
            ),
            (
                "1/30/26,1/30/26,09:07:11,TRD,ref,"
                "BOT +1 /MGCJ26:XCEC @5086.00,-0.62,-1.80,--,"
                "\"49,997.58\""
            ),
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            (
                "1/30/26 09:07:11,FUTURE,BUY,+1,TO OPEN,/MGCJ26,"
                "APR 26,,FUTURE,5086.00,5086.00,LMT,open-mgc"
            ),
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_futures_close_statement(path):
    path.write_text(
        "\n".join([
            "Futures Statements",
            (
                "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,"
                "Misc Fees,Commissions & Fees,Amount,Balance"
            ),
            (
                "2/3/26,2/3/26,01:00:00,BAL,--,"
                "Futures cash balance at the start of business day,"
                "--,--,--,\"49,997.58\""
            ),
            (
                "2/3/26,2/3/26,11:16:23,TRD,ref,"
                "SOLD -1 /MGCJ26:XCEC @4985.00,-0.62,-1.80,"
                "\"3,324.00\",\"53,319.16\""
            ),
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            (
                "2/3/26 11:16:23,FUTURE,SELL,-1,TO CLOSE,/MGCJ26,"
                "APR 26,,FUTURE,4985.00,4985.00,LMT,close-mgc"
            ),
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_futures_cash_settlement_statement(path):
    path.write_text(
        "\n".join([
            "Futures Statements",
            (
                "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,"
                "Misc Fees,Commissions & Fees,Amount,Balance"
            ),
            (
                "3/20/26,3/20/26,01:00:00,BAL,--,"
                "Futures cash balance at the start of business day,"
                "--,--,--,\"10,000.00\""
            ),
            (
                "3/20/26,3/20/26,10:16:18,TRD,ref,"
                "SOLD -4 /MESM26:XCME 1/5 27 MAR 26 (Wk4) "
                "/EX4H26:XCME 6250 PUT @13.20,-0.88,-7.20,"
                "264.00,\"10,255.92\""
            ),
            (
                "3/27/26,3/27/26,01:00:00,BAL,--,"
                "Futures cash balance at the start of business day,"
                "--,--,--,\"10,255.92\""
            ),
            (
                "3/27/26,3/27/26,09:00:00,TRD,ref,"
                "BOT +1 /MESM26:XCME @6500.00,-0.37,-1.80,--,"
                "\"10,253.75\""
            ),
            (
                "3/27/26,3/27/26,16:00:15,TRD,ref,"
                "Removal of option due to expiration of /MESM26 XCME "
                "5 (WEEKLY) 27 Mar 2026 6250.0 PUT,--,--,--,"
                "\"10,253.75\""
            ),
            (
                "3/27/26,3/27/26,17:06:03,TRD,ref,"
                "cash settle future,-5.08,-7.20,-902.64,"
                "\"9,338.83\""
            ),
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            (
                "3/20/26 10:16:18,SINGLE,SELL,-4,TO OPEN,"
                "/MESM26 1/5 27 MAR 26 (Wk4),/EX4H26,6250,"
                "PUT,13.20,13.20,LMT,open-mes-put"
            ),
            (
                "3/27/26 09:00:00,FUTURE,BUY,+1,TO OPEN,"
                "/MESM26,JUN 26,,FUTURE,6500.00,6500.00,LMT,"
                "open-mes-future"
            ),
            "Equities",
        ]),
        encoding="utf-8",
    )


def test_v2_import_uses_master_inventory_for_futures_closes(tmp_path):
    open_statement = tmp_path / "2026-01-30-AccountStatement.csv"
    close_statement = tmp_path / "2026-02-03-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_futures_open_statement(open_statement)
    write_futures_close_statement(close_statement)

    run_v2(
        input_file=open_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    run_v2(
        input_file=close_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    paths = v2_output_paths(output_dir)
    master = pd.read_csv(paths["master"])
    close = master.loc[master["Order ID"].astype(str).eq("close-mgc")].iloc[0]
    candidates = pd.read_csv(paths["cash_trade_correction_candidates"])
    close_candidate = candidates.loc[
        candidates["statement_trade_row"].eq(1)
    ].iloc[0]
    reconciliation = pd.read_csv(paths["cash_reconciliation"])

    assert round(close["trade_pnl"], 2) == -1010.00
    assert round(close["net_pnl"], 2) == -1012.75
    assert round(close_candidate["corrected_trade_pnl"], 2) == -1010.00
    assert close_candidate["is_fee_only"]
    assert set(reconciliation["status"]) == {"explained"}
    assert reconciliation.loc[0, "settlement_basis_explained"]


def test_v2_imports_futures_cash_settlement_rows(tmp_path):
    statement = tmp_path / "2026-03-31-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_futures_cash_settlement_statement(statement)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    master = pd.read_csv(output_dir / "master_cleaned_tos_data.csv")
    settlement = master[
        master["cash_correction_source"].astype(str).eq(
            "cash_ledger_futures_cash_settlement",
        )
    ].iloc[0]
    reconciliation = pd.read_csv(output_dir / "cash_balance_reconciliation.csv")

    assert settlement["Symbol"] == "/MESM26 27 MAR 26 (WEEKLY)"
    assert settlement["Pos Effect"] == "CASH SETTLE"
    assert round(settlement["trade_pnl"], 2) == -902.64
    assert round(settlement["fees"], 2) == 12.28
    assert round(settlement["net_pnl"], 2) == -914.92
    assert set(reconciliation["status"]) == {"reconciled"}


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


def write_overlap_statement_with_existing_trade_after_new_trade(path):
    path.write_text(
        "\n".join([
            "Cash Balance",
            "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE",
            "1/1/26,09:00:00,BAL,,Cash balance,,,,1000.00",
            "1/1/26,09:45:00,TRD,,SOLD 1 NEW,,,50.00,1050.00",
            "1/1/26,10:00:00,TRD,,SOLD 1 XYZ,,,100.00,1150.00",
            "Account Trade History",
            (
                "Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,"
                "Type,Price,Net Price,Order Type,Order ID"
            ),
            "1/1/26 09:45:00,STOCK,SELL,-1,TO OPEN,NEW,,,,50,50,MKT,new",
            "1/1/26 10:00:00,STOCK,SELL,-1,TO OPEN,XYZ,,,,100,100,MKT,xyz",
            "Equities",
        ]),
        encoding="utf-8",
    )


def write_fully_overlapping_statement_with_ytd(
    path,
    statement_open_pnl,
    mbtm_open_pnl,
    closed_gross_ytd_pnl=10000.0,
):
    statement_ytd_pnl = closed_gross_ytd_pnl + statement_open_pnl
    mbtm_ytd_pnl = 5529.5 + mbtm_open_pnl
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
            "Profits and Losses",
            "Symbol,P/L Open,P/L %,P/L Day,Mark Value,P/L YTD,Description",
            (
                f"/MBTM26,\"${mbtm_open_pnl:,.2f}\",0.00%,"
                f"$0.00,$0.00,\"${mbtm_ytd_pnl:,.2f}\","
                "\"Micro Bitcoin Futures,Jun-2026, (prev. /MBTM6)\""
            ),
            (
                f",\"${statement_open_pnl:,.2f}\",0.00%,$0.00,$0.00,"
                f"\"${statement_ytd_pnl:,.2f}\",OVERALL TOTALS"
            ),
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


def test_overlap_reconciliation_counts_skipped_existing_master_rows(tmp_path):
    first_statement = tmp_path / "2026-01-01-AccountStatement.csv"
    second_statement = tmp_path / "2026-01-02-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(first_statement)
    write_overlap_statement_with_existing_trade_after_new_trade(
        second_statement
    )

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
    cash_reconciliation = reconciliation.loc[
        reconciliation["account_bucket"] == "cash"
    ]
    assert master["Symbol"].value_counts().to_dict() == {
        "ABC": 1,
        "XYZ": 1,
        "NEW": 1,
    }
    assert cash_reconciliation["status"].tolist() == ["reconciled"]
    assert cash_reconciliation["extracted_net_pnl"].sum() == 150.0
    assert cash_reconciliation["statement_trade_cash_flow"].sum() == 150.0


def test_overlapping_statement_import_skips_aggregated_split_fills(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    master = pd.DataFrame([
        {
            "Exec Time": "1/1/26 09:30:28",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 900,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.7899,
            "Net Price": 4.7899,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 09:30:28",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 100,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.7777,
            "Net Price": 4.7777,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 09:35:11",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 580,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.37,
            "Net Price": 4.37,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 09:35:11",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 69,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.37,
            "Net Price": 4.37,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 09:35:11",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 351,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.37,
            "Net Price": 4.37,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 10:17:04",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -103,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.985,
            "Net Price": 4.985,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 10:17:05",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -100,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.985,
            "Net Price": 4.985,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 10:17:05",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -100,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.985,
            "Net Price": 4.985,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 10:17:05",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -100,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.9837,
            "Net Price": 4.9837,
            "Order Type": "LMT",
        },
    ])
    master.to_csv(master_file, index=False)

    incoming = pd.DataFrame([
        {
            "Exec Time": "1/1/26 09:30:28",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 1000,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.78868,
            "Net Price": 4.78868,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 09:35:11",
            "Spread": "STOCK",
            "Side": "BUY",
            "Qty": 1000,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.37,
            "Net Price": 4.37,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 10:17:05",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -403,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPCE",
            "Price": 4.98467742,
            "Net Price": 4.98467742,
            "Order Type": "LMT",
        },
        {
            "Exec Time": "1/1/26 11:00:00",
            "Spread": "STOCK",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "NEW",
            "Price": 50.0,
            "Net Price": 50.0,
            "Order Type": "LMT",
        },
    ])

    filtered, skipped = skip_existing_trade_overlaps(incoming, master_file)

    assert skipped == 3
    assert filtered["Symbol"].tolist() == ["NEW"]


def test_overlapping_statement_import_skips_aggregated_option_spread(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    master = pd.DataFrame([
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 2,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7535,
            "Type": "PUT",
            "Price": 3.02,
            "Net Price": 1.95,
        },
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 3,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7535,
            "Type": "PUT",
            "Price": 3.02,
            "Net Price": 1.95,
        },
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "",
            "Side": "SELL",
            "Qty": -2,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7515,
            "Type": "PUT",
            "Price": 1.07,
            "Net Price": "DEBIT",
        },
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "",
            "Side": "SELL",
            "Qty": -3,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7515,
            "Type": "PUT",
            "Price": 1.07,
            "Net Price": "DEBIT",
        },
    ])
    master.to_csv(master_file, index=False)
    incoming = pd.DataFrame([
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 5,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7535,
            "Type": "PUT",
            "Price": 1.95,
            "Net Price": 1.95,
        },
        {
            "Exec Time": "1/1/26 11:00:55",
            "Spread": "",
            "Side": "SELL",
            "Qty": 5,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Exp": "1 JAN 26",
            "Strike": 7515,
            "Type": "PUT",
            "Price": 1.95,
            "Net Price": "DEBIT",
        },
    ])

    filtered, skipped = skip_existing_trade_overlaps(incoming, master_file)

    assert skipped == 2
    assert filtered.empty


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


def test_fully_overlapping_rerun_refreshes_statement_ytd_outputs(tmp_path):
    first_statement = tmp_path / "2026-06-07-AccountStatement.csv"
    second_statement = tmp_path / "2026-06-15-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_fully_overlapping_statement_with_ytd(
        first_statement,
        statement_open_pnl=1945.0,
        mbtm_open_pnl=1945.0,
    )
    write_fully_overlapping_statement_with_ytd(
        second_statement,
        statement_open_pnl=0.0,
        mbtm_open_pnl=0.0,
        closed_gross_ytd_pnl=11000.0,
    )

    run_v2(
        input_file=first_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    result = run_v2(
        input_file=second_statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    paths = v2_output_paths(output_dir)
    summary = pd.read_csv(paths["statement_ytd_summary"]).iloc[0]
    positions = pd.read_csv(paths["statement_ytd_positions"])
    history = pd.read_csv(paths["statement_ytd_history"])
    mbtm = positions[positions["Symbol"].eq("/MBTM26")].iloc[0]
    dashboard = Path(paths["dashboard"]).read_text()
    assert result["context"] is None
    assert result["skipped_overlap_trades"] == 2
    assert summary["statement_file"] == str(second_statement)
    assert round(summary["statement_open_position_pnl"], 2) == 0.0
    assert round(summary["statement_closed_net_ytd_pnl"], 2) == 11000.0
    assert history["statement_basename"].tolist() == [
        first_statement.name,
        second_statement.name,
    ]
    assert mbtm["statement_file"] == str(second_statement)
    assert round(mbtm["statement_open_pnl"], 2) == 0.0
    assert round(mbtm["statement_closed_gross_pnl"], 2) == 5529.50
    assert '"statement_closed_net_ytd_pnl_change_since_previous": 1000.0' in (
        dashboard
    )


def test_fully_overlapping_rerun_applies_saved_cash_corrections(tmp_path):
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
    saved_corrections = pd.DataFrame([
        {
            "statement_file": "2026-01-01-AccountStatement.csv",
            "statement_trade_row": 1,
            "date": "2026-01-01",
            "account_bucket": "cash",
            "event_sequence": 1,
            "event_leg_sequence": 1,
            "correction_status": "cash_ledger_applied",
            "correction_source": "cash_ledger",
            "ledger_timestamp": "2026-01-01 09:30:00",
            "ledger_description": "SOLD 1 ABC",
            "ledger_amount": 25.0,
            "ledger_cash_flow": 24.0,
            "ledger_misc_fees": 0.0,
            "ledger_commissions_fees": -1.0,
            "original_trade_pnl": 25.0,
            "original_fees": 0.0,
            "original_net_pnl": 25.0,
            "corrected_trade_pnl": 25.0,
            "corrected_fees": 1.0,
            "corrected_net_pnl": 24.0,
        }
    ]).reindex(columns=CASH_CORRECTION_COLUMNS)
    saved_corrections.to_csv(paths["cash_trade_corrections"], index=False)
    duplicated_cleaned = pd.read_csv(paths["cleaned"])
    pd.concat(
        [
            duplicated_cleaned,
            duplicated_cleaned,
        ],
        ignore_index=True,
        sort=False,
    ).to_csv(paths["cleaned"], index=False)

    result = run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )

    master = pd.read_csv(paths["master"])
    cleaned = pd.read_csv(paths["cleaned"])
    corrected = master[master["statement_trade_row"].eq(1)].iloc[0]
    assert result["context"] is None
    assert round(corrected["fees"], 2) == 1.0
    assert round(corrected["net_pnl"], 2) == 24.0
    assert corrected["cash_correction_applied"]
    assert len(cleaned) == 2
    assert not cleaned.duplicated([
        "statement_file",
        "statement_trade_row",
    ]).any()


def test_existing_dashboard_save_corrections_refreshes_outputs(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_tiny_statement(statement, ledger_amount=99.50)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    paths = v2_output_paths(output_dir)
    candidates = pd.read_csv(paths["cash_trade_correction_candidates"])
    master_before = pd.read_csv(paths["master"])

    result = save_corrections_to_existing_outputs(
        paths,
        candidates,
        paths["dashboard"],
        cash_validation_tolerance=1.0,
        cash_corrections_path=paths["cash_trade_corrections"],
    )

    master = pd.read_csv(paths["master"])
    saved_candidates = pd.read_csv(paths["cash_trade_correction_candidates"])
    dashboard = Path(paths["dashboard"]).read_text()
    assert round(master_before.loc[0, "net_pnl"], 2) == 100.00
    assert result["submitted_rows"] == 1
    assert round(master.loc[0, "trade_pnl"], 2) == 99.50
    assert round(master.loc[0, "fees"], 2) == 0.00
    assert round(master.loc[0, "net_pnl"], 2) == 99.50
    assert bool(master.loc[0, "cash_correction_applied"])
    assert bool(saved_candidates.loc[0, "is_saved"])
    assert '"is_saved": true' in dashboard
    assert '"serverEnabled": true' in dashboard


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


def test_dashboard_payload_lists_current_import_untagged_trades(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(statement)

    result = run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    payload = html_payload(
        result["context"],
        result["outputs"],
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert len(payload["newTrades"]) == 2
    assert len(payload["untaggedTrades"]) == 2
    assert {
        row["statement_trade_row"]
        for row in payload["untaggedTrades"]
    } == {1, 2}


def test_write_outputs_preserves_saved_strategy_names_in_current_import(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(statement)

    result = run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    paths = v2_output_paths(output_dir)
    first_trade = pd.read_csv(paths["master"]).iloc[0]
    save_strategy_updates(
        paths,
        [
            {
                "statement_file": first_trade["statement_file"],
                "statement_trade_row": first_trade["statement_trade_row"],
                "strategy_name": "Discretionary",
            }
        ],
    )

    outputs = write_outputs(result["context"], pd.DataFrame())

    cleaned = pd.read_csv(paths["cleaned"])
    assert outputs["cleaned"].loc[0, "Strategy_Name"] == "Discretionary"
    assert cleaned.loc[0, "Strategy_Name"] == "Discretionary"


def test_no_new_trade_rerun_backfills_missing_strategy_names(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    strategy_source = tmp_path / "strategy_source.csv"
    write_initial_overlap_statement(statement)

    run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    paths = v2_output_paths(output_dir)
    source = pd.read_csv(paths["master"])
    source["Strategy_Name"] = "Discretionary"
    source.to_csv(strategy_source, index=False)

    result = run_v2(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=strategy_source,
        strict_daily_import=True,
    )

    master = pd.read_csv(paths["master"])
    cleaned = pd.read_csv(paths["cleaned"])
    dashboard = Path(paths["dashboard"]).read_text()
    assert result["skipped_overlap_trades"] == 2
    assert master["Strategy_Name"].tolist() == [
        "Discretionary",
        "Discretionary",
    ]
    assert cleaned["Strategy_Name"].tolist() == [
        "Discretionary",
        "Discretionary",
    ]
    assert '"Strategy_Name": "Discretionary"' in dashboard


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


def test_preview_after_master_write_does_not_double_count_current_import(tmp_path):
    statement = tmp_path / "2026-01-01-AccountStatement.csv"
    output_dir = tmp_path / "rebuild"
    write_initial_overlap_statement(statement)
    context = build_context(
        input_file=statement,
        output_dir=output_dir,
        strategy_source_master=None,
        strict_daily_import=True,
    )
    empty_corrections = pd.DataFrame(columns=CASH_CORRECTION_COLUMNS)

    write_outputs(context, empty_corrections)
    preview = write_preview_outputs(context, empty_corrections)

    cleaned = preview["cleaned"]
    reconciliation = preview["reconciliation"]
    cash_reconciliation = reconciliation[
        reconciliation["account_bucket"].astype(str).eq("cash")
    ].iloc[0]

    assert len(cleaned) == 2
    assert round(cash_reconciliation["extracted_net_pnl"], 2) == 125.0
    assert round(cash_reconciliation["statement_trade_cash_flow"], 2) == 125.0
    assert cash_reconciliation["status"] == "reconciled"


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
