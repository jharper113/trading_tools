#!/usr/bin/env python3
"""Standalone, scope-aware AmiBroker cross-sector optimization analyzer.

The primary verdict is the declared core market. Same-family and broad-universe
results are reported separately so a specialist strategy is not rejected merely
because it is not universal. Zero-trade rows are data-quality exclusions, not
economic losses.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import pandas as pd

from workflow import evaluate_scope, load_profile, normalize_symbol, symbol_from_path


SECTOR_SYMBOLS = {
    "Equity indexes": {"ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "EMD", "NKD", "SPY"},
    "Currencies": {"6A", "6B", "6C", "6E", "6J", "6M", "6N", "6S", "DX", "MXP"},
    "Energy": {"CL", "MCL", "BZ", "NG", "RB", "HO"},
    "Metals": {"GC", "MGC", "SI", "SIL", "HG", "PL", "PA"},
    "Crypto": {"BTC", "MBT", "ETH", "MET", "SOL", "MSL", "XRP"},
    "Agricultural": {"ZC", "ZW", "ZS", "ZM", "ZL", "ZO", "KE", "HE", "LE", "GF"},
    "Rates": {"ZT", "ZF", "ZN", "ZB", "UB", "GE"},
    "Softs": {"KC", "SB", "CC", "CT", "OJ", "LB___CCB", "RF___CCB"},
}
SECTOR_BY_SYMBOL = {symbol: sector for sector, symbols in SECTOR_SYMBOLS.items() for symbol in symbols}
NON_FUTURES = {"AUDUSD", "EURJPY", "EURUSD", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"}
REQUIRED = {"Net Profit", "Profit Factor", "# Trades"}


def _clean_number(value):
    if value is None:
        return math.nan
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "")
        if value.upper() in {"", "N/A", "NAN", "INF", "-INF"}:
            return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def load_directory(input_dir):
    rows = []
    files = sorted(Path(input_dir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No optimization CSV files found in {input_dir}")
    parameter_columns = set()
    for path in files:
        frame = pd.read_csv(path)
        frame.columns = [str(column).strip() for column in frame.columns]
        missing = REQUIRED - set(frame.columns)
        if missing:
            raise ValueError(f"{path.name} is missing: {', '.join(sorted(missing))}")
        symbol = symbol_from_path(path)
        parameter_columns.update(column for column in frame.columns if column.startswith("Opt "))
        for record in frame.to_dict(orient="records"):
            clean = dict(record)
            for column in frame.columns:
                if column in REQUIRED or column in {"CAR", "CAR/MDD", "Max. Sys % Drawdown"}:
                    clean[column] = _clean_number(record.get(column))
            clean["Symbol"] = symbol
            clean["Sector"] = SECTOR_BY_SYMBOL.get(symbol, "Other")
            clean["Source File"] = path.name
            rows.append(clean)
    return rows, files, sorted(parameter_columns)


def symbol_summary(rows):
    output = []
    for symbol in sorted({normalize_symbol(row.get("Symbol")) for row in rows}):
        selected = [row for row in rows if normalize_symbol(row.get("Symbol")) == symbol]
        eligible = [row for row in selected if (_clean_number(row.get("# Trades")) or 0) > 0]
        if eligible:
            profitable = 100 * sum((_clean_number(row.get("Net Profit")) or 0) > 0 for row in eligible) / len(eligible)
            pf = pd.Series([_clean_number(row.get("Profit Factor")) for row in eligible]).dropna().median()
            trades = pd.Series([_clean_number(row.get("# Trades")) for row in eligible]).dropna().median()
        else:
            profitable = pf = trades = 0
        output.append({
            "Symbol": symbol,
            "Sector": SECTOR_BY_SYMBOL.get(symbol, "Other"),
            "Paramsets": len(selected),
            "Eligible Paramsets": len(eligible),
            "Profitable %": round(float(profitable), 2),
            "Median PF": round(float(pf), 3),
            "Median Trades": round(float(trades), 1),
            "Status": "DATA EXCLUDED" if not eligible else "ECONOMIC",
        })
    return output


def analyze(input_dir, profile_path, core_symbols):
    profile = load_profile(profile_path)
    rows, files, parameters = load_directory(input_dir)
    core_symbols = [normalize_symbol(symbol) for symbol in core_symbols]
    core_sectors = {SECTOR_BY_SYMBOL.get(symbol, "Other") for symbol in core_symbols}
    family_symbols = sorted(symbol for symbol, sector in SECTOR_BY_SYMBOL.items() if sector in core_sectors)
    broad_symbols = sorted({normalize_symbol(row.get("Symbol")) for row in rows if normalize_symbol(row.get("Symbol")) not in NON_FUTURES})
    result = {
        "profile": profile["profile_name"],
        "periodicity": profile["periodicity"],
        "input_directory": str(Path(input_dir).resolve()),
        "input_file_count": len(files),
        "parameter_columns": parameters,
        "interpretation": "Core is the strategy decision. Family and broad scopes are transferability diagnostics, not automatic vetoes.",
        "core": evaluate_scope(rows, core_symbols, profile["thresholds"]["core"]),
        "family": evaluate_scope(rows, family_symbols, profile["thresholds"]["family"]),
        "broad": evaluate_scope(rows, broad_symbols, profile["thresholds"]["broad"]),
        "core_symbols": core_symbols,
        "family_symbols": family_symbols,
        "broad_symbols": broad_symbols,
        "symbols": symbol_summary(rows),
        "decision": "ADVANCE TO WFA" if evaluate_scope(rows, core_symbols, profile["thresholds"]["core"])["verdict"] == "PASS" else "REJECT OR REVISE",
        "caveat": "This verdict evaluates the exported results as supplied. Rerun after the corrected per-contract costs, metadata, dates, and periodicity are loaded in AmiBroker.",
    }
    return result


def render_html(result):
    def card(name, data):
        verdict_class = "pass" if data["verdict"] == "PASS" else "fail"
        gates = "".join(f"<li>{html.escape(k.replace('_', ' ').title())}: <b>{'pass' if v else 'fail'}</b></li>" for k, v in data.get("gates", {}).items())
        return f"""<section class='card'><h2>{html.escape(name)}</h2><div class='verdict {verdict_class}'>{html.escape(data['verdict'])}</div>
        <dl><dt>Profitable paramsets</dt><dd>{data['profitable_paramsets_pct']:.1f}%</dd><dt>Median PF</dt><dd>{data['median_profit_factor']:.2f}</dd><dt>Median trades</dt><dd>{data['median_trades']:.0f}</dd><dt>Excluded zero-trade rows</dt><dd>{data['zero_trade_rows']}</dd></dl><ul>{gates}</ul></section>"""
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row[key]))}</td>" for key in ("Symbol", "Sector", "Paramsets", "Eligible Paramsets", "Profitable %", "Median PF", "Median Trades", "Status")) + "</tr>"
        for row in result["symbols"]
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Cross-Sector Optimization Review</title><style>
    body{{font-family:Inter,system-ui,sans-serif;margin:0;background:#f5f7fb;color:#182033}}main{{max-width:1180px;margin:auto;padding:32px}}h1{{margin-bottom:4px}}.note{{background:#fff4d6;border-left:5px solid #d69400;padding:14px;margin:20px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}.card{{background:white;border:1px solid #dfe4ee;border-radius:14px;padding:20px;box-shadow:0 3px 12px #15203a12}}.verdict{{font-weight:800;font-size:1.35rem}}.pass{{color:#087a44}}.fail{{color:#b42318}}dl{{display:grid;grid-template-columns:1fr auto;gap:8px}}dt,dd{{margin:0}}table{{width:100%;border-collapse:collapse;background:white;margin-top:20px;font-size:.9rem}}th,td{{padding:9px;border-bottom:1px solid #e4e8f0;text-align:right}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}th{{position:sticky;top:0;background:#23395d;color:white}}.scroll{{overflow:auto;max-height:620px;border-radius:12px}}code{{background:#e9edf5;padding:2px 5px;border-radius:4px}}
    </style></head><body><main><p>AMI BROKER RESEARCH WORKFLOW</p><h1>{html.escape(result['profile'])} optimization review</h1><p><b>Decision: {html.escape(result['decision'])}</b> · {result['input_file_count']} input files · Core: {html.escape(', '.join(result['core_symbols']))}</p>
    <div class='note'><b>How to read this:</b> {html.escape(result['interpretation'])}<br>{html.escape(result['caveat'])}</div>
    <div class='grid'>{card('Core market', result['core'])}{card('Same-family transfer', result['family'])}{card('Broad-universe diagnostic', result['broad'])}</div>
    <h2>Symbol detail</h2><div class='scroll'><table><thead><tr>{''.join(f'<th>{h}</th>' for h in ('Symbol','Sector','Paramsets','Eligible','Profitable %','Median PF','Median Trades','Status'))}</tr></thead><tbody>{rows}</tbody></table></div>
    <h2>What happens next</h2><ol><li>Rerun exports with the corrected AFL and matching daily or intraday project.</li><li>Require a broad profitable parameter neighborhood on the core market.</li><li>Use family/broad results as evidence about portability, not as a universal hard gate.</li><li>Advance only the frozen rule to rolling WFA and doubled-cost stress.</li></ol></main></body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--settings", type=Path, required=True, help="Daily or intraday JSON settings profile")
    parser.add_argument("--core", nargs="+", required=True, help="Declared core symbols, without or with slash")
    parser.add_argument("--output", type=Path, default=Path("cross_sector_optimization_dashboard.html"))
    parser.add_argument("--json", dest="json_output", type=Path)
    args = parser.parse_args(argv)
    result = analyze(args.input_dir, args.settings, args.core)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(result), encoding="utf-8")
    json_path = args.json_output or args.output.with_suffix(".json")
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "core": result["core"], "dashboard": str(args.output), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
