# Generic AmiBroker Optimization and WFA Analysis

Use one command for every strategy. The runner reads the loaded AFL and
periodicity from the APX project, then detects whether the CSV folder contains
optimization results or walk-forward summaries.

```bash
cd /home/jon/Dropbox/HarpFolders/04_Code/Python/trading_tools

python run_amibroker_analysis.py \
  "/path/to/current_project.apx" \
  "/path/to/one_strategy_result_folder"
```

`run_strategy_analysis.py` is an equivalent compatibility command:

```bash
python run_strategy_analysis.py PROJECT.apx RESULTS_DIRECTORY
```

## Core symbols

The strategy name comes from `FormulaPath` in the APX project. Core symbols are
looked up in `strategy_analysis_registry.json`. Add an entry when introducing a
strategy:

```json
{
  "strategies": {
    "IDEA0282": {
      "core_symbols": ["GC", "ZC"]
    }
  }
}
```

You may override the registry by placing symbols after the two required values:

```bash
python run_amibroker_analysis.py PROJECT.apx RESULTS_DIRECTORY GC ZC
```

If no core symbol is declared, the runner still creates symbol diagnostics but
returns `CORE SYMBOL REQUIRED`; it will not create a false pass verdict.

## Why optimization and WFA use different analyzers

Optimization CSVs contain many parameter combinations per symbol. They are
evaluated for broad profitable neighborhoods, median profit factor, and trade
count.

WFA summary CSVs contain IS/OOS folds. The WFA analyzer uses only rows marked
`OOS` (or `Out of Sample`) for reported fold metrics. IS rows remain in the
source data but cannot make OOS results look better.

The user-facing command is the same; routing is automatic.

## Required folder separation

Use a new directory for each strategy and run type. Do not mix optimization and
WFA exports or reuse a folder for another strategy.

```text
Reports/
  IDEA0282/
    Daily/
      Optimization/
      WFA/
  ES_ORB/
    Intraday_15m/
      Optimization/
      WFA/
```

The runner rejects a mixed optimization/WFA folder and rejects a WFA export
paired with an optimization APX (or the reverse).

## Outputs

Reports are automatically stored below `Analysis_Reports` unless `--output-dir`
is supplied. Each run creates:

- HTML report
- JSON data file
- JSON audit manifest containing the project hash, AFL path, result type,
  timeframe, and declared core symbols

## Existing FX_6E helper

`Python_Tools/run_fx_6e_analysis.py` is a legacy convenience command. New work
should use `run_amibroker_analysis.py`; the generic runner is not tied to 6E or
any other strategy.

## WFA trade-list exports

The generic WFA summary analyzer is for AmiBroker `ExportWalkForward` summary
CSVs. Detailed WFA trade lists continue to use:

1. `normalize_amibroker_wfa_trade_export.py`
2. `prepare_amibroker_wfa_for_strategy_analysis.py`
3. `analyze_strategy_performance.py`

Those trade-level tools answer different questions and are not substitutes for
the fold-level WFA summary.
