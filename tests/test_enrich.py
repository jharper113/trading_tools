import pandas as pd

from src.enrich import (
    add_strategy_names,
    add_cumulative_log_return,
    add_log_return_columns,
    add_margin_return_columns,
    add_pnl_columns,
    build_equity_curve,
    calculate_summary_statistics,
    calculate_margin_requirements,
    lookup_fees,
    lookup_margin_requirement,
    normalize_root_symbol,
)


def test_normalize_futures_option_root_symbol():
    assert (
        normalize_root_symbol("/MESH26 1/5 12 JAN 26 (Monday) (Wk3)")
        == "/MES"
    )


def test_lookup_futures_option_fees():
    row = {
        "Symbol": "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
        "Qty": 2,
    }

    assert lookup_fees(row) == 4.0


def test_lookup_short_futures_option_margin_uses_strike_not_premium():
    row = {
        "Spread": "SINGLE",
        "Side": "SELL",
        "Qty": -4,
        "Pos Effect": "TO OPEN",
        "Symbol": "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
        "Strike": 6775,
        "Price": 6.30,
    }

    assert lookup_margin_requirement(row) == 10840.0


def test_calculate_credit_vertical_spread_margin():
    df = pd.DataFrame([
        {
            "Spread": "VERTICAL",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Strike": 6890,
            "Type": "PUT",
            "Price": 2.72,
            "Net Price": 1.50,
        },
        {
            "Spread": None,
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Strike": 6870,
            "Type": "PUT",
            "Price": 1.22,
            "Net Price": "CREDIT",
        },
    ])

    assert calculate_margin_requirements(df) == [1850.0, 0.0]


def test_calculate_debit_vertical_spread_margin():
    df = pd.DataFrame([
        {
            "Spread": "VERTICAL",
            "Side": "BUY",
            "Qty": 2,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Strike": 6900,
            "Type": "CALL",
            "Price": 6.50,
            "Net Price": 4.50,
        },
        {
            "Spread": None,
            "Side": "SELL",
            "Qty": -2,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Strike": 6920,
            "Type": "CALL",
            "Price": 2.00,
            "Net Price": "DEBIT",
        },
    ])

    assert calculate_margin_requirements(df) == [900.0, 0.0]


def test_add_pnl_columns():
    df = pd.DataFrame([
        {
            "Spread": "SINGLE",
            "Side": "SELL",
            "Qty": -4,
            "Symbol": "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "Price": 6.30,
            "fees": 2.50,
        },
        {
            "Spread": "VERTICAL",
            "Side": "SELL",
            "Qty": -1,
            "Symbol": "SPX",
            "Price": 2.72,
            "Net Price": 1.50,
            "fees": 1.25,
        },
        {
            "Spread": None,
            "Side": "BUY",
            "Qty": 1,
            "Symbol": "SPX",
            "Price": 1.22,
            "Net Price": "CREDIT",
            "fees": 1.25,
        },
    ])

    out = add_pnl_columns(df)

    assert list(out["trade_pnl"]) == [126.0, 150.0, 0.0]
    assert list(out["net_pnl"]) == [123.5, 148.75, -1.25]
    assert list(out["cumulative_pnl"]) == [123.5, 272.25, 271.0]


def test_add_pnl_columns_treats_negative_net_price_as_credit():
    df = pd.DataFrame([
        {
            "Spread": "CUSTOM",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Price": 4.97,
            "Net Price": -3.25,
            "fees": 1.25,
        },
        {
            "Spread": None,
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO OPEN",
            "Symbol": "SPX",
            "Price": 1.72,
            "Net Price": "CREDIT",
            "fees": 1.25,
        },
    ])

    out = add_pnl_columns(df)

    assert list(out["trade_pnl"]) == [325.0, 0.0]
    assert list(out["net_pnl"]) == [323.75, -1.25]


def test_add_pnl_columns_matches_futures_close():
    df = pd.DataFrame([
        {
            "Spread": "FUTURE",
            "Side": "SELL",
            "Qty": -1,
            "Pos Effect": "TO OPEN",
            "Symbol": "/ESH26",
            "Type": "FUTURE",
            "Price": 7000,
            "fees": 3.50,
        },
        {
            "Spread": "FUTURE",
            "Side": "BUY",
            "Qty": 1,
            "Pos Effect": "TO CLOSE",
            "Symbol": "/ESH26",
            "Type": "FUTURE",
            "Price": 6995,
            "fees": 3.50,
        },
    ])

    out = add_pnl_columns(df)

    assert list(out["trade_pnl"]) == [0.0, 250.0]
    assert list(out["net_pnl"]) == [-3.5, 246.5]
    assert list(out["cumulative_pnl"]) == [-3.5, 243.0]


