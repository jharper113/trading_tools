import re
from pathlib import Path


SCRIPT_HELP_FILES = {
    "download_market_data.py": "docs/help/download_market_data.md",
    "validate_market_data.py": "docs/help/validate_market_data.md",
    "extract_trade_history.py": "docs/help/extract_trade_history.md",
    "analyze_strategy_performance.py": "docs/help/analyze_strategy_performance.md",
}


def documented_flags(script_path):
    source = Path(script_path).read_text()

    return sorted(
        set(
            re.findall(
                r"""["'](-{1,2}[A-Za-z][A-Za-z0-9-]*)["']""",
                source,
            )
        )
    )


def test_script_flags_are_documented_in_help_files():
    for script_path, help_path in SCRIPT_HELP_FILES.items():
        help_text = Path(help_path).read_text()

        for flag in documented_flags(script_path):
            assert flag in help_text, f"{flag} missing from {help_path}"
