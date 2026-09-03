# ici 검증 엔진 레퍼런스 (Engine Reference)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; **📏 검증 엔진 레퍼런스** &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 소프트웨어 공학적 품질과 보안을 보장하기 위해 15종 검증 엔진을 제공합니다.
`standard` profile은 13종(`line/lint/compile_db/test/type/resource/security/cycle/complexity/sanitize/dead/dup/exception`)을
선택하고, `deep` profile은 여기에 `cognitive`와 C++ 전용 `thread_sanitize`를 더합니다.
`thread_sanitize`는 deep-only이며 Python에서는 unsupported입니다.

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

[engines.sanitize]
enabled = true
mode = "pass_fail"
# Set required = false when missing applicable sanitizer tests are optional.
# required = false

[engines.thread_sanitize]
enabled = true
mode = "pass_fail"
# Deep profile only; this engine applies TSan to C++/Qt test scopes.
# required = false

[engines.lint]
enabled = true
mode = "pass_warn_fail"
# Optional by default: missing Ruff is reported as WARN/ESTIMATED.
ruff_required = false
# C++ clang-tidy policy: auto (optional), required (gate), or off (disabled).
clang_tidy = "auto"
# Each item is one check glob; ici joins the list with commas for --checks.
clang_tidy_checks = ["-*", "bugprone-*", "performance-*"]
# Optional project-relative clang-tidy configuration file.
# clang_tidy_config = "config/.clang-tidy"
# Qt clazy policy: standalone/wrapper auto, required, or off.
clazy = "auto"
# Explicit Clazy level; independent of ici.profile.
clazy_profile = "level0"
# Optional explicit level2/manual noisy checks; takes precedence over profile.
# clazy_checks = ["qdatetime-utc", "qcolor-from-literal"]

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
# "project" preserves Mypy's discovered project config. "ici" is an explicit
# overlay that also checks untyped bodies, redundant casts, and unused ignores.
mypy_profile = "project"  # project | ici

[engines.complexity]
enabled = true
mode = "pass_warn_fail"
warn_cc = 15
fail_cc = 25
warn_nesting = 4
cpp_boundaries = "auto"  # auto | required | off

[engines.dead]
cpp_unused = "auto"         # auto | required | off (C++ compiler probe)
cpp_linker = "off"          # auto | required | off (Linux GNU ELF CMake section-GC)
include_generated = false  # generated/autogen/moc 입력을 포함하려면 true
include_vendor = false     # vendor/dependency 입력을 포함하려면 true

