"""Development-only capture: frozen 6fe3e405 suite overview facts for legacy renderers.

Run with the immutable oracle on PYTHONPATH: python capture_suite_overview.py OUT.json
Records serialize_suite_result plus gate_reason, format_score_display and the
select_baseline_details order/severity transitions each frozen renderer printed.
"""
import json
import sys

from ici.core.models import (
    BaselineComparison, DeltaState, EngineResult, EngineStatus, EvidenceState, FindingDelta,
    FindingSeverity, SourceLocation, VerificationSuiteResult, format_score_display, gate_reason,
)
from ici.reporters.baseline_view import select_baseline_details, severity_transition
from ici.reporters.json_rep import serialize_suite_result

S, E = EngineStatus, EvidenceState
FP = "sha256:" + "a" * 64


def engine(name, status, **kw):
    return EngineResult(engine_name=name, status=status, summary=f"{name} summary", **kw)


def loc(path, line, end=None, label=""):
    return SourceLocation(path=path, start_line=line, end_line=end, label=label)


def baseline(gate_failed):
    entries = [
        FindingDelta(DeltaState.UNCHANGED, "lint", FP, "ici.r", "same", loc("a.py", 3), loc("a.py", 3),
                     FindingSeverity.LOW, FindingSeverity.LOW),
        FindingDelta(DeltaState.RESOLVED, "lint", "sha256:" + "b" * 64, "ici.r", "", None, loc("b.py", 1),
                     None, FindingSeverity.HIGH),
        FindingDelta(DeltaState.NEW, "lint", "sha256:" + "c" * 64, "ici.r", "new one", loc("c.py", 9, 12),
                     None, FindingSeverity.HIGH, None, gated=gate_failed),
        FindingDelta(DeltaState.MOVED, "type", "sha256:" + "d" * 64, "ici.t", "moved", loc("d.py", 5), loc("d.py", 2),
                     FindingSeverity.HIGH, FindingSeverity.MEDIUM, regressed=True, gated=gate_failed),
        FindingDelta(DeltaState.NEW, "lint", "sha256:" + "c" * 64, "ici.r", "twin", loc("a.py", 1),
                     None, FindingSeverity.MEDIUM, None),
    ]
    return BaselineComparison(source_path="base.json", entries=entries, warnings=["policy changed"],
                              fail_on_new=True, gate_failed=gate_failed)


suites = {
    "not-run": [engine("lint", S.PASS, evidence=E.NOT_RUN), engine("type", S.FAIL)],
    "required-skip": [engine("line", S.PASS, score=0.125, max_score=2.675), engine("test", S.SKIP)],
    "optional-error-then-fail": [engine("dup", S.ERROR, required=False), engine("cycle", S.FAIL, duration=1.005)],
    "baseline-gate": [engine("lint", S.PASS, extra={"metrics_summary": "Max CC: 3"}, cache_hit=True)],
    "warned-many": [engine(name, S.WARN, required=False, duration=0.004) for name in "abcde"],
    "pass": [engine("lint", S.PASS, score=7, duration=2.5), engine("security", S.PASS, evidence=E.NOT_APPLICABLE)],
}
cases = []
for name, results in suites.items():
    status = S.PASS if name == "pass" else (S.WARN if name == "warned-many" else S.FAIL)
    comparison = baseline(name == "baseline-gate") if name in ("baseline-gate", "pass") else None
    suite = VerificationSuiteResult(suite_status=status, results=results, duration=3.14159,
                                    tem_score=2.345 if name == "pass" else None,
                                    baseline_comparison=comparison)
    selection = select_baseline_details(comparison) if comparison else None
    cases.append({
        "name": name,
        "document": serialize_suite_result(suite),
        "gate_reason": gate_reason(suite.results, suite.suite_status, comparison),
        "scores": [format_score_display(result) for result in results],
        "baseline_order": [entry.message for entry in selection.visible] if selection else [],
        "severity_transitions": [" → ".join(severity_transition(entry)) for entry in selection.visible] if selection else [],
    })
json.dump({"oracle": "6fe3e405", "cases": cases}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
