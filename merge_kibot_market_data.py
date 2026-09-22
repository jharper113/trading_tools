"""Stage, audit, and transactionally publish purchased Kibot market data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from download_market_data import CANONICAL_COLUMNS
from kibot_market_data import (
    CONFLICT_COLUMNS,
    KIBOT_SYMBOL_MAP,
    REJECTION_COLUMNS,
    KibotDataError,
    inventory_kibot_zip,
    merge_market_data_sources,
    read_kibot_member,
)


class SpaceError(RuntimeError):
    """Raised when there is not enough room to build the staged repository."""


class PublishError(RuntimeError):
    """Raised when a staged repository cannot be published safely."""


@dataclass(frozen=True)
class MigrationPaths:
    market_data_dir: Path
    archive_root: Path
    daily_zip: Path
    intraday_zip: Path
    stage_root: Path
    quality_stage: Path
    vendor_archive_dir: Path
    sixty_archive_dir: Path
    canonical_archive_dir: Path
    quality_destination: Path
    archive_date: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    frame = pd.read_csv(path)
    for column in CANONICAL_COLUMNS:
        if column not in frame:
            frame[column] = pd.NA
    return frame[CANONICAL_COLUMNS]


def _archive_record(path: Path, root: Path) -> dict[str, object]:
    record = {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if path.suffix.lower() == ".csv":
        try:
            record["rows"] = sum(1 for _ in path.open("rb")) - 1
        except OSError:
            record["rows"] = None
    return record


def _directory_manifest(path: Path) -> dict[str, object]:
    files = [
        _archive_record(item, path)
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.name != "archive_manifest.json"
    ]
    return {"status": "PASS", "root": str(path), "files": files}


def _stage_file_records(stage_root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(stage_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(stage_root.rglob("*"))
        if path.is_file() and path.name != "stage_complete.json"
    ]


def _coverage_rows(stage_root: Path) -> list[dict[str, object]]:
    records = []
    for frequency in ("daily", "5min"):
        for path in sorted((stage_root / frequency).glob("*.csv")):
            frame = pd.read_csv(path, usecols=["timestamp", "source"])
            records.append(
                {
                    "symbol": path.stem,
                    "frequency": frequency,
                    "rows": len(frame),
                    "first_timestamp": frame["timestamp"].min() if len(frame) else "",
                    "last_timestamp": frame["timestamp"].max() if len(frame) else "",
                    "sources": ",".join(sorted(frame["source"].dropna().astype(str).unique())),
                }
            )
    return records


def _transition_rows(stage_root: Path) -> list[dict[str, object]]:
    records = []
    for frequency in ("daily", "5min"):
        for path in sorted((stage_root / frequency).glob("*.csv")):
            frame = pd.read_csv(path, usecols=["timestamp", "source"]).sort_values("timestamp")
            previous = frame["source"].shift()
            changed = previous.notna() & frame["source"].ne(previous)
            if changed.any():
                transitions = pd.DataFrame(
                    {
                        "symbol": path.stem,
                        "frequency": frequency,
                        "timestamp": frame.loc[changed, "timestamp"],
                        "from_source": previous.loc[changed],
                        "to_source": frame.loc[changed, "source"],
                    }
                )
                records.extend(transitions.to_dict("records"))
    return records


def _multi_day_outage_rows(stage_root: Path) -> list[dict[str, object]]:
    records = []
    for frequency in ("daily", "5min"):
        threshold = pd.Timedelta(days=3)
        for path in sorted((stage_root / frequency).glob("*.csv")):
            frame = pd.read_csv(path, usecols=["timestamp"])
            times = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna().sort_values()
            gaps = times.diff()
            selected = gaps.gt(threshold)
            if selected.any():
                current = times.loc[selected]
                previous = times.shift().loc[selected]
                gap_frame = pd.DataFrame(
                    {
                        "symbol": path.stem,
                        "frequency": frequency,
                        "previous_timestamp": previous.dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "timestamp": current.dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "gap_minutes": gaps.loc[selected].dt.total_seconds() / 60,
                    }
                )
                records.extend(gap_frame.to_dict("records"))
    return records


RESEARCH_WINDOWS = {
    "daily": (
        pd.Timestamp("2009-01-05", tz="UTC"),
        pd.Timestamp("2018-12-28 23:59:59", tz="UTC"),
    ),
    "5min": (
        pd.Timestamp("2009-10-01", tz="UTC"),
        pd.Timestamp("2018-12-28 23:59:59", tz="UTC"),
    ),
}
MINIMUM_RESEARCH_ROWS = {"daily": 2_000, "5min": 100_000}


def _timestamp_bounds(path: Path) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if not path.exists():
        return None, None
    frame = pd.read_csv(path, usecols=["timestamp"])
    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna()
    if timestamps.empty:
        return None, None
    return timestamps.min(), timestamps.max()


def _validate_staged_coverage(
    stage_root: Path,
    market_data_dir: Path,
    required_tickers: dict[str, set[str]],
    minimum_research_rows: dict[str, int],
) -> None:
    for frequency, tickers in required_tickers.items():
        research_start, research_end = RESEARCH_WINDOWS[frequency]
        for ticker in sorted(tickers):
            staged_path = stage_root / frequency / f"{ticker}.csv"
            staged_first, staged_last = _timestamp_bounds(staged_path)
            if staged_first is None:
                raise KibotDataError(
                    f"Required {ticker} {frequency} series has no usable rows"
                )
            if (
                staged_first.date() > research_start.date()
                or staged_last.date() < research_end.date()
            ):
                raise KibotDataError(
                    f"Required {ticker} {frequency} series does not cover the research window"
                )
            timestamps = pd.to_datetime(
                pd.read_csv(staged_path, usecols=["timestamp"])["timestamp"],
                utc=True,
                errors="coerce",
            )
            research_rows = int(
                timestamps.between(research_start, research_end, inclusive="both").sum()
            )
            minimum_rows = int(minimum_research_rows[frequency])
            if research_rows < minimum_rows:
                raise KibotDataError(
                    f"Required {ticker} {frequency} series has insufficient "
                    f"research-window observations: {research_rows} < {minimum_rows}"
                )

            active_first, active_last = _timestamp_bounds(
                market_data_dir / frequency / f"{ticker}.csv"
            )
            if active_first is not None and (
                staged_first > active_first or staged_last < active_last
            ):
                raise KibotDataError(
                    f"Required {ticker} {frequency} coverage regressed from the active repository"
                )


def _stage_kibot_merge(
    daily_zip,
    intraday_zip,
    market_data_dir,
    archive_root,
    acquired_at: str,
    *,
    free_bytes: int | None = None,
    minimum_research_rows: dict[str, int] | None = None,
    stage_root: Path,
) -> MigrationPaths:
    """Build and audit a merged repository without changing active data."""
    market_data_dir = Path(market_data_dir)
    archive_root = Path(archive_root)
    daily_zip = Path(daily_zip)
    intraday_zip = Path(intraday_zip)
    daily_meta = inventory_kibot_zip(daily_zip, "daily")
    intraday_meta = inventory_kibot_zip(intraday_zip, "5min")
    minimum_research_rows = {
        **MINIMUM_RESEARCH_ROWS,
        **(minimum_research_rows or {}),
    }

    uncompressed = 0
    import zipfile
    for archive_path in (daily_zip, intraday_zip):
        with zipfile.ZipFile(archive_path) as archive:
            uncompressed += sum(item.file_size for item in archive.infolist())
    required = int(1.2 * (uncompressed + _directory_size(market_data_dir / "daily") + _directory_size(market_data_dir / "5min")))
    available = free_bytes if free_bytes is not None else shutil.disk_usage(market_data_dir.parent).free
    if available < required:
        raise SpaceError(f"Insufficient free space: need {required} bytes, have {available}")

    archive_date = acquired_at[:10]
    quality_stage = stage_root / "quality"
    paths = MigrationPaths(
        market_data_dir=market_data_dir,
        archive_root=archive_root,
        daily_zip=daily_zip,
        intraday_zip=intraday_zip,
        stage_root=stage_root,
        quality_stage=quality_stage,
        vendor_archive_dir=market_data_dir / "source_archives" / "kibot" / archive_date,
        sixty_archive_dir=archive_root / f"60min_before_kibot_merge_{archive_date}",
        canonical_archive_dir=archive_root / f"canonical_before_kibot_merge_{archive_date}",
        quality_destination=market_data_dir / "quality" / f"kibot_merge_{archive_date}",
        archive_date=archive_date,
    )

    conflict_frames = []
    rejection_frames = []
    reviewed_path = market_data_dir / "reviewed_bars.csv"
    reviewed_bars = pd.read_csv(reviewed_path) if reviewed_path.exists() else None
    required_tickers: dict[str, set[str]] = {"daily": set(), "5min": set()}
    for metadata in (daily_meta, intraday_meta):
        output_dir = stage_root / metadata.frequency
        output_dir.mkdir(parents=True, exist_ok=True)
        imported_names = set()
        for member in metadata.members:
            vendor = Path(member).stem.upper()
            ticker = KIBOT_SYMBOL_MAP.get(vendor, vendor)
            required_tickers[metadata.frequency].add(ticker)
            kibot = read_kibot_member(
                metadata.path,
                member,
                metadata.frequency,
                acquired_at,
                reject_invalid_rows=True,
            )
            parser_rejections = kibot.attrs.get("rejections", [])
            if parser_rejections:
                rejection_frames.append(
                    pd.DataFrame(parser_rejections, columns=REJECTION_COLUMNS)
                )
            if kibot.empty:
                raise KibotDataError(
                    f"Kibot member {vendor} mapped to {ticker} has no usable rows"
                )
            imported_names.add(ticker)
            existing = _read_existing(market_data_dir / metadata.frequency / f"{ticker}.csv")
            member_reviews = reviewed_bars
            if reviewed_bars is not None:
                member_reviews = reviewed_bars[
                    reviewed_bars["symbol"].astype(str).eq(f"/{ticker}")
                    & reviewed_bars["frequency"].astype(str).eq(metadata.frequency)
                ]
            result = merge_market_data_sources(
                [kibot, existing], reviewed_bars=member_reviews
            )
            _atomic_csv(result.rows, output_dir / f"{ticker}.csv")
            if len(result.conflicts):
                conflict_frames.append(result.conflicts)
            if len(result.rejections):
                rejection_frames.append(result.rejections)

        active_dir = market_data_dir / metadata.frequency
        if active_dir.exists():
            for existing_path in sorted(active_dir.glob("*.csv")):
                if existing_path.stem not in imported_names:
                    shutil.copy2(existing_path, output_dir / existing_path.name)

    _validate_staged_coverage(
        stage_root, market_data_dir, required_tickers, minimum_research_rows
    )

    quality_stage.mkdir(parents=True, exist_ok=True)
    coverage_columns = ["symbol", "frequency", "rows", "first_timestamp", "last_timestamp", "sources"]
    transition_columns = ["symbol", "frequency", "timestamp", "from_source", "to_source"]
    gap_columns = ["symbol", "frequency", "previous_timestamp", "timestamp", "gap_minutes"]
    pd.DataFrame(_coverage_rows(stage_root), columns=coverage_columns).to_csv(quality_stage / "coverage.csv", index=False)
    pd.concat(conflict_frames, ignore_index=True).to_csv(quality_stage / "conflicts.csv", index=False) if conflict_frames else pd.DataFrame(columns=CONFLICT_COLUMNS).to_csv(quality_stage / "conflicts.csv", index=False)
    pd.concat(rejection_frames, ignore_index=True).to_csv(quality_stage / "rejections.csv", index=False) if rejection_frames else pd.DataFrame(columns=REJECTION_COLUMNS).to_csv(quality_stage / "rejections.csv", index=False)
    pd.DataFrame(_transition_rows(stage_root), columns=transition_columns).to_csv(quality_stage / "source_transitions.csv", index=False)
    pd.DataFrame(_multi_day_outage_rows(stage_root), columns=gap_columns).to_csv(
        quality_stage / "multi_day_outages.csv", index=False
    )
    _write_json(
        quality_stage / "source_manifest.json",
        {"archives": [
            {"path": str(item.path), "frequency": item.frequency, "sha256": item.sha256, "size": item.size, "members": list(item.members)}
            for item in (daily_meta, intraday_meta)
        ]},
    )
    _write_json(
        quality_stage / "merge_summary.json",
        {
            "status": "PASS",
            "timezone": "America/Detroit",
            "dst": {"ambiguous_rows": 0, "nonexistent_rows": 0},
            "gap_audit": {
                "kind": "multi_day_outage",
                "threshold_days": 3,
                "session_aware": False,
            },
            "required_free_bytes": required,
            "available_free_bytes": available,
            "minimum_research_rows": minimum_research_rows,
        },
    )
    _write_json(
        stage_root / "stage_complete.json",
        {"status": "PASS", "files": _stage_file_records(stage_root)},
    )
    return paths


def stage_kibot_merge(
    daily_zip,
    intraday_zip,
    market_data_dir,
    archive_root,
    acquired_at: str,
    *,
    free_bytes: int | None = None,
    minimum_research_rows: dict[str, int] | None = None,
) -> MigrationPaths:
    """Build and audit a merged repository without changing active data."""
    market_data_dir = Path(market_data_dir)
    stage_parent = market_data_dir / ".staging"
    stage_root = stage_parent / (
        f"kibot_merge_{acquired_at[:10]}_{uuid.uuid4().hex}"
    )
    try:
        return _stage_kibot_merge(
            daily_zip,
            intraday_zip,
            market_data_dir,
            archive_root,
            acquired_at,
            free_bytes=free_bytes,
            minimum_research_rows=minimum_research_rows,
            stage_root=stage_root,
        )
    except Exception:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        if stage_parent.exists() and not any(stage_parent.iterdir()):
            stage_parent.rmdir()
        raise


def publish_staged_repository(
    paths: MigrationPaths,
    *,
    rename: Callable[[os.PathLike, os.PathLike], None] = os.replace,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Publish staged data as one recoverable directory transaction."""
    destinations = [
        paths.vendor_archive_dir,
        paths.sixty_archive_dir,
        paths.canonical_archive_dir,
        paths.quality_destination,
    ]
    existing = [str(path) for path in destinations if path.exists()]
    if existing:
        raise PublishError(f"Archive destination already exists: {', '.join(existing)}")
    completion_path = paths.stage_root / "stage_complete.json"
    try:
        completion = json.loads(completion_path.read_text())
        expected_stage_files = {
            item["path"]: (int(item["size"]), item["sha256"])
            for item in completion["files"]
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublishError(f"Staged completion manifest is invalid: {error}") from error
    if completion.get("status") != "PASS":
        raise PublishError("Staged completion manifest status is not PASS")
    actual_stage_files = {
        path.relative_to(paths.stage_root).as_posix(): path
        for path in paths.stage_root.rglob("*")
        if path.is_file() and path.name != "stage_complete.json"
    }
    if set(actual_stage_files) != set(expected_stage_files):
        raise PublishError("Staged data changed after audit: file set differs")
    for relative_path, path in actual_stage_files.items():
        expected_size, expected_hash = expected_stage_files[relative_path]
        if path.stat().st_size != expected_size or _sha256(path) != expected_hash:
            raise PublishError(
                f"Staged data changed after audit: {relative_path}"
            )
    summary_path = paths.quality_stage / "merge_summary.json"
    if not summary_path.exists():
        raise PublishError("Staged merge summary is missing")
    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PublishError(f"Staged merge summary is invalid: {error}") from error
    if summary.get("status") != "PASS":
        raise PublishError("Staged merge summary status is not PASS")

    manifest_path = paths.quality_stage / "source_manifest.json"
    try:
        source_manifest = json.loads(manifest_path.read_text())
        expected_hashes = {
            item["frequency"]: item["sha256"]
            for item in source_manifest["archives"]
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise PublishError(f"Staged source manifest is invalid: {error}") from error
    for frequency, source in (("daily", paths.daily_zip), ("5min", paths.intraday_zip)):
        try:
            actual_hash = _sha256(source)
        except OSError as error:
            raise PublishError(f"Unable to verify staged source {source}: {error}") from error
        if expected_hashes.get(frequency) != actual_hash:
            raise PublishError(f"{source} changed after staging")

    moved_old = []
    installed_new = []
    vendor_moves = []
    sixty_moved = False
    quality_moved = False
    completion_archived = False
    fault_hook = fault_hook or (lambda _point: None)
    try:
        paths.canonical_archive_dir.mkdir(parents=True)
        for frequency in ("daily", "5min"):
            active = paths.market_data_dir / frequency
            archived = paths.canonical_archive_dir / frequency
            if active.exists():
                rename(active, archived)
                moved_old.append((archived, active))
            staged = paths.stage_root / frequency
            rename(staged, active)
            installed_new.append((active, staged))
        _write_json(paths.canonical_archive_dir / "archive_manifest.json", _directory_manifest(paths.canonical_archive_dir))
        fault_hook("after_canonical_manifest")

        active_sixty = paths.market_data_dir / "60min"
        if active_sixty.exists():
            paths.sixty_archive_dir.parent.mkdir(parents=True, exist_ok=True)
            rename(active_sixty, paths.sixty_archive_dir)
            sixty_moved = True
            _write_json(paths.sixty_archive_dir / "archive_manifest.json", _directory_manifest(paths.sixty_archive_dir))
        fault_hook("after_sixty_archive")

        paths.vendor_archive_dir.mkdir(parents=True)
        for source, name, point in (
            (paths.daily_zip, "daily.zip", "after_daily_zip"),
            (paths.intraday_zip, "intraday.zip", "after_intraday_zip"),
        ):
            destination = paths.vendor_archive_dir / name
            shutil.copy2(source, destination)
            if _sha256(source) != _sha256(destination):
                raise PublishError(f"Hash mismatch while archiving {source}")
            source.unlink()
            vendor_moves.append((destination, source))
            fault_hook(point)

        paths.quality_destination.parent.mkdir(parents=True, exist_ok=True)
        rename(paths.quality_stage, paths.quality_destination)
        quality_moved = True
        rename(
            completion_path,
            paths.quality_destination / "stage_complete.json",
        )
        completion_archived = True
        paths.stage_root.rmdir()
        stage_parent = paths.stage_root.parent
        if stage_parent.exists() and not any(stage_parent.iterdir()):
            stage_parent.rmdir()
        return {"status": "PASS", "market_data_dir": str(paths.market_data_dir)}
    except Exception as error:
        try:
            if quality_moved and paths.quality_destination.exists():
                rename(paths.quality_destination, paths.quality_stage)
            if completion_archived:
                archived_completion = paths.quality_stage / "stage_complete.json"
                if archived_completion.exists():
                    rename(archived_completion, completion_path)
            for archived_zip, source_zip in reversed(vendor_moves):
                if archived_zip.exists() and not source_zip.exists():
                    shutil.copy2(archived_zip, source_zip)
            if sixty_moved and paths.sixty_archive_dir.exists():
                (paths.sixty_archive_dir / "archive_manifest.json").unlink(missing_ok=True)
                rename(paths.sixty_archive_dir, paths.market_data_dir / "60min")
            for active, staged in reversed(installed_new):
                if active.exists():
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    rename(active, staged)
            for archived, active in reversed(moved_old):
                if archived.exists():
                    rename(archived, active)
            if paths.vendor_archive_dir.exists():
                shutil.rmtree(paths.vendor_archive_dir)
            manifest = paths.canonical_archive_dir / "archive_manifest.json"
            manifest.unlink(missing_ok=True)
            if paths.canonical_archive_dir.exists():
                shutil.rmtree(paths.canonical_archive_dir)
            if paths.sixty_archive_dir.exists():
                shutil.rmtree(paths.sixty_archive_dir)
            if paths.quality_destination.exists():
                shutil.rmtree(paths.quality_destination)
        except Exception as rollback_error:
            raise PublishError(f"{error}; rollback also failed: {rollback_error}") from error
        raise PublishError(str(error)) from error


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-zip", required=True, type=Path)
    parser.add_argument("--intraday-zip", required=True, type=Path)
    parser.add_argument("--market-data-dir", required=True, type=Path)
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--acquired-at", required=True)
    parser.add_argument("--publish", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = stage_kibot_merge(
        args.daily_zip,
        args.intraday_zip,
        args.market_data_dir,
        args.archive_root,
        args.acquired_at,
    )
    result = publish_staged_repository(paths) if args.publish else {
        "status": "STAGED",
        "stage_root": str(paths.stage_root),
        "quality": str(paths.quality_stage),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
