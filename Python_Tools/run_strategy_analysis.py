#!/usr/bin/env python3
"""Analyze one strategy's result folder using simple positional values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from analyze_cross_sector_optimization import analyze, render_html


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    if len(sys.argv) not in (4, 5):
        raise SystemExit(
            "Usage:\n"
            "  python run_strategy_analysis.py daily RESULTS_DIR CORE_SYMBOL [REPORT_NAME]\n"
            "  python run_strategy_analysis.py intraday RESULTS_DIR CORE_SYMBOL [REPORT_NAME]"
        )
    timeframe = sys.argv[1].lower()
    if timeframe not in {"daily", "intraday"}:
        raise SystemExit("First value must be daily or intraday")
    results_dir = Path(sys.argv[2])
    core_symbol = sys.argv[3].lstrip("/")
    report_name = sys.argv[4] if len(sys.argv) == 5 else f"{core_symbol}_{timeframe}_review"
    profile_name = (
        "Daily_Analyzer_Profile.json"
        if timeframe == "daily"
        else "Intraday_15m_Analyzer_Profile.json"
    )
    profile = ROOT / "Analyzer_Profiles" / profile_name
    if not results_dir.exists():
        raise SystemExit(f"Results directory was not found: {results_dir}")
    result = analyze(results_dir, profile, [core_symbol])
    output = results_dir.parent / f"{report_name}.html"
    output.write_text(render_html(result), encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Decision: {result['decision']}")
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
