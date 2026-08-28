import argparse
import math
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from analyze_single_symbol_optimization import (
    coerce_numeric_columns,
    finite_number,
    format_metric_value,
    html_escape,
    numeric_series,
    parameter_columns as detect_parameter_columns,
    sortable_value,
)


DEFAULT_INPUT_DIR = Path(
    "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Reports/OptResults"
)
DEFAULT_OUTPUT = Path(
    "output/backtesting/cross_sector_optimization_dashboard.html"
)

SYMBOL_COLUMN = "Symbol"
SECTOR_COLUMN = "Sector"
SOURCE_FILE_COLUMN = "Source File"

NET_PROFIT_COLUMN = "Net Profit"
PROFIT_FACTOR_COLUMN = "Profit Factor"
TRADE_COUNT_COLUMN = "# Trades"
CAGR_COLUMN = "CAR"
CAR_MDD_COLUMN = "CAR/MDD"
MAX_DRAWDOWN_COLUMN = "Max. Sys % Drawdown"

DEFAULT_BASKET_SYMBOLS = ["/ES", "/6E", "/CL", "/GC", "/BTC", "/ZC", "/ZN"]
UNPREFIXED_SYMBOLS = {"SPY"}

SECTOR_SYMBOLS = [
    (
        "Equity Indexes",
        ["/ES", "/MES", "/NQ", "/MNQ", "/YM", "/MYM", "/RTY", "SPY"],
    ),
    (
        "Currencies",
        ["/6E", "/6B", "/6J", "/6A", "/6C", "/6S", "/DX", "/MXP"],
    ),
    ("Energy", ["/CL", "/MCL", "/NG", "/RB", "/HO"]),
    ("Metals", ["/GC", "/MGC", "/SI", "/SIL", "/HG", "/PL", "/PA"]),
    ("Crypto", ["/BTC", "/MBT", "/ETH", "/MET", "/MSL", "/SOL", "/XRP"]),
    ("Agricultural", ["/ZC", "/ZW", "/ZS", "/ZM", "/ZL", "/HE", "/LE", "/GF"]),
    ("Bond Futures", ["/ZN", "/ZB", "/ZF", "/ZT", "/UB"]),
    ("Softs", ["/KC", "/SB", "/CC", "/CT", "/OJ"]),
]

SECTOR_BY_SYMBOL = {
    symbol: sector
    for sector, symbols in SECTOR_SYMBOLS
    for symbol in symbols
}


@dataclass(frozen=True)
class ThresholdConfig:
    profitable_groups_threshold: float = 65.0
    profit_factor_threshold: float = 1.10
    trade_count_threshold: float = 100.0
    trade_coverage_threshold: float = 50.0


@dataclass(frozen=True)
class OptimizationDataset:
    frame: pd.DataFrame
    parameter_columns: list
    input_files: list


@dataclass(frozen=True)
class DistributionMetric:
    key: str
    label: str
    column: str
    suffix: str = ""
    decimals: int = 2
    reference_value: Optional[float] = None
    description: str = ""


DISTRIBUTION_METRICS = [
    DistributionMetric(
        "profitability",
        "Profitability (Net Profit)",
        NET_PROFIT_COLUMN,
        reference_value=0.0,
    ),
    DistributionMetric(
        "profit_factor",
        "Profit Factor",
        PROFIT_FACTOR_COLUMN,
        reference_value=1.10,
    ),
    DistributionMetric(
        "cagr",
        "CAGR (CAR)",
        CAGR_COLUMN,
        "%",
        reference_value=10.0,
    ),
    DistributionMetric(
        "max_drawdown",
        "Max Drawdown %",
        MAX_DRAWDOWN_COLUMN,
        "%",
        reference_value=-20.0,
    ),
    DistributionMetric(
        "trade_count",
        "Trade Count",
        TRADE_COUNT_COLUMN,
        decimals=0,
        reference_value=100.0,
        description=(
            "More trades provide a larger sample for judging whether results "
            "are repeatable. The dashed line marks 100 trades."
        ),
    ),
    DistributionMetric(
        "car_mdd",
        "Risk-Adjusted Return (CAR/MDD)",
        CAR_MDD_COLUMN,
        reference_value=0.0,
        description=(
            "Annualized return divided by maximum drawdown. A broad positive "
            "distribution across symbols and paramsets is a useful robustness "
            "signal, but does not guarantee out-of-sample performance."
        ),
    ),
]


def required_columns():
    return [
        NET_PROFIT_COLUMN,
        PROFIT_FACTOR_COLUMN,
        TRADE_COUNT_COLUMN,
        CAGR_COLUMN,
        MAX_DRAWDOWN_COLUMN,
    ]


def validate_required_columns(frame, path):
    missing = [column for column in required_columns() if column not in frame.columns]

    if missing:
        raise ValueError(
            f"{path} is missing required column(s): " + ", ".join(missing)
        )


def load_optimization_csv(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Optimization CSV not found: {path}")

    frame = pd.read_csv(path)
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = coerce_numeric_columns(frame)
    validate_required_columns(frame, path)
    return frame


def normalize_symbol(value):
    symbol = str(value).strip().upper()

    if not symbol:
        return "/UNKNOWN"

    base_symbol = symbol.lstrip("/")

    if base_symbol in UNPREFIXED_SYMBOLS:
        return base_symbol

    return f"/{base_symbol}"


def symbol_from_path(path):
    stem = Path(path).stem.strip()

    if "_" in stem:
        stem = stem.split("_")[-1]

    return normalize_symbol(stem)


def sector_for_symbol(symbol):
    return SECTOR_BY_SYMBOL.get(normalize_symbol(symbol), "Other")


def merge_parameter_columns(parameter_sets):
    merged = []

    for columns in parameter_sets:
        for column in columns:
            if column not in merged:
                merged.append(column)

    return merged


def load_optimization_directory(input_dir):
    input_dir = Path(input_dir)

    if not input_dir.exists():
        raise FileNotFoundError(f"Optimization directory not found: {input_dir}")

    csv_paths = sorted(input_dir.glob("*.csv"))

    if not csv_paths:
        raise FileNotFoundError(f"No CSV files found in {input_dir}")

    frames = []
    parameter_sets = []

    for path in csv_paths:
        symbol = symbol_from_path(path)
        frame = load_optimization_csv(path)
        parameter_sets.append(detect_parameter_columns(frame))

        frame = frame.copy()
        frame[SYMBOL_COLUMN] = symbol
        frame[SECTOR_COLUMN] = sector_for_symbol(symbol)
        frame[SOURCE_FILE_COLUMN] = path.name
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True, sort=False)
    return OptimizationDataset(
        frame=combined,
        parameter_columns=merge_parameter_columns(parameter_sets),
        input_files=[str(path) for path in csv_paths],
    )


