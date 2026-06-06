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

## Outputs

By default, outputs are written under `output/`:

- `cleaned_tos_data.csv`
- `master_cleaned_tos_data.csv`
- `pnl_chart.png`
- `equity_curve.csv`
- `summary_statistics.csv`

`master_cleaned_tos_data.csv` is updated incrementally and deduplicated so repeated imports do not duplicate existing trades.

## Flags

`--input INPUT`
: Path to the raw Thinkorswim account statement CSV. Default: `data/trades.csv`.

`--output-dir OUTPUT_DIR`
: Directory where cleaned trade outputs are written. Default: `output`.

## Notes

The script looks for the `Account Trade History` section in the account statement export, enriches trades with strategy names, fees, margin requirement, PnL, equity, and return columns, then updates the master cleaned trade file.
