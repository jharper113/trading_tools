import json
from pathlib import Path
import shutil
import subprocess

import pytest


ARCHIVE = Path("amibroker_import/Archive-AmiBroker-Databases.ps1")
IMPORT = Path("amibroker_import/Import-MarketData.ps1")
VERIFY = Path("amibroker_import/Verify-AmiBroker-Databases.ps1")


def test_archive_script_guards_hashes_moves_and_rollback():
    script = ARCHIVE.read_text()

    assert "Get-Process" in script and '"Broker"' in script
    assert "Get-FileHash" in script
    assert "archive_manifest.json" in script
    assert 'ArchiveDate = "2026-09-22"' in script
    assert "Harp_Daily_before_kibot_merge_{0}" in script
    assert "Harp_Intraday_before_kibot_merge_{0}" in script
    assert "ROLLBACK" in script
    assert "2,000,000 bars" in script


def test_importer_requires_passing_merge_and_matching_export_hashes():
    script = IMPORT.read_text()

    assert "merge_summary.json" in script
    assert '.status -ne "PASS"' in script
    assert "$manifest.files" in script
    assert "Get-FileHash" in script
    assert "hash does not match" in script


def test_verifier_contract_checks_counts_ranges_and_es_seasons():
    script = VERIFY.read_text()

    assert "amibroker_database_verification.json" in script
    assert "Quotations.Count" in script
    assert "truncated" in script.lower()
    assert "winter" in script.lower()
    assert "summer" in script.lower()
    assert "FixturePath" in script


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_verifier_rejects_truncated_intraday_fixture(tmp_path):
    expected = {
        "daily": {"symbols": []},
        "intraday": {
            "symbols": [{
                "ticker": "ES",
                "rows": 1_100_000,
                "first": "2009-09-01 09:30:00",
                "last": "2026-09-20 16:00:00",
            }],
            "es_research_sessions": {"winter": True, "summer": True},
        },
    }
    fixture = {
        "daily": {"symbols": []},
        "intraday": {
            "symbols": [{
                "ticker": "ES",
                "rows": 10_000,
                "first": "2026-06-01 09:30:00",
                "last": "2026-09-20 16:00:00",
            }],
            "es_research_sessions": {"winter": False, "summer": True},
        },
    }
    manifest = tmp_path / "export_complete.json"
    fixture_path = tmp_path / "fixture.json"
    output = tmp_path / "verification.json"
    manifest.write_text(json.dumps(expected))
    fixture_path.write_text(json.dumps(fixture))

    result = subprocess.run(
        [
            _powershell(), "-NoProfile", "-File", str(VERIFY.resolve()),
            "-ExportManifest", str(manifest), "-FixturePath", str(fixture_path),
            "-OutputPath", str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "truncated" in (result.stdout + result.stderr).lower()
    assert json.loads(output.read_text())["status"] == "FAIL"