def numeric_column(frame, column):
    if column not in frame.columns:
        return pd.Series(dtype="float64")

    return numeric_series(frame[column]).dropna()


def median_for_column(frame, column):
    values = numeric_column(frame, column)

    if values.empty:
        return None

    return float(values.median())


def threshold_status(value, threshold, inclusive=True):
    number = finite_number(value)

    if number is None:
        return "bad"

    if inclusive:
        return "good" if number >= threshold else "bad"

    return "good" if number > threshold else "bad"


def percentage_groups_with_positive_median(frame, group_column):
    if group_column not in frame.columns or NET_PROFIT_COLUMN not in frame.columns:
        return None

    working = frame[[group_column, NET_PROFIT_COLUMN]].copy()
    working[NET_PROFIT_COLUMN] = numeric_series(working[NET_PROFIT_COLUMN])
    working = working.dropna(subset=[group_column, NET_PROFIT_COLUMN])

    if working.empty:
        return None

    medians = working.groupby(group_column)[NET_PROFIT_COLUMN].median()

    if medians.empty:
        return None

    return float((medians > 0).mean() * 100.0)


def percentage_paramsets_over_trade_threshold(frame, threshold):
    trades = numeric_column(frame, TRADE_COUNT_COLUMN)

    if trades.empty:
        return None

    return float((trades > threshold).mean() * 100.0)


def build_summary_metrics(frame, thresholds, group_column, group_label):
    profitable_groups_pct = percentage_groups_with_positive_median(
        frame,
        group_column,
    )
    median_profit_factor = median_for_column(frame, PROFIT_FACTOR_COLUMN)
    trade_count_pct = percentage_paramsets_over_trade_threshold(
        frame,
        thresholds.trade_count_threshold,
    )

    return [
        {
            "key": "profitable_groups_pct",
            "label": f"{group_label} With Median Profitability",
            "value": profitable_groups_pct,
            "display_value": format_metric_value(profitable_groups_pct, "%"),
            "threshold_text": f">= {thresholds.profitable_groups_threshold:.0f}%",
            "status": threshold_status(
                profitable_groups_pct,
                thresholds.profitable_groups_threshold,
            ),
        },
        {
            "key": "median_profit_factor",
            "label": "Median Profit Factor",
            "value": median_profit_factor,
            "display_value": format_metric_value(median_profit_factor),
            "threshold_text": f">= {thresholds.profit_factor_threshold:.2f}",
            "status": threshold_status(
                median_profit_factor,
                thresholds.profit_factor_threshold,
            ),
        },
        {
            "key": "paramsets_over_trade_threshold_pct",
            "label": (
                f"Paramsets With > {thresholds.trade_count_threshold:,.0f} Trades"
            ),
            "value": trade_count_pct,
            "display_value": format_metric_value(trade_count_pct, "%"),
            "threshold_text": "Informational",
            "status": "neutral",
        },
    ]


def metric_lookup(metrics):
    return {metric["key"]: metric for metric in metrics}


def metric_value(metrics, key):
    return metric_lookup(metrics).get(key, {}).get("value")


def gate_status(value, threshold, inclusive=True):
    return threshold_status(value, threshold, inclusive=inclusive) == "good"


def validation_passes(validation, thresholds):
    if validation["frame"].empty:
        return False

    metrics = metric_lookup(validation["summary_metrics"])
    return all([
        gate_status(
            metrics["profitable_groups_pct"]["value"],
            thresholds.profitable_groups_threshold,
        ),
        gate_status(
            metrics["median_profit_factor"]["value"],
            thresholds.profit_factor_threshold,
        ),
        gate_status(
            metrics["paramsets_over_trade_threshold_pct"]["value"],
            thresholds.trade_coverage_threshold,
        ),
    ])


def pass_fail_label(passed, has_data=True):
    if not has_data:
        return "No Data"

    return "Pass" if passed else "Fail"


def pass_fail_status_class(passed, has_data=True):
    if not has_data:
        return "neutral"

    return "good" if passed else "bad"


def sector_decision_row(validation, thresholds):
    frame = validation["frame"]
    has_data = not frame.empty
    passed = validation_passes(validation, thresholds)
    metrics = metric_lookup(validation["summary_metrics"])

    return {
        "sector": validation["name"],
        "status": pass_fail_label(passed, has_data),
        "status_class": pass_fail_status_class(passed, has_data),
        "passed": passed,
        "symbols": ", ".join(validation["symbols"]) if validation["symbols"] else "n/a",
        "symbol_count": int(frame[SYMBOL_COLUMN].nunique()) if has_data else 0,
        "paramsets": int(len(frame)),
        "median_pnl": median_for_column(frame, NET_PROFIT_COLUMN),
        "profitable_groups_pct": metrics["profitable_groups_pct"]["value"],
        "median_profit_factor": metrics["median_profit_factor"]["value"],
        "trade_coverage_pct": metrics["paramsets_over_trade_threshold_pct"]["value"],
    }


