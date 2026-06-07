import pandas as pd

from download_market_data import (
    DEFAULT_SYMBOLS,
    EQUITY_PRODUCTS,
    FUTURES_PRODUCTS,
    SchwabProvider,
    aggregate_bars_to_60min,
    append_market_data,
    apply_daily_intraday_fix_candidates,
    auto_fix_integrity_issues,
    build_daily_intraday_fix_candidates,
    build_daily_intraday_quality_report,
    build_integrity_report,
    elapsed_text,
    extract_authorization_code,
    load_schwab_token_file,
    normalize_bar_frame,
    normalize_frequency,
    normalize_symbol,
    output_file_for,
    parse_args,
    repair_saved_integrity,
    save_schwab_token_file,
    save_market_data,
    schwab_authorization_url,
    schwab_client_credentials,
    schwab_price_history_params,
    token_file_for,
    write_quality_reports,
    write_symbol_manifest,
)


def test_normalize_symbol_adds_futures_slash():
    assert normalize_symbol("es") == "/ES"
    assert normalize_symbol("/6e") == "/6E"
    assert normalize_symbol("SPY") == "SPY"


def test_normalize_frequency_aliases():
    assert normalize_frequency("1d") == "daily"
    assert normalize_frequency("5-minute") == "5min"
    assert normalize_frequency("60m") == "60min"
    assert normalize_frequency("1h") == "60min"


def test_elapsed_text_formats_seconds_minutes_and_hours():
    assert elapsed_text(9) == "9s"
    assert elapsed_text(61) == "1m 1s"
    assert elapsed_text(3661) == "1h 1m 1s"


def test_parse_args_prefers_long_all_flag_but_accepts_legacy_alias(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--all"],
    )

    assert parse_args().all

    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "-all"],
    )

    assert parse_args().all


def test_default_symbols_cover_liquid_futures_categories():
    categories = {
        FUTURES_PRODUCTS[symbol]["category"]
        for symbol in DEFAULT_SYMBOLS
    }

    assert categories >= {
        "agriculture",
        "currency",
        "energy",
        "equity_index",
        "metal",
        "rates",
        "soft",
    }
    assert "/ES" in DEFAULT_SYMBOLS
    assert "/NQ" in DEFAULT_SYMBOLS
    assert "/ZB" in DEFAULT_SYMBOLS
    assert "/KC" in DEFAULT_SYMBOLS


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


def test_build_integrity_report_flags_invalid_ohlc():
    bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 99,
                    "low": 101,
                    "close": 100,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="test",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    report = build_integrity_report(bars)

    assert set(report["issue_type"]) == {"high_below_ohlc", "low_above_ohlc"}


def test_auto_fix_integrity_issues_repairs_high_low_and_drops_bad_prices():
    bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 99,
                    "low": 101,
                    "close": 100,
                },
                {
                    "timestamp": "2026-01-02T00:00:00Z",
                    "open": 0,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                },
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="test",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    fixed, report = auto_fix_integrity_issues(bars)

    assert len(report) == 3
    assert len(fixed) == 1
    assert fixed.loc[0, "high"] == 101
    assert fixed.loc[0, "low"] == 100


def test_daily_intraday_quality_report_flags_mismatched_daily_bar():
    daily = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 120,
                    "low": 99,
                    "close": 110,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="daily",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    intraday = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T14:30:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                },
                {
                    "timestamp": "2026-01-01T14:35:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 98,
                    "close": 101,
                },
            ]
        ),
        symbol="/ES",
        frequency="5min",
        source="5min",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    report = build_daily_intraday_quality_report(
        daily,
        intraday,
        threshold_pct=0.25,
    )
    candidates = build_daily_intraday_fix_candidates(
        daily,
        intraday,
        threshold_pct=0.25,
    )

    assert set(report["field"]) == {"high", "low", "close"}
    assert len(candidates) == 1
    assert candidates.loc[0, "high"] == 102
    assert candidates.loc[0, "close"] == 101


