from src.loader import load_trades_csv

import pytest


def test_missing_required_columns(tmp_path):
    csv_content = """Date,Symbol
2026-01-01,AAPL
"""

    csv_file = tmp_path / "bad.csv"
    csv_file.write_text(csv_content)

    with pytest.raises(ValueError):
        load_trades_csv(csv_file)
