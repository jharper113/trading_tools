def calculate_equity_pnl(
    entry_price,
    exit_price,
    qty,
    fees=0
):
    gross = (exit_price - entry_price) * qty
    return gross - fees
