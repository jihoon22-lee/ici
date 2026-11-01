"""Capture frozen v0.13.0 historical `ici verify --sarif` documents; never product code.

    PYTHONPATH=<oracle>/src python capture_legacy_sarif.py <oracle-root> <output.json>

Part 1 builds suites from the frozen models in process and records, per case, the saved v3
suite (`serialize_suite_result`, trimmed to what the SARIF writer reads) with the document the
frozen `ici.reporters.sarif.serialize_sarif` wrote for the same suite, or its refusal. The
cases cover related locations (labelled and bare), every suppression kind, multi-file fixes,
tool fields, percent-encoded paths, spans without columns, the highest-severity rule choice,
every baseline delta state (a moved delta's baseline location, a resolved delta's recomputed
identity, paired deltas consumed in order), ERROR/SKIP/PASS suite invocations and both
size bounds (the bound cases record only their shape and the refusal message).

Part 2 runs the frozen stable `ici verify --report --sarif` CLI in fresh Python, C++ and
hybrid projects (empty HOME, PATH=/usr/bin:/bin) plus a baseline round trip (write, edit,
compare with --fail-on-new) and records the trimmed saved suite and the SARIF document, with
the project root as `<root>`.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ici.core.findings import findings_for_result
from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingDelta,
    FindingFix,
    FindingFixReplacement,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    SuppressionKind,
    ToolEvidence,
    VerificationSuiteResult,
)
from ici.reporters.json_rep import serialize_suite_result
from ici.reporters.sarif import serialize_sarif

PROJECT = Path("/project")
ROOT = "<root>"
ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"
REPO = Path(__file__).resolve().parents[3]


def trim(suite):
    """Keep exactly the saved fields the historical SARIF writer reads."""

    return {
        "schema_version": suite["schema_version"],
        "suite_status": suite["suite_status"],
        "results": [
            {
                "engine_name": result["engine_name"],
                "status": result["status"],
                "findings": result["findings"],
            }
            for result in suite["results"]
        ],
        "baseline_comparison": suite["baseline_comparison"],
    }


def finding(rule, path, line, message, severity="medium", **fields):
    return Finding(
        rule_id=rule,
        category=fields.pop("category", FindingCategory.CORRECTNESS),
        severity=FindingSeverity(severity),
        confidence=fields.pop("confidence", FindingConfidence.HIGH),
        fingerprint="sha256:" + "0" * 64,
        primary_location=fields.pop("location", SourceLocation(path=path, start_line=line)),
        message=message,
        **fields,
    )


def edges_suite():
    lint = EngineResult(
        engine_name="lint",
        status=EngineStatus.FAIL,
        summary="lint",
        evidence=EvidenceState.MEASURED,
        tool_evidence=[ToolEvidence(name="ruff", path="/usr/bin/ruff", version="0.5.7")],
        targets=[
            InspectionTarget("src/a.py", 3, 3, "E501", EngineStatus.WARN, "too long"),
            InspectionTarget("src/a.py", 9, None, "F401:os", EngineStatus.FAIL, "unused"),
            InspectionTarget("src/a b/ü.py", 1, None, "ok", EngineStatus.PASS, "passed"),
            InspectionTarget("src/skip.py", 2, None, "s", EngineStatus.SKIP, "", "", {"n": 1.5}),
        ],
        findings=[
            finding(
                "ici.lint.ruff.e501",
                "src/a.py",
                4,
                "line too long",
                "medium",
                location=SourceLocation(
                    path="src/a.py",
                    start_line=4,
                    end_line=5,
                    start_column=2,
                    end_column=7,
                    label="E501",
                ),
                related_locations=[
                    SourceLocation(path="src/b.py", start_line=8, label="defined here"),
                    SourceLocation(path="src/a.py", start_line=1, start_column=1),
                ],
                explanation="Lines should be short.",
                remediation="Wrap the line.",
                tool_rule_id="E501",
                tool_name="ruff",
                tool_version="0.5.7",
                suppression=FindingSuppression(True, SuppressionKind.INLINE, "accepted <risk>"),
                metrics={"length": FindingMetric(120, "characters"), "ratio": FindingMetric(1.5)},
                snippet="x = 1",
                fixes=[
                    FindingFix(
                        description="wrap both",
                        replacements=(
                            FindingFixReplacement("src/b.py", 2, 1, 2, 4, "new"),
                            FindingFixReplacement("src/a.py", 4, 2, 4, 7, ""),
                            FindingFixReplacement("src/a.py", 5, 1, 5, 2, "y"),
                        ),
                    ),
                    FindingFix(description="", replacements=()),
                    FindingFix(
                        replacements=(FindingFixReplacement("src/a b/c.py", 1, 1, 1, 1, "z"),)
                    ),
                ],
            ),
            finding("ici.lint.ruff.e501", "src/c.py", 2, "", "high"),
            finding(
                "ici.lint.ruff.e501",
                "src/d.py",
                1,
                "low one",
                "low",
                explanation="never chosen",
            ),
            finding(
                "ici.lint.ruff.f401",
                "src/a.py",
                9,
                "configured",
                "low",
                suppression=FindingSuppression(True, SuppressionKind.CONFIG, ""),
            ),
            finding(
                "ici.lint.other",
                "src/a.py",
                9,
                "baselined",
                "critical",
                category=FindingCategory.SECURITY,
                confidence=FindingConfidence.EXACT,
                suppression=FindingSuppression(True, SuppressionKind.BASELINE, "baseline"),
            ),
            finding("ici.lint.info", "src/a.py", 2, "info", "info", remediation="do x"),
        ],
    )
    security = EngineResult(
        engine_name="security",
        status=EngineStatus.WARN,
        summary="security",
        evidence=EvidenceState.ESTIMATED,
        targets=[InspectionTarget("src/a.py", 4, 4, "B101", EngineStatus.WARN, "assert")],
        findings=[finding("ici.lint.ruff.e501", "src/a.py", 4, "same rule other engine", "high")],
    )
    return VerificationSuiteResult(suite_status=EngineStatus.FAIL, results=[lint, security])


def baseline_suite():
    lint = EngineResult(
        engine_name="lint",
        status=EngineStatus.FAIL,
        summary="lint",
        findings=[
            finding("ici.lint.new", "src/a.py", 1, "new one", "high"),
            finding("ici.lint.same", "src/a.py", 2, "same", "medium"),
            finding("ici.lint.moved", "src/a.py", 30, "moved", "medium"),
            finding("ici.lint.twice", "src/a.py", 40, "twice", "low"),
            finding(
                "ici.lint.twice",
                "src/a.py",
                40,
                "twice",
                "low",
                location=SourceLocation(path="src/a.py", start_line=40, label="second"),
            ),
            finding("ici.lint.unmatched", "src/a.py", 50, "no delta", "medium"),
        ],
    )
    current = {
        item.rule_id + item.primary_location.label: item for item in findings_for_result(lint)
    }

    def delta(state, item, **fields):
        return FindingDelta(
            state=state,
            engine_name="lint",
            fingerprint=item.fingerprint,
            rule_id=item.rule_id,
            message=item.message,
            current_location=item.primary_location,
            current_severity=item.severity,
            **fields,
        )

    new = current["ici.lint.new"]
    same = current["ici.lint.same"]
    moved = current["ici.lint.moved"]
    twice = current["ici.lint.twice"]
    entries = [
        delta(DeltaState.NEW, new, gated=True),
        delta(
            DeltaState.UNCHANGED,
            same,
            baseline_location=same.primary_location,
            baseline_severity=FindingSeverity.LOW,
            regressed=True,
            gated=True,
        ),
        delta(
            DeltaState.MOVED,
            moved,
            baseline_location=SourceLocation(path="src/old.py", start_line=3, label="was"),
            baseline_severity=FindingSeverity.MEDIUM,
        ),
        delta(DeltaState.NEW, twice),
        FindingDelta(
            state=DeltaState.RESOLVED,
            engine_name="lint",
            fingerprint="sha256:" + "a" * 64,
            rule_id="ici.lint.gone",
            message="gone",
            baseline_location=SourceLocation(path="src/gone.py", start_line=7, label="g"),
            baseline_severity=FindingSeverity.HIGH,
        ),
        FindingDelta(
            state=DeltaState.RESOLVED,
            engine_name="security",
            fingerprint="sha256:" + "b" * 64,
            rule_id="ici.security.gone",
            message="",
            baseline_location=SourceLocation(path="src/gone.py", start_line=1),
            baseline_severity=FindingSeverity.LOW,
            suppressed=True,
        ),
    ]
    comparison = BaselineComparison(
        source_path="base.json", entries=entries, fail_on_new=True, gate_failed=True
    )
    return VerificationSuiteResult(
        suite_status=EngineStatus.FAIL, results=[lint], baseline_comparison=comparison
    )


def status_suite(status):
    line = EngineResult(
        engine_name="line",
        status=status,
        summary="line",
        targets=[InspectionTarget("src/a.py", 1, None, "a.py", status, "counted")],
    )
    return VerificationSuiteResult(suite_status=status, results=[line])


def bound_suite(findings, rules):
    engine = EngineResult(
        engine_name="lint",
        status=EngineStatus.FAIL,
        summary="lint",
        findings=[
            finding(f"ici.bound.r{index % rules}", "src/a.py", index + 1, "bounded", "low")
            for index in range(findings)
        ],
    )
    return VerificationSuiteResult(suite_status=EngineStatus.FAIL, results=[engine])


def synthetic():
    cases = {}
    for name, suite in (
        ("edges", edges_suite()),
        ("baseline", baseline_suite()),
        ("error", status_suite(EngineStatus.ERROR)),
        ("skip", status_suite(EngineStatus.SKIP)),
        ("pass", status_suite(EngineStatus.PASS)),
        ("empty", VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[])),
    ):
        cases[name] = {
            "suite": trim(serialize_suite_result(suite, project_root=PROJECT)),
            "sarif": serialize_sarif(suite, project_root=PROJECT),
        }
    for name, findings, rules in (
        ("rule-bound", 10_001, 10_001),
        ("rule-bound-edge", 10_000, 10_000),
        ("result-bound", 100_001, 100_001),
    ):
        try:
            sarif = serialize_sarif(bound_suite(findings, rules), project_root=PROJECT)
            outcome = {"results": len(sarif["runs"][0]["results"])}
            outcome["rules"] = len(sarif["runs"][0]["tool"]["driver"]["rules"])
        except ValueError as error:
            outcome = {"refused": str(error)}
        cases[name] = {"findings": findings, "rules": rules, **outcome}
    return cases


# Source text the frozen analyses flag; written to disk, never imported or executed.
PYTHON = {
    "src/__init__.py": '"""Package."""\n',
    "src/core.py": (
        "import pickle\nimport subprocess\n\n\ndef load(blob):\n    return pickle.loads(blob)\n\n\n"
        "def run(cmd):\n    return subprocess.call(cmd, shell=True)\n\n\n"
        "def read(path):\n    f = open(path)\n    return f.read()\n"
    ),
    "src/exc.py": "def bad():\n    try:\n        return 1 / 0\n    except:\n        pass\n",
}
EDITED = {
    "src/exc.py": PYTHON["src/exc.py"]
    + "\n\ndef worse():\n    try:\n        return 2\n    except:\n        pass\n",
    "src/core.py": "\n\n" + PYTHON["src/core.py"],
}
CPP = "parity-fixtures/cpp/lint-warnings"
HYBRID = {
    **PYTHON,
    "src/calc.cpp": (
        "#include <cstdio>\nint divide(int a, int b) {\n    int unused = 0;\n    return a / b;\n}\n"
    ),
}
VERIFY = ["verify", "--report", "--sarif", "out/report.sarif"]
END_TO_END = {
    "python": (PYTHON, None, None, [VERIFY]),
    "cpp": (None, CPP, None, [VERIFY]),
    "hybrid": (HYBRID, None, None, [VERIFY]),
    "baseline": (
        PYTHON,
        None,
        EDITED,
        [["verify", "--write-baseline", "base.json"], [*VERIFY, "--baseline", "base.json"]],
    ),
    "baseline-fail-on-new": (
        PYTHON,
        None,
        EDITED,
        [
            ["verify", "--write-baseline", "base.json"],
            [*VERIFY, "--baseline", "base.json", "--fail-on-new"],
        ],
    ),
}


def write(base, tree):
    for name, text in tree.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_case(oracle, tree, copied, edit, steps):
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary).resolve() / "project"
        if copied:
            shutil.copytree(REPO / copied, base)
        else:
            write(base, tree)
        (base / ".git").mkdir(exist_ok=True)
        environment = {"HOME": home, "XDG_CONFIG_HOME": home, "PATH": "/usr/bin:/bin"}
        codes = []
        for index, argv in enumerate(steps):
            if index == len(steps) - 1 and edit:
                write(base, edit)
            completed = subprocess.run(
                [sys.executable, "-I", "-c", ENTRY, str(oracle / "src"), *argv],
                cwd=base,
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            codes.append(completed.returncode)
        suite = json.loads(
            (base / "verify_report.json").read_text(encoding="utf-8").replace(str(base), ROOT)
        )
        sarif = json.loads(
            (base / "out/report.sarif").read_text(encoding="utf-8").replace(str(base), ROOT)
        )
        return {
            "tree": tree,
            "copied": copied,
            "edit": edit,
            "steps": steps,
            "exit_codes": codes,
            "stdout_mentions_sarif": "report.sarif" in completed.stdout,
            "suite": trim(suite),
            "sarif": sarif,
        }


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "synthetic": synthetic(),
        "end_to_end": {name: run_case(oracle, *case) for name, case in END_TO_END.items()},
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    for name, case in document["synthetic"].items():
        print("synthetic", name, case.get("refused") or len(case.get("sarif", {}).get("runs", [])))
    for name, case in document["end_to_end"].items():
        print("end_to_end", name, case["exit_codes"], len(case["sarif"]["runs"][0]["results"]))


if __name__ == "__main__":
    main()
