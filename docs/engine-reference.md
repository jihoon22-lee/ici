# ici 검증 엔진 레퍼런스 (Engine Reference)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; **📏 검증 엔진 레퍼런스** &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 소프트웨어 공학적 품질과 보안을 보장하기 위해 14종 검증 엔진을 제공합니다.
기본 활성은 13종(`line/lint/compile_db/test/type/resource/security/cycle/complexity/sanitize/dead/dup/exception`)이며,
`cognitive`(인지 복잡도)는 `enabled = false` 기본값으로 필요 시 옵트인합니다.

---

## 1. 품질 정책 설정 (`ici.toml`)

전사 공용 정책 또는 프로젝트별 기준을 `ici.toml`을 통해 중앙에서 관리합니다.
설정 파일이 하나도 없는 최초 실행 시에는 `~/.config/ici/ici.toml`(XDG 존중)에
기본 정책 파일이 자동 생성되므로, 이를 열어 조직 기준으로 수정하면 됩니다.

### 1.0 프로젝트 레이아웃 (`[project]`)
소스 디렉토리가 `src`가 아닌 프로젝트는 `source_dirs`로 지정할 수 있습니다
(미지정 시 `src`, `lib`, `app`, `packages`, `python` 중 존재하는 디렉토리 자동 탐색):

```toml
[project]
source_dirs = ["lib"]
```

### 1.1 평가 모드 (`mode`)
각 엔진별로 결과 평가 방식을 설정할 수 있습니다:
- `"pass_warn_fail"`: FAIL 조건 시 FAIL, WARN 조건 시 WARN, 그 외 PASS (기본값)
- `"pass_fail"`: 경고(WARN)를 허용하지 않고 즉시 FAIL 처리
- `"pass_warn"`: 실패(FAIL) 없이 정보성 경고(WARN)로만 관리

모든 `[engines.<name>]` 테이블은 공통으로 `required = true|false`를 지원합니다.
생략하면 `true`이며, `false`인 엔진의 선택적 `SKIP`/`NOT_RUN`은 전체 게이트를
차단하지 않습니다. 값은 반드시 TOML boolean이어야 합니다.

```toml
[engines.line]
enabled = true
mode = "pass_warn"
warn_limit = 500
fail_limit = 1000
gate_dirs = ["src", "include", "lib", "app"]
exclude_dirs = ["docs"]

[engines.test]
enabled = true
mode = "pass_fail"
min_tem_score = 4.0
min_branch_cov = 80.0
min_func_cov = 90.0
# Use the project interpreter for pytest, coverage.py, and unittest.
# python = "/workspace/.venv/bin/python"
coverage_required = false

[engines.lint]
enabled = true
mode = "pass_warn_fail"
# Optional by default: missing Ruff is reported as WARN/ESTIMATED.
ruff_required = false

[engines.compile_db]
enabled = true
mode = "pass_warn_fail"
# true이면 C/C++ 프로젝트에서 DB 부재를 FAIL로 승격합니다.
database_required = false
required_flags = []
forbidden_flags = []

[engines.type]
enabled = true
mode = "pass_warn"
# Optional by default: missing Mypy is reported as WARN/ESTIMATED.
mypy_required = false

[engines.complexity]
enabled = true
mode = "pass_warn_fail"
warn_cc = 15
fail_cc = 25
warn_nesting = 4

[engines.dup]
enabled = true
mode = "pass_warn"
min_window = 6
warn_pct = 5.0
fail_pct = 15.0
```

이 저장소의 dogfood 정책(`ici.toml`)은 2026-08-31 세 번의 연속 self verify에서
동일하게 측정된 TEM `4.78`, Branch `77.9%`, Function `95.691%`에 변동 여유를 두어
TEM `4.5`, Branch `70%`, Function `90%`를 floor로 사용합니다. `mode = "pass_fail"`은
그 floor 미달과 테스트 실행 실패를 모두 게이트 실패로 승격합니다. 다음 ratchet은
현재 floor보다 TEM 0.10점·Branch/Function 3%p 이상 높은 측정값이 설명 가능한 상태로
세 번 연속 확인될 때 한 항목씩 적용합니다. 반복 측정의 console/duplicate 기준선은
[`docs/baselines/2026-08-31-self-quality.json`](baselines/2026-08-31-self-quality.json)에
기록합니다.

### 1.2 결과 상태와 게이트 계약

모든 엔진은 `EngineResult`를 반환하며, 결과 상태와 증거 상태를 함께 기록합니다.

- `PASS`: 정책 기준을 통과했습니다.
- `WARN`: 조치가 권장되지만 게이트를 차단하지 않는 경고입니다.
- `FAIL`: 필수 엔진의 정책 기준을 충족하지 못했습니다.
- `ERROR`: 실행 오류 또는 필수 검증을 완료하지 못한 상태입니다.
- `SKIP`: 검증이 실행되지 않은 상태입니다. 필수 엔진의 `SKIP`은 게이트에서 `ERROR`로 승격됩니다.

증거 상태(`EvidenceState`)는 다음과 같습니다.

- `MEASURED`: 도구를 실제 실행해 측정한 결과입니다.
- `ESTIMATED`: 도구를 실행할 수 없어 추정한 결과입니다.
- `NOT_RUN`: 검증이 실행되지 않았음을 명시합니다.

