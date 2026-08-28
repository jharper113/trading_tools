import json
import re

import pandas as pd

from extract_trade_history import (
    annotate_cash_reconciliation_reviews,
    apply_cash_trade_corrections,
    auto_approved_cash_trade_corrections,
    build_fee_correction_suggestions,
    build_cash_trade_corrections,
    combine_cash_trade_corrections,
    correction_key,
    current_statement_approved_corrections,
    drop_invalid_strategy_duplicates,
    filter_by_exec_date,
    filter_cash_ledger_by_date,
    filter_reviewed_reconciliation_groups,
    fill_missing_execution_times,
    group_review_key,
    parse_filter_date,
    parse_cash_ledger,
    parse_statement_ytd_summary,
    reconcile_cash_balances,
    strict_reconciliation_failures,
    summarize_cash_reconciliation,
    write_cash_reconciliation_dashboard,
    ytd_dashboard_section,
)


def test_fill_missing_execution_times_uses_previous_trade_time():
    df = pd.DataFrame([
        {
            "Exec Time": "1/15/26 10:35:41",
            "Symbol": "SPX",
            "Strike": 6950,
        },
        {
            "Exec Time": "",
            "Symbol": "SPX",
            "Strike": 6930,
        },
        {
            "Exec Time": None,
            "Symbol": "SPX",
            "Strike": 6920,
        },
    ])

    filled = fill_missing_execution_times(df)

    assert filled["Exec Time"].tolist() == [
        "1/15/26 10:35:41",
        "1/15/26 10:35:41",
        "1/15/26 10:35:41",
    ]


def test_parse_statement_ytd_summary_uses_overall_totals_and_fee_labels():
    lines = [
        "Profits and Losses\n",
        "Symbol,P/L Open,P/L %,P/L Day,Mark Value,P/L YTD,Description\n",
        "SPCX,($356.48),-0.53%,\"$2,304.00\",\"$66,684.00\",$497.01,SPACE EX TECH SPACEX A\n",
        ",\"$1,868.54\",+2.13%,\"$2,458.33\",\"$67,335.02\",\"$23,334.89\",OVERALL TOTALS\n",
        "\n",
        "Forex Account Summary\n",
        "Forex Commissions YTD,$0.00\n",
        "\n",
        "Account Summary\n",
        "Equity Commissions & Fees YTD,\"$1,343.26\"\n",
        "Futures Commissions & Fees YTD,\"$3,368.13\"\n",
        "Crypto Trading Fees YTD,$13.63\n",
        "Total Commissions & Fees YTD,\"$4,711.39\"\n",
    ]

    summary, positions = parse_statement_ytd_summary(
        lines,
        "/tmp/2026-06-13-AccountStatement.csv",
    )

    assert summary["statement_year"] == 2026
    assert summary["statement_gross_ytd_pnl"] == 23334.89
    assert summary["statement_open_position_pnl"] == 1868.54
    assert summary["statement_total_ytd_commissions_and_fees"] == 4725.02
    assert round(summary["statement_closed_net_ytd_pnl"], 2) == 16741.33
    assert positions.loc[0, "Symbol"] == "SPCX"
    assert positions.loc[0, "statement_closed_gross_pnl"] == 853.49


def test_ytd_dashboard_shows_only_nonzero_open_pnl_positions():
    summary = {
        "statement_gross_ytd_pnl": 23334.89,
        "statement_open_position_pnl": 1868.54,
        "statement_closed_gross_ytd_pnl": 21466.35,
        "statement_total_ytd_commissions_and_fees": 4725.02,
        "statement_closed_net_ytd_pnl": 16741.33,
    }
    positions = pd.DataFrame([
        {
            "Symbol": "SPCX",
            "statement_open_pnl": 1868.54,
            "statement_closed_gross_pnl": 853.49,
        },
        {
            "Symbol": "/MGCM26",
            "statement_open_pnl": 0.0,
            "statement_closed_gross_pnl": -4312.0,
        },
        {
            "Symbol": "SPCE",
            "statement_open_pnl": 0.0,
            "statement_closed_gross_pnl": 15743.32,
        },
    ])

    html = ytd_dashboard_section(summary, positions)

    assert "Open PnL Positions" in html
    totals_heading = html.index("<h2>Statement Totals</h2>")
    totals_section_end = html.index("</section>", totals_heading)
    open_positions_heading = html.index("<h2>Open PnL Positions</h2>")
    assert totals_section_end < open_positions_heading
    assert "SPCX" in html
    assert "/MGCM26" not in html
    assert "SPCE" not in html


def test_parse_cash_ledger_extracts_cash_balance_rows():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/1/26,01:00:00,BAL,,Cash balance,,,,1000.00\n",
        "1/1/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "Account Trade History\n",
    ]

    ledger = parse_cash_ledger(lines)

    assert ledger["account_bucket"].tolist() == ["cash", "cash"]
    assert ledger.loc[1, "cash_flow"] == 97.0


