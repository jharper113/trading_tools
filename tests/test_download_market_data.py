import gzip
import io
from types import SimpleNamespace
from urllib.error import HTTPError

import pandas as pd
import pytest

import download_market_data as market_data_module
from download_market_data import (
    DEFAULT_SYMBOLS,
    EQUITY_PRODUCTS,
    FUTURES_PRODUCTS,
    LEGACY_PRODUCTS,
    SchwabProvider,
    aggregate_bars_to_60min,
    append_market_data,
    apply_daily_intraday_fix_candidates,
    auto_fix_integrity_issues,
    build_daily_intraday_fix_candidates,
    build_daily_intraday_quality_report,
    build_integrity_report,
    create_provider,
    elapsed_text,
    extract_authorization_code,
    load_schwab_token_file,
    main,
    normalize_bar_frame,
    normalize_frequency,
    normalize_symbol,
    output_file_for,
    parse_args,
    prompt_for_schwab_token_payload,
    repair_saved_integrity,
    request_schwab_token,
    save_schwab_token_file,
    save_market_data,
    schwab_provider_symbol,
    schwab_access_token_expires_at,
    schwab_access_token_is_current,
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


@pytest.mark.parametrize(
    ("stored_symbol", "provider_symbol"),
    [
        ("AUDUSD", "AUD/USD"),
        ("EURJPY", "EUR/JPY"),
        ("EURUSD", "EUR/USD"),
        ("GBPUSD", "GBP/USD"),
        ("LB___CCB", "/LBS"),
        ("NZDUSD", "NZD/USD"),
        ("RF___CCB", "/RF"),
        ("USDCAD", "USD/CAD"),
        ("USDCHF", "USD/CHF"),
        ("USDJPY", "USD/JPY"),
    ],
)
def test_schwab_provider_symbol_maps_stored_legacy_names(
    stored_symbol,
    provider_symbol,
):
    assert normalize_symbol(stored_symbol) == stored_symbol
    assert schwab_provider_symbol(stored_symbol) == provider_symbol
    assert schwab_price_history_params(stored_symbol, "daily")["symbol"] == (
        provider_symbol
    )


@pytest.mark.parametrize(
    "symbol",
    [
        "BZ",
        "EMD",
        "GF",
        "KE",
        "6M",
        "6N",
        "NKD",
        "ZO",
        "PA",
        "UB",
        "VX",
        "GE",
    ],
)
def test_validated_mappings_are_canonical_futures_products(symbol):
    canonical_symbol = f"/{symbol}"

    assert normalize_symbol(symbol) == canonical_symbol
    assert canonical_symbol in FUTURES_PRODUCTS
    assert symbol not in LEGACY_PRODUCTS
    assert schwab_provider_symbol(symbol) == canonical_symbol


def test_normalize_frequency_aliases():
    assert normalize_frequency("1d") == "daily"
    assert normalize_frequency("5-minute") == "5min"
    assert normalize_frequency("60m") == "60min"
    assert normalize_frequency("1h") == "60min"


def test_elapsed_text_formats_seconds_minutes_and_hours():
    assert elapsed_text(9) == "9s"
    assert elapsed_text(61) == "1m 1s"
    assert elapsed_text(3661) == "1h 1m 1s"


def test_send_desktop_notification_supplies_cron_desktop_environment(
    monkeypatch,
):
    calls = []

    class SuccessfulNotification:
        returncode = 0

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return SuccessfulNotification()

    monkeypatch.setattr("download_market_data.os.getuid", lambda: 1000)

    delivered = market_data_module.send_desktop_notification(
        title="Market data update succeeded",
        message="All jobs completed.",
        urgency="normal",
        notifier_path="/usr/bin/notify-send",
        runner=fake_runner,
        environment={},
    )

    command, options = calls[0]
    assert delivered
    assert command == [
        "/usr/bin/notify-send",
        "--urgency=normal",
        "Market data update succeeded",
        "All jobs completed.",
    ]
    assert options["env"]["DISPLAY"] == ":0"
    assert options["env"]["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert options["env"]["DBUS_SESSION_BUS_ADDRESS"] == (
        "unix:path=/run/user/1000/bus"
    )


def test_send_desktop_notification_is_best_effort():
    def unavailable_notifier(command, **kwargs):
        raise OSError("desktop session is unavailable")

    delivered = market_data_module.send_desktop_notification(
        title="Market data update failed",
        message="Provider request failed.",
        urgency="critical",
        notifier_path="/usr/bin/notify-send",
        runner=unavailable_notifier,
        environment={},
    )

    assert not delivered


def test_run_cli_sends_success_notification_when_requested():
    notifications = []

    market_data_module.run_cli(
        args=SimpleNamespace(notify=True),
        run_main=lambda args: None,
        notifier=lambda **kwargs: notifications.append(kwargs),
    )

    assert notifications == [
        {
            "title": "Market data update succeeded",
            "message": "download_market_data.py completed successfully.",
            "urgency": "normal",
        }
    ]


def test_run_cli_sends_failure_notification_and_preserves_cli_error():
    notifications = []

    def fail_market_data_run(args):
        raise RuntimeError("2 of 21 market data jobs failed")

    with pytest.raises(
        SystemExit,
        match="error: 2 of 21 market data jobs failed",
    ):
        market_data_module.run_cli(
            args=SimpleNamespace(notify=True),
            run_main=fail_market_data_run,
            notifier=lambda **kwargs: notifications.append(kwargs),
        )

    assert notifications == [
        {
            "title": "Market data update failed",
            "message": "2 of 21 market data jobs failed",
            "urgency": "critical",
        }
    ]


def test_run_cli_notifier_error_does_not_turn_success_into_failure():
    def fail_notification(**kwargs):
        raise RuntimeError("notification service failed")

    market_data_module.run_cli(
        args=SimpleNamespace(notify=True),
        run_main=lambda args: None,
        notifier=fail_notification,
    )


def test_run_cli_notifier_error_does_not_mask_market_data_error():
    def fail_market_data_run(args):
        raise ValueError("invalid market data settings")

    def fail_notification(**kwargs):
        raise RuntimeError("notification service failed")

    with pytest.raises(SystemExit, match="error: invalid market data settings"):
        market_data_module.run_cli(
            args=SimpleNamespace(notify=True),
            run_main=fail_market_data_run,
            notifier=fail_notification,
        )


def test_run_cli_notifies_and_reraises_unexpected_error():
    notifications = []
    unexpected_error = LookupError("unexpected provider response")

    def fail_market_data_run(args):
        raise unexpected_error

    with pytest.raises(LookupError) as caught:
        market_data_module.run_cli(
            args=SimpleNamespace(notify=True),
            run_main=fail_market_data_run,
            notifier=lambda **kwargs: notifications.append(kwargs),
        )

    assert caught.value is unexpected_error
    assert notifications == [
        {
            "title": "Market data update failed",
            "message": "unexpected provider response",
            "urgency": "critical",
        }
    ]


def test_parse_args_prefers_long_all_flag_but_accepts_legacy_alias(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--provider", "schwab", "--all"],
    )

    assert parse_args().all

    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--provider", "schwab", "-all"],
    )

    assert parse_args().all


