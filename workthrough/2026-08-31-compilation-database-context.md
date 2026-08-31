# I3-1 컴파일 데이터베이스 맥락과 C++ coverage gate

## Overview

I3-1은 C++ 분석이 각 엔진의 추측이나 고정 compiler flag가 아니라 하나의
`compile_commands.json` snapshot을 기준으로 동작하도록 만든 작업이다. 신뢰할 수 없는
compile database를 shell 없이 bounded하게 읽고, immutable `CompilationContext`로
정규화한 뒤, 모든 production translation unit coverage와 compile policy를
`InspectionTarget`/finding으로 보고한다. compile database의 내용과 parse 상태도 cache
identity에 포함해 DB가 바뀐 뒤 오래된 엔진 결과가 재사용되지 않도록 했다.

## Context

I2에서 `AnalysisContext`의 소유권과 경계는 고정됐지만 compile database는 아직 경로와
argv를 보관하는 수준이었다. 이 상태에서는 다음 문제가 있었다.

- `command`를 shell로 실행하거나 재구성하면 untrusted JSON이 명령 실행 경계가 될 수 있다.
- 같은 source의 debug/release configuration, include path, sysroot를 한 entry로 합치면
  실제 compiler invocation과 다른 분석이 된다.
- malformed row, stale source, missing include directory가 전체 검증을 중단하거나 결과에
  연결되지 않을 수 있다.
- DB가 `build/compile_commands.json`으로 선택되거나 내용이 변경돼도 기존 cache key가
  같으면 낡은 결과를 반환할 수 있다.
- compiler argv에 포함된 checkout, 사용자 홈, 외부 SDK 경로가 JSON/HTML에 그대로 남을 수 있다.

따라서 loader, model, engine, cache, reporting을 분리하되 모두 같은 immutable context를
공유하는 것을 I3-1의 경계로 삼았다.

## Changes Made

### 1. Safe, bounded compilation-database loader

파일: `src/ici/core/compile_db.py` (facade), `src/ici/core/_compile_db_paths.py`,
`src/ici/core/_compile_db_commands.py`, `src/ici/core/_compile_db_metadata.py`

- loader를 facade와 path/command/metadata 전용 모듈로 분리했다. 네 모듈은 각각 순수 코드
  500줄 미만이며, compile_db 범위의 최종 line·type·high-complexity 이슈는 모두 0건이다.

- DB 선택 순서는 명시된 `project.compile_database`, 프로젝트 루트의
  `compile_commands.json`, `build/compile_commands.json`이며 project-relative containment를
  강제한다.
- DB는 `O_NOFOLLOW` regular-file descriptor로 열고, 최대 32 MiB·200,000 entries까지
  읽은 뒤 metadata를 다시 확인한다. symlink, 비정규 파일, 크기 초과, 읽기 중 변경은
  검증을 중단하는 예외가 아니라 bounded diagnostic이 된다.
- JSON duplicate key와 `NaN`/`Infinity`를 거부한다. row별 오류는 유효한 다른 row를
  버리지 않으며, database bytes에는 `sha256:` digest를 기록한다.
- `arguments`를 `command`보다 우선한다. command와 response file은 POSIX `shlex` 또는
  Windows CRT argv 규칙으로만 tokenize하며 shell을 호출하지 않는다.
- response file은 project 내부 regular file만 대상으로 최대 깊이·총 4 MiB·인자/문자 수
  한도를 적용한다. 외부 경로, cycle, missing file, malformed content는 명시적인
  response diagnostic으로 남긴다.
- source, working directory, output, include/search path와 sysroot를 canonical containment로
  정규화한다. stale source, missing directory/include, source/argv mismatch, foreign path와
  invalid compiler flag도 entry 또는 unit diagnostic으로 보존한다.

### 2. Immutable compilation model

파일: `src/ici/core/context.py`

`CompilationDiagnostic`, `CompilationDefine`, `CompilationSearchPath`, `CompilationUnit`,
`CompilationContext`를 frozen model로 추가했다. 각 unit은 compiler basename, language,
standard, define, include/quote/system search path와 존재 여부, sysroot, output, 원본 argv,
configuration digest와 unit diagnostic을 보유한다. 같은 source가 여러 configuration에
등장해도 directory와 configuration을 기준으로 안정적인 순서로 모두 보존한다.