def test_parse_cash_ledger_extracts_futures_statement_rows():
    lines = [
        "Futures Statements\n",
        "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance\n",
        (
            "1/2/26,1/2/26,14:17:49,TRD,=\"1005042627468\","
            "SOLD -4 /MESH26:XCME 1/5 9 JAN 26 (Wk2) /EX2F26:XCME 6740 PUT @6.35,"
            "-0.88,-7.20,127.00,\"45,048.78\"\n"
        ),
        (
            "1/2/26,1/2/26,16:00:15,TRD,=\"109566804912\","
            "Removal of option due to expiration of /MESH26 XCME 5 (WEEKLY) 2 Jan 2026 6840.0 PUT,"
            "--,--,--,\"45,048.78\"\n"
        ),
        "Account Trade History\n",
    ]

    ledger = parse_cash_ledger(lines)

    assert ledger["account_bucket"].tolist() == ["futures", "futures"]
    assert ledger["timestamp"].tolist() == [
        pd.Timestamp("2026-01-02 14:17:49"),
        pd.Timestamp("2026-01-02 16:00:15"),
    ]
    assert ledger.loc[0, "cash_flow"] == 118.92
    assert ledger.loc[1, "cash_flow"] == 0.0


def test_reconcile_cash_balances_counts_futures_mark_to_market_as_trade_cash():
    lines = [
        "Futures Statements\n",
        "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance\n",
        "1/13/26,1/13/26,01:00:00,BAL,--,Futures cash balance at the start of business day,--,--,--,\"42,608.50\"\n",
        "1/13/26,1/13/26,15:57:53,TRD,ref,SOLD -1 /ESH26:XCME @7000.00,-1.40,-1.80,--,\"42,605.30\"\n",
        "1/13/26,1/13/26,17:00:00,ADJ,--,/ESH26:XCME mark to market at 7001.75 official settlement price,--,--,-87.50,\"42,517.80\"\n",
        "1/13/26,1/13/26,17:00:00,ADJ,--,/ZBH26:XCBT mark to market at 115'26 official settlement price,--,--,375.00,\"42,892.80\"\n",
        "1/13/26,1/13/26,21:43:27,TRD,ref,BOT +1 /ESH26:XCME @6993.25,-1.40,-1.80,425.00,\"43,314.60\"\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "Exec Time": "1/13/26 15:57:53",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "/ESH26",
            "Type": "FUTURE",
            "Price": 7000.00,
            "trade_pnl": 0.0,
            "fees": 3.2,
            "net_pnl": -3.2,
        },
        {
            "Exec Time": "1/13/26 21:43:27",
            "Spread": "FUTURE",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/ESH26",
            "Type": "FUTURE",
            "Price": 6993.25,
            "trade_pnl": 337.5,
            "fees": 3.2,
            "net_pnl": 334.3,
        },
    ])

    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=0.01,
    )
    row = reconciliation.iloc[0]

    assert row["account_bucket"] == "futures"
    assert row["statement_trade_rows"] == 3
    assert round(row["statement_trade_cash_flow"], 2) == 331.10
    assert round(row["non_trade_cash_flow"], 2) == 375.0
    assert round(row["extracted_net_pnl"], 2) == 331.10
    assert row["status"] == "reconciled"


def test_reconcile_cash_balances_does_not_count_mark_to_market_before_later_open():
    lines = [
        "Futures Statements\n",
        "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance\n",
        "2/4/26,2/4/26,01:00:00,BAL,--,Futures cash balance at the start of business day,--,--,--,\"51,527.78\"\n",
        "2/4/26,2/4/26,09:31:12,TRD,ref,SOLD -3 /MESH26:XCME 1/5 11 FEB 26 /X2CG26:XCME 6690 PUT @10.50,-0.66,-5.40,157.50,\"51,679.22\"\n",
        "2/4/26,2/4/26,17:00:00,ADJ,--,/MESH26:XCME mark to market at 6906.25 official settlement price,--,--,-887.50,\"50,791.72\"\n",
        "2/5/26,2/4/26,22:00:21,TRD,ref,BOT +1 /MESH26:XCME @6895.00,-0.37,-1.80,--,\"50,789.55\"\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "Exec Time": "2/4/26 09:31:12",
            "Spread": "SINGLE",
            "Side": "SELL",
            "Qty": -3,
            "Pos Effect": "TO OPEN",
            "Symbol": "/MESH26 1/5 11 FEB 26",
            "Type": "PUT",
            "Price": 10.50,
            "trade_pnl": 157.5,
            "fees": 6.06,
            "net_pnl": 151.44,
        },
        {
            "Exec Time": "2/4/26 22:00:21",
            "Spread": "FUTURE",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO OPEN",
            "Symbol": "/MESH26",
            "Type": "FUTURE",
            "Price": 6895.00,
            "trade_pnl": 0.0,
            "fees": 2.17,
            "net_pnl": -2.17,
        },
    ])

    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=0.01,
    )
    row = reconciliation.iloc[0]

    assert row["statement_trade_rows"] == 2
    assert round(row["statement_trade_cash_flow"], 2) == 149.27
    assert round(row["non_trade_cash_flow"], 2) == -887.50
    assert round(row["extracted_net_pnl"], 2) == 149.27
    assert row["status"] == "reconciled"


