import pandas as pd
from src.normalizer import normalize_trades


def test_normalize_tos_side():
    df = pd.DataFrame({
        "side": [
            "BUY_TO_OPEN",
            "SELL_TO_CLOSE",
            "SELL_TO_OPEN",
            "BUY_TO_CLOSE",
        ]
    })

    out = normalize_trades(df)

    assert list(out["side"]) == ["BUY", "SELL", "SELL", "BUY"]
