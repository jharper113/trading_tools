import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "Build-AmiBroker-ExperimentJob.ps1"
PWSH = os.environ.get("AMIBROKER_TEST_PWSH") or shutil.which("pwsh") or shutil.which("powershell")
SOURCE_0063 = Path(
    "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Strategies/2026 Strategy Testing/0063_intraday_opening_range_breakout.afl"
)


def test_builder_script_exists():
    assert BUILDER.is_file()
    text = BUILDER.read_text(encoding="utf-8")
    assert "'Explore'" in text
    assert "GENERATED EXPERIMENT PROVENANCE" in text
    assert "AttemptId" in text


def _project(path: Path):
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<AmiBroker-Analysis><General>
<FormulaPath>old.afl</FormulaPath><FormulaContent>old</FormulaContent>
<Periodicity>8</Periodicity><ChartInterval>900</ChartInterval>
<RangeType>3</RangeType><FromDate>2001-01-01</FromDate><ToDate>2002-01-01</ToDate>
<InitialEquity>1</InitialEquity><CommissionMode>0</CommissionMode><CommissionAmount>0</CommissionAmount>
<PointsOnlyTest>0</PointsOnlyTest><MinShares>1</MinShares><AllowSameBarExit>0</AllowSameBarExit>
<ReverseSignalForcesExit>1</ReverseSignalForcesExit><UsePrevBarEquity>0</UsePrevBarEquity>
<OptTarget>Net Profit</OptTarget>
</General></AmiBroker-Analysis>""",
        encoding="utf-8",
    )


def _profile(path: Path, seconds=900):
    path.write_text(
        json.dumps(
            {
                "initial_equity": 100000,
                "futures_mode": True,
                "min_shares": 1,
                "allow_same_bar_exit": True,
                "reverse_signal_forces_exit": False,
                "commission_mode": 3,
                "commission_per_contract_side": 3.76,
                "use_previous_bar_equity": True,
                "fitness": "CAR/MDD",
                "periodicity": {"apx_code": 2 if seconds == 3600 else 3, "seconds": seconds},
                "optimization_window": {"start": "2009-01-01", "end": "2019-01-01"},
            }
        ),
        encoding="utf-8",
    )


def _matrix(tmp_path: Path, adapter="entry_cutoff_110000", seconds=900, source_text=None):
    source_dir = tmp_path / "strategies"
    source_dir.mkdir(exist_ok=True)
    source = source_dir / SOURCE_0063.name
    source.write_text(source_text if source_text is not None else SOURCE_0063.read_text(), encoding="utf-8")
    database = tmp_path / "database"
    database.mkdir(exist_ok=True)
    (database / "broker.workspace").write_bytes(b"test workspace")
    project = tmp_path / "template.apx"
    profile = tmp_path / "profile.json"
    _project(project)
    _profile(profile, seconds)
    job_id = "test-job"
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "matrix_id": "test",
                "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
                "timezone": "America/Detroit",
                "strategy_root_windows": str(source_dir),
                "modes": {"pilot": [job_id]},
                "jobs": {
                    job_id: {
                        "job_id": job_id,
                        "strategy_id": source.stem,
                        "source_afl": source.name,
                        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "periodicity": "60m" if seconds == 3600 else "15m",
                        "interval_seconds": seconds,
                        "database": str(tmp_path / "database"),
                        "project_template": str(project),
                        "symbols": ["ES", "ZN"],
                        "adapter": {"name": adapter},
                        "analysis_profile": str(profile),
                        "enabled": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return matrix, source


@pytest.fixture
def ps_builder():
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell job builder")

    def run(tmp_path, adapter="entry_cutoff_110000", seconds=900, source_text=None):
        matrix, source = _matrix(tmp_path, adapter, seconds, source_text)
        destination = tmp_path / "built"
        result = subprocess.run(
            [
                PWSH,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(BUILDER),
                "-Matrix",
                str(matrix),
                "-JobId",
                "test-job",
                "-Destination",
                str(destination),
                "-ReportsRoot",
                str(tmp_path / "reports"),
                "-AttemptId",
                "pilot-1-attempt-1",
            ],
            text=True,
            capture_output=True,
        )
        return result, destination, source, matrix

    return run


def test_builder_keeps_source_immutable_and_applies_only_cutoff(tmp_path, ps_builder):
    result, built, source, _ = ps_builder(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    before = source.read_bytes()
    formula = (built / "formula.afl").read_text(encoding="utf-8-sig")
    assert source.read_bytes() == before
    assert "afterRange AND tn <= 110000 AND Cross(Close, orHigh)" in formula
    assert "Sell = tn >= exitTime OR Close < orLow;" in formula
    manifest = json.loads((built / "build_manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["adapter"] == "entry_cutoff_110000"
    assert manifest["replacement_count"] == 2
    assert manifest["attempt_id"] == "pilot-1-attempt-1"
    assert "GENERATED EXPERIMENT PROVENANCE" in formula


def test_builder_sets_interval_dates_and_embeds_generated_formula(tmp_path, ps_builder):
    result, built, _, _ = ps_builder(tmp_path, adapter="native", seconds=3600)
    assert result.returncode == 0, result.stdout + result.stderr
    root = ET.parse(built / "project.apx").getroot()
    assert root.findtext(".//ChartInterval") == "3600"
    assert root.findtext(".//Periodicity") == "2"
    assert root.findtext(".//FromDate")[:10] == "2009-01-01"
    assert root.findtext(".//ToDate")[:10] == "2019-01-01"
    assert root.findtext(".//FormulaContent") == (built / "formula.afl").read_text(encoding="utf-8-sig")
    batch = ET.parse(built / "batch.abb").getroot()
    actions = [node.findtext("Action") for node in batch]
    assert actions.count("Optimize") == 2
    assert actions.count("Explore") == 2
    load_database = next(node for node in batch if node.findtext("Action") == "LoadDatabase")
    assert load_database.findtext("Param") == str(tmp_path / "database" / "broker.workspace").replace("\\", "\\\\")
    load_project = next(node for node in batch if node.findtext("Action") == "LoadProject")
    assert load_project.findtext("Param") == str(built / "project.apx").replace("\\", "\\\\")


def test_builder_rejects_mismatched_analysis_periodicity(tmp_path):
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell job builder")
    matrix, _ = _matrix(tmp_path, adapter="native", seconds=900)
    profile = tmp_path / "profile.json"
    data = json.loads(profile.read_text())
    data["periodicity"]["apx_code"] = 11
    profile.write_text(json.dumps(data))
    destination = tmp_path / "built"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(BUILDER), "-Matrix", str(matrix),
         "-JobId", "test-job", "-Destination", str(destination),
         "-ReportsRoot", str(tmp_path / "reports"), "-AttemptId", "pilot-1-attempt-1"],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "periodicity code" in result.stdout + result.stderr
    assert not (destination / "batch.abb").exists()


@pytest.mark.parametrize("mutation", ["stale_hash", "missing_anchor", "duplicate_anchor"])
def test_builder_fails_closed_without_runnable_artifacts(tmp_path, ps_builder, mutation):
    source_text = SOURCE_0063.read_text()
    adapter = "entry_cutoff_110000"
    result, destination, source, matrix = ps_builder(tmp_path, adapter=adapter, source_text=source_text)
    if mutation == "stale_hash":
        data = json.loads(matrix.read_text())
        data["jobs"]["test-job"]["source_sha256"] = "0" * 64
        matrix.write_text(json.dumps(data))
    elif mutation == "missing_anchor":
        source.write_text(source_text.replace("Buy = afterRange AND Cross(Close, orHigh);", "Buy = False;"))
        data = json.loads(matrix.read_text())
        data["jobs"]["test-job"]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        matrix.write_text(json.dumps(data))
    else:
        source.write_text(source_text + "\nBuy = afterRange AND Cross(Close, orHigh);\n")
        data = json.loads(matrix.read_text())
        data["jobs"]["test-job"]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        matrix.write_text(json.dumps(data))
    shutil.rmtree(destination)
    rerun = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(BUILDER), "-Matrix", str(matrix), "-JobId", "test-job", "-Destination", str(destination), "-ReportsRoot", str(tmp_path / "reports"), "-AttemptId", "pilot-1-attempt-2"],
        text=True,
        capture_output=True,
    )
    assert rerun.returncode != 0
    assert not (destination / "project.apx").exists()
    assert not (destination / "batch.abb").exists()
