
FUTURES_MULTIPLIERS = {
    "ES": 50,
    "MES": 5,
    "NQ": 20,
    "MNQ": 2,
    "CL": 1000,
}


def calculate_gross_pnl(
    asset_type,
    entry_price,
    exit_price,
    qty,
    symbol=None,
):
    #
    # EQUITIES
    #
    if asset_type == "EQUITY":
        return (
            exit_price - entry_price
        ) * qty

    #
    # OPTIONS
    #
    elif asset_type == "OPTION":
        return (
            exit_price - entry_price
        ) * qty * 100

    #
    # FUTURES
    #
    elif asset_type == "FUTURE":

        multiplier = FUTURES_MULTIPLIERS.get(symbol)

        if multiplier is None:
            raise ValueError(
                f"No futures multiplier found for {symbol}"
            )

        return (
            exit_price - entry_price
        ) * qty * multiplier

    #
    # FUTURES OPTION
    #
    elif asset_type == "FUTURE_OPTION":

        multiplier = FUTURES_MULTIPLIERS[symbol]
        
        if multiplier is None:
            raise ValueError(
                f"No futures option multiplier found for {symbol}"
            )

        return (
            exit_price - entry_price
        ) * qty * multiplier

    else:
        raise ValueError(
            f"Unknown asset type: {asset_type}"
        )





def calculate_equity_pnl(
    entry_price,
    exit_price,
    qty,
    fees=0
):
    gross = (exit_price - entry_price) * qty
    return gross - fees

def calculate_futures_pnl(
    entry_price,
    exit_price,
    qty,
    multiplier,
    fees=0
):
    return (exit_price - entry_price) * qty * multiplier - fees
