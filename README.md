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

Ingest futures market data into the canonical daily and 5-minute files:

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

To create or replace the saved Schwab tokens without downloading data, run:

```bash
python download_market_data.py \
  --provider schwab \
  --auth-only \
  --force-reauth
```

The script opens the Schwab authorization page in the default browser and
keeps the printed URL available as a fallback. After approval, paste the full
redirect URL into the waiting terminal.

To request as much Schwab history as the script can ask for, add `--all`:

```bash
python download_market_data.py \
  --provider schwab \
  --all \
  --frequencies daily 5min
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
you do not need, or use `--start` to limit the requested date range. Add
`--continue-on-error` to scheduled runs so a provider error for one
symbol/frequency does not prevent the remaining jobs from running. The command
still exits nonzero after completing the batch if any jobs failed. Add
`--notify` to show a desktop notification after a successful run or a final
error. Desktop notifications are best effort and require the user to be logged
in to the graphical desktop session.

```cron
15 17 * * * cd /home/jon/Dropbox/HarpFolders/04_Code/Python/trading_tools && . /home/jon/.schwab_env && /home/jon/anaconda3/bin/python download_market_data.py --provider schwab --frequencies daily 5min --continue-on-error --export-amibroker --notify >> /home/jon/Dropbox/HarpFolders/04_Code/Python/trading_tools/logs/market_data.log 2>&1
```

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

The validated and repaired bar files stay in
`data/market_data/<frequency>/<symbol>.csv`, such as
`data/market_data/daily/ES.csv`, `data/market_data/5min/ES.csv`, and
`data/market_data/60min/ES.csv`. Run `export_amibroker_market_data.py` or pass
`--export-amibroker` to the downloader to create combined daily and 5-minute
files plus instrument-property imports for the Windows importer in
`amibroker_import/`. The maintained point values, tick sizes, and optional
dated margin deposits are in `amibroker_import/instrument_settings.csv`. The
validator also
writes `auto_review_decisions.csv`,
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
`/ZL`, `/ZW`, `/LE`, `/HE`), softs (`/KC`, `/SB`, `/CT`, `/CC`), and crypto
(`/BTC`, `/ETH`, `/MBT`, `/MET`, `/SOL`, `/MSL`, `/XRP`, `/MXP`, `/MCA`).
Pass `--symbols` to run a smaller subset.
The default list is defined in `download_market_data.py`; each run also writes
the symbols requested for that run to `data/market_data/symbols.csv`.

Generated files are written to `output/`. Raw brokerage exports in `data/` and generated outputs are intentionally ignored by Git.

## Rebuild AmiBroker after the Kibot merge

On the Windows VM, close AmiBroker and run these commands in order:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Archive-AmiBroker-Databases.ps1"

# Create Harp_Daily and Harp_Intraday using the settings printed by the script.

powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Import-MarketData.ps1"

powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_Code\Python\trading_tools\amibroker_import\Verify-AmiBroker-Databases.ps1"
```

Do not run an optimization or WFA batch until the verification JSON says
`PASS`. If verification reports truncation, recreate `Harp_Intraday` with a
higher bar capacity, reimport, and verify again.