def test_parse_cash_ledger_does_not_label_forex_as_futures_after_empty_futures_section():
    lines = [
        "Futures Statements\n",
        "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance\n",
        "\n",
        "Forex Statements\n",
        ",Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n",
        ",5/28/26,01:00:00,BAL,--,Cash balance at the start of the business day 28.05 CST.,--,--,--,$689.35\n",
        "Account Trade History\n",
    ]

    ledger = parse_cash_ledger(lines)

    assert ledger["account_bucket"].tolist() == ["forex"]
    assert ledger.loc[0, "balance"] == 689.35


def test_start_date_filter_removes_old_trade_rows_after_filling_exec_times():
    df = pd.DataFrame([
        {
            "Exec Time": "12/31/25 10:00:00",
            "Symbol": "SPX",
        },
        {
            "Exec Time": "",
            "Symbol": "SPX",
        },
        {
            "Exec Time": "1/2/26 10:00:00",
            "Symbol": "XSP",
        },
    ])

    filtered = filter_by_exec_date(
        fill_missing_execution_times(df),
        parse_filter_date("2026-01-01", "--start-date"),
    )

    assert filtered["Symbol"].tolist() == ["XSP"]


def test_start_date_filter_removes_old_cash_ledger_rows():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "12/31/25,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "1/2/26,10:00:00,TRD,,SOLD XSP,-1.00,-2.00,50.00,1144.00\n",
        "Account Trade History\n",
    ]
    ledger = parse_cash_ledger(lines)

    filtered = filter_cash_ledger_by_date(
        ledger,
        parse_filter_date("2026-01-01", "--start-date"),
    )

    assert filtered["description"].tolist() == ["SOLD XSP"]


def test_invalid_auto_strategy_duplicate_drops_when_reviewed_trade_exists():
    trades = pd.DataFrame([
        {
            "Exec Time": "2/23/26 10:00:00",
            "Symbol": "SPX",
            "Exp": "2/23/26",
            "Strike": 6700,
            "Type": "PUT",
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
        },
        {
            "Exec Time": "2/23/26 10:00:00",
            "Symbol": "SPX",
            "Exp": "2/23/26",
            "Strike": 6700,
            "Type": "PUT",
            "Strategy_Name": "SPX Put Vertical Credit",
        },
        {
            "Exec Time": "6/3/26 10:00:00",
            "Symbol": "SPX",
            "Exp": "6/3/26",
            "Strike": 6800,
            "Type": "PUT",
            "Strategy_Name": "SPX Put Vertical Credit",
        },
    ])

    cleaned = drop_invalid_strategy_duplicates(trades)

    assert cleaned["Strategy_Name"].tolist() == [
        "Opt026-60m0DTE-PutSpread",
        "",
    ]
    assert cleaned["Exec Time"].tolist() == [
        "2/23/26 10:00:00",
        "6/3/26 10:00:00",
    ]


def test_cash_reconciliation_start_date_ignores_old_statement_rows():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "12/31/25,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "1/2/26,10:00:00,TRD,,SOLD XSP,-1.00,-2.00,50.00,1144.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "Exec Time": "1/2/26 10:00:00",
            "Spread": "SINGLE",
            "Symbol": "XSP",
            "Type": "PUT",
            "net_pnl": 47.0,
        },
    ])

    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=1.0,
        start_date=parse_filter_date("2026-01-01", "--start-date"),
    )

    assert reconciliation["date"].tolist() == ["2026-01-02"]


def test_cash_reconciliation_date_window_ignores_ledger_before_trade_extract():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/5/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "5/28/26,11:00:00,TRD,,SOLD XSP,-1.00,-2.00,50.00,1144.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "Exec Time": "5/28/26 11:00:00",
            "Spread": "SINGLE",
            "Symbol": "XSP",
            "Type": "PUT",
            "net_pnl": 47.0,
        },
    ])

    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=1.0,
        start_date=parse_filter_date("2026-05-28", "--start-date"),
        end_date=parse_filter_date("2026-05-28", "--end-date"),
    )

    assert reconciliation["date"].tolist() == ["2026-05-28"]
    assert reconciliation.loc[0, "status"] == "reconciled"


def test_reviewed_reconciliation_group_filter_hides_matching_group():
    groups = pd.DataFrame([
        {
            "date": "2026-05-28",
            "account_bucket": "cash",
            "statement_trade_rows": 4,
            "extracted_trade_count": 6,
            "statement_trade_cash_flow": 1858.12,
            "extracted_net_pnl": 1850.0,
            "unreconciled_delta": -8.12,
        },
        {
            "date": "2026-05-29",
            "account_bucket": "cash",
            "statement_trade_rows": 14,
            "extracted_trade_count": 11,
            "statement_trade_cash_flow": 9455.90,
            "extracted_net_pnl": 9458.12,
            "unreconciled_delta": 2.22,
        },
    ])
    reviews = groups.iloc[[0]].copy()
    reviews["review_status"] = "reviewed_no_auto_correction"

    filtered = filter_reviewed_reconciliation_groups(
        groups,
        reviews,
    )

    assert filtered["date"].tolist() == ["2026-05-29"]
    assert group_review_key(groups.iloc[0]) == group_review_key(reviews.iloc[0])


