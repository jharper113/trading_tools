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
    load_experiment,
    parse_number,
    sha256,
)
from amibroker_experiment_workbook import COMPARISON_COLUMNS, FAILURE_COLUMNS, SHEET_NAMES, SYMBOL_COLUMNS
from amibroker_paths import resolve_portable_path


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
    actual_missing = actual is None or (isinstance(actual, float) and not math.isfinite(actual))
    expected_missing = expected is None or (isinstance(expected, float) and not math.isfinite(expected))
    if actual_missing or expected_missing:
        return actual_missing and expected_missing
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


def _median(values) -> float:
    finite = [parse_number(value) for value in values]
    finite = [value for value in finite if math.isfinite(value)]
    return float(pd.Series(finite, dtype=float).median()) if finite else math.nan


def _independent_symbol_results(job: dict, profile: dict, groups: dict) -> list[dict]:
    thresholds = profile["thresholds"]["core"]
    sectors = {str(symbol).upper().lstrip("/"): sector for sector, symbols in groups.items() for symbol in symbols}
    output = []
    for export in job["run_manifest"].get("exports", []):
        symbol = str(export["symbol"]).upper().lstrip("/")
        path = Path(job["run_path"]) / Path(str(export["file"]).replace("\\", "/"))
        frame = pd.read_csv(path)
        frame.columns = [str(column).strip() for column in frame]
        required = {"Net Profit", "Profit Factor", "# Trades", "CAR/MDD", "Max. Sys % Drawdown"}
        if not required.issubset(frame):
            raise ValueError(f"{export['file']} is missing required metrics")
        parameters = sorted(column for column in frame if column.startswith("Opt "))
        grid = [tuple(parse_number(value) for value in row)
                for row in frame[parameters].itertuples(index=False, name=None)] if parameters else []
        if parameters and (any(not all(math.isfinite(value) for value in point) for point in grid)
                           or len(grid) != len(set(grid))):
            raise ValueError(f"{export['file']} has invalid parameter coordinates")
        net = [parse_number(value) for value in frame["Net Profit"]]
        trades = [parse_number(value) for value in frame["# Trades"]]
        profit_factor = [parse_number(value) for value in frame["Profit Factor"]]
        car_mdd = [parse_number(value) for value in frame["CAR/MDD"]]
        drawdown = [abs(parse_number(value)) for value in frame["Max. Sys % Drawdown"]]
        eligible = [index for index, value in enumerate(trades) if math.isfinite(value) and value > 0]
        count = len(frame)
        profitable = 100.0 * sum(math.isfinite(value) and value > 0 for value in net) / count if count else 0.0
        median_pf = _median(profit_factor[index] for index in eligible)
        median_trades = _median(trades[index] for index in eligible)
        finite_car = [value for value in car_mdd if math.isfinite(value)]
        ranking_ready = bool(count) and len(finite_car) == count and all(math.isfinite(value) and value > 0 for value in drawdown)
        individual = bool(eligible) and profitable >= thresholds["profitable_paramsets_pct"] \
            and median_pf >= thresholds["median_profit_factor"] and median_trades >= thresholds["median_trades"]
        output.append({
            "experiment_id": job["experiment_id"], "job_id": job["job_id"],
            "strategy": job["strategy"], "periodicity": job["periodicity"],
            "schedule": job["schedule"], "symbol": symbol,
            "sector": sectors.get(symbol, "Other"), "parameter_rows": count,
            "eligible_rows": len(eligible), "profitable_paramsets_pct": profitable,
            "median_profit_factor": median_pf, "median_trades": median_trades,
            "median_car_mdd": _median(car_mdd),
            "lower_quartile_car_mdd": float(pd.Series(finite_car).quantile(.25)) if finite_car else math.nan,
            "median_net_profit": _median(net), "median_max_drawdown_pct": _median(drawdown),
            "individual_pass": individual, "sector_pass": False,
            "selected_representative": False, "tested_count": 0, "passing_count": 0,
            "run_path": str(job["run_path"]), "source_hash": job["source_hash"],
            "parameter_names": parameters, "parameter_grid": [list(point) for point in sorted(grid)],
            "ranking_ready": ranking_ready,
        })
    return output


