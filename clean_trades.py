import pandas as pd


INPUT_FILE = "./data/trades.csv"


OUTPUT_FILE = "./cleaned_tos_data.csv"


def main():

    #
    # Load CSV
    #
    df = pd.read_csv(INPUT_FILE)

    #
    # Normalize column names
    #
    df.columns = [
        c.strip().lower()
        for c in df.columns
    ]

    #
    # Optional basic cleaning
    #
    if "symbol" in df.columns:
        df["symbol"] = (
            df["symbol"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

    #
    # Save cleaned output
    #
    df.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print(f"Saved cleaned file to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
