# Python·C++·Qt 코드 품질 분석기 마스터 실행 계획

**상태:** 승인된 장기 마스터 계획. 2026-08-30 이후 ici 기능 계획의 우선순위와 완료 조건은 이 문서를 기준으로 판단한다.
**문서 기준일:** 2026-08-31. 이 계획은 ici [PR #78](https://github.com/jihoon22-lee/ici/pull/78)로 `main`에 병합됐고, 현재 상태는 이 체크리스트와 병합된 PR의 실측을 함께 기준으로 삼는다.

**목표:** ici를 Python, C++, Qt 프로젝트의 실제 빌드·테스트·배포 맥락을 이해하고, 위치와 근거가 있는 상세 진단을 낮은 노이즈로 제공하는 고신뢰 코드 품질 분석기로 발전시킨다.

**제품 원칙:** 엔진 수보다 판정 신뢰도가 우선이다. 실행하지 못한 검사를 통과로 보이지 않게 하고, 휴리스틱은 휴리스틱이라고 표시하며, 외부 도구의 원시 진단은 하나의 안정적인 finding 계약으로 정규화한다.

**대응 toy 계획:** [`toy-projects 제품 포트폴리오 마스터 계획`](https://github.com/jihoon22-lee/toy-projects/blob/main/docs/superpowers/plans/2026-08-30-product-portfolio-master-plan.md). ici 기능은 단위 fixture만으로 완료하지 않고 대응하는 실물 프로젝트 또는 `quality-zoo` 시나리오에서 검증한다.

---

## 1. 이 계획이 대체하고 보존하는 것

- `2026-08-30-viewer-qt-tests-and-include-resolution.md`는 **I0의 세부 실행 입력**으로 보존한다. 다만 아래 세 가지 보정이 우선한다.
  - suffix 기반 include 해석은 중간 개선이며 compile context 기반 해석이 끝날 때까지 B-2를 완전히 닫지 않는다.
  - viewer 실패 테스트는 정상 보고서를 먼저 연 뒤 missing/malformed 보고서를 열어 stale state 제거를 검증한다.
  - GUI를 루트 CMake에 넣더라도 Qt 없는 환경에서 CLI configure/build가 가능해야 한다.
- `2026-08-19-ci-validation-features.md`의 완료된 CMake/qmake adapter 작업은 반복하지 않는다. 남은 toolchain, compile DB, Python compatibility, binary compatibility, integration 설계는 I2·I3·I5·I7로 재배치한다.
- `2026-08-19-existing-validation-hardening.md`와 `2026-08-29-cmake-qmake-build-adapter.md`는 완료 이력과 회귀 근거로 유지한다.
- 체크박스가 남았다는 이유만으로 과거 계획을 재실행하지 않는다. 최신 마스터 계획, 인수인계서, Git 이력을 함께 본다.

### 1.1 I0-1 상태 기준 (2026-08-31)

아래 표는 문서 감사 시점의 GitHub `main` 사실을 고정한 기록이다. 로컬 worktree의
unpushed branch나 커밋 수를 완료 근거로 사용하지 않으며, 이후 상태는 각 PR과 그 PR의
Merge Gate를 다시 확인한다.

| 저장소 | 기준 `main` | 이 계획과 직접 관련된 병합 사실 |
|---|---|---|
| ici | [`fa3ad28`](https://github.com/jihoon22-lee/ici/commit/fa3ad28dabccac840d2e19246ccf35d8cb30182b) | 계획 #78, cycle #79, CI/리포트 gate #80, viewer Qt 셸 #81, release provenance #82 |
| toy-projects | [`f267695`](https://github.com/jihoon22-lee/toy-projects/commit/f26769527fa7443aa56cdea693fba278000f0816) | 제품 계획 #12, manifest/report gate #13, loglens state #14, T0-1 계획·환경 #15, diskmap Qt 셸 #16 |

현재 환경에는 Qt 5.15.18과 Qt 6.10.2가 모두 설치돼 있다. ici viewer는 [PR #81](https://github.com/jihoon22-lee/ici/pull/81)에서 Qt 5·Qt 6 각각 4/4 CTest와 Qt-free CLI를 통과했고, toy 쪽 환경·실측 기록은 [PR #15](https://github.com/jihoon22-lee/toy-projects/pull/15)에 있다. 따라서 과거 문서의 “Qt 5 미설치/미검증” 또는 “아직 푸시되지 않음” 표현은 현재 상태가 아니다.

---

## 2. 현재 기준선과 문제 정의

2026-08-31 실측 기준 (`origin/main@fa3ad28`, self-quality annotation 반영):

| 대상 | 결과 |
|---|---|
| ici pytest | 634/634 PASS |
| ici self verify | Pass 8 · Warn 4 · Fail 0 · Error 0, TEM 4.78 |
| line/function/branch | 85.9% / 95.7% / 77.9% |
| self verify console / duplicate | 2,276 lines / 237 groups (3회 반복 동일) |
| viewer | 4/4 PASS, TEM 4.86 |

현재 강점:

- Python 3.10+ 단일 `ici.pyz`와 재현 가능한 빌드
- Python/C++ 프로젝트 탐지와 CMake/CTest·qmake/Make adapter
- build/test/sanitize의 실제 프로젝트 빌드 정의 사용
- JSON/HTML/Markdown/console reporter 분리
- `ToolEvidence`, `EvidenceState`, 파일·행 위치 계약
- 실물 Qt 프로젝트에서 발견한 adapter 회귀를 포함한 634개 테스트

현재 핵심 한계:

- `InspectionTarget`에 안정적인 rule id, confidence, fingerprint, related location, remediation이 없다.
- 엔진 목록과 실행 순서가 orchestrator에 하드코딩돼 있고, build/test/sanitize가 공유할 분석 맥락이 없다.
- C++ lint가 실제 번역 단위 명령 대신 고정 `g++ -std=c++17` 명령을 만든다.
- C++ type, dead, cognitive, resource 분석은 구현되지 않았거나 Python 전용이다.
- C++ cycle은 텍스트 include와 basename 휴리스틱이며 실제 `-I` 순서를 모른다.
- C++ 함수 coverage 위치가 gcov text 한계 때문에 1행으로 기록된다.
- security/resource 일부 규칙은 단순 정규식·얕은 AST임에도 분석 정밀도를 별도로 표현하지 못한다.
- duplicate와 console drill-down이 결과를 과도하게 펼쳐 issues-first 불변식을 위반한다.
- self gate 임계값이 실제 baseline보다 지나치게 낮다.

---

## 3. 목표 아키텍처

```text
Project discovery
    ↓
ProjectModel + CapabilityInventory
    ↓
BuildSession / ArtifactManifest / CompilationContext
    ↓
Engine DAG (Python / C++ / Qt / cross-language)
    ↓
Normalized Finding + Measurement
    ↓
Policy evaluation + baseline/delta
    ↓
Console / JSON / HTML / Markdown / SARIF / viewer
```

### 3.1 분석과 정책을 분리한다

- analyzer는 관측 사실과 confidence를 반환한다.
- policy는 severity, required 여부, threshold, baseline을 적용한다.
- reporter는 결과를 표시할 뿐 상태를 다시 계산하지 않는다.
- 도구 미설치, 프로젝트 부적용, 분석 실패, 실제 무결함을 서로 다른 상태로 유지한다.

### 3.2 pyz와 외부 도구의 경계

- pyz에는 계속 pure-Python, `py3-none-any` 의존성만 포함한다.
- Ruff, mypy, compiler, clang-tidy, clazy, gcov, binutils 같은 도구는 설치된 실행 파일로 탐지한다.
- 외부 도구의 path, version, argv, return code, timeout, output truncation을 모두 보존한다.
- 도구가 없을 때 fallback을 쓸 수 있지만 confidence와 evidence를 낮추고, required 정책이면 게이트를 막는다.
- 네트워크 설치나 자동 다운로드를 시도하지 않는다.

### 3.3 실물과 의도된 결함을 함께 쓴다

- green real project는 false positive, build integration, 성능과 사용성을 검증한다.
- `quality-zoo`의 red scenario는 false negative, rule id, 위치와 remediation을 검증한다.
- 신규 엔진은 둘 중 하나만 통과해서 완료되지 않는다.

---

## 4. 공통 구현 불변식

- 모든 변경은 별도 브랜치와 PR로 진행한다. 아래 PR 표의 브랜치명은 권장 이름이다.
- 의미 있는 단위가 끝날 때마다 Conventional Commit을 만든다.
- 기능·정책·스키마 변경마다 `CHANGELOG.md`, engine reference, user guide를 동기화한다.
- Python 3.10 문법 하한과 `tomli`/`tomli-w` 사용을 유지한다.
- command는 shell string이 아니라 argv로 실행하고 secret을 reporter에 노출하지 않는다.
- 모든 finding은 project-relative primary path와 1-indexed line을 가진다. 프로젝트 전체 정책은 `.`/1과 명시적 policy target을 쓴다.
- 결과 순서, fingerprint와 JSON 직렬화는 재현 가능해야 한다.
- HTML은 Zero-CDN을 유지한다.
- default console은 요약과 제한된 actionable finding만 출력한다. 전체 inventory는 구조화 reporter에 남긴다.
- 같은 shadow build directory를 여러 엔진이 동시에 수정하지 않는다.
- 각 PR은 관련 단위 테스트, E2E fixture, 대응 toy 검증 중 적용 가능한 것을 포함한다.
- 품질 게이트는 AGENTS.md의 pytest, Ruff, pyz build, smoke, self verify 전체다.

---

## 5. 전체 작업 흐름

| 단계 | ici 결과물 | 대응 실물 검증 | 선행 단계 |
|---|---|---|---|
| I0 | 현재 계획 보정, viewer와 cycle 안전망 | viewer, loglens, diskmap | 없음 |
| I1 | finding v3, baseline, issues-first | viewer, quality-zoo | I0 |
| I2 | capability와 공유 실행/산출물 맥락 | 기존 3개 앱 | I1 |
| I3 | compile context와 정확한 C++ scope | buildscope | I2 |
| I4 | C++·Qt 정밀 분석 | buildscope, loglens, diskmap | I3 |
| I5 | Python 정밀 분석과 runtime/package 호환성 | envlens, buildscope | I1·I2 |
| I6 | coverage·test quality 고도화 | 전 프로젝트, quality-zoo | I2·I3 |
| I7 | Makefile·artifact·ABI·hybrid integration | abilens, buildscope | I2·I3·I5 |
| I8 | report workbench와 표준 출력 | viewer | I1 이후 점진적 |
| I9 | 성능·회귀 corpus·1.0 readiness | 전체 portfolio | I3~I8 |

I1 이후 Python과 C++ 작업은 병렬 가능하지만, 각 언어 안에서는 compile/runtime context가 개별 analyzer보다 먼저다.

---

## 6. I0 — 현재 공백을 정확한 테스트로 닫는다

### I0-1. 계획 보존과 상태 정리

**브랜치:** `docs/quality-plan-status`

- [x] 이 마스터 계획과 toy master plan을 각각 문서 PR로 병합한다.
- [x] 인수인계서의 stale commit count와 Qt 5 미설치 정보를 고친다.
- [x] 과거 계획 상단에 완료·보류·보정 관계를 표시한다.
- [x] GitHub의 main에서 모든 계획 링크가 실제로 열린다는 것을 확인한다.

**완료 조건:** 새 세션이 과거 체크박스를 활성 작업으로 오인하지 않는다.

### I0-2. cycle path suffix 개선을 중간 휴리스틱으로 구현

**브랜치:** `fix/cycle-include-path`

- [x] 같은 basename을 가진 두 헤더와 directory-qualified include 회귀 테스트를 추가한다.
- [x] path suffix로 유일하게 결정되는 include만 연결한다.
- [x] bare basename 충돌은 계속 추측하지 않는다.
- [x] unresolved/ambiguous include 수와 예시 위치를 result extra와 target에 남긴다.
- [x] evidence/confidence가 compiler-exact가 아님을 문서화한다.
- [x] Python import graph와 C++ E2E 회귀를 함께 실행한다.

**완료 조건:** 탐지력 저하가 조용히 사라지지 않으며, I3 이전 임시 방식임이 명확하다.

### I0-3. viewer Qt 셸과 실패 상태를 검증

**브랜치:** `test/viewer-qt-shell`

- [x] root CMake가 `ICIRV_BUILD_GUI` 옵션으로 Qt GUI를 선택적으로 구성한다.
- [x] `ICIRV_BUILD_GUI=OFF`에서 Qt가 없는 configure와 static CLI 빌드 계약을 테스트한다.
- [x] GUI library와 executable을 분리해 QtTest가 MainWindow를 링크한다.
- [x] 정상 보고서 → missing report 순서에서 model, suite, labels, title이 초기화되는지 테스트한다.
- [x] 정상 보고서 → malformed report도 동일하게 테스트한다.
- [x] Qt 6과 `CMAKE_DISABLE_FIND_PACKAGE_Qt6=ON` Qt 5 빌드를 각각 검증한다.
- [x] viewer `ici.toml`에 Qt5/Qt6 pkg-config scope를 기록한다.

**완료 조건:** Qt-free CLI 요구를 유지하면서 4개 이상 CTest가 통과하고 GUI failure state에 stale data가 없다.

### I0-4. self gate를 현재 사실에 맞게 정리

**브랜치:** `chore/self-quality-baseline`

- [x] mypy note의 원인이 되는 untyped function body를 식별한다.
- [x] 즉시 무리 없이 올릴 수 있는 TEM/branch/function floor를 실측한다.
- [x] 변경 이유와 다음 ratchet 조건을 `ici.toml`에 기록한다.
- [x] 결과 출력 줄 수와 duplicate group 수를 I1 성능 기준선으로 저장한다.

**초기 목표:** TEM 4.5, branch 70% 이상, function 90% 이상을 검토하되 측정값과 변동 근거 없이 숫자만 바꾸지 않는다.

**실측 완료:** `ici.toml`에 TEM `4.5`, branch `70%`, function `90%` floor를
설정했다. 세 번의 self verify가 동일하게 TEM `4.78`, branch `77.9%`, function
`95.691%`, console `2,276`줄, duplicate `237` groups를 기록했으며,
[`docs/baselines/2026-08-31-self-quality.json`](../../baselines/2026-08-31-self-quality.json)에
구조화해 저장했다. mypy `[annotation-unchecked]` note는 네 엔진 생성자의
untyped body에서만 발생했고 모두 Python 3.10 호환 시그니처로 정리했다.

---

## 7. I1 — finding 계약과 낮은 노이즈

### I1-1. `ici.result/v3` finding 계약

**브랜치:** `feat/finding-contract-v3`

다음 구조를 설계하고 JSON schema와 dataclass를 함께 제공한다.

- `rule_id`: 도구 버전과 무관한 ici namespace id
- `category`: correctness, type, security, resource, build, test, maintainability 등
- `severity`: info, low, medium, high, critical 또는 정책에 대응하는 안정 enum
- `confidence`: exact, high, medium, low
- `fingerprint`: rule, canonical path, symbol/region을 사용한 안정 hash
- `primary_location`: path, start/end line과 column
- `related_locations`: include edge, clone occurrence, caller/callee 등
- `message`, `explanation`, `remediation`
- `tool_rule_id`, `tool_name`, `tool_version`
- `suppression`: inline/config/baseline 여부와 근거
- `metrics`: 숫자형 측정값과 단위

- [x] v2 reader와 viewer의 backward compatibility를 유지한다.
- [x] v2→v3 migration 테스트를 만든다.
- [x] path separator와 checkout root가 달라도 fingerprint가 안정적인지 테스트한다.
- [x] secret redaction이 message, snippet, tool output, remediation 전부에 적용되는지 테스트한다.
- [x] 모든 engine의 legacy target을 adapter로 v3 finding으로 옮긴 뒤 점진적으로 native v3를 발행한다.

### I1-2. support/capability matrix

**브랜치:** `feat/engine-support-matrix`

- [x] 엔진별 지원 언어, exact/heuristic/tool-backed mode, 필요한 도구와 fallback을 선언한다.
- [x] project discovery 후 적용 가능한 mode를 계산한다.
- [x] NOT_APPLICABLE, NOT_RUN, ESTIMATED와 confidence를 일관되게 집계한다.
- [x] doctor, JSON, HTML, viewer에서 같은 matrix를 표시한다.
- [x] 문서의 B-3 언어 지원 범위를 실제 선언에서 생성하거나 검증한다.

**완료 측정(2026-08-31):** 13개 엔진 × Python/C++ 26개 선언·평가 행, v3 object/null 호환
직렬화, doctor/JSON/HTML 및 viewer 표시 경로를 연결했다. 최종 `dist/ici.pyz` self-verify는
WARN(Pass 8/Warn 4/Fail 0/Error 0), 672/672, TEM 4.81, line/branch/function
87.2%/78.7%/96.1%였고, doctor renderer complexity 초과는 `e65c742`에서 helper로 분리해
해결했다. 실제 v3 JSON의 Draft 2020-12 검증, 26 unique rows, doctor/static declaration
exact match, zero-CDN HTML support tab 및 viewer CLI parse도 확인했다.

### I1-3. baseline과 delta gate

**브랜치:** `feat/finding-baseline`

- [x] 이전 v3 JSON을 baseline으로 읽는다.
- [x] new, unchanged, moved, resolved를 fingerprint와 location 보조 정보로 구분한다.
- [x] 전체 inventory와 PR gate 대상인 new/regressed finding을 분리한다.
- [x] baseline schema/version/tool policy 불일치를 경고한다.
- [x] baseline이 현재 프로젝트 밖 경로를 참조하지 못하게 한다.
- [x] `--baseline`, `--fail-on-new`, `--write-baseline` CLI와 문서를 추가한다.

**완료 측정(초안, 2026-08-31):** v3 baseline reader와 delta model, actionable gate,
compatibility warning, root-contained path validation, CLI 옵션 및 report parity를 구현했다.
new·unchanged·moved·resolved 전체 inventory와 new/regressed gated subset을 분리하고,
duplicate fingerprint는 occurrence 단위로 비교한다. console/Markdown/HTML/JSON과 GitHub
single·multi-project sticky comment에서 같은 baseline summary를 확인하도록 연결했다.

- 단위·회귀 테스트: Python 3.10 전체 732개 통과. baseline 집중 경로는
  tests/test_baseline.py, tests/test_verify_orchestrator.py,
  tests/test_baseline_reporters.py, tests/test_cli.py, tests/test_publish.py가 담당한다.
- PR: [#87](https://github.com/jihoon22-lee/ici/pull/87) — finding baseline/delta gate 병합 완료
- CI/Merge Gate: [workflow run 33327928094](https://github.com/jihoon22-lee/ici/actions/runs/33327928094) — Verify, Viewer Qt5, Viewer Qt6, Publish PR Report, Merge Gate 모두 SUCCESS
- 로컬 release-candidate pyz self-verify: WARN(Pass 8/Warn 4/Fail 0/Error 0), 732/732,
  TEM 4.82, line/branch/function 87.6%/78.6%/96.5%. 동일 소스 baseline 왕복에서
  2,783 unchanged, new/moved/resolved/regressed/gated 0, fail-on-new PASSED를 확인했고,
  v3 Draft 2020-12 schema, 0 external reference zero-CDN HTML, publish summary, 기존 C++
  viewer parse를 모두 통과했다.
- CI runner의 동일 검증 측정값은 line/branch 87.7%/78.7%였다. 로컬
  release-candidate의 87.6%/78.6%와의 차이는 코드 불일치가 아니라 실행 환경별
  coverage 측정치 차이로 기록한다.

I1-1~I1-4 기능 구현과 로컬 품질 검증이 완료되어 I1 전체 checkpoint를 완료로 표시한다.
I1-4의 [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
[`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
병합됐고 [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의 모든 required checks가
green(756 tests)이었다.

### I1-4. issues-first console과 공통 grouping

**브랜치:** `fix/console-issues-first`

- [x] 기본 출력은 engine summary와 엔진별 최대 5 display group만 표시한다.
- [x] 전체 actionable finding 수, 표시 group 수, 숨긴 수와 재출력 명령을 명시한다.
- [x] clone occurrence를 같은 실행의 같은 clone group 안에서 같은 파일의 겹치는 region만
  표시 병합하고 원본 occurrence와 fingerprint를 보존한다.
- [x] `verify` 전용 `--verbose`, `--max-findings`, `--group-by`를 추가했다. `--verbose`는
  cap을 해제하고 `--max-findings 0`은 summary만 표시한다.
- [x] `engine|severity|category|file|rule` 5종 grouping과 80-column terminal golden 회귀를
  검증했다.
- [x] console cap과 관계없이 JSON·HTML·Markdown·baseline 원본 inventory를 보존한다. HTML
  `Issues` 탭도 native v3 finding inventory를 기반으로 전체 결과를 표시한다.

**완료 측정(최종 안정 로컬 검증, 2026-08-31):** 구현·테스트 기준 commit은 `814679c` +
`d80a027`이다. 현재 Python 3.10 전체 품질 게이트 756/756, focused console 테스트 16개,
Ruff check/format, pure-Python 10-distribution·no-certifi·2.0 MiB pyz 빌드, smoke 전체
검증을 통과했다. built `dist/ici.pyz`는 exit 0으로 실행됐고 suite는 WARN이었다. self
verify 출력은 144 lines/15,288 bytes, HTML은 3,383,523 bytes이며, 해당 출력에 내장된 test
engine 수치는 756/756이다. local self verify coverage는 line/function/branch 87.8%/96.6%/78.8%, TEM은
4.83이었다. engines는 Pass 8/Warn 4/Fail 0/Error 0/Skip 0, complexity는 최대 23·이슈
64건, duplicate는 16.2%·338 groups·1,006 actionable occurrences였다.

콘솔 측정은 actionable 1,088건, visible 21/420 display groups, represented 34,
hidden 1,054 findings/399 groups였다. HTML clone group card는 정확히 338개, issue engine
row 합계는 1,088개였고 external script/stylesheet reference는 0개였다. 초기 self 측정의
lint 실패는 에이전트 파일 작성 경합에 따른 참고 기록이며, 위 최종 안정 측정을 기준으로 한다.

**완료 조건:** 구현, 회귀 테스트, 전체 로컬 품질 게이트와 안정 self verify를 모두 충족했다.
기본 cap·summary-only·verbose·5종 grouping과 80-column 표시를 확인했고, console projection과
무관하게 JSON·HTML·Markdown·baseline 원본 inventory를 보존했다. I1-4와 I1 로컬 checkpoint는
완료다. [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
[`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
병합됐다. [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의 모든 required checks가
green(756 tests)이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/89#issuecomment-5470778278)에
결과가 기록됐다. CI report stats는 ici WARN(TEM 4.83, Pass 8, Warn 4, line 87.8%, function
96.6%, branch 78.9%), viewer PASS(TEM 4.89, 7/7 tests)였다. [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/89/)
는 HTTP 200·external script/stylesheet refs 0, [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/89/)
는 HTTP 200·external refs 0이었다.

---

## 8. I2 — capability, 실행 그래프와 공유 산출물

### I2-1. capability inventory

**브랜치:** `feat/tool-capabilities`

- [x] Slice 1 — bounded probe/parser registry
- [x] Slice 2 — `doctor` shared inventory, redaction, and policy
- [x] Slice 3 — `verify`/report shared inventory

> Slice 3 구현은 `feat/capability-reporting`에서 완료됐다. 이 문서의 구현 체크리스트는
> 완료됐지만, 최종 full quality gate·CI·PR/Pages 게시 증거는 main 통합 검증에서
> 보강한다.

- [x] Python interpreters, compilers, CMake, qmake, make, gcov, clang tools, Qt, binutils를 탐지한다.
- [x] version parser는 vendor suffix와 multi-line 출력을 견딘다.
- [x] compiler target triple, Qt major, generator와 지원 feature를 기록한다.
- [x] required/optional 도구 정책을 유효한 support matrix의 `applicable`·`enabled` 행과 `[doctor].required_tools`에서 계산한다. required provenance가 optional보다 우선하지만 모든 provenance를 보존한다.
- [x] `ici doctor --json`은 전체 bounded registry와 redacted evidence를 담은 `capability_inventory`를 제공하고, 기존 `tools` map을 유지한다. active support-matrix 행과 `[doctor].required_tools`의 provenance도 기록한다.
- [x] verify는 엔진 실행 전에 정책을 계산하고 bounded registry를 정확히 한 번 수집해 suite-level immutable `CapabilityInventory`로 공유한다. console/Markdown/zero-CDN HTML/JSON reporter는 이 snapshot을 재사용하며, JSON root의 선택적 `capability_inventory`와 checked-in schema는 inventory 없는 기존 v3 리포트와 호환된다.

### I2-2. `AnalysisContext`와 artifact manifest

**브랜치:** `refactor/analysis-context`

- [x] `ProjectModel`, `CapabilityInventory`, `BuildSession`, `ArtifactManifest`, `CompilationContext`의 소유권을 정의한다.
- [x] artifact path는 project/shadow root 아래인지 검증하고 symlink escape를 거부한다.
- [x] source commit, config digest, toolchain digest를 manifest에 기록한다.
- [x] test/sanitize/coverage가 필요한 build variant를 명시적으로 요청한다.
- [x] reporter는 context를 변경하지 못한다.

**완료 기록 (2026-08-31):** `ProjectModel`은 canonical root와 project-relative source,
header, include scope를 한 번만 발견해 frozen tuple snapshot으로 소유한다. 이미 수집된
`CapabilityInventory`는 `AnalysisContext`에 그대로 전달되고, `CompilationContext`는 compile
unit의 상대 source/directory/argv/output을 immutable tuple로 보존한다. adapter가 configure,
build, test 중 변경하는 상태는 mutable `BuildSession`에만 두며, 성공한 project/shadow regular
file은 frozen `ArtifactManifest`로 발행한다. manifest는 canonical containment와 symlink
escape를 검사하고 variant, producer, source/config/toolchain identity, SHA-256·size·mode를
기록한다. `source_commit`은 Git HEAD 또는 명시적 `unavailable`이고 config/toolchain은
canonical SHA-256 digest다.

`RELEASE`, `COVERAGE`, `SANITIZE`를 명시적으로 선택해 shadow와 계측 flags를 분리했으며,
build/test/sanitize가 같은 context snapshot을 공유한다. console·Markdown·HTML·JSON
reporter는 context를 변경하지 않고 projection을 만든다. `ici.result/v3`의 optional
`analysis_context` (`ici.analysis-context/v1`)와 `artifact_manifests`
(`ici.artifacts/v1`)는 project-relative POSIX 경로와 전체 provenance를 보존하고, 외부
include/search path는 context JSON에서 `-I[external]`로 치환한다. 기존 HTML editor-link와
tool evidence의 path/redaction 계약은 변경하지 않는다. 기존 v3 payload는 두 확장 없이도
계속 읽고 migration할 수 있다. 전체 품질 게이트의 병합
조건은 full suite green이며, 작업 중인 테스트 수는 이 문서에 고정하지 않는다.

I2-2는 완료됐다. I2-3 선언형 verification pipeline 구현도 `refactor/verification-pipeline`에서
완료됐다. I2-4의 cache key·local cache·CLI·report contract 구현과 사용자 문서화는
`feat/analysis-cache` 작업대에 반영했고 pyz 재현성·비변경 로컬 게이트도 통과했다. PR·CI·
Pages·release evidence는 아직 남아 있다.

### I2-3. hardcoded loop를 의존성 그래프로 교체

**브랜치:** `refactor/verification-pipeline`

- [x] engine descriptor에 `name`, `dependencies`, `produces`/`consumes`,
  `profiles`, `execution`, `build_variant`를 선언한다.
- [x] startup에서 dependency/artifact 계약과 cycle이 없는 DAG인지 검증한다.
- [x] 독립적인 read-only engine만 기본 최대 4개로 제한 병렬 실행하고, 결과를 registry 순서로
  반환한다.
- [x] build variant를 쓰는 engine은 read-only 작업 및 다른 build node와 겹치지 않게 직렬화한다.
- [x] engine 초기화·실행 crash가 context를 훼손하지 않고 명시적 `ERROR`/`NOT_RUN` 결과가
  되도록 격리한다.
- [x] `fast`, `standard`, `deep` profile을 추가하되 profile은 engine selection만
  바꾸고 동일 rule의 threshold·의미는 변경하지 않는다. JSON `analysis_context.profile`은
  optional로 유지해 기존 v3 payload와 호환한다.

I2-3은 [PR #96](https://github.com/jihoon22-lee/ici/pull/96)으로 병합됐다. 최종
[CI run 33343118306](https://github.com/jihoon22-lee/ici/actions/runs/33343118306)에서
898 tests, C++ detection fixtures, reproducible pyz build/smoke, ici/viewer dogfood,
Qt5·Qt6 GUI build, PR report 게시와 Merge Gate가 모두 통과했다. 실제
[sticky comment](https://github.com/jihoon22-lee/ici/pull/96#issuecomment-5472080848)는
ici WARN(TEM 4.84)과 viewer PASS(TEM 4.89)를 게시했고, 두 Pages는 HTTP 200·`text/html`·
외부 script/stylesheet 참조 0건이었다. 병합 commit은 `edd775ac192baea4f9ce7dad882ab8e090d9c065`다.

### I2-4. 캐시와 재현성

**브랜치:** `feat/analysis-cache`

- [x] cache key에 project root, source/build-config content, effective ici config, toolchain
  version, engine implementation, build variant, ici version을 포함한다.
- [x] 완료된 `PASS`/`WARN`/`FAIL`은 유효한 증거라면 cache할 수 있고,
  `ERROR`/`SKIP`/`NOT_RUN`, timeout/truncated output/tool error 및 invalid artifact는
  성공 cache로 재사용하지 않는다.
- [x] `--no-cache`, `ici cache` inventory/`--clear`, cache key invalidation과 local-only
  atomic entry 경계를 사용자 문서에 설명한다.
- [x] engine-level report에 optional `cache_hit`와 nullable `cache_key`를 기록하면서
  기존 v3 archive 소비자와 호환한다.
- [x] pyz 재현성과 프로젝트 파일 비변경 불변식을 테스트한다.

구현은 `cache.py`, `cache_identity.py`, `cache_codec.py`, `VerifyOrchestrator`, CLI, v3 JSON
schema에 분리했다. cache는 user-local `entries-v1` 아래에만 atomic write를 수행하고 project
source/config는 읽기 전용으로 digest한다. 입력을 해시하는 동안 파일 변경을 감지하면 해당
실행의 cache를 끄며, artifact manifest도 저장·조회 경계에서 다시 검증한다.

로컬 Python 3.10 전체 테스트는 935개가 통과했다. `standard` 최초 실행은 118.49초·hits 0,
동일 입력 재실행은 2.38초·hits 12였으며 cache metadata를 제외한 result SHA-256
`95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`와 finding 3,497건이
일치했다. HTML은 4,095,550 bytes·외부 참조 0건이었다. pyz 두 빌드는 SHA-256
`6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`으로 일치했고 project
source status unchanged 및 smoke 전체 통과를 확인했다. I2-4는 PR #97, merge commit
`ef30059522729b376c5409e5bb49164aa538b128`, CI run `33345993304`, sticky comment
`5472411964`와 ici/viewer Pages 게시까지 완료됐다.

---

## 9. I3 — compile context를 C++ 분석의 단일 진실로 만든다

### I3-1. compilation database model과 검증 엔진

**브랜치:** `feat/compile-db-context`

- [x] `arguments`를 우선하고 `command`는 플랫폼별 안전한 parser를 통해 읽는다.
- [x] directory, file, output과 여러 configuration의 동일 source를 보존한다.
- [x] project-relative/absolute canonical path와 symlink 경계를 검증한다.
- [x] compiler, language, standard, defines, include/search path, sysroot를 구조화한다.
- [x] 전체 production translation unit이 DB에 포함되는지 검사한다.
- [x] stale/missing source, 존재하지 않는 include dir, 금지·필수 flag를 finding으로 만든다.

**I3-1 로컬 완료(2026-08-31):** bounded descriptor read와 strict JSON, POSIX/MSVC
metadata, project-contained response file, immutable context, `compile_db` engine, cache key v2,
report redaction과 v3 schema를 함께 구현했다. compile_db loader는 facade
`src/ici/core/compile_db.py`와 `_compile_db_paths.py`, `_compile_db_commands.py`,
`_compile_db_metadata.py`로 분리했으며, 네 모듈은 각각 순수 코드 500줄 미만이다. compile_db
범위의 최종 line·type·high-complexity 이슈는 0건이다.

Python 3.10 focused 109 tests와 full suite 1,032 tests(46.29s)가 통과했고, Ruff
check/format은 127 files에서 통과했으며 focused mypy도 clean이었다. reproducible pyz 두
빌드의 SHA-256은 `408fcd0fcf153b5e63927d10d34d55cea680eb472dc6f0e95bf174efcf6e8b36`으로
일치했고 pure-Python 10 distributions/no certifi, smoke와 Zero-CDN도 PASS였다. 최종
`--no-cache` self verify는 WARN(13 total: Pass 8, Warn 4, Fail 0, Error 0, Skip 1),
compile_db `SKIP`/`NOT_APPLICABLE`(Python-only), test 1,032/1,032, coverage
line/function/branch 88.6%/97.1%/79.6%, TEM 4.86, cache hits 0, 109.26s, HTML
4,627,454 bytes였으며 compile_db-specific high-complexity/line-threshold/type issues는
0건이다. 이 문단은 로컬 증거만 기록하며 PR·CI·Pages 증거는 아직 pending이다. CMake DB
생성·qmake capture·lint/include graph 이관(I3-2~I3-4)은 포함하지 않는다.

### I3-2. CMake compile DB 생성

**브랜치:** `feat/cmake-compile-db`

- [ ] adapter configure에 `CMAKE_EXPORT_COMPILE_COMMANDS=ON`을 넣는다.
- [ ] Makefile/Ninja generator 제약과 unity build를 탐지한다.
- [ ] coverage/sanitize/release variant 중 analyzer에 사용할 canonical DB 정책을 정한다.
- [ ] generated source가 build 전 필요한 경우 generation 단계 후 DB를 소비한다.
- [ ] buildscope와 viewer에서 실제 target별 명령을 대조한다.

### I3-3. qmake compile capture

**브랜치:** `feat/qmake-compile-context`

- [x] qmake configure 뒤 shadow에서 deterministic `make clean`을 먼저 실행하고,
  clean evidence와 실패 사유를 결과에 남긴다. 재사용한 qmake shadow에서 정적 archive와
  test executable의 freshness가 어긋나 stale 실행 및 gcov stamp 불일치가 coverage 0%로
  둔갑하지 않게 하는 선행 안전망이며, CMake build path는 변경하지 않는다. 이 체크는
  compile capture 완료를 의미하지 않는다.
- [ ] qmake verbose build, compiler wrapper, 선택적 외부 capture 도구를 실측 비교하는 spike를 먼저 한다.
- [ ] shell parsing이나 임의 command 실행 없이 argv를 보존할 수 있는 방식을 채택한다.
- [ ] Qt5/Qt6, target wrapper, shadow relative path를 fixture와 diskmap에서 검증한다.
- [ ] exact capture가 불가능한 환경은 명시적 lower-confidence mode로 남긴다.

**Freshness 선행 안전망의 최종 로컬 증거 (2026-08-31):** rebase된 test/fix 커밋
`1098a62`/`f692a3c` 기준으로 Python 3.10 `pytest 811 passed (42.79s)`, Ruff
check/format 103 files, reproducible pyz 두 회의 동일 SHA-256
`8fdb816ae394e5327ffa6f6ca6ddc0efca0a45addb48975e3b8eef6412a39018`, pure-Python
10 distributions/no certifi, smoke(Python 3.10·integrity·Zero-CDN 포함) PASS를
확인했다. 같은 candidate pyz를 실제 DiskMap에 적용한 결과는 Suite PASS
(10 pass, 0 warn, 0 fail/error, 2 skip; 9/9 tests; line/function/branch
96.6%/98.0%/85.0%; TEM 4.90; complexity 14/101/0 issues; duplicate 2.0%;
sanitizer clean, 85.96s)였고, test와 sanitize qmake build JSON evidence 양쪽에
`/usr/bin/make clean` 성공이 남았다. capability snapshot은 30 tools/21 ready/
0 incomplete/9 unavailable이며 required `g++`는 ready/health READY였다. HTML은
281,264 bytes, external `src`/`href` reference 0개이고 capability snapshot을
렌더링했다. 이 기록은 qmake compile capture 완료가 아니며, 원격 CI·PR·Pages 및
main 반영은 후속 검증에서 완료해야 한다.

### I3-4. lint와 include graph 이관

**브랜치:** `refactor/cpp-analysis-context`

- [ ] C++ lint가 고정 `-std=c++17` 명령을 만들지 않고 translation unit command를 재생한다.
- [ ] compile-only/output/dependency flags를 안전하게 정리한다.
- [ ] compiler의 dependency output으로 실제 include edge와 resolved path를 수집한다.
- [ ] generated/system/third-party header 포함 정책을 구분한다.
- [ ] suffix fallback은 DB가 없는 heuristic mode로만 유지한다.
- [ ] include ambiguity와 미해석 edge를 보고한다.

**완료 조건:** buildscope에서 source별 define·standard·include가 실제 build와 일치하고, 같은 basename header가 compiler 선택과 같은 edge로 연결된다.

---

## 10. I4 — C++·Qt 정밀 분석

### I4-1. compiler/clang-tidy adapter

**브랜치:** `feat/cpp-static-analysis`

- [ ] compiler JSON/text diagnostics를 stable rule과 정확한 location으로 파싱한다.
- [ ] clang-tidy가 있으면 compilation DB를 사용해 선택 check를 실행한다.
- [ ] Clang Static Analyzer check를 별도 category로 구분한다.
- [ ] tool config와 project `.clang-tidy`를 존중하고 ici override 우선순위를 문서화한다.
- [ ] fix-it은 report에 제안으로 보존하되 기본 실행에서 소스를 수정하지 않는다.
- [ ] tool 부재와 compile failure를 분석 무결함으로 처리하지 않는다.

### I4-2. Qt clazy와 생성 단계

**브랜치:** `feat/qt-analysis`

- [ ] clazy standalone/tool plugin capability를 탐지한다.
- [ ] level0/level1 기본 profile과 opt-in noisy checks를 나눈다.
- [ ] Q_OBJECT, signal/slot, object lifetime, container detach, temporary 사용 finding을 정규화한다.
- [ ] AUTOMOC/AUTOUIC/AUTORCC 또는 qmake의 moc/uic/rcc 산출물 존재와 build 연결을 검증한다.
- [ ] Qt major별 API compatibility를 compile evidence로 확인한다.
- [ ] buildscope의 `.ui`/`.qrc`, 기존 앱의 Q_OBJECT 경로로 검증한다.

### I4-3. maintainability 분석 정확도

**브랜치:** `refactor/cpp-maintainability`

- [ ] complexity/cognitive/function boundary를 AST/tool output 우선으로 바꾼다.
- [ ] template, lambda, operator, macro-generated code 처리 정책을 정한다.
- [ ] dead/unused symbol은 compiler/linker/clang-tidy evidence가 있을 때만 exact로 표시한다.
- [ ] duplicate는 generated/moc/vendor code를 기본 제외하고 token/region fingerprint를 통합한다.
- [ ] heuristic parser는 tool 없는 fallback으로 남기고 confidence를 낮춘다.

### I4-4. C++ safety

**브랜치:** `feat/cpp-safety`

- [ ] ASan/UBSan/LSan 결과를 sanitizer kind, stack location, process evidence로 정규화한다.
- [ ] TSan은 별도 deep profile과 build variant로 제공한다.
- [ ] resource/lifetime/security는 clang analyzer·clang-tidy·clazy 결과를 category별로 매핑한다.
- [ ] sanitizer가 build됐지만 테스트가 실행되지 않은 경우 ERROR로 구분한다.
- [ ] quality-zoo의 UAF, leak, UB, Qt lifetime scenario가 예상 rule/location을 검증한다.

---

## 11. I5 — Python 정밀 분석과 호환성

### I5-1. Ruff와 mypy의 프로젝트 설정 존중

**브랜치:** `fix/python-tool-config`

- [ ] Ruff JSON/JSON-lines output과 rule code를 직접 파싱한다.
- [ ] project `pyproject.toml`/ruff config의 select, ignore, per-file policy를 보존한다.
- [ ] mypy의 project config를 자동 발견하게 하고 전역 `--ignore-missing-imports` 강제를 제거한다.
- [ ] opt-in ici profile은 별도 argv/config overlay로 명시한다.
- [ ] self project에서 mypy를 required로 만들고 `check_untyped_defs`를 단계적으로 켠다.
- [ ] tool note와 actual error를 구분하되 위치가 있는 note를 유실하지 않는다.

### I5-2. 내장 AST 규칙 재설계

**브랜치:** `refactor/python-rules`

- [ ] security regex를 call/assignment/import-aware AST 규칙으로 옮긴다.
- [ ] secret detector는 entropy/context와 allowlist를 지원하고 항상 redaction한다.
- [ ] resource engine이 `close()`, context manager, ownership transfer와 escaping return을 구분한다.
- [ ] mutable default는 correctness category로 분리한다.
- [ ] exception, dead, cognitive와 Ruff/mypy 중복 rule을 fingerprint로 합친다.
- [ ] rule별 limitation과 confidence를 문서화한다.

### I5-3. Python runtime compatibility

**브랜치:** `feat/python-compatibility`

- [ ] configured interpreter마다 `-VV`, compileall, import smoke를 argv로 실행한다.
- [ ] `requires-python`과 실제 interpreter version을 비교한다.
- [ ] 3.10 하한 위반 문법/API를 위치와 함께 보고한다.
- [ ] interpreter별 optional/required 정책과 unavailable 상태를 구분한다.
- [ ] envlens를 3.10과 최신 설치 interpreter에서 검증한다.

### I5-4. packaging과 환경 무결성

**브랜치:** `feat/python-package-analysis`

- [ ] pyproject metadata, src layout, package discovery와 entry point를 검증한다.
- [ ] wheel build가 요청된 경우 tag, included files, native extension, direct_url/build trace를 검사한다.
- [ ] pure-Python 정책과 일반 프로젝트의 native wheel 허용 정책을 분리한다.
- [ ] import name/distribution name 불일치와 누락 package data를 finding으로 만든다.
- [ ] envlens와 ici pyz build를 서로 다른 packaging 사례로 사용한다.

---

## 12. I6 — test와 coverage의 의미를 깊게 검증

### I6-1. gcov JSON 이관

**브랜치:** `fix/gcov-json-coverage`

- [ ] 지원 GCC에서는 `gcov --json-format`을 우선 사용한다.
- [ ] function start/end line, demangled name, line, branch, call을 파싱한다.
- [ ] format version과 GCC version을 검증한다.
- [ ] gzip corruption, missing data, path relocation을 ERROR로 구분한다.
- [ ] 오래된 GCC는 text fallback과 제한을 명시한다.
- [ ] 기존 throw branch 정책을 JSON 결과와 재검증한다.

### I6-2. coverage policy

**브랜치:** `feat/coverage-policy`

- [ ] 전체, 파일, 함수, changed-line threshold를 분리한다.
- [ ] generated, entry point, test, vendor 제외 근거를 report에 남긴다.
- [ ] Python coverage contexts와 C++ test/binary coverage mapping을 검토한다.
- [ ] uncovered function은 정확한 symbol location과 관련 test scope를 표시한다.
- [ ] baseline 대비 coverage regression을 별도 finding으로 만든다.

### I6-3. test quality deep profile

**브랜치:** `feat/test-quality`

- [ ] test count, pass/fail, collection evidence와 coverage를 계속 분리한다.
- [ ] retry/repeat를 통한 flaky test 탐지를 opt-in으로 제공한다.
- [ ] timeout과 slow-test inventory를 제공한다.
- [ ] Python mutation 도구와 C++ mutation 가능성을 spike하고, 재현 가능한 범위만 deep profile로 채택한다.
- [ ] mutation unavailable은 기본 test gate를 왜곡하지 않는다.

---

## 13. I7 — Makefile, artifact, binary와 hybrid integration

### I7-1. 수제 Makefile adapter

**브랜치:** `feat/make-adapter`

- [ ] 암묵적으로 임의 target을 추측하지 않고 config에 build/test/clean argv 계약을 둔다.
- [ ] shadow 또는 out-of-tree 지원 여부를 사전 진단한다.
- [ ] parallel jobs, coverage/sanitize flag 주입 방식을 명시한다.
- [ ] build target 0개, test target 0개, ignored failure를 구분한다.
- [ ] abilens의 실제 Makefile로 build/test/sanitize/coverage를 검증한다.

### I7-2. artifact manifest

**브랜치:** `feat/artifact-manifest`

- [ ] executable, shared/static library, Python wheel, report artifact를 typed record로 남긴다.
- [ ] hash, size, mode, producing target/command와 build variant를 기록한다.
- [ ] artifact glob이 빈 결과거나 project 밖으로 나가면 ERROR로 처리한다.
- [ ] downstream binary/integration engine은 manifest만 소비한다.

### I7-3. binary compatibility

**브랜치:** `feat/binary-compatibility`

- [ ] ELF class, machine, NEEDED, RPATH/RUNPATH를 readelf/objdump evidence로 읽는다.
- [ ] GLIBC, GLIBCXX, CXXABI maximum required version을 계산한다.
- [ ] static requirement, forbidden dependency/path와 configured floor를 정책화한다.
- [ ] stripped/malformed/non-ELF를 구분한다.
- [ ] abilens의 executable/shared library와 viewer static CLI를 실측한다.

### I7-4. hybrid integration contract

**브랜치:** `feat/hybrid-integration`

- [ ] shell 없는 argv case와 typed placeholder를 제공한다.
- [ ] Python interpreter와 artifact id를 manifest에서 안전하게 해석한다.
- [ ] stdout/stderr substring, exit code, timeout, output artifact assertion을 지원한다.
- [ ] 빈 required case, unknown placeholder, missing artifact를 config ERROR로 처리한다.
- [ ] buildscope의 Python analyzer → C++/Qt consumer E2E를 검증한다.

---

## 14. I8 — report와 viewer를 품질 조사 워크벤치로 만든다

### I8-1. reporter parity와 SARIF

**브랜치:** `feat/reporter-parity`

- [ ] 모든 reporter가 v3 finding, related location, confidence, suppression, delta를 보존하는지 contract test를 만든다.
- [ ] SARIF 2.1.0 export를 추가하고 rule/result/location/fix mapping을 검증한다.
- [ ] GitHub annotation은 new/high-priority finding만 제한적으로 발행한다.
- [ ] HTML은 full inventory를 검색/필터할 수 있지만 초기 DOM 크기를 제한한다.

### I8-2. viewer report diff

**브랜치:** `feat/viewer-report-diff`

- [ ] v2/v3 단일 보고서와 두 v3 보고서 비교를 지원한다.
- [ ] new, regressed, unchanged, moved, resolved를 표시한다.
- [ ] engine/rule/category/severity/confidence/file별 filtering과 정렬을 제공한다.
- [ ] related location과 정확한 line/column으로 이동한다.
- [ ] malformed/partial/oversized report를 명확한 오류로 처리한다.

### I8-3. triage와 suppression

**브랜치:** `feat/viewer-triage`

- [ ] finding에서 config suppression 초안을 만들되 자동으로 source를 수정하지 않는다.
- [ ] suppression reason, owner, expiry를 지원한다.
- [ ] baseline과 suppression의 차이를 UI와 문서에서 명확히 한다.
- [ ] resolved finding과 만료 suppression을 정리할 수 있다.

### I8-4. 대형 report 성능

**브랜치:** `perf/report-viewer`

- [ ] 10만 finding synthetic report benchmark를 만든다.
- [ ] lazy model/pagination 또는 equivalent 구조로 startup과 memory를 측정한다.
- [ ] console, HTML, viewer 각각 성능 budget을 실측 후 고정한다.
- [ ] benchmark 결과를 CI의 불안정한 wall-clock hard gate가 아니라 추세 artifact로 먼저 운영한다.

---

## 15. I9 — 회귀 corpus와 1.0 완료 기준

### I9-1. quality-zoo contract runner

**브랜치:** `test/quality-zoo-contract`

- [ ] toy repository의 manifest schema와 ici v3 report matcher를 정의한다.
- [ ] expected rule id, status/evidence/confidence, path, line 범위를 검증한다.
- [ ] expected absence를 지원해 false positive도 고정한다.
- [ ] ici release candidate pyz를 local path로 주입할 수 있게 한다.
- [ ] quality-zoo 실패가 어떤 engine regression인지 한 화면에 요약된다.

### I9-2. self dogfood ratchet

**브랜치:** `chore/quality-ratchet`

- [ ] self verify의 unexplained WARN/ERROR/SKIP을 0으로 만든다.
- [ ] heuristic warning은 limitation inventory로 분리한다.
- [ ] TEM/branch/function/file별 threshold를 baseline에 근접하게 단계 상승한다.
- [ ] giant module, complexity, duplication을 실제 리팩터링하거나 승인된 debt로 명시한다.
- [ ] console 기본 출력과 full report 모두 사람이 검토 가능한지 확인한다.

### I9-3. 1.0 support contract

- [ ] Python, C++, Qt별 engine support matrix를 문서와 report가 동일하게 표시한다.
- [ ] CMake, qmake, configured Makefile의 green real project가 모두 PASS한다.
- [ ] pure Python, pure C++/Qt, hybrid project가 각각 최소 하나 있다.
- [ ] Qt5/Qt6, Python 3.10과 최신 지원 runtime을 실측한다.
- [ ] buildscope/envlens/abilens과 기존 앱의 release artifact가 재현 가능하다.
- [ ] quality-zoo의 모든 stable scenario가 expected finding/location을 만족한다.
- [ ] 네트워크와 root 권한 없이 standard profile이 완료된다.
- [ ] v2 report migration과 v3 schema 안정성 정책을 발표한다.
- [ ] 사용자 문서에 설치 도구, fallback, limitation과 remediation workflow가 있다.

---

## 16. PR 및 릴리스 운영

### 16.1 PR 크기

- 위 하위 절 하나가 기본 PR 상한이다.
- schema migration처럼 큰 절은 model/serialization/reporter migration으로 더 나눈다.
- 서로 무관한 viewer UI와 engine 알고리즘을 같은 PR에 넣지 않는다.
- toy에서 발견한 ici 결함은 재현 scenario 또는 fixture를 먼저 기록하고 ici PR에서 수정한다.

### 16.2 교차 저장소 순서

1. ici fixture와 대응 toy/quality-zoo branch에 기대 계약을 먼저 정의한다. 실제 프로젝트에서
   발견된 문제는 toy 재현이 먼저일 수 있고, 계획된 engine은 ici contract test가 먼저일 수 있다.
2. 현재 release에서 적용 가능한 양쪽 재현이 실패하는지 확인한다. 실패 상태는 main에 병합하지 않는다.
3. ici 구현과 전체 gate를 통과시킨다.
4. candidate pyz로 toy native test와 ici verify를 통과시킨다.
5. ici PR을 병합하고 release 또는 검증 가능한 release candidate를 만든다.
6. toy PR의 pin을 갱신해 병합하고 `ICI-GAPS.md`에 재현, 양쪽 PR과 final evidence를 남긴다.

릴리스가 불필요한 독립 변경은 억지로 서로 기다리지 않는다. 다만 toy가 아직 배포되지 않은 ici 기능에 의존한 채 main에 병합되지는 않게 한다.

### 16.3 각 PR의 완료 증거

- 단위 테스트와 parser golden/contract test
- relevant E2E fixture
- 대응 toy project 또는 quality-zoo 실측
- before/after JSON 일부와 console 요약
- 성능 또는 출력량에 영향이 있으면 측정값
- docs와 CHANGELOG
- Python 3.10 full quality gate와 `./dist/ici.pyz verify`

---

## 17. 명시적 비목표

- ici가 compiler, Ruff, mypy, clang-tidy, clazy를 자체 재구현하지 않는다.
- 인터넷 보안 DB나 SaaS가 없으면 기본 코드 품질 검증이 멈추는 구조를 만들지 않는다.
- 하나의 TEM 숫자로 모든 품질 차원을 숨기지 않는다. TEM은 test signal 중 하나로 유지한다.
- 결과를 좋게 보이게 하려고 unsupported scope를 PASS로 바꾸지 않는다.
- toy 프로젝트에 제품과 무관한 기능을 억지로 넣지 않는다. 그런 경로는 quality-zoo가 담당한다.
- auto-fix와 suppression이 기본 실행에서 사용자 소스를 수정하지 않는다.

---

## 18. 마스터 체크포인트

- [x] I0: 현재 viewer/cycle 계획이 보정된 테스트와 함께 완료
- [x] I1: v3 finding, support matrix, baseline, issues-first console 완료
- [x] I2: toolchain inventory, shared context, engine DAG, cache/reproducibility와 PR·CI·Pages 증거 완료
- [ ] I3: I3-1 compilation model/검증 게이트 로컬 완료; I3-2~I3-4 pending
- [ ] I4: C++/Qt tool-backed analyzer와 safety profile 완료
- [ ] I5: Python tool config, AST rules, runtime/package 호환성 완료
- [ ] I6: gcov JSON, coverage policy, test-quality deep profile 완료
- [ ] I7: Makefile, artifacts, ABI, hybrid integration 완료
- [ ] I8: reporter parity, viewer diff/triage, 대형 report 처리 완료
- [ ] I9: quality-zoo, self ratchet, 1.0 support contract 완료

I1 기능과 로컬 실물 검증 및 PR/CI Merge Gate는 완료됐다. [PR #89](https://github.com/jihoon22-lee/ici/pull/89)의
병합 commit과 [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의 required checks
결과는 위 I1-4 완료 조건에 기록한 evidence를 따른다. I2-2 shared context와 artifact
manifest와 I2-3 선언형 pipeline 구현은 완료됐다. I2-4 cache contract는 PR #97의 merge
commit `ef30059522729b376c5409e5bb49164aa538b128`로 병합됐고 CI run `33345993304`의 모든
required check와 Merge Gate가 성공했다. sticky comment `5472411964`의 ici/viewer Pages도
게시됐다. 후속 source-scope 보정 PR #98도 CI run `33355330343` green 뒤 merge commit
`6a0eadb20464569be9573d41ab72a27bd96d58a7`로 병합됐다. I3-1의 로컬 evidence는 위에
기록했으며 PR·CI·Pages evidence는 아직 pending이다. 다음 ici 구현 단계는 I3-2 CMake
compile DB 생성이다.
