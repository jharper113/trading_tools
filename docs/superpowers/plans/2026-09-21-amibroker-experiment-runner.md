# AmiBroker Experiment Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable Windows runner that executes the approved five-job AmiBroker optimization pilot, verifies Eastern-time alignment, archives every input and export, and unlocks a later full-library run only after an external audit passes.

**Architecture:** Checked-in JSON defines strategies and jobs. A PowerShell job builder creates isolated AFL/APX/ABB artifacts without editing canonical strategies, and a PowerShell controller runs those jobs sequentially through AmiBroker. The existing archive helper remains responsible for atomic run publication and hashes; experiment manifests link the individual run archives.

**Tech Stack:** Windows PowerShell 5.1+, AmiBroker batch automation, AFL, Python 3.9+ validation tests, pytest, XML and JSON from the standard libraries.

**Spec:** `docs/superpowers/specs/2026-09-21-amibroker-strategy-matrix-design.md`

## Global Constraints

- Optimization dates are exactly `2009-01-01` through `2019-01-01`.
- Intraday data are interpreted as `America/Detroit`; the live AmiBroker database must report a zero-second time shift.
- Intraday source data use a 5-minute base interval; 15-minute and 60-minute jobs use AmiBroker compression.
- Jobs run sequentially in one AmiBroker instance.
- Canonical AFL files are never modified to create timing or periodicity variants.
- Pilot mode contains exactly five approved jobs and must pass before full mode can run.
- A failed, interrupted, partial, or hash-invalid run cannot be marked complete.
- The existing shared position-sizing, cost, and date policy remains authoritative.
- Generated and partial artifacts are retained; the runner performs no automatic cleanup.
- Windows Python is not required to execute an already prepared matrix.

## Review Focus

- A canonical AFL changes after the matrix was reviewed: Task 2 must reject its source hash before generating a job.
- An adapter anchor is absent or occurs twice: Task 2 must fail without writing a runnable APX or ABB.
- AmiBroker exits successfully but omits or reuses a CSV: Task 4 must mark the job `FAILED`, never `COMPLETE`.
- A machine restarts while a job is running: Task 4 must retain the partial run, mark it `INTERRUPTED`, and resume at the next valid pending attempt.
- Intraday timestamps have a nonzero shift or lack expected winter/summer ES session bars: Task 3 must block the pilot before optimization begins.

---

## File structure

- Create `amibroker_experiment_config.py`: shared Python validation for the catalog, matrix, and audit unlock artifact.
- Create `amibroker_experiments/strategy_catalog.json`: canonical strategy identities, hashes, native intervals, and supported adapters.
- Create `amibroker_experiments/strategy_test_matrix.json`: pilot and full experiment job definitions.
- Create `Analyzer_Profiles/Intraday_60m_Analyzer_Profile.json`: 60-minute project settings with the standard intraday gate.
- Create `Build-AmiBroker-ExperimentJob.ps1`: deterministic AFL/APX/ABB generation for one job.
- Create `AmiBroker_Timezone_Preflight.afl`: diagnostic ES exploration.
- Create `Run-AmiBroker-Experiment.ps1`: experiment lifecycle, preflight, AmiBroker invocation, and resume.
- Modify `Archive_AmiBroker_Run.ps1`: copy optional experiment provenance into each run manifest.
- Create `tests/test_amibroker_experiment_config.py`: JSON/schema and catalog coverage tests.
- Create `tests/test_amibroker_experiment_job_builder.py`: PowerShell generation tests.
- Create `tests/test_amibroker_experiment_runner.py`: fake-AmiBroker lifecycle and resume tests.
- Create `docs/amibroker_experiment_runner.md`: concise operating guide.

### Task 1: Versioned catalog, matrix, and 60-minute profile

**Files:**
- Create: `amibroker_experiment_config.py`
- Create: `amibroker_experiments/strategy_catalog.json`
- Create: `amibroker_experiments/strategy_test_matrix.json`
- Create: `Analyzer_Profiles/Intraday_60m_Analyzer_Profile.json`
- Test: `tests/test_amibroker_experiment_config.py`

**Interfaces:**
- Produces: `load_catalog(path: Path) -> dict`, `load_matrix(path: Path, catalog: dict) -> dict`, `validate_unlock(path: Path, pilot_id: str, audit_sha256: str) -> dict`.
- Produces matrix fields: `schema_version`, `matrix_id`, `research_window`, `timezone`, `modes`, and `jobs`.
- Each job provides: `job_id`, `strategy_id`, `source_afl`, `source_sha256`, `periodicity`, `interval_seconds`, `database`, `project_template`, `symbols`, `adapter`, `analysis_profile`, and `enabled`.
- Consumed by: Tasks 2–4 and the analyzer plan.

