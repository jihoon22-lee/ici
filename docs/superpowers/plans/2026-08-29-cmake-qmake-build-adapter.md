# CMake/qmake 빌드 어댑터 구현 계획 (ici)

> **I0-1 상태 보정 (2026-08-31): 완료·역사 보존.** 이 계획의 CMake/qmake adapter
> 구현과 Task 11 릴리스는 [ici PR #76](https://github.com/jihoon22-lee/ici/pull/76)과
> `v0.6.0`으로 완료됐다. 아래 체크박스는 구현 당시의 단계별 레시피와 회귀 근거를
> 보존하며 활성 작업을 뜻하지 않는다. toy-projects의 후속 검증은 [PR #10](https://github.com/jihoon22-lee/toy-projects/pull/10) 이후
> [PR #15](https://github.com/jihoon22-lee/toy-projects/pull/15)의 현재 환경 기록과
> toy master plan을 따른다. 남은 adapter/toolchain 정밀화는 ici master plan I2·I3·I5·I7의
> 범위다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ici` 가 프로젝트의 실제 빌드 시스템(CMake/CTest, qmake/Make)으로 configure·build·test 를 수행해, `Q_OBJECT` 클래스의 단위 테스트와 CMake/qmake 프로젝트의 `ici build` 를 가능하게 한다.

**Architecture:** `src/ici/core/cmake.py` 에 백엔드 프로토콜과 두 구현을 두고 `build`·`test` 엔진이 공유한다. 백엔드는 프로젝트 루트의 빌드 디스크립터로 고른다 — 지금 A-2 가 거부하는 조건이 그대로 어댑터 진입 조건이 된다. 디스크립터가 없으면 기존 g++ 경로가 변경 없이 유지된다.

**Tech Stack:** Python 3.10, stdlib `xml.etree.ElementTree`, `subprocess`(`ici.core.runner.run_process`), cmake/ctest, qmake6/make, gcov, pytest

**Spec:** [`docs/superpowers/specs/2026-08-29-cmake-qmake-build-adapter-design.md`](../specs/2026-08-29-cmake-qmake-build-adapter-design.md)

**이 계획은 저장소 두 곳 중 `ici` 만 다룬다.** `toy-projects` 쪽(loglens→CMake, diskmap→qmake)은 별도 계획이며, **이 계획의 릴리스(Task 11)가 선행 조건**이다. toy-projects CI 는 체크섬 검증된 릴리스 에셋을 내려받으므로 소스 빌드로 앞당길 수 없다.

## Global Constraints

- **Python 3.10 하한.** `tomllib` 금지(`tomli` 사용, ruff TID251 로 강제), `match-case`·`ExceptionGroup` 등 3.11+ 문법 금지
- **순수 파이썬 의존성만.** 새 서드파티 의존성을 추가하지 않는다 — XML 은 stdlib `xml.etree.ElementTree` 로 파싱한다
- **모든 외부 명령은 argv 배열이며 shell 을 거치지 않는다.** `ici.core.runner.run_process` 만 사용한다
- **`sudo` 또는 루트 권한을 요구하지 않는다**
- **위치 추적 필수.** 모든 `InspectionTarget` 은 파일 경로와 라인 번호를 갖는다 (`AGENTS.md` 5-1)
- **결과·증거 계약** (스펙 §3.6): configure 실패 → `ERROR`, build 실패 → `FAIL`, 테스트 실패 → `FAIL`, 빌드 도구 부재 → `ERROR`(`NOT_APPLICABLE` 아님), 커버리지 미측정 → `ESTIMATED`(게이트 차단)
- **shadow 빌드 디렉터리**: `build/ici-cmake`, `build/ici-qmake`. `build/` 는 이미 gitignore 된다
- **품질 게이트**: `uv run --python 3.10 pytest`, `uvx ruff check .`, `uvx ruff format --check .`, `./scripts/build-pyz.sh`, `./scripts/smoke.sh`
- **브랜치**: `feat/build-adapter` 에서 작업하고 PR 로 병합한다. `main` 직접 작업 금지 (`AGENTS.md` §1)

---

### Task 1: 백엔드 선택

**Files:**
- Create: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `BACKEND_CMAKE: str`, `BACKEND_QMAKE: str`, `BackendChoice(kind: str | None, reason: str, descriptor: str)`, `select_backend(root: Path) -> BackendChoice`

- [ ] **Step 1: Write the failing test**

`tests/test_build_adapter.py` 를 만든다.

```python
"""Tests for the CMake/qmake build adapter."""

from pathlib import Path

from ici.core.cmake import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    select_backend,
)


def test_root_cmakelists_selects_cmake(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_CMAKE
    assert choice.descriptor == "CMakeLists.txt"
    assert "CMakeLists.txt" in choice.reason


def test_root_pro_file_selects_qmake(tmp_path):
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_QMAKE
    assert choice.descriptor == "app.pro"


def test_cmake_wins_when_both_present(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_CMAKE
    # The reason must say the other candidate was seen and passed over, or the
    # report cannot explain why qmake did not run.
    assert "app.pro" in choice.reason


def test_makefile_only_selects_nothing(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind is None
    assert "Makefile" in choice.reason


def test_no_descriptor_selects_nothing(tmp_path):
    choice = select_backend(tmp_path)
    assert choice.kind is None
    assert choice.descriptor == ""


def test_subdirectory_descriptor_is_ignored(tmp_path):
    gui = tmp_path / "src" / "gui"
    gui.mkdir(parents=True)
    (gui / "CMakeLists.txt").write_text("project(gui)\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind is None


def test_symlinked_descriptor_is_ignored(tmp_path):
    real = tmp_path / "elsewhere.txt"
    real.write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").symlink_to(real)
    choice = select_backend(tmp_path)
    assert choice.kind is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ici.core.cmake'`

- [ ] **Step 3: Write minimal implementation**

`src/ici/core/cmake.py` 를 만든다.

```python
"""CMake/CTest and qmake/Make build adapters.

The build and test engines share this module. Both need a configure step, and
running configure from each engine separately would either configure the same
shadow tree twice or drift on flags. Scope rules differing per engine is a
problem this repository has already hit twice (B-1, C-9), so the new build path
starts in one place.
"""

from dataclasses import dataclass
from pathlib import Path

BACKEND_CMAKE = "cmake"
BACKEND_QMAKE = "qmake"

# Only the project root is inspected. Descriptors in subdirectories do not
# select a backend, which is what keeps projects that have not been converted
# yet (a CMakeLists.txt under src/gui) on their existing g++ path.
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")


@dataclass(frozen=True)
class BackendChoice:
    """Which backend runs, and why. The reason is recorded as tool evidence."""

    kind: str | None
    reason: str
    descriptor: str = ""


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def select_backend(root: Path) -> BackendChoice:
    """Pick a build backend from the descriptor at the project root."""

    cmake_file = root / "CMakeLists.txt"
    has_cmake = _is_real_file(cmake_file)
    pro_files = sorted(p for p in root.glob("*.pro") if _is_real_file(p))
    makefiles = [name for name in _MAKEFILE_NAMES if _is_real_file(root / name)]

    if has_cmake:
        reason = "CMakeLists.txt at the project root selected the CMake backend"
        if pro_files:
            reason += f"; {pro_files[0].name} was present and passed over"
        return BackendChoice(BACKEND_CMAKE, reason, "CMakeLists.txt")

    if pro_files:
        return BackendChoice(
            BACKEND_QMAKE,
            f"{pro_files[0].name} at the project root selected the qmake backend",
            pro_files[0].name,
        )

    if makefiles:
        return BackendChoice(
            None,
            f"{makefiles[0]} at the project root has no adapter; "
            "only CMake and qmake are supported",
            makefiles[0],
        )

    return BackendChoice(None, "No build descriptor at the project root", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): select a build backend from the root descriptor"
```

---

### Task 2: CMake 명령 구성과 버전 게이트

**Files:**
- Modify: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: `BACKEND_CMAKE` (Task 1)
- Produces: `shadow_dir(root: Path, backend: str) -> Path`, `parse_cmake_version(text: str) -> tuple[int, int] | None`, `cmake_configure_argv(cmake_bin: str, root: Path, shadow: Path) -> list[str]`, `cmake_build_argv(cmake_bin: str, shadow: Path) -> list[str]`, `cmake_test_argv(ctest_bin: str, shadow: Path, version: tuple[int, int] | None) -> tuple[list[str], Path | None]`

- [ ] **Step 1: Write the failing test**

`tests/test_build_adapter.py` 에 추가한다.

```python
from ici.core.cmake import (
    cmake_build_argv,
    cmake_configure_argv,
    cmake_test_argv,
    parse_cmake_version,
    shadow_dir,
)


def test_shadow_dir_is_under_build(tmp_path):
    assert shadow_dir(tmp_path, BACKEND_CMAKE) == tmp_path / "build" / "ici-cmake"
    assert shadow_dir(tmp_path, BACKEND_QMAKE) == tmp_path / "build" / "ici-qmake"


def test_parse_cmake_version():
    assert parse_cmake_version("cmake version 3.22.1\n\nCMake suite...") == (3, 22)
    assert parse_cmake_version("cmake version 4.2.3") == (4, 2)
    assert parse_cmake_version("nonsense") is None


def test_cmake_configure_injects_coverage(tmp_path):
    argv = cmake_configure_argv("/usr/bin/cmake", tmp_path, tmp_path / "build/ici-cmake")
    assert argv[0] == "/usr/bin/cmake"
    assert "-S" in argv and "-B" in argv
    # Debug gives -O0 -g. Optimised builds smear gcov's line and branch mapping,
    # which is what the TEM score stands on.
    assert "-DCMAKE_BUILD_TYPE=Debug" in argv
    assert "-DCMAKE_CXX_FLAGS=--coverage" in argv
    assert "-DCMAKE_EXE_LINKER_FLAGS=--coverage" in argv


def test_cmake_build_is_parallel(tmp_path):
    argv = cmake_build_argv("/usr/bin/cmake", tmp_path / "build/ici-cmake")
    assert argv == ["/usr/bin/cmake", "--build", str(tmp_path / "build/ici-cmake"), "--parallel"]


def test_ctest_uses_junit_on_new_cmake(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 21))
    assert "--test-dir" in argv
    assert "--output-junit" in argv
    assert junit == shadow / "ici-ctest.xml"


def test_ctest_drops_junit_on_cmake_320(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 20))
    assert "--test-dir" in argv
    assert "--output-junit" not in argv
    assert junit is None


def test_ctest_drops_test_dir_on_old_cmake(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 19))
    assert "--test-dir" not in argv
    assert junit is None


def test_ctest_unknown_version_is_most_conservative(tmp_path):
    argv, junit = cmake_test_argv("/usr/bin/ctest", tmp_path / "s", None)
    assert "--test-dir" not in argv
    assert junit is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'shadow_dir'`

- [ ] **Step 3: Write minimal implementation**

`src/ici/core/cmake.py` 에 추가한다. `import re` 를 파일 상단 import 에 더한다.

```python
# --test-dir arrived in CMake 3.20 and --output-junit in 3.21. The roadmap
# treats RHEL 7.9 as a target runtime, so an old ctest cannot be assumed away.
_CTEST_TEST_DIR_MIN = (3, 20)
_CTEST_JUNIT_MIN = (3, 21)

_CMAKE_VERSION_RE = re.compile(r"cmake version (\d+)\.(\d+)")


def shadow_dir(root: Path, backend: str) -> Path:
    """Build directory ici owns. Never the project's own build tree."""

    return root / "build" / f"ici-{backend}"


def parse_cmake_version(text: str) -> tuple[int, int] | None:
    match = _CMAKE_VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def cmake_configure_argv(cmake_bin: str, root: Path, shadow: Path) -> list[str]:
    return [
        cmake_bin,
        "-S",
        str(root),
        "-B",
        str(shadow),
        "-DCMAKE_BUILD_TYPE=Debug",
        "-DCMAKE_CXX_FLAGS=--coverage",
        "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
    ]


def cmake_build_argv(cmake_bin: str, shadow: Path) -> list[str]:
    return [cmake_bin, "--build", str(shadow), "--parallel"]


def cmake_test_argv(
    ctest_bin: str, shadow: Path, version: tuple[int, int] | None
) -> tuple[list[str], Path | None]:
    """Return the ctest argv and the JUnit path, or None when stdout must be parsed."""

    argv = [ctest_bin, "--output-on-failure"]
    if version is not None and version >= _CTEST_TEST_DIR_MIN:
        argv.extend(["--test-dir", str(shadow)])
    if version is not None and version >= _CTEST_JUNIT_MIN:
        junit = shadow / "ici-ctest.xml"
        argv.extend(["--output-junit", str(junit)])
        return argv, junit
    return argv, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): construct cmake and ctest commands with version gates"
```

---

### Task 3: qmake 명령 구성

**Files:**
- Modify: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: `shadow_dir` (Task 2)
- Produces: `qmake_configure_argv(qmake_bin: str, pro_file: Path) -> list[str]`, `qmake_build_argv(make_bin: str, jobs: int) -> list[str]`, `qmake_test_argv(make_bin: str) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
from ici.core.cmake import qmake_build_argv, qmake_configure_argv, qmake_test_argv


def test_qmake_configure_injects_coverage(tmp_path):
    pro = tmp_path / "app.pro"
    argv = qmake_configure_argv("/usr/bin/qmake6", pro)
    assert argv[0] == "/usr/bin/qmake6"
    assert str(pro) in argv
    # qmake uses its own flag variables; CMAKE_CXX_FLAGS has no effect here.
    assert "QMAKE_CXXFLAGS+=--coverage" in argv
    assert "QMAKE_LFLAGS+=--coverage" in argv


def test_qmake_build_is_parallel():
    assert qmake_build_argv("/usr/bin/make", 4) == ["/usr/bin/make", "--jobs=4"]


def test_qmake_build_rejects_bad_jobs():
    # A zero or negative job count would make GNU make spawn unbounded jobs.
    assert qmake_build_argv("/usr/bin/make", 0) == ["/usr/bin/make", "--jobs=1"]
    assert qmake_build_argv("/usr/bin/make", -3) == ["/usr/bin/make", "--jobs=1"]


def test_qmake_test_requests_xunit_xml():
    argv = qmake_test_argv("/usr/bin/make")
    assert argv[:2] == ["/usr/bin/make", "check"]
    # CONFIG += testcase forwards TESTARGS to each QtTest binary.
    assert "TESTARGS=-xunitxml" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'qmake_configure_argv'`

- [ ] **Step 3: Write minimal implementation**

```python
def qmake_configure_argv(qmake_bin: str, pro_file: Path) -> list[str]:
    """qmake runs with the shadow directory as its cwd; the .pro path is absolute."""

    return [
        qmake_bin,
        str(pro_file),
        "QMAKE_CXXFLAGS+=--coverage",
        "QMAKE_LFLAGS+=--coverage",
    ]


def qmake_build_argv(make_bin: str, jobs: int) -> list[str]:
    return [make_bin, f"--jobs={max(1, jobs)}"]


def qmake_test_argv(make_bin: str) -> list[str]:
    """`CONFIG += testcase` generates the check target and forwards TESTARGS."""

    return [make_bin, "check", "TESTARGS=-xunitxml"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): construct qmake and make commands"
```

---

### Task 4: 테스트 결과 파싱

**Files:**
- Modify: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: 없음
- Produces: `TestCaseResult(name: str, passed: bool, message: str)`, `parse_ctest_junit(xml_text: str) -> list[TestCaseResult]`, `parse_ctest_stdout(text: str) -> list[TestCaseResult]`, `parse_qtest_xunit(text: str) -> list[TestCaseResult]`

- [ ] **Step 1: Write the failing test**

```python
from ici.core.cmake import parse_ctest_junit, parse_ctest_stdout, parse_qtest_xunit

_CTEST_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="ctest" tests="3">
  <testcase name="test_ring_buffer" classname="ctest" time="0.01"/>
  <testcase name="test_log_model" classname="ctest" time="0.02">
    <failure message="row count mismatch">expected 3 got 2</failure>
  </testcase>
  <testcase name="test_skipped" classname="ctest" status="notrun"/>
</testsuite>
"""


def test_parse_ctest_junit():
    results = parse_ctest_junit(_CTEST_JUNIT)
    assert [r.name for r in results] == ["test_ring_buffer", "test_log_model", "test_skipped"]
    assert results[0].passed is True
    assert results[1].passed is False
    assert "row count mismatch" in results[1].message
    # A test that never ran is not a passing test.
    assert results[2].passed is False


def test_parse_ctest_junit_rejects_malformed_xml():
    assert parse_ctest_junit("<testsuite><testcase") == []


_CTEST_STDOUT = """    Start 1: test_ring_buffer
1/2 Test #1: test_ring_buffer .................   Passed    0.01 sec
    Start 2: test_log_model
2/2 Test #2: test_log_model ...................***Failed    0.02 sec
"""


def test_parse_ctest_stdout():
    results = parse_ctest_stdout(_CTEST_STDOUT)
    assert [r.name for r in results] == ["test_ring_buffer", "test_log_model"]
    assert results[0].passed is True
    assert results[1].passed is False
    assert "Failed" in results[1].message


_QTEST_XUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite errors="0" failures="0" tests="2" name="TestScanner">
  <testcase result="pass" name="initTestCase"/>
  <testcase result="pass" name="scanCountsFiles"/>
</testsuite>
<?xml version="1.0" encoding="UTF-8"?>
<testsuite errors="0" failures="1" tests="1" name="TestTreemapWidget">
  <testcase result="fail" name="clickSelectsNode">
    <failure result="fail" message="no signal emitted"/>
  </testcase>
</testsuite>
"""


def test_parse_qtest_xunit_reads_concatenated_suites():
    # TEMPLATE = subdirs runs several test binaries; their XML documents are
    # concatenated on one stream, so a single ElementTree.fromstring fails.
    results = parse_qtest_xunit(_QTEST_XUNIT)
    names = [r.name for r in results]
    assert names == [
        "TestScanner::initTestCase",
        "TestScanner::scanCountsFiles",
        "TestTreemapWidget::clickSelectsNode",
    ]
    assert results[0].passed is True
    assert results[2].passed is False
    assert "no signal emitted" in results[2].message


def test_parse_qtest_xunit_on_empty_output():
    assert parse_qtest_xunit("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_ctest_junit'`

- [ ] **Step 3: Write minimal implementation**

`from xml.etree import ElementTree` 을 파일 상단 import 에 더한다.

```python
# 1/2 Test #1: test_name ......   Passed    0.01 sec
_CTEST_LINE_RE = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>\S+)\s+[. ]*(?P<verdict>.+?)\s+[\d.]+\s+sec\s*$"
)
_TESTSUITE_RE = re.compile(r"<testsuite\b.*?</testsuite>", re.DOTALL)


@dataclass(frozen=True)
class TestCaseResult:
    """One test as the build system reported it."""

    name: str
    passed: bool
    message: str = ""


def _junit_case(node: ElementTree.Element) -> TestCaseResult:
    name = node.get("name", "")
    failures = node.findall("failure") + node.findall("error")
    if failures:
        parts = [f.get("message", "") or (f.text or "").strip() for f in failures]
        return TestCaseResult(name, False, "; ".join(p for p in parts if p))
    # A test ctest never ran is not evidence that it passes.
    status = node.get("status", "")
    if status and status not in ("run", "passed"):
        return TestCaseResult(name, False, f"ctest reported status {status!r}")
    return TestCaseResult(name, True)


def parse_ctest_junit(xml_text: str) -> list[TestCaseResult]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    return [_junit_case(node) for node in root.iter("testcase")]


def parse_ctest_stdout(text: str) -> list[TestCaseResult]:
    results: list[TestCaseResult] = []
    for line in text.splitlines():
        match = _CTEST_LINE_RE.match(line)
        if match is None:
            continue
        verdict = match.group("verdict").strip().lstrip("*")
        results.append(
            TestCaseResult(
                match.group("name"),
                verdict == "Passed",
                "" if verdict == "Passed" else verdict,
            )
        )
    return results


def parse_qtest_xunit(text: str) -> list[TestCaseResult]:
    """Parse one or more concatenated QtTest xunitxml documents."""

    results: list[TestCaseResult] = []
    for block in _TESTSUITE_RE.findall(text):
        try:
            suite = ElementTree.fromstring(block)
        except ElementTree.ParseError:
            continue
        suite_name = suite.get("name", "")
        for node in suite.iter("testcase"):
            case = _junit_case(node)
            name = f"{suite_name}::{case.name}" if suite_name else case.name
            passed = case.passed and node.get("result", "pass") == "pass"
            message = case.message
            if not passed and not message:
                message = f"QtTest reported result {node.get('result', '')!r}"
            results.append(TestCaseResult(name, passed, message))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): parse ctest and QtTest result output"
```

---

### Task 5: shadow 트리의 gcov 수집 계획

**Files:**
- Modify: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: 없음
- Produces: `plan_gcov(shadow: Path, gcov_bin: str) -> tuple[Path, list[list[str]]]`

`.gcov` 출력 디렉터리와 실행할 argv 목록을 함께 돌려주는 **순수 함수**다. 서브프로세스를 돌리지 않으므로 argv 묶음 규칙을 도구 없이 검증할 수 있다.

- [ ] **Step 1: Write the failing test**

```python
from ici.core.cmake import plan_gcov


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_plan_gcov_groups_by_object_directory(tmp_path):
    shadow = tmp_path / "build" / "ici-cmake"
    _touch(shadow / "CMakeFiles" / "core.dir" / "a.cpp.gcno")
    _touch(shadow / "CMakeFiles" / "core.dir" / "b.cpp.gcno")
    _touch(shadow / "CMakeFiles" / "gui.dir" / "c.cpp.gcno")

    out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")

    assert out_dir == shadow / "ici-gcov"
    assert len(argvs) == 2
    for argv in argvs:
        assert argv[0] == "/usr/bin/gcov"
        # -b gives branch counts; -p keeps the source path in the .gcov filename
        # so two objects with the same basename do not overwrite each other.
        assert "-b" in argv and "-p" in argv
        assert argv[argv.index("-o") + 1] in (
            str(shadow / "CMakeFiles" / "core.dir"),
            str(shadow / "CMakeFiles" / "gui.dir"),
        )
    core_argv = next(a for a in argvs if "core.dir" in a[a.index("-o") + 1])
    assert len([x for x in core_argv if x.endswith(".gcno")]) == 2


def test_plan_gcov_skips_its_own_output_directory(tmp_path):
    shadow = tmp_path / "build" / "ici-cmake"
    _touch(shadow / "CMakeFiles" / "core.dir" / "a.cpp.gcno")
    _touch(shadow / "ici-gcov" / "stale.gcno")

    _out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")

    assert len(argvs) == 1
    assert "core.dir" in argvs[0][argvs[0].index("-o") + 1]


def test_plan_gcov_with_no_gcno(tmp_path):
    shadow = tmp_path / "build" / "ici-qmake"
    shadow.mkdir(parents=True)
    out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")
    assert out_dir == shadow / "ici-gcov"
    assert argvs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'plan_gcov'`

- [ ] **Step 3: Write minimal implementation**

```python
GCOV_OUTPUT_DIRNAME = "ici-gcov"


def plan_gcov(shadow: Path, gcov_bin: str) -> tuple[Path, list[list[str]]]:
    """Group .gcno files by object directory and return one gcov argv per group.

    Callers must run every argv with ``cwd`` set to the returned directory.
    gcov writes .gcov files into its own working directory, so fixing the cwd
    makes the output flat and lets engines.coverage_support.parse_gcov_dir stay
    as it is — it globs a single directory level.
    """

    out_dir = shadow / GCOV_OUTPUT_DIRNAME
    groups: dict[Path, list[str]] = {}
    for gcno in sorted(shadow.rglob("*.gcno")):
        if out_dir == gcno.parent or out_dir in gcno.parents:
            continue
        groups.setdefault(gcno.parent, []).append(str(gcno))

    argvs = [
        [gcov_bin, "-b", "-p", "-o", str(obj_dir), *files]
        for obj_dir, files in sorted(groups.items())
    ]
    return out_dir, argvs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (27 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): plan gcov invocations over a shadow build tree"
```

---

### Task 6: 세션 오케스트레이션

**Files:**
- Modify: `src/ici/core/cmake.py`
- Test: `tests/test_build_adapter.py`

**Interfaces:**
- Consumes: Task 1–5 의 전부
- Produces: `BuildSession(root, shadow, backend, descriptor, reason, configured, tool_evidence, errors)`, `configure(root: Path) -> BuildSession`, `build(session: BuildSession) -> bool`, `run_tests(session: BuildSession) -> list[TestCaseResult]`, `collect_coverage(session: BuildSession) -> Path | None`

- [ ] **Step 1: Write the failing test**

```python
import ici.core.cmake as cmake_mod
from ici.core.models import ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.cmake import BuildSession, build, collect_coverage, configure, run_tests


def _ok(*_args, **_kwargs) -> ProcessResult:
    return ProcessResult(0, "cmake version 3.28.1", "", 0.01)


def test_configure_records_backend_reason_as_evidence(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cmake_mod, "run_process", _ok)

    session = configure(tmp_path)

    assert session.backend == BACKEND_CMAKE
    assert session.configured is True
    assert session.shadow == tmp_path / "build" / "ici-cmake"
    # Choosing a backend silently would make "why did this build run this way"
    # untraceable from the report alone.
    names = [e.name for e in session.tool_evidence]
    assert any("CMakeLists.txt" in name for name in names)


def test_configure_without_descriptor_has_no_backend(tmp_path):
    session = configure(tmp_path)
    assert session.backend is None
    assert session.configured is False


def test_configure_missing_tool_is_an_error(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda _name: None)

    session = configure(tmp_path)

    assert session.configured is False
    # Not NOT_APPLICABLE: there was something to build and it was not measured.
    assert any("cmake" in err for err in session.errors)


def test_configure_failure_records_stderr(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fail(cmd, **_kwargs):
        if "--version" in cmd:
            return ProcessResult(0, "cmake version 3.28.1", "", 0.01)
        return ProcessResult(1, "", "CMake Error: bad target", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _fail)
    session = configure(tmp_path)

    assert session.configured is False
    assert any("bad target" in err for err in session.errors)


def test_run_tests_prefers_junit_when_written(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    shadow = tmp_path / "build" / "ici-cmake"
    shadow.mkdir(parents=True)
    (shadow / "ici-ctest.xml").write_text(_CTEST_JUNIT, encoding="utf-8")

    def _run(cmd, **_kwargs):
        if "--version" in cmd:
            return ProcessResult(0, "cmake version 3.28.1", "", 0.01)
        return ProcessResult(0, _CTEST_STDOUT, "", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _run)
    session = configure(tmp_path)
    results = run_tests(session)

    # The JUnit file has three cases; stdout has two. Proving which source was
    # used matters, because only one of them reports the skipped test.
    assert len(results) == 3


def test_collect_coverage_runs_every_group(tmp_path, monkeypatch):
    shadow = tmp_path / "build" / "ici-cmake"
    for name in ("core.dir", "gui.dir"):
        target = shadow / "CMakeFiles" / name / "a.cpp.gcno"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs.get("cwd") == shadow / "ici-gcov"
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _run)
    session = BuildSession(root=tmp_path, shadow=shadow, backend=BACKEND_CMAKE)

    out_dir = collect_coverage(session)

    assert out_dir == shadow / "ici-gcov"
    assert len(calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'BuildSession'`

- [ ] **Step 3: Write minimal implementation**

파일 상단 import 에 `import os`, `import shutil`, `from dataclasses import dataclass, field`, `from ici.core.models import ToolEvidence`, `from ici.core.runner import run_process` 를 더한다.

```python
@dataclass
class BuildSession:
    """One configure of one project. Shared by the build and test engines."""

    root: Path
    shadow: Path
    backend: str | None = None
    descriptor: str = ""
    reason: str = ""
    configured: bool = False
    cmake_version: tuple[int, int] | None = None
    tool_evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _record(session: BuildSession, name: str, argv: list[str], result) -> None:
    session.tool_evidence.append(
        ToolEvidence(
            name=name,
            path=argv[0],
            argv=argv,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )
    )


def _fail(session: BuildSession, message: str) -> None:
    if message not in session.errors:
        session.errors.append(message)


def _which(session: BuildSession, name: str) -> str | None:
    found = shutil.which(name)
    if found is None:
        _fail(session, f"{name} executable was unavailable")
    return found


def configure(root: Path) -> BuildSession:
    """Select a backend and configure a shadow build tree."""

    choice = select_backend(root)
    session = BuildSession(
        root=root,
        shadow=shadow_dir(root, choice.kind or BACKEND_CMAKE),
        backend=choice.kind,
        descriptor=choice.descriptor,
        reason=choice.reason,
    )
    if choice.kind is None:
        return session

    # The choice itself is evidence. Without it the report cannot say why one
    # backend ran and the other did not. The reason goes in the name, not in
    # `error` — this is a normal outcome, not a failure.
    session.tool_evidence.append(
        ToolEvidence(name=f"build backend selection: {choice.reason}", path="")
    )
    session.shadow.mkdir(parents=True, exist_ok=True)

    if choice.kind == BACKEND_CMAKE:
        return _configure_cmake(session)
    return _configure_qmake(session)


def _configure_cmake(session: BuildSession) -> BuildSession:
    cmake_bin = _which(session, "cmake")
    if cmake_bin is None:
        return session

    version_argv = [cmake_bin, "--version"]
    version_result = run_process(version_argv)
    _record(session, "cmake --version", version_argv, version_result)
    session.cmake_version = parse_cmake_version(version_result.stdout)

    argv = cmake_configure_argv(cmake_bin, session.root, session.shadow)
    result = run_process(argv, cwd=session.root)
    _record(session, "cmake configure", argv, result)
    if result.returncode != 0:
        _fail(session, f"cmake configure failed: {result.stderr[:200]}")
        return session
    session.configured = True
    return session


def _configure_qmake(session: BuildSession) -> BuildSession:
    # Debian ships Qt6's qmake as qmake6; some distributions only have `qmake`.
    # Probing both before recording an error keeps the message honest.
    qmake_bin = shutil.which("qmake6") or shutil.which("qmake")
    if qmake_bin is None:
        _fail(session, "qmake6 executable was unavailable")
        return session

    argv = qmake_configure_argv(qmake_bin, session.root / session.descriptor)
    result = run_process(argv, cwd=session.shadow)
    _record(session, "qmake configure", argv, result)
    if result.returncode != 0:
        _fail(session, f"qmake configure failed: {result.stderr[:200]}")
        return session
    session.configured = True
    return session


def build(session: BuildSession) -> bool:
    """Build the configured tree. Returns False on failure."""

    if not session.configured:
        return False
    if session.backend == BACKEND_CMAKE:
        cmake_bin = _which(session, "cmake")
        if cmake_bin is None:
            return False
        argv = cmake_build_argv(cmake_bin, session.shadow)
        cwd = session.root
    else:
        make_bin = _which(session, "make")
        if make_bin is None:
            return False
        argv = qmake_build_argv(make_bin, os.cpu_count() or 1)
        cwd = session.shadow

    result = run_process(argv, cwd=cwd)
    _record(session, f"{session.backend} build", argv, result)
    if result.returncode != 0:
        _fail(session, f"{session.backend} build failed: {result.stderr[:200]}")
        return False
    return True


def run_tests(session: BuildSession) -> list[TestCaseResult]:
    """Run the project's tests through its own build system."""

    if session.backend == BACKEND_CMAKE:
        ctest_bin = _which(session, "ctest")
        if ctest_bin is None:
            return []
        argv, junit = cmake_test_argv(ctest_bin, session.shadow, session.cmake_version)
        result = run_process(argv, cwd=session.shadow)
        _record(session, "ctest", argv, result)
        if junit is not None and junit.is_file():
            parsed = parse_ctest_junit(junit.read_text(encoding="utf-8", errors="replace"))
            if parsed:
                return parsed
        return parse_ctest_stdout(result.stdout)

    make_bin = _which(session, "make")
    if make_bin is None:
        return []
    argv = qmake_test_argv(make_bin)
    result = run_process(argv, cwd=session.shadow)
    _record(session, "make check", argv, result)
    return parse_qtest_xunit(result.stdout)


def collect_coverage(session: BuildSession) -> Path | None:
    """Run gcov over the shadow tree. Must be called after run_tests."""

    gcov_bin = _which(session, "gcov")
    if gcov_bin is None:
        return None
    out_dir, argvs = plan_gcov(session.shadow, gcov_bin)
    if not argvs:
        _fail(session, "C++ gcov data files were unavailable")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    for argv in argvs:
        result = run_process(argv, cwd=out_dir)
        _record(session, "gcov", argv, result)
    return out_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`
Expected: PASS (33 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ici/core/cmake.py tests/test_build_adapter.py
git commit -m "feat(build): orchestrate configure, build, test and coverage"
```

---

### Task 7: `build` 엔진 통합 — A-2 부분 수정

**Files:**
- Modify: `src/ici/engines/build.py:393-398` (`_compile_cpp` 의 거부 분기)
- Modify: `CHANGELOG.md`
- Test: `tests/test_build_engine.py`

**Interfaces:**
- Consumes: `configure`, `build`, `BuildSession`, `select_backend`, `BACKEND_CMAKE` (Task 1, 6)
- Produces: 없음 (엔진 동작 변경)

- [ ] **Step 1: Write the failing test**

`tests/test_build_engine.py` 에 추가한다.

```python
import ici.core.cmake as cmake_mod
from ici.core.cmake import BACKEND_CMAKE, BuildSession


def test_cmake_project_is_built_through_the_adapter(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")

    shadow = tmp_path / "build" / "ici-cmake"

    def _fake_configure(root):
        shadow.mkdir(parents=True, exist_ok=True)
        return BuildSession(
            root=root,
            shadow=shadow,
            backend=BACKEND_CMAKE,
            descriptor="CMakeLists.txt",
            reason="CMakeLists.txt at the project root selected the CMake backend",
            configured=True,
        )

    def _fake_build(session):
        binary = session.shadow / "app"
        binary.write_bytes(b"\x7fELF")
        binary.chmod(0o755)
        return True

    monkeypatch.setattr("ici.engines.build.adapter_configure", _fake_configure)
    monkeypatch.setattr("ici.engines.build.adapter_build", _fake_build)

    result = BuildEngine(tmp_path).run()

    # Before this change the engine refused outright with
    # "C++ build descriptor requires an adapter".
    assert result.status is not EngineStatus.ERROR
    assert "requires an adapter" not in result.summary


def test_makefile_only_project_still_refuses_with_a_precise_reason(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    _write_project(tmp_path, "cpp")

    result = BuildEngine(tmp_path).run()

    assert result.status is EngineStatus.ERROR
    # The old message predates the adapters existing at all. Now that CMake and
    # qmake are handled, it has to say which adapter is missing.
    messages = " ".join(t.message for t in result.targets)
    assert "Makefile" in messages
    assert "CMake and qmake" in messages
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_engine.py -v -k "adapter or refuses"`
Expected: FAIL — 첫 테스트는 `ERROR` 로 끝나고, 둘째는 옛 메시지라 `"CMake and qmake"` 가 없다

- [ ] **Step 3: Write minimal implementation**

`src/ici/engines/build.py` 의 import 에 더한다.

```python
from ici.core.cmake import build as adapter_build
from ici.core.cmake import configure as adapter_configure
from ici.core.cmake import select_backend
```

`_compile_cpp` 의 거부 분기(현재 393-398행)를 바꾼다.

```python
        choice = select_backend(base)
        if choice.kind is not None:
            self._build_with_adapter(base, targets)
            return
        if self._has_build_descriptor(base):
            self._record_error(
                targets,
                f"{choice.descriptor or 'A build descriptor'} at the project root has no "
                "adapter; only CMake and qmake are supported, and generic g++ was not invoked",
            )
            return
```

같은 클래스에 메서드를 더한다.

```python
    def _build_with_adapter(self, base: Path, targets: list[InspectionTarget]) -> None:
        """Delegate configure and build to the project's own build system."""

        session = adapter_configure(base)
        if not session.configured:
            self._tool_evidence.extend(session.tool_evidence)
            for message in session.errors:
                self._record_error(targets, message, file_path=session.descriptor or ".")
            return

        built = adapter_build(session)
        # Copy evidence once, after every adapter call. Slicing by the engine's
        # own list length would index into the wrong list.
        self._tool_evidence.extend(session.tool_evidence)
        if not built:
            self._has_fail = True
            for message in session.errors:
                targets.append(
                    InspectionTarget(
                        file_path=session.descriptor or ".",
                        start_line=1,
                        target_name="BuildAdapter",
                        status=EngineStatus.FAIL,
                        message=message,
                    )
                )
            return

        produced = self._count_adapter_artifacts(session.shadow)
        self._artifact_count += produced
        targets.append(
            InspectionTarget(
                file_path=session.descriptor,
                start_line=1,
                target_name=f"BuildAdapter[{session.backend}]",
                status=EngineStatus.PASS,
                message=f"{session.reason}; produced {produced} artifact(s)",
            )
        )

    @staticmethod
    def _count_adapter_artifacts(shadow: Path) -> int:
        """Count linked outputs in the shadow tree: executables and libraries."""

        count = 0
        for path in shadow.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix in (".a", ".so"):
                count += 1
                continue
            if os.access(path, os.X_OK) and path.read_bytes()[:4] == b"\x7fELF":
                count += 1
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_engine.py -v`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: Update CHANGELOG**

`CHANGELOG.md` 의 `## [Unreleased]` 아래에 `### Added` 절을 만들어 넣는다.

```markdown
### Added
- **CMake·qmake 프로젝트를 실제 빌드 정의로 빌드합니다 (`build`)**: 루트에 `CMakeLists.txt` 나 `*.pro` 가 있으면 `build` 엔진이 거부하는 대신 그 빌드 시스템에 configure·build 를 위임합니다. 지금까지 정상적인 CMake/qmake 프로젝트는 `ici build` 를 아예 쓸 수 없었습니다.
  - 백엔드 선택 근거를 `ToolEvidence` 로 남깁니다 — 조용히 정하면 "이 빌드가 왜 이렇게 돌았나" 를 리포트만 보고 알 수 없습니다.
  - 손으로 쓴 `Makefile` 만 있는 프로젝트는 여전히 거부되며, 메시지가 **어느 어댑터가 없어서인지**를 말하도록 바뀌었습니다.
```

- [ ] **Step 6: Commit**

```bash
git add src/ici/engines/build.py tests/test_build_engine.py CHANGELOG.md
git commit -m "feat(build): build CMake and qmake projects through their own definitions"
```

---

### Task 8: `test` 엔진 통합 — A-3 수정

**Files:**
- Modify: `src/ici/engines/test.py:156-215` (`_run_project_tests`, `_run_cpp_tests`)
- Modify: `CHANGELOG.md`
- Test: `tests/test_test_engine.py`

**Interfaces:**
- Consumes: `configure`, `build`, `run_tests`, `collect_coverage`, `TestCaseResult`, `select_backend` (Task 1, 4, 6)
- Produces: 없음 (엔진 동작 변경)

- [ ] **Step 1: Write the failing test**

`tests/test_test_engine.py` 에 추가한다.

```python
from ici.core.cmake import BACKEND_CMAKE, BuildSession, TestCaseResult


def _cmake_project(root):
    (root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "model.cpp").write_text("int f() { return 1; }\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_model.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "ici.toml").write_text('name = "x"\ntype = "cpp"\nversion = "1.0.0"\n', encoding="utf-8")


def test_cpp_tests_run_through_the_adapter(tmp_path, monkeypatch):
    _cmake_project(tmp_path)
    shadow = tmp_path / "build" / "ici-cmake"

    monkeypatch.setattr(
        "ici.engines.test.adapter_configure",
        lambda root: BuildSession(
            root=root, shadow=shadow, backend=BACKEND_CMAKE,
            descriptor="CMakeLists.txt", reason="root CMakeLists.txt", configured=True,
        ),
    )
    monkeypatch.setattr("ici.engines.test.adapter_build", lambda _s: True)
    monkeypatch.setattr(
        "ici.engines.test.adapter_run_tests",
        lambda _s: [
            TestCaseResult("test_model", True),
            TestCaseResult("test_broken", False, "assertion failed"),
        ],
    )
    monkeypatch.setattr("ici.engines.test.adapter_collect_coverage", lambda _s: None)

    result = TestEngine(tmp_path).run()

    names = {t.target_name for t in result.targets}
    assert any("test_model" in n for n in names)
    assert any("test_broken" in n for n in names)
    failed = [t for t in result.targets if t.status is EngineStatus.FAIL]
    assert any("assertion failed" in t.message for t in failed)


def test_adapter_test_target_points_at_its_source_file(tmp_path, monkeypatch):
    _cmake_project(tmp_path)
    shadow = tmp_path / "build" / "ici-cmake"
    monkeypatch.setattr(
        "ici.engines.test.adapter_configure",
        lambda root: BuildSession(
            root=root, shadow=shadow, backend=BACKEND_CMAKE,
            descriptor="CMakeLists.txt", reason="root CMakeLists.txt", configured=True,
        ),
    )
    monkeypatch.setattr("ici.engines.test.adapter_build", lambda _s: True)
    monkeypatch.setattr(
        "ici.engines.test.adapter_run_tests", lambda _s: [TestCaseResult("test_model", True)]
    )
    monkeypatch.setattr("ici.engines.test.adapter_collect_coverage", lambda _s: None)

    result = TestEngine(tmp_path).run()

    # AGENTS.md 5-1 requires a file path on every target. CTest reports only a
    # name, so the engine resolves it against tests/ by stem.
    target = next(t for t in result.targets if "test_model" in t.target_name)
    assert target.file_path == "tests/test_model.cpp"


def test_adapter_build_failure_is_a_fail_not_a_silent_pass(tmp_path, monkeypatch):
    _cmake_project(tmp_path)
    shadow = tmp_path / "build" / "ici-cmake"
    session = BuildSession(
        root=tmp_path, shadow=shadow, backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt", reason="root CMakeLists.txt", configured=True,
    )
    session.errors.append("cmake build failed: undefined reference to vtable for LogModel")
    monkeypatch.setattr("ici.engines.test.adapter_configure", lambda _root: session)
    monkeypatch.setattr("ici.engines.test.adapter_build", lambda _s: False)

    result = TestEngine(tmp_path).run()

    assert result.status in (EngineStatus.FAIL, EngineStatus.ERROR)
    assert any("vtable" in t.message for t in result.targets)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_test_engine.py -v -k adapter`
Expected: FAIL — `AttributeError: module 'ici.engines.test' has no attribute 'adapter_configure'`

- [ ] **Step 3: Write minimal implementation**

`src/ici/engines/test.py` 의 import 에 더한다.

```python
from ici.core.cmake import build as adapter_build
from ici.core.cmake import collect_coverage as adapter_collect_coverage
from ici.core.cmake import configure as adapter_configure
from ici.core.cmake import run_tests as adapter_run_tests
from ici.core.cmake import select_backend
```

`_run_cpp_tests` 의 맨 앞에 분기를 넣는다.

```python
    def _run_cpp_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        if select_backend(self.project_root).kind is not None:
            return self._run_cpp_tests_via_adapter(targets)
        gxx = shutil.which("g++")
        ...  # 기존 본문은 그대로 둔다
```

같은 클래스에 메서드를 더한다.

```python
    def _resolve_test_source(self, test_name: str) -> str:
        """Map a CTest/QtTest name onto a file, as AGENTS.md 5-1 requires.

        Neither ctest nor QtTest reports the source file, so the stem is matched
        against tests/. When nothing matches, the build descriptor is the most
        specific location that is still true.
        """

        stem = test_name.split("::")[0]
        tests_root = self.project_root / "tests"
        if tests_root.is_dir():
            for candidate in sorted(tests_root.rglob("*.cpp")):
                if candidate.stem == stem:
                    return str(candidate.relative_to(self.project_root))
        return select_backend(self.project_root).descriptor or "."

    def _run_cpp_tests_via_adapter(
        self, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []

        session = adapter_configure(self.project_root)

        if not session.configured:
            self._tool_evidence.extend(session.tool_evidence)
            for message in session.errors:
                self._record_tool_error(message)
            self._coverage_errors.append("C++ build was not configured")
            return 0, 0, False

        if not adapter_build(session):
            self._tool_evidence.extend(session.tool_evidence)
            self._coverage_errors.append("C++ build failed before tests could run")
            for message in session.errors:
                targets.append(
                    InspectionTarget(
                        file_path=session.descriptor or ".",
                        start_line=1,
                        target_name="[C++] build",
                        status=EngineStatus.FAIL,
                        message=message,
                    )
                )
            return 0, 0, True

        results = adapter_run_tests(session)

        passed = 0
        has_failure = False
        for case in results:
            relative = self._resolve_test_source(case.name)
            if case.passed:
                passed += 1
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"[C++] {case.name}",
                        status=EngineStatus.PASS,
                        message="C++ Test Passed",
                    )
                )
            else:
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"[C++] {case.name}",
                        status=EngineStatus.FAIL,
                        message=f"Execution Failed: {case.message}",
                    )
                )

        # gcov only after the tests ran: .gcda does not exist until then.
        gcov_dir = adapter_collect_coverage(session)
        # One copy at the end, covering configure, build, ctest and gcov.
        self._tool_evidence.extend(session.tool_evidence)
        if gcov_dir is None:
            self._coverage_errors.append("C++ gcov coverage output was missing or malformed")
        else:
            # cpp_external_build_dirs does not apply here: the build system
            # links everything, so everything it built is coverage scope.
            sources = {
                str(path.relative_to(self.project_root))
                for path in get_all_cpp_sources(self.project_root, self.config)
            }
            self._cpp_coverage_rows = self._parse_gcov_dir(gcov_dir, sources)
            self._cpp_function_rows = self._parse_gcov_functions(gcov_dir, sources)
            if self._cpp_coverage_rows:
                self._coverage_measured = True
            else:
                self._coverage_errors.append("C++ gcov coverage output was missing or malformed")

        return passed, len(results), has_failure
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_test_engine.py -v`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: Update CHANGELOG**

Task 7 에서 만든 `### Added` 절에 항목을 더한다.

```markdown
- **`Q_OBJECT` 클래스를 단위 테스트할 수 있습니다 (`test`)**: C++ 테스트를 `g++ -std=c++17` 로 직접 컴파일·링크하는 대신 프로젝트의 CMake/qmake 정의에 위임합니다. moc 가 빌드 시스템 쪽에서 돌므로 `Q_OBJECT` 클래스가 더 이상 vtable 미해결로 링크에 실패하지 않고, `-std` 고정도 사라져 C++20/23 프로젝트를 검증할 수 있습니다.
  - 커버리지 계측 플래그는 ici 가 주입합니다. 프로젝트가 커버리지 빌드를 선언하도록 요구하면 설정을 빠뜨렸을 때 측정이 조용히 사라지고, TEM 점수가 그 측정 위에 서 있습니다.
  - 어댑터 경로에서는 `project.cpp_external_build_dirs` 가 무시됩니다. 이 설정은 ici 가 moc 를 돌리지 못한다는 전제 위에 있었고, 어댑터가 그 전제를 없앱니다.
```

- [ ] **Step 6: Commit**

```bash
git add src/ici/engines/test.py tests/test_test_engine.py CHANGELOG.md
git commit -m "feat(test): run C++ tests through the project's build system"
```

---

### Task 9: 실물 픽스처와 E2E 검증

**Files:**
- Create: `examples/cpp-fixtures/cmake_project/CMakeLists.txt`
- Create: `examples/cpp-fixtures/cmake_project/src/counter.hpp`
- Create: `examples/cpp-fixtures/cmake_project/src/counter.cpp`
- Create: `examples/cpp-fixtures/cmake_project/tests/test_counter.cpp`
- Create: `examples/cpp-fixtures/qmake_project/qmake_project.pro`
- Create: `examples/cpp-fixtures/qmake_project/src/src.pro`
- Create: `examples/cpp-fixtures/qmake_project/src/counter.hpp`
- Create: `examples/cpp-fixtures/qmake_project/src/counter.cpp`
- Create: `examples/cpp-fixtures/qmake_project/tests/tests.pro`
- Create: `examples/cpp-fixtures/qmake_project/tests/test_counter.cpp`
- Test: `tests/test_build_adapter_e2e.py`

**Interfaces:**
- Consumes: `configure`, `build`, `run_tests`, `collect_coverage` (Task 6)
- Produces: 없음

픽스처는 실물 프로젝트를 대체하지 않는다. ici 단위 테스트가 외부 저장소 없이 돌게 하는 것이 목적이며, 어댑터의 실측 근거는 `toy-projects` 의 `loglens` 와 `diskmap` 이다. **`Q_OBJECT` 를 넣어야 픽스처가 의미를 갖는다** — moc 가 실제로 필요해야 moc 지원을 검증한 것이 된다.

- [ ] **Step 1: Write the failing test**

`tests/test_build_adapter_e2e.py` 를 만든다.

```python
"""End-to-end adapter runs against the fixture projects.

Two layers exist on purpose. Argv construction and output parsing are pure and
tested without tools in test_build_adapter.py. These tests need cmake/qmake/Qt
and are skipped when those are missing, because ici supports RHEL 7.9 where they
may not be.

The skip must not be silent. ici shipped a green gate for several releases while
lint had never actually run in CI (C-6). ICI_REQUIRE_BUILD_ADAPTERS=1 turns a
missing tool into a failure, and ici's own CI sets it.
"""

import os
import shutil
from pathlib import Path

import pytest

from ici.core.cmake import build, collect_coverage, configure, run_tests

FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "cpp-fixtures"


def _require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if not missing:
        return
    message = f"build adapter tools unavailable: {', '.join(missing)}"
    if os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _copy(fixture: str, tmp_path: Path) -> Path:
    target = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, target)
    return target


def test_cmake_fixture_builds_and_tests_a_q_object(tmp_path):
    _require("cmake", "ctest", "gcov", "moc")
    root = _copy("cmake_project", tmp_path)

    session = configure(root)
    assert session.configured, session.errors
    assert build(session), session.errors

    results = run_tests(session)
    # A Q_OBJECT class links only when moc ran. Before the adapter this failed
    # with "undefined reference to vtable".
    assert results, "no tests were reported"
    assert all(r.passed for r in results), [r.message for r in results if not r.passed]

    gcov_dir = collect_coverage(session)
    assert gcov_dir is not None
    assert list(gcov_dir.glob("*.gcov")), "gcov produced no output"


def test_qmake_fixture_builds_and_tests_a_q_object(tmp_path):
    _require("qmake6", "make", "gcov", "moc")
    root = _copy("qmake_project", tmp_path)

    session = configure(root)
    assert session.configured, session.errors
    assert build(session), session.errors

    results = run_tests(session)
    assert results, "no tests were reported"
    assert all(r.passed for r in results), [r.message for r in results if not r.passed]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --python 3.10 pytest tests/test_build_adapter_e2e.py -v`
Expected: FAIL — `FileNotFoundError`, 픽스처 디렉터리가 없다

- [ ] **Step 3: Write the fixtures**

`examples/cpp-fixtures/cmake_project/src/counter.hpp`

```cpp
#pragma once

#include <QObject>

// Q_OBJECT is the point of this fixture: the class does not link without a
// moc-generated translation unit, so a passing test proves moc ran.
class Counter : public QObject {
    Q_OBJECT

public:
    explicit Counter(QObject* parent = nullptr);

    int value() const;
    void add(int amount);

signals:
    void changed(int value);

private:
    int value_ = 0;
};
```

`examples/cpp-fixtures/cmake_project/src/counter.cpp`

```cpp
#include "counter.hpp"

Counter::Counter(QObject* parent) : QObject(parent) {}

int Counter::value() const {
    return value_;
}

void Counter::add(int amount) {
    if (amount == 0) {
        return;
    }
    value_ += amount;
    emit changed(value_);
}
```

`examples/cpp-fixtures/cmake_project/tests/test_counter.cpp`

```cpp
#include <QSignalSpy>
#include <QtTest>

#include "counter.hpp"

class TestCounter : public QObject {
    Q_OBJECT

private slots:
    void addEmitsChanged();
    void addZeroIsSilent();
};

void TestCounter::addEmitsChanged() {
    Counter counter;
    QSignalSpy spy(&counter, &Counter::changed);
    counter.add(3);
    QCOMPARE(counter.value(), 3);
    QCOMPARE(spy.count(), 1);
}

void TestCounter::addZeroIsSilent() {
    Counter counter;
    QSignalSpy spy(&counter, &Counter::changed);
    counter.add(0);
    QCOMPARE(spy.count(), 0);
}

QTEST_MAIN(TestCounter)
#include "test_counter.moc"
```

`examples/cpp-fixtures/cmake_project/CMakeLists.txt`

```cmake
cmake_minimum_required(VERSION 3.16)
project(cmake_fixture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Test)

add_library(counter STATIC src/counter.cpp)
target_include_directories(counter PUBLIC src)
target_link_libraries(counter PUBLIC Qt6::Core)

enable_testing()

add_executable(test_counter tests/test_counter.cpp)
target_link_libraries(test_counter PRIVATE counter Qt6::Test)
# Always the project root, matching the contract fixed in C-9: a test that opens
# a fixture by relative path must not depend on who launched it.
add_test(NAME test_counter COMMAND test_counter WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
```

`examples/cpp-fixtures/qmake_project/src/counter.hpp` 와 `counter.cpp` 는 위 CMake 픽스처의 같은 이름 파일과 **내용이 동일하다.** 그대로 복사한다.

`examples/cpp-fixtures/qmake_project/tests/test_counter.cpp` 도 위 파일과 **내용이 동일하다.** 그대로 복사한다.

`examples/cpp-fixtures/qmake_project/qmake_project.pro`

```qmake
TEMPLATE = subdirs
CONFIG += ordered
SUBDIRS = src tests
tests.depends = src
```

`examples/cpp-fixtures/qmake_project/src/src.pro`

```qmake
TEMPLATE = lib
CONFIG += staticlib
QT = core
TARGET = counter
HEADERS = counter.hpp
SOURCES = counter.cpp
```

`examples/cpp-fixtures/qmake_project/tests/tests.pro`

```qmake
TEMPLATE = app
# testcase generates the `check` target and forwards TESTARGS to the binary.
CONFIG += testcase
QT = core testlib
TARGET = test_counter
INCLUDEPATH += $$PWD/../src
SOURCES = test_counter.cpp
LIBS += -L$$OUT_PWD/../src -lcounter
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --python 3.10 pytest tests/test_build_adapter_e2e.py -v`
Expected: PASS (2 tests)

도구가 없어 skip 된다면 강제 모드로 다시 돌려 정말 도구 문제인지 확인한다.

Run: `ICI_REQUIRE_BUILD_ADAPTERS=1 uv run --python 3.10 pytest tests/test_build_adapter_e2e.py -v`
Expected: 도구가 있으면 PASS, 없으면 skip 이 아니라 FAIL

- [ ] **Step 5: Commit**

```bash
git add examples/cpp-fixtures/cmake_project examples/cpp-fixtures/qmake_project tests/test_build_adapter_e2e.py
git commit -m "test(build): exercise both adapters against Q_OBJECT fixtures"
```

---

### Task 10: CI 와 사용자 문서

**Files:**
- Modify: `.github/workflows/ci.yml:77-83`
- Modify: `docs/user-guide.md`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 9 의 E2E 테스트와 `ICI_REQUIRE_BUILD_ADAPTERS`
- Produces: 없음

- [ ] **Step 1: Add qmake6 and the strict flag to CI**

`.github/workflows/ci.yml` 의 Qt6 설치 스텝(77-80행 근처)에서 패키지 목록에 `qmake6` 를 더한다. **`qmake6` 는 `qt6-base-dev` 와 별개 패키지라 따로 적지 않으면 설치되지 않는다.** cmake 는 GitHub 러너에 기본 제공된다.

```yaml
      - name: Install Qt6 headers for the C++ gate
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends qt6-base-dev qmake6 pkg-config
```

Python 품질 게이트 job 의 pytest 스텝에 환경 변수를 더한다.

```yaml
      - name: Run tests
        env:
          # The adapter E2E tests skip when cmake/qmake/Qt are missing. On this
          # runner they are present, so a skip means something broke — and a
          # silently skipped gate is exactly how lint went unrun for several
          # releases (C-6).
          ICI_REQUIRE_BUILD_ADAPTERS: "1"
        run: uv run --python 3.10 pytest
```

- [ ] **Step 2: Verify the workflow parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Document the scope split**

`docs/user-guide.md` 의 C++ 설정 절에 다음을 더한다. **엔진마다 스코프 규칙이 다른 것은 B-1 에서 이미 문제가 된 적이 있으므로, 어느 경로에서 무엇이 적용되는지를 명시한다.**

```markdown
### C++ 빌드 경로는 두 가지다

`ici` 는 프로젝트 루트의 빌드 디스크립터를 보고 경로를 고른다.

| 루트에 있는 것 | `build` 와 `test` 가 하는 일 |
|---|---|
| `CMakeLists.txt` | CMake 로 configure·build 하고 CTest 로 테스트한다 |
| `*.pro` | qmake 로 configure·build 하고 `make check` 로 테스트한다 |
| `Makefile` 만 | 어댑터가 없어 거부한다 |
| 없음 | 모든 소스를 `g++` 로 직접 컴파일·링크한다 |

하위 디렉터리의 디스크립터는 보지 않는다. `src/gui/CMakeLists.txt` 만 있는 프로젝트는
g++ 경로를 쓴다.

**`project.cpp_external_build_dirs` 는 g++ 경로에서만 적용된다.** 이 설정은 "moc 가 필요해
ici 가 직접 빌드할 수 없는 소스를 링크 대상에서 뺀다" 는 뜻인데, 어댑터 경로에서는 빌드
시스템이 moc 를 돌리므로 전제가 사라진다. 어댑터 경로에서는 빌드 시스템이 빌드한 전부가
테스트 링크와 커버리지 집계의 대상이다.

**어댑터 경로에서는 `-std=c++17` 고정이 사라진다.** C++ 표준을 프로젝트의 빌드 정의가 정한다.

커버리지 계측 플래그(`--coverage`)는 어느 경로에서든 `ici` 가 주입한다. 프로젝트가 커버리지
빌드를 따로 선언할 필요는 없다.
```

`docs/engine-reference.md` 의 `build` 와 `test` 항목에 각각 한 줄을 더해 위 표로 링크한다.

- [ ] **Step 4: Update CHANGELOG**

Task 7·8 에서 만든 `### Added` 절 아래에 더한다.

```markdown
### Changed
- **C++ 빌드 경로가 두 갈래가 되었습니다**: 루트 빌드 디스크립터 유무로 어댑터 경로와 기존 g++ 경로가 갈립니다. 어느 경로에서 `project.cpp_external_build_dirs` 와 `-std` 고정이 적용되는지는 `docs/user-guide.md` 에 표로 정리했습니다.
```

- [ ] **Step 5: Run the full quality gate**

```bash
uv run --python 3.10 pytest
uvx ruff check .
uvx ruff format --check .
./scripts/build-pyz.sh
./scripts/smoke.sh
```
Expected: 전부 통과, ruff 위반 0건

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml docs/user-guide.md docs/engine-reference.md CHANGELOG.md
git commit -m "docs(build): document the two C++ build paths and gate the adapter E2E in CI"
```

---

### Task 11: 자기 검증과 릴리스 v0.6.0

**Files:**
- Modify: `ici.toml` (`[ici] version`)
- Modify: `pyproject.toml` (`version`)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1–10 전부
- Produces: `dist/ici.pyz` 릴리스 에셋 — **`toy-projects` 계획의 선행 조건**

- [ ] **Step 1: Verify the g++ path did not regress**

`viewer/` 는 루트에 `CMakeLists.txt` 가 있지만 **`viewer/` 자체가 프로젝트 루트**이므로 어댑터 경로로 넘어간다. 이것이 의도된 동작인지 실제로 확인한다.

```bash
cd viewer && ../dist/ici.pyz verify 2>&1 | tail -20
```
Expected: 스위트 PASS, C++ tests 3/3. 어댑터 경로로 넘어갔다면 `viewer/CMakeLists.txt` 에 `enable_testing()` 과 `add_test` 가 없으므로 **테스트 0건**이 되어 FAIL 한다.

- [ ] **Step 2: Decide and record what viewer does**

Step 1 이 FAIL 이면 `viewer/CMakeLists.txt` 에 테스트 타깃을 더해 어댑터 경로로 정식 전환한다.

```cmake
enable_testing()

foreach(name IN ITEMS json_parser report_model summary)
    add_executable(test_${name} tests/test_${name}.cpp)
    target_link_libraries(test_${name} PRIVATE icirv_core)
    add_test(NAME test_${name} COMMAND test_${name} WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endforeach()
```

이 경우 스펙 §4.3("viewer 는 g++ 경로 유지")이 성립하지 않으므로, **g++ 경로의 회귀 증거가 사라진다.** `examples/cpp-fixtures/clean_baseline` 를 대상으로 한 g++ 경로 E2E 테스트를 `tests/test_cpp_e2e.py` 에 추가해 그 자리를 메우고, 스펙 §4.3 을 실제 상태에 맞게 고친다.

- [ ] **Step 3: Run the full gate one more time**

```bash
uv run --python 3.10 pytest
uvx ruff check . && uvx ruff format --check .
./scripts/build-pyz.sh && ./scripts/build-pyz.sh   # 두 번 빌드해 체크섬이 같은지 본다
sha256sum dist/ici.pyz
./scripts/smoke.sh
./dist/ici.pyz verify
```
Expected: 전부 통과, 두 번의 빌드 체크섬 일치

- [ ] **Step 4: Bump the version**

`ici.toml` 의 `[ici] version` 과 `pyproject.toml` 의 `version` 을 `0.6.0` 으로 올린다. `CHANGELOG.md` 의 `## [Unreleased]` 를 `## [0.6.0] - 2026-08-29` 로 바꾸고 그 위에 빈 `## [Unreleased]` 를 새로 만든다.

MINOR 인 이유: 신규 기능이며 기존 g++ 경로는 호환이 유지된다. 다만 **루트에 빌드 디스크립터를 둔 프로젝트는 동작이 바뀐다**(거부 → 어댑터). 그 사실을 CHANGELOG 의 `### Changed` 에 명시한다.

- [ ] **Step 5: Commit, push and open the PR**

```bash
git add ici.toml pyproject.toml CHANGELOG.md
git commit -m "chore(release): 0.6.0"
git push -u origin feat/build-adapter
gh pr create --title "feat(build): CMake and qmake build adapters" --body "..."
```

- [ ] **Step 6: Merge and tag after CI passes**

```bash
gh pr merge --squash --delete-branch
git checkout main && git pull origin main
```

릴리스 워크플로가 `dist/ici.pyz` 와 `dist/ici.pyz.sha256` 를 게시하는지 확인한다. **`toy-projects` 계획은 이 에셋이 게시된 뒤에 시작할 수 있다.**

---

## 자체 검토 결과

**스펙 커버리지**

| 스펙 절 | 태스크 |
|---|---|
| §3.1 백엔드 프로토콜 | Task 1–6 |
| §3.2 백엔드 선택 | Task 1 |
| §3.3 명령 | Task 2, 3 |
| §3.4 커버리지 | Task 5, 6, 8 |
| §3.5 결과 파싱과 파일 경로 | Task 4, 8 |
| §3.6 결과·증거 계약 | Task 6, 7, 8 |
| §3.7 설정 계약 변화 | Task 8, 10 |
| §4.4 ici 픽스처 | Task 9 |
| §5 테스트 전략 | Task 9 |
| §6 CI | Task 10 |
| §4.1 loglens, §4.2 diskmap | **별도 계획** (toy-projects) |

**두 가지를 짚어둔다.**

`ici/viewer` 는 스펙 §4.3 이 "기존 g++ 경로 유지" 로 적었지만, **`viewer/` 를 프로젝트 루트로 검증하면 `viewer/CMakeLists.txt` 가 루트 디스크립터가 되어 어댑터 경로로 넘어간다.** 스펙 작성 시점에 놓친 부분이다. Task 11 Step 1–2 가 이것을 실제로 확인하고, 어느 쪽이든 g++ 경로의 회귀 증거가 남도록 처리한다.

`qmake` 테스트 결과 계약(Task 3, 4)은 스펙 §3.5 가 "가장 덜 확정된 부분" 이라고 적은 곳이다. Task 9 의 qmake 픽스처가 처음으로 실물 검증을 하며, `TESTARGS=-xunitxml` 이 기대대로 동작하지 않으면 Task 3·4 로 돌아가 고친다.
