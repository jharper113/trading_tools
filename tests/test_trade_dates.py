import pandas as pd

from src.matcher import match_trades


def test_trade_dates():

    df = pd.DataFrame([
        {
            "date": pd.Timestamp("2026-01-01"),
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 10,
            "price": 100,
            "fees": 1,
            "asset_type": "EQUITY",
        },
        {
            "date": pd.Timestamp("2026-01-03"),
            "symbol": "AAPL",
            "side": "SELL",
            "qty": 10,
            "price": 110,
            "fees": 1,
            "asset_type": "EQUITY",
        },
    ])

    trades = match_trades(df)

    t = trades[0]

    assert t["entry_date"] == pd.Timestamp("2026-01-01")
    assert t["exit_date"] == pd.Timestamp("2026-01-03")
