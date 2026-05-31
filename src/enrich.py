import math
import re
from collections import deque

import pandas as pd

from src.lookup import (
    FUTURES_OPTIONS,
    FUTURES_PRODUCTS,
    INDEX_OPTIONS,
)

FUTURES_CONTRACT_PATTERN = r"(/?[A-Z]+?)[FGHJKMNQUVXZ]\d{2}"


def is_futures_option_symbol(symbol):

    if symbol is None:
        return False

    symbol = str(symbol).strip().upper()

    return re.match(
        f"^{FUTURES_CONTRACT_PATTERN}\\s+",
        symbol
    ) is not None

def parse_price(price):
    """
    Parse normal decimal prices and Treasury futures prices.

    Examples:
        56.83 -> 56.83
        115'20 -> 115.625
    """

    if price is None:
        return None

    if isinstance(price, float) and math.isnan(price):
        return None

    price = str(price).strip()

    if price == "":
        return None

    #
    # Treasury futures format
    #
    if "'" in price:

        whole, frac = price.split("'")

        return float(whole) + (
            float(frac) / 32
        )

    #
    # Standard decimal
    #
    return float(price)


def parse_number(value):

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    value = str(value).strip()

    if value == "":
        return None

    value = (
        value
        .replace("$", "")
        .replace(",", "")
        .replace("+", "")
    )

    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]

    try:
        return float(value)
    except ValueError:
        return None


def is_blank(value):

    if value is None:
        return True

    if isinstance(value, float) and math.isnan(value):
        return True

    return str(value).strip() == ""


def normalize_root_symbol(symbol):

    if symbol is None:
        return None

    symbol = str(symbol).strip().upper()

    #
    # Futures root extraction
    #
    match = re.match(f"^{FUTURES_CONTRACT_PATTERN}$", symbol)

    if match:
        return match.group(1)

    #
    # Futures option symbols start with their underlying futures contract,
    # followed by option expiration detail.
    #
    match = re.match(f"^{FUTURES_CONTRACT_PATTERN}\\s+", symbol)

    if match:
        return match.group(1)

    return symbol


def lookup_fees(row):

    symbol = row["Symbol"]

    root = normalize_root_symbol(
        symbol
    )

    qty = abs(float(row["Qty"]))

    #
    # Futures options
    #
    if is_futures_option_symbol(symbol) and root in FUTURES_OPTIONS:

        return (
            FUTURES_OPTIONS[root]["fees_per_contract"]
            * qty
        )

    #
    # Futures
    #
    if root in FUTURES_PRODUCTS:

        return (
            FUTURES_PRODUCTS[root]["fees_per_contract"]
            * qty
        )

    #
    # Index options
    #
    if root in INDEX_OPTIONS:

        return (
            INDEX_OPTIONS[root]["fees_per_contract"]
            * qty
        )

    return 0.0


def lookup_margin_requirement(row):

    symbol = row["Symbol"]

    root = normalize_root_symbol(
        symbol
    )

    qty = abs(parse_number(row["Qty"]) or 0)

    price = abs(parse_price(row["Price"]))

    #
    # Single futures options
    #
    if is_futures_option_symbol(symbol) and root in FUTURES_OPTIONS:

        metadata = {
            **FUTURES_PRODUCTS.get(root, {}),
            **FUTURES_OPTIONS[root],
        }

        multiplier = metadata.get("multiplier", 1)
        margin_pct = metadata.get("margin_pct", 0)
        strike = parse_number(row.get("Strike"))

        side = str(row.get("Side", "")).upper()
        pos_effect = str(row.get("Pos Effect", "")).upper()

        premium_risk = price * multiplier * qty

        if side == "BUY" and pos_effect == "TO OPEN":
            return premium_risk

        if strike is None:
            return premium_risk

        return strike * multiplier * qty * margin_pct


    #
    # Futures
    #
    if root in FUTURES_PRODUCTS:

        metadata = FUTURES_PRODUCTS[root]

        multiplier = metadata.get("multiplier", 1)
        margin_pct = metadata.get("margin_pct", 0)

        notional = (
            price
            * multiplier
            * qty
        )

        return notional * margin_pct

    #
    # Index options
    #
    if root in INDEX_OPTIONS:

        metadata = INDEX_OPTIONS[root]

        multiplier = metadata.get("multiplier", 1)
        margin_pct = metadata.get("margin_pct", 0)

        notional = (
            price
            * multiplier
            * qty
        )

        return notional * margin_pct

    if not is_blank(row.get("Price")) and not is_blank(row.get("Qty")):
        return price * qty

    return 0.0


