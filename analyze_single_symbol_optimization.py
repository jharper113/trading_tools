import argparse
import html
import math
import os
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_INPUT = Path("data/backtesting/SingleSymbolOptimization.csv")
DEFAULT_OUTPUT = Path(
    "output/backtesting/single_symbol_optimization_dashboard.html"
)

PROFIT_FACTOR_COLUMN = "Profit Factor"
TRADE_COUNT_COLUMN = "# Trades"
AVG_TRADE_COLUMN = "Avg Profit/Loss"
NET_PROFIT_COLUMN = "Net Profit"
PARAMETER_MARKER_COLUMN = "L. Avg % Loss"

AMIBROKER_STATIC_COLUMNS = [
    "No.",
    "Net Profit",
    "Net % Profit",
    "Exposure %",
    "CAR",
    "RAR",
    "Max. Trade Drawdown",
    "Max. Trade % Drawdown",
    "Max. Sys Drawdown",
    "Max. Sys % Drawdown",
    "Recovery Factor",
    "CAR/MDD",
    "RAR/MDD",
    "Profit Factor",
    "Payoff Ratio",
    "Standard Error",
    "RRR",
    "Ulcer Index",
    "Ulcer Perf. Index",
    "Sharpe Ratio",
    "K-Ratio",
    "# Trades",
    "Avg Profit/Loss",
    "Avg % Profit/Loss",
    "Avg Bars Held",
    "# of winners",
    "% of Winners",
    "W. Tot. Profit",
    "W. Avg. Profit",
    "W. Avg % Profit",
    "W. Avg. Bars Held",
    "# of losers",
    "% of Losers",
    "L. Tot. Loss",
    "L. Avg. Loss",
    "L. Avg % Loss",
    "L. Avg. Bars Held",
]


@dataclass(frozen=True)
class ThresholdConfig:
    profit_factor_threshold: float = 1.10
    trades_threshold: float = 100.0
    avg_trade_cost_multiple: float = 3.0
    commission_per_trade: float = 0.0
    slippage_per_trade: float = 0.0
    profitable_paramsets_threshold: float = 70.0
    profitable_profit_threshold: float = 0.0

    @property
    def avg_trade_threshold(self):
        return self.avg_trade_cost_multiple * (
            self.commission_per_trade + self.slippage_per_trade
        )


def numeric_series(series):
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace("$", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace('"', "", regex=False)
    )
    cleaned = cleaned.replace({
        "": pd.NA,
        "--": pd.NA,
        "nan": pd.NA,
        "NaN": pd.NA,
        "N/A": pd.NA,
    })
    return pd.to_numeric(cleaned, errors="coerce")


def coerce_numeric_columns(frame):
    result = frame.copy()

    for column in result.columns:
        original = result[column]
        non_empty = (
            original.notna()
            & original.astype(str).str.strip().ne("")
            & original.astype(str).str.strip().ne("--")
        )

        if not non_empty.any():
            continue

        converted = numeric_series(original)

        if converted[non_empty].notna().all():
            result[column] = converted

    return result


