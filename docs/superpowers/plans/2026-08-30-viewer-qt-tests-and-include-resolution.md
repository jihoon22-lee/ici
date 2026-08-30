# viewer Qt 셸 테스트와 include 해석 개선 (ici)

> **병합 상태 (2026-08-31): 완료·역사 보존.** cycle 경로 해석 개선은 [ici PR #79](https://github.com/jihoon22-lee/ici/pull/79),
> viewer Qt 셸·실패 상태·Qt 5/6 검증은 [PR #81](https://github.com/jihoon22-lee/ici/pull/81)로
> `main`에 병합됐다. PR #81은 Qt 6 4/4, Qt 5.15 강제 4/4, Qt-free CLI와 Merge Gate를
> 통과했다. 아래 원래 체크박스는 설계와 회귀 근거이며 활성 작업으로 재실행하지 않는다.

> **상태 보정:** 이 문서는 마스터 계획
> `2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`의 I0 세부 입력이다. suffix include
> 해석은 compiler-exact B-2 완료가 아닌 중간 휴리스틱이다. missing/malformed viewer 테스트는
> 먼저 정상 report를 열어 stale state를 만든 뒤 model·suite·labels·title 초기화를 검증한다.
> root CMake는 `ICIRV_BUILD_GUI=OFF`에서 Qt 없이 CLI configure가 가능해야 한다. 이 보정이 아래
> 원래 step보다 우선한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `viewer` 의 Qt 셸에 단위 테스트를 붙이고, `cycle` 엔진이 `#include` 를 basename 이 아니라 경로로 해석하게 한다(B-2).

**Architecture:** `viewer` 의 GUI 는 지금 독립 CMake 프로젝트라 루트에서 테스트가 링크할 수 없다. `loglens` 와 같은 모양으로 루트가 `add_subdirectory(src/gui)` 하고 GUI 를 라이브러리로 분리한다. Qt 는 버전을 고정하지 않고 설치된 것을 찾는다. `cycle` 의 include 해석은 순수 함수라 도구 없이 TDD 가 된다.

**Tech Stack:** Python 3.10, C++17, Qt (설치된 6 또는 5), CMake/CTest, pytest

**Spec:** 이 계획은 대화에서 합의된 설계를 구현한다. 별도 스펙 문서는 없다 — 두 변경 모두 기존 구조 안에서 끝나고, 새 서브시스템이나 외부 계약을 만들지 않는다. 근거는 각 태스크에 적었다.

## Global Constraints

- **Python 3.10 하한.** `tomllib` 금지(`tomli` 사용), 3.11+ 문법 금지
- **C++17.** `viewer` 는 `CMAKE_CXX_STANDARD 17` 유지
- **Qt 버전을 고정하지 않는다.** `find_package(QT NAMES Qt6 Qt5 ...)` 후 `Qt${QT_VERSION_MAJOR}::` 를 쓴다. 설치된 것이 잡힌다
- **`icirv` 는 Qt 를 링크하지 않는다.** 정적 링크로 RHEL 8(glibc 2.28, Qt 6 패키지 없음)에 나가는 바이너리다. 릴리스 워크플로가 정적 링크가 아니면 빌드를 실패시킨다
- **테스트는 프로젝트 루트에서 실행된다** (`WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}`). C-9 계약
- **위치 추적 필수.** 모든 `InspectionTarget` 은 파일 경로와 라인 번호를 갖는다
- **품질 게이트**: `uv run --python 3.10 pytest`, `uvx ruff check .`, `uvx ruff format --check .`, `./scripts/build-pyz.sh`, `./scripts/smoke.sh`, 그리고 **`./dist/ici.pyz verify` 자기 검증** — 기준선은 `Pass 7 · Warn 5 · Fail 0`
- **브랜치**: `feat/viewer-qt-tests` 에서 작업하고 PR 로 병합한다. `main` 직접 작업 금지

---

### Task 1: `cycle` 이 include 를 경로로 해석하게 한다 (B-2)

**Files:**
- Modify: `src/ici/engines/cycle.py:118-150` (`_build_cpp_graph`)
- Test: `tests/test_cycle.py`

**Interfaces:**
- Consumes: 없음
- Produces: `_resolve_include(inc_name: str, files: list[Path]) -> Path | None`

`#include "..."` 를 **basename 만으로** 해석한다. 같은 basename 이 둘 이상이면 잘못된 엣지를 만드느니 낫다는 이유로 엣지를 **버린다**. 안전한 선택이지만 대가가 있다 — `#include "core/format.hpp"` 는 디렉터리까지 적혀 있어서 애초에 모호하지 않은데도 basename 으로 깎아내린 뒤 모호하다고 판정한다. 리포트에는 아무 표시도 남지 않는다.

- [ ] **Step 1: Write the failing test**

`tests/test_cycle.py` 에 추가한다.

`_CFG` 와 `CycleEngine` 은 파일 상단에 이미 import 되어 있다. 기존 테스트처럼 `ici.toml` 없이 `src/` 아래 파일만 만든다.

```python
def test_include_with_directories_resolves_even_when_basenames_collide(tmp_path: Path):
    """A path in the include is information; dropping it invents ambiguity.

    Two files named format.hpp make the basename ambiguous, but
    "core/format.hpp" names exactly one of them. Resolving by basename throws
    that away and silently drops the edge — the cycle goes unreported and the
    report says nothing about why.
    """
    core = tmp_path / "src" / "core"
    gui = tmp_path / "src" / "gui"
    core.mkdir(parents=True)
    gui.mkdir(parents=True)
    (core / "format.hpp").write_text('#include "gui/format.hpp"\n', encoding="utf-8")
    (gui / "format.hpp").write_text('#include "core/format.hpp"\n', encoding="utf-8")

    result = CycleEngine(tmp_path, _CFG).run()

    assert result.extra["total_cycles"] == 1, result.summary


def test_bare_basename_stays_ambiguous_when_several_files_match(tmp_path: Path):
    """Without directories there is nothing to disambiguate with, so the edge
    is still dropped rather than guessed — a false edge would report a cycle
    that is not there."""
    core = tmp_path / "src" / "core"
    gui = tmp_path / "src" / "gui"
    core.mkdir(parents=True)
    gui.mkdir(parents=True)
    (core / "format.hpp").write_text('#include "format.hpp"\n', encoding="utf-8")
    (gui / "format.hpp").write_text('#include "format.hpp"\n', encoding="utf-8")

    result = CycleEngine(tmp_path, _CFG).run()

    assert result.extra["total_cycles"] == 0, result.summary
```

- [ ] **Step 2: Run the tests to verify the first one fails**

Run: `uv run --python 3.10 pytest tests/test_cycle.py -k "basenames_collide or bare_basename" -v`
Expected: `test_include_with_directories_resolves_even_when_basenames_collide` FAILs — 엣지가 버려져 순환이 보고되지 않는다. 두 번째는 통과한다(현재 동작).

- [ ] **Step 3: Resolve by path suffix**

`src/ici/engines/cycle.py` 의 `_build_cpp_graph` 를 고친다. 파일 상단 import 에 `from pathlib import Path, PurePosixPath` 를 반영한다.

```python
def _include_matches(candidate: Path, wanted: tuple[str, ...]) -> bool:
    """Whether candidate's path ends with every component the include named."""

    parts = candidate.parts
    return len(parts) >= len(wanted) and parts[-len(wanted) :] == wanted


def _resolve_include(inc_name: str, files: list[Path]) -> Path | None:
    """Resolve an #include against the project's files by path, not basename.

    The directories in an include are information. "core/format.hpp" names
    exactly one file even in a project holding several format.hpp, and matching
    on the basename alone throws that away and then calls the result ambiguous.
    A bare "format.hpp" really is ambiguous when several match, and is still
    left unresolved rather than guessed.
    """

    wanted = PurePosixPath(inc_name).parts
    if not wanted:
        return None
    matches = [f for f in files if _include_matches(f, wanted)]
    return matches[0] if len(matches) == 1 else None
```

`_build_cpp_graph` 본문에서 `by_name`/`unambiguous` 를 지우고 해석부를 바꾼다.

```python
    all_files = _iter_cpp_and_headers(project_root, config)

    graph: dict[Path, set[Path]] = {}
    known: dict[Path, Path] = {}
    for f in all_files:
        resolved_f = f.resolve()
        known[resolved_f] = f
        graph[resolved_f] = set()
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _INCLUDE_RE.finditer(content):
            target = _resolve_include(match.group(1), all_files)
            if target and target.resolve() != resolved_f:
                graph[resolved_f].add(target.resolve())
    return graph, known
```

독스트링도 새 동작에 맞게 고친다.

```python
    """Build file -> included-file graph including headers.

    ``#include "..."`` is resolved by matching the whole path the include gave,
    not just its basename: "core/format.hpp" names one file even where several
    format.hpp exist. A bare basename that several files answer to is genuinely
    ambiguous and is left unresolved — a false edge would report a cycle that
    is not there.
    """
```

- [ ] **Step 4: Run the tests to verify both pass**

Run: `uv run --python 3.10 pytest tests/test_cycle.py -v`
Expected: PASS 전부

- [ ] **Step 5: Run the C++ fixture suite**

`cycle` 은 `examples/cpp-fixtures/cycle_pair` 로 E2E 검증된다. 해석 규칙을 바꿨으니 기존 탐지가 그대로인지 본다.

Run: `uv run --python 3.10 pytest tests/test_cpp_e2e.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ici/engines/cycle.py tests/test_cycle.py
git commit -m "fix(cycle): resolve includes by path, not basename alone"
```

---

### Task 2: viewer 의 GUI 를 루트 프로젝트로 들인다

**Files:**
- Modify: `viewer/CMakeLists.txt`
- Modify: `viewer/src/gui/CMakeLists.txt` (독립 프로젝트 → 서브디렉터리)
- Modify: `.github/workflows/ci.yml` (viewer GUI 빌드 스텝)
- Modify: `.github/workflows/release.yml` (viewer GUI 빌드 스텝)

**Interfaces:**
- Consumes: 없음
- Produces: CMake 타깃 `icirv_gui`(정적 라이브러리), `icirv-gui`(실행 파일). 루트에서 `--target icirv-gui` 로 빌드된다

GUI 가 독립 프로젝트라 루트의 테스트가 링크할 수 없다. Task 3 의 셸 테스트가 성립하려면 먼저 이 구조가 바뀌어야 한다.

**`icirv` 는 계속 Qt 를 링크하지 않고, Qt 없는 머신에서도 configure할 수 있어야 한다.** GUI는
`ICIRV_BUILD_GUI` 옵션 안에서만 Qt를 찾는다. ici verify와 GUI CI는 ON, RHEL 8 static CLI release는
OFF를 명시한다.

- [ ] **Step 1: Rewrite the root CMakeLists**

`viewer/CMakeLists.txt` 를 아래로 바꾼다.

```cmake
cmake_minimum_required(VERSION 3.16)
project(icirv LANGUAGES CXX)

# The project ici verifies (the repository root is a Python project with its own
# ici.toml). ici reads this file and drives configure, build and CTest from here.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(ICIRV_BUILD_GUI "Build the Qt report viewer" ON)

if(ICIRV_BUILD_GUI)
    set(CMAKE_AUTOMOC ON)
    # Whichever Qt the machine has. Development here is Qt 6; the closed network
    # uses Qt 5.15. The static CLI build sets ICIRV_BUILD_GUI=OFF and needs no Qt.
    find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Widgets Test)
    find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Widgets Test)
endif()

# The parsing and model code lives in a library so the tests can link it without
# dragging in main().
add_library(icirv_core STATIC
    src/json_parser.cpp
    src/json_value.cpp
    src/report_model.cpp
    src/summary.cpp
)
target_include_directories(icirv_core PUBLIC include)

# Qt-free on purpose. This is the binary that goes to the closed network, where
# there is no Qt 6 and glibc is 2.28, so it links statically and cannot take a
# toolkit dependency. Nothing forbids Qt in icirv_core as a rule — this one
# binary is the reason it stays out.
add_executable(icirv src/main.cpp)
target_link_libraries(icirv PRIVATE icirv_core)

option(ICIRV_STATIC "Link the CLI statically" ON)
# -static and -fsanitize=address cannot be combined, and ici's sanitize engine
# configures its own tree with the sanitizers on. Asking for both produces a
# build failure rather than a diagnosis, so the static link stands down when
# this is a sanitizer build.
if(ICIRV_STATIC AND NOT CMAKE_CXX_FLAGS MATCHES "-fsanitize")
    target_link_options(icirv PRIVATE -static)
endif()

if(ICIRV_BUILD_GUI)
    add_subdirectory(src/gui)
endif()

enable_testing()

foreach(name IN ITEMS json_parser report_model summary)
    add_executable(test_${name} tests/test_${name}.cpp)
    target_link_libraries(test_${name} PRIVATE icirv_core)
    target_include_directories(test_${name} PRIVATE tests)
    # Always the project root. tests/fixtures.hpp opens tests/data/ by relative
    # path, which is the contract C-9 fixed: a test must not depend on who
    # launched it or on whether gcov happened to be installed.
    add_test(NAME test_${name} COMMAND test_${name} WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endforeach()
```

- [ ] **Step 2: Turn the GUI CMakeLists into a subdirectory that exports a library**

`viewer/src/gui/CMakeLists.txt` 를 아래로 바꾼다. `cmake_minimum_required`/`project`/`find_package` 는 루트가 이미 했으므로 지운다.

```cmake
# Included from the project root, which already defines icirv_core and found Qt.
#
# The widgets live in a library rather than only in the executable so tests can
# link them. A Q_OBJECT class needs moc-generated sources, and the headers are
# listed explicitly because they sit under include/ rather than beside their
# .cpp — AUTOMOC finds a Q_OBJECT header on its own only when it shares a
# directory and a basename with a source in the target.
add_library(icirv_gui STATIC
    engine_tree_model.cpp
    main_window.cpp
    ${CMAKE_SOURCE_DIR}/include/icirv/gui/engine_tree_model.hpp
    ${CMAKE_SOURCE_DIR}/include/icirv/gui/main_window.hpp
)
target_link_libraries(icirv_gui PUBLIC icirv_core Qt${QT_VERSION_MAJOR}::Widgets)

add_executable(icirv-gui gui_main.cpp)
target_link_libraries(icirv-gui PRIVATE icirv_gui)
```

- [ ] **Step 3: Build both targets and smoke the GUI**

```bash
cd viewer
rm -rf build/check
cmake -S . -B build/check -DCMAKE_BUILD_TYPE=Release
cmake --build build/check --parallel
ldd build/check/icirv 2>&1 | head -1        # "not a dynamic executable" 이어야 한다
QT_QPA_PLATFORM=offscreen timeout 5 ./build/check/src/gui/icirv-gui verify_report.json &
sleep 3 && kill %1

# A machine building only the static CLI must not need Qt even at configure time.
rm -rf build/cli-only
cmake -S . -B build/cli-only -DCMAKE_BUILD_TYPE=Release -DICIRV_BUILD_GUI=OFF
cmake --build build/cli-only --parallel --target icirv
```
Expected: 둘 다 빌드되고, `icirv` 는 여전히 정적이며, GUI 가 3초를 버틴다. cli-only configure
log에는 Qt 탐색이 없어야 한다.

- [ ] **Step 4: Update both workflows**

`.github/workflows/ci.yml` 의 `viewer-gui` 잡에서 빌드 스텝을 바꾼다.

```yaml
      - name: Configure and build
        working-directory: viewer
        run: |
          cmake -S . -B build/gui -DCMAKE_BUILD_TYPE=Release
          cmake --build build/gui --parallel --target icirv-gui
```

그 잡의 스모크 스텝에서 바이너리 경로를 `build/gui/src/gui/icirv-gui` 로 바꾼다.

`.github/workflows/release.yml` 의 `Build Viewer GUI (development asset)` 스텝도 같은 모양으로
바꾸고, 복사 경로를 `viewer/build/gui/src/gui/icirv-gui` 로 맞춘다. `Build Viewer CLI (static)`
configure에는 `-DICIRV_BUILD_GUI=OFF`를, build에는 `--target icirv`를 명시한다.

- [ ] **Step 5: Verify the workflows parse and viewer still verifies**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); yaml.safe_load(open('.github/workflows/release.yml')); print('yaml ok')"
cd viewer && rm -rf build/ici-* && QT_QPA_PLATFORM=offscreen ../dist/ici.pyz verify 2>&1 | tail -4
```
Expected: `yaml ok`, 그리고 `Suite: PASS`

- [ ] **Step 6: Commit**

```bash
git add viewer/CMakeLists.txt viewer/src/gui/CMakeLists.txt .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "build(viewer): bring the GUI into the root project"
```

---

### Task 3: viewer 셸에 Qt 테스트

**Files:**
- Create: `viewer/tests/test_main_window.cpp`
- Modify: `viewer/CMakeLists.txt` (테스트 타깃 추가)

**Interfaces:**
- Consumes: `icirv_gui` (Task 2), `MainWindow::openReport(const QString&)`
- Produces: 없음

`viewer/src/gui/main_window.cpp` 는 지금 단위 테스트가 없다. 리포트를 읽어 트리를 채우는 경로가 핵심이고, `viewer/tests/data/ici_self_report.json` 이 이미 있어 실물로 검증할 수 있다.

- [ ] **Step 1: Write the failing test**

`viewer/tests/test_main_window.cpp`

```cpp
#include <QSignalSpy>
#include <QtTest>

#include <QTreeView>

#include "icirv/gui/main_window.hpp"

class TestMainWindow : public QObject {
    Q_OBJECT

private slots:
    void openingARealReportFillsTheTree();
    void openingAMissingFileLeavesTheTreeEmpty();
    void openingMalformedJsonLeavesTheTreeEmpty();
};

namespace {

// The tree is the only thing a user sees, so its row count is what "the report
// loaded" means here. Reaching it by object type rather than by name keeps the
// test off the widget's private members.
int treeRowCount(MainWindow& window) {
    auto* tree = window.findChild<QTreeView*>();
    if (tree == nullptr || tree->model() == nullptr) {
        return -1;
    }
    return tree->model()->rowCount(QModelIndex());
}

} // namespace

void TestMainWindow::openingARealReportFillsTheTree() {
    MainWindow window;
    // Project-relative: ici runs test binaries from the project root (C-9).
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));

    // A real ici report always carries engines; an empty tree would mean the
    // parse silently produced nothing.
    QVERIFY(treeRowCount(window) > 0);
}

