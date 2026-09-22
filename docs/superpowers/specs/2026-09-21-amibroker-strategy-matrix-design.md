# AmiBroker Strategy Matrix Automation Design

**Date:** 2026-09-21  
**Status:** Draft for review  
**Scope:** Three-strategy optimization pilot, audit, and controlled expansion to the full strategy library

## Purpose

Automate repeatable AmiBroker optimization experiments without losing the evidence behind each result. The system will run a small, audited pilot first. After the pilot is verified, the same interfaces can run the complete AFL strategy library across selected periodicities and entry schedules.

The workflow has two separately runnable stages:

1. A Windows runner controls AmiBroker and archives raw results.
2. A Python analyzer validates completed runs, applies the existing optimization gates, compares paired timing variants, and writes a single Excel workbook.

This separation lets a long AmiBroker session finish independently. Analysis can be rerun later without repeating AmiBroker work.

## Success criteria

The pilot succeeds when:

- all five planned jobs produce isolated, complete archives;
- the stored APX, AFL, policy, dates, symbols, periodicity, sizing, costs, and exports are traceable by hash;
- the live intraday database passes the Eastern-time preflight;
- independently recomputed symbol statistics agree with the normal analysis JSON and workbook;
- a stopped run resumes without rerunning completed jobs or treating partial jobs as complete;
- failed or incomplete jobs cannot appear on a passing-strategy worksheet;
- timing variants are compared over common symbols and common dates; and
- the workbook clearly identifies optimization candidates, low-touch recommendations, and any frequent-entry exception.

The full 205-strategy library remains disabled until the pilot audit passes.

## Research policy

All jobs use the shared AFL research policy rather than strategy-specific sizing or dates. The optimization period is 2009-01-01 through 2019-01-01. WFA is a later stage and is not run by this pilot.

An optimization symbol passes individually when:

- at least 70% of parameter sets are profitable;
- median profit factor is at least 1.10; and
- median trades is at least 60 for daily jobs or 120 for intraday jobs, including 15-minute and 60-minute jobs.

A sector passes when at least three tested symbols pass individually and those passing symbols represent at least 50% of the tested sector. Zero-trade symbols count as tested failures. If a sector passes, the highest median CAR/MDD symbol advances, subject to the existing near-tie, liquidity, and execution rules documented in `docs/promotion_criteria.md`.

The initial optimization report does not grant a frequent-entry exception. It only records whether a native schedule is promising enough to retain for WFA. A native schedule can earn a production exception only after WFA when, on common dates and symbols:

- it passes the normal sector and WFA gates;
- median CAR/MDD improves by at least 25% relative to the low-touch version;
- median profit factor improves by at least 0.10 or maximum drawdown improves by at least 15%;
- the advantage appears across multiple symbols and WFA windows; and
- the result survives the configured cost stress.

Overnight manual entry is never a supported schedule. A conditional order staged earlier may trigger overnight when the strategy and broker order type support it.

## Pilot matrix

The versioned matrix contains these five jobs:

| Job | Strategy | Bars | Entry schedule | Purpose |
|---|---|---:|---|---|
| 1 | `0001_exhaustion_open_dislocation_fade` | Daily | Native | Validate the ordinary daily path |
| 2 | `0063_intraday_opening_range_breakout` | 15 min | Native | Preserve the strategy's unconstrained research behavior |
| 3 | `0063_intraday_opening_range_breakout` | 15 min | Low-touch | Permit new entries only through 11:00 Eastern; preserve exits |
| 4 | `0063_intraday_opening_range_breakout` | 60 min | Native | Test whether lower monitoring frequency retains the edge |
| 5 | `0084_ES_INTRADAY_PCTB` | 15 min | Fixed 11:00 signal | Validate an existing scheduled-entry strategy |

The generated low-touch copy changes only entry eligibility. It must not move stop logic, exit logic, position sizing, costs, or the underlying signal calculation. The 60-minute job uses AmiBroker compression of the maintained 5-minute intraday database.

The matrix is data, not executable code. Each entry declares a stable job ID, canonical AFL, database, interval, project template, symbol universe, entry-schedule adapter, analysis profile, and enabled state. A top-level `pilot` mode selects only these five jobs. A later `full` mode selects all enabled catalog jobs.

## Components

### Strategy catalog and test matrix

The catalog inventories every AFL in `2026 Strategy Testing`, its native timeframe, supported alternative timeframes, and whether a timing adapter is available. The first release recognizes `Daily`, `15m`, and `60m`.

