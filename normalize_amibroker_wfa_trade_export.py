#!/usr/bin/env python3
# Usage:
#   python normalize_amibroker_wfa_trade_export.py \
#       --input raw_wfa_trades.csv --output normalized_wfa_trades.csv
# Add --dry-run to preview header changes without writing an output file.

"""Normalize trailing custom-metric headers in AmiBroker WFA trade exports."""

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


WFA_HEADERS = [
    "WFA_01_Segment",
    "WFA_02_EntryDateNum",
    "WFA_03_EntryYear",
    "WFA_04_EntryMonth",
    "WFA_05_AmtRisked",
    "WFA_06_EntryInd",
    "WFA_07_BBW",
    "WFA_08_TrendFilter",
    "WFA_09_EntryATR",
    "WFA_10_ADX",
    "WFA_11_pctB",
    "WFA_12_pctRange",
    "WFA_13_DollarsPerPt",
    "WFA_14_StopDist",
    "WFA_15_PositionSize",
    "WFA_16_EntryIndex",
    "WFA_17_TrendIndex",
    "WFA_18_TrendLength",
    "WFA_19_LongEntryThresh",
    "WFA_20_ShortEntryThresh",
    "WFA_21_StopParam",
    "WFA_22_TargType",
    "WFA_23_TargParam",
    "WFA_24_LongOrShort",
]

GENERIC_HEADER_RE = re.compile(r"^(?:column|field)[\s_-]*\d+$", re.IGNORECASE)
UNNAMED_HEADER_RE = re.compile(r"^unnamed\s*:", re.IGNORECASE)
EXTRA_HEADER_RE = re.compile(r"^WFA_EXTRA_\d+$")
PLACEHOLDER_HEADERS = {
    "column",
    "field",
    "metric",
    "custom metric",
    "placeholder",
    "unknown",
    "n/a",
    "na",
    "null",
    "none",
}


@dataclass(frozen=True)
class ColumnRename:
    position: int
    original: str
    replacement: str


@dataclass(frozen=True)
class NormalizationResult:
    input_path: Path
    output_path: Path
    row_count: int
    original_column_count: int
    final_column_count: int
    delimiter: str
    renames: list
    warnings: list
    wrote_output: bool


def delimiter_character(delimiter_name):
    if delimiter_name == "comma":
        return ","
    if delimiter_name == "tab":
        return "\t"
    raise ValueError(f"Unsupported delimiter: {delimiter_name}")


def detect_delimiter(path, encoding, delimiter_name):
    if delimiter_name != "auto":
        return delimiter_character(delimiter_name)

    with Path(path).open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(65536)

    if not sample:
        raise ValueError("Input file is empty; a CSV header row is required.")

    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        comma_count = first_line.count(",")
        tab_count = first_line.count("\t")

        if comma_count == tab_count == 0:
            raise ValueError(
                "Could not auto-detect a comma or tab delimiter. "
                "Use --delimiter comma or --delimiter tab."
            )

        return "\t" if tab_count > comma_count else ","


def read_raw_headers(path, encoding, delimiter):
    with Path(path).open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            return next(reader)
        except StopIteration as exc:
            raise ValueError(
                "Input file is empty; a CSV header row is required."
            ) from exc


def duplicate_header_positions(raw_headers):
    positions = set()
    seen = set()

    for index, header in enumerate(raw_headers):
        normalized = str(header).strip().casefold()

        if not normalized:
            continue

        if normalized in seen:
            positions.add(index)
        else:
            seen.add(normalized)

    return positions


def is_bad_header(raw_header, pandas_header, is_duplicate=False):
    raw = str(raw_header).strip()
    parsed = str(pandas_header).strip()
    normalized = raw.casefold()

    return any([
        not raw,
        not parsed,
        is_duplicate,
        bool(UNNAMED_HEADER_RE.match(raw)),
        bool(UNNAMED_HEADER_RE.match(parsed)),
        bool(GENERIC_HEADER_RE.match(raw)),
        normalized in PLACEHOLDER_HEADERS,
    ])


def is_wfa_header(header):
    name = str(header).strip()
    return name in WFA_HEADERS or bool(EXTRA_HEADER_RE.match(name))


def trailing_custom_region(columns, bad_positions):
    """Return indices in the rightmost block of bad or recognized WFA headers."""
    region = []

    for index in range(len(columns) - 1, -1, -1):
        if index in bad_positions or is_wfa_header(columns[index]):
            region.append(index)
            continue
        break

    return list(reversed(region))


def next_extra_header(used_names, start_number=1):
    number = start_number

    while True:
        candidate = f"WFA_EXTRA_{number:02d}"
        if candidate not in used_names:
            return candidate, number + 1
        number += 1


