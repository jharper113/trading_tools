"""Behavior checks for the profile-driven analyzer and its packaged entry points."""

import importlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "Analyzer_Profiles"
DAILY = PROFILES / "Daily_Analyzer_Profile.json"
INTRADAY = PROFILES / "Intraday_15m_Analyzer_Profile.json"


@pytest.fixture(params=["analyze_cross_sector_optimization", "Python_Tools.analyze_cross_sector_optimization"])
def analyzer(request):
    return importlib.import_module(request.param)


def write_results(directory, symbol, profits=(100, 200), factors=(1.2, 1.4), trades=(60, 100)):
    path = directory / f"Daily_{symbol}.csv"
    pd.DataFrame({"Net Profit": profits, "Profit Factor": factors, "# Trades": trades}).to_csv(path, index=False)
    return path


def test_load_directory_normalizes_numbers_and_attaches_source(analyzer, tmp_path):
    path = tmp_path / "Strategy_Daily_6E.csv"
    pd.DataFrame({
        " Net Profit ": ["1,200", "-250"],
        "Profit Factor": ["1.25", "0.8"],
        "# Trades": ["120", "60"],
        "CAR": ["12.5%", "-2%"],
        "Opt Lookback": [10, 20],
    }).to_csv(path, index=False)
    rows, files, parameters = analyzer.load_directory(tmp_path)
    assert files == [path]
    assert parameters == ["Opt Lookback"]
    assert [row["Net Profit"] for row in rows] == [1200, -250]
    assert [row["CAR"] for row in rows] == [12.5, -2]
    assert [row["# Trades"] for row in rows] == [120, 60]
    assert all(row["Symbol"] == "6E" and row["Sector"] == "Currencies" for row in rows)
    assert all(row["Source File"] == path.name for row in rows)


def test_empty_directory_is_rejected(analyzer, tmp_path):
    with pytest.raises(FileNotFoundError, match="No optimization CSV"):
        analyzer.load_directory(tmp_path)


@pytest.mark.parametrize("missing", ["Net Profit", "Profit Factor", "# Trades"])
def test_missing_required_column_is_rejected(analyzer, tmp_path, missing):
    path = write_results(tmp_path, "6E")
    pd.read_csv(path).drop(columns=[missing]).to_csv(path, index=False)
    with pytest.raises(ValueError) as error:
        analyzer.load_directory(tmp_path)
    assert path.name in str(error.value)
    assert missing in str(error.value)


def test_failed_family_and_broad_scopes_do_not_veto_passing_core(analyzer, tmp_path):
    write_results(tmp_path, "6E")
    write_results(tmp_path, "6B", (-100, -200), (0.5, 0.7))
    write_results(tmp_path, "CL", (-100, -200), (0.5, 0.7))
    result = analyzer.analyze(tmp_path, DAILY, [" /6e "])
    assert result["core_symbols"] == ["6E"]
    assert result["core"]["verdict"] == "PASS"
    assert result["core"]["selected_rows"] == 2
    assert result["family"]["selected_rows"] == 4
    assert result["broad"]["selected_rows"] == 6
    assert result["family"]["verdict"] == "FAIL"
    assert result["broad"]["verdict"] == "FAIL"
    assert result["decision"] == "ADVANCE TO WFA"


@pytest.mark.parametrize("profits,factors,trades,failed_gate", [
    ((100, -100), (1.2, 1.4), (60, 100), "profitable_paramsets"),
    ((100, 200), (0.8, 1.0), (60, 100), "median_profit_factor"),
    ((100, 200), (1.2, 1.4), (20, 40), "median_trades"),
])
def test_each_core_gate_can_reject_strategy(analyzer, tmp_path, profits, factors, trades, failed_gate):
    write_results(tmp_path, "6E", profits, factors, trades)
    write_results(tmp_path, "CL", (100, 200, 300, 400), (2, 2, 2, 2), (200, 200, 200, 200))
    result = analyzer.analyze(tmp_path, DAILY, ["6E"])
    assert result["broad"]["verdict"] == "PASS"
    assert result["core"]["verdict"] == "FAIL"
    assert result["core"]["gates"][failed_gate] is False
    assert sum(result["core"]["gates"].values()) == 2
    assert result["decision"] == "REJECT OR REVISE"


def test_profile_controls_trade_threshold_and_includes_boundary(analyzer, tmp_path):
    write_results(tmp_path, "6E", trades=(60, 60), factors=(1.1, 1.1))
    daily = analyzer.analyze(tmp_path, DAILY, ["6E"])
    intraday = analyzer.analyze(tmp_path, INTRADAY, ["6E"])
    assert daily["core"]["verdict"] == "PASS"
    assert intraday["core"]["verdict"] == "FAIL"
    assert intraday["core"]["gates"]["median_trades"] is False
    assert daily["periodicity"]["seconds"] == 86400
    assert intraday["periodicity"]["seconds"] == 900


