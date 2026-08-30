"""End-to-end detection checks for the C++ engines.

ici's unit tests exercise the engines against synthetic inputs built inside the
test, which verifies the parsing but not the path a real project takes. These
fixtures are small, deliberately broken C++ projects under
``examples/cpp-fixtures/``; each one is run through the engine that should
notice it.

The fixtures live under ``examples/`` rather than ``tests/`` on purpose. ici's
own test engine globs ``tests/**/*.cpp`` from the repository root, so a C++ file
placed there would be compiled and run as one of ici's own tests during
self-verification. ``examples/`` is not a default source directory either, so
the fixtures stay invisible to the self-verify run.

``clean_baseline`` is the counterweight: every engine must stay quiet on it, so
a detector that starts firing everywhere fails here rather than in a user's
report.
"""

from pathlib import Path

import pytest

from ici.core.cmake import select_backend
from ici.core.models import EngineStatus
from ici.engines.complexity import ComplexityEngine
from ici.engines.cpp_text import defines_main
from ici.engines.cycle import CycleEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.line import LineCountEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "cpp-fixtures"

_TEXT_ENGINES = (
    CycleEngine,
    ComplexityEngine,
    DuplicateEngine,
    ExceptionSafetyEngine,
    LineCountEngine,
)


def _run(engine_cls, fixture: str):
    root = FIXTURES / fixture
    assert root.is_dir(), f"missing fixture: {root}"
    return engine_cls(root).run()


def _findings(result) -> list:
    return [t for t in result.targets if t.status != EngineStatus.PASS]


def _messages(result) -> str:
    return " ".join(t.message for t in _findings(result))


def test_cycle_engine_detects_a_header_cycle():
    result = _run(CycleEngine, "cycle_pair")
    assert result.status == EngineStatus.WARN
    assert "a.hpp" in _messages(result)
    assert "b.hpp" in _messages(result)


def test_complexity_engine_detects_a_hot_function():
    result = _run(ComplexityEngine, "complexity_hot")
    assert result.status == EngineStatus.WARN
    findings = _findings(result)
    assert findings, "a function over the threshold should be reported"
    assert findings[0].target_name.startswith("classify")
    assert findings[0].metrics["complexity"] > 15


def test_duplicate_engine_detects_a_type_2_clone():
    """The two functions differ only in identifiers and literals."""
    result = _run(DuplicateEngine, "clone_pair")
    assert result.status == EngineStatus.WARN
    assert _findings(result), "a renamed copy is still a clone"


def test_exception_engine_detects_a_throwing_destructor():
    result = _run(ExceptionSafetyEngine, "dtor_throw")
    assert result.status == EngineStatus.FAIL
    assert "destructor" in _messages(result).lower()


def test_line_engine_detects_an_oversized_file():
    result = _run(LineCountEngine, "oversized_file")
    assert result.status == EngineStatus.WARN
    assert "500" in _messages(result)


@pytest.mark.parametrize("engine_cls", _TEXT_ENGINES, ids=lambda c: c.__name__)
def test_clean_baseline_produces_no_findings(engine_cls):
    """A detector that fires here has gained a false positive."""
    result = _run(engine_cls, "clean_baseline")
    assert result.status == EngineStatus.PASS
    assert _findings(result) == []


def test_sanitize_detects_a_heap_overflow():
    """Compiles and runs the fixture under ASan/UBSan, so it needs a compiler."""
    result = _run(SanitizeEngine, "asan_overflow")
    if result.status == EngineStatus.ERROR:
        pytest.skip(f"sanitizer unavailable: {result.summary}")
    assert result.status == EngineStatus.FAIL
    assert _findings(result), "reading past the end must be reported"


def _cwd_fixture_project(tmp_path: Path) -> Path:
    """A project whose C++ test reads a data file by a project-relative path.

    This is the ordinary way to write such a test, and it used to depend on
    which engine launched the binary: the test engine ran it from build/tests
    when gcov was installed and from the project root when it was not, and
    sanitize ran it from a temporary directory outside the project entirely.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "data").mkdir(parents=True)
    (tmp_path / "tests" / "data" / "fixture.txt").write_text("expected-payload\n", encoding="utf-8")
    (tmp_path / "src" / "lib.hpp").write_text("#pragma once\nint twice(int a);\n", encoding="utf-8")
    (tmp_path / "src" / "lib.cpp").write_text(
        '#include "lib.hpp"\nint twice(int a) { return a * 2; }\n', encoding="utf-8"
    )
    (tmp_path / "tests" / "test_reads_fixture.cpp").write_text(
        '#include "../src/lib.hpp"\n'
        "\n"
        "#include <fstream>\n"
        "#include <string>\n"
        "\n"
        "int main() {\n"
        '    std::ifstream in("tests/data/fixture.txt");\n'
        "    if (!in) {\n"
        "        return 1;\n"
        "    }\n"
        "    std::string line;\n"
        "    std::getline(in, line);\n"
        '    return (line == "expected-payload" && twice(2) == 4) ? 0 : 1;\n'
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_fixture_project_stays_on_the_generic_gxx_path(tmp_path: Path):
    """This fixture is the regression witness for the non-adapter path.

    viewer/ used to fill that role, but it has a root CMakeLists.txt and now
    goes through the CMake adapter. If this project ever grew a root build
    descriptor too, the generic g++ path would stop being exercised anywhere
    and nothing would say so — the shape of C-6, where lint sat unrun in CI
    behind a green gate.
    """
    root = _cwd_fixture_project(tmp_path)
    assert select_backend(root).kind is None


def test_cpp_tests_run_from_the_project_root(tmp_path: Path):
    """Both engines must launch a test binary from the same place."""
    root = _cwd_fixture_project(tmp_path)

    test_result = TestEngine(root).run()
    if test_result.status == EngineStatus.ERROR:
        pytest.skip(f"compiler unavailable: {test_result.summary}")
    assert test_result.status == EngineStatus.PASS, test_result.summary

    sanitize_result = SanitizeEngine(root).run()
    if sanitize_result.status == EngineStatus.ERROR:
        pytest.skip(f"sanitizer unavailable: {sanitize_result.summary}")
    assert sanitize_result.status == EngineStatus.PASS, sanitize_result.summary


@pytest.mark.parametrize("entry_name", ["main.cpp", "gui_main.cpp", "cli.cpp"])
def test_entry_points_are_excluded_by_the_same_rule_everywhere(tmp_path: Path, entry_name: str):
    """test and sanitize must agree on what an entry point is.

    They did not. The test engine excluded any name containing "main.cpp" while
    sanitize matched the name exactly, so gui_main.cpp was linked by one and not
    the other — and a second main() makes the link fail. cli.cpp defines main()
    under a name neither substring rule would ever catch.

    Engines disagreeing about scope is the shape of B-1 and C-9. One rule now.
    """
    entry = tmp_path / entry_name
    entry.write_text("int main() { return 0; }\n", encoding="utf-8")
    plain = tmp_path / "helper.cpp"
    plain.write_text("int twice(int a) { return a * 2; }\n", encoding="utf-8")

    assert defines_main(entry) is True
    assert defines_main(plain) is False
