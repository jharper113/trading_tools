import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from amibroker_experiment_results import (
    _verify_experiment_provenance,
    admit_jobs,
    evaluate_sectors,
    load_experiment,
    parse_number,
    summarize_job,
)
from amibroker_paths import resolve_portable_path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _optimization(path, net=(100, 100, 100), pf=1.2, trades=100, car_mdd=.4, drawdown=-10):
    pd.DataFrame(
        {
            "Net Profit": list(net),
            "Profit Factor": [pf] * len(net),
            "# Trades": [trades] * len(net),
            "CAR/MDD": [car_mdd] * len(net),
            "Max. Sys % Drawdown": [drawdown] * len(net),
            "Opt X": list(range(1, len(net) + 1)),
        }
    ).to_csv(path, index=False)


@pytest.fixture
def experiment_fixture(tmp_path):
    matrix = {
        "schema_version": 1,
        "matrix_id": "fixture",
        "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
        "timezone": "America/Detroit",
        "modes": {"pilot": ["complete-valid", "failed", "tampered"]},
        "jobs": {},
    }
    states = []
    for job_id, state in (("complete-valid", "COMPLETE"), ("failed", "FAILED"), ("tampered", "COMPLETE")):
        run = tmp_path / job_id
        run.mkdir()
        csv_path = run / "ES.csv"
        _optimization(csv_path)
        audit_dir = run / "entry_audit"
        audit_dir.mkdir()
        audit = audit_dir / "ES.csv"
        audit.write_text("DateTime,TimeNum,Buy,Short\n2018-01-02 11:00:00,110000,1,0\n")
        project = run / "project.apx"
        project.write_text("<AmiBroker-Analysis/>")
        run_manifest = run / "run_manifest.json"
        run_manifest.write_text(
            json.dumps(
                {
                    "status": "COMPLETE",
                    "expected_symbols": ["ES"],
                    "exports": [{"symbol": "ES", "file": "ES.csv", "sha256": _sha(csv_path)}],
                    "expected_audit_symbols": ["ES"],
                    "audit_exports": [{"symbol": "ES", "file": "entry_audit/ES.csv", "sha256": _sha(audit)}],
                    "experiment": {"source_sha256": "a" * 64},
                }
            )
        )
        matrix["jobs"][job_id] = {
            "job_id": job_id,
            "strategy_id": "Demo",
            "periodicity": "Daily",
            "adapter": {"name": "native"},
            "analysis_profile": "Analyzer_Profiles/Daily_Analyzer_Profile.json",
            "symbols": ["ES"],
            "source_sha256": "a" * 64,
        }
        states.append(
            {
                "job_id": job_id,
                "strategy": "Demo",
                "periodicity": "Daily",
                "adapter": "native",
                "status": state,
                "run_manifest_path": str(run_manifest),
                "run_manifest_sha256": _sha(run_manifest),
            }
        )
        if job_id == "tampered":
            csv_path.write_text(csv_path.read_text() + "\n")
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix))
    manifest = tmp_path / "experiment_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": "experiment-1",
                "mode": "pilot",
                "status": "FAILED",
                "matrix_path": str(matrix_path),
                "matrix_sha256": _sha(matrix_path),
                "jobs": states,
            }
        )
    )
    return type("Fixture", (), {"manifest": manifest})


def test_admission_separates_complete_valid_and_failed_jobs(experiment_fixture):
    experiment = load_experiment(experiment_fixture.manifest)
    admitted, rejected = admit_jobs(experiment)
    assert [job["job_id"] for job in admitted] == ["complete-valid"]
    assert {row["job_id"]: row["reason"] for row in rejected} == {
        "failed": "experiment job status is FAILED",
        "tampered": "run archive hash mismatch: ES.csv",
    }


def _job_with_symbols(tmp_path, definitions, periodicity="Daily"):
    run = tmp_path / "run"
    run.mkdir()
    exports = []
    for symbol, values in definitions.items():
        path = run / f"{symbol}.csv"
        _optimization(path, **values)
        exports.append({"symbol": symbol, "file": path.name, "sha256": _sha(path)})
    manifest = run / "run_manifest.json"
    manifest.write_text(json.dumps({"exports": exports}))
    return {
        "experiment_id": "experiment-1",
        "job_id": "job-1",
        "strategy": "Demo",
        "periodicity": periodicity,
        "schedule": "native",
        "run_path": run,
        "run_manifest": json.loads(manifest.read_text()),
        "source_hash": "a" * 64,
    }


def test_zero_trade_symbol_counts_as_tested_sector_failure(tmp_path):
    job = _job_with_symbols(
        tmp_path,
        {
            "ZN": {},
            "ZF": {},
            "ZB": {"net": (-1, -1, -1), "pf": .9},
            "ZT": {"net": (-1, -1, -1), "pf": .9},
            "UB": {"net": (0, 0, 0), "pf": 0, "trades": 0},
        },
    )
    profile = {"thresholds": {"core": {"profitable_paramsets_pct": 70, "median_profit_factor": 1.1, "median_trades": 60}}}
    records = summarize_job(job, profile, {"Rates": ["ZT", "ZF", "ZN", "ZB", "UB"]})
    evaluated = evaluate_sectors(records)
    rates = next(row for row in evaluated if row["sector"] == "Rates")
    assert rates["tested_count"] == 5
    assert rates["passing_count"] == 2
    assert rates["sector_pass"] is False
    assert next(row for row in evaluated if row["symbol"] == "UB")["individual_pass"] is False


