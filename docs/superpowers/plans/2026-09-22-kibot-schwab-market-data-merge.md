# Kibot and Schwab Market Data Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and migrate to one validated daily and 5-minute repository that extends history with Kibot, gives valid Schwab bars precedence, and rebuilds both AmiBroker databases without truncation.

**Architecture:** A streaming Kibot reader normalizes one ZIP member at a time, then a deterministic merge engine selects rows by key and source priority and writes a staged repository plus audit evidence. Directory-level publication replaces only `daily` and `5min` after validation. The downloader reuses the precedence engine, while a streaming AmiBroker exporter and Windows verification scripts handle the larger history with bounded memory.

**Tech Stack:** Python 3, pandas, standard-library `zipfile`, `zoneinfo`, `hashlib`, `csv`, and `pathlib`; pytest; Windows PowerShell 5.1; AmiBroker OLE and ASCII import formats.

**Spec:** `docs/superpowers/specs/2026-09-22-kibot-schwab-market-data-merge-design.md`

## Global Constraints

- Canonical data lives in `data/market_data/daily` and `data/market_data/5min`; no active persisted 60-minute directory remains after migration.
- A manually reviewed bar remains protected; otherwise valid source priority is direct Schwab, Kibot, then other existing sources.
- A valid Schwab row keeps Schwab OHLC and volume. Volume differences alone never select Kibot.
- Convert Kibot intraday timestamps with `America/Detroit` daylight-saving rules before joining UTC timestamps.
- Keep the two vendor ZIPs byte-for-byte immutable and outside Git.
- Never remove active data or a database before its dated archive and hash manifest exist.
- Retain explicit `60min` downloader support but remove it from all defaults and scheduled commands.
- `RP` means Continuous Euro FX/British Pound, remains Kibot-only, and must never be requested from Schwab as `/RP`.
- Stage and commit only files named by each task; the working tree contains unrelated user changes.
- Do not claim the AmiBroker rebuild is complete until Windows verification proves row counts and research-window coverage.

## Review Focus

- DST transition bars must convert deterministically; reject nonexistent or ambiguous local times rather than silently shifting them. Task 1 tests this.
- Missing ZIP members, changed hashes, malformed rows, and conflicting source duplicates must fail before publication. Tasks 1 and 3 test this.
- Large valid rollover disagreements must select Schwab and remain visible in the conflict report. Task 2 tests this.
- A mid-publication failure must restore both active frequency directories. Task 3 injects this failure.
- AmiBroker bar-cap truncation must fail verification. Task 6 tests the verifier contract and requires Windows evidence.

---

### Task 1: Streaming Kibot Parser and Symbol Map

**Files:**
- Create: `kibot_market_data.py`
- Create: `tests/test_kibot_market_data.py`

**Interfaces:**
- Consumes: a ZIP path, member name, `frequency: Literal["daily", "5min"]`, and acquisition timestamp.
- Produces: `KIBOT_SYMBOL_MAP`, `KIBOT_REQUIRED_SYMBOLS`, `inventory_kibot_zip(...)`, and `read_kibot_member(...) -> pandas.DataFrame` using `download_market_data.CANONICAL_COLUMNS`.

- [ ] **Step 1: Write failing parser tests**

Create small ZIP fixtures. Pin mapping, schema, January/July timezone conversion, daily timestamps, and lineage:

```python
def test_read_intraday_member_maps_symbol_and_converts_eastern(tmp_path):
    archive = write_zip(tmp_path / "intraday.zip", {
        "purchase/EU.txt": (
            "01/15/2020,09:30,1.10,1.11,1.09,1.105,12\n"
            "07/15/2020,09:30,1.12,1.13,1.11,1.125,13\n"
        )
    })
    frame = read_kibot_member(
        archive, "purchase/EU.txt", "5min", "2026-09-22T12:00:00Z"
    )
    assert frame["symbol"].tolist() == ["/6E", "/6E"]
    assert frame["timestamp"].tolist() == [
        "2020-01-15T14:30:00Z", "2020-07-15T13:30:00Z"
    ]
    assert set(frame["source"]) == {"kibot"}
```

Also test all 24 members, `RP -> /RP`, unknown symbols, malformed numbers, impossible OHLC, off-grid minutes, DST anomalies, identical duplicates, and conflicting duplicates.

- [ ] **Step 2: Run tests and confirm failure**

```bash
.venv/bin/python -m pytest -q tests/test_kibot_market_data.py
```

