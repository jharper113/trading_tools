# AmiBroker Experiment Analysis and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analyze completed AmiBroker experiment archives, produce detailed reports and one clear Excel workbook, independently audit the pilot, and create the signed evidence that permits the full-library run.

**Architecture:** A manifest reader admits only hash-valid completed jobs. A normalization module recomputes symbol and sector metrics directly from raw optimization CSVs, while the existing `run_amibroker_analysis.run_analysis()` API continues to create detailed HTML/JSON reports. Separate comparison, workbook, and audit modules keep decision rules, presentation, and verification independent.

**Tech Stack:** Python 3.9+, pandas, openpyxl, pytest, existing AmiBroker analysis modules, standard-library JSON/XML/hash utilities.

**Spec:** `docs/superpowers/specs/2026-09-21-amibroker-strategy-matrix-design.md`

**Prerequisite plan:** `docs/superpowers/plans/2026-09-21-amibroker-experiment-runner.md`

## Global Constraints

- Analyze only jobs whose experiment and run manifests say `COMPLETE` and whose recorded hashes still match.
- Optimization dates are exactly `2009-01-01` through `2019-01-01`.
- Individual pass thresholds are 70% profitable sets, median PF 1.10, and median trades 60 daily or 120 intraday.
- A sector passes only with at least three individually passing symbols and at least 50% of tested symbols passing; zero-trade exports count as tested failures.
- The sector representative is the passing symbol with highest median CAR/MDD over the entire shared grid, including losing combinations.
- Paired optimization comparisons inner-join by symbol and shared parameter coordinates; later trade-series comparisons inner-join by symbol and date.
- A frequent-entry WFA candidate requires a normal optimization pass, at least 25% higher median CAR/MDD, and either PF higher by 0.10 or drawdown lower by 15%.
- Optimization can nominate a frequent-entry variant for WFA; only later WFA can approve the production exception.
- Workbook pass sheets cannot contain failed, interrupted, partial, invalid, or unaudited jobs.
- Full mode unlock requires a pilot audit status of `PASS`, including a supplied manual-versus-automated Windows reference comparison.

## Review Focus

- AmiBroker numeric fields contain commas, percent signs, blanks, or infinity: Task 1 must reject nonfinite ranking data and normalize valid values consistently.
- A zero-trade symbol has many parameter rows: Task 1 must count the symbol as one tested sector failure, without excluding it from sector breadth.
- Native and low-touch jobs use different grids or research windows: Task 3 must label them `NOT COMPARABLE` instead of manufacturing a recommendation.
- A workbook is regenerated after a run archive changes: Tasks 2 and 4 must refuse the hash-invalid job and keep it off all pass sheets.
- The automated pilot agrees internally but lacks the manual Windows reference: Task 5 must return `MANUAL CHECK REQUIRED` and must not write an unlock artifact.

---

## File structure

- Create `amibroker_experiment_results.py`: manifest admission, CSV normalization, independent metrics, and sector gate.
- Create `amibroker_experiment_reports.py`: calls the existing detailed analyzer and records report hashes.
- Create `amibroker_schedule_comparison.py`: native/low-touch shared-grid and shared-date comparisons.
- Create `amibroker_experiment_workbook.py`: six-sheet formatted Excel output.
- Create `analyze_amibroker_experiment.py`: analysis CLI and normalized JSON/CSV output.
- Create `audit_amibroker_experiment.py`: independent pilot verification and unlock artifact.
- Modify `pyproject.toml` and `uv.lock`: add openpyxl.
- Create `tests/fixtures/amibroker_experiment/`: small complete, failed, mismatched-grid, and tampered-run archives.
- Create `tests/test_amibroker_experiment_results.py`: metric and sector tests.
- Create `tests/test_amibroker_schedule_comparison.py`: paired-comparison tests.
- Create `tests/test_amibroker_experiment_workbook.py`: workbook contents and formatting tests.
- Create `tests/test_analyze_amibroker_experiment.py`: end-to-end analyzer tests.
- Create `tests/test_audit_amibroker_experiment.py`: audit and unlock tests.
- Create `docs/amibroker_experiment_analysis.md`: commands and workbook interpretation.

