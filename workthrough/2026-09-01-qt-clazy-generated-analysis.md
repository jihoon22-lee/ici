# I4-2 Qt clazy·생성 단계 분석

## Overview

I4-2는 I3의 immutable `AnalysisContext`를 Qt-aware static analysis까지 확장한다. 구현은
canonical clazy capability와 두 provider 경로, strict diagnostic normalization, Qt
`moc`/`uic`/`rcc` generated-code linkage, Qt 5/Qt 6 compile evidence를 `lint` facade의
독립적인 evidence로 묶는다. 기본 실행은 source와 compilation context를 읽기만 한다. 구현은
PR #122의 run 33499500259와 squash merge 뒤 exact-main run 33500281653에서 검증되어 ici I4-2
원격 acceptance까지 완료됐으며, PR/main ici·viewer Pages 감사도 통과했다. v0.10.0 release
artifact와 toy-projects BuildScope B5는 다음 단계다.

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
- 우선 후보가 실행 또는 version parsing에 실패하면 다음 alias를 probe한다. 모든 후보가
  실패한 경우에는 첫 실패 증거를 보존하며, 정상 fallback provider의 alias는 결정적으로 기록한다.

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
- clazy와 함께 출력되는 일반 compiler warning/note는 동일한 path·line·message·rule bound로
  원자 검증한 뒤 제외한다. compiler lint replay가 이미 이를 별도로 보고하므로 중복 finding과
  clazy parser 오판을 모두 피하며, compiler error나 unknown text는 계속 거부한다.
- Ubuntu Noble의 clazy 1.11이 내보내는 legacy raw-source/caret/replacement context는 located
  diagnostic의 project-contained 실제 source line과 raw text가 exact match할 때만 허용한다.
  caret 뒤의 replacement preview는 8,192자 이하 한 줄로 제한하며, source mismatch,
  forged/duplicate/oversized preview와 unknown text는 partial finding 없이 atomic reject한다.
  rule selection, diagnostic construction, context state consumption을 helper로 분리해 parser의
  최대 cyclomatic complexity를 35에서 16으로 낮췄다.
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
  literals·preprocessor directive·확실히 비활성인 `#if 0` branch를 제외한 `Q_OBJECT` 선언을
  bounded하게 발견한다.
- exact compilation database와 project-contained include roots를 이용해 다음 linkage를
  원본 입력 파일과 1-indexed line target에 기록한다.
  - `.ui` → `ui_<stem>.h` bounded indirect translation-unit include linkage (`QtUicLinkage`)
  - `.qrc` → `qrc_<stem>.cpp` generated compilation unit (`QtRccLinkage`)
  - `Q_OBJECT` → `moc_<stem>.cpp`, `<stem>.moc`, 또는 `mocs_compilation.cpp`
    (`QtMocLinkage`)
- CMake AUTOMOC/AUTOUIC/AUTORCC와 qmake direct generated unit을 모두 지원한다. generated
  compilation unit도 exact compiler replay 대상이며, structural linkage는 successful replay가
  있어야 PASS다. 중복 generated stem은 오연결하지 않고 WARN으로 닫는다. exact
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

### 6. Self-dogfood maintainability remediation

- 원격 run 33498022877은 1,511 tests와 actual clazy E2E, Qt5/Qt6 build를 통과한 뒤 새 I4
  함수 네 개의 critical complexity를 탐지했다: capability probing 31, clazy parser 35,
  C++ source masker 27, generated-code verifier 36.
- candidate iteration/metadata validation, stateful C++ masking, clazy rule/context state,
  UI/RCC/MOC target construction을 각각 focused helper로 분리했다. `verify_qt_codegen`에서 같은
  이름의 branch-local 변수가 서로 다른 optional type으로 추론되던 mypy finding 두 건도
  per-kind helper 경계로 제거했다.
- `tests/test_complexity.py`에 toolchain/clazy/diagnostic/tooling/Qt helper가 complexity 25를
  넘지 못하도록 회귀 계약을 추가했다. 변경 후 이 범위의 최대 complexity는 기존
  `_split_clang_tidy_text`의 warning-only 24이고 새 `verify_qt_codegen`은 22다.

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
되어야 한다. 이 focused 결과는 local evidence로 별도 보존하며, 이후 PR #122의 head
`c3a8fe21639cecef395f0bc28777066401927da0`가 run `33499500259`에서 1,517/1,517 테스트와
네 개 actual compiler/clang-tidy/clazy process E2E, Qt 5/Qt 6, self/viewer dogfood,
publisher/sticky comment, Merge Gate를 통과했고 PR ici/viewer Pages 감사도 통과했다. PR은
`9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327`로 squash merge됐고 exact-main run `33500281653`도
같은 tool/matrix/dogfood/Merge Gate와 trusted main publication, main ici/viewer Pages 감사를
통과했다. 따라서 ici I4-2의 PR/main remote acceptance는 완료됐으며, 이
문서의 local 수치가 해당 원격 검증을 대체하지는 않는다.

리뷰 및 clazy 1.11 legacy context 보강 후 최종 mandatory local gate:

```text
uv run --python 3.10 pytest
1513 passed, 4 skipped in 58.99s
uvx ruff check .
All checks passed!
uvx ruff format --check .
158 files already formatted
./scripts/build-pyz.sh
10 distributions: py3-none-any; public schemas packaged; dist/ici.pyz built
./scripts/smoke.sh
all smoke tests passed; packaged self-verification and HTML Zero-CDN passed (verify exit 0)
```

네 skip은 로컬 `clang-tidy` 2건, `clazy` 1건, `clang++` 1건의 actual-process 조건이다.
`dist/ici.pyz`는 2,198,997 bytes이며 SHA-256은
`4eea5105ab503cd602575e8ad4b7195a060d87523252ddf135b025b68444a356`다.

Ubuntu Noble clazy 1.11의 실제 출력 형태는 다음과 같으며, parser regression은 실제 source
line 검증 뒤에만 legacy context state를 전진시킨다.

```text
src/datetime.cpp:2:37: warning: Use QDateTime::currentDateTimeUtc() instead [-Wclazy-qdatetime-utc]
QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }
                                    ^        ~~~~~
                                             ::currentDateTimeUtc()
1 warning generated.
```

세 차례의 원격 회귀는 순서대로 E2E inventory의 `clang++` probe 누락(run 33495534003), clazy
1.11에서 불안정한 `qcolor-from-literal` fixture(run 33495778941), 위 legacy context parser
호환성(run 33496761909)을 드러냈다. 각 원인은 capability fixture, cross-version
`qdatetime-utc` fixture, project-source exact-match parser로 분리해 보강했고, 후속 PR #122와
exact-main run에서 그 수정의 원격 acceptance를 완료했다.

## Next Steps

- [x] `feat/qt-analysis`를 PR로 제출하고 full Python 3.10 quality gate, 실제 clazy process E2E,
  Qt5/Qt6 matrix, sticky comment와 hosted HTML을 독립 확인했다. PR #122와 exact-main run
  `33500281653`에서 ici I4-2 원격 acceptance가 완료됐다.
- [ ] v0.10.0 release artifact/provenance를 확정한다.
- [ ] toy-projects BuildScope B5에 `.ui`, `.qrc`, Q_OBJECT 경로를 추가하거나 기존 fixture를
  확장하고, released ici로 PR 검증을 수행한다.
- [ ] I4-3/I4-4를 진행하고 I4 전체 checkpoint를 별도 완료 조건에 따라 닫는다.
