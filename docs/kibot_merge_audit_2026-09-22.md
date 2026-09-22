# Kibot + Schwab market-data merge audit — 2026-09-22

## Result

**PASS on Ubuntu. Windows AmiBroker database rebuild and OLE verification are pending.**

The published canonical repository contains only `daily` and `5min` data.
Valid Schwab OHLC and Schwab volume win on unreviewed overlaps; explicit entries
in `reviewed_bars.csv` remain protected; Kibot fills missing history.

## Sources and archives

| Source | SHA-256 | Members |
|---|---|---:|
| Kibot daily | `332c7ac308386ad9c41a204d52ef175c6ce64062c1ac0b82a8ad5c64198633e6` | 24 |
| Kibot 5-minute | `95db0e292b557e695a65df6c27ed6595ea73d8e110b855ae7f0ee819d8f2528a` | 24 |

The immutable ZIPs are stored under
`data/market_data/source_archives/kibot/2026-09-22/` as `daily.zip` and
`intraday.zip`.

The prior canonical repository is under
`data/market_data_archives/canonical_before_kibot_merge_2026-09-22/`:
206 files, 208,139,250 bytes, and 1,552,096 CSV rows. The prior 60-minute
repository is under
`data/market_data_archives/60min_before_kibot_merge_2026-09-22/`:
65 files, 50,940,356 bytes, and 371,864 rows. Both contain SHA-256 manifests.

## Published coverage and selection checks

- 66 daily files, 452,411 rows total.
- 66 five-minute files, 26,482,761 rows total.
- All 24 purchased symbols are present at both frequencies, including `RP`.
- ES daily: 1997-09-09 through 2026-09-21, 7,400 rows.
- ES 5-minute: 2009-09-27 22:00 UTC through 2026-09-22 07:20 UTC,
  1,198,105 rows.
- No duplicate canonical timestamps were found.
- All 36,440 conflicts involving Schwab selected Schwab. No unreviewed
  Schwab conflict selected another source.
- The review ledger was applied to 37 daily and 29 intraday symbols.
- Two clearly invalid Kibot daily opens were excluded: SI on 1998-05-07 and
  ZN (Kibot TY) on 1995-12-19. Daily settlement closes outside the session
  high/low were retained because that is valid futures data.
- DST conversion used `America/Detroit`; ambiguous and nonexistent rows: zero.

Detailed evidence is in
`data/market_data/quality/kibot_merge_2026-09-22/`.

## Post-publication verification

The saved-data integrity report contains zero OHLC errors. The daily-versus-
intraday diagnostic produced 52,110 review candidates across 43 symbols; it is
a cross-frequency comparison and does not indicate corrupt canonical bars.

The AmiBroker export manifest records 452,411 daily and 26,482,761 five-minute
rows. All six output hashes were recomputed successfully, and ES contains both
winter and summer observations in the 2009–2018 research period.

The separate local validator checked 132 symbol-frequency groups and reported:

- duplicate rows: 0
- price differences: 0 (reference fetching disabled)
- missing rows: 0

## Commands

The migration used `merge_kibot_market_data.py`, followed by:

```bash
.venv/bin/python download_market_data.py \
  --quality-only --frequencies daily 5min --export-amibroker
```

Local validation called `validate_market_data(...)` with
`reference_frequencies=[]` so no network/reference source could alter or veto
the local audit.

Before running any optimization or WFA batch on Windows, archive/recreate the
two AmiBroker databases, import the generated files, and require
`Verify-AmiBroker-Databases.ps1` to write a `PASS` result.
