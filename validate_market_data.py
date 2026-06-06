import argparse
import json
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from download_market_data import (
    CANONICAL_COLUMNS,
    DEFAULT_SYMBOLS,
    REVIEWED_COLUMNS,
    empty_bars_frame,
    load_reviewed_bars,
    normalize_bar_frame,
    normalize_frequency,
    normalize_symbol,
    output_file_for,
    reviewed_bars_file_for,
)


DEFAULT_SOURCE_DIR = Path("data/market_data")
DEFAULT_OUTPUT_DIR = Path("output/market_data_validation")
DEFAULT_THRESHOLD_PCT = 0.25
DASHBOARD_FILE = "validation_dashboard.html"
DEFAULT_DASHBOARD_MAX_ROWS = 1000
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
PRICE_COLUMNS = ["open", "high", "low", "close"]
DIFFERENCE_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "field",
    "local_value",
    "reference_value",
    "abs_diff",
    "pct_diff",
    "threshold_pct",
    "approved",
]
MISSING_COLUMNS = ["symbol", "frequency", "comparison_key", "missing_from"]
DUPLICATE_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
]


YAHOO_FUTURES_SYMBOLS = {
    "/ES": "ES=F",
    "/6E": "6E=F",
    "/GC": "GC=F",
    "/ZW": "ZW=F",
    "/ZN": "ZN=F",
    "/CL": "CL=F",
}


def yahoo_symbol_for(symbol):
    symbol = normalize_symbol(symbol)

    if symbol in YAHOO_FUTURES_SYMBOLS:
        return YAHOO_FUTURES_SYMBOLS[symbol]

    return f"{symbol.lstrip('/')}=F"


def yahoo_interval_for(frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        return "1d"

    if frequency == "5min":
        return "5m"

    raise ValueError(f"Unsupported Yahoo validation frequency: {frequency}")


def default_yahoo_range_for(frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        return "5y"

    return "1mo"


def date_to_epoch_seconds(value):
    if not value:
        return None

    timestamp = pd.to_datetime(value, utc=True, errors="raise")

    return int(timestamp.timestamp())


def yahoo_chart_params(frequency, start=None, end=None):
    params = {
        "interval": yahoo_interval_for(frequency),
        "includePrePost": "true",
        "events": "history",
    }
    period1 = date_to_epoch_seconds(start)
    period2 = date_to_epoch_seconds(end)

    if period1 is not None or period2 is not None:
        params["period1"] = period1 or 0
        params["period2"] = period2 or int(
            datetime.now(timezone.utc).timestamp()
        )
    else:
        params["range"] = default_yahoo_range_for(frequency)

    return params


def fetch_yahoo_bars(symbol, frequency, start=None, end=None):
    yahoo_symbol = yahoo_symbol_for(symbol)
    params = yahoo_chart_params(
        frequency=frequency,
        start=start,
        end=end,
    )
    url = f"{YAHOO_CHART_URL}/{yahoo_symbol}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "trading-tools-market-data-validator/1.0",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Yahoo request failed for {symbol} {frequency}: "
            f"HTTP {error.code} {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Yahoo request failed for {symbol} {frequency}: {error.reason}"
        ) from error

    result = payload.get("chart", {}).get("result") or []

    if not result:
        return pd.DataFrame(columns=[])

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", []),
        }
    )

    return normalize_bar_frame(
        bars,
        symbol=symbol,
        frequency=frequency,
        source="yahoo",
    )


def load_local_bars(source_dir, symbol, frequency):
    path = output_file_for(source_dir, symbol, frequency)

    if not path.exists():
        return pd.DataFrame(columns=[])

    return pd.read_csv(path)


def normalize_for_comparison(bars, frequency):
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=DUPLICATE_COLUMNS)

    working = bars.copy()
    working["timestamp"] = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )
    working = working[working["timestamp"].notna()].copy()
    working["date"] = working["timestamp"].dt.date.astype(str)

    for column in PRICE_COLUMNS:
        working[column] = pd.to_numeric(
            working.get(column),
            errors="coerce",
        )

    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        working["comparison_key"] = working["date"]
    else:
        working["comparison_key"] = working["timestamp"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    return working


def find_duplicate_bars(bars, symbol, frequency):
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=[])

    working = normalize_for_comparison(bars, frequency)
    duplicates = working[
        working.duplicated("comparison_key", keep=False)
    ].copy()

    if len(duplicates) == 0:
        return pd.DataFrame(columns=DUPLICATE_COLUMNS)

    duplicates["symbol"] = normalize_symbol(symbol)
    duplicates["frequency"] = normalize_frequency(frequency)

    return duplicates[DUPLICATE_COLUMNS].copy()


