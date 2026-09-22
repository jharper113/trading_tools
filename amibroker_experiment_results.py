"""Independent admission and normalization of AmiBroker experiment results."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from amibroker_paths import resolve_portable_path


HERE = Path(__file__).resolve().parent
REQUIRED_COLUMNS = {
    "Net Profit",
    "Profit Factor",
    "# Trades",
    "CAR/MDD",
    "Max. Sys % Drawdown",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_number(value: object) -> float:
    text = str(value if value is not None else "").strip().replace(",", "").removesuffix("%")
    if text.upper() in {"", "N/A", "NAN", "INF", "+INF", "-INF"}:
        return math.nan
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256(path)
    if not hmac.compare_digest(actual, str(expected).lower()):
        raise ValueError(f"hash mismatch: {Path(path).name}")


def _resolved(path: str, parent: Path) -> Path:
    return resolve_portable_path(path, parent)


def load_experiment(path: Path) -> dict:
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported experiment manifest schema")
    matrix_path = _resolved(value.get("matrix_path", ""), path.parent)
    if not matrix_path.is_file():
        raise ValueError(f"Experiment matrix is missing: {matrix_path}")
    verify_sha256(matrix_path, value.get("matrix_sha256", ""))
    matrix = json.loads(matrix_path.read_text(encoding="utf-8-sig"))
    if matrix.get("schema_version") != 1:
        raise ValueError("Unsupported experiment matrix schema")
    return {
        **value,
        "_manifest_path": path,
        "_root": path.parent,
        "_matrix_path": matrix_path,
        "_matrix": matrix,
    }


def _reject(job_id: str, reason: str) -> dict:
    return {"job_id": job_id, "reason": reason}


def _verify_exports(run_path: Path, run: dict, field: str, expected: list[str]) -> None:
    entries = list(run.get(field, []))
    symbols = [str(entry.get("symbol", "")) for entry in entries]
    if len(symbols) != len(set(symbols)):
        raise ValueError(f"run archive has duplicate {field} symbols")
    if sorted(symbols) != sorted(expected):
        raise ValueError(f"run archive {field} do not match expected symbols")
    for entry in entries:
        relative = str(entry.get("file", "")).replace("\\", "/")
        path = run_path.joinpath(*relative.split("/"))
        if not path.is_file():
            raise ValueError(f"run archive is missing: {relative}")
        try:
            verify_sha256(path, entry.get("sha256", ""))
        except ValueError as exc:
            raise ValueError(f"run archive hash mismatch: {relative}") from exc


def _verify_experiment_provenance(run_path: Path, run: dict, job: dict, experiment: dict) -> None:
    """Verify the generated inputs for current schema-2 experiment archives."""
    if int(run.get("schema_version", 0) or 0) < 2:
        return
    project = run_path / "project.apx"
    formula = run_path / "formula.afl"
    if not project.is_file() or sha256(project) != run.get("project_sha256"):
        raise ValueError("archived project hash mismatch")
    if not formula.is_file():
        raise ValueError("archived formula is missing")
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError as exc:
        raise ValueError("archived project XML is invalid") from exc
    embedded = (root.findtext(".//FormulaContent") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    archived = formula.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not embedded or embedded != archived:
        raise ValueError("archived formula differs from APX FormulaContent")
    provenance = run.get("experiment") or {}
    if provenance.get("job_id") != job.get("job_id"):
        raise ValueError("run provenance job identity mismatch")
    if not provenance.get("attempt_id"):
        raise ValueError("run provenance attempt identity is missing")
    if provenance.get("source_sha256") != job.get("source_sha256"):
        raise ValueError("run provenance source hash mismatch")
    if provenance.get("matrix_sha256") != experiment.get("matrix_sha256"):
        raise ValueError("run provenance matrix hash mismatch")
    artifacts = {str(item.get("name")): item for item in run.get("experiment_artifacts", [])}
    required = {"matrix_path", "source_afl", "analysis_profile", "build_manifest"}
    if not required.issubset(artifacts):
        raise ValueError("run provenance artifacts are incomplete")
    for name, artifact in artifacts.items():
        path = resolve_portable_path(artifact.get("path"), run_path)
        if not path.is_file() or sha256(path) != artifact.get("sha256"):
            raise ValueError(f"run provenance artifact hash mismatch: {name}")
    build_path = resolve_portable_path(artifacts["build_manifest"]["path"], run_path)
    build = json.loads(build_path.read_text(encoding="utf-8-sig"))
    if build.get("job_id") != job.get("job_id"):
        raise ValueError("build manifest job identity mismatch")
    if build.get("attempt_id") != provenance.get("attempt_id"):
        raise ValueError("build manifest attempt identity mismatch")
    for name, artifact in build.get("outputs", {}).items():
        path = resolve_portable_path(artifact.get("path"), build_path.parent)
        if not path.is_file() or sha256(path) != artifact.get("sha256"):
            raise ValueError(f"build output hash mismatch: {name}")


def admit_jobs(experiment: dict) -> tuple[list[dict], list[dict]]:
    """Separate complete hash-valid jobs from failures with stable reasons."""
    matrix = experiment["_matrix"]
    admitted, rejected = [], []
    for state in experiment.get("jobs", []):
        job_id = str(state.get("job_id", ""))
        status = str(state.get("status", ""))
        if status != "COMPLETE":
            rejected.append(_reject(job_id, f"experiment job status is {status or 'MISSING'}"))
            continue
        try:
            job = matrix.get("jobs", {}).get(job_id)
            if not job:
                raise ValueError("job is absent from the experiment matrix")
            run_manifest_path = _resolved(
                str(state.get("run_manifest_path", "")), experiment["_root"]
            )
            if not run_manifest_path.is_file():
                raise ValueError("run manifest is missing")
            verify_sha256(run_manifest_path, state.get("run_manifest_sha256", ""))
            run = json.loads(run_manifest_path.read_text(encoding="utf-8-sig"))
            if run.get("status") != "COMPLETE":
                raise ValueError(f"run manifest status is {run.get('status', 'MISSING')}")
            expected = [str(symbol) for symbol in job.get("symbols", [])]
            if sorted(run.get("expected_symbols", [])) != sorted(expected):
                raise ValueError("run manifest expected symbols do not match the matrix")
            _verify_exports(run_manifest_path.parent, run, "exports", expected)
            expected_audit = [str(symbol) for symbol in run.get("expected_audit_symbols", [])]
            if int(run.get("schema_version", 0) or 0) >= 2 and sorted(expected_audit) != sorted(expected):
                raise ValueError("run manifest audit symbols do not match the matrix")
            if expected_audit:
                if sorted(expected_audit) != sorted(expected):
                    raise ValueError("run manifest audit symbols do not match the matrix")
                _verify_exports(run_manifest_path.parent, run, "audit_exports", expected)
            _verify_experiment_provenance(run_manifest_path.parent, run, job, experiment)
            profile_path = _resolved(str(job.get("analysis_profile", "")), HERE)
            admitted.append(
                {
                    "experiment_id": experiment["experiment_id"],
                    "job_id": job_id,
                    "strategy": str(job.get("strategy_id", state.get("strategy", ""))),
                    "periodicity": str(job.get("periodicity", state.get("periodicity", ""))),
                    "schedule": str(job.get("adapter", {}).get("name", state.get("adapter", "native"))),
                    "run_path": run_manifest_path.parent,
                    "results_dir": run_manifest_path.parent,
                    "run_manifest_path": run_manifest_path,
                    "run_manifest": run,
                    "project_path": run_manifest_path.parent / "project.apx",
                    "analysis_profile": profile_path,
                    "symbols": expected,
                    "source_hash": str(job.get("source_sha256", "")),
                    "research_window": matrix.get("research_window", {}),
                    "matrix_job": job,
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            rejected.append(_reject(job_id, str(exc)))
    return admitted, rejected


def _median(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(pd.Series(finite, dtype=float).median()) if finite else math.nan


def _quantile(values: list[float], q: float) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(pd.Series(finite, dtype=float).quantile(q)) if finite else math.nan


def summarize_job(job: dict, profile: dict, groups: dict[str, list[str]]) -> list[dict]:
    thresholds = profile["thresholds"]["core"]
    sector_by_symbol = {
        str(symbol).upper().lstrip("/"): sector
        for sector, symbols in groups.items()
        for symbol in symbols
    }
    run_path = Path(job["run_path"])
    records = []
    seen_symbols = set()
    for export in job["run_manifest"].get("exports", []):
        symbol = str(export["symbol"]).upper().lstrip("/")
        if symbol in seen_symbols:
            raise ValueError(f"Duplicate optimization export for {symbol}")
        seen_symbols.add(symbol)
        relative = str(export["file"]).replace("\\", "/")
        frame = pd.read_csv(run_path.joinpath(*relative.split("/")))
        frame.columns = [str(column).strip() for column in frame.columns]
        missing = REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"{relative} is missing: {', '.join(sorted(missing))}")
        parameters = sorted(column for column in frame if column.startswith("Opt "))
        parameter_grid = [
            tuple(parse_number(value) for value in row)
            for row in frame[parameters].itertuples(index=False, name=None)
        ] if parameters else []
        if parameters and (any(not all(math.isfinite(v) for v in cell) for cell in parameter_grid)
                           or len(parameter_grid) != len(set(parameter_grid))):
            raise ValueError(f"{relative} has invalid or duplicate parameter coordinates")
        net = [parse_number(value) for value in frame["Net Profit"]]
        trades = [parse_number(value) for value in frame["# Trades"]]
        pf = [parse_number(value) for value in frame["Profit Factor"]]
        car_mdd = [parse_number(value) for value in frame["CAR/MDD"]]
        drawdown = [abs(parse_number(value)) for value in frame["Max. Sys % Drawdown"]]
        eligible_indexes = [index for index, value in enumerate(trades) if math.isfinite(value) and value > 0]
        parameter_rows = len(frame)
        profitable_pct = (
            100.0 * sum(math.isfinite(value) and value > 0 for value in net) / parameter_rows
            if parameter_rows else 0.0
        )
        median_pf = _median([pf[index] for index in eligible_indexes])
        median_trades = _median([trades[index] for index in eligible_indexes])
        ranking_ready = bool(parameter_rows) and all(math.isfinite(value) for value in car_mdd) and all(
            math.isfinite(value) and value > 0 for value in drawdown
        )
        individual_pass = bool(eligible_indexes) and (
            profitable_pct >= thresholds["profitable_paramsets_pct"]
            and median_pf >= thresholds["median_profit_factor"]
            and median_trades >= thresholds["median_trades"]
        )
        records.append(
            {
                "experiment_id": job["experiment_id"],
                "job_id": job["job_id"],
                "strategy": job["strategy"],
                "periodicity": job["periodicity"],
                "schedule": job["schedule"],
                "symbol": symbol,
                "sector": sector_by_symbol.get(symbol, "Other"),
                "parameter_rows": parameter_rows,
                "eligible_rows": len(eligible_indexes),
                "profitable_paramsets_pct": profitable_pct,
                "median_profit_factor": median_pf,
                "median_trades": median_trades,
                "median_car_mdd": _median(car_mdd),
                "lower_quartile_car_mdd": _quantile(car_mdd, .25),
                "median_net_profit": _median(net),
                "median_max_drawdown_pct": _median(drawdown),
                "individual_pass": individual_pass,
                "sector_pass": False,
                "selected_representative": False,
                "tested_count": 0,
                "passing_count": 0,
                "run_path": str(run_path),
                "source_hash": job["source_hash"],
                "parameter_names": parameters,
                "parameter_grid": [list(cell) for cell in sorted(parameter_grid)],
                "ranking_ready": ranking_ready,
            }
        )
    return records


def _tie_value(value: object) -> float:
    parsed = parse_number(value)
    return parsed if math.isfinite(parsed) else -math.inf


def evaluate_sectors(records: list[dict]) -> list[dict]:
    """Apply sector breadth and select at most one representative per job/sector."""
    output = [dict(record) for record in records]
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for record in output:
        key = (record["experiment_id"], record["job_id"], record["sector"])
        buckets.setdefault(key, []).append(record)
    for candidates in buckets.values():
        tested = [row for row in candidates if row["parameter_rows"] > 0]
        passing = [row for row in tested if row["individual_pass"]]
        sector_pass = bool(tested) and len(passing) >= 3 and len(passing) / len(tested) >= .5
        for row in candidates:
            row["tested_count"] = len(tested)
            row["passing_count"] = len(passing)
            row["sector_pass"] = sector_pass
            row["selected_representative"] = False
        if not sector_pass or not passing:
            continue
        first_names = passing[0]["parameter_names"]
        first_grid = passing[0]["parameter_grid"]
        comparable = all(
            row["ranking_ready"]
            and row["parameter_names"] == first_names
            and row["parameter_grid"] == first_grid
            and math.isfinite(row["median_car_mdd"])
            for row in passing
        )
        if not comparable:
            continue
        leader_score = max(row["median_car_mdd"] for row in passing)
        near = [
            row for row in passing
            if leader_score <= 0 or row["median_car_mdd"] >= .9 * leader_score
        ]
        winner = max(
            near,
            key=lambda row: (
                _tie_value(row.get("execution_quality")),
                _tie_value(row.get("liquidity_volume")),
                _tie_value(row["lower_quartile_car_mdd"]),
                _tie_value(row["median_car_mdd"]),
                row["symbol"],
            ),
        )
        winner["selected_representative"] = True
    return output