def test_reviewed_reconciliation_groups_do_not_fail_strict_mode():
    reconciliation = pd.DataFrame([
        {
            "date": "2026-06-01",
            "account_bucket": "cash",
            "statement_trade_rows": 2,
            "statement_trade_cash_flow": 100.0,
            "extracted_trade_count": 2,
            "extracted_net_pnl": 98.0,
            "unreconciled_delta": -2.0,
            "status": "unreconciled",
        },
        {
            "date": "2026-06-02",
            "account_bucket": "cash",
            "statement_trade_rows": 1,
            "statement_trade_cash_flow": 50.0,
            "extracted_trade_count": 1,
            "extracted_net_pnl": 40.0,
            "unreconciled_delta": -10.0,
            "status": "unreconciled",
        },
    ])
    reviews = pd.DataFrame([
        {
            "date": "2026-06-01",
            "account_bucket": "cash",
            "statement_trade_rows": 2,
            "extracted_trade_count": 2,
            "statement_trade_cash_flow": 100.0,
            "extracted_net_pnl": 98.0,
            "unreconciled_delta": -2.0,
            "review_status": "reviewed_no_auto_correction",
        }
    ])

    annotated = annotate_cash_reconciliation_reviews(
        reconciliation,
        reviews,
    )
    summary = summarize_cash_reconciliation(annotated)
    failures = strict_reconciliation_failures(annotated)

    assert annotated["review_state"].tolist() == [
        "reviewed_unreconciled",
        "unreviewed_unreconciled",
    ]
    assert summary.loc[0, "reviewed_unreconciled_groups"] == 1
    assert summary.loc[0, "unreviewed_unreconciled_groups"] == 1
    assert failures["date"].tolist() == ["2026-06-02"]


def test_saved_approval_keys_do_not_match_renumbered_candidates():
    saved = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 10,
            "date": "2026-06-02",
            "account_bucket": "cash",
            "event_sequence": 1,
            "event_leg_sequence": 1,
            "ledger_timestamp": "2026-06-02 10:00:00",
            "ledger_description": "SOLD XSP",
            "corrected_net_pnl": 97.0,
        },
    ])
    candidates = pd.DataFrame([
        {
            "statement_file": "2026-06-06-AccountStatement.csv",
            "statement_trade_row": 200,
            "date": "2026-06-02",
            "account_bucket": "cash",
            "event_sequence": 3,
            "event_leg_sequence": 1,
            "ledger_timestamp": "2026-06-02 10:00:00",
            "ledger_description": "SOLD XSP",
            "corrected_net_pnl": 97.0,
        },
    ])

    current = current_statement_approved_corrections(
        candidates,
        saved,
    )
    combined = combine_cash_trade_corrections(
        saved,
        current,
    )

    assert current.empty
    assert combined["statement_file"].tolist() == [
        "trades.csv",
    ]


def test_cash_reconciliation_counts_trades_in_unreconciled_groups():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/1/26,01:00:00,BAL,,Cash balance,,,,1000.00\n",
        "1/1/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "1/2/26,01:00:00,BAL,,Cash balance,,,,1097.00\n",
        "1/2/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,50.00,1144.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "Exec Time": "1/1/26 10:00:00",
                "Spread": "SINGLE",
                "Symbol": "SPX",
                "Type": "PUT",
                "net_pnl": 97.0,
            },
            {
                "Exec Time": "1/2/26 10:00:00",
                "Spread": "SINGLE",
                "Symbol": "SPX",
                "Type": "PUT",
                "net_pnl": 40.0,
            },
        ]
    )

    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=1.0,
    )
    summary = summarize_cash_reconciliation(reconciliation)

    assert summary.loc[0, "unreconciled_groups"] == 1
    assert summary.loc[0, "trades_in_unreconciled_groups"] == 1
    failed = reconciliation[
        reconciliation["status"] == "unreconciled"
    ].iloc[0]
    assert failed["date"] == "2026-01-02"


def test_fee_correction_suggestions_prefer_cash_ledger_for_fee_mismatch():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/1/26,01:00:00,BAL,,Cash balance,,,,1000.00\n",
        "1/1/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,100.00,1097.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "Exec Time": "1/1/26 10:00:00",
                "Spread": "SINGLE",
                "Side": "SELL",
                "Qty": -1,
                "Symbol": "SPX",
                "Type": "PUT",
                "fees": 1.0,
                "trade_pnl": 100.0,
                "net_pnl": 99.0,
            },
        ]
    )

    suggestions = build_fee_correction_suggestions(
        parse_cash_ledger(lines),
        trades,
        tolerance=1.0,
    )

    assert len(suggestions) == 1
    assert suggestions.loc[0, "suggestion_status"] == "fee_only"
    assert suggestions.loc[0, "safe_to_apply"]
    assert suggestions.loc[0, "broker_implied_fees"] == 3.0
    assert suggestions.loc[0, "suggested_net_pnl"] == 97.0


