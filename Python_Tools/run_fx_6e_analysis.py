#!/usr/bin/env python3
"""Run the FX_6E optimization review without requiring command-line flags."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from analyze_cross_sector_optimization import analyze, render_html


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parent
PROFILE = PACKAGE_ROOT / "Analyzer_Profiles" / "Daily_Analyzer_Profile.json"
DEFAULT_WINDOWS_RESULTS = Path(r"Z:\04_Code\Amibroker\Reports\Daily_OptResults")


def main():
    if len(sys.argv) > 3:
        raise SystemExit(
            "Usage: python run_fx_6e_analysis.py [results_directory] [output_html]"
        )
    configured = os.environ.get("AMIBROKER_DAILY_OPT_RESULTS")
    results_dir = Path(sys.argv[1]) if len(sys.argv) >= 2 else (
        Path(configured) if configured else DEFAULT_WINDOWS_RESULTS
    )
    output = Path(sys.argv[2]) if len(sys.argv) >= 3 else (
        results_dir.parent / "FX_6E_GAP_FADE_Optimization_Review.html"
    )
    if not results_dir.exists():
        raise SystemExit(
            "Results directory was not found:\n"
            f"  {results_dir}\n\n"
            "Pass it as the first value, without a flag:\n"
            '  python run_fx_6e_analysis.py "FULL_PATH_TO_RESULTS"'
        )
    result = analyze(results_dir, PROFILE, ["6E"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(result), encoding="utf-8")
    json_output = output.with_suffix(".json")
    json_output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Decision: {result['decision']}")
    print(f"HTML report: {output}")
    print(f"JSON audit data: {json_output}")


if __name__ == "__main__":
    main()
