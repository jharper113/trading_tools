from src.matcher import match_trades
import pandas as pd


def test_simple_fifo_match():
    df = pd.DataFrame([
        {"symbol": "AAPL", "side": "BUY", "qty": 10, "price": 100},
        {"symbol": "AAPL", "side": "SELL", "qty": 10, "price": 110},
    ])

    trades = match_trades(df)

    assert len(trades) == 1

    t = trades[0]
    assert t["entry_price"] == 100
    assert t["exit_price"] == 110
    assert t["qty"] == 10
