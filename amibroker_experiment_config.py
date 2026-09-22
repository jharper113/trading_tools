"""Validated configuration for repeatable AmiBroker experiment runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


RESEARCH_WINDOW = {"start": "2009-01-01", "end": "2019-01-01"}
JOB_FIELDS = {
    "job_id",
    "strategy_id",
    "source_afl",
    "source_sha256",
    "periodicity",
    "interval_seconds",
    "database",
    "project_template",
    "symbols",
    "adapter",
    "analysis_profile",
    "enabled",
}
PERIODICITY = {
    "Daily": (86400, "daily", "Analyzer_Profiles/Daily_Analyzer_Profile.json"),
    "5m": (300, "intraday", "Analyzer_Profiles/Intraday_5m_Analyzer_Profile.json"),
    "15m": (900, "intraday", "Analyzer_Profiles/Intraday_15m_Analyzer_Profile.json"),
    "60m": (3600, "intraday", "Analyzer_Profiles/Intraday_60m_Analyzer_Profile.json"),
}
DOCUMENTED_PERIODS = {
    "Daily": ("Daily",),
    "5min": ("5m",),
    "5min or 60min": ("5m", "60m"),
    "60min or 5min": ("60m", "5m"),
    "Intraday (match source interval)": ("15m",),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_digest(strategies: dict) -> str:
    serialized = json.dumps(strategies, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_strategy(strategy_id: str, entry: dict) -> None:
    required = {
        "source_afl",
        "source_sha256",
        "enabled",
        "native_periodicity",
        "supported_periodicities",
    }
    missing = required - entry.keys()
    if missing:
        raise ValueError(
            f"Catalog strategy {strategy_id} is missing: {', '.join(sorted(missing))}"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", entry["source_sha256"]):
        raise ValueError(f"Catalog strategy {strategy_id} has an invalid source hash")
    if entry["native_periodicity"] not in entry["supported_periodicities"]:
        raise ValueError(f"Catalog strategy {strategy_id} has an invalid native periodicity")
    unknown = set(entry["supported_periodicities"]) - PERIODICITY.keys()
    if unknown:
        raise ValueError(f"Catalog strategy {strategy_id} has unsupported periods: {unknown}")


def load_catalog(path: Path) -> dict:
    """Load a frozen strategy catalog and verify every canonical AFL hash."""
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported strategy catalog schema")
    if "strategies" in value:
        strategies = value["strategies"]
        root = Path(value.get("strategy_root_posix", path.parent))
    else:
        root = Path(value["strategy_root_posix"])
        documentation = Path(value["catalog_markdown_posix"])
        text = documentation.read_text(encoding="utf-8")
        timeframes = {
            match.group(1): match.group(2).strip()
            for match in re.finditer(
                r"^\|\s*\d+\s*\|\s*\[([^]]+)\]\([^)]+\)\s*\|\s*([^|]+?)\s*\|",
                text,
                re.MULTILINE,
            )
        }
        strategies = {}
        for source in sorted(root.glob("*.afl")):
            if source.stem not in timeframes:
                raise ValueError(f"Catalog documentation has no timeframe for {source.name}")
            documented = timeframes[source.stem]
            try:
                supported = list(DOCUMENTED_PERIODS[documented])
            except KeyError as exc:
                raise ValueError(
                    f"Unsupported documented timeframe for {source.stem}: {documented}"
                ) from exc
            strategies[source.stem] = {
                "source_afl": source.name,
                "source_sha256": _sha256(source),
                "documented_timeframe": documented,
                "native_periodicity": supported[0],
                "supported_periodicities": supported,
                "enabled": True,
            }
        if set(timeframes) != set(strategies):
            missing = sorted(set(timeframes) - set(strategies))
            raise ValueError(f"Catalog documentation references missing AFLs: {missing}")
        actual_digest = _catalog_digest(strategies)
        if actual_digest != value.get("catalog_sha256"):
            raise ValueError("Strategy catalog hash does not match canonical AFL files")
    if not strategies:
        raise ValueError("Strategy catalog is empty")
    for strategy_id, entry in strategies.items():
        _validate_strategy(strategy_id, entry)
        source = Path(entry["source_afl"])
        if not source.is_absolute():
            source = root / source
        if source.is_file() and _sha256(source) != entry["source_sha256"]:
            raise ValueError(f"Catalog strategy {strategy_id} source hash mismatch")
    return {
        **value,
        "strategy_root_posix": str(root),
        "strategies": strategies,
        "catalog_sha256": _catalog_digest(strategies),
    }


def _expanded_job(
    job_id: str,
    strategy_id: str,
    entry: dict,
    period: str,
    value: dict,
) -> dict:
    seconds, database_key, profile = PERIODICITY[period]
    database = value["databases"][database_key]
    project_template = value["project_templates"][
        "daily" if period == "Daily" else "intraday"
    ]
    symbol_set = "daily" if period == "Daily" else "intraday"
    return {
        "job_id": job_id,
        "strategy_id": strategy_id,
        "source_afl": entry["source_afl"],
        "source_sha256": entry["source_sha256"],
        "periodicity": period,
        "interval_seconds": seconds,
        "database": database,
        "project_template": project_template,
        "symbols": list(value["symbol_sets"][symbol_set]),
        "adapter": {"name": "native"},
        "analysis_profile": profile,
        "enabled": True,
    }


def load_matrix(path: Path, catalog: dict) -> dict:
    """Load and expand an experiment matrix, rejecting stale strategy inputs."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "matrix_id",
        "research_window",
        "timezone",
        "modes",
        "jobs",
    }
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Matrix is missing: {', '.join(sorted(missing))}")
    if value["schema_version"] != 1:
        raise ValueError("Unsupported experiment matrix schema")
    if value["research_window"] != RESEARCH_WINDOW:
        raise ValueError("Matrix research window must match the shared policy")
    if value["timezone"] != "America/Detroit":
        raise ValueError("Matrix timezone must be America/Detroit")
    expected_catalog_hash = value.get("catalog_sha256")
    if expected_catalog_hash and expected_catalog_hash != catalog.get("catalog_sha256"):
        raise ValueError("Matrix strategy catalog hash mismatch")

    jobs = {job_id: dict(job) for job_id, job in value["jobs"].items()}
    modes = {mode: list(ids) for mode, ids in value["modes"].items()}
    if modes.get("full") == ["$catalog_supported"]:
        modes["full"] = []
        for strategy_id, entry in sorted(catalog["strategies"].items()):
            if not entry["enabled"]:
                continue
            for period in entry["supported_periodicities"]:
                job_id = f"full-{strategy_id}-{period.lower()}"
                jobs[job_id] = _expanded_job(
                    job_id, strategy_id, entry, period, value
                )
                modes["full"].append(job_id)

    for mode, ids in modes.items():
        if len(ids) != len(set(ids)):
            raise ValueError(f"Mode {mode} contains duplicate jobs")
        for job_id in ids:
            if job_id not in jobs:
                raise ValueError(f"Mode {mode} references unknown job {job_id}")
    for job_id, job in jobs.items():
        missing_job = JOB_FIELDS - job.keys()
        if missing_job:
            raise ValueError(
                f"Job {job_id} is missing: {', '.join(sorted(missing_job))}"
            )
        if job["job_id"] != job_id:
            raise ValueError(f"Job {job_id} identity mismatch")
        strategy_id = job["strategy_id"]
        if strategy_id not in catalog["strategies"]:
            raise ValueError(f"Job {job_id} references unknown strategy")
        catalog_entry = catalog["strategies"][strategy_id]
        if job["source_sha256"] != catalog_entry["source_sha256"]:
            raise ValueError(f"Job {job_id} source hash mismatch")
        if job["periodicity"] not in PERIODICITY:
            raise ValueError(f"Job {job_id} has unsupported periodicity")
        if job["interval_seconds"] != PERIODICITY[job["periodicity"]][0]:
            raise ValueError(f"Job {job_id} interval does not match periodicity")
        if not job["symbols"] or len(job["symbols"]) != len(set(job["symbols"])):
            raise ValueError(f"Job {job_id} has an empty or duplicate symbol list")
        source = Path(job["source_afl"])
        if source.is_absolute() and source.is_file() and _sha256(source) != job["source_sha256"]:
            raise ValueError(f"Job {job_id} source file hash mismatch")
    return {**value, "jobs": jobs, "modes": modes}


def validate_unlock(path: Path, pilot_id: str, audit_sha256: str) -> dict:
    """Validate the small artifact that authorizes a full-library run."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("audit_status") != "PASS":
        raise ValueError("Full mode requires a passing audit")
    if value.get("pilot_experiment_id") != pilot_id:
        raise ValueError("Unlock pilot experiment mismatch")
    if value.get("audit_sha256") != audit_sha256:
        raise ValueError("Unlock audit hash mismatch")
    return value
