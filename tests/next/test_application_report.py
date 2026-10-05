"""From a run to a saved result to a page, with analysis and render apart.

#206 items 5 and 6. The chain is exercised with the real Ruff, because the
lesson from PR A is still fresh: hand-written fixtures agreed with each other
and disagreed with the tool.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.application.plan import Plan, PlannedCheck
from ici.application.report import assemble, digest_of
from ici.application.verify import verify
from ici.domain.enums import GateVerdict, ScopeKind
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import SourceSnapshot
from ici.languages.python.checks import LINE_CHECK, LINT_CHECK
from ici.languages.python.lines import LineRequest, count
from ici.reporting.offline_html import render

RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    return root


def _lint(root: Path) -> PlannedCheck:
    provider = RuffProvider()
    plan = provider.plan(
        RuffRequest(executable=RUFF, project_root=root, targets=("src",), task_id="python.lint")
    )
    return PlannedCheck(check=LINT_CHECK, task=plan)


def _run(root: Path, *, with_ruff: bool = True):
    lint = (
        _lint(root)
        if with_ruff
        else PlannedCheck(check=LINT_CHECK, blocked="ruff is not in this bundle")
    )
    sources = tuple(sorted((root / "src").rglob("*.py")))
    return verify(
        Plan(checks=(PlannedCheck(check=LINE_CHECK), lint)),
        providers={"ruff": RuffProvider()},
        analyses={"python.line": lambda: count(LineRequest(project_root=root, files=sources))},
    )


def _stored(root: Path, verification) -> tuple[Path, Path]:
    result = assemble(
        verification,
        run_id="run-1",
        ici_version="0.0.0-next",
        source=SourceSnapshot(digest=digest_of("sources")),
        policy_digest=digest_of("policy"),
        toolchain_digest=digest_of("toolchain"),
        component_ids=("app",),
        languages=("python",),
        scope=ScopeKind.PARTIAL,
    )
    out = root / ".ici"
    out.mkdir(exist_ok=True)
    saved = out / "result.json"
    saved.write_text(dumps(run_result_to_dict(result)), encoding="utf-8")
    page = out / "result.html"
    # Rendered from what was *read back*, not from the object in hand: that is
    # what "분석과 render 호출은 분리한다" has to mean in practice.
    page.write_text(render(loads(saved.read_text(encoding="utf-8"))), encoding="utf-8")
    return saved, page


@needs_ruff
def test_a_seeded_defect_is_a_finding_a_json_and_a_page(project: Path) -> None:
    (project / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")

    verification = _run(project)
    saved, page = _stored(project, verification)

    assert verification.gate.selected is GateVerdict.FAIL
    assert verification.exit_code == 1

    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["gate"]["selected"] == "FAIL"
    assert len(stored["findings"]) == 1
    assert stored["findings"][0]["rule_id"] == "ruff.F401"

    body = page.read_text(encoding="utf-8")
    assert "ruff.F401" in body
    assert "src/app.py:1" in body


@needs_ruff
def test_a_clean_project_passes_and_the_page_says_so(project: Path) -> None:
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    verification = _run(project)
    saved, page = _stored(project, verification)

    assert verification.exit_code == 0
    assert json.loads(saved.read_text(encoding="utf-8"))["gate"]["selected"] == "PASS"
    assert "none recorded" in page.read_text(encoding="utf-8")


def test_a_missing_required_tool_is_incomplete_in_the_json_and_the_page(project: Path) -> None:
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    verification = _run(project, with_ruff=False)
    saved, page = _stored(project, verification)

    assert verification.exit_code == 3
    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["gate"]["selected"] == "INCOMPLETE"
    assert stored["execution"]["required_complete"] is False
    assert "not in this bundle" in page.read_text(encoding="utf-8")


@needs_ruff
def test_the_same_scope_appears_in_the_json_and_the_page(project: Path) -> None:
    # #206: tool·config·선택 범위가 JSON/HTML에서 동일하다.
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    saved, page = _stored(project, _run(project))

    stored = json.loads(saved.read_text(encoding="utf-8"))
    body = page.read_text(encoding="utf-8")

    assert stored["scope"]["selected_components"] == ["app"]
    assert "app" in body
    assert stored["scope"]["kind"] in body


@needs_ruff
def test_verifying_writes_nothing_into_the_project(project: Path) -> None:
    # Not only "the sources are unchanged": nothing appears at all. Left to
    # itself Ruff drops .ruff_cache/ into the tree it is checking, which this
    # test caught, and a verification tool that writes into what it verifies
    # cannot run against a read-only checkout.
    (project / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}

    _run(project)

    after = {p: p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}
    assert after == before, "verifying changed the tree it was verifying"


@needs_ruff
def test_a_partial_run_never_claims_the_workspace_passed(project: Path) -> None:
    # R05. Stored rather than derived, so a consumer cannot recompute it from
    # an incomplete list and reach the wrong conclusion.
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    saved, _ = _stored(project, _run(project))

    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["full_required_satisfied"] is False
    assert stored["gate"]["workspace"] == "NOT_EVALUATED"


# --- a read-only checkout -------------------------------------------------


@needs_ruff
def test_a_project_that_cannot_be_written_to_is_still_verified(project: Path) -> None:
    # #206 asks for a read-only install to work; the same has to hold for the
    # tree being checked. This does not catch the .ruff_cache problem on its
    # own -- Ruff tolerates a cache write it cannot make, so this passed both
    # before and after that fix, and the sibling test above is what caught it.
    # It is here for the property itself: a future change that genuinely needs
    # write access to the subject would fail here and nowhere else.
    (project / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    for path in sorted(project.rglob("*"), reverse=True):
        path.chmod(0o500 if path.is_dir() else 0o400)
    project.chmod(0o500)
    try:
        verification = _run(project)

        assert verification.exit_code == 1
        assert len(verification.findings) == 1
    finally:
        project.chmod(0o700)
        for path in sorted(project.rglob("*")):
            path.chmod(0o700 if path.is_dir() else 0o600)
