# ici 시스템 아키텍처 및 상세 설계 (System Architecture Guide)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; **🏛️ 시스템 아키텍처** &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

이 문서는 `ici` (Integrated CI)의 시스템 설계, 내부 아키텍처, 런타임 수명 주기, 엔진 파이프라인 및 확장 방법을 상세히 기술합니다.

---

## 1. 시스템 개요 및 하이레벨 구조

`ici`는 로컬 개발 머신, 사내 폐쇄망 서버, GitHub Actions 가상머신 등 서로 다른 환경에서 **같은 정책·결과 계약과 엄격한 품질 게이트**를 적용하도록 설계되었습니다. OS, 컴파일러, Python, 외부 도구의 가용성과 버전은 실행 증거로 보존되며 환경이 다르면 검증 결과도 달라질 수 있습니다.

### 1.1 컴포넌트 아키텍처 다이어그램

```text
+-----------------------------------------------------------------------------------+
|                                  CLI Layer (__main__.py)                          |
|    - CLI Parsing (Typer)         - Signal Handling        - Exit Code Mapping     |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                             VerifyOrchestrator Layer                              |
|    - Config Deep-Merge (ici.toml)                         - Engine Registry       |
|    - Declarative Engine DAG + bounded Scheduler        - TEM Scoring Engine       |
|    - Finding/metadata assembly                             - Baseline gate         |
+------------------------------------------+----------------------------------------+
                                           |
       +-----------------------------------+-----------------------------------+
       |                                   |                                   |
       v                                   v                                   v
+---------------+                   +---------------+                   +---------------+
|  Line Engine  |                   |  Test Engine  |                   |  Dup Engine   |
| (Line/Tree)   |                   | (Coverage/TEM)|        ...        | (Maximal Cln) |
+-------+-------+                   +-------+-------+                   +-------+-------+
        |                                   |                                   |
        +-----------------------------------+-----------------------------------+
                                           |
                                           | (InspectionTarget & EngineResult)
                                           v
+-----------------------------------------------------------------------------------+
|                                Multi-Reporter Layer                               |
|  +---------------------+  +---------------------+  +----------------------------+ |
|  | RichConsoleReporter |  |     HtmlReporter    |  |      MarkdownReporter      | |
|  | - Color Table       |  | - 10/11 Dynamic Tabs |  | - GitHub Step Summary      | |
|  | - file:// Links     |  | - Hierarchical Tree |  | - Optional Trusted Publish | |
|  | - Summary Banners   |  | - Source Previews   |  | - Inline Annotations       | |
|  +---------------------+  +---------------------+  +----------------------------+ |
+-----------------------------------------------------------------------------------+
```

---

## 2. 코드베이스 디렉토리 구조

```text
ici/
├── dist/
│   └── ici.pyz                      # 약 2.0MB 단일 실행 ZipApp 산출물
├── docs/                            # 프로젝트 문서화
│   ├── architecture.md              # [본 문서] 시스템 아키텍처 및 내부 설계
│   ├── user-guide.md                # 사용자 가이드 및 빠른 시작
│   ├── engine-reference.md          # 검증 엔진 레퍼런스 및 수식
│   └── ci-integration.md            # GitHub Actions & 폐쇄망 CI 연동 가이드
├── scripts/                         # 빌드 및 런처 스크립트
│   ├── launcher.sh                  # ZipApp polyglot 프리앰블 런처
│   ├── build-pyz.sh                 # 재현 가능한 ZipApp 빌드 파이프라인
│   └── smoke.sh                     # 독립 환경 스모크 테스트
├── src/
│   └── ici/
│       ├── __init__.py              # 패키지 메타데이터 (동적 프로젝트 버전)
│       ├── __main__.py              # CLI 엔트리포인트 및 서브커맨드 라우터
│       ├── compilation_export_cli.py # standalone export CLI/exit-code adapter
│       ├── config.py                # 전사 기본 정책(DEFAULT_CONFIG) 및 toml 로더
│       ├── core/                    # 코어 도메인 로직
│       │   ├── backend.py           # 독립 build backend discovery
│       │   ├── env.py               # 파이썬 탐색 및 시스템 환경 진단
│       │   ├── models.py            # 결과·finding·baseline 데이터 모델
│       │   ├── context.py           # immutable 분석 맥락·variant·artifact manifest
│       │   ├── pipeline.py          # 선언형 engine descriptor, DAG 검증 및 bounded scheduler
│       │   ├── capabilities.py      # bounded tool capability inventory
│       │   ├── cache.py              # user-local, digest-addressed analysis cache store
│       │   ├── cache_identity.py     # source digest와 실행 identity/cache key
│       │   ├── cache_codec.py        # strict cache JSON encode/decode/검증
│       │   ├── findings.py          # v3 finding canonicalization/fingerprint
│       │   ├── baseline.py          # v3 baseline loader/comparison/gate
│       │   ├── compilation_export.py # standalone measured context v1 projection
│       │   ├── _compilation_export_argv.py # compiler metadata redaction/digest
│       │   ├── _compilation_export_io.py # bounded atomic output boundary
│       │   ├── _compilation_export_project.py # process-free project discovery
│       │   ├── project.py           # 소스 파일 탐색 및 프로젝트 루트 감지
│       │   └── runner.py            # 서브프로세스 격리 실행기
│       ├── engines/                 # 표준 검증 엔진 + 퍼블리셔
│       │   ├── base.py              # BaseEngine 인터페이스 & evaluate_status()
│       │   ├── verify.py            # VerifyOrchestrator (검증 오케스트레이터)
│       │   ├── line.py              # 코드/주석/공백 분석 및 트리 구조 생성
│       │   ├── lint.py              # Ruff 및 GCC/Clang/clang-tidy/clazy 린터 facade
│       │   ├── _cpp_diagnostics.py  # bounded GCC/Clang/clang-tidy/clazy diagnostic parser
│       │   ├── _cpp_diagnostic_categories.py # isolated rule-only C++ category policy
│       │   ├── _cpp_tooling.py      # 공통 compiler-tool argument/context helper
│       │   ├── _clang_tidy.py       # exact-context clang-tidy adapter
│       │   ├── _clazy.py            # exact-context Qt-aware clazy adapter
│       │   ├── _qt_codegen.py       # Qt moc/uic/rcc linkage 및 major evidence
│       │   ├── compile_db.py        # compile_commands coverage와 C++ flag policy
│       │   ├── test.py              # 테스트 실행 & TEM 스코어링 (coverage.py/gcov 실측)
│       │   ├── type_check.py        # mypy/AST 타입 검사 (C++은 명시적 SKIP)
│       │   ├── complexity.py        # Cyclomatic & Nesting 복잡도 분석기
│       │   ├── _cpp_function_boundaries.py # bounded clang-tidy AST 경계 adapter
│       │   ├── sanitize.py          # ASan/UBSan & Python 누수 검증
│       │   ├── thread_sanitize.py   # deep-only TSan thread-safety 검증
│       │   ├── _sanitizer_diagnostics.py # bounded ASan/LSan/UBSan/TSan parser
│       │   ├── _source_inputs.py    # dead/dup 공통 bounded UTF-8 source snapshot
│       │   ├── dead.py              # 미사용 심볼 & 데드코드 탐지기
│       │   ├── dup.py               # 연결 컴포넌트 클러스터링 기반 중복 감지기
│       │   ├── _cpp_dup_tokenization.py # C/C++/Qt line-preserving lexical records
│       │   ├── _python_dup_tokenization.py # Python tokenize/AST lexical records
│       │   ├── _dup_regions.py      # function/import/preprocessor hard boundaries
│       │   ├── _dup_signal.py       # low-information window suppression policy
│       │   ├── _dup_matching.py     # bounded rolling-seed exact extension
│       │   ├── exception.py         # 예외 삼킴 및 소멸자 throw 방지
│       │   ├── publish.py           # GitHub HTML 리포트 퍼블리셔 (gh-pages/hub)
│       │   └── publish_baseline.py  # strict delta summary/comment adapter
│       ├── reporters/               # 다중 리포터 계층
│       │   ├── console.py           # Rich 터미널 대시보드 & file:// 링크
│       │   ├── html/                # 기본 10개, baseline 비교 시 11개 Zero-CDN 탭
│       │   ├── html_assets.py       # 이전 import 호환 facade
│       │   ├── markdown.py          # GitHub Actions Summary & PR 코멘트
│       │   └── json_rep.py          # JSON 리포트 직렬화기
│       └── schemas/                  # 배포되는 기계 검증 계약
│           ├── ici-result-v3.schema.json
│           └── ici-compilation-export-v1.schema.json
├── tests/                           # Pytest 단위 테스트 스위트
├── AGENTS.md                        # 개발 규약, 브랜칭 전략, 커밋 룰
├── CHANGELOG.md                     # 변경 이력 및 릴리스 노트
└── ici.toml                         # 전사 공용 표준 품질 게이트 설정
```

---

## 3. ZipApp 패키징 및 Polyglot 런처 수명 주기

`ici`는 배포 단순화를 위해 단일 실행형 ZipApp(`ici.pyz`)으로 패키징됩니다.

```text
[ici 실행]
    ↓
[scripts/launcher.sh 프리앰블 해석] (Bash/Sh 실행)
    ↓
[파이썬 인터프리터 탐색] ($ICI_PYTHON -> python3.14 ~ python3.10 -> 사내 표준 경로)
    ↓
[Python 실행 및 ZipApp 부팅] (shiv 런타임이 ~/.shiv 캐시에 압축 해제 후 진입)
    ↓
[src/ici/__main__.py:main() 진입]
```

### 3.1 결정론적 재현 빌드 (Deterministic Reproducible Builds)
`scripts/build-pyz.sh`는 다음 4단계 파이프라인을 거칩니다:
1. `uv pip install`로 Python 3.10 타깃 순수 파이썬(`py3-none-any`) 의존성만 격리 설치
2. `*.dist-info` 내 `direct_url.json`, `uv_cache.json`, `RECORD` 등 빌드 머신 의존 메타데이터 제거
3. `shiv`를 통해 ZipApp 아카이브 생성 (고정 타임스탬프 `SOURCE_DATE_EPOCH=1700000000` 적용)
4. 생성된 아카이브 앞단에 `scripts/launcher.sh` 셸 프리앰블 결합

---

## 4. 엔진 파이프라인 및 데이터 모델

### 4.1 핵심 데이터 모델 (`src/ici/core/models.py`)

- **`InspectionTarget`**: 검사 대상의 정밀 위치와 메트릭을 추적합니다.
  - `file_path`: 대상 파일 상대 경로
  - `start_line` / `end_line`: 소스 코드 라인 범위
  - `target_name`: 함수명, 클래스명, 또는 규칙 심볼명
  - `status`: `EngineStatus` (`PASS`, `WARN`, `FAIL`, `ERROR`, `SKIP`)
  - `message`: 진단 메시지
  - `snippet`: 원본 소스 코드 블록 (포맷팅 보존)
  - `metrics`: 세부 수치 데이터 (`complexity`, `nesting`, `duplicate_lines` 등)와 엔진별
    provenance (`boundary_source`, `boundary_confidence`, `metric_confidence` 등)

