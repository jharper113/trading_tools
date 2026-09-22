import json
import os
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from download_market_data import CANONICAL_COLUMNS
from kibot_market_data import KIBOT_REQUIRED_SYMBOLS
from merge_kibot_market_data import (
    PublishError,
    SpaceError,
    publish_staged_repository,
    stage_kibot_merge,
)


def write_purchase(path, frequency, missing=None):
    missing = set(missing or [])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for symbol in sorted(KIBOT_REQUIRED_SYMBOLS - missing):
            if frequency == "daily":
                content = "01/02/2020,100,102,99,101,10\n"
            else:
                content = "01/02/2020,09:30,100,102,99,101,10\n"
                if symbol == "ES":
                    content += "01/02/2020,09:35,101,103,100,102,11\n"
            archive.writestr(f"purchase/{symbol}.txt", content)
    return path


def canonical_row(symbol, frequency, timestamp, source="schwab", close=102):
    return {
        "timestamp": timestamp,
        "date": timestamp[:10],
        "symbol": symbol,
        "frequency": frequency,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 99,
        "open_interest": pd.NA,
        "source": source,
        "retrieved_at": "2026-09-22T12:00:00Z",
    }


def write_canonical(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=CANONICAL_COLUMNS).to_csv(path, index=False)


def arrange_repository(tmp_path):
    market = tmp_path / "market_data"
    archive_root = tmp_path / "market_data_archives"
    daily_zip = write_purchase(tmp_path / "daily.zip", "daily")
    intraday_zip = write_purchase(tmp_path / "intraday.zip", "5min")
    write_canonical(
        market / "daily" / "ES.csv",
        [canonical_row("/ES", "daily", "2020-01-02T00:00:00Z")],
    )
    write_canonical(
        market / "5min" / "ES.csv",
        [canonical_row("/ES", "5min", "2020-01-02T14:35:00Z", close=103)],
    )
    write_canonical(
        market / "daily" / "SPY.csv",
        [canonical_row("SPY", "daily", "2020-01-02T00:00:00Z")],
    )
    write_canonical(
        market / "5min" / "SPY.csv",
        [canonical_row("SPY", "5min", "2020-01-02T14:30:00Z")],
    )
    write_canonical(
        market / "60min" / "ES.csv",
        [canonical_row("/ES", "60min", "2020-01-02T14:00:00Z")],
    )
    return market, archive_root, daily_zip, intraday_zip


def stage_fixture(tmp_path, **kwargs):
    market, archive_root, daily_zip, intraday_zip = arrange_repository(tmp_path)
    paths = stage_kibot_merge(
        daily_zip=daily_zip,
        intraday_zip=intraday_zip,
        market_data_dir=market,
        archive_root=archive_root,
        acquired_at="2026-09-22T12:00:00Z",
        **kwargs,
    )
    return paths, market, archive_root, daily_zip, intraday_zip


def test_stage_is_non_mutating_and_preserves_non_kibot_symbols(tmp_path):
    paths, market, _, daily_zip, intraday_zip = stage_fixture(tmp_path)

    assert daily_zip.exists() and intraday_zip.exists()
    assert (market / "60min" / "ES.csv").exists()
    assert pd.read_csv(market / "5min" / "ES.csv").shape[0] == 1
    assert (paths.stage_root / "daily" / "SPY.csv").exists()
    assert (paths.stage_root / "5min" / "SPY.csv").exists()
    summary = json.loads((paths.quality_stage / "merge_summary.json").read_text())
    assert summary["status"] == "PASS"
    assert summary["timezone"] == "America/Detroit"
    assert summary["dst"]["ambiguous_rows"] == 0
    assert summary["dst"]["nonexistent_rows"] == 0
    assert len(json.loads((paths.quality_stage / "source_manifest.json").read_text())["archives"]) == 2


def test_stage_writes_coverage_conflict_gap_and_transition_audits(tmp_path):
    paths, *_ = stage_fixture(tmp_path)

    coverage = pd.read_csv(paths.quality_stage / "coverage.csv")
    conflicts = pd.read_csv(paths.quality_stage / "conflicts.csv")
    transitions = pd.read_csv(paths.quality_stage / "source_transitions.csv")
    gaps = pd.read_csv(paths.quality_stage / "interval_gaps.csv")
    merged_es = pd.read_csv(paths.stage_root / "5min" / "ES.csv")

    assert {"ES", "SPY"}.issubset(set(coverage["symbol"]))
    assert conflicts.loc[0, "selected_source"] == "schwab"
    assert transitions.loc[0, "from_source"] == "kibot"
    assert transitions.loc[0, "to_source"] == "schwab"
    assert list(gaps.columns) == [
        "symbol", "frequency", "previous_timestamp", "timestamp", "gap_minutes"
    ]
    assert merged_es["source"].tolist() == ["kibot", "schwab"]


def test_stage_rejects_missing_required_member(tmp_path):
    market = tmp_path / "market_data"
    daily_zip = write_purchase(tmp_path / "daily.zip", "daily", missing={"RP"})
    intraday_zip = write_purchase(tmp_path / "intraday.zip", "5min")

    with pytest.raises(ValueError, match="missing.*RP"):
        stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market,
            tmp_path / "archives",
            "2026-09-22T12:00:00Z",
        )


def test_stage_rejects_insufficient_free_space(tmp_path):
    with pytest.raises(SpaceError, match="free space"):
        stage_fixture(tmp_path, free_bytes=1)


def test_publish_moves_vendor_zips_archives_60min_and_swaps_data(tmp_path):
    paths, market, archive_root, daily_zip, intraday_zip = stage_fixture(tmp_path)

    result = publish_staged_repository(paths)

    assert result["status"] == "PASS"
    assert not daily_zip.exists() and not intraday_zip.exists()
    vendor = market / "source_archives" / "kibot" / "2026-09-22"
    assert (vendor / "daily.zip").exists()
    assert (vendor / "intraday.zip").exists()
    sixty = archive_root / "60min_before_kibot_merge_2026-09-22"
    assert (sixty / "ES.csv").exists()
    manifest = json.loads((sixty / "archive_manifest.json").read_text())
    assert manifest["files"][0]["sha256"]
    assert manifest["files"][0]["rows"] == 1
    assert not (market / "60min").exists()
    assert len(pd.read_csv(market / "5min" / "ES.csv")) == 2


def test_publish_refuses_existing_archive_destination(tmp_path):
    paths, *_ = stage_fixture(tmp_path)
    paths.vendor_archive_dir.mkdir(parents=True)

    with pytest.raises(PublishError, match="already exists"):
        publish_staged_repository(paths)


def test_publish_rolls_back_both_directories_on_second_stage_move_failure(tmp_path):
    paths, market, _, daily_zip, intraday_zip = stage_fixture(tmp_path)
    real_replace = os.replace
    stage_moves = 0

    def fail_second_stage_move(source, destination):
        nonlocal stage_moves
        source = Path(source)
        if source.parent == paths.stage_root and source.name in {"daily", "5min"}:
            stage_moves += 1
            if stage_moves == 2:
                raise OSError("injected second stage move failure")
        real_replace(source, destination)

    with pytest.raises(PublishError, match="injected"):
        publish_staged_repository(paths, rename=fail_second_stage_move)

    assert len(pd.read_csv(market / "daily" / "ES.csv")) == 1
    assert len(pd.read_csv(market / "5min" / "ES.csv")) == 1
    assert (market / "60min" / "ES.csv").exists()
    assert daily_zip.exists() and intraday_zip.exists()
