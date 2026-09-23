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
    assert 'archive_manifest_{0}.json' in script
    assert "failed_" in script
    assert "Remove-Item -LiteralPath $ManifestPath" in script
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
    assert "truncated" in script.lower()
    assert "winter" in script.lower()
    assert "summer" in script.lower()
    assert "if ($winter -and $summer) { break }" in script
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


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_verifier_reports_null_live_quote_with_ticker_and_database(tmp_path):
    harness = tmp_path / "null_quote.ps1"
    harness.write_text(r'''
$script = Get-Content -LiteralPath $args[0] -Raw
$functions = ($script -split '(?m)^\$manifest =', 2)[0]
$functions = $functions.Substring($functions.IndexOf('function Get-ObjectProperty'))
Invoke-Expression $functions
$quotes = [pscustomobject]@{ Count = 1 }
$quotes | Add-Member -MemberType ScriptMethod -Name Item -Value { param($index) $null }
$global:fakeStock = [pscustomobject]@{ Quotations = $quotes }
$stocks = [pscustomobject]@{ Count = 1 }
$stocks | Add-Member -MemberType ScriptMethod -Name Item -Value { param($ticker) $global:fakeStock }
$broker = [pscustomobject]@{ Stocks = $stocks }
$broker | Add-Member -MemberType ScriptMethod -Name LoadDatabase -Value { param($path) $true }
$expected = '{"symbols":[{"ticker":"ES","rows":1,"first":"2009-01-01 09:30:00","last":"2009-01-01 09:30:00"}],"es_research_sessions":{"winter":false,"summer":false}}' | ConvertFrom-Json
$actual = Read-LiveDatabase $broker 'fixture-daily' $expected $false
@{ actual = $actual; failures = @(Compare-Snapshot 'daily' $expected $actual) } | ConvertTo-Json -Depth 10
''')
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-File", str(harness), str(VERIFY.resolve())],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "fixture-daily" in output["failures"][0]
    assert "ES" in output["failures"][0]
    assert "quotation 0" in output["failures"][0]


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_verifier_writes_failure_report_for_unavailable_quote_dates(tmp_path):
    expected = {
        "daily": {
            "symbols": [{
                "ticker": "ES", "rows": 1,
                "first": "2009-01-01 09:30:00",
                "last": "2009-01-01 09:30:00",
            }],
        },
        "intraday": {"symbols": []},
    }
    fixture = {
        "daily": {
            "symbols": [{"ticker": "ES", "rows": 1, "first": None, "last": None}],
            "diagnostics": ["Harp_Daily ticker ES: quotation 0 is null"],
        },
        "intraday": {"symbols": []},
    }
    manifest = tmp_path / "manifest.json"
    snapshot = tmp_path / "snapshot.json"
    report = tmp_path / "verification.json"
    manifest.write_text(json.dumps(expected))
    snapshot.write_text(json.dumps(fixture))

    result = subprocess.run(
        [
            _powershell(), "-NoProfile", "-File", str(VERIFY.resolve()),
            "-ExportManifest", str(manifest), "-FixturePath", str(snapshot),
            "-OutputPath", str(report),
        ],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    output = json.loads(report.read_text())
    assert output["status"] == "FAIL"
    assert "quotation 0 is null" in " ".join(output["failures"])
    assert "first date is unavailable" in " ".join(output["failures"])
    assert "last date is unavailable" in " ".join(output["failures"])
    assert "Cannot convert null" not in result.stderr


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_verifier_identifies_unavailable_stocks_collection(tmp_path):
    harness = tmp_path / "missing_stocks.ps1"
    harness.write_text(r'''
$script = Get-Content -LiteralPath $args[0] -Raw
$functions = ($script -split '(?m)^\$manifest =', 2)[0]
$functions = $functions.Substring($functions.IndexOf('function Get-ObjectProperty'))
Invoke-Expression $functions
$broker = [pscustomobject]@{ Stocks = $null; DatabasePath = 'fixture-daily' }
$broker | Add-Member -MemberType ScriptMethod -Name LoadDatabase -Value { param($path) $true }
$expected = '{"symbols":[{"ticker":"ES","rows":1}]}' | ConvertFrom-Json
try {
    $null = Read-LiveDatabase $broker 'fixture-daily' $expected $false
    Write-Output 'unexpected PASS'
} catch {
    Write-Output $_.Exception.Message
}
''')
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-File", str(harness), str(VERIFY.resolve())],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Stocks collection is unavailable" in result.stdout
    assert "fixture-daily" in result.stdout
    assert "unexpected PASS" not in result.stdout


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_verifier_dispatches_com_properties_and_indexed_items(tmp_path):
    harness = tmp_path / "com_properties.ps1"
    harness.write_text(r'''
$script = Get-Content -LiteralPath $args[0] -Raw
$functions = ($script -split '(?m)^\$manifest =', 2)[0]
$functions = $functions.Substring($functions.IndexOf('function Get-ObjectProperty'))
Invoke-Expression $functions
$dictionary = New-Object -ComObject Scripting.Dictionary
$dictionary.Add('ES', 7400)
@{
    count = Get-ObjectProperty $dictionary 'Count'
    es = Get-ObjectItem $dictionary 'ES'
} | ConvertTo-Json
''')
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-File", str(harness), str(VERIFY.resolve())],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"count": 1, "es": 7400}
