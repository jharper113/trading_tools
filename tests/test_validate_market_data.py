import pandas as pd
import numpy as np

from download_market_data import normalize_bar_frame
from validate_market_data import (
    apply_reconciliation_decisions,
    compare_market_data,
    dashboard_reconciliation_rows,
    dashboard_html,
    find_duplicate_bars,
    validate_market_data,
    yahoo_chart_params,
    yahoo_interval_for,
    yahoo_symbol_for,
)


def make_bars(rows, symbol="/ES", frequency="daily", source="test"):
    return normalize_bar_frame(
        pd.DataFrame(rows),
        symbol=symbol,
        frequency=frequency,
        source=source,
        retrieved_at="2026-01-01T00:00:00Z",
    )


def test_yahoo_symbol_mapping_for_default_futures_roots():
    assert yahoo_symbol_for("/ES") == "ES=F"
    assert yahoo_symbol_for("6e") == "6E=F"


def test_yahoo_interval_mapping():
    assert yahoo_interval_for("daily") == "1d"
    assert yahoo_interval_for("5m") == "5m"


def test_yahoo_chart_params_uses_range_without_dates():
    params = yahoo_chart_params("daily")

    assert params["interval"] == "1d"
    assert params["range"] == "5y"


def test_yahoo_chart_params_uses_epoch_dates_when_dates_are_given():
    params = yahoo_chart_params(
        frequency="5min",
        start="2026-01-01",
        end="2026-01-02",
    )

    assert params["interval"] == "5m"
    assert "period1" in params
    assert "period2" in params
    assert "range" not in params


def test_compare_market_data_approves_small_price_differences():
    local = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100.01,
                "high": 101.01,
                "low": 99.01,
                "close": 100.51,
            }
        ]
    )
    reference = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
            }
        ],
        source="yahoo",
    )

    differences, missing, summary = compare_market_data(
        local,
        reference,
        symbol="/ES",
        frequency="daily",
        threshold_pct=0.25,
    )

    assert len(missing) == 0
    assert summary.loc[0, "approved"]
    assert summary.loc[0, "failed_bars"] == 0
    assert differences[differences["field"] == "row_max"][
        "approved"
    ].iloc[0]


def test_compare_market_data_flags_large_price_differences():
    local = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 110,
                "high": 111,
                "low": 109,
                "close": 110,
            }
        ]
    )
    reference = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            }
        ],
        source="yahoo",
    )

    _, _, summary = compare_market_data(
        local,
        reference,
        symbol="/ES",
        frequency="daily",
        threshold_pct=0.25,
    )

    assert not summary.loc[0, "approved"]
    assert summary.loc[0, "failed_bars"] == 1
    assert summary.loc[0, "max_pct_diff"] > 9


def test_compare_market_data_reports_missing_bars():
    local = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            }
        ]
    )
    reference = make_bars(
        [
            {
                "timestamp": "2026-01-02T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            }
        ],
        source="yahoo",
    )

    _, missing, summary = compare_market_data(
        local,
        reference,
        symbol="/ES",
        frequency="daily",
        threshold_pct=0.25,
    )

    assert set(missing["missing_from"]) == {"local", "reference"}
    assert not summary.loc[0, "approved"]


def test_dashboard_reconciliation_rows_include_confirmed_neighbor_prices():
    local = make_bars(
        [
            {"timestamp": "2026-01-01T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"timestamp": "2026-01-02T00:00:00Z", "open": 150, "high": 151, "low": 149, "close": 150},
            {"timestamp": "2026-01-03T00:00:00Z", "open": 102, "high": 103, "low": 101, "close": 102},
        ]
    )
    reference = make_bars(
        [
            {"timestamp": "2026-01-01T00:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"timestamp": "2026-01-02T00:00:00Z", "open": 101, "high": 102, "low": 100, "close": 101},
            {"timestamp": "2026-01-03T00:00:00Z", "open": 102, "high": 103, "low": 101, "close": 102},
        ],
        source="yahoo",
    )
    differences, _, _ = compare_market_data(
        local,
        reference,
        symbol="/ES",
        frequency="daily",
        threshold_pct=0.25,
    )

    rows = dashboard_reconciliation_rows(differences, max_rows=0)
    failed = [row for row in rows if not row["approved"]][0]

    assert failed["comparison_key"] == "2026-01-02"
    assert failed["previous_confirmed_key"] == "2026-01-01"
    assert failed["previous_confirmed_local_close"] == 100
    assert failed["next_confirmed_key"] == "2026-01-03"
    assert failed["next_confirmed_reference_close"] == 102


def test_find_duplicate_bars_reports_duplicate_timestamps():
    bars = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            },
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            },
        ]
    )

    duplicates = find_duplicate_bars(bars, symbol="/ES", frequency="daily")

    assert len(duplicates) == 2
    assert duplicates.loc[0, "symbol"] == "/ES"