- [ ] **Step 1: Write failing catalog and matrix tests**

```python
from pathlib import Path
import pytest
from amibroker_experiment_config import load_catalog, load_matrix

ROOT = Path(__file__).resolve().parents[1]

def test_pilot_matrix_has_exact_approved_jobs():
    catalog = load_catalog(ROOT / "amibroker_experiments/strategy_catalog.json")
    matrix = load_matrix(ROOT / "amibroker_experiments/strategy_test_matrix.json", catalog)
    jobs = [matrix["jobs"][job_id] for job_id in matrix["modes"]["pilot"]]
    assert [(j["strategy_id"], j["periodicity"], j["adapter"]["name"]) for j in jobs] == [
        ("0001_exhaustion_open_dislocation_fade", "Daily", "native"),
        ("0063_intraday_opening_range_breakout", "15m", "native"),
        ("0063_intraday_opening_range_breakout", "15m", "entry_cutoff_110000"),
        ("0063_intraday_opening_range_breakout", "60m", "native"),
        ("0084_ES_INTRADAY_PCTB", "15m", "fixed_110000"),
    ]
    assert matrix["research_window"] == {"start": "2009-01-01", "end": "2019-01-01"}
    assert matrix["timezone"] == "America/Detroit"

def test_matrix_rejects_duplicate_job_and_unknown_strategy(tmp_path):
    catalog = {"schema_version": 1, "strategies": {}}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "schema_version": 1, "matrix_id": "bad",
        "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
        "timezone": "America/Detroit",
        "jobs": {"x": {"strategy_id": "missing"}}, "modes": {"pilot": ["x", "x"]},
    }))
    with pytest.raises(ValueError, match="duplicate|unknown"):
        load_matrix(bad, catalog)
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_config.py -q`

Expected: collection fails with `ModuleNotFoundError: amibroker_experiment_config`.

- [ ] **Step 3: Implement strict loaders and checked-in JSON**

```python
def load_matrix(path: Path, catalog: dict) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schema_version", "matrix_id", "research_window", "timezone", "modes", "jobs"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Matrix is missing: {', '.join(sorted(missing))}")
    if value["research_window"] != {"start": "2009-01-01", "end": "2019-01-01"}:
        raise ValueError("Matrix research window must match the shared policy")
    for mode, ids in value["modes"].items():
        if len(ids) != len(set(ids)):
            raise ValueError(f"Mode {mode} contains duplicate jobs")
        for job_id in ids:
            if job_id not in value["jobs"]:
                raise ValueError(f"Mode {mode} references unknown job {job_id}")
    for job_id, job in value["jobs"].items():
        if job["strategy_id"] not in catalog["strategies"]:
            raise ValueError(f"Job {job_id} references unknown strategy")
    return value
```

Populate the pilot list exactly as asserted. Populate full mode with one enabled native job for each catalog strategy whose timeframe is explicitly classified; add 15-minute/60-minute pairs only where the catalog explicitly marks both as supported. Store SHA-256 hashes in lowercase hex. Create the 60-minute profile by copying all cost, sizing, optimization-window, and threshold values from `Intraday_15m_Analyzer_Profile.json`, changing only `profile_name` and `periodicity.seconds` to `3600`.

- [ ] **Step 4: Add catalog coverage and stale-hash tests**