def test_cash_trade_corrections_apply_cash_ledger_to_multileg_event():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/23/26,01:00:00,BAL,,Cash balance,,,,1000.00\n",
        "1/23/26,13:13:45,TRD,,BOT +1 VERTICAL SPX @-3.25,-1.14,-1.04,325.00,1322.82\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "Exec Time": "1/23/26 13:13:45",
                "Spread": "CUSTOM",
                "Side": "SELL",
                "Qty": -1,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Type": "PUT",
                "fees": 1.25,
                "trade_pnl": -325.0,
                "net_pnl": -326.25,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 2,
                "Exec Time": "1/23/26 13:13:45",
                "Spread": "",
                "Side": "BUY",
                "Qty": 1,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Type": "PUT",
                "fees": 1.25,
                "trade_pnl": 0.0,
                "net_pnl": -1.25,
            },
        ]
    )

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )

    assert len(corrections) == 2
    assert corrected.loc[0, "trade_pnl"] == 325.0
    assert round(corrected.loc[0, "fees"], 2) == 2.18
    assert corrected.loc[0, "net_pnl"] == 322.82
    assert corrected.loc[1, "trade_pnl"] == 0.0
    assert corrected.loc[1, "fees"] == 0.0
    assert corrected.loc[1, "net_pnl"] == 0.0
    assert round(corrected["net_pnl"].sum(), 2) == 322.82


def test_cash_trade_corrections_preserve_split_stock_fill_trade_pnl():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "6/12/26,01:00:00,BAL,,Cash balance,,,,1000.00\n",
        "6/12/26,12:01:48,TRD,,BOT +7 SPCX @161.46 | BOT +93 SPCX @161.46,,,-16146.00,-15146.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 122,
                "Exec Time": "6/12/26 12:01:48",
                "Spread": "STOCK",
                "Side": "BUY",
                "Qty": 7,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPCX",
                "Type": "STOCK",
                "fees": 0.0,
                "trade_pnl": -1130.22,
                "net_pnl": -1130.22,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 123,
                "Exec Time": "6/12/26 12:01:48",
                "Spread": "STOCK",
                "Side": "BUY",
                "Qty": 93,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPCX",
                "Type": "STOCK",
                "fees": 0.0,
                "trade_pnl": -15015.78,
                "net_pnl": -15015.78,
            },
        ]
    )

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )

    assert len(corrections) == 2
    assert corrected.loc[0, "trade_pnl"] == -1130.22
    assert corrected.loc[1, "trade_pnl"] == -15015.78
    assert corrected["net_pnl"].sum() == -16146.0


def test_saved_aggregate_stock_correction_does_not_overwrite_split_row_pnl():
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 122,
                "Exec Time": "6/12/26 12:01:48",
                "Spread": "STOCK",
                "Side": "BUY",
                "Qty": 7,
                "Symbol": "SPCX",
                "trade_pnl": -1130.22,
                "fees": 0.0,
                "net_pnl": -1130.22,
            },
        ]
    )
    stale_correction = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 122,
                "correction_status": "cash_ledger_applied",
                "correction_source": "cash_ledger",
                "ledger_description": (
                    "BOT +7 SPCX @161.46 | BOT +93 SPCX @161.46"
                ),
                "original_trade_pnl": -1130.22,
                "corrected_trade_pnl": -16146.0,
                "corrected_fees": 0.0,
                "corrected_net_pnl": -16146.0,
                "ledger_cash_flow": -16146.0,
            },
        ]
    )

    corrected = apply_cash_trade_corrections(
        trades,
        stale_correction,
    )

    assert corrected.loc[0, "cash_correction_applied"]
    assert corrected.loc[0, "trade_pnl"] == -1130.22
    assert corrected.loc[0, "net_pnl"] == -1130.22


