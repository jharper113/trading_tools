#!/usr/bin/env python3
"""Independently audit an AmiBroker pilot before authorizing a full run."""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openpyxl
import pandas as pd

from amibroker_experiment_config import load_catalog, load_matrix
from amibroker_experiment_results import (
    admit_jobs,
    evaluate_sectors,
    load_experiment,
    parse_number,
    sha256,
    summarize_job,
)
from analyze_amibroker_experiment import _comparisons


HERE = Path(__file__).resolve().parent
METRIC_KEYS = [
    "experiment_id", "job_id", "strategy", "periodicity", "schedule", "symbol", "sector",
    "parameter_rows", "eligible_rows", "profitable_paramsets_pct", "median_profit_factor",
    "median_trades", "median_car_mdd", "lower_quartile_car_mdd", "median_net_profit",
    "median_max_drawdown_pct", "individual_pass", "sector_pass", "selected_representative",
    "tested_count", "passing_count", "source_hash", "parameter_names", "parameter_grid",
    "ranking_ready",
]
COMPARISON_KEYS = [
    "strategy", "symbol", "native_job", "low_touch_job", "shared_grid_rows", "grid_coverage",
    "native_median_car_mdd", "low_touch_median_car_mdd", "relative_car_mdd_improvement",
    "profit_factor_improvement", "drawdown_reduction", "recommendation", "reason",
]


@dataclass(frozen=True)
class AuditResult:
    json_path: Path
    markdown_path: Path
    unlock_path: Path


def equal_number(actual: object, expected: object) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _node(root, *names):
    for name in names:
        value = root.findtext(f".//{name}")
        if value is not None:
            return value.strip()
    return ""


def _same(actual, expected) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) or isinstance(expected, (int, float)):
        if actual is None or expected is None:
            return actual is expected
        return equal_number(actual, expected)
    return actual == expected


def _compare_rows(actual: list[dict], expected: list[dict], keys: list[str], failures: list[str], label: str):
    identity = [key for key in ("job_id", "symbol", "native_job", "low_touch_job") if key in keys]
    sorter = lambda row: tuple(str(row.get(key, "")) for key in identity)
    left, right = sorted(actual, key=sorter), sorted(expected, key=sorter)
    if len(left) != len(right):
        failures.append(f"{label} row count differs: recomputed {len(left)}, summary {len(right)}")
        return
    for index, (recomputed, recorded) in enumerate(zip(left, right), start=1):
        for key in keys:
            if not _same(recomputed.get(key), recorded.get(key)):
                failures.append(f"{label} row {index} {key} differs")


def _validate_project(job: dict, failures: list[str]):
    project = Path(job["project_path"])
    try:
        root = ET.parse(project).getroot()
    except (OSError, ET.ParseError) as exc:
        failures.append(f"{job['job_id']}: project APX is invalid: {exc}")
        return
    matrix_job = job["matrix_job"]
    profile = json.loads(Path(job["analysis_profile"]).read_text(encoding="utf-8-sig"))
    expected = {
        "ChartInterval": matrix_job["interval_seconds"],
        "Periodicity": profile["periodicity"]["apx_code"],
        "FromDate": job["research_window"]["start"],
        "ToDate": job["research_window"]["end"],
        "InitialEquity": profile["initial_equity"],
        "CommissionMode": profile["commission_mode"],
        "CommissionAmount": profile["commission_per_contract_side"],
        "PointsOnlyTest": 1 if profile["futures_mode"] else 0,
        "MinShares": profile["min_shares"],
        "AllowSameBarExit": 1 if profile["allow_same_bar_exit"] else 0,
        "ReverseSignalForcesExit": 1 if profile["reverse_signal_forces_exit"] else 0,
        "UsePrevBarEquity": 1 if profile["use_previous_bar_equity"] else 0,
        "OptTarget": profile["fitness"],
    }
    aliases = {"CommissionAmount": ("CommissionAmount", "CommissionValue"),
               "UsePrevBarEquity": ("UsePrevBarEquity", "UsePrevBarEquityForPosSizing")}
    for key, value in expected.items():
        actual = _node(root, *aliases.get(key, (key,)))
        if key in {"FromDate", "ToDate", "OptTarget"}:
            matches = actual[:10] == str(value) if key != "OptTarget" else actual == str(value)
        else:
            matches = equal_number(actual, value)
        if not matches:
            failures.append(f"{job['job_id']}: APX {key} differs")
    formula_path = Path(job["run_path"]) / "formula.afl"
    if not formula_path.is_file():
        failures.append(f"{job['job_id']}: archived formula is missing")
    else:
        embedded = _node(root, "FormulaContent").replace("\r\n", "\n").replace("\r", "\n").strip()
        file_text = formula_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
        if embedded and embedded != file_text:
            failures.append(f"{job['job_id']}: APX embedded formula differs from formula.afl")
    if Path(_node(root, "FormulaPath").replace("\\", "/")).name.lower() != "formula.afl":
        failures.append(f"{job['job_id']}: APX FormulaPath does not select the archived formula")