- **`TestCaseResult`** (`src/ici/core/cmake.py`): CMake/CTest와 qmake/QtTest가 서로 다른
  출력 형식을 공통 테스트 case 상태로 정규화한 adapter 모델입니다. `name`, `passed`,
  `message` 뒤에 네 번째 필드 `executed`를 두며 기본값은 `true`입니다. 이 필드는 마지막에
  추가되어 기존 세 인자 positional 생성과 호환되고, `passed = false`인 실행 실패와 수집됐지만
  실행되지 않은 case를 구분합니다. `passed = true`·`executed = false` 조합은 불변식 위반으로
  거부됩니다. QtTest XML parser는 `<testcase>`별 `skip`/`xfail`/`xpass`/unknown 상태를
  이 계약으로 해석하지만, qmake adapter의 권위 있는 집계 단위는 `make check` transcript가
  식별한 테스트 바이너리 하나입니다. QtTest XML은 해당 바이너리의 failure detail을 보강할
  뿐이며, qmake에서 모든 function-level skip을 개별 scope로 집계한다고 주장하지 않습니다.

  Python pytest 출력도 같은 의미로 projection됩니다. verbose/terminal summary의 `SKIPPED`는
  미실행, `XFAIL`은 실행된 예상 실패이자 PASS, `XPASS`는 실행된 실패입니다. `test` 엔진은 모든 언어의
  수집 case가 미실행이면 required에서 `ERROR`/`NOT_RUN`, optional에서 `SKIP`/`ESTIMATED`로
  집계하고, 해당 run의 coverage 산출물은 테스트 실행 증거로 사용하지 않습니다.

- **`EngineResult`**: 단일 검증 엔진의 종합 결과입니다.
  - `engine_name`: 엔진 식별자 (`line`, `lint`, `test`, ...)
  - `status`: 엔진 종합 평가 상태
  - `summary`: 인간 친화적 한 줄 요약
  - `score` / `max_score`: 점수 (예: TEM 4.75 / 5.0)
  - `targets`: 세부 `InspectionTarget` 목록
  - `extra`: 엔진별 추가 구조화 데이터 (`files_data`, `top_complex_funcs`, `clone_groups` 등)
  - `required`: 결과가 품질 게이트에 필수인지 여부
  - `evidence`: 결과가 실측(`MEASURED`), 추정(`ESTIMATED`) 또는 미실행(`NOT_RUN`)인지 여부
  - `tool_evidence`: 호출한 외부 도구의 경로·인자·버전·종료 상태·오류 증거
  - `cache_hit` / `cache_key`: 해당 엔진 결과가 user-local analysis cache에서 왔는지와
    cache identity digest. v3에서 optional extension으로 취급해 기존 archive를 깨뜨리지 않습니다.
  - `findings`: v3의 안정적인 issue/inventory 목록. legacy `targets`도 adapter를 통해 전부
    finding으로 노출되며 native finding이 같은 fingerprint를 제공하면 더 풍부한 native 정보가
    우선합니다.

- **`Finding`**: 도구 출력 형식과 독립적인 분석 결과입니다.
  - `rule_id`, `category`, `severity`, `confidence`: ici가 소유하는 안정적인 분류
  - `fingerprint`: canonical project-relative path와 symbol 또는 region을 결합한 SHA-256 identity
  - `primary_location`, `related_locations`: 1-indexed line/column을 가진 위치
  - `message`, `explanation`, `remediation`, tool identity, suppression 근거
  - `metrics`: 숫자 값과 단위를 분리한 측정치. 문자열형 보조 정보는 finding metric으로 승격하지 않습니다.
  - native finding은 primary와 related location을 canonical project-relative path/region으로
    정규화하고 related 목록을 `(path, start line/column, end line/column, label)` 순으로
    deterministic하게 정렬합니다. JSON과 HTML은 전체 related inventory를 유지하고, GitHub
    Markdown은 informational/suppressed finding을 제외한 related row를 engine당 100개로
    제한하며 생략 수를 명시합니다.

- **`AnalysisMetadata`**: baseline과 현재 분석이 같은 계약을 사용했는지 판별하는 네 가지
  identity를 보존합니다. `producer_version`, `fingerprint_version`, `policy_digest`,
  `tool_policy_digest`가 각각 producer, fingerprint 규칙, 분석 정책, 도구 정책의
  호환성 식별자입니다. 차이가 있으면 delta 자체는 계속 계산하되 comparison warning으로
  노출합니다.

- **`FindingDelta` / `BaselineComparison`**: 현재 finding inventory와 선택한 baseline의
  관계를 각각 `new`, `unchanged`, `moved`, `resolved`로 표현합니다. `FindingDelta`는
  현재·baseline 위치와 severity, `regressed`/`suppressed`/`gated` 상태를 함께 가집니다.
  `BaselineComparison`은 baseline source, 네 compatibility identity의 warning, 전체 delta 목록,
  `fail_on_new` 설정과 `gate_failed` verdict를 보존합니다.

- **`VerificationSuiteResult`**: 전체 검증 스위트의 최종 집계 결과입니다.
  - `suite_status`: 전체 집계 상태. 필수 `ERROR`/`SKIP`/`NOT_RUN`은 `ERROR`, 필수 `FAIL`은 `FAIL`, 경고 또는 선택 검증의 비측정 결과는 `WARN`으로 집계합니다.
  - `results`: 각 엔진 결과 목록
  - `tem_score`: TEM 종합 품질 점수
  - `support_matrix`: 발견된 Python/C++ 및 Qt scope에 대해 중앙 엔진 선언을 적용한 결과입니다.
    선언 mode와 실제 active mode, 적용·활성 여부, evidence/confidence, 필수·선택 도구,
    fallback 및 limitation을 보존합니다. reporter와 viewer는 이를 다시 계산하지 않습니다.
  - JSON writer는 `ici.result/v3`를 사용합니다. 기존 `targets`와 엔진·도구 증거를 보존하면서
    `findings`를 추가하므로 점진적으로 소비자를 전환할 수 있습니다. v2 archive는 migration
    helper와 v2/v3 겸용 viewer로 계속 읽을 수 있습니다. 기계 검증 계약은
    [`ici-result-v3.schema.json`](../src/ici/schemas/ici-result-v3.schema.json)입니다.

### 4.2 공유 분석 맥락과 산출물 소유권 (`AnalysisContext`)

엔진마다 프로젝트를 다시 탐색하거나 리포터가 실행 중인 객체를 수정하면, 같은 실행 안에서도
범위·도구·산출물의 기준이 달라질 수 있습니다. I2-2부터 한 검증 실행은 아래 immutable
snapshot을 생성하고 모든 엔진과 리포터가 이를 읽기 전용으로 공유합니다.

- **`ProjectModel`**: canonical project root, 이름·버전·언어/프레임워크 유형, source와
  header 범위, Python/C++ 및 직접 컴파일 가능한 C++ 목록, external build directory,
  include flags와 선택된 backend descriptor/reason을 한 번만 발견해 tuple로 보존합니다.
- **`CapabilityInventory`**: bounded probe가 수집한 도구 경로·버전·세부 정보·실행 evidence와
  required/optional provenance를 mapping-proxy로 고정합니다. 엔진이나 리포터는 재탐색하거나
  snapshot을 추가할 수 없습니다.
  - **`CompilationContext`**: I3 preflight가 선택한 compile database와 번역 단위별 `source`,
  `directory`, `argv`, `output`, compiler metadata를 immutable tuple로 전달합니다. DB는
  project root의 `compile_commands.json`, `build/compile_commands.json` 또는 명시된
  project-relative 경로에서만 선택하며, 모든 바이트와 parse diagnostic을 snapshot에
  바이트 digest와 parse diagnostic을 snapshot에 포함합니다. 같은 source의 debug/release 등
  여러 configuration도 합치지 않습니다.
- **`BuildSession`**: configure/build/test 중 누적되는 도구 evidence와 오류를 보유하는
  유일한 mutable adapter 상태입니다. session은 명시적인 `RELEASE`, `COVERAGE`,
  `SANITIZE`, `THREAD_SANITIZE` variant를 받아 각 shadow tree와 계측 flags를 분리합니다.
- **`ArtifactManifest`**: 성공한 session이 발행하는 frozen 산출물 목록입니다. project 또는
  shadow root 아래의 regular file만 허용하고, 각 record에 variant·producer·source/config/
  toolchain identity와 SHA-256, size, mode를 남깁니다. project/shadow root와 symlink
  escape는 canonical containment 검사에서 거부됩니다.
- **`AnalysisIdentity`**: source commit, canonical config digest, toolchain digest를 묶어
  build와 report가 어느 입력 snapshot에서 만들어졌는지 재현 가능하게 합니다. git 밖의
  실행은 source commit을 명시적인 `unavailable`로 기록합니다.
- **`AnalysisProfile`**: `fast`/`standard`/`deep` 중 하나로 비용과 실행 범위를
  선택합니다. profile은 descriptor가 지원하는 엔진을 고르는 정책일 뿐이며, 같은 rule의
  임계값이나 판정 의미를 바꾸지 않습니다.

리포터는 `AnalysisContext`를 변경하지 않고 reporting-safe copy와 JSON projection만 만듭니다.
`ici.result/v3`에는 기존 archive를 깨지 않도록 선택적인 `analysis_context` 객체
(`ici.analysis-context/v1`)와 engine-level `artifact_manifests` 배열
(`ici.artifacts/v1`)을 둡니다. JSON의 project/source/header/compile/artifact 경로는
project-relative POSIX 형식이며, 외부 include/search path처럼 호스트 정보가 섞일 수 있는
경로는 redaction 경계를 통과해 절대 경로를 노출하지 않습니다. 두 확장 필드가 없는 기존
v3 payload도 그대로 읽고 migration할 수 있습니다. `analysis_context`가 포함된 경우에도
`profile`은 선택 필드이므로, profile이 없는 기존 context와 context 자체가 없는 기존
v3 payload를 모두 호환합니다.

#### Standalone compilation context export

`ici export-compilation-context`는 `verify`의 엔진 DAG와 분리된 public projection 경계다.
이 명령은 측정된 compile database를 `ici.compilation-export/v1`로 내보내며, 내부의 frozen
`CompilationContext`와 원본 argv를 소비하되 raw command를 공개하지 않는다.

```text
CLI/config (global-default write disabled)
        │
        ├─ default: static project metadata + bounded DB loader (no process/shell/compiler)
        │
        └─ --prepare: selected root CMake/qmake adapter → owned build/ici-* shadow
                              │
                  normalized context → redaction → deterministic v1 JSON
                              │
                    stdout (-) or validated atomic file output
```

기본 경로의 project discovery는 루트 descriptor와 metadata만 확인하고 recursive source scan이나
subprocess를 사용하지 않는다. loader는 `arguments`를 `command`보다 우선하고 POSIX `shlex`/
Windows CRT 규칙으로만 분해한다. project-contained response file을 읽을 수 있지만 shell을
실행하지 않으며, DB 32 MiB·200,000 entries, row별 argv 32,768 arguments·1 MiB, DB 전체
expanded arguments 1,000,000개·32 MiB, command 4 MiB, response depth 4·파일/aggregate
4 MiB의 상한을 적용한다. duplicate key, non-finite JSON,
foreign/escaping path, symlink escape, malformed row와 읽는 중 변경은 bounded diagnostic으로
보존하거나 거부한다.

`--prepare`는 명시적으로 선택·설정한 DB와 auto-discovered DB가 모두 없을 때만 adapter를
호출한다. 명시 DB가 누락·손상됐으면 다른 canonical DB로 조용히 대체하지 않고 그 오류가
우선한다. 루트 `CMakeLists.txt`는 `build/ici-cmake-build`, 루트 `*.pro`는
`build/ici-qmake-build`를 소유 shadow로 사용하며, configure/build와 generated source capture
때문에 프로젝트 파일과 외부 compiler/build 도구에 side effect가 생길 수 있다. 따라서 default
read-only와 명시적 preparation은 별도의 신뢰 경계다. 기존 DB가 있으면 preparation보다 그 DB가
우선한다.

projection에는 project-relative POSIX path, compiler family/name, language/standard, defines,
include/search path scope, sysroot, target, unit/configuration digest와 diagnostic만 남는다.
외부 경로는 `[external]`, credential과 공개 불가 scalar는 `***REDACTED***`가 되고 include
existence는 외부 범위에서 `null`이다. unit에는 raw `argv`/`command`가 없다. `source_bytes_digest`
는 선택된 DB의 원본 bytes SHA-256이고, `semantic_digest`는 origin/generator/unity와 정렬된
normalized unit semantics를 canonical JSON으로 해시한다. 따라서 raw-byte identity와 의미 비교
identity를 구분한다. 모든 JSON key와 unit 순서는 deterministic하며 `--pretty`는 whitespace만
바꾼다.