def cross_gate_cards(cross_validation, thresholds, cross_passed):
    metrics = metric_lookup(cross_validation["summary_metrics"])
    gates = [
        {
            "label": "Cross-Sector Result",
            "display_value": pass_fail_label(
                cross_passed,
                not cross_validation["frame"].empty,
            ),
            "threshold_text": "All gates must pass",
            "status": pass_fail_status_class(
                cross_passed,
                not cross_validation["frame"].empty,
            ),
        },
        {
            "label": "Sectors With Median Profitability",
            "display_value": metrics["profitable_groups_pct"]["display_value"],
            "threshold_text": f">= {thresholds.profitable_groups_threshold:.0f}%",
            "status": threshold_status(
                metrics["profitable_groups_pct"]["value"],
                thresholds.profitable_groups_threshold,
            ),
        },
        {
            "label": "Median Profit Factor",
            "display_value": metrics["median_profit_factor"]["display_value"],
            "threshold_text": f">= {thresholds.profit_factor_threshold:.2f}",
            "status": threshold_status(
                metrics["median_profit_factor"]["value"],
                thresholds.profit_factor_threshold,
            ),
        },
        {
            "label": (
                f"Paramsets With > {thresholds.trade_count_threshold:,.0f} Trades"
            ),
            "display_value": metrics[
                "paramsets_over_trade_threshold_pct"
            ]["display_value"],
            "threshold_text": f">= {thresholds.trade_coverage_threshold:.0f}%",
            "status": threshold_status(
                metrics["paramsets_over_trade_threshold_pct"]["value"],
                thresholds.trade_coverage_threshold,
            ),
        },
    ]
    return gates


def build_decision_board(cross_validation, sector_validations, thresholds):
    cross_passed = validation_passes(cross_validation, thresholds)
    sector_rows = [
        sector_decision_row(validation, thresholds)
        for validation in sector_validations
    ]
    data_sector_rows = [row for row in sector_rows if row["paramsets"] > 0]
    passing_sector_rows = [row for row in data_sector_rows if row["passed"]]
    single_sector_passed = bool(passing_sector_rows)

    if cross_validation["frame"].empty:
        verdict = "Insufficient Data"
        classification = "Insufficient Data"
        status = "neutral"
    elif cross_passed:
        verdict = "Cross-Sector Pass"
        classification = "Broadly Robust"
        status = "good"
    elif single_sector_passed:
        verdict = "Single-Sector Pass Only"
        classification = "Sector Specialist"
        status = "neutral"
    else:
        verdict = "Fail"
        classification = "Reject"
        status = "bad"

    return {
        "verdict": verdict,
        "classification": classification,
        "status": status,
        "cross_passed": cross_passed,
        "single_sector_passed": single_sector_passed,
        "passing_sector_count": len(passing_sector_rows),
        "data_sector_count": len(data_sector_rows),
        "sector_rows": sector_rows,
        "cross_gate_cards": cross_gate_cards(
            cross_validation,
            thresholds,
            cross_passed,
        ),
    }


def ordered_symbols(frame, preferred_symbols=None):
    if SYMBOL_COLUMN not in frame.columns or frame.empty:
        return []

    preferred_symbols = preferred_symbols or []
    present = [symbol for symbol in frame[SYMBOL_COLUMN].dropna().unique()]
    preferred = [symbol for symbol in preferred_symbols if symbol in present]
    extras = sorted(symbol for symbol in present if symbol not in preferred)
    return preferred + extras


def filter_basket_frame(frame, basket_symbols):
    basket_symbols = list(basket_symbols or [])

    if not basket_symbols:
        return frame.copy()

    basket = frame[frame[SYMBOL_COLUMN].isin(basket_symbols)].copy()

    if basket.empty:
        return frame.copy()

    return basket


def unique_sorted_values(frame, column):
    if column not in frame.columns:
        return []

    values = [
        value
        for value in frame[column].dropna().unique().tolist()
        if str(value).strip() != ""
    ]
    return sorted(values, key=sortable_value)


def select_heatmap_parameters(frame, parameter_columns):
    scores = []

    for index, column in enumerate(parameter_columns):
        if column not in frame.columns:
            continue

        values = numeric_column(frame, column)

        if values.nunique(dropna=True) <= 1:
            continue

        variance = float(values.var(ddof=0))

        if math.isnan(variance):
            continue

        scores.append((variance, index, column))

    scores.sort(key=lambda item: (-item[0], item[1]))
    return [column for _, _, column in scores[:2]]


def heatmap_scale(frame, x_param, y_param):
    if not x_param or not y_param:
        return 1.0

    working = frame[[x_param, y_param, NET_PROFIT_COLUMN]].copy()
    working[NET_PROFIT_COLUMN] = numeric_series(working[NET_PROFIT_COLUMN])
    working = working.dropna(subset=[x_param, y_param, NET_PROFIT_COLUMN])

    if working.empty:
        return 1.0

    medians = working.groupby([y_param, x_param])[NET_PROFIT_COLUMN].median()

    if medians.empty:
        return 1.0

    max_abs = max(abs(float(medians.min())), abs(float(medians.max())))
    return max(max_abs, 1.0)


def build_heatmap_config(frame, parameter_columns):
    selected = select_heatmap_parameters(frame, parameter_columns)

    if len(selected) < 2:
        return None

    x_param, y_param = selected
    return {
        "x_param": x_param,
        "y_param": y_param,
        "x_values": unique_sorted_values(frame, x_param),
        "y_values": unique_sorted_values(frame, y_param),
        "max_abs": heatmap_scale(frame, x_param, y_param),
    }


def distribution_metrics_for_frame(frame):
    return [
        metric
        for metric in DISTRIBUTION_METRICS
        if metric.column in frame.columns
    ]


def quantile_stats(values):
    values = values.dropna()

    if values.empty:
        return None

    return {
        "min": float(values.min()),
        "q1": float(values.quantile(0.25)),
        "median": float(values.median()),
        "q3": float(values.quantile(0.75)),
        "max": float(values.max()),
        "count": int(values.count()),
    }


def boxplot_categories(frame, metric, symbols):
    categories = [
        {
            "label": "Aggregate",
            "stats": quantile_stats(numeric_column(frame, metric.column)),
        }
    ]

    for symbol in symbols:
        symbol_frame = frame[frame[SYMBOL_COLUMN] == symbol]
        categories.append({
            "label": symbol,
            "stats": quantile_stats(numeric_column(symbol_frame, metric.column)),
        })

    return categories