def test_validate_market_data_writes_dashboard(tmp_path):
    source_dir = tmp_path / "market_data"
    output_dir = tmp_path / "validation"
    local = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100.01,
                "high": 101.01,
                "low": 99.01,
                "close": 100.51,
            }
        ]
    )
    daily_dir = source_dir / "daily"
    daily_dir.mkdir(parents=True)
    local.to_csv(daily_dir / "ES.csv", index=False)

    def fake_reference_fetcher(symbol, frequency, start=None, end=None):
        return make_bars(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                }
            ],
            symbol=symbol,
            frequency=frequency,
            source="yahoo",
        )

    result = validate_market_data(
        source_dir=source_dir,
        output_dir=output_dir,
        symbols=["/ES"],
        frequencies=["daily"],
        reference_fetcher=fake_reference_fetcher,
        dashboard_max_rows=10,
    )

    dashboard_path = result["dashboard_path"]

    assert dashboard_path.exists()
    dashboard_html = dashboard_path.read_text()
    assert "Reconciliation Choices" in dashboard_html
    assert "Review Rows Loaded" in dashboard_html
    assert (output_dir / "validation_summary.csv").exists()


def test_dashboard_html_sanitizes_non_json_float_values():
    html = dashboard_html(
        {
            "summary": [{"max_pct_diff": np.inf}],
            "differences": [{"max_pct_diff": np.nan}],
            "missing": [],
            "duplicates": [],
            "heatmapSrc": "",
            "serverEnabled": False,
        }
    )

    assert "Infinity" not in html
    assert '"max_pct_diff": null' in html


def test_dashboard_html_uses_escaped_newlines_in_javascript():
    html = dashboard_html(
        {
            "summary": [],
            "differences": [],
            "missing": [],
            "duplicates": [],
            "heatmapSrc": "",
            "serverEnabled": False,
        }
    )

    assert 'return /[",\\n]/.test(text)' in html
    assert "lines.join('\\n')" in html
    assert 'return /[",\n]/.test(text)' not in html
    assert "lines.join('\n')" not in html


def test_apply_reconciliation_decisions_updates_data_and_marks_reviewed(tmp_path):
    source_dir = tmp_path / "market_data"
    daily_dir = source_dir / "daily"
    daily_dir.mkdir(parents=True)
    local = make_bars(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
            }
        ]
    )
    local.to_csv(daily_dir / "ES.csv", index=False)

    result = apply_reconciliation_decisions(
        source_dir=source_dir,
        decisions=[
            {
                "symbol": "/ES",
                "frequency": "daily",
                "comparison_key": "2026-01-01",
                "decision": "yahoo",
                "local_open": 100,
                "local_high": 101,
                "local_low": 99,
                "local_close": 100,
                "reference_open": 110,
                "reference_high": 111,
                "reference_low": 109,
                "reference_close": 110,
            }
        ],
    )

    updated = pd.read_csv(daily_dir / "ES.csv")
    reviewed = pd.read_csv(source_dir / "reviewed_bars.csv")

    assert result["updated_bars"] == 1
    assert updated.loc[0, "open"] == 110
    assert updated.loc[0, "source"] == "reviewed_yahoo"
    assert reviewed.loc[0, "comparison_key"] == "2026-01-01"
    assert reviewed.loc[0, "selected_source"] == "yahoo"