def lookup_spread_margin_requirement(row, next_row):

    spread = str(row.get("Spread", "")).upper()

    if spread != "VERTICAL":
        return None

    if next_row is None:
        return None

    root = normalize_root_symbol(row.get("Symbol"))

    if root not in INDEX_OPTIONS:
        return None

    first_strike = parse_number(row.get("Strike"))
    second_strike = parse_number(next_row.get("Strike"))

    if first_strike is None or second_strike is None:
        return None

    qty = abs(parse_number(row.get("Qty")) or 0)
    net_price = abs(parse_number(row.get("Net Price")) or 0)

    metadata = INDEX_OPTIONS[root]
    multiplier = metadata.get("multiplier", 1)
    width = abs(first_strike - second_strike)

    if qty == 0 or width == 0:
        return 0.0

    side = str(row.get("Side", "")).upper()
    pos_effect = str(row.get("Pos Effect", "")).upper()
    max_width_risk = width * multiplier * qty

    if pos_effect == "TO OPEN" and side == "SELL":
        return max(
            max_width_risk - (net_price * multiplier * qty),
            0.0
        )

    if pos_effect == "TO OPEN" and side == "BUY":
        return net_price * multiplier * qty

    return max_width_risk


def calculate_margin_requirements(df):

    margins = []
    records = df.to_dict("records")

    for index, row in enumerate(records):

        next_row = (
            records[index + 1]
            if index + 1 < len(records)
            else None
        )

        spread_margin = lookup_spread_margin_requirement(
            row,
            next_row
        )

        if spread_margin is not None:
            margins.append(spread_margin)
            continue

        if is_blank(row.get("Spread")):
            previous_row = (
                records[index - 1]
                if index > 0
                else None
            )

            if (
                previous_row is not None
                and str(previous_row.get("Spread", "")).upper() == "VERTICAL"
            ):
                margins.append(0.0)
                continue

        margins.append(
            lookup_margin_requirement(row)
        )

    return margins


def infer_strategy_name(row):

    spread = str(row.get("Spread", "")).upper()
    symbol = str(row.get("Symbol", "")).upper()
    option_type = str(row.get("Type", "")).upper()
    side = str(row.get("Side", "")).upper()
    pos_effect = str(row.get("Pos Effect", "")).upper()
    root = normalize_root_symbol(symbol)

    direction = ""

    if pos_effect == "TO OPEN":
        direction = "Short" if side == "SELL" else "Long"

    if spread == "VERTICAL":
        credit_debit = "Credit" if side == "SELL" else "Debit"
        return f"{root} {option_type.title()} Vertical {credit_debit}".strip()

    if spread == "STRADDLE":
        return f"{root} {direction} Straddle".strip()

    if spread == "SINGLE" and is_futures_option_symbol(symbol):
        return f"{root} {direction} Futures Option {option_type.title()}".strip()

    if spread == "FUTURE":
        return f"{root} Future"

    if spread == "CRYPTO":
        return "Crypto"

    if root in INDEX_OPTIONS:
        return f"{root} {direction} Index Option {option_type.title()}".strip()

    if root in FUTURES_PRODUCTS:
        return f"{root} Future"

    if symbol:
        return "Equity"

    return "Unknown"


