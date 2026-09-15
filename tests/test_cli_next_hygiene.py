"""WP20-C — security and resource hygiene as ici's own checks.

The promises under test (#218):

- ``python.security`` and ``python.resource`` run the same per-file AST
  analyses the stable engines call — ``analyze_python_security`` and
  ``analyze_python_resources`` — no duplicate rule implementation
- findings keep their native rule name (``Security:PickleLoad``,
  ``Resource:OpenWithoutWith``), source path and line
- a file that cannot be parsed is a limitation, not a clean file
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ici.__main__ import app
from ici.languages.hygiene import HygieneRequest, measure_hygiene

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'

_UNSAFE = """import pickle


def load(blob):
    return pickle.loads(blob)
"""

_LEAK = """def slurp(path):
    handle = open(path)
    handle.read()
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
    assert outcome.exit_code in (0, 1), outcome.output
    return json.loads(result_path.read_text(encoding="utf-8"))


def _findings(payload: dict, task_fragment: str) -> list:
    return [
        finding for finding in payload["findings"] if task_fragment in (finding["task_id"] or "")
    ]


def _measure(root: Path, kind: str, name: str, text: str):
    target = root / name
    target.write_text(text, encoding="utf-8")
    return measure_hygiene(
        HygieneRequest(
            kind=kind,
            project_root=root,
            files=(target,),
            task_id=f"python.{kind}",
        )
    )


def test_security_flags_pickle_load(tmp_path: Path) -> None:
    observation = _measure(tmp_path, "security", "lib.py", _UNSAFE)

    assert observation.state.value == "SUCCEEDED"
    assert len(observation.findings) == 1
    finding = observation.findings[0]
    assert finding.native_rule_id == "Security:PickleLoad"
    assert finding.primary_location.path == "lib.py"
    assert finding.primary_location.start_line == 5
    assert finding.severity == "high"


def test_resource_flags_open_without_with(tmp_path: Path) -> None:
    observation = _measure(tmp_path, "resource", "lib.py", _LEAK)

    rules = {f.native_rule_id for f in observation.findings}
    assert "Resource:OpenWithoutWith" in rules


def test_clean_source_produces_no_finding(tmp_path: Path) -> None:
    for kind in ("security", "resource"):
        observation = _measure(tmp_path, kind, f"{kind}.py", "def calm() -> int:\n    return 1\n")
        assert observation.findings == ()
        assert observation.limitations == ()


def test_unparseable_source_is_a_limitation_not_a_pass(tmp_path: Path) -> None:
    observation = _measure(tmp_path, "security", "bad.py", "def broken(:\n")

    assert observation.findings == ()
    assert any("syntax" in item for item in observation.limitations)


def test_verify_reports_security_finding_with_location(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, _UNSAFE)

    payload = _verify(tmp_path)

    findings = _findings(payload, "python.security")
    assert findings, "pickle.loads produced no python.security finding"
    finding = findings[0]
    assert finding["native_rule_id"] == "Security:PickleLoad"
    assert finding["primary_location"]["path"].endswith("lib.py")
    assert finding["primary_location"]["start_line"] == 5


def test_verify_reports_resource_finding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _python_workspace(tmp_path, _LEAK)

    payload = _verify(tmp_path)

    findings = _findings(payload, "python.resource")
    assert any(f["native_rule_id"] == "Resource:OpenWithoutWith" for f in findings)