def _validate_artifacts(job: dict, failures: list[str]):
    run = job["run_manifest"]
    experiment = run.get("experiment", {}) or {}
    if experiment.get("source_sha256") != job["source_hash"]:
        failures.append(f"{job['job_id']}: source hash differs from matrix")
    if experiment.get("adapter") and experiment.get("adapter") != job["schedule"]:
        failures.append(f"{job['job_id']}: archived adapter differs from matrix")
    for artifact in run.get("experiment_artifacts", []):
        path = Path(str(artifact.get("path", "")))
        if not path.is_file() or sha256(path) != artifact.get("sha256"):
            failures.append(f"{job['job_id']}: experiment artifact changed: {artifact.get('name')}")


def _validate_entries(job: dict, failures: list[str], checks: list[dict]):
    distribution = {}
    exports = job["run_manifest"].get("audit_exports", [])
    if {str(item.get("symbol")) for item in exports} != set(job["symbols"]):
        failures.append(f"{job['job_id']}: entry audit symbols differ from the matrix")
    for export in exports:
        path = Path(job["run_path"]) / Path(str(export["file"]).replace("\\", "/"))
        frame = pd.read_csv(path)
        frame.columns = [str(column).strip() for column in frame]
        if "TimeNum" not in frame:
            failures.append(f"{job['job_id']}/{export['symbol']}: audit has no TimeNum")
            continue
        entries = frame[(pd.to_numeric(frame.get("Buy", 0), errors="coerce").fillna(0) != 0) |
                        (pd.to_numeric(frame.get("Short", 0), errors="coerce").fillna(0) != 0)]
        times = pd.to_numeric(entries["TimeNum"], errors="coerce")
        distribution[str(export["symbol"])] = {
            str(int(key)): int(value) for key, value in times.value_counts().sort_index().items()
        }
        if times.isna().any():
            failures.append(f"{job['job_id']}/{export['symbol']}: audit has malformed entry time")
        if job["schedule"] == "entry_cutoff_110000" and (times > 110000).any():
            failures.append(f"{job['job_id']}/{export['symbol']}: low-touch entry after 11:00 Eastern")
        if job["schedule"] == "fixed_110000" and (not len(times) or not times.eq(111500).all()):
            failures.append(f"{job['job_id']}/{export['symbol']}: 11:00 signal did not enter on 11:15 bar")
    checks.append({"check": "entry_time_distribution", "job_id": job["job_id"], "value": distribution})


def _validate_reports(summary: dict, failures: list[str]):
    for report in summary.get("detailed_reports", []):
        for key, value in report.items():
            if not key.endswith("_path") or not value:
                continue
            digest_key = key[:-5] + "_sha256"
            if digest_key not in report:
                continue
            path = Path(value)
            if not path.is_file() or sha256(path) != report[digest_key]:
                failures.append(f"{report.get('job_id')}: detailed report changed: {key}")


def _validate_workbook(summary_path: Path, summary: dict, failures: list[str]):
    workbook = summary_path.parent / f"{summary['experiment_id']}_Optimization_Review.xlsx"
    if not workbook.is_file():
        failures.append("analysis workbook is missing")
        return
    try:
        sheet = openpyxl.load_workbook(workbook, data_only=False, read_only=True)["All Symbol Results"]
        values = list(sheet.values)
    except Exception as exc:
        failures.append(f"analysis workbook cannot be read: {exc}")
        return
    if not values:
        failures.append("analysis workbook symbol sheet is empty")
        return
    headings = list(values[0])
    workbook_rows = [dict(zip(headings, row)) for row in values[1:]]
    keys = [key for key in ("job_id", "symbol", "median_profit_factor", "median_car_mdd",
                            "individual_pass", "sector_pass", "selected_representative") if key in headings]
    _compare_rows(
        [{key: row.get(key) for key in keys} for row in summary.get("all_symbol_results", [])],
        [{key: row.get(key) for key in keys} for row in workbook_rows],
        keys, failures, "workbook",
    )


