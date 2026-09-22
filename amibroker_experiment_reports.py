"""Generate the existing detailed report set for one admitted experiment job."""

from __future__ import annotations

from pathlib import Path

from amibroker_experiment_results import sha256
from run_amibroker_analysis import run_analysis


def analyze_job(job: dict, output_root: Path) -> dict:
    """Analyze a job through the library API; never open an external browser."""
    try:
        result, files = run_analysis(
            job["project_path"],
            job["run_path"],
            output_root,
            core_symbols=job.get("core_symbols", []),
            research_start="2009-01-01",
            research_end="2019-01-01",
        )
        report = {
            "job_id": job["job_id"],
            "status": "COMPLETE",
            "decision": result["decision"],
            "error": None,
        }
        for name, path in files.items():
            path = Path(path)
            report[f"{name}_path"] = str(path.resolve())
            report[f"{name}_sha256"] = sha256(path)
        return report
    except Exception as exc:  # one bad job must not stop an experiment summary
        return {
            "job_id": job.get("job_id"),
            "status": "FAILED",
            "decision": None,
            "error": str(exc),
        }
