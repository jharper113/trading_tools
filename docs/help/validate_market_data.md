# validate_market_data.py

Validate locally stored futures market data against Yahoo Finance continuous futures reference data.

## Typical Uses

Validate the full default futures universe and write static reports:

```bash
python -u validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min 60min \
  --threshold-pct 0.25
```

Serve the dashboard locally so selected decisions can be applied directly:

```bash
python -u validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min 60min \
  --threshold-pct 0.25 \
  --serve-dashboard
```

Automatically apply non-review auto decisions and regenerate the post-apply dashboard:

```bash
python -u validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min 60min \
  --auto-apply-decisions \
  --review-new-only
```

## Outputs

Validation reports are written under `output/market_data_validation/` by default:

- `validation_summary.csv`
- `price_differences.csv`
- `missing_bars.csv`
- `duplicate_bars.csv`
- `auto_review_decisions.csv`
- `auto_review_applied_decisions.csv`
- `timezone_alignment.csv`
- `market_data_difference_heatmap.png`
- `validation_dashboard.html`

Validated and repaired market data is stored in `data/market_data/<frequency>/<symbol>.csv`, for example `data/market_data/daily/ES.csv`, `data/market_data/5min/ES.csv`, and `data/market_data/60min/ES.csv`. These normalized CSV files are the files to import into AmiBroker.

Applied decisions update `data/market_data/`, write reviewed keys to `data/market_data/reviewed_bars.csv`, and write the latest auto-applied decision rows to `output/market_data_validation/auto_review_applied_decisions.csv`.

## Flags

`--source-dir SOURCE_DIR`
: Local normalized market data directory to validate. Default: `data/market_data`.

`--output-dir OUTPUT_DIR`
: Directory where validation reports are written. Default: `output/market_data_validation`.

`--symbols SYMBOLS [SYMBOLS ...]`
: Futures roots to validate. Omit this flag to use the full default futures universe.

`--frequencies FREQUENCIES [FREQUENCIES ...]`
: Frequencies to validate. Supported values are `daily`, `5min`, and `60min`.

`--threshold-pct THRESHOLD_PCT`
: Maximum allowed OHLC percentage difference for approval. Default: `0.25`.

`--start START`
: Optional inclusive validation start date/time.

`--end END`
: Optional inclusive validation end date/time.

`--open-dashboard`
: Open the static validation dashboard in your default browser.

`--serve-dashboard`
: Serve the dashboard locally so Apply selected changes can update market data and mark bars reviewed.

`--dashboard-host DASHBOARD_HOST`
: Host for `--serve-dashboard`. Default: `127.0.0.1`.

`--dashboard-port DASHBOARD_PORT`
: Port for `--serve-dashboard`. Default: `8765`.

`--dashboard-max-rows DASHBOARD_MAX_ROWS`
: Maximum reconciliation rows embedded in the dashboard. Rows are sorted by failed/largest difference first. Use `0` to embed all rows.

`--auto-apply-decisions`
: Automatically apply auto-review decisions marked `local` or `yahoo`, mark those bars reviewed, and write the dashboard from the post-apply validation state. In CLI usage, this defaults to new-only review behavior unless `--recheck-reviewed` is also passed.

`--review-new-only`
: Skip bars already listed in `data/market_data/reviewed_bars.csv` when building auto-review/apply decisions. Full validation summaries, difference reports, missing-bar reports, and duplicate checks are still computed.

`--recheck-reviewed`
: Reconsider bars already listed in `data/market_data/reviewed_bars.csv`. Use this when you intentionally want to rerun auto-review/apply logic across previously reviewed bars after changing the algorithm or threshold.

## Notes

The validator uses Yahoo as a reference, but Yahoo is not treated as automatically correct. Auto-review prefers local data unless the Yahoo close is closer to confirmed neighboring closes, or local data is missing and Yahoo has neighbor support.

For cron, use `python -u` so redirected logs update immediately. The validator also flushes progress lines while each symbol/frequency is being checked, including elapsed time and ETA.
