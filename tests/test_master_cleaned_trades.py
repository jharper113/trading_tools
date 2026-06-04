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
            "Strategy_Name": "Custom Strategy",
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
        "Custom Strategy",
    ]


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
