import hashlib
import json
import re
from pathlib import Path

import pytest

from amibroker_experiment_config import load_catalog, load_matrix, validate_unlock


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "amibroker_experiments/strategy_catalog.json"
MATRIX = ROOT / "amibroker_experiments/strategy_test_matrix.json"
STRATEGIES = Path(
    "/home/jon/Dropbox/HarpFolders/04_Code/Amibroker/Strategies/2026 Strategy Testing"
)


def test_active_strategies_use_custom_include_directory():
    include_dir = STRATEGIES.parent / "Include"
    directive = re.compile(
        r'^\s*#\s*include(?:_once)?\s*(?P<open>[<"\'])(?P<name>[^>"\']+)[>"\']',
        re.IGNORECASE | re.MULTILINE,
    )
    violations = []
    missing = []
    for source in sorted(STRATEGIES.glob("*.afl")):
        for match in directive.finditer(source.read_text(encoding="utf-8-sig")):
            if match.group("open") != "<":
                violations.append(f"{source.name}: {match.group(0).strip()}")
            if not (include_dir / match.group("name")).is_file():
                missing.append(f"{source.name}: {match.group('name')}")
    assert not violations, "Quoted strategy includes: " + ", ".join(violations)
    assert not missing, "Missing shared includes: " + ", ".join(missing)


def test_pilot_matrix_has_exact_approved_jobs():
    catalog = load_catalog(CATALOG)
    matrix = load_matrix(MATRIX, catalog)
    jobs = [matrix["jobs"][job_id] for job_id in matrix["modes"]["pilot"]]
    assert [
        (job["strategy_id"], job["periodicity"], job["adapter"]["name"])
        for job in jobs
    ] == [
        ("0001_exhaustion_open_dislocation_fade", "Daily", "native"),
        ("0063_intraday_opening_range_breakout", "15m", "native"),
        ("0063_intraday_opening_range_breakout", "15m", "entry_cutoff_110000"),
        ("0063_intraday_opening_range_breakout", "60m", "native"),
        ("0084_ES_INTRADAY_PCTB", "15m", "fixed_110000"),
    ]
    assert matrix["research_window"] == {
        "start": "2009-01-01",
        "end": "2019-01-01",
    }
    assert matrix["timezone"] == "America/Detroit"


def test_matrix_rejects_duplicate_job_and_unknown_strategy(tmp_path):
    catalog = {"schema_version": 1, "strategies": {}}
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "matrix_id": "bad",
                "research_window": {
                    "start": "2009-01-01",
                    "end": "2019-01-01",
                },
                "timezone": "America/Detroit",
                "jobs": {"x": {"strategy_id": "missing"}},
                "modes": {"pilot": ["x", "x"]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate|unknown"):
        load_matrix(bad, catalog)


def test_catalog_covers_every_canonical_afl_and_hashes_match():
    catalog = load_catalog(CATALOG)
    actual = sorted(STRATEGIES.glob("*.afl"))
    assert actual
    assert len(actual) == len(catalog["strategies"])
    for path in actual:
        entry = catalog["strategies"][path.stem]
        assert entry["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_mode_covers_each_enabled_catalog_strategy_with_a_native_job():
    catalog = load_catalog(CATALOG)
    matrix = load_matrix(MATRIX, catalog)
    selected = {
        matrix["jobs"][job_id]["strategy_id"]
        for job_id in matrix["modes"]["full"]
        if matrix["jobs"][job_id]["adapter"]["name"] == "native"
    }
    assert selected == {
        strategy_id
        for strategy_id, entry in catalog["strategies"].items()
        if entry["enabled"]
    }


def test_matrix_rejects_a_stale_source_hash(tmp_path):
    source = tmp_path / "strategy.afl"
    source.write_text("Buy = True;", encoding="utf-8")
    catalog = {
        "schema_version": 1,
        "strategies": {
            "strategy": {
                "source_afl": str(source),
                "source_sha256": "0" * 64,
                "enabled": True,
                "native_periodicity": "Daily",
            }
        },
    }
    with pytest.raises(ValueError, match="hash"):
        load_matrix(_write_minimal_matrix(tmp_path, source), catalog)


def _write_minimal_matrix(tmp_path, source):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "matrix_id": "test",
                "research_window": {"start": "2009-01-01", "end": "2019-01-01"},
                "timezone": "America/Detroit",
                "modes": {"pilot": ["job"]},
                "jobs": {
                    "job": {
                        "job_id": "job",
                        "strategy_id": "strategy",
                        "source_afl": str(source),
                        "source_sha256": "0" * 64,
                        "periodicity": "Daily",
                        "interval_seconds": 86400,
                        "database": "daily",
                        "project_template": "template.apx",
                        "symbols": ["ES"],
                        "adapter": {"name": "native"},
                        "analysis_profile": "daily.json",
                        "enabled": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_unlock_must_match_pilot_and_audit_hash(tmp_path):
    unlock = tmp_path / "unlock.json"
    unlock.write_text(
        json.dumps(
            {
                "audit_status": "PASS",
                "pilot_experiment_id": "pilot-1",
                "audit_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    assert validate_unlock(unlock, "pilot-1", "a" * 64)["audit_status"] == "PASS"
    with pytest.raises(ValueError, match="pilot"):
        validate_unlock(unlock, "pilot-2", "a" * 64)