def load_optimization_csv(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Optimization CSV not found: {path}")

    frame = pd.read_csv(path)
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = coerce_numeric_columns(frame)
    validate_required_columns(frame)
    return frame


def validate_required_columns(frame):
    missing = [
        column
        for column in [
            PROFIT_FACTOR_COLUMN,
            TRADE_COUNT_COLUMN,
            AVG_TRADE_COLUMN,
            NET_PROFIT_COLUMN,
        ]
        if column not in frame.columns
    ]

    if missing:
        raise ValueError(
            "Optimization CSV is missing required column(s): "
            + ", ".join(missing)
        )


def parameter_columns(frame):
    columns = list(frame.columns)
    static_indexes = [
        columns.index(column)
        for column in AMIBROKER_STATIC_COLUMNS
        if column in frame.columns
    ]

    if static_indexes:
        start_index = max(static_indexes) + 1
    elif PARAMETER_MARKER_COLUMN in frame.columns:
        start_index = columns.index(PARAMETER_MARKER_COLUMN) + 1
    else:
        raise ValueError(
            f"Could not find parameter boundary column: "
            f"{PARAMETER_MARKER_COLUMN}"
        )

    return [
        column
        for column in columns[start_index:]
        if column and not str(column).startswith("Unnamed")
    ]


def finite_number(value):
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(number) or math.isinf(number):
        return None

    return number


def median_value(frame, column):
    if column not in frame.columns:
        return None

    values = numeric_series(frame[column]).dropna()

    if values.empty:
        return None

    return float(values.median())


def percentage_profitable_paramsets(frame, profit_threshold=0.0):
    profits = numeric_series(frame[NET_PROFIT_COLUMN]).dropna()

    if profits.empty:
        return None

    return float((profits > profit_threshold).mean() * 100.0)


def percentage_paramsets_over_trade_threshold(frame, threshold):
    trades = numeric_series(frame[TRADE_COUNT_COLUMN]).dropna()

    if trades.empty:
        return None

    return float((trades > threshold).mean() * 100.0)


def pass_fail_status(value, threshold):
    number = finite_number(value)

    if number is None:
        return "bad"

    return "good" if number >= threshold else "bad"


def format_metric_value(value, suffix="", decimals=2):
    number = finite_number(value)

    if number is None:
        return "n/a"

    if abs(number) >= 100:
        formatted = f"{number:,.0f}"
    else:
        formatted = f"{number:,.{decimals}f}"

    return f"{formatted}{suffix}"


def build_summary_metrics(frame, thresholds):
    median_profit_factor = median_value(frame, PROFIT_FACTOR_COLUMN)
    median_trades = median_value(frame, TRADE_COUNT_COLUMN)
    median_avg_trade = median_value(frame, AVG_TRADE_COLUMN)
    profitable_pct = percentage_profitable_paramsets(
        frame,
        thresholds.profitable_profit_threshold,
    )
    trade_count_pct = percentage_paramsets_over_trade_threshold(
        frame,
        thresholds.trades_threshold,
    )
    avg_trade_threshold = thresholds.avg_trade_threshold

    return [
        {
            "key": "median_profit_factor",
            "label": "Median Profit Factor",
            "value": median_profit_factor,
            "display_value": format_metric_value(median_profit_factor),
            "threshold": thresholds.profit_factor_threshold,
            "threshold_text": (
                f">= {thresholds.profit_factor_threshold:.2f}"
            ),
            "status": pass_fail_status(
                median_profit_factor,
                thresholds.profit_factor_threshold,
            ),
        },
        {
            "key": "median_trades",
            "label": "Median # of Trades",
            "value": median_trades,
            "display_value": format_metric_value(median_trades, decimals=0),
            "threshold": thresholds.trades_threshold,
            "threshold_text": f">= {thresholds.trades_threshold:,.0f}",
            "status": pass_fail_status(
                median_trades,
                thresholds.trades_threshold,
            ),
        },
        {
            "key": "median_avg_trade",
            "label": "Median Avg Trade",
            "value": median_avg_trade,
            "display_value": format_metric_value(median_avg_trade),
            "threshold": avg_trade_threshold,
            "threshold_text": f">= {avg_trade_threshold:,.2f}",
            "status": pass_fail_status(median_avg_trade, avg_trade_threshold),
        },
        {
            "key": "paramsets_over_trade_threshold_pct",
            "label": (
                f"Paramsets With > {thresholds.trades_threshold:,.0f} Trades"
            ),
            "value": trade_count_pct,
            "display_value": format_metric_value(trade_count_pct, suffix="%"),
            "threshold_text": "Informational",
            "threshold_prefix": "",
            "status": "neutral",
        },
        {
            "key": "profitable_paramsets_pct",
            "label": "Profitable Paramsets",
            "value": profitable_pct,
            "display_value": format_metric_value(
                profitable_pct,
                suffix="%",
            ),
            "threshold": thresholds.profitable_paramsets_threshold,
            "threshold_text": (
                f">= {thresholds.profitable_paramsets_threshold:.0f}%"
            ),
            "status": pass_fail_status(
                profitable_pct,
                thresholds.profitable_paramsets_threshold,
            ),
        },
    ]


def sortable_value(value):
    number = finite_number(value)

    if number is not None:
        return (0, number)

    return (1, str(value))


def unique_sorted_values(frame, column):
    if column not in frame.columns:
        return []

    values = [
        value
        for value in frame[column].dropna().unique().tolist()
        if str(value).strip() != ""
    ]
    return sorted(values, key=sortable_value)


def select_heatmap_parameters(frame, parameters):
    scores = []

    for index, column in enumerate(parameters):
        if column not in frame.columns:
            continue

        values = numeric_series(frame[column])

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


def build_heatmap_config_for_pair(frame, x_param, y_param):
    return {
        "x_param": x_param,
        "y_param": y_param,
        "x_values": unique_sorted_values(frame, x_param),
        "y_values": unique_sorted_values(frame, y_param),
        "max_abs": heatmap_scale(frame, x_param, y_param),
    }


def build_heatmap_config(frame, parameters):
    selected = select_heatmap_parameters(frame, parameters)

    if len(selected) < 2:
        return None

    return build_heatmap_config_for_pair(frame, selected[0], selected[1])


def build_heatmap_configs(frame, parameters):
    varying_parameters = []

    for parameter in parameters:
        if parameter not in frame.columns:
            continue

        values = numeric_series(frame[parameter])
        if values.nunique(dropna=True) > 1:
            varying_parameters.append(parameter)

    configs = []
    for x_index, x_param in enumerate(varying_parameters):
        for y_param in varying_parameters[x_index + 1:]:
            configs.append(
                build_heatmap_config_for_pair(frame, x_param, y_param)
            )

    if configs:
        shared_max_abs = max(config["max_abs"] for config in configs)
        for config in configs:
            config["max_abs"] = shared_max_abs

    return configs


def parameter_profile(frame, parameter_column):
    if parameter_column not in frame.columns:
        return pd.DataFrame(columns=[])

    working = frame[[parameter_column, PROFIT_FACTOR_COLUMN, NET_PROFIT_COLUMN]]
    working = working.copy()
    working[PROFIT_FACTOR_COLUMN] = numeric_series(
        working[PROFIT_FACTOR_COLUMN]
    )
    working[NET_PROFIT_COLUMN] = numeric_series(working[NET_PROFIT_COLUMN])
    working = working.dropna(subset=[parameter_column, PROFIT_FACTOR_COLUMN])

    if working.empty:
        return pd.DataFrame(columns=[])

    rows = []

    for value, group in working.groupby(parameter_column, dropna=False):
        profit_factor = group[PROFIT_FACTOR_COLUMN].dropna()
        net_profit = group[NET_PROFIT_COLUMN].dropna()
        rows.append({
            "parameter": parameter_column,
            "parameter_value": value,
            "paramsets": int(len(group)),
            "median_profit_factor": (
                float(profit_factor.median())
                if not profit_factor.empty
                else None
            ),
            "p25_profit_factor": (
                float(profit_factor.quantile(0.25))
                if not profit_factor.empty
                else None
            ),
            "p75_profit_factor": (
                float(profit_factor.quantile(0.75))
                if not profit_factor.empty
                else None
            ),
            "profitable_pct": (
                float((net_profit > 0).mean() * 100.0)
                if not net_profit.empty
                else None
            ),
            "median_net_profit": (
                float(net_profit.median())
                if not net_profit.empty
                else None
            ),
            "sort_key": sortable_value(value),
        })

    profile = pd.DataFrame(rows)
    profile = profile.sort_values("sort_key").drop(columns=["sort_key"])
    return profile.reset_index(drop=True)


def parameter_profiles(frame, parameters):
    return {
        parameter: parameter_profile(frame, parameter)
        for parameter in parameters
    }


def preferred_range_status(value, threshold, comparison):
    number = finite_number(value)

    if number is None:
        return "neutral"

    if comparison == "at_least":
        preferred = number >= threshold
    elif comparison == "above":
        preferred = number > threshold
    elif comparison == "at_most":
        preferred = number <= threshold
    elif comparison == "absolute_at_most":
        preferred = abs(number) <= threshold
    else:
        raise ValueError(f"Unknown robustness comparison: {comparison}")

    return "good" if preferred else "bad"


def robustness_metric(
    key,
    label,
    value,
    display_value,
    threshold,
    threshold_text,
    comparison,
    description,
):
    status = preferred_range_status(value, threshold, comparison)
    return {
        "key": key,
        "label": label,
        "value": value,
        "display_value": display_value,
        "threshold_text": threshold_text,
        "status": status,
        "status_text": (
            "In preferred range"
            if status == "good"
            else "Outside preferred range"
            if status == "bad"
            else "No data"
        ),
        "description": description,
    }


def robustness_metrics(frame):
    metrics = []

    for spec in [
        {
            "key": "median_car_mdd",
            "column": "CAR/MDD",
            "label": "Median CAR/MDD",
            "suffix": "",
            "threshold": 1.0,
            "threshold_text": ">= 1.00",
            "comparison": "at_least",
            "description": (
                "Annualized return earned per unit of maximum drawdown; "
                "higher is better."
            ),
        },
        {
            "key": "median_max_drawdown",
            "column": "Max. Sys % Drawdown",
            "label": "Median Max Sys % Drawdown",
            "suffix": "%",
            "threshold": 20.0,
            "threshold_text": "absolute drawdown <= 20%",
            "comparison": "absolute_at_most",
            "description": (
                "Typical worst peak-to-trough equity decline across "
                "paramsets; closer to zero is better."
            ),
        },
        {
            "key": "median_recovery_factor",
            "column": "Recovery Factor",
            "label": "Median Recovery Factor",
            "suffix": "",
            "threshold": 1.0,
            "threshold_text": ">= 1.00",
            "comparison": "at_least",
            "description": (
                "Net profit relative to maximum drawdown; higher means "
                "drawdowns were recovered more efficiently."
            ),
        },
        {
            "key": "median_ulcer_index",
            "column": "Ulcer Index",
            "label": "Median Ulcer Index",
            "suffix": "",
            "threshold": 10.0,
            "threshold_text": "<= 10.00",
            "comparison": "at_most",
            "description": (
                "Depth and duration of equity drawdowns; lower indicates "
                "a smoother equity curve."
            ),
        },
    ]:
        if spec["column"] not in frame.columns:
            continue

        value = median_value(frame, spec["column"])
        metrics.append(robustness_metric(
            spec["key"],
            spec["label"],
            value,
            format_metric_value(value, suffix=spec["suffix"]),
            spec["threshold"],
            spec["threshold_text"],
            spec["comparison"],
            spec["description"],
        ))

    profit_factor = numeric_series(frame[PROFIT_FACTOR_COLUMN]).dropna()
    if not profit_factor.empty:
        value = float(
            profit_factor.quantile(0.75) - profit_factor.quantile(0.25)
        )
        metrics.append(robustness_metric(
            "profit_factor_iqr",
            "Profit Factor IQR",
            value,
            format_metric_value(value),
            0.50,
            "<= 0.50",
            "at_most",
            (
                "Spread of the middle 50% of Profit Factor results; lower "
                "means parameters behave more consistently."
            ),
        ))

    net_profit = numeric_series(frame[NET_PROFIT_COLUMN]).dropna()
    if not net_profit.empty:
        value = float(net_profit.quantile(0.10))
        formatted_value = format_metric_value(abs(value))
        display_value = (
            f"-${formatted_value}" if value < 0 else f"${formatted_value}"
        )
        metrics.append(robustness_metric(
            "worst_decile_net_profit",
            "Worst Decile Net Profit",
            value,
            display_value,
            0.0,
            "> $0",
            "above",
            (
                "10th-percentile net profit; positive means even weaker "
                "paramsets remained profitable."
            ),
        ))

    return metrics


def html_escape(value):
    return html.escape(str(value), quote=True)


def fmt_axis(value):
    number = finite_number(value)

    if number is None:
        return ""

    if abs(number) >= 100:
        return f"{number:,.0f}"

    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_currency(value, decimals=0):
    number = finite_number(value)

    if number is None:
        return "n/a"

    formatted = f"{abs(number):,.{decimals}f}"
    return f"-${formatted}" if number < 0 else f"${formatted}"


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


def svg_heatmap(frame, heatmap_config, label="Single Symbol"):
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

    working = frame[[x_param, y_param, NET_PROFIT_COLUMN]].copy()
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
  <title>{html_escape(label)} | {html_escape(x_param)}={html_escape(x_value)} | {html_escape(y_param)}={html_escape(y_value)} | Median PNL {html_escape(title_value)}</title>
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
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html_escape(label)} PNL heatmap">
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