`evidence`는 유효한 DB를 관측했다는 `MEASURED`이고, 외부/redacted 값·unknown compiler·
unmodeled option·비치명 diagnostic·unity build가 있으면 `comparison_state`를 `inconclusive`로
낮춘다. 치명적인 error-level diagnostic은 payload를 만들지 않고 exit 1로 닫는다. 이는 측정
근거를 `ESTIMATED`로 바꾸지 않는다. 출력은 32 MiB를 넘을 수 없고, stdout 성공 출력은
JSON-only이며 오류는 stderr로 보낸다. 파일 출력은 validated target에 같은 디렉터리
임시 파일, flush/`fsync`, atomic replace와 directory sync를 사용한다. DB와 project policy
파일, alias 및 special file은 보호하며 허용된 symlink는 referent가 아니라 link 자체를 교체한다.
기계 계약은 [`ici-compilation-export-v1.schema.json`](../src/ici/schemas/ici-compilation-export-v1.schema.json)이며,
`build-pyz.sh`가 ZipApp 구성에 이 schema와 기존 v3 schema가 포함되는지 확인한다.

#### Compile database boundary and C++ coverage gate

`src/ici/core/compile_db.py`는 신뢰할 수 없는 `compile_commands.json`을 compiler나 shell을
실행하지 않고 읽습니다. `arguments` 배열이 `command`보다 우선하고, command 문자열은 POSIX
`shlex` 또는 Windows CRT 규칙으로만 분해됩니다. response file은 project 안의 regular file만
제한된 깊이·총 바이트·인자 수로 확장하며, 외부 경로·symlink escape·중복 JSON key·비유한
숫자·비정상 파일·읽기 중 변경은 오류 diagnostic으로 남깁니다. 데이터베이스와 각 row는
bounded read, canonical containment 검사를 통과해야 하며, 한 row의 오류가 다른 유효한 row를
버리지 않습니다.

정규화된 unit에는 compiler basename, language, standard, define, include/quote/system search
path와 존재 여부, sysroot, output, configuration digest가 들어갑니다. `CompilationUnit`과
`CompilationContext`는 frozen model이므로 엔진이 argv나 경로를 다시 발견하지 않습니다.
source·working directory·output·include path의 project-relative/외부 scope와 stale source,
missing directory, source/argv mismatch는 location-bearing `CompilationDiagnostic`으로
보존됩니다. JSON/HTML/Markdown으로 투영할 때 compile argv와 path-bearing flag도 공통
redaction 경계를 통과해 checkout·사용자 홈·외부 SDK 경로를 노출하지 않습니다.

`CompileDatabaseEngine`은 shared context를 소비해 모든 production C/C++ translation unit을
DB의 source set과 대조합니다. DB가 없으면 기본적으로 C++ unit별 `WARN`을 내고
`database_required = true`일 때 `FAIL`로 승격합니다. DB가 있으면 각 unit의 coverage, 모든
loader/unit diagnostic, configuration별 required/forbidden flag를 각각 `InspectionTarget`으로
반환하므로 PASS도 source와 line 1을 갖습니다. 결과의 `coverage_percent`, configuration 수와
database path는 `EngineResult.extra`에 남고, engine은 모든 profile에서 read-only descriptor
DAG의 read-only node로 실행됩니다.

```toml
[engines.compile_db]
enabled = true
mode = "pass_warn_fail"
database_required = true
required_flags = ["-Wall", "-Wextra"]
forbidden_flags = ["-fpermissive"]
```

#### Canonical CMake compilation context

`src/ici/core/cmake_context.py`는 immutable `AnalysisContext`와 cache identity가 만들어지기
전에 CMake 기반 C/C++ 프로젝트의 compile database를 준비합니다. 명시적으로 설정했거나
자동 발견한 DB가 있으면 그것을 우선하고 CMake를 다시 실행하지 않습니다. DB가 없고 root
backend가 CMake인 C/C++ 프로젝트에만 `build/ici-cmake-build` shadow를 사용해 다음과 같은
단일 Release 분석 구성을 수행합니다.

```text
cmake -S <project> -B <project>/build/ici-cmake-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_UNITY_BUILD=OFF
```

canonical preflight는 `Ninja` 또는 이름이 `Makefiles`로 끝나는 single-config generator만
exact context 대상으로 허용합니다. multi-config 또는 알 수 없는 generator, export 비활성,
해석할 수 없는 unity 값은 context diagnostic으로 보존해 정확한 compile scope인 것처럼
표시하지 않습니다. `CMakeCache.txt`는 `O_NOFOLLOW` regular-file descriptor로 최대 4 MiB만
읽고 generator/export/unity 관련 키만 bounded하게 해석합니다.

처음 읽은 DB에 canonical shadow 아래 generated source의 `stale-source`가 있으면 preflight는
한 번의 full build 후 DB를 다시 읽습니다. 이 조건 밖의 stale source는 자동 build를 유발하지
않습니다. CMake subdirectory entry의 `output` 표기가 DB parent 기준인지 entry working
directory 기준인지 불일치하는 경우에도 두 경로가 같은 canonical output을 가리킬 때만
reconcile하며, 기존 project containment 검사는 그대로 적용합니다. generated 또는 unity
unit이 남으면 `cmake-generation-failed`/`cmake-unity-build` diagnostic과 위치를 보존합니다.

생성된 context는 `origin = "cmake"`, generator, nullable unity 상태, DB digest와 함께 각
`CompilationUnit`의 compiler/language/standard/defines/include paths/sysroot/output,
configuration digest와 CMake target을 보유합니다. target은 `CMakeFiles/<target>.dir` 출력
관례에서 도출됩니다. JSON report payload와 이를 내장하는 HTML은 이 metadata와 normalized
argv를 redaction 경계로 통과시키고, cache key는 DB 내용·parse state뿐 아니라 origin/generator/
unity/target까지 포함하므로 CMake context가 바뀐 결과를 재사용하지 않습니다. DB digest는
preflight가 immutable context로 캡처한 snapshot의 identity이며 live-file lease가 아니다.
실행 중인 context는 DB 파일 변이를 따라가지 않고, 다음 preflight가 새 DB bytes를 읽어 새
digest와 context를 반영한다.

#### Compiler-backed C++ lint and include graph

`lint`와 `cycle`의 C++ 경로는 shared `AnalysisContext`의 `CompilationContext`를 단일
입력으로 사용합니다. context/database가 있으면 모든 covered production translation unit의
각 configuration에서 `CapabilityInventory`가 직접 probe한 실행 가능한 GCC/Clang compiler와
normalized argv를 선택해 replay합니다. replay는 source와 working directory의 project 경계를
재검사하고, compile-only·출력·dependency 생성 옵션과 plugin/wrapper/toolchain 주입처럼
안전하지 않은 flag를 제거하거나 거부합니다. 보존되는 compiler option은 positive allowlist와
허용된 value에만 한정되며, allowlist 밖의 unknown option도 fail-closed로 거부합니다. compiler
process에는 inherited override를 주지 않는 minimal replacement environment를 전달하고,
stdin은 빈 입력으로 명시적으로 닫습니다. 따라서 엔진이 임의로 `g++ -std=c++17` 명령을
재구성하지 않으며, context가 존재하는데 replay할 수 없을 때 suffix 또는 고정 compiler
폴백으로 조용히 바뀌지 않습니다.

- C++ lint는 configuration마다 controlled syntax check를 실행합니다. 위치가 있는
  compiler `error`/`warning`/`note:`와 무진단 PASS를 원래 source·line target으로 보존하고,
  error-level context/unit diagnostic, context coverage 누락·unsafe replay·malformed
  output·timeout·truncation·spawn 또는 검증할 수 없는 nonzero 결과만 `ERROR`/`NOT_RUN`으로
  fail-closed 기록합니다. warning-level context/unit diagnostic은 위치가 있는 `WARN` target으로
  보존하고 exact 실행을 계속하므로, 다른 오류가 없으면 engine evidence는 `MEASURED`입니다.
- C++ cycle은 configuration별 `-E -H` compiler trace에서 실제 active resolved include edge를
  추출하고, edge를 `project`/`generated`/`system`/`third_party` scope로 집계합니다. 각
  configuration graph를 독립적으로 분석하고 동일한 cycle component만 중복 제거하며,
  configuration 간 edge는 union하지 않습니다. 같은 component가 여러 configuration에서
  확인되면 report metadata에 configuration 목록만 보탭니다. active missing include는 위치 있는 `CppIncludeUnresolved`
  `WARN`으로 남기고 edge를 만들지 않습니다. malformed/truncated/timed-out trace, 검증할 수
  없는 nonzero 종료, replay/spawn 실패는 `ERROR`/`NOT_RUN`입니다.
- compilation context가 실제로 없을 때만 cycle은 기존 unique project path-suffix heuristic을
  사용하고 `ESTIMATED`로 표시합니다. 이 경로의 ambiguous/unresolved include도 위치와
  후보를 보존합니다. context가 있으면 DB 부재 heuristic을 사용하지 않습니다.
- DB 부재 C++ lint도 임의 argv를 직접 실행하지 않습니다. ready capability의 `g++`를 우선하고,
  standalone에서는 canonical direct driver만 허용한 뒤 exact 경로와 같은 positive allowlist,
  source/compiler 경계, argument bound, minimal replacement environment, closed stdin을 적용합니다.
  unsafe package/include flag나 project-contained driver는 compiler 실행 전에 거부합니다.
- `ici.engines._cpp_include_trace` parser는 `-H` entry/depth, missing-include trace,
  include-guard trailer와 pseudo frame을 bounded하게 검증합니다. stale/unrecognized shape는 edge를
  추측하지 않고 `ERROR`/`NOT_RUN`으로 닫힙니다.

#### Compiler/clang-tidy adapter

이 adapter는 immutable `AnalysisContext`를 C++ compiler diagnostics와 optional clang-tidy의
단일 입력으로 확장합니다. `LintEngine`은 context를 다시 읽거나 compile database를 직접
재탐색하지 않고, `run_cpp_lint`와 `run_clang_tidy`에 같은 normalized
`CompilationUnit`을 전달합니다. 두 adapter는 `build_replay_command`의 approved external
compiler와 sanitized argv를 공유하며, 결과는 legacy `InspectionTarget`, native v3 `Finding`,
`ToolEvidence`와 structured `extra` counters로 함께 보존됩니다.

- 등록된 `gcc`/`g++` capability probe는 `--version` banner를 사용하며, 관측 결과가 지원된 GCC
  또는 Clang family를 식별할 때만 capability를 complete로 인정합니다. 중립 이름이나 Apple
  alias도 executable spelling이 아닌 기록된 family를 따릅니다. Compiler diagnostics는 capability
  path와 관측된 family/version을 기준으로 출력 형식을 선택합니다.
  실제 GCC family가 version 9 이상이면 `-fdiagnostics-format=json`을 사용하고, older approved
  GCC와 approved Clang-family driver/alias는 `-fdiagnostics-parseable-fixits` bounded text를
  사용합니다.
  같은 executable의 관측된 Clang family는 `g++` alias spelling보다 우선하고, controlled
  `-fdiagnostics-show-option`이 project의 rule-visibility 설정을 대체합니다. capability에 resolve되지
  않는 unknown/unapproved compiler는 text fallback 대상이 아닙니다. syntax replay는 controlled
  diagnostic flags를 최종 source operand 앞에 넣고, unused-function replay는 source의 원래
  positional semantics를 보존한 채 flags를 뒤에 붙입니다. `_cpp_diagnostics.py`는 JSON/text를 atomic하게 파싱하며, malformed
  일부 결과를 성공한 일부와 섞지 않습니다. project-relative와 external location을 정규화하고
  stable rule ID, child/note diagnostic, fix-it range/replacement를 보존합니다.
