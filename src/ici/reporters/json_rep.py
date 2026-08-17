"""JSON Report Serializer for CI/CD Pipelines."""

import json
from pathlib import Path

from ici.core.models import VerificationSuiteResult


def save_json_report(suite: VerificationSuiteResult, output_path: Path) -> None:
    """Serializes the verification suite result to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "suite_status": suite.suite_status.value,
        "duration": suite.duration,
        "passed_count": suite.passed_count,
        "warned_count": suite.warned_count,
        "failed_count": suite.failed_count,
        "total_count": suite.total_count,
        "tem_score": suite.tem_score,
        "max_tem_score": suite.max_tem_score,
        "results": [
            {
                "engine_name": r.engine_name,
                "status": r.status.value,
                "summary": r.summary,
                "score": r.score,
                "max_score": r.max_score,
                "duration": r.duration,
                "extra": r.extra,
                "targets": [
                    {
                        "file_path": t.file_path,
                        "start_line": t.start_line,
                        "end_line": t.end_line,
                        "target_name": t.target_name,
                        "status": t.status.value,
                        "message": t.message,
                        "metrics": t.metrics,
                    }
                    for t in r.targets
                ],
            }
            for r in suite.results
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