`verify` preflight는 engine 실행 전에 한 번만 `load_compilation_context()`를 호출하고,
engine과 reporter는 동일한 `AnalysisContext` 객체를 읽는다. 모델 생성자는 collection,
digest, path, enum과 bool의 runtime type을 검증해 malformed 외부 입력이 `TypeError`로
파이프라인을 탈출하지 않도록 한다.

### 3. Compile database coverage and policy engine

파일: `src/ici/engines/compile_db.py`, `src/ici/core/pipeline.py`, `src/ici/config.py`,
`src/ici/config_schema.py`

`CompileDatabaseEngine`은 fast/standard/deep 전체 profile의 read-only descriptor로 등록되어
`AnalysisContext.project.compilable_cpp_sources`와 DB unit source를 정확히 비교한다.

- C++ production unit이 없으면 `SKIP`/`NOT_APPLICABLE`이다.
- DB가 없으면 기본적으로 각 production unit에 `WARN`을 내고,
  `database_required = true`이면 `FAIL`로 승격한다.
- DB가 있으면 coverage, loader/unit diagnostic, configuration별 required/forbidden flag를
  각각 위치가 있는 target으로 반환한다. PASS target도 source와 line 1을 갖는다.
- `EngineResult.extra`에는 `database_path`, production/covered unit 수, configuration 수,
  issue 수와 `coverage_percent`가 남는다. status는 공통 `mode` 정책으로 집계한다.

지원 정책은 다음처럼 프로젝트별로 조정할 수 있다.

```toml
[engines.compile_db]
enabled = true
mode = "pass_warn_fail"
database_required = true
required_flags = ["-Wall", "-Wextra"]
forbidden_flags = ["-fpermissive"]
```

`required_flags`와 `forbidden_flags`는 각 normalized argv의 exact token을 검사한다. 이
단계는 compiler를 재실행하지 않고 compile intent와 coverage를 검증하는 gate이며, 실제
CMake/qmake capture와 compiler-exact include edge 소비는 후속 I3-2~I3-4의 책임이다.

### 4. Cache identity v2

파일: `src/ici/core/cache_identity.py`, `tests/test_cache_identity.py`

`ici.analysis-cache-key/v2`에 `compilation_digest`를 추가했다. digest payload에는
`ici.compilation-identity/v1`, 선택된 project-relative DB path와 bytes digest, normalized
unit configuration/metadata, unit/context diagnostic과 invalid/missing parse state가
포함된다. 따라서 source가 같아도 DB 선택 경로, DB 내용, compiler metadata 또는 loader
diagnostic이 달라지면 cache miss가 된다. 기존 user-local entry는 key version mismatch로
재사용되지 않는다.

### 5. Reporting redaction and schema projection

파일: `src/ici/core/redaction.py`, `src/ici/reporters/json_rep.py`,
`src/ici/schemas/ici-result-v3.schema.json`, `tests/test_context_reporting.py`

compile context를 `ici.result/v3`의 optional `analysis_context`에 project-relative POSIX
형식으로 투영하고, 외부 include/search path와 path-bearing compiler flag는 공통 redaction
경계를 통과시킨다. `-I`, `-isystem`, `--sysroot`뿐 아니라 response-file, module, linker,
`-D`/`-include` 등 embedded path도 host 경로가 JSON·Markdown·HTML·console로 새지 않도록
정규화한다. reporter는 원본 shared context를 바꾸지 않고 reporting-safe projection만
생성한다.

### 6. Regression coverage

주요 회귀 입력을 `tests/test_compile_context.py`, `tests/test_compile_db_engine.py`,
`tests/test_analysis_context.py`, `tests/test_context_reporting.py`에 고정했다.

- arguments 우선순위, POSIX/Windows tokenizer와 MSVC `/D`, `/I`, `/std`, `/Fo`
- duplicate JSON key, non-finite value, wrong root/row type, oversize DB/entry/argv
- relative/absolute/symlink/foreign path, stale source, missing directory/include와 argv mismatch
- bounded response-file depth/bytes/cycle/outside-project 및 DB TOCTOU read boundary
- duplicate source configurations의 deterministic ordering과 frozen model validation
- production coverage, missing DB policy, required/forbidden flags, location-bearing target
- compilation digest가 DB bytes와 diagnostic state 변화에 따라 달라지는 cache key
- nested/embedded compiler path redaction과 v3 schema projection

## Code Examples

### Normalized context shape

