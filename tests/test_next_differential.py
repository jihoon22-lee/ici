"""WP28 #226 item 2 — old-vs-new differential over the seeded defect corpus.

The stable engines and the next-path internal checks call the same analysis
bodies — ``ici.languages.*`` wraps the ``measure_*`` functions the engines
were built on — so the honest comparison is per fixture:

- every defect the stable engine reports must still surface under ``next
  verify``, on the same files, attributed to the check that owns the rule;
- a fixture that is clean on the stable path stays clean on the next path;
- where the two disagree, the difference is *classified*: the only allowed
  kind is a structural one the design documents (declared scope beating
  directory defaults — R05/R07). A finding that simply vanished is a
  regression and fails this test.

Checks backed by an external tool are disabled in the generated configs —
this matrix compares the shared analysis bodies, not this machine's PATH.
The real-tool fixtures (``asan_overflow``, ``cmake_*``, ``qmake_project``)
keep their own tool-gated tests; their differential belongs to the runner
matrix, not here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.core.models import EngineStatus
from ici.engines.complexity import ComplexityEngine
from ici.engines.cycle import CycleEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.line import LineCountEngine

runner = CliRunner()

FIXTURES = Path(__file__).resolve().parent.parent / "examples"

HEADER = 'schema_version = 1\n[workspace]\nname = "differential"\n'

# Provider-backed checks are opted out — the differential exercises the
# internal bodies, and a missing tool must stay "blocked", never a finding.
_CPP_TOOL_CHECKS = (
    "cpp.compile",
    "cpp.diagnostics",
    "cpp.tidy",
    "cpp.test",
    "cpp.coverage",
    "cpp.artifact",
    "cpp.binary-compat",
)
_PY_TOOL_CHECKS = (
    "python.lint",
    "python.format",
    "python.type",
    "python.test",
    "python.coverage",
    "python.compat-runtime",
)
_CPP_TEXT_CHECKS = (
    "cpp.line",
    "cpp.complexity",
    "cpp.cognitive",
    "cpp.cycle",
    "cpp.dup",
    "cpp.exception",
)
_PY_TEXT_CHECKS = (
    "python.line",
    "python.complexity",
    "python.cognitive",
    "python.cycle",
    "python.dup",
    "python.security",
    "python.resource",
    "python.exception",
    "python.dead",
)

# stable engine → the next check that carries its rules
_CPP_DEFECT_CASES = (
    ("cycle_pair", CycleEngine, "cpp.cycle"),
    ("complexity_hot", ComplexityEngine, "cpp.complexity"),
    ("clone_pair", DuplicateEngine, "cpp.dup"),
    ("dtor_throw", ExceptionSafetyEngine, "cpp.exception"),
    ("oversized_file", LineCountEngine, "cpp.line"),
)


def _workspace(
    tmp_path: Path,
    source: Path,
    languages: tuple[str, ...],
    disabled: tuple[str, ...],
    *,
    component_root: str = ".",
) -> Path:
    """Copy the fixture into a next-schema workspace.

    The fixture itself is never written to — the copy is where ``ici.toml``
    and ``.ici/`` land.
    """
    root = tmp_path / "ws"
    shutil.copytree(source, root)
    lines = [
        HEADER,
        '[[components]]\nid = "comp"\n'
        f'root = "{component_root}"\n'
        f"languages = {json.dumps(list(languages))}\n",
    ]
    for check in disabled:
        lines.append(f'[checks."{check}"]\nenabled = false\n')
    (root / "ici.toml").write_text("".join(lines), encoding="utf-8")
    return root


def _next_findings(root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict]:
    result_path = tmp_path / "result.json"
    monkeypatch.chdir(root)
    result = runner.invoke(app, ["next", "verify", "--result", str(result_path)])
    # Findings may fail the gate (1); nothing may be unreadable or incomplete
    # (2/3) — the differential only compares runs that finished.
    assert result.exit_code in (0, 1), result.output
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["schema_id"] == "ici.next.run"
    return document["findings"]


def _stable_files(engine_cls, fixture: Path) -> set[str]:
    result = engine_cls(fixture).run()
    assert result.status != EngineStatus.ERROR, result.summary
    return {
        Path(target.file_path).name
        for target in result.targets
        if target.status != EngineStatus.PASS
    }


def _check_files(findings: list[dict], check_id: str) -> set[str]:
    """Every file a check's findings name — primary *and* related.

    The next model makes one finding per defect and hangs the other files
    off ``related_locations``; the stable model made a target per file.
    Comparing only primary locations would read that shape change as a lost
    finding — the files are all still named.
    """
    files: set[str] = set()
    for finding in findings:
        if not (finding.get("task_id") or "").endswith(check_id):
            continue
        spans = [finding["primary_location"], *finding.get("related_locations", [])]
        files.update(Path(span["path"]).name for span in spans)
    return files


@pytest.mark.parametrize(
    ("fixture", "engine_cls", "check_id"),
    _CPP_DEFECT_CASES,
    ids=[case[0] for case in _CPP_DEFECT_CASES],
)
def test_a_cpp_defect_the_stable_engine_found_still_surfaces(
    fixture: str, engine_cls, check_id: str, tmp_path: Path, monkeypatch
) -> None:
    source = FIXTURES / "cpp-fixtures" / fixture
    stable_files = _stable_files(engine_cls, source)
    assert stable_files, f"stable {engine_cls.__name__} found nothing on {fixture}"

    root = _workspace(tmp_path, source, ("cpp",), _CPP_TOOL_CHECKS)
    findings = _next_findings(root, monkeypatch, tmp_path)
    next_files = _check_files(findings, check_id)

    # Per-file parity: every file the stable engine named must appear under
    # the owning check. A file present only on the stable side is a lost
    # finding — a regression, not a structural difference.
    missing = stable_files - next_files
    assert not missing, f"{check_id} lost findings the stable engine had: {missing}"


def test_a_clean_cpp_fixture_stays_clean(tmp_path: Path, monkeypatch) -> None:
    source = FIXTURES / "cpp-fixtures" / "clean_baseline"
    for engine_cls in (CycleEngine, ComplexityEngine, DuplicateEngine, ExceptionSafetyEngine):
        assert not _stable_files(engine_cls, source), engine_cls.__name__

    root = _workspace(tmp_path, source, ("cpp",), _CPP_TOOL_CHECKS)
    findings = _next_findings(root, monkeypatch, tmp_path)

    noisy = {
        finding.get("task_id")
        for finding in findings
        for check in _CPP_TEXT_CHECKS
        if (finding.get("task_id") or "").endswith(check)
    }
    assert not noisy, f"text checks fired on a clean fixture: {noisy}"


def test_a_clean_python_fixture_stays_clean(tmp_path: Path, monkeypatch) -> None:
    source = FIXTURES / "python-fixtures" / "single_project"
    assert not _stable_files(LineCountEngine, source)

    root = _workspace(tmp_path, source, ("python",), _PY_TOOL_CHECKS)
    findings = _next_findings(root, monkeypatch, tmp_path)

    noisy = {
        finding.get("task_id")
        for finding in findings
        for check in _PY_TEXT_CHECKS
        if (finding.get("task_id") or "").endswith(check)
    }
    assert not noisy, f"text checks fired on a clean fixture: {noisy}"


def test_declared_scope_beats_directory_discovery(tmp_path: Path, monkeypatch) -> None:
    """The one intended structural difference the corpus demonstrates.

    ``multi_component`` keeps its packages under ``components/``, which is not
    a stable default source dir — the stable engine runs over zero files and
    reports PASS. On the next path the component declares its root, so
    ``python.complexity`` sees ``beta`` and reports the classify() function
    the fixture planted. Old-quiet/new-finding here is the designed scope
    fix, not a false positive — it is the R05/R07 contract working.
    """
    source = FIXTURES / "python-fixtures" / "multi_component"
    # The stable path's documented behaviour on this fixture: nothing to see.
    assert not _stable_files(ComplexityEngine, source)

    root = _workspace(
        tmp_path,
        source,
        ("python",),
        _PY_TOOL_CHECKS,
        component_root="components/beta",
    )
    findings = _next_findings(root, monkeypatch, tmp_path)

    beta_findings = _check_files(findings, "python.complexity")
    assert "__init__.py" in beta_findings, "the declared component must see beta"
