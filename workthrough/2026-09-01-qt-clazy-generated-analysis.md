# I4-2 Qt clazy·생성 단계 분석

## Overview

I4-2는 I3의 immutable `AnalysisContext`를 Qt-aware static analysis까지 확장한다. 구현은
canonical clazy capability와 두 provider 경로, strict diagnostic normalization, Qt
`moc`/`uic`/`rcc` generated-code linkage, Qt 5/Qt 6 compile evidence를 `lint` facade의
독립적인 evidence로 묶는다. 기본 실행은 source와 compilation context를 읽기만 하며, 실제
원격 PR/merge/release 증거는 이 workthrough 작성 시점에 아직 없다.

## Context

기존 C++ lint는 compiler와 clang-tidy를 exact compilation context로 replay했지만, Qt
프로젝트의 framework-specific diagnostics와 build-system generated sources가 별도 검증
대상이 아니었다. 특히 `Q_OBJECT`, `.ui`, `.qrc`가 파일에 존재해도 generated output이
활성 compile command에 연결됐는지와 Qt major compatibility를 확인하지 않으면 Qt 분석의
성공을 주장할 수 없다.

## Changes Made

### 1. Clazy capability and policy

- `src/ici/config.py`와 `src/ici/config_schema.py`에 `clazy = auto|required|off`,
  `clazy_profile = level0|level1`, bounded unique `clazy_checks`를 추가했다.
- global `ici.profile`은 engine set만 고르고 clazy rule semantics를 바꾸지 않는다.
  clazy profile의 기본은 명시적 `level0`이며, level2/manual noisy checks는
  `clazy_checks`를 직접 설정할 때만 선택한다.
- `src/ici/core/toolchain.py`는 canonical `clazy`를 `clazy-standalone` 우선으로 probe하고,
  `clazy` alias를 compiler-wrapper provider로 기록한다. `src/ici/core/support.py`는
  `required` 정책에서 clazy를 optional에서 required로 승격한다.

### 2. Exact-context clazy adapter

- `src/ici/engines/_clazy.py`는 loader가 만든 covered production `CompilationUnit`만
  선택한다. standalone 경로는 다음 형태의 argv를 사용한다.

  ```text
  clazy-standalone --checks=<checks> --only-qt <source> -- <sanitized compiler args>
  ```

- wrapper 경로는 approved `clang++`를 `CLANGXX`로 고정하고 `CLAZY_CHECKS`를 replacement
  environment로 전달한다.
- 두 경로 모두 `build_replay_command`/`tooling_arguments`를 사용하고 compilation database
  재탐색, `-p`, `--fix`, shell, source/context 변경을 하지 않는다. stdin은 빈 입력으로 닫고
  argv·path·version·return code·timeout/truncation을 `ToolEvidence`에 남긴다.
- 최대 2,048 translation units, unit당 120초, 전체 600초 global budget과 1,000,000자
  output bound를 적용한다. context/coverage/replay/process/parser 실패, timeout,
  truncation, budget 초과는 partial clean 결과로 바꾸지 않고 `ERROR`/`NOT_RUN`으로 닫는다.

### 3. Strict diagnostic families and findings

- `src/ici/engines/_cpp_diagnostics.py`에 `parse_clazy_diagnostics`를 추가했다.
  `-Wclazy-*` rule shape만 허용하며 located diagnostic과 parent rule을 따르는 note의
  file/line/column을 보존한다. malformed/unknown output과 clazy fix-it output은 atomic
  parse error다.
- normalized diagnostic은 `family = "clazy"`, stable `clazy-<check>` rule ID와
  project-relative `InspectionTarget`을 갖는다.
