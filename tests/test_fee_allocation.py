import pandas as pd

from src.matcher import match_trades


def test_partial_fee_allocation():

    df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 10,
            "price": 100,
            "fees": 1.00,
        },
        {
            "symbol": "AAPL",
            "side": "SELL",
            "qty": 5,
            "price": 110,
            "fees": 1.00,
        },
    ])

    trades = match_trades(df)

    t = trades[0]

    assert t["entry_fees"] == 0.50