Expected: collection fails because `kibot_market_data` does not exist.

- [ ] **Step 3: Implement strict streaming normalization**

Define:

```python
KIBOT_SYMBOL_MAP = {
    "AD": "6A", "BP": "6B", "CD": "6C", "EU": "6E",
    "JY": "6J", "SF": "6S", "C": "ZC", "S": "ZS",
    "W": "ZW", "FV": "ZF", "TY": "ZN", "TU": "ZT",
    "US": "ZB", "RP": "RP",
}
```

Use `ZipFile.open()` and process one member at a time. Parse wall times with `ZoneInfo("America/Detroit")`, round-trip through UTC to detect invalid local times, require five-minute alignment, and raise `KibotDataError` with archive, member, and row number. Collapse identical duplicates and reject conflicting duplicates.

- [ ] **Step 4: Run Task 1 tests**

```bash
.venv/bin/python -m pytest -q tests/test_kibot_market_data.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kibot_market_data.py tests/test_kibot_market_data.py
git commit -m "feat: parse purchased Kibot market data"
```

### Task 2: Source Precedence and Conflict Audit

**Files:**
- Modify: `kibot_market_data.py`
- Modify: `download_market_data.py`
- Modify: `tests/test_kibot_market_data.py`
- Modify: `tests/test_download_market_data.py`

**Interfaces:**
- Consumes: canonical existing, Kibot, and incoming Schwab frames.
- Produces: `MergeResult`, `validate_price_rows(frame)`, `merge_market_data_sources(frames, reviewed_bars=None)`, and `source_priority(source)` reused by `append_market_data`.

- [ ] **Step 1: Write failing precedence tests**

```python
def test_valid_schwab_price_and_volume_win_and_conflict_is_reported():
    result = merge_market_data_sources([
        frame("kibot", close=100, volume=10),
        frame("schwab", close=112, volume=99),
    ])
    assert result.rows.iloc[0]["close"] == 112
    assert result.rows.iloc[0]["volume"] == 99
    assert result.conflicts.iloc[0]["selected_source"] == "schwab"

def test_invalid_schwab_falls_back_to_kibot():
    result = merge_market_data_sources([
        frame("kibot", open=100, high=102, low=99, close=101),
        frame("schwab", open=100, high=98, low=99, close=101),
    ])
    assert result.rows.iloc[0]["source"] == "kibot"
    assert result.rejections.iloc[0]["reason"] == "invalid_ohlc_envelope"
```

Test priority over Yahoo and legacy rows, manual-review protection, zero Schwab volume, daily keying, large rollover conflicts, and idempotent ordering.

- [ ] **Step 2: Run tests and confirm current ordering fails**

```bash
.venv/bin/python -m pytest -q tests/test_kibot_market_data.py \
  tests/test_download_market_data.py -k 'precedence or fallback or conflict'
```

- [ ] **Step 3: Implement deterministic selection**

Add:

```python
@dataclass(frozen=True)
class MergeResult:
    rows: pd.DataFrame
    conflicts: pd.DataFrame
    rejections: pd.DataFrame
    summary: dict[str, object]
```

Use ranks `schwab=300`, `kibot=200`, other sources `100`, after protecting reviewed keys. Require finite positive OHLC and `low <= min(open, close) <= max(open, close) <= high`; allow null or zero volume. Record both source values for every valid disagreement. Refactor `append_market_data` to use the same selector.

- [ ] **Step 4: Run merge and downloader tests**

```bash
.venv/bin/python -m pytest -q tests/test_kibot_market_data.py tests/test_download_market_data.py
```

Expected: pass, including existing review behavior.

- [ ] **Step 5: Commit**

```bash
git add kibot_market_data.py download_market_data.py \
  tests/test_kibot_market_data.py tests/test_download_market_data.py
git commit -m "feat: enforce market data source precedence"
```

### Task 3: Transactional Import and Archives

