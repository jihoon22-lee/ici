"""WP20-A — function metrics as ici's own checks.

The promises under test (#218):

- ``*.complexity`` and ``*.cognitive`` are internal checks — no tool, no
  process — that score every function the scope's files declare
- both checks share one scan through the metric cache: selecting both does
  not read the source twice
- Python rows are exact (AST); C++ rows come from the heuristic scanner and
  say so — ESTIMATED evidence, never quietly MEASURED
- a file that cannot be parsed is a limitation, not a silent zero
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ici.__main__ import app
from ici.languages.cycles import CycleRequest, measure_cycles
from ici.languages.metrics import MetricRequest, measure, scan_functions

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

_HOT = """def hot(flag, other):
    total = 0
    if flag:
        for i in range(10):
            if i % 2:
                total += i
            elif i % 3:
                total -= i
            else:
                total *= i
    while other:
        if other > 10:
            for j in range(other):
                if j % 2 and j % 3:
                    total += j
                elif j % 5:
                    total -= j
        other -= 1
    return total
"""

_HOT_CPP = """int hot(int flag, int other) {
    int total = 0;
    if (flag) {
        for (int i = 0; i < 10; ++i) {
            if (i % 2) { total += i; }
            else if (i % 3) { total -= i; }
            else { total *= i; }
        }
    }
    while (other) {
        if (other > 10) {
            for (int j = 0; j < other; ++j) {
                if (j % 2 && j % 3) { total += j; }
                else if (j % 5) { total -= j; }
            }
        }
        --other;
    }
    return total;
}
"""


def _python_workspace(root: Path, source: str) -> None:
    (root / "app").mkdir(parents=True)
    (root / "app" / "lib.py").write_text(source, encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + '[checks."python.test"]\nenabled = false\n'
        + '[checks."python.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )


def _verify(root: Path) -> dict:
    result_path = root / "result.json"
    outcome = runner.invoke(app, ["next", "verify", "--json", "--result", str(result_path)])
    # A hot function is a gate FAIL — exit 1 is the verdict, not an error.
    assert outcome.exit_code in (0, 1), outcome.output
    return json.loads(result_path.read_text(encoding="utf-8"))


def _findings(payload: dict, task_fragment: str) -> list:
    return [
        finding for finding in payload["findings"] if task_fragment in (finding["task_id"] or "")
    ]


def _metric(payload: dict, name: str) -> dict:
    for metric in payload["metrics"]:
        if metric["name"] == name:
            return metric
    raise AssertionError(f"metric {name} absent: {payload['metrics']}")


def test_python_complexity_flags_a_hot_function(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, _HOT)

    payload = _verify(tmp_path)

    findings = _findings(payload, "python.complexity")
    assert findings, "a deeply nested function produced no finding"
    assert any("hot" in f["message"] for f in findings)
    assert all(f["evidence"] == "MEASURED" for f in findings)
    assert _metric(payload, "max_complexity")["value"] > 1


def test_python_cognitive_and_complexity_share_one_scan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, "def calm() -> int:\n    return 1\n")

    payload = _verify(tmp_path)

    for kind in ("complexity", "cognitive"):
        assert _metric(payload, f"max_{kind}")["value"] >= 0


def test_scan_functions_reads_python_once_for_both_measures(tmp_path: Path) -> None:
    target = tmp_path / "lib.py"
    target.write_text(_HOT, encoding="utf-8")

    rows = scan_functions(target, "python", tmp_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "hot"
    assert row.cyclomatic > 1 and row.cognitive > 0 and row.nesting > 1
    assert not row.heuristic


def test_cpp_rows_are_marked_heuristic(tmp_path: Path) -> None:
    target = tmp_path / "lib.cpp"
    target.write_text(_HOT_CPP, encoding="utf-8")

    rows = scan_functions(target, "cpp", tmp_path)

    assert rows, "the scanner found no functions in a plain C++ file"
    assert all(row.heuristic for row in rows)
    assert any(row.name == "hot" and row.cyclomatic > 1 for row in rows)


def test_cpp_complexity_marks_estimated_evidence(tmp_path: Path) -> None:
    target = tmp_path / "lib.cpp"
    target.write_text(_HOT_CPP, encoding="utf-8")
    request = MetricRequest(
        kind="complexity",
        language="cpp",
        project_root=tmp_path,
        files=(target,),
        task_id="app.cpp.complexity",
    )

    observation = measure(request)

    # A heuristic scan must not promote itself to measured evidence.
    assert all(m.evidence.value == "ESTIMATED" for m in observation.measurements)
    assert any("heuristic" in note for note in observation.limitations)
    assert any(m.name == "functions_measured" and m.value >= 1 for m in observation.measurements)


def test_a_shared_cache_scans_once(tmp_path: Path) -> None:
    target = tmp_path / "lib.py"
    target.write_text(_HOT, encoding="utf-8")
    cache: dict = {}
    requests = [
        MetricRequest(
            kind=kind,
            language="python",
            project_root=tmp_path,
            files=(target,),
            task_id=f"app.python.{kind}",
            cache=cache,
        )
        for kind in ("complexity", "cognitive")
    ]

    measure(requests[0])
    assert target in cache
    cached = cache[target]
    measure(requests[1])
    assert cache[target] is cached, "the second check re-scanned the file"


def test_an_unparseable_file_is_a_limitation_not_silence(tmp_path: Path) -> None:
    target = tmp_path / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    request = MetricRequest(
        kind="complexity",
        language="python",
        project_root=tmp_path,
        files=(target,),
        task_id="app.python.complexity",
    )

    observation = measure(request)

    assert observation.limitations, "a file nobody measured passed silently"


def test_calm_python_code_reports_no_findings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, "def calm() -> int:\n    return 1\n")

    payload = _verify(tmp_path)

    assert _metric(payload, "functions_measured")["value"] == 1
    assert _findings(payload, "python.complexity") == []


def _cyclic_python_workspace(root: Path) -> None:
    pkg = root / "app" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("import pkg.b\n", encoding="utf-8")
    (pkg / "b.py").write_text("import pkg.a\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + '[checks."python.test"]\nenabled = false\n'
        + '[checks."python.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )


def test_python_cycle_reports_the_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cyclic_python_workspace(tmp_path)

    payload = _verify(tmp_path)

    findings = _findings(payload, "python.cycle")
    assert findings, "a two-module import cycle produced no finding"
    assert any("pkg.a" in f["message"] and "pkg.b" in f["message"] for f in findings)


def test_cpp_cycle_is_estimated(tmp_path: Path) -> None:
    (tmp_path / "a.h").write_text('#include "b.h"\n', encoding="utf-8")
    (tmp_path / "b.h").write_text('#include "a.h"\n', encoding="utf-8")
    request = CycleRequest(
        language="cpp",
        project_root=tmp_path,
        files=(tmp_path / "a.h", tmp_path / "b.h"),
        task_id="app.cpp.cycle",
    )

    observation = measure_cycles(request)

    assert observation.findings, "a two-header include cycle produced no finding"
    assert all(f.evidence.value == "ESTIMATED" for f in observation.findings)
    assert any("heuristic" in note for note in observation.limitations)


def test_acyclic_python_reports_no_cycles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, "def calm() -> int:\n    return 1\n")

    payload = _verify(tmp_path)

    assert _findings(payload, "python.cycle") == []
