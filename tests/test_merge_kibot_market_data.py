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


def write_purchase(path, frequency, missing=None, empty=None, truncated=None):
    missing = set(missing or [])
    empty = set(empty or [])
    truncated = set(truncated or [])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for symbol in sorted(KIBOT_REQUIRED_SYMBOLS - missing):
            if symbol in empty:
                content = ""
            elif frequency == "daily":
                content = "01/05/2009,100,102,99,101,10\n12/28/2018,101,103,100,102,11\n01/02/2020,102,104,101,103,12\n"
            else:
                content = "10/01/2009,09:30,100,102,99,101,10\n12/28/2018,09:30,101,103,100,102,11\n01/02/2020,09:30,102,104,101,103,12\n"
                if symbol == "ES":
                    content += "01/02/2020,09:35,103,105,102,104,13\n"
            if symbol in truncated:
                content = content.splitlines(keepends=True)[-1]
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
    kwargs.setdefault("minimum_research_rows", {"daily": 2, "5min": 2})
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
    gaps = pd.read_csv(paths.quality_stage / "multi_day_outages.csv")
    merged_es = pd.read_csv(paths.stage_root / "5min" / "ES.csv")

    assert {"ES", "SPY"}.issubset(set(coverage["symbol"]))
    assert conflicts.loc[0, "selected_source"] == "schwab"
    assert transitions.loc[0, "from_source"] == "kibot"
    assert transitions.loc[0, "to_source"] == "schwab"
    assert list(gaps.columns) == [
        "symbol", "frequency", "previous_timestamp", "timestamp", "gap_minutes"
    ]
    assert merged_es["source"].tolist() == ["kibot", "kibot", "kibot", "schwab"]
    summary = json.loads((paths.quality_stage / "merge_summary.json").read_text())
    assert summary["gap_audit"] == {
        "kind": "multi_day_outage",
        "session_aware": False,
        "threshold_days": 3,
    }


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


def test_stage_rejects_empty_required_member_under_canonical_ticker(tmp_path):
    market = tmp_path / "market_data"
    daily_zip = write_purchase(tmp_path / "daily.zip", "daily", empty={"TY"})
    intraday_zip = write_purchase(tmp_path / "intraday.zip", "5min")

    with pytest.raises(ValueError, match=r"TY.*ZN.*no usable rows"):
        stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market,
            tmp_path / "archives",
            "2026-09-22T12:00:00Z",
        )

def test_stage_rejects_required_series_without_research_window(tmp_path):
    market = tmp_path / "market_data"
    daily_zip = write_purchase(tmp_path / "daily.zip", "daily", truncated={"ES"})
    intraday_zip = write_purchase(tmp_path / "intraday.zip", "5min")

    with pytest.raises(ValueError, match=r"ES.*daily.*research window"):
        stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market,
            tmp_path / "archives",
            "2026-09-22T12:00:00Z",
            minimum_research_rows={"daily": 2, "5min": 2},
        )
    staging = market / ".staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_stage_rejects_sparse_required_series_even_when_endpoints_span_window(tmp_path):
    market, archive_root, daily_zip, intraday_zip = arrange_repository(tmp_path)

    with pytest.raises(ValueError, match="insufficient research-window observations"):
        stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market,
            archive_root,
            "2026-09-22T12:00:00Z",
        )


def test_stage_rejects_coverage_regression_from_active_repository(tmp_path):
    market, archive_root, daily_zip, intraday_zip = arrange_repository(tmp_path)
    write_canonical(
        market / "daily" / "ES.csv",
        [canonical_row("/ES", "daily", "2000-01-03T00:00:00Z")],
    )
    # Force the active row to be unusable so it cannot be copied into the stage.
    frame = pd.read_csv(market / "daily" / "ES.csv")
    frame["close"] = pd.NA
    frame.to_csv(market / "daily" / "ES.csv", index=False)

    with pytest.raises(ValueError, match=r"ES.*coverage regressed"):
        stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market,
            archive_root,
            "2026-09-22T12:00:00Z",
            minimum_research_rows={"daily": 2, "5min": 2},
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
    assert len(pd.read_csv(market / "5min" / "ES.csv")) == 4
    assert not paths.stage_root.exists()


def test_cleanup_failure_does_not_roll_back_successful_publish(tmp_path):
    paths, market, *_ = stage_fixture(tmp_path)

    def fail_cleanup(_stage_root):
        raise OSError("injected cleanup failure")

    result = publish_staged_repository(
        paths,
        cleanup_empty_stage=fail_cleanup,
    )

    assert result["status"] == "PASS"
    assert len(pd.read_csv(market / "5min" / "ES.csv")) == 4
    assert (paths.quality_destination / "stage_complete.json").exists()


def test_publish_refuses_existing_archive_destination(tmp_path):
    paths, *_ = stage_fixture(tmp_path)
    paths.vendor_archive_dir.mkdir(parents=True)

    with pytest.raises(PublishError, match="already exists"):
        publish_staged_repository(paths)


def test_publish_rejects_source_zip_changed_after_staging(tmp_path):
    paths, market, _, daily_zip, _ = stage_fixture(tmp_path)
    with daily_zip.open("ab") as stream:
        stream.write(b"changed-after-staging")

    with pytest.raises(PublishError, match="changed after staging"):
        publish_staged_repository(paths)

    assert len(pd.read_csv(market / "daily" / "ES.csv")) == 1
    assert not paths.canonical_archive_dir.exists()


def test_publish_rejects_staged_csv_changed_after_audit(tmp_path):
    paths, market, *_ = stage_fixture(tmp_path)
    staged_es = paths.stage_root / "daily" / "ES.csv"
    frame = pd.read_csv(staged_es)
    frame.loc[0, "close"] = 999
    frame.to_csv(staged_es, index=False)

    with pytest.raises(PublishError, match="(?i)staged data changed after audit"):
        publish_staged_repository(paths)

    assert len(pd.read_csv(market / "daily" / "ES.csv")) == 1
    assert not paths.canonical_archive_dir.exists()


def test_publish_requires_passing_staged_summary(tmp_path):
    paths, market, *_ = stage_fixture(tmp_path)
    summary_path = paths.quality_stage / "merge_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["status"] = "FAIL"
    summary_path.write_text(json.dumps(summary))

    with pytest.raises(PublishError, match="changed after audit|status is not PASS"):
        publish_staged_repository(paths)

    assert len(pd.read_csv(market / "daily" / "ES.csv")) == 1
    assert not paths.canonical_archive_dir.exists()


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


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_canonical_manifest",
        "after_sixty_archive",
        "after_daily_zip",
        "after_intraday_zip",
    ],
)
def test_publish_failure_leaves_no_destinations_and_can_retry(tmp_path, failure_point):
    paths, market, _, daily_zip, intraday_zip = stage_fixture(tmp_path)

    def fail_at(point):
        if point == failure_point:
            raise OSError(f"injected failure at {point}")

    with pytest.raises(PublishError, match="injected failure"):
        publish_staged_repository(paths, fault_hook=fail_at)

    assert len(pd.read_csv(market / "daily" / "ES.csv")) == 1
    assert len(pd.read_csv(market / "5min" / "ES.csv")) == 1
    assert (market / "60min" / "ES.csv").exists()
    assert daily_zip.exists() and intraday_zip.exists()
    assert not paths.vendor_archive_dir.exists()
    assert not paths.sixty_archive_dir.exists()
    assert not paths.canonical_archive_dir.exists()
    assert not paths.quality_destination.exists()

    assert publish_staged_repository(paths)["status"] == "PASS"