**Files:**
- Create: `merge_kibot_market_data.py`
- Modify: `kibot_market_data.py`
- Create: `tests/test_merge_kibot_market_data.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `--daily-zip`, `--intraday-zip`, `--market-data-dir`, `--archive-root`, `--acquired-at`, and optional `--publish`.
- Produces: staged frequency directories, source and archive manifests, coverage/conflict/rejection reports, and recoverable publication.

- [ ] **Step 1: Write failing transaction tests**

Test dry-run staging, SHA-256 hashes, non-Kibot preservation, missing members,
insufficient disk space, archive collisions, no mutation without `--publish`,
verified ZIP movement, 60-minute archive creation, coverage gaps, source-transition
dates, DST outcomes, and rollback:

```python
def test_publish_rolls_back_both_directories_on_second_rename_failure(
    tmp_path, monkeypatch
):
    paths = arrange_active_and_staged_repositories(tmp_path)
    monkeypatch.setattr(paths.transaction, "rename", fail_on_second_rename())
    with pytest.raises(PublishError):
        publish_staged_repository(paths)
    assert read_marker(paths.active_daily) == "old-daily"
    assert read_marker(paths.active_5min) == "old-5min"
```

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/python -m pytest -q tests/test_merge_kibot_market_data.py
```

- [ ] **Step 3: Implement staging and rollback**

Process one symbol/frequency at a time and write via sibling temporary files plus
`os.replace`. Require free space equal to ZIP uncompressed sizes plus active
directory sizes and 20%. Write coverage, unexpected interval gaps, DST parsing,
source-selection, source-transition, conflict, and rejection reports before
publication. Journal directory moves: active to transaction archive, staged to
active, and restore both active directories on any exception. Archive manifests
must contain relative paths, sizes, row counts for CSV files, and SHA-256 hashes.

On `--publish`, copy each ZIP to `data/market_data/source_archives/kibot/2026-09-22`, compare hashes, then remove the original. Move `data/market_data/60min` to `data/market_data_archives/60min_before_kibot_merge_2026-09-22` only after its hash manifest is durable. Refuse an existing destination.

- [ ] **Step 4: Run transaction tests**

```bash
.venv/bin/python -m pytest -q tests/test_kibot_market_data.py \
  tests/test_merge_kibot_market_data.py
```

Expected: pass, including injected rollback.

- [ ] **Step 5: Commit**

```bash
git add .gitignore kibot_market_data.py merge_kibot_market_data.py \
  tests/test_merge_kibot_market_data.py
git commit -m "feat: add transactional Kibot data import"
```

### Task 4: Downloader Defaults and Schwab Overlay

**Files:**
- Modify: `download_market_data.py`
- Modify: `tests/test_download_market_data.py`
- Modify: `docs/help/download_market_data.md`
- Modify: `amibroker_import/README.md`

**Interfaces:**
- Consumes: existing CLI and Task 2 selector.
- Produces: defaults `daily 5min`, explicit `60min` compatibility, and preserved `--export-amibroker` behavior.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_default_frequencies_are_daily_and_5min(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["download_market_data.py", "--quality-only"])
    assert parse_args().frequencies == ["daily", "5min"]

def test_explicit_60min_remains_supported(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "download_market_data.py", "--quality-only", "--frequencies", "60min"
    ])
    assert parse_args().frequencies == ["60min"]
```

Add an integration case proving a future Schwab save preserves older Kibot rows and replaces a valid overlap.

- [ ] **Step 2: Run and confirm default failure**

```bash
.venv/bin/python -m pytest -q tests/test_download_market_data.py \
  -k 'default_frequencies or explicit_60min or kibot'
```

- [ ] **Step 3: Change defaults and documentation**

Change normal defaults from `["daily", "5min", "60min"]` to `["daily", "5min"]`. Retain supported aliases and 30-minute-to-60-minute aggregation for explicit requests. Change all normal and cron examples to `--frequencies daily 5min --export-amibroker`; document explicit 60-minute output as optional and outside the canonical active repository.

Keep the existing daily-versus-intraday comparison diagnostic. Do not enable
automatic replacement of vendor daily bars with calendar-day intraday
aggregates.

- [ ] **Step 4: Run downloader tests**

```bash
.venv/bin/python -m pytest -q tests/test_download_market_data.py
```

- [ ] **Step 5: Commit**

```bash
git add download_market_data.py tests/test_download_market_data.py \
  docs/help/download_market_data.md amibroker_import/README.md
