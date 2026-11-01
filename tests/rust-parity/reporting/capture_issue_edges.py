"""Capture frozen clone-occurrence edge cases of reporters/issue_view.project_issue_groups.

    PYTHONPATH=<oracle>/src python capture_issue_edges.py <issue-groups.json> <out.json>

Each case mutates the frozen clone case of issue-groups.json: an occurrence whose end line
precedes its start is dropped (_canonical_occurrence), a missing end line is the start line,
a non-integer lines_count reads "matched code", and an empty id is not a clone group.
"""

import copy
import json
import sys
import tempfile
from pathlib import Path

from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
)
from ici.reporters.issue_view import project_issue_groups


def mutate(case, name):
    data = copy.deepcopy(case["input"])
    group = data["results"][0]["extra"]["clone_groups"][0]
    occurrences = group["occurrences"]
    if name == "end-before-start-is-dropped":
        occurrences[2]["end_line"] = 2
    elif name == "missing-end-is-the-start-line":
        del occurrences[2]["end_line"]
    elif name == "non-integer-lines-count":
        group["lines_count"] = "6"
    elif name == "empty-id-is-not-a-clone-group":
        group["id"] = ""
    return data


def engine(data):
    result = data["results"][0]
    findings = [
        Finding(
            rule_id=item["rule_id"],
            category=FindingCategory(item["category"]),
            severity=FindingSeverity(item["severity"]),
            confidence=FindingConfidence(item["confidence"]),
            fingerprint=item["fingerprint"],
            primary_location=SourceLocation(
                **{
                    key: item["primary_location"][key]
                    for key in ("path", "start_line", "end_line", "label")
                }
            ),
            message=item["message"],
        )
        for item in result["findings"]
    ]
    return EngineResult(
        engine_name=result["engine_name"],
        status=EngineStatus(result["status"]),
        summary=result["summary"],
        extra=result["extra"],
        findings=findings,
    )


def view(group):
    return {
        "message": group.message,
        "fingerprints": list(group.fingerprints),
        "locations": [[item.path, item.start_line, item.end_line] for item in group.locations],
        "original_finding_count": group.original_finding_count,
        "clone_group_id": group.clone_group_id,
    }


def main():
    corpus = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    base = next(
        case for case in corpus["cases"] if case["name"] == "clone-transitive-overlap-cross-file"
    )
    cases = []
    for name in (
        "end-before-start-is-dropped",
        "missing-end-is-the-start-line",
        "non-integer-lines-count",
        "empty-id-is-not-a-clone-group",
    ):
        data = mutate(base, name)
        with tempfile.TemporaryDirectory() as root:
            groups, total = project_issue_groups([engine(data)], root)
        cases.append(
            {"name": name, "input": data, "groups": [view(g) for g in groups], "total": total}
        )
    document = {"oracle": "6fe3e405", "cases": cases}
    Path(sys.argv[2]).write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
