import pandas as pd

from src.loader import load_trades_csv


def test_load_trades_csv(tmp_path):
    csv_content = """Date,Symbol,Side,Qty,Price,Fees
2026-01-01,AAPL,BUY,10,100,1
2026-01-02,AAPL,SELL,10,110,1
"""

    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(csv_content)

    df = load_trades_csv(csv_file)

    assert isinstance(df, pd.DataFrame)

    assert len(df) == 2

    assert list(df.columns) == [
        "date",
        "symbol",
        "side",
        "qty",
        "price",
        "fees",
    ]

def test_dates_are_parsed(tmp_path):
    csv_content = """Date,Symbol,Side,Qty,Price,Fees
2026-01-01,AAPL,BUY,10,100,1
"""

    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(csv_content)

    df = load_trades_csv(csv_file)

    assert pd.api.types.is_datetime64_any_dtype(
        df["date"]
    )
