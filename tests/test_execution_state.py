"""Focused tests for explicit test execution state across adapters and engines."""

import os
import shutil
from pathlib import Path

import pytest

from ici.core.cmake import (
    BACKEND_CMAKE,
    BuildSession,
    ConfigureOptions,
    TestCaseResult,
    build,
    configure,
    parse_ctest_junit,
    parse_ctest_stdout,
    parse_qtest_xunit,
    run_tests,
)
from ici.core.context import BuildVariant
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
    VerificationSuiteResult,
)
from ici.core.runner import ProcessResult
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine
from ici.reporters.html import generate_html_report


def test_test_case_result_rejects_passed_without_execution():
    with pytest.raises(ValueError, match="not executed"):
        TestCaseResult("impossible", True, executed=False)


def test_ctest_junit_notrun_status_is_not_executed():
    results = parse_ctest_junit(
        '<testsuite name="ctest"><testcase name="disabled_case" status="notrun"/></testsuite>'
    )

    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].executed is False


def test_ctest_junit_explicit_skipped_case_is_not_executed():
    results = parse_ctest_junit(
        '<testsuite name="ctest"><testcase name="filtered_case">'
        '<skipped message="filtered by platform"/>'
        "</testcase></testsuite>"
    )

    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].executed is False
    assert "filtered by platform" in results[0].message


def test_ctest_stdout_not_run_disabled_case_is_not_executed():
    results = parse_ctest_stdout(
        "1/1 Test #1: disabled_case ................***Not Run (Disabled) 0.00 sec\n"
    )

    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].executed is False
    assert "Not Run (Disabled)" in results[0].message


def test_qtest_xunit_distinguishes_skip_xfail_xpass_and_unknown_states():
    results = parse_qtest_xunit(
        """<testsuite name="QtSuite">
  <testcase result="skip" name="resultSkip"/>
  <testcase result="pass" name="explicitSkip">
    <skipped message="disabled by fixture"/>
  </testcase>
  <testcase result="xfail" name="expectedFailure"/>
  <testcase result="xpass" name="unexpectedPass"/>
  <testcase result="mystery" name="unknownState"/>
</testsuite>"""
    )

    by_name = {result.name.rsplit("::", 1)[-1]: result for result in results}
    assert by_name["resultSkip"].passed is False
    assert by_name["resultSkip"].executed is False
    assert by_name["explicitSkip"].passed is False
    assert by_name["explicitSkip"].executed is False
    assert by_name["expectedFailure"].passed is True
    assert by_name["expectedFailure"].executed is True
    assert by_name["unexpectedPass"].passed is False
    assert by_name["unexpectedPass"].executed is True
    assert by_name["unknownState"].passed is False
    assert by_name["unknownState"].executed is True


