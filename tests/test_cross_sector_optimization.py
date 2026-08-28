import pandas as pd

from analyze_cross_sector_optimization import (
    DISTRIBUTION_METRICS,
    SECTOR_COLUMN,
    SYMBOL_COLUMN,
    ThresholdConfig,
    build_dashboard_payload,
    build_summary_metrics,
    format_currency,
    load_optimization_directory,
    parse_symbol_list,
    sector_for_symbol,
    select_heatmap_parameters,
    symbol_from_path,
    svg_boxplot,
    svg_heatmap,
    write_dashboard,
)


def write_sample_optimization(path, rows):
    header = (
        "No.,Net Profit,Net % Profit,Exposure %,CAR,RAR,"
        "Max. Trade Drawdown,Max. Trade % Drawdown,"
        "Max. Sys Drawdown,Max. Sys % Drawdown,Recovery Factor,"
        "CAR/MDD,RAR/MDD,Profit Factor,Payoff Ratio,Standard Error,"
        "RRR,Ulcer Index,Ulcer Perf. Index,Sharpe Ratio,"
        "# Trades,Avg Profit/Loss,Avg % Profit/Loss,Avg Bars Held,"
        "# of winners,% of Winners,W. Tot. Profit,W. Avg. Profit,"
        "W. Avg % Profit,W. Avg. Bars Held,# of losers,% of Losers,"
        "L. Tot. Loss,L. Avg. Loss,L. Avg % Loss,L. Avg. Bars Held,"
        "trendIndex,lEntryThresh,stopParam: ATRmult|nLagLoHi"
    )
    lines = [header]

    for row in rows:
        values = [
            row.get("No.", 1),
            row["Net Profit"],
            row.get("Net % Profit", 0),
            row.get("Exposure %", 0),
            row.get("CAR", 0),
            row.get("RAR", 0),
            row.get("Max. Trade Drawdown", -10),
            row.get("Max. Trade % Drawdown", -1),
            row.get("Max. Sys Drawdown", -20),
            row.get("Max. Sys % Drawdown", -5),
            row.get("Recovery Factor", 1),
            row.get("CAR/MDD", 1),
            row.get("RAR/MDD", 1),
            row["Profit Factor"],
            row.get("Payoff Ratio", 1),
            row.get("Standard Error", 0),
            row.get("RRR", 1),
            row.get("Ulcer Index", 1),
            row.get("Ulcer Perf. Index", 1),
            row.get("Sharpe Ratio", 1),
            row["# Trades"],
            row.get("Avg Profit/Loss", 1),
            row.get("Avg % Profit/Loss", 1),
            row.get("Avg Bars Held", 1),
            row.get("# of winners", 1),
            row.get("% of Winners", 50),
            row.get("W. Tot. Profit", 10),
            row.get("W. Avg. Profit", 10),
            row.get("W. Avg % Profit", 10),
            row.get("W. Avg. Bars Held", 1),
            row.get("# of losers", 1),
            row.get("% of Losers", 50),
            row.get("L. Tot. Loss", -10),
            row.get("L. Avg. Loss", -10),
            row.get("L. Avg % Loss", -10),
            row.get("L. Avg. Bars Held", 1),
            row["trendIndex"],
            row["lEntryThresh"],
            row["stopParam: ATRmult|nLagLoHi"],
        ]
        lines.append(",".join(str(value) for value in values))

    path.write_text("\n".join(lines), encoding="utf-8")


