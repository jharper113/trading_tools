import pandas as pd


def normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["side"] = (
        df["side"]
        .str.upper()
        .str.replace("_TO_OPEN", "", regex=False)
        .str.replace("_TO_CLOSE", "", regex=False)
    )

    return df
