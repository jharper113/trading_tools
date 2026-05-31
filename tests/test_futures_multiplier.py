def test_futures_pnl():
    from src.pnl import calculate_futures_pnl

    pnl = calculate_futures_pnl(
        entry_price=5000,
        exit_price=5010,
        qty=1,
        multiplier=50,
        fees=0
    )

    assert pnl == 500
