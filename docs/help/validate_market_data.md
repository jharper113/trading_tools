# validate_market_data.py

Validate locally stored futures market data against Yahoo Finance continuous futures reference data.

## Typical Uses

Validate the full default futures universe and write static reports:

```bash
python validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min \
  --threshold-pct 0.25
```

Serve the dashboard locally so selected decisions can be applied directly:

```bash
python validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min \
  --threshold-pct 0.25 \
  --serve-dashboard
```

Automatically apply non-review auto decisions and regenerate the post-apply dashboard:

```bash
python validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min \
  --auto-apply-decisions
```

## Outputs

Validation reports are written under `output/market_data_validation/` by default:

- `validation_summary.csv`
- `price_differences.csv`
- `missing_bars.csv`
- `duplicate_bars.csv`
- `auto_review_decisions.csv`
- `timezone_alignment.csv`
- `market_data_difference_heatmap.png`
- `validation_dashboard.html`

Applied decisions update `data/market_data/` and write reviewed keys to `data/market_data/reviewed_bars.csv`.

## Flags

`--source-dir SOURCE_DIR`
: Local normalized market data directory to validate. Default: `data/market_data`.

`--output-dir OUTPUT_DIR`
: Directory where validation reports are written. Default: `output/market_data_validation`.

`--symbols SYMBOLS [SYMBOLS ...]`
: Futures roots to validate. Omit this flag to use the full default futures universe.

`--frequencies FREQUENCIES [FREQUENCIES ...]`
: Frequencies to validate. Supported values are `daily` and `5min`.

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
: Automatically apply auto-review decisions marked `local` or `yahoo`, mark those bars reviewed, and write the dashboard from the post-apply validation state.

## Notes

The validator uses Yahoo as a reference, but Yahoo is not treated as automatically correct. Auto-review prefers local data unless the Yahoo close is closer to confirmed neighboring closes, or local data is missing and Yahoo has neighbor support.