def compare_market_data(local_bars, reference_bars, symbol, frequency, threshold_pct):
    local = normalize_for_comparison(local_bars, frequency)
    reference = normalize_for_comparison(reference_bars, frequency)
    symbol = normalize_symbol(symbol)
    frequency = normalize_frequency(frequency)

    if len(local) == 0 or len(reference) == 0:
        return empty_comparison_outputs(symbol, frequency)

    merged = pd.merge(
        local[["comparison_key"] + PRICE_COLUMNS],
        reference[["comparison_key"] + PRICE_COLUMNS],
        on="comparison_key",
        how="outer",
        suffixes=("_local", "_reference"),
        indicator=True,
    )
    matched = merged[merged["_merge"] == "both"].copy()

    difference_rows = []

    for _, row in matched.iterrows():
        row_max_pct_diff = 0.0
        row_approved = True

        for column in PRICE_COLUMNS:
            local_value = row[f"{column}_local"]
            reference_value = row[f"{column}_reference"]

            if pd.isna(local_value) or pd.isna(reference_value):
                abs_diff = np.nan
                pct_diff = np.nan
                approved = False
            else:
                abs_diff = local_value - reference_value
                denominator = abs(reference_value)
                pct_diff = (
                    abs(abs_diff) / denominator * 100
                    if denominator != 0
                    else np.nan
                )
                approved = bool(
                    not pd.isna(pct_diff) and pct_diff <= threshold_pct
                )

            if not approved:
                row_approved = False

            if not pd.isna(pct_diff):
                row_max_pct_diff = max(row_max_pct_diff, pct_diff)

            difference_rows.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "comparison_key": row["comparison_key"],
                    "field": column,
                    "local_value": local_value,
                    "reference_value": reference_value,
                    "abs_diff": abs_diff,
                    "pct_diff": pct_diff,
                    "threshold_pct": threshold_pct,
                    "approved": approved,
                }
            )

        difference_rows.append(
            {
                "symbol": symbol,
                "frequency": frequency,
                "comparison_key": row["comparison_key"],
                "field": "row_max",
                "local_value": np.nan,
                "reference_value": np.nan,
                "abs_diff": np.nan,
                "pct_diff": row_max_pct_diff,
                "threshold_pct": threshold_pct,
                "approved": row_approved,
            }
        )

    differences = pd.DataFrame(difference_rows, columns=DIFFERENCE_COLUMNS)
    missing = build_missing_bars_report(merged, symbol, frequency)
    summary = summarize_comparison(
        differences=differences,
        missing=missing,
        symbol=symbol,
        frequency=frequency,
        local_count=len(local),
        reference_count=len(reference),
        threshold_pct=threshold_pct,
    )

    return differences, missing, summary


def empty_comparison_outputs(symbol, frequency):
    differences = pd.DataFrame(columns=DIFFERENCE_COLUMNS)
    missing = pd.DataFrame(columns=MISSING_COLUMNS)
    summary = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "frequency": frequency,
                "local_bars": 0,
                "reference_bars": 0,
                "matched_bars": 0,
                "missing_local_bars": 0,
                "missing_reference_bars": 0,
                "max_pct_diff": np.nan,
                "mean_pct_diff": np.nan,
                "approved_bars": 0,
                "failed_bars": 0,
                "threshold_pct": np.nan,
                "approved": False,
            }
        ]
    )

    return differences, missing, summary


def build_missing_bars_report(merged, symbol, frequency):
    missing_rows = []

    for _, row in merged[merged["_merge"] != "both"].iterrows():
        missing_from = (
            "local" if row["_merge"] == "right_only" else "reference"
        )
        missing_rows.append(
            {
                "symbol": symbol,
                "frequency": frequency,
                "comparison_key": row["comparison_key"],
                "missing_from": missing_from,
            }
        )

    return pd.DataFrame(missing_rows, columns=MISSING_COLUMNS)


def summarize_comparison(
    differences,
    missing,
    symbol,
    frequency,
    local_count,
    reference_count,
    threshold_pct,
):
    row_max = differences[differences["field"] == "row_max"].copy()
    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)].copy()
    failed_bars = int((~row_max["approved"].fillna(False)).sum())
    approved_bars = int(row_max["approved"].fillna(False).sum())
    missing_from = (
        missing["missing_from"]
        if "missing_from" in missing.columns
        else pd.Series(dtype=object)
    )
    missing_local = int((missing_from == "local").sum())
    missing_reference = int((missing_from == "reference").sum())
    max_pct_diff = price_diffs["pct_diff"].max()
    mean_pct_diff = price_diffs["pct_diff"].mean()

    approved = (
        failed_bars == 0
        and missing_local == 0
        and missing_reference == 0
        and len(row_max) > 0
    )

    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "frequency": frequency,
                "local_bars": local_count,
                "reference_bars": reference_count,
                "matched_bars": len(row_max),
                "missing_local_bars": missing_local,
                "missing_reference_bars": missing_reference,
                "max_pct_diff": max_pct_diff,
                "mean_pct_diff": mean_pct_diff,
                "approved_bars": approved_bars,
                "failed_bars": failed_bars,
                "threshold_pct": threshold_pct,
                "approved": approved,
            }
        ]
    )