def write_sample_directory(tmp_path):
    write_sample_optimization(
        tmp_path / "Daily_ES.csv",
        [
            {
                "Net Profit": 100,
                "Profit Factor": 1.2,
                "# Trades": 120,
                "CAR": 5,
                "Max. Sys % Drawdown": -6,
                "trendIndex": 0,
                "lEntryThresh": 2,
                "stopParam: ATRmult|nLagLoHi": 1,
            },
            {
                "Net Profit": 50,
                "Profit Factor": 1.4,
                "# Trades": 80,
                "CAR": 4,
                "Max. Sys % Drawdown": -4,
                "trendIndex": 1,
                "lEntryThresh": 10,
                "stopParam: ATRmult|nLagLoHi": 2,
            },
        ],
    )
    write_sample_optimization(
        tmp_path / "Daily_CL.csv",
        [
            {
                "Net Profit": -100,
                "Profit Factor": 0.8,
                "# Trades": 140,
                "CAR": -2,
                "Max. Sys % Drawdown": -12,
                "trendIndex": 0,
                "lEntryThresh": 2,
                "stopParam: ATRmult|nLagLoHi": 1,
            },
            {
                "Net Profit": -50,
                "Profit Factor": 0.9,
                "# Trades": 90,
                "CAR": -1,
                "Max. Sys % Drawdown": -8,
                "trendIndex": 1,
                "lEntryThresh": 30,
                "stopParam: ATRmult|nLagLoHi": 3,
            },
        ],
    )


def test_load_optimization_directory_adds_symbols_and_sectors(tmp_path):
    write_sample_directory(tmp_path)

    dataset = load_optimization_directory(tmp_path)

    assert len(dataset.frame) == 4
    assert dataset.parameter_columns == [
        "trendIndex",
        "lEntryThresh",
        "stopParam: ATRmult|nLagLoHi",
    ]
    assert dataset.frame[SYMBOL_COLUMN].tolist() == ["/CL", "/CL", "/ES", "/ES"]
    assert dataset.frame[SECTOR_COLUMN].tolist() == [
        "Energy",
        "Energy",
        "Equity Indexes",
        "Equity Indexes",
    ]


def test_sector_for_symbol_classifies_extended_instruments():
    assert sector_for_symbol("/MSL") == "Crypto"
    assert sector_for_symbol("/MXP") == "Currencies"
    assert sector_for_symbol("/SOL") == "Crypto"
    assert sector_for_symbol("/SPY") == "Equity Indexes"
    assert sector_for_symbol("SPY") == "Equity Indexes"
    assert sector_for_symbol("/XRP") == "Crypto"
    assert symbol_from_path("optimization_SPY.csv") == "SPY"
    assert parse_symbol_list("/SPY, ES") == ["SPY", "/ES"]


def test_summary_metrics_apply_cross_sector_thresholds(tmp_path):
    write_sample_directory(tmp_path)
    dataset = load_optimization_directory(tmp_path)
    thresholds = ThresholdConfig(
        profitable_groups_threshold=65.0,
        profit_factor_threshold=1.10,
        trade_count_threshold=100,
    )

    metrics = {
        metric["key"]: metric
        for metric in build_summary_metrics(
            dataset.frame,
            thresholds,
            SECTOR_COLUMN,
            "Sectors",
        )
    }

    assert metrics["profitable_groups_pct"]["value"] == 50.0
    assert metrics["profitable_groups_pct"]["status"] == "bad"
    assert metrics["median_profit_factor"]["value"] == 1.05
    assert metrics["median_profit_factor"]["status"] == "bad"
    assert metrics["paramsets_over_trade_threshold_pct"]["value"] == 50.0
    assert metrics["paramsets_over_trade_threshold_pct"]["status"] == "neutral"


def test_select_heatmap_parameters_uses_highest_numeric_variance():
    frame = pd.DataFrame(
        {
            "lowVariance": [0, 1, 0, 1],
            "highVariance": [2, 10, 20, 30],
            "mediumVariance": [1, 2, 3, 4],
        }
    )

    assert select_heatmap_parameters(
        frame,
        ["lowVariance", "highVariance", "mediumVariance"],
    ) == ["highVariance", "mediumVariance"]