def svg_parameter_chart(frame, profile, parameter, thresholds):
    chart_width = 680
    chart_height = 300
    left = 56
    right = 20
    top = 22
    bottom = 58
    plot_width = chart_width - left - right
    plot_height = chart_height - top - bottom

    if profile.empty:
        return (
            f'<svg viewBox="0 0 {chart_width} {chart_height}" '
            'role="img" aria-label="No chart data"></svg>'
        )

    raw = frame[[parameter, PROFIT_FACTOR_COLUMN, NET_PROFIT_COLUMN]].copy()
    raw[PROFIT_FACTOR_COLUMN] = numeric_series(raw[PROFIT_FACTOR_COLUMN])
    raw[NET_PROFIT_COLUMN] = numeric_series(raw[NET_PROFIT_COLUMN])
    raw = raw.dropna(subset=[parameter, PROFIT_FACTOR_COLUMN])

    values = profile["parameter_value"].tolist()
    x_lookup = {value: index for index, value in enumerate(values)}
    y_values = [
        finite_number(value)
        for value in raw[PROFIT_FACTOR_COLUMN].tolist()
    ] + [
        finite_number(value)
        for value in profile["median_profit_factor"].tolist()
    ] + [thresholds.profit_factor_threshold]
    y_values = [value for value in y_values if value is not None]

    if not y_values:
        y_min = 0.0
        y_max = 1.0
    else:
        y_min = min(0.0, min(y_values))
        y_max = max(y_values)

    if abs(y_max - y_min) < 0.000001:
        y_max += 1.0

    padding = (y_max - y_min) * 0.12
    y_min -= padding
    y_max += padding

    def x_for(value, offset=0.0):
        index = x_lookup.get(value, 0)
        if len(values) == 1:
            base = left + plot_width / 2
        else:
            base = left + (plot_width * index / (len(values) - 1))
        return base + offset

    def y_for(value):
        return top + ((y_max - value) / (y_max - y_min)) * plot_height

    threshold_y = y_for(thresholds.profit_factor_threshold)
    y_ticks = [y_min, (y_min + y_max) / 2, y_max]
    tick_markup = []

    for tick in y_ticks:
        y = y_for(tick)
        tick_markup.append(
            f'<line x1="{left}" x2="{chart_width - right}" '
            f'y1="{y:.2f}" y2="{y:.2f}" class="grid-line" />'
        )
        tick_markup.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" '
            f'class="axis-label" text-anchor="end">{fmt_axis(tick)}</text>'
        )

    median_points = []
    for _, row in profile.iterrows():
        value = row["parameter_value"]
        median_pf = finite_number(row["median_profit_factor"])

        if median_pf is None:
            continue

        median_points.append(f"{x_for(value):.2f},{y_for(median_pf):.2f}")

    raw_points = []
    for index, row in raw.reset_index(drop=True).iterrows():
        value = row[parameter]
        profit_factor = finite_number(row[PROFIT_FACTOR_COLUMN])

        if profit_factor is None:
            continue

        jitter_slot = (index % 7) - 3
        jitter = jitter_slot * min(5.0, plot_width / max(len(values), 1) / 18)
        x = x_for(value, jitter)
        y = y_for(profit_factor)
        profitable = (finite_number(row[NET_PROFIT_COLUMN]) or 0.0) > 0
        point_class = "dot good-dot" if profitable else "dot bad-dot"
        raw_points.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" '
            f'class="{point_class}"><title>'
            f'{html_escape(parameter)}={html_escape(value)}, '
            f'PF={profit_factor:.2f}</title></circle>'
        )

    label_step = max(1, math.ceil(len(values) / 10))
    x_labels = []
    for index, value in enumerate(values):
        if index % label_step != 0 and index != len(values) - 1:
            continue

        x = x_for(value)
        label = html_escape(value)
        x_labels.append(
            f'<text x="{x:.2f}" y="{chart_height - 26}" '
            'class="x-label" text-anchor="end" '
            f'transform="rotate(-35 {x:.2f} {chart_height - 26})">'
            f'{label}</text>'
        )

    median_polyline = ""
    if median_points:
        median_polyline = (
            f'<polyline points="{" ".join(median_points)}" '
            'class="median-line" />'
        )

    return f"""
<svg viewBox="0 0 {chart_width} {chart_height}" role="img"
     aria-label="{html_escape(parameter)} Profit Factor chart">
  <rect x="0" y="0" width="{chart_width}" height="{chart_height}"
        class="chart-bg" />
  {"".join(tick_markup)}
  <line x1="{left}" x2="{chart_width - right}"
        y1="{threshold_y:.2f}" y2="{threshold_y:.2f}"
        class="threshold-line" />
  <text x="{chart_width - right}" y="{threshold_y - 6:.2f}"
        class="threshold-label" text-anchor="end">
        PF threshold {thresholds.profit_factor_threshold:.2f}</text>
  <line x1="{left}" x2="{left}" y1="{top}" y2="{chart_height - bottom}"
        class="axis-line" />
  <line x1="{left}" x2="{chart_width - right}"
        y1="{chart_height - bottom}" y2="{chart_height - bottom}"
        class="axis-line" />
  {"".join(raw_points)}
  {median_polyline}
  {"".join(x_labels)}
  <text x="18" y="{top + plot_height / 2:.2f}" class="axis-title"
        text-anchor="middle" transform="rotate(-90 18 {top + plot_height / 2:.2f})">
        Profit Factor</text>
</svg>
"""