### Task 1: Admit valid jobs and independently normalize optimization results

**Files:**
- Create: `amibroker_experiment_results.py`
- Create: `tests/fixtures/amibroker_experiment/`
- Test: `tests/test_amibroker_experiment_results.py`

**Interfaces:**
- Consumes: `experiment_manifest.json`, its recorded matrix, run manifests, APX files, and optimization CSVs.
- Produces: `load_experiment(path: Path) -> dict`.
- Produces: `admit_jobs(experiment: dict) -> tuple[list[dict], list[dict]]`, where the second list contains normalized failures.
- Produces: `summarize_job(job: dict, profile: dict, groups: dict[str, list[str]]) -> list[dict]`.
- Produces: `evaluate_sectors(records: list[dict]) -> list[dict]`.
- Normalized record keys: `experiment_id`, `job_id`, `strategy`, `periodicity`, `schedule`, `symbol`, `sector`, `parameter_rows`, `eligible_rows`, `profitable_paramsets_pct`, `median_profit_factor`, `median_trades`, `median_car_mdd`, `lower_quartile_car_mdd`, `median_net_profit`, `median_max_drawdown_pct`, `individual_pass`, `sector_pass`, `selected_representative`, `run_path`, and `source_hash`.

- [ ] **Step 1: Write failing admission, metric, and breadth tests**

```python
def test_admission_separates_complete_valid_and_failed_jobs(experiment_fixture):
    experiment = load_experiment(experiment_fixture.manifest)
    admitted, rejected = admit_jobs(experiment)
    assert [job["job_id"] for job in admitted] == ["complete-valid"]
    assert {row["job_id"]: row["reason"] for row in rejected} == {
        "failed": "experiment job status is FAILED",
        "tampered": "run archive hash mismatch: ES.csv",
    }

def test_zero_trade_symbol_counts_as_tested_sector_failure(rows_fixture):
    records = summarize_job(rows_fixture.job, rows_fixture.profile, rows_fixture.groups)
    rates = next(row for row in evaluate_sectors(records) if row["sector"] == "Rates")
    assert rates["tested_count"] == 5
    assert rates["passing_count"] == 2
    assert rates["sector_pass"] is False
```

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_results.py -q`

Expected: collection fails with `ModuleNotFoundError: amibroker_experiment_results`.

- [ ] **Step 3: Implement hash admission and safe numeric parsing**

```python
def parse_number(value: object) -> float:
    text = str(value if value is not None else "").strip().replace(",", "").removesuffix("%")
    if text.upper() in {"", "N/A", "NAN", "INF", "+INF", "-INF"}:
        return math.nan
    value = float(text)
    return value if math.isfinite(value) else math.nan

def verify_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual, expected.lower()):
        raise ValueError(f"hash mismatch: {path.name}")
```

Require exact expected symbol filenames and reject duplicate symbols, mixed schemas, missing required columns, and nonfinite CAR/MDD or zero drawdown when ranking a representative.

- [ ] **Step 4: Implement per-symbol metrics and sector-first selection**

Use every exported parameter row for profitable percentage and CAR/MDD ranking. Rows with zero trades remain in `parameter_rows`; the symbol fails individually and counts once in `tested_count`. Compute median PF and median trades from positive-trade rows. A sector passes when `passing_count >= 3` and `passing_count / tested_count >= 0.5`. Among passing symbols with a common complete grid, choose the highest median CAR/MDD; apply the existing 10% near-tie, execution/liquidity, and lower-quartile rules through the current selection policy.

```python
def sector_status(records: list[dict]) -> tuple[bool, int, int]:
    tested = [row for row in records if row["parameter_rows"] > 0]
    passing = [row for row in tested if row["individual_pass"]]
    passed = len(passing) >= 3 and bool(tested) and len(passing) / len(tested) >= 0.5
    return passed, len(passing), len(tested)
```

- [ ] **Step 5: Add exact-threshold, malformed-number, and full-grid tests**

```python
def test_exact_thresholds_pass():
    result = symbol_record(profitable_pct=70, median_pf=1.10, median_trades=60, periodicity="Daily")
    assert result["individual_pass"] is True