def test_exact_thresholds_pass(tmp_path):
    job = _job_with_symbols(
        tmp_path,
        {"ES": {"net": (1, 1, 1, 1, 1, 1, 1, -1, -1, -1), "pf": 1.10, "trades": 60}},
    )
    profile = {"thresholds": {"core": {"profitable_paramsets_pct": 70, "median_profit_factor": 1.1, "median_trades": 60}}}
    result = summarize_job(job, profile, {"Equity indexes": ["ES"]})[0]
    assert result["individual_pass"] is True


def test_representative_uses_median_of_entire_common_grid(tmp_path):
    job = _job_with_symbols(
        tmp_path,
        {
            "ZN": {"car_mdd": .3},
            "ZF": {"car_mdd": .3},
            "ZB": {"car_mdd": .6},
            "ZT": {"net": (-1, -1, -1), "pf": .9},
            "UB": {"net": (-1, -1, -1), "pf": .9},
        },
    )
    profile = {"thresholds": {"core": {"profitable_paramsets_pct": 70, "median_profit_factor": 1.1, "median_trades": 60}}}
    records = summarize_job(job, profile, {"Rates": ["ZT", "ZF", "ZN", "ZB", "UB"]})
    selected = [row["symbol"] for row in evaluate_sectors(records) if row["selected_representative"]]
    assert selected == ["ZB"]


@pytest.mark.parametrize("bad", ["", "N/A", "nan", "inf", "-inf"])
def test_nonfinite_car_mdd_cannot_win(tmp_path, bad):
    job = _job_with_symbols(
        tmp_path,
        {
            "ZN": {"car_mdd": bad},
            "ZF": {"car_mdd": .3},
            "ZB": {"car_mdd": .6},
        },
    )
    profile = {"thresholds": {"core": {"profitable_paramsets_pct": 70, "median_profit_factor": 1.1, "median_trades": 60}}}
    records = summarize_job(job, profile, {"Rates": ["ZN", "ZF", "ZB"]})
    invalid = next(row for row in evaluate_sectors(records) if row["symbol"] == "ZN")
    assert invalid["selected_representative"] is False


@pytest.mark.parametrize("text,expected", [("1,234.5", 1234.5), ("12.5%", 12.5)])
def test_parse_number_accepts_amibroker_formatting(text, expected):
    assert parse_number(text) == expected


def test_windows_z_drive_paths_resolve_on_linux(monkeypatch):
    monkeypatch.setenv("AMIBROKER_WINDOWS_ROOT", "/mnt/harp")
    assert resolve_portable_path(r"Z:\04_Code\run.json") == Path("/mnt/harp/04_Code/run.json")


def test_current_archive_provenance_rejects_modified_generated_formula(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    formula = run / "formula.afl"
    formula.write_text("Buy = 1;")
    project = run / "project.apx"
    project.write_text("<AnalysisDoc><FormulaContent>Buy = 1;</FormulaContent></AnalysisDoc>")
    matrix = tmp_path / "matrix.json"
    source = tmp_path / "source.afl"
    profile = tmp_path / "profile.json"
    batch = run / "batch.abb"
    config = run / "batch.archive.json"
    for path, text in ((matrix, "{}"), (source, "Buy = 1;"), (profile, "{}"),
                       (batch, "<batch/>"), (config, "{}")):
        path.write_text(text)
    build = tmp_path / "build_manifest.json"
    build.write_text(json.dumps({
        "job_id": "job", "attempt_id": "attempt-1", "outputs": {
            "formula": {"path": str(formula), "sha256": _sha(formula)},
            "project": {"path": str(project), "sha256": _sha(project)},
            "batch": {"path": str(batch), "sha256": _sha(batch)},
            "archive_config": {"path": str(config), "sha256": _sha(config)},
        },
    }))
    artifacts = []
    for name, path in (("matrix_path", matrix), ("source_afl", source),
                       ("analysis_profile", profile), ("build_manifest", build)):
        artifacts.append({"name": name, "path": str(path), "sha256": _sha(path)})
    run_manifest = {
        "schema_version": 2, "project_sha256": _sha(project),
        "experiment": {"job_id": "job", "attempt_id": "attempt-1", "source_sha256": _sha(source),
                       "matrix_sha256": _sha(matrix)},
        "experiment_artifacts": artifacts,
    }
    job = {"job_id": "job", "source_sha256": _sha(source)}
    experiment = {"matrix_sha256": _sha(matrix)}
    _verify_experiment_provenance(run, run_manifest, job, experiment)
    formula.write_text("Buy = 0;")
    with pytest.raises(ValueError, match="formula"):
        _verify_experiment_provenance(run, run_manifest, job, experiment)