def _independent_sector_gate(records: list[dict]) -> list[dict]:
    output = [dict(row) for row in records]
    buckets = {}
    for row in output:
        buckets.setdefault((row["experiment_id"], row["job_id"], row["sector"]), []).append(row)
    for candidates in buckets.values():
        tested = [row for row in candidates if row["parameter_rows"] > 0]
        passing = [row for row in tested if row["individual_pass"]]
        sector_pass = bool(tested) and len(passing) >= 3 and len(passing) / len(tested) >= .5
        for row in candidates:
            row.update(tested_count=len(tested), passing_count=len(passing),
                       sector_pass=sector_pass, selected_representative=False)
        if not sector_pass or not passing:
            continue
        names, grid = passing[0]["parameter_names"], passing[0]["parameter_grid"]
        if not all(row["ranking_ready"] and row["parameter_names"] == names
                   and row["parameter_grid"] == grid and math.isfinite(row["median_car_mdd"])
                   for row in passing):
            continue
        best = max(row["median_car_mdd"] for row in passing)
        near = [row for row in passing if best <= 0 or row["median_car_mdd"] >= .9 * best]
        winner = max(near, key=lambda row: (
            parse_number(row.get("execution_quality")) if math.isfinite(parse_number(row.get("execution_quality"))) else -math.inf,
            parse_number(row.get("liquidity_volume")) if math.isfinite(parse_number(row.get("liquidity_volume"))) else -math.inf,
            row["lower_quartile_car_mdd"], row["median_car_mdd"], row["symbol"],
        ))
        winner["selected_representative"] = True
    return output


