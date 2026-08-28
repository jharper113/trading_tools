import pandas as pd
import pytest

from analyze_single_symbol_optimization import (
    ThresholdConfig,
    build_heatmap_config,
    build_heatmap_configs,
    build_dashboard_payload,
    build_summary_metrics,
    format_currency,
    load_optimization_csv,
    parameter_columns,
    parameter_profile,
    percentage_paramsets_over_trade_threshold,
    robustness_cards_html,
    robustness_metrics,
    select_heatmap_parameters,
    svg_heatmap,
    write_dashboard,
)


def write_sample_optimization(path):
    path.write_text(
        "\n".join([
            (
                "No.,Net Profit,Profit Factor,# Trades,Avg Profit/Loss,"
                "L. Avg % Loss,L. Avg. Bars Held,trendIndex,kbMult"
            ),
            "1,100,1.20,120,9.00,-4.0,7,1,1.0",
            "2,-10,1.50,80,12.00,-6.0,8,2,1.0",
            "3,50,0.80,130,3.00,-5.0,9,1,1.5",
            "4,75,2.00,90,18.00,-7.0,10,2,1.5",
        ]),
        encoding="utf-8",
    )


def test_parameter_columns_start_after_static_amibroker_columns(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    write_sample_optimization(csv_path)

    frame = load_optimization_csv(csv_path)

    assert parameter_columns(frame) == ["trendIndex", "kbMult"]
    assert "L. Avg. Bars Held" not in parameter_columns(frame)


def test_summary_metrics_apply_configurable_thresholds(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    write_sample_optimization(csv_path)
    frame = load_optimization_csv(csv_path)
    thresholds = ThresholdConfig(
        profit_factor_threshold=1.10,
        trades_threshold=100,
        commission_per_trade=2.0,
        slippage_per_trade=1.0,
        avg_trade_cost_multiple=3.0,
        profitable_paramsets_threshold=70.0,
    )

    metrics = {
        metric["key"]: metric
        for metric in build_summary_metrics(frame, thresholds)
    }

    assert metrics["median_profit_factor"]["value"] == 1.35
    assert metrics["median_profit_factor"]["status"] == "good"
    assert metrics["median_trades"]["value"] == 105
    assert metrics["median_trades"]["status"] == "good"
    assert metrics["median_avg_trade"]["value"] == 10.5
    assert metrics["median_avg_trade"]["threshold"] == 9.0
    assert metrics["median_avg_trade"]["status"] == "good"
    assert metrics["paramsets_over_trade_threshold_pct"]["value"] == 50.0
    assert metrics["paramsets_over_trade_threshold_pct"]["status"] == "neutral"
    assert metrics["profitable_paramsets_pct"]["value"] == 75.0
    assert metrics["profitable_paramsets_pct"]["status"] == "good"


def test_trade_count_percentage_uses_strictly_greater_than_threshold():
    frame = pd.DataFrame({"# Trades": [99, 100, 101, None]})

    percentage = percentage_paramsets_over_trade_threshold(frame, 100)

    assert percentage == pytest.approx(100 / 3)


def test_summary_metrics_turn_red_below_threshold(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    write_sample_optimization(csv_path)
    frame = load_optimization_csv(csv_path)
    thresholds = ThresholdConfig(
        profit_factor_threshold=1.40,
        trades_threshold=110,
        commission_per_trade=3.0,
        slippage_per_trade=1.0,
        avg_trade_cost_multiple=3.0,
        profitable_paramsets_threshold=80.0,
    )

    metrics = {
        metric["key"]: metric
        for metric in build_summary_metrics(frame, thresholds)
    }

    assert metrics["median_profit_factor"]["status"] == "bad"
    assert metrics["median_trades"]["status"] == "bad"
    assert metrics["median_avg_trade"]["status"] == "bad"
    assert metrics["profitable_paramsets_pct"]["status"] == "bad"


def test_parameter_profile_groups_profit_factor_by_parameter_value(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    write_sample_optimization(csv_path)
    frame = load_optimization_csv(csv_path)

    profile = parameter_profile(frame, "trendIndex")

    assert profile["parameter_value"].tolist() == [1, 2]
    assert profile["paramsets"].tolist() == [2, 2]
    assert profile["median_profit_factor"].tolist() == [1.0, 1.75]
    assert profile["profitable_pct"].tolist() == [100.0, 50.0]


def test_heatmap_uses_two_parameters_with_highest_numeric_variance():
    frame = pd.DataFrame({
        "lowVariance": [0, 1, 0, 1],
        "highVariance": [2, 10, 20, 30],
        "mediumVariance": [1, 2, 3, 4],
        "constant": [5, 5, 5, 5],
    })

    selected = select_heatmap_parameters(
        frame,
        ["lowVariance", "highVariance", "mediumVariance", "constant"],
    )

    assert selected == ["highVariance", "mediumVariance"]


def test_heatmap_shows_median_pnl_and_currency_legend():
    frame = pd.DataFrame({
        "paramX": [1, 1, 2],
        "paramY": [10, 10, 20],
        "Net Profit": [-300, -100, 200],
    })
    config = build_heatmap_config(frame, ["paramX", "paramY"])

    svg = svg_heatmap(frame, config)

    assert format_currency(-200) == "-$200"
    assert "Median PNL -$200" in svg
    assert "Loss -$200" in svg
    assert ">$0</text>" in svg
    assert "Profit $200" in svg


def test_heatmaps_include_every_unique_parameter_pair():
    frame = pd.DataFrame({
        "a": [0, 0, 1, 1],
        "b": [0, 1, 0, 1],
        "c": [10, 20, 30, 40],
        "constant": [5, 5, 5, 5],
        "Net Profit": [-100, 50, 200, 400],
    })

    configs = build_heatmap_configs(frame, ["a", "b", "c", "constant"])
    pairs = [(config["x_param"], config["y_param"]) for config in configs]

    assert pairs == [("a", "b"), ("a", "c"), ("b", "c")]
    assert len({config["max_abs"] for config in configs}) == 1


def test_robustness_snapshot_highlights_preferred_ranges_and_explains_metrics():
    frame = pd.DataFrame({
        "CAR/MDD": [1.2, 1.4, 1.3, 1.5],
        "Max. Sys % Drawdown": [-25, -30, -24, -28],
        "Recovery Factor": [1.1, 1.3, 1.2, 1.4],
        "Ulcer Index": [12, 14, 11, 13],
        "Profit Factor": [1.0, 1.2, 1.4, 1.6],
        "Net Profit": [-100, -50, 100, 200],
    })

    metrics = {metric["key"]: metric for metric in robustness_metrics(frame)}

    assert metrics["median_car_mdd"]["status"] == "good"
    assert metrics["median_max_drawdown"]["status"] == "bad"
    assert metrics["median_recovery_factor"]["status"] == "good"
    assert metrics["median_ulcer_index"]["status"] == "bad"
    assert metrics["profit_factor_iqr"]["status"] == "good"
    assert metrics["worst_decile_net_profit"]["status"] == "bad"
    assert "Annualized return" in metrics["median_car_mdd"]["description"]

    html = robustness_cards_html(metrics.values())
    assert 'class="mini-card good"' in html
    assert 'class="mini-card bad"' in html
    assert "Preferred absolute drawdown &lt;= 20%" in html
    assert "Outside preferred range" in html


def test_write_dashboard_creates_parameter_charts(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    output_path = tmp_path / "dashboard.html"
    write_sample_optimization(csv_path)

    dashboard_path, payload = write_dashboard(
        csv_path,
        output_path,
        ThresholdConfig(),
    )
    html = dashboard_path.read_text(encoding="utf-8")

    assert dashboard_path == output_path
    assert payload["row_count"] == 4
    assert payload["parameter_columns"] == ["trendIndex", "kbMult"]
    assert payload["heatmap_config"]["x_param"] == "trendIndex"
    assert payload["heatmap_config"]["y_param"] == "kbMult"
    assert len(payload["heatmap_configs"]) == 1
    assert "Median Profit Factor" in html
    assert "PNL Heatmaps" in html
    assert "1 parameter pair" in html
    assert "Median PNL" in html
    assert "Loss -$100" in html
    assert "Profit $100" in html
    assert "Profit Factor by Parameter" in html
    assert "trendIndex" in html
    assert "kbMult" in html
    assert "PF threshold 1.10" in html


def test_dashboard_payload_keeps_variable_parameter_count(tmp_path):
    csv_path = tmp_path / "optimization.csv"
    write_sample_optimization(csv_path)
    frame = pd.read_csv(csv_path)
    frame["extraParam"] = [5, 5, 10, 10]
    frame.to_csv(csv_path, index=False)

    loaded = load_optimization_csv(csv_path)
    payload = build_dashboard_payload(
        loaded,
        csv_path,
        ThresholdConfig(),
    )

    assert payload["parameter_count"] == 3
    assert payload["parameter_columns"] == [
        "trendIndex",
        "kbMult",
        "extraParam",
    ]
    assert [
        (config["x_param"], config["y_param"])
        for config in payload["heatmap_configs"]
    ] == [
        ("trendIndex", "kbMult"),
        ("trendIndex", "extraParam"),
        ("kbMult", "extraParam"),
    ]