def format_currency(value, decimals=0):
    number = finite_number(value)

    if number is None:
        return "n/a"

    formatted = f"{abs(number):,.{decimals}f}"
    return f"-${formatted}" if number < 0 else f"${formatted}"


def fmt_axis(value, currency=False):
    number = finite_number(value)

    if number is None:
        return ""

    if currency:
        return format_currency(number)

    if abs(number) >= 100:
        return f"{number:,.0f}"

    return f"{number:.2f}".rstrip("0").rstrip(".")


def svg_boxplot(frame, metric, symbols):
    categories = boxplot_categories(frame, metric, symbols)
    stats = [item["stats"] for item in categories if item["stats"] is not None]
    width = max(720, 92 * max(len(categories), 1))
    height = 330
    left = 112 if metric.key == "profitability" else 72
    right = 24
    top = 24
    bottom = 72
    plot_width = width - left - right
    plot_height = height - top - bottom

    if not stats:
        return empty_svg(width, height, "No boxplot data")

    y_min = min(item["min"] for item in stats)
    y_max = max(item["max"] for item in stats)

    if metric.reference_value is not None:
        y_min = min(y_min, metric.reference_value)
        y_max = max(y_max, metric.reference_value)

    if abs(y_max - y_min) < 0.000001:
        y_max += 1.0
        y_min -= 1.0

    padding = (y_max - y_min) * 0.10
    y_min -= padding
    y_max += padding

    def y_for(value):
        return top + ((y_max - value) / (y_max - y_min)) * plot_height

    def x_for(index):
        if len(categories) == 1:
            return left + plot_width / 2

        return left + (plot_width * index / (len(categories) - 1))

    y_ticks = [y_min + (y_max - y_min) * index / 4 for index in range(5)]
    tick_markup = []

    for tick in y_ticks:
        y = y_for(tick)
        tick_markup.append(
            f'<line x1="{left}" x2="{width - right}" y1="{y:.2f}" '
            'y2="{y:.2f}" class="grid-line" />'
        )
        tick_markup.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" class="axis-label" '
            f'text-anchor="end">{fmt_axis(tick, metric.key == "profitability")}</text>'
        )

    reference_line = ""
    if metric.reference_value is not None:
        reference_y = y_for(metric.reference_value)
        reference_line = (
            f'<line x1="{left}" x2="{width - right}" y1="{reference_y:.2f}" '
            f'y2="{reference_y:.2f}" class="reference-line" '
            f'data-reference-value="{metric.reference_value:.2f}" />'
        )

    boxes = []
    labels = []
    spacing = plot_width / max(len(categories) - 1, 1)
    box_width = max(24, min(46, spacing * 0.48))

    for index, category in enumerate(categories):
        x = x_for(index)
        label = html_escape(category["label"])
        labels.append(
            f'<text x="{x:.2f}" y="{height - 28}" class="x-label" '
            f'text-anchor="end" transform="rotate(-35 {x:.2f} {height - 28})">'
            f'{label}</text>'
        )

        stat = category["stats"]

        if stat is None:
            continue

        whisker_top = y_for(stat["max"])
        whisker_bottom = y_for(stat["min"])
        box_top = y_for(stat["q3"])
        box_bottom = y_for(stat["q1"])
        median_y = y_for(stat["median"])
        box_height = max(2.0, box_bottom - box_top)
        if metric.key == "profitability":
            median_value = format_currency(stat["median"])
            min_value = format_currency(stat["min"])
            max_value = format_currency(stat["max"])
        else:
            median_value = format_metric_value(
                stat["median"],
                metric.suffix,
                metric.decimals,
            )
            min_value = format_metric_value(stat["min"], metric.suffix, metric.decimals)
            max_value = format_metric_value(stat["max"], metric.suffix, metric.decimals)

        title = (
            f'{category["label"]}: median {median_value}, '
            f'min {min_value}, max {max_value}, n={stat["count"]}'
        )
        boxes.append(f"""
<g class="boxplot">
  <title>{html_escape(title)}</title>
  <line x1="{x:.2f}" x2="{x:.2f}" y1="{whisker_top:.2f}" y2="{whisker_bottom:.2f}" class="whisker" />
  <line x1="{x - box_width / 3:.2f}" x2="{x + box_width / 3:.2f}" y1="{whisker_top:.2f}" y2="{whisker_top:.2f}" class="whisker" />
  <line x1="{x - box_width / 3:.2f}" x2="{x + box_width / 3:.2f}" y1="{whisker_bottom:.2f}" y2="{whisker_bottom:.2f}" class="whisker" />
  <rect x="{x - box_width / 2:.2f}" y="{box_top:.2f}" width="{box_width:.2f}" height="{box_height:.2f}" class="box" />
  <line x1="{x - box_width / 2:.2f}" x2="{x + box_width / 2:.2f}" y1="{median_y:.2f}" y2="{median_y:.2f}" class="median-mark" />
</g>
""")

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(metric.label)} boxplot">
  <rect x="0" y="0" width="{width}" height="{height}" class="chart-bg" />
  {"".join(tick_markup)}
  {reference_line}
  <line x1="{left}" x2="{left}" y1="{top}" y2="{height - bottom}" class="axis-line" />
  <line x1="{left}" x2="{width - right}" y1="{height - bottom}" y2="{height - bottom}" class="axis-line" />
  {"".join(boxes)}
  {"".join(labels)}
  <text x="18" y="{top + plot_height / 2:.2f}" class="axis-title"
        text-anchor="middle" transform="rotate(-90 18 {top + plot_height / 2:.2f})">
        {html_escape(metric.label)}</text>
