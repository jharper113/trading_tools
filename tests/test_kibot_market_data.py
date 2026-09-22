import zipfile

import pandas as pd
import pytest

from download_market_data import CANONICAL_COLUMNS
from kibot_market_data import (
    KIBOT_REQUIRED_SYMBOLS,
    KIBOT_SYMBOL_MAP,
    KibotDataError,
    inventory_kibot_zip,
    merge_market_data_sources,
    read_kibot_member,
    source_priority,
)


ACQUIRED_AT = "2026-09-22T12:00:00Z"


def write_zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def frame(
    source,
    *,
    timestamp="2026-09-18T13:30:00Z",
    frequency="5min",
    open=100,
    high=102,
    low=99,
    close=101,
    volume=10,
    retrieved_at="2026-09-22T12:00:00Z",
):
    return pd.DataFrame(
        [
            {
                "timestamp": timestamp,
                "date": timestamp[:10],
                "symbol": "/ES",
                "frequency": frequency,
                "open": open,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "open_interest": pd.NA,
                "source": source,
                "retrieved_at": retrieved_at,
            }
        ],
        columns=CANONICAL_COLUMNS,
    )


def test_read_intraday_member_maps_symbol_and_converts_eastern(tmp_path):
    archive = write_zip(
        tmp_path / "intraday.zip",
        {
            "purchase/EU.txt": (
                "01/15/2020,09:30,1.10,1.11,1.09,1.105,12\n"
                "07/15/2020,09:30,1.12,1.13,1.11,1.125,13\n"
            )
        },
    )

    frame = read_kibot_member(
        archive,
        "purchase/EU.txt",
        "5min",
        ACQUIRED_AT,
    )

    assert list(frame.columns) == CANONICAL_COLUMNS
    assert frame["symbol"].tolist() == ["/6E", "/6E"]
    assert frame["timestamp"].tolist() == [
        "2020-01-15T14:30:00Z",
        "2020-07-15T13:30:00Z",
    ]
    assert frame["date"].tolist() == ["2020-01-15", "2020-07-15"]
    assert set(frame["frequency"]) == {"5min"}
    assert set(frame["source"]) == {"kibot"}
    assert set(frame["retrieved_at"]) == {ACQUIRED_AT}


def test_read_daily_member_uses_trading_date_at_midnight_utc(tmp_path):
    archive = write_zip(
        tmp_path / "daily.zip",
        {"purchase/ES.txt": "01/15/2020,3200,3220,3190,3210,42\n"},
    )

    frame = read_kibot_member(
        archive,
        "purchase/ES.txt",
        "daily",
        ACQUIRED_AT,
    )

    assert frame.loc[0, "timestamp"] == "2020-01-15T00:00:00Z"
    assert frame.loc[0, "date"] == "2020-01-15"
    assert frame.loc[0, "symbol"] == "/ES"
    assert frame.loc[0, "volume"] == 42
    assert pd.isna(frame.loc[0, "open_interest"])


def test_symbol_map_and_required_inventory_cover_purchase():
    assert KIBOT_SYMBOL_MAP["RP"] == "RP"
    assert KIBOT_SYMBOL_MAP["TY"] == "ZN"
    assert KIBOT_SYMBOL_MAP["C"] == "ZC"
    assert KIBOT_REQUIRED_SYMBOLS == {
        "AD", "BP", "C", "CD", "CL", "ES", "EU", "FV", "GC", "HG",
        "JY", "NG", "NQ", "PA", "PL", "RP", "S", "SF", "SI", "TU",
        "TY", "US", "W", "YM",
    }


def test_inventory_requires_all_24_members(tmp_path):
    members = {
        f"purchase/{symbol}.txt": "01/15/2020,1,2,0.5,1.5,10\n"
        for symbol in KIBOT_REQUIRED_SYMBOLS
    }
    archive = write_zip(tmp_path / "daily.zip", members)

    inventory = inventory_kibot_zip(archive, "daily")

    assert inventory.vendor_symbols == tuple(sorted(KIBOT_REQUIRED_SYMBOLS))
    assert inventory.member_count == 24
    assert inventory.frequency == "daily"
    assert len(inventory.sha256) == 64


def test_inventory_rejects_missing_member(tmp_path):
    symbols = KIBOT_REQUIRED_SYMBOLS - {"RP"}
    archive = write_zip(
        tmp_path / "daily.zip",
        {
            f"purchase/{symbol}.txt": "01/15/2020,1,2,0.5,1.5,10\n"
            for symbol in symbols
        },
    )

    with pytest.raises(KibotDataError, match="missing.*RP"):
        inventory_kibot_zip(archive, "daily")


@pytest.mark.parametrize(
    ("member", "content", "message"),
    [
        ("purchase/XX.txt", "01/15/2020,1,2,0.5,1.5,10\n", "unknown"),
        ("purchase/ES.txt", "01/15/2020,09:30,bad,2,0.5,1.5,10\n", "numeric"),
        ("purchase/ES.txt", "01/15/2020,09:30,1,0.9,0.5,1.5,10\n", "OHLC"),
        ("purchase/ES.txt", "01/15/2020,09:32,1,2,0.5,1.5,10\n", "five-minute"),
        ("purchase/ES.txt", "03/08/2020,02:30,1,2,0.5,1.5,10\n", "nonexistent"),
        ("purchase/ES.txt", "11/01/2020,01:30,1,2,0.5,1.5,10\n", "ambiguous"),
    ],
)
def test_parser_rejects_bad_rows_with_source_location(
    tmp_path,
    member,
    content,
    message,
):
    archive = write_zip(tmp_path / "intraday.zip", {member: content})

    with pytest.raises(KibotDataError, match=message) as error:
        read_kibot_member(archive, member, "5min", ACQUIRED_AT)

    assert str(archive) in str(error.value)
    assert member in str(error.value)
    if member != "purchase/XX.txt":
        assert "row 1" in str(error.value)