- clang-tidy는 `auto`/`required`/`off` 정책으로 capability와 exact compilation database를
  확인한 뒤 covered production unit만 실행합니다. `clang_tidy_checks`가 있으면 built-in
  checks보다 우선하고, explicit `clang_tidy_config`가 project-bounded `.clang-tidy` discovery보다
  우선합니다. discovery는 source에서 project root까지만 올라가며 parent-of-project 설정을
  찾지 않습니다. explicit/discovered config는 project containment·regular-file·size·NUL
  경계를 통과해야 하고 `ExtraArgs`/`ExtraArgsBefore` compiler-argument injection과
  `InheritParentConfig` parent inheritance는 거부됩니다.
- Security boundary는 direct approved compiler/clang-tidy executable, positive replay option
  allowlist, source/working-directory/project containment, minimal replacement environment,
  closed stdin, bounded argv/output와 no-shell execution입니다. fix-it은 finding remediation과
  report metadata의 제안으로만 남으며 기본 실행은 source나 compilation context를 수정하지
  않습니다. context가 없거나 compile coverage가 맞지 않거나 context/output이 missing/malformed,
  process가 timeout/truncated/실패하거나 translation-unit/global budget을 넘으면 heuristic으로
  조용히 대체하지 않고 `ERROR`/`NOT_RUN`으로 fail-closed합니다.
- Compiler와 clang-tidy adapter는 서로 독립적으로 최대 2,048 translation units, unit당 120초,
  전체 600초의 실행 예산을 적용합니다. compilation context 자체에 error diagnostic이 있으면
  compiler replay도 시작하지 않으며, 남은 정상 unit의 부분 결과를 clean evidence로 만들지 않습니다.
- Evidence는 compiler와 clang-tidy를 별도 `ToolEvidence`와 diagnostic family로 기록하고,
  `clang-analyzer-*` rule은 clang-tidy 일반 rule과 별도 analyzer family로 유지합니다. clang-tidy
  rule-less 또는 primary와 같은 rule의 `note:`는 출력 stream에서 바로 앞의 primary에만
  contiguous하게 귀속되고 다음 primary가 새 group을 시작합니다. 이 관련 진단은 lint의
  primary-only target/finding/count를 늘리지 않으면서 `Finding.related_locations`와 fix-it
  metadata로 투영됩니다. canonicalization은 관련 위치를 deterministic 순서로 정렬하며 JSON/
  HTML은 전체 inventory를, GitHub Markdown은 engine당 최대 100개의 visible related row를
  보존/표시합니다. optional `auto`에서 unavailable tool은 분석 결과를 무효화하지 않는 경고로,
  `required`에서는 실행 오류로 승격되며, compiler diagnostics와 analyzer findings의
  category/confidence/remediation은 서로 섞이지 않습니다.