def dataframe_to_table(frame, max_rows=None):
    if frame is None or frame.empty:
        return '<div class="empty">No data.</div>'

    display = frame.copy()

    if max_rows is not None:
        display = display.head(max_rows)

    for column in display.columns:
        if pd.api.types.is_numeric_dtype(display[column]):
            display[column] = display[column].map(
                lambda value: format_metric_value(value)
            )

    return display.to_html(
        index=False,
        border=0,
        classes="data-table",
        escape=True,
    )


def metric_cards_html(metrics):
    cards = []

    for metric in metrics:
        threshold_prefix = metric.get("threshold_prefix", "Target ")
        cards.append(f"""
<article class="metric-card {html_escape(metric["status"])}">
  <div class="metric-topline">{html_escape(metric["label"])}</div>
  <div class="metric-value">{html_escape(metric["display_value"])}</div>
  <div class="metric-threshold">{html_escape(threshold_prefix)}{html_escape(metric["threshold_text"])}</div>
</article>
""")

    return "\n".join(cards)


def robustness_cards_html(metrics):
    cards = []

    for metric in metrics:
        cards.append(f"""
<article class="mini-card {html_escape(metric["status"])}">
  <div class="metric-topline">{html_escape(metric["label"])}</div>
  <div class="mini-value">{html_escape(metric["display_value"])}</div>
  <div class="mini-range">Preferred {html_escape(metric["threshold_text"])} | {html_escape(metric["status_text"])}</div>
  <div class="mini-description">{html_escape(metric["description"])}</div>
</article>
""")

    return "\n".join(cards)


