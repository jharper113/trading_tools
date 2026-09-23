import hashlib
import json
from pathlib import Path
import subprocess
import sys
import webbrowser

import pandas as pd
import pytest

from amibroker_experiment_reports import analyze_job
from amibroker_analysis_common import read_project_context
import run_amibroker_analysis as runner
import analyze_amibroker_experiment as experiment_analyzer


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def valid_job(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    project = run / "project.apx"
    project.write_text(
        """<?xml version="1.0"?><AmiBroker-Analysis><General>
<FormulaPath>Z:\\Strategies\\Demo.afl</FormulaPath><Periodicity>0</Periodicity>
<ChartInterval>86400</ChartInterval><RangeType>3</RangeType>
<FromDate>2009-01-01</FromDate><ToDate>2019-01-01</ToDate>
</General></AmiBroker-Analysis>""",
        encoding="utf-8",
    )
    result = run / "ES.csv"
    pd.DataFrame(
        [
            {"Net Profit": 100, "Profit Factor": 1.3, "# Trades": 100, "CAR/MDD": .5, "Max. Sys % Drawdown": -10, "Opt X": 1},
            {"Net Profit": 80, "Profit Factor": 1.2, "# Trades": 90, "CAR/MDD": .4, "Max. Sys % Drawdown": -12, "Opt X": 2},
        ]
    ).to_csv(result, index=False)
    return {
        "experiment_id": "experiment-1",
        "job_id": "job-1",
        "strategy": "Demo",
        "periodicity": "Daily",
        "schedule": "native",
        "run_path": run,
        "project_path": project,
        "core_symbols": [],
    }


@pytest.fixture
def cli_experiment_fixture(tmp_path):
    matrix = {
        "schema_version": 1,
        "matrix_id": "cli-fixture",
        "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
        "timezone": "America/Detroit",
        "jobs": {},
    }
    states = []
    for job_id in ("valid", "tampered"):
        run = tmp_path / job_id
        run.mkdir()
        result = run / "ES.csv"
        pd.DataFrame(
            [{"Net Profit": 100, "Profit Factor": 1.3, "# Trades": 100,
              "CAR/MDD": .5, "Max. Sys % Drawdown": -10, "Opt X": 1}]
        ).to_csv(result, index=False)
        project = run / "project.apx"
        project.write_text(
            "<AnalysisDoc><FormulaPath>Z:\\Strategies\\Demo.afl</FormulaPath>"
            "<Periodicity>0</Periodicity><ChartInterval>86400</ChartInterval>"
            "<FromDate>2009-01-01</FromDate><ToDate>2019-01-01</ToDate></AnalysisDoc>"
        )
        run_manifest = run / "run_manifest.json"
        run_manifest.write_text(json.dumps({
            "status": "COMPLETE", "expected_symbols": ["ES"],
            "exports": [{"symbol": "ES", "file": "ES.csv", "sha256": _sha(result)}],
        }))
        matrix["jobs"][job_id] = {
            "job_id": job_id, "strategy_id": "Demo", "periodicity": "Daily",
            "adapter": {"name": "native"},
            "analysis_profile": "Analyzer_Profiles/Daily_Analyzer_Profile.json",
            "symbols": ["ES"], "source_sha256": "a" * 64,
        }
        states.append({
            "job_id": job_id, "status": "COMPLETE",
            "run_manifest_path": str(run_manifest),
            "run_manifest_sha256": _sha(run_manifest),
        })
        if job_id == "tampered":
            result.write_text(result.read_text() + "\n")
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix))
    manifest = tmp_path / "experiment_manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "experiment_id": "experiment-1", "mode": "pilot",
        "matrix_path": str(matrix_path), "matrix_sha256": _sha(matrix_path), "jobs": states,
    }))
    return type("Fixture", (), {"manifest": manifest})


