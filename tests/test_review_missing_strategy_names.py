import pandas as pd

from review_missing_strategy_names import (
    apply_strategy_updates,
    load_strategy_review_data,
)


def write_master_file(path):
    pd.DataFrame(
        [
            {
                "Strategy_Name": "SPX Put Vertical Credit",
                "Exec Time": "01/01/26 09:30:00",
                "Spread": "VERTICAL",
                "Side": "BUY",
                "Qty": 1,
                "Pos Effect": "TO OPEN",
                "Symbol": "SPX",
                "Exp": "01/02/26",
                "Type": "CALL",
                "Price": "1.00",
                "Net Price": "1.00",
            },
            {
                "Strategy_Name": "",
                "Exec Time": "01/02/26 09:30:00",
                "Spread": "SINGLE",
                "Side": "SELL",
                "Qty": 2,
                "Pos Effect": "TO CLOSE",
                "Symbol": "ES",
                "Exp": "",
                "Type": "FUTURE",
                "Price": "2.00",
                "Net Price": "4.00",
            },
            {
                "Strategy_Name": None,
                "Exec Time": "01/03/26 09:30:00",
                "Spread": "SINGLE",
                "Side": "BUY",
                "Qty": 3,
                "Pos Effect": "TO OPEN",
                "Symbol": "AAPL",
                "Exp": "01/16/26",
                "Type": "PUT",
                "Price": "3.00",
                "Net Price": "9.00",
            },
        ]
    ).to_csv(path, index=False)


def test_load_strategy_review_data_filters_missing_rows(tmp_path):
    input_file = tmp_path / "master_cleaned_tos_data.csv"
    write_master_file(input_file)

    data = load_strategy_review_data(input_file)

    assert data["total_rows"] == 3
    assert data["missing_count"] == 3
    assert data["strategies"] == [
        "Discretionary",
        "Opt021-1DTE-ShortStraddle-Mon-Thurs",
        "Opt025-TradeBusters-7DTE-Naked-Puts",
        "Opt026-60m0DTE-PutSpread",
    ]
    assert [row["row_id"] for row in data["rows"]] == [0, 1, 2]
    assert data["rows"][0]["Symbol"] == "SPX"


def test_apply_strategy_updates_saves_strategy_names(tmp_path):
    input_file = tmp_path / "master_cleaned_tos_data.csv"
    write_master_file(input_file)

    result = apply_strategy_updates(
        input_file,
        [
            {"row_id": 1, "strategy_name": "  New Strategy  "},
            {"row_id": 2, "strategy_name": "Discretionary"},
        ],
    )
    df = pd.read_csv(input_file)

    assert result["saved_rows"] == 2
    assert result["remaining_missing"] == 2
    assert df.loc[1, "Strategy_Name"] == "New Strategy"
    assert df.loc[2, "Strategy_Name"] == "Discretionary"


def test_extra_valid_strategy_names_are_not_reviewed(tmp_path):
    input_file = tmp_path / "master_cleaned_tos_data.csv"
    write_master_file(input_file)

    data = load_strategy_review_data(
        input_file,
        extra_strategy_names=["SPX Put Vertical Credit"],
    )

    assert data["missing_count"] == 2
    assert [row["row_id"] for row in data["rows"]] == [1, 2]
    assert "SPX Put Vertical Credit" in data["strategies"]
