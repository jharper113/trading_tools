import csv

import pandas as pd

from analyze_strategy_performance import (
    aggregate_realized_trades,
    load_cleaned_trades,
    prepare_wfa_analysis_input,
)
from prepare_amibroker_wfa_for_strategy_analysis import (
    convert_wfa_trades,
    main,
    prepare_file,
)
from normalize_amibroker_wfa_trade_export import WFA_HEADERS


def sample_wfa_frame():
    return pd.DataFrame([
        {
            "Ticker": "/ES",
            "Trade": "Long",
            "Entry date": "1/2/2024",
            "Entry price": "4800",
            "Exit date": "1/5/2024",
            "Exit price": "4820",
            "Profit": "1,000",
            "Shares": "2",
            "Pos. value": "25,000",
            "WFA_01_Segment": "2024",
        },
        {
            "Ticker": "/ES",
            "Trade": "Short (max loss)",
            "Entry date": "2/1/2024",
            "Entry price": "4900",
            "Exit date": "2/2/2024",
            "Exit price": "4910",
            "Profit": "(500)",
            "Shares": "1",
            "Pos. value": "20,000",
            "WFA_01_Segment": "2024",
        },
    ])


def test_convert_wfa_trades_creates_matched_open_and_close_executions():
    converted = convert_wfa_trades(
        sample_wfa_frame(),
        "WFA1 OOS",
        2_000_000,
        "WFA_Trade_Output.csv",
    )

    assert len(converted) == 4
    assert converted["Pos Effect"].tolist() == [
        "TO OPEN",
        "TO CLOSE",
        "TO OPEN",
        "TO CLOSE",
    ]
    assert converted["Side"].tolist() == ["BUY", "SELL", "SELL", "BUY"]
    assert converted["Exec Time"].tolist() == [
        "1/2/24 09:30:00",
        "1/5/24 16:00:00",
        "2/1/24 09:30:00",
        "2/2/24 16:00:00",
    ]
    assert converted["net_pnl"].tolist() == [0.0, 1000.0, 0.0, -500.0]
    assert converted["WFA_01_Segment"].tolist() == ["2024"] * 4


def test_convert_ignores_fully_blank_export_padding_rows():
    frame = sample_wfa_frame()
    blank_rows = pd.DataFrame(
        [{column: "" for column in frame.columns} for _ in range(3)]
    )
    padded = pd.concat([frame, blank_rows], ignore_index=True)

    converted = convert_wfa_trades(
        padded,
        "WFA1 OOS",
        2_000_000,
        "WFA_Trade_Output.csv",
    )

    assert len(converted) == 4
    assert converted["WFA_Source_Row"].unique().tolist() == [2, 3]


def test_convert_uses_initial_stop_risk_for_wfa_returns():
    frame = sample_wfa_frame().iloc[[0]].copy()
    frame["WFA_05_AmtRisked"] = "500"
    frame["WFA_13_DollarsPerPt"] = "50"
    frame["WFA_14_StopDist"] = "10"

    converted = convert_wfa_trades(
        frame,
        "WFA1 OOS",
        2_000_000,
        "WFA_Trade_Output.csv",
    )
    close = converted.iloc[-1]

    assert close["WFA_Risk_Per_Contract"] == 500
    assert close["WFA_Total_Initial_Risk"] == 1000
    assert close["margin_requirement"] == 1000
    assert close["return_on_margin"] == 1.0
    assert close["WFA_Return_On_Initial_Risk"] == 1.0


def test_convert_still_rejects_partially_populated_rows():
    frame = sample_wfa_frame()
    partial_row = {column: "" for column in frame.columns}
    partial_row["Ticker"] = "/ES"
    frame = pd.concat([frame, pd.DataFrame([partial_row])], ignore_index=True)

    try:
        convert_wfa_trades(frame, "WFA1 OOS", 2_000_000, "wfa.csv")
    except ValueError as exc:
        assert "shares" in str(exc)
        assert "[4]" in str(exc)
    else:
        raise AssertionError("Expected partial-row validation error")