def _job_raw_rows(job: dict) -> pd.DataFrame:
    frames = []
    for export in job["run_manifest"].get("exports", []):
        path = Path(job["run_path"]) / Path(str(export["file"]).replace("\\", "/"))
        frame = pd.read_csv(path)
        frame["Symbol"] = str(export["symbol"]).upper().lstrip("/")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _independent_comparisons(jobs: list[dict], records: list[dict]) -> list[dict]:
    buckets = {}
    for job in jobs:
        buckets.setdefault((job["strategy"], job["periodicity"]), []).append(job)
    output = []
    for (strategy, _), candidates in buckets.items():
        native_jobs = [job for job in candidates if job["schedule"] == "native"]
        if len(native_jobs) != 1:
            continue
        native = native_jobs[0]
        native_records = {row["symbol"]: row for row in records if row["job_id"] == native["job_id"]}
        for low in [job for job in candidates if job["schedule"] != "native"]:
            low_records = {row["symbol"]: row for row in records if row["job_id"] == low["job_id"]}
            left, right = _job_raw_rows(native), _job_raw_rows(low)
            for symbol in sorted(set(left.get("Symbol", [])) | set(right.get("Symbol", []))):
                a, b = left[left["Symbol"] == symbol], right[right["Symbol"] == symbol]
                ap = sorted(column for column in a if str(column).startswith("Opt "))
                bp = sorted(column for column in b if str(column).startswith("Opt "))
                shared = sorted(set(ap) & set(bp))
                ag = {tuple(row) for row in a[shared].itertuples(index=False, name=None)} if shared else ({()} if len(a) == 1 else set())
                bg = {tuple(row) for row in b[shared].itertuples(index=False, name=None)} if shared else ({()} if len(b) == 1 else set())
                common = ag & bg
                base = {"strategy": strategy, "symbol": symbol, "native_job": native["job_id"],
                        "low_touch_job": low["job_id"], "shared_grid_rows": len(common),
                        "grid_coverage": len(common) / max(len(ag), len(bg), 1)}
                dates_match = native["research_window"] == low["research_window"] and bool(native["research_window"].get("start"))
                same_provenance = native["source_hash"] == low["source_hash"] and sha256(native["analysis_profile"]) == sha256(low["analysis_profile"])
                reason = None
                if not dates_match:
                    reason = "Declared research dates do not match"
                elif a.empty or b.empty:
                    reason = "Symbol is missing from one schedule"
                elif ap != bp or not ag or ag != bg:
                    reason = "Parameter grids do not have full shared coverage"
                elif not same_provenance:
                    reason = "Schedules do not share source and policy provenance"
                elif not {"CAR/MDD", "Profit Factor", "Max. Sys % Drawdown"}.issubset(a) \
                        or not {"CAR/MDD", "Profit Factor", "Max. Sys % Drawdown"}.issubset(b):
                    reason = "Required optimization metrics are missing"
                if reason:
                    output.append({**base, "native_median_car_mdd": math.nan,
                                   "low_touch_median_car_mdd": math.nan,
                                   "relative_car_mdd_improvement": math.nan,
                                   "profit_factor_improvement": math.nan, "drawdown_reduction": math.nan,
                                   "recommendation": "NOT COMPARABLE",
                                   "reason": reason})
                    continue
                native_car, low_car = _median(a["CAR/MDD"]), _median(b["CAR/MDD"])
                native_pf, low_pf = _median(a["Profit Factor"]), _median(b["Profit Factor"])
                native_dd = abs(_median(a["Max. Sys % Drawdown"]))
                low_dd = abs(_median(b["Max. Sys % Drawdown"]))
                values = (native_car, low_car, native_pf, low_pf, native_dd, low_dd)
                if not all(math.isfinite(value) for value in values) or low_car <= 0 or low_dd <= 0:
                    output.append({**base, "native_median_car_mdd": native_car,
                                   "low_touch_median_car_mdd": low_car,
                                   "relative_car_mdd_improvement": math.nan,
                                   "profit_factor_improvement": math.nan, "drawdown_reduction": math.nan,
                                   "recommendation": "NOT COMPARABLE",
                                   "reason": "Low-touch baseline is nonpositive or nonfinite"})
                    continue
                car = (native_car - low_car) / low_car
                pf = native_pf - low_pf
                dd = (low_dd - native_dd) / low_dd
                native_gate = native_records.get(symbol, {}).get("individual_pass") and native_records.get(symbol, {}).get("sector_pass")
                low_gate = low_records.get(symbol, {}).get("individual_pass") and low_records.get(symbol, {}).get("sector_pass")
                material = native_gate and car >= .25 and (pf >= .10 or dd >= .15)
                recommendation = "FREQUENT_ENTRY_WFA_CANDIDATE" if material else "LOW_TOUCH" if low_gate else "NO_WFA_CANDIDATE"
                reason = {"FREQUENT_ENTRY_WFA_CANDIDATE": "Native schedule clears both material-improvement layers",
                          "LOW_TOUCH": "Low-touch schedule passes its gates and remains preferred",
                          "NO_WFA_CANDIDATE": "Neither schedule qualifies under the paired schedule policy"}[recommendation]
                output.append({**base, "native_median_car_mdd": native_car,
                               "low_touch_median_car_mdd": low_car,
                               "relative_car_mdd_improvement": car, "profit_factor_improvement": pf,
                               "drawdown_reduction": dd, "recommendation": recommendation, "reason": reason})
    return output


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
        path = resolve_portable_path(artifact.get("path", ""), Path(job["run_path"]))
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
        if job["schedule"] == "fixed_110000" and len(times) and not times.eq(111500).all():
            failures.append(f"{job['job_id']}/{export['symbol']}: 11:00 signal did not enter on 11:15 bar")
    checks.append({"check": "entry_time_distribution", "job_id": job["job_id"], "value": distribution})


def _validate_reports(summary: dict, job_ids: set[str], failures: list[str]):
    reports = {str(report.get("job_id")): report for report in summary.get("detailed_reports", [])}
    if set(reports) != job_ids:
        failures.append("detailed report job set differs from admitted pilot jobs")
    for report in summary.get("detailed_reports", []):
        if report.get("status") != "COMPLETE":
            failures.append(f"{report.get('job_id')}: detailed analysis did not complete")
        for key, value in report.items():
            if not key.endswith("_path") or not value:
                continue
            digest_key = key[:-5] + "_sha256"
            if digest_key not in report:
                continue
            path = resolve_portable_path(value)
            if not path.is_file() or sha256(path) != report[digest_key]:
                failures.append(f"{report.get('job_id')}: detailed report changed: {key}")


