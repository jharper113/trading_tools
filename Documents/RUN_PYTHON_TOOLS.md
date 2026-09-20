# AmiBroker ABS settings and Python scripts

## Which files AmiBroker loads

AmiBroker loads the .ABS files in AmiBroker_Settings. The JSON files are not
AmiBroker presets. They are Python-only analyzer profiles containing thresholds
and labels.

- Use 2026_Tools_Daily_WFA_Settings.ABS for daily AFL strategies.
- Use 2026_Tools_Intraday_15m_WFA_Settings.ABS for intraday AFL strategies.

The intraday preset analyzes 15-minute bars. Your database may store 5-minute
bars; AmiBroker compresses them to 15 minutes for the analysis.

## Load and verify an ABS file

1. Open AmiBroker Analysis.
2. Choose File > Load Settings and select the appropriate .ABS.
3. Confirm these values before running:

| Setting | Daily | Intraday |
|---|---:|---:|
| Periodicity | Daily | 15-minute |
| Initial equity | $100,000 | $100,000 |
| Futures mode | On | On |
| Minimum shares/contracts | 1 | 1 |
| Positions | Long and short | Long and short |
| Allow same-bar exit | On | On |
| Reverse entry signal forces exit | Off | Off |
| Commission mode | Per share/contract | Per share/contract |
| Commission | $3.76 per contract side | $3.76 per contract side |
| Previous-bar equity sizing | On | On |
| Optimization target | CAR/MDD | CAR/MDD |

Confirm the rolling WFA schedule:

- First IS: September 28, 2009 through September 27, 2014
- First OOS: September 28, 2014 through September 27, 2015
- Step: 12 months
- Last complete OOS end: September 27, 2024
- Rolling, not anchored

Do not continue if AmiBroker changes the displayed values. ABS is a binary,
version-sensitive format, so this UI check is mandatory.

## Easiest way to analyze FX_6E

Extract the entire package so Python_Tools and Analyzer_Profiles remain sibling
directories. Open a terminal in Python_Tools.

The analyzer requires Python 3 and pandas. Install pandas once if needed:

~~~bat
python -m pip install pandas
~~~

On Ubuntu, use:

~~~bash
python3 -m pip install pandas
~~~

### Windows

Double-click Run_FX_6E_Analysis.bat, or run:

~~~bat
python run_fx_6e_analysis.py "Z:\04_Code\Amibroker\Reports\Daily_OptResults"
~~~

No flags are required. The first value is simply the directory containing the
AmiBroker optimization CSV files.

### Ubuntu/Linux

~~~bash
python3 run_fx_6e_analysis.py "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Reports/Daily_OptResults"
~~~

The script creates an HTML report plus a JSON audit file. The HTML is the
report to read. The JSON is used by Python; AmiBroker does not load it.

## Analyze another strategy without flags

Syntax:

~~~text
python run_strategy_analysis.py TIMEFRAME RESULTS_DIRECTORY CORE_SYMBOL REPORT_NAME
~~~

Daily example:

~~~bat
python run_strategy_analysis.py daily "Z:\04_Code\Amibroker\Reports\FX_6E_GAP_FADE" 6E FX_6E_GAP_FADE
~~~

Intraday example:

~~~bat
python run_strategy_analysis.py intraday "Z:\04_Code\Amibroker\Reports\ES_ORB" ES ES_ORB
~~~

The values are positional:

1. daily or intraday
2. Directory containing that strategy's CSV exports
3. Intended core symbol, without or with a slash
4. Optional report filename

## Original analyzer with flags

~~~text
python analyze_cross_sector_optimization.py INPUT_DIRECTORY
    --settings ANALYZER_PROFILE
    --core CORE_SYMBOL
    --output REPORT_FILE
~~~

- INPUT_DIRECTORY: folder containing AmiBroker CSV exports.
- --settings: Python analyzer profile, not an AmiBroker settings file.
- --core: intended primary market, such as 6E.
- --output: where to save the HTML report.
- --json: optional location for JSON audit output.

FX example:

~~~bat
python analyze_cross_sector_optimization.py "Z:\04_Code\Amibroker\Reports\Daily_OptResults" --settings "..\Analyzer_Profiles\Daily_Analyzer_Profile.json" --core 6E --output "FX_6E_review.html"
~~~

## Important current limitation

The FX_6E optimization can be run now. The complete all-strategy WFA package
still needs formula-specific projects, unique result folders, and removal of
the internal 2018/2019 date gates before all 138 AFLs can be processed safely.
Do not launch the full all-strategy WFA yet.
