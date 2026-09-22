#!/usr/bin/env python3
"""Analyze a sealed AmiBroker experiment and create one review workbook."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import pandas as pd

from amibroker_experiment_reports import analyze_job
from amibroker_experiment_results import admit_jobs, evaluate_sectors, load_experiment, summarize_job
from amibroker_experiment_workbook import write_workbook
from amibroker_schedule_comparison import compare_optimization_pair


HERE = Path(__file__).resolve().parent


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _raw_rows(job: dict) -> list[dict]:
    rows = []
    for export in job["run_manifest"].get("exports", []):
        relative = str(export["file"]).replace("\\", "/")
        frame = pd.read_csv(Path(job["run_path"]).joinpath(*relative.split("/")))
        frame["Symbol"] = str(export["symbol"])
        rows.extend(frame.to_dict("records"))
    return rows


def _comparisons(jobs: list[dict], records: list[dict]) -> list[dict]:
    buckets = {}
    for job in jobs:
        buckets.setdefault((job["strategy"], job["periodicity"]), []).append(job)
    result = []
    for (strategy, _periodicity), candidates in buckets.items():
        natives = [job for job in candidates if job["schedule"] == "native"]
        alternatives = [job for job in candidates if job["schedule"] != "native"]
        if len(natives) != 1:
            continue
        native = natives[0]
        native_records = [row for row in records if row["job_id"] == native["job_id"]]
        individual = {row["symbol"]: row["individual_pass"] for row in native_records}
        sector = {row["symbol"]: row["sector_pass"] for row in native_records}
        for low_touch in alternatives:
            native_window = native.get("research_window", {})
            low_window = low_touch.get("research_window", {})
            result.extend(compare_optimization_pair(
                _raw_rows(native), _raw_rows(low_touch), {
                    "strategy": strategy,
                    "native_job": native["job_id"],
                    "low_touch_job": low_touch["job_id"],
                    "native_research_start": native_window.get("start"),
                    "native_research_end": native_window.get("end"),
                    "low_touch_research_start": low_window.get("start"),
                    "low_touch_research_end": low_window.get("end"),
                    "native_individual_pass": individual,
                    "native_sector_pass": sector,
                }
            ))
    return result


def build_summary(manifest_path: Path) -> dict:
    experiment = load_experiment(manifest_path)
    jobs, rejected = admit_jobs(experiment)
    groups = _load_json(HERE / "peer_robustness_policy.json")["economic_groups"]
    records = []
    reports = {}
    analysis_failures = []
    for job in jobs:
        profile = _load_json(job["analysis_profile"])
        records.extend(summarize_job(job, profile, groups))
        report = analyze_job(job, Path(job["run_path"]) / "Analysis_Reports")
        reports[job["job_id"]] = report
        if report["status"] != "COMPLETE":
            analysis_failures.append({"job_id": job["job_id"], "reason": report["error"]})
    records = evaluate_sectors(records)
    for row in records:
        report = reports.get(row["job_id"], {})
        row["html_path"] = report.get("html_path")
        row["json_path"] = report.get("json_path")
    comparisons = _comparisons(jobs, records)
    passed = [row for row in records if row["selected_representative"]]
    return {
        "schema_version": 1,
        "experiment_id": experiment["experiment_id"],
        "experiment_manifest": str(Path(manifest_path).resolve()),
        "job_counts": {
            "total": len(experiment.get("jobs", [])),
            "admitted": len(jobs),
            "invalid": len(rejected),
            "analysis_failed": len(analysis_failures),
        },
        "passed_candidates": passed,
        "low_touch_recommendations": [row for row in comparisons if row["recommendation"] == "LOW_TOUCH"],
        "frequent_entry_exceptions": [row for row in comparisons if row["recommendation"] == "FREQUENT_ENTRY_WFA_CANDIDATE"],
        "all_symbol_results": records,
        "failed_or_incomplete": rejected + analysis_failures,
        "schedule_comparisons": comparisons,
        "detailed_reports": list(reports.values()),
    }


def write_outputs(summary: dict, output_dir: Path) -> Path:
    output_dir = Path(output_dir).resolve()
    temporary = output_dir.with_name(output_dir.name + ".tmp")
    backup = output_dir.with_name(output_dir.name + ".previous")
    for path in (temporary, backup):
        if path.exists():
            shutil.rmtree(path)
    temporary.mkdir(parents=True)
    safe = _json_value(summary)
    try:
        (temporary / "experiment_summary.json").write_text(
            json.dumps(safe, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        pd.DataFrame(safe["all_symbol_results"]).to_csv(
            temporary / "all_symbol_results.csv", index=False
        )
        pd.DataFrame(safe["schedule_comparisons"]).to_csv(
            temporary / "schedule_comparisons.csv", index=False
        )
        workbook_name = f"{summary['experiment_id']}_Optimization_Review.xlsx"
        write_workbook(safe, temporary / workbook_name)
        if output_dir.exists():
            output_dir.rename(backup)
        temporary.rename(output_dir)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception as exc:
        (temporary / "output_error.txt").write_text(str(exc), encoding="utf-8")
        if backup.exists() and not output_dir.exists():
            backup.rename(output_dir)
        raise
    return output_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir or args.experiment_manifest.resolve().parent / "Analysis"
    destination = write_outputs(build_summary(args.experiment_manifest), output)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