def test_parse_args_requires_input_dir_for_csv_provider(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--provider", "csv"],
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_parse_args_allows_quality_only_without_input_dir(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--quality-only"],
    )

    assert parse_args().quality_only


def test_parse_args_accepts_schwab_authorization_and_batch_flags(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "schwab",
            "--auth-only",
            "--force-reauth",
            "--continue-on-error",
        ],
    )

    args = parse_args()

    assert args.auth_only
    assert args.force_reauth
    assert args.continue_on_error


def test_parse_args_accepts_desktop_notification_flag(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["download_market_data.py", "--provider", "schwab", "--notify"],
    )

    assert parse_args().notify


def test_parse_args_accepts_amibroker_export_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--quality-only",
            "--export-amibroker",
            "--amibroker-timezone",
            "America/Detroit",
        ],
    )

    args = parse_args()

    assert args.export_amibroker
    assert args.amibroker_timezone == "America/Detroit"


def test_quality_only_can_refresh_amibroker_exports(monkeypatch, tmp_path):
    output_dir = tmp_path / "market-data"
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--quality-only",
            "--symbols",
            "/ES",
            "--frequencies",
            "daily",
            "5min",
            "--output-dir",
            str(output_dir),
            "--export-amibroker",
        ],
    )

    main()

    assert (output_dir / "amibroker" / "daily.csv").exists()
    assert (output_dir / "amibroker" / "5min.csv").exists()
    assert (output_dir / "amibroker" / "export_complete.json").exists()


def test_parse_args_rejects_schwab_authorization_flags_for_csv(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "csv",
            "--input-dir",
            "vendor-data",
            "--auth-only",
        ],
    )

    with pytest.raises(SystemExit):
        parse_args()