def test_cash_trade_corrections_preserve_futures_trade_pnl():
    lines = [
        "Futures Statements\n",
        ",2/3/26,11:16:23,TRD,ref,SOLD -1 /MGCJ26:XCEC @4985.00,-0.62,-1.80,3324.00,0\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 259,
            "Exec Time": "2/3/26 11:16:23",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MGCJ26",
            "Type": "FUTURE",
            "Price": 4985.00,
            "fees": 2.75,
            "trade_pnl": -1010.00,
            "net_pnl": -1012.75,
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

    assert corrections.loc[0, "corrected_trade_pnl"] == -1010.00
    assert round(corrections.loc[0, "corrected_fees"], 2) == 2.42
    assert round(corrections.loc[0, "corrected_net_pnl"], 2) == -1012.42
    assert corrected.loc[0, "trade_pnl"] == -1010.00
    assert round(corrected.loc[0, "fees"], 2) == 2.42
    assert round(corrected.loc[0, "net_pnl"], 2) == -1012.42


def test_saved_futures_cash_correction_does_not_overwrite_trade_pnl():
    trades = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 259,
            "Exec Time": "2/3/26 11:16:23",
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MGCJ26",
            "Type": "FUTURE",
            "Price": 4985.00,
            "fees": 2.75,
            "trade_pnl": -1010.00,
            "net_pnl": -1012.75,
        },
    ])
    stale_correction = pd.DataFrame([
        {
            "statement_file": "trades.csv",
            "statement_trade_row": 259,
            "correction_status": "cash_ledger_applied",
            "correction_source": "cash_ledger",
            "ledger_description": "SOLD -1 /MGCJ26:XCEC @4985.00",
            "ledger_cash_flow": 3321.58,
            "original_trade_pnl": -1010.00,
            "corrected_trade_pnl": 3324.00,
            "corrected_fees": 2.42,
            "corrected_net_pnl": 3321.58,
        },
    ])

    corrected = apply_cash_trade_corrections(
        trades,
        stale_correction,
    )

    assert corrected.loc[0, "cash_correction_applied"]
    assert corrected.loc[0, "trade_pnl"] == -1010.00
    assert corrected.loc[0, "fees"] == 2.42
    assert corrected.loc[0, "net_pnl"] == -1012.42


def test_cash_trade_corrections_match_exact_event_when_day_counts_differ():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "6/4/26,10:55:00,TRD,,SOLD OTHER SYMBOL,-1.00,-1.00,10.00,1008.00\n",
        "6/4/26,11:00:55,TRD,,BOT +5 VERTICAL SPX 100 (Weeklys) 4 JUN 26 7535/7515 PUT @1.95,-5.00,-5.92,-975.00,27.08\n",
        "6/4/26,11:17:45,TRD,,SOLD -5 VERTICAL SPX 100 (Weeklys) 4 JUN 26 7535/7515 PUT @0.80,-5.00,-5.47,400.00,416.61\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "Exec Time": "6/4/26 11:00:55",
                "Spread": "VERTICAL",
                "Side": "BUY",
                "Qty": 5,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Exp": "4 JUN 26",
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": -975.0,
                "net_pnl": -981.25,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 2,
                "Exec Time": "6/4/26 11:00:55",
                "Spread": "",
                "Side": "SELL",
                "Qty": -5,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Exp": "4 JUN 26",
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": 0.0,
                "net_pnl": -6.25,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 3,
                "Exec Time": "6/4/26 11:17:45",
                "Spread": "VERTICAL",
                "Side": "SELL",
                "Qty": -5,
                "Pos Effect": "TO CLOSE",
                "Symbol": "SPX",
                "Exp": "4 JUN 26",
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": 400.0,
                "net_pnl": 393.75,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 4,
                "Exec Time": "6/4/26 11:17:45",
                "Spread": "",
                "Side": "BUY",
                "Qty": 5,
                "Pos Effect": "TO CLOSE",
                "Symbol": "SPX",
                "Exp": "4 JUN 26",
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": 0.0,
                "net_pnl": -6.25,
            },
        ]
    )

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )

    assert len(corrections) == 4
    assert corrected.loc[0, "trade_pnl"] == -975.0
    assert round(corrected.loc[0, "fees"], 2) == 10.92
    assert round(corrected.loc[0, "net_pnl"], 2) == -985.92
    assert corrected.loc[1, "net_pnl"] == 0.0
    assert round(corrected.loc[2, "fees"], 2) == 10.47
    assert round(corrected.loc[2, "net_pnl"], 2) == 389.53
    assert corrected.loc[3, "net_pnl"] == 0.0


