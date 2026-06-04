import pandas as pd

from extract_trade_history import fill_missing_execution_times


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
