import pandas as pd

from extract_trade_history import update_master_cleaned_trades


def make_trade(exec_time, side, price, order_id):
    return {
        "Exec Time": exec_time,
        "Spread": "SINGLE",
        "Side": side,
        "Qty": 1,
        "Pos Effect": "TO OPEN",
        "Symbol": "AAPL",
        "Exp": "",
        "Strike": "",
        "Type": "",
        "Price": price,
        "Net Price": "",
        "Order Type": "MKT",
        "Order ID": order_id,
        "Exp.1": "",
        "Settlement Date": "",
    }


def test_update_master_cleaned_trades_appends_without_duplicates(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    first_run = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
    ])
    first_master = update_master_cleaned_trades(
        first_run,
        master_file,
        starting_equity,
    )
    first_master.to_csv(
        master_file,
        index=False,
    )

    second_run = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            110,
            "order-2",
        ),
    ])
    second_master = update_master_cleaned_trades(
        second_run,
        master_file,
        starting_equity,
    )

    assert len(second_master) == 2
    assert second_master["Order ID"].tolist() == [
        "order-1",
        "order-2",
    ]
    assert second_master["cumulative_pnl"].tolist() == [
        -100.0,
        10.0,
    ]
    assert second_master["ending_equity"].tolist() == [
        900.0,
        1010.0,
    ]


def test_update_master_cleaned_trades_is_idempotent_for_same_run(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000
    source_rows = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            110,
            "order-2",
        ),
    ])

    first_master = update_master_cleaned_trades(
        source_rows.copy(),
        master_file,
        starting_equity,
    )
    first_master.to_csv(
        master_file,
        index=False,
    )
    second_master = update_master_cleaned_trades(
        source_rows.copy(),
        master_file,
        starting_equity,
    )

    pd.testing.assert_frame_equal(
        first_master.reset_index(drop=True),
        second_master.reset_index(drop=True),
        check_dtype=False,
    )


def test_update_master_cleaned_trades_replaces_legacy_blank_times(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        make_trade(
            "",
            "BUY",
            100,
            "order-1",
        ),
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
    ])

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert len(updated) == 1
    assert updated["Exec Time"].tolist() == [
        "1/1/26 09:30:00",
    ]


def test_update_master_cleaned_trades_preserves_existing_strategy_name(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "1/1/26 09:30:00",
                "BUY",
                100,
                "order-1",
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
    ])

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert updated["Strategy_Name"].tolist() == [
        "Opt026-60m0DTE-PutSpread",
    ]


def test_update_master_cleaned_trades_clears_unapproved_strategy_name(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "1/1/26 09:30:00",
                "BUY",
                100,
                "order-1",
            ),
            "Strategy_Name": "SPX Put Vertical Credit",
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        make_trade(
            "1/1/26 09:30:00",
            "BUY",
            100,
            "order-1",
        ),
    ])

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert updated["Strategy_Name"].tolist() == [""]


def test_update_master_cleaned_trades_clears_stale_cash_correction_metadata(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "1/23/26 13:13:45",
                "SELL",
                4.97,
                "order-1",
            ),
            "Qty": -1,
            "cash_correction_applied": True,
            "cash_correction_status": "cash_ledger_applied",
            "cash_correction_source": "cash_ledger",
            "statement_cash_flow": 322.82,
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    updated = update_master_cleaned_trades(
        pd.DataFrame(),
        master_file,
        starting_equity,
    )

    assert updated["cash_correction_applied"].tolist() == [False]
    assert updated["cash_correction_status"].tolist() == [""]
    assert updated["cash_correction_source"].tolist() == [""]
    assert updated["statement_cash_flow"].tolist() == [""]


def test_update_master_cleaned_trades_dedupes_numeric_format_changes(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        make_trade(
            "1/15/26 09:32:15",
            "BUY",
            7007.0,
            "",
        ),
    ])
    old_master.loc[0, "Qty"] = 1.0
    old_master.loc[0, "Strike"] = float("nan")
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        make_trade(
            "1/15/26 09:32:15",
            "BUY",
            "7007",
            "",
        ),
    ])
    new_run["Qty"] = new_run["Qty"].astype(object)
    new_run.loc[0, "Qty"] = "1"
    new_run.loc[0, "Strike"] = ""

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert len(updated) == 1
    assert updated["Exec Time"].tolist() == [
        "1/15/26 09:32:15",
    ]


def test_update_master_cleaned_trades_preserves_repeated_statement_fills(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "2/26/26 11:26:31",
                "SELL",
                67495,
                "",
            ),
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MBTG26",
            "Type": "FUTURE",
            "statement_file": "",
            "statement_trade_row": "",
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    first_fill = {
        **make_trade(
            "2/26/26 11:26:31",
            "SELL",
            67495,
            "",
        ),
        "Qty": -1,
        "Pos Effect": "TO CLOSE",
        "Symbol": "/MBTG26",
        "Type": "FUTURE",
        "statement_file": "statement.csv",
        "statement_trade_row": 4992,
    }
    second_fill = {
        **first_fill,
        "statement_trade_row": 4993,
    }

    updated = update_master_cleaned_trades(
        pd.DataFrame([
            first_fill,
            second_fill,
        ]),
        master_file,
        starting_equity,
    )

    fills = updated[
        updated["Symbol"] == "/MBTG26"
    ]

    assert len(fills) == 2
    assert fills["statement_trade_row"].tolist() == [
        4992,
        4993,
    ]


def test_update_master_cleaned_trades_replaces_stale_statement_row_for_same_trade(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "5/29/26 14:11:06",
                "SELL",
                6.305,
                "",
            ),
            "Qty": -2000,
            "Spread": "STOCK",
            "Pos Effect": "TO CLOSE",
            "Symbol": "SPCE",
            "statement_file": "2026-06-06-AccountStatement.csv",
            "statement_trade_row": 1758,
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        {
            **make_trade(
                "5/29/26 14:11:06",
                "SELL",
                "6.305",
                "",
            ),
            "Qty": "-2000",
            "Spread": "STOCK",
            "Pos Effect": "TO CLOSE",
            "Symbol": "SPCE",
            "statement_file": "2026-06-06-AccountStatement.csv",
            "statement_trade_row": 26,
        },
    ])

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert len(updated) == 1
    assert updated["statement_trade_row"].tolist() == [26]


def test_update_master_cleaned_trades_preserves_strategy_when_statement_row_added(tmp_path):
    master_file = tmp_path / "master_cleaned_tos_data.csv"
    starting_equity = 1000

    old_master = pd.DataFrame([
        {
            **make_trade(
                "2/26/26 11:26:31",
                "SELL",
                67495,
                "",
            ),
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MBTG26",
            "Type": "FUTURE",
            "statement_file": "",
            "statement_trade_row": "",
            "Strategy_Name": "Discretionary",
        },
    ])
    old_master.to_csv(
        master_file,
        index=False,
    )

    new_run = pd.DataFrame([
        {
            **make_trade(
                "2/26/26 11:26:31",
                "SELL",
                67495,
                "",
            ),
            "Qty": -1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/MBTG26",
            "Type": "FUTURE",
            "statement_file": "statement.csv",
            "statement_trade_row": 4992,
        },
    ])

    updated = update_master_cleaned_trades(
        new_run,
        master_file,
        starting_equity,
    )

    assert updated["Strategy_Name"].tolist() == [
        "Discretionary",
    ]
    assert updated["statement_trade_row"].tolist() == [
        4992,
    ]
