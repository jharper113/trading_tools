import json
import os
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest

from amibroker_timezone_preflight import validate_timezone_export


ROOT = Path(__file__).resolve().parents[1]
AFL = ROOT / "AmiBroker_Timezone_Preflight.afl"
SCRIPT = ROOT / "Test-AmiBroker-Timezone.ps1"
PWSH = os.environ.get("AMIBROKER_TEST_PWSH") or shutil.which("pwsh") or shutil.which("powershell")


def _write_fixture(path, shift=0, interval=300, omit=None, malformed=False, duplicate=False):
    rows = [
        ["2018-01-16 09:30:00", 93000, shift, interval],
        ["2018-01-16 15:55:00", 155500, shift, interval],
        ["2018-07-17 09:30:00", 93000, shift, interval],
        ["2018-07-17 15:55:00", 155500, shift, interval],
    ]
    labels = ["winter_0930", "winter_1555", "summer_0930", "summer_1555"]
    rows = [row for row, label in zip(rows, labels) if label != omit]
    if malformed:
        rows[0][0] = "not-a-date"
    if duplicate:
        rows.append(list(rows[0]))
    pd.DataFrame(
        rows,
        columns=["DateTime", "TimeNum", "TimeShiftSeconds", "IntervalSeconds"],
    ).to_csv(path, index=False)
    return path


def test_preflight_files_exist():
    assert AFL.is_file()
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "'Explore'" in text
    assert "Remove-Item -LiteralPath $csvPath" in text
    assert "Start-Process" in text
    assert "-Wait" in text
    assert "-PassThru" in text
    assert ".ExitCode" in text
    assert "& $Broker '/runbatch'" not in text
    assert "Harp_Intraday has no qualifying ES data" in text


@pytest.mark.parametrize("shift,interval", [(3600, 300), (0, 900)])
def test_preflight_rejects_shift_or_wrong_base_interval(tmp_path, shift, interval):
    csv_path = _write_fixture(tmp_path / "preflight.csv", shift=shift, interval=interval)
    with pytest.raises(ValueError, match="shift|5-minute"):
        validate_timezone_export(csv_path, "database")


def test_preflight_requires_winter_and_summer_0930_1555_pairs(tmp_path):
    csv_path = _write_fixture(tmp_path / "preflight.csv", omit="summer_1555")
    with pytest.raises(ValueError, match="summer"):
        validate_timezone_export(csv_path, "database")


def test_preflight_accepts_zero_shift_with_complete_winter_and_summer_sessions(tmp_path):
    csv_path = _write_fixture(tmp_path / "preflight.csv")
    report = validate_timezone_export(csv_path, "database")
    assert report["status"] == "PASS"
    assert report["timezone"] == "America/Detroit"
    assert report["timeshift_seconds"] == 0
    assert report["interval_seconds"] == 300
    assert report["winter_sessions"] == ["2018-01-16"]
    assert report["summer_sessions"] == ["2018-07-17"]


@pytest.mark.parametrize("fault", ["malformed", "duplicate"])
def test_preflight_rejects_malformed_or_duplicate_rows(tmp_path, fault):
    csv_path = _write_fixture(
        tmp_path / "preflight.csv",
        malformed=fault == "malformed",
        duplicate=fault == "duplicate",
    )
    with pytest.raises(ValueError, match="date|duplicate"):
        validate_timezone_export(csv_path, "database")


def test_powershell_fixture_mode_matches_valid_result(tmp_path):
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell preflight")
    fixture = _write_fixture(tmp_path / "fixture.csv")
    work = tmp_path / "work"
    result = subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-Broker",
            "unused",
            "-Database",
            "test-database",
            "-ProjectTemplate",
            "unused",
            "-WorkDir",
            str(work),
            "-CsvFixture",
            str(fixture),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((work / "timezone_preflight.json").read_text(encoding="utf-8-sig"))
    assert report["status"] == "PASS"


def test_preflight_batch_loads_workspace_file(tmp_path):
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell preflight")
    database = tmp_path / "database"
    database.mkdir()
    (database / "broker.workspace").write_bytes(b"test workspace")
    broker = tmp_path / "Broker.exe"
    broker.write_bytes(b"stub")
    project = tmp_path / "template.apx"
    project.write_text("<AmiBroker-Analysis><FormulaPath>old.afl</FormulaPath><FormulaContent>old</FormulaContent></AmiBroker-Analysis>")
    work = tmp_path / "work"
    harness = tmp_path / "generate.ps1"
    harness.write_text(r'''
function Start-Process {
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$Wait, [switch]$PassThru)
    [pscustomobject]@{ ExitCode = 1 }
}
& $args[0] -Broker $args[1] -Database $args[2] -ProjectTemplate $args[3] -WorkDir $args[4]
''')
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(harness), str(SCRIPT), str(broker), str(database), str(project), str(work)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0  # The stub stops execution after batch generation.
    import xml.etree.ElementTree as ET
    batch = ET.parse(work / "timezone_preflight.abb").getroot()
    load_database = next(node for node in batch if node.findtext("Action") == "LoadDatabase")
    assert load_database.findtext("Param") == str(database / "broker.workspace").replace("\\", "\\\\")
    load_project = next(node for node in batch if node.findtext("Action") == "LoadProject")
    assert load_project.findtext("Param") == str(work / "timezone_preflight.apx").replace("\\", "\\\\")
