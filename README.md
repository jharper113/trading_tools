# TOS Trade Analysis

Tools for extracting, enriching, and analyzing Thinkorswim trade history exports.

## Common Commands

Script help files:

- [download_market_data.py](docs/help/download_market_data.md)
- [validate_market_data.py](docs/help/validate_market_data.md)
- [extract_trade_history.py](docs/help/extract_trade_history.md)
- [analyze_strategy_performance.py](docs/help/analyze_strategy_performance.md)

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

Ingest futures market data into local daily and 5-minute files:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --frequencies daily 5min
```

Schwab API access can be tested interactively with:

```bash
python download_market_data.py \
  --provider schwab \
  --frequencies daily 5min
```

To request as much Schwab history as the script can ask for, add `-all`:

```bash
python download_market_data.py \
  --provider schwab \
  -all \
  --frequencies daily 5min
```

The script prompts for `client_id`, hides `client_secret`, prints the Schwab
authorization URL, and then prompts for either the full redirect URL or the
authorization `code` value. You can also set `SCHWAB_ACCESS_TOKEN`,
`SCHWAB_CLIENT_ID`, or `SCHWAB_CLIENT_SECRET` in your shell to skip prompts.
Without `-all`, Schwab mode requests its shorter default window. With `-all`,
it requests up to 20 years for daily bars and up to 10 days for 5-minute bars.

Schwab may not provide historical futures candles through its price-history endpoint,
so the CSV provider is intended for futures-history vendors or broker exports.
Normalized bars are written under `data/market_data/`.
Each download/ingest run also writes local quality reports under
`data/market_data/quality/` before any Yahoo reconciliation review. The quality
step safely fixes OHLC envelope issues during save, reports remaining OHLC
integrity problems, compares daily bars against daily OHLC aggregated from
5-minute bars, and writes daily repair candidates. To apply daily repairs from
5-minute data during ingest, add:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --frequencies daily 5min \
  --apply-daily-intraday-fixes
```

Validate locally stored futures bars against Yahoo Finance continuous futures data:

```bash
python validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min \
  --threshold-pct 0.25 \
  --serve-dashboard
```

Validation reports and the difference heatmap are written under
`output/market_data_validation/`. The dashboard lets you choose local/Schwab or
Yahoo reference data for compared bars. In `--serve-dashboard` mode, the Apply
button updates `data/market_data/` and records reviewed bars in
`data/market_data/reviewed_bars.csv`, which protects those reviewed timestamps
from being overwritten during later market-data refreshes. The dashboard can
also download either a decisions CSV or a selected-bars CSV.

The validator also writes `auto_review_decisions.csv` and
`timezone_alignment.csv`. Auto-review decisions prefer local data, choose Yahoo
only when the Yahoo close is closer to the prior or next confirmed close, keep
local bars when Yahoo is missing, and accept missing local bars from Yahoo when
they are near confirmed neighbor closes. The timezone report checks intraday
UTC timestamp alignment and flags cases where a common hour shift would match
more bars than exact UTC timestamps.

The default futures universe covers liquid roots across equity indexes
(`/ES`, `/NQ`, `/RTY`, `/YM`), rates (`/ZB`, `/ZN`, `/ZF`, `/ZT`), currencies
(`/6E`, `/6J`, `/6B`, `/6A`, `/6C`, `/6S`), metals (`/GC`, `/SI`, `/HG`,
`/PL`), energy (`/CL`, `/NG`, `/RB`, `/HO`), agriculture (`/ZC`, `/ZS`, `/ZM`,
`/ZL`, `/ZW`, `/LE`, `/HE`), and softs (`/KC`, `/SB`, `/CT`, `/CC`). Pass
`--symbols` to run a smaller subset.

Generated files are written to `output/`. Raw brokerage exports in `data/` and generated outputs are intentionally ignored by Git.
