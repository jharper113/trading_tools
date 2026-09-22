"""Compare native and low-touch AmiBroker optimization schedules."""

from __future__ import annotations

import math

import pandas as pd

from amibroker_experiment_results import parse_number


def _symbol(value: object) -> str:
    return str(value).strip().upper().lstrip("/")


def _metadata_flag(metadata: dict, name: str, symbol: str) -> bool:
    value = metadata.get(name, False)
    if isinstance(value, dict):
        value = value.get(symbol, value.get(f"/{symbol}", False))
    return bool(value)


def _median(frame: pd.DataFrame, column: str) -> float:
    values = [parse_number(value) for value in frame[column]]
    finite = [value for value in values if math.isfinite(value)]
    return float(pd.Series(finite, dtype=float).median()) if finite else math.nan


def _not_comparable(base: dict, reason: str) -> dict:
    return {
        **base,
        "native_median_car_mdd": math.nan,
        "low_touch_median_car_mdd": math.nan,
        "relative_car_mdd_improvement": math.nan,
        "profit_factor_improvement": math.nan,
        "drawdown_reduction": math.nan,
        "recommendation": "NOT COMPARABLE",
        "reason": reason,
    }


def compare_optimization_pair(
    native_rows: list[dict], low_touch_rows: list[dict], metadata: dict
) -> list[dict]:
    """Compare schedules only on identical symbols and parameter coordinates."""
    native = pd.DataFrame(native_rows)
    low_touch = pd.DataFrame(low_touch_rows)
    if "Symbol" not in native or "Symbol" not in low_touch:
        raise ValueError("Optimization rows require a Symbol column")
    native["Symbol"] = native["Symbol"].map(_symbol)
    low_touch["Symbol"] = low_touch["Symbol"].map(_symbol)
    symbols = sorted(set(native["Symbol"]) | set(low_touch["Symbol"]))
    dates_match = (
        metadata.get("native_research_start") == metadata.get("low_touch_research_start")
        and metadata.get("native_research_end") == metadata.get("low_touch_research_end")
        and bool(metadata.get("native_research_start"))
        and bool(metadata.get("native_research_end"))
    )
    output = []
    for symbol in symbols:
        left = native[native["Symbol"] == symbol].copy()
        right = low_touch[low_touch["Symbol"] == symbol].copy()
        native_parameters = sorted(column for column in left if str(column).startswith("Opt "))
        low_parameters = sorted(column for column in right if str(column).startswith("Opt "))
        shared_parameters = sorted(set(native_parameters) & set(low_parameters))
        left_grid = {
            tuple(row) for row in left[shared_parameters].itertuples(index=False, name=None)
        } if shared_parameters else ({()} if len(left) == 1 else set())
        right_grid = {
            tuple(row) for row in right[shared_parameters].itertuples(index=False, name=None)
        } if shared_parameters else ({()} if len(right) == 1 else set())
        shared_grid = left_grid & right_grid
        denominator = max(len(left_grid), len(right_grid), 1)
        coverage = len(shared_grid) / denominator
        base = {
            "strategy": metadata.get("strategy", ""),
            "symbol": symbol,
            "native_job": metadata.get("native_job", ""),
            "low_touch_job": metadata.get("low_touch_job", ""),
            "shared_grid_rows": len(shared_grid),
            "grid_coverage": coverage,
        }
        if not dates_match:
            output.append(_not_comparable(base, "Declared research dates do not match"))
            continue
        if left.empty or right.empty:
            output.append(_not_comparable(base, "Symbol is missing from one schedule"))
            continue
        if native_parameters != low_parameters or not left_grid or left_grid != right_grid:
            output.append(_not_comparable(base, "Parameter grids do not have full shared coverage"))
            continue
        required = {"CAR/MDD", "Profit Factor", "Max. Sys % Drawdown"}
        if not required.issubset(left) or not required.issubset(right):
            output.append(_not_comparable(base, "Required optimization metrics are missing"))
            continue
        native_car = _median(left, "CAR/MDD")
        low_car = _median(right, "CAR/MDD")
        native_pf = _median(left, "Profit Factor")
        low_pf = _median(right, "Profit Factor")
        native_dd = abs(_median(left, "Max. Sys % Drawdown"))
        low_dd = abs(_median(right, "Max. Sys % Drawdown"))
        if not math.isfinite(low_car) or low_car <= 0 or not math.isfinite(low_dd) or low_dd <= 0:
            result = _not_comparable(base, "Low-touch baseline is nonpositive or nonfinite")
            result.update(native_median_car_mdd=native_car, low_touch_median_car_mdd=low_car)
            output.append(result)
            continue
        relative_car_mdd = (native_car - low_car) / low_car
        profit_factor = native_pf - low_pf
        drawdown = (low_dd - native_dd) / low_dd
        material = (
            _metadata_flag(metadata, "native_individual_pass", symbol)
            and _metadata_flag(metadata, "native_sector_pass", symbol)
            and relative_car_mdd >= .25
            and (profit_factor >= .10 or drawdown >= .15)
        )
        recommendation = "FREQUENT_ENTRY_WFA_CANDIDATE" if material else "LOW_TOUCH"
        reason = (
            "Native schedule clears both material-improvement layers"
            if material
            else "Low-touch schedule remains preferred"
        )
        output.append(
            {
                **base,
                "native_median_car_mdd": native_car,
                "low_touch_median_car_mdd": low_car,
                "relative_car_mdd_improvement": relative_car_mdd,
                "profit_factor_improvement": profit_factor,
                "drawdown_reduction": drawdown,
                "recommendation": recommendation,
                "reason": reason,
            }
        )
    return output


def _normalize_daily(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"Symbol", "Date", "Return"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Daily series is missing: {', '.join(sorted(missing))}")
    output = frame.loc[:, ["Symbol", "Date", "Return"]].copy()
    output["Symbol"] = output["Symbol"].map(_symbol)
    output["Date"] = pd.to_datetime(output["Date"], errors="raise").dt.normalize()
    output["Return"] = pd.to_numeric(output["Return"], errors="raise")
    duplicate = output.duplicated(["Symbol", "Date"], keep=False)
    if duplicate.any():
        row = output.loc[duplicate, ["Symbol", "Date"]].iloc[0]
        raise ValueError(f"Daily series has duplicate Symbol/Date: {row['Symbol']} {row['Date'].date()}")
    return output


def inner_join_daily_series(native: pd.DataFrame, low_touch: pd.DataFrame) -> pd.DataFrame:
    """Align daily return evidence on the exact common symbol/date observations."""
    keys = ["Symbol", "Date"]
    left = _normalize_daily(native).rename(columns={"Return": "NativeReturn"})
    right = _normalize_daily(low_touch).rename(columns={"Return": "LowTouchReturn"})
    joined = left.merge(right, on=keys, how="inner", validate="one_to_one").sort_values(keys)
    joined = joined.reset_index(drop=True)
    joined.attrs = {
        "first_common_date": joined["Date"].min().date().isoformat() if len(joined) else None,
        "last_common_date": joined["Date"].max().date().isoformat() if len(joined) else None,
        "common_observations": len(joined),
    }
    return joined