void TestMainWindow::openingAMissingFileLeavesTheTreeEmpty() {
    MainWindow window;
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));
    QVERIFY(treeRowCount(window) > 0);

    window.openReport(QStringLiteral("tests/data/does-not-exist.json"));

    // The valid report must not survive a failed replacement.
    QCOMPARE(treeRowCount(window), 0);
}

void TestMainWindow::openingMalformedJsonLeavesTheTreeEmpty() {
    QTemporaryFile broken;
    QVERIFY(broken.open());
    broken.write("{ this is not json");
    broken.close();

    MainWindow window;
    window.openReport(QStringLiteral("tests/data/ici_self_report.json"));
    QVERIFY(treeRowCount(window) > 0);

    window.openReport(broken.fileName());

    QCOMPARE(treeRowCount(window), 0);
}

QTEST_MAIN(TestMainWindow)
#include "test_main_window.moc"
```

`viewer/CMakeLists.txt` 의 `foreach` 아래에 더한다.

```cmake
# The Qt shell test links the widget library and Qt Test. Before ici 0.6.0 the
# gate could not build this at all: moc never ran, so MainWindow had no vtable.
add_executable(test_main_window tests/test_main_window.cpp)
target_link_libraries(test_main_window PRIVATE icirv_gui Qt${QT_VERSION_MAJOR}::Test)
target_include_directories(test_main_window PRIVATE tests)
add_test(NAME test_main_window COMMAND test_main_window WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd viewer && cmake -S . -B build/check -DCMAKE_BUILD_TYPE=Debug && cmake --build build/check --parallel
```
Expected: 컴파일 또는 링크 실패. `openReport` 가 실패 경로에서 트리를 비우지 않으면 실행 시 실패한다.

- [ ] **Step 3: Make the failure paths leave the tree empty**

`viewer/src/gui/main_window.cpp` 의 `openReport` 를 읽고, 파일을 못 읽거나 JSON 파싱이 실패했을
때 `suite_`, model, gate/score label과 loaded title을 함께 초기화하고 원인 status를 남긴다. tree
row만 아니라 label/title도 objectName 또는 안정적인 test seam으로 단언한다. fresh window에서
빈 tree만 확인하는 테스트로 대체하지 않는다.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd viewer && cmake --build build/check --parallel && QT_QPA_PLATFORM=offscreen ctest --test-dir build/check --output-on-failure

# Force the installed Qt 5 instead of letting NAMES choose Qt 6 first.
rm -rf build/qt5
cmake -S . -B build/qt5 -DCMAKE_BUILD_TYPE=Debug -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON
cmake --build build/qt5 --parallel
QT_QPA_PLATFORM=offscreen ctest --test-dir build/qt5 --output-on-failure
```
Expected: Qt 6/Qt 5에서 각각 4/4 통과 (기존 3 + 새 1). configure log와 link target에서
선택된 major를 확인한다.

- [ ] **Step 5: Verify through ici and check the coverage move**

```bash
cd viewer && rm -rf build/ici-* && QT_QPA_PLATFORM=offscreen ../dist/ici.pyz verify 2>&1 | tail -6
```
Expected: `Suite: PASS`. `src/gui/main_window.cpp` 의 커버리지가 0% 에서 올라간 것을 리포트에서 확인한다.

- [ ] **Step 6: Commit**

```bash
git add viewer/tests/test_main_window.cpp viewer/CMakeLists.txt viewer/src/gui/main_window.cpp
git commit -m "test(viewer): cover the report-loading path in the shell"
```

---

### Task 4: 문서

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/engine-reference.md` (`cycle` 항목)
- Modify: `viewer/ici.toml` (`cpp_pkg_config`)

**Interfaces:**
- Consumes: Task 1–3
- Produces: 없음

- [ ] **Step 1: Make the pkg-config setting version-agnostic**

`viewer/ici.toml` 의 `cpp_pkg_config` 를 바꾼다. `get_cpp_pkg_config_flags` 는 **해석에 실패한 패키지를 건너뛰므로** 둘을 나열하면 설치된 쪽이 잡힌다. ici 수정은 필요 없다.

```toml
# Whichever Qt the machine has. ici skips a package pkg-config cannot resolve,
# so listing both makes the same config work on a Qt 6 desktop and a Qt 5.15
# closed-network box.
cpp_pkg_config = ["Qt6Widgets", "Qt5Widgets"]
```

- [ ] **Step 2: Document the cycle change**

`docs/engine-reference.md` 의 `cycle` 엔진 설명에 한 줄을 더한다.

```markdown
- **include 해석**: `#include "a/b/c.hpp"` 는 **적힌 경로 전체**로 해석합니다. 디렉터리가 붙어 있으면
  같은 파일명이 여러 개여도 하나로 좁혀집니다. 디렉터리 없는 `#include "c.hpp"` 가 여러 파일에
  해당하면 잘못된 엣지를 만드느니 해석하지 않고 넘어갑니다.
```

- [ ] **Step 3: Update the CHANGELOG**

`## [Unreleased]` 아래에 넣는다.

```markdown
### Fixed
- **`cycle` 이 디렉터리가 붙은 include 를 basename 으로 깎아내렸습니다**: `#include "core/format.hpp"` 는 `format.hpp` 가 여러 개인 프로젝트에서도 하나를 정확히 가리키는데, basename 만 보고 모호하다고 판정해 **엣지를 조용히 버렸습니다.** 순환이 있어도 보고되지 않고 리포트에 아무 표시도 남지 않았습니다. 이제 적힌 경로 전체로 해석합니다.

### Changed
- **`viewer` 의 GUI 가 루트 CMake 프로젝트로 들어왔습니다**: 독립 프로젝트였던 `src/gui` 가 서브디렉터리가 되어, 루트의 테스트가 위젯 라이브러리를 링크할 수 있습니다. `icirv` CLI 는 계속 Qt 를 링크하지 않으며 정적 링크도 그대로입니다.
- **Qt 버전을 고정하지 않습니다**: `find_package(QT NAMES Qt6 Qt5 ...)` 로 설치된 Qt 를 찾습니다.
```

- [ ] **Step 4: Run the full gate**

```bash
uv run --python 3.10 pytest
uvx ruff check . && uvx ruff format --check .
./scripts/build-pyz.sh && ./scripts/smoke.sh
./dist/ici.pyz verify 2>&1 | tail -3
```
Expected: 전부 통과, 자기 검증은 `Pass 7 · Warn 5 · Fail 0` 기준선

- [ ] **Step 5: Commit, push and open the PR**

```bash
git add CHANGELOG.md docs/engine-reference.md viewer/ici.toml
git commit -m "docs: record the cycle fix and the viewer restructure"
git push -u origin feat/viewer-qt-tests
gh pr create --title "feat(viewer): Qt shell tests, and resolve includes by path" --body "..."
```

---

## 자체 검토 결과

**커버리지**

| 합의된 항목 | 태스크 |
|---|---|
| B-2 include 해석 | Task 1 |
| viewer 루트 통합 | Task 2 |
| viewer Qt 셸 테스트 | Task 3 |
| Qt 버전 탐지 | Task 2 Step 1, Task 4 Step 1 |
| CI·릴리스 워크플로 | Task 2 Step 4 |

**두 가지를 짚어둔다.**

Task 3 Step 3 은 **조건부 스텝**이다. `openReport` 의 실패 경로가 이미 모델을 비우는지 코드를 읽어봐야 알 수 있어서, 이미 그렇다면 변경 없이 넘어가라고 적었다. 테스트를 먼저 쓰고 실행해 보면 어느 쪽인지 바로 드러난다.

**`icirv` 의 Qt-free 성질은 규칙이 아니라 이 바이너리 하나의 요구다.** "Qt 는 core 에 못 들어간다" 는 일반 원칙은 폐기됐고, `icirv_core` 가 Qt 를 안 쓰는 이유는 RHEL 8 에 정적으로 나가는 바이너리가 거기 매달려 있기 때문이다. 루트 CMakeLists 주석에 그 구분을 적었다.