```text
CompilationContext(
  database_path="build/compile_commands.json",
  database_digest="sha256:<64 lowercase hex>",
  units=(
    CompilationUnit(
      source="src/main.cpp",
      directory="build",
      compiler="g++",
      language="c++",
      standard="c++20",
      defines=(...),
      include_paths=(...),
      configuration="sha256:<64 lowercase hex>",
    ),
  ),
  diagnostics=(...),
)
```

### Coverage result contract

```text
compile_db
  status: PASS | WARN | FAIL
  target: src/main.cpp:1  ici.compile-db.coverage
  extra: covered_units / production_units, coverage_percent, configurations
```

## Verification Results

I3-1 최종 로컬 품질 게이트는 Python 3.10에서 focused 109 passed, full suite
1,032 passed in 46.29s였다. Ruff check/format은 127 files에서 통과했고, focused mypy도
clean이었다. reproducible pyz는 두 번 빌드한 SHA-256이 모두
`408fcd0fcf153b5e63927d10d34d55cea680eb472dc6f0e95bf174efcf6e8b36`으로 일치했다.
의존성 검사는 pure-Python 10 distributions/no certifi였고, smoke와 Zero-CDN도 PASS였다.

최종 `--no-cache` self verify는 exit 0의 WARN이었다.

```text
engines: 13 total — Pass 8 / Warn 4 / Fail 0 / Error 0 / Skip 1
compile_db: SKIP / NOT_APPLICABLE (Python-only)
test: 1,032/1,032
coverage: line 88.6% / function 97.1% / branch 79.6%
TEM: 4.86
cache hits: 0
elapsed: 109.26s
HTML: 4,627,454 bytes
compile_db-specific high-complexity / line-threshold / type issues: 0
```

문서 변경 후 `git diff --check`와 변경 문서의 내부 경로 링크(`README.md`,
`src/ici/schemas/ici-result-v3.schema.json`, `docs/engine-reference.md`)도 확인했다. 위
수치(HTML 4,627,454 bytes, branch 79.6% 등)는 로컬 증거다.

I3-1 원격 병합 증거도 완료됐다. [PR #99](https://github.com/jihoon22-lee/ici/pull/99)는
squash로 병합되어 commit
[`64c4f7b57826e088e9b74b5950c7f3d8091188b9`](https://github.com/jihoon22-lee/ici/commit/64c4f7b57826e088e9b74b5950c7f3d8091188b9)가
되었다. [CI run 33380721019](https://github.com/jihoon22-lee/ici/actions/runs/33380721019)의
`Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`,
`Publish PR Report & Sticky Comment`, `Merge Gate`가 모두 SUCCESS였고, PR에서
`Publish Main`은 expected skipped였다. [sticky comment](https://github.com/jihoon22-lee/ici/pull/99#issuecomment-5476836988)는
ici와 viewer를 함께 포함했다. CI stats는 ici WARN(Pass 8, Warn 4, Fail 0, Error 0,
Skip 1, TEM 4.86, tests 1,032, branch 79.7%), viewer WARN(Pass 10, Warn 1, Fail 0,
Error 0, Skip 2, TEM 4.89, tests 7)였다.

독립적으로 fetch한 [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/99/)와
[viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/99/)는 각각 HTTP/2 200,
`Content-Type: text/html; charset=utf-8`, title present, 외부
`script`/`link`/`img`/`iframe` dependency 0건이었다. 관측 bytes는 ici 4,496,996,
viewer 344,663이었다. 이로써 I3-1은 완료됐으며 다음 단계는 I3-2 CMake compile DB
생성이다. I3 전체는 아직 완료되지 않았다.

## Next Steps

I3-1은 compile context model과 coverage/policy gate의 기반이며, 다음 항목은 이 문서의
범위에 포함하지 않는다.

- **I3-2 CMake compile DB 생성**: configure에서 `CMAKE_EXPORT_COMPILE_COMMANDS=ON`을
  canonical하게 적용하고 Makefile/Ninja 제약, unity/generated source, release/coverage/
  sanitize variant를 buildscope와 대조한다.
- **I3-3 qmake compile capture**: verbose build, compiler wrapper, 외부 capture 도구를
  실측 비교하고 Qt5/Qt6·target wrapper·shadow path를 검증한다. exact capture가 불가능한
  환경은 lower-confidence mode로 명시한다.
- **I3-4 lint/include graph 이관**: compile unit argv를 재생해 고정 `-std=c++17`을 제거하고,
  compiler dependency output으로 실제 include edge, generated/system/third-party 정책,
  ambiguity와 unresolved edge를 수집한다. DB가 없는 경우에만 suffix heuristic fallback을
  허용한다.