def test_representative_uses_median_of_entire_common_grid(sector_fixture):
    selected = [r["symbol"] for r in evaluate_sectors(sector_fixture) if r["selected_representative"]]
    assert selected == ["ZB"]

@pytest.mark.parametrize("bad", ["", "N/A", "nan", "inf", "-inf"])
def test_nonfinite_car_mdd_cannot_win(bad):
    assert rank_fixture(car_mdd=bad)["selected_representative"] is False
```

- [ ] **Step 6: Run focused tests and commit**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_results.py tests/test_sector_first_selection.py -q`

Expected: all tests pass.

```bash
git add amibroker_experiment_results.py tests/fixtures/amibroker_experiment tests/test_amibroker_experiment_results.py
git commit -m "feat: normalize AmiBroker experiment results"
```

### Task 2: Generate detailed per-run reports without opening browsers

**Files:**
- Create: `amibroker_experiment_reports.py`
- Modify: `run_amibroker_analysis.py`
- Test: `tests/test_analyze_amibroker_experiment.py`

**Interfaces:**
- Consumes: admitted job dictionaries from Task 1.
- Produces: `analyze_job(job: dict, output_root: Path) -> dict` with report paths, SHA-256 values, decision, and exception fields.
- Uses: existing `run_analysis(project_path, results_dir, output_root, ..., research_start="2009-01-01", research_end="2019-01-01")`.
- `run_analysis()` gains optional `open_report: bool = False` only if needed by callers; CLI behavior remains controlled by `--no-open-report`.

- [ ] **Step 1: Write a failing report-generation test**

```python
def test_analyze_job_writes_detailed_reports_inside_run_without_browser(valid_job, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    result = analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")
    assert Path(result["html_path"]).is_file()
    assert Path(result["json_path"]).is_file()
    assert result["json_sha256"] == sha256(Path(result["json_path"]))
    assert opened == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `.venv/bin/python -m pytest tests/test_analyze_amibroker_experiment.py::test_analyze_job_writes_detailed_reports_inside_run_without_browser -q`

Expected: FAIL because `amibroker_experiment_reports` does not exist.

- [ ] **Step 3: Implement the thin report adapter**

Call the Python API directly rather than invoking a subprocess. Pass the matrix job's core symbols when declared; otherwise pass an empty list so symbol diagnostics are still generated. Pass the fixed research dates and record all returned files. Catch an exception into a structured failure but do not alter the run manifest.

```python
def analyze_job(job: dict, output_root: Path) -> dict:
    result, files = run_analysis(
        job["project_path"], job["run_path"], output_root,
        core_symbols=job.get("core_symbols", []),
        research_start="2009-01-01", research_end="2019-01-01",
    )
    return {"job_id": job["job_id"], "decision": result["decision"],
            **{f"{name}_path": str(path) for name, path in files.items()},
            **{f"{name}_sha256": sha256(path) for name, path in files.items()}}
```

- [ ] **Step 4: Preserve CLI browser behavior with a regression test**

```python
def test_library_analysis_never_opens_browser(monkeypatch, valid_job):
    monkeypatch.setattr(webbrowser, "open", lambda *_: pytest.fail("browser opened"))
    analyze_job(valid_job, valid_job["run_path"] / "Analysis_Reports")