def _validate_workbook(summary_path: Path, summary: dict, failures: list[str]):
    workbook = summary_path.parent / f"{summary['experiment_id']}_Optimization_Review.xlsx"
    if not workbook.is_file():
        failures.append("analysis workbook is missing")
        return
    try:
        book = openpyxl.load_workbook(workbook, data_only=False, read_only=True)
    except Exception as exc:
        failures.append(f"analysis workbook cannot be read: {exc}")
        return
    if book.sheetnames != SHEET_NAMES:
        failures.append("analysis workbook sheet set differs")
        return
    expected = {
        "Passed Candidates": (SYMBOL_COLUMNS, summary.get("passed_candidates", []), METRIC_KEYS),
        "Low-Touch Recommendations": (COMPARISON_COLUMNS, summary.get("low_touch_recommendations", []), COMPARISON_KEYS),
        "Frequent-Entry Exceptions": (COMPARISON_COLUMNS, summary.get("frequent_entry_exceptions", []), COMPARISON_KEYS),
        "All Symbol Results": (SYMBOL_COLUMNS, summary.get("all_symbol_results", []), METRIC_KEYS),
        "Failed or Incomplete": (FAILURE_COLUMNS, summary.get("failed_or_incomplete", []), FAILURE_COLUMNS),
    }
    for title, (columns, rows, compare_keys) in expected.items():
        values = list(book[title].values)
        headings = list(values[0]) if values else []
        if headings != columns:
            failures.append(f"analysis workbook schema differs: {title}")
            continue
        workbook_rows = [dict(zip(headings, row)) for row in values[1:]]
        keys = [key for key in compare_keys if key in columns]
        _compare_rows([{key: row.get(key) for key in keys} for row in rows],
                      [{key: row.get(key) for key in keys} for row in workbook_rows],
                      keys, failures, f"workbook {title}")


def _validate_reference(reference_path: Path, jobs: list[dict], failures: list[str]):
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8-sig"))
    selected = [job for job in jobs if job["job_id"] == reference.get("automated_job_id")]
    if len(selected) != 1:
        failures.append("manual reference job is not an admitted pilot job")
        return
    job = selected[0]
    for name in ("apx", "formula"):
        path = resolve_portable_path(reference.get(f"{name}_path", ""), reference_path.parent)
        if not path.is_file() or sha256(path) != reference.get(f"{name}_sha256"):
            failures.append(f"manual reference {name} hash mismatch")
    auto_root = Path(job["run_path"])
    try:
        auto_xml = ET.parse(auto_root / "project.apx").getroot()
        ref_xml = ET.parse(resolve_portable_path(reference["apx_path"], reference_path.parent)).getroot()
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
    reference_formula = resolve_portable_path(reference.get("formula_path", ""), reference_path.parent)
    if reference_formula.is_file() and (auto_root / "formula.afl").is_file():
        if sha256(auto_root / "formula.afl") != sha256(reference_formula):
            failures.append("manual reference formula differs")
    expected = {entry["symbol"]: entry for entry in job["run_manifest"].get("exports", [])}
    supplied = reference.get("exports", {})
    if set(supplied) != set(expected):
        failures.append("manual reference symbols differ")
        return
    for symbol, reference_entry in supplied.items():
        ref_path = resolve_portable_path(reference_entry.get("path", ""), reference_path.parent)
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


