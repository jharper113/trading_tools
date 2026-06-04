import pandas as pd

from analyze_strategy_performance import (
    aggregate_execution_packages,
    aggregate_realized_trades,
    calculate_strategy_position_sizing,
    build_strategy_equity_curves,
    build_strategy_pnl_events,
    build_strategy_trade_ledgers,
    filter_realized_trades,
    recalculate_account_columns,
)


def make_trade(
    exec_time,
    side,
    pos_effect,
    symbol,
    expiration,
    strike,
    option_type,
    net_pnl,
):
    return {
        "Strategy_Name": "Test Strategy",
        "Exec Time": exec_time,
        "timestamp": pd.to_datetime(exec_time, format="%m/%d/%y %H:%M:%S"),
        "Spread": "SINGLE",
        "Side": side,
        "Qty": -1 if side == "SELL" else 1,
        "Pos Effect": pos_effect,
        "Symbol": symbol,
        "Exp": expiration,
        "Strike": strike,
        "Type": option_type,
        "trade_pnl": net_pnl,
        "fees": 0,
        "net_pnl": net_pnl,
        "margin_requirement": 100,
        "return_on_margin": net_pnl / 100,
        "starting_equity": 1000,
        "log_return_on_margin": 0.0 if net_pnl <= -100 else 0.01,
    }


def test_filter_realized_trades_keeps_closed_trades():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO CLOSE",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            150,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert realized["Pos Effect"].tolist() == [
        "TO OPEN",
        "TO CLOSE",
    ]


def test_build_strategy_trade_ledgers_calculates_cumulative_pnl_by_strategy():
    realized = pd.DataFrame([
        {
            "Strategy_Name": "Strategy B",
            "Exec Time": "1/2/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-02 09:30:00"),
            "net_pnl": 50,
        },
        {
            "Strategy_Name": "Strategy A",
            "Exec Time": "1/1/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-01 09:30:00"),
            "net_pnl": 100,
        },
        {
            "Strategy_Name": "Strategy A",
            "Exec Time": "1/3/26 09:30:00",
            "timestamp": pd.Timestamp("2026-01-03 09:30:00"),
            "net_pnl": -25,
        },
    ])

    ledgers = build_strategy_trade_ledgers(realized)
    strategy_a = ledgers[
        ledgers["Strategy_Name"] == "Strategy A"
    ]
    strategy_b = ledgers[
        ledgers["Strategy_Name"] == "Strategy B"
    ]

    assert strategy_a["strategy_trade_number"].tolist() == [
        1,
        2,
    ]
    assert strategy_a["strategy_cumulative_pnl"].tolist() == [
        100,
        75,
    ]
    assert strategy_b["strategy_trade_number"].tolist() == [
        1,
    ]
    assert strategy_b["strategy_cumulative_pnl"].tolist() == [
        50,
    ]


def test_calculate_strategy_position_sizing_uses_risk_and_unit_margin():
    risk_summary = pd.DataFrame([
        {
            "Strategy_Name": "Test Strategy",
            "risk_per_trade_dollars": 10000,
        },
        {
            "Strategy_Name": "Discretionary",
            "risk_per_trade_dollars": 10000,
        },
    ])
    realized_trades = pd.DataFrame([
        {
            "Strategy_Name": "Test Strategy",
            "Qty": 2,
            "margin_requirement": 5000,
        },
        {
            "Strategy_Name": "Test Strategy",
            "Qty": 4,
            "margin_requirement": 12000,
        },
        {
            "Strategy_Name": "Discretionary",
            "Qty": 1,
            "margin_requirement": 100,
        },
    ])

    sized = calculate_strategy_position_sizing(
        risk_summary,
        realized_trades,
    )

    test_strategy = sized[
        sized["Strategy_Name"] == "Test Strategy"
    ].iloc[0]
    discretionary = sized[
        sized["Strategy_Name"] == "Discretionary"
    ].iloc[0]

    assert test_strategy["estimated_margin_per_contract_or_share"] == 2750
    assert test_strategy["contracts_or_shares_to_trade"] == 3
    assert discretionary["contracts_or_shares_to_trade"] == "N/A"


def test_filter_realized_trades_drops_unclosed_long_options():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-20",
    )

    assert realized.empty