```

- [ ] **Step 5: Run focused and existing analyzer tests**

Run: `.venv/bin/python -m pytest tests/test_analyze_amibroker_experiment.py tests/test_generic_amibroker_analysis.py -q`

Expected: all tests pass and the direct CLI still opens unless `--no-open-report` is supplied.

- [ ] **Step 6: Commit the report adapter**

```bash
git add amibroker_experiment_reports.py run_amibroker_analysis.py tests/test_analyze_amibroker_experiment.py
git commit -m "feat: analyze completed AmiBroker jobs"
```

### Task 3: Native versus low-touch comparisons

**Files:**
- Create: `amibroker_schedule_comparison.py`
- Test: `tests/test_amibroker_schedule_comparison.py`

**Interfaces:**
- Produces: `compare_optimization_pair(native_rows: list[dict], low_touch_rows: list[dict], metadata: dict) -> list[dict]`.
- Produces: `inner_join_daily_series(native: pd.DataFrame, low_touch: pd.DataFrame) -> pd.DataFrame` keyed by normalized `Symbol` and `Date`.
- Produces comparison keys: `strategy`, `symbol`, `native_job`, `low_touch_job`, `shared_grid_rows`, `grid_coverage`, `native_median_car_mdd`, `low_touch_median_car_mdd`, `relative_car_mdd_improvement`, `profit_factor_improvement`, `drawdown_reduction`, `recommendation`, and `reason`.

- [ ] **Step 1: Write failing shared-grid and material-advantage tests**

```python
def test_native_candidate_requires_car_mdd_and_secondary_improvement(pair_fixture):
    result = compare_optimization_pair(
        pair_fixture.native(car_mdd=.75, pf=1.35, drawdown=18),
        pair_fixture.low_touch(car_mdd=.60, pf=1.20, drawdown=20),
        pair_fixture.metadata(sector_pass=True),
    )[0]
    assert result["relative_car_mdd_improvement"] == pytest.approx(.25)
    assert result["recommendation"] == "FREQUENT_ENTRY_WFA_CANDIDATE"

def test_mismatched_grid_is_not_comparable(pair_fixture):
    result = compare_optimization_pair(pair_fixture.native(grid=(1, 2)), pair_fixture.low_touch(grid=(1,)), pair_fixture.metadata())[0]
    assert result["recommendation"] == "NOT COMPARABLE"
```

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_schedule_comparison.py -q`

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement exact shared-grid comparison**

Derive shared parameter names from columns beginning with `Opt `. Key rows by normalized symbol and the tuple of shared parameter values. Require identical declared research dates and full coverage of both grids on shared dimensions. Use positive finite low-touch CAR/MDD as the denominator; otherwise return `NOT COMPARABLE`. Prefer `LOW_TOUCH` unless the native job passes its individual and sector gates and clears both material-improvement layers.

```python
material = (
    metadata["native_individual_pass"] and metadata["native_sector_pass"]
    and relative_car_mdd >= 0.25
    and (profit_factor_improvement >= 0.10 or drawdown_reduction >= 0.15)
)
recommendation = "FREQUENT_ENTRY_WFA_CANDIDATE" if material else "LOW_TOUCH"
```

- [ ] **Step 4: Implement common-date trade-series join**

```python
def inner_join_daily_series(native: pd.DataFrame, low_touch: pd.DataFrame) -> pd.DataFrame:
    keys = ["Symbol", "Date"]
    left = normalize_daily(native).rename(columns={"Return": "NativeReturn"})
    right = normalize_daily(low_touch).rename(columns={"Return": "LowTouchReturn"})
    return left.merge(right, on=keys, how="inner", validate="one_to_one").sort_values(keys)
```

Reject duplicate symbol/date keys and report the first/last common date and common observation count.

- [ ] **Step 5: Add boundary and inner-join tests**

```python
@pytest.mark.parametrize("car,pf,dd,expected", [
    (.2499, .10, .15, "LOW_TOUCH"),
    (.25, .0999, .15, "FREQUENT_ENTRY_WFA_CANDIDATE"),
    (.25, .10, .1499, "FREQUENT_ENTRY_WFA_CANDIDATE"),
])
def test_material_threshold_boundaries(car, pf, dd, expected, comparison_from_improvements):
    assert comparison_from_improvements(car, pf, dd)["recommendation"] == expected

def test_daily_comparison_keeps_only_common_symbol_dates():
    joined = inner_join_daily_series(native_frame(), low_touch_frame())
    assert list(joined[["Symbol", "Date"]].itertuples(index=False, name=None)) == [("ES", pd.Timestamp("2020-01-03"))]
```

- [ ] **Step 6: Run focused tests and commit**

Run: `.venv/bin/python -m pytest tests/test_amibroker_schedule_comparison.py -q`

Expected: all tests pass.

```bash
git add amibroker_schedule_comparison.py tests/test_amibroker_schedule_comparison.py
git commit -m "feat: compare AmiBroker entry schedules"
```

### Task 4: Experiment CLI and six-sheet Excel workbook

