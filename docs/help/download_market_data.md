# download_market_data.py

Download or ingest market data and store normalized daily, 5-minute, and 60-minute bars locally.

## Typical Uses

Ingest vendor CSV data for the full default futures universe:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --frequencies daily 5min 60min
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
  --frequencies daily 5min 60min
```

Download or ingest only a subset:

```bash
python download_market_data.py \
  --provider csv \
  --input-dir data/vendor_market_data \
  --symbols /ES /NQ /ZN /CL \
  --frequencies daily 5min 60min
```

Download daily SPY data for the buy-and-hold benchmark:

```bash
python download_market_data.py \
  --provider schwab \
  --symbols SPY \
  --frequencies daily
```

## Outputs

Normalized bars are written to `data/market_data/<frequency>/<symbol>.csv` by default.

Quality reports are written under `data/market_data/quality/`:

- `quality_summary.csv`
- `market_data_integrity.csv`
- `daily_intraday_quality.csv`
- `daily_intraday_fix_candidates.csv`

The symbol manifest is written to `data/market_data/symbols.csv`.

## Flags

`--symbols SYMBOLS [SYMBOLS ...]`
: Symbols to download, such as futures roots/contracts (`/ES`, `/6E`, `/GC`) or equities/ETFs (`SPY`). Omit this flag to use the full default futures universe.

`--frequencies FREQUENCIES [FREQUENCIES ...]`
: Frequencies to download or check. Supported values are `daily`, `5min`, and `60min`.

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

`--all`
: Request as much Schwab price history as the script can ask for. This controls history depth, not symbol selection.

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

For cron, client id and client secret are not enough by themselves on the first run because Schwab OAuth requires browser approval. Run the command manually once, paste the authorization redirect URL/code, and the script will save tokens to `data/market_data/schwab_tokens.json` with `0600` permissions. Later cron runs can renew access with the saved refresh token as long as `SCHWAB_CLIENT_ID` and `SCHWAB_CLIENT_SECRET` are available.
