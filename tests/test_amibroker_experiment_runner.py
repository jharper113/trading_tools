import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "Run-AmiBroker-Experiment.ps1"
ARCHIVER = ROOT / "Archive_AmiBroker_Run.ps1"
PWSH = os.environ.get("AMIBROKER_TEST_PWSH") or shutil.which("pwsh") or shutil.which("powershell")


def test_runner_and_archive_contract_are_present():
    assert RUNNER.is_file()
    runner = RUNNER.read_text(encoding="utf-8")
    archiver = ARCHIVER.read_text(encoding="utf-8")
    assert "'PENDING','RUNNING','COMPLETE','FAILED','INTERRUPTED'" in runner
    assert "Start-Process" in runner
    assert "-Wait" in runner
    assert "-PassThru" in runner
    assert ".ExitCode" in runner
    assert "& $Broker '/runbatch'" not in runner
    assert "PublishAudit" in archiver
    assert "audit_exports" in archiver
    assert "experiment_artifacts" in archiver
    assert "attempt_id" in runner
    assert "attempt_id" in archiver
    assert "Validate-FullUnlock" in runner
    assert "Builder exited" not in runner


def test_runner_rejects_compact_matrix_for_full_mode_without_unlock(tmp_path):
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell runner")
    result = subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(RUNNER),
            "-Mode",
            "full",
            "-Matrix",
            str(ROOT / "amibroker_experiments/strategy_test_matrix.json"),
            "-Broker",
            "missing-broker",
            "-ReportsRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unlock" in (result.stdout + result.stderr).lower()


def test_resume_refuses_matrix_hash_change(tmp_path):
    if not PWSH:
        pytest.skip("Set AMIBROKER_TEST_PWSH to exercise the PowerShell runner")
    matrix = tmp_path / "matrix.json"
    matrix.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "experiment_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": "x",
                "mode": "pilot",
                "matrix_path": str(matrix),
                "matrix_sha256": "0" * 64,
                "status": "RUNNING",
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(RUNNER),
            "-Resume",
            str(manifest),
            "-Broker",
            "missing-broker",
            "-ReportsRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "hash" in (result.stdout + result.stderr).lower()
