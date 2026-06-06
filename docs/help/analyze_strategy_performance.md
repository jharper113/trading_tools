# analyze_strategy_performance.py

Analyze strategy performance from cleaned Thinkorswim trade history.

## Typical Uses

Analyze all strategies from the default master cleaned trade file:

```bash
python analyze_strategy_performance.py
```

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

## Outputs

Outputs are written under `output/strategy_performance/` by default:

- `strategy_dashboard.html`
- `strategy_summary_statistics.csv`
- `strategy_equity_curves.csv`
- `account_equity_curve.csv`
- `account_summary_statistics.csv`
- `realized_trades.csv`
- `open_positions.csv`
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