def test_save_market_data_auto_fixes_integrity_before_writing(tmp_path):
    bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 99,
                    "low": 101,
                    "close": 100,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="test",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    output_path, row_count = save_market_data(tmp_path, "/ES", "daily", bars)
    saved = pd.read_csv(output_path)

    assert row_count == 1
    assert saved.loc[0, "high"] == 101
    assert saved.loc[0, "low"] == 100


def test_repair_saved_integrity_rewrites_existing_bad_file(tmp_path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    bad_bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 99,
                    "low": 101,
                    "close": 100,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="bad",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    bad_bars.to_csv(output_file_for(tmp_path, "/ES", "daily"), index=False)

    result = repair_saved_integrity(tmp_path, ["/ES"], ["daily"])
    saved = pd.read_csv(output_file_for(tmp_path, "/ES", "daily"))

    assert result["repaired_issue_rows"] == 2
    assert saved.loc[0, "high"] == 101
    assert saved.loc[0, "low"] == 100


def test_write_quality_reports_outputs_summary_and_candidates(tmp_path):
    daily = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 120,
                    "low": 99,
                    "close": 110,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="daily",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    intraday = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T14:30:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                },
                {
                    "timestamp": "2026-01-01T14:35:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 98,
                    "close": 101,
                },
            ]
        ),
        symbol="/ES",
        frequency="5min",
        source="5min",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    save_market_data(tmp_path, "/ES", "daily", daily)
    save_market_data(tmp_path, "/ES", "5min", intraday)

    reports = write_quality_reports(tmp_path, ["/ES"])

    assert reports["summary_path"].exists()
    assert reports["daily_intraday_path"].exists()
    assert len(reports["daily_fix_candidates"]) == 1


def test_write_quality_reports_includes_60min_integrity(tmp_path):
    hourly = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T14:00:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                }
            ]
        ),
        symbol="/ES",
        frequency="60min",
        source="hourly",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    save_market_data(tmp_path, "/ES", "60min", hourly)

    reports = write_quality_reports(tmp_path, ["/ES"], frequencies=["60min"])

    row = reports["summary"].iloc[0]
    assert row["frequency"] == "60min"
    assert row["bars_checked"] == 1
    assert row["status"] == "passed"


def test_aggregate_bars_to_60min_combines_30min_bars():
    bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T14:00:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "volume": 10,
                },
                {
                    "timestamp": "2026-01-01T14:30:00Z",
                    "open": 100.5,
                    "high": 102,
                    "low": 100,
                    "close": 101,
                    "volume": 20,
                },
            ]
        ),
        symbol="/ES",
        frequency="60min",
        source="schwab",
        retrieved_at="2026-01-01T00:00:00Z",
    )

    aggregated = aggregate_bars_to_60min(bars, symbol="/ES")

    assert len(aggregated) == 1
    assert aggregated.loc[0, "timestamp"] == "2026-01-01T14:00:00Z"
    assert aggregated.loc[0, "open"] == 100
    assert aggregated.loc[0, "high"] == 102
    assert aggregated.loc[0, "low"] == 99
    assert aggregated.loc[0, "close"] == 101
    assert aggregated.loc[0, "volume"] == 30
    assert aggregated.loc[0, "frequency"] == "60min"


def test_apply_daily_intraday_fix_candidates_rewrites_daily_bar(tmp_path):
    daily = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100,
                    "high": 120,
                    "low": 99,
                    "close": 110,
                }
            ]
        ),
        symbol="/ES",
        frequency="daily",
        source="daily",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    intraday = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01T14:30:00Z",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                },
                {
                    "timestamp": "2026-01-01T14:35:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 98,
                    "close": 101,
                },
            ]
        ),
        symbol="/ES",
        frequency="5min",
        source="5min",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    save_market_data(tmp_path, "/ES", "daily", daily)
    candidates = build_daily_intraday_fix_candidates(
        daily,
        intraday,
        threshold_pct=0.25,
    )

    result = apply_daily_intraday_fix_candidates(
        tmp_path,
        candidates,
        min_intraday_bars=2,
    )
    saved = pd.read_csv(output_file_for(tmp_path, "/ES", "daily"))

    assert result["updated_bars"] == 1
    assert saved.loc[0, "source"] == "candidate_from_5min"
    assert saved.loc[0, "high"] == 102


