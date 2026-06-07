# extract_trade_history.py

Extract, enrich, and summarize Thinkorswim account trade history exports.

## Typical Uses

Run against the default raw export path:

```bash
python extract_trade_history.py
```

Run against a specific export and output directory:

```bash
python extract_trade_history.py \
  --input data/2026-06-02-AccountStatement.csv \
  --output-dir output
```

Only include trades and reconciliation rows from 2026 onward:

```bash
python extract_trade_history.py \
  --input data/2026-06-02-AccountStatement.csv \
  --start-date 2026-01-01
```

## Outputs

By default, outputs are written under `output/`:

- `cleaned_tos_data.csv`
- `master_cleaned_tos_data.csv`
- `pnl_chart.png`
- `equity_curve.csv`
- `summary_statistics.csv`
- `cash_balance_reconciliation.csv`
- `daily_trade_cash_reconciliation.csv`
- `cash_balance_reconciliation_summary.csv`
- `cash_balance_reconciliation_dashboard.html`
- `fee_correction_suggestions.csv`
- `cash_trade_correction_candidates.csv`
- `cash_trade_corrections.csv`
- `cash_reconciliation_group_reviews.csv`

`master_cleaned_tos_data.csv` is updated incrementally and deduplicated so repeated imports do not duplicate existing trades.

## Flags

`--input INPUT`
: Path to the raw Thinkorswim account statement CSV. Default: `data/trades.csv`.

`--output-dir OUTPUT_DIR`
: Directory where cleaned trade outputs are written. Default: `output`.

`--start-date START_DATE`
: Only include trades and cash reconciliation rows on or after this date, for example `2026-01-01`.
When a statement cash ledger starts earlier than its trade history section, cash reconciliation is also bounded to the actual date range covered by the extracted trades.

`--skip-cash-validation`
: Skip cash-balance reconciliation reports from statement cash ledger sections.

`--cash-validation-tolerance CASH_VALIDATION_TOLERANCE`
: Allowed dollar difference between extracted trade PnL and statement trade cash flow by day/account bucket. Default: `1.0`.

`--skip-cash-dashboard`
: Skip the HTML cash reconciliation review dashboard.

`--open-cash-dashboard`
: Open the cash reconciliation review dashboard after writing it.

`--serve-cash-dashboard`
: Serve the cash dashboard locally so approval selections can be saved to the corrections file.

`--cash-dashboard-host CASH_DASHBOARD_HOST`
: Host for `--serve-cash-dashboard`. Default: `127.0.0.1`.

`--cash-dashboard-port CASH_DASHBOARD_PORT`
: Port for `--serve-cash-dashboard`. Default: `8766`.

`--apply-cash-corrections`
: Apply saved approved cash-ledger trade corrections before writing cleaned/master outputs.

`--cash-corrections-file CASH_CORRECTIONS_FILE`
: CSV file used to persist cash-ledger trade corrections. Default: `<output-dir>/cash_trade_corrections.csv`.

`--ignore-cash-corrections`
: Do not load or apply a saved cash corrections file.

`--strict-reconciliation`
: Exit with an error after writing outputs if cash validation finds unreviewed unreconciled day/account groups. Reviewed groups saved in `cash_reconciliation_group_reviews.csv` do not fail this check unless their counts or dollar totals change.

## Notes

The script looks for the `Account Trade History` section in the account statement export, enriches trades with fees, margin requirement, PnL, equity, and return columns, then updates the master cleaned trade file. `Strategy_Name` is not auto-populated for new trades; assign it manually with `review_missing_strategy_names.py`.

By default, the script also reads detailed statement cash ledger sections such as `Cash Balance`, `Cash & Sweep Vehicle`, `Futures Cash Balance`, `Forex Cash Balance`, and `Crypto Cash Balance` when they are present. It reconciles statement trade cash flow against extracted `net_pnl` by date and account bucket, reports how many extracted trades fall on unreconciled day/account groups, and writes an HTML dashboard for manual review. The dashboard does not apply corrections.

Fee correction suggestions are generated only for safer one-to-one matches where a day/account bucket has the same number of extracted trade rows and statement `TRD` cash-ledger rows. In those cases, the broker cash ledger is preferred for the actual cash impact, and the script reports broker-implied fees plus the fee/net-PnL adjustment needed for review.

The script builds event-level correction candidates from statement cash-ledger `TRD` rows and writes them to `cash_trade_correction_candidates.csv`. Use `--serve-cash-dashboard` to approve selected candidates in the dashboard and save them to `cash_trade_corrections.csv`. Later runs automatically load that approved corrections file and reapply the saved corrections unless `--ignore-cash-corrections` is passed, so reviewed cash-ledger corrections do not need to be re-reviewed.

If an unreconciled dashboard group has no safe automatic correction candidate, use `Mark group reviewed` and then `Save approved corrections`. The reviewed group is saved to `cash_reconciliation_group_reviews.csv` and hidden on later runs unless its counts or dollar totals change.

`daily_trade_cash_reconciliation.csv` is the terminal-friendly audit table for each day/account bucket. It includes review status and a `strict_reconciliation_failure` column so cron jobs can detect new reconciliation issues without opening the dashboard.