- C++ primary finding category는 isolated `_cpp_diagnostic_categories.py`의 `tool-rule-v1`
  정책으로 투영되며 free-form diagnostic message가 아닌 normalized `family`와 `tool_rule_id`만
  입력으로 사용합니다. compiler와 unknown family는 `CORRECTNESS`로 fallback하고, recognized
  family별 ordered security/resource/compatibility/correctness exceptions 뒤에 보수적인
  fallback을 적용합니다. lint `extra`에는 policy ID와 모든 v3 category의 primary diagnostic
  count가 함께 남습니다. 정확한 ID 목록과 precedence table은
  [사용자 가이드](user-guide.md#c-diagnostic-category-policy)에 고정합니다.
- Clang 기반 clang-tidy/clazy replay가 compile database의 approved `g++`를 선택하면
  `_cpp_tooling`은 replay executable과 capability path의 resolved file identity를 비교합니다.
  일치할 때만 같은 GCC를 `c++`와 `c`로 각각 bounded `-E -x <lang> -v -` probe하고,
  sanitized `-m*`/sysroot selector만 probe argv에 남깁니다. C++ include-search에서 C include-search를
  차집합한 경로를 compiler 순서 그대로 `-nostdinc++`와 ordered `-isystem` pairs로 두 adapter의
  compiler arguments에 붙입니다. probe별 최대 5초·합계 최대 10초·131,072 characters·64
  directories, replacement environment, closed stdin과 compiler의
  device/inode/mode/size/mtime/ctime 및 working-directory의 device/inode/mode/mtime/ctime
  identity를 포함한 projection cache key를 사용하며, 두 identity는 probe 전후에도 재검증됩니다.
  결과는 별도
  `g++ stdlib include search` `ToolEvidence`로 발행하고 parsing 뒤 raw compiler prose는 보관하지
  않습니다. C translation unit 또는 다른 compiler identity에는 projection을 적용하지 않습니다.
  일치한 GCC가 probe 도중 교체되거나 probe parse/timeout/truncation/nonzero/unresolved 오류가 나면
  analyzer 실행 전 atomic toolchain-context error입니다.
  이 경계는 Ubuntu 24.04의 GCC 13/14 혼재를 재현한 toy-projects PR #38 run `33531285208`의
  Qt 5/Qt 6 deep clazy 실패를 해결했고, fixed local pyz에서 `/usr/include/c++/13`,
  `/usr/include/x86_64-linux-gnu/c++/13`, `/usr/include/c++/13/backward` projection, 2 probes,
  12 sources의 exit 0과 expected warning 보존으로 확인했습니다.

#### Qt clazy and generated-code linkage

이 단계는 compiler/clang-tidy의 exact-context 경계를 유지하면서 Qt 프레임워크 특화 진단과 빌드 생성 단계를
추가한다. `LintEngine`은 한 번 만든 `AnalysisContext`를 compiler, clang-tidy, clazy와
generated-code verifier에 공유하며, 각 adapter는 독립된 `ToolEvidence`·diagnostic family·
target을 발행한다.

`Toolchain`의 canonical `clazy` capability는 `clazy-standalone`을 먼저 probe하고, 후보
alias가 `clazy`인 경우 compiler-wrapper provider로 표시한다. `clazy = auto|required|off`는
선택/필수/비활성 정책이며, `clazy_profile = level0|level1`은 전역 `AnalysisProfile`과
독립적인 rule selection이다. `clazy_checks`가 있으면 제한된 명시 목록이 profile을 대신해
manual/level2 noisy check를 opt-in한다. standalone argv는 `--checks`, `--only-qt`, source,
`--` 뒤의 `tooling_arguments`로 구성되고, wrapper argv는 context replay를 유지하면서 approved
`clang++` 경로를 replacement environment의 `CLANGXX`로 고정한다. 두 provider 모두 no-shell,
closed stdin, no `-p`, no `--fix`, no compilation-database reread 원칙을 지킨다.

`tooling_arguments`는 include/define/standard/ABI와 warning selection을 포함한 exact semantic
context를 유지하되 production gate의 warning-as-error 정책만 diagnostic-only projection으로
낮춘다. `-Werror`는 제거하고 `-Werror=<rule>`은 `-W<rule>`, 두 pedantic-errors 철자는
`-pedantic`, GCC legacy implicit-declaration alias는 대응 warning으로 바꾸며 `-Wno-error*`는
그대로 둔다. clang-tidy, clazy standalone, clazy
compiler-wrapper가 이 단일 projection을 공유하므로 ordinary finding은 tool execution failure가
아니라 위치 있는 diagnostic으로 보고된다. 실제 syntax/context 오류와 process·parser 오류는
기존처럼 fail-closed한다.

projection 결과는 core replay의 drop/reject/positive-safe 정책을 다시 통과해야 한다. 따라서
crafted `-Werror=<suffix>`가 변환 뒤 `-Wp,-MD,...`, `-Wa,...`, `-Wl,...` 같은 compiler
subtool forwarding으로 바뀌면 명령을 만들지 않고 `unsafe-tooling-warning-policy` replay error로
닫는다. 중첩 `-Werror=error=<rule>`도 warning flag가 될 때까지 반복 demotion한 뒤 같은 검증을
적용한다.

`_cpp_diagnostics.py`의 clazy parser는 compiler text parser와 별도로 `-Wclazy-*` rule shape,
located source/line/column, parent-note 관계를 bounded atomic하게 검증한다. `LintEngine`은
clazy family를 독립적으로 집계하고 `tool-rule-v1`의 bounded stem과 stable exact rule set
precedence로 category를 매핑한다. `clazy-lifetime`/`clazy-ownership`/`clazy-parent-less`/
`clazy-qobject-cast` bounded stems와 stable resource rules는 `RESOURCE`, `clazy-qt6`/
`clazy-deprecated`/`clazy-qstring-arg`/`clazy-qt-keyword` bounded stems와 stable compatibility
rules는 `COMPATIBILITY`, `clazy-qobject`/`clazy-connect`/`clazy-signal`/`clazy-slot`/
`clazy-qevent-cast` bounded stems와 stable correctness rules는 `CORRECTNESS`, 나머지는
`MAINTAINABILITY`이다. stem 자체 또는 `-`/`.` child만 인정하고 arbitrary substring은
분류하지 않으며 resource row가 correctness row보다 우선한다. 함께 출력된 일반 compiler warning은 같은 bounded 문법으로
원자 검증한 뒤 compiler lint의 중복 finding을 만들지 않도록 제외한다. parser가 일부만 이해한
output을 성공으로 남기지 않기
때문에 malformed output, replay/context mismatch, process failure는 engine `ERROR`와
`NOT_RUN` evidence로 격하된다. Ubuntu Noble clazy 1.11의 legacy raw-source/caret/replacement
context는 located diagnostic의 project source line과 raw text가 exact match일 때만, 뒤이어
bounded replacement preview를 최대 하나 허용한다. source mismatch, forged/extra preview와
그 밖의 malformed legacy context는 partial finding을 남기지 않고 atomic하게 거부한다.

외부 Qt macro context를 검증할 때 parser의 read authority는 exact sanitized compiler argv에서
추출한 approved include roots와 project root로 한정된다. 최대 512개의 bounded directory만
허용하고 외부 header 위치는 결과에서 `[external]`로 투영한다. source line은 `O_NOFOLLOW`
regular-file descriptor로 읽으며 열기 전후 device/inode/size/mtime identity를 비교한다.
source-context 누적은 1,000,000 bytes, 한 줄은 8,192 characters로 제한되고, symlink·비정규
파일·identity 변화·root 밖 경로·preview mismatch·extra/forged preview·bound 초과는 partial
finding 없이 fail-closed한다. clazy process가 nonzero로 끝나는 경우에도 parser를 실행하지
않고 atomic `ERROR`를 발행하며, `ToolEvidence.error`에는 raw prose/path 대신 bounded exit code,
kind counts와 processing/output flags만 둔다.

Qt generated-code verifier는 project source scope에서 `.ui`, `.qrc`, 그리고 주석·문자열을
제거한 C++에서 실제 `Q_OBJECT` 선언을 bounded하게 발견한다. immutable compilation context의
include path와 unit source를 이용해 `ui_<stem>.h`의 bounded indirect translation-unit include
linkage, `qrc_<stem>.cpp` generated
unit, `moc_<stem>.cpp`·`<stem>.moc`·`mocs_compilation.cpp`의 moc linkage를 확인한다. 모든
target은 원래 `.ui`/`.qrc`/header의 project-relative path와 1-indexed line을 가리킨다.
동일 context의 include path, defines, successful compiler replay에서 Qt 5/Qt 6 major를 계산하며,
successful replay가 확인된 경우에만 generated linkage와 compatibility PASS를 생성한다.
중복 generated stem은 exact 연결로 주장하지 않고 WARN으로 닫는다. 따라서 CMake
AUTOMOC/AUTOUIC/AUTORCC와 qmake direct generated unit을 같은 evidence 모델로 다룬다.

clazy는 최대 2,048 translation units, unit당 120초, 전체 600초와 1,000,000자 output bound를
사용한다. generated-code input도 bounded file/input limit과 project containment를 적용한다.
이 계층은 소스를 수정하거나 generator를 재실행하지 않으며, 생성 산출물의 존재만 확인하는
heuristic으로 성공을 주장하지 않고 exact compilation database linkage를 요구한다.

#### `dead`/`dup` 공통 bounded source intake

`dead`와 `dup`는 엔진별 파일 열기 대신 `engines/_source_inputs.py`의 공통 intake를 사용한다.
각 엔진이 선택한 path를 symlink를 따라가지 않는 lexical `.`/`..` 정규화로 project-relative
POSIX 경로에 매핑하고 unique path로 deduplicate한 뒤, deterministic lexical order로 정렬한다.
그 후 project containment와 regular-file/no-follow read를 확인하고 strict UTF-8 immutable
snapshot으로 만든다. 이 경계를 공유하므로 변경·누락·symlink escape·invalid UTF-8/NUL·지원하지
않는 source 확장자 같은 입력 오류가 한 엔진에서만 조용히 사라지지 않는다.

기본 ownership 정책은 generated/autogen 디렉터리, `moc_`/`qrc_`/`ui_`/
`mocs_compilation`/`.moc` 이름과 vendor/dependency 디렉터리를 제외한다. `dead`와 `dup`의
`include_generated` 및 `include_vendor`는 각각 이 정책을 해제하는 boolean opt-in이며 기본값은
`false`다. 두 분류가 겹치는 path는 두 opt-in이 모두 literal `true`일 때만 포함된다. `dup`의
owned C/C++ headers (`.h`, `.hh`, `.hpp`, `.hxx`)도 inventory에 포함되며, standalone `.moc`는
discoverable하지만 generated 분류로 기본 제외된다. unique candidate는 최대 8,192개,
정책 적용 뒤 owned/analyzed source는 최대 2,048개, source당 8 MiB, aggregate 64 MiB UTF-8
bytes를 사용한다. 정책으로 제외된 파일은 owned cap을 소비하지 않으며, `source_files_excluded`
는 unique path 수이고 `source_exclusion_counts`는 겹치는 reason을 각각 세므로 합계가 더 클 수
있다. 한도를 넘거나 read가 안전하지 않으면 위치가 있는 `SourceInputError` `ERROR`/`NOT_RUN`을
발행하고 partial source 결과를 만들지 않는다. 전부 제외된 inventory는 `SKIP`/`NOT_APPLICABLE`로
구분해 기록한다.

directory-relative open을 제공하지 않는 플랫폼에서는 reader가 열기 전에 모든 path component의
symlink를 `lstat`으로 precheck하고, 읽은 descriptor의 identity와 내용을 second read로 다시
확인해 intake 중 변경을 fail-closed한다. 직접 주입되는 resource limit도 positive integer가
아니면 `invalid-limit`로 닫힌다. `dead`는 Python source discovery를 한 번만 캡처한 뒤 그
목록을 ordering과 intake에 재사용한다.

Python `dead`의 AST reachability/name-reference와 `dup`의 token/region matching은 이 snapshot
위에서 수행되며 각각 `python-ast-heuristic` 및 `language-lexical-region-heuristic`
provenance와 `ESTIMATED` evidence를 보존한다. C/C++ `dead`의 별도 compiler-backed slice는
아래에 정의한 좁은 TU-local claim만 `MEASURED`/`EXACT`로 기록한다. `dup`의 accepted bounded
language-aware slice는 Python과 C/C++ token window를 language key로 분리하고, 언어별
line-preserving lexical normalization과 function/class/import 또는 function/preprocessor
region boundary를 적용한다. shared normalized window seed는 rolling hash 후 실제 normalized
equality를 확인하고, blank/comment gap을 허용하되 region을 넘지 않으며, bounded extension과
maximal-match deduplication을 수행한다. clone fingerprint는 `sha256/type2-region-v2`로
안정적으로 생성하고 분석된 파일마다 PASS location target을 남긴다. 이 lexical/token-region
slice는 PR #135와 exact-main CI/Pages evidence까지 완료됐지만, whole-program/linker dead-symbol
분석, full duplicate semantic analysis 및 I4-3 전체 checkpoint는 아직 pending이다.

정확한 local test, toy/project 측정값과 remote acceptance는
[`bounded language-aware duplicate workthrough`](../workthrough/2026-09-02-bounded-language-aware-duplicate.md)에
고정한다. 이 slice는 버전을 올리지 않으며 stable release를 만들지 않는다.

#### Compiler-backed C/C++ unused internal functions

`DeadCodeEngine`의 C/C++ 경로는 shared immutable `AnalysisContext`의 compilation database가 정확히
덮는 모든 owned project C/C++ source translation unit을 분석한다. `project.cpp_external_build_dirs`로
지정한 external build directory 안의 owned source도 immutable exact database가 해당 unit의 모든
알려진 configuration을 덮으면 포함하며, build/link engine의 self-link 제외 정책은 이 source scan에
적용하지 않는다. 각 selected `CompilationUnit`의 explicit `language`는 `c` 또는 `c++`여야 하며,
다른 값이나 빈 값은 compiler 실행 전에 거부한다. 각 unit은 canonical `sha256:` configuration identity를
가져야 하고, identity는 `directory`/`argv`/`output` payload의 canonical digest를 replay 전에 다시
계산해 일치해야 한다. `unity_build = true`인 context만 source ownership을 증명할 수 없다는 이유로
거부하며, `false` 또는 `null`이라는 값 자체는 이 guard의 거부 사유가 아니다.
`cpp_unused = "auto" | "required" | "off"` 정책은 Python `dead`와 독립적이다. pure C++ scope에서
exact context/compiler가 unavailable 또는 not-applicable이고 실제 analysis/context/intake error가
없을 때만 `auto`가 required gate를 완화한다. 이 경우 C++ scope는 `SKIP`/`NOT_RUN`, `required = false`가
되어 suite에는 `WARN`만 기여한다. `required`는 unavailable도 `ERROR`/`NOT_RUN`으로 승격하며,
auto/required 모두 context가 존재한 뒤 드러난 source coverage, ingestion/configuration/identity/replay/
parser/compiler 오류를 휴리스틱으로 대체하지 않고 `ERROR`/`NOT_RUN`으로 fail-closed한다.

compiler는 capability inventory가 성공적으로 probe한 외부 regular **직접 GCC/Clang driver**만
허용한다. `gcc`/`g++`/`clang`/`clang++` capability에 resolve되는 `cc`/`c++` 또는
target/version 표기의 approved alias는 사용할 수 있지만, wrapper·project-contained executable·
unprobed/unknown driver는 거부한다. 등록된 `gcc`/`g++` probe는 `--version` banner에서 관측된
GCC 또는 Clang family를 확인해야 complete이며, 중립/Apple alias는 executable spelling이 아닌
기록된 family를 따른다. `cpp_unused = auto|required`인 standalone `ici dead`는
`dead`에 필요한 scoped compiler probe와 설정된 `[doctor].required_tools`만 요청하며, 전체 tool
registry를 무조건 probe하지 않는다. `cpp_unused = "off"`이면 C++ path discovery로 scope 존재
여부만 판별하고 C++ bytes를 source intake/replay에 넣지 않으며 compiler/tool
evidence도 만들지 않는다. standalone `ici dead`는 이 경우 C++ compilation-context preflight도
요청하지 않으며, full verify에서 다른 엔진을 위해 shared context가 준비되더라도 이 정책은
hybrid의 Python 분석을 차단하지 않는다.

선택된 각 source/configuration의 compile argv는 `build_replay_command(...,
operation="unused-functions")`로 sanitized replay command가 된다. 기존 output operand와
warning suppression을 제거·투영한 뒤 `-Wunused-function`, `-Wno-error=unused-function`,
`-S`, `-o os.devnull`을 추가하고 project의 diagnostic-option visibility 설정을 제거한 뒤
`-fdiagnostics-show-option`을 강제한다. 이 operation에서는 canonical source operand를 원래 compile
argv 위치에 정확히 한 번 유지하고, controlled diagnostic flag를 source 뒤에 덧붙인다. option
separator 뒤의 추가 operand는 `-w`나 두 번째 `--`를 포함해 모두 거부한다. `-S`는
compiler front end가 소유한 unused-function diagnostic 단계를 실행하게 하지만 assembly를
discard하므로 assembler/linker를 호출하지 않고 project output을 만들지 않는다. resolved
capability의 관측 family가 GCC이고 version이 9 이상이면 JSON diagnostics를, older GCC와
approved Clang-family driver/alias는 `-fdiagnostics-parseable-fixits` bounded text를 사용한다. 같은
executable에 `g++`와 Clang capability가 함께 resolve되면 관측된 Clang family가 alias spelling보다
우선한다. unknown/unapproved driver는 text fallback 대상이 아니며 replay 전에 거부한다. diagnostic parser는
일부만 이해한 출력을 성공으로 남기지 않는다.

exact finding은 compiler가 해당 owned translation unit source에 internal-linkage 함수의
`-Wunused-function` 위치 범위를 귀속하고, 그 source의 모든 configuration에서 필터된
`(path, start/end line, start/end column)` 집합이 관찰될 때만 생성된다. configuration별
집합이 다르면 오류로 닫고 finding을 비운다. accepted diagnostic의 logical path는 selected TU와
정확히 같아야 하고 line/column 범위도 immutable source snapshot 안에 있어야 한다. 범위를 벗어난
`#line`/macro-expansion remapping은 fail-closed하며, ici는 accepted logical 위치를 physical source
provenance 또는 source-spelled declaration으로 역추적하지 않는다. logical path가 선택된 TU
source와 다르면 source-owned finding이 되지 않는다.

source snapshot은 no-follow regular-file reader로 읽고 descriptor identity와 double-read content를
검사한다. 준비 시와 각 replay 직전에 project-contained working directory의
`device/inode/mode` identity를, approved external regular compiler의
`device/inode/mode/size/mtime/ctime` identity를 캡처·검사하며, otherwise-successful replay 뒤에도
각 identity와 source snapshot bytes를 다시 검사한다. 다른 replay 실패는 그 자체로 즉시
fail-closed한다. `ToolEvidence`는 compiler name/path/version, sanitized argv, return code 및
timeout/truncation을 보존하고, `cpp_unused_details`는 source 위치, tool rule
`-Wunused-function`, configuration identity, tool name/version과 원 compiler message를 보존한다.
C++ finding의 confidence는 `EXACT`이다. 순수 C++ `dead` result의 `analysis_provenance`는
`cpp-compiler-unused-function`이고, hybrid result는
`python-ast-heuristic+cpp-compiler-unused-function`으로 두 scope를 함께 표시한다.

이것은 TU-local compiler-warning 계약이며 whole-program reachability 증거가 아니다. exclusion
count는 먼저 rule ID가 정확히 `-Wunused-function`이고 kind가 warning인 diagnostic만 대상으로
하며, 그 뒤 logical path가 selected source와 다른 경우에만
`cpp_unused_non_tu_diagnostics_excluded`를 1 증가시킨다. unrelated warning, note와 error는 이
count에 포함하지 않는다. matching warning 자체가 unlocated이면 source 귀속을 증명할 수 없으므로
clean 결과로 제외하지 않고 전체 C++ probe를 fail-closed한다. malformed output도 마찬가지다. macro-generated 정의도 별도
physical source provenance를 만들지 않고 compiler-attributed expansion 위치를 사용한다.
external-linkage 함수, template, inline/COMDAT, linker reachability, dynamic lookup, plugin 및
Qt meta-object reachability는 분류하지 않는다. generated/autogen, `moc_`/`qrc_`/`ui_`/`.moc`
source도 기본 ownership 정책으로 제외되며, 이 slice는 Qt generated-code linkage를 대신하지
않는다. C++ source가 compile database에서 직접 replay되지 않으면 heuristic fallback을 사용하지
않고 위 policy에 따라 닫는다.

실행 예산은 최대 2,048 translation units, unit당 120초, 전체 600초, compiler output
1,000,000 characters다. 공통 source intake의 최대 8,192 candidate, 2,048 owned source,
source당 8 MiB, aggregate 64 MiB 및 sanitized replay의 최대 32,768 arguments/1 MiB
characters bound도 함께 적용한다.
malformed/nonzero/timeout/truncated output, source/tool/cwd identity 변경, replay 오류와 budget
초과는 partial finding 없이 `ERROR`/`NOT_RUN`으로 fail-closed한다. C++ scope는 첫 replay/process/
parse/identity 오류에서 fail-fast하고 이후 unit을 실행하지 않는다. 모든 replay가 성공한 경우에도
source별 configuration diagnostic set이 하나라도 다르면 C++ exact targets를 전부 폐기하는
atomic merge를 적용한다. 이전에 성공적으로 완료·기록된 compiler observation의 source는 exact PASS/WARN 대신 위치가 있는
`C++UnusedFunctionsInvalidated` `SKIP` target으로 남겨 실행 사실과 증거 폐기를 함께 표현한다.

Python과 C/C++ 범위가 모두 정상 완료된 실행은 language evidence와 provenance를 분리한다.
예를 들어 `extra.language_evidence`는 `python = ESTIMATED`, `cpp = MEASURED`이고
`analysis_provenance`는 `python-ast-heuristic+cpp-compiler-unused-function`이다. 따라서
Python source가 하나라도 있으면 전체 `dead` result evidence는 `ESTIMATED`이며, C++만 exact로
실행된 경우에만 전체 evidence가 `MEASURED`가 된다. C++ scope가 나중에 실패해도 이미 완료된
Python findings는 유지되며, Python finding은 `FindingConfidence.MEDIUM`과 빈 compiler
tool attribution을 유지한다. Support Matrix의 선언은 C++ `dead`를 `TOOL_BACKED`/`EXACT`로,
Python `dead`를 `HEURISTIC`으로 표시하고, 관측 confidence는 실제 language evidence가
`MEASURED`일 때만 `EXACT`가 된다.

정확한 focused test와 toy/project 측정값은
[`compiler-backed C/C++ unused-function workthrough`](workthrough/2026-09-03-compiler-backed-cpp-unused-functions.md)에
고정한다. 최종 viewer standalone `dead` evidence는 `PASS`/`MEASURED`이며, 정확히 8개 source, 8개
configuration, 8개 target, 8개 `tool_evidence` 행, 0개 unused function, `cache_key = null`을 기록한다.
이 slice는 PR #137의 required CI, 단일 sticky comment의 두 report 링크,
PR·main artifact/Pages byte match, exact-main CI와 Pages 배포까지 수락됐다. 상세
provenance와 해시는 위 workthrough에 고정한다. 앞서 수락된 function-boundary/metric
slice와 이번 TU-local slice보다 넓은 whole-program dead-symbol, linker/dynamic
reachability, full duplicate semantics 및 I4 aggregate는 여전히 미완료 범위이다.
버전은 `0.10.2`로 유지하고 별도 release는 만들지 않는다.

#### Compiler-backed C++ function boundaries

`ComplexityEngine`은 shared immutable `AnalysisContext`의 exact `CompilationContext`와 covered
production units를 사용해 전용 clang-tidy probe를 실행합니다. probe는 프로젝트나 lint 정책을
재사용하지 않고 `readability-function-size`와 threshold-zero options만 전달하며, diagnostic
location/size notes를 source body geometry로 매핑합니다. shared clang-tidy parser는 설명 note를
primary 아래 `related_diagnostics`에 보관하지만, boundary parser는 각 primary와 그 related
diagnostic을 stream 순서로 소비해 function-size lines/statements/parameters evidence를 다시
매핑합니다. 이는 structural consumer의 입력만 확장하며 lint의 primary-only finding/count
계약은 유지합니다. `boundary_source = "clang-tidy-ast"`
는 함수 경계에만 해당합니다. 경계 안의 CC는 masked `if`/`for`/`while`/`case`/`catch`/`&&`/`||`/`?`
token을, nesting은 brace를 세는 ici metric이며 `metric_confidence = "medium"`입니다.

AST boundary target은 source-spelled named function이며 function template, conversion/call/subscript
operator, literal operator를 포함합니다. `function_kind`, `function_template`, `function_origin`으로
kind/template/provenance를 보존합니다. lambda는 독립 함수 target으로 만들지 않으며 그 body는
enclosing function의 CC/nesting에서 제외합니다. Macro-generated function이 expansion site에서
진단되면 해당 scope를 명시적으로 제외하고 `extra.cpp_scope_exclusions.macro_generated_function`에
count를 기록합니다. 이 경우 파일의 다음 brace를 body로 추정하지 않습니다. fallback scanner는
operator 이름을 보존하고 multiline preprocessor definition/continuation과 standalone macro
invocation을 skip하며, lambda 제외 count는 `extra.cpp_scope_exclusions.lambda`로 공개합니다.

`cpp_boundaries = "auto" | "required" | "off"` (기본 `auto`)는 lint의 `clang_tidy` 정책과
독립적입니다. `auto`는 exact context/database 또는 approved direct clang-tidy가 없을 때만
source scanner로 폴백해 `ESTIMATED`/heuristic 경계를 남깁니다. 빈/미보고 source-spelled definition은
heuristic으로 남을 수 있지만 macro-generated expansion은 target에서 제외됩니다. 시도된 tool·replay·parser·timeout·truncation·coverage·budget
오류는 silent fallback 없이 `ERROR`/`NOT_RUN`으로 닫습니다. 단, clang-tidy가 visible project
diagnostics와 함께 정확한 `Suppressed N warnings (N in non-user code).`를 보고하는 경우는
외부/system 진단만
억제한 정형 회계로 허용합니다. NOLINT/project/mixed/malformed/count-mismatch suppression은 계속
`ERROR`/`NOT_RUN`으로 fail-closed합니다. `required`는 unavailable 또는 partial/estimated boundary도
오류로 승격합니다. `off`는 probe를 실행하지 않고 heuristic
경로를 사용합니다. caller가 만든 bounded source snapshot과 mapped-source cache를 전달하고,
replay 전·도구 완료 후 source identity를 재검증합니다. C++ 전체 source inventory는 최대 2,048
source files와 64 MiB aggregate UTF-8 source bytes cap을 적용하며, 성공한 configuration마다
geometry, name, kind, provenance가 일치할 때만 merge합니다. configuration별 clang-tidy
lines/statements/parameters는 `configuration_metrics`에 보존합니다. 누락이나 configuration-dependent
geometry는 partial warning으로 보존됩니다. function-size metric 값이 configuration마다 다르거나
body에 conditional preprocessor branch가 있으면 mode가 `partial`, 해당 metric confidence가 `low`가
됩니다. compiler-backed function metrics 또는 configuration coverage가 partial/low-confidence로
남으면 `required`에서는 `ERROR`/`NOT_RUN`으로 fail-closed합니다.
한 실행의 한도는 2,048 units, source당 8 MiB, run source bytes 64 MiB, mapped-source cache bytes
16 MiB, output 1,000,000자, parser 10초, unit당 120초, 전체 600초입니다. approved tool executable은
매 process 실행 직전에 다시 resolve해 device/inode/mode/size/mtime/ctime identity를 확인하며 변경·부재는
fail-closed입니다. assigned `[]`/`+[]` lambda initializer는 phantom fallback 함수로 만들지 않습니다. same-line/overload,
constructor·parameter/default·noexcept·trailing `requires`의 braced expression, function-try/catch,
`<%`/`%>` digraph body를 parser regression으로 고정합니다. `dir_fd`/`O_DIRECTORY`가 없는
fallback도 resolved named path의 containment와 device/inode/size/mtime identity를 read 뒤
재검증해 intermediate symlink/TOCTOU를 fail-closed합니다.

PR #130의 historical compiler-boundary baseline은 두 번 byte-identical인 candidate SHA
`7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`와 Python 3.10
`1,626 passed, 2 skipped`를 남겼습니다. 이후 local `feat/cpp-function-scope-policy`
candidate는 두 번 byte-identical인 `dist/ici.pyz` SHA
`2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7`와 real extracted
`clang-tidy-21`을 사용한 Python 3.10 full suite `1,656 passed, 2 skipped`, Ruff check/format,
mypy, packaged smoke, parser/source mapping 628 pure code lines 및 process runner facade 487
pure code lines의 self line gate 통과를 남겼습니다. Fresh clean toy `main`의 BuildScope
`auto`/`required`, DiskMap `auto`, LogLens `auto` 교차 probe와 4/4 title·Zero-CDN 검사도
완료됐으며 exact/partial 수치와 required 오류는 [C++ function-scope policy workthrough](workthrough/2026-09-02-cpp-function-scope-policy.md)에
기록합니다.

[PR #131](https://github.com/jihoon22-lee/ici/pull/131) `feat(complexity): classify C++ function
scopes and metric provenance`는
[`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7)로
squash merge됐습니다. PR CI [run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495)은
성공했고 exactly one sticky marker/current run을 남겼습니다. PR ici/viewer Pages는 HTTP/title/
Zero-CDN과 artifact byte-match를 통과했으며 각각 `7,454,995`/`356,598` bytes였습니다.
Exact-main [run `33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450)도
성공했고 main JSON `source_commit`은 같은 SHA와 일치했습니다. main ici/viewer Pages 역시
HTTP/title/Zero-CDN과 byte-match를 통과했으며 ici는 `7,454,995` bytes/SHA
`182a0d05…5adbb75`, viewer는 `356,598` bytes/SHA `fb772d4a…c0c4794`였습니다. 두 run의
skip은 예상된 PR/main publish job뿐입니다. 이는 scope-policy slice acceptance이며
compiler-backed C/C++ unused-function의 narrow TU-local 범위를 넘어서는 whole-program/linker
dead 분석, full duplicate semantics, 남은 I4-3/I4-4 및 I4 전체 checkpoint를 닫지 않습니다.
버전은 `0.10.2`로 유지하고 release는 만들지 않습니다.

#### CTest/JUnit와 sanitizer evidence 경계

CMake adapter는 지원되는 CTest에서 `--output-junit`을 사용해 shadow 디렉터리에 report를
만들고, 실행 전에 예정된 `ici-ctest.xml`을 제거해 이전 실행의 stale report를 읽지 않는다.
그 뒤 `_read_ctest_junit`가 새 report만 stable regular-file/no-follow descriptor로 최대
1,000,000 bytes까지 읽는다. `_compile_db_paths._read_bounded_regular`의 containment,
크기, before/after identity 검사가 XML 입력에도 적용되며, 파일 누락·변경·malformed·oversized
입력은 bounded CTest stdout parser로 폴백한다. XML parser는 DTD와 NUL도 거부한다.

JUnit `failure`/`error` 및 `system-out`/`system-err`에 LeakSanitizer, AddressSanitizer,
UndefinedBehaviorSanitizer marker가 있으면 nominal PASS status라도 해당 executed case를
실패로 보존하고, public `message`에는 bounded 분류만 남긴다. sanitizer engine은 별도의
private `TestCaseResult.diagnostic_output` transport(UTF-8 최대 65,536 bytes, truncation flag)를
소비해 deterministic `kind`/`defect`, `ici.sanitize.*` detail rule, related stack-frame
locations, 관측/프로젝트 frame count 및 sanitizer process evidence link를 갖는 normalized
detail을 발행하고, 검증 가능한 경우 project-owned primary location과 native finding을
제공한다. native finding은 기존 `ici.legacy.sanitize.target` 호환 rule을 유지하고 상세
식별자는 `tool_rule_id`에 둔다. 외부 frame path는 `[external]`로
redacted되고 raw transcript/prose는 public finding이나 일반 test message에 복사되지 않는다.
timeout·process-output truncation·정규화 오류·project location이 없는 diagnostic은 clean
결과로 축약하지 않고 `ERROR`/`NOT_RUN` 또는 위치 오류 target으로 fail-closed한다. 완전한
project-owned location을 가진 signal 종료만 measured `FAIL`로 보존한다. 그 밖의 실패 text와
test name도 512 characters로 제한한다.

CTest/QtTest 상태도 같은 `TestCaseResult`로 보존한다. CTest JUnit의 `<skipped>`와
`status="notrun"`/skip/skipped/disabled/blacklisted, 그리고 stdout의 `Not Run`/`Disabled`/
`Skipped` verdict는 `passed = false`, `executed = false`입니다. 알 수 없는 CTest status는
`executed = true`, `passed = false`인 실패로 fail-closed합니다. QtTest의 `skip`/`<skipped>`는
미실행, `xfail`은 실행된 예상 실패이자 통과, `xpass`와 알 수 없는 `result`는 실행된 실패로
처리합니다. 이렇게 미실행 case는 측정 scope나 failure count에 섞이지 않으면서, 알 수 없는
상태가 통과로 축약되지 않습니다.

QtTest case-state parsing은 `-xunitxml`이 제공한 각 `<testcase>`를 읽는 경계이며, qmake의
`make check` transcript를 대체하지 않습니다. qmake adapter는 우선 transcript에서 테스트
바이너리별 결과를 권위 있는 scope로 세고, QtTest XML은 그 바이너리에서 관찰된 failure
detail을 보강하는 데만 사용합니다. 따라서 QtTest와 자체 `main()` 테스트가 섞인 qmake
프로젝트에서도 함수-level skip 전부를 개별 qmake scope로 추정하지 않습니다.

Sanitizer build가 성공했어도 적용 가능한 테스트 실행이 빠졌다면 evidence는 다음 정책을
따릅니다. required에서는 모든 case가 미실행이거나 실행된 case와 미실행 case가 섞인 경우
모두 `ERROR`/`NOT_RUN`이고, 실행된 실제 failure target은 `FAIL`로 보존합니다. optional에서는
모두 미실행이면 `SKIP`/`ESTIMATED`, clean 실행과 미실행의 혼합이면 `WARN`/`ESTIMATED`,
실제 failure와 미실행의 혼합이면 `FAIL`/`ESTIMATED`입니다. Test reporter는
`extra.skipped_tests`와 suite별 `skipped`를 보존하고 HTML에서는 SKIP을 별도 그룹으로
렌더링하므로, adapter state와 gate state가 서로 다른 층에서 사라지지 않습니다.

#### ThreadSanitizer deep profile의 격리 경계

`ThreadSanitizeEngine`은 `AnalysisProfile.DEEP`에만 등록된 별도 build owner이며,
`BuildVariant.THREAD_SANITIZE`(`thread-sanitize`)와 `-tsan` shadow를 요청합니다. CMake와
qmake adapter는 `-fsanitize=thread`를 compile/link에, `-fno-omit-frame-pointer -g`를
compile에 전달합니다. build descriptor가 없는 generic g++ 경로는 같은 instrumentation과
debug/frame-pointer flags에 generic `-pthread` link를 추가합니다. 이 shadow와 flags는
ASan/LSan/UBSan `SanitizeEngine` 경로와 분리되어 서로의 object나 runtime option을 재사용하지
않습니다.

실행 시 기존 `TSAN_OPTIONS`는 보존되고 `halt_on_error=1`이 추가됩니다. 공통 bounded
normalizer는 TSan의 완전한 `WARNING: ThreadSanitizer:` 또는 `SUMMARY: ThreadSanitizer:`
signature만 starter로 받아 diagnostic/frame/source 한도를 적용합니다. 알려진 defect는
stable taxonomy rule로 정규화하고 unknown wording은
`ici.sanitize.tsan.thread-safety-defect`로 수렴하며, validated project location만 primary로
남기고 외부 frame은 `[external]` sentinel로 redacted합니다. Python은 이 engine의 scope가
아니므로 ResourceWarning을 실행하지 않습니다. 실제 g++ race regression은 local에서 통과했지만,
이 구현은 아직 feature PR/main 및 Quality Zoo TSan acceptance 전의 증거이고 I4-4 전체와
release 완료를 뜻하지 않습니다.

### 4.3 선언형 엔진 파이프라인과 예외 격리 (`VerifyOrchestrator`)

`src/ici/core/pipeline.py`의 immutable `EngineDescriptor`가 각 엔진의 실행 계약과
데이터 흐름을 선언합니다. descriptor는 다음 필드를 가집니다.

- `name`, `dependencies`: 엔진 식별자와 선행 엔진
- `produces`, `consumes`: 엔진이 발행하거나 요구하는 artifact 이름
- `profiles`: 엔진이 선택될 수 있는 `fast`/`standard`/`deep` 집합
- `execution`, `build_variant`: 읽기 전용 관찰인지, `COVERAGE`/`SANITIZE`/`THREAD_SANITIZE` 같은
  mutable build session 소유자인지

내장 descriptor registry는 import 시점과 executor 생성 시점에 검증됩니다. 검증은 중복
엔진명·artifact producer, 알 수 없는 dependency, profile closure, 소비 artifact의
producer와 dependency 연결, cycle을 거부합니다. 따라서 잘못된 graph나 artifact 계약은
분석을 시작한 뒤 조용히 누락되지 않고 startup definition error가 됩니다.

검증 실행에서는 profile과 `enabled` 정책을 먼저 적용해 선택된 descriptor만 남깁니다.
선택된 graph는 안정적인 registry 순서의 topological layer로 실행됩니다. 한 layer의
독립적인 `read-only` 엔진은 기본 최대 4개 worker로 제한된 pool에서만 병렬 실행되고,
완료 수집은 descriptor 순서를 유지합니다. `build` 엔진은 read-only 작업이 끝난 뒤
실행되며 build owner끼리도 겹치지 않습니다. 따라서 동일 project/shadow tree를 변경하는
build node는 read-only 관찰이나 다른 build node와 동시에 실행되지 않습니다. 최종 결과
목록도 registry 선언 순서를 따르므로 완료 시점의 흔들림이 결과 순서를 바꾸지 않습니다.

엔진 초기화 또는 `run()` 중 예외는 해당 엔진의 명시적인 `ERROR` 결과와
`NOT_RUN` evidence로 변환되고, 공유 `AnalysisContext`와 다른 엔진의 결과는
보존됩니다. 단독 명령과 전체 검증은 `PASS`/`WARN`은 0, `FAIL`/`ERROR`는
1, `SKIP`은 2를 반환하는 공통 종료 코드 계약을 사용합니다.

### 4.4 분석 결과 캐시와 재현성 경계

`VerifyOrchestrator`는 엔진을 실행하기 전 `AnalysisCache`를 통해 user-local entry를
조회하고, 실행이 끝난 뒤 재사용 가능한 결과만 저장합니다. 구현은 책임을 세 모듈로
나눕니다. `cache_identity.py`는 입력을 읽기 전용으로 digest하고 key를 만들며,
`cache_codec.py`는 untrusted JSON의 decode/검증과 native finding serialization을 맡고,
`cache.py`는 inventory·load/store/clear와 원자적 파일 수명을 조정합니다. 기본 경로는
`~/.cache/ici/analysis/entries-v1/`이며 `XDG_CACHE_HOME` 또는 `ICI_CACHE_DIR`로
명시적인 로컬 경로를 지정할 수 있습니다. 네트워크 저장소는 자동으로 사용하지 않으며,
기본 cache store는 프로젝트 내부 `.ici`/`build` 경로가 아닙니다. `ICI_CACHE_DIR` override를
사용할 때도 checkout과 분리된 user-local 경로를 지정해야 합니다. `ici cache --clear`는
선택된 exact entries directory의 JSON/TMP entry만 대상으로 합니다.

cache key(`ici.analysis-cache-key/v3`)는 다음 identity를 canonical JSON으로 만든
SHA-256 digest입니다.

- canonical project root
- project source와 인식된 build/config 파일의 path·content·mode digest (`.ui`/`.qrc` 포함)
- profile을 포함한 effective ici configuration digest
- capability inventory의 toolchain path·version·details digest
- engine descriptor와 engine class source digest, 그리고 engine class가
  `CACHE_IMPLEMENTATION_MODULES`로 명시적으로 선언한 helper/dependency module source digest
  목록 (C++ lint에는 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`,
  `ici.engines._clang_tidy`, `ici.engines._clazy`, `ici.engines._cpp_diagnostic_categories`,
  `ici.engines._cpp_diagnostics`, `ici.engines._cpp_lint`, `ici.engines._cpp_tooling`,
  `ici.engines._qt_codegen` 포함;
  cycle에는 `ici.core._cpp_replay_policy`, `ici.engines._cpp_include_trace` 포함;
  complexity에는 `ici.core._compile_db_paths`, `ici.core._cpp_replay_policy`,
  `ici.core.cpp_replay`, `ici.engines._cpp_function_boundaries`, `ici.engines._cpp_tooling`,
  `ici.engines.cpp_text` 포함)
- `none`, `release`, `coverage`, `sanitize` 중 engine build variant
- compilation context identity: 선택된 database의 project-relative path와 바이트 digest,
  loader schema version, 정규화된 unit configuration/metadata와 diagnostics를 포함한 parse state
- ici producer version과 cache key schema version

엔진 implementation identity는 engine class의 module/qualname와 class source digest를 포함하며,
class가 명시적으로 선언한 helper/dependency module 이름의 sorted unique 목록과 각 module
source digest도 포함합니다. 따라서 import tree 전체를 암묵적으로 따라가지 않고, 엔진이
선언한 구현 의존성만 identity 경계에 들어갑니다. 이 key 설계는 소스나 정책이 같아 보여도
toolchain·엔진 구현·variant·compile database
내용/선택 경로·parse state·ici 버전이 달라지면 entry를 재사용하지 않게 합니다. database
digest는 실행 중인 immutable captured context를 식별할 뿐 live-file lease를 보장하지 않으며,
DB mutation은 다음 preflight에서 새 bytes와 context로 반영됩니다. 완전한
`PASS`/`WARN`/`FAIL` 결과는 evidence가
`NOT_RUN`이 아니고 timeout·truncation·tool error가 없으며 artifact manifest가 유효한
경우에만 저장할 수 있습니다. `ERROR`/`SKIP`, `NOT_RUN`, timeout/truncated/tool error,
invalid 또는 stale artifact는 저장·재사용하지 않습니다. 따라서 결과를 좋게 보이게 만드는
실패 cache는 허용하지 않지만, 완전한 증거를 가진 `WARN`/`FAIL`은 정상적인 분석 결과로
재사용될 수 있습니다.

단, `DeadCodeEngine`은 현재 `CACHE_REUSE_SAFE = false`를 선언하므로 verify 경로에서 `dead`
결과의 cache load와 store를 모두 건너뛴다(따라서 이 엔진에는 cache key도 발행하지 않는다).
현재 cache-v3 identity는 compiler executable content와 external/generated include의 완전한
dependency closure를 모델링하지 않으므로, 이 두 identity를 cache v3가 모델링할 때까지
Python-only와 hybrid를 포함한 모든 `dead` 결과의 cache key/load/store를 비활성화한다.

entry는 임시 파일에 전체 JSON을 쓰고 flush·`fsync`한 뒤 `os.replace`하는 방식으로
원자적으로 발행하며, cache 디렉터리와 파일은 user-local 권한(0700/0600)으로 생성됩니다.
손상·stale·symlink entry는 cache miss로 격하되어 검증 자체를 실패시키지 않습니다.
artifact manifest가 포함된 hit는 project/shadow containment, variant, config/toolchain
identity와 실제 artifact의 content·size·mode를 다시 검증합니다.

source digest 계산은 declared source와 인식된 build/config 파일을 프로젝트 밖으로 따라가지
않는 regular-file read-only 해시입니다. hash 전후 metadata가 달라지면 해당 실행의 cache를
끄므로, cache read/write가 프로젝트 source를 수정하거나 프로젝트 안에 임시 파일을 만들지
않습니다. `verify_report.json`과 engine별 `*_report.json`처럼 ici가 생성하는 report JSON
이름은 source input 후보에서 제외되어, report를 쓸 때 자기 자신 때문에 cache key가
바뀌지 않습니다. `--no-cache`는 lookup과 write를 모두 끄는 명시적 실행 경계입니다.

`cache_codec.py`의 read boundary는 다음을 모두 검사합니다.

- `O_NOFOLLOW`와 regular-file 확인으로 cache entry symlink/비정규 파일을 거부하고,
  entry 크기를 32 MiB 이하로 제한합니다.
- JSON object의 duplicate key와 `NaN`/`Infinity` 등 non-finite constant를 거부합니다.
- schema·key identity·finding/target/tool evidence·artifact manifest를 모두 decode한 뒤
  하나라도 맞지 않으면 해당 entry를 miss로 격하합니다.

새 directory는 `0700`, 새 entry 파일은 `0600`으로 만들고, `cache.py`는 entry 전체를
임시 파일에 쓴 뒤 flush·`fsync`·`os.replace` 순서로 발행합니다. 손상·stale·symlink·oversize
entry나 저장 오류는 검증 실패가 아니라 cache miss/저장 생략으로 처리합니다. `ici cache
--clear`는 정확한 `entries-v1` 아래의 `.json`/`.tmp` 파일만 지웁니다. artifact manifest가
있는 결과는 store와 load 양쪽에서 project/shadow containment, variant, config/toolchain
identity와 실제 artifact의 content·size·mode를 다시 검증합니다.

engine-level v3 JSON은 `cache_hit` boolean과 nullable `cache_key` digest를 선택적으로
가질 수 있습니다. 새 writer는 이 필드를 기록하지만 오래된 v3 archive에는 없을 수 있으므로
reader는 누락을 false/unknown으로 처리해야 합니다.

로컬 검증에서는 전체 Python 3.10 run이 935 tests passed였고 targeted cache 테스트도
통과했습니다. `standard` 첫 실행은 118.49초·hits 0, 두 번째는 2.38초·hits 12였으며,
두 normalized 결과의 SHA-256은 `95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`,
findings는 각각 3,497건이었습니다. HTML은 4,095,550 bytes이고 외부 참조는 0건이며,
재현성 script 두 build의 SHA-256은
`6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`으로 같고 project source
status unchanged였습니다. I2-4는 PR #97의 merge commit
`ef30059522729b376c5409e5bb49164aa538b128`, CI run `33345993304`, sticky comment
`5472411964`의 ici/viewer Pages 게시까지 완료됐습니다.

### 4.5 finding baseline 비교 파이프라인

`verify --baseline <project-relative-v3.json>`을 지정하면 오케스트레이터는 엔진 결과에서
native finding과 legacy `InspectionTarget` adapter를 모두 모아 baseline과 비교합니다.
비교 identity는 `(engine_name, fingerprint)`이며, 같은 identity가 여러 번 나타나는 경우에도
하나의 finding으로 접지하지 않고 multiset occurrence를 보존합니다.

각 identity 그룹은 다음 순서로 결정론적으로 pair됩니다.

1. primary location의 canonical path·line·column·label이 정확히 같은 occurrence를 먼저
   `unchanged`로 pair합니다.
2. 남은 current와 baseline occurrence는 정렬된 순서로 pair해 `moved`로 표시합니다. 이
   단계에서는 current와 baseline 위치를 모두 남깁니다.
3. current surplus는 `new`, baseline surplus는 `resolved`가 됩니다. 따라서 duplicate
   occurrence와 위치 이동이 서로의 결과를 덮어쓰지 않습니다.

severity rank가 상승하거나 baseline에서 suppression된 finding이 현재 actionable 상태로
돌아오면 `regressed = true`입니다. gate 대상(`gated`)은 현재 finding이 suppression되지
않고 severity가 `info`가 아닌 actionable occurrence이면서 `new`이거나 regression인
경우입니다. `resolved`, suppression이 유지되는 finding, informational finding은
inventory에는 남지만 gate를 만들지 않습니다. `--fail-on-new`가 켜져 있고 gated count가
0보다 크면 baseline gate가 실패하며, 엔진 자체의 `FAIL`/`ERROR`가 이미 있으면 그 결과를
우선 보존합니다.

baseline loader는 보안 경계를 결과 계약의 일부로 취급합니다. 입력 파일은 프로젝트 루트
안에서만 해석하고 64 MiB를 초과하면 거부하며, `ici.result/v3` schema version과 비교에
필요한 finding/metadata 계약을 엄격히 검증합니다.
각 primary/related location은 canonical slash-separated project-relative path와 유효한
1-indexed line/column region인지 검증하고, 절대 경로·`..` 탈출·backslash alias와 root
밖 symlink를 거부합니다. metadata, fingerprint digest, suppression boolean도 같은
경계에서 검증합니다.

`--write-baseline`은 결과 JSON을 output과 같은 디렉터리의 고유한
`.{name}.<random>.tmp` 파일에 먼저 쓰고 flush·`fsync`한 뒤 `Path.replace`로 원자 교체합니다.
쓰기/교체 실패 시 임시 파일을 정리하므로 기존 baseline이 부분 파일로 덮이지 않습니다.
같은 파일을 입력과 출력으로 쓰더라도 먼저 비교하지만, `fail-on-new` gate가 실패하면 원본을
보존해 새 regression이 다음 실행의 baseline으로 자동 승격되지 않게 합니다.

v3 schema의 `analysis_metadata`와 `baseline_comparison`은 optional nullable field입니다.
따라서 이 필드가 없는 기존 v3 archive도 계속 읽고 렌더링할 수 있으며, metadata가 없는
baseline을 비교하면 호환성 warning만 추가합니다. v2 archive는 migration helper를 통해
v3 copy로 변환할 수 있지만, baseline loader가 직접 비교하는 입력은 v3입니다.

---

### 4.6 Candidate artifact와 Quality Zoo 수용 경계

candidate 검증은 일반 PR 검증·stable release·HTML publish와 분리된 두 단계 control plane이다.
`candidate-artifact.yml`은 exact ici `main` commit의 Merge Gate와 재현 빌드에 결합된
`ici.pyz`/checksum/provenance 세 파일을 만들고, `candidate-quality-zoo.yml`은 ici 저장소에서
수동으로 실행된다. consumer workflow는 exact ici target SHA와 toy-projects `main` SHA,
artifact ID, 원본 ZIP SHA-256을 먼저 검증한 뒤 후보 ZIP을 내려받는다.

consumer는 candidate manifest가 가리키는 Actions run/check/job/attempt와 canonical URL을
독립 API 응답으로 재검증한다. 인증된 Actions/Checks/Contents 읽기는 artifact와 provenance
evidence를 가져오는 단계에만 사용하며, candidate preflight·intake·Quality Zoo 실행 전에는
`GH_TOKEN`, `GITHUB_TOKEN`, OIDC/runtime token을 제거한다. 검증된 로컬 `ici.pyz` 경로만
Quality Zoo runner에 전달하고, preflight/intake/API evidence/runner 결과는 별도 bounded
14일 artifact로 보존한다.

이 경계에서는 `publish`, Pages 배포, PR comment 또는 `<!-- ici-report -->` marker를 생성하지
않는다. 따라서 toy PR의 normal gate는 계속 released ici `v0.10.2`를 사용하고, released
artifact Q0 acceptance와 candidate consumer acceptance는 서로 다른 증거다. 첫 sanitizer
범위는 exact-revision remote acceptance를 완료했다. 이 feature head의 taxonomy/tool
provisioning과 후속 Qt lifetime expectation은 아직 별도 candidate acceptance가 필요하며,
이전 candidate evidence를 재사용하지 않는다.

## 5. 다중 리포터 계층 설계

모든 리포터는 동일한 `VerificationSuiteResult`를 소비하여 각 플랫폼에 최적화된 출력을 만듭니다:

리포터에 도달하기 전과 개별 reporter API 경계에서 공통 redaction copy를 만들며 원본 분석
객체는 변경하지 않습니다. message, snippet, raw output, tool argv/error, extra, finding 설명·개선안과
suppression reason·metric·파일 경로에 포함된 credential은 마스킹됩니다. credential 형태가 없는
일반 파일 경로는 탐색을 위해 그대로 유지됩니다.

1. **`RichConsoleReporter`**: 터미널 환경 최적화
   - ANSI 컬러 및 Rich 테이블/패널
   - 안전한 `file://` URI와 Rich 링크 마크업을 통한 터미널 파일 위치 원클릭 이동
2. **`HtmlReporter`**: 브라우저 독립형 대시보드
   - Zero-CDN 인라인 CSS/JS 구조
   - 기본 10개 전용 탭에 비교가 있을 때 `Baseline Delta`를 더하는 동적 구성: `Summary`, `Support & Capabilities`, `[Baseline Delta]`, `Line & Tree`, `Tests & Coverage`, `Static Types`, `Complexity`, `Clone Groups`, `Cycles`, `Security & Resources`, `Issues`
   - 계층형 디렉토리 트리 뷰 (depth별 들여쓰기 및 언어별 아이콘)
   - 원본 소스 코드 블록 인스펙터
   - baseline comparison이 있으면 source·delta count·compatibility warning·fail-on-new
     verdict를 별도 탭에 표시하고 gated delta를 먼저 보여줍니다. current/baseline 위치와
     severity transition을 포함하되, 화면은 최대 20개 row(그중 unchanged 예시는 최대 3개)로
     제한합니다.
3. **`MarkdownReporter`**: CI/CD 파이프라인 최적화
   - GitHub Step Summary 테이블 및 TEM 게이지
   - GitHub Blob 영구 링크 (`blob/<sha>/file#L10-L25`)
   - PR 인라인 에러 어노테이션 (`::error file=...::`); 별도 신뢰 job이 아티팩트로 sticky 댓글과 HTML 링크를 게시
   - baseline comparison이 있으면 동일한 source/count/gate/warning 요약과 gated-first
     delta table을 추가합니다. unchanged inventory 전체를 Markdown에 펼치지 않고 bounded
     상세만 표시합니다.
4. **`JsonReporter`**: 데이터 파이프라인 연동
   - `verify_report.json` 포맷 직렬화
   - baseline `entries`는 축약하지 않은 전체 inventory를 유지하므로 화면 reporter의
     bounded issues-first view와 데이터 파이프라인의 완전한 비교 결과를 동시에 보장합니다.

---

## 6. 새로운 검증 엔진 확장 가이드

새로운 검증 엔진을 추가하려면 다음 단계를 따릅니다:

1. `src/ici/engines/` 아래에 `BaseEngine`을 상속받는 클래스 작성:
   ```python
   from ici.core.models import EngineResult, EngineStatus
   from ici.engines.base import BaseEngine


   class SecurityEngine(BaseEngine):
       def run(self) -> EngineResult:
           cfg = self.get_config("security")
           # 검증 로직 수행...
           return self.create_result(
               name="security",
               status=EngineStatus.PASS,
               summary="Security scan clean",
               targets=[],
           )
   ```
2. `src/ici/config.py`의 `DEFAULT_CONFIG["engines"]`에 기본 정책 추가
3. `src/ici/engines/verify.py`의 `engine_defs` 목록에 등록
4. `tests/`에 단위 테스트 추가

---

> **기여 및 개발 규약**: [📜 AGENTS.md](../AGENTS.md)에서 브랜칭 전략과 커밋 규약을 확인하세요.