The test matrix points to catalog entries and defines experiments. It does not edit canonical AFL files. Generated variants live inside the experiment archive and include a provenance header naming the canonical file, source hash, adapter, matrix version, and generation time.

Pilot adapters use exact, reviewed anchors and fail closed if an expected AFL fragment is absent or occurs more than once. This prevents a text edit from silently changing the wrong strategy logic. Full-library expansion requires either a declared reusable timing interface in the strategy or a reviewed strategy-specific adapter.

### Windows AmiBroker runner

A PowerShell controller performs the following sequence:

1. Load and validate the matrix.
2. Create a unique experiment directory and durable experiment manifest.
3. Run the blocking intraday timezone preflight when any intraday job is selected.
4. For each pending job, generate a job-specific AFL, APX, ABB, and archive sidecar.
5. Invoke AmiBroker with its documented batch interface and wait for completion.
6. Validate expected CSV exports, snapshot inputs, calculate SHA-256 hashes, and atomically mark the job complete.
7. Continue to the next job, recording a failure without publishing partial results as complete.

Jobs run sequentially in one AmiBroker instance. This avoids shared project, database, and export-folder races. The runner reuses the existing run archive layout under:

```text
Reports/Runs/<strategy>/<periodicity>/Optimization/<run-id>/
```

The experiment manifest links those job archives under one experiment ID. It is updated atomically after every state transition.

Job states are `PENDING`, `RUNNING`, `COMPLETE`, `FAILED`, and `INTERRUPTED`. On restart, `COMPLETE` jobs with valid hashes are skipped. A prior `RUNNING` job becomes `INTERRUPTED` and may be rerun into a new run directory; the partial archive remains available for inspection. `FAILED` and `INTERRUPTED` jobs never qualify for analysis.

### Eastern-time preflight

The maintained import pipeline converts UTC timestamps using `America/Detroit` daylight-saving rules. Its documentation also requires zero additional AmiBroker database shift. The runner will verify the live database rather than assume those settings are still correct.

Before the first intraday job, a diagnostic AFL exports:

- `Status("timeshift")`;
- `Interval()`;
- and ES `DateTime()` and `TimeNum()` samples from the 5-minute database.

The runner records the selected database path from the job configuration alongside that export.

The preflight requires:

- a time shift of zero seconds;
- a 5-minute base interval;
- an ES 09:30 bar and a 15:55 bar on sampled complete sessions; and
- the same local session alignment on sampled winter and daylight-saving dates within the research period.

The manifest records `America/Detroit`, the samples, and the diagnostic export hash. Any failure stops all intraday jobs before optimization. Daily jobs may run only when explicitly requested with a `continue_daily_after_intraday_preflight_failure` option; the default pilot behavior is to stop the whole experiment.

### Python experiment analyzer

A new experiment-level Python command reads the experiment manifest. For every hash-valid `COMPLETE` job, it calls the existing `run_amibroker_analysis.run_analysis()` API with browser opening disabled. Existing per-run HTML, JSON, selection plan, decision, and manifest files remain the authoritative detailed reports.

The experiment analyzer adds a normalized summary record for each strategy, periodicity, schedule, sector, and symbol. Required fields include:

- experiment and job IDs;
- strategy and source hash;
- periodicity and entry schedule;
- symbol and sector;
- profitable parameter-set percentage;
- median profit factor, trades, CAR/MDD, net profit, and maximum drawdown;
- individual, sector, and overall decision;
- run/report paths and hashes; and
- failure or exclusion reason.

The analyzer may be rerun without modifying AmiBroker archives. Its own output is versioned by analysis-policy and matrix hashes.

### Excel workbook

The experiment analyzer writes one formatted `.xlsx` workbook with these worksheets:

1. **Passed Candidates** — symbols whose individual and sector gates pass, with strategy, periodicity, schedule, median PF, trades, and CAR/MDD.
2. **Low-Touch Recommendations** — paired native and low-touch results on common symbols and dates, with a clear preferred schedule.
3. **Frequent-Entry Exceptions** — native schedules retained as WFA candidates because the optimization evidence could justify later exception testing; this sheet explicitly says final approval requires WFA.
4. **All Symbol Results** — every normalized symbol result and gate field.
5. **Failed or Incomplete** — missing exports, invalid hashes, preflight failures, analysis failures, and interrupted jobs.
6. **Experiment Summary** — matrix version, policy hashes, research dates, timezone preflight result, job counts, audit status, and links to detailed reports.

