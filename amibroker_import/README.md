# AmiBroker market-data import

This directory contains the Windows-side importer for normalized market data.
It imports long daily history into `Harp_daily` and 5-minute history into
`Harp_intraday`. AmiBroker can display and analyze 60-minute bars by compressing
the 5-minute database, so the separate 60-minute CSV set is not imported.

## Prepare the files on Ubuntu

Add `--export-amibroker` to the normal downloader command so every completed
market-data run refreshes the two import files:

```bash
.venv/bin/python download_market_data.py \
  --provider schwab \
  --frequencies daily 5min 60min \
  --all \
  --continue-on-error \
  --export-amibroker \
  --notify
```

The export can also be refreshed without downloading data:

```bash
.venv/bin/python export_amibroker_market_data.py
```

Both commands atomically replace files under `data/market_data/amibroker/`:

- `daily.csv`
- `5min.csv`
- `instrument_details.csv`
- `point_values.csv`
- `tick_sizes.csv`
- `margins.csv`
- `export_complete.json`

Daily trading dates are preserved. Intraday UTC timestamps are converted with
the daylight-saving rules for `America/Detroit`. Missing volume and open
interest values are exported as zero because AmiBroker expects numeric fields.

## One-time AmiBroker database settings

Configure `Harp_daily` as a local end-of-day database. Configure
`Harp_intraday` as a local database with a 5-minute base interval and show all
24 hours. The imported intraday timestamps are already Eastern time, so no
additional AmiBroker time shift is needed.

The format definitions use the CSV ticker column and automatically add missing
symbols. Tickers follow the slash-free filenames, such as `ES`, `6E`, and
`BZ`. They allow negative prices so historical contracts such as crude oil are
not rejected.

The maintained source for AmiBroker symbol properties is
`amibroker_import/instrument_settings.csv`. Each export validates that every
symbol with price rows has an entry in this table. The PowerShell importer then
sets the full name, round-lot size, currency, point value, tick size, and any
available margin deposit in both databases.

Properties are split across several import files so a blank setting never
overwrites an AmiBroker value with zero. Point value and tick size are omitted
for `6J`, `HG`, and `SI` because those stored histories change price scale at
the Norgate-to-Schwab boundary. The point value is also omitted for
`RF___CCB` and spot-FX pairs whose P&L needs a contemporaneous currency
conversion.

Margin deposits are intentionally blank in the checked-in table because
Schwab says margin requirements can change at any time and exposes the current
account requirement in thinkorswim rather than its public price-history API.
To maintain them, enter both `margin_deposit` and `margin_as_of` for a symbol in
`instrument_settings.csv`, rerun the export, and then run the PowerShell
importer. Rows without both values are excluded from `margins.csv` and logged
as skipped. Use the current Schwab initial/overnight dollar requirement, not
the intraday requirement.

## Import from the Windows VM

Wait for Dropbox to finish syncing, then open PowerShell and run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  "Z:\04_code\python\trading_tools\amibroker_import\Import-MarketData.ps1"
```

The script is preconfigured with:

- Market data: `Z:\04_code\python\trading_tools\data\market_data`
- AmiBroker: `C:\Program Files (x86)\AmiBroker\Broker.exe`
- Daily database: `Z:\04_code\amibroker\databases\Harp_daily`
- Intraday database: `Z:\04_code\amibroker\databases\Harp_intraday`

It verifies the export completion manifest, imports prices and instrument
properties, loads and saves each database, and leaves AmiBroker open on
`Harp_intraday`. Import logs are written to
`data/market_data/amibroker/logs/`.

Do not run an Analysis job while the script is switching databases. If any
configured path changes, pass a different value using the corresponding
PowerShell parameter or edit the defaults at the top of
`Import-MarketData.ps1`.

Official references:

- [AmiBroker OLE object model](https://www.amibroker.com/guide/objects.html)
- [AmiBroker ASCII importer](https://www.amibroker.com/guide/d_ascii.html)
- [AmiBroker database settings](https://www.amibroker.com/guide/w_dbsettings.html)
- [Schwab futures contract specifications](https://www.schwab.com/futures/futures-markets)
- [Schwab futures margin](https://www.schwab.com/futures/futures-margin)