**Files:**
- Create: `amibroker_experiment_workbook.py`
- Create: `analyze_amibroker_experiment.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/test_amibroker_experiment_workbook.py`
- Modify: `tests/test_analyze_amibroker_experiment.py`

**Interfaces:**
- Command: `.venv/bin/python analyze_amibroker_experiment.py <experiment_manifest.json> [--output-dir <path>]`.
- Produces: `experiment_summary.json`, `all_symbol_results.csv`, `schedule_comparisons.csv`, and `<experiment-id>_Optimization_Review.xlsx`.
- Produces: `write_workbook(summary: dict, destination: Path) -> Path`.
- Workbook sheets: `Passed Candidates`, `Low-Touch Recommendations`, `Frequent-Entry Exceptions`, `All Symbol Results`, `Failed or Incomplete`, and `Experiment Summary`.

- [ ] **Step 1: Add openpyxl as the single workbook dependency**

Run: `uv add 'openpyxl>=3.1.5'`

Expected: `pyproject.toml` and `uv.lock` contain openpyxl, and `.venv/bin/python -c 'import openpyxl'` succeeds.

- [ ] **Step 2: Write failing workbook safety and sheet tests**

```python
def test_workbook_has_exact_sheets_and_excludes_invalid_jobs(workbook_summary, tmp_path):
    path = write_workbook(workbook_summary, tmp_path / "review.xlsx")
    book = openpyxl.load_workbook(path, data_only=False)
    assert book.sheetnames == [
        "Passed Candidates", "Low-Touch Recommendations", "Frequent-Entry Exceptions",
        "All Symbol Results", "Failed or Incomplete", "Experiment Summary",
    ]
    passed = list(book["Passed Candidates"].values)
    assert "tampered-job" not in repr(passed)
    assert "tampered-job" in repr(list(book["Failed or Incomplete"].values))
```

- [ ] **Step 3: Run tests and verify workbook modules are missing**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_workbook.py -q`

Expected: FAIL because the workbook module does not exist.

- [ ] **Step 4: Implement workbook tables, styles, links, and filters**

Use `openpyxl.Workbook`. Remove the default sheet, create sheets in the asserted order, append stable column lists, apply bold colored headers, freeze `A2`, enable `auto_filter`, set numeric formats, and add green/yellow/red conditional fills for pass/candidate/failure decisions. Add clickable `file:///` hyperlinks for detailed HTML and JSON paths while retaining the plain path value.

```python
def prepare_sheet(book, title: str, columns: list[str], rows: list[dict]):
    sheet = book.create_sheet(title)
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(column) for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    return sheet
```

- [ ] **Step 5: Implement the analysis CLI and atomic outputs**

Load and admit jobs, run detailed reports, normalize metrics, evaluate sectors, compare declared pairs, and write JSON/CSV/workbook to a temporary analysis directory. Rename it only after all four outputs close successfully. Return nonzero if the workbook fails; retain already valid per-run reports and record the exception in the temporary summary.

```python
temporary = output_dir.with_name(output_dir.name + ".tmp")
write_json(temporary / "experiment_summary.json", summary)
pd.DataFrame(summary["all_symbol_results"]).to_csv(temporary / "all_symbol_results.csv", index=False)
pd.DataFrame(summary["schedule_comparisons"]).to_csv(temporary / "schedule_comparisons.csv", index=False)
write_workbook(summary, temporary / workbook_name)
temporary.replace(output_dir)
```

- [ ] **Step 6: Add CLI rerun and corrupted-archive tests**

```python
def test_cli_is_rerunnable_and_never_promotes_corrupt_job(experiment_fixture, tmp_path):
    first = run_cli(experiment_fixture.manifest, tmp_path)
    second = run_cli(experiment_fixture.manifest, tmp_path)
    assert first.returncode == second.returncode == 0
    summary = json.loads((tmp_path / "experiment_summary.json").read_text())
    assert summary["job_counts"]["invalid"] == 1
    assert all(row["job_id"] != "tampered" for row in summary["passed_candidates"])
```

