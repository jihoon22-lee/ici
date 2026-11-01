"""Capture the frozen legacy sticky PR comment bodies (engines/publish.py; oracle only).

Usage: PYTHONPATH=<oracle>/src python capture_publish_comment.py <out.json>
The suites are minimal ici.result/v3 documents read through the frozen load_suite_from_json.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from ici.engines.publish import (
    ReportPublisher,
    _multi_report_body,
    _PublishedReport,
    load_suite_from_json,
)

ENTRY_NEW = {"state": "new", "regressed": False, "gated": True}
ENTRY_SAME = {"state": "unchanged", "regressed": True, "gated": True}
SUITES = {
    "warn-tem": {
        "schema_version": "ici.result/v3",
        "suite_status": "WARN",
        "tem_score": 3.456,
        "max_tem_score": 5.0,
        "results": [
            {"engine_name": "line", "status": "PASS", "summary": "Total 4 lines"},
            {"engine_name": "lint", "status": "WARN", "summary": "a | b\nnext line"},
            {"engine_name": "type", "status": "FAIL", "summary": "2 errors"},
            {"engine_name": "test", "status": "ERROR", "summary": "did not run"},
            {"engine_name": "dead", "status": "SKIP", "summary": "no sources"},
        ],
        "baseline_comparison": {
            "source_path": "base.json",
            "warnings": ["first `warn` | x", "second"],
            "baseline_metadata": None,
            "fail_on_new": True,
            "gate_failed": True,
            "new_count": 1,
            "unchanged_count": 1,
            "moved_count": 0,
            "resolved_count": 0,
            "regressed_count": 1,
            "gated_count": 2,
            "entries": [ENTRY_NEW, ENTRY_SAME],
        },
    },
    "pass-no-tem": {
        "schema_version": "ici.result/v3",
        "suite_status": "PASS",
        "tem_score": None,
        "max_tem_score": 5.0,
        "results": [{"engine_name": "line", "status": "PASS", "summary": "ok"}],
        "baseline_comparison": None,
    },
}
# Each fault makes the frozen parse_baseline_summary drop the baseline (publish_baseline.py);
# "multiline-warning" is accepted and pins the warning escape.
BASELINE_FAULTS = {
    "bool-count": {"new_count": True},
    "negative-count": {"unchanged_count": -1},
    "float-count": {"moved_count": 0.0},
    "empty-source": {"source_path": ""},
    "warnings-not-list": {"warnings": "one"},
    "empty-warning": {"warnings": [""]},
    "metadata-list": {"baseline_metadata": []},
    "fail-on-new-int": {"fail_on_new": 1},
    "gate-inconsistent": {"gate_failed": False},
    "entries-not-list": {"entries": {}},
    "entry-not-object": {"entries": ["new"]},
    "entry-missing-gated": {"entries": [{"state": "new", "regressed": False}, ENTRY_SAME]},
    "regressed-new": {"entries": [{"state": "new", "regressed": True, "gated": True}, ENTRY_SAME]},
    "gated-unchanged": {
        "entries": [ENTRY_NEW, {"state": "unchanged", "regressed": False, "gated": True}]
    },
    "missing-field": {"resolved_count": None},
    "multiline-warning": {"warnings": ["line one\r\nline <two> `x` | y\u2028z\n"]},
}
# Documents the frozen loader refuses (whole suite) or partly drops (baseline only).
NEGATIVE = {
    "unknown-engine-status": {
        **SUITES["pass-no-tem"],
        "results": [{"engine_name": "line", "status": "OK", "summary": "ok"}],
    },
    "null-engine-status": {
        **SUITES["pass-no-tem"],
        "results": [{"engine_name": "line", "status": None}],
    },
    "unknown-schema": {**SUITES["pass-no-tem"], "schema_version": "ici.result/v4"},
    "bad-duration": {
        **SUITES["pass-no-tem"],
        "results": [{"engine_name": "line", "status": "PASS", "duration": "slow"}],
    },
    "null-summary-bool-tem": {
        **SUITES["pass-no-tem"],
        "tem_score": True,
        "results": [{"engine_name": "line", "status": "PASS", "summary": None}],
    },
    "miscounted-baseline": {
        **SUITES["warn-tem"],
        "baseline_comparison": {**SUITES["warn-tem"]["baseline_comparison"], "gated_count": 1},
    },
    **{
        f"baseline-{name}": {
            **SUITES["warn-tem"],
            "baseline_comparison": {**SUITES["warn-tem"]["baseline_comparison"], **change},
        }
        for name, change in BASELINE_FAULTS.items()
    },
    "uppercase-baseline-state": {
        **SUITES["warn-tem"],
        "baseline_comparison": {
            **SUITES["warn-tem"]["baseline_comparison"],
            "entries": [{**ENTRY_NEW, "state": "NEW"}, ENTRY_SAME],
        },
    },
}


def main() -> None:
    publisher = ReportPublisher(env={})
    suites = {}
    with tempfile.TemporaryDirectory() as temporary:
        for name, document in {**SUITES, **NEGATIVE}.items():
            path = Path(temporary) / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            suites[name] = load_suite_from_json(path)
    run_url = "https://github.example/team/repo/actions/runs/9"
    single = {
        "viewer": publisher._build_comment_body(
            suites["warn-tem"],
            "https://pages.example/a/index.html",
            True,
            "self",
            "pr/7/index.html",
            run_url,
            True,
        ),
        "no-pages": publisher._build_comment_body(
            suites["pass-no-tem"], None, False, "hub", "proj/pr/7/index.html", None, True
        ),
        "no-suite": publisher._build_comment_body(
            None, "https://pages.example/b/index.html", True, "self", "index.html", None, True
        ),
    }
    negative = {
        name: publisher._build_comment_body(
            suites[name], None, True, "self", "index.html", None, True
        )
        for name in NEGATIVE
    }
    reports = [
        _PublishedReport(
            "api", suites["warn-tem"], "api/pr/7/index.html", "https://pages.example/api", True
        ),
        _PublishedReport("web", suites["pass-no-tem"], "web/pr/7/index.html", None, True),
        _PublishedReport("docs", None, "docs/pr/7/index.html", None, True),
    ]
    document = {
        "source": "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "suites": SUITES,
        "negative_suites": NEGATIVE,
        "single": single,
        "negative": negative,
        "multi": {
            "pages": _multi_report_body(reports, "self", run_url, True),
            "no-pages": _multi_report_body(reports[1:], "hub", None, False),
        },
    }
    Path(sys.argv[1]).write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