def test_cash_trade_corrections_do_not_aggregate_identical_partial_closes():
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "6/8/26,11:13:44,TRD,,SOLD -5 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @2.35 CBOE,-5.72,-5.20,1175.00,81537.87\n",
        "6/8/26,12:38:28,TRD,,BOT +1 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @7.10 CBOE,-1.14,-1.04,-710.00,80825.69\n",
        "6/8/26,12:38:28,TRD,,BOT +1 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @7.10 CBOE,-1.14,-1.04,-710.00,80113.51\n",
        "6/8/26,12:38:28,TRD,,BOT +1 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @7.10 CBOE,-1.16,-1.04,-710.00,79401.31\n",
        "6/8/26,12:38:28,TRD,,BOT +1 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @7.10 CBOE,-1.14,-1.04,-710.00,78689.13\n",
        "6/8/26,12:38:28,TRD,,BOT +1 VERTICAL SPX 100 (Weeklys) 8 JUN 26 7430/7410 PUT @7.10 CBOE,-1.14,-1.04,-710.00,77976.95\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 69,
                "Exec Time": "6/8/26 11:13:44",
                "Spread": "VERTICAL",
                "Side": "SELL",
                "Qty": -5,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Exp": "8 JUN 26",
                "Strike": 7430,
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": 1175.0,
                "net_pnl": 1168.75,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 70,
                "Exec Time": "6/8/26 11:13:44",
                "Spread": "",
                "Side": "BUY",
                "Qty": 5,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Exp": "8 JUN 26",
                "Strike": 7410,
                "Type": "PUT",
                "fees": 6.25,
                "trade_pnl": 0.0,
                "net_pnl": -6.25,
            },
        ]
    )

    for offset in range(5):
        trades = pd.concat(
            [
                trades,
                pd.DataFrame([
                    {
                        "statement_file": "trades.csv",
                        "statement_trade_row": 71 + offset * 2,
                        "Exec Time": "6/8/26 12:38:28",
                        "Spread": "VERTICAL",
                        "Side": "BUY",
                        "Qty": 1,
                        "Pos Effect": "TO CLOSE",
                        "Symbol": "SPX",
                        "Exp": "8 JUN 26",
                        "Strike": 7430,
                        "Type": "PUT",
                        "fees": 1.25,
                        "trade_pnl": -710.0,
                        "net_pnl": -711.25,
                    },
                    {
                        "statement_file": "trades.csv",
                        "statement_trade_row": 72 + offset * 2,
                        "Exec Time": "6/8/26 12:38:28",
                        "Spread": "",
                        "Side": "SELL",
                        "Qty": -1,
                        "Pos Effect": "TO CLOSE",
                        "Symbol": "SPX",
                        "Exp": "8 JUN 26",
                        "Strike": 7410,
                        "Type": "PUT",
                        "fees": 1.25,
                        "trade_pnl": 0.0,
                        "net_pnl": -1.25,
                    },
                ]),
            ],
            ignore_index=True,
        )

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )
    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )
    corrected_main_closes = corrected[
        corrected["statement_trade_row"].isin([71, 73, 75, 77, 79])
    ]

    assert len(corrections) == 12
    assert not corrections["corrected_trade_pnl"].eq(-3550.0).any()
    assert corrected_main_closes["trade_pnl"].tolist() == [-710.0] * 5
    assert [
        round(value, 2)
        for value in corrected_main_closes["fees"].tolist()
    ] == [2.18, 2.18, 2.20, 2.18, 2.18]
    assert round(corrected["net_pnl"].sum(), 2) == -2396.84
    assert len({
        correction_key(row)
        for row in corrections.to_dict("records")
    }) == len(corrections)


def test_cash_trade_corrections_keep_same_second_futures_fills_separate():
    lines = [
        "Futures Statements\n",
        "Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Commissions & Fees,Amount,Balance\n",
        "6/7/26,6/7/26,03:56:55,TRD,,SOLD -1 /MBTM26:XCME @62545.00,--,-2.97,203.00,1200.00\n",
        "6/7/26,6/7/26,03:56:55,TRD,,SOLD -1 /MBTM26:XCME @62555.00,--,-2.97,204.00,1401.03\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "Exec Time": "6/7/26 03:56:55",
                "Spread": "FUTURE",
                "Side": "SELL",
                "Qty": -1,
                "Pos Effect": "TO CLOSE",
                "Symbol": "/MBTM26",
                "Price": 62545.0,
                "fees": 3.0,
                "trade_pnl": 218.5,
                "net_pnl": 215.5,
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 2,
                "Exec Time": "6/7/26 03:56:55",
                "Spread": "FUTURE",
                "Side": "SELL",
                "Qty": -1,
                "Pos Effect": "TO CLOSE",
                "Symbol": "/MBTM26",
                "Price": 62555.0,
                "fees": 3.0,
                "trade_pnl": 227.0,
                "net_pnl": 224.0,
            },
        ]
    )

    corrections = build_cash_trade_corrections(
        parse_cash_ledger(lines),
        trades,
    )

    assert corrections["corrected_trade_pnl"].tolist() == [218.5, 227.0]
    assert [
        round(value, 2)
        for value in corrections["corrected_net_pnl"].tolist()
    ] == [215.53, 224.03]


def test_auto_approved_corrections_include_futures_and_opt026_only():
    candidates = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "date": "2026-06-04",
                "account_bucket": "cash",
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 2,
                "date": "2026-06-07",
                "account_bucket": "futures",
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 3,
                "date": "2026-06-04",
                "account_bucket": "cash",
            },
        ]
    ).reindex(columns=[
        "statement_file",
        "statement_trade_row",
        "date",
        "account_bucket",
        "event_sequence",
        "event_leg_sequence",
        "correction_status",
        "correction_source",
        "ledger_timestamp",
        "ledger_description",
        "ledger_amount",
        "ledger_cash_flow",
        "ledger_misc_fees",
        "ledger_commissions_fees",
        "original_trade_pnl",
        "original_fees",
        "original_net_pnl",
        "corrected_trade_pnl",
        "corrected_fees",
        "corrected_net_pnl",
    ])
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 2,
                "Strategy_Name": "Discretionary",
            },
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 3,
                "Strategy_Name": "Discretionary",
            },
        ]
    )

    auto_approved = auto_approved_cash_trade_corrections(
        candidates,
        trades,
    )

    assert auto_approved["statement_trade_row"].tolist() == [1, 2]


