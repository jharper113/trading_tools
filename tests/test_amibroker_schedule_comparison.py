import pandas as pd
import pytest

from amibroker_schedule_comparison import (
    compare_optimization_pair,
    inner_join_daily_series,
)


def _rows(*, car_mdd, pf, drawdown, grid=(1, 2), symbol="ES"):
    return [
        {
            "Symbol": symbol,
            "Opt Length": point,
            "CAR/MDD": car_mdd,
            "Profit Factor": pf,
            "Max. Sys % Drawdown": -drawdown,
        }
        for point in grid
    ]


def _metadata(**updates):
    value = {
        "strategy": "0063_intraday_opening_range_breakout",
        "native_job": "native-job",
        "low_touch_job": "cutoff-job",
        "native_research_start": "2009-01-01",
        "native_research_end": "2019-01-01",
        "low_touch_research_start": "2009-01-01",
        "low_touch_research_end": "2019-01-01",
        "native_individual_pass": True,
        "native_sector_pass": True,
        "low_touch_individual_pass": True,
        "low_touch_sector_pass": True,
        "same_source_policy": True,
    }
    value.update(updates)
    return value


def test_native_candidate_requires_car_mdd_and_secondary_improvement():
    result = compare_optimization_pair(
        _rows(car_mdd=.75, pf=1.35, drawdown=18),
        _rows(car_mdd=.60, pf=1.20, drawdown=20),
        _metadata(),
    )[0]
    assert result["relative_car_mdd_improvement"] == pytest.approx(.25)
    assert result["profit_factor_improvement"] == pytest.approx(.15)
    assert result["drawdown_reduction"] == pytest.approx(.10)
    assert result["recommendation"] == "FREQUENT_ENTRY_WFA_CANDIDATE"


def test_mismatched_grid_is_not_comparable():
    result = compare_optimization_pair(
        _rows(car_mdd=.75, pf=1.35, drawdown=18, grid=(1, 2)),
        _rows(car_mdd=.60, pf=1.20, drawdown=20, grid=(1,)),
        _metadata(),
    )[0]
    assert result["recommendation"] == "NOT COMPARABLE"
    assert result["grid_coverage"] < 1


@pytest.mark.parametrize(
    "native_car,native_pf,native_dd,expected",
    [
        (.74994, 1.30, 17.0, "LOW_TOUCH"),
        (.75, 1.2999, 17.0, "FREQUENT_ENTRY_WFA_CANDIDATE"),
        (.75, 1.30, 17.002, "FREQUENT_ENTRY_WFA_CANDIDATE"),
    ],
)
def test_material_threshold_boundaries(native_car, native_pf, native_dd, expected):
    # Baseline values make the three improvements approximately .25, .10, .15.
    result = compare_optimization_pair(
        _rows(car_mdd=native_car, pf=native_pf, drawdown=native_dd),
        _rows(car_mdd=.60, pf=1.20, drawdown=20),
        _metadata(),
    )[0]
    assert result["recommendation"] == expected


def test_failed_gate_and_nonpositive_baseline_are_handled():
    failed = compare_optimization_pair(
        _rows(car_mdd=.90, pf=1.50, drawdown=10),
        _rows(car_mdd=.60, pf=1.20, drawdown=20),
        _metadata(native_sector_pass=False),
    )[0]
    invalid = compare_optimization_pair(
        _rows(car_mdd=.90, pf=1.50, drawdown=10),
        _rows(car_mdd=0, pf=1.20, drawdown=20),
        _metadata(),
    )[0]
    assert failed["recommendation"] == "LOW_TOUCH"
    assert invalid["recommendation"] == "NOT COMPARABLE"


def test_low_touch_must_pass_its_own_gates():
    result = compare_optimization_pair(
        _rows(car_mdd=.50, pf=1.20, drawdown=20),
        _rows(car_mdd=.60, pf=.80, drawdown=20),
        _metadata(low_touch_individual_pass=False, low_touch_sector_pass=False),
    )[0]
    assert result["recommendation"] == "NO_WFA_CANDIDATE"


def test_nonfinite_pair_metrics_are_not_comparable():
    result = compare_optimization_pair(
        _rows(car_mdd=.75, pf=float("inf"), drawdown=18),
        _rows(car_mdd=.60, pf=1.20, drawdown=20),
        _metadata(low_touch_individual_pass=True, low_touch_sector_pass=True),
    )[0]
    assert result["recommendation"] == "NOT COMPARABLE"


def test_declared_research_dates_must_match():
    result = compare_optimization_pair(
        _rows(car_mdd=.75, pf=1.35, drawdown=18),
        _rows(car_mdd=.60, pf=1.20, drawdown=20),
        _metadata(low_touch_research_end="2018-12-31"),
    )[0]
    assert result["recommendation"] == "NOT COMPARABLE"
    assert "research dates" in result["reason"]


def test_daily_comparison_keeps_only_common_symbol_dates():
    native = pd.DataFrame(
        {"Symbol": ["/es", "ES"], "Date": ["2020-01-02", "2020-01-03"], "Return": [1, 2]}
    )
    low_touch = pd.DataFrame(
        {"Symbol": ["ES", "NQ"], "Date": ["2020-01-03", "2020-01-03"], "Return": [3, 4]}
    )
    joined = inner_join_daily_series(native, low_touch)
    assert list(joined[["Symbol", "Date"]].itertuples(index=False, name=None)) == [
        ("ES", pd.Timestamp("2020-01-03"))
    ]
    assert joined.attrs == {
        "first_common_date": "2020-01-03",
        "last_common_date": "2020-01-03",
        "common_observations": 1,
    }


def test_daily_comparison_rejects_duplicate_keys():
    frame = pd.DataFrame(
        {"Symbol": ["ES", "ES"], "Date": ["2020-01-03", "2020-01-03"], "Return": [1, 2]}
    )
    with pytest.raises(ValueError, match="duplicate Symbol/Date"):
        inner_join_daily_series(frame, frame.iloc[:1])
