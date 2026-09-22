# download_market_data.py

Download or ingest market data and store normalized daily and 5-minute bars locally. A 60-minute output remains available when explicitly requested.

## Typical Uses

Ingest vendor CSV data for the full default futures universe:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --frequencies daily 5min
```

Run quality checks against already-saved market data without fetching new bars:

```bash
python download_market_data.py --quality-only
```

Request Schwab's maximum supported history window:

```bash
python download_market_data.py \
  --provider schwab \
  --all \
  --frequencies daily 5min
```

Download or ingest only a subset:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --symbols /ES /NQ /ZN /CL \
  --frequencies daily 5min
```

Download daily SPY data for the buy-and-hold benchmark:

```bash
python download_market_data.py \
  --provider schwab \
  --symbols SPY \
  --frequencies daily
```

Create or replace saved Schwab tokens without downloading market data:

```bash
python download_market_data.py \
  --provider schwab \
  --auth-only \
  --force-reauth
```

Update every configured symbol while allowing the remaining jobs to finish
when Schwab rejects one symbol/frequency:

```bash
python download_market_data.py \
  --provider schwab \
  --frequencies daily 5min \
  --continue-on-error \
  --notify
```

Omitting `--symbols` selects every configured symbol. The `--all` flag controls
history depth and is not required to select all symbols.

## Outputs

Normalized bars are written to `data/market_data/<frequency>/<symbol>.csv` by default.

Pass `--export-amibroker` to also create combined AmiBroker import files after
the quality checks finish:

- `data/market_data/amibroker/daily.csv`
- `data/market_data/amibroker/5min.csv`
- `data/market_data/amibroker/instrument_details.csv`
- `data/market_data/amibroker/point_values.csv`
- `data/market_data/amibroker/tick_sizes.csv`
- `data/market_data/amibroker/margins.csv`
- `data/market_data/amibroker/export_complete.json`

The daily file preserves the supplied trading date. The 5-minute file converts
UTC timestamps to `America/Detroit` by default. The Windows importer and setup
instructions are in `amibroker_import/README.md`.
After a full historical merge, archive and recreate both AmiBroker databases,
import these files, and run `Verify-AmiBroker-Databases.ps1`. A passing export
manifest alone does not prove that AmiBroker retained the full history.
The export validates and uses the maintained contract-property table at
`amibroker_import/instrument_settings.csv`. The Windows importer applies the
available full names, currencies, round-lot sizes, point values, tick sizes,
and dated margin deposits to both AmiBroker databases.

Quality reports are written under `data/market_data/quality/`:

- `quality_summary.csv`
- `market_data_integrity.csv`
- `daily_intraday_quality.csv`
- `daily_intraday_fix_candidates.csv`

The generated symbol manifest is written to `data/market_data/symbols.csv`.
It records the symbols requested for the latest run; the authoritative default
list is defined by `FUTURES_PRODUCTS`, `EQUITY_PRODUCTS`, and
`LEGACY_PRODUCTS` in
`download_market_data.py`.

`LEGACY_PRODUCTS` keeps the existing filenames for imported historical
series that do not have a validated Schwab price-history mapping. Validated
futures use their canonical Schwab symbols. The mapping and live validation
results are in `data/market_data/daily/etc/schwab_symbol_mapping.csv`.

## Flags

`--symbols SYMBOLS [SYMBOLS ...]`
: Symbols to download, such as futures roots/contracts (`/ES`, `/6E`, `/GC`) or equities/ETFs (`SPY`). Omit this flag to use the full configured default universe.

`--frequencies FREQUENCIES [FREQUENCIES ...]`
: Frequencies to download or check. The default is `daily 5min`. `60min` remains supported as an explicit optional output outside the canonical active repository.

`--provider {csv,schwab}`
: Data provider. Use `csv` for vendor/exported bars and `schwab` for Schwab API price history.

`--input-dir INPUT_DIR`
: Directory containing source CSV bars when `--provider csv`.

`--output-dir OUTPUT_DIR`
: Directory where normalized market data files are written. Default: `data/market_data`.

`--quality-only`
: Run local integrity and daily-vs-5min quality checks against already saved data without fetching provider data.

`--start START`
: Optional inclusive start date/time, for example `2026-01-01`.

`--end END`
: Optional inclusive end date/time, for example `2026-06-05`.

