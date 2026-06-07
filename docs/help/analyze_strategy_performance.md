# analyze_strategy_performance.py

Analyze strategy performance from cleaned Thinkorswim trade history.

## Typical Uses

Analyze all strategies from the default master cleaned trade file:

```bash
python analyze_strategy_performance.py
```

The script prints stage-by-stage progress with elapsed time, so terminal and
cron logs show what it is doing during longer runs.

By default, the analyzer uses the current timestamp for expiration checks and
auto-detects the raw account statement CSV referenced by the master trade file
when that file is available in `data/` or the Thinkorswim statement folder. It
also compares account performance against a SPY buy-and-hold benchmark when
`data/market_data/daily/SPY.csv` exists. Expired options are checked against
local daily market data when possible, and ITM expirations are adjusted by
estimated intrinsic value.

Analyze selected strategies and leave the dashboard closed:

```bash
python analyze_strategy_performance.py \
  --strategy "Iron Condor" \
  --strategy "Put Spread" \
  --no-open-dashboard
```

Run faster risk sizing with fewer bootstrap simulations:

```bash
python analyze_strategy_performance.py \
  --risk-simulations 500 \
  --risk-last-n-trades 100
```

Analyze as of a specific historical time:

```bash
python analyze_strategy_performance.py \
  --as-of-date "2026-06-02 16:01:00"
```

Use the raw statement futures cash-flow rows to settle old futures positions
that no longer have a current mark value in the statement:

```bash
python analyze_strategy_performance.py \
  --as-of-date "2026-06-06 16:01:00" \
  --futures-statement-file data/2026-06-02-AccountStatement.csv
```

Disable automatic futures statement settlement:

```bash
python analyze_strategy_performance.py \
  --no-auto-futures-settlement
```

Skip expired option settlement checks:

```bash
python analyze_strategy_performance.py \
  --no-expired-option-settlement-check
```

Use a different buy-and-hold benchmark:

```bash
python analyze_strategy_performance.py \
  --benchmark-symbol QQQ
```

Use a specific benchmark CSV:

```bash
python analyze_strategy_performance.py \
  --benchmark-file data/market_data/daily/SPY.csv
```

## Outputs

Outputs are written under `output/strategy_performance/` by default:

- `strategy_dashboard.html`
- `strategy_summary_statistics.csv`
- `strategy_equity_curves.csv`
- `account_equity_curve.csv`
- `account_summary_statistics.csv`
- `benchmark_summary_statistics.csv`
- `realized_trades.csv`
- `expired_option_settlement_check.csv`
- `settlement_coverage.csv`
- `open_positions.csv`
- `open_position_audit.csv`
- `strategy_decision_board.csv`
- `capital_allocation.csv`
- `data_quality_warnings.csv`
- `strategy_pnl_correlation.csv`
- `strategy_drawdown_correlation.csv`
- `strategy_drawdown_overlap.csv`
- `strategy_daily_pnl.csv`
- `charts/`
- `strategy_trades/`
- `risk_per_trade/`

## Flags

`--input INPUT`
: Path to `master_cleaned_tos_data.csv`. Default: `output/master_cleaned_tos_data.csv`.

`--strategy STRATEGY`
: Strategy name to include. Repeat this flag to include multiple strategies.

`--no-open-dashboard`
: Do not open the dashboard in a browser after the script completes.

`--as-of-date AS_OF_DATE`
: Timestamp used to decide whether unmatched option positions have expired. Default: current timestamp when the script runs.

`--futures-statement-file FUTURES_STATEMENT_FILE`
: Raw Thinkorswim account statement CSV. Default: the latest matching `*AccountStatement*.csv` or `*Statement*.csv` file referenced by the master trade file when found in `data/` or `~/Dropbox/HarpFolders/02_Trading/thinkorswim/TOS_Account_Statements`, otherwise the latest matching file in those locations. Futures cash flows are used to settle stale futures positions that the statement shows with zero current mark value.

`--no-auto-futures-settlement`
: Do not auto-detect a statement file in `data/` for stale futures settlement. Explicit `--futures-statement-file` still applies.

`--benchmark-symbol BENCHMARK_SYMBOL`
: Buy-and-hold benchmark symbol. Default: `SPY`. The analyzer looks for a matching daily CSV under `data/market_data/daily/`.

`--benchmark-file BENCHMARK_FILE`
: Daily OHLC benchmark CSV with `timestamp` or `date` and `close` columns. Overrides `--benchmark-symbol` file discovery.

`--no-benchmark`
: Skip buy-and-hold benchmark comparison.

`--no-expired-option-settlement-check`
: Skip estimated intrinsic-value checks for expired options. By default, expired options are checked against local daily market data when available. The analyzer adjusts ITM expirations and writes `expired_option_settlement_check.csv`.

`--risk-simulations RISK_SIMULATIONS`
: Number of bootstrap equity curves for risk-per-trade calculations.

`--risk-bankroll RISK_BANKROLL`
: Account equity used for risk-per-trade dollar sizing.

`--risk-drawdown-limit RISK_DRAWDOWN_LIMIT`
: Drawdown limit used by the Safe-F risk test.

`--risk-pct-above-dd-limit RISK_PCT_ABOVE_DD_LIMIT`
: Required percentage of simulations above the drawdown limit.

`--risk-safe-f-increment RISK_SAFE_F_INCREMENT`
: Step size used when searching for Safe-F.

`--risk-safe-f-start RISK_SAFE_F_START`
: Starting value used when searching for Safe-F.

`--risk-last-n-trades RISK_LAST_N_TRADES`
: Use only the most recent N realized trades per strategy. `0` uses all trades.

`--risk-random-seed RISK_RANDOM_SEED`
: Random seed for risk-per-trade bootstrap sampling.

## Notes

The strategy analyzer consumes `master_cleaned_tos_data.csv`, aggregates execution rows into realized trades, tracks open positions, builds account and strategy equity curves, computes correlations and drawdown overlap, and writes a dashboard plus per-strategy risk sizing reports.

The dashboard includes a decision board and capital allocation table. `Data Confidence` is lowered when expired option settlement data is missing or open positions look stale. `Suggested Action` is a compact helper based on data confidence, strategy status, total return, drawdown, and profit factor; review the supporting CSVs before changing live allocation.