def test_zero_trade_rows_are_excluded_from_metrics(analyzer, tmp_path):
    write_results(tmp_path, "6E", (100, 200, -9999), (1.2, 1.4, 0), (60, 100, 0))
    result = analyzer.analyze(tmp_path, DAILY, ["6E"])
    core = result["core"]
    assert core["selected_rows"] == 3
    assert core["eligible_rows"] == 2
    assert core["zero_trade_rows"] == 1
    assert core["profitable_paramsets_pct"] == 100
    assert core["median_profit_factor"] == 1.3
    assert core["median_trades"] == 80
    assert core["verdict"] == "PASS"
    assert result["symbols"] == [{
        "Symbol": "6E", "Sector": "Currencies", "Paramsets": 3,
        "Eligible Paramsets": 2, "Profitable %": 100.0,
        "Median PF": 1.3, "Median Trades": 80.0, "Status": "ECONOMIC",
    }]


@pytest.mark.parametrize("core_symbol,selected_rows", [("6E", 2), ("ES", 0)])
def test_no_eligible_core_data_does_not_advance(analyzer, tmp_path, core_symbol, selected_rows):
    write_results(tmp_path, "6E", (0, 0), (0, 0), (0, 0))
    result = analyzer.analyze(tmp_path, DAILY, [core_symbol])
    assert result["core"]["verdict"] == "NO DATA"
    assert result["core"]["selected_rows"] == selected_rows
    assert result["core"]["eligible_rows"] == 0
    assert result["core"]["zero_trade_rows"] == selected_rows
    assert result["decision"] == "REJECT OR REVISE"
    assert result["symbols"][0]["Status"] == "DATA EXCLUDED"


def test_spot_fx_is_excluded_from_broad_universe(analyzer, tmp_path):
    write_results(tmp_path, "6E")
    write_results(tmp_path, "EURUSD", (-100, -200), (0.1, 0.2))
    result = analyzer.analyze(tmp_path, DAILY, ["6E"])
    assert result["broad_symbols"] == ["6E"]
    assert result["broad"]["selected_rows"] == 2
    assert result["broad"]["verdict"] == "PASS"
    assert {row["Symbol"] for row in result["symbols"]} == {"6E", "EURUSD"}


def test_html_reports_verdicts_and_escapes_profile_name(analyzer, tmp_path):
    write_results(tmp_path, "6E")
    profile = json.loads(DAILY.read_text())
    profile["profile_name"] = "<script>alert(1)</script>"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    html = analyzer.render_html(analyzer.analyze(tmp_path, profile_path, ["6E"]))
    assert "Decision: ADVANCE TO WFA" in html
    assert "Core market" in html
    assert "Same-family transfer" in html
    assert "Broad-universe diagnostic" in html
    assert "<td>6E</td>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


@pytest.mark.parametrize("script", ["analyze_cross_sector_optimization.py", "Python_Tools/analyze_cross_sector_optimization.py"])
@pytest.mark.parametrize("custom_json", [False, True])
def test_cli_writes_html_and_json_from_outside_repository(tmp_path, script, custom_json):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    write_results(inputs, "6E")
    output = tmp_path / "reports" / "review.html"
    audit = output.with_suffix(".json") if not custom_json else tmp_path / "audit.json"
    command = [sys.executable, str(ROOT / script), str(inputs), "--settings", str(DAILY),
               "--core", "/6E", "--output", str(output)]
    if custom_json:
        command += ["--json", str(audit)]
    process = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(process.stdout)["decision"] == "ADVANCE TO WFA"
    assert "Decision: ADVANCE TO WFA" in output.read_text()
    result = json.loads(audit.read_text())
    assert result["core_symbols"] == ["6E"]
    assert result["input_file_count"] == 1
    assert result["core"]["median_trades"] == 80


@pytest.mark.parametrize("script,args,report_name,decision", [
    ("run_fx_6e_analysis.py", [], "FX_6E_GAP_FADE_Optimization_Review", "ADVANCE TO WFA"),
    ("run_strategy_analysis.py", ["daily"], "6E_daily_review", "ADVANCE TO WFA"),
    ("run_strategy_analysis.py", ["intraday"], "6E_intraday_review", "REJECT OR REVISE"),
])
def test_packaged_launchers_find_profiles(tmp_path, script, args, report_name, decision):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    write_results(inputs, "6E")
    command = [sys.executable, str(ROOT / "Python_Tools" / script), *args, str(inputs)]
    if args:
        command.append("/6E")
    subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert f"Decision: {decision}" in (tmp_path / f"{report_name}.html").read_text()
    result = json.loads((tmp_path / f"{report_name}.json").read_text())
    assert result["decision"] == decision
    assert result["core_symbols"] == ["6E"]
