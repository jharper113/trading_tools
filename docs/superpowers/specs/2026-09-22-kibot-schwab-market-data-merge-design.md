# Kibot and Schwab Market Data Merge Design

## Purpose

Create one canonical market-data repository under `data/market_data` that:

- retains the longest trustworthy daily and 5-minute history available;
- uses valid Schwab API bars wherever Schwab overlaps another source;
- uses the purchased Kibot history to extend the 24 covered futures series;
- retains existing history for symbols and dates neither source covers;
- produces complete, validated import files for `Harp_Daily` and
  `Harp_Intraday`; and
- keeps 60-minute downloads out of normal operation because 60-minute bars can
  be derived from the 5-minute data when needed.

This change does not alter strategy logic or resample and persist a new
60-minute dataset.

## Source Assets

The immutable purchased files will live at:

```text
data/market_data/source_archives/kibot/2026-09-22/
  Kibot 5 Min Data - Purchased 2026-09-22.zip
  Kibot Daily Data - Purchased 2026-09-22.zip
```

The ZIP files remain byte-for-byte vendor artifacts. A manifest beside them
will record their SHA-256 hashes, sizes, acquisition date, data frequency,
timezone, and symbol inventory. They will not be committed to Git.

Kibot files are headerless CSV records:

- intraday: `Date,Time,Open,High,Low,Close,Volume`
- daily: `Date,Open,High,Low,Close,Volume`

Kibot documents its timestamps as US Eastern by default and its continuous
futures as calendar-rolled and unadjusted. The supplied files and the direct
Schwab overlap confirm Eastern timestamps: an ES bar labeled 09:30 by Kibot
matches the Schwab bar stored at 13:30 UTC during daylight saving time.

References:

- <https://www.kibot.com/>
- <https://www.kibot.com/futures/continuous-futures.html>

## Repository Layout

The active canonical datasets remain:

```text
data/market_data/daily/<SYMBOL>.csv
data/market_data/5min/<SYMBOL>.csv
```

The existing `data/market_data/60min` directory will be moved to:

```text
data/market_data_archives/60min_before_kibot_merge_2026-09-22/
```

The archive will include a manifest of relative paths, sizes, row counts, and
SHA-256 hashes. The active `data/market_data` directory will then contain no
persisted 60-minute dataset.

Merge reports and source manifests will be written beneath:

```text
data/market_data/quality/kibot_merge_2026-09-22/
```

## Canonical Symbols

Kibot symbols will be normalized to the repository's current futures roots:

| Kibot | Canonical | Instrument |
|---|---|---|
| AD | 6A | Australian dollar |
| BP | 6B | British pound |
| CD | 6C | Canadian dollar |
| EU | 6E | Euro FX |
| JY | 6J | Japanese yen |
| SF | 6S | Swiss franc |
| C | ZC | Corn |
| S | ZS | Soybeans |
| W | ZW | Chicago wheat |
| FV | ZF | 5-year Treasury note |
| TY | ZN | 10-year Treasury note |
| TU | ZT | 2-year Treasury note |
| US | ZB | 30-year Treasury bond |
| RP | RP | Continuous Euro FX/British Pound |

The remaining Kibot symbols already match the repository names: `CL`, `ES`,
`GC`, `HG`, `NG`, `NQ`, `PA`, `PL`, `SI`, and `YM`.

`RP` will be added to instrument metadata and the currency family. It will be
Kibot-only until a separately verified Schwab identifier is available. The
downloader must not guess that `/RP` is the equivalent Schwab contract.

## Normalization

Intraday Kibot timestamps will be interpreted with the
`America/Detroit`/US-Eastern daylight-saving rules and stored as UTC ISO-8601
timestamps. Daily dates remain trading dates without timezone conversion.

Normalized rows retain the existing schema. Imported Kibot rows use
`source=kibot` and an acquisition timestamp derived from the import manifest.
The immutable manifest provides the precise vendor asset lineage.

Each source is normalized before merging. Normalization must reject malformed
dates, nonnumeric OHLCV values, timestamps off the five-minute grid, and
unknown symbols rather than silently dropping them.

## Merge Grain and Precedence

The unique keys are:

- daily: `(symbol, date)`
- intraday: `(symbol, timestamp_utc)`

For the 24 purchased symbols, valid candidate rows use this precedence:

1. direct Schwab API data (`source=schwab`);
2. purchased Kibot data (`source=kibot`);
3. existing normalized rows from other sources, including `reviewed_local`,
   `reviewed_yahoo`, and named legacy files.

For symbols Kibot does not cover, the existing merge behavior remains in
place. A source wins only when its row passes validation. A valid Schwab row
keeps both its OHLC values and its volume even when Kibot volume differs.

