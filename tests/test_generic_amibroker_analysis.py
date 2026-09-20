import json
from pathlib import Path

import pandas as pd

from amibroker_analysis_common import (
    detect_result_type,
    read_project_context,
    resolve_core_symbols,
)
from analyze_walk_forward_results import analyze_walk_forward
from run_amibroker_analysis import run_analysis


def write_project(path, formula, periodicity=0, interval=86400, wfa=False):
    is_start = "2009-09-28" if wfa else "1970-01-01"
    path.write_text(
        "<?xml version='1.0'?><AnalysisDoc><GeneralSettings>"
        f"<FormulaPath>{formula}</FormulaPath>"
        f"<Periodicity>{periodicity}</Periodicity>"
        f"<ChartInterval>{interval}</ChartInterval>"
        "</GeneralSettings><WalkForwardSettings>"
        f"<ISStartDate>{is_start}</ISStartDate>"
        "</WalkForwardSettings></AnalysisDoc>",
        encoding="utf-8",
    )


def test_project_context_comes_from_loaded_afl_and_periodicity(tmp_path):
    project = tmp_path / "Daily_Project.apx"
    write_project(project, r"Z:\Strategies\IDEA0282.afl")

    context = read_project_context(project)

    assert context.strategy_name == "IDEA0282"
    assert context.formula_path == r"Z:\Strategies\IDEA0282.afl"
    assert context.timeframe == "Daily"
    assert context.project_mode == "optimization"


def test_result_type_detection_distinguishes_optimization_and_wfa(tmp_path):
    optimization = tmp_path / "optimization"
    optimization.mkdir()
    pd.DataFrame([{
        "Net Profit": 100,
        "Profit Factor": 1.2,
        "# Trades": 50,
        "Opt Lookback": 20,
    }]).to_csv(optimization / "ES.csv", index=False)

    wfa = tmp_path / "wfa"
    wfa.mkdir()
    pd.DataFrame([{
        "Mode": "OOS",
        "Net Profit": 100,
        "Profit Factor": 1.2,
        "# Trades": 10,
    }]).to_csv(wfa / "ES.csv", index=False)

    assert detect_result_type(optimization) == "optimization"
    assert detect_result_type(wfa) == "wfa"


def test_mixed_optimization_and_wfa_directory_is_rejected(tmp_path):
    pd.DataFrame([{
        "Net Profit": 100,
        "Profit Factor": 1.2,
        "# Trades": 50,
        "Opt Lookback": 20,
    }]).to_csv(tmp_path / "ES.csv", index=False)
    pd.DataFrame([{
        "Mode": "OOS",
        "Net Profit": 100,
        "Profit Factor": 1.2,
        "# Trades": 10,
    }]).to_csv(tmp_path / "NQ.csv", index=False)

    try:
        detect_result_type(tmp_path)
    except ValueError as exc:
        assert "mixes optimization and WFA" in str(exc)
    else:
        raise AssertionError("Expected mixed result schemas to be rejected")


def test_registry_resolves_core_by_afl_stem(tmp_path):
    registry = tmp_path / "strategy_analysis_registry.json"
    registry.write_text(json.dumps({"strategies": {
        "IDEA0282": {"core_symbols": ["/GC", "ZC"]}
    }}), encoding="utf-8")

    assert resolve_core_symbols(registry, "IDEA0282") == ["GC", "ZC"]
    assert resolve_core_symbols(registry, "UNKNOWN") == []


def test_wfa_analyzer_uses_only_oos_rows_for_admission_metrics(tmp_path):
    pd.DataFrame([
        {"Mode": "IS", "Net Profit": 500, "Profit Factor": 1.8, "# Trades": 30, "CAR/MDD": 1.5},
        {"Mode": "OOS", "Net Profit": 100, "Profit Factor": 1.2, "# Trades": 8, "CAR/MDD": 0.7},
        {"Mode": "OOS", "Net Profit": -40, "Profit Factor": 0.9, "# Trades": 6, "CAR/MDD": -0.2},
    ]).to_csv(tmp_path / "ES.csv", index=False)

    result = analyze_walk_forward(tmp_path, ["ES"])

    assert result["result_type"] == "wfa"
    assert result["symbols"][0]["Symbol"] == "ES"
    assert result["symbols"][0]["OOS Folds"] == 2
    assert result["symbols"][0]["Profitable OOS %"] == 50.0
    assert result["symbols"][0]["Median OOS PF"] == 1.05
    assert result["symbols"][0]["Total OOS Net Profit"] == 60.0


def test_generic_runner_names_output_from_project_and_routes_wfa(tmp_path):
    project = tmp_path / "project.apx"
    write_project(project, r"Z:\Strategies\ES_ORB.afl", 8, 900, wfa=True)
    results = tmp_path / "results"
    results.mkdir()
    pd.DataFrame([
        {"Mode": "OOS", "Net Profit": 100, "Profit Factor": 1.2, "# Trades": 8},
    ]).to_csv(results / "ES.csv", index=False)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"strategies": {
        "ES_ORB": {"core_symbols": ["ES"]}
    }}), encoding="utf-8")
    output = tmp_path / "reports"

    result, files = run_analysis(project, results, output, registry)

    assert result["strategy"] == "ES_ORB"
    assert result["timeframe"] == "Intraday_15m"
    assert result["result_type"] == "wfa"
    assert files["html"].name == "ES_ORB_Intraday_15m_WFA_Review.html"
    assert files["json"].exists()
    assert files["manifest"].exists()


def test_unknown_strategy_does_not_receive_false_pass_verdict(tmp_path):
    project = tmp_path / "project.apx"
    write_project(project, r"Z:\Strategies\UNKNOWN.afl")
    results = tmp_path / "results"
    results.mkdir()
    pd.DataFrame([{
        "Net Profit": 100,
        "Profit Factor": 1.4,
        "# Trades": 100,
        "Opt Lookback": 20,
    }]).to_csv(results / "ES.csv", index=False)
    registry = tmp_path / "registry.json"
    registry.write_text('{"strategies": {}}', encoding="utf-8")

    result, _ = run_analysis(project, results, tmp_path / "reports", registry)

    assert result["decision"] == "CORE SYMBOL REQUIRED"
    assert result["core_symbols"] == []


def test_project_and_export_type_mismatch_is_rejected(tmp_path):
    project = tmp_path / "Optimization_Project.apx"
    write_project(project, r"Z:\Strategies\IDEA0282.afl", wfa=False)
    results = tmp_path / "results"
    results.mkdir()
    pd.DataFrame([{
        "Mode": "OOS",
        "Net Profit": 100,
        "Profit Factor": 1.2,
        "# Trades": 10,
    }]).to_csv(results / "GC.csv", index=False)

    try:
        run_analysis(project, results, tmp_path / "reports", tmp_path / "missing.json")
    except ValueError as exc:
        assert "project is optimization" in str(exc)
        assert "exports are wfa" in str(exc)
    else:
        raise AssertionError("Expected a project/export mismatch error")
