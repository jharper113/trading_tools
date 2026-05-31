from src.pnl import calculate_equity_pnl

def test_equity_pnl():
    pnl = calculate_equity_pnl(
        entry_price=100,
        exit_price=110,
        qty=10,
        fees=5
    )

    assert pnl == 95