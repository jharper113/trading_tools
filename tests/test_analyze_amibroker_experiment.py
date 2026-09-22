import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from amibroker_experiment_reports import analyze_job
from amibroker_analysis_common import read_project_context
import run_amibroker_analysis as runner


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


def test_analyze_job_writes_detailed_reports_inside_run_without_browser(valid_job, monkeypatch):
    opened = []
    monkeypatch.setattr(runner.webbrowser, "open", lambda url: opened.append(url))
    result = analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")
    assert result["status"] == "COMPLETE"
    assert Path(result["html_path"]).is_file()
    assert Path(result["json_path"]).is_file()
    assert result["json_sha256"] == _sha(Path(result["json_path"]))
    assert opened == []


def test_library_analysis_never_opens_browser(monkeypatch, valid_job):
    monkeypatch.setattr(runner.webbrowser, "open", lambda *_: pytest.fail("browser opened"))
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


@pytest.mark.parametrize("seconds,timeframe", [(300, "Intraday_5m"), (900, "Intraday_15m"), (3600, "Intraday_60m")])
def test_project_context_names_supported_intraday_intervals(tmp_path, seconds, timeframe):
    project = tmp_path / "project.apx"
    project.write_text(
        f"<AnalysisDoc><FormulaPath>Z:\\Strategies\\Demo.afl</FormulaPath><Periodicity>8</Periodicity><ChartInterval>{seconds}</ChartInterval></AnalysisDoc>"
    )
    assert read_project_context(project).timeframe == timeframe