[engines.dup]
enabled = true
mode = "pass_warn"
min_window = 6
warn_pct = 5.0
fail_pct = 15.0
python_semantic = "auto"   # auto | required | off (bounded Python 3.10 AST-shape clones)
include_generated = false
include_vendor = false
```

`dead`와 `dup`는 같은 bounded source intake 정책을 공유합니다. 생성물·moc와 vendor/dependency
경로는 기본적으로 소유한 분석 범위에서 제외하며, 프로젝트별로 필요한 경우 엔진마다 독립적으로
opt-in할 수 있습니다. `include_generated`와 `include_vendor`는 각각 boolean입니다.

선택된 입력은 먼저 lexical normalization/deduplication 뒤 deterministic lexical sorting을
거치며, 최대 8,192개의 unique candidate path와 정책 적용 후 2,048개의 owned/analyzed source
file, 파일당 8 MiB, 전체 64 MiB의 UTF-8 source bytes를 넘을 수 없습니다. generated/vendor로
제외된 파일은 owned cap을 소비하지 않습니다. 프로젝트 경계 이탈·symlink·missing/unsafe
read·지원하지 않는 확장자·invalid UTF-8/NUL·상한 초과는 조용히 건너뛰지 않고 위치가 있는
`ERROR`/`NOT_RUN`으로 닫습니다. 겹치는 generated/vendor path는 두 opt-in이 모두 literal
`true`여야 포함되며, exclusion file count는 unique path 기준입니다. no-dirfd fallback은
component symlink precheck 후 descriptor identity와 second-read content stability를 확인하고,
직접 주입되는 limit이 positive integer가 아니면 fail-closed합니다.

위의 source-intake bound와 별도로 `dup` lexer/index/matcher에는 내부 token·normalized-character·
comparison budget이 적용됩니다. 이 resource bound는 사용자 설정으로 노출하지 않으며, 초과 시
partial clone 결과나 PASS를 남기지 않고 위치가 있는 `ERROR`/`NOT_RUN`으로 닫습니다. 정상적으로
실행된 duplicate 분석도 compiler/linker 실측이 아니므로 §2.8의 `ESTIMATED`/heuristic evidence
계약을 따릅니다.

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
| `complexity` | heuristic | tool-backed (Qt) → heuristic fallback |
| `sanitize` | tool-backed | tool-backed (Qt) |
| `thread_sanitize` | unsupported | tool-backed (Qt) |
| `dead` | heuristic | tool-backed (Qt) |
| `dup` | heuristic | heuristic (Qt) |
| `exception` | heuristic | heuristic (Qt) |
<!-- ici:support-matrix:end -->

표의 mode는 다음 뜻입니다.

- `exact`: 소스 텍스트의 결정적 값을 계산합니다. 의미적 정확성을 넓게 주장하지 않습니다.
- `tool-backed`: 외부 compiler/test/lint/type 도구의 실제 실행 증거를 사용합니다. `complexity`의
  C++에서는 도구가 함수 경계 geometry를 제공하며, 경계 안의 CC/nesting 자체는 ici metric입니다.
- `heuristic`: AST, 경량 parser, token 또는 pattern 기반으로 한계를 명시하는 분석입니다.
- `unsupported`: 현재 그 언어를 분석하지 않습니다. 언어가 없거나 지원하지 않는 행은
  `NOT_APPLICABLE`, 대상이 있지만 실행하지 못한 행은 `NOT_RUN`, 제한된 fallback만 수행한
  행은 `ESTIMATED`로 남으므로 미지원 범위가 PASS로 보이지 않습니다.

`thread_sanitize` 행은 C++/Qt 지원을 선언하지만 실행 범위는 `deep` profile의 compiled C/C++
test뿐입니다. Python row는 의도적인 `unsupported`이며 Python ResourceWarning을 실행하지
않습니다. CMake/qmake가 선택되면 각 adapter의 `THREAD_SANITIZE` build variant를 사용하고,
descriptor가 없으면 generic g++ 경로를 사용합니다. 표의 `tool-backed`는 이 실제 tool execution
evidence를 뜻하며 TSan이 도달하지 못한 interleaving이나 테스트되지 않은 경로의 부재를 증명하지
않습니다.

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
  확인한 실행 가능한 직접 GCC/Clang driver만 허용하고, 등록된 `gcc`/`g++` probe는 `--version`
  banner에서 지원된 GCC 또는 Clang family를 식별한 경우에만 complete로 인정합니다. source·working directory 경계도 다시
  검사합니다. replay adapter는 `-c`·출력·dependency 생성 옵션은 제거하고, positive allowlist
  밖의 option과 plugin/wrapper/toolchain 주입 등 unsafe option은 fail-closed로 거부한 뒤
  controlled syntax operation을 붙여 실행합니다. inherited override를 배제한 minimal
  replacement environment와 closed stdin을 사용합니다. 위치가 있는 `error`/`warning`/`note:`
  진단과 PASS target은 원래 파일·라인에 보존하며, error-level context/unit diagnostic만
  `ERROR`/`NOT_RUN`으로 닫습니다. warning-level diagnostic은 위치 있는 `WARN`/`MEASURED`로
  남기고 replay를 계속하며, compilation context가 존재하는 동안 고정 `g++ -std=c++17`
  폴백은 사용하지 않습니다. C++ lint cache helper는 `ici.core._cpp_replay_policy`,
  `ici.core.cpp_replay`, `ici.engines._clang_tidy`, `ici.engines._clazy`,
  `ici.engines._cpp_diagnostic_categories`, `ici.engines._cpp_diagnostics`,
  `ici.engines._cpp_lint`, `ici.engines._cpp_tooling`, `ici.engines._qt_codegen`,
  `ici.engines.lint`를 명시하고, cycle cache helper는
  `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_include_graph`,
  `ici.engines._cpp_include_trace`, `ici.engines.cycle`을, complexity cache helper는
  `ici.core._compile_db_paths`, `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`,
  `ici.engines._cpp_function_boundaries`, `ici.engines._cpp_tooling`, `ici.engines.cpp_text`를
  명시합니다. `.ui`와 `.qrc`도 project source digest에 포함됩니다.
- **C++ clang-tidy**: `clang_tidy`는 `auto`(도구가 없으면 `WARN`), `required`(도구가
  없으면 `ERROR`), `off`(명령과 evidence 없음) 중 하나로 정책을 정합니다. 이 adapter는
  capability inventory의 approved direct `clang-tidy`와 exact `CompilationContext`의 covered
  production unit만 실행합니다. 설정 우선순위는 명시한 `clang_tidy_config` > source에서 project
  root까지 올라가 발견한 가장 가까운 project `.clang-tidy` > built-in defaults입니다. 프로젝트
  밖의 config·프로젝트 밖을 가리키는 symlink·비정규 파일은 거부하고, project root 위의 parent
  config는 탐색하지 않으며 config가 없을 때 `--config={}`를 전달해 clang-tidy의 암묵적인 parent
  lookup도 막습니다.
  `clang_tidy_checks`는 1~128개의 중복 없는 non-empty glob 문자열 목록이며, 각 항목에 쉼표를
  넣지 않고 별도 항목으로 적습니다. 지정한 checks는 config 파일이나 built-in defaults보다
  우선합니다. config의 `ExtraArgs`/`ExtraArgsBefore` compiler-argument injection과
  `InheritParentConfig` parent inheritance도 실행 전에 거부합니다.
- clang-tidy 명령은 context가 이미 정규화한 compiler replay의 안전한 인자만 `--` 뒤에 전달하며,
  compilation database를 직접 다시 읽거나 `-p`를 사용하지 않습니다. `-c`, output/dependency
  생성, plugin/wrapper 주입과 allowlist 밖 옵션은 제거하거나 fail-closed로 거부합니다. 명령에는
  `--fix`를 넣지 않고 source와 context를 read-only로 다루므로 fix-it은 report finding의
  remediation 제안으로만 남습니다. 단위 실행은 120초, 전체 clang-tidy 실행은 최대 600초의
  bounded budget을 공유하며, budget을 넘긴 나머지 unit은 실행하지 않고 `ERROR`로 기록합니다.
- clang-tidy 또는 clazy가 Clang 기반이고 exact replay의 compiler가 capability-approved `g++`와
  resolved file identity까지 일치하면, 두 adapter는 같은 GCC driver로 `c++`와 `c` include-search를
  각각 한 번 probe합니다. probe에는 sanitized `-m*`, `--sysroot`/`-isysroot` selector만 보존하고,
  C++ search 결과에서 C search 결과를 뺀 나머지를 compiler 출력 순서대로
  `-nostdinc++`와 `-isystem <root>` 쌍으로 투영합니다. 각 probe는 최대 5초, 두 probe 합계는 최대
  10초이며 131,072 output characters·64 directories bound와 replacement environment/closed stdin을 사용해
  `g++ stdlib include search` `ToolEvidence`로 기록합니다. compiler의
  device/inode/mode/size/mtime/ctime 및 working-directory의 device/inode/mode/mtime/ctime
  identity를 projection cache key와 probe 전후 검증에 함께 묶으며, identity가 다르면 projection 대상이
  아니며, 일치한 GCC의 malformed·timeout·truncated·nonzero probe 또는 표준 라이브러리 경로
  미확인은 Clang 도구를 실행하기 전에 fail-closed합니다. 이미 `-nostdinc`/`-nostdinc++`가 있으면
  projection을 중복 적용하지 않고, C translation unit에는 C++ 표준 라이브러리를 투영하지
  않습니다.
- compiler diagnostic format은 capability path와 관측된 family/version을 기준으로 선택합니다.
  실제 GCC family version 9 이상은 `-fdiagnostics-format=json`, older approved GCC와 approved
  Clang-family driver/alias는 `-fdiagnostics-parseable-fixits` text를 사용합니다. 같은 executable의 관측된
  Clang family는 `g++` alias spelling보다 우선하며, 중립 이름이나 Apple alias도 기록된 family를
  따릅니다. controlled `-fdiagnostics-show-option`이
  project의 rule-visibility 설정을 대체합니다. capability에 resolve되지 않는 unknown/unapproved
  compiler는 text fallback 대상이 아니며 replay 전에 거부됩니다. syntax replay는 source를 최종
  operand로 유지하고, unused-function replay는 source의 원래 positional slot을 보존합니다.
  JSON/text parser는 malformed output을 일부 성공 결과와 섞지 않고 atomic하게
  거부하며, project-relative/external location, stable rule ID, child/note diagnostic과 fix-it
  range/replacement를 보존합니다. compiler의
  child/note와 Clazy의 rule-owned `ClazyNote`는 기존 정책대로 독립 진단으로 유지합니다.
  반대로 clang-tidy text의 `clang-analyzer-*` rule과 일반 check에서 primary를 설명하는
  rule-less `note:`와 primary와 같은 rule을 명시한 `note:`는 같은 contiguous stream에서
  직전 primary의 `CppDiagnostic.related_diagnostics`에만 결합합니다. 새 primary가 나오면
  다음 group이 시작되고, orphan note 또는 다른 check rule을 명시한 note는 전체 stream을
  atomic하게 거부합니다. note의 위치·메시지는 `Finding.related_locations`로 보존하고 note
  fix-it은 primary finding의 remediation과 `extra` metadata에 포함하지만,
  clang-tidy/analyzer의 warning, violation, diagnostic-family, finding 집계에는 primary만
  포함합니다. Native finding canonicalization은 related location을 canonical project-relative
  path와 1-indexed region으로 정규화한 뒤 `(path, start line/column, end line/column, label)`
  순서로 deterministic하게 정렬합니다. C++ category projection은 isolated
  `_cpp_diagnostic_categories.py`의 `tool-rule-v1`이며 free-form diagnostic message가 아닌
  normalized `family`/`tool_rule_id`만 읽습니다. `clang-analyzer-security.*`,
  `clang-analyzer-alpha.security.*`, `clang-analyzer-optin.taint.*`, 그리고 clang-tidy의
  `cert-*`/`android-cloexec-*`와 exact `bugprone-command-processor`,
  `bugprone-signal-handler`, `bugprone-unsafe-functions`, `concurrency-mt-unsafe` rules는
  `SECURITY`입니다. Analyzer의
  exact resource IDs 및 `clang-analyzer-alpha.webkit.*`/`clang-analyzer-webkit.*` prefixes와
  tidy의 exact resource IDs는 `RESOURCE`로 승격하고, 나머지 analyzer는 `CORRECTNESS`로
  fallback합니다. tidy의 `portability-*`와 `modernize-deprecated-headers`는
  `COMPATIBILITY`, security/resource 예외를 제외한 모든 `bugprone-*`/`concurrency-*`는
  `CORRECTNESS`, 나머지 tidy는 `MAINTAINABILITY`입니다. compiler와 unknown family는
  `CORRECTNESS`로 안전하게 fallback합니다. exact ID 목록은 [사용자 가이드의 정책 표](user-guide.md#c-diagnostic-category-policy)에
  고정합니다.
  JSON/HTML은 전체 related-location inventory를 보존하고, GitHub Markdown은
  informational/suppressed finding을 제외한 related row를 engine당 최대 100개까지만 표에
  표시하며 생략 수와 full JSON/HTML 안내를 남깁니다. fix-it은 최대 bounded suggestion으로
  remediation과 `extra` metadata에 기록되며 자동 적용하지 않습니다. lint `extra`의
  `cpp_diagnostic_category_policy`는 정책 ID를, `cpp_diagnostic_categories`는 모든 v3
  category의 primary diagnostic count를 제공합니다.
  정상 실행 evidence는 `MEASURED`이고, timeout·truncation·nonzero·malformed output·context
  mismatch/coverage 누락·replay 오류는 heuristic으로 조용히 대체하지 않고 `ERROR`/`NOT_RUN`으로
  fail-closed 처리합니다.
- compiler adapter도 최대 2,048 translation units, unit당 120초, 전체 600초로 제한합니다.
  compilation context 자체에 error diagnostic이 있으면 replay를 시작하지 않습니다. 위치가 없는
  GCC command-line/ICE diagnostic은 버리지 않고 bounded `[external]`:1 target으로 보존하며,
  context가 불완전한 상태에서 정상 unit 일부만 실행해 clean 결과를 만들지 않습니다.
- **C++ Qt clazy 및 generated-code 검증**: `clazy`는 capability registry에서
  `clazy-standalone`을 우선하고 `clazy` compiler-wrapper를 fallback provider로 기록합니다.
  `clazy = "auto" | "required" | "off"`는 optional/required/disabled 정책이며,
  `clazy_profile = "level0" | "level1"`은 global `ici.profile`과 독립적인 명시적 profile입니다.
  `clazy_checks`를 지정한 경우에만 level2/manual noisy checks를 선택하고 profile보다 우선합니다.
  standalone은 `--checks`·`--only-qt`와 exact context의 sanitized compiler arguments를,
  wrapper는 approved `clang++`를 `CLANGXX`로 고정한 replacement environment와 `CLAZY_CHECKS`를
  사용합니다. 두 경로 모두 compilation database 재탐색, `-p`, `--fix`, shell, source/context
  수정을 하지 않습니다.
- Clang 기반 clazy 실행도 선택 GCC의 exact libstdc++를 사용하도록 위 표준-library projection을
  공유합니다. Ubuntu 24.04의 GCC 13/14 혼재처럼 최신 header가 잘못 선택될 수 있는 환경에서
  projection은 `/usr/include/c++/13`, `/usr/include/x86_64-linux-gnu/c++/13`,
  `/usr/include/c++/13/backward`를 compiler가 보고한 순서로 `-nostdinc++`와 `-isystem`으로
  전달합니다. toy-projects PR #38 run `33531285208`의 Qt 5/Qt 6 deep 실패를 재현한 뒤 fixed
  local pyz에서 12 sources, 2 probes, clazy exit 0과 expected warning 보존을 확인했습니다.
- clazy parser는 `-Wclazy-*` warning option 형태를 strict하게 검증하고 위치 있는 diagnostics와
  parent rule note를 `family = "clazy"` 및 stable rule ID로 보존합니다. `tool-rule-v1`은
  `clazy-lifetime`, `clazy-ownership`, `clazy-parent-less`, `clazy-qobject-cast`의 bounded
  stem과 `clazy-connect-3arg-lambda`, `clazy-ctor-missing-parent-argument`,
  `clazy-lambda-in-connect`, `clazy-post-event`, `clazy-returning-data-from-temporary`,
  `clazy-temporary-iterator` exact rules를 `RESOURCE`로, `clazy-qt6`, `clazy-deprecated`,
  `clazy-qstring-arg`, `clazy-qt-keyword`의 bounded stem과
  `clazy-modernize-overloaded-connects`, `clazy-no-module-include`, `clazy-old-style-connect`,
  `clazy-qenums`, `clazy-qstring-ref`, `clazy-use-chrono-in-qtimer` exact rules를
  `COMPATIBILITY`로, `clazy-qobject`, `clazy-connect`, `clazy-signal`, `clazy-slot`,
  `clazy-qevent-cast`의 bounded stem과 stable correctness exact rules를 `CORRECTNESS`로
  매핑하고, 그 밖의 rule은 `MAINTAINABILITY`로 fallback합니다. stem 자체 또는 `-`/`.` child만
  매칭하며 arbitrary substring은 인정하지 않습니다. stable correctness exact rule의 전체 목록은
  [사용자 가이드의 clazy 표](user-guide.md#c-diagnostic-category-policy)에 고정합니다. adapter와 parser는
  최대 2,048 units·unit당 120초·전체 600초 및 1,000,000자 output bound를 적용하고,
  malformed output·context/coverage/replay/process 오류·timeout/truncation·budget 초과는
  `ERROR`/`NOT_RUN`으로 fail-closed합니다. Ubuntu Noble clazy 1.11의 legacy raw-source/caret/
  replacement context는 located diagnostic의 project source line과 exact match일 때만
  bounded replacement preview 하나까지 허용하며, source mismatch나 forged/extra preview 등
  malformed context는 partial finding 없이 atomic하게 거부합니다.
- Legacy macro context의 외부 header source line은 exact sanitized compiler argv에서 추출한
  approved include root를 통해서만 검증합니다. root는 최대 512개의 bounded directory이며,
  parser는 외부 위치를 항상 `[external]`로 export합니다. source line은 `O_NOFOLLOW`
  regular-file descriptor로 읽고 device/inode/size/mtime identity를 열기 전후 비교하며,
  source-context 누적 1,000,000 bytes와 line 8,192 characters bound를 적용합니다. symlink·비정규
  파일·identity 변경·root 밖 경로·exact preview mismatch·forged/extra preview와 bound 초과는
  partial 결과 없이 fail-closed합니다. clazy process의 nonzero 종료도 ordinary warning output과
  무관하게 atomic `ERROR`이며, evidence에는 raw prose/path 대신 bounded exit code와
  `fatal`/`error`/`warning`/`note`/`remark` counts 및 processing/output flags만 기록합니다.
- Qt generated-code stage는 source scope의 `.ui`, `.qrc`, `Q_OBJECT`를 bounded하게 찾고,
  exact database에서 `ui_<stem>.h`의 bounded indirect translation-unit include linkage,
  `qrc_<stem>.cpp` generated unit,
  `moc_<stem>.cpp`·`<stem>.moc`·`mocs_compilation.cpp` linkage를 원본 입력 위치에 기록합니다.
  include/define/compiler replay evidence에서 Qt 5/Qt 6 major를 구분하고 successful replay가
  있을 때만 linkage와 compatibility PASS를 냅니다. 중복 generated stem은 WARN으로 닫습니다.
  CMake AUTOMOC/AUTOUIC/AUTORCC와 qmake direct unit을
  모두 지원하며, `.ui`/`.qrc`와 I4-2 helper source는 analysis cache identity에 포함됩니다.
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
- CMake의 CTest JUnit 경로는 실행 전에 예정된 shadow report를 제거하고, 그 실행이 만든
  `--output-junit` report만 stable regular file/no-follow descriptor로 최대 1,000,000 bytes까지
  읽습니다. 파일 변경·symlink·비정규 파일·malformed/oversized XML은 bounded CTest stdout
  parser로 폴백합니다. JUnit failure/error와 `system-out`/`system-err`에서
  LeakSanitizer, AddressSanitizer, UndefinedBehaviorSanitizer marker를 찾으면 nominal PASS라도
  executed failure로 바꾸고, public message에는 bounded 분류만 남깁니다. raw transcript는
  private `TestCaseResult.diagnostic_output`(UTF-8 최대 65,536 bytes)으로 sanitizer engine에
  전달되며, engine은 `kind`/`defect`, `ici.sanitize.*` detail rule, related frame locations,
  frame counts와 process evidence link를 가진 normalized detail을 발행하고, 검증 가능한 경우
  project-owned primary location과 native finding을 제공합니다. native finding은 호환 `rule_id`인
  `ici.legacy.sanitize.target`을 유지하고 상세 sanitizer identity는 `tool_rule_id`에 둡니다.
  외부 path는 related location에서 `[external]`로 redacted됩니다.
  timeout·process-output truncation·정규화 오류·unlocated diagnostic은 clean result가 아닌
  `ERROR`/`NOT_RUN`으로 fail-closed하고, complete located signal failure만 measured `FAIL`로
  보존합니다. test name과 일반 failure message도 512 characters로 제한합니다.
- **테스트 실행 상태 계약**: adapter가 반환하는 `TestCaseResult`는
  `name`, `passed`, `message`, `executed` 네 필드를 가지며, 마지막 `executed`는 기본값이
  `true`인 하위 호환 필드입니다. 따라서 기존의 세 인자 positional 생성은 그대로 동작하고,
  `passed = false`만으로 실행된 실패와 수집됐지만 실행되지 않은 case를 혼동하지 않습니다.
  `passed = true`와 `executed = false` 조합은 유효하지 않습니다.
- **CTest 상태 해석**: JUnit `<skipped>`와 `status="notrun"`, `skip`, `skipped`, `disabled`,
  `blacklisted`는 `passed = false`, `executed = false`로 기록합니다. JUnit의 알 수 없는
  status는 `executed = true`인 실패로 남겨 조용한 통과를 막습니다. stdout 폴백에서도
  `Not Run`·`Disabled`·`Skipped` verdict는 같은 미실행 상태로 보존하며 bounded 사유를
  `message`에 남깁니다.
- **QtTest 상태 해석**: `skip` 또는 `<skipped>`는 미실행 case, `xfail`은 실행된 예상 실패로서
  통과, `xpass`는 실행됐지만 예상 밖으로 통과한 실패, 알 수 없는 `result`는 실행 여부를
  알 수 없는 실행 상태를 통과로 추정하지 않고 `executed = true`, `passed = false`인 실패로
  처리합니다. 즉 `xpass`와 unknown state는 fail-closed이며, skip은 실패 카운트에 섞이지
  않습니다. `test` 엔진 JSON의 `extra.skipped_tests`와 각
  `test_suites[*].skipped`가 이 상태를 집계하고, HTML은 SKIP case를 별도 amber 행으로
  표시합니다. 이 parsing은 QtTest `-xunitxml`의 각 `<testcase>` 상태를 해석하는 범위입니다.
  qmake에서는 `make check` transcript가 테스트 바이너리 하나를 권위 있는 scope로 세며,
  QtTest XML은 그 바이너리의 failure detail만 보강합니다. 따라서 qmake 경로가 모든
  function-level skip을 개별 scope로 집계한다고 해석하지 않습니다.
- **pytest 상태 해석**: verbose per-test 출력과 terminal summary에서 `SKIPPED`는 수집됐지만
  실행되지 않은 case로 `SKIP`/비실행 evidence가 됩니다. `XFAIL`은 실행된 예상 실패이자
  PASS이고, `XPASS`는 실행된 unexpected pass이자 `FAIL`입니다. case line이 없고 summary에
  `N skipped`만 있는 경우에도 `[Python] Skipped (N)` aggregate target으로 미실행 상태를
  보존합니다. 수집된 Python/C++ case가 전부 미실행이면 `[engines.test].required = true`에서
  test engine은 `ERROR`/`NOT_RUN`, `false`에서 `SKIP`/`ESTIMATED`입니다. coverage.py/gcov
  생성물이나 별도 coverage pass는 이 execution state를 대체하거나 all-skipped run을 clean
  measured 결과로 승격할 수 없습니다.
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
  커버리지 임계값의 `PASS` 근거로 사용되지 않습니다. Coverage 산출물은 테스트 실행 상태의
  대체 증거가 아니므로, all-collected-skipped pytest run을 `MEASURED` 또는 `PASS`로 바꾸지
  않습니다.
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
  기본 `mypy_profile = "project"`는 project root를 working directory로 사용해 Mypy의
  `mypy.ini`, `.mypy.ini`, `pyproject.toml`, `setup.cfg` 탐색과 import/error 정책을 그대로
  보존하며 `--ignore-missing-imports`를 강제하지 않습니다. `mypy_profile = "ici"`는 명시적
  argv overlay로 `--check-untyped-defs`, `--warn-redundant-casts`, `--warn-unused-ignores`만
  추가합니다. 두 profile 모두 오류와 note를 구분하고 도구가 제공한 line/column을 보존합니다.
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
- **Python 함수 경계**: 중첩 함수·클래스·lambda의 실행 본문은 바깥 함수 점수에서 제외하고,
  중첩된 named function/method는 자체 위치 target으로 별도 측정합니다. decorator·default·annotation·
  class base/keyword처럼 정의 시점에 평가되는 표현식은 바깥 함수 metric에 유지합니다.
- **C++ 함수 경계**: `cpp_boundaries = "auto" | "required" | "off"` (기본 `auto`)를 사용합니다.
  exact `CompilationContext`/compilation database와 capability-approved direct `clang-tidy`가
  있을 때만 전용 `readability-function-size` diagnostic의 AST 결과로 함수 경계 geometry를
  확정합니다. `boundary_source = "clang-tidy-ast"`와 `boundary_confidence = "exact"`는
  경계에만 해당하며, 경계 안의 CC/nesting은 masked source token/brace metric이고
  `metric_confidence = "medium"`입니다. clang-tidy의 lines/statements/parameters notes는
  별도 tool metadata로 보존합니다. 공유 parser가 이 metric notes를 primary 아래
  `related_diagnostics`로 보관하더라도, boundary parser는 primary와 related stream을 순서대로
  소비해 notes를 geometry mapping에 사용합니다. 이 구조적 소비는 lint finding을 다시
  독립 finding으로 flatten하지 않습니다.
- **C++ scope classification**: AST boundary target은 source-spelled named function이며 function
  template, conversion/call/subscript operator, literal operator를 포함합니다. `function_kind`,
  `function_template`, `function_origin` metadata로 kind/template/provenance를 보존합니다.
  lambda는 독립 함수 target으로 만들지 않고 lambda body는 enclosing function의 CC/nesting에서
  제외합니다. Macro-generated function이 expansion site에서 진단되면 해당 scope를 명시적으로
  제외하고 `extra.cpp_scope_exclusions.macro_generated_function`에 count를 기록하며, 파일의
  다음 brace로 body를 추정하지 않습니다. fallback scanner는 operator 이름을 보존하고 multiline
  preprocessor definition/continuation과 standalone macro invocation을 skip합니다.
  lambda 제외 수는 `extra.cpp_scope_exclusions.lambda`에서 확인할 수 있습니다.
- **C++ 폴백·실패 경계**: `auto`는 exact context/database 또는 approved tool이 없을 때만
  source scanner로 폴백하며 `ESTIMATED`와 `boundary_source = "heuristic"`를 남깁니다. 빈/미보고
  source-spelled definition은 heuristic으로 남을 수 있지만 macro-generated expansion은 target에서
  제외됩니다. 시도된 tool·replay·parser·timeout·
  truncation·coverage·budget 오류는 heuristic으로 숨기지 않고 `ERROR`/`NOT_RUN`입니다. 단,
  clang-tidy가 visible project diagnostics와 함께 `Suppressed N warnings (N in non-user code).`를
  정확히 보고하면 외부/system 진단만 억제한 정형 회계로 허용합니다. NOLINT/project/mixed/malformed/
  count-mismatch suppression은 계속 `ERROR`/`NOT_RUN`으로 fail-closed합니다. `required`는
  unavailable 또는 partial/estimated boundary도 오류로 승격하고, `off`는 probe 없이
  의도적으로 heuristic 경로를 사용합니다. probe 입력은 caller의 bounded source snapshot과
  mapped-source cache이며, replay 전·도구 완료 후 source identity를 재검증합니다. C++ 전체
  source inventory는 최대 2,048 source files와 64 MiB aggregate UTF-8 source bytes cap 안에서만 만들고,
  같은 geometry, name, kind, provenance가 성공한 모든 configuration에서 일치할 때만 exact로
  merge합니다. configuration별 clang-tidy lines/statements/parameters는 `configuration_metrics`에
  보존합니다. missing 또는 configuration-dependent geometry는 partial warning으로 남으며,
  function-size metric 값이 configuration마다 다르거나 body에 conditional preprocessor branch가
  있으면 mode도 `partial`, 해당 metric confidence도 `low`가 됩니다. compiler-backed function
  metrics 또는 configuration coverage가 partial/low-confidence로 남으면 `required`에서는
  `ERROR`/`NOT_RUN`으로 fail-closed합니다. 단위/캐시/출력 경계는
  2,048 units, source당 8 MiB, run source bytes 64 MiB, mapped-source cache bytes 16 MiB,
  output 1,000,000자, parser 10초, unit당 120초, 전체 600초입니다. approved tool executable은
  매 process 실행 직전에 다시 resolve하고 device/inode/mode/size/mtime/ctime identity를 확인하며,
  변경·부재는 fail-closed입니다. parser regression은
  same-line 인접·overload, constructor/parameter/default/noexcept/trailing `requires`의
  brace, function-try/catch, `<%`/`%>` digraph body 및 assigned `[]`/`+[]` lambda initializer의
  phantom-function 배제를 포함합니다. `dir_fd`/`O_DIRECTORY`가
  없는 descriptor fallback도 read 뒤 resolved named path의 containment와 device/inode/size/mtime
  identity를 재검증해 intermediate symlink/TOCTOU를 fail-closed합니다.
- **검증 상태**: PR #130의 historical compiler-boundary baseline은 두 번 byte-identical인
  candidate SHA `7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`와 Python
  3.10 `1,626 passed, 2 skipped`를 남겼습니다. 이후 local
  `feat/cpp-function-scope-policy` candidate는 두 번 byte-identical인 `dist/ici.pyz` SHA
  `2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7`이며, real extracted
  `clang-tidy-21`을 사용한 Python 3.10 full suite `1,656 passed, 2 skipped`, Ruff check/format,
  mypy와 packaged smoke가 통과했습니다. Parser/source mapping(628 pure code lines)과 process
  runner facade(487 pure code lines)는 분리되어 self line gate도 통과합니다. Fresh clean toy
  `main`의 BuildScope `auto`/`required`, DiskMap `auto`, LogLens `auto` 교차 probe와 4/4
  title·Zero-CDN 검사도 완료됐으며 상세는 [C++ function-scope policy workthrough](workthrough/2026-09-02-cpp-function-scope-policy.md)에
  기록합니다.

  [PR #131](https://github.com/jihoon22-lee/ici/pull/131) `feat(complexity): classify C++ function
  scopes and metric provenance`는
  [`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7)로
  squash merge됐습니다. PR CI [run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495)은
  성공했고 sticky marker/current run은 정확히 하나였습니다. PR ici/viewer Pages는
  HTTP/title/Zero-CDN과 artifact byte-match를 통과했으며 `7,454,995`/`356,598` bytes였습니다.
  Exact-main [run `33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450)도
  성공했고 main JSON `source_commit`은 같은 SHA와 일치했습니다. main ici/viewer Pages도
  같은 검사를 통과했으며 ici는 `7,454,995` bytes/SHA `182a0d05…5adbb75`, viewer는
  `356,598` bytes/SHA `fb772d4a…c0c4794`로 artifact byte-match됐습니다. 두 run에서 skip된
  것은 예상된 PR/main publish job뿐입니다. 이 acceptance는 target-local GNU ELF section-GC를
  넘어서는 whole-program/dynamic dead reachability, full semantic duplicate, 남은 I4-4, I4 전체
  checkpoint의 완료를 의미하지 않습니다. 버전은
  `0.10.2`로 유지하고 release는 만들지 않습니다.
- **코드 스니펫**: 고복잡도 함수의 실제 원본 소스 코드를 추출하여 HTML 리포트에 즉시 표시

### 2.6 🛡️ `sanitize` (메모리 안전성 및 리소스 누수 진단)
- **C++**: AddressSanitizer(`-fsanitize=address`) 및 UndefinedBehaviorSanitizer(`-fsanitize=undefined`)를
  임시 프로젝트 외부 산출물로 빌드·실행하고, 실행 환경에 leak 검출을 활성화해 LSan 결과도
  수집한다. ASan/LSan/UBSan의 bounded `ERROR`/`SUMMARY` 또는 UBSan `runtime error` 서명만
  normalized diagnostic으로 인정하며, 각 결과는 deterministic `kind`/`defect`,
  `ici.sanitize.*` detail rule, related stack-frame locations, 관측/프로젝트 frame count와
  sanitizer process evidence link를 갖고, 검증 가능한 경우 project-owned primary location과
  native finding을 제공한다. native
  finding은 호환 `rule_id`인 `ici.legacy.sanitize.target`을 유지하고 상세 sanitizer identity는
  `tool_rule_id`에 둔다. 외부 frame path는 `[external]`로
  redacted된다. CTest/QtTest adapter의 raw transcript는 public message와 분리한 private
  transport로 최대 65,536 UTF-8 bytes만 전달한다. 컴파일/실행 도구 오류, timeout·출력 절단,
  malformed/oversized transcript, 정규화 오류 또는 project location이 없는 diagnostic은
  `ERROR`/`NOT_RUN`으로 fail-closed한다. 완전한 project-owned location을 가진 signal 종료의
  failure만 `FAIL`/`MEASURED`로 보존하며, nominal PASS case에 붙은 sanitizer marker도
  executed failure로 승격한다.
- **Python**: Task 5가 선택한 동일 인터프리터로 `-W error::ResourceWarning -m pytest -o addopts= tests`를 실행해 리소스 경고를 측정한다. `test_*.py`와 `*_test.py`를 모두 대상으로 하며, 0개 실행 테스트(전부 skipped/deselected)·pytest 부재·timeout·출력 절단·실행 실패·잘못된 성공은 통과로 간주하지 않는다. 기존 `PYTHONPATH`와 WSL 임시 디렉터리 정책도 보존한다.
- **진단 판정**: 출력에 sanitizer 이름만 언급된 경우는 결함으로 판정하지 않는다. 위치가 있는 UBSan `runtime error` 또는 ASan/LSan/UBSan의 `ERROR`/`SUMMARY` 서명만 `sanitize`에서 실제 진단으로 인정하며, project-owned 위치가 검증되지 않은 진단은 clean 결과가 아닌 위치 오류로 남긴다. TSan은 별도 deep-only `thread_sanitize` 경계에서 `WARNING`/`SUMMARY` report signature를 처리하며 `sanitize`와 섞지 않는다.
- **적용 범위**: Python/C++ hybrid에서 한 언어의 scope가 건너뛰면 결과는 `WARN`/`ESTIMATED`이며, 대상 자체가 없으면 명시적 `SKIP`이다. C++ 테스트 파일은 project 경계 안의 실제 파일만 선택하며 외부 symlink는 제외한다. ResourceWarning의 Windows drive/공백 경로도 원본 파일과 라인 위치를 보존한다. 실행 시 기존 `ASAN_OPTIONS`/`UBSAN_OPTIONS`를 보존하면서 leak 검출과 UBSan 중단 옵션을 추가한다.
- **sanitizer 테스트 누락 정책**: CTest/QtTest가 build된 sanitizer scope를 수집했지만
  실행하지 않은 case를 `executed = false`로 보고하면, required 정책에서는 모든 case가
  미실행인 경우와 실행된 case와 섞인 경우 모두 `ERROR`/`NOT_RUN`입니다. 실행된 실제
  sanitizer failure target은 이때도 `FAIL`로 보존합니다. `required = false`인 선택 정책은
  모든 case가 미실행이면 `SKIP`/`ESTIMATED`, 실행된 clean case와 미실행 case가 섞이면
  `WARN`/`ESTIMATED`, 실행된 실제 failure와 미실행 case가 섞이면 `FAIL`/`ESTIMATED`로
  집계합니다. 미실행 case는 issue 수나 측정 scope에 포함하지 않습니다.

#### 2.6.1 🧵 `thread_sanitize` (deep ThreadSanitizer profile)

- **적용 범위**: `thread_sanitize`는 `deep` profile에서만 선택되는 C++ 전용 build engine이다.
  `ici thread-sanitize` direct command도 같은 `ThreadSanitizeEngine`을 호출한다. Python은
  support matrix에서 `unsupported`이며 Python ResourceWarning 검사를 수행하지 않는다.
- **격리된 계측**: `BuildVariant.THREAD_SANITIZE`의 값은 `thread-sanitize`이고 shadow suffix는
  `-tsan`이다. CMake/qmake adapter에는 `-fsanitize=thread` compile/link flag와
  `-fno-omit-frame-pointer -g` compile flag를 전달한다. descriptor가 없는 generic g++ 경로는
  같은 TSan/debug/frame-pointer flags와 generic `-pthread` link를 사용한다. ASan/LSan/UBSan
  `sanitize` variant와 같은 build tree나 instrumentation을 공유하지 않는다.
- **실행 환경**: 기존 `TSAN_OPTIONS`를 지우거나 덮어쓰지 않고 `halt_on_error=1`을 추가한다.
  TSan output은 complete `WARNING: ThreadSanitizer:` 또는 `SUMMARY: ThreadSanitizer:`
  signature만 report starter로 인정하며 bounded UTF-8 transcript, diagnostic count, stack
  frame, source-file read limits를 적용한다.
- **정규화와 위치**: data race, lock-order inversion, thread leak, mutex 오류 등 알려진
  defect prefix는 deterministic `ici.sanitize.tsan.<defect>` rule로 매핑한다. 알 수 없는
  TSan wording은 불안정한 자유 문구를 public rule로 만들지 않고
  `ici.sanitize.tsan.thread-safety-defect`로 수렴한다. 검증된 project-owned 위치만 primary로
  보존하고 외부 frame은 `[external]`로 redacted한다. complete located report가 있는 signal
  failure는 measured `FAIL`이며 malformed·oversized·unlocated evidence는 clean으로 축약하지
  않고 fail-closed한다. TSan taxonomy는 ASan/LSan/UBSan prefix와 분리한다. Aggregate CTest 또는
  qmake stream에 complete TSan report가 있으면 framework가 모든 case를 PASS로 표시하거나 process가
  0으로 끝나도 첫 executed case에 진단을 연결해 measured failure로 보존한다. Executed case가
  하나도 없으면 synthetic process case를 추가해 aggregate 진단 자체가 사라지지 않게 한다.
- **검증 상태**: 실제 `g++` data-race fixture를 포함한 local regression, PR #146 run
  `33717584710`, exact-main run `33718399268`, toy PR #56과 exact candidate run
  `33737405098`의 8/8 contract가 통과했다. 이는 TSan sub-scope의 완료 증거이며 broader
  resource/lifetime/security taxonomy, I4-4 전체 checkpoint나 release 완료를 의미하지 않는다.

### 2.7 💀 `dead` (죽은 코드 및 미사용 심볼)
- 도달할 수 없는 블록과 private module-level Python 함수의 실제 `Name`/호출 및 cross-module `from`/attribute 참조를 분석한다.
- `import pkg.a; pkg.a._foo()` 및 `from pkg import a; a._foo()`처럼 중첩 모듈을 거치는 참조는 실제 정의 모듈에만 연결하며, 같은 이름의 무관한 함수는 별도 경고로 남긴다.
- package `__init__.py`의 `.a`/`from . import a` 상대 import도 package-qualified 모듈로 해석한다. `project.source_dirs`가 충돌하면 앞선 source directory의 모듈 정의만 선택하며, 동일 alias의 여러 lexical import 후보는 누락 없이 보수적으로 연결한다.
- `if`/loop/`try`의 `else`·`finally` 및 `match` case를 포함한 모든 statement-list에서 terminal statement 뒤의 코드를 검사한다.
- decorator 등록 함수, `__all__` export, class method, nested callback function은 합리적인 false positive를 피하기 위해 제외하며, 분석된 정상 source 위치도 `PASS` target으로 보존한다.
- 공통 intake는 Python discovery를 한 번만 캡처한 뒤 deterministic lexical order로 정렬된
  strict UTF-8 snapshot을 읽는다. `generated`/`autogen` 디렉터리와 `moc_`·`qrc_`·`ui_`·
  `mocs_compilation`·`.moc` 입력 및 vendor/dependency 디렉터리를 기본 제외한다. unique
  candidate 8,192개, owned/analyzed file 2,048개, 파일당 8 MiB, aggregate 64 MiB 한도를
  적용하며 위반·경계 이탈·읽기 오류·NUL text는 partial 결과로 숨기지 않고 `ERROR`/`NOT_RUN`
  target으로 남긴다. 직접 엔진 config에서는 literal `true`만 opt-in으로 인정한다.
- **Python evidence**: AST reachability/name-reference에는 compiler/linker 증거가 없으므로
  `evidence = ESTIMATED`, `analysis_provenance = python-ast-heuristic`이다.
- **C++ compiler-backed unused-function probe**: `[engines.dead].cpp_unused`는 `auto`,
  `required`, `off` 중 하나이며 기본값은 `auto`다. exact `CompilationContext`가 정확히 덮는 모든
  owned project C/C++ source translation unit의 모든 알려진 canonical configuration을 immutable
  command로 재생하고 `-Wunused-function`을 추가한다. `project.cpp_external_build_dirs`로 지정한
  external build directory 안의 owned source도 exact database가 해당 unit을 덮으면 포함하며,
  build/link engine의 self-link 제외 정책은 이 source scan에 적용하지 않는다. 각 selected
  `CompilationUnit`의 explicit `language`는 `c` 또는 `c++`여야 하고 다른 값이나 빈 값은 compiler
  실행 전에 거부한다. 각 configuration identity는
  `directory`/`argv`/`output` payload의 canonical digest를 replay 전에 다시 계산해 대조한다.
  `unity_build = true`인 context만 이
  source-ownership 계약에서 거부하며, `false` 또는 `null` 값 자체는 거부하지 않는다.
  capability inventory가 성공적으로 probe한 외부 regular **직접 GCC/Clang driver**만 허용하며,
  `gcc`/`g++`/`clang`/`clang++` capability에 resolve되는 `cc`/`c++` 또는 target/version 표기의
  approved alias만 사용할 수 있다. wrapper·project-contained·unprobed/unknown driver는
  거부한다. 관측된 compiler family가 GCC이고 version 9 이상이면
  `-fdiagnostics-format=json`, older GCC와 approved Clang-family driver/alias는
  `-fdiagnostics-parseable-fixits` bounded text를 사용한다. 같은 executable의 관측된 Clang
  family는 `g++` alias spelling보다 우선하며, project가 지정한 rule-visibility 설정은 제거하고
  controlled `-fdiagnostics-show-option`을 강제한다.
  GCC가 `-fsyntax-only`에서 이 경고를 생략할 수 있으므로 diagnostic-only probe는 `-S -o
  os.devnull`로 discarded assembly를 생성하며, object·실행 파일·링커 산출물은 만들지 않는다.
  sanitized command에서는 canonical source operand를 원래 compile argv 위치에 정확히 한 번
  보존하고 controlled diagnostic arguments를 뒤에 추가한다. option separator 뒤의 추가 operand는
  `-w`나 두 번째 `--`를 포함해 모두 거부한다.
- C++ exact finding은 **compiler가 선택된 translation unit source에 귀속한 logical 위치 범위**에
  있고 rule ID가 정확히 `-Wunused-function`인 warning diagnostic만 보존한다. logical path가
  selected TU와 정확히 같고 line/column 범위가 immutable source snapshot 안에 있어야 한다.
  범위를 벗어난 `#line`/macro-expansion remapping은 fail-closed하며, ici는 accepted 위치를
  physical source provenance 또는 source-spelled declaration으로 역추적하지 않는다. 같은 source에 속한
  모든 알려진 configuration에서 필터된 위치 집합이 일치할 때만 exact로 승격하며, clean source도
  configuration 수를 가진 `PASS` target으로 남긴다. 결과 `extra`에는 `cpp_unused_policy`,
  `cpp_unused_mode`, 검사한 source/configuration 수, tool metadata와 `language_evidence`
  (`python`/`cpp`별 `NOT_APPLICABLE`·`NOT_RUN`·`ESTIMATED`·`MEASURED`)를 기록한다. target name은
  `Compiler:-Wunused-function`이고 native finding의 `tool_rule_id`는 `-Wunused-function`이며
  `FindingConfidence.EXACT`를 사용한다.
- `cpp_unused_non_tu_diagnostics_excluded`는 rule ID가 정확히 `-Wunused-function`이고 warning인
  diagnostic이 먼저 확인된 뒤, 그 logical path가 selected source와 다를 때만 증가한다. 따라서
  header/other-TU/external 위치의 matching warning만 count에 남고 unrelated warning·note·error는
  세지 않는다. matching warning이 unlocated이면 source 귀속을 증명할 수 없으므로 clean PASS로
  제외하지 않고 전체 C++ probe를 fail-closed하며, malformed output도 동일하다. macro-generated definition도
  compiler-attributed expansion 위치를 사용한다. 일반 external-linkage symbol, template,
  inline/COMDAT definition, linker reachability, dynamic lookup/plugin entry point 및 Qt meta-object
  reachability도 이 `cpp_unused` TU-local probe의 dead 증거가 아니다. GNU ELF target-local
  discarded-function 증거는 아래의 독립 `cpp_linker` 정책으로 다룬다.
- **정책과 fail-closed**: pure C++ scope에서 compilation context/database/approved compiler가
  unavailable 또는 not-applicable이고 실제 analysis/context/intake error가 없을 때만 `auto`가
  required gate를 완화한다. 이때 C++는 `SKIP`/`NOT_RUN`, `required = false`가 되어 suite에는
  `WARN`만 기여한다. hybrid은 Python 분석을 계속하되 C++ unavailable을 명시하고 정상 완료된
  Python scope의 aggregate evidence를 `ESTIMATED`로 유지한다. `required`는 unavailable 상태도
  `ERROR`/`NOT_RUN`으로 승격한다. context가 존재한 뒤의 invalid context, 실행/coverage/configuration/
  replay/parser/compiler/identity 오류는 `auto`와 `required` 모두 `ERROR`/`NOT_RUN`이며 휴리스틱으로
  대체하지 않는다. `off`는 `dead`의
  C++ path를 source intake/replay에 넣지 않고 compiler/tool evidence도 만들지 않으며, standalone
  `ici dead`에서는 C++ compilation-context preflight도 요청하지 않는다. pure C++ dead gate를
  활성화하지 않으므로 `off`인 C++ scope는 hybrid의 Python 분석을 차단할 수 없고 혼합 프로젝트의
  Python 정책은 계속 적용된다.
- C++ scope는 첫 replay/process/parse/identity 오류에서 fail-fast하며 이후 unit을 실행하지 않는다.
  모든 replay가 성공해도 source configuration의 diagnostic set이 다르면 C++ exact targets를
  전부 폐기하는 atomic merge를 적용한다. 이미 성공적으로 완료·기록된 compiler observation의 source에는 exact PASS/WARN
  대신 위치가 있는 `C++UnusedFunctionsInvalidated` `SKIP` target을 남긴다. 반대로 C++ scope가 나중에 실패해도 이미 완료된
  Python findings는 유지하며, Python finding은 `FindingConfidence.MEDIUM`과 빈 compiler
  tool attribution을 유지한다. hybrid에서 Python `ESTIMATED`와 C++ `MEASURED`가 함께 있으면
  전체 engine evidence는 보수적으로 `ESTIMATED`이고, 언어별 상태는 `extra.language_evidence`
  와 support matrix에 표시된다.
- source snapshot은 no-follow regular-file reader의 descriptor identity와 double-read content를
  검사하고 replay 직전에 다시 읽는다. project-contained working directory의 `device/inode/mode`
  identity와 approved external compiler의 `device/inode/mode/size/mtime/ctime` identity를 process
  직전에 검사하고, otherwise-successful replay 뒤에도 working directory·compiler identity와
  source snapshot bytes를 다시 검사한다. 변경·unsafe replay·timeout·truncated/malformed output·
  비정상 종료는 partial finding 없이 `ERROR`/`NOT_RUN`이다.
- verify의 모든 `dead` result cache key/load/store는 현재 비활성화되어 있다. cache-v3 identity가
  external/generated include의 완전한 dependency closure와 compiler executable content를 아직
  모델링하지 않기 때문이며, 이 두 identity를 cache v3가 모델링할 때까지 Python-only와 hybrid를
  포함한 모든 `dead` result를 재사용하거나 저장하지 않는다. `cpp_unused = auto|required`인
  standalone `ici dead`는 `dead`에 필요한 scoped probes와 설정된 `[doctor].required_tools`만
  요청하고 전체 tool registry를 무조건 probe하지 않는다. `off`는 C++ path discovery로 scope
  존재만 판별하고 C++ context/tool probe와 bytes intake를 생략한다.
- 이 구현은 compiler의 `-Wunused-function` TU-local 계약과 Linux GNU ELF target-local
  section-GC 계약을 함께 제공한다. 후자는 특정 CMake executable에서 GNU `ld`가 버린 uniquely
  mapped local/hidden function section만 `EXACT`로 보존하며 `cmake`/`readelf`/`addr2line`
  도구 증거와 link-target/symbol/section/source 위치를 함께 기록한다. archives/shared/LTO/
  PIE/COMDAT/dynamic/export/linker-script/whole-archive와 ambiguous mapping은 제외한다.
  따라서 여러 target/object/library/plugin과 dynamic lookup을 포함하는 whole-program
  dead-symbol reachability와 C++ semantic/behavioral duplicate equivalence는 여전히 지원
  범위가 아니다. public version은 `0.10.2`로 유지하고 이 feature PR만으로 release를 만들지 않는다.

#### C++ GNU ELF target-local discarded-function 정책

`[engines.dead].cpp_linker = "auto" | "required" | "off"`의 기본값은 `off`이며 `cpp_unused`와
독립적이다. Linux root CMake project에서 ici 소유 Release shadow를 `Unix Makefiles`와
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`으로 구성하고 function-section/section-GC flags를 적용한
뒤 direct-object executable `link.txt`만 검증한다. capability-approved GCC driver가 GNU
`ld`를 실제로 사용한다는 banner도 확인한다. `auto`에서 계약 밖 context/tool/target은
`SKIP`/`NOT_RUN`으로, `required`에서는 `ERROR`/`NOT_RUN`으로 닫는다. context가 시작된 뒤의
malformed command, relink/ELF/binutils/source identity 오류, timeout/truncation은 두 정책 모두
partial finding 없이 fail-closed한다.

link file 256개, direct object 4,096개/target, link command 4 MiB·32,768 arguments·1 MiB
argument characters, discarded section 16,384개, tool-output cap 4 MiB, 전체 900초의 내부 한도를
넘으면 같은 fail-closed 계약을 적용한다. 이 증거는 target-local reachability observation일
뿐 whole-program deadness나 behavioral unreachability 주장이 아니다.

### 2.8 📦 `dup` (코드 복제 및 중복률 감지기)
- **언어별 lexical normalization**: Python과 C/C++에 전용 line-preserving lexer를 사용하고
  language key를 먼저 분리한 뒤 Type-2 clone window를 만든다.
  - Python은 stdlib `tokenize`와 AST context를 사용한다. 주석과 import-first logical
    statement(여러 physical line에 걸친 multiline import 포함)는 window에서 제외하고,
    `match`/`case` soft keyword의 구조적 사용과 imported/API semantic anchor를 보존한다.
    ordinary identifier는 `ID`로, literal 값은 각 integer/float/complex 및
    string/bytes/f-string category 안에서 정규화하며 `INDENT`/`DEDENT`와 operator 경계는
    그대로 구분한다. tokenizer는 malformed input을
    결정적인 opaque marker로 만들지만, 엔진이 AST region boundary를 확정할 수 없는 Python
    source는 partial lexical 결과를 채택하지 않고 `ERROR`/`NOT_RUN`으로 닫는다.
  - C/C++ lexer는 comments와 preprocessor directives를 제거하고 C++ translation-phase
    backslash-newline splice를 적용하면서 physical source line을 유지한다. punctuator
    longest-match 경계, ordinary/raw string·char·number category, UDL suffix와 Qt semantic
    anchors를 보존한다.
- **매칭과 구조 경계**: normalized rolling/window seed를 인덱싱한 뒤 seed token을 exact
  검증하고 양쪽으로 확장해 maximal region을 만든다. Python AST function/class/import scope와
  C++ function/directive scope를 region key로 사용하므로 함수·import·directive 경계를 넘지
  않는다. semantic-signal policy는 literal/identifier 위주의 Python 상수표와 C++ array/enum
  data table을 clone으로 올리지 않으면서 실제 6줄 이상 control-flow 구조를 보존한다. literal
  대입만 반복되는 block은 low-information으로 제외하지만, identifier 사이의 value-flow 대입은
  actionable signal로 취급하는 보수적 trade-off를 regression contract로 고정한다.
  중복 라인 집계는 고유 line 위치 합집합 기준이며, 원본 인덴트와 줄바꿈을 보존한 snippet을
  제공한다.
- **평가 기준**: 전체 코드베이스 대비 중복 라인 비율이 `warn_pct`(5%) 초과 시 경고, `fail_pct`(15%) 초과 시 실패
- `dead`와 동일한 strict UTF-8 bounded intake와 generated/vendor 기본 제외 정책을 사용하며,
  `include_generated`/`include_vendor`를 독립적으로 지정할 수 있다. owned C/C++ headers
  (`.h`, `.hh`, `.hpp`, `.hxx`)도 discoverable하며 standalone `.moc`는 generated로 분류되어
  `include_generated = true`일 때만 포함된다. 두 정책이 겹친 path는 두 switch가 모두 literal
  `true`여야 한다. 분석된 모든 파일은 `DuplicateScan` 또는 clone 위치의 `PASS`/finding
  target으로 보존한다.
- **결과 metadata와 evidence**: clone group과 target metric에는
  `sha256/type2-region-v2` fingerprint를 기록하고 `extra`에 `tokenizer_versions`(현재
  `cpp-lexical-v1`/`python-lexical-v1`), `region_policy = language-function-scope-v1`,
  `signal_policy = minimum-semantic-lines-v1`를 남긴다. 정상 완료된 결과는 compiler/linker
  실측이 아닌 `ESTIMATED` (`analysis_provenance = language-lexical-region-heuristic`)다.
- **Python AST-shape semantic groups**: `[engines.dup].python_semantic = "auto" | "required" | "off"`
  (기본값 `auto`)는 Python 3.10 AST에서 named region을 추출하고 nested callable parent를
  제외한 leaf function/method만 group 대상으로 삼는다. local binding은 alpha-renaming하고
  AST 위치/물리적 layout은 무시하지만 control flow, operator, literal 종류·값, source-spelled
  imported-name/attribute anchor는 보존한다. `sha256/semantic-shape-v1` canonical shape가
  exact하게 같을 때만 group을 생성하며, 같은 occurrence의 lexical Type-2 group은 dedup한다.
  malformed/unsupported AST, lambda/comprehension, `global`/`nonlocal`, star import,
  `eval`/`exec` 호출과 그 이름의 literal `getattr` lookup, nested parent span과 trivial region은
  보수적으로 제외한다.
  `auto`는 exclusion을 partial metadata로 남기고 `required`는 `ERROR`/`NOT_RUN`으로 닫으며,
  `off`는 이 pass를 실행하지 않는다. 파일 256개·named region 20,000개·AST node 500,000개·
  serialized shape 16 MiB 한도 초과는 semantic partial을 버린다. `auto`는 lexical 결과를
  유지하고 `required`는 `ERROR`/`NOT_RUN`으로 닫는다. 이 구조적 clone 신호는
  behavioral equivalence가 아니며 `dup` evidence는 계속 `ESTIMATED`다.
- **내부 한도와 fail-closed**: tokenizer token 수, normalized-character/indexed-record 수,
  shared-window occurrence, same/cross-file seed pair, extension comparison, raw match에는
  deterministic internal budget이 있다. 이 값들은 `[engines.dup]` 사용자 설정 키가 아니다.
  한도를 넘으면 partial clone/PASS를 만들지 않고 `ERROR`/`NOT_RUN`과 위치 있는
  `SourceTokenizationError` 또는 `DuplicateComparisonLimit` target을 반환한다.
- target-local GNU ELF section-GC를 넘어 whole-program/dynamic symbol reachability를 포함하는
  exact dead-symbol 증거와 C++ semantic/behavioral duplicate equivalence는 아직 완료 범위가
  아니다. C++ TU-local `-Wunused-function` 및 GNU ELF target-local probe 계약은 §2.7에 기술한다.

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
- **함수 경계**: Python `complexity`와 같은 per-function AST 경계를 사용해 중첩 scope 본문과
  바깥 loop state가 현재 함수로 새지 않게 하며, 정의 시점 표현식은 보존합니다.
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
  파일을 가리킬 때만 보정합니다. DB digest는 preflight가 immutable context로 캡처한 snapshot을
  식별하며 live-file lease가 아니다. DB가 변경되면 실행 중 context를 바꾸지 않고 다음 preflight에서
  새 bytes와 context를 반영합니다.

---

> **다음 단계**