def test_analyze_job_writes_detailed_reports_inside_run_without_browser(valid_job, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    result = analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")
    assert result["status"] == "COMPLETE"
    assert Path(result["html_path"]).is_file()
    assert Path(result["json_path"]).is_file()
    assert result["json_sha256"] == _sha(Path(result["json_path"]))
    assert opened == []


def test_library_analysis_never_opens_browser(monkeypatch, valid_job):
    monkeypatch.setattr(webbrowser, "open", lambda *_: pytest.fail("browser opened"))
    analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")


def test_analyze_job_returns_structured_failure_without_changing_run(valid_job):
    before = sorted(path.name for path in valid_job["run_path"].iterdir())
    valid_job["project_path"] = valid_job["run_path"] / "missing.apx"
    result = analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")
    assert result["status"] == "FAILED"
    assert result["job_id"] == "job-1"
    assert "missing.apx" in result["error"]
    assert sorted(path.name for path in valid_job["run_path"].iterdir()) == before


@pytest.mark.parametrize(
    "timeframe,seconds,profile",
    [
        ("Intraday_5m", 300, "Intraday_5m_Analyzer_Profile.json"),
        ("Intraday_60m", 3600, "Intraday_60m_Analyzer_Profile.json"),
    ],
)
def test_profile_path_supports_all_experiment_intervals(timeframe, seconds, profile):
    context = type("Context", (), {"timeframe": timeframe, "interval_seconds": seconds})
    assert runner._profile_path(context).name == profile


@pytest.mark.parametrize("code,seconds,timeframe", [(4, 300, "Intraday_5m"), (3, 900, "Intraday_15m"), (2, 3600, "Intraday_60m")])
def test_project_context_names_supported_intraday_intervals(tmp_path, code, seconds, timeframe):
    project = tmp_path / "project.apx"
    project.write_text(
        f"<AnalysisDoc><FormulaPath>Z:\\Strategies\\Demo.afl</FormulaPath><Periodicity>{code}</Periodicity><ChartInterval>{seconds}</ChartInterval></AnalysisDoc>"
    )
    assert read_project_context(project).timeframe == timeframe


def test_project_context_rejects_mismatched_intraday_code(tmp_path):
    project = tmp_path / "project.apx"
    project.write_text(
        "<AnalysisDoc><FormulaPath>Demo.afl</FormulaPath>"
        "<Periodicity>8</Periodicity><ChartInterval>900</ChartInterval></AnalysisDoc>"
    )
    assert read_project_context(project).timeframe == "Interval_900s"


def test_cli_is_rerunnable_and_never_promotes_corrupt_job(cli_experiment_fixture, tmp_path):
    output = tmp_path / "analysis"
    command = [
        sys.executable,
        str(Path(__file__).parents[1] / "analyze_amibroker_experiment.py"),
        str(cli_experiment_fixture.manifest),
        "--output-dir",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert (first.returncode, first.stderr) == (0, "")
    assert (second.returncode, second.stderr) == (0, "")
    summary = json.loads((output / "experiment_summary.json").read_text())
    assert summary["job_counts"]["invalid"] == 1
    assert all(row["job_id"] != "tampered" for row in summary["passed_candidates"])
    assert (output / "all_symbol_results.csv").is_file()
    assert (output / "schedule_comparisons.csv").is_file()
    assert (output / "experiment-1_Optimization_Review.xlsx").is_file()


def test_failed_detailed_analysis_is_excluded_without_stopping_other_jobs(tmp_path, monkeypatch):
    jobs = [
        {"job_id": "good", "run_path": tmp_path / "good", "analysis_profile": tmp_path / "p.json"},
        {"job_id": "bad", "run_path": tmp_path / "bad", "analysis_profile": tmp_path / "p.json"},
    ]
    for job in jobs:
        job["run_path"].mkdir()
    monkeypatch.setattr(experiment_analyzer, "load_experiment", lambda _: {
        "experiment_id": "x", "jobs": [{}, {}]
    })
    monkeypatch.setattr(experiment_analyzer, "admit_jobs", lambda _: (jobs, []))
    monkeypatch.setattr(experiment_analyzer, "_load_json", lambda _: {
        "economic_groups": {}, "thresholds": {"core": {}}
    })
    monkeypatch.setattr(experiment_analyzer, "analyze_job", lambda job, _: {
        "job_id": job["job_id"], "status": "FAILED" if job["job_id"] == "bad" else "COMPLETE",
        "decision": None, "error": "broken" if job["job_id"] == "bad" else None,
    })
    monkeypatch.setattr(experiment_analyzer, "summarize_job", lambda job, *_: [{
        "job_id": job["job_id"], "selected_representative": True,
    }])
    monkeypatch.setattr(experiment_analyzer, "evaluate_sectors", lambda rows: rows)
    captured = []
    monkeypatch.setattr(experiment_analyzer, "_comparisons", lambda good_jobs, _: captured.extend(good_jobs) or [])
    summary = experiment_analyzer.build_summary(tmp_path / "manifest.json")
    assert [row["job_id"] for row in summary["all_symbol_results"]] == ["good"]
    assert [job["job_id"] for job in captured] == ["good"]
    assert summary["failed_or_incomplete"] == [{"job_id": "bad", "reason": "broken"}]


def test_output_rerun_recovers_previous_before_failed_replacement(tmp_path, monkeypatch):
    output = tmp_path / "Analysis"
    previous = tmp_path / "Analysis.previous"
    previous.mkdir()
    (previous / "marker.txt").write_text("last complete")
    monkeypatch.setattr(experiment_analyzer, "write_workbook", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    summary = {
        "experiment_id": "x", "all_symbol_results": [], "schedule_comparisons": [],
        "passed_candidates": [], "low_touch_recommendations": [],
        "frequent_entry_exceptions": [], "failed_or_incomplete": [], "job_counts": {},
    }
    with pytest.raises(RuntimeError, match="boom"):
        experiment_analyzer.write_outputs(summary, output)
    assert (output / "marker.txt").read_text() == "last complete"