`EngineResult.required`의 기본값은 `true`이므로, 별도 설정이 없는 엔진은 필수 엔진으로
간주됩니다. `aggregate_suite_status()`는 빈 결과 집합, 필수 엔진의 `ERROR`/`SKIP`, 또는
필수 엔진의 `NOT_RUN` 증거를 `ERROR`로 처리합니다. 그 외에는 필수 엔진의 `FAIL`, 모든
엔진의 `WARN`, 마지막으로 `PASS` 순서로 전체 게이트 상태를 결정합니다. `required = false`인
선택 엔진의 `FAIL`/`ERROR`/`SKIP` 또는 `PASS`이지만 `MEASURED`가 아닌 결과는 게이트를
차단하지 않고 전체 suite를 `WARN`으로 낮춥니다. 따라서 선택 엔진의 실행 실패나 측정 불가가
`PASS`로 사라지지 않습니다. 도구 실행 정보가 있는 경우 `ToolEvidence`에 도구 이름·경로·버전·인자·반환 코드를 기록할 수 있습니다.
이 정책은 오케스트레이터가 결합한 결과뿐 아니라 `line`, `complexity`, `dup`을 포함한
각 엔진의 직접 `EngineResult`에도 적용됩니다.

---

### 1.3 결과 리포트 계약과 종료 코드

verify --report 및 모든 단독 엔진의 --report는 schema_version = ici.result/v3를
사용합니다. JSON에는 엔진 상태·요약·점수·실행 시간·원시 출력·extra·required·증거 상태,
검사 대상의 위치·메시지·snippet·metrics, 그리고 도구별 경로·버전·argv·반환 코드·timeout·출력
절단·오류를 포함합니다. v3는 여기에 `findings`를 추가합니다. 각 finding은 ici rule id,
category/severity/confidence, checkout 위치와 무관한 fingerprint, primary/related location,
설명·개선안, tool identity, suppression 근거와 단위가 붙은 숫자 metric을 가집니다. 기존
`targets`는 이행 기간 동안 그대로 남고 모든 legacy target은 finding adapter로도 제공됩니다.
suite 집계에는 기존 호환성을 위해 FAIL과 ERROR를 합산한
legacy failed_count와 함께 순수 ERROR 수인 error_count 및 skipped_count가 제공됩니다.
화면 리포터에서 표시하는 순수 FAIL 수는 failed_count - error_count입니다.

writer가 사용하는 JSON Schema는
[`src/ici/schemas/ici-result-v3.schema.json`](../src/ici/schemas/ici-result-v3.schema.json)에
있습니다. v2 archive는 `migrate_report_payload()`로 v3 copy를 만들 수 있고 viewer는 두 버전을
모두 읽습니다. reporter에 전달되는 모든 자유 형식 문자열은 공통 credential redaction을 거칩니다.

모든 CLI 결과의 종료 코드는 동일합니다.

- PASS/WARN: 0
- FAIL/ERROR: 1
- SKIP: 2

HTML은 외부 CDN 없이 동작하며, 파일 위치 값은 escaped data-abs-path/data-rel-path/data-line
속성으로만 전달됩니다. 리포터의 JavaScript는 정적인 이벤트 위임으로 이 값을 읽으므로
경로·메시지에 따옴표나 HTML/스크립트 문자열이 포함되어도 실행 코드로 해석되지 않습니다.

### 1.3.1 finding baseline 및 delta gate

`verify --baseline <path>`는 현재 v3 finding inventory를 프로젝트 루트 안의
`ici.result/v3` JSON baseline과 비교합니다. `--fail-on-new`를 함께 지정하면 새롭거나
회귀한 actionable finding이 하나라도 있을 때 baseline gate가 실패합니다. baseline 없이
`--fail-on-new`를 지정하는 것은 CLI 입력 오류이며, 현재 엔진의 `FAIL`/`ERROR` 결과는
baseline verdict보다 우선합니다. 현재 inventory를 저장할 때는
`--write-baseline <path>`을 사용합니다.

#### 비교 identity와 pairing

비교는 `(engine_name, fingerprint)`를 그룹 identity로 사용합니다. 같은 identity의
occurrence가 여러 개이면 fingerprint 하나로 축약하지 않고 multiset으로 유지합니다.
각 그룹 안에서는 다음 순서로 pairing합니다.

1. canonical primary location(path, line/column, label)이 정확히 같은 occurrence를 먼저
   `unchanged`로 pair합니다.
2. 남은 current/baseline occurrence를 deterministic record order로 pair하면 `moved`이며,
   current와 baseline 위치를 모두 보존합니다.
3. pair되지 않은 current surplus는 `new`, baseline surplus는 `resolved`입니다.

따라서 위치가 이동한 duplicate와 실제 추가·해결된 occurrence를 구분할 수 있습니다.
`FindingDelta`는 상태 외에도 양쪽 severity와 위치, `regressed`, `suppressed`, `gated`를
포함합니다. severity rank가 올라가거나 baseline suppression이 현재 제거되면 regression으로
분류됩니다. actionable은 현재 suppression이 없고 severity가 `info`가 아닌 finding입니다.
`new` actionable 또는 actionable regression만 `gated`이며, informational·suppressed
finding과 `resolved` 항목은 inventory에는 남지만 gate를 만들지 않습니다.

#### 호환성 identity와 입력 보안

현재와 baseline의 `AnalysisMetadata`는 다음 네 가지 identity로 호환성을 설명합니다.

- `producer_version`: 결과를 만든 ici producer 버전
- `fingerprint_version`: finding fingerprint 규칙 버전
- `policy_digest`: 엔진 분석 정책 digest
- `tool_policy_digest`: 프로젝트 scope·engine mode·도구/fallback 정책 digest

네 값 중 하나라도 다르면 비교를 폐기하지 않고 `warnings`에 차이를 남긴 채 delta를
계산합니다. metadata가 없는 기존 v3 baseline도 허용하지만 네 identity를 검증할 수 없다는
명시적 warning을 추가합니다.