</svg>
"""


def color_channel(start, end, amount):
    return int(round(start + (end - start) * amount))


def blend_color(start, end, amount):
    amount = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % (
        color_channel(start[0], end[0], amount),
        color_channel(start[1], end[1], amount),
        color_channel(start[2], end[2], amount),
    )


def heatmap_color(value, max_abs):
    number = finite_number(value)

    if number is None:
        return "#edf2f5"

    amount = min(abs(number) / max(max_abs, 1.0), 1.0) ** 0.55
    neutral = (247, 250, 251)

    if number >= 0:
        return blend_color(neutral, (17, 122, 79), amount)

    return blend_color(neutral, (180, 35, 24), amount)


def empty_svg(width, height, label):
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(label)}">
  <rect x="0" y="0" width="{width}" height="{height}" class="chart-bg" />
  <text x="{width / 2:.2f}" y="{height / 2:.2f}" class="empty-chart" text-anchor="middle">
    {html_escape(label)}
  </text>
</svg>
"""


def svg_heatmap(symbol_frame, symbol, heatmap_config):
    if heatmap_config is None:
        return empty_svg(720, 280, "Not enough parameter data")

    x_param = heatmap_config["x_param"]
    y_param = heatmap_config["y_param"]
    x_values = heatmap_config["x_values"]
    y_values = heatmap_config["y_values"]
    max_abs = heatmap_config["max_abs"]

    if not x_values or not y_values:
        return empty_svg(720, 280, "No heatmap data")

    width = max(760, 48 * len(x_values) + 120)
    cell_height = 30
    left = 82
    right = 26
    top = 62
    bottom = 84
    plot_width = width - left - right
    plot_height = max(180, cell_height * len(y_values))
    height = top + plot_height + bottom
    cell_width = plot_width / len(x_values)
    cell_height = plot_height / len(y_values)

    working = symbol_frame[[x_param, y_param, NET_PROFIT_COLUMN]].copy()
    working[NET_PROFIT_COLUMN] = numeric_series(working[NET_PROFIT_COLUMN])
    working = working.dropna(subset=[x_param, y_param, NET_PROFIT_COLUMN])
    medians = {}

    if not working.empty:
        grouped = working.groupby([y_param, x_param])[NET_PROFIT_COLUMN].median()
        medians = {
            (y_value, x_value): float(value)
            for (y_value, x_value), value in grouped.items()
        }

    cells = []
    for y_index, y_value in enumerate(y_values):
        for x_index, x_value in enumerate(x_values):
            value = medians.get((y_value, x_value))
            x = left + x_index * cell_width
            y = top + y_index * cell_height
            color = heatmap_color(value, max_abs)
            title_value = (
                "n/a"
                if finite_number(value) is None
                else format_currency(value)
            )
            cells.append(f"""
<rect x="{x:.2f}" y="{y:.2f}" width="{cell_width + 0.5:.2f}" height="{cell_height + 0.5:.2f}"
      class="heat-cell" fill="{color}">
  <title>{html_escape(symbol)} | {html_escape(x_param)}={html_escape(x_value)} | {html_escape(y_param)}={html_escape(y_value)} | Median PNL {html_escape(title_value)}</title>
</rect>
""")

    x_labels = []
    label_step = max(1, math.ceil(len(x_values) / 14))
    for index, value in enumerate(x_values):
        if index % label_step != 0 and index != len(x_values) - 1:
            continue

        x = left + index * cell_width + cell_width / 2
        x_labels.append(
            f'<text x="{x:.2f}" y="{height - 34}" class="x-label" '
            f'text-anchor="end" transform="rotate(-35 {x:.2f} {height - 34})">'
            f'{html_escape(value)}</text>'
        )

    y_labels = []
    for index, value in enumerate(y_values):
        y = top + index * cell_height + cell_height / 2 + 4
        y_labels.append(
            f'<text x="{left - 10}" y="{y:.2f}" class="axis-label" text-anchor="end">'
            f'{html_escape(value)}</text>'
        )

    legend_width = 300
    legend_height = 10
    legend_x = width - right - legend_width
    legend_y = 20
    legend_steps = 30
    legend_segments = []

    for index in range(legend_steps):
        segment_x = legend_x + legend_width * index / legend_steps
        segment_width = legend_width / legend_steps + 0.5
        segment_value = -max_abs + (2 * max_abs * (index + 0.5) / legend_steps)
        legend_segments.append(
            f'<rect x="{segment_x:.2f}" y="{legend_y}" '
            f'width="{segment_width:.2f}" height="{legend_height}" '
            f'fill="{heatmap_color(segment_value, max_abs)}" />'
        )

    legend_min = f"Loss {format_currency(-max_abs)}"
    legend_zero = format_currency(0)
    legend_max = f"Profit {format_currency(max_abs)}"

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(symbol)} PNL heatmap">
  <rect x="0" y="0" width="{width}" height="{height}" class="chart-bg" />
  {"".join(cells)}
  <rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" class="heat-frame" />
  {"".join(x_labels)}
  {"".join(y_labels)}
  <text x="{left + plot_width / 2:.2f}" y="{height - 8}" class="axis-title" text-anchor="middle">
    {html_escape(x_param)}
  </text>
  <text x="18" y="{top + plot_height / 2:.2f}" class="axis-title"
        text-anchor="middle" transform="rotate(-90 18 {top + plot_height / 2:.2f})">
    {html_escape(y_param)}
  </text>
  <text x="{legend_x - 10}" y="{legend_y + 9}" class="axis-title" text-anchor="end">Median PNL</text>
  {"".join(legend_segments)}
  <rect x="{legend_x}" y="{legend_y}" width="{legend_width}" height="{legend_height}" class="heat-frame" />
  <text x="{legend_x}" y="{legend_y + 25}" class="axis-label">{html_escape(legend_min)}</text>
  <text x="{legend_x + legend_width / 2}" y="{legend_y + 25}" class="axis-label" text-anchor="middle">{html_escape(legend_zero)}</text>
  <text x="{legend_x + legend_width}" y="{legend_y + 25}" class="axis-label" text-anchor="end">{html_escape(legend_max)}</text>
</svg>
"""


def metric_cards_html(metrics):
    cards = []

    for metric in metrics:
        threshold = html_escape(metric["threshold_text"])
        cards.append(f"""
<article class="metric-card {html_escape(metric["status"])}">
  <div class="metric-topline">{html_escape(metric["label"])}</div>
  <div class="metric-value">{html_escape(metric["display_value"])}</div>
  <div class="metric-threshold">Target {threshold}</div>