def test_identical_duplicate_collapses(tmp_path):
    row = "01/15/2020,09:30,1,2,0.5,1.5,10\n"
    archive = write_zip(tmp_path / "intraday.zip", {"purchase/ES.txt": row * 2})

    frame = read_kibot_member(
        archive,
        "purchase/ES.txt",
        "5min",
        ACQUIRED_AT,
    )

    assert len(frame) == 1


def test_negative_crude_price_is_preserved_when_ohlc_envelope_is_valid(tmp_path):
    archive = write_zip(
        tmp_path / "intraday.zip",
        {"purchase/CL.txt": "04/20/2020,14:05,-10,-8,-12,-11,100\n"},
    )

    frame = read_kibot_member(
        archive,
        "purchase/CL.txt",
        "5min",
        ACQUIRED_AT,
    )

    assert frame.loc[0, "low"] == -12
    assert frame.loc[0, "high"] == -8


def test_conflicting_duplicate_is_rejected(tmp_path):
    archive = write_zip(
        tmp_path / "intraday.zip",
        {
            "purchase/ES.txt": (
                "01/15/2020,09:30,1,2,0.5,1.5,10\n"
                "01/15/2020,09:30,1,2,0.5,1.6,10\n"
            )
        },
    )

    with pytest.raises(KibotDataError, match="conflicting duplicate"):
        read_kibot_member(
            archive,
            "purchase/ES.txt",
            "5min",
            ACQUIRED_AT,
        )


def test_valid_schwab_price_and_volume_win_and_conflict_is_reported():
    result = merge_market_data_sources(
        [
            frame("kibot", close=100, volume=10),
            frame("schwab", close=112, high=113, volume=99),
        ]
    )

    assert result.rows.iloc[0]["close"] == 112
    assert result.rows.iloc[0]["volume"] == 99
    assert result.rows.iloc[0]["source"] == "schwab"
    assert result.conflicts.iloc[0]["selected_source"] == "schwab"
    assert result.conflicts.iloc[0]["other_source"] == "kibot"


def test_invalid_schwab_falls_back_to_kibot():
    result = merge_market_data_sources(
        [
            frame("kibot"),
            frame("schwab", high=98),
        ]
    )

    assert result.rows.iloc[0]["source"] == "kibot"
    assert result.rejections.iloc[0]["source"] == "schwab"
    assert result.rejections.iloc[0]["reason"] == "invalid_ohlc_envelope"


def test_source_precedence_places_kibot_above_yahoo_and_legacy():
    result = merge_market_data_sources(
        [
            frame("reviewed_yahoo", close=98),
            frame("Prices_Daily_Futures.csv#ES", close=99),
            frame("kibot", close=100),
        ]
    )

    assert source_priority("schwab") > source_priority("kibot")
    assert source_priority("kibot") > source_priority("reviewed_yahoo")
    assert result.rows.iloc[0]["source"] == "kibot"


def test_reviewed_key_preserves_first_existing_candidate():
    reviewed = pd.DataFrame(
        [
            {
                "symbol": "/ES",
                "frequency": "5min",
                "comparison_key": "2026-09-18T13:30:00Z",
                "selected_source": "local",
                "reviewed_at": "2026-09-22T12:00:00Z",
            }
        ]
    )

    result = merge_market_data_sources(
        [frame("reviewed_local", close=100), frame("schwab", close=101)],
        reviewed_bars=reviewed,
    )

    assert result.rows.iloc[0]["source"] == "reviewed_local"
    assert result.summary["reviewed_selections"] == 1


def test_zero_schwab_volume_does_not_lose_precedence():
    result = merge_market_data_sources(
        [frame("kibot", volume=50), frame("schwab", volume=0)]
    )

    assert result.rows.iloc[0]["source"] == "schwab"
    assert result.rows.iloc[0]["volume"] == 0


def test_daily_merge_keys_by_trading_date_and_orders_stably():
    result = merge_market_data_sources(
        [
            frame(
                "kibot",
                timestamp="2026-09-19T00:00:00Z",
                frequency="daily",
            ),
            frame(
                "schwab",
                timestamp="2026-09-18T05:00:00Z",
                frequency="daily",
                close=102,
            ),
            frame(
                "schwab",
                timestamp="2026-09-19T05:00:00Z",
                frequency="daily",
                close=103,
                high=104,
            ),
        ]
    )

    assert result.rows["date"].tolist() == ["2026-09-18", "2026-09-19"]
    assert result.rows["source"].tolist() == ["schwab", "schwab"]


def test_large_rollover_difference_is_reported_without_rejecting_schwab():
    result = merge_market_data_sources(
        [frame("kibot", close=100), frame("schwab", open=120, high=123, low=119, close=122)]
    )

    assert result.rows.iloc[0]["close"] == 122
    assert result.conflicts.iloc[0]["close_abs_diff"] == 22
    assert result.rejections.empty


def test_merge_is_idempotent_and_ordered():
    inputs = [
        frame("kibot", timestamp="2026-09-18T13:35:00Z"),
        frame("schwab", timestamp="2026-09-18T13:30:00Z"),
    ]

    once = merge_market_data_sources(inputs).rows
    twice = merge_market_data_sources([once, once]).rows

    pd.testing.assert_frame_equal(once, twice)
    assert once["timestamp"].tolist() == [
        "2026-09-18T13:30:00Z",
        "2026-09-18T13:35:00Z",
    ]
