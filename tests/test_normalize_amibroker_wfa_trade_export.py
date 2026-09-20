import csv

import pandas as pd

from normalize_amibroker_wfa_trade_export import (
    WFA_HEADERS,
    main,
    normalize_file,
)


def write_export(path, headers, rows, delimiter=","):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)


def test_normalizes_all_bad_trailing_headers_and_preserves_data(tmp_path):
    input_path = tmp_path / "raw.csv"
    output_path = tmp_path / "nested" / "normalized.csv"
    headers = ["Symbol", "Trade"] + [""] * len(WFA_HEADERS)
    row = ["ES", "Long"] + [str(index) for index in range(len(WFA_HEADERS))]
    write_export(input_path, headers, [row])
    original_bytes = input_path.read_bytes()

    result = normalize_file(input_path, output_path)
    normalized = pd.read_csv(output_path, dtype=str, encoding="utf-8-sig")

    assert result.row_count == 1
    assert result.original_column_count == 26
    assert result.final_column_count == 26
    assert len(result.renames) == 24
    assert normalized.columns.tolist() == ["Symbol", "Trade"] + WFA_HEADERS
    assert normalized.iloc[0].tolist() == row
    assert input_path.read_bytes() == original_bytes


def test_partial_existing_headers_skip_only_names_already_present(tmp_path):
    input_path = tmp_path / "partial.csv"
    output_path = tmp_path / "normalized.csv"
    headers = [
        "Symbol",
        "Trade",
        "WFA_01_Segment",
        "",
        "WFA_03_EntryYear",
        "Column4",
    ]
    write_export(input_path, headers, [["ES", "Long", "A", "2", "2026", "6"]])

    result = normalize_file(input_path, output_path)
    normalized = pd.read_csv(output_path, dtype=str, encoding="utf-8-sig")

    assert normalized.columns.tolist() == [
        "Symbol",
        "Trade",
        "WFA_01_Segment",
        "WFA_02_EntryDateNum",
        "WFA_03_EntryYear",
        "WFA_04_EntryMonth",
    ]
    assert len(result.warnings) == 1
    assert "22 expected WFA header(s) were missing" in result.warnings[0]


def test_duplicate_and_extra_trailing_headers_receive_unique_names(tmp_path):
    input_path = tmp_path / "extras.csv"
    output_path = tmp_path / "normalized.csv"
    bad_headers = [""] * len(WFA_HEADERS) + ["Field25", "Field26"]
    headers = ["Symbol"] + bad_headers
    row = ["ES"] + [str(index) for index in range(len(bad_headers))]
    write_export(input_path, headers, [row])

    result = normalize_file(input_path, output_path)
    normalized = pd.read_csv(output_path, dtype=str, encoding="utf-8-sig")

    assert normalized.columns.tolist() == ["Symbol"] + WFA_HEADERS + [
        "WFA_EXTRA_01",
        "WFA_EXTRA_02",
    ]
    assert len(normalized.columns) == len(set(normalized.columns))
    assert "2 additional bad trailing column(s)" in result.warnings[0]


def test_duplicate_trailing_header_is_detected_before_pandas_mangling(tmp_path):
    input_path = tmp_path / "duplicate.csv"
    output_path = tmp_path / "normalized.csv"
    write_export(
        input_path,
        ["Symbol", "Price", "Price"],
        [["ES", "100", "custom-value"]],
    )

    result = normalize_file(input_path, output_path)
    normalized = pd.read_csv(output_path, dtype=str, encoding="utf-8-sig")

    assert normalized.columns.tolist() == ["Symbol", "Price", "WFA_01_Segment"]
    assert normalized.iloc[0].tolist() == ["ES", "100", "custom-value"]
    assert result.renames[0].original == "Price"


def test_auto_detects_tab_and_dry_run_does_not_write(tmp_path, capsys):
    input_path = tmp_path / "raw.tsv"
    output_path = tmp_path / "normalized.tsv"
    write_export(
        input_path,
        ["Symbol", "Trade", "Unnamed: 2"],
        [["ES", "Long", "A"]],
        delimiter="\t",
    )

    exit_code = main([
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--dry-run",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not output_path.exists()
    assert "Delimiter: tab" in captured.out
    assert "Column 3: 'Unnamed: 2' -> 'WFA_01_Segment'" in captured.out
    assert "Dry run: output file was not written." in captured.out


def test_missing_input_returns_clear_error(tmp_path, capsys):
    exit_code = main([
        "--input",
        str(tmp_path / "missing.csv"),
        "--output",
        str(tmp_path / "output.csv"),
    ])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "ERROR: Input file does not exist" in captured.err


def test_refuses_to_overwrite_input_file(tmp_path, capsys):
    input_path = tmp_path / "raw.csv"
    write_export(input_path, ["Symbol", ""], [["ES", "A"]])

    exit_code = main([
        "--input",
        str(input_path),
        "--output",
        str(input_path),
    ])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Output path must differ from the input path" in captured.err