</article>
""")

    return "\n".join(cards)


def decision_cards_html(decision):
    has_data = decision["data_sector_count"] > 0
    cross_status = pass_fail_status_class(decision["cross_passed"], has_data)
    single_status = pass_fail_status_class(
        decision["single_sector_passed"],
        has_data,
    )
    cards = [
        {
            "label": "Overall Verdict",
            "display_value": decision["verdict"],
            "threshold_text": "Cross-sector preferred; sector-only is constrained",
            "status": decision["status"],
        },
        {
            "label": "Cross-Sector Validation",
            "display_value": pass_fail_label(decision["cross_passed"], has_data),
            "threshold_text": "All cross-sector gates pass",
            "status": cross_status,
        },
        {
            "label": "Single-Sector Validation",
            "display_value": (
                f'{decision["passing_sector_count"]} of '
                f'{decision["data_sector_count"]} sectors'
            ),
            "threshold_text": "At least one sector passes all gates",
            "status": single_status,
        },
        {
            "label": "Strategy Classification",
            "display_value": decision["classification"],
            "threshold_text": "Broad, sector specialist, or reject",
            "status": decision["status"],
        },
    ]
    return metric_cards_html(cards)


def sector_decision_table_html(rows):
    if not rows:
        return '<div class="empty">No sector decision data.</div>'

    body_rows = []

    for row in rows:
        body_rows.append(f"""
<tr>
  <td>{html_escape(row["sector"])}</td>
  <td><span class="status-pill {html_escape(row["status_class"])}">{html_escape(row["status"])}</span></td>
  <td>{html_escape(row["symbols"])}</td>
  <td>{row["paramsets"]:,}</td>
  <td>{html_escape(format_currency(row["median_pnl"]))}</td>
  <td>{html_escape(format_metric_value(row["profitable_groups_pct"], "%"))}</td>
  <td>{html_escape(format_metric_value(row["median_profit_factor"]))}</td>
  <td>{html_escape(format_metric_value(row["trade_coverage_pct"], "%"))}</td>
</tr>
""")

    return f"""
<div class="table-scroll">
  <table class="data-table decision-table">
    <thead>
      <tr>
        <th>Sector</th>
        <th>Status</th>
        <th>Symbols</th>
        <th>Paramsets</th>
        <th>Median PNL</th>
        <th>Positive Symbols</th>
        <th>Median PF</th>
        <th>&gt;100 Trades</th>
      </tr>
    </thead>
    <tbody>
      {"".join(body_rows)}
    </tbody>
  </table>
</div>
"""


def decision_board_html(decision):
    return f"""
<section class="decision-board">
  <div class="section-head">
    <h2>Decision Board</h2>
    <span class="pill">Open by default</span>
  </div>
  <div class="metric-grid">
    {decision_cards_html(decision)}
  </div>
  <div class="section-head decision-subhead">
    <h2>Cross-Sector Gates</h2>
  </div>
  <div class="metric-grid">
    {metric_cards_html(decision["cross_gate_cards"])}
  </div>
  <div class="section-head decision-subhead">
    <h2>Sector Pass Table</h2>
  </div>
  {sector_decision_table_html(decision["sector_rows"])}
</section>
"""


def boxplot_sections_html(validation):
    sections = []
    frame = validation["frame"]
    symbols = validation["symbols"]

    for metric in distribution_metrics_for_frame(frame):
        description = (
            f'<div class="chart-description">{html_escape(metric.description)}</div>'
            if metric.description
            else ""
        )
        sections.append(f"""
<section class="chart-card">
  <div class="section-head">
    <h3>{html_escape(metric.label)}</h3>
    <span class="pill">Aggregate + {len(symbols)} symbols</span>
  </div>
  {description}
  {svg_boxplot(frame, metric, symbols)}
</section>
""")

    return "\n".join(sections) if sections else '<div class="empty">No boxplot data.</div>'


def heatmap_sections_html(validation):
    heatmap_config = validation["heatmap_config"]
    frame = validation["frame"]
    sections = []

    for symbol in validation["symbols"]:
        symbol_frame = frame[frame[SYMBOL_COLUMN] == symbol]
        sections.append(f"""
<section class="chart-card">
  <div class="section-head">
    <h3>{html_escape(symbol)} PNL</h3>
    <span class="pill">{len(symbol_frame):,} paramsets</span>
  </div>
  {svg_heatmap(symbol_frame, symbol, heatmap_config)}
</section>
""")

    return "\n".join(sections) if sections else '<div class="empty">No heatmap data.</div>'


def validation_block_html(validation):
    if validation["frame"].empty:
        return '<div class="empty">No optimization rows matched this group.</div>'

    return f"""
<section class="section">
  <div class="section-head"><h2>Summary Statistics</h2></div>
  <div class="metric-grid">
    {metric_cards_html(validation["summary_metrics"])}
  </div>
</section>
<section class="section">
  <div class="section-head"><h2>Boxplots</h2></div>
  <div class="chart-grid">
    {boxplot_sections_html(validation)}
  </div>
</section>
<section class="section">
  <div class="section-head"><h2>PNL Heatmaps</h2></div>
  <div class="chart-grid">
    {heatmap_sections_html(validation)}
  </div>
