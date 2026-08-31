# Compiler-backed C++ Analysis Context

## Overview

I3-4의 C++ `lint`와 `cycle`이 고정된 compiler 명령이나 파일명 휴리스틱이 아니라
공유 `AnalysisContext`/`CompilationContext`를 사용하도록 연결했다. compilation database가
있을 때는 각 covered translation unit configuration의 sanitized direct GCC/Clang argv를
재생하고, DB가 진짜 없을 때만 명시적으로 `ESTIMATED` heuristic fallback을 사용한다.

## Context

기존 C++ lint는 source마다 `g++ -std=c++17` 명령을 만들었고, C++ include graph는
프로젝트 파일의 unique path suffix만으로 edge를 추정했다. 이 방식은 configuration별
define·standard·include search path와 generated/system header를 반영하지 못하고, 서로 다른
configuration에서만 존재하는 edge를 조합할 수 있었다. I3-4에서는 compile database가 이미
정규화한 context를 재사용하고, compiler가 실제로 선택한 include trace를 증거로 삼는다.

## Changes Made

### 1. Bounded compiler replay

- `src/ici/core/cpp_replay.py`가 `CompilationUnit`과 `CapabilityInventory`를 받아 한 번의
  안전한 replay command를 만든다.
- compiler는 capability inventory가 probe한 실행 가능한 직접 GCC/Clang driver여야 하며,
  source와 working directory는 project 경계 안에 있어야 한다.
- `-c`, output/dependency 생성 옵션은 제거하고 plugin·wrapper·toolchain 주입 등 unsafe option과
  positive allowlist 밖의 option은 fail-closed로 거부한다. syntax/include operation은 adapter가
  통제된 flag로 추가한다.
- compiler는 inherited override를 배제한 minimal replacement environment에서 실행되고, stdin은
  빈 입력으로 명시적으로 닫힌다.
- shell parsing이나 임의 compiler 발견을 하지 않으며, replay 거부는 성공으로 위장하지 않는다.

핵심 흐름은 다음과 같다.

```text
CompilationUnit argv
  -> revalidate project source/cwd and probed compiler
  -> remove unsafe compile/output/dependency/plugin/wrapper flags
  -> append controlled syntax or -E -H include operation
  -> run the direct compiler and retain ToolEvidence
```

### 2. C++ lint

`src/ici/engines/_cpp_lint.py`는 context가 존재하면 모든 covered production TU/configuration을
선택해 replay한다. compiler의 위치 있는 `error`/`warning`/`note:`와 진단 없는 PASS는
source·line target으로 보존한다. error-level context/unit diagnostic, context coverage 누락,
unsafe replay, malformed output, timeout·truncation, spawn 실패 또는 검증할 수 없는 nonzero
결과는 `ERROR`/`NOT_RUN`으로 fail-closed한다. warning-level context/unit diagnostic은 위치 있는
`WARN` target으로 남기고 replay를 계속하며, 다른 오류가 없으면 exact evidence는 `MEASURED`다.

compilation context가 실제로 없을 때만 `g++ -fsyntax-only -std=c++17 -Wall -Wextra`를
사용한다. 도구를 실행할 수 있었던 fallback 결과는 기존 호환성을 위해 `ESTIMATED`로
표시된다. 이 fallback도 ready capability의 `g++`를 우선하고 standalone에서는 canonical direct
driver만 허용하며, exact replay와 같은 positive allowlist, bounds, minimal environment와 closed
stdin을 적용한다. unsafe package/include flag나 project-contained driver는 실행 전에 거부한다.
g++ 부재·거부나 fallback 실행 실패는 `ERROR`/`NOT_RUN`이다. context가 있는데 replay가 실패했다고
이 경로로 전환하지 않는다.

### 3. Compiler include graph and cycle behavior

`src/ici/engines/_cpp_include_graph.py`는 configuration마다 compiler `-E -H` trace를
실행해 active resolved include edge를 추출한다. 각 edge는 `project`, `generated`, `system`,
`third_party` scope로 집계된다. `src/ici/engines/cycle.py`는 각 configuration graph를
독립적으로 분석하고, 동일한 cycle component만 중복 제거한다. configuration 간 edge는
union하지 않으며, 동일 component가 여러 configuration에서 확인되면 configuration 목록은
report metadata로만 보존한다. 따라서 한 configuration의 `A -> B`와 다른 configuration의
`B -> A`를 합쳐 false cycle을 만들지 않는다.

compiler가 active missing include를 보고하면 include 위치의 `CppIncludeUnresolved` `WARN`을
남기고 edge를 연결하지 않는다. malformed/truncated/timed-out trace, 검증할 수 없는 nonzero
종료, replay 또는 spawn 실패는 error target과 `ERROR`/`NOT_RUN` evidence로 남긴다.
`src/ici/engines/_cpp_include_trace.py`는 missing-include trace, include-guard trailer, pseudo
frame, stale path와 entry/depth bound를 별도로 검증하고 untrusted shape에서 edge를 만들지 않는다.

DB가 정말 없을 때만 기존 unique project path-suffix resolver를 사용하며, ambiguous/unresolved
include는 위치와 후보를 가진 경고로 보존한다.

### 4. Cache identity and documentation

lint와 cycle은 `CACHE_IMPLEMENTATION_MODULES`에 replay/include-graph helper module을 명시하며,
여기에 `ici.core._cpp_replay_policy`와 `ici.engines._cpp_include_trace`가 포함된다.
`ici.analysis-cache-key/v3`는 engine class source digest와 함께 이 선언된 helper/dependency
module source digest의 sorted unique 목록을 identity에 포함한다. import tree 전체를 암묵적으로
따라가지 않으므로, 엔진이 명시한 구현 경계가 cache invalidation 경계가 된다.

다음 문서에 exact/fallback 동작, scope와 failure contract, 현재 pending 조건을 반영했다.

- `docs/engine-reference.md`
- `docs/architecture.md`
- `docs/user-guide.md`
- `CHANGELOG.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `docs/superpowers/2026-08-30-handover.md`
- `README.md`의 현재 cache key 설명

## Verification Results

### Focused tests

The related focused test bundle passed 308 tests.

### Ruff

Ruff check passed for all files; Ruff format covered 142 files, and mypy passed 83 source files.
Every new source passed the line gate and the new helpers had no complexity issue.

### Full and packaged verification (2026-09-01 local revalidation)

- Python 3.10 full pytest: 1,275 passed in 48.61s.
- Two final `build-pyz` runs produced the same SHA-256
  `f6c6cfb85f55f41d548b65e9cb921b6b56d005eae838ec873ff7c927eaac2dc2` and the same
  2,151,981-byte artifact.
- Smoke passed all checks.
- Packaged pyz deep/no-cache LogLens: PASS, 12/14 applicable (2 SKIP), 12/12 tests,
  40 configurations, 21.59s, HTML 443,828 bytes, correct title, external assets 0.
- Packaged pyz deep/no-cache DiskMap: PASS, 12/14 applicable (2 SKIP), 9/9 tests,
  20 configurations, 79.74s, HTML 310,558 bytes, correct title, external assets 0.

These are local results. BuildScope target comparison and PR/CI/Pages evidence remain pending;
I3 as a whole remains pending until the BuildScope comparison is complete.

## Next Steps

- BuildScope에서 target-by-target define·standard·include를 실제 build와 대조하고,
  same-basename header가 compiler 선택과 동일한 edge를 만드는지 확인한다.
- PR/CI/Pages evidence는 아직 pending이며, 확인 후 기록한다.
- 위 조건이 충족되기 전까지 I3 전체는 pending으로 유지한다.