def save_heatmap(summary, output_dir):
    if len(summary) == 0:
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    heatmap_path = output_dir / "market_data_difference_heatmap.png"
    working = summary.copy()
    working["symbol_frequency"] = (
        working["symbol"] + " " + working["frequency"]
    )
    heatmap = working.pivot_table(
        index="symbol_frequency",
        values="max_pct_diff",
        aggfunc="max",
    ).sort_index()

    if len(heatmap) == 0:
        return None

    values = heatmap[["max_pct_diff"]].to_numpy(dtype=float)
    fig_height = max(4, len(heatmap) * 0.45)
    fig, ax = plt.subplots(figsize=(7, fig_height))
    image = ax.imshow(
        values,
        aspect="auto",
        cmap="RdYlGn_r",
        interpolation="nearest",
    )
    ax.set_xticks([0])
    ax.set_xticklabels(["Max % Difference"])
    ax.set_yticks(np.arange(len(heatmap.index)))
    ax.set_yticklabels(heatmap.index)
    ax.set_title("Market Data Validation vs Yahoo")

    for row_index, value in enumerate(values[:, 0]):
        label = "N/A" if np.isnan(value) else f"{value:.3f}%"
        ax.text(
            0,
            row_index,
            label,
            ha="center",
            va="center",
            color="black",
            fontsize=9,
        )

    fig.colorbar(image, ax=ax, label="Max % Difference")
    fig.tight_layout()
    fig.savefig(heatmap_path, dpi=160)
    plt.close(fig)

    return heatmap_path


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_for_comparison_key(comparison_key, frequency):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        timestamp = pd.to_datetime(
            str(comparison_key),
            utc=True,
            errors="raise",
        )
    else:
        timestamp = pd.to_datetime(
            str(comparison_key),
            utc=True,
            errors="raise",
        )

    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def selected_value(decision, field, selected_source):
    prefix = "reference" if selected_source == "yahoo" else "local"
    value = decision.get(f"{prefix}_{field}")

    return pd.to_numeric(value, errors="coerce")


def decision_to_bar(decision, reviewed_at):
    selected_source = str(decision.get("decision", "")).strip().lower()

    if selected_source not in {"local", "yahoo"}:
        return None

    symbol = normalize_symbol(decision["symbol"])
    frequency = normalize_frequency(decision["frequency"])

    return {
        "timestamp": timestamp_for_comparison_key(
            decision["comparison_key"],
            frequency,
        ),
        "date": str(decision["comparison_key"])[:10],
        "symbol": symbol,
        "frequency": frequency,
        "open": selected_value(decision, "open", selected_source),
        "high": selected_value(decision, "high", selected_source),
        "low": selected_value(decision, "low", selected_source),
        "close": selected_value(decision, "close", selected_source),
        "volume": pd.NA,
        "open_interest": pd.NA,
        "source": f"reviewed_{selected_source}",
        "retrieved_at": reviewed_at,
    }


def comparison_key_for_row(row):
    timestamp = pd.to_datetime(
        row["timestamp"],
        utc=True,
        errors="coerce",
    )
    frequency = normalize_frequency(row["frequency"])

    if pd.isna(timestamp):
        return ""

    if frequency == "daily":
        return str(timestamp.date())

    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_reconciliation_decisions(source_dir, decisions):
    source_dir = Path(source_dir)
    reviewed_at = utc_now_text()
    selected_bars = []
    reviewed_rows = []

    for decision in decisions:
        selected_source = str(decision.get("decision", "")).strip().lower()

        if selected_source not in {"local", "yahoo"}:
            continue

        bar = decision_to_bar(decision, reviewed_at=reviewed_at)

        if bar is None:
            continue

        selected_bars.append(bar)
        reviewed_rows.append(
            {
                "symbol": bar["symbol"],
                "frequency": bar["frequency"],
                "comparison_key": str(decision["comparison_key"]),
                "selected_source": selected_source,
                "reviewed_at": reviewed_at,
            }
        )

    if not selected_bars:
        return {"updated_bars": 0, "reviewed_bars": 0}

    selected = pd.DataFrame(selected_bars, columns=CANONICAL_COLUMNS)
    selected["timestamp"] = pd.to_datetime(
        selected["timestamp"],
        utc=True,
        errors="coerce",
    )
    selected["timestamp"] = selected["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    for (symbol, frequency), group in selected.groupby(["symbol", "frequency"]):
        output_path = output_file_for(source_dir, symbol, frequency)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            pd.read_csv(output_path)
            if output_path.exists()
            else empty_bars_frame()
        )

        if len(existing) > 0:
            existing = existing.copy()
            existing["symbol"] = existing["symbol"].map(normalize_symbol)
            existing["frequency"] = existing["frequency"].map(normalize_frequency)
            existing["_comparison_key"] = existing.apply(
                comparison_key_for_row,
                axis=1,
            )
            selected_keys = set(group["timestamp"].map(
                lambda value: comparison_key_for_row(
                    {
                        "timestamp": value,
                        "frequency": frequency,
                    }
                )
            ))
            existing = existing[
                ~existing["_comparison_key"].isin(selected_keys)
            ].drop(columns=["_comparison_key"])

        combined = pd.concat([existing, group], ignore_index=True)
        combined["timestamp"] = pd.to_datetime(
            combined["timestamp"],
            utc=True,
            errors="coerce",
        )
        combined = combined[combined["timestamp"].notna()].copy()
        combined = combined.sort_values(["symbol", "frequency", "timestamp"])
        combined["date"] = combined["timestamp"].dt.date.astype(str)
        combined["timestamp"] = combined["timestamp"].dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        combined = combined[CANONICAL_COLUMNS].reset_index(drop=True)
        combined.to_csv(output_path, index=False)

    reviewed_existing = load_reviewed_bars(source_dir)
    reviewed_new = pd.DataFrame(reviewed_rows, columns=REVIEWED_COLUMNS)
    reviewed = pd.concat(
        [reviewed_existing, reviewed_new],
        ignore_index=True,
    )
    reviewed = reviewed.drop_duplicates(
        subset=["symbol", "frequency", "comparison_key"],
        keep="last",
    )
    reviewed_path = reviewed_bars_file_for(source_dir)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed[REVIEWED_COLUMNS].to_csv(reviewed_path, index=False)

    return {
        "updated_bars": len(selected),
        "reviewed_bars": len(reviewed_new),
        "reviewed_file": str(reviewed_path),
    }