</section>
"""


def build_validation_payload(
    name,
    frame,
    symbols,
    thresholds,
    group_column,
    group_label,
    heatmap_config,
):
    return {
        "name": name,
        "frame": frame,
        "symbols": symbols,
        "summary_metrics": build_summary_metrics(
            frame,
            thresholds,
            group_column,
            group_label,
        ),
        "heatmap_config": heatmap_config,
    }


def sector_order(frame):
    ordered = [sector for sector, _ in SECTOR_SYMBOLS]

    if SECTOR_COLUMN in frame.columns:
        extras = sorted(
            sector
            for sector in frame[SECTOR_COLUMN].dropna().unique()
            if sector not in ordered
        )
        ordered.extend(extras)

    return ordered


def build_sector_validations(frame, thresholds, heatmap_config, preferred_symbols):
    sectors = []

    for sector in sector_order(frame):
        sector_frame = frame[frame[SECTOR_COLUMN] == sector].copy()
        symbols = ordered_symbols(sector_frame, preferred_symbols)
        sectors.append(
            build_validation_payload(
                sector,
                sector_frame,
                symbols,
                thresholds,
                SYMBOL_COLUMN,
                "Symbols",
                heatmap_config,
            )
        )

    return sectors


def build_dashboard_payload(
    dataset,
    input_dir,
    thresholds=None,
    basket_symbols=None,
):
    thresholds = thresholds or ThresholdConfig()
    basket_symbols = list(basket_symbols or DEFAULT_BASKET_SYMBOLS)
    frame = dataset.frame
    basket_frame = filter_basket_frame(frame, basket_symbols)
    heatmap_config = build_heatmap_config(frame, dataset.parameter_columns)
    basket_present = ordered_symbols(basket_frame, basket_symbols)
    cross_validation = build_validation_payload(
        "Cross-Sector Validation",
        basket_frame,
        basket_present,
        thresholds,
        SECTOR_COLUMN,
        "Sectors",
        heatmap_config,
    )
    sector_validations = build_sector_validations(
        frame,
        thresholds,
        heatmap_config,
        basket_symbols,
    )

    return {
        "frame": frame,
        "input_dir": str(input_dir),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": int(len(frame)),
        "symbol_count": int(frame[SYMBOL_COLUMN].nunique()),
        "input_files": dataset.input_files,
        "parameter_columns": dataset.parameter_columns,
        "heatmap_config": heatmap_config,
        "cross_validation": cross_validation,
        "sector_validations": sector_validations,
        "decision_board": build_decision_board(
            cross_validation,
            sector_validations,
            thresholds,
        ),
    }


def sector_details_html(sector_validations):
    blocks = []

    for validation in sector_validations:
        frame = validation["frame"]
        symbol_count = frame[SYMBOL_COLUMN].nunique() if not frame.empty else 0
        blocks.append(f"""
<details class="sector-detail">
  <summary>
    <span>{html_escape(validation["name"])}</span>
    <span class="pill">{symbol_count:,} symbols | {len(frame):,} paramsets</span>
  </summary>
  <div class="validation-body">
    {validation_block_html(validation)}
  </div>
</details>
""")

    return "\n".join(blocks)


def heatmap_axis_label(heatmap_config):
    if heatmap_config is None:
        return "n/a"

    return (
        f'{heatmap_config["x_param"]} x {heatmap_config["y_param"]}'
    )


def dashboard_html(payload):
    input_dir = html_escape(payload["input_dir"])
    generated_at = html_escape(payload["generated_at"])
    heatmap_axes = html_escape(heatmap_axis_label(payload["heatmap_config"]))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cross-Sector Optimization Dashboard</title>
  <style>
    :root {{
      --bg: #f4f7f8;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667481;
      --line: #d8e1e7;
      --good: #117a4f;
      --good-bg: #e8f7ee;
      --bad: #b42318;
      --bad-bg: #fff1ef;
      --neutral: #126277;
      --neutral-bg: #ebf5f8;
      --accent: #126277;
      --accent-2: #5e3b9a;
      --warm: #c4622d;
      --soft: #f8fbfc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.42;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 22px 26px 18px;
    }}
    main {{
      max-width: 1720px;
      margin: 0 auto;
      padding: 20px 26px 34px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 26px; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 16px; letter-spacing: 0; }}
    h3 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    .header-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: end;
    }}
    .source-box {{
      color: var(--muted);
      font-size: 12px;
      text-align: right;
    }}
    .validation-section,
    .sector-detail {{
      margin: 0 0 16px;
      background: transparent;
    }}
    .validation-section > summary,
    .sector-detail > summary {{
      cursor: pointer;
      list-style: none;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
      box-shadow: 0 14px 28px rgba(23, 32, 42, 0.05);
    }}
    .validation-section > summary::-webkit-details-marker,
    .sector-detail > summary::-webkit-details-marker {{
      display: none;
    }}
    .validation-section > summary::before,
    .sector-detail > summary::before {{
      content: ">";
      color: var(--accent);
      font-size: 15px;
      margin-right: 2px;
    }}
    .validation-section[open] > summary::before,
    .sector-detail[open] > summary::before {{
      content: "v";
    }}
    .validation-body {{
      padding: 16px 0 4px;
    }}
    .section {{
      margin: 0 0 22px;
    }}
    .decision-board {{
      margin: 0 0 20px;
      padding: 16px;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 14px 28px rgba(23, 32, 42, 0.05);
    }}
    .decision-subhead {{
      margin-top: 16px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .metric-card,
    .chart-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 14px 28px rgba(23, 32, 42, 0.05);
    }}
    .metric-card {{
      padding: 16px;
      border-left-width: 6px;
    }}
    .metric-card.good {{
      border-left-color: var(--good);
      background: linear-gradient(180deg, #ffffff 0%, var(--good-bg) 100%);
    }}
    .metric-card.bad {{
      border-left-color: var(--bad);
      background: linear-gradient(180deg, #ffffff 0%, var(--bad-bg) 100%);
    }}
    .metric-card.neutral {{
      border-left-color: var(--neutral);
      background: linear-gradient(180deg, #ffffff 0%, var(--neutral-bg) 100%);
    }}
    .metric-topline {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .metric-value {{
      margin-top: 8px;
      font-size: 30px;
      font-weight: 800;
    }}
    .metric-threshold {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
      gap: 14px;
    }}
    .chart-card {{
      padding: 14px;
      overflow: hidden;
    }}
    .chart-description {{
      max-width: 920px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    .pill {{
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      padding: 4px 9px;
      white-space: nowrap;
    }}
    svg {{
      width: 100%;
      height: auto;
      display: block;
      margin-top: 8px;
    }}
    .chart-bg {{ fill: #fbfdfe; }}
    .grid-line {{ stroke: #dfe7ec; stroke-width: 1; }}
    .axis-line {{ stroke: #9aa8b3; stroke-width: 1.3; }}
    .reference-line {{
      stroke: var(--warm);
      stroke-width: 1.2;
      stroke-dasharray: 5 5;
    }}
    .axis-label,
    .x-label,
    .axis-title {{
      fill: #657280;
      font-size: 11px;
    }}
    .box {{
      fill: #dceff4;
      stroke: var(--accent);
      stroke-width: 1.4;
    }}
    .whisker {{
      stroke: #6f7d88;
      stroke-width: 1.4;
      stroke-linecap: round;
    }}
    .median-mark {{
      stroke: var(--accent-2);
      stroke-width: 2.4;
      stroke-linecap: round;
    }}
    .heat-cell {{
      stroke: #ffffff;
      stroke-width: 1.2;
    }}
    .heat-frame {{
      fill: none;
      stroke: #9aa8b3;
      stroke-width: 1.2;
    }}
    .table-scroll {{
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      white-space: nowrap;
    }}
    .data-table th {{
      position: sticky;
      top: 0;
      background: #eef4f6;
      color: #2f3c45;
      font-weight: 760;
      text-align: left;
      padding: 8px 9px;
      border-bottom: 1px solid var(--line);
    }}
    .data-table td {{
      padding: 7px 9px;
      border-bottom: 1px solid #edf1f4;
    }}
    .data-table tr:nth-child(even) td {{ background: #fbfdfe; }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      min-width: 58px;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 3px 8px;
      font-weight: 800;
    }}
    .status-pill.good {{
      color: var(--good);
      background: var(--good-bg);
      border-color: #bde8cc;
    }}
    .status-pill.bad {{
      color: var(--bad);
      background: var(--bad-bg);
      border-color: #ffd4cf;
    }}
    .status-pill.neutral {{
      color: var(--neutral);
      background: var(--neutral-bg);
      border-color: #c9e7ef;
    }}
    .empty,
    .empty-chart {{
      color: var(--muted);
      fill: var(--muted);
    }}
    .empty {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    @media (max-width: 760px) {{
      header, main {{ padding-left: 14px; padding-right: 14px; }}
      .header-grid {{ grid-template-columns: 1fr; }}
      .source-box {{ text-align: left; }}
      .chart-grid {{ grid-template-columns: 1fr; }}
      .validation-section > summary,
      .sector-detail > summary {{
        align-items: flex-start;
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-grid">
      <div>
        <h1>Cross-Sector Optimization Dashboard</h1>
        <div class="subtle">
          {payload["row_count"]:,} paramsets | {payload["symbol_count"]:,} symbols | {len(payload["input_files"]):,} CSVs | Heatmap axes {heatmap_axes}
        </div>
      </div>
      <div class="source-box">
        <div>{input_dir}</div>
        <div>{generated_at}</div>
      </div>
    </div>
  </header>
  <main>
    {decision_board_html(payload["decision_board"])}
    <details class="validation-section">
      <summary>
        <span>Cross-Sector Validation</span>
        <span class="pill">{len(payload["cross_validation"]["symbols"]):,} symbols | {len(payload["cross_validation"]["frame"]):,} paramsets</span>
      </summary>
      <div class="validation-body">
        {validation_block_html(payload["cross_validation"])}
      </div>
    </details>
    <details class="validation-section">
      <summary>
        <span>Single-Sector Validation</span>
        <span class="pill">{len(payload["sector_validations"]):,} sectors</span>
      </summary>
      <div class="validation-body">
        {sector_details_html(payload["sector_validations"])}
      </div>
    </details>
  </main>
</body>
</html>
"""


