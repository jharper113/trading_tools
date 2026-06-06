import pandas as pd

from download_market_data import (
    append_market_data,
    extract_authorization_code,
    normalize_bar_frame,
    normalize_frequency,
    normalize_symbol,
    output_file_for,
    schwab_authorization_url,
    schwab_price_history_params,
    write_symbol_manifest,
)


def test_normalize_symbol_adds_futures_slash():
    assert normalize_symbol("es") == "/ES"
    assert normalize_symbol("/6e") == "/6E"


def test_normalize_frequency_aliases():
    assert normalize_frequency("1d") == "daily"
    assert normalize_frequency("5-minute") == "5min"


def test_normalize_bar_frame_accepts_epoch_milliseconds():
    bars = pd.DataFrame(
        [
            {
                "datetime": 1_767_225_600_000,
                "open": "100",
                "high": "105",
                "low": "99",
                "close": "104",
                "volume": "10",
            }
        ]
    )

    normalized = normalize_bar_frame(
        bars,
        symbol="ES",
        frequency="5m",
        source="test",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    assert normalized.loc[0, "symbol"] == "/ES"
    assert normalized.loc[0, "frequency"] == "5min"
    assert normalized.loc[0, "open"] == 100
    assert normalized.loc[0, "retrieved_at"] == "2026-01-01T00:00:00Z"


def test_append_market_data_dedupes_by_symbol_frequency_timestamp():
    existing = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 105,
                    "low": 99,
                    "close": 104,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="old",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    incoming = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 101,
                    "high": 106,
                    "low": 100,
                    "close": 105,
                },
                {
                    "timestamp": "2026-01-02T00:00:00Z",
                    "open": 105,
                    "high": 107,
                    "low": 103,
                    "close": 106,
                },
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="new",
        retrieved_at="2026-01-02T00:00:00Z",
    )

    combined = append_market_data(existing, incoming)

    assert len(combined) == 2
    assert combined.loc[0, "open"] == 101
    assert combined.loc[1, "close"] == 106


def test_append_market_data_preserves_reviewed_existing_bar():
    existing = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 105,
                    "low": 99,
                    "close": 104,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="reviewed",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    incoming = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 101,
                    "high": 106,
                    "low": 100,
                    "close": 105,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="schwab",
        retrieved_at="2026-01-02T00:00:00Z",
    )
    reviewed = pd.DataFrame(
        [
            {
                "symbol": "/ES",
                "frequency": "daily",
                "comparison_key": "2026-01-01",
                "selected_source": "local",
                "reviewed_at": "2026-01-03T00:00:00Z",
            }
        ]
    )

    combined = append_market_data(
        existing,
        incoming,
        reviewed_bars=reviewed,
    )

    assert len(combined) == 1
    assert combined.loc[0, "open"] == 100
    assert combined.loc[0, "source"] == "reviewed"


def test_output_file_for_partitions_by_frequency(tmp_path):
    output_path = output_file_for(tmp_path, "/6E", "5min")

    assert output_path == tmp_path / "5min" / "6E.csv"


def test_write_symbol_manifest(tmp_path):
    manifest_path = write_symbol_manifest(tmp_path, ["/ES", "CL"])

    manifest = pd.read_csv(manifest_path)

    assert list(manifest["symbol"]) == ["/ES", "/CL"]
    assert manifest.loc[0, "name"] == "E-mini S&P 500"


def test_schwab_price_history_params_for_5min():
    params = schwab_price_history_params(
        symbol="ES",
        frequency="5min",
        start="2026-01-01",
        end="2026-01-02",
    )

    assert params["symbol"] == "/ES"
    assert params["frequencyType"] == "minute"
    assert params["frequency"] == 5
    assert params["periodType"] == "day"
    assert params["period"] == 1
    assert "startDate" in params
    assert "endDate" in params


def test_schwab_price_history_params_daily_uses_default_window():
    params = schwab_price_history_params(
        symbol="GC",
        frequency="daily",
    )

    assert params["symbol"] == "/GC"
    assert params["periodType"] == "year"
    assert params["period"] == 1
    assert params["frequencyType"] == "daily"
    assert params["frequency"] == 1


def test_schwab_price_history_params_can_request_all_daily_history():
    params = schwab_price_history_params(
        symbol="GC",
        frequency="daily",
        max_history=True,
    )

    assert params["period"] == 20


def test_schwab_price_history_params_can_request_all_intraday_history():
    params = schwab_price_history_params(
        symbol="ES",
        frequency="5min",
        max_history=True,
    )

    assert params["periodType"] == "day"
    assert params["period"] == 10


def test_schwab_authorization_url_contains_required_oauth_fields():
    auth_url = schwab_authorization_url(
        client_id="client-123",
        redirect_uri="https://developer.schwab.com/oauth2-redirect.html",
    )

    assert auth_url.startswith(
        "https://api.schwabapi.com/v1/oauth/authorize?"
    )
    assert "response_type=code" in auth_url
    assert "client_id=client-123" in auth_url
    assert "scope=readonly" in auth_url
    assert "redirect_uri=https%3A%2F%2Fdeveloper.schwab.com" in auth_url


def test_extract_authorization_code_from_full_redirect_url():
    redirect_url = (
        "https://developer.schwab.com/oauth2-redirect.html"
        "?code=AUTH_CODE_123&state=abc"
    )

    assert extract_authorization_code(redirect_url) == "AUTH_CODE_123"


def test_extract_authorization_code_accepts_raw_code():
    assert extract_authorization_code("AUTH_CODE_123") == "AUTH_CODE_123"
