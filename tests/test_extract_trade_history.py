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
    current_statement_approved_corrections,
    drop_invalid_strategy_duplicates,
    filter_by_exec_date,
    filter_cash_ledger_by_date,
    filter_reviewed_reconciliation_groups,
    fill_missing_execution_times,
    group_review_key,
    parse_filter_date,
    parse_cash_ledger,
    reconcile_cash_balances,
    strict_reconciliation_failures,
    summarize_cash_reconciliation,
    write_cash_reconciliation_dashboard,
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


def test_saved_approval_keys_match_current_statement_candidates():
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

    assert current["statement_file"].tolist() == [
        "2026-06-06-AccountStatement.csv",
    ]
    assert set(combined["statement_file"]) == {
        "trades.csv",
        "2026-06-06-AccountStatement.csv",
    }


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

    assert corrections["corrected_trade_pnl"].tolist() == [203.0, 204.0]
    assert corrections["corrected_net_pnl"].tolist() == [200.03, 201.03]


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

    write_cash_reconciliation_dashboard(
        output_file,
        reconciliation,
        parse_cash_ledger(lines),
        trades,
    )

    html = output_file.read_text()
    assert "Extracted Trade History" in html
    assert "Statement Cash Ledger" in html
    assert "Suggested delta" in html
    assert "Approved" in html
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
    renamed_candidate = {
        **correction,
        "statement_file": "new-name.csv",
        "statement_trade_row": 20,
    }
    output_file = tmp_path / "cash_dashboard.html"

    write_cash_reconciliation_dashboard(
        output_file,
        reconciliation,
        pd.DataFrame(),
        pd.DataFrame(),
        correction_candidates=pd.DataFrame([renamed_candidate]),
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
