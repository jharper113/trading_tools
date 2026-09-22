import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from amibroker_experiment_workbook import write_workbook
from amibroker_experiment_results import evaluate_sectors, summarize_job
from analyze_amibroker_experiment import _comparisons
from audit_amibroker_experiment import run_audit, sha256


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def audit_fixture(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "initial_equity": 100000, "commission_mode": 3,
        "commission_per_contract_side": 3.76, "futures_mode": True,
        "min_shares": 1, "allow_same_bar_exit": True,
        "reverse_signal_forces_exit": False, "use_previous_bar_equity": True,
        "fitness": "CAR/MDD", "periodicity": {"apx_code": 8, "seconds": 900},
        "thresholds": {"core": {"profitable_paramsets_pct": 70,
                                  "median_profit_factor": 1.1, "median_trades": 60}},
    }))
    groups = {"Equity indexes": ["ES", "NQ", "YM"]}
    jobs = {}
    states = []
    for job_id, adapter in (("native", "native"), ("low", "entry_cutoff_110000")):
        run = tmp_path / job_id
        run.mkdir()
        (run / "entry_audit").mkdir()
        formula = run / "formula.afl"
        formula.write_text("Buy = 1; Short = 0;")
        project = run / "project.apx"
        project.write_text(
            "<AnalysisDoc><FormulaPath>formula.afl</FormulaPath>"
            f"<FormulaContent>{formula.read_text()}</FormulaContent>"
            "<Periodicity>8</Periodicity><ChartInterval>900</ChartInterval>"
            "<FromDate>2009-01-01</FromDate><ToDate>2019-01-01</ToDate>"
            "<InitialEquity>100000</InitialEquity><CommissionMode>3</CommissionMode>"
            "<CommissionAmount>3.76</CommissionAmount><PointsOnlyTest>1</PointsOnlyTest>"
            "<MinShares>1</MinShares><AllowSameBarExit>1</AllowSameBarExit>"
            "<ReverseSignalForcesExit>0</ReverseSignalForcesExit>"
            "<UsePrevBarEquity>1</UsePrevBarEquity><OptTarget>CAR/MDD</OptTarget></AnalysisDoc>"
        )
        exports, audits = [], []
        for symbol in groups["Equity indexes"]:
            result = run / f"{symbol}.csv"
            car = .75 if adapter == "native" else .60
            pf = 1.35 if adapter == "native" else 1.20
            dd = -18 if adapter == "native" else -20
            pd.DataFrame([
                {"Net Profit": 100, "Profit Factor": pf, "# Trades": 100,
                 "CAR/MDD": car, "Max. Sys % Drawdown": dd, "Opt X": 1},
                {"Net Profit": 90, "Profit Factor": pf, "# Trades": 90,
                 "CAR/MDD": car, "Max. Sys % Drawdown": dd, "Opt X": 2},
            ]).to_csv(result, index=False)
            audit = run / "entry_audit" / f"{symbol}.csv"
            pd.DataFrame([{"Audit DateTime": "2018-01-02 10:45:00", "TimeNum": 104500,
                           "Buy": 1, "Short": 0}]).to_csv(audit, index=False)
            exports.append({"symbol": symbol, "file": result.name, "sha256": _hash(result)})
            audits.append({"symbol": symbol, "file": f"entry_audit/{symbol}.csv", "sha256": _hash(audit)})
        run_manifest = run / "run_manifest.json"
        run_manifest.write_text(json.dumps({
            "status": "COMPLETE", "expected_symbols": list(groups["Equity indexes"]),
            "exports": exports, "expected_audit_symbols": list(groups["Equity indexes"]),
            "audit_exports": audits,
            "experiment": {"source_sha256": "a" * 64, "adapter": adapter},
            "experiment_artifacts": [],
        }))
        jobs[job_id] = {
            "job_id": job_id, "strategy_id": "Demo", "periodicity": "15m",
            "interval_seconds": 900, "adapter": {"name": adapter},
            "analysis_profile": str(profile), "symbols": list(groups["Equity indexes"]),
            "source_sha256": "a" * 64, "source_afl": "Demo.afl", "enabled": True,
            "database": "db", "project_template": "template.apx",
        }
        states.append({"job_id": job_id, "status": "COMPLETE",
                       "run_manifest_path": str(run_manifest),
                       "run_manifest_sha256": _hash(run_manifest)})
    matrix = {
        "schema_version": 1, "matrix_id": "fixture",
        "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
        "timezone": "America/Detroit", "jobs": jobs,
        "modes": {"pilot": ["native", "low"], "full": ["native", "low"]},
    }
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix))
    experiment = tmp_path / "experiment_manifest.json"
    experiment.write_text(json.dumps({
        "schema_version": 1, "experiment_id": "pilot-1", "mode": "pilot",
        "matrix_path": str(matrix_path), "matrix_sha256": _hash(matrix_path), "jobs": states,
    }))
    records = []
    comparison_jobs = []
    for state in states:
        job = jobs[state["job_id"]]
        run_manifest = json.loads(Path(state["run_manifest_path"]).read_text())
        normalized_job = {
            "experiment_id": "pilot-1", "job_id": state["job_id"], "strategy": "Demo",
            "periodicity": "15m", "schedule": job["adapter"]["name"],
            "run_path": Path(state["run_manifest_path"]).parent,
            "run_manifest": run_manifest, "source_hash": "a" * 64,
            "research_window": matrix["research_window"],
        }
        comparison_jobs.append(normalized_job)
        records.extend(summarize_job(normalized_job, json.loads(profile.read_text()), groups))
    records = evaluate_sectors(records)
    comparisons = _comparisons(comparison_jobs, records)
    summary = {
        "schema_version": 1, "experiment_id": "pilot-1",
        "job_counts": {"total": 2, "admitted": 2, "invalid": 0, "analysis_failed": 0},
        "passed_candidates": [row for row in records if row["selected_representative"]],
        "low_touch_recommendations": [row for row in comparisons if row["recommendation"] == "LOW_TOUCH"],
        "frequent_entry_exceptions": [row for row in comparisons if row["recommendation"] == "FREQUENT_ENTRY_WFA_CANDIDATE"],
        "all_symbol_results": records, "failed_or_incomplete": [],
        "schedule_comparisons": comparisons, "detailed_reports": [],
    }
    analysis_dir = tmp_path / "Analysis"
    analysis_dir.mkdir()
    analysis = analysis_dir / "experiment_summary.json"
    analysis.write_text(json.dumps(summary, allow_nan=True))
    write_workbook(summary, analysis_dir / "pilot-1_Optimization_Review.xlsx")
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    automated = tmp_path / "native"
    reference_exports = {}
    for symbol in groups["Equity indexes"]:
        target = reference_dir / f"{symbol}.csv"
        target.write_bytes((automated / f"{symbol}.csv").read_bytes())
        reference_exports[symbol] = {"path": str(target), "sha256": _hash(target)}
    reference_project = reference_dir / "project.apx"
    reference_project.write_bytes((automated / "project.apx").read_bytes())
    reference_formula = reference_dir / "formula.afl"
    reference_formula.write_bytes((automated / "formula.afl").read_bytes())
    reference = reference_dir / "manual_reference.json"
    reference.write_text(json.dumps({
        "automated_job_id": "native",
        "apx_path": str(reference_project), "apx_sha256": _hash(reference_project),
        "formula_path": str(reference_formula), "formula_sha256": _hash(reference_formula),
        "exports": reference_exports,
    }))
    return type("AuditFixture", (), {
        "experiment": experiment, "analysis": analysis, "reference": reference,
        "root": tmp_path,
    })


