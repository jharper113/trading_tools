# AmiBroker experiment analysis and audit

Run the analyzer after the pilot controller finishes. It admits only `COMPLETE`
jobs whose archived files still match their recorded SHA-256 hashes, creates the
detailed per-job HTML reports, and writes one review workbook.

```bash
EXPERIMENT="/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Reports/Experiments/<experiment-id>/experiment_manifest.json"
.venv/bin/python analyze_amibroker_experiment.py "$EXPERIMENT"
```

The analyzer writes `Analysis/experiment_summary.json`, two CSV extracts, and
`<experiment-id>_Optimization_Review.xlsx`. Detailed HTML and JSON reports stay
inside each archived run's `Analysis_Reports` directory.

On Linux, recorded `Z:\...` paths map by default to
`/home/jon/Dropbox/HarpFolders/...`. Set `AMIBROKER_WINDOWS_ROOT` if the Dropbox
mirror is mounted elsewhere.

The workbook contains six sheets:

- **Passed Candidates** lists the sector representatives selected for WFA.
- **Low-Touch Recommendations** lists paired tests where the restricted entry schedule remains preferred.
- **Frequent-Entry Exceptions** lists native schedules that earned WFA testing through material improvement; these are WFA candidates, not production approvals.
- **All Symbol Results** contains every admitted symbol and its gate metrics.
- **Failed or Incomplete** explains jobs rejected for status, missing files, or hash failures.
- **Experiment Summary** gives the experiment ID and compact job and decision counts.

Before authorizing the full strategy library, reproduce one declared pilot job
manually in AmiBroker with the same APX controls, AFL, symbols, parameter grid,
and research dates. Save its APX, AFL, and per-symbol exports, calculate their
SHA-256 hashes, and describe them in `manual_reference.json`:

```json
{
  "automated_job_id": "pilot-0063-15m-native",
  "apx_path": "/path/to/manual/project.apx",
  "apx_sha256": "...",
  "formula_path": "/path/to/manual/formula.afl",
  "formula_sha256": "...",
  "exports": {
    "ES": {"path": "/path/to/manual/ES.csv", "sha256": "..."}
  }
}
```

Run the independent audit from Linux:

```bash
.venv/bin/python audit_amibroker_experiment.py "$EXPERIMENT" \
  --analysis "$(dirname "$EXPERIMENT")/Analysis/experiment_summary.json" \
  --reference-run "$(dirname "$EXPERIMENT")/manual_reference.json"
```

The audit re-reads the archived optimization and entry-audit CSVs. It checks
provenance, APX dates and controls, sector and symbol decisions, paired-grid
coverage, workbook values, entry times, and every manual-reference metric. With
no manual reference, its status is `MANUAL CHECK REQUIRED`. Any mismatch is
`FAIL`. Only `PASS` creates `full_run_unlock.json` and the sealed
`full_strategy_test_matrix.json`.

Use both generated files for the full Windows run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\Run-AmiBroker-Experiment.ps1" `
  -Mode full `
  -Matrix "Z:\04_Code\Amibroker\Reports\Experiments\<pilot-id>\Analysis\full_strategy_test_matrix.json" `
  -Unlock "Z:\04_Code\Amibroker\Reports\Experiments\<pilot-id>\Analysis\full_run_unlock.json" `
  -Broker "C:\Program Files (x86)\AmiBroker\Broker.exe" `
  -ReportsRoot "Z:\04_Code\Amibroker\Reports"
```

The unlock is valid only for the exact expanded matrix, passing audit, analysis
profiles, project templates, policies, and runner/analyzer code hashes. Full-run
resume revalidates the stored authorization before continuing.
Run the real five-job Windows pilot and obtain an audit `PASS` before starting
the full library.
