# TOS Trade Analysis

Tools for extracting, enriching, and analyzing Thinkorswim trade history exports.

## Common Commands

Run the full test suite:

```bash
pytest -q
```

Generate enriched trade history, equity curve, summary statistics, and PnL chart:

```bash
python extract_trade_history.py
```

Analyze performance by strategy:

```bash
python analyze_strategy_performance.py
```

Generated files are written to `output/`. Raw brokerage exports in `data/` and generated outputs are intentionally ignored by Git.