- [ ] **Step 7: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_workbook.py tests/test_analyze_amibroker_experiment.py -q`

Expected: all tests pass and the workbook reopens cleanly with openpyxl.

- [ ] **Step 8: Commit the analyzer and workbook**

```bash
git add amibroker_experiment_workbook.py analyze_amibroker_experiment.py pyproject.toml uv.lock tests/test_amibroker_experiment_workbook.py tests/test_analyze_amibroker_experiment.py
git commit -m "feat: create AmiBroker experiment workbook"
```

### Task 5: Independent pilot audit and full-mode unlock

**Files:**
- Create: `audit_amibroker_experiment.py`
- Create: `tests/test_audit_amibroker_experiment.py`

**Interfaces:**
- Command: `.venv/bin/python audit_amibroker_experiment.py <experiment_manifest.json> --analysis <experiment_summary.json> [--reference-run <manual_reference.json>]`.
- Produces: `pilot_audit.json`, `pilot_audit.md`, and only on full success `full_run_unlock.json`.
- Audit status is exactly `PASS`, `FAIL`, or `MANUAL CHECK REQUIRED`.
- Manual reference JSON supplies `automated_job_id`, manual APX/AFL/export paths and hashes, and a per-symbol export map generated with identical inputs.

- [ ] **Step 1: Write failing audit-status tests**

```python
def test_internal_success_without_reference_requires_manual_check(audit_fixture):
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis)
    report = json.loads(result.json_path.read_text())
    assert report["status"] == "MANUAL CHECK REQUIRED"
    assert not result.unlock_path.exists()

def test_matching_reference_produces_hash_bound_unlock(audit_fixture):
    result = run_audit(audit_fixture.experiment, audit_fixture.analysis, audit_fixture.reference)
    report = json.loads(result.json_path.read_text())
    unlock = json.loads(result.unlock_path.read_text())
    assert report["status"] == "PASS"
    assert unlock["pilot_experiment_id"] == report["experiment_id"]
    assert unlock["audit_sha256"] == sha256(result.json_path)
```

- [ ] **Step 2: Run tests and verify the audit module is missing**

Run: `.venv/bin/python -m pytest tests/test_audit_amibroker_experiment.py -q`

Expected: FAIL because `audit_amibroker_experiment.py` does not exist.

- [ ] **Step 3: Implement independent recomputation and provenance checks**

Do not import workbook rows as authoritative calculations. Re-read raw CSVs and recompute counts, decisions, profitable percentage, medians, sector breadth, representative, and comparison labels. Compare counts and strings exactly. Compare numeric JSON and underlying workbook cells with absolute tolerance `1e-9`. Validate APX formula, interval, dates, costs, sizing, symbols, matrix hash, policies, exports, and report hashes.

```python
def equal_number(actual: object, expected: object) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-9)

checks.append(check_equal("matrix_sha256", sha256(matrix_path), experiment["matrix_sha256"]))
checks.extend(compare_recomputed_records(raw_records, summary["all_symbol_results"], equal_number))
```

- [ ] **Step 4: Implement entry-time and paired-coverage audit**

Read each archived `entry_audit/<symbol>.csv`. Require low-touch 0063 entries to have `TimeNum <= 110000`. Require fixed-signal 0084 entries to occur at 11:15 on 15-minute bars because its 11:00 close signal enters on the next bar. Record native 0063 entry-time distributions without imposing a cutoff. Require paired optimization rows to have full common-grid coverage and identical declared dates.

```python
if job["adapter"] == "entry_cutoff_110000" and (entries["TimeNum"] > 110000).any():
    fail(job, "low-touch entry after 11:00 Eastern")
if job["adapter"] == "fixed_110000" and not entries["TimeNum"].eq(111500).all():
    fail(job, "11:00 close signal did not enter on the 11:15 bar")
```

- [ ] **Step 5: Implement manual-reference equality and unlock sealing**

For the selected reference job, require matching APX control fields, formula hash, expected symbols, CSV schemas, parameter coordinates, and every exported metric cell after numeric normalization. On any mismatch return `FAIL`. With no reference return `MANUAL CHECK REQUIRED`. Write the unlock only after saving `pilot_audit.json`; bind it to the experiment ID, matrix hash, audit hash, and status `PASS`.

```python
status = "FAIL" if failures else "PASS" if reference is not None else "MANUAL CHECK REQUIRED"
write_json(audit_path, {"status": status, "checks": checks, "failures": failures})
if status == "PASS":
    write_json(unlock_path, {"audit_status": "PASS", "pilot_experiment_id": experiment_id,
                             "matrix_sha256": matrix_hash, "audit_path": str(audit_path),
                             "audit_sha256": sha256(audit_path)})
