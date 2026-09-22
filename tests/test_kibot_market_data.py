import zipfile

import pandas as pd
import pytest

from download_market_data import CANONICAL_COLUMNS
from kibot_market_data import (
    KIBOT_REQUIRED_SYMBOLS,
    KIBOT_SYMBOL_MAP,
    KibotDataError,
    inventory_kibot_zip,
    read_kibot_member,
)


ACQUIRED_AT = "2026-09-22T12:00:00Z"


def write_zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


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
