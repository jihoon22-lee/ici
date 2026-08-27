"""Tests for Complexity and Exception Safety Engines."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.complexity import ComplexityEngine
from ici.engines.exception import ExceptionSafetyEngine


def test_complexity_engine(tmp_python_project: Path):
    engine = ComplexityEngine(tmp_python_project)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert res.score is not None
    assert len(res.targets) > 0


def test_process_validation_helpers_stay_below_complexity_limit():
    project_root = Path(__file__).resolve().parents[1]
    _, targets = ComplexityEngine(project_root)._analyze_python_complexity(15, 25, 4)

    task4_paths = {
        "src/ici/core/runner.py",
        "src/ici/engines/lint.py",
        "src/ici/engines/test.py",
        "src/ici/engines/type_check.py",
    }
    offenders = [
        (target.file_path, target.target_name, target.metrics["complexity"])
        for target in targets
        if target.file_path in task4_paths and target.metrics.get("complexity", 0) > 25
    ]

    assert offenders == []


def test_coverage_validation_helpers_stay_below_complexity_limit():
    project_root = Path(__file__).resolve().parents[1]
    _, targets = ComplexityEngine(project_root)._analyze_python_complexity(15, 25, 4)

    task5_paths = {
        "src/ici/engines/coverage_support.py",
        "src/ici/engines/test.py",
    }
    offenders = [
        (target.file_path, target.target_name, target.metrics["complexity"])
        for target in targets
        if target.file_path in task5_paths and target.metrics.get("complexity", 0) > 25
    ]

    assert offenders == []


def test_process_runner_has_no_silent_cleanup_exceptions():
    project_root = Path(__file__).resolve().parents[1]
    targets = []
    ExceptionSafetyEngine(project_root)._check_python_exceptions(targets)

    silent_runner_errors = [
        target
        for target in targets
        if target.file_path == "src/ici/core/runner.py" and target.target_name == "ErrorSwallowing"
    ]

    assert silent_runner_errors == []


def test_exception_safety_detects_swallowed_error(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bad_error.py"
    bad_py.write_text(
        """def dangerous():
    try:
        x = 1 / 0
    except Exception:
        pass
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("ErrorSwallowing" in t.target_name for t in res.targets)


def test_exception_safety_detects_bare_except(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bare.py"
    bad_py.write_text(
        """def bad_bare():
    try:
        x = 1
    except:
        print("error")
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("BareExcept" in t.target_name for t in res.targets)


def _cpp_targets(tmp_path: Path, source: str) -> dict:
    """Run the C++ path of the complexity engine over one file."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "sample.cpp").write_text(source, encoding="utf-8")
    result = ComplexityEngine(tmp_path).run()
    return {t.target_name: t for t in result.targets}


def test_cpp_single_line_definition_closes_itself(tmp_path: Path):
    """A body on the signature line must not absorb the functions after it."""
    targets = _cpp_targets(
        tmp_path,
        "void trivial() { return; }\n"
        "\n"
        "bool busy(int a, int b) {\n"
        "    if (a > 0) {\n"
        "        while (b > 0) {\n"
        "            --b;\n"
        "        }\n"
        "    }\n"
        "    return a > b;\n"
        "}\n",
    )
    assert set(targets) == {"trivial()", "busy()"}
    assert targets["trivial()"].start_line == 1
    assert targets["trivial()"].end_line == 1
    assert targets["trivial()"].metrics["complexity"] == 1
    # The loop and branch belong to busy(), not to the one-liner above it.
    assert targets["busy()"].start_line == 3
    assert targets["busy()"].metrics["complexity"] == 3


def test_cpp_control_flow_is_not_reported_as_a_function(tmp_path: Path):
    """`for (int i = ...)` has parens, a brace and a type, but is not a definition."""
    targets = _cpp_targets(
        tmp_path,
        "int total(int n) {\n"
        "    int sum = 0;\n"
        "    for (int i = 0; i < n; ++i) {\n"
        "        sum += i;\n"
        "    }\n"
        "    return sum;\n"
        "}\n",
    )
    assert set(targets) == {"total()"}
    assert targets["total()"].end_line == 7


def test_cpp_multi_line_signature_is_detected(tmp_path: Path):
    """A signature wrapped across lines used to be invisible to the scanner."""
    targets = _cpp_targets(
        tmp_path,
        "void first() { return; }\n"
        "\n"
        "void wrapped(const int& a,\n"
        "             const int& b) {\n"
        "    if (a && b) {\n"
        "        return;\n"
        "    }\n"
        "}\n",
    )
    assert "wrapped()" in targets
    assert targets["wrapped()"].start_line == 3
    # The if and the && are both decision points.
    assert targets["wrapped()"].metrics["complexity"] == 3


def test_cpp_literals_do_not_create_decision_points(tmp_path: Path):
    """Braces and operators inside strings and comments must not be counted."""
    targets = _cpp_targets(
        tmp_path,
        'const char* text() { return "if (a && b) {"; }  // while (x) {\n',
    )
    assert set(targets) == {"text()"}
    assert targets["text()"].metrics["complexity"] == 1


def test_cpp_nesting_depth_counts_blocks_inside_the_body(tmp_path: Path):
    """Three nested ifs are depth 3 — the function's own braces are not nesting."""
    targets = _cpp_targets(
        tmp_path,
        "void deep(int a) {\n"
        "    if (a) {\n"
        "        if (a) {\n"
        "            if (a) {\n"
        "                return;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n",
    )
    assert targets["deep()"].metrics["nesting"] == 3


def test_cpp_and_python_agree_on_nesting_depth(tmp_path: Path):
    """The same shape in either language must produce the same number.

    C++ used to count the function body as a level, so identical code read one
    deeper than Python and the shared warn_nesting threshold was quietly
    stricter for C++.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "same.cpp").write_text(
        "int threeLevels(int a, int b) {\n"
        "    if (a) {\n"
        "        while (b) {\n"
        "            for (int i = 0; i < 3; ++i) {\n"
        "                --b;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    return a;\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "same.py").write_text(
        "def three_levels(a, b):\n"
        "    if a:\n"
        "        while b:\n"
        "            for i in range(3):\n"
        "                b -= 1\n"
        "    return a\n",
        encoding="utf-8",
    )
    by_file = {t.file_path: t for t in ComplexityEngine(tmp_path).run().targets}
    assert by_file["src/same.cpp"].metrics["nesting"] == 3
    assert by_file["src/same.py"].metrics["nesting"] == 3


def test_cpp_function_body_alone_is_zero_nesting(tmp_path: Path):
    targets = _cpp_targets(tmp_path, "int flat(int a) {\n    return a + 1;\n}\n")
    assert targets["flat()"].metrics["nesting"] == 0