`--access-token ACCESS_TOKEN`
: Schwab bearer token. Defaults to `SCHWAB_ACCESS_TOKEN`.

`--refresh-token REFRESH_TOKEN`
: Schwab refresh token for non-interactive token renewal. Defaults to `SCHWAB_REFRESH_TOKEN` or the token file.

`--token-file TOKEN_FILE`
: Path to a JSON file for saved Schwab tokens. Defaults to `SCHWAB_TOKEN_FILE` or `data/market_data/schwab_tokens.json`.

`--client-id CLIENT_ID`
: Schwab OAuth client id. Defaults to `SCHWAB_CLIENT_ID` or an interactive prompt.

`--client-secret CLIENT_SECRET`
: Schwab OAuth client secret. Defaults to `SCHWAB_CLIENT_SECRET` or a hidden prompt.

`--redirect-uri REDIRECT_URI`
: Schwab OAuth redirect URI configured for your app.

`--auth-only`
: Prepare and save Schwab tokens, then exit without downloading data or running quality checks.

`--force-reauth`
: Ignore saved Schwab access and refresh tokens and start browser authorization. Use this with `--auth-only` when Schwab expires or revokes the saved refresh token.

`--continue-on-error`
: Continue remaining symbol/frequency jobs after a provider error. The command runs quality checks and then exits nonzero if any jobs failed.

`--notify`
: Send a desktop notification when the command succeeds or fails. Delivery is best effort and does not change the command's exit status.

`--all`
: Request as much Schwab price history as the script can ask for. This controls history depth, not symbol selection.

`--export-amibroker`
: Create combined daily and 5-minute AmiBroker import files after the normal
quality checks.

`--amibroker-timezone AMIBROKER_TIMEZONE`
: IANA timezone used for the AmiBroker 5-minute export. Default:
`America/Detroit`.

`--quality-threshold-pct QUALITY_THRESHOLD_PCT`
: Maximum allowed OHLC percentage difference when comparing daily bars to daily bars aggregated from 5-minute data. Default: `0.25`.

`--apply-daily-intraday-fixes`
: Replace mismatched daily bars with OHLC aggregated from 5-minute bars when enough intraday bars are available.

`--min-intraday-bars-for-daily-fix MIN_INTRADAY_BARS_FOR_DAILY_FIX`
: Minimum number of 5-minute bars required before applying a daily repair candidate. Default: `50`.

## Notes

The quality gate safely fixes OHLC envelope issues during save and reports remaining integrity issues. Daily-vs-5min repairs are candidate-only unless `--apply-daily-intraday-fixes` is passed.

Long Schwab runs can take a while because the script requests each symbol/frequency pair separately. The script prints per-job progress, elapsed time, and an ETA while fetching and saving. To speed up a scheduled run, pass a smaller `--symbols` list, omit frequencies you do not need, or use `--start` to limit the requested date range.

Schwab does not accept `60` as a minute frequency. When you request `60min`, the script asks Schwab for 30-minute bars and aggregates them into 60-minute bars locally before saving.

For cron, client id and client secret are not enough by themselves on the first run because Schwab OAuth requires browser approval. Run `--provider schwab --auth-only --force-reauth` manually once, paste the authorization redirect URL/code, and the script will save tokens to `data/market_data/schwab_tokens.json` with `0600` permissions. Later cron runs can renew access with the saved refresh token as long as `SCHWAB_CLIENT_ID` and `SCHWAB_CLIENT_SECRET` are available. Browser authorization is still required again whenever Schwab expires or revokes the refresh token.

Desktop notifications from `--notify` use `notify-send` and can reach the normal Ubuntu desktop session from cron. The notification appears only while the user is logged in; an unavailable desktop session does not make the market-data command fail.

Example daily cron entry for all configured symbols:

```cron
SHELL=/bin/bash
CRON_TZ=America/New_York
15 17 * * * cd /home/jon/Dropbox/HarpFolders/04_Code/Python/trading_tools && . /home/jon/.schwab_env && /home/jon/anaconda3/bin/python download_market_data.py --provider schwab --frequencies daily 5min --continue-on-error --export-amibroker --notify >> /home/jon/Dropbox/HarpFolders/04_Code/Python/trading_tools/logs/market_data.log 2>&1
```