def test_prepared_file_is_accepted_by_strategy_analyzer(tmp_path):
    input_path = tmp_path / "wfa.csv"
    output_path = tmp_path / "analyzer_input.csv"
    sample_wfa_frame().to_csv(input_path, index=False, encoding="utf-8-sig")

    prepared = prepare_file(
        input_path,
        output_path,
        "WFA1 OOS",
        2_000_000,
    )
    loaded = load_cleaned_trades(output_path)
    realized = aggregate_realized_trades(loaded)

    assert len(prepared) == 4
    assert len(realized) == 2
    assert realized["net_pnl"].tolist() == [1000.0, -500.0]
    assert realized["Strategy_Name"].tolist() == ["WFA1 OOS", "WFA1 OOS"]
    assert realized["starting_equity"].tolist() == [2_000_000, 2_000_000]
    assert realized["WFA_01_Segment"].astype(str).tolist() == ["2024", "2024"]


def test_convert_rejects_missing_required_columns():
    frame = sample_wfa_frame().drop(columns=["Exit price"])

    try:
        convert_wfa_trades(frame, "WFA1 OOS", 2_000_000, "wfa.csv")
    except ValueError as exc:
        assert "Exit price" in str(exc)
    else:
        raise AssertionError("Expected missing-column validation error")


def test_cli_warns_when_loss_exceeds_exported_position_value(tmp_path, capsys):
    input_path = tmp_path / "wfa.csv"
    output_path = tmp_path / "analyzer_input.csv"
    frame = sample_wfa_frame().iloc[[1]].copy()
    frame["Profit"] = "-25,000"
    frame.to_csv(input_path, index=False, encoding="utf-8-sig")

    exit_code = main([
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--strategy-name",
        "WFA1 OOS",
        "--starting-equity",
        "2000000",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "lost at least 100% of exported Pos. value" in captured.err


def test_analyzer_auto_prepares_raw_wfa_and_infers_starting_equity(tmp_path):
    report_dir = tmp_path / "WFA1_Reports"
    report_dir.mkdir()
    input_path = report_dir / "WFA_Trade_Output.csv"
    output_path = tmp_path / "prepared.csv"
    sample_wfa_frame().to_csv(input_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([
        {
            "Mode": "IS",
            "Net Profit": 1_000_000,
            "Net % Profit": 50,
        },
        {
            "Mode": "IS",
            "Net Profit": 2_000_100,
            "Net % Profit": 100,
        },
    ]).to_csv(report_dir / "Daily_ES_WF1.csv", index=False)

    prepared_path, equity, strategy = prepare_wfa_analysis_input(
        input_path,
        output_path,
    )
    loaded = load_cleaned_trades(prepared_path)

    assert prepared_path == output_path
    assert equity == 2_000_000
    assert strategy == "WFA1_OOS"
    assert len(loaded) == 4


def test_analyzer_normalizes_blank_wfa_headers_before_preparing(tmp_path):
    report_dir = tmp_path / "WFA2_Reports"
    report_dir.mkdir()
    input_path = report_dir / "WFA_Trade_Output_RAW.csv"
    output_path = tmp_path / "prepared.csv"
    normalized_path = tmp_path / "normalized.csv"
    source = sample_wfa_frame().drop(columns=["WFA_01_Segment"])
    custom_values = ["2024"] + [str(index) for index in range(2, 25)]

    with input_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(source.columns.tolist() + [""] * len(WFA_HEADERS))
        for row in source.itertuples(index=False, name=None):
            writer.writerow(list(row) + custom_values)

    pd.DataFrame([{
        "Mode": "IS",
        "Net Profit": 1_000_000,
        "Net % Profit": 50,
    }]).to_csv(report_dir / "Daily_ES_WF2.csv", index=False)

    prepared_path, equity, strategy = prepare_wfa_analysis_input(
        input_path,
        output_path,
        normalized_output_path=normalized_path,
    )
    normalized = pd.read_csv(normalized_path, dtype=str)
    loaded = load_cleaned_trades(prepared_path)

    assert normalized.columns[-len(WFA_HEADERS):].tolist() == WFA_HEADERS
    assert normalized.loc[0, "WFA_01_Segment"] == "2024"
    assert normalized.loc[0, "WFA_24_LongOrShort"] == "24"
    assert equity == 2_000_000
    assert strategy == "WFA2_OOS"
    assert len(loaded) == 4
    assert input_path.read_text(encoding="utf-8-sig").splitlines()[0].endswith(
        "," * len(WFA_HEADERS)
    )
