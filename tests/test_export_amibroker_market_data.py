import json
from pathlib import Path

import pandas as pd

from export_amibroker_market_data import export_amibroker_market_data
from download_market_data import DEFAULT_SYMBOLS, safe_symbol_filename


CANONICAL_COLUMNS = [
    "timestamp",
    "date",
    "symbol",
    "frequency",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "source",
    "retrieved_at",
]


def write_market_data(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=CANONICAL_COLUMNS).to_csv(path, index=False)


def write_instrument_settings(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "full_name",
            "point_value",
            "tick_size",
            "margin_deposit",
            "margin_as_of",
            "round_lot_size",
            "currency",
            "notes",
        ],
    ).to_csv(path, index=False)


def test_export_builds_daily_file_with_filename_tickers_and_trading_dates(
    tmp_path,
):
    market_data = tmp_path / "market_data"
    write_market_data(
        market_data / "daily" / "ES.csv",
        [
            {
                "timestamp": "2026-01-03T00:00:00Z",
                "date": "2026-01-03",
                "symbol": "/ES",
                "frequency": "daily",
                "open": 6100,
                "high": 6125,
                "low": 6080,
                "close": 6110,
                "volume": 100,
                "open_interest": 200,
                "source": "test",
            }
        ],
    )
    write_market_data(
        market_data / "daily" / "6E.csv",
        [
            {
                "timestamp": "2026-01-02T00:00:00Z",
                "date": "2026-01-02",
                "symbol": "/6E",
                "frequency": "daily",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "volume": None,
                "open_interest": None,
                "source": "test",
            }
        ],
    )

    result = export_amibroker_market_data(market_data)

    exported = pd.read_csv(result["daily_path"], dtype={"ticker": str})
    assert list(exported.columns) == [
        "ticker",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]
    assert exported["ticker"].tolist() == ["6E", "ES"]
    assert exported["date"].tolist() == ["2026-01-02", "2026-01-03"]
    assert exported.loc[0, "volume"] == 0
    assert exported.loc[0, "open_interest"] == 0


def test_export_converts_intraday_utc_to_detroit_with_dst(tmp_path):
    market_data = tmp_path / "market_data"
    write_market_data(
        market_data / "5min" / "ES.csv",
        [
            {
                "timestamp": "2026-07-01T13:30:00Z",
                "date": "2026-07-01",
                "symbol": "/ES",
                "frequency": "5min",
                "open": 6100,
                "high": 6101,
                "low": 6099,
                "close": 6100.5,
                "volume": 10,
                "open_interest": 20,
                "source": "test",
            },
            {
                "timestamp": "2026-01-02T14:30:00Z",
                "date": "2026-01-02",
                "symbol": "/ES",
                "frequency": "5min",
                "open": 6000,
                "high": 6001,
                "low": 5999,
                "close": 6000.5,
                "volume": 11,
                "open_interest": 21,
                "source": "test",
            },
        ],
    )

    result = export_amibroker_market_data(
        market_data,
        timezone_name="America/Detroit",
    )

    exported = pd.read_csv(result["intraday_path"], dtype={"ticker": str})
    assert exported[["date", "time"]].to_dict("records") == [
        {"date": "2026-01-02", "time": "09:30:00"},
        {"date": "2026-07-01", "time": "09:30:00"},
    ]


def test_export_skips_empty_sources_and_invalid_prices(tmp_path):
    market_data = tmp_path / "market_data"
    write_market_data(market_data / "daily" / "EMPTY.csv", [])
    write_market_data(
        market_data / "daily" / "ES.csv",
        [
            {
                "timestamp": "2026-01-02T00:00:00Z",
                "date": "2026-01-02",
                "symbol": "/ES",
                "frequency": "daily",
                "open": 6000,
                "high": 6010,
                "low": 5990,
                "close": None,
                "volume": 10,
                "source": "test",
            }
        ],
    )

    result = export_amibroker_market_data(market_data)

    exported = pd.read_csv(result["daily_path"])
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert exported.empty
    assert manifest["daily"]["source_files"] == 2
    assert manifest["daily"]["exported_rows"] == 0
    assert manifest["daily"]["skipped_rows"] == 1


def test_export_writes_completion_manifest_after_both_csv_files(tmp_path):
    market_data = tmp_path / "market_data"

    result = export_amibroker_market_data(market_data)

    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    assert Path(result["daily_path"]).exists()
    assert Path(result["intraday_path"]).exists()
    assert manifest["timezone"] == "America/Detroit"
    assert manifest["daily"]["file"] == "daily.csv"
    assert manifest["intraday"]["file"] == "5min.csv"