def test_cash_trade_corrections_coerce_existing_applied_column_to_bool():
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "trade_pnl": 10.0,
                "fees": 1.0,
                "net_pnl": 9.0,
                "cash_correction_applied": 0.0,
            },
        ]
    )
    corrections = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "correction_status": "cash_ledger_applied",
                "correction_source": "cash_ledger",
                "corrected_trade_pnl": 10.0,
                "corrected_fees": 2.0,
                "corrected_net_pnl": 8.0,
                "ledger_cash_flow": 8.0,
            },
        ]
    )

    corrected = apply_cash_trade_corrections(
        trades,
        corrections,
    )

    assert corrected.loc[0, "cash_correction_applied"]
    assert corrected.loc[0, "net_pnl"] == 8.0


def test_write_cash_reconciliation_dashboard_shows_trade_and_ledger_sides(tmp_path):
    lines = [
        "Cash Balance\n",
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n",
        "1/2/26,01:00:00,BAL,,Cash balance,,,,1097.00\n",
        "1/2/26,10:00:00,TRD,,SOLD SPX,-1.00,-2.00,50.00,1144.00\n",
        "Account Trade History\n",
    ]
    trades = pd.DataFrame(
        [
            {
                "statement_file": "trades.csv",
                "statement_trade_row": 1,
                "Exec Time": "1/2/26 10:00:00",
                "Spread": "SINGLE",
                "Side": "SELL",
                "Qty": -1,
                "Symbol": "SPX",
                "Type": "PUT",
                "Price": 0.5,
                "Net Price": 0.5,
                "fees": 1.0,
                "trade_pnl": 50.0,
                "net_pnl": 40.0,
                "Order ID": "abc",
            },
        ]
    )
    reconciliation = reconcile_cash_balances(
        lines,
        trades,
        tolerance=1.0,
    )
    output_file = tmp_path / "cash_dashboard.html"
    cash_ledger = parse_cash_ledger(lines)
    correction_candidates = build_cash_trade_corrections(
        cash_ledger,
        trades,
    )

    write_cash_reconciliation_dashboard(
        output_file,
        reconciliation,
        cash_ledger,
        trades,
        correction_candidates=correction_candidates,
    )

    html = output_file.read_text()
    assert "Extracted Trade History + Suggested Cash Adjustment" in html
    assert "Statement Cash Ledger" in html
    assert "Suggested delta" in html
    assert "Approved" in html
    assert "Discrepancy" in html
    assert "Current Net PnL" in html
    assert "Cash Ledger Cash Flow" in html
    assert "Suggested Net PnL" in html
    assert "fee_mismatch" in html
    assert "suggested_cash_net_pnl" in html
    assert "matched_ledger_description" in html
    assert "Mark group reviewed" in html
    assert "reviewed_groups" in html
    assert "reviewed_no_auto_correction" in html
    assert "No automatic correction candidates are available for this group" in html
    assert "Future runs will apply or hide these reviews" in html
    assert "SOLD SPX" in html


def test_write_cash_reconciliation_dashboard_hides_saved_groups(tmp_path):
    reconciliation = pd.DataFrame(
        [
            {
                "date": "2026-01-02",
                "account_bucket": "cash",
                "status": "unreconciled",
                "unreconciled_delta": 10.0,
                "extracted_trade_count": 1,
                "statement_trade_rows": 1,
            },
        ]
    )
    correction = {
        "statement_file": "old-name.csv",
        "statement_trade_row": 10,
        "date": "2026-01-02",
        "account_bucket": "cash",
        "event_sequence": 1,
        "event_leg_sequence": 1,
        "correction_status": "cash_ledger_applied",
        "correction_source": "cash_ledger",
        "ledger_timestamp": "2026-01-02 10:00:00",
        "ledger_description": "SOLD SPX",
        "ledger_amount": 50.0,
        "ledger_cash_flow": 48.0,
        "ledger_misc_fees": -1.0,
        "ledger_commissions_fees": -1.0,
        "original_trade_pnl": 50.0,
        "original_fees": 1.0,
        "original_net_pnl": 49.0,
        "corrected_trade_pnl": 50.0,
        "corrected_fees": 2.0,
        "corrected_net_pnl": 48.0,
    }
    output_file = tmp_path / "cash_dashboard.html"

    write_cash_reconciliation_dashboard(
        output_file,
        reconciliation,
        pd.DataFrame(),
        pd.DataFrame(),
        correction_candidates=pd.DataFrame([correction]),
        approved_corrections=pd.DataFrame([correction]),
    )

    html = output_file.read_text()
    match = re.search(
        r"const DATA = (.*?);\n    let selectedIndex",
        html,
        re.S,
    )
    payload = json.loads(match.group(1))

    assert payload["groups"] == []
    assert len(payload["approvedCorrectionKeys"]) == 1
