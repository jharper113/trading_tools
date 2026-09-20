#!/usr/bin/env python3
"""Analyze AmiBroker walk-forward summary exports by OOS fold."""

from __future__ import annotations

import html
import math
from pathlib import Path

import pandas as pd

from workflow import normalize_symbol, symbol_from_path


def _number(value):
    if value is None:
        return math.nan
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


def _is_oos(value):
    text = str(value).strip().upper().replace("_", " ").replace("-", " ")
    return text == "OOS" or "OUT OF SAMPLE" in text


def _median(frame, column):
    if column not in frame.columns:
        return None
    values = pd.Series([_number(value) for value in frame[column]]).dropna()
    return None if values.empty else round(float(values.median()), 4)


def analyze_walk_forward(input_dir, core_symbols=None):
    input_dir = Path(input_dir)
    files = sorted(input_dir.glob("*.csv"))
    summaries = []
    used_files = []
    for path in files:
        frame = pd.read_csv(path, encoding="utf-8-sig")
        frame.columns = [str(column).strip() for column in frame.columns]
        if "Mode" not in frame.columns or "Net Profit" not in frame.columns:
            continue
        oos = frame.loc[frame["Mode"].map(_is_oos)].copy()
        if oos.empty:
            continue
        profits = pd.Series([_number(value) for value in oos["Net Profit"]]).dropna()
        symbol = symbol_from_path(path)
        summaries.append({
            "Symbol": symbol,
            "OOS Folds": int(len(oos)),
            "Profitable OOS %": round(100.0 * float((profits > 0).sum()) / len(oos), 2),
            "Total OOS Net Profit": round(float(profits.sum()), 4),
            "Median OOS PF": _median(oos, "Profit Factor"),
            "Median OOS Trades": _median(oos, "# Trades"),
            "Median OOS CAR/MDD": _median(oos, "CAR/MDD"),
            "Source File": path.name,
        })
        used_files.append(path)
    if not summaries:
        raise ValueError(f"No WFA summary CSV containing OOS rows was found in {input_dir}")
    core = [normalize_symbol(symbol) for symbol in (core_symbols or [])]
    return {
        "result_type": "wfa",
        "input_directory": str(input_dir.resolve()),
        "input_file_count": len(used_files),
        "core_symbols": core,
        "symbols": summaries,
        "decision": "REVIEW WFA EVIDENCE" if core else "CORE SYMBOL REQUIRED",
        "interpretation": (
            "WFA metrics use out-of-sample rows only. IS rows are retained in the "
            "source exports but do not determine the reported OOS evidence."
        ),
    }


def render_wfa_html(result):
    rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(str(row.get(key, '')))}</td>"
            for key in (
                "Symbol", "OOS Folds", "Profitable OOS %", "Total OOS Net Profit",
                "Median OOS PF", "Median OOS Trades", "Median OOS CAR/MDD",
            )
        ) + "</tr>"
        for row in result["symbols"]
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Walk-Forward Review</title><style>body{{font-family:system-ui,sans-serif;background:#f5f7fb;color:#182033;margin:0}}main{{max-width:1100px;margin:auto;padding:32px}}.note{{background:#fff4d6;border-left:5px solid #d69400;padding:14px}}table{{width:100%;border-collapse:collapse;background:white;margin-top:22px}}th,td{{padding:10px;border-bottom:1px solid #e3e7ef;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#23395d;color:white}}</style></head><body><main><p>AMIBROKER RESEARCH WORKFLOW</p><h1>{html.escape(result.get('strategy', 'Strategy'))} walk-forward review</h1><p><b>{html.escape(result['decision'])}</b></p><div class='note'>{html.escape(result['interpretation'])}</div><table><thead><tr><th>Symbol</th><th>OOS Folds</th><th>Profitable OOS %</th><th>Total OOS Net Profit</th><th>Median OOS PF</th><th>Median OOS Trades</th><th>Median OOS CAR/MDD</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>"""