@pytest.mark.parametrize("authorization_flag", ["--auth-only", "--force-reauth"])
def test_parse_args_rejects_authorization_flags_with_quality_only(
    monkeypatch,
    authorization_flag,
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "schwab",
            "--quality-only",
            authorization_flag,
        ],
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_main_auth_only_prepares_provider_without_downloading(
    monkeypatch,
    tmp_path,
    capsys,
):
    token_path = tmp_path / "tokens.json"

    class ReadyProvider:
        def __init__(self):
            self.token_file = token_path

        def fetch_bars(self, *args, **kwargs):
            raise AssertionError("auth-only must not download bars")

    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "schwab",
            "--auth-only",
            "--force-reauth",
            "--output-dir",
            str(tmp_path / "market-data"),
        ],
    )
    monkeypatch.setattr(
        "download_market_data.create_provider",
        lambda args: ReadyProvider(),
    )

    main()

    assert not (tmp_path / "market-data" / "symbols.csv").exists()
    assert "Schwab authorization is ready" in capsys.readouterr().out


def test_main_continue_on_error_processes_remaining_jobs(
    monkeypatch,
    tmp_path,
):
    calls = []
    output_dir = tmp_path / "market-data"
    spy_bars = normalize_bar_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": "2026-08-27T20:00:00Z",
                    "open": 650,
                    "high": 652,
                    "low": 649,
                    "close": 651,
                    "volume": 100,
                }
            ]
        ),
        symbol="SPY",
        frequency="daily",
        source="test",
    )

    class PartiallyFailingProvider:
        token_file = tmp_path / "tokens.json"

        def fetch_bars(self, symbol, frequency, start=None, end=None):
            calls.append((symbol, frequency))

            if symbol == "/ES":
                raise RuntimeError("unsupported symbol")

            return spy_bars

    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "schwab",
            "--symbols",
            "/ES",
            "SPY",
            "--frequencies",
            "daily",
            "--continue-on-error",
            "--output-dir",
            str(output_dir),
        ],
    )
    monkeypatch.setattr(
        "download_market_data.create_provider",
        lambda args: PartiallyFailingProvider(),
    )

    with pytest.raises(RuntimeError, match="1 of 2 market data jobs failed"):
        main()

    assert calls == [("/ES", "daily"), ("SPY", "daily")]
    assert output_file_for(output_dir, "SPY", "daily").exists()


def test_default_symbols_cover_liquid_futures_categories():
    categories = {
        FUTURES_PRODUCTS[symbol]["category"]
        for symbol in DEFAULT_SYMBOLS
        if symbol in FUTURES_PRODUCTS
    }

    assert categories >= {
        "agriculture",
        "currency",
        "energy",
        "equity_index",
        "crypto",
        "metal",
        "rates",
        "soft",
    }
    assert "/ES" in DEFAULT_SYMBOLS
    assert "/NQ" in DEFAULT_SYMBOLS
    assert "/ZB" in DEFAULT_SYMBOLS
    assert "/KC" in DEFAULT_SYMBOLS
    assert "/BTC" in DEFAULT_SYMBOLS
    assert "/ETH" in DEFAULT_SYMBOLS
    assert "/MBT" in DEFAULT_SYMBOLS
    assert "/MET" in DEFAULT_SYMBOLS
    assert "/SOL" in DEFAULT_SYMBOLS
    assert "/MSL" in DEFAULT_SYMBOLS
    assert "/XRP" in DEFAULT_SYMBOLS
    assert "/MXP" in DEFAULT_SYMBOLS
    assert "/MCA" in DEFAULT_SYMBOLS
    assert "SPY" in DEFAULT_SYMBOLS


def test_default_symbols_include_all_configured_products():
    assert set(FUTURES_PRODUCTS).issubset(DEFAULT_SYMBOLS)
    assert set(EQUITY_PRODUCTS).issubset(DEFAULT_SYMBOLS)
    assert set(LEGACY_PRODUCTS).issubset(DEFAULT_SYMBOLS)


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