def parameter_sections_html(frame, profiles, thresholds):
    sections = []

    for parameter, profile in profiles.items():
        chart = svg_parameter_chart(frame, profile, parameter, thresholds)
        table_columns = [
            "parameter_value",
            "paramsets",
            "median_profit_factor",
            "p25_profit_factor",
            "p75_profit_factor",
            "profitable_pct",
            "median_net_profit",
        ]
        table = dataframe_to_table(
            profile[[column for column in table_columns if column in profile]],
            max_rows=30,
        )
        sections.append(f"""
<section class="chart-card">
  <div class="section-head">
    <h2>{html_escape(parameter)}</h2>
    <span class="pill">{len(profile)} unique values</span>
  </div>
  {chart}
  <div class="table-scroll compact">{table}</div>
</section>
""")

    if not sections:
        return '<div class="empty">No parameter columns were found.</div>'

    return "\n".join(sections)


def top_paramsets_table(frame):
    display_columns = [
        column
        for column in [
            "No.",
            PROFIT_FACTOR_COLUMN,
            NET_PROFIT_COLUMN,
            TRADE_COUNT_COLUMN,
            AVG_TRADE_COLUMN,
            "CAR/MDD",
            "Max. Sys % Drawdown",
        ]
        if column in frame.columns
    ]
    params = parameter_columns(frame)
    display_columns.extend(params)

    top = frame.sort_values(
        by=PROFIT_FACTOR_COLUMN,
        ascending=False,
    )[display_columns]
    return dataframe_to_table(top, max_rows=20)


