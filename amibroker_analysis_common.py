"""Shared project and result-discovery helpers for AmiBroker analysis."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from workflow import normalize_symbol


@dataclass(frozen=True)
class ProjectContext:
    project_path: Path
    formula_path: str
    strategy_name: str
    timeframe: str
    periodicity: int
    interval_seconds: int
    project_mode: str


def _text(root, name, default=""):
    node = root.find(f".//{name}")
    return default if node is None or node.text is None else node.text.strip()


def read_project_context(project_path):
    project_path = Path(project_path)
    root = ET.parse(project_path).getroot()
    formula_path = _text(root, "FormulaPath")
    if not formula_path:
        raise ValueError(f"Project has no FormulaPath: {project_path}")
    strategy_name = Path(formula_path.replace("\\", "/")).stem
    periodicity = int(_text(root, "Periodicity", "0"))
    interval = int(_text(root, "ChartInterval", "86400"))
    if periodicity == 0 or interval == 86400:
        timeframe = "Daily"
    elif periodicity == 8 and interval in {300, 900, 3600}:
        timeframe = f"Intraday_{interval // 60}m"
    else:
        timeframe = f"Interval_{interval}s"
    is_start = _text(root, "ISStartDate")
    project_mode = (
        "wfa"
        if "wfa" in project_path.stem.lower()
        or (is_start and is_start != "1970-01-01")
        else "optimization"
    )
    return ProjectContext(
        project_path=project_path,
        formula_path=formula_path,
        strategy_name=strategy_name,
        timeframe=timeframe,
        periodicity=periodicity,
        interval_seconds=interval,
        project_mode=project_mode,
    )


def _csv_kind(path):
    try:
        columns = {
            str(column).strip()
            for column in pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns
        }
    except (OSError, pd.errors.ParserError, UnicodeError):
        return None
    if "Mode" in columns:
        return "wfa"
    required = {"Net Profit", "Profit Factor", "# Trades"}
    if required.issubset(columns):
        return "optimization"
    return None


def detect_result_type(results_dir):
    results_dir = Path(results_dir)
    files = sorted(results_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV result files found in {results_dir}")
    kinds = {kind for path in files if (kind := _csv_kind(path))}
    if not kinds:
        raise ValueError(
            "No supported optimization or WFA summary CSV schema was found in "
            f"{results_dir}"
        )
    if len(kinds) > 1:
        raise ValueError(
            f"Results directory mixes optimization and WFA exports: {results_dir}"
        )
    return kinds.pop()


def resolve_core_symbols(registry_path, strategy_name):
    registry_path = Path(registry_path)
    if not registry_path.exists():
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entry = registry.get("strategies", {}).get(strategy_name, {})
    return [normalize_symbol(symbol) for symbol in entry.get("core_symbols", [])]