def test_output_file_for_partitions_by_frequency(tmp_path):
    output_path = output_file_for(tmp_path, "/6E", "5min")

    assert output_path == tmp_path / "5min" / "6E.csv"

    hourly_path = output_file_for(tmp_path, "/6E", "60m")

    assert hourly_path == tmp_path / "60min" / "6E.csv"

    equity_path = output_file_for(tmp_path, "SPY", "daily")

    assert equity_path == tmp_path / "daily" / "SPY.csv"


def test_write_symbol_manifest(tmp_path):
    manifest_path = write_symbol_manifest(tmp_path, ["/ES", "CL", "SPY"])

    manifest = pd.read_csv(manifest_path)

    assert list(manifest["symbol"]) == ["/ES", "/CL", "SPY"]
    assert manifest.loc[0, "name"] == "E-mini S&P 500"
    assert manifest.loc[1, "category"] == "energy"
    assert manifest.loc[2, "name"] == EQUITY_PRODUCTS["SPY"]["name"]


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


def test_schwab_price_history_params_for_60min():
    params = schwab_price_history_params(
        symbol="ES",
        frequency="60min",
        start="2026-01-01",
        end="2026-01-02",
    )

    assert params["symbol"] == "/ES"
    assert params["frequencyType"] == "minute"
    assert params["frequency"] == 30
    assert params["periodType"] == "day"


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


def test_schwab_price_history_params_equity_symbol_has_no_futures_slash():
    params = schwab_price_history_params(
        symbol="SPY",
        frequency="daily",
    )

    assert params["symbol"] == "SPY"
    assert params["frequencyType"] == "daily"


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

    hourly_params = schwab_price_history_params(
        symbol="ES",
        frequency="60min",
        max_history=True,
    )

    assert hourly_params["periodType"] == "day"
    assert hourly_params["period"] == 10
    assert hourly_params["frequency"] == 30


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


def test_schwab_token_file_round_trips_and_sets_private_permissions(tmp_path):
    token_path = tmp_path / "tokens.json"

    saved_path = save_schwab_token_file(
        token_path,
        {
            "access_token": "ACCESS",
            "refresh_token": "REFRESH",
        },
    )
    loaded = load_schwab_token_file(token_path)

    assert saved_path == token_path
    assert loaded["access_token"] == "ACCESS"
    assert loaded["refresh_token"] == "REFRESH"
    assert loaded["retrieved_at"]
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"


def test_token_file_for_prefers_argument_then_env(monkeypatch, tmp_path):
    env_path = tmp_path / "env_tokens.json"
    arg_path = tmp_path / "arg_tokens.json"
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(env_path))

    assert token_file_for(arg_path) == arg_path
    assert token_file_for() == env_path


def test_schwab_client_credentials_can_read_environment(monkeypatch):
    monkeypatch.setenv("SCHWAB_CLIENT_ID", "client-id")
    monkeypatch.setenv("SCHWAB_CLIENT_SECRET", "client-secret")

    assert schwab_client_credentials(prompt=False) == (
        "client-id",
        "client-secret",
    )


def test_schwab_provider_refreshes_saved_refresh_token(monkeypatch, tmp_path):
    token_path = tmp_path / "tokens.json"
    save_schwab_token_file(
        token_path,
        {
            "access_token": "OLD_ACCESS",
            "refresh_token": "REFRESH",
        },
    )
    monkeypatch.setenv("SCHWAB_CLIENT_ID", "client-id")
    monkeypatch.setenv("SCHWAB_CLIENT_SECRET", "client-secret")

    def fake_refresh(client_id, client_secret, refresh_token):
        assert client_id == "client-id"
        assert client_secret == "client-secret"
        assert refresh_token == "REFRESH"
        return {
            "access_token": "NEW_ACCESS",
            "refresh_token": "NEW_REFRESH",
        }

    monkeypatch.setattr(
        "download_market_data.exchange_schwab_refresh_token",
        fake_refresh,
    )

    provider = SchwabProvider(token_file=token_path)
    saved = load_schwab_token_file(token_path)

    assert provider.access_token == "NEW_ACCESS"
    assert saved["refresh_token"] == "NEW_REFRESH"