def heatmap_axis_label(heatmap_config):
    if heatmap_config is None:
        return "Not enough varying parameters"

    return f'{heatmap_config["x_param"]} x {heatmap_config["y_param"]}'


def heatmap_sections_html(frame, heatmap_configs):
    sections = []

    for config in heatmap_configs:
        axis_label = heatmap_axis_label(config)
        cell_count = len(config["x_values"]) * len(config["y_values"])
        sections.append(f"""
<section class="chart-card">
  <div class="section-head">
    <h2>{html_escape(axis_label)}</h2>
    <span class="pill">{cell_count:,} parameter cells</span>
  </div>
  {svg_heatmap(frame, config, label=axis_label)}
</section>
""")

    if not sections:
        return '<div class="empty">At least two varying numeric parameters are required.</div>'

    return "\n".join(sections)


def dashboard_html(payload):
    input_file = html_escape(payload["input_file"])
    generated_at = html_escape(payload["generated_at"])
    heatmap_pair_count = len(payload["heatmap_configs"])
    heatmap_pair_label = (
        f"{heatmap_pair_count:,} parameter pair"
        + ("s" if heatmap_pair_count != 1 else "")
    )
    parameter_list = ", ".join(
        html_escape(parameter)
        for parameter in payload["parameter_columns"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Single Symbol Optimization</title>
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
    .section {{
      margin: 0 0 22px;
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
    .mini-card,
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
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 10px;
    }}
    .mini-card {{
      padding: 13px;
      border-left-width: 5px;
    }}
    .mini-card.good {{
      border-left-color: var(--good);
      background: linear-gradient(180deg, #ffffff 0%, var(--good-bg) 100%);
    }}
    .mini-card.bad {{
      border-left-color: var(--bad);
      background: linear-gradient(180deg, #ffffff 0%, var(--bad-bg) 100%);
    }}
    .mini-value {{
      margin-top: 5px;
      font-size: 20px;
      font-weight: 760;
    }}
    .mini-range {{
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .mini-description {{
      margin-top: 8px;
      color: #43515c;
      font-size: 12px;
      line-height: 1.45;
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
    .pill {{
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      padding: 4px 9px;
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
    .axis-label,
    .x-label,
    .axis-title,
    .threshold-label {{
      fill: #657280;
      font-size: 11px;
    }}
    .threshold-line {{
      stroke: var(--warm);
      stroke-width: 1.4;
      stroke-dasharray: 5 5;
    }}
    .dot {{
      opacity: 0.70;
      stroke: #ffffff;
      stroke-width: 1.2;
    }}
    .good-dot {{ fill: var(--accent); }}
    .bad-dot {{ fill: var(--bad); }}
    .median-line {{
      fill: none;
      stroke: var(--accent-2);
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
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
    .empty-chart {{
      fill: var(--muted);
      font-size: 13px;
    }}
    .table-scroll {{
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .table-scroll.compact {{ margin-top: 10px; max-height: 260px; }}
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
    .empty {{
      color: var(--muted);
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
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-grid">
      <div>
        <h1>Single Symbol Optimization</h1>
        <div class="subtle">
          {payload["row_count"]:,} paramsets · {payload["parameter_count"]:,} parameters · {parameter_list}
        </div>
      </div>
      <div class="source-box">
        <div>{input_file}</div>
        <div>{generated_at}</div>
      </div>
    </div>
  </header>
  <main>
    <section class="section">
      <div class="section-head"><h2>Decision Metrics</h2></div>
      <div class="metric-grid">
        {metric_cards_html(payload["summary_metrics"])}
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>Robustness Snapshot</h2></div>
      <div class="mini-grid">
        {robustness_cards_html(payload["robustness_metrics"])}
      </div>
    </section>
    <section class="section">
      <div class="section-head">
        <h2>PNL Heatmaps</h2>
        <span class="pill">{heatmap_pair_label}</span>
      </div>
      <div class="chart-grid">
        {heatmap_sections_html(payload["frame"], payload["heatmap_configs"])}
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>Profit Factor by Parameter</h2></div>
      <div class="chart-grid">
        {parameter_sections_html(payload["frame"], payload["profiles"], payload["thresholds"])}
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>Top Paramsets by Profit Factor</h2></div>
      <div class="table-scroll">{top_paramsets_table(payload["frame"])}</div>
    </section>
  </main>
</body>
</html>
"""


def build_dashboard_payload(frame, input_file, thresholds):
    parameters = parameter_columns(frame)
    return {
        "frame": frame,
        "input_file": str(input_file),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(frame),
        "parameter_count": len(parameters),
        "parameter_columns": parameters,
        "summary_metrics": build_summary_metrics(frame, thresholds),
        "robustness_metrics": robustness_metrics(frame),
        "heatmap_config": build_heatmap_config(frame, parameters),
        "heatmap_configs": build_heatmap_configs(frame, parameters),
        "profiles": parameter_profiles(frame, parameters),
        "thresholds": thresholds,
    }


def write_dashboard(input_file, output_file, thresholds):
    frame = load_optimization_csv(input_file)
    payload = build_dashboard_payload(frame, input_file, thresholds)
    html_text = dashboard_html(payload)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html_text, encoding="utf-8")
    return output_file, payload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Analyze an AmiBroker single-symbol optimization CSV and write "
            "a dashboard."
        )
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Optimization CSV path. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Dashboard HTML path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--profit-factor-threshold",
        type=float,
        default=1.10,
        help="Green threshold for median Profit Factor.",
    )
    parser.add_argument(
        "--trades-threshold",
        type=float,
        default=100.0,
        help="Green threshold for median number of trades.",
    )
    parser.add_argument(
        "--commission-per-trade",
        type=float,
        default=0.0,
        help="Estimated commission per trade for Avg Trade threshold.",
    )
    parser.add_argument(
        "--slippage-per-trade",
        type=float,
        default=0.0,
        help="Estimated slippage per trade for Avg Trade threshold.",
    )
    parser.add_argument(
        "--avg-trade-cost-multiple",
        type=float,
        default=3.0,
        help="Avg Trade must be this multiple of commission + slippage.",
    )
    parser.add_argument(
        "--profitable-paramsets-threshold",
        type=float,
        default=70.0,
        help="Green threshold for percent profitable paramsets.",
    )
    parser.add_argument(
        "--profitable-profit-threshold",
        type=float,
        default=0.0,
        help="Net Profit must exceed this value to count as profitable.",
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
        profit_factor_threshold=args.profit_factor_threshold,
        trades_threshold=args.trades_threshold,
        avg_trade_cost_multiple=args.avg_trade_cost_multiple,
        commission_per_trade=args.commission_per_trade,
        slippage_per_trade=args.slippage_per_trade,
        profitable_paramsets_threshold=args.profitable_paramsets_threshold,
        profitable_profit_threshold=args.profitable_profit_threshold,
    )
    output_file, payload = write_dashboard(
        args.input,
        args.output,
        thresholds,
    )
    print(f"Loaded {payload['row_count']:,} optimization rows")
    print(
        "Parameter columns: "
        + (
            ", ".join(payload["parameter_columns"])
            if payload["parameter_columns"]
            else "none"
        )
    )
    print(f"Dashboard written to {output_file}")

    if args.open_dashboard:
        webbrowser.open(output_file.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
