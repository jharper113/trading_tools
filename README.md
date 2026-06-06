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

Ingest futures market data into local daily, 5-minute, and 60-minute files:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --frequencies daily 5min 60min
```

Schwab API access can be tested interactively with:

```bash
python download_market_data.py \
  --provider schwab \
  --frequencies daily 5min 60min
```

To request as much Schwab history as the script can ask for, add `--all`:

```bash
python download_market_data.py \
  --provider schwab \
  --all \
  --frequencies daily 5min 60min
```

The script prompts for `client_id`, hides `client_secret`, prints the Schwab
authorization URL, and then prompts for either the full redirect URL or the
authorization `code` value. You can also set `SCHWAB_ACCESS_TOKEN`,
`SCHWAB_REFRESH_TOKEN`, `SCHWAB_CLIENT_ID`, or `SCHWAB_CLIENT_SECRET` in your
shell to skip prompts. After the first interactive Schwab authorization, the
script saves tokens to `data/market_data/schwab_tokens.json` with private file
permissions so later cron runs can renew with the saved refresh token.
Without `--all`, Schwab mode requests its shorter default window. With `--all`,
it requests up to 20 years for daily bars and up to 10 days for intraday bars.

Schwab may not provide historical futures candles through its price-history endpoint,
so the CSV provider is intended for futures-history vendors or broker exports.
Normalized bars are written under `data/market_data/`.
Long Schwab runs print per-symbol/frequency progress, elapsed time, and ETA.
To speed up scheduled jobs, pass a smaller `--symbols` list, omit frequencies
you do not need, or use `--start` to limit the requested date range.
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
  --frequencies daily 5min 60min \
  --apply-daily-intraday-fixes
```

Validate locally stored futures bars against Yahoo Finance continuous futures data:

```bash
python validate_market_data.py \
  --source-dir data/market_data \
  --frequencies daily 5min 60min \
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

The validated and repaired bar files stay in
`data/market_data/<frequency>/<symbol>.csv`, such as
`data/market_data/daily/ES.csv`, `data/market_data/5min/ES.csv`, and
`data/market_data/60min/ES.csv`. These normalized CSV files are the ones to
import into AmiBroker. The validator also writes `auto_review_decisions.csv`,
`auto_review_applied_decisions.csv`, and `timezone_alignment.csv`.
Auto-review decisions prefer local data, choose Yahoo only when the Yahoo close
is closer to the prior or next confirmed close, keep local bars when Yahoo is
missing, and accept missing local bars from Yahoo when they are near confirmed
neighbor closes. With `--auto-apply-decisions`, already reviewed bars are
skipped by default on later runs; pass `--recheck-reviewed` to intentionally
reconsider them. The timezone report checks intraday UTC timestamp alignment
and flags cases where a common hour shift would match more bars than exact UTC
timestamps.

The default futures universe covers liquid roots across equity indexes
(`/ES`, `/NQ`, `/RTY`, `/YM`), rates (`/ZB`, `/ZN`, `/ZF`, `/ZT`), currencies
(`/6E`, `/6J`, `/6B`, `/6A`, `/6C`, `/6S`), metals (`/GC`, `/SI`, `/HG`,
`/PL`), energy (`/CL`, `/NG`, `/RB`, `/HO`), agriculture (`/ZC`, `/ZS`, `/ZM`,
`/ZL`, `/ZW`, `/LE`, `/HE`), and softs (`/KC`, `/SB`, `/CT`, `/CC`). Pass
`--symbols` to run a smaller subset.

Generated files are written to `output/`. Raw brokerage exports in `data/` and generated outputs are intentionally ignored by Git.
