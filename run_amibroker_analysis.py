#!/usr/bin/env python3
"""Run strategy-agnostic AmiBroker optimization or WFA analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from amibroker_analysis_common import (
    detect_result_type,
    read_project_context,
    resolve_core_symbols,
)
from analyze_cross_sector_optimization import analyze, render_html
from analyze_walk_forward_results import analyze_walk_forward, render_wfa_html


HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "strategy_analysis_registry.json"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_path(context):
    profiles = {
        "Daily": "Daily_Analyzer_Profile.json",
        "Intraday_5m": "Intraday_5m_Analyzer_Profile.json",
        "Intraday_15m": "Intraday_15m_Analyzer_Profile.json",
        "Intraday_60m": "Intraday_60m_Analyzer_Profile.json",
    }
    try:
        name = profiles[context.timeframe]
    except KeyError as exc:
        raise ValueError(f"No analyzer profile for {context.timeframe}") from exc
    return HERE / "Analyzer_Profiles" / name


def run_analysis(project_path, results_dir, output_root, registry_path=DEFAULT_REGISTRY, core_symbols=None):
    context = read_project_context(project_path)
    result_type = detect_result_type(results_dir)
    if context.project_mode != result_type:
        raise ValueError(
            f"AmiBroker project is {context.project_mode}, but the exports are "
            f"{result_type}. Select the matching APX project and result folder."
        )
    declared_core = [str(symbol).lstrip("/").upper() for symbol in (core_symbols or [])]
    core = declared_core or resolve_core_symbols(registry_path, context.strategy_name)

    if result_type == "optimization":
        if core:
            result = analyze(results_dir, _profile_path(context), core)
        else:
            from analyze_cross_sector_optimization import load_directory, symbol_summary
            rows, files, parameters = load_directory(results_dir)
            result = {
                "profile": context.timeframe,
                "periodicity": {"seconds": context.interval_seconds},
                "input_directory": str(Path(results_dir).resolve()),
                "input_file_count": len(files),
                "parameter_columns": parameters,
                "core_symbols": [],
                "symbols": symbol_summary(rows),
                "decision": "CORE SYMBOL REQUIRED",
                "interpretation": "Declare the intended core market before making an advancement decision.",
                "caveat": "Symbol diagnostics are shown, but no strategy verdict was assigned.",
                "core": {"verdict": "NO DATA", "profitable_paramsets_pct": 0, "median_profit_factor": 0, "median_trades": 0, "zero_trade_rows": 0, "gates": {}},
                "family": {"verdict": "NO DATA", "profitable_paramsets_pct": 0, "median_profit_factor": 0, "median_trades": 0, "zero_trade_rows": 0, "gates": {}},
                "broad": {"verdict": "NO DATA", "profitable_paramsets_pct": 0, "median_profit_factor": 0, "median_trades": 0, "zero_trade_rows": 0, "gates": {}},
            }
        renderer = render_html
        type_label = "Optimization"
    else:
        result = analyze_walk_forward(results_dir, core)
        renderer = render_wfa_html
        type_label = "WFA"

    result.update({
        "strategy": context.strategy_name,
        "timeframe": context.timeframe,
        "project_path": str(Path(project_path).resolve()),
        "formula_path": context.formula_path,
        "detected_project_mode": context.project_mode,
        "result_type": result_type,
        "core_symbols": core,
    })
    output_dir = Path(output_root) / context.strategy_name / context.timeframe / type_label
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{context.strategy_name}_{context.timeframe}_{type_label}_Review"
    html_path = output_dir / f"{stem}.html"
    json_path = output_dir / f"{stem}.json"
    manifest_path = output_dir / f"{stem}_Manifest.json"
    html_path.write_text(renderer(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    manifest = {
        "strategy": context.strategy_name,
        "timeframe": context.timeframe,
        "result_type": result_type,
        "project": str(Path(project_path).resolve()),
        "project_sha256": _sha256(project_path),
        "formula_path": context.formula_path,
        "results_directory": str(Path(results_dir).resolve()),
        "core_symbols": core,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return result, {"html": html_path, "json": json_path, "manifest": manifest_path}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="AmiBroker APX project currently holding the strategy")
    parser.add_argument("results", type=Path, help="Directory containing one run's CSV exports")
    parser.add_argument("core", nargs="*", help="Optional core symbols; registry is used when omitted")
    parser.add_argument("--output-dir", type=Path, help="Root report directory")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output = args.output_dir or args.results.parent / "Analysis_Reports"
    result, files = run_analysis(args.project, args.results, output, args.registry, args.core)
    print(f"Strategy: {result['strategy']}")
    print(f"Input type: {result['result_type']}")
    print(f"Decision: {result['decision']}")
    print(f"HTML report: {files['html']}")
    print(f"JSON report: {files['json']}")
    print(f"Manifest: {files['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