def _validate_pilot_manifest(experiment: dict, failures: list[str]) -> None:
    if experiment.get("mode") != "pilot":
        failures.append("audit input is not a pilot experiment")
    if experiment.get("status") != "COMPLETE":
        failures.append("pilot experiment status is not COMPLETE")
    declared = list(experiment.get("_matrix", {}).get("modes", {}).get("pilot", []))
    observed = [str(job.get("job_id")) for job in experiment.get("jobs", [])]
    if len(observed) != len(set(observed)) or set(observed) != set(declared):
        failures.append("pilot job set differs from the matrix declaration")
    if any(job.get("status") != "COMPLETE" for job in experiment.get("jobs", [])):
        failures.append("not every declared pilot job is COMPLETE")
    matrix_jobs = experiment.get("_matrix", {}).get("jobs", {})
    intraday = any(matrix_jobs.get(job_id, {}).get("periodicity") != "Daily" for job_id in declared)
    if not intraday:
        return
    preflight = experiment.get("preflight") or {}
    if preflight.get("status") != "PASS":
        failures.append("intraday pilot has no passing timezone preflight")
        return
    report_path = resolve_portable_path(preflight.get("report_path"), experiment["_root"])
    if not report_path.is_file() or sha256(report_path) != preflight.get("report_sha256"):
        failures.append("timezone preflight report is missing or changed")
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        failures.append("timezone preflight report is invalid")
        return
    required = (
        report.get("status") == "PASS"
        and report.get("timezone") == "America/Detroit"
        and report.get("timeshift_seconds") == 0
        and report.get("interval_seconds") == 300
        and bool(report.get("winter_sessions"))
        and bool(report.get("summer_sessions"))
    )
    if not required:
        failures.append("timezone preflight evidence does not satisfy the session checks")


def _authorization_hashes(experiment: dict, failures: list[str]) -> dict:
    paths = {
        "job_builder": HERE / "Build-AmiBroker-ExperimentJob.ps1",
        "runner": HERE / "Run-AmiBroker-Experiment.ps1",
        "archiver": HERE / "Archive_AmiBroker_Run.ps1",
        "timezone_preflight": HERE / "Test-AmiBroker-Timezone.ps1",
        "analyzer": HERE / "analyze_amibroker_experiment.py",
        "audit": Path(__file__).resolve(),
        "result_rules": HERE / "amibroker_experiment_results.py",
        "schedule_rules": HERE / "amibroker_schedule_comparison.py",
        "peer_policy": HERE / "peer_robustness_policy.json",
    }
    for profile in (HERE / "Analyzer_Profiles").glob("*_Analyzer_Profile.json"):
        paths[f"profile_{profile.stem}"] = profile
    for name, value in experiment.get("_matrix", {}).get("project_templates", {}).items():
        paths[f"project_template_{name}"] = resolve_portable_path(value, experiment["_matrix_path"].parent)
    shared = experiment.get("_matrix", {}).get("shared_policy_windows")
    if shared:
        paths["shared_policy"] = resolve_portable_path(shared, experiment["_matrix_path"].parent)
    output = {}
    for name, path in paths.items():
        if not Path(path).is_file():
            failures.append(f"authorization evidence is missing: {name}")
            continue
        output[name] = {"path": str(Path(path).resolve()), "path_windows": _windows_path(path),
                        "sha256": sha256(path)}
    return output


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
        _validate_pilot_manifest(experiment, failures)
        jobs, rejected = admit_jobs(experiment)
        if rejected:
            failures.extend(f"{row['job_id']}: {row['reason']}" for row in rejected)
        if summary.get("experiment_id") != experiment.get("experiment_id"):
            failures.append("analysis experiment ID differs")
        if summary.get("matrix_sha256") != experiment.get("matrix_sha256"):
            failures.append("analysis matrix hash differs")
        if summary.get("research_window") != experiment.get("_matrix", {}).get("research_window"):
            failures.append("analysis research window differs")
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
            recomputed.extend(_independent_symbol_results(job, profile, groups))
        recomputed = _independent_sector_gate(recomputed)
        _compare_rows(recomputed, summary.get("all_symbol_results", []), METRIC_KEYS, failures, "symbol results")
        comparisons = _independent_comparisons(jobs, recomputed)
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
        _validate_reports(summary, {job["job_id"] for job in jobs}, failures)
        _validate_workbook(analysis_path, summary, failures)
        for name, artifact in experiment.get("policy_hashes", {}).items():
            path = resolve_portable_path(artifact.get("path", ""), experiment["_root"])
            if not path.is_file() or sha256(path) != artifact.get("sha256"):
                failures.append(f"experiment policy changed: {name}")
        if reference_path is not None:
            _validate_reference(Path(reference_path), jobs, failures)
        checks.append({"check": "matrix_sha256", "status": "PASS",
                       "value": sha256(experiment["_matrix_path"])})
        authorization_hashes = _authorization_hashes(experiment, failures)
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
            "authorization_hashes": authorization_hashes,
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