def to_json_records(frame):
    if frame is None or len(frame) == 0:
        return []

    safe = frame.replace([np.inf, -np.inf], np.nan).copy()
    safe = safe.astype(object).where(pd.notna(safe), None)

    return safe.to_dict(orient="records")


def json_safe_value(value):
    if isinstance(value, dict):
        return {
            key: json_safe_value(nested_value)
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [json_safe_value(item) for item in value]

    if isinstance(value, tuple):
        return [json_safe_value(item) for item in value]

    if isinstance(value, np.generic):
        value = value.item()

    if isinstance(value, float) and not np.isfinite(value):
        return None

    if pd.isna(value):
        return None

    return value


def dashboard_reconciliation_rows(differences, max_rows=DEFAULT_DASHBOARD_MAX_ROWS):
    if differences is None or len(differences) == 0:
        return []

    price_diffs = differences[differences["field"].isin(PRICE_COLUMNS)].copy()

    if len(price_diffs) == 0:
        return []

    rows = []
    row_max = differences[differences["field"] == "row_max"].copy()
    confirmed_lookup = build_confirmed_price_lookup(price_diffs, row_max)

    for key, group in price_diffs.groupby(
        ["symbol", "frequency", "comparison_key"],
        dropna=False,
    ):
        row = {
            "symbol": key[0],
            "frequency": key[1],
            "comparison_key": key[2],
            "threshold_pct": group["threshold_pct"].max(),
            "approved": bool(group["approved"].fillna(False).all()),
            "max_pct_diff": group["pct_diff"].max(),
        }

        for _, diff in group.iterrows():
            field = diff["field"]
            row[f"local_{field}"] = diff["local_value"]
            row[f"reference_{field}"] = diff["reference_value"]
            row[f"pct_diff_{field}"] = diff["pct_diff"]

        row.update(
            confirmed_neighbor_context(
                confirmed_lookup,
                symbol=key[0],
                frequency=key[1],
                comparison_key=key[2],
            )
        )

        rows.append(row)

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            bool(row.get("approved", False)),
            -float(row.get("max_pct_diff") or 0),
            str(row.get("symbol", "")),
            str(row.get("frequency", "")),
            str(row.get("comparison_key", "")),
        ),
    )

    if max_rows and max_rows > 0:
        return sorted_rows[:max_rows]

    return sorted_rows


def build_confirmed_price_lookup(price_diffs, row_max):
    confirmed = row_max[row_max["approved"].fillna(False)].copy()

    if len(confirmed) == 0:
        return {}

    close_diffs = price_diffs[price_diffs["field"] == "close"].copy()
    confirmed_closes = pd.merge(
        confirmed[["symbol", "frequency", "comparison_key"]],
        close_diffs[
            [
                "symbol",
                "frequency",
                "comparison_key",
                "local_value",
                "reference_value",
            ]
        ],
        on=["symbol", "frequency", "comparison_key"],
        how="left",
    )
    lookup = {}

    for key, group in confirmed_closes.groupby(["symbol", "frequency"]):
        working = group.copy()
        working["_sort_key"] = pd.to_datetime(
            working["comparison_key"],
            utc=True,
            errors="coerce",
        )
        working = working.sort_values(
            ["_sort_key", "comparison_key"],
            na_position="last",
        )
        lookup[key] = working[
            ["comparison_key", "local_value", "reference_value"]
        ].to_dict(orient="records")

    return lookup


def confirmed_neighbor_context(confirmed_lookup, symbol, frequency, comparison_key):
    confirmed_rows = confirmed_lookup.get((symbol, frequency), [])
    target_time = pd.to_datetime(
        comparison_key,
        utc=True,
        errors="coerce",
    )
    previous_row = None
    next_row = None

    for row in confirmed_rows:
        row_time = pd.to_datetime(
            row["comparison_key"],
            utc=True,
            errors="coerce",
        )

        if pd.isna(row_time) or pd.isna(target_time):
            if str(row["comparison_key"]) < str(comparison_key):
                previous_row = row
            elif str(row["comparison_key"]) > str(comparison_key) and next_row is None:
                next_row = row

            continue

        if row_time < target_time:
            previous_row = row
        elif row_time > target_time and next_row is None:
            next_row = row
            break

    return {
        "previous_confirmed_key": previous_row.get("comparison_key") if previous_row else None,
        "previous_confirmed_local_close": previous_row.get("local_value") if previous_row else None,
        "previous_confirmed_reference_close": previous_row.get("reference_value") if previous_row else None,
        "next_confirmed_key": next_row.get("comparison_key") if next_row else None,
        "next_confirmed_local_close": next_row.get("local_value") if next_row else None,
        "next_confirmed_reference_close": next_row.get("reference_value") if next_row else None,
    }