def propose_column_names(raw_headers, pandas_headers):
    if len(raw_headers) != len(pandas_headers):
        raise ValueError(
            "Raw CSV header count does not match the parsed dataframe column "
            f"count ({len(raw_headers)} vs {len(pandas_headers)})."
        )

    duplicates = duplicate_header_positions(raw_headers)
    bad_positions = {
        index
        for index, (raw, parsed) in enumerate(zip(raw_headers, pandas_headers))
        if is_bad_header(raw, parsed, index in duplicates)
    }
    region = trailing_custom_region(pandas_headers, bad_positions)
    rename_positions = [index for index in region if index in bad_positions]
    existing_wfa = {
        str(header).strip()
        for header in pandas_headers
        if str(header).strip() in WFA_HEADERS
    }
    missing_wfa = [header for header in WFA_HEADERS if header not in existing_wfa]

    final_headers = list(pandas_headers)
    renames = []
    used_names = set(final_headers)
    extra_number = 1

    for offset, index in enumerate(rename_positions):
        if offset < len(missing_wfa):
            replacement = missing_wfa[offset]
        else:
            replacement, extra_number = next_extra_header(
                used_names,
                extra_number,
            )

        original = str(raw_headers[index])
        final_headers[index] = replacement
        used_names.add(replacement)
        renames.append(ColumnRename(index + 1, original, replacement))

    warnings = []
    if len(rename_positions) < len(missing_wfa):
        warnings.append(
            f"Found {len(rename_positions)} bad trailing column(s), but "
            f"{len(missing_wfa)} expected WFA header(s) were missing. Only "
            "the available trailing columns were renamed."
        )

    if len(rename_positions) > len(missing_wfa):
        extras = len(rename_positions) - len(missing_wfa)
        warnings.append(
            f"Found {extras} additional bad trailing column(s); assigned "
            "WFA_EXTRA names."
        )

    if len(final_headers) != len(set(final_headers)):
        raise ValueError(
            "Final headers are not unique. Check for duplicate non-trailing "
            "columns in the source export."
        )

    return final_headers, renames, warnings


def normalize_file(
    input_path,
    output_path,
    delimiter_name="auto",
    encoding="utf-8-sig",
    dry_run=False,
):
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"Input path is not a file: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError(
            "Output path must differ from the input path; the original export "
            "will not be modified."
        )

    delimiter = detect_delimiter(input_path, encoding, delimiter_name)
    raw_headers = read_raw_headers(input_path, encoding, delimiter)
    frame = pd.read_csv(
        input_path,
        sep=delimiter,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    final_headers, renames, warnings = propose_column_names(
        raw_headers,
        list(frame.columns),
    )
    frame.columns = final_headers

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(
            output_path,
            index=False,
            sep=delimiter,
            encoding=encoding,
        )

    return NormalizationResult(
        input_path=input_path,
        output_path=output_path,
        row_count=len(frame),
        original_column_count=len(raw_headers),
        final_column_count=len(frame.columns),
        delimiter=delimiter,
        renames=renames,
        warnings=warnings,
        wrote_output=not dry_run,
    )


def printable_header(header):
    return header if str(header).strip() else "<blank>"


def print_result(result, dry_run=False):
    print(f"Input path: {result.input_path}")
    print(f"Output path: {result.output_path}")
    print(f"Row count: {result.row_count:,}")
    print(f"Original column count: {result.original_column_count:,}")
    print(f"Final column count: {result.final_column_count:,}")
    print("Delimiter: " + ("tab" if result.delimiter == "\t" else "comma"))
    print("Proposed changes:" if dry_run else "Renamed columns:")

    if result.renames:
        for rename in result.renames:
            print(
                f"  Column {rename.position}: "
                f"{printable_header(rename.original)!r} -> "
                f"{rename.replacement!r}"
            )
    else:
        print("  None")

    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if dry_run:
        print("Dry run: output file was not written.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Normalize trailing custom-metric headers in an AmiBroker Walk "
            "Forward Analysis trade-list export."
        )
    )
    parser.add_argument("--input", required=True, help="Raw AmiBroker trade CSV.")
    parser.add_argument("--output", required=True, help="Normalized output path.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed changes without writing the output file.",
    )
    parser.add_argument(
        "--delimiter",
        choices=["auto", "comma", "tab"],
        default="auto",
        help="Input/output delimiter. Default: auto-detect comma or tab.",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Input/output text encoding. Default: utf-8-sig.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        result = normalize_file(
            args.input,
            args.output,
            delimiter_name=args.delimiter,
            encoding=args.encoding,
            dry_run=args.dry_run,
        )
        print_result(result, dry_run=args.dry_run)
        return 0
    except (
        FileNotFoundError,
        UnicodeError,
        csv.Error,
        pd.errors.ParserError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