def add_strategy_names(df):

    df = df.copy()
    df["Strategy_Name"] = df.apply(
        infer_strategy_name,
        axis=1,
    )

    return df


def get_contract_multiplier(row):

    symbol = row.get("Symbol")
    root = normalize_root_symbol(symbol)

    if is_futures_option_symbol(symbol) and root in FUTURES_OPTIONS:
        return FUTURES_OPTIONS[root].get("multiplier", 1)

    if root in FUTURES_PRODUCTS:
        return FUTURES_PRODUCTS[root].get("multiplier", 1)

    if root in INDEX_OPTIONS:
        return INDEX_OPTIONS[root].get("multiplier", 1)

    return 1


def get_side_sign(row):

    side = str(row.get("Side", "")).upper()

    if side == "SELL":
        return 1

    if side == "BUY":
        return -1

    return 0


def is_futures_trade(row):

    spread = str(row.get("Spread", "")).upper()
    type_value = str(row.get("Type", "")).upper()

    return spread == "FUTURE" or type_value == "FUTURE"


def is_multileg_continuation(row, previous_row):

    if previous_row is None:
        return False

    if not is_blank(row.get("Spread")):
        return False

    previous_spread = str(previous_row.get("Spread", "")).upper()

    return previous_spread not in {
        "",
        "NAN",
        "SINGLE",
        "FUTURE",
        "CRYPTO",
    }


def calculate_premium_trade_pnl(row):

    qty = abs(parse_number(row.get("Qty")) or 0)
    multiplier = get_contract_multiplier(row)
    side_sign = get_side_sign(row)

    net_price = parse_number(row.get("Net Price"))
    price = parse_price(row.get("Price"))
    trade_price = net_price if net_price is not None else price

    if trade_price is None:
        return 0.0

    return trade_price * qty * multiplier * side_sign


def calculate_trade_pnls(df):

    trade_pnls = []
    futures_positions = {}
    records = df.to_dict("records")

    for index, row in enumerate(records):

        previous_row = (
            records[index - 1]
            if index > 0
            else None
        )

        if is_multileg_continuation(row, previous_row):
            trade_pnls.append(0.0)
            continue

        if not is_futures_trade(row):
            trade_pnls.append(
                calculate_premium_trade_pnl(row)
            )
            continue

        symbol = row.get("Symbol")
        root = normalize_root_symbol(symbol)
        multiplier = get_contract_multiplier(row)
        qty_remaining = abs(parse_number(row.get("Qty")) or 0)
        price = parse_price(row.get("Price"))
        side = str(row.get("Side", "")).upper()
        pos_effect = str(row.get("Pos Effect", "")).upper()

        if price is None or qty_remaining == 0:
            trade_pnls.append(0.0)
            continue

        if root not in futures_positions:
            futures_positions[root] = deque()

        if pos_effect == "TO OPEN":
            direction = 1 if side == "BUY" else -1
            futures_positions[root].append({
                "qty_remaining": qty_remaining,
                "price": price,
                "direction": direction,
            })
            trade_pnls.append(0.0)
            continue

        if pos_effect != "TO CLOSE":
            trade_pnls.append(0.0)
            continue

        pnl = 0.0

        while qty_remaining > 0 and futures_positions[root]:
            lot = futures_positions[root][0]
            matched_qty = min(qty_remaining, lot["qty_remaining"])

            pnl += (
                price - lot["price"]
            ) * matched_qty * multiplier * lot["direction"]

            lot["qty_remaining"] -= matched_qty
            qty_remaining -= matched_qty

            if lot["qty_remaining"] == 0:
                futures_positions[root].popleft()

        trade_pnls.append(pnl)

    return trade_pnls


def add_pnl_columns(df):

    df = df.copy()

    df["trade_pnl"] = calculate_trade_pnls(df)

    df["net_pnl"] = (
        df["trade_pnl"]
        - df.get("fees", 0)
    )

    df["cumulative_pnl"] = df["net_pnl"].cumsum()

    return df