def _validate_reference(reference_path: Path, jobs: list[dict], failures: list[str]):
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8-sig"))
    selected = [job for job in jobs if job["job_id"] == reference.get("automated_job_id")]
    if len(selected) != 1:
        failures.append("manual reference job is not an admitted pilot job")
        return
    job = selected[0]
    for name in ("apx", "formula"):
        path = Path(reference.get(f"{name}_path", ""))
        if not path.is_file() or sha256(path) != reference.get(f"{name}_sha256"):
            failures.append(f"manual reference {name} hash mismatch")
    auto_root = Path(job["run_path"])
    try:
        auto_xml = ET.parse(auto_root / "project.apx").getroot()
        ref_xml = ET.parse(reference["apx_path"]).getroot()
        fields = {
            "Periodicity": ("Periodicity",), "ChartInterval": ("ChartInterval",),
            "FromDate": ("FromDate",), "ToDate": ("ToDate",),
            "InitialEquity": ("InitialEquity",), "CommissionMode": ("CommissionMode",),
            "CommissionAmount": ("CommissionAmount", "CommissionValue"),
            "PointsOnlyTest": ("PointsOnlyTest",), "MinShares": ("MinShares",),
            "AllowSameBarExit": ("AllowSameBarExit",),
            "ReverseSignalForcesExit": ("ReverseSignalForcesExit",),
            "UsePrevBarEquity": ("UsePrevBarEquity", "UsePrevBarEquityForPosSizing"),
            "OptTarget": ("OptTarget",),
        }
        for field, aliases in fields.items():
            if _node(auto_xml, *aliases) != _node(ref_xml, *aliases):
                failures.append(f"manual reference APX {field} differs")
    except (OSError, ET.ParseError) as exc:
        failures.append(f"manual reference APX is invalid: {exc}")
    if Path(reference.get("formula_path", "")).is_file() and (auto_root / "formula.afl").is_file():
        if sha256(auto_root / "formula.afl") != sha256(Path(reference["formula_path"])):
            failures.append("manual reference formula differs")
    expected = {entry["symbol"]: entry for entry in job["run_manifest"].get("exports", [])}
    supplied = reference.get("exports", {})
    if set(supplied) != set(expected):
        failures.append("manual reference symbols differ")
        return
    for symbol, reference_entry in supplied.items():
        ref_path = Path(reference_entry.get("path", ""))
        if not ref_path.is_file() or sha256(ref_path) != reference_entry.get("sha256"):
            failures.append(f"manual reference export hash mismatch: {symbol}")
            continue
        automatic = pd.read_csv(auto_root / expected[symbol]["file"])
        manual = pd.read_csv(ref_path)
        if list(automatic.columns) != list(manual.columns) or automatic.shape != manual.shape:
            failures.append(f"manual reference schema differs: {symbol}")
            continue
        for column in automatic.columns:
            for left, right in zip(automatic[column], manual[column]):
                left_number, right_number = parse_number(left), parse_number(right)
                if math.isfinite(left_number) and math.isfinite(right_number):
                    matches = equal_number(left_number, right_number)
                else:
                    matches = str(left).strip() == str(right).strip()
                if not matches:
                    failures.append(f"manual reference metric differs: {symbol}/{column}")
                    break


def _write_json(path: Path, value: dict):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _windows_path(path: Path) -> str:
    text = str(Path(path).resolve())
    prefix = "/home/jon/Dropbox/HarpFolders"
    return ("Z:" + text[len(prefix):]).replace("/", "\\") if text.startswith(prefix) else text


def _expanded_matrix(experiment: dict, destination: Path) -> Path:
    matrix = experiment["_matrix"]
    if matrix.get("modes", {}).get("full") == ["$catalog_supported"]:
        catalog = load_catalog(HERE / "amibroker_experiments" / "strategy_catalog.json")
        matrix = load_matrix(experiment["_matrix_path"], catalog)
    _write_json(destination, matrix)
    return destination