git commit -m "feat: default market downloads to daily and 5-minute"
```

### Task 5: Streaming AmiBroker Export and RP Metadata

**Files:**
- Modify: `export_amibroker_market_data.py`
- Modify: `tests/test_export_amibroker_market_data.py`
- Modify: `amibroker_import/instrument_settings.csv`
- Modify: `download_market_data.py`
- Modify: `tests/test_download_market_data.py`

**Interfaces:**
- Consumes: canonical per-symbol CSVs.
- Produces: the existing AmiBroker files and completion manifest with bounded memory; knows `/RP` without downloading it from Schwab.

- [ ] **Step 1: Write failing streaming and RP tests**

Force small chunks and assert deterministic multi-symbol output, counters, cleanup after failure, and no completion manifest before every output exists. Add:

```python
def test_rp_is_known_but_not_a_default_schwab_download():
    assert FUTURES_PRODUCTS["/RP"]["category"] == "currency"
    assert FUTURES_PRODUCTS["/RP"]["schwab_enabled"] is False
    assert "/RP" not in DEFAULT_SYMBOLS
```

Require instrument ticker `RP`, full name `Continuous Euro FX/British Pound`, round lot 1, and blank point/tick values until contract specifications are verified.

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/python -m pytest -q tests/test_export_amibroker_market_data.py \
  tests/test_download_market_data.py -k 'stream or manifest or rp or instrument'
```

- [ ] **Step 3: Implement bounded-memory export**

Open one temporary output, iterate sorted source paths, read chunks, validate/format, append without repeated headers, and update counters. Preserve the public return structure and atomic final `os.replace`. Add `/RP` with `schwab_enabled=False`; derive `DEFAULT_SYMBOLS` from products not explicitly disabled.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest -q tests/test_export_amibroker_market_data.py \
  tests/test_download_market_data.py
```

- [ ] **Step 5: Commit**

```bash
git add export_amibroker_market_data.py tests/test_export_amibroker_market_data.py \
  amibroker_import/instrument_settings.csv download_market_data.py \
  tests/test_download_market_data.py
git commit -m "feat: stream complete AmiBroker market data exports"
```

### Task 6: AmiBroker Rebuild and Verification Tools

**Files:**
- Create: `amibroker_import/Archive-AmiBroker-Databases.ps1`
- Create: `amibroker_import/Verify-AmiBroker-Databases.ps1`
- Modify: `amibroker_import/Import-MarketData.ps1`
- Modify: `amibroker_import/README.md`
- Create: `tests/test_amibroker_database_rebuild.py`

**Interfaces:**
- Consumes: validated export manifest, generated CSVs, current databases, and archive root.
- Produces: database archive manifests and `amibroker_database_verification.json` with counts/date ranges and PASS/FAIL.

- [ ] **Step 1: Write failing PowerShell contract tests**

Test refusal when Broker is running or an archive exists, hashes before moves, two-directory rollback, merge-summary and export-hash checks, and fixture-driven verification:

```python
def test_verifier_rejects_truncated_intraday_fixture(tmp_path):
    result = run_verifier_fixture(tmp_path, expected_rows=1_100_000,
                                  actual_rows=10_000)
    assert result.returncode != 0
    assert "truncated" in (result.stdout + result.stderr).lower()
```

Require passing fixtures to include ES January and July research sessions and the expected latest date.

- [ ] **Step 2: Run and confirm failure**

```bash
.venv/bin/python -m pytest -q tests/test_amibroker_database_rebuild.py
```

- [ ] **Step 3: Implement archive and import guards**

Archive to `Amibroker/Databases/Archive/Harp_*_before_kibot_merge_2026-09-22`, verify every hash, and roll back the first move if the second fails. Do not use an undocumented database-creation API. Print and save these manual settings:

```text
Create Harp_Daily as a local end-of-day database.
Create Harp_Intraday as a local database with 5-minute base interval,
zero time shift, all-session display, and at least 2,000,000 bars.
```

Make the importer verify PASS merge status and all expected hashes before OLE import.

- [ ] **Step 4: Implement post-import verification**

Load each database, enumerate expected tickers, and compare `Stock.Quotations.Count` plus earliest/latest dates with compact per-symbol expectations emitted by the exporter. Write JSON before returning nonzero on missing symbols, count/range truncation, or missing ES winter/summer evidence.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest -q tests/test_amibroker_database_rebuild.py \
  tests/test_export_amibroker_market_data.py
```

Expected: fixture/static tests pass; live OLE tests skip outside Windows.

- [ ] **Step 6: Commit**

```bash
git add amibroker_import/Archive-AmiBroker-Databases.ps1 \
  amibroker_import/Verify-AmiBroker-Databases.ps1 \
  amibroker_import/Import-MarketData.ps1 amibroker_import/README.md \
  tests/test_amibroker_database_rebuild.py
git commit -m "feat: guard and verify AmiBroker database rebuilds"
```