A Schwab row is ineligible to override Kibot when it has an invalid timestamp,
missing or nonpositive OHLC prices, an impossible OHLC envelope, or unresolved
conflicting duplicates. Zero volume is permitted for instruments or sessions
where zero is legitimate; volume alone never causes a valid Schwab price bar
to lose precedence.

Identical duplicates collapse. Nonidentical duplicates within one source are
a blocking error unless an existing deterministic repair rule resolves them
and records the repair.

## Conflict and Boundary Audits

The merger will emit inspectable CSV and JSON reports with:

- input, valid, rejected, and selected row counts by symbol and source;
- minimum and maximum dates;
- duplicate and missing-field counts;
- overlap counts and source-selection counts;
- OHLC and volume disagreement rates;
- material conflicts with both source values;
- gaps and unexpected interval spacing;
- DST parsing outcomes;
- source-transition dates; and
- discontinuities around Kibot-to-Schwab boundaries and contract rolls.

Material price differences do not automatically disqualify a valid Schwab
bar because the vendors may use different continuous-contract roll dates.
They are reported so the boundary can be reviewed. The merge fails when it
cannot establish a unique valid row, when coverage regresses, or when a
configured required symbol lacks the research window needed by its analysis.

The existing daily-versus-intraday comparison remains diagnostic. Daily bars
are vendor trading-session bars and must not be overwritten automatically by
calendar-day aggregation of intraday bars.

## Reusable Import Flow

A dedicated Kibot ingestion command will:

1. verify the source manifest and ZIP hashes;
2. stream files directly from the ZIPs without unpacking a second raw copy;
3. normalize and validate the purchased records;
4. merge them into staged per-symbol daily and 5-minute files;
5. overlay valid direct Schwab records;
6. add lower-priority existing records only where neither preferred source has
   a valid row;
7. write quality and conflict reports;
8. validate the complete staged repository; and
9. atomically replace active per-symbol files only after every blocking check
   passes.

The command is idempotent: rerunning it with the same inputs produces the same
canonical rows and source choices.

## Ongoing Schwab Downloads

`download_market_data.py` will default to `daily` and `5min`. Documentation,
examples, and scheduled-command examples will use those two frequencies.

Explicit `--frequencies 60min` support remains for an intentional one-off
request, but no default or scheduled path requests it. A normal Schwab update
will merge new bars into the canonical per-symbol files using the same
validation and precedence rules, preserving the older Kibot history. When
`--export-amibroker` is used, the run continues to regenerate the combined
daily and 5-minute AmiBroker files and their completion manifest.

Regression tests will prove that a new Schwab overlap replaces Kibot, a bad
Schwab row falls back to Kibot, older Kibot history survives later downloads,
and explicit 60-minute requests still work.

## AmiBroker Databases

Before replacement, the current databases will be moved to dated archives:

```text
Amibroker/Databases/Archive/Harp_Daily_before_kibot_merge_2026-09-22/
Amibroker/Databases/Archive/Harp_Intraday_before_kibot_merge_2026-09-22/
```

Fresh `Harp_Daily` and `Harp_Intraday` databases will be built at their current
paths. `Harp_Intraday` will use a five-minute base interval, zero time shift,
all-session display, and enough bars to hold the longest purchased series.
The importer must verify capacity before loading rather than allowing AmiBroker
to truncate history silently.

The Windows importer will load only the validated generated files:

```text
data/market_data/amibroker/daily.csv
data/market_data/amibroker/5min.csv
```

After import, a database verification step will export per-symbol row counts
and date ranges. It must confirm ES coverage in both winter and summer of the
2009-2019 research period and confirm the latest expected date before the
experiment timezone preflight can pass.

## Safe Rollout

The rollout order is:

1. move and hash the vendor ZIPs;
2. stage and validate the merged repository;
3. archive active 60-minute data;
4. atomically publish daily and 5-minute files;
5. generate and validate AmiBroker import artifacts;
6. archive existing AmiBroker databases;
7. rebuild and verify both databases on Windows; and
8. rerun the timezone preflight, then the three-strategy pilot.

No active normalized file or AmiBroker database is removed before its archive
and manifest exist. Any failure leaves the last validated active repository
and current databases recoverable.

## Verification

Automated tests will cover symbol mapping, timezone conversion across standard
and daylight time, schema validation, duplicate handling, precedence, invalid
Schwab fallback, atomic publication, idempotence, downloader defaults,
explicit 60-minute compatibility, AmiBroker manifest generation, and archive
manifests.

The real-data audit will record coverage and conflicts for every purchased
symbol. Completion requires all normalized integrity tests to pass, all 24
Kibot files to appear under the expected canonical names, no active 60-minute
directory, reproducible AmiBroker exports, and verified data in both rebuilt
databases.
