import pandas as pd

REQUIRED_COLUMNS = {
    "date",
    "symbol",
    "side",
    "qty",
    "price",
    "fees",
}


def load_trades_csv(path: str) -> pd.DataFrame:
    """
    Load and normalize a thinkorswim trade history CSV.
    """

    df = pd.read_csv(path)

    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    # Validate schema
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Type normalization
    df["date"] = pd.to_datetime(df["date"], errors="raise")

    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["side"] = df["side"].astype(str).str.upper().str.strip()

    df["qty"] = pd.to_numeric(df["qty"], errors="raise")
    df["price"] = pd.to_numeric(df["price"], errors="raise")
    df["fees"] = pd.to_numeric(df["fees"], errors="coerce").fillna(0)

    # Optional: enforce consistent ordering
    df = df.sort_values("date").reset_index(drop=True)

    return df