```

- [ ] **Step 6: Add tamper, entry-time, metric, and reference mismatch tests**

```python
@pytest.mark.parametrize("fault", [
    "apx_date", "cost", "source_hash", "missing_csv", "metric_cell",
    "late_low_touch_entry", "fixed_entry_wrong_bar", "reference_difference",
])
def test_any_material_audit_fault_blocks_unlock(audit_fixture, fault):
    result = run_audit(audit_fixture.with_fault(fault), reference=audit_fixture.reference)
    assert json.loads(result.json_path.read_text())["status"] == "FAIL"
    assert not result.unlock_path.exists()
```

- [ ] **Step 7: Run focused tests and commit**

Run: `.venv/bin/python -m pytest tests/test_audit_amibroker_experiment.py -q`

Expected: all tests pass.

```bash
git add audit_amibroker_experiment.py tests/test_audit_amibroker_experiment.py
git commit -m "feat: audit AmiBroker pilot experiments"
```

### Task 6: Analysis guide and complete validation

**Files:**
- Create: `docs/amibroker_experiment_analysis.md`
- Modify: `README.md`

**Interfaces:**
- Documents analysis, audit, manual reference, workbook interpretation, and unlock/full-run sequence.
- Produces no new runtime API.

- [ ] **Step 1: Write exact Linux analysis and audit commands**

```bash
EXPERIMENT="/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Reports/Experiments/<experiment-id>/experiment_manifest.json"
.venv/bin/python analyze_amibroker_experiment.py "$EXPERIMENT"
.venv/bin/python audit_amibroker_experiment.py "$EXPERIMENT" \
  --analysis "$(dirname "$EXPERIMENT")/Analysis/experiment_summary.json" \
  --reference-run "$(dirname "$EXPERIMENT")/manual_reference.json"
```

Explain each worksheet in one sentence, state that frequent-entry rows are WFA candidates rather than production approvals, and show where detailed HTML reports live.

- [ ] **Step 2: Document the full-mode unlock command**

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Mode full `
  -Unlock "Z:\04_Code\Amibroker\Reports\Experiments\<pilot-id>\Analysis\full_run_unlock.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

- [ ] **Step 3: Run all new analysis tests**

Run: `.venv/bin/python -m pytest tests/test_amibroker_experiment_results.py tests/test_analyze_amibroker_experiment.py tests/test_amibroker_schedule_comparison.py tests/test_amibroker_experiment_workbook.py tests/test_audit_amibroker_experiment.py -q`

Expected: all tests pass.

- [ ] **Step 4: Run repository-wide validation**

Run: `.venv/bin/python -m pytest -q`

Expected: the complete existing and new test suite passes.

- [ ] **Step 5: Exercise the fixture workflow end to end**

Run:

```bash
.venv/bin/python analyze_amibroker_experiment.py tests/fixtures/amibroker_experiment/complete/experiment_manifest.json --output-dir /tmp/amibroker-experiment-analysis
.venv/bin/python audit_amibroker_experiment.py tests/fixtures/amibroker_experiment/complete/experiment_manifest.json --analysis /tmp/amibroker-experiment-analysis/experiment_summary.json --reference-run tests/fixtures/amibroker_experiment/complete/manual_reference.json
```

Expected: analyzer exit `0`, audit status `PASS`, a reopenable six-sheet workbook, and a hash-bound unlock JSON.

- [ ] **Step 6: Commit documentation**

```bash
git add docs/amibroker_experiment_analysis.md README.md
git commit -m "docs: explain AmiBroker experiment analysis"
```

## Analysis completion boundary

Implementation is complete after all fixture and repository tests pass. Operational acceptance requires running the real five-job Windows pilot, manually reproducing one declared reference job, running this audit, and obtaining `PASS`. Only then may the full-library runner accept the generated unlock artifact.