def test_add_cumulative_log_return():
    df = pd.DataFrame({
        "log_return": [
            0.10,
            None,
            -0.03,
        ]
    })

    out = add_cumulative_log_return(df)

    assert list(out["cumulative_log_return"]) == [0.10, 0.10, 0.07]


def test_add_cumulative_log_return_requires_log_return():
    df = pd.DataFrame({
        "net_pnl": [1.0],
    })

    try:
        add_cumulative_log_return(df)
    except ValueError as error:
        assert "log_return" in str(error)
    else:
        raise AssertionError("Expected missing log_return to raise ValueError")


def test_add_log_return_columns_uses_starting_equity():
    df = pd.DataFrame({
        "net_pnl": [
            100.0,
            -50.0,
        ]
    })

    out = add_log_return_columns(
        df,
        starting_equity=10000.0,
    )

    assert round(out["ending_equity"].iloc[-1], 2) == 10050.00
    assert round(out["cumulative_log_return"].iloc[-1], 6) == 0.004988


def test_add_log_return_columns_requires_starting_equity():
    df = pd.DataFrame({
        "net_pnl": [1.0],
    })

    try:
        add_log_return_columns(
            df,
            starting_equity=0,
        )
    except ValueError as error:
        assert "Starting equity" in str(error)
    else:
        raise AssertionError("Expected invalid starting equity to raise")


def test_add_margin_return_columns():
    df = pd.DataFrame({
        "net_pnl": [
            100.0,
            -25.0,
            10.0,
        ],
        "margin_requirement": [
            1000.0,
            500.0,
            0.0,
        ],
    })

    out = add_margin_return_columns(df)

    assert list(out["return_on_margin"][:2]) == [0.10, -0.05]
    assert pd.isna(out["return_on_margin"].iloc[2])
    assert round(out["cumulative_log_return_on_margin"].iloc[1], 6) == 0.044017


def test_equity_margin_requirement_uses_notional():
    row = {
        "Symbol": "AAPL",
        "Qty": 10,
        "Price": 200,
    }

    assert lookup_margin_requirement(row) == 2000


def test_add_strategy_names():
    df = pd.DataFrame([
        {
            "Spread": "VERTICAL",
            "Side": "SELL",
            "Symbol": "SPX",
            "Type": "PUT",
        },
        {
            "Spread": "SINGLE",
            "Side": "SELL",
            "Pos Effect": "TO OPEN",
            "Symbol": "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "Type": "PUT",
        },
    ])

    out = add_strategy_names(df)

    assert list(out["Strategy_Name"]) == [
        "SPX Put Vertical Credit",
        "/MES Short Futures Option Put",
    ]


def test_build_equity_curve_adds_drawdown():
    df = pd.DataFrame({
        "Exec Time": [
            "1/1/26 09:30:00",
            "1/1/26 09:31:00",
        ],
        "Strategy_Name": ["A", "B"],
        "Symbol": ["SPX", "SPX"],
        "Spread": ["VERTICAL", "VERTICAL"],
        "Side": ["SELL", "BUY"],
        "Qty": [-1, 1],
        "net_pnl": [100.0, -50.0],
        "cumulative_pnl": [100.0, 50.0],
        "ending_equity": [10100.0, 10050.0],
        "log_return": [0.01, -0.005],
        "cumulative_log_return": [0.01, 0.005],
    })

    out = build_equity_curve(df)

    assert "drawdown" in out.columns
    assert round(out["drawdown"].iloc[0], 6) == 0.0
    assert round(out["drawdown"].iloc[1], 6) == round(-50.0 / 10100.0, 6)


def test_calculate_summary_statistics():
    df = pd.DataFrame({
        "Exec Time": [
            "1/1/26 09:30:00",
            "1/2/26 09:30:00",
        ],
        "net_pnl": [100.0, -50.0],
        "starting_equity": [10000.0, 10000.0],
        "ending_equity": [10100.0, 10050.0],
        "log_return": [0.00995, -0.00496],
        "return_on_margin": [0.10, -0.05],
    })

    summary = calculate_summary_statistics(df)
    stats = dict(
        zip(
            summary["metric"],
            summary["value"],
        )
    )

    assert stats["trade_count"] == 2
    assert stats["profit_factor"] == 2.0
    assert stats["total_pnl"] == 50.0
    assert stats["max_drawdown"] < 0