```python
def test_catalog_covers_every_canonical_afl_and_hashes_match():
    catalog = load_catalog(ROOT / "amibroker_experiments/strategy_catalog.json")
    actual = sorted(STRATEGIES.glob("*.afl"))
    assert len(actual) == len(catalog["strategies"])
    for path in actual:
        entry = catalog["strategies"][path.stem]
        assert entry["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

def test_full_mode_covers_each_enabled_catalog_strategy_with_a_native_job():
    catalog = load_catalog(CATALOG)
    matrix = load_matrix(MATRIX, catalog)
    selected = {matrix["jobs"][job]["strategy_id"] for job in matrix["modes"]["full"]
                if matrix["jobs"][job]["adapter"]["name"] == "native"}
    assert selected == {s for s, v in catalog["strategies"].items() if v["enabled"]}
```

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_config.py -q`

Expected: all tests pass and report exactly the current canonical AFL count.

- [ ] **Step 6: Commit the catalog contract**

```bash
git add amibroker_experiment_config.py amibroker_experiments/strategy_catalog.json amibroker_experiments/strategy_test_matrix.json Analyzer_Profiles/Intraday_60m_Analyzer_Profile.json tests/test_amibroker_experiment_config.py
git commit -m "feat: define AmiBroker experiment matrix"
```

### Task 2: Deterministic job artifact builder

**Files:**
- Create: `Build-AmiBroker-ExperimentJob.ps1`
- Test: `tests/test_amibroker_experiment_job_builder.py`

**Interfaces:**
- Consumes: one validated matrix job, canonical AFL, APX template, output directory, and existing `Archive_AmiBroker_Run.ps1`.
- Produces: `<job>/formula.afl`, `<job>/project.apx`, `<job>/batch.abb`, `<job>/batch.archive.json`, and `<job>/build_manifest.json`. The batch also exports a fixed-parameter entry audit for every symbol into each run archive's `entry_audit/` directory.
- Command: `Build-AmiBroker-ExperimentJob.ps1 -Matrix <json> -JobId <id> -Destination <dir> -ReportsRoot <windows-path>`.
- `build_manifest.json` includes every input/output path and SHA-256, the exact replacement count, strategy, periodicity, adapter, symbols, and research window.

- [ ] **Step 1: Write failing native and cutoff generation tests**

```python
def test_builder_keeps_source_immutable_and_applies_only_cutoff(tmp_path, ps_builder):
    before = SOURCE_0063.read_bytes()
    built = ps_builder("pilot-0063-15m-cutoff", tmp_path)
    formula = (built / "formula.afl").read_text(encoding="utf-8-sig")
    assert SOURCE_0063.read_bytes() == before
    assert "afterRange AND tn <= 110000 AND Cross(Close, orHigh)" in formula
    assert "Sell = tn >= exitTime OR Close < orLow;" in formula
    manifest = json.loads((built / "build_manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["adapter"] == "entry_cutoff_110000"
    assert manifest["replacement_count"] == 2

def test_builder_sets_interval_dates_and_embeds_generated_formula(tmp_path, ps_builder):
    built = ps_builder("pilot-0063-60m-native", tmp_path)
    root = ET.parse(built / "project.apx").getroot()
    assert root.findtext(".//ChartInterval") == "3600"
    assert root.findtext(".//FromDate")[:10] == "2009-01-01"
    assert root.findtext(".//ToDate")[:10] == "2019-01-01"
    assert root.findtext(".//FormulaContent") == (built / "formula.afl").read_text(encoding="utf-8-sig")
```

- [ ] **Step 2: Run the builder tests and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_job_builder.py -q`

Expected: FAIL because `Build-AmiBroker-ExperimentJob.ps1` does not exist.

- [ ] **Step 3: Implement exact-anchor AFL adapters**

```powershell
function Replace-Exactly([string]$Text, [string]$Old, [string]$New, [int]$ExpectedCount) {
    $count = ([regex]::Matches($Text, [regex]::Escape($Old))).Count
    if ($count -ne $ExpectedCount) { throw "Adapter anchor count $count; expected $ExpectedCount" }
    return $Text.Replace($Old, $New)
}
```

Implement `native` as a byte-for-text copy after hash validation. Implement `entry_cutoff_110000` with two exact replacements: add `tn <= 110000` to the long and short entry expressions in strategy 0063. Implement `fixed_110000` as a validation-only adapter that requires the existing `SignalBar = TN == 110000;` exactly once and changes no code. Reject unknown adapters before any APX or ABB is written. Append a generated exploration-only block that sets `Filter = Buy OR Short` and exports `DateTime()`, `TimeNum()`, `Buy`, and `Short`; `Filter` and `AddColumn` do not change optimization behavior.

- [ ] **Step 4: Implement APX, ABB, sidecar, and manifest rendering**

Use `System.Xml.XmlDocument` with `XmlResolver = $null`. Update `FormulaPath`, `FormulaContent`, `Periodicity`, `ChartInterval`, all range start/end aliases, and the profile-controlled cost and sizing nodes. For every symbol, generate the optimize/export/publish steps followed by scan/export/publish-audit steps. Finish with the existing archive `Complete` call. Write all files to a temporary directory, hash them, then rename the directory to its final job path.

```powershell
foreach ($symbol in @($job.symbols)) {
    Add-BatchStep $batch 'SetCurrentSymbol' $symbol
    Add-BatchStep $batch 'Optimize' ''
    Add-BatchStep $batch 'Export' (Join-Path $staging "$symbol.csv")
    Add-ArchiveStep $batch 'Publish' $symbol
    Add-BatchStep $batch 'Scan' ''
    Add-BatchStep $batch 'Export' (Join-Path $auditStaging "$symbol.csv")
    Add-ArchiveStep $batch 'PublishAudit' $symbol
}
```

- [ ] **Step 5: Add stale-hash, ambiguous-anchor, and partial-write tests**

```python
@pytest.mark.parametrize("mutation", ["stale_hash", "missing_anchor", "duplicate_anchor"])
def test_builder_fails_closed_without_runnable_artifacts(tmp_path, mutation, ps_builder_failure):
    result, destination = ps_builder_failure(mutation, tmp_path)
    assert result.returncode != 0
    assert not (destination / "project.apx").exists()
    assert not (destination / "batch.abb").exists()
```

- [ ] **Step 6: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_job_builder.py -q`

Expected: all tests pass under `pwsh`; skip with a clear message when no PowerShell runtime exists.

- [ ] **Step 7: Commit the builder**

```bash
git add Build-AmiBroker-ExperimentJob.ps1 tests/test_amibroker_experiment_job_builder.py
git commit -m "feat: build isolated AmiBroker experiment jobs"
```

### Task 3: Blocking Eastern-time database preflight

**Files:**
- Create: `AmiBroker_Timezone_Preflight.afl`
- Create: `Test-AmiBroker-Timezone.ps1`
- Test: `tests/test_amibroker_timezone_preflight.py`

**Interfaces:**
- Command: `Test-AmiBroker-Timezone.ps1 -Broker <exe> -Database <path> -ProjectTemplate <apx> -WorkDir <dir>`.
- Produces: `timezone_preflight.csv` and `timezone_preflight.json` with `status`, `timezone`, `database`, `timeshift_seconds`, `interval_seconds`, `winter_sessions`, `summer_sessions`, and SHA-256.
- Returns exit code `0` only for `PASS`; all other outcomes are blocking.

- [ ] **Step 1: Write failing validation tests using diagnostic CSV fixtures**

```python
@pytest.mark.parametrize("shift,interval", [(3600, 300), (0, 900)])
def test_preflight_rejects_shift_or_wrong_base_interval(preflight_fixture, shift, interval):
    result = preflight_fixture(shift=shift, interval=interval)
    assert result.returncode != 0
    assert json.loads(result.report.read_text())["status"] == "FAIL"

def test_preflight_requires_winter_and_summer_0930_1555_pairs(preflight_fixture):
    result = preflight_fixture(shift=0, interval=300, omit="summer_1555")
    assert result.returncode != 0
    assert "summer" in json.loads(result.report.read_text())["reason"].lower()
```

- [ ] **Step 2: Run the tests and verify the preflight scripts are missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_timezone_preflight.py -q`

Expected: FAIL because the scripts do not exist.

- [ ] **Step 3: Write the diagnostic AFL**

```afl
SetBarsRequired( sbrAll, sbrAll );
TN = TimeNum();
MonthNumber = Month();
SessionSample = ( MonthNumber == 1 OR MonthNumber == 7 ) AND ( TN == 93000 OR TN == 155500 );
Filter = Name() == "ES" AND SessionSample;
AddColumn( DateTime(), "DateTime", formatDateTime );
AddColumn( TN, "TimeNum", 1.0 );
AddColumn( Status("timeshift"), "TimeShiftSeconds", 1.0 );
AddColumn( Interval(), "IntervalSeconds", 1.0 );
```

- [ ] **Step 4: Implement the PowerShell validator and atomic report**

Run the diagnostic as an AmiBroker scan/export against the configured intraday database and 2009–2019 range. Parse the CSV with `Import-Csv`. Require every row to report shift `0` and interval `300`; group rows by date and require at least one January and one July weekday containing both `93000` and `155500`. Save the report atomically and include the CSV hash.

```powershell
$rows = @(Import-Csv -LiteralPath $csv)
if (-not $rows -or @($rows | Where-Object { [int]$_.TimeShiftSeconds -ne 0 }).Count) { throw 'AmiBroker time shift is not zero' }
if (@($rows | Where-Object { [int]$_.IntervalSeconds -ne 300 }).Count) { throw 'Intraday database is not 5-minute' }
$pairs = $rows | Group-Object { ([datetime]$_.DateTime).ToString('yyyy-MM-dd') }
$winter = @($pairs | Where-Object { ([datetime]$_.Name).Month -eq 1 -and (Test-SessionPair $_.Group) })
$summer = @($pairs | Where-Object { ([datetime]$_.Name).Month -eq 7 -and (Test-SessionPair $_.Group) })
if (-not $winter -or -not $summer) { throw 'Missing winter or summer 09:30/15:55 session pair' }
```

- [ ] **Step 5: Add a valid DST fixture and unsafe-data tests**

```python
def test_preflight_accepts_zero_shift_with_complete_winter_and_summer_sessions(preflight_fixture):
    result = preflight_fixture(shift=0, interval=300)
    assert result.returncode == 0
    report = json.loads(result.report.read_text())
    assert report["status"] == "PASS"
    assert report["timezone"] == "America/Detroit"

def test_preflight_rejects_malformed_or_duplicate_rows(preflight_fixture):
    assert preflight_fixture(malformed=True).returncode != 0
    assert preflight_fixture(duplicate=True).returncode != 0
```

- [ ] **Step 6: Run focused tests and commit**

Run: `.venv/bin/python -m pytest tests/test_amibroker_timezone_preflight.py -q`

Expected: all fixture-driven tests pass.

```bash
git add AmiBroker_Timezone_Preflight.afl Test-AmiBroker-Timezone.ps1 tests/test_amibroker_timezone_preflight.py
git commit -m "feat: verify AmiBroker intraday timezone"
```

### Task 4: Resumable experiment controller and archive provenance

**Files:**
- Create: `Run-AmiBroker-Experiment.ps1`
- Modify: `Archive_AmiBroker_Run.ps1`
- Test: `tests/test_amibroker_experiment_runner.py`
- Modify: `tests/test_archive_amibroker_batches.py`

**Interfaces:**
- Command: `Run-AmiBroker-Experiment.ps1 -Mode pilot -Matrix <json> -Broker <exe> -ReportsRoot <path> [-ExperimentId <id>]`.
- Consumes build artifacts from Task 2 and preflight report from Task 3.
- Produces `Reports/Experiments/<experiment-id>/experiment_manifest.json` with `status`, ordered job records, attempts, run-manifest paths and hashes, entry-audit hashes, preflight record, matrix hash, and policy hashes.
- Supports `-Resume <experiment_manifest.json>` and `-ContinueDailyAfterIntradayPreflightFailure`.

- [ ] **Step 1: Write fake-Broker lifecycle tests**

```python
def test_runner_is_sequential_and_links_only_complete_archives(fake_broker, pilot_matrix):
    result = fake_broker.run(mode="pilot")
    manifest = json.loads(result.manifest.read_text(encoding="utf-8-sig"))
    assert fake_broker.max_concurrent == 1
    assert [j["status"] for j in manifest["jobs"]] == ["COMPLETE"] * 5
    assert all(Path(j["run_manifest_path"]).is_file() for j in manifest["jobs"])

def test_success_exit_with_missing_csv_fails_job(fake_broker):
    result = fake_broker.run(omit_export="ZN.csv")
    job = json.loads(result.manifest.read_text())["jobs"][0]
    assert job["status"] == "FAILED"
    assert "ZN.csv" in job["error"]
```

- [ ] **Step 2: Run tests and verify the controller is missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_runner.py -q`

Expected: FAIL because `Run-AmiBroker-Experiment.ps1` does not exist.

- [ ] **Step 3: Extend archive manifests with optional experiment provenance**

When `batch.archive.json` contains `experiment`, copy this object unchanged into `run_manifest.json` and hash the generated AFL, build manifest, experiment matrix, shared policy, and analyzer profile. Add optional `audit_symbols`; a new `PublishAudit` stage validates and copies each scan export to `run_dir/entry_audit/<symbol>.csv` with its hash. `Complete` requires all optimization and audit exports when `audit_symbols` is present. Keep schema-1 sidecars without audit fields valid for existing batches.

```powershell
if ($null -ne $cfg.PSObject.Properties['experiment']) {
    $manifest.experiment = $cfg.experiment
}
```

- [ ] **Step 4: Implement atomic experiment state and sequential invocation**

Create the manifest before preflight. Before each invocation set the job to `RUNNING` and save atomically. Invoke `& $Broker /runbatch $batch /exit`, require exit code zero, read the archive context and run manifest, independently validate export names and hashes, then set `COMPLETE`. Catch errors per job, write `FAILED`, and continue unless the matrix marks the job `stop_on_failure`.

```powershell
$jobState.status = 'RUNNING'
Save-Json $experiment $manifestPath
try {
    & $Broker '/runbatch' $jobState.batch_path '/exit'
    if ($LASTEXITCODE -ne 0) { throw "AmiBroker exited $LASTEXITCODE" }
    Assert-CompleteRunManifest -Path $jobState.run_manifest_path -ExpectedSymbols $jobState.symbols
    $jobState.status = 'COMPLETE'
} catch {
    $jobState.status = 'FAILED'
    $jobState.error = $_.Exception.Message
} finally {
    Save-Json $experiment $manifestPath
}
```

- [ ] **Step 5: Implement resume and full-mode lock**

On resume, verify the matrix hash. Revalidate every completed job's referenced manifest and hashes before skipping it. Convert stale `RUNNING` to `INTERRUPTED`, preserve its attempt, and create a new attempt directory. For `-Mode full`, PowerShell independently enforces the same unlock contract as `validate_unlock`: matching pilot ID, audit SHA-256, and `audit_status: PASS`. It does not invoke Python at runtime.

```powershell
if ($Mode -eq 'full') {
    $unlock = Read-Json $Unlock
    if ($unlock.audit_status -ne 'PASS') { throw 'Full mode requires a passing pilot audit' }
    if ((Get-FileHash $unlock.audit_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $unlock.audit_sha256) {
        throw 'Pilot audit hash mismatch'
    }
}
```

- [ ] **Step 6: Add restart, corrupt-hash, preflight, and unlock tests**

```python
def test_resume_skips_valid_complete_jobs_and_retries_interrupted(fake_broker):
    first = fake_broker.interrupt_after(2)
    resumed = fake_broker.resume(first.manifest)
    assert resumed.invocations_for_job(0) == 0
    assert resumed.invocations_for_job(1) == 0
    assert resumed.final_status == "COMPLETE"

def test_full_mode_requires_matching_passing_unlock(fake_broker):
    for unlock in (None, "wrong_pilot", "wrong_hash", "manual_check_required"):
        assert fake_broker.run_full(unlock=unlock).returncode != 0
```

- [ ] **Step 7: Run focused PowerShell integration tests**

Run: `AMIBROKER_TEST_PWSH=$(command -v pwsh) .venv/bin/python -m pytest tests/test_amibroker_experiment_runner.py tests/test_archive_amibroker_batches.py -q`

Expected: all tests pass; the fake broker records a maximum concurrency of one.

- [ ] **Step 8: Commit the controller**

```bash
git add Run-AmiBroker-Experiment.ps1 Archive_AmiBroker_Run.ps1 tests/test_amibroker_experiment_runner.py tests/test_archive_amibroker_batches.py
git commit -m "feat: run resumable AmiBroker experiments"
```

### Task 5: Operating guide and runner acceptance check

**Files:**
- Create: `docs/amibroker_experiment_runner.md`
- Modify: `README.md`

**Interfaces:**
- Documents one pilot command, one resume command, manifest locations, failure interpretation, and the later full-mode command.
- Produces no new runtime API.

- [ ] **Step 1: Write the guide with exact Windows commands**

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Mode pilot `
  -Matrix "Z:\04_Code\Python\trading_tools\amibroker_experiments\strategy_test_matrix.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

Document that the command must run after Dropbox sync, with no concurrent AmiBroker analysis. Include the corresponding `-Resume` invocation and explain that `COMPLETE` means archived evidence, not a strategy pass.

- [ ] **Step 2: Add a documentation link to README**

Add a short “AmiBroker experiment pilot” entry linking `docs/amibroker_experiment_runner.md` beside the existing run-archive documentation.

- [ ] **Step 3: Run all runner tests**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_config.py tests/test_amibroker_experiment_job_builder.py tests/test_amibroker_timezone_preflight.py tests/test_amibroker_experiment_runner.py tests/test_archive_amibroker_batches.py -q`

Expected: all available tests pass; PowerShell tests explicitly skip only when neither `pwsh` nor `powershell` is installed.

- [ ] **Step 4: Verify repository-wide compatibility**

Run: `.venv/bin/python -m pytest -q`

Expected: the existing suite and the new runner tests pass.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/amibroker_experiment_runner.md README.md
git commit -m "docs: explain AmiBroker experiment runner"
```

## Runner completion boundary

This plan is complete when the checked-in pilot can be generated and exercised against the fake broker on Linux/PowerShell. Running the real five-job pilot is an acceptance step on the Windows AmiBroker machine. Its output becomes the input to the analyzer/audit plan; full mode remains locked until that audit writes a passing unlock artifact.