loader는 baseline 경로를 프로젝트 루트에 canonical하게 resolve하고 파일 크기를 64 MiB로
제한합니다. JSON parse와 `ici.result/v3` schema version 및 baseline 관련 필드를 요구하며,
각 finding의 primary 및
related location이 canonical POSIX project-relative path이고 1-indexed line/column region인지
검증합니다. 절대 경로, `..` root 탈출, backslash/non-canonical alias, root 밖으로 향하는
symlink, 잘못된 fingerprint digest·metadata·suppression 값은 거부합니다.

v3 결과에서 `analysis_metadata`와 `baseline_comparison`은 optional nullable field입니다.
따라서 두 필드가 없는 기존 v3 archive도 기존 소비자와 viewer가 계속 읽을 수 있습니다.
v2 archive는 migration helper로 v3 copy를 만들 수 있지만 baseline loader의 직접 입력은
v3입니다. JSON writer는 `baseline_comparison.entries` 전체 inventory를 저장합니다.
반면 console/Markdown/HTML reporter는 issues-first로 gated·변경 항목을 먼저 보여주고,
화면 상세는 최대 20개 row(unchanged 예시는 최대 3개)로 제한해 unchanged noise를 줄입니다.

`--write-baseline`의 writer는 output과 같은 디렉터리에 고유한
`.{name}.<random>.tmp` 파일을 만들고 내용을 flush·`fsync`한 뒤 `Path.replace`로 원자
교체합니다. 쓰기나 교체가 실패하면 임시 파일을 정리하여 기존 baseline이 부분 결과로
덮어써지지 않습니다. 입력과 출력이 같은 갱신에서 `fail-on-new` gate가 실패하면 원본을
그대로 보존하며, 실패 snapshot이 필요하면 다른 출력 경로를 사용해야 합니다.

### 1.4 엔진 지원·기능 매트릭스

아래 표는 설명용으로 손으로 관리하지 않습니다. `ici.core.support`의 실행 가능한 선언에서
생성하며 테스트가 이 블록과 선언의 완전한 일치를 확인합니다. `Qt` 표시는 별도 언어나
Qt 의미 분석을 뜻하지 않고, 해당 C++ 경로가 Qt 프로젝트 소스를 포함하거나 CMake/qmake
어댑터로 Qt 테스트를 실행할 수 있다는 호환성 표기입니다. 실제 프로젝트 보고서는 발견된
언어·프레임워크와 활성 정책을 적용해 각 행의 `applicable`, `enabled`, `active_mode`,
`evidence`, `confidence`, 필수·선택 도구, fallback과 현재 한계를 함께 기록합니다.

<!-- ici:support-matrix:start -->
| Engine | Python | C++ / Qt |
|---|---|---|
| `line` | exact | exact (Qt) |
| `lint` | tool-backed → heuristic fallback | tool-backed (Qt) → heuristic fallback |
| `compile_db` | unsupported | exact (Qt) → heuristic fallback |
| `test` | tool-backed → heuristic fallback | tool-backed (Qt) → heuristic fallback |
| `type` | tool-backed → heuristic fallback | unsupported |
| `cognitive` | heuristic | unsupported |
| `resource` | heuristic | unsupported |
| `security` | heuristic | unsupported |
| `cycle` | heuristic | tool-backed (Qt) → heuristic fallback |
| `complexity` | heuristic | heuristic (Qt) |
| `sanitize` | tool-backed | tool-backed (Qt) |
| `dead` | heuristic | unsupported |
| `dup` | heuristic | heuristic (Qt) |
| `exception` | heuristic | heuristic (Qt) |
<!-- ici:support-matrix:end -->

표의 mode는 다음 뜻입니다.

- `exact`: 소스 텍스트의 결정적 값을 계산합니다. 의미적 정확성을 넓게 주장하지 않습니다.
- `tool-backed`: 외부 compiler/test/lint/type 도구의 실제 실행 증거를 사용합니다.
- `heuristic`: AST, 경량 parser, token 또는 pattern 기반으로 한계를 명시하는 분석입니다.
- `unsupported`: 현재 그 언어를 분석하지 않습니다. 언어가 없거나 지원하지 않는 행은
  `NOT_APPLICABLE`, 대상이 있지만 실행하지 못한 행은 `NOT_RUN`, 제한된 fallback만 수행한
  행은 `ESTIMATED`로 남으므로 미지원 범위가 PASS로 보이지 않습니다.

## 2. 검증 엔진 상세

### 2.1 📏 `line` (코드 라인 및 파일 크기 분석기)
- **검증 규칙**:
  - 파일당 순수 코드(코드 라인) 기준으로 크기 과대화 진단
  - `warn_limit` (기본 500줄): 모듈 분리 검토 권고
  - `fail_limit` (기본 1000줄): 단일 파일 과대화 실패
- **소스 전용 스캔**: 통계 집계와 임계값 검증 모두 기본적으로 소스 디렉터리
  (`src`, `include`, `lib`, `app` + 설정된 `project.source_dirs`)만 대상으로 합니다.
  `tests`/`docs`/`scripts`는 기본 제외되며 Top 파일 목록·Total Volume·게이트 어디에도
  나타나지 않습니다. `include_dirs`(스캔 범위 추가, 예: 테스트 코드까지 집계)와
  `exclude_dirs`(제외)로 조직 정책 조절 가능. 임계값 판정은 `gate_dirs`에 속한
  경로에서만 수행되며, `gate_dirs`를 기본값에서 명시적으로 좁히지 않는 한
  `project.source_dirs`도 자동으로 게이트에 포함됩니다.