def test_export_writes_separate_safe_instrument_property_files(tmp_path):
    market_data = tmp_path / "market_data"
    write_market_data(
        market_data / "daily" / "ES.csv",
        [{
            "timestamp": "2026-01-02T00:00:00Z",
            "date": "2026-01-02",
            "symbol": "/ES",
            "frequency": "daily",
            "open": 6000,
            "high": 6010,
            "low": 5990,
            "close": 6005,
        }],
    )
    write_market_data(
        market_data / "daily" / "6J.csv",
        [{
            "timestamp": "2026-01-02T00:00:00Z",
            "date": "2026-01-02",
            "symbol": "/6J",
            "frequency": "daily",
            "open": 0.006,
            "high": 0.0061,
            "low": 0.0059,
            "close": 0.006,
        }],
    )
    settings_path = tmp_path / "instrument_settings.csv"
    write_instrument_settings(
        settings_path,
        [
            {
                "ticker": "ES",
                "full_name": "E-mini S&P 500",
                "point_value": 50,
                "tick_size": 0.25,
                "margin_deposit": 28644,
                "margin_as_of": "2026-09-19",
                "round_lot_size": 1,
                "currency": "USD",
                "notes": "",
            },
            {
                "ticker": "6J",
                "full_name": "Japanese Yen",
                "point_value": "",
                "tick_size": "",
                "margin_deposit": "",
                "margin_as_of": "",
                "round_lot_size": 1,
                "currency": "USD",
                "notes": "Historical price-scale discontinuity.",
            },
        ],
    )

    result = export_amibroker_market_data(
        market_data,
        instrument_settings_path=settings_path,
    )

    details = pd.read_csv(result["instrument_details_path"])
    points = pd.read_csv(result["point_values_path"])
    ticks = pd.read_csv(result["tick_sizes_path"])
    margins = pd.read_csv(result["margins_path"])
    assert details["ticker"].tolist() == ["6J", "ES"]
    assert points.to_dict("records") == [{"ticker": "ES", "point_value": 50.0}]
    assert ticks.to_dict("records") == [{"ticker": "ES", "tick_size": 0.25}]
    assert margins.to_dict("records") == [
        {
            "ticker": "ES",
            "margin_deposit": 28644.0,
            "margin_as_of": "2026-09-19",
        }
    ]
    assert result["manifest"]["instrument_properties"] == {
        "details_file": "instrument_details.csv",
        "details_rows": 2,
        "point_values_file": "point_values.csv",
        "point_values_rows": 1,
        "tick_sizes_file": "tick_sizes.csv",
        "tick_sizes_rows": 1,
        "margins_file": "margins.csv",
        "margins_rows": 1,
        "margin_as_of": "2026-09-19",
    }


def test_export_rejects_market_symbol_missing_from_settings(tmp_path):
    market_data = tmp_path / "market_data"
    write_market_data(
        market_data / "daily" / "ES.csv",
        [{
            "timestamp": "2026-01-02T00:00:00Z",
            "date": "2026-01-02",
            "symbol": "/ES",
            "frequency": "daily",
            "open": 6000,
            "high": 6010,
            "low": 5990,
            "close": 6005,
        }],
    )
    settings_path = tmp_path / "instrument_settings.csv"
    write_instrument_settings(settings_path, [])

    try:
        export_amibroker_market_data(
            market_data,
            instrument_settings_path=settings_path,
        )
    except ValueError as error:
        assert "missing AmiBroker instrument settings: ES" in str(error)
    else:
        raise AssertionError("Expected missing settings to fail the export")


def test_export_rejects_margin_without_as_of_date(tmp_path):
    market_data = tmp_path / "market_data"
    settings_path = tmp_path / "instrument_settings.csv"
    write_instrument_settings(
        settings_path,
        [{
            "ticker": "ES",
            "full_name": "E-mini S&P 500",
            "point_value": 50,
            "tick_size": 0.25,
            "margin_deposit": 28644,
            "margin_as_of": "",
            "round_lot_size": 1,
            "currency": "USD",
            "notes": "",
        }],
    )

    try:
        export_amibroker_market_data(
            market_data,
            instrument_settings_path=settings_path,
        )
    except ValueError as error:
        assert "margin_deposit and margin_as_of must be supplied together: ES" in str(error)
    else:
        raise AssertionError("Expected an undated margin to fail the export")


def test_default_instrument_settings_cover_every_download_symbol():
    settings = pd.read_csv("amibroker_import/instrument_settings.csv")
    expected = {safe_symbol_filename(symbol) for symbol in DEFAULT_SYMBOLS}

    assert set(settings["ticker"]) == expected
    assert not settings["ticker"].duplicated().any()
    assert settings["full_name"].notna().all()
    assert settings["round_lot_size"].eq(1).all()
    assert settings["currency"].notna().all()

    unsafe = settings.set_index("ticker").loc[["6J", "HG", "SI", "RF___CCB"]]
    assert unsafe["point_value"].isna().all()
    assert unsafe.loc[["6J", "HG", "SI"], "tick_size"].isna().all()


def test_powershell_importer_contains_configured_windows_paths():
    script = Path("amibroker_import/Import-MarketData.ps1").read_text()

    assert r"Z:\04_code\python\trading_tools\data\market_data" in script
    assert r"C:\Program Files (x86)\AmiBroker\Broker.exe" in script
    assert r"Z:\04_code\amibroker\databases\Harp_daily" in script
    assert r"Z:\04_code\amibroker\databases\Harp_intraday" in script
    assert 'New-Object -ComObject "Broker.Application"' in script
    assert "$ab.SaveDatabase()" in script
    assert "$InstrumentDetailsFile" in script
    assert "$PointValuesFile" in script
    assert "$TickSizesFile" in script
    assert "$MarginsFile" in script
    assert "$InstrumentDetailsFormat" in script
    assert "$PointValuesFormat" in script
    assert "$TickSizesFormat" in script
    assert "$MarginsFormat" in script