def test_boxplots_use_metric_specific_reference_lines():
    frame = pd.DataFrame(
        {
            SYMBOL_COLUMN: ["/ES", "/ES"],
            "Net Profit": [100, 200],
            "Profit Factor": [1.2, 1.3],
            "CAR": [15, 20],
            "CAR/MDD": [0.5, 0.8],
            "Max. Sys % Drawdown": [-12, -8],
            "# Trades": [120, 180],
        }
    )
    expected_values = {
        "profitability": "0.00",
        "profit_factor": "1.10",
        "cagr": "10.00",
        "max_drawdown": "-20.00",
        "trade_count": "100.00",
        "car_mdd": "0.00",
    }

    for metric in DISTRIBUTION_METRICS:
        svg = svg_boxplot(frame, metric, ["/ES"])
        expected = expected_values.get(metric.key)

        assert f'data-reference-value="{expected}"' in svg
        assert 'class="reference-line"' in svg

    profitability_svg = svg_boxplot(frame, DISTRIBUTION_METRICS[0], ["/ES"])
    assert "median $150, min $100, max $200" in profitability_svg

    metrics = {metric.key: metric for metric in DISTRIBUTION_METRICS}
    assert "larger sample" in metrics["trade_count"].description
    assert "out-of-sample" in metrics["car_mdd"].description


def test_heatmap_legend_and_tooltips_show_currency_pnl():
    frame = pd.DataFrame(
        {
            SYMBOL_COLUMN: ["/ES", "/ES"],
            "paramX": [1, 2],
            "paramY": [10, 10],
            "Net Profit": [-200, 100],
        }
    )
    config = {
        "x_param": "paramX",
        "y_param": "paramY",
        "x_values": [1, 2],
        "y_values": [10],
        "max_abs": 200,
    }

    svg = svg_heatmap(frame, "/ES", config)

    assert format_currency(-200) == "-$200"
    assert "Median PNL -$200" in svg
    assert "Loss -$200" in svg
    assert ">$0</text>" in svg
    assert "Profit $200" in svg


def test_dashboard_payload_uses_basket_for_cross_validation(tmp_path):
    write_sample_directory(tmp_path)
    dataset = load_optimization_directory(tmp_path)

    payload = build_dashboard_payload(
        dataset,
        tmp_path,
        ThresholdConfig(),
        basket_symbols=["/ES"],
    )

    assert payload["cross_validation"]["symbols"] == ["/ES"]
    assert len(payload["cross_validation"]["frame"]) == 2
    assert payload["heatmap_config"]["x_param"] == "lEntryThresh"
    assert payload["heatmap_config"]["y_param"] == "stopParam: ATRmult|nLagLoHi"


def test_decision_board_identifies_cross_sector_pass(tmp_path):
    write_sample_directory(tmp_path)
    dataset = load_optimization_directory(tmp_path)

    payload = build_dashboard_payload(
        dataset,
        tmp_path,
        ThresholdConfig(trade_coverage_threshold=40),
        basket_symbols=["/ES"],
    )

    decision = payload["decision_board"]

    assert decision["verdict"] == "Cross-Sector Pass"
    assert decision["classification"] == "Broadly Robust"
    assert decision["cross_passed"] is True
    assert decision["single_sector_passed"] is True
    assert decision["passing_sector_count"] == 1


def test_write_dashboard_creates_collapsed_validation_sections(tmp_path):
    write_sample_directory(tmp_path)
    output_path = tmp_path / "dashboard.html"

    dashboard_path, payload = write_dashboard(
        tmp_path,
        output_path,
        ThresholdConfig(),
        basket_symbols=["/ES", "/CL"],
    )
    html = dashboard_path.read_text(encoding="utf-8")

    assert dashboard_path == output_path
    assert payload["row_count"] == 4
    assert "Decision Board" in html
    assert "Overall Verdict" in html
    assert "Cross-Sector Gates" in html
    assert "Sector Pass Table" in html
    assert "<details class=\"validation-section\">" in html
    assert "<details class=\"validation-section\" open>" not in html
    assert "Cross-Sector Validation" in html
    assert "Single-Sector Validation" in html
    assert "Summary Statistics" in html
    assert "Profitability (Net Profit)" in html
    assert "Trade Count" in html
    assert "The dashed line marks 100 trades." in html
    assert "PNL Heatmaps" in html
    assert "Equity Indexes" in html
    assert "Energy" in html
