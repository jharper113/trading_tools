def calculate_futures_pnl(entry_price, exit_price, qty, multiplier, fees=0):
    return (exit_price - entry_price) * qty * multiplier - fees
