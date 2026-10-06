"""Known-answer fixtures exercised through the next-path internal checks.

These are the repository's defect corpus: files written to contain one known
defect each. The stable engine e2e tests used to guard the "a detector that
stops detecting fails here" property; with the engines gone the same fixtures
are read by the ``ici.languages`` measures directly — no toolchain required,
which is why each is registered ``kind = "data"`` in the fixture manifest.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixture_manifest import fixture

from ici.languages.cycles import CycleRequest, measure_cycles
from ici.languages.duplicates import DuplicateRequest, measure_duplicates
from ici.languages.hygiene import HygieneRequest, measure_hygiene
from ici.languages.metrics import MetricRequest, measure
from ici.languages.python.lines import LineRequest, count

CPP_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "cpp-fixtures"
PY_FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "python-fixtures"


def _files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*") if path.is_file()))


def _rules(observation) -> set[str]:
    return {finding.rule_id for finding in observation.findings}


def test_cycle_fixture_reports_the_include_cycle() -> None:
    root = fixture("cpp/cycle_pair").path
    observation = measure_cycles(
        CycleRequest(
            language="cpp",
            project_root=root,
            files=_files(root),
            task_id="cpp.cycle",
        )
    )
    assert "cycle.include" in _rules(observation)
    text = " ".join(f.message for f in observation.findings)
    assert "a.hpp" in text and "b.hpp" in text


def test_complexity_fixture_reports_the_hot_function() -> None:
    root = fixture("cpp/complexity_hot").path
    observation = measure(
        MetricRequest(
            kind="complexity",
            language="cpp",
            project_root=root,
            files=_files(root),
            task_id="cpp.complexity",
        )
    )
    assert observation.findings, "expected a complexity finding on the seeded hotspot"


def test_clone_fixture_reports_the_type_2_clone() -> None:
    root = fixture("cpp/clone_pair").path
    observation = measure_duplicates(
        DuplicateRequest(
            language="cpp",
            project_root=root,
            files=_files(root),
            task_id="cpp.dup",
        )
    )
    assert observation.findings, "expected a duplicate finding on the seeded clone pair"


def test_dtor_fixture_reports_the_throwing_destructor() -> None:
    root = fixture("cpp/dtor_throw").path
    observation = measure_hygiene(
        HygieneRequest(
            kind="exception",
            project_root=root,
            files=_files(root),
            task_id="cpp.exception",
        )
    )
    assert observation.findings, "expected an exception finding on the throwing destructor"


def test_oversized_fixture_reports_the_file_size_finding() -> None:
    root = fixture("cpp/oversized_file").path
    observation = count(
        LineRequest(
            project_root=root,
            files=_files(root),
            task_id="cpp.line",
        )
    )
    assert observation.findings, "expected a size finding on the oversized file"


def test_clean_baseline_reports_no_findings() -> None:
    root = fixture("cpp/clean_baseline").path
    files = _files(root)
    observations = (
        measure_cycles(
            CycleRequest(language="cpp", project_root=root, files=files, task_id="cpp.cycle")
        ),
        measure_duplicates(
            DuplicateRequest(language="cpp", project_root=root, files=files, task_id="cpp.dup")
        ),
        measure(
            MetricRequest(
                kind="complexity",
                language="cpp",
                project_root=root,
                files=files,
                task_id="cpp.complexity",
            )
        ),
        measure(
            MetricRequest(
                kind="cognitive",
                language="cpp",
                project_root=root,
                files=files,
                task_id="cpp.cognitive",
            )
        ),
        measure_hygiene(
            HygieneRequest(
                kind="exception", project_root=root, files=files, task_id="cpp.exception"
            )
        ),
        count(LineRequest(project_root=root, files=files, task_id="cpp.line")),
    )
    for observation in observations:
        assert not observation.findings, (
            f"{observation.task_id} reported findings on the clean baseline: "
            f"{[f.rule_id for f in observation.findings]}"
        )


def test_multi_component_attributes_the_defect_to_beta_only() -> None:
    # The pair exists to pin attribution: the defect lives in beta, so a
    # finding attributed to alpha is a misattribution. On the next path the
    # component declares its files — that is the declared-scope behaviour the
    # fixture now guards.
    root = fixture("python/multi_component").path
    beta = root / "components" / "beta"
    observation = measure(
        MetricRequest(
            kind="complexity",
            language="python",
            project_root=beta,
            files=_files(beta),
            task_id="python.complexity",
        )
    )
    assert any("classify" in finding.message for finding in observation.findings)
    for finding in observation.findings:
        assert "alpha" not in str(finding.primary_location.path)


def test_python_single_project_reports_no_findings() -> None:
    root = fixture("python/single_project").path
    files = _files(root)
    observations = (
        measure_cycles(
            CycleRequest(language="python", project_root=root, files=files, task_id="python.cycle")
        ),
        measure_duplicates(
            DuplicateRequest(
                language="python", project_root=root, files=files, task_id="python.dup"
            )
        ),
        measure(
            MetricRequest(
                kind="complexity",
                language="python",
                project_root=root,
                files=files,
                task_id="python.complexity",
            )
        ),
        count(LineRequest(project_root=root, files=files, task_id="python.line")),
    )
    for observation in observations:
        assert not observation.findings, (
            f"{observation.task_id} reported findings on the control project: "
            f"{[f.rule_id for f in observation.findings]}"
        )