Filters, frozen headers, readable numeric formats, and conditional colors make the workbook easy to scan. The workbook does not replace the existing HTML diagnostics and heat maps.

## Paired comparison rules

Optimization comparisons require the same declared research window and use an inner join on symbol. Later WFA or best-estimate trade-set comparisons use an inner join on symbol and trading date, so both variants are measured over the same realized time series. A pair is `NOT COMPARABLE` if either job is incomplete, source strategy identity differs, non-schedule policy differs, or common coverage is inadequate.

For optimization summaries, the analyzer compares the same symbol and shared parameter dimensions. If adapters create different parameter grids, it compares only shared combinations and reports coverage. A recommendation requires full shared-grid coverage for the dimensions common to both jobs.

The low-touch version is preferred by default when it passes the same gates and the native version does not show a material advantage. A native version remains visible as a frequent-entry WFA candidate when it passes the normal optimization gate, median CAR/MDD is at least 25% better, and at least one of these is also true:

- median PF is at least 0.10 better;
- maximum drawdown is at least 15% lower.

This optimization label preserves promising research. It does not waive the WFA requirements or authorize production use.

## Audit and expansion gate

The pilot audit is a separate command and artifact. It checks:

- hashes for generated AFL, APX, ABB, matrix, policies, exports, and reports;
- APX dates, mode, formula, database, interval, symbol list, costs, sizing, and trade settings against declared values;
- CSV file counts, symbol coverage, nonempty files, schema, duplicates, and mixed optimization/WFA output;
- independent recomputation of profitable-set percentage, median PF, median trades, median CAR/MDD, net profit, and maximum drawdown from raw exports;
- exact agreement for counts and decisions, and numeric agreement within `1e-9`, with per-run JSON and underlying workbook cells;
- entry-time samples for each intraday schedule;
- common-date and common-symbol coverage for paired comparisons;
- interruption and resume behavior;
- proof that failed, partial, or hash-invalid jobs do not enter passing sheets; and
- one Windows reference job comparing manual AmiBroker execution with automated execution using identical inputs.

The audit writes machine-readable JSON plus a short human-readable Markdown report. Its final state is `PASS`, `FAIL`, or `MANUAL CHECK REQUIRED`.

Full-library mode is locked unless the pilot audit is `PASS`, including the Windows reference comparison. Unlocking is recorded in a small approval artifact containing the pilot experiment ID and audit hash. Changing shared dates, sizing, costs, matrix schema, generation logic, or audit rules invalidates that approval and requires a new pilot. Adding ordinary catalog entries after the interface is stable does not invalidate it.

## Error handling

- Invalid matrix or duplicate job IDs: stop before AmiBroker starts.
- Generated AFL anchor mismatch: fail that job before AmiBroker starts.
- Timezone or database mismatch: stop the pilot before any optimization.
- AmiBroker nonzero exit, timeout, or missing exports: retain evidence and mark the job failed.
- Export schema mismatch or mixed result types: quarantine from aggregation.
- Hash mismatch: treat the job as invalid until explicitly rerun.
- Analyzer exception: record it on **Failed or Incomplete** and continue analyzing other valid jobs.
- Workbook write failure: keep normalized JSON/CSV outputs and return a nonzero process status.

No automatic cleanup deletes experiment inputs, partial results, or completed archives.

## Testing strategy

Python unit tests cover matrix validation, catalog discovery, state transitions, metric normalization, gate aggregation, paired inner joins, frequent-entry labeling, workbook contents, and expansion-lock invalidation.

PowerShell integration tests use fixture exports and a fake AmiBroker executable to cover command construction, atomic manifests, success, failure, interruption, and resume. Existing archive tests remain in place.

AFL generation tests compare expected pilot variants, verify exactly one reviewed replacement, and assert that shared policy and all unrelated signal/exit text remain unchanged.

The live Windows pilot is the final acceptance test because Linux tests cannot prove AmiBroker database settings, OLE behavior, or batch equivalence. The audit must pass before full-library execution is enabled.

## Deliverables

- Versioned strategy catalog and pilot/full matrix.
- PowerShell experiment runner and timezone preflight.
- Safe pilot AFL/APX/ABB generation.
- Python experiment analyzer and Excel workbook.
- Pilot audit command and reports.
- Automated tests and concise operating documentation.
- A locked full-library mode that becomes available only after a passing pilot audit.

WFA batch automation is intentionally outside this implementation. The workbook identifies candidates for the existing WFA workflow, and the frequent-entry exception is finalized only after later WFA evidence exists.
