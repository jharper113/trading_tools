from collections import deque


def match_trades(df):
    open_positions = {}
    trades = []

    for row in df.to_dict("records"):
        symbol = row["symbol"]

        if symbol not in open_positions:
            open_positions[symbol] = deque()

        #
        # BUY SIDE
        #
        if row["side"] == "BUY":

            lot = row.copy()

            # Preserve original qty for fee allocation
            lot["qty_original"] = row["qty"]

            # Track remaining qty separately
            lot["qty_remaining"] = row["qty"]

            open_positions[symbol].append(lot)

        #
        # SELL SIDE
        #
        elif row["side"] == "SELL":

            qty_to_close = row["qty"]

            while qty_to_close > 0:

                if not open_positions[symbol]:
                    raise ValueError(
                        f"No open position available for {symbol}"
                    )

                buy = open_positions[symbol][0]

                match_qty = min(
                    qty_to_close,
                    buy["qty_remaining"]
                )

                #
                # Fee allocation
                #

                entry_fee_alloc = (
                    buy.get("fees", 0)
                    * match_qty
                    / buy["qty_original"]
                )

                exit_fee_alloc = (
                    row.get("fees", 0)
                    * match_qty
                    / row["qty"]
                )

                total_fees = (
                    entry_fee_alloc
                    + exit_fee_alloc
                )

                #
                # Gross PnL
                #

                gross_pnl = (
                    row["price"]
                    - buy["price"]
                ) * match_qty


  #              gross_pnl =
  #              (exit - entry)
  #              * qty
  #              * multiplier
                
                #
                # Net PnL
                #

                net_pnl = gross_pnl - total_fees

                #
                # Emit completed trade
                #

                trades.append({
                    "symbol": symbol,

                    "entry_date": buy.get("date"),
                    "exit_date": row.get("date"),
                    
                    "entry_price": buy["price"],
                    "exit_price": row["price"],

                    "qty": match_qty,

                    "entry_fees": entry_fee_alloc,
                    "exit_fees": exit_fee_alloc,
                    "total_fees": total_fees,

                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                })

                #
                # Reduce remaining quantities
                #

                buy["qty_remaining"] -= match_qty
                qty_to_close -= match_qty

                #
                # Remove exhausted lot
                #

                if buy["qty_remaining"] == 0:
                    open_positions[symbol].popleft()

    return trades