def _cpp_project(root: Path, *, two_tests: bool = False) -> None:
    (root / "CMakeLists.txt").write_text("project(execution_state)\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        'name = "execution_state"\ntype = "cpp"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    src = root / "src"
    src.mkdir()
    (src / "library.cpp").write_text("int value() { return 1; }\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_pass.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    if two_tests:
        (tests / "test_skip.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")


def _configured_session(root: Path, *, suffix: str = "asan") -> BuildSession:
    return BuildSession(
        root=root,
        shadow=root / "build" / f"ici-cmake-{suffix}",
        backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt",
        reason="root CMakeLists.txt",
        configured=True,
    )


def _run_sanitizer_cases(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cases: list[TestCaseResult],
    *,
    required: bool,
) -> tuple[SanitizeEngine, EngineResult]:
    session = _configured_session(root)

    def run_cases(actual_session: BuildSession, env=None) -> list[TestCaseResult]:
        actual_session.tool_evidence.append(
            ToolEvidence(
                name="ctest",
                path="/usr/bin/ctest",
                argv=["/usr/bin/ctest", "--output-on-failure"],
                returncode=0 if all(case.passed or not case.executed for case in cases) else 1,
            )
        )
        return cases

    monkeypatch.setattr(
        "ici.engines.sanitize.adapter_configure",
        lambda _root, _options=None: session,
    )
    monkeypatch.setattr("ici.engines.sanitize.adapter_build", lambda _session: True)
    monkeypatch.setattr(
        "ici.engines.sanitize.adapter_run_tests",
        run_cases,
    )
    engine = SanitizeEngine(
        root,
        {"engines": {"sanitize": {"required": required}}},
    )
    return engine, engine.run()


@pytest.mark.parametrize(
    ("required", "expected_status", "expected_evidence", "target_status"),
    [
        (True, EngineStatus.ERROR, EvidenceState.NOT_RUN, EngineStatus.ERROR),
        (False, EngineStatus.SKIP, EvidenceState.ESTIMATED, EngineStatus.SKIP),
    ],
    ids=["required", "optional"],
)
def test_sanitizer_all_skipped_cases_have_no_measured_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    expected_status: EngineStatus,
    expected_evidence: EvidenceState,
    target_status: EngineStatus,
):
    _cpp_project(tmp_path)
    engine, result = _run_sanitizer_cases(
        tmp_path,
        monkeypatch,
        [TestCaseResult("test_pass", False, "disabled", executed=False)],
        required=required,
    )

    assert result.status is expected_status
    assert result.evidence is expected_evidence
    assert engine._measured_scopes == 0
    assert result.extra["sanitize_issues"] == 0
    assert len(result.targets) == 1
    assert result.targets[0].status is target_status


@pytest.mark.parametrize(
    ("required", "expected_status", "expected_evidence", "skip_status"),
    [
        (True, EngineStatus.ERROR, EvidenceState.NOT_RUN, EngineStatus.ERROR),
        (False, EngineStatus.WARN, EvidenceState.ESTIMATED, EngineStatus.SKIP),
    ],
    ids=["required", "optional"],
)
def test_sanitizer_mixed_pass_and_skip_keeps_skip_out_of_failure_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    expected_status: EngineStatus,
    expected_evidence: EvidenceState,
    skip_status: EngineStatus,
):
    _cpp_project(tmp_path)
    _engine, result = _run_sanitizer_cases(
        tmp_path,
        monkeypatch,
        [
            TestCaseResult("test_pass", True, executed=True),
            TestCaseResult("test_skip", False, "disabled", executed=False),
        ],
        required=required,
    )

    assert result.status is expected_status
    assert result.evidence is expected_evidence
    assert any(target.status is EngineStatus.PASS for target in result.targets)
    skipped = [target for target in result.targets if "test_skip" in target.target_name]
    assert len(skipped) == 1
    assert skipped[0].status is skip_status
    assert result.extra["sanitize_issues"] == 0


@pytest.mark.parametrize(
    ("required", "expected_status", "expected_evidence", "skip_status"),
    [
        (True, EngineStatus.ERROR, EvidenceState.NOT_RUN, EngineStatus.ERROR),
        (False, EngineStatus.FAIL, EvidenceState.ESTIMATED, EngineStatus.SKIP),
    ],
    ids=["required", "optional"],
)
def test_sanitizer_actual_failure_with_skip_preserves_failure_and_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    expected_status: EngineStatus,
    expected_evidence: EvidenceState,
    skip_status: EngineStatus,
):
    _cpp_project(tmp_path)
    _engine, result = _run_sanitizer_cases(
        tmp_path,
        monkeypatch,
        [
            TestCaseResult("test_fail", False, "AddressSanitizer diagnostic", executed=True),
            TestCaseResult("test_skip", False, "disabled", executed=False),
        ],
        required=required,
    )

    assert result.status is expected_status
    assert result.evidence is expected_evidence
    failed = [target for target in result.targets if "test_fail" in target.target_name]
    skipped = [target for target in result.targets if "test_skip" in target.target_name]
    assert len(failed) == 1 and failed[0].status is EngineStatus.FAIL
    assert len(skipped) == 1 and skipped[0].status is skip_status
    assert result.extra["sanitize_issues"] == 1


def test_test_engine_emits_skip_target_and_suite_skip_count_without_failed_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _cpp_project(tmp_path, two_tests=True)
    session = _configured_session(tmp_path, suffix="coverage")
    monkeypatch.setattr(
        "ici.engines.test.adapter_configure",
        lambda _root, _options=None: session,
    )
    monkeypatch.setattr("ici.engines.test.adapter_build", lambda _session: True)
    monkeypatch.setattr(
        "ici.engines.test.adapter_run_tests",
        lambda _session: [
            TestCaseResult("test_pass", True, executed=True),
            TestCaseResult("test_skip", False, "disabled", executed=False),
        ],
    )
    monkeypatch.setattr("ici.engines.test.adapter_collect_coverage", lambda _session: None)
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "coverage_required": False,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )
    monkeypatch.setattr(
        engine,
        "_measure_coverage",
        lambda _project_type, _has_failure: (100.0, 100.0, []),
    )

    result = engine.run()

    skipped = [target for target in result.targets if "test_skip" in target.target_name]
    assert len(skipped) == 1
    assert skipped[0].status is EngineStatus.SKIP
    assert not any(
        "test_skip" in target.target_name and target.status is EngineStatus.FAIL
        for target in result.targets
    )
    assert result.extra["passed_tests"] == 1
    assert result.extra["total_tests"] == 2
    assert result.extra["skipped_tests"] == 1
    assert sum(suite["skipped"] for suite in result.extra["test_suites"]) == 1
    assert sum(suite["failed"] for suite in result.extra["test_suites"]) == 0