def write_dashboard(
    input_dir=DEFAULT_INPUT_DIR,
    output_file=DEFAULT_OUTPUT,
    thresholds=None,
    basket_symbols=None,
):
    dataset = load_optimization_directory(input_dir)
    payload = build_dashboard_payload(
        dataset,
        input_dir,
        thresholds or ThresholdConfig(),
        basket_symbols,
    )
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(dashboard_html(payload), encoding="utf-8")
    return output_file, payload


def parse_symbol_list(value):
    symbols = []

    for item in str(value).split(","):
        item = item.strip()

        if not item:
            continue

        symbols.append(normalize_symbol(item))

    return symbols


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate AmiBroker optimization CSVs into a cross-sector "
            "validation dashboard."
        )
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Directory containing optimization CSVs. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Dashboard HTML path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--basket-symbols",
        default=",".join(DEFAULT_BASKET_SYMBOLS),
        help="Comma-separated symbols for the Cross-Sector Validation basket.",
    )
    parser.add_argument(
        "--profitable-groups-threshold",
        type=float,
        default=65.0,
        help="Green threshold for sectors/symbols with median Net Profit above 0.",
    )
    parser.add_argument(
        "--profit-factor-threshold",
        type=float,
        default=1.10,
        help="Green threshold for median Profit Factor.",
    )
    parser.add_argument(
        "--trade-count-threshold",
        type=float,
        default=100.0,
        help="Trade-count cutoff for the informational paramset percentage.",
    )
    parser.add_argument(
        "--trade-coverage-threshold",
        type=float,
        default=50.0,
        help=(
            "Green threshold for the percent of paramsets above the "
            "trade-count cutoff."
        ),
    )
    parser.add_argument(
        "--open-dashboard",
        action="store_true",
        help="Open the generated dashboard in the default browser.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    thresholds = ThresholdConfig(
        profitable_groups_threshold=args.profitable_groups_threshold,
        profit_factor_threshold=args.profit_factor_threshold,
        trade_count_threshold=args.trade_count_threshold,
        trade_coverage_threshold=args.trade_coverage_threshold,
    )
    output_file, payload = write_dashboard(
        args.input_dir,
        args.output,
        thresholds,
        parse_symbol_list(args.basket_symbols),
    )
    print(f"Loaded {payload['row_count']:,} optimization rows")
    print(f"Loaded {payload['symbol_count']:,} symbols")
    print(f"Heatmap axes: {heatmap_axis_label(payload['heatmap_config'])}")
    print(f"Dashboard written to {output_file}")

    if args.open_dashboard:
        webbrowser.open(output_file.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
