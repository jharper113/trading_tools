"""Shared deterministic helpers for AmiBroker optimization and WFA workflows."""

from __future__ import annotations

import json
import re
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path


def load_profile(path):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "profile_name",
        "initial_equity",
        "periodicity",
        "optimization_window",
        "wfa",
        "fitness",
        "last_research_date",
        "sealed_start",
    }
    missing = required - profile.keys()
    if missing:
        raise ValueError(f"Settings profile is missing: {', '.join(sorted(missing))}")
    return profile


def normalize_symbol(value):
    return str(value or "").strip().upper().lstrip("/")


def symbol_from_path(path):
    stem = Path(path).stem.strip()
    for marker in ("_Daily_", "_Intra_", "_Intraday_"):
        if marker in stem:
            stem = stem.rsplit(marker, 1)[1]
            return normalize_symbol(stem)
    for prefix in ("Daily_", "Intra_", "Intraday_"):
        if stem.startswith(prefix):
            return normalize_symbol(stem[len(prefix) :])
    return normalize_symbol(stem)


def _num(row, key):
    try:
        value = float(row.get(key, 0) or 0)
        return value
    except (TypeError, ValueError):
        return 0.0


def evaluate_scope(rows, symbols, thresholds):
    wanted = {normalize_symbol(symbol) for symbol in symbols}
    selected = [row for row in rows if normalize_symbol(row.get("Symbol")) in wanted]
    eligible = [row for row in selected if _num(row, "# Trades") > 0]
    zero_count = len(selected) - len(eligible)
    if not eligible:
        return {
            "verdict": "NO DATA",
            "selected_rows": len(selected),
            "eligible_rows": 0,
            "zero_trade_rows": zero_count,
            "profitable_paramsets_pct": 0.0,
            "median_profit_factor": 0.0,
            "median_trades": 0.0,
            "gates": {},
        }
    profitable = 100.0 * sum(_num(row, "Net Profit") > 0 for row in eligible) / len(eligible)
    median_pf = statistics.median(_num(row, "Profit Factor") for row in eligible)
    median_trades = statistics.median(_num(row, "# Trades") for row in eligible)
    gates = {
        "profitable_paramsets": profitable >= thresholds["profitable_paramsets_pct"],
        "median_profit_factor": median_pf >= thresholds["median_profit_factor"],
        "median_trades": median_trades >= thresholds["median_trades"],
    }
    return {
        "verdict": "PASS" if all(gates.values()) else "FAIL",
        "selected_rows": len(selected),
        "eligible_rows": len(eligible),
        "zero_trade_rows": zero_count,
        "profitable_paramsets_pct": round(profitable, 4),
        "median_profit_factor": round(median_pf, 4),
        "median_trades": round(median_trades, 4),
        "gates": gates,
    }


def _set_text(root, tags, value):
    for tag in tags:
        for node in root.findall(f".//{tag}"):
            node.text = str(value)


def _ensure(root, parent_tag, tag, value):
    node = root.find(f".//{tag}")
    if node is None:
        parent = root.find(f".//{parent_tag}") or root
        node = ET.SubElement(parent, tag)
    node.text = str(value)


def render_project(template_path, formula_path, formula_content, profile, mode):
    root = ET.parse(template_path).getroot()
    _set_text(root, ["FormulaPath"], formula_path)
    _set_text(root, ["FormulaContent"], formula_content)
    _set_text(root, ["InitialEquity"], profile["initial_equity"])
    _set_text(root, ["CommissionMode"], profile["commission_mode"])
    _set_text(root, ["CommissionValue", "CommissionAmount"], profile["commission_per_contract_side"])
    _set_text(root, ["PointsOnlyTest"], 1 if profile["futures_mode"] else 0)
    _set_text(root, ["Periodicity"], profile["periodicity"]["apx_code"])
    _set_text(root, ["ChartInterval"], profile["periodicity"]["seconds"])
    _set_text(root, ["MinShares"], profile["min_shares"])
    _set_text(root, ["AllowSameBarExit"], 1 if profile["allow_same_bar_exit"] else 0)
    _set_text(root, ["ReverseSignalForcesExit"], 1 if profile["reverse_signal_forces_exit"] else 0)
    _set_text(root, ["UsePrevBarEquity", "UsePrevBarEquityForPosSizing"], 1 if profile["use_previous_bar_equity"] else 0)
    _set_text(root, ["OptTarget"], profile["fitness"])
    window = profile["optimization_window"]
    _set_text(root, ["RangeType", "BacktestRangeType"], 3)
    _set_text(root, ["FromDate", "RangeFromDate", "BacktestRangeFromDate"], window["start"])
    _set_text(root, ["ToDate", "RangeToDate", "BacktestRangeToDate"], window["end"])
    if mode.lower() == "wfa":
        wf = profile["wfa"]
        mapping = {
            "ISStartDate": wf["in_sample_start"],
            "ISEndDate": wf["in_sample_end"],
            "ISLastDate": wf["in_sample_last"],
            "OSStartDate": wf["out_sample_start"],
            "OSEndDate": wf["out_sample_end"],
            "OSLastDate": wf["out_sample_last"],
            "ISStep": wf["step_months"],
            "OSStep": wf["step_months"],
            "ISStepUnit": 2,
            "OSStepUnit": 2,
            "ISAnchored": 0,
            "OSAnchored": 0,
            "Anchored": 0,
            "Step": wf["step_months"],
            "StepUnit": 2,
        }
        for tag, value in mapping.items():
            _set_text(root, [tag], value)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _batch_step(root, action, param=None):
    step = ET.SubElement(root, "Step")
    ET.SubElement(step, "Action").text = action
    node = ET.SubElement(step, "Param")
    if param is not None:
        node.text = str(param)


def render_batch(project_path, symbols, operation, export_dir="Reports"):
    root = ET.Element("AmiBroker-Batch", {"CompactMode": "0"})
    _batch_step(root, "LoadProject", project_path)
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            continue
        _batch_step(root, "SetCurrentSymbol", symbol)
        _batch_step(root, operation)
        export_action = "ExportWalkForward" if operation == "WalkForward" else "Export"
        _batch_step(root, export_action, f"{export_dir}\\{symbol}.csv")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def patch_common_validation(text):
    text = patch_per_contract_commission(text)
    include = '#include_once "Common_Contract_Metadata.afl"\n'
    if include.strip() not in text:
        text = include + text
    text = re.sub(
        r'V2TickSize\s*=\s*Param\("Tick size"[^;]+;',
        "V2TickSize = ContractTickSize;",
        text,
    )
    text = re.sub(
        r'V2PointValue\s*=\s*Param\("Point value"[^;]+;',
        "V2PointValue = ContractPointValue;",
        text,
    )
    return text


def patch_per_contract_commission(text):
    return re.sub(
        r'SetOption\(\s*"CommissionMode"\s*,\s*2\s*\);',
        'SetOption("CommissionMode", 3);',
        text,
    )


def patch_normalized_contract_selection(text):
    pattern = r'StudyContract\s*=\s*ParamList\(([^;]+)\);'
    match = re.search(pattern, text)
    if not match:
        return text
    replacement = (
        f"ManualStudyContract=ParamList({match.group(1)});\n"
        'UseCurrentSymbol=ParamToggle("Contract source","Manual|Current symbol",1);\n'
        "StudyContract=ManualStudyContract;\n"
        "if(UseCurrentSymbol) StudyContract=Name();"
    )
    return text[: match.start()] + replacement + text[match.end() :]