def test_internal_success_without_reference_requires_manual_check(audit_fixture):
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis)
    report = json.loads(result.json_path.read_text())
    assert report["status"] == "MANUAL CHECK REQUIRED"
    assert not result.unlock_path.exists()


def test_matching_reference_produces_hash_bound_unlock(audit_fixture):
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    report = json.loads(result.json_path.read_text())
    unlock = json.loads(result.unlock_path.read_text())
    assert report["status"] == "PASS"
    assert unlock["pilot_experiment_id"] == report["experiment_id"]
    assert unlock["audit_sha256"] == sha256(result.json_path)
    assert Path(unlock["full_matrix_path"]).is_file()
    assert unlock["full_matrix_sha256"] == sha256(Path(unlock["full_matrix_path"]))


def test_late_low_touch_entry_blocks_unlock(audit_fixture):
    path = audit_fixture.root / "low" / "entry_audit" / "ES.csv"
    frame = pd.read_csv(path)
    frame["TimeNum"] = 111500
    frame.to_csv(path, index=False)
    # Preserve archive admission so the semantic audit, rather than the hash gate, finds the fault.
    manifest_path = audit_fixture.root / "low" / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(item for item in manifest["audit_exports"] if item["symbol"] == "ES")["sha256"] = _hash(path)
    manifest_path.write_text(json.dumps(manifest))
    experiment = json.loads(audit_fixture.experiment.read_text())
    next(item for item in experiment["jobs"] if item["job_id"] == "low")["run_manifest_sha256"] = _hash(manifest_path)
    audit_fixture.experiment.write_text(json.dumps(experiment))
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    assert json.loads(result.json_path.read_text())["status"] == "FAIL"
    assert not result.unlock_path.exists()


def test_metric_or_reference_difference_blocks_unlock(audit_fixture):
    summary = json.loads(audit_fixture.analysis.read_text())
    summary["all_symbol_results"][0]["median_profit_factor"] = 999
    audit_fixture.analysis.write_text(json.dumps(summary))
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    assert json.loads(result.json_path.read_text())["status"] == "FAIL"
    assert not result.unlock_path.exists()


def test_fixed_signal_wrong_entry_bar_blocks_unlock(audit_fixture):
    matrix_path = audit_fixture.root / "matrix.json"
    matrix = json.loads(matrix_path.read_text())
    matrix["jobs"]["low"]["adapter"]["name"] = "fixed_110000"
    matrix_path.write_text(json.dumps(matrix))
    run_manifest_path = audit_fixture.root / "low" / "run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text())
    run_manifest["experiment"]["adapter"] = "fixed_110000"
    run_manifest_path.write_text(json.dumps(run_manifest))
    experiment = json.loads(audit_fixture.experiment.read_text())
    experiment["matrix_sha256"] = _hash(matrix_path)
    next(item for item in experiment["jobs"] if item["job_id"] == "low")["run_manifest_sha256"] = _hash(run_manifest_path)
    audit_fixture.experiment.write_text(json.dumps(experiment))
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    report = json.loads(result.json_path.read_text())
    assert report["status"] == "FAIL"
    assert any("11:15 bar" in reason for reason in report["failures"])


def test_reference_metric_difference_blocks_unlock(audit_fixture):
    reference = json.loads(audit_fixture.reference.read_text())
    path = Path(reference["exports"]["ES"]["path"])
    frame = pd.read_csv(path)
    frame.loc[0, "Profit Factor"] = 9.9
    frame.to_csv(path, index=False)
    reference["exports"]["ES"]["sha256"] = _hash(path)
    audit_fixture.reference.write_text(json.dumps(reference))
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    report = json.loads(result.json_path.read_text())
    assert report["status"] == "FAIL"
    assert any("manual reference metric differs" in reason for reason in report["failures"])