- **출력 메트릭**: 코드 라인, 주석 라인, 공백 라인 수 및 디렉토리 계층 트리 ([HTML 뷰어 지원](user-guide.md#22-인터랙티브-html-리포트-생성-및-자동-브라우저-열기))

### 2.2 🧹 `lint` (문법 및 코드 스타일 린터)
- **Python**: `ruff check --output-format=json`을 실행하고, 로컬 `ruff format --help` capability
  probe에서 `--output-format` 지원을 확인하면 `ruff format --check --output-format=json`을
  실행합니다. Ruff 0.15 legacy `format --check`는 plain `N file(s) would be reformatted`
  summary와 mixed `..., M file(s) already formatted` suffix를 모두 허용합니다. 두 count의
  singular/plural grammar와 `N` 및 `Would reformat:` 경로 수를 엄격히 검증하며, malformed·unknown·
  misordered output은 부분 `Format:Style` target을 남기지 않고 원자적으로 거부합니다. `warning:`으로
  시작하는 Ruff의 한 줄 또는 들여쓴 다중 줄 warning block은
  `tool_warnings`로 보존하며 유효한 결과를 `ERROR`로 승격하지 않습니다. 그 외 stderr,
  malformed JSON, timeout·절단·비정상 종료·spawn 실패는 계속 도구 오류로 처리합니다.
  JSON formatter의 빈 배열 성공과 `unformatted` 위치 진단은 엄격히 검증하며 도구 오류와
  실제 진단을 구분합니다. Ruff가 없으면 AST 문법 검사만 수행하는 부분 폴백으로 전환하며,
  `ruff_required = true`이면 `ERROR`/`NOT_RUN`, 기본 선택 정책이면 `WARN`/`ESTIMATED`로 기록합니다.
  Ruff는 PATH에 직접 실행 가능한 파일 또는 프로젝트 `.venv/bin`/`.venv/Scripts`의 실행 파일만
  사용합니다. `uvx`/`uv run`을 도구 설치로 간주하거나 패키지 해석을 시도하지 않으므로 폐쇄망에서
  실행 시 부작용이 없습니다. `ruff format --check`가 종료 코드 0과 빈 stdout/stderr를 반환하는
  것도 정상 성공으로 인정합니다.
- **C++ (정확한 compilation context)**: compilation database/context가 있으면 각 production
  translation unit의 각 configuration에 저장된 normalized command를 사용합니다. `CapabilityInventory`가
  확인한 실행 가능한 직접 GCC/Clang driver만 허용하고, source·working directory 경계도 다시
  검사합니다. replay adapter는 `-c`·출력·dependency 생성 옵션은 제거하고, positive allowlist
  밖의 option과 plugin/wrapper/toolchain 주입 등 unsafe option은 fail-closed로 거부한 뒤
  controlled syntax operation을 붙여 실행합니다. inherited override를 배제한 minimal
  replacement environment와 closed stdin을 사용합니다. 위치가 있는 `error`/`warning`/`note:`
  진단과 PASS target은 원래 파일·라인에 보존하며, error-level context/unit diagnostic만
  `ERROR`/`NOT_RUN`으로 닫습니다. warning-level diagnostic은 위치 있는 `WARN`/`MEASURED`로
  남기고 replay를 계속하며, compilation context가 존재하는 동안 고정 `g++ -std=c++17`
  폴백은 사용하지 않습니다. C++ 엔진 cache dependency에는 `ici.core._cpp_replay_policy`가,
  cycle에는 `ici.engines._cpp_include_trace`가 명시적으로 포함됩니다.
- **C++ (DB 부재 폴백)**: compilation context가 실제로 없을 때만 `g++`를 찾아
  `-fsyntax-only -std=c++17 -Wall -Wextra` 휴리스틱 명령을 실행하고 `ESTIMATED`로 표시합니다.
  ready capability의 compiler를 우선하며 standalone driver도 canonical/project 경계를 확인합니다.
  이 command 역시 exact replay와 같은 positive allowlist, argument bound, minimal replacement
  environment와 closed stdin을 사용하므로 unsafe package/include flag는 실행 전에 거부됩니다.
  g++ 부재, timeout·출력 절단·spawn/비정상 종료·malformed 또는 진단 없는 비정상 출력은
  fail-closed `ERROR`/`NOT_RUN`으로 남깁니다. 모든 Ruff/compiler 시도는
  `ToolEvidence`에 argv, 반환 코드와 timeout/절단/실패 사유를 기록합니다.

### 2.3 🧪 `test` & TEM 스코어링 (단위 테스트 및 테스트 효과성 지표)
- **동작**: 프로젝트 내 pytest 또는 C++ 테스트 바이너리를 실행하여 단위 테스트 전수 통과 여부 검증
- **C++ 경로**: 프로젝트 **루트**에 `CMakeLists.txt`나 `*.pro`가 있으면 그 빌드 시스템에 위임하고
  (CTest / `make check`), 없으면 모든 소스를 `g++`로 직접 컴파일·링크합니다. 어댑터 경로에서는
  `project.cpp_external_build_dirs`와 `-std=c++17` 고정이 적용되지 않고, `Q_OBJECT` 클래스를
  단위 테스트할 수 있습니다. 두 경로의 테스트 카운트 단위가 다르다는 점을 포함해 자세한 것은
  [`user-guide.md` §2.5](user-guide.md)를 봅니다.
- **Python 실행기**: `[engines.test].python`이 지정되면 해당 인터프리터를 우선 사용하고,
  없으면 프로젝트 `.venv`의 Python, 마지막으로 `sys.executable` 순서로 선택합니다. pytest,
  coverage.py, unittest는 모두 이 동일한 인터프리터의 `-m` 모듈 호출로 실행하며 PATH에 있는
  `pytest`/`coverage` 스크립트를 섞어 사용하지 않습니다.
- **테스트 수집 증거**: pytest 종료 코드 `5` 또는 수집된 테스트가 0개이면 `total_tests = 0`인
  `FAIL`입니다. 인터프리터/도구 부재, timeout, 출력 절단, 도구 자체 오류는 `ERROR`/`NOT_RUN`으로
  기록되어 추정치로 통과할 수 없습니다. 소스·테스트 대상이 전혀 없는 빈 프로젝트도 일반
  0개 테스트 `FAIL` 대상으로 기록되며, 선택적 커버리지는 `ESTIMATED`, 필수 커버리지는
  `ERROR`/`NOT_RUN`입니다. pytest가 `returncode = 0`을 반환하더라도 collection 줄만 있고
  통과/실패 per-test 또는 terminal summary가 없으면 실행 증거 부족으로 `ERROR`/`NOT_RUN`입니다.
- **언어별 실행 범위**: `python`/`cpp`/`hybrid` 소스가 있으면 해당 언어의 테스트 시도를 기록합니다.
  `hybrid`에서 한 언어의 테스트만 있어도 다른 언어의 0개 수집을 별도 `FAIL`로 표시하며, pytest가
  실제로 0개를 수집한 뒤 unittest로 우회하지 않습니다. pytest 모듈 자체가 명확히 없는 경우에만
  동일 인터프리터의 `-m unittest discover`로 대체합니다.
- **커버리지 정책**: `coverage_required = true`이면 Python `coverage.json` 또는 C++ `gcov`
  산출물이 실제로 생성·파싱되어야 하며, 누락·불완전·잘못된 출력은 `ERROR`/`NOT_RUN`입니다.
  `false`이면 커버리지 수치는 `ESTIMATED`로 표시되고 결과는 최소 `WARN`이며, 추정치는 TEM·
  커버리지 임계값의 `PASS` 근거로 사용되지 않습니다.
- **실측 JSON 검증**: Python JSON은 파일/전체 statement·branch 수의 일관성, 양의 정수인
  `executed_lines`/`missing_lines` 배열의 개수·중복·교집합을 검증합니다. 0 statement 또는
  측정 불가능한 산출물은 실측으로 인정하지 않으며, coverage `--version`의 timeout·절단·spawn·
  signal·기타 오류도 선택적 누락이 아닌 `ERROR`/`NOT_RUN`입니다.
- **모듈별 실측 커버리지 수집** (HTML `🧪 Tests & Coverage` 탭의 Module Coverage Table):
  - **Python**: 프로젝트 환경의 `coverage.py`가 있으면 `coverage run --branch`로 테스트를 재실행하고 `coverage json`을 파싱하여 파일별 Stmts/Miss/Cover/Branch 실측값 수집
  - **C++**: `gcov`가 있으면 `g++ --coverage` 2단계 컴파일(객체별 `-c` 후 링크)로 테스트를 빌드·실행하고 `gcov -b -p` 산출물을 파싱하여 동일한 파일별 테이블 생성 (테스트 파일 제외)
  - 둘 다 없으면 KPI는 추정치로 표시되며, HTML에 설치 안내가 노출됨
- **pytest 임시 파일**: pytest에는 프로젝트 내부 `--basetemp`를 강제로 지정하지 않으므로
  `tmp_path`와 캡처 파일은 시스템 임시 디렉토리를 사용합니다. WSL에서는 Windows 마운트의
  `TMP`/`TEMP` 대신 `/tmp`를 자식 테스트 프로세스에 전달합니다.
- **Function Coverage (gcov 호출 기준) 실측**: 함수 본문이 한 번 이상 실행되면 커버된 것으로 간주
  - Python: coverage.json의 `executed_lines`와 AST 함수 라인 범위를 교차 계산
  - C++: gcov 산출물의 `function ... called N` 라인 파싱
  - HTML `🧪 Tests & Coverage` 탭에 함수별 테이블(Function Coverage Table)로 상세 표시
  - 측정 불가 시에만 기존 추정치(통과 95%/실패 50%)로 폴백
- **TEM (Test Effectiveness Metric) 5.0 만점 산출 공식**:
  - **Line Coverage 측정 가능 시 (기본)**:
    $$\text{TEM} = \frac{\min(80, \text{Line Coverage})}{80} \times \frac{\text{Function Coverage}}{100} \times \text{Pass Rate} \times 5.0$$
  - **Branch Coverage만 측정 가능한 경우** (Branch에 $\times \frac{5}{4}$ 보정 후 동일 공식 적용):
    $$\text{TEM} = \frac{\min(80, \text{Branch} \times 1.25)}{80} \times \frac{\text{Function Coverage}}{100} \times \text{Pass Rate} \times 5.0$$
- **평가 기준**:
  - 모든 테스트 케이스 통과 필수
  - 기본 내장 정책은 TEM 스코어 $\ge 4.0$, Branch Coverage $\ge 80\%$, Function Coverage $\ge 90\%$를 요구
  - 이 저장소 dogfood 정책은 2026-08-31 기준 TEM $\ge 4.5$, Branch $\ge 70\%$, Function $\ge 90\%$ floor를 사용하며, 세 번 연속 실측과 변동 여유를 둔 ratchet 조건은 위 정책 설명과 기준선 파일에 기록
  - 실측 커버리지가 있는 경우 Branch Coverage는 실측값(coverage.py/gcov)으로 대체됨
  - 커버리지 80% 미만 모듈은 `Coverage:Module` WARN 타깃으로 Issues 탭/PR 어노테이션에 노출

### 2.4 🏷️ `type` (정적 타입 안정성 검사기)
- **Python**: `mypy`의 정상 성공 문법과 위치가 있는 `error`/`note` 진단을 엄격히 파싱합니다.
  종료 코드 `1`의 유효한 진단은 실제 타입 발견 사항으로 `mode` 정책을 따르며, 종료 코드 `2`
  이상·timeout·출력 절단·spawn/신호 종료·잘못된 성공/진단 출력은 진단 문구가 포함되어도
  도구 `ERROR`/`NOT_RUN`입니다. `mypy_required = true`에서 미설치 도구는 `ERROR`/`NOT_RUN`이고,
  기본 선택 정책에서는 AST 함수 어노테이션 폴백을 `WARN`/`ESTIMATED`로 표시합니다. Mypy는
  공유 capability가 선택한 프로젝트 `.venv` 또는 현재 interpreter의 `python -m mypy`를
  우선 재사용하고, 독립 엔진 실행에서는 PATH나 프로젝트 `.venv/bin`/`.venv/Scripts`의 직접
  실행 파일을 사용합니다. `uv`/`uvx`를 통한 설치나 네트워크 패키지 해석은 시도하지 않습니다.
  hybrid 프로젝트에서는 발견된 Python 파일을 실제로 포함하는 source root만 mypy argv에
  전달하므로 C++ 전용 `src`/`include` 경로가 도구 오류를 만들지 않습니다. 적용할 Python
  소스가 0개이면 Mypy를 실행하지 않고 명시적 `SKIP` 대상과 `WARN`/`ESTIMATED`를 남깁니다.
  따라서 `Success: no issues found in 0 source files`도 실측 성공 문법으로 인정하지 않습니다.
- **C++**: 현재 C++ 타입 검증은 구현되어 있지 않습니다. C++ 소스가 발견되면 소스별 `SKIP`
  대상을 남기고 요약에 미구현 범위를 명시하며 `WARN`/`ESTIMATED`로 기록합니다. `type = "cpp"`
  로 선언했지만 적용 가능한 C/C++ 소스가 0개인 경우에도 프로젝트 범위를 검증하지 않았다는
  명시적 `SKIP` 대상을 남기고 `WARN`/`ESTIMATED`로 표시합니다. 반대로 `hybrid` 프로젝트는
  실제로 존재하는 언어 범위만 대상으로 삼아, Python만 있는 경우 C++ 부재를 추가 skip 경고로
  만들지 않습니다. Python/C++ 혼합 프로젝트도 C++ 검증 누락 때문에 전체 증거를 `MEASURED`로
  승격하지 않습니다.
- 모든 Mypy 시도 및 미설치 상태는 `ToolEvidence`로 기록되며, rc>=2·파싱 실패 등 최종 도구
  오류 원인도 `error`에 보존됩니다. C++ skip은 Missing Annotations가 아닌 일반 type finding/
  warning 요약으로 표시됩니다.
- **노이즈 제로**: 오류가 없을 경우 개별 함수 통과 로그를 숨기고 `✅ Static Type Check Passed (0 Errors)` 단일 행으로 축약 요약

### 2.5 🧩 `complexity` (순환 복잡도 및 블록 중첩도)
- **Cyclomatic Complexity (CC)**: 조건문(`if`, `while`, `for`, `match`(케이스 guard 포함), 삼항 연산자, comprehension `if`, `and`/`or`)에 따른 선형 독립 경로 수 계산
  - $\text{CC} \le 15$: 양호 (PASS)
  - $15 < \text{CC} \le 25$: 주의 및 리팩토링 권고 (WARN)
  - $\text{CC} > 25$: 과도한 복잡도 (FAIL)
- **Max Nesting Depth**: 블록 중첩 깊이 $\ge 4$ 초과 시 경고
- **코드 스니펫**: 고복잡도 함수의 실제 원본 소스 코드를 추출하여 HTML 리포트에 즉시 표시

### 2.6 🛡️ `sanitize` (메모리 안전성 및 리소스 누수 진단)
- **C++**: AddressSanitizer(`-fsanitize=address`) 및 UndefinedBehaviorSanitizer(`-fsanitize=undefined`)를 임시 프로젝트 외부 산출물로 빌드·실행한다. 컴파일/실행 도구 오류는 `ERROR`이며, timeout·출력 절단도 `ERROR`/`NOT_RUN`이다. 음수 signal 종료라도 완전한 ASan/UBSan 진단이 있으면 `FAIL`/`MEASURED`로 보존하고, 진단 없는 signal 종료는 `ERROR`로 처리한다.
- **Python**: Task 5가 선택한 동일 인터프리터로 `-W error::ResourceWarning -m pytest -o addopts= tests`를 실행해 리소스 경고를 측정한다. `test_*.py`와 `*_test.py`를 모두 대상으로 하며, 0개 실행 테스트(전부 skipped/deselected)·pytest 부재·timeout·출력 절단·실행 실패·잘못된 성공은 통과로 간주하지 않는다. 기존 `PYTHONPATH`와 WSL 임시 디렉터리 정책도 보존한다.
- **진단 판정**: 출력에 sanitizer 이름만 언급된 경우는 결함으로 판정하지 않는다. 위치가 있는 UBSan `runtime error` 또는 ASan/LSan/UBSan의 `ERROR`/`SUMMARY` 서명만 실제 진단으로 인정한다.
- **적용 범위**: Python/C++ hybrid에서 한 언어의 scope가 건너뛰면 결과는 `WARN`/`ESTIMATED`이며, 대상 자체가 없으면 명시적 `SKIP`이다. C++ 테스트 파일은 project 경계 안의 실제 파일만 선택하며 외부 symlink는 제외한다. ResourceWarning의 Windows drive/공백 경로도 원본 파일과 라인 위치를 보존한다. 실행 시 기존 `ASAN_OPTIONS`/`UBSAN_OPTIONS`를 보존하면서 leak 검출과 UBSan 중단 옵션을 추가한다.

### 2.7 💀 `dead` (죽은 코드 및 미사용 심볼)
- 도달할 수 없는 블록과 private module-level Python 함수의 실제 `Name`/호출 및 cross-module `from`/attribute 참조를 분석한다.
- `import pkg.a; pkg.a._foo()` 및 `from pkg import a; a._foo()`처럼 중첩 모듈을 거치는 참조는 실제 정의 모듈에만 연결하며, 같은 이름의 무관한 함수는 별도 경고로 남긴다.
- package `__init__.py`의 `.a`/`from . import a` 상대 import도 package-qualified 모듈로 해석한다. `project.source_dirs`가 충돌하면 앞선 source directory의 모듈 정의만 선택하며, 동일 alias의 여러 lexical import 후보는 누락 없이 보수적으로 연결한다.
- `if`/loop/`try`의 `else`·`finally` 및 `match` case를 포함한 모든 statement-list에서 terminal statement 뒤의 코드를 검사한다.
- decorator 등록 함수, `__all__` export, class method, nested callback function은 합리적인 false positive를 피하기 위해 제외하며, 분석된 정상 source 위치도 `PASS` target으로 보존한다.

### 2.8 📦 `dup` (코드 복제 및 중복률 감지기)
- **알고리즘**: 토큰 정규화(식별자/리터럴 치환) 슬라이딩 윈도우 해싱 + **최대 클론 병합**
  - **Type-2 클론 검출**: 변수명/리터럴만 다른 복사-붙여넣기도 동일 구조로 인식
  - 교차 파일은 갭 허용 블록 매칭(`SequenceMatcher`), 동일 파일은 비중첩 그리디 확장
  - 중복 라인 집계는 고유 라인 위치 합집합 기준 (과대 집계 방지)
  - 원본 인덴트와 줄바꿈을 완벽히 보존한 소스 코드 프리뷰 제공
- **평가 기준**: 전체 코드베이스 대비 중복 라인 비율이 `warn_pct`(5%) 초과 시 경고, `fail_pct`(15%) 초과 시 실패

### 2.9 ⚠️ `exception` (예외 처리 안전성 검출기)
- `except: pass` (예외 무시/삼킴 패턴) 검출
- `except BaseException:`, tuple 안의 `BaseException`, `builtins.BaseException`을 모두 위치와 함께 `FAIL` target으로 차단
- `builtins`는 명시적으로 import된 alias만 module attribute 경로로 인정한다. `del BaseException` 뒤에는 builtin 이름으로 복귀하지만 삭제된 `BE` alias는 외부 alias로 재해석하지 않는다.
- 함수 lexical local, module/class 실행 순서, handler의 transient binding을 구분하며, BoolOp·IfExp·match capture·복수 `with` context의 조건부 binding은 기존 alias 가능성을 보존하는 보수적 정책을 사용한다.
- Python `except ... as exc` 내부의 암묵적 `raise exc` lost traceback 감지 (`raise`와 `raise exc from cause`, 중첩 함수 scope는 구분)
- 함수·중첩 함수의 enclosing scope alias는 child 정의 시점의 binding과 그 이후 가능한 alias 이벤트를 호출 graph 없이 path-insensitive하게 함께 고려한다. 정의 전에 안정적으로 shadow되고 이후 alias 가능성이 없는 경우에만 억제하며, class body는 정의 시점 cutoff를 적용한다.
- C++ 소멸자(`destructor`) 내부 throw와 구문상 비어 있는(syntactically empty) `catch(...)` 감지. 주석·일반/Raw 문자열을 분석에서 제외하고 multiline body도 위치와 함께 보존한다. 표준 raw-string prefix와 `//` line-splice 주석도 마스킹한다. 소멸자 선언이 `;`로 끝나면 뒤의 함수 body를 소멸자로 오인하지 않으며, 빈 catch 계산은 파일별 한 번만 수행한다.


### 2.10 🔄 `cycle` (순환 참조 탐지)
- **동작**: Python `import` 그래프와 C++ include 그래프를 각각 구축해 반복(iterative) Tarjan
  SCC로 순환을 탐지합니다 (재귀 구현이 아니므로 수천 노드짜리 체인에서도 재귀 한도 초과로
  죽지 않습니다). C++ production context가 있으면 각 configuration의 normalized direct
  GCC/Clang command를 안전하게 replay하고 `-E -H` compiler trace에서 active resolved edge를
  수집합니다. edge는 `project`/`generated`/`system`/`third_party` scope로 집계됩니다. 각
  configuration graph를 독립적으로 SCC 분석하고 동일 cycle component만 중복 제거하며,
  configuration 간 edge는 union하지 않습니다. 동일 component가 여러 configuration에서
  발견되면 configuration 목록은 report metadata로만 보존됩니다. 실제 cycle path는 SCC 멤버를
  임의 정렬하지 않고 간선을 따라갑니다.
- **정확한 trace의 진단과 실패**: active compiler missing-include 진단은 include 위치를 가진
  `CppIncludeUnresolved` `WARN`으로 보존하고 해당 edge는 만들지 않습니다. malformed/truncated/
  timed-out trace, 검증할 수 없는 nonzero 종료, spawn/replay 실패는 fail-closed `ERROR`/
  `NOT_RUN`입니다. context가 존재하면 suffix fallback으로 전환하지 않습니다.
- **DB 부재 폴백과 한계**: compilation context가 실제로 없을 때만 C++는 프로젝트 파일의
  **유일한 전체 path suffix** 휴리스틱을 사용하고 `ESTIMATED`로 기록합니다. bare `util.h`에
  여러 후보가 있거나 프로젝트 안에 후보가 없으면 간선을 추측하지 않고
  `CppIncludeAmbiguous`/`CppIncludeUnresolved` 타깃에 include 위치와 후보를 남깁니다.
- **설정**: `[engines.cycle] enabled=true, mode="pass_warn_fail", max_reported=20, required=false`.
  다른 신규 휴리스틱 엔진과 마찬가지로 기본 `required=false`이므로, 엔진 `ERROR`가 나도
  전체 게이트를 막지 않습니다.
- **대상**: `src` 등 소스 디렉터리 내 파일만 대상.

### 2.11 🧠 `cognitive` (인지 복잡도)
- **동작**: SonarQube S3776 스타일 인지 복잡도를 함수별로 계산. `if/for/while/except/with`는 중첩 깊이에 따라 가중치를 더하고, `and/or` 체인, `comprehension`, `assert` 등을 반영.
- **설정**: `[engines.cognitive] enabled=false, mode="pass_warn", warn=30, fail=60, warn_nesting=4`
  (기본 비활성 — 옵트인). `pass_warn` 모드이므로 개별 함수가 `fail` 임계값을 넘어도 엔진
  전체 상태는 `WARN`까지만 올라가고 `FAIL`로 게이트를 막지 않습니다.
- **대상**: Python 소스만 분석, `__dunder__` 함수는 제외.

### 2.12 🔒 `security` (보안 위생)
- **동작**: 하드코딩 시크릿(`password|secret` 등), 프라이빗 키 블록, `hashlib.md5/sha1`, `random` 약한 난수, `eval/exec`, `pickle.loads`, `shell=True` 등을 정규식으로 탐지. `# nosec` 주석과 전체가 주석인 줄은 억제됩니다.
- **시크릿 마스킹**: `HardcodedSecret`/`PrivateKey` 발견 사항은 실제 시크릿 값을
  `***REDACTED***`로 치환한 뒤 리포트(콘솔/HTML/JSON/gh-pages 게시본)에 기록합니다 —
  시크릿 스캐너가 찾아낸 시크릿 원문 자체를 노출하지 않기 위함입니다.
- **설정**: `[engines.security] enabled=true, mode="pass_warn", scan_tests=false`. `scan_tests=true`면
  `project.source_dirs`와 별개로 프로젝트 최상위 `tests/` 디렉터리도 함께 스캔합니다.
- **대상**: Python 소스만, `tests/`는 기본 제외.

### 2.13 🧹 `resource` (리소스 누수)
- **동작**: `open()`이 `with` 밖에서 사용되거나 `close()` 없이 방치된 경우, `List/Dict/Set` 가변 기본 인자 등을 AST로 탐지.
- **설정**: `[engines.resource] enabled=true, mode="pass_warn"`.
- **대상**: Python 소스만 분석.

### 2.14 🧭 `compile_db` (컴파일 맥락·translation unit 검증)
- **동작**: root 또는 `build/compile_commands.json`을 안전하게 읽어 production C/C++
  translation unit이 빠짐없이 포함됐는지 확인합니다. 동일 source의 debug/release 등 여러
  configuration을 보존하고 compiler, language/standard, define, include/search path,
  sysroot와 output을 구조화합니다.
- **입력 안전성**: `arguments`가 `command`보다 우선합니다. command와 project-contained
  `@response` file은 플랫폼별 tokenizer로만 분해하며 shell/compiler를 실행하지 않습니다.
  database/response file은 크기·개수·깊이가 제한되고 symlink/path escape, duplicate JSON
  key, 읽기 중 변경, malformed row는 위치가 있는 진단으로 보고합니다.
- **정책**: `[engines.compile_db]`의 `database_required`, `required_flags`,
  `forbidden_flags`를 사용합니다. 자동 탐색 DB 부재는 기본 WARN이며
  `database_required = true`이면 FAIL입니다. 명시한 DB의 malformed/missing 상태,
  production TU 누락, stale source, 누락 include/working directory, 검사할 수 없는 response
  file은 FAIL입니다.
- **대상**: C/C++/Qt production translation unit. Python-only 프로젝트에서는
  `SKIP`/`NOT_APPLICABLE`입니다.
- **CMake preflight**: 명시적 또는 자동 발견 DB가 없고 root backend가 CMake인 C/C++ 프로젝트는
  `build/ici-cmake-build`에서 Release canonical configure를 수행합니다. configure에는
  `CMAKE_EXPORT_COMPILE_COMMANDS=ON`과 `CMAKE_UNITY_BUILD=OFF`가 포함됩니다. `Ninja`와
  `*Makefiles` single-config generator만 exact context로 허용하고, multi-config/unknown
  generator와 export·unity metadata 문제는 diagnostic으로 남깁니다.
- **generated source와 metadata**: canonical DB 첫 load에서 shadow 내부 generated source가
  stale이면 full build를 한 번 수행한 뒤 DB를 reload합니다. `CMakeCache.txt`는 최대 4 MiB의
  no-follow bounded read입니다. context/report/cache에는 `origin`, generator, unity 상태,
  DB digest 및 `CMakeFiles/<target>.dir`에서 도출한 target이 포함됩니다. CMake subdirectory의
  output이 DB parent 기준으로 기록된 경우에도 entry directory 기준 명령과 같은 canonical
  파일을 가리킬 때만 보정합니다.

---

> **다음 단계**