def test_pytest_parser_preserves_skip_xfail_and_xpass_execution_semantics(tmp_path: Path):
    engine = TestEngine(tmp_path)
    targets = []

    passed, total, has_failure = engine._parse_pytest_stdout(
        "tests/test_states.py::test_pass PASSED\n"
        "tests/test_states.py::test_skip SKIPPED (platform)\n"
        "tests/test_states.py::test_expected XFAIL (known defect)\n"
        "tests/test_states.py::test_unexpected XPASS (fixed)\n",
        targets,
    )

    assert (passed, total, has_failure) == (2, 4, True)
    by_name = {target.target_name.rsplit("::", 1)[-1]: target for target in targets}
    assert by_name["test_pass"].status is EngineStatus.PASS
    assert by_name["test_skip"].status is EngineStatus.SKIP
    assert by_name["test_expected"].status is EngineStatus.PASS
    assert by_name["test_unexpected"].status is EngineStatus.FAIL


def test_pytest_summary_only_skip_is_retained_as_not_executed(tmp_path: Path):
    engine = TestEngine(tmp_path)
    targets = []

    passed, total, has_failure = engine._parse_pytest_stdout(
        "===================== 3 skipped in 0.01s =====================\n",
        targets,
    )

    assert (passed, total, has_failure) == (0, 3, False)
    assert len(targets) == 1
    assert targets[0].status is EngineStatus.SKIP
    assert targets[0].target_name == "[Python] Skipped (3)"


def test_pytest_summary_combines_failures_and_collection_errors(tmp_path: Path):
    engine = TestEngine(tmp_path)
    targets = []

    passed, total, has_failure = engine._parse_pytest_stdout(
        "======= 2 passed, 1 failed, 3 errors, 4 skipped, 5 xfailed, 6 xpassed =======\n",
        targets,
    )

    assert (passed, total, has_failure) == (7, 21, True)
    by_name = {target.target_name: target for target in targets}
    assert by_name["[Python] Failed (4)"].metrics["test_cases"] == 4
    assert by_name["[Python] XPass (6)"].status is EngineStatus.FAIL


def test_pytest_summary_uses_last_authoritative_collection_error_count(tmp_path: Path):
    engine = TestEngine(tmp_path)
    targets = []

    passed, total, has_failure = engine._parse_pytest_stdout(
        "collected 0 items / 1 error\n"
        "!!!!!!!! Interrupted: 1 error during collection !!!!!!!!\n"
        "===================== 1 error in 0.10s =====================\n",
        targets,
    )

    assert (passed, total, has_failure) == (0, 1, True)
    assert targets[0].target_name == "[Python] Failed (1)"
    assert targets[0].metrics["test_cases"] == 1


@pytest.mark.parametrize(
    ("summary", "returncode", "expected_status"),
    [
        ("1 xfailed in 0.01s", 0, EngineStatus.PASS),
        ("1 xpassed in 0.01s", 1, EngineStatus.FAIL),
    ],
)
def test_summary_only_expected_failure_states_are_parseable_evidence(
    tmp_path: Path,
    summary: str,
    returncode: int,
    expected_status: EngineStatus,
):
    engine = TestEngine(tmp_path)
    targets = []

    passed, total, has_failure = engine._parse_pytest_result(
        ProcessResult(returncode, summary, "", 0.01), targets
    )

    assert total == 1
    assert passed == int(expected_status is EngineStatus.PASS)
    assert has_failure is (expected_status is EngineStatus.FAIL)
    assert targets[0].status is expected_status
    assert engine._tool_errors == []


def test_unittest_parser_preserves_skip_and_expected_failure_states(tmp_path: Path):
    engine = TestEngine(tmp_path)
    targets = []
    result = ProcessResult(
        1,
        "test_skip (suite.Case) ... skipped 'platform'\n"
        "test_expected (suite.Case) ... expected failure\n"
        "test_unexpected (suite.Case) ... unexpected success\n",
        "",
        0.01,
    )

    passed, total, has_failure = engine._parse_unittest_stdout(result, targets)

    assert (passed, total, has_failure) == (1, 3, True)
    assert [target.status for target in targets] == [
        EngineStatus.SKIP,
        EngineStatus.PASS,
        EngineStatus.FAIL,
    ]