def test_prompt_for_schwab_token_payload_opens_authorization_url(
    monkeypatch,
):
    opened_urls = []
    redirect_uri = "https://developer.schwab.com/oauth2-redirect.html"
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: f"{redirect_uri}?code=AUTH_CODE_123&state=abc",
    )
    monkeypatch.setattr(
        "download_market_data.exchange_schwab_authorization_code",
        lambda **kwargs: {
            "access_token": "ACCESS",
            "refresh_token": "REFRESH",
            "expires_in": 1800,
        },
    )

    token_payload = prompt_for_schwab_token_payload(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri=redirect_uri,
        browser_opener=opened_urls.append,
    )

    assert token_payload["access_token"] == "ACCESS"
    assert len(opened_urls) == 1
    assert opened_urls[0].startswith(
        "https://api.schwabapi.com/v1/oauth/authorize?"
    )
    assert "client_id=client-id" in opened_urls[0]


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


def test_schwab_access_token_expiry_uses_retrieved_at_and_expires_in():
    token_payload = {
        "access_token": "ACCESS",
        "retrieved_at": "2999-01-01T00:00:00Z",
        "expires_in": 1800,
    }

    assert schwab_access_token_expires_at(token_payload).isoformat() == (
        "2999-01-01T00:30:00+00:00"
    )
    assert schwab_access_token_is_current(token_payload)
    assert not schwab_access_token_is_current(
        {
            **token_payload,
            "retrieved_at": "2000-01-01T00:00:00Z",
        }
    )


def test_schwab_provider_uses_current_saved_access_token_without_refresh(
    monkeypatch,
    tmp_path,
):
    token_path = tmp_path / "tokens.json"
    save_schwab_token_file(
        token_path,
        {
            "access_token": "CURRENT_ACCESS",
            "refresh_token": "REFRESH",
            "expires_in": 1800,
        },
    )

    def fake_refresh(client_id, client_secret, refresh_token):
        raise AssertionError("Refresh should not be called for current token")

    monkeypatch.setattr(
        "download_market_data.exchange_schwab_refresh_token",
        fake_refresh,
    )

    provider = SchwabProvider(token_file=token_path)

    assert provider.access_token == "CURRENT_ACCESS"


def test_schwab_provider_force_reauth_replaces_current_saved_tokens(
    monkeypatch,
    tmp_path,
):
    token_path = tmp_path / "tokens.json"
    save_schwab_token_file(
        token_path,
        {
            "access_token": "CURRENT_ACCESS",
            "refresh_token": "CURRENT_REFRESH",
            "expires_in": 1800,
        },
    )

    def fake_prompt(client_id, client_secret, redirect_uri):
        assert client_id == "client-id"
        assert client_secret == "client-secret"
        return {
            "access_token": "REAUTHORIZED_ACCESS",
            "refresh_token": "REAUTHORIZED_REFRESH",
            "expires_in": 1800,
        }

    monkeypatch.setattr(
        "download_market_data.prompt_for_schwab_token_payload",
        fake_prompt,
    )
    monkeypatch.setattr(
        "download_market_data.exchange_schwab_refresh_token",
        lambda *args, **kwargs: pytest.fail("refresh must be bypassed"),
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "download_market_data.py",
            "--provider",
            "schwab",
            "--token-file",
            str(token_path),
            "--client-id",
            "client-id",
            "--client-secret",
            "client-secret",
            "--force-reauth",
        ],
    )
    provider = create_provider(parse_args())
    saved = load_schwab_token_file(token_path)

    assert provider.access_token == "REAUTHORIZED_ACCESS"
    assert saved["access_token"] == "REAUTHORIZED_ACCESS"
    assert saved["refresh_token"] == "REAUTHORIZED_REFRESH"


def test_schwab_provider_reports_expired_saved_token_missing_credentials(
    tmp_path,
):
    token_path = tmp_path / "tokens.json"
    save_schwab_token_file(
        token_path,
        {
            "access_token": "OLD_ACCESS",
            "refresh_token": "REFRESH",
            "expires_in": 0,
        },
    )

    with pytest.raises(ValueError, match="Saved Schwab access token is expired"):
        SchwabProvider(token_file=token_path)


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


def test_request_schwab_token_decodes_gzip_error_body(monkeypatch):
    def fake_urlopen(request, timeout):
        body = gzip.compress(
            b'{"error":"invalid_grant","error_description":"bad refresh"}'
        )
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {"Content-Encoding": "gzip"},
            io.BytesIO(body),
        )

    monkeypatch.setattr("download_market_data.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="invalid_grant") as error:
        request_schwab_token(
            client_id="client-id",
            client_secret="client-secret",
            token_fields={
                "grant_type": "refresh_token",
                "refresh_token": "REFRESH",
            },
        )

    assert "bad refresh" in str(error.value)