- `src/ici/engines/lint.py`는 clazy diagnostics를 별도 family counter와 ToolEvidence로
  보고하고 다음 category mapping을 사용한다.

  | Rule token | Finding category |
  |---|---|
  | `lifetime`, `ownership`, `parent-less`, `qobject-cast` | `RESOURCE` |
  | `qt6`, `deprecated`, `qstring-arg`, `qt-keyword` | `COMPATIBILITY` |
  | `qobject`, `connect`, `signal`, `slot`, `qevent-cast` | `CORRECTNESS` |
  | otherwise (container detach/temporary 포함) | `MAINTAINABILITY` |

### 4. Qt generated-code and compatibility verification

- `src/ici/engines/_qt_codegen.py`는 source scope의 `.ui`, `.qrc`, 그리고 C++ comments/
  literals를 제외한 실제 `Q_OBJECT` 선언을 bounded하게 발견한다.
- exact compilation database와 project-contained include roots를 이용해 다음 linkage를
  원본 입력 파일과 1-indexed line target에 기록한다.
  - `.ui` → `ui_<stem>.h` active include (`QtUicLinkage`)
  - `.qrc` → `qrc_<stem>.cpp` generated compilation unit (`QtRccLinkage`)
  - `Q_OBJECT` → `moc_<stem>.cpp`, `<stem>.moc`, 또는 `mocs_compilation.cpp`
    (`QtMocLinkage`)
- CMake AUTOMOC/AUTOUIC/AUTORCC와 qmake direct generated unit을 모두 지원한다. exact
  include path, define, argv에서 Qt 5/Qt 6 major를 구분하고, successful compiler replay가
  확인된 경우에만 `QtCompatibility:Qt5`/`QtCompatibility:Qt6` PASS를 생성한다. major 불명확
  또는 replay 부재는 WARN이며, conflicting major는 compatibility FAIL이다.
- generated verifier는 generator를 다시 실행하지 않는다. generated output 존재만으로
  성공을 만들지 않고 exact context linkage를 요구한다.

### 5. Cache and CI integration

- `src/ici/core/cache_identity.py`의 source suffix에 `.ui`와 `.qrc`를 포함했다.
- `LintEngine.CACHE_IMPLEMENTATION_MODULES`에 `_clazy`, `_cpp_tooling`, `_qt_codegen`와
  관련 diagnostic helper를 명시해 adapter implementation 변경이 stale cache를 만들지
  않게 했다.
- `.github/workflows/ci.yml`와 `release.yml`은 clazy를 설치하고
  `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`을 설정한다. 실제 tool E2E가 환경에서 skip되면
  required gate가 실패한다.

## Verification Results

관련 parser/config/adapter/codegen/cache와 C++ process-level E2E를 묶은 Python 3.10 focused
run:

```text
uv run --python 3.10 pytest tests/test_clazy.py tests/test_qt_codegen.py \
  tests/test_cpp_diagnostics.py tests/test_config.py tests/test_support_matrix.py \
  tests/test_toolchain_capabilities.py tests/test_cpp_tool_e2e.py
262 passed, 3 skipped in 1.43s
```

세 skip은 현재 로컬 환경에서 `clang-tidy`·`clazy`가 설치되지 않은 process-level 조건이다.
CI/release의 `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1` 경로에서는 같은 조건이 skip 대신 failure가
되어야 한다. 이 문서에 기록한 결과는 local evidence이며, PR CI·sticky HTML comment·Pages,
toy-projects BuildScope B5 및 새 release evidence를 의미하지 않는다.

## Next Steps

- `feat/qt-analysis`를 PR로 제출하고 full Python 3.10 quality gate, 실제 clazy process E2E,
  Qt5/Qt6 matrix, sticky comment와 hosted HTML을 독립 확인한다.
- toy-projects BuildScope B5에 `.ui`, `.qrc`, Q_OBJECT 경로를 추가하거나 기존 fixture를
  확장하고, released ici로 PR 검증을 수행한다.
- 위 원격 증거가 모두 green인 뒤에만 I4-2 delivery 상태와 적절한 ici release version을
  기록한다. I4-3/I4-4와 I4 전체 checkpoint는 그 이후에도 별도 완료 조건을 따른다.