def add_log_return_columns(df, starting_equity):

    if "net_pnl" not in df.columns:
        raise ValueError(
            "Cannot calculate log_return without net_pnl"
        )

    if starting_equity is None or starting_equity <= 0:
        raise ValueError(
            "Starting equity must be greater than zero"
        )

    df = df.copy()
    equity = float(starting_equity)
    log_returns = []
    cumulative_log_returns = []

    for net_pnl in df["net_pnl"]:
        net_pnl = parse_number(net_pnl) or 0.0
        ending_equity = equity + net_pnl

        if equity <= 0 or ending_equity <= 0:
            log_return = None
        else:
            log_return = math.log(
                ending_equity / equity
            )

        equity = ending_equity

        log_returns.append(log_return)

        if equity <= 0:
            cumulative_log_returns.append(None)
        else:
            cumulative_log_returns.append(
                math.log(
                    equity / starting_equity
                )
            )

    df["starting_equity"] = starting_equity
    df["ending_equity"] = (
        starting_equity
        + df["net_pnl"].cumsum()
    )
    df["log_return"] = log_returns
    df["cumulative_log_return"] = cumulative_log_returns

    return df


def add_margin_return_columns(df):

    for column in {"net_pnl", "margin_requirement"}:
        if column not in df.columns:
            raise ValueError(
                f"Cannot calculate return_on_margin without {column}"
            )

    df = df.copy()
    margin_returns = []
    margin_log_returns = []

    for _, row in df.iterrows():
        net_pnl = parse_number(row.get("net_pnl")) or 0.0
        margin = parse_number(row.get("margin_requirement")) or 0.0

        if margin <= 0:
            margin_return = None
            margin_log_return = None
        else:
            margin_return = net_pnl / margin

            if 1 + margin_return <= 0:
                margin_log_return = None
            else:
                margin_log_return = math.log(
                    1 + margin_return
                )

        margin_returns.append(margin_return)
        margin_log_returns.append(margin_log_return)

    df["return_on_margin"] = margin_returns
    df["log_return_on_margin"] = margin_log_returns
    df["cumulative_log_return_on_margin"] = (
        df["log_return_on_margin"]
        .apply(parse_number)
        .fillna(0.0)
        .cumsum()
    )

    return df


