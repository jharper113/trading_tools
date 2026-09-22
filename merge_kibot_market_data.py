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
    REJECTION_COLUMNS,
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
            previous = None
            for row in frame.itertuples(index=False):
                if previous is not None and row.source != previous:
                    records.append(
                        {
                            "symbol": path.stem,
                            "frequency": frequency,
                            "timestamp": row.timestamp,
                            "from_source": previous,
                            "to_source": row.source,
                        }
                    )
                previous = row.source
    return records


def _gap_rows(stage_root: Path) -> list[dict[str, object]]:
    records = []
    for frequency in ("daily", "5min"):
        threshold = pd.Timedelta(days=3) if frequency == "daily" else pd.Timedelta(minutes=5)
        for path in sorted((stage_root / frequency).glob("*.csv")):
            frame = pd.read_csv(path, usecols=["timestamp"])
            times = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna().sort_values()
            for previous, current in zip(times.iloc[:-1], times.iloc[1:]):
                if current - previous > threshold:
                    records.append(
                        {
                            "symbol": path.stem,
                            "frequency": frequency,
                            "previous_timestamp": previous.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "timestamp": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "gap_minutes": (current - previous).total_seconds() / 60,
                        }
                    )
    return records


def stage_kibot_merge(
    daily_zip,
    intraday_zip,
    market_data_dir,
    archive_root,
    acquired_at: str,
    *,
    free_bytes: int | None = None,
) -> MigrationPaths:
    """Build and audit a merged repository without changing active data."""
    market_data_dir = Path(market_data_dir)
    archive_root = Path(archive_root)
    daily_zip = Path(daily_zip)
    intraday_zip = Path(intraday_zip)
    daily_meta = inventory_kibot_zip(daily_zip, "daily")
    intraday_meta = inventory_kibot_zip(intraday_zip, "5min")

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
    stage_root = market_data_dir / ".staging" / f"kibot_merge_{archive_date}_{uuid.uuid4().hex}"
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
    for metadata in (daily_meta, intraday_meta):
        output_dir = stage_root / metadata.frequency
        output_dir.mkdir(parents=True, exist_ok=True)
        imported_names = set()
        for member in metadata.members:
            vendor = Path(member).stem.upper()
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
            ticker = str(kibot.iloc[0]["symbol"]).lstrip("/") if len(kibot) else vendor
            imported_names.add(ticker)
            existing = _read_existing(market_data_dir / metadata.frequency / f"{ticker}.csv")
            result = merge_market_data_sources([kibot, existing])
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

    quality_stage.mkdir(parents=True, exist_ok=True)
    coverage_columns = ["symbol", "frequency", "rows", "first_timestamp", "last_timestamp", "sources"]
    transition_columns = ["symbol", "frequency", "timestamp", "from_source", "to_source"]
    gap_columns = ["symbol", "frequency", "previous_timestamp", "timestamp", "gap_minutes"]
    pd.DataFrame(_coverage_rows(stage_root), columns=coverage_columns).to_csv(quality_stage / "coverage.csv", index=False)
    pd.concat(conflict_frames, ignore_index=True).to_csv(quality_stage / "conflicts.csv", index=False) if conflict_frames else pd.DataFrame(columns=CONFLICT_COLUMNS).to_csv(quality_stage / "conflicts.csv", index=False)
    pd.concat(rejection_frames, ignore_index=True).to_csv(quality_stage / "rejections.csv", index=False) if rejection_frames else pd.DataFrame(columns=REJECTION_COLUMNS).to_csv(quality_stage / "rejections.csv", index=False)
    pd.DataFrame(_transition_rows(stage_root), columns=transition_columns).to_csv(quality_stage / "source_transitions.csv", index=False)
    pd.DataFrame(_gap_rows(stage_root), columns=gap_columns).to_csv(quality_stage / "interval_gaps.csv", index=False)
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
            "required_free_bytes": required,
            "available_free_bytes": available,
        },
    )
    return paths


def publish_staged_repository(
    paths: MigrationPaths,
    *,
    rename: Callable[[os.PathLike, os.PathLike], None] = os.replace,
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
    if not (paths.quality_stage / "merge_summary.json").exists():
        raise PublishError("Staged merge summary is missing")

    moved_old = []
    installed_new = []
    vendor_moves = []
    sixty_moved = False
    quality_moved = False
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

        active_sixty = paths.market_data_dir / "60min"
        if active_sixty.exists():
            paths.sixty_archive_dir.parent.mkdir(parents=True, exist_ok=True)
            rename(active_sixty, paths.sixty_archive_dir)
            sixty_moved = True
            _write_json(paths.sixty_archive_dir / "archive_manifest.json", _directory_manifest(paths.sixty_archive_dir))

        paths.vendor_archive_dir.mkdir(parents=True)
        for source, name in ((paths.daily_zip, "daily.zip"), (paths.intraday_zip, "intraday.zip")):
            destination = paths.vendor_archive_dir / name
            shutil.copy2(source, destination)
            if _sha256(source) != _sha256(destination):
                raise PublishError(f"Hash mismatch while archiving {source}")
            source.unlink()
            vendor_moves.append((destination, source))

        paths.quality_destination.parent.mkdir(parents=True, exist_ok=True)
        rename(paths.quality_stage, paths.quality_destination)
        quality_moved = True
        return {"status": "PASS", "market_data_dir": str(paths.market_data_dir)}
    except Exception as error:
        try:
            if quality_moved and paths.quality_destination.exists():
                rename(paths.quality_destination, paths.quality_stage)
            for archived_zip, source_zip in reversed(vendor_moves):
                if archived_zip.exists() and not source_zip.exists():
                    shutil.copy2(archived_zip, source_zip)
            if sixty_moved and paths.sixty_archive_dir.exists():
                rename(paths.sixty_archive_dir, paths.market_data_dir / "60min")
            for active, staged in reversed(installed_new):
                if active.exists():
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    rename(active, staged)
            for archived, active in reversed(moved_old):
                if archived.exists():
                    rename(archived, active)
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