def write_validation_dashboard(
    output_dir,
    summary,
    differences,
    missing,
    duplicates,
    heatmap_path=None,
    server_enabled=False,
    dashboard_max_rows=DEFAULT_DASHBOARD_MAX_ROWS,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = output_dir / DASHBOARD_FILE
    heatmap_src = (
        Path(heatmap_path).name
        if heatmap_path
        else ""
    )
    payload = {
        "summary": to_json_records(summary),
        "differences": dashboard_reconciliation_rows(
            differences,
            max_rows=dashboard_max_rows,
        ),
        "totalReconciliationRows": len(dashboard_reconciliation_rows(
            differences,
            max_rows=0,
        )),
        "dashboardMaxRows": dashboard_max_rows,
        "missing": to_json_records(
            missing.head(dashboard_max_rows)
            if dashboard_max_rows and dashboard_max_rows > 0
            else missing
        ),
        "totalMissingRows": len(missing),
        "duplicates": to_json_records(duplicates),
        "heatmapSrc": heatmap_src,
        "serverEnabled": server_enabled,
    }
    html = dashboard_html(payload)
    dashboard_path.write_text(html, encoding="utf-8")

    return dashboard_path


def dashboard_html(payload):
    payload_json = json.dumps(json_safe_value(payload), allow_nan=False)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Data Validation</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #64707d;
      --line: #d9dde2;
      --good: #146c43;
      --bad: #b42318;
      --warn: #9a5b00;
      --accent: #245b73;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 24px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 17px; letter-spacing: 0; }}
    main {{ padding: 20px 28px 36px; }}
    section {{ margin-bottom: 24px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 14px;
    }}
    .metric-label {{ color: var(--muted); font-size: 12px; }}
    .metric-value {{ font-size: 22px; font-weight: 700; margin-top: 5px; }}
    .good {{ color: var(--good); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .warn {{ color: var(--warn); font-weight: 700; }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin: 0 0 12px;
    }}
    button, select, input {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
      font-size: 13px;
    }}
    button {{ cursor: pointer; background: var(--accent); color: #fff; border-color: var(--accent); }}
    button.secondary {{ background: #fff; color: var(--ink); border-color: var(--line); }}
    .table-wrap {{ overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 9px; text-align: right; font-size: 12px; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #f0f2f3; z-index: 1; color: #38424c; }}
    td:first-child, th:first-child, td:nth-child(2), th:nth-child(2), td:nth-child(3), th:nth-child(3) {{ text-align: left; }}
    tr.failed {{ background: #fff5f3; }}
    tr.approved {{ background: #f4fbf7; }}
    .heatmap {{ background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 12px; }}
    .heatmap img {{ max-width: 100%; height: auto; display: block; }}
    .choice {{ display: inline-flex; gap: 8px; align-items: center; }}
    .choice label {{ display: inline-flex; gap: 4px; align-items: center; }}
  </style>
</head>
<body>
  <header>
    <h1>Market Data Validation</h1>
    <div class="subtle">Compare locally stored Schwab/API bars to Yahoo Finance continuous futures reference data.</div>
  </header>
  <main>
    <section>
      <div class="cards" id="cards"></div>
    </section>
    <section id="heatmap-section">
      <h2>Difference Heatmap</h2>
      <div class="heatmap" id="heatmap"></div>
    </section>
    <section>
      <h2>Summary</h2>
      <div class="table-wrap"><table id="summary-table"></table></div>
    </section>
    <section>
      <h2>Reconciliation Choices</h2>
      <div class="toolbar">
        <select id="filter-status">
          <option value="all">All rows</option>
          <option value="failed" selected>Failed only</option>
          <option value="approved">Approved only</option>
        </select>
        <button class="secondary" id="choose-local">Mark visible: keep local/Schwab</button>
        <button class="secondary" id="choose-yahoo">Mark visible: use Yahoo</button>
        <button id="download-decisions">Download decisions CSV</button>
        <button id="download-selected-bars">Download selected bars CSV</button>
        <button id="apply-decisions">Apply selected changes</button>
      </div>
      <div class="subtle" id="apply-status" style="margin-bottom:10px;"></div>
      <div class="table-wrap"><table id="diff-table"></table></div>
    </section>
    <section>
      <h2>Missing Bars</h2>
      <div class="table-wrap"><table id="missing-table"></table></div>
    </section>
    <section>
      <h2>Duplicate Local Bars</h2>
      <div class="table-wrap"><table id="duplicate-table"></table></div>
    </section>
  </main>
  <script>
    const DATA = {payload_json};
    const decisions = new Map();

    function fmt(value, digits = 4) {{
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
      return Number(value).toFixed(digits);
    }}
    function pct(value) {{ return value === null || value === undefined ? '' : `${{fmt(value, 4)}}%`; }}
    function keyFor(row) {{ return `${{row.symbol}}|${{row.frequency}}|${{row.comparison_key}}`; }}
    function sourceChoice(row) {{ return decisions.get(keyFor(row)) || (row.approved ? 'local' : 'review'); }}
    function setChoice(row, choice) {{ decisions.set(keyFor(row), choice); }}
    function visibleDiffRows() {{
      const filter = document.getElementById('filter-status').value;
      return DATA.differences.filter(row => filter === 'all' || (filter === 'failed' && !row.approved) || (filter === 'approved' && row.approved));
    }}
    function renderCards() {{
      const summary = DATA.summary || [];
      const approved = summary.filter(row => row.approved).length;
      const failedBars = summary.reduce((sum, row) => sum + Number(row.failed_bars || 0), 0);
      const missing = summary.reduce((sum, row) => sum + Number(row.missing_local_bars || 0) + Number(row.missing_reference_bars || 0), 0);
      const maxDiff = Math.max(...summary.map(row => Number(row.max_pct_diff || 0)));
      const displayedRows = `${{DATA.differences.length}} / ${{DATA.totalReconciliationRows || DATA.differences.length}}`;
      const cards = [
        ['Groups Approved', `${{approved}} / ${{summary.length}}`, approved === summary.length ? 'good' : 'bad'],
        ['Failed Bars', failedBars, failedBars === 0 ? 'good' : 'bad'],
        ['Missing Bars', missing, missing === 0 ? 'good' : 'warn'],
        ['Largest Difference', pct(maxDiff), maxDiff <= 0.25 ? 'good' : 'bad'],
        ['Review Rows Loaded', displayedRows, DATA.differences.length ? 'warn' : 'bad'],
      ];
      document.getElementById('cards').innerHTML = cards.map(([label, value, klass]) => `<div class="card"><div class="metric-label">${{label}}</div><div class="metric-value ${{klass}}">${{value}}</div></div>`).join('');
    }}
    function renderHeatmap() {{
      const el = document.getElementById('heatmap');
      if (!DATA.heatmapSrc) {{ el.innerHTML = '<span class="subtle">No heatmap available.</span>'; return; }}
      el.innerHTML = `<img src="${{DATA.heatmapSrc}}" alt="Market data difference heatmap">`;
    }}
    function renderSimpleTable(id, rows, columns) {{
      const table = document.getElementById(id);
      if (!rows || rows.length === 0) {{ table.innerHTML = '<tbody><tr><td>No rows.</td></tr></tbody>'; return; }}
      table.innerHTML = `<thead><tr>${{columns.map(col => `<th>${{col}}</th>`).join('')}}</tr></thead><tbody>` +
        rows.map(row => `<tr>${{columns.map(col => `<td>${{row[col] ?? ''}}</td>`).join('')}}</tr>`).join('') + '</tbody>';
    }}
    function renderSummary() {{
      const cols = ['symbol','frequency','local_bars','reference_bars','matched_bars','missing_local_bars','missing_reference_bars','max_pct_diff','mean_pct_diff','approved_bars','failed_bars','threshold_pct','approved'];
      renderSimpleTable('summary-table', DATA.summary, cols);
    }}
    function renderDifferences() {{
      const rows = visibleDiffRows();
      const table = document.getElementById('diff-table');
      const headers = ['symbol','frequency','comparison_key','max_pct_diff','previous confirmed close','open','high','low','close','next confirmed close','decision'];
      table.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join('')}}</tr></thead><tbody>` + rows.map(row => {{
        const klass = row.approved ? 'approved' : 'failed';
        const choice = sourceChoice(row);
        const fields = ['open','high','low','close'].map(field => `<td>Local ${{fmt(row[`local_${{field}}`])}}<br>Yahoo ${{fmt(row[`reference_${{field}}`])}}<br><span class="${{Number(row[`pct_diff_${{field}}`] || 0) > Number(row.threshold_pct || 0) ? 'bad' : 'good'}}">${{pct(row[`pct_diff_${{field}}`])}}</span></td>`).join('');
        const previousConfirmed = `<td>${{row.previous_confirmed_key || ''}}<br>Local ${{fmt(row.previous_confirmed_local_close)}}<br>Yahoo ${{fmt(row.previous_confirmed_reference_close)}}</td>`;
        const nextConfirmed = `<td>${{row.next_confirmed_key || ''}}<br>Local ${{fmt(row.next_confirmed_local_close)}}<br>Yahoo ${{fmt(row.next_confirmed_reference_close)}}</td>`;
        return `<tr class="${{klass}}"><td>${{row.symbol}}</td><td>${{row.frequency}}</td><td>${{row.comparison_key}}</td><td>${{pct(row.max_pct_diff)}}</td>${{previousConfirmed}}${{fields}}${{nextConfirmed}}<td><span class="choice"><label><input type="radio" name="${{keyFor(row)}}" value="local" ${{choice === 'local' ? 'checked' : ''}}>Local</label><label><input type="radio" name="${{keyFor(row)}}" value="yahoo" ${{choice === 'yahoo' ? 'checked' : ''}}>Yahoo</label><label><input type="radio" name="${{keyFor(row)}}" value="review" ${{choice === 'review' ? 'checked' : ''}}>Review</label></span></td></tr>`;
      }}).join('') + '</tbody>';
      table.querySelectorAll('input[type="radio"]').forEach(input => input.addEventListener('change', event => {{ decisions.set(event.target.name, event.target.value); }}));
    }}
    function csvEscape(value) {{
      const text = value === null || value === undefined ? '' : String(value);
      return /[",\\n]/.test(text) ? `"${{text.replaceAll('"', '""')}}"` : text;
    }}
    function downloadDecisions() {{
      const header = ['symbol','frequency','comparison_key','decision','approved','max_pct_diff','local_open','reference_open','local_high','reference_high','local_low','reference_low','local_close','reference_close'];
      const lines = [header.join(',')];
      selectedDecisionRows().forEach(row => {{
        const values = [row.symbol,row.frequency,row.comparison_key,row.decision,row.approved,row.max_pct_diff,row.local_open,row.reference_open,row.local_high,row.reference_high,row.local_low,row.reference_low,row.local_close,row.reference_close];
        lines.push(values.map(csvEscape).join(','));
      }});
      const blob = new Blob([lines.join('\\n')], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'market_data_reconciliation_decisions.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    function downloadSelectedBars() {{
      const header = ['symbol','frequency','comparison_key','selected_source','open','high','low','close','max_pct_diff','approved'];
      const lines = [header.join(',')];
      selectedDecisionRows().forEach(row => {{
        const choice = row.decision;
        const prefix = choice === 'yahoo' ? 'reference' : choice === 'local' ? 'local' : '';
        const values = [
          row.symbol,
          row.frequency,
          row.comparison_key,
          choice,
          prefix ? row[`${{prefix}}_open`] : '',
          prefix ? row[`${{prefix}}_high`] : '',
          prefix ? row[`${{prefix}}_low`] : '',
          prefix ? row[`${{prefix}}_close`] : '',
          row.max_pct_diff,
          row.approved,
        ];
        lines.push(values.map(csvEscape).join(','));
      }});
      const blob = new Blob([lines.join('\\n')], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'market_data_selected_bars.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}
    function selectedDecisionRows() {{
      return DATA.differences.map(row => ({{ ...row, decision: sourceChoice(row) }}));
    }}
    async function applyDecisions() {{
      const status = document.getElementById('apply-status');
      if (!DATA.serverEnabled) {{
        status.innerHTML = 'Static dashboard mode: download a CSV or rerun with <code>--serve-dashboard</code> to apply changes directly.';
        return;
      }}
      const actionable = selectedDecisionRows().filter(row => row.decision === 'local' || row.decision === 'yahoo');
      status.textContent = `Applying ${{actionable.length}} reviewed bar choices...`;
      try {{
        const response = await fetch('/api/apply-decisions', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ decisions: actionable }}),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Apply failed');
        status.textContent = `Applied ${{payload.updated_bars}} bars and marked ${{payload.reviewed_bars}} bars reviewed. Reviewed file: ${{payload.reviewed_file}}`;
      }} catch (error) {{
        status.textContent = `Apply failed: ${{error.message}}`;
      }}
    }}
    document.getElementById('filter-status').addEventListener('change', renderDifferences);
    document.getElementById('choose-local').addEventListener('click', () => {{ visibleDiffRows().forEach(row => setChoice(row, 'local')); renderDifferences(); }});
    document.getElementById('choose-yahoo').addEventListener('click', () => {{ visibleDiffRows().forEach(row => setChoice(row, 'yahoo')); renderDifferences(); }});
    document.getElementById('download-decisions').addEventListener('click', downloadDecisions);
    document.getElementById('download-selected-bars').addEventListener('click', downloadSelectedBars);
    document.getElementById('apply-decisions').addEventListener('click', applyDecisions);
    document.getElementById('apply-status').innerHTML = DATA.serverEnabled ? 'Server mode: Apply selected changes will update local market data and write reviewed_bars.csv.' : 'Static dashboard mode: choices can be downloaded, but direct updates require <code>--serve-dashboard</code>.';
    if ((DATA.totalReconciliationRows || 0) > DATA.differences.length) {{
      document.getElementById('apply-status').innerHTML += ` Showing the top ${{DATA.differences.length}} review rows sorted by failed/largest difference. Increase with <code>--dashboard-max-rows</code>.`;
    }}
    renderCards();
    renderHeatmap();
    renderSummary();
    renderDifferences();
    renderSimpleTable('missing-table', DATA.missing, ['symbol','frequency','comparison_key','missing_from']);
    if ((DATA.totalMissingRows || 0) > DATA.missing.length) {{
      const table = document.getElementById('missing-table');
      table.insertAdjacentHTML('beforebegin', `<div class="subtle" style="padding:0 0 8px;">Showing ${{DATA.missing.length}} / ${{DATA.totalMissingRows}} missing-bar rows. Increase with <code>--dashboard-max-rows</code>.</div>`);
    }}
    renderSimpleTable('duplicate-table', DATA.duplicates, ['symbol','frequency','comparison_key','timestamp','open','high','low','close']);
  </script>
</body>
</html>"""


def validate_market_data(
    source_dir=DEFAULT_SOURCE_DIR,
    output_dir=DEFAULT_OUTPUT_DIR,
    symbols=None,
    frequencies=None,
    threshold_pct=DEFAULT_THRESHOLD_PCT,
    start=None,
    end=None,
    reference_fetcher=fetch_yahoo_bars,
    dashboard_max_rows=DEFAULT_DASHBOARD_MAX_ROWS,
):
    symbols = symbols or DEFAULT_SYMBOLS
    frequencies = frequencies or ["daily", "5min"]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    all_differences = []
    all_missing = []
    all_duplicates = []
    all_summary = []

    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)

        for raw_frequency in frequencies:
            frequency = normalize_frequency(raw_frequency)
            local_bars = load_local_bars(source_dir, symbol, frequency)
            reference_bars = reference_fetcher(
                symbol=symbol,
                frequency=frequency,
                start=start,
                end=end,
            )
            differences, missing, summary = compare_market_data(
                local_bars=local_bars,
                reference_bars=reference_bars,
                symbol=symbol,
                frequency=frequency,
                threshold_pct=threshold_pct,
            )
            duplicates = find_duplicate_bars(
                local_bars,
                symbol=symbol,
                frequency=frequency,
            )

            all_differences.append(differences)
            all_missing.append(missing)
            all_summary.append(summary)

            if len(duplicates) > 0:
                all_duplicates.append(duplicates)

    differences = pd.concat(all_differences, ignore_index=True)
    missing = pd.concat(all_missing, ignore_index=True)
    summary = pd.concat(all_summary, ignore_index=True)
    duplicates = (
        pd.concat(all_duplicates, ignore_index=True)
        if all_duplicates
        else pd.DataFrame(columns=DUPLICATE_COLUMNS)
    )
    heatmap_path = save_heatmap(summary, output_dir)
    dashboard_path = write_validation_dashboard(
        output_dir=output_dir,
        summary=summary,
        differences=differences,
        missing=missing,
        duplicates=duplicates,
        heatmap_path=heatmap_path,
        dashboard_max_rows=dashboard_max_rows,
    )

    summary.to_csv(output_dir / "validation_summary.csv", index=False)
    differences.to_csv(output_dir / "price_differences.csv", index=False)
    missing.to_csv(output_dir / "missing_bars.csv", index=False)
    duplicates.to_csv(output_dir / "duplicate_bars.csv", index=False)

    return {
        "summary": summary,
        "differences": differences,
        "missing": missing,
        "duplicates": duplicates,
        "heatmap_path": heatmap_path,
        "dashboard_path": dashboard_path,
    }


class ValidationDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        output_dir = self.server.output_dir
        path = self.path.split("?", 1)[0]

        if path in {"/", f"/{DASHBOARD_FILE}"}:
            file_path = output_dir / DASHBOARD_FILE
            content_type = "text/html; charset=utf-8"
        elif path == "/market_data_difference_heatmap.png":
            file_path = output_dir / "market_data_difference_heatmap.png"
            content_type = "image/png"
        else:
            self.send_error(404)
            return

        if not file_path.exists():
            self.send_error(404)
            return

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/apply-decisions":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = apply_reconciliation_decisions(
                source_dir=self.server.source_dir,
                decisions=payload.get("decisions", []),
            )
            self.send_json(200, result)
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def serve_validation_dashboard(
    output_dir,
    source_dir,
    host="127.0.0.1",
    port=8765,
    open_browser=True,
):
    output_dir = Path(output_dir)
    source_dir = Path(source_dir)
    server = HTTPServer((host, port), ValidationDashboardHandler)
    server.output_dir = output_dir
    server.source_dir = source_dir
    url = f"http://{host}:{server.server_port}/{DASHBOARD_FILE}"

    if open_browser:
        webbrowser.open(url)

    print(f"Serving validation dashboard at {url}")
    print("Press Ctrl+C to stop the dashboard server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped validation dashboard server.")
    finally:
        server.server_close()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Validate locally stored futures market data against Yahoo "
            "Finance continuous futures data."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help="Local normalized market data directory to validate.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where validation reports are written.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Futures roots to validate, e.g. /ES /GC /CL.",
    )
    parser.add_argument(
        "--frequencies",
        nargs="+",
        default=["daily", "5min"],
        help="Frequencies to validate: daily, 5min.",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=DEFAULT_THRESHOLD_PCT,
        help="Maximum allowed OHLC percentage difference for approval.",
    )
    parser.add_argument(
        "--start",
        help="Optional inclusive validation start date/time.",
    )
    parser.add_argument(
        "--end",
        help="Optional inclusive validation end date/time.",
    )
    parser.add_argument(
        "--open-dashboard",
        action="store_true",
        help="Open the validation dashboard in your default browser.",
    )
    parser.add_argument(
        "--serve-dashboard",
        action="store_true",
        help=(
            "Serve the dashboard locally so Apply selected changes can "
            "update market data and mark bars reviewed."
        ),
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Port for --serve-dashboard.",
    )
    parser.add_argument(
        "--dashboard-max-rows",
        type=int,
        default=DEFAULT_DASHBOARD_MAX_ROWS,
        help=(
            "Maximum reconciliation rows embedded in the dashboard. "
            "Rows are sorted by failed/largest difference first. Use 0 "
            "to embed all rows."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()
    result = validate_market_data(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        symbols=args.symbols,
        frequencies=args.frequencies,
        threshold_pct=args.threshold_pct,
        start=args.start,
        end=args.end,
        dashboard_max_rows=args.dashboard_max_rows,
    )
    summary = result["summary"]
    approved_count = int(summary["approved"].fillna(False).sum())
    total_count = len(summary)
    print(
        f"Approved {approved_count}/{total_count} symbol-frequency "
        "validation groups."
    )
    print(f"Wrote: {Path(args.output_dir) / 'validation_summary.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'price_differences.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'missing_bars.csv'}")
    print(f"Wrote: {Path(args.output_dir) / 'duplicate_bars.csv'}")

    if result["heatmap_path"]:
        print(f"Wrote: {result['heatmap_path']}")

    if result["dashboard_path"]:
        print(f"Wrote: {result['dashboard_path']}")

        if args.serve_dashboard:
            write_validation_dashboard(
                output_dir=args.output_dir,
                summary=result["summary"],
                differences=result["differences"],
                missing=result["missing"],
                duplicates=result["duplicates"],
                heatmap_path=result["heatmap_path"],
                server_enabled=True,
                dashboard_max_rows=args.dashboard_max_rows,
            )
            serve_validation_dashboard(
                output_dir=args.output_dir,
                source_dir=args.source_dir,
                host=args.dashboard_host,
                port=args.dashboard_port,
                open_browser=True,
            )
        elif args.open_dashboard:
            webbrowser.open(result["dashboard_path"].resolve().as_uri())


if __name__ == "__main__":
    main()