def build_equity_curve(df):

    required_columns = {
        "net_pnl",
        "ending_equity",
        "cumulative_pnl",
        "log_return",
        "cumulative_log_return",
    }
    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Cannot build equity curve without columns: {missing}"
        )

    equity_curve = df.copy()

    equity_curve["timestamp"] = pd.to_datetime(
        equity_curve["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    )

    equity_curve["equity_peak"] = (
        equity_curve["ending_equity"]
        .cummax()
    )
    equity_curve["drawdown"] = (
        equity_curve["ending_equity"]
        / equity_curve["equity_peak"]
        - 1
    )
    equity_curve["drawdown_dollars"] = (
        equity_curve["ending_equity"]
        - equity_curve["equity_peak"]
    )

    columns = [
        "timestamp",
        "Exec Time",
        "Strategy_Name",
        "Symbol",
        "Spread",
        "Side",
        "Qty",
        "net_pnl",
        "cumulative_pnl",
        "ending_equity",
        "equity_peak",
        "drawdown",
        "drawdown_dollars",
        "log_return",
        "cumulative_log_return",
        "return_on_margin",
        "log_return_on_margin",
        "cumulative_log_return_on_margin",
    ]

    return equity_curve[
        [column for column in columns if column in equity_curve.columns]
    ]


def safe_ratio(numerator, denominator):

    if denominator in {0, None}:
        return None

    if isinstance(denominator, float) and math.isnan(denominator):
        return None

    return numerator / denominator


def calculate_summary_statistics(df):

    if "ending_equity" not in df.columns:
        raise ValueError(
            "Cannot calculate summary statistics without ending_equity"
        )

    stats = {}
    net_pnl = df["net_pnl"].apply(parse_number).fillna(0.0)
    returns = df["log_return"].apply(parse_number).dropna()
    wins = net_pnl[net_pnl > 0]
    losses = net_pnl[net_pnl < 0]

    starting_equity = parse_number(df["starting_equity"].iloc[0])
    ending_equity = parse_number(df["ending_equity"].iloc[-1])
    total_pnl = net_pnl.sum()
    total_return = safe_ratio(
        ending_equity - starting_equity,
        starting_equity,
    )

    timestamps = pd.to_datetime(
        df["Exec Time"],
        format="%m/%d/%y %H:%M:%S",
        errors="coerce",
    ).dropna()

    if len(timestamps) >= 2:
        elapsed_days = max(
            (timestamps.max() - timestamps.min()).days,
            1,
        )
    else:
        elapsed_days = None

    if total_return is None or elapsed_days is None:
        cagr = None
    else:
        cagr = (
            (1 + total_return)
            ** (365 / elapsed_days)
            - 1
        )

    equity_peak = df["ending_equity"].cummax()
    drawdown = df["ending_equity"] / equity_peak - 1
    max_drawdown = drawdown.min()
    max_drawdown_dollars = (
        df["ending_equity"]
        - equity_peak
    ).min()

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = safe_ratio(
        gross_profit,
        gross_loss,
    )

    if len(returns) > 1 and returns.std(ddof=1) != 0:
        sharpe_ratio = (
            returns.mean()
            / returns.std(ddof=1)
            * math.sqrt(len(returns))
        )
    else:
        sharpe_ratio = None

    mar_ratio = (
        safe_ratio(cagr, abs(max_drawdown))
        if cagr is not None
        else None
    )

    stats["trade_count"] = len(df)
    stats["winning_trades"] = len(wins)
    stats["losing_trades"] = len(losses)
    stats["win_rate"] = safe_ratio(len(wins), len(df))
    stats["gross_profit"] = gross_profit
    stats["gross_loss"] = gross_loss
    stats["profit_factor"] = profit_factor
    stats["total_pnl"] = total_pnl
    stats["starting_equity"] = starting_equity
    stats["ending_equity"] = ending_equity
    stats["total_return"] = total_return
    stats["cagr"] = cagr
    stats["sharpe_ratio"] = sharpe_ratio
    stats["max_drawdown"] = max_drawdown
    stats["max_drawdown_dollars"] = max_drawdown_dollars
    stats["mar_ratio"] = mar_ratio
    stats["average_trade_pnl"] = net_pnl.mean()
    stats["median_trade_pnl"] = net_pnl.median()
    stats["average_win"] = wins.mean() if len(wins) else None
    stats["average_loss"] = losses.mean() if len(losses) else None
    stats["largest_win"] = net_pnl.max()
    stats["largest_loss"] = net_pnl.min()
    stats["average_return_on_margin"] = (
        df["return_on_margin"]
        .apply(parse_number)
        .dropna()
        .mean()
        if "return_on_margin" in df.columns
        else None
    )

    return pd.DataFrame([
        {
            "metric": metric,
            "value": value,
        }
        for metric, value in stats.items()
    ])


def add_cumulative_log_return(df):

    if "log_return" not in df.columns:
        raise ValueError(
            "Cannot calculate cumulative_log_return without log_return"
        )

    df = df.copy()

    df["cumulative_log_return"] = (
        df["log_return"]
        .apply(parse_number)
        .fillna(0.0)
        .cumsum()
    )

    return df


def calculate_log_return(
    pnl,
    margin_requirement
):

    pnl = float(pnl)

    if margin_requirement <= 0:
        return None

    #
    # Prevent invalid log
    #
    if 1 + pnl / margin_requirement <= 0:
        return None

    return math.log(
        1 + pnl / margin_requirement
    )