def run_audit(experiment_path: Path, analysis_path: Path, reference_path: Optional[Path] = None) -> AuditResult:
    analysis_path = Path(analysis_path).resolve()
    output = analysis_path.parent
    json_path = output / "pilot_audit.json"
    markdown_path = output / "pilot_audit.md"
    unlock_path = output / "full_run_unlock.json"
    if unlock_path.exists():
        unlock_path.unlink()
    failures, checks = [], []
    try:
        experiment = load_experiment(experiment_path)
        summary = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
        jobs, rejected = admit_jobs(experiment)
        if rejected:
            failures.extend(f"{row['job_id']}: {row['reason']}" for row in rejected)
        if summary.get("experiment_id") != experiment.get("experiment_id"):
            failures.append("analysis experiment ID differs")
        expected_counts = {
            "total": len(experiment.get("jobs", [])),
            "admitted": len(jobs),
            "invalid": len(rejected),
        }
        for key, value in expected_counts.items():
            if summary.get("job_counts", {}).get(key) != value:
                failures.append(f"analysis job count differs: {key}")
        groups = json.loads((HERE / "peer_robustness_policy.json").read_text())["economic_groups"]
        recomputed = []
        for job in jobs:
            _validate_project(job, failures)
            _validate_artifacts(job, failures)
            _validate_entries(job, failures, checks)
            profile = json.loads(Path(job["analysis_profile"]).read_text(encoding="utf-8-sig"))
            recomputed.extend(summarize_job(job, profile, groups))
        recomputed = evaluate_sectors(recomputed)
        _compare_rows(recomputed, summary.get("all_symbol_results", []), METRIC_KEYS, failures, "symbol results")
        comparisons = _comparisons(jobs, recomputed)
        _compare_rows(comparisons, summary.get("schedule_comparisons", []), COMPARISON_KEYS, failures, "schedule comparisons")
        _compare_rows(
            [row for row in comparisons if row["recommendation"] == "LOW_TOUCH"],
            summary.get("low_touch_recommendations", []), COMPARISON_KEYS, failures,
            "low-touch recommendations",
        )
        _compare_rows(
            [row for row in comparisons if row["recommendation"] == "FREQUENT_ENTRY_WFA_CANDIDATE"],
            summary.get("frequent_entry_exceptions", []), COMPARISON_KEYS, failures,
            "frequent-entry exceptions",
        )
        expected_passed = [row for row in recomputed if row["selected_representative"]]
        _compare_rows(expected_passed, summary.get("passed_candidates", []), METRIC_KEYS, failures, "passed candidates")
        _validate_reports(summary, failures)
        _validate_workbook(analysis_path, summary, failures)
        for name, artifact in experiment.get("policy_hashes", {}).items():
            path = Path(artifact.get("path", ""))
            if not path.is_file() or sha256(path) != artifact.get("sha256"):
                failures.append(f"experiment policy changed: {name}")
        if reference_path is not None:
            _validate_reference(Path(reference_path), jobs, failures)
        checks.append({"check": "matrix_sha256", "status": "PASS",
                       "value": sha256(experiment["_matrix_path"])})
    except Exception as exc:
        experiment = {"experiment_id": "unknown", "_matrix_path": Path(experiment_path)}
        failures.append(f"audit exception: {exc}")
    status = "FAIL" if failures else "PASS" if reference_path is not None else "MANUAL CHECK REQUIRED"
    report = {
        "schema_version": 1,
        "experiment_id": experiment.get("experiment_id", "unknown"),
        "status": status,
        "experiment_manifest": str(Path(experiment_path).resolve()),
        "analysis_summary": str(analysis_path),
        "manual_reference": str(Path(reference_path).resolve()) if reference_path else None,
        "checks": checks,
        "failures": failures,
    }
    _write_json(json_path, report)
    markdown_path.write_text(
        f"# AmiBroker Pilot Audit\n\n**Status:** {status}\n\n" +
        ("## Failures\n\n" + "\n".join(f"- {item}" for item in failures) + "\n" if failures else "No internal failures found.\n"),
        encoding="utf-8",
    )
    if status == "PASS":
        full_matrix = _expanded_matrix(experiment, output / "full_strategy_test_matrix.json")
        unlock = {
            "schema_version": 1,
            "audit_status": "PASS",
            "pilot_experiment_id": report["experiment_id"],
            "matrix_sha256": sha256(experiment["_matrix_path"]),
            "audit_path": str(json_path),
            "audit_path_windows": _windows_path(json_path),
            "audit_sha256": sha256(json_path),
            "full_matrix_path": str(full_matrix),
            "full_matrix_path_windows": _windows_path(full_matrix),
            "full_matrix_sha256": sha256(full_matrix),
        }
        _write_json(unlock_path, unlock)
    return AuditResult(json_path, markdown_path, unlock_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_manifest", type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--reference-run", type=Path)
    args = parser.parse_args(argv)
    result = run_audit(args.experiment_manifest, args.analysis, args.reference_run)
    report = json.loads(result.json_path.read_text())
    print(f"Audit: {report['status']}")
    print(f"Report: {result.json_path}")
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