### Task 7: Execute and Audit the Real Migration

**Files:**
- Create: `docs/kibot_merge_audit_2026-09-22.md`
- Runtime only: `data/market_data/source_archives/kibot/2026-09-22/`
- Runtime only: `data/market_data/quality/kibot_merge_2026-09-22/`
- Runtime only: `data/market_data_archives/60min_before_kibot_merge_2026-09-22/`

**Interfaces:**
- Consumes: the two purchased ZIPs and Tasks 1-5 tooling.
- Produces: published canonical files, source/archive evidence, AmiBroker exports, and a durable audit summary.

- [ ] **Step 1: Stage without publishing**

```bash
.venv/bin/python merge_kibot_market_data.py \
  --daily-zip "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Databases/CSV_Files/Kibot Daily Data - Purchased 2026-09-22.zip" \
  --intraday-zip "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Databases/CSV_Files/Kibot 5 Min Data - Purchased 2026-09-22.zip" \
  --market-data-dir data/market_data \
  --archive-root data/market_data_archives \
  --acquired-at 2026-09-22T12:00:00Z
```

Expected: PASS staging summary and no active/source mutation.

- [ ] **Step 2: Review audit evidence**

Confirm 24 mappings, unique keys, ES coverage from September 2009 through September 2026, valid Schwab selection on overlaps, preserved unmatched history, and visible rollover conflicts.

- [ ] **Step 3: Publish**

Rerun with `--publish`, obtaining filesystem approval because verified ZIP movement removes the originals. Confirm matching hashes, active directory swaps, archived 60-minute data, and no active `60min` directory.

- [ ] **Step 4: Export and validate**

```bash
.venv/bin/python download_market_data.py \
  --quality-only --frequencies daily 5min --export-amibroker
.venv/bin/python validate_market_data.py \
  --frequencies daily 5min --local-only
```

Expected: success and research-period ES rows in the 5-minute export.

- [ ] **Step 5: Write and commit the audit summary**

Record source hashes, archive paths, coverage, rejected counts, overlap selections, conflict counts, commands, and whether Windows verification remains pending.

```bash
git add docs/kibot_merge_audit_2026-09-22.md
git commit -m "docs: record Kibot market data migration audit"
```

### Task 8: Windows Rebuild, Preflight, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `Documents/RUN_PYTHON_TOOLS.md`
- Modify: `docs/help/download_market_data.md`
- Modify: `amibroker_import/README.md`
- Modify: `docs/kibot_merge_audit_2026-09-22.md`

**Interfaces:**
- Consumes: canonical repository, AmiBroker exports, and Task 6 scripts.
- Produces: verified databases, passing timezone preflight, operator instructions, and final evidence.

- [ ] **Step 1: Document the Windows sequence**

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Archive-AmiBroker-Databases.ps1"

# Create both databases with the settings printed by the archive script.

powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Import-MarketData.ps1"

powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Verify-AmiBroker-Databases.ps1"
```

State that no experiment may start until verification is PASS.

- [ ] **Step 2: Rebuild on Windows**

Run the commands, create the settings exactly as printed, and retain verification JSON. If counts differ, increase bar capacity, recreate, reimport, and reverify; never accept a partial database.

- [ ] **Step 3: Run timezone preflight and pilot**

Require zero shift, 5-minute interval, and complete winter/summer ES evidence. Then resume the pilot and confirm the fixed `Start-Process -Wait -PassThru` path completes without using an unset Broker `$LASTEXITCODE`.

- [ ] **Step 4: Run the full suite**

```bash
.venv/bin/python -m pytest -q
git diff --check
git status --short
```

Expected: tests pass; live PowerShell tests skip only where Windows/AmiBroker is unavailable. Inspect remaining changes and keep unrelated work out of commits.

- [ ] **Step 5: Commit final documentation**

```bash
git add README.md Documents/RUN_PYTHON_TOOLS.md \
  docs/help/download_market_data.md amibroker_import/README.md \
  docs/kibot_merge_audit_2026-09-22.md
git commit -m "docs: document merged market data workflow"
```

- [ ] **Step 6: Request whole-branch review**

Have a fresh reviewer compare the branch with the approved spec, inspect real-data and Windows evidence, and confirm unrelated dirty-tree changes did not enter feature commits. Fix correctness issues and rerun focused tests, then the full suite once.