def test_actual_failure_takes_precedence_over_all_skipped_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "required": False,
                    "coverage_required": False,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )

    def fake_project_tests(_project_type, targets):
        targets.extend(
            [
                InspectionTarget(
                    file_path="tests/test_skip.py",
                    start_line=1,
                    target_name="[Python] skipped",
                    status=EngineStatus.SKIP,
                    message="not executed",
                ),
                InspectionTarget(
                    file_path="tests",
                    start_line=1,
                    target_name="[C++] Tests",
                    status=EngineStatus.FAIL,
                    message="No tests collected",
                ),
            ]
        )
        return 0, 1, True

    monkeypatch.setattr(engine, "_run_project_tests", fake_project_tests)
    monkeypatch.setattr(engine, "_measure_coverage", lambda *_args: (0.0, 0.0, []))

    result = engine.run()

    assert result.status is EngineStatus.FAIL
    assert "every collected test was skipped" not in result.summary


@pytest.mark.parametrize(
    ("required", "expected_status", "expected_evidence"),
    [
        (True, EngineStatus.ERROR, EvidenceState.NOT_RUN),
        (False, EngineStatus.SKIP, EvidenceState.ESTIMATED),
    ],
    ids=["required", "optional"],
)
def test_real_pytest_all_skipped_never_becomes_clean_evidence(
    tmp_path: Path,
    required: bool,
    expected_status: EngineStatus,
    expected_evidence: EvidenceState,
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_skip.py").write_text(
        "import pytest\n\n@pytest.mark.skip(reason='platform')\ndef test_skipped():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "ici.toml").write_text(
        'name = "pytest_skip"\ntype = "python"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "required": required,
                    "coverage_required": False,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )

    result = engine.run()

    assert result.status is expected_status
    assert result.evidence is expected_evidence
    assert result.extra["passed_tests"] == 0
    assert result.extra["total_tests"] == 1
    assert result.extra["skipped_tests"] == 1
    assert "every collected test was skipped" in result.summary
    assert any(target.status is EngineStatus.SKIP for target in result.targets)


def test_html_renders_skipped_test_case_as_skip(tmp_path: Path):
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.WARN,
        summary="1/2 Tests Passed",
        extra={
            "passed_tests": 1,
            "total_tests": 2,
            "branch_coverage": 100.0,
            "function_coverage": 100.0,
            "tem_score": 2.5,
            "test_suites": [
                {
                    "file": "tests/test_skip.cpp",
                    "passed": 1,
                    "failed": 0,
                    "skipped": 1,
                    "total": 2,
                    "tests": [
                        {"name": "[C++] test_pass", "status": "PASS", "message": "ok"},
                        {
                            "name": "[C++] test_skip",
                            "status": "SKIP",
                            "message": "Execution skipped: disabled",
                        },
                    ],
                }
            ],
        },
    )
    suite = VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[result])
    output = tmp_path / "report.html"

    generate_html_report(suite, output, project_name="Skip Rendering", base_dir=tmp_path)
    page = output.read_text(encoding="utf-8")

    assert "1/2 Passed · 1 Skipped" in page
    assert ">SKIP</span>" in page
    assert "Execution skipped: disabled" in page
    assert "#f59e0b" in page


def test_real_cmake_disabled_test_is_not_sanitizer_measurement(tmp_path: Path):
    missing = [tool for tool in ("cmake", "ctest", "g++") if shutil.which(tool) is None]
    if missing:
        message = f"build adapter tools unavailable: {', '.join(missing)}"
        if os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") == "1":
            pytest.fail(message)
        pytest.skip(message)

    root = tmp_path / "cmake-disabled"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_pass.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "CMakeLists.txt").write_text(
        """cmake_minimum_required(VERSION 3.16)
project(execution_state LANGUAGES CXX)
enable_testing()
add_executable(test_pass tests/test_pass.cpp)
add_test(NAME test_pass COMMAND test_pass)
add_test(NAME test_disabled COMMAND test_pass)
set_tests_properties(test_disabled PROPERTIES DISABLED TRUE)
""",
        encoding="utf-8",
    )
    (root / "ici.toml").write_text(
        'name = "execution_state"\ntype = "cpp"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    session = configure(root, ConfigureOptions(BuildVariant.RELEASE))
    assert session.configured, session.errors
    assert build(session), session.errors
    by_name = {case.name: case for case in run_tests(session)}

    assert by_name["test_pass"].passed is True
    assert by_name["test_pass"].executed is True
    assert by_name["test_disabled"].passed is False
    assert by_name["test_disabled"].executed is False

    result = SanitizeEngine(root).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert any(target.status is EngineStatus.PASS for target in result.targets)
    assert any(
        "test_disabled" in target.target_name and target.status is EngineStatus.ERROR
        for target in result.targets
    )