def test_filter_realized_trades_keeps_expired_short_options():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "/X2AF26",
            6800,
            "PUT",
            125,
        ),
        make_trade(
            "1/2/26 09:31:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            100,
        ),
    ])

    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-13",
    )

    assert realized["Symbol"].tolist() == [
        "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
    ]


def test_recalculate_account_columns_after_filtering():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "2 JAN 26",
            6800,
            "PUT",
            100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            50,
        ),
    ])
    realized = filter_realized_trades(
        df,
        as_of_date="2026-01-03",
    )

    recalculated = recalculate_account_columns(realized)

    assert recalculated["cumulative_pnl"].tolist() == [
        100,
    ]
    assert recalculated["ending_equity"].tolist() == [
        1100,
    ]


def test_aggregate_realized_trades_counts_open_and_close_as_one_trade():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
        make_trade(
            "1/3/26 09:30:00",
            "SELL",
            "TO CLOSE",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            150,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert len(realized) == 1
    assert realized["realized_status"].tolist() == [
        "CLOSED",
    ]
    assert realized["net_pnl"].tolist() == [
        50,
    ]
    assert realized["cumulative_pnl"].tolist() == [
        50,
    ]
    assert realized["Exec Time"].tolist() == [
        "1/3/26 09:30:00",
    ]


def test_aggregate_realized_trades_counts_expired_short_option_as_one_trade():
    df = pd.DataFrame([
        make_trade(
            "1/2/26 09:30:00",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 12 JAN 26 (Monday) (Wk3)",
            "/X2AF26",
            6800,
            "PUT",
            125,
        ),
        make_trade(
            "1/2/26 09:31:00",
            "BUY",
            "TO OPEN",
            "SPX",
            "16 JAN 26",
            6800,
            "PUT",
            -100,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13",
    )

    assert len(realized) == 1
    assert realized["realized_status"].tolist() == [
        "EXPIRED",
    ]
    assert realized["net_pnl"].tolist() == [
        125,
    ]
    assert realized["Exec Time"].tolist() == [
        "1/12/26 16:00:00",
    ]


def test_aggregate_realized_trades_does_not_expire_after_today_by_default():
    df = pd.DataFrame([
        make_trade(
            "1/14/26 15:06:22",
            "SELL",
            "TO OPEN",
            "/ESH26 1/50 15 JAN 26 (Thursday) (Wk3)",
            "/E3DF26",
            6955,
            "CALL",
            2047.5,
        ),
    ])

    realized = aggregate_realized_trades(df)

    assert realized.empty


def test_aggregate_realized_trades_expires_prior_rolling_short_options():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/7/26 09:32:23",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 14 JAN 26 (Wednesday) (Wk3)",
                "/X2CF26",
                6815,
                "PUT",
                127,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
        {
            **make_trade(
                "1/8/26 09:31:05",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 15 JAN 26 (Thursday) (Wk3)",
                "/X3DF26",
                6770,
                "PUT",
                101.25,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
        {
            **make_trade(
                "1/14/26 09:32:25",
                "SELL",
                "TO OPEN",
                "/MESH26 1/5 21 JAN 26 (Wednesday) (Wk4)",
                "/X3CF26",
                6765,
                "PUT",
                149,
            ),
            "Strategy_Name": "Opt025-TradeBusters-7DTE-Naked-Puts",
        },
    ])

    realized = aggregate_realized_trades(df)

    assert realized["Symbol"].tolist() == [
        "/MESH26 1/5 14 JAN 26 (Wednesday) (Wk3)",
        "/MESH26 1/5 15 JAN 26 (Thursday) (Wk3)",
    ]
    assert realized["realized_status"].tolist() == [
        "EXPIRED",
        "EXPIRED",
    ]


def test_aggregate_realized_trades_counts_straddle_legs_as_one_trade():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/5/26 15:00:00",
                "SELL",
                "TO OPEN",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "CALL",
                1447.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "1/5/26 15:00:00",
                "SELL",
                "TO OPEN",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "PUT",
                -2.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
        {
            **make_trade(
                "1/6/26 09:30:00",
                "BUY",
                "TO CLOSE",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "CALL",
                -1127.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "STRADDLE",
        },
        {
            **make_trade(
                "1/6/26 09:30:00",
                "BUY",
                "TO CLOSE",
                "/ESH26 1/50 6 JAN 26 (Tuesday) (Wk2)",
                "/E1BF26",
                6945,
                "PUT",
                -2.5,
            ),
            "Strategy_Name": "Opt021-1DTE-ShortStraddle-Mon-Thurs",
            "Spread": "",
        },
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-10",
    )

    assert len(realized) == 1
    assert realized["execution_leg_count"].tolist() == [
        2,
    ]
    assert realized["net_pnl"].tolist() == [
        315.0,
    ]


def test_aggregate_realized_trades_counts_vertical_legs_as_one_trade():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/7/26 11:45:04",
                "SELL",
                "TO OPEN",
                "SPX",
                "7 JAN 26",
                6945,
                "PUT",
                173.75,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
        {
            **make_trade(
                "1/7/26 11:45:04",
                "BUY",
                "TO OPEN",
                "SPX",
                "7 JAN 26",
                6925,
                "PUT",
                -1.25,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
        {
            **make_trade(
                "1/7/26 14:10:39",
                "BUY",
                "TO CLOSE",
                "SPX",
                "7 JAN 26",
                6945,
                "PUT",
                -1845,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
        {
            **make_trade(
                "1/7/26 14:10:39",
                "SELL",
                "TO CLOSE",
                "SPX",
                "7 JAN 26",
                6925,
                "PUT",
                -5,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-07 15:00:00",
    )

    assert len(realized) == 1
    assert realized["execution_leg_count"].tolist() == [
        2,
    ]
    assert realized["net_pnl"].tolist() == [
        -1677.5,
    ]


def test_aggregate_execution_packages_uses_explicit_spread_leg_as_primary():
    df = pd.DataFrame([
        {
            **make_trade(
                "1/6/26 12:47:27",
                "BUY",
                "TO OPEN",
                "SPX",
                "6 JAN 26",
                6900,
                "PUT",
                -5,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "",
        },
        {
            **make_trade(
                "1/6/26 12:47:27",
                "SELL",
                "TO OPEN",
                "SPX",
                "6 JAN 26",
                6920,
                "PUT",
                435,
            ),
            "Strategy_Name": "Opt026-60m0DTE-PutSpread",
            "Spread": "VERTICAL",
        },
    ])

    packages = aggregate_execution_packages(df)

    assert packages["Side"].tolist() == [
        "SELL",
    ]
    assert packages["net_pnl"].tolist() == [
        430,
    ]


def test_strategy_pnl_events_split_short_option_open_and_close_pnl():
    df = pd.DataFrame([
        make_trade(
            "1/13/26 09:32:02",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            116,
        ),
        make_trade(
            "1/13/26 10:29:02",
            "BUY",
            "TO CLOSE",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            -253,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13 11:00:00",
    )
    events = build_strategy_pnl_events(realized)

    assert events["pnl_event_type"].tolist() == [
        "OPEN",
        "CLOSE",
    ]
    assert events["Exec Time"].tolist() == [
        "1/13/26 09:32:02",
        "1/13/26 10:29:02",
    ]
    assert events["net_pnl"].tolist() == [
        116,
        -253,
    ]


def test_strategy_equity_curve_uses_short_option_cashflow_dates():
    df = pd.DataFrame([
        make_trade(
            "1/13/26 09:32:02",
            "SELL",
            "TO OPEN",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            116,
        ),
        make_trade(
            "1/13/26 10:29:02",
            "BUY",
            "TO CLOSE",
            "/MESH26 1/5 20 JAN 26 (Tuesday) (Wk4)",
            "/X3BF26",
            6875,
            "PUT",
            -253,
        ),
    ])

    realized = aggregate_realized_trades(
        df,
        as_of_date="2026-01-13 11:00:00",
    )
    events = build_strategy_pnl_events(realized)
    curve = build_strategy_equity_curves(events)

    assert curve["strategy_pnl"].tolist() == [
        116,
        -253,
    ]
    assert curve["CMPNL"].tolist() == [
        116,
        -137,
    ]
    assert curve["strategy_equity"].tolist() == [
        1116,
        863,
    ]
