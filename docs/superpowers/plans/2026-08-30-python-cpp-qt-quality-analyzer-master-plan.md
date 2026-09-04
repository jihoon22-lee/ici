# Python·C++·Qt 코드 품질 분석기 마스터 실행 계획

**상태:** 승인된 장기 마스터 계획. 2026-08-30 이후 ici 기능 계획의 우선순위와 완료 조건은 이 문서를 기준으로 판단한다.
**문서 기준일:** 2026-09-04. 이 계획은 ici [PR #78](https://github.com/jihoon22-lee/ici/pull/78)로 `main`에 병합됐고, 현재 상태는 이 체크리스트와 병합된 PR의 실측을 함께 기준으로 삼는다.

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

당시 검증 환경에는 Qt 5.15.18과 Qt 6.10.2가 모두 설치돼 있었다. ici viewer는 [PR #81](https://github.com/jihoon22-lee/ici/pull/81)에서 Qt 5·Qt 6 각각 4/4 CTest와 Qt-free CLI를 통과했고, toy 쪽 환경·실측 기록은 [PR #15](https://github.com/jihoon22-lee/toy-projects/pull/15)에 있다. 따라서 과거 문서의 “Qt 5 미설치/미검증” 또는 “아직 푸시되지 않음” 표현은 당시 기준에서도 현재 상태가 아니었다.

### 1.2 상태 기준 snapshot (2026-09-01)

1.1의 기준선은 당시 감사 snapshot으로 보존한다. 현재 병합된 기준은 ici `main`
[`9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327`](https://github.com/jihoon22-lee/ici/commit/9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327)와
toy-projects `main`
[`590899a0a9430e9ce35162b301bfef5d7dfc78a4`](https://github.com/jihoon22-lee/toy-projects/commit/590899a0a9430e9ce35162b301bfef5d7dfc78a4)다.
I4-1의 LLVM 18 structural-note parser 보정은 ici [PR #119](https://github.com/jihoon22-lee/ici/pull/119),
v0.9.1 release prep은 [PR #120](https://github.com/jihoon22-lee/ici/pull/120)로 병합됐고, 각
exact-main run `33482849448`/`33484388337`이 green이었다. annotated `v0.9.1`은 release
provenance와 9개 asset audit까지 완료됐다.

toy-projects BuildScope B4는 released ici v0.9.1을 사용한 [PR #36](https://github.com/jihoon22-lee/toy-projects/pull/36)에서
16/16 check, sticky comment의 marker 1개·현재 run·세 report link, 세 hosted HTML의
HTTP 200/exact title/Zero-CDN을 모두 통과했다. squash merge 후 toy main exact run
`33488169769`의 14개 prerequisite job과 Merge Gate, Dependency Graph run `33488174425`도
성공했다. 따라서 I4-1과 B4 precondition은 닫혔다. `feat/qt-analysis`에서 I4-2의 여섯
코드 조건과 focused local contract가 완료됐고 [PR #122](https://github.com/jihoon22-lee/ici/pull/122)의
PR CI와 squash merge 뒤 [exact-main run `33500281653`](https://github.com/jihoon22-lee/ici/actions/runs/33500281653)까지
통과해 ici 원격 acceptance도 완료됐다. v0.10.1은 공개 v0.10.0의 production warning-policy
결함을 교정하는 corrective stabilization으로 취급한다. 이 단락은 당시의 snapshot이며,
이후 공개된 v0.10.2와 그 release evidence는 다음 절을 현재 기준으로 삼는다.

### 1.3 현재 공개 release 기준 (2026-09-02)

현재 stable release는 [v0.10.2](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)다.
annotated tag는 exact `main` commit
[`3b50dd4c485ddab212beb23ff820e82286a06e77`](https://github.com/jihoon22-lee/ici/commit/3b50dd4c485ddab212beb23ff820e82286a06e77)을
가리킨다. [exact-main CI run `33541134010`](https://github.com/jihoon22-lee/ici/actions/runs/33541134010)은
Verify, Qt 5/Qt 6, `Publish Main Verification Report`, `Merge Gate`를 성공시켰고, push에서
실행하지 않는 PR publisher는 expected `skipped`였다. [release run
`33541928666`](https://github.com/jihoon22-lee/ici/actions/runs/33541928666)의
`Validate Release Provenance`와 `Build & Publish Release`도 성공했다.

공개 release는 non-draft/non-prerelease이며 정확히 9개 asset
(`ici.pyz`, `ici.pyz.sha256`, `ici-self-report.html`, `ici-self-report.json`,
`viewer-report.html`, `viewer-report.json`, `icirv`, `icirv-gui`, `icirv-gui.README.txt`)을
포함한다. `ici.pyz` SHA-256은
`8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`다. 독립 main Pages
audit도 ici `ici Verification Report — ici`, viewer `ici Verification Report — viewer`의
정확한 title과 HTTP 200·`text/html`·외부 resource URL 0건을 확인했다. 전체 asset 표와
검증 로그는 [`v0.10.2 public evidence workthrough`](../../workthrough/2026-09-02-public-v0.10.2-evidence.md)에
고정한다.

### 1.4 최신 main 기준과 candidate acceptance 경계 (2026-09-04)

현재 ici `main`은 PR #151의 GNU ELF target-local discarded-function 및 bounded Python
AST-shape duplicate slice가 병합된
[`c87e4075634fceea0770435e7a513787c10122ed`](https://github.com/jihoon22-lee/ici/commit/c87e4075634fceea0770435e7a513787c10122ed)에서
PR #152의 Python compatibility·semantic-analysis 보정이 이어진
[`2113b5ddc21905660afa4002ee8b25af6a8f5fcf`](https://github.com/jihoon22-lee/ici/commit/2113b5ddc21905660afa4002ee8b25af6a8f5fcf)다.
[PR #151](https://github.com/jihoon22-lee/ici/pull/151)의 PR CI
[`33746819016`](https://github.com/jihoon22-lee/ici/actions/runs/33746819016)과 exact-main
[`33747985638`](https://github.com/jihoon22-lee/ici/actions/runs/33747985638), [PR #152](https://github.com/jihoon22-lee/ici/pull/152)의
PR CI [`33763034968`](https://github.com/jihoon22-lee/ici/actions/runs/33763034968)과 exact-main
[`33763926843`](https://github.com/jihoon22-lee/ici/actions/runs/33763926843)은 Verify, Qt 5·Qt 6,
해당 report publisher와 Merge Gate를 성공시켰다. 각 PR에서 push에 적용되지 않는 반대 publisher만
expected `skipped`였다. 따라서 두 PR의 implementation과 PR/main CI acceptance는 현재 main에
반영됐지만, 버전 `0.10.2`와 release 경계는 그대로다.

Quality Zoo acceptance는 candidate가 생성된 exact feature head에만 귀속한다. 기존 sanitizer
run [`33710695336`](https://github.com/jihoon22-lee/ici/actions/runs/33710695336)은 5개 scenario,
category/Qt run [`33718024450`](https://github.com/jihoon22-lee/ici/actions/runs/33718024450)은
6개 scenario, 별도 TSan run [`33737405098`](https://github.com/jihoon22-lee/ici/actions/runs/33737405098)은
8/8 scenario contract를 모두 `PASS`로 기록했다. 이 runs는 각각의 이전 exact target을 검증한
historical candidate evidence이며, 현재 `main@2113b5ddc21905660afa4002ee8b25af6a8f5fcf`에
대한 Quality Zoo 재실행을 의미하지 않는다. 그러므로 broader Qt lifetime/ownership, resource·
lifetime·security taxonomy, Q1–Q5, I4 aggregate와 release는 계속 pending이다.

---

## 2. 기준선과 문제 정의 (2026-08-31 snapshot)

2026-08-31 실측 기준 (`origin/main@fa3ad28`, self-quality annotation 반영)을 historical
baseline으로 보존한다:

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
- security/resource 일부 규칙은 당시 구조 인식과 분석 정밀도를 별도로 표현하지 못했다.
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
현재 `dead` 엔진은 Python-only, C++-only, hybrid 여부와 관계없이 cache key 계산, load, store를
모두 비활성화한다. 특히 C++ compiler-backed 결과는 external/generated include closure와
compiler binary content가 cache identity에 모델링될 때까지 안전하게 재사용할 수 없다.

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

**I3-1 완료(2026-08-31; 로컬 및 원격 evidence):** bounded descriptor read와 strict JSON, POSIX/MSVC
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
0건이다. 위 수치(HTML 4,627,454 bytes, branch 79.6% 등)는 로컬 증거다. 원격 병합도
완료됐다. [PR #99](https://github.com/jihoon22-lee/ici/pull/99)는 squash로 병합되어
commit [`64c4f7b57826e088e9b74b5950c7f3d8091188b9`](https://github.com/jihoon22-lee/ici/commit/64c4f7b57826e088e9b74b5950c7f3d8091188b9)가
되었고, [CI run 33380721019](https://github.com/jihoon22-lee/ici/actions/runs/33380721019)의
`Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`, `Publish PR Report &
Sticky Comment`, `Merge Gate`가 모두 SUCCESS였다(`Publish Main`은 PR에서 expected skipped).
[sticky comment](https://github.com/jihoon22-lee/ici/pull/99#issuecomment-5476836988)는 ici와
viewer를 함께 포함했으며, CI stats는 ici WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1,
TEM 4.86, tests 1,032, branch 79.7%), viewer WARN(Pass 10, Warn 1, Fail 0, Error 0, Skip 2,
TEM 4.89, tests 7)였다. 독립적으로 fetch한 [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/99/)
와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/99/)는 각각 HTTP/2 200,
`Content-Type: text/html; charset=utf-8`, title present, 외부 `script`/`link`/`img`/`iframe`
dependency 0건이었고 관측 bytes는 각각 4,496,996와 344,663이었다. 이로써 I3-1은 완료됐다.
이 단락은 I3-1의 범위만 기록하며, CMake DB 생성·qmake capture·lint/include graph 이관은
아래 I3-2~I3-4 절에서 별도로 추적한다. 당시 시점에는 I3 전체가 아직 완료되지 않았고, 아래
후속 절의 PR #113 및 exact-main evidence로 현재 complete됐다.

### I3-2. CMake compile DB 생성

**브랜치:** `feat/cmake-compile-db`

- [x] adapter configure에 `CMAKE_EXPORT_COMPILE_COMMANDS=ON`을 넣는다.
- [x] Makefile/Ninja generator 제약과 unity build를 탐지한다.
- [x] coverage/sanitize/release variant 중 analyzer에 사용할 canonical DB 정책을 정한다.
- [x] generated source가 build 전 필요한 경우 generation 단계 후 DB를 소비한다.
- [x] I3-2 구현을 PR로 병합하고 CI Merge Gate와 ici/viewer Pages HTML evidence를 독립적으로 확인한다.
- [x] BuildScope와 viewer에서 실제 target별 명령을 대조한다. v0.8.0 public projection은
  define·standard·include를 포함한 16 unit·6 target·14 field group에서 mismatch 0을 확인했다.

**I3-2 구현 및 로컬 증거 (2026-08-31):** CMake root project에 기존 DB가 없을 때만
`build/ici-cmake-build` Release shadow를 사용하고, configure에
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`과 `CMAKE_UNITY_BUILD=OFF`를 넣는다. `Ninja` 또는
`*Makefiles` single-config generator만 exact context로 허용하며, 최대 4 MiB no-follow
`CMakeCache.txt`에서 generator/export/unity metadata를 bounded하게 읽는다. generated
source가 canonical shadow에서 stale이면 한 번 full build한 뒤 DB를 reload하고, CMake
subdirectory output은 entry directory와 database parent 해석이 같은 경우에만 reconcile한다.
`CompilationContext`/unit report와 cache identity에는 origin/generator/unity/target이
포함된다.

Python 3.10 `pytest`는 1,074 passed (46.32s), Ruff check/format은 130 files, focused mypy는
11 source files에서 clean이었다. reproducible pyz 두 build의 SHA-256은
`2874e081cc27e0fc7f77e1285229c5fd0ba2803a149ddf1c6e4a3c4fb4d6db90`로 일치했고 pure-Python
10 distributions/no certifi, smoke·Zero-CDN도 PASS였다. self verify는 WARN(Pass 8, Warn 4,
Skip 1; tests 1,074; line/function/branch 88.7%/97.2%/79.7%; TEM 4.86; 113.38s;
HTML 4,697,480 bytes; external dependencies 0)였다. candidate viewer는 PASS(5/5 production,
20 configurations, 0 issues, 23.27s), LogLens는 PASS(14/14, 40 configurations, 0 issues,
32.27s)였다. self-dogfood에서 처음 발견한 불필요한 silent `OSError` inspection은 제거했고
final exception path가 PASS했다. 위 수치는 local implementation evidence다.

I3-2 원격 병합 증거도 완료됐다. [PR #101](https://github.com/jihoon22-lee/ici/pull/101)은
squash commit [`459abbaa5d6c80d91dfe07e54403c9bf88e63602`](https://github.com/jihoon22-lee/ici/commit/459abbaa5d6c80d91dfe07e54403c9bf88e63602)로
병합됐다. [CI run 33386134812](https://github.com/jihoon22-lee/ici/actions/runs/33386134812)의
`Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`, `Publish PR Report &
Sticky Comment`, `Merge Gate`가 모두 SUCCESS였고 `Publish Main`은 PR에서 expected SKIPPED였다.
[sticky comment](https://github.com/jihoon22-lee/ici/pull/101#issuecomment-5477565364)는 ici와
viewer 링크를 모두 포함했다. CI stats는 ici WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1,
TEM 4.86, tests 1,074, branch 79.8%), viewer PASS(Pass 11, Warn 0, Fail 0, Error 0, Skip 2,
TEM 4.89, tests 7, compile_db 5/5 production units, 20 configurations, 0 issues)였다.
독립적으로 확인한 [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/101/)와
[viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/101/)는 모두 HTTP/2 200,
`text/html`, title present, 외부 dependency 0건이었고 관측 bytes는 각각 4,574,483와 337,918이었다.
v0.8.0 public projection으로 BuildScope target-by-target 대조가 완료됐고 I3-3은 완료됐다.
I3-4는 기존 구현·원격 evidence에 더해 아래 local actual-process edge revalidation을
기록했고, 새 테스트의 PR/CI/Pages evidence와 exact-main verification도 아래에서 완료했다.
I3 전체는 complete이며 다음 단계는 I4이다.

### I3-3. qmake compile capture

**병합:** [PR #103](https://github.com/jihoon22-lee/ici/pull/103), squash commit
[`e97d6d4502232bf7bc5b36a21f3b031306f43554`](https://github.com/jihoon22-lee/ici/commit/e97d6d4502232bf7bc5b36a21f3b031306f43554)

- [x] qmake configure 뒤 shadow에서 deterministic `make clean`을 먼저 실행하고,
  clean evidence와 실패 사유를 결과에 남긴다. 재사용한 qmake shadow에서 정적 archive와
  test executable의 freshness가 어긋나 stale 실행 및 gcov stamp 불일치가 coverage 0%로
  둔갑하지 않게 하는 선행 안전망이며, CMake build path는 변경하지 않는다. 이 체크는
  compile capture 완료를 의미하지 않는다.
- [x] qmake verbose build, trace output, compiler wrapper, 선택적 외부 capture 도구를
  실측 비교하는 spike를 먼저 한다.
- [x] shell parsing이나 임의 command 실행 없이 compiler `argv`와 working directory를
  보존하는 compiler-wrapper 방식을 채택한다.
- [x] Qt5/Qt6, target wrapper, shadow-relative path를 real qmake fixture와 DiskMap에서
  검증한다.
- [x] exact capture가 불가능한 환경은 명시적 POSIX lower-confidence mode로 남긴다.
- [x] Python 3.10 full quality gate, reproducible pyz/build/smoke와 self-verify local evidence를
  확정한다.
- [x] PR/CI/Merge Gate/Pages remote evidence를 이 slice의 최종 증거로 확정한다.

**I3-3 완료 및 bounded local/remote evidence (2026-08-31):** qmake는 자체적으로
`compile_commands.json`을 내보내지 않으므로, 명시적·자동 발견 database가 없고 root backend가
qmake인 C/C++ project에만 `build/ici-qmake-build` Release shadow를 사용한다. 기존 database는
항상 우선하며, capture가 실패해도 임의 database를 대신 실행하지 않는다.

DiskMap에서 첫 qmake pass에 wrapper text를 주입하면 nested `$$`가 collapse되는 것이 발견됐다.
그래서 preflight는 owned canonical shadow를 먼저 reset하고, `-recursive` configure를 한 번
수행해 nested Makefile을 materialize한다. 첫 단계는 recursively 찾은 `Makefile*`를 no-follow
bounded read로 검사해 정확히 하나의 일관된 `CC`/`CXX` compiler pair만 허용한다. 각 값은 단일
recognized gcc/g++/clang driver여야 하고, whitespace·multiword·불일치·미설치·symlink 또는
non-executable regular file이면 fail closed한다. Makefile 하나는 최대 4 MiB, 전체는 최대
4,096개이며 aggregate metadata도 bounded하다.

두 번째 단계는 같은 canonical shadow를 `-recursive`로 다시 configure하면서 선택한
`sys.executable`을 shebang으로 고정한 compiler wrapper와 probe에서 resolve한 literal absolute
C/C++ compiler path를 `QMAKE_CC`/`QMAKE_CXX`에 전달한다. wrapper는 `-c` invocation의 wrapper
뒤 exact `argv`와 실제 working directory만 32 MiB/200,000-record JSONL journal에 기록한 뒤
원래 compiler를 직접 `execvp`한다. shell parsing, Makefile recipe 재해석, captured command
replay는 없다. 두 번째 configure 뒤에는 adapter의 deterministic `make clean`을 evidence로
기록하고 capture build를 수행한다.

journal은 no-follow regular-file, owner/mode와 permission 재검사 및 locking을 거치며 wrapper는
0700, journal은 0600이다. generated `compile_commands.json`은 owned shadow 내부 temporary
file에서 atomic replace로 발행한다. Non-POSIX host는 configure하지 않고 명시적 warning인
`qmake-capture-unsupported`로 lower confidence를 표시한다. capture된 source set과
production translation unit을 비교해 빠진 단위는 `qmake-capture-incomplete` diagnostic으로
남긴다.

`CompilationContext`는 `origin = "qmake"`, `generator = "qmake"`, `unity_build = null`과
capture diagnostics를 보존한다. v3 schema의 origin enum에 qmake를 추가했고, compilation
identity는 `ici.compilation-identity/v2`, cache key는 `ici.analysis-cache-key/v2` 계약으로
이 provenance를 포함한다. `VerifyOrchestrator`는 qmake backend에서
`prepare_qmake_compilation_context`를 선택하고, CMake backend는 기존 CMake preflight를
사용한다.

현재 확인한 local E2E facts는 다음으로 한정한다. Qt5와 Qt6의 real qmake fixture에서 각각
3 compilation units가 수집됐고 generated moc unit도 포함됐다. 실제 DiskMap Qt5/Qt6 실행은
총 20 configurations에서 9/9 production units를 포함했고 compilation diagnostics는 없었으며,
temporary capture shadows는 정리됐다. 이 사실은 implementation/E2E evidence이지 final
remote evidence가 아니다.

최종 local gate는 Python 3.10 `1,112 passed (52.96s)`, Ruff check/format 134 files, focused
mypy 7 source files clean이었다. current source pyz 두 빌드는 SHA-256
`5610617022a6accaf0b8fa0313ee0fd6c414317e839d23e2c879fa8b4c918d23`로 일치했고 pure-Python
10 distributions/no certifi, smoke의 Python 3.10 직접 실행·artifact integrity·Zero-CDN도
PASS였다. packaged self-verify는 WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1; tests 1,112;
line/function/branch 88.8%/96.5%/79.9%; TEM 4.82; complexity 25; 117.25s)이었다. HTML은
4,722,391 bytes, title present, 외부 script/link/image dependency 0건이었다. 최초 self run에서
qmake dispatch conditional이 `VerifyOrchestrator.run_all` complexity를 25→26으로 올려 FAIL을
만든 것을 발견해 typed helper로 분리했고, final self run은 25/WARN으로 복구됐다. qmake argv
builder도 분리해 `cmake.py`를 512→495 code lines로 낮췄고 self line issues는 10→9가 됐다.
원격 evidence도 완료됐다. [PR #103](https://github.com/jihoon22-lee/ici/pull/103)은
squash commit [`e97d6d4502232bf7bc5b36a21f3b031306f43554`](https://github.com/jihoon22-lee/ici/commit/e97d6d4502232bf7bc5b36a21f3b031306f43554)로
병합됐다. [CI run 33394395321](https://github.com/jihoon22-lee/ici/actions/runs/33394395321)의
`Verify & Dogfood ici`, `Viewer GUI Qt5`, `Viewer GUI Qt6`, `Publish PR Report & Sticky Comment`,
`Merge Gate`가 모두 SUCCESS였고 `Publish Main`은 PR에서 expected SKIPPED였다. [sticky comment](https://github.com/jihoon22-lee/ici/pull/103#issuecomment-5478744238)는
ici WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1, TEM 4.82, tests 1,112,
line/function/branch 88.9%/96.5%/80.1%)와 viewer PASS(Pass 11, Warn 0, Fail 0, Error 0,
Skip 2, TEM 4.89, tests 7, compile DB production 5/5, 20 configurations)를 기록했다.
Independent [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/103/)와
[viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/103/)는 각각 HTTP 200,
`text/html`, title `ici Verification Report — ici`와 `ici Verification Report — viewer`,
관측 bytes 4,716,032와 337,918, 외부 `script`/`link`/`img` reference 0건을 확인했다.
I3-3은 완전히 완료됐다. I3-2 BuildScope target-by-target 대조는 v0.8.0 public projection의
16 unit·6 target·14 field group mismatch 0으로 완료됐다. I3-4 구현·기존 focused local
test·PR·CI·Pages evidence도 완료됐고, 새 same-basename actual-process edge는 local에서
완료됐다. 새 테스트의 PR/CI/Pages 및 exact-main evidence도 완료되어 I3 전체가 complete됐다.
다음 단계는 I4이다.

### I3-4. lint와 include graph 이관

**브랜치:** `refactor/cpp-analysis-context`

- [x] C++ lint가 고정 `-std=c++17` 명령을 만들지 않고 shared compilation context의 모든
  covered translation unit configuration에서 `CapabilityInventory`가 probe한 직접 GCC/Clang
  argv를 sanitized replay한다.
- [x] compile-only/output/dependency와 plugin/wrapper/toolchain 주입 flags를 안전하게
  제거하거나 거부한다.
- [x] compiler `-E -H` dependency output으로 configuration별 active include edge와 resolved
  path를 수집한다.
- [x] generated/system/third-party header scope를 구분하고 configuration별 scope counts를
  기록한다.
- [x] suffix fallback은 compilation DB/context가 실제로 없는 heuristic mode로만 유지하고,
  exact context에서는 replay 실패를 `ERROR`/`NOT_RUN`으로 닫는다.
- [x] active missing include의 위치 있는 `CppIncludeUnresolved` 경고와 ambiguous/unresolved
  edge를 보고한다. 서로 다른 configuration의 edge를 섞어 false cycle을 만들지 않는다.

**완료 조건:** buildscope에서 source별 define·standard·include가 실제 build와 일치하고, 같은 basename header가 compiler 선택과 같은 edge로 연결된다.

**I3-4 구현 및 local revalidation (2026-09-01):** C++ lint와 include graph는 위 여섯 checklist의
구현을 완료했다. exact context에서는 sanitized direct compiler replay와 fail-closed 오류 처리를
사용하고, DB 부재에서만 lint/cycle heuristic을 `ESTIMATED`로 남긴다. replay option은 positive
allowlist와 허용된 value만 보존하며 unknown/unsafe option은 fail-closed로 거부한다. compiler는
minimal replacement environment와 closed stdin으로 실행된다. error-level context/unit
diagnostic만 `ERROR`/`NOT_RUN`으로 올리고 warning-level diagnostic은 위치 있는 `WARN`으로
보존해 다른 오류가 없으면 `MEASURED` exact evidence를 유지한다. DB 부재 lint도
ready/canonical direct `g++`와 동일 replay policy/bounds를 사용하며 unsafe package/include
flag와 project-contained driver를 실행 전에 거부한다. bounded include trace parser는
missing-include trace, include-guard trailer, pseudo frame과 stale path를 fail-closed로 처리한다.
관련 focused test 묶음은 총 308 tests passed였다. Python 3.10 full pytest는 1,275 passed
(48.61s), Ruff check는 전체 파일에서 통과했으며 Ruff format은 142 files, mypy는 83 source
files를 통과했다. 모든 새 source는 line gate PASS이고 새 helper complexity issue는 0이다.
원격 evidence는 [PR #105](https://github.com/jihoon22-lee/ici/pull/105)의 squash merge commit
[`183b2d83421cd3173fb2e6f745c0e39bd5c36a78`](https://github.com/jihoon22-lee/ici/commit/183b2d83421cd3173fb2e6f745c0e39bd5c36a78)로
완료됐다. [CI run `33409862110`](https://github.com/jihoon22-lee/ici/actions/runs/33409862110)의
`Verify & Dogfood ici` (3m58s), `Viewer GUI Qt5` (45s), `Viewer GUI Qt6` (1m15s),
`Publish PR Report & Sticky Comment` (1m16s), `Merge Gate` (3s)는 모두 SUCCESS였고,
`Publish Main`은 PR에서 expected SKIPPED였다. [sticky comment](https://github.com/jihoon22-lee/ici/pull/105#issuecomment-5480770505)는
ici와 viewer 링크/표를 모두 포함하며, ici WARN (Pass 8, Warn 4, Fail 0, Error 0, Skip 1,
TEM 4.84, tests 1,275/1,275, branch 80.4%)와 viewer PASS (Pass 11, Warn 0, Fail 0, Error 0,
Skip 2, TEM 4.89, tests 7/7)를 기록했다. 독립 [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/105/)
와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/105/)는 모두 HTTP/2 200,
`text/html;charset=utf-8`, 올바른 title, 외부 `script`/`link`/`img`/`iframe`/`import` 0건을
확인했으며 관측 bytes는 각각 5,458,757와 344,868이다. BuildScope target-by-target 대조는
v0.8.0 public projection에서 16 unit·6 target·14 field group mismatch 0으로 완료됐다.
새 same-basename actual-process test는 local과 PR #113 및 exact-main evidence까지 완료됐다.
이로써 I3 전체가 complete됐고 다음 단계는 I4이다.

현재 cache key는 `ici.analysis-cache-key/v3`이며, I3-4 engine class가
`CACHE_IMPLEMENTATION_MODULES`로 명시한 helper/dependency module source digest의 sorted
unique 목록을 implementation identity에 포함한다. C++ lint 선언에는 isolated
`ici.engines._cpp_diagnostic_categories` taxonomy helper가 포함되고, cycle 선언에는
`ici.core._cpp_replay_policy`와 `ici.engines._cpp_include_trace`가 포함된다. I3-1~I3-3 절의 당시 v2 compilation
context/cache 문구는 과거 evidence이므로 변경하지 않는다.

**Same-basename active-header local revalidation (2026-09-01):** 기존
`test_trace_uses_compiler_selected_same_basename_without_ambiguity`는 `run_process`를
monkeypatch한 mock runner로 parser/선택 회귀만 검증한다. 새
`test_real_compiler_trace_selects_the_first_same_basename_header`는
`build_compiler_cpp_graph(..., runner=run_process)`를 호출하고, capability probe를 통과한
실제 `g++`/`clang++`를 parameterize해 preprocessor trace를 확인한다. 현재 로컬 Python 3.10
focused 실행은 mock 케이스와 실제 `g++` 케이스가 통과해 `2 passed`였고, 설치되지 않은
`clang++` 케이스는 `1 skipped`였다. 실제 trace에서 첫 번째 `-I`의 `common.hpp`가 선택되고
두 번째 동일 basename header는 edge에서 제외됨을 확인했다. 이 문단은 이 브랜치에서의 local
process evidence를 기록하며, 새 테스트의 PR/CI/Pages 및 exact-main evidence는 I3-5 remote
evidence 절에 별도로 기록한다.

**현재 I3 상태:** BuildScope에서 target-by-target으로 define·standard·include를 실제 build와
대조한 public projection은 16 unit·6 target·14 field group에서 mismatch 0으로 완료됐다.
same-basename active-header edge의 실제 compiler trace 대조도 위 local revalidation으로
완료됐고, 새 actual-process test의 PR #113·exact-main remote evidence도 아래에 기록했다.
I3 전체가 complete됐으며 다음 단계는 I4이다.

### I3-5. 독립 compilation-context export와 교차 구현 대조

**브랜치:** `feat/compilation-context-export`

- [x] `ici export-compilation-context`의 기본 경로를 process-free/read-only로 분리하고,
  CMake/qmake configure·build는 명시적 `--prepare`에서만 허용한다.
- [x] raw `argv`/`command`와 외부 host path를 공개하지 않는 deterministic
  `ici.compilation-export/v1` projection, source/semantic/configuration digest 및
  `comparable`/`inconclusive` 상태를 정의한다.
- [x] DB·response file·DB 전체 expanded argument·출력 크기를 모두 제한하고, no-follow
  containment read, duplicate-key rejection, protected output, symlink-safe atomic replacement를
  테스트한다.
- [x] 공개 schema가 wheel/ZipApp package data에 실제 포함되는지 build gate로 강제한다.
- [x] I3-4의 same-basename active-header edge가 실제 compiler trace와 일치함을 local
  actual-process test로 확정한다. 기존 mock runner 회귀와 새 실제 `run_process` 경로를
  구분하며, compiler 미설치 시 parameterized case는 skip한다.

이 export는 전체 `verify` report의 대체물이 아니라 BuildScope와 같은 독립 consumer가 동일
compile database 해석을 안전하게 비교하기 위한 최소 계약이다. 기본 호출은 root descriptor와
metadata, 선택된 DB만 읽고 전역 default config도 생성하지 않는다. `--database`는 project-relative
POSIX path만, `--output`은 stdout 또는 검증된 atomic file target만 허용한다. 실제 build가 필요한
경우에만 `--prepare`가 owned `build/ici-*` shadow를 사용할 수 있다. 명시적으로 설정한 DB가
missing 또는 malformed여도 그 선택은 authoritative하며, `--prepare`가 이를 조용히 대체하지 않는다.
I3 기능 완료 조건은 same-basename active-header edge를 포함한 BuildScope target-by-target
대조까지 local/public projection으로 충족됐다. 위 edge checkbox는 local actual-process
evidence이며, 새 테스트의 PR #113·exact-main PR/CI/Pages evidence도 아래 기록으로 확인됐다.
이에 따라 I3 checkpoint 전체를 complete로 닫고 다음 단계는 I4이다.

**I3-5 final local revalidation evidence (2026-09-01):** Python 3.10 full suite 1,333 tests
passed in 51.99s, Ruff check/format 148 files, mypy 88 source files가 통과했다. quoted relative
define path regression은 unit directory 기준 해석과 외부 탈출 redaction으로 고정했다. 두 pyz
build는 SHA-256 `d9d83b20832ca8d0133653e00b1f7a20861c2ee855b06d0de1f0328137a382ca`으로 일치했고,
10개 pure-Python distribution/no certifi, 두 공개 schema package data, smoke·Zero-CDN을
확인했다. packaged self verify는 WARN(8/4/0/0/1: pass/warn/fail/error/skip; tests 1,333/1,333;
line/function/branch 89.2%/96.8%/80.6%; TEM 4.84; cache 0; engine 121.72s, wall 125.09s)이었다.
HTML은 5,696,688 bytes, SHA-256 `adc9a49c78c2f5ea5666c58a96555cd73b281587f891e11175654a7ac973b3d5`,
expected title, external references 0건이며 변경 범위의 line/coverage/type/high-complexity/
exception finding은 0건이다. 최종 candidate BuildScope verify는 WARN(11/2/0/0/0;
tests 45/45; line/function/branch 95.2%/100%/84.3%; compile DB 7/7 production units,
16 configurations, 0 issues; TEM 5.00; engine 20.52s, wall 21.22s)이었다. BuildScope HTML은
490,420 bytes, SHA-256 `faf4646b27b2e2c50501fb96280aa70741254dba8e7b383e5ede033ab519cb85`,
expected title, external references 0건이다. BuildScope v2 native snapshot SHA-256은
`ee0e59f484a82cbdb09d8085a241929e15b0130e2c51f824c361f808f6c611f5`, ici v1 export deterministic
SHA-256은 `6f0e99872ab0041f174f9b708cb2a0bd5e60569ce06fe825644541c0ae2162c9`, semantic digest는
  `sha256:a7db541ae2daa0c19365f80c1bdbe5090049c86b423000fdf9b6f8e85a857a48`였다. 같은 public
projection으로 16 unit·6 target·14 field group을 대조해 mismatch, checkout leak, raw
`argv`/`command` 모두 0건이었다. 공개 release artifact와 schema/HTML/JSON/checksum evidence는
아래에 기록했으며, same-basename header edge의 local actual compiler 대조도 완료했다.

**I3-5 latest local follow-up evidence (2026-09-01):** Python 3.10 full pytest는 `1,334
passed, 1 skipped`였다. Ruff check/format은 148 files에서 PASS했고, mypy는 88 source files에서
PASS했다. `build-pyz`와 smoke도 PASS했으며, 현재 artifact는 2,166,828 bytes,
SHA-256 `0f82aa95eb940072a735c591737f5b77d9dd16b32751aa03600ad3c5978bb158`이다. 이 수치는
새 actual-process test를 포함한 최신 local evidence이며, 새 PR/CI/Pages remote evidence는
아래에 별도로 기록한다.

### I3-5 remote PR, main, and release evidence

- Feature [PR #110](https://github.com/jihoon22-lee/ici/pull/110)은 head `3ce564a`에서
  `6b44f32869944a0941cab63eb94489b92c543a58`로 병합됐다. [CI run 33448847117](https://github.com/jihoon22-lee/ici/actions/runs/33448847117)은
  required checks와 `Merge Gate`를 모두 통과했고, sticky marker 1개·report link 2개 및 독립 PR
  ici/viewer Pages HTTP 200·correct title·external resource reference 0건을 확인했다.
- Release [PR #111](https://github.com/jihoon22-lee/ici/pull/111)은 head
  [`13d870f`](https://github.com/jihoon22-lee/ici/commit/13d870f6bd8c6bd9ddc89b703e40b1d22b7567f4)에서
  exact main commit
  [`27574109e0f3fc24d6e96eca05bfded4e041d3fa`](https://github.com/jihoon22-lee/ici/commit/27574109e0f3fc24d6e96eca05bfded4e041d3fa)로
  병합됐다. [PR CI run 33450379770](https://github.com/jihoon22-lee/ici/actions/runs/33450379770)은
  all green이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/111#issuecomment-5486185531)는
  marker 1과 두 report link를 기록했다. 독립 PR Pages는 HTTP 200·`text/html`·correct title·
  external reference 0건으로, ici 5,690,362 bytes/SHA-256
  `862c72443ca80040e0bc4524d31c5f5f7e8adb26292faf665f125ce09a9e53af`, viewer 345,176 bytes/SHA-256
  `e6c86558ce00666e8151c1b4020abd26115f3dd6846dca06b275d5b7b75366ff`였다.
- Exact main [CI run 33450906375](https://github.com/jihoon22-lee/ici/actions/runs/33450906375)은 all green이었다.
  main Pages도 HTTP 200·`text/html`·correct title·external reference 0건이며 기존 기록 hash를
  유지했다: ici 5,690,362 bytes/SHA-256
  `99445ff8da2458d6bd5d861d63ae9318db374dfbc60a66bc6cc60ff5cc05894d`, viewer 345,176 bytes/SHA-256
  `4626e354eba2638e07c3c6a254e4ae5cb95291a86c13f4bebe92bef1d892696d`.
- Annotated [`v0.8.0` tag](https://github.com/jihoon22-lee/ici/releases/tag/v0.8.0)는 exact main SHA에
  연결됐다. [Release run 33451310453](https://github.com/jihoon22-lee/ici/actions/runs/33451310453)은
  `Validate Release Provenance`와 `Build & Publish Release`를 모두 green으로 완료했다. Published
  release는 non-draft/non-prerelease이며 정확히 9개 asset을 포함하고, downloaded `ici.pyz` version
  `0.8.0`과 checksum은 GitHub API digest
  `sha256:bb723a30b0ed07936fcf81c7e2b4425832fd86210286b0e6b1b619e1b434142e`와 일치했다.
- Release self/viewer HTML SHA-256은 각각
  `ccfbb3709864c7bf578a0635d66a63b82448304aefd616e1b57a3d9d59038539`와
  `6ee8d2e5b29453155af5e84323a8d829c1bcb3be80c345ab6d99d27b6560412a`였고, correct title·external
  reference 0건 및 두 JSON valid를 확인했다.
- Public v0.8.0 BuildScope verify는 WARN(Pass 11, Warn 2; tests 45/45; TEM 5.00), HTML SHA-256은
  `567957be0fcf978d756116262b4075f1655050902227b0b9d1428fe7a1080b6b`였다. Public export SHA-256은
  `f1d7e1297c773f55777d939a552c11f300a5f59652839f59495037ac227e83d`, semantic digest는
  `sha256:68f86ddf572ba781573f24d8a7319c6abd0f606b980ea1594e9f0616da71e95f`, native v2 snapshot은
  `085f70450cd89171d3fd4011d35ccc35e8658ab5308b64e398ea0b0793c45d8a`였다. Schema validation은
  passed했고, 16 unit·6 target·14 field group에서 mismatch·checkout leak·raw `argv`/`command` key는
  모두 0건이었다.
- 위 I3-5 checkbox는 same-basename active-header edge의 local actual compiler trace 대조가
  완료됐음을 기록한다. BuildScope target-by-target define·standard·include 대조도 public
  projection에서 16 unit·6 target·14 field group mismatch 0으로 완료됐다. 새 테스트의 PR #113
  및 exact-main PR/CI/Pages evidence까지 확인되어 I3 전체가 complete됐으며, 다음 단계는 I4이다.

### I3 same-basename follow-up remote evidence — PR #113 and exact main

The actual-process follow-up was delivered through [feature PR #113](https://github.com/jihoon22-lee/ici/pull/113),
whose head was [`61f613f6cd264327956f65db1dc81d5fe5ef5be7`](https://github.com/jihoon22-lee/ici/commit/61f613f6cd264327956f65db1dc81d5fe5ef5be7).
PR workflow [run 33458308024](https://github.com/jihoon22-lee/ici/actions/runs/33458308024) completed
all checks green, including `Merge Gate`. Its [sticky comment](https://github.com/jihoon22-lee/ici/pull/113#issuecomment-5487193195)
had exactly one marker and two report links; the reported ici result was 1,335/1,335 tests with
TEM 4.84, and viewer was 7/7 tests with TEM 4.89.

Independent PR Pages audits found HTTP 200 `text/html`, exact titles, and zero external references:

- [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/113/): 5,691,035 bytes,
  SHA-256 `4118bd7f42aa16e6082b56ce65a874d668b23c18a20d3c31876d81885e859561`.
- [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/113/): 345,176 bytes,
  SHA-256 `22aff0be7894b4f416169f547ee9862e133ceca55e8caa3bef201e8f924bc2d0`.

PR #113 was squash-merged to exact main
[`c78b40a15a64423f742aa2e75b09d35cc09a5e62`](https://github.com/jihoon22-lee/ici/commit/c78b40a15a64423f742aa2e75b09d35cc09a5e62).
Exact-main [run 33458962715](https://github.com/jihoon22-lee/ici/actions/runs/33458962715) was
SUCCESS, including main `Publish` and `Merge Gate`. Independent main Pages audits also found
HTTP 200 `text/html`, exact titles, and zero external references:

- [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/): 5,690,362 bytes,
  SHA-256 `ef9c2869adebf596ab257a19c30ad1f61352d531ec30fa8df8e0a7ec3020e93f`.
- [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/): 345,176 bytes,
  SHA-256 `8ba214c4c019db341a44719191a721de8c2aa144743f1b2484d60b7021556dd9`.

This closes the I3 checkpoint end-to-end: the feature conditions were complete locally, and the
new same-basename actual-process test now has PR and exact-main workflow/Pages evidence. At that
historical snapshot, the release/version remained v0.8.0; no version bump was made, and the next
planned stage was I4.

---

## 10. I4 — C++·Qt 정밀 분석

### I4-1. compiler/clang-tidy adapter

**브랜치:** `feat/cpp-static-analysis`

- [x] compiler JSON/text diagnostics를 stable rule과 정확한 location으로 파싱한다.
- [x] clang-tidy가 있으면 compilation DB를 사용해 선택 check를 실행한다.
- [x] Clang Static Analyzer check를 별도 category로 구분한다.
- [x] tool config와 project `.clang-tidy`를 존중하고 ici override 우선순위를 문서화한다.
- [x] fix-it은 report에 제안으로 보존하되 기본 실행에서 소스를 수정하지 않는다.
- [x] tool 부재와 compile failure를 분석 무결함으로 처리하지 않는다.

I4-1의 로컬 구현은 I3의 immutable `AnalysisContext`와 normalized
`CompilationUnit`을 단일 입력으로 사용한다. `LintEngine`은 compilation database를 다시
읽거나 source를 재탐색하지 않고, approved capability와 sanitized exact replay를 compiler와
clang-tidy adapter에 함께 전달한다. GCC 9+는 JSON diagnostics를, Clang과 version metadata를
알 수 없는 compiler는 bounded parseable-fixit text를 사용한다. parser는 malformed output을
부분 성공으로 취급하지 않으며, project-relative/external location, stable rule, child/note,
analyzer family와 fix-it을 atomic하게 정규화한다.

clang-tidy는 `auto`/`required`/`off` 정책을 따르고, explicit checks가 built-in defaults보다
우선한다. explicit `clang_tidy_config`는 project-bounded `.clang-tidy` discovery보다 우선하며,
discovery는 project root 위로 올라가지 않는다. config는 project containment, regular-file,
size/NUL 경계를 통과해야 하고 `ExtraArgs`/`ExtraArgsBefore` compiler injection 및
`InheritParentConfig` parent inheritance는 거부한다.
compiler와 clang-tidy/analyzer 결과는 별도 tool evidence와 category로 보고하며, fix-it은
remediation 제안으로만 보존한다. 기본 실행은 source/context를 수정하지 않는다.

보안·증거 경계는 approved external executable, positive replay option allowlist, project/source/
working-directory containment, minimal replacement environment, closed stdin, no-shell 실행과
bounded argv/output/unit/global budget이다. missing 또는 malformed context/output, compile
mismatch, timeout/truncation, spawn/검증 불가능한 종료와 budget 초과는 조용한 heuristic fallback
없이 `ERROR`/`NOT_RUN`으로 fail-closed한다. optional `auto`의 tool 부재는 분석을 무효화하지
않는 경고로, `required`의 tool 부재는 오류로 남긴다. lint cache implementation identity에는
`ici.engines._clang_tidy`, `ici.engines._clazy`, `ici.engines._cpp_diagnostic_categories`와
`ici.engines._cpp_diagnostics`를 포함한 declared helper source digest가 들어가고, project
`.clang-tidy`도 input identity에 포함된다.

compiler와 clang-tidy는 각각 최대 2,048 units, unit당 120초, 전체 600초로 제한한다. context
자체 error가 있으면 compiler replay도 시작하지 않는다. GCC의 위치 없는 command-line/ICE
diagnostic은 `[external]`:1 target으로 유지하며, CI/release에서는 실제 GCC JSON과 clang-tidy
adapter E2E를 필수 도구 gate로 실행한다.

여섯 구현 항목과 focused local contract evidence는 완료됐고, [PR #115](https://github.com/jihoon22-lee/ici/pull/115)의
최종 head `b7ed26c68aa61f2d3f3f8e58afb4556a16c681cd`가 exact `main`
`973cf2423728f9d808873f548bc00c7878cceadd`로 병합됐다. PR
[run 33469332734](https://github.com/jihoon22-lee/ici/actions/runs/33469332734)와 exact-main
[run 33469789628](https://github.com/jihoon22-lee/ici/actions/runs/33469789628)은 실제 GCC와
clang-tidy E2E를 포함한 1,417/1,417 tests, Qt5·Qt6, self/viewer dogfood, report publication과
Merge Gate를 모두 통과했다. PR sticky comment는 marker 1개와 report link 2개를 유지했다.

독립 PR Pages audit는 HTTP 200 `text/html`, 정확한 title, external reference 0건이었다.

- [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/115/): 6,034,768 bytes,
  SHA-256 `f26b34d75a0e0561b48106cf4aaea122f1cd6a558ecc154f02299ac039f38075`
- [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/115/): 345,256 bytes,
  SHA-256 `ae40367d35b7db172b37698422185d3dacf64db83f344860eca6c3a3754c1936`

Exact-main Pages도 같은 HTTP/content/title/Zero-CDN 계약을 통과했다.

- [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/): 5,691,036 bytes,
  SHA-256 `048421ca94e83250da1a4411900a4748b239d2da211b84dd5e4fb9f1ab057af4`
- [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/): 345,176 bytes,
  SHA-256 `6f0e2e10e4a075651c6b893341ab6d2e70798513766c7420179529fe798ed758`

따라서 I4-1의 ici 저장소 local/remote checkpoint는 완료됐다. 아래 v0.9.0 release boundary와
B4 pending 표현은 당시의 historical snapshot이다. 당시 v0.9.1 release와 B4 완료 evidence는
다음 절에 기록한다.

#### Clang-tidy/analyzer related-note aggregation follow-up (PR #134 merged; artifact/Pages audit complete)

[`PR #134`](https://github.com/jihoon22-lee/ici/pull/134)의
`fix/clang-tidy-related-notes`는 head
[`603a00e6574bae1fe2927421a28795780caa7f67`](https://github.com/jihoon22-lee/ici/commit/603a00e6574bae1fe2927421a28795780caa7f67)에서
merge commit
[`b5ebeaecee1737973b407d328bd5d655eca7256a`](https://github.com/jihoon22-lee/ici/commit/b5ebeaecee1737973b407d328bd5d655eca7256a)로
병합됐다. `f999ee3`는 일반 clang-tidy 및 `clang-analyzer-*` 설명용 `note:`를 rule-less이거나
primary와 같은 rule일 때 출력 stream의 바로 앞 primary `CppDiagnostic.related_diagnostics`에
결합한다. 이는 contiguous group 단위의 association이며, 다음 primary가 나오면 새 group을
시작한다. conflicting-rule 또는 orphan note는 partial 결과를 남기지 않고 atomic하게
fail-closed한다. `3fc45a7`은 이 grouping을 linear하게
구성하고, `c3108a7`/`0f46ec5`는 reporter projection과 contract coverage를 추가했다. 후속
reporter/finding 보강은 native-only related evidence, 동일 fingerprint occurrence의 multiset
보존, external non-link, exact line/column과 accessible controls를 고정했고, `e1a665d`는
dogfood가 잡은 Markdown critical complexity를 output-equivalent bounded helpers로 분리했다.

관련 note의 위치·메시지는 `Finding.related_locations`로 보존하고, note fix-it은 primary
remediation과 `extra` metadata에 남긴다. Finding canonicalization은 related location을
canonical project-relative path/region으로 정규화한 뒤 path, line/column region, label 기준으로
deterministic하게 정렬한다. JSON과 HTML은 전체 related inventory를 보존하며 GitHub Markdown은
informational/suppressed finding을 제외한 row를 engine당 최대 100개로 제한하고 생략 사실과
full report 위치를 표시한다. warning/violation/diagnostic-family/finding 집계는 primary만
대상으로 하며, compiler 진단과 Clazy의 rule-owned `ClazyNote` 정책은 변경하지 않았다.

`e86c982`는 이 nested parser contract를 소비하는 compiler-backed function-boundary parser를
보정했다. 해당 structural consumer만 primary와 `related_diagnostics`를 stream 순서대로
읽어 `readability-function-size`의 lines/statements/parameters note를 body geometry mapping에
사용하고, lint finding을 독립 finding으로 되돌리지 않는다. 이는 I4-1 finding noise를 줄이는
correctness follow-up이지 기존 I4-1 체크박스를 새로 닫는 근거가 아니며 I4-3 aggregate도 닫지
않는다.

최종 Python 3.10 gate는 실제 LLVM 21 clang-tidy를 포함해 `1768 passed, 2 skipped`이며,
Ruff check/format과 mypy 98 source files가 통과했다. 두 pyz 빌드는 `2,242,724` bytes와
SHA-256 `3602c2cb1b6998a54f00bf809a88d81617bec58c891bfaf12bf22bc882e71890`로
byte-identical이고 packaged smoke 및 no-cache self-check(exit 0/WARN, complexity max 25,
exact UTF-8 title, Zero-CDN)도 통과했다. 이전 full local attempt의
`1750 passed, 7 skipped, 10 failed`는 function-boundary consumer mismatch를 발견한 historical
evidence이며 `e86c982`로 보정됐다. [PR CI run `33616285870`](https://github.com/jihoon22-lee/ici/actions/runs/33616285870)와
exact-main [run `33617482194`](https://github.com/jihoon22-lee/ici/actions/runs/33617482194)은
각각 `SUCCESS`다. [PR sticky comment](https://github.com/jihoon22-lee/ici/pull/134#issuecomment-5507780937)는
현재 근거로 기록한다. marker `<!-- ici-report -->`는 정확히 1개이고 현재 run 및 report link
2개를 가리킨다. PR artifact/Pages와 main artifact/Pages는 각각 byte-identical이며, 네 Pages는
HTTP 200·`text/html;charset=utf-8`·정확한 title·external `src`/`href` 0건을 통과했다. 네
HTML의 bytes/SHA-256 및 artifact `source_commit`은
[`clang-tidy related-note workthrough`](../../../workthrough/2026-09-02-clang-tidy-related-notes.md)의
canonical table에 고정한다. 버전은 `0.10.2`로 유지하고 release는 만들지 않는다.

### I4 release boundary — v0.9.0 (historical)

annotated `v0.9.0` tag는 exact `main` commit
`061950834a135a30bd5d4e974ec1dfce33df68a9`을 가리킨다. [release workflow 33472668716](https://github.com/jihoon22-lee/ici/actions/runs/33472668716)의
`Validate Release Provenance`와 `Build & Publish Release`가 모두 SUCCESS였고, release는
non-draft·non-prerelease로 게시됐다. 독립 다운로드 audit에서 정확히 다음 9개 asset을 확인했고,
모든 파일이 GitHub API의 size/SHA-256 digest와 일치했다:
`ici.pyz`, `ici.pyz.sha256`, `ici-self-report.html`, `ici-self-report.json`, `viewer-report.html`,
`viewer-report.json`, `icirv`, `icirv-gui`, `icirv-gui.README.txt`.

`ici.pyz`는 `ici 0.9.0`을 보고하고 checksum manifest가 통과했다. 두 JSON은 `ici.result/v3`로
파싱됐고, 두 HTML은 정확한 report title과 Zero-CDN(external asset reference 0)을 만족했다.
`icirv`는 static ELF이며 `ldd`가 `not a dynamic executable`을 반환했고, release viewer JSON을
실제로 파싱했다. 당시 release 조건은 완료됐지만 toy-projects B4가 남아 있어 I4 전체
체크포인트와 I4-2는 미완료였다. v0.9.0 기록은 회귀·provenance 비교를 위해 보존한다.

### I4-1 follow-up — v0.9.1 parser fix와 B4 precondition closure

toy-projects BuildScope B4가 실제 LLVM 18의 located message-less structural `note:`를 발견해,
ici [PR #119](https://github.com/jihoon22-lee/ici/pull/119)에서 bounded parser 보정을 추가했다.
PR #119는 merge commit `74030248345d61c6a394634a9ad9c19b7da4323d`로 병합됐고 exact-main
[run 33482849448](https://github.com/jihoon22-lee/ici/actions/runs/33482849448)가 green이었다.
v0.9.1 release prep [PR #120](https://github.com/jihoon22-lee/ici/pull/120)은
`d6022f613bd997eb557e6af860f5e9b7c6639327`로 병합됐고 exact-main
[run 33484388337](https://github.com/jihoon22-lee/ici/actions/runs/33484388337)도 green이었다.

annotated `v0.9.1` tag object `ebce6307ff51ba14dfb2368f9807ecd24b544578`는 exact main
`d6022f613bd997eb557e6af860f5e9b7c6639327`로 dereference된다. [release workflow
33484950163](https://github.com/jihoon22-lee/ici/actions/runs/33484950163)의 provenance와
publish job이 성공했고, 공개 release는 non-draft·non-prerelease이며 정확히 9개 asset을
독립 감사했다. `ici.pyz`는 2,181,513 bytes, SHA-256
`8668af0eddf117d31e99e25cff4f64b1da68fb5e6d41fb01ef5c9d8107542284`이고 `ici 0.9.1`을
보고했다. 나머지 asset size/digest 및 JSON v3·HTML Zero-CDN·정적 `icirv` 검증은
[인수인계 historical release evidence](../2026-08-30-handover.md)에 고정한다.

released ici v0.9.1을 pin한 toy-projects [PR #36](https://github.com/jihoon22-lee/toy-projects/pull/36)은
head `68ae3b59aacfbd5c57bde2a88718641cd1cfb9e0`에서 [run 33487556779](https://github.com/jihoon22-lee/toy-projects/actions/runs/33487556779)의
16/16 check를 통과했다. sticky comment marker 1개·현재 run·세 report link와 BuildScope/DiskMap/LogLens
hosted HTML의 HTTP 200·exact title·Zero-CDN을 확인했고, B4 PR은 toy main
`590899a0a9430e9ce35162b301bfef5d7dfc78a4`로 squash merge됐다. exact-main [run 33488169769](https://github.com/jihoon22-lee/toy-projects/actions/runs/33488169769)의
14개 prerequisite job과 Merge Gate, [Dependency Graph run 33488174425](https://github.com/jihoon22-lee/toy-projects/actions/runs/33488174425)도
성공했고 feature branch는 삭제됐다. 따라서 B4 precondition은 닫혔다.

### I4-2. Qt clazy와 생성 단계

**구현 및 ici 원격 상태:** `feat/qt-analysis`에서 여섯 코드 조건과 focused local contract를
완료했다. [PR #122](https://github.com/jihoon22-lee/ici/pull/122)의 head
`c3a8fe21639cecef395f0bc28777066401927da0`은 [run `33499500259`](https://github.com/jihoon22-lee/ici/actions/runs/33499500259)에서
1,517/1,517 테스트(네 개 actual compiler/clang-tidy/clazy process E2E 포함), Qt 5/Qt 6
build, self/viewer dogfood, publisher/sticky comment, Merge Gate를 통과했다. 정확히 하나의
현재 sticky comment에 두 report link가 남았으며 PR ici/viewer Pages의 독립 HTTP/title/Zero-CDN
감사도 통과했다. squash merge commit
`9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327` 뒤 [exact-main run `33500281653`](https://github.com/jihoon22-lee/ici/actions/runs/33500281653)도
같은 tool/matrix/dogfood/Merge Gate와 trusted main publication, main ici/viewer Pages 감사를
통과했다. 따라서 ici I4-2 remote acceptance는 완료됐다. 이후 exact-main
`33541134010`과 [v0.10.2 release](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)의
provenance·9개 asset audit도 완료됐다. toy-projects BuildScope B5는 PR #38~#42의 remote
acceptance, exact-main/Pages, [immutable BuildScope 0.5.0 release](https://github.com/jihoon22-lee/toy-projects/releases/tag/buildscope-v0.5.0)
(ID `380863869`)와 public asset audit까지 완료됐다. I4-2 delivery gate는 닫혔지만 I4-3/I4-4가
남아 있으므로 I4 전체 checkpoint는 아직 닫지 않는다.

**구현 브랜치:** `feat/qt-analysis` (PR #122로 squash merge 완료)

- [x] canonical `clazy` capability를 `clazy-standalone` 우선, `clazy` compiler-wrapper fallback
      provider로 탐지한다.
- [x] `clazy = auto|required|off`와 global pipeline profile과 독립적인 명시
      `clazy_profile = level0|level1`을 제공하고, bounded `clazy_checks`로 level2/manual noisy
      checks를 opt-in한다.
- [x] strict `-Wclazy-*` parser와 stable `clazy` family를 추가하고, QObject/connect/signal/slot,
      lifetime/ownership, Qt compatibility/API, container detach/temporary 등의 rule을
      correctness/resource/compatibility/maintainability finding으로 정규화한다.
- [x] exact compilation context에 기반해 CMake AUTOMOC/AUTOUIC/AUTORCC와 qmake의
      `moc_<stem>.cpp`·`<stem>.moc`·`mocs_compilation.cpp`, `ui_<stem>.h`, `qrc_<stem>.cpp`
      산출물과 bounded indirect include/build linkage를 원본 입력 위치로 검증한다. 중복
      generated stem은 오연결하지 않고 WARN으로 닫는다.
- [x] exact include/define/compiler replay evidence에서 Qt 5/Qt 6 major를 식별하고,
      generated compilation unit도 compiler replay하며 successful replay가 있을 때만 linkage와
      compatibility PASS를 기록한다.
- [x] BuildScope의 `.ui`/`.qrc` 및 기존 Qt 앱의 Q_OBJECT 경로에 적용할 verifier contract와
      local fixture shape를 추가했다. clazy는 2,048 units·unit당 120초·전체 600초와 bounded
      output을 사용하며, CI/release workflow는 `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`로 실제
      tool E2E를 요구한다. 실제 BuildScope B5 fixture 검증과 공개 release audit도 완료됐다.

I4-2 full local run은 `1513 passed, 4 skipped`였고, skip은 당시 환경의
`clang-tidy`·`clazy`·`clang++` 미설치에 따른 것이다. 위 PR/main remote acceptance로 ici
측 원격 delivery gate는 닫혔다. v0.10.2 release provenance/artifact evidence는 위 1.3절에
기록했고, BuildScope B5의 released ici 교차 검증·remote acceptance·0.5.0 release audit도
완료됐다. 다음 delivery 범위는 I4-3 잔여 항목과 I4-4다.

### Maintainability 분석 정확도 (I4-3)

**기준 branch (PR #130, merged):** `feat/compiler-backed-cpp-functions`
(`8083267d864d3f29e6f3ae7c53358ce0b1674b44`)
**현재 기준 (PR #131, merged):** `feat(complexity): classify C++ function scopes and metric provenance`
(`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`)

- [ ] complexity/cognitive/function boundary를 AST/tool output 우선으로 바꾼다.
  - [x] Python complexity/cognitive는 nested function/class/lambda body를 enclosing function에서
    제외하고 named nested function/method를 독립 target으로 유지한다. definition-time 표현식과
    comprehension 정책, async loop-state 경계는 regression test로 고정했다.
  - [x] C++ function boundary는 compiler/tool output 우선으로 전환한다.
    - 경계 probe는 bounded source snapshot/cache와 최대 2,048 source files·64 MiB aggregate
      UTF-8 source-bytes cap을 사용하고, replay 전·도구 완료 후 source identity를 재검증한다. 동일 geometry가 성공한
      모든 configuration에 있을 때만 exact로 승격하며, missing/config-dependent boundary는
      partial로 남긴다. same-line/overload, braced declarator·default/noexcept/trailing
      `requires`, function-try/catch, `<%`/`%>` digraph body, assigned `[]`/`+[]` lambda initializer
      phantom 배제와 no-dirfd named-path revalidation을
      regression contract로 고정했다. Approved tool executable은 매 process 직전에 다시 resolve해
      device/inode/mode/size/mtime/ctime identity를 확인하고 변경·부재를 fail-closed한다.
  - [x] C++ cognitive metric은 compiler-backed exact function geometry 안에서만 계산하도록
    전환했다. bounded statement parser는 unbraced nested control flow, `do`/`while`,
    function-try/catch, labels/case, alternative operators, `if constexpr`/`consteval`, attributes와
    digraph를 구분하며 delimiter·statement 구조가 모호하면 위치 있는 오류로 fail-closed한다.
    이 결과의 함수 경계는 compiler-backed이지만 metric 자체는 AST 의미 분석이 아닌
    `bounded-cpp-statement-v1` lexical estimate이므로 상위 aggregate는 계속 열린 상태다.
- [x] template, lambda, operator, macro-generated code 처리 정책을 정한다.
  - source-spelled named function만 target으로 유지하며 function template, conversion/call/subscript
    operator, literal operator의 `function_kind`/template/provenance를 보존한다.
  - lambda는 독립 target으로 만들지 않고 enclosing function의 CC/nesting에서 body를 제외한다.
    expansion-site macro-generated function은 명시적으로 제외하고 count를
    `extra.cpp_scope_exclusions.macro_generated_function`에
    남기며 다음 brace로 매핑하지 않는다. fallback scanner는 operator 이름을 보존하고 multiline
    preprocessor definition/continuation과 standalone macro invocation을 skip한다.
  - 성공 configuration 사이에는 geometry/name/kind/provenance가 일치해야 하며, clang-tidy
    function-size lines/statements/parameters는 configuration별로 보존한다. metric 차이 또는
    conditional-preprocessor body는 `partial`/low-confidence이고, compiler-backed function metrics
    또는 configuration coverage가 partial/low-confidence로 남으면 `required`는 fail-closed다.
- [ ] dead/unused symbol의 broader whole-program/linker-backed evidence가 있을 때만 전역
  판정을 exact로 표시한다.
  - [x] `dead`는 공통 bounded UTF-8 source intake를 사용하고, generated/vendor 기본 제외와
    `include_generated`/`include_vendor` literal-boolean opt-in, 8,192 unique candidate·2,048
    owned/analyzed files·파일당 8 MiB·aggregate 64 MiB 한도를 적용한다. policy-excluded file은
    owned cap을 소비하지 않으며, lexical normalization/deduplication/sorting, NUL rejection,
    fallback symlink precheck와 second-read stability 검사를 거친다. 현재 AST
    reachability/name-reference 결과는 위치 target과 함께 `ESTIMATED`/`python-ast-heuristic`으로
    남기고 Python discovery를 한 번만 캡처해 재사용한다.
  - [x] **C++ translation-unit unused-function compiler diagnostic slice**: `[engines.dead]`
    의 `cpp_unused = "auto" | "required" | "off"` 정책을 Python dead-code 경로와 독립적으로
    추가했다. 전체 `verify`와 standalone `ici dead`는 동일한 shared immutable
    project/tool/compilation preflight/context model을 사용한다. standalone 명령은 `dead`가
    선택한 capability와 configured `[doctor].required_tools`만 probe하도록 범위를 좁힌다.
    `cpp_unused = "off"`이면 C++ path를 discovery에서만 식별하고 candidate bytes를 읽거나
    source intake에 넣지 않는다. standalone 명령은 compilation context/tool probe를 요청하지
    않고 C++ scope만 건너뛴다. exact context의 compilation database에서 owned 및 configured
    external compilable C/C++ translation unit을 모두 고르고, source가 모든 known configuration에
    포함되며 각 unit과
    database가 canonical `sha256:` identity를 가질 때만 replay한다. compilation-database digest는
    preflight에서 캡처한 immutable context snapshot의 identity이며, database는 copied/frozen
    `CompilationUnit` 값으로 변환된다. engine은 그 값을 replay하고 실행 중 live
    `compile_commands.json`을 다시 읽지 않으며, 이후 database mutation은 다음 preflight에서
    새 digest/context로 관찰한다. 각 unit configuration digest는 directory/argv/output에서
    다시 계산해 identity와 대조한다. context의
    `unity_build=true`, 누락된 source configuration, context ingestion error 또는 replay할
    approved compiler 부재를 추측으로 보완하지 않는다. 선택된 unit의 language는 정확히 `c` 또는
    `c++`여야 하며, unknown/empty language와 Objective-C 계열 unit은 compiler 실행 전에 거부한다.
  - [x] compiler replay와 진단 수집은 원래 compile command의 warning-as-error/suppression
    정책을 diagnostic-only projection으로 정리한 뒤 `-Wunused-function`,
    `-Wno-error=unused-function`, discarded `-S -o os.devnull`을 사용한다. object, executable,
    project artifact 또는 linker result를 만들지 않으며, capability-approved GCC/Clang
    driver와 approved alias만 허용한다. 관측된 family가 GCC이고 version 9 이상이면 structured
    JSON diagnostics를, older GCC와 approved Clang-family driver/alias는 bounded parseable-text diagnostics를
    사용해 공통 위치 계약으로 정규화한다. 같은 executable의 관측된 Clang family는 `g++` alias
    spelling보다 우선하고, project rule-visibility 설정 대신 controlled
    `-fdiagnostics-show-option`을 강제한다. compiler family는 bounded version banner에서 식별해
    neutral executable이나 `g++` 이름의 Clang alias를 filename/numeric version만으로 GCC로
    오인하지 않는다. option separator 뒤에는 canonical source operand 하나만 허용하며 extra
    operand, second separator, operand 위치의 `-w`는 `extra-compiler-operand`로 실행 전에
    거부한다. source와 working-directory 및 approved compiler executable identity를 replay
    전·후에 재검증하고, GCC standard-library include projection cache도 compiler identity와
    replacement-sensitive working-directory identity(device/inode/mode/mtime/ctime)를 key에
    포함해 lookup 전, cache hit 반환 전, probe 후 cwd를 재검증한다. accepted finding은 선택된 source의 compiler-attributed
    `(path, start/end line, start/end column)` 범위와 정확한 `-Wunused-function` rule에
    한정한다. compiler logical path가 selected TU와 정확히 같고 line/column 범위가 immutable
    source snapshot 안에 있어야 한다. 범위를 벗어난 `#line`/macro remapping은 fail-closed하며
    physical macro-definition origin은 재구성하지 않는다. 같은 source의 모든 configuration에서
    동일한 diagnostic 위치 집합이 관찰될 때만 exact finding을 만들며, clean source도
    configuration 수를 가진 PASS target으로 남긴다. 실행별 tool argv/version/status는
    `ToolEvidence`에, finding별 normalized `diagnostic_message`는 `cpp_unused_details`에
    분리해 보존한다.
  - [x] 이 경로는 atomic fail-closed 계약을 사용한다. missing/invalid context 또는 canonical
    digest, compilation ingestion/coverage/configuration 오류, unsafe replay, compiler
    nonzero/비정상 종료, malformed·truncated output, timeout, source/compiler identity 변경,
    configuration별 진단 불일치와 bounded budget 초과는 partial finding을 남기지 않고
    `ERROR`/`NOT_RUN`으로 닫는다. matching `-Wunused-function` warning이 source location 없이
    오거나 위치를 판별할 수 없으면 excluded count로 세지 않고 fail-closed한다. C++ probe는
    atomic하며 replay/process/parser 오류에는
    fail-fast로 첫 오류 뒤 남은 compiler unit을 실행하지 않고, 이미 모은 C++
    observation/finding도 폐기한다. 최종 merge에서 발견된 configuration disagreement도 모든
    C++ finding을 폐기한다. 이미 성공적으로 완료·기록된 compiler observation의 source에는 exact PASS/WARN 대신 위치가 있는
    `C++UnusedFunctionsInvalidated` `SKIP` target을 남겨 폐기 범위를 추적한다. hybrid에서 이미 완료한 Python finding은 유지하며 Python은
    `ESTIMATED`/`python-ast-heuristic`, native C++ finding은 `EXACT`와 compiler/tool-rule
    attribution(`tool_name`, `tool_rule_id = "-Wunused-function"`)을 보존한다. 두 scope가 모두 완료되면 aggregate evidence는 보수적으로
    `ESTIMATED`이고, C++ 실패 시 aggregate status/evidence는 그 결과를 반영한다. C++-only project에서
    `auto`의 exact context/tool 부재는 `required = false`인 unavailable/`NOT_RUN`과 suite optional
    `WARN`으로 남기고, `required`의 동일 조건은 `ERROR`/`NOT_RUN`, `off`는 C++ intake/probe만 끈다.
    context가 존재한 뒤의 invalid
    context/coverage/configuration/replay/parser/identity 오류는 `auto`와 `required` 모두
    `ERROR`/`NOT_RUN`으로 닫는다.
  - [x] `extra.language_evidence`와 provenance는 Python과 C++ 범위를 분리한다. Python AST
    dead-code는 `ESTIMATED`/`python-ast-heuristic`, 정상 C++ compiler probe는
    `MEASURED`/`cpp-compiler-unused-function`으로 기록하며, hybrid aggregate evidence는
    두 scope가 모두 완료된 경우 보수적으로 `ESTIMATED`를 유지한다. C++ native finding의
    confidence와 compiler/tool-rule attribution 및 support matrix는 그 언어 범위에서만
    `EXACT`/tool-backed로 표시한다.
  - [x] non-TU/header/external-location diagnostic은 `cpp_unused_non_tu_diagnostics_excluded`
    count로만 보존하고 finding으로 만들지 않는다. 단, 이 count는 exact
    `-Wunused-function` warning이 선택된 TU 밖을 가리킬 때만 증가하며 다른 diagnostic은
    세지 않는다. macro-generated definition은 compiler가 귀속한 expansion 위치를 그대로
    유지하며, external-linkage symbol, template,
    inline/COMDAT definition, linker reachability, dynamic lookup, plugin entry point와 Qt
    meta-object reachability는 이 TU-local probe가 분류하지 않는다. generated/autogen,
    `moc_`/`qrc_`/`ui_`/`.moc`, vendor/dependency 입력은 공통 source-ownership 정책상 기본
    제외되며 이 slice가 Qt generated-code linkage를 대신하지 않는다.
  - [x] `dead` 결과(Python-only, C++-only, hybrid)는 cache key 계산, load, store를 모두
    비활성화한다. C++ compiler-backed 결과는 external/generated include closure와 compiler
    binary content가 cache identity에 모델링될 때까지 안전하게 재사용할 수 없다.
  - [x] **이 slice의 local acceptance gate (remote acceptance와 별개):** focused regression은
    `607 passed, 6 skipped`, Python 3.10 `uv run --python 3.10 pytest -ra`는
    `1,966 passed, 7 skipped`, Ruff check와 format은 184 files에서 pass, mypy는 104 source
    files pass였다. 두 pyz build는 각각 2,273,944 bytes, SHA-256
    `2a3c8b011e53d21529ee03e20b0f7eeafbf7fbfaf6b8a9e35f5445b166c88d28`로 동일했고, smoke는
    pass, packaged self verify는 exit 0이었다. no-cache self verify는 WARN, 14 engines (8 PASS,
    5 WARN, 0 FAIL, 0 ERROR, 1 SKIP), TEM 4.84였고 HTML 5,526,617 bytes
    (`159ba3db668127541c4ff56ebc535138fbd5541ad86eccad45879e606e50742d`), JSON 15,590,867
    bytes (`0d38d3b9daa92977b3933dd3b2bbf58531b52134d87f462d7a9271b659affe1a`)와 title
    `ici Verification Report — ici`/Zero-CDN을 확인했다. viewer standalone `dead --report`는
    configured `cpp_unused = "required"` 아래에서 PASS/MEASURED/exact 8/8 source/configuration,
    0 functions, cache key null, success evidence
    8건이었다. viewer deep no-cache는 `clang-tidy`/`clazy` 미설치만으로 WARN, 14 engines (12 PASS,
    1 WARN, 0 FAIL, 0 ERROR, 1 SKIP), TEM 4.89였고 HTML 355,996 bytes
    (`9098bec837b61d2ed08c15cdb21b4b4f59741a160eb0a09dfb74d8163bb33d8c`), JSON 743,422 bytes
    (`069eb0dced6c835c2690b8d45da2216ffb15af233b5dc7a3f92e609fd90d67ad`)와 title
    `ici Verification Report — viewer`/Zero-CDN을 확인했다. 로컬 경로는 기록하지 않으며, 이
    수치는 remote PR acceptance 수치가 아니다.
  - [x] 후속 hardening은 `8f8f4d0` (compiler family alias detection), `7522fe7` (strict
    option-separator operands), `88c18da` (alias-family regression coverage), `3a38997`
    (compiler/cwd-bound include-projection cache identity), `ea1d4b5` (project formatting),
    `2b7ff41` (automatic C++ analysis scope), `13099ca` (translation-unit language guard)로
    반영했다.
  - [x] **GNU ELF target-local linker discarded-function slice**를 별도
    `[engines.dead].cpp_linker = "auto" | "required" | "off"` 정책으로 추가했다. 이 slice는
    root CMake project의 isolated Release shadow에서 capability-approved GCC driver와 GNU
    `ld`를 확인하고, direct-object executable link command에 `-ffunction-sections`,
    `--gc-sections`, `--print-gc-sections`, no-PIE/no-LTO를 적용한 뒤 GNU `ld`가 실제로
    discard한 project-owned local/hidden function section만 `addr2line`으로 위치화한다. 단일
    symbol/section/target 매핑과 source location이 확인될 때만 `EXACT`/`MEASURED` finding을
    만들고, ambiguous/COMDAT/clone/default-visible/archive/shared/LTO/linker-script 경로는
    제외한다. `auto`의 unavailable context/tool은 `SKIP`/`NOT_RUN`, `required`는
    `ERROR`/`NOT_RUN`, `off`는 probe 자체를 끈다. 이 결과는 여러 object/library의 전역
    reachability 증명이 아니다.
  - [x] 위 linker adapter에는 discovered `link.txt` 256개, link file 4 MiB, argument
    32,768개/1 MiB, direct object 4,096개, tool-output cap 4 MiB, discarded section 16,384개,
    command 180초/전체
    900초의 bounded contract와 shell/response-file/unsafe linker flag 거부, owned shadow 및
    source mapping 검증을 적용했다. `examples/cpp-fixtures/cmake_elf_dead`는 live entry/leaf와
    discarded `dead_leaf`/hidden `dead_entry`를 함께 가진 CMake fixture이며, focused real-tool
    E2E에서 두 discarded 위치만 finding으로 보고하고 live/main은 discarded
    symbol로 보고하지 않는다(PASS scan target은 남을 수 있다).
  - [ ] **Whole-program/linker-backed dead-symbol reachability**는 아직 pending이다. 여러
    object/library를 연결한 결과, external/dynamic lookup, plugin 또는 Qt meta-object 경로를
    포함하는 전역 unused-symbol 판정은 별도 범위로 남긴다. 위 target-local GNU ELF 증거를
    broader whole-program deadness의 완료로 해석하지 않는다.

  - [x] **이 C++ TU-local slice의 remote acceptance:** descriptive PR #137,
    `feat(dead): add compiler-backed C/C++ unused-function evidence`의 required checks가
    [workflow run `33675765436`](https://github.com/jihoon22-lee/ici/actions/runs/33675765436)에서
    통과했다. PR head는 `9c9d83cdaae02384bbc58e7cb79b4bbb098b86d3`, synthetic merge source는
    `f2cfce8b8a7ebc90308bb442f3a323e01ed9ef34`다. current-run sticky comment는 정확히 하나
    ([comment `5515582296`](https://github.com/jihoon22-lee/ici/pull/137#issuecomment-5515582296))였고
    report link 두 개를 포함했다. PR artifact와 PR Pages copy는 byte-identical이며, synthetic
    merge `source_commit`, 정확한 title, UTF-8, Zero-CDN을 통과했다.

    | Report | PR HTML bytes / SHA-256 | PR JSON bytes / SHA-256 |
    | --- | ---: | ---: |
    | ici | 5,188,748 / `8648d7ac06fded3afaa004568a9665bb3bc2b10c7e41f1da06af41b0eb3952f8` | 15,288,643 / `f9401da10828ab3d0c1c6b9430789d25b4ef4ac15e8dbe410f0f244a584aefef` |
    | viewer | 363,787 / `0123db7d6e5c820fc0bd952a0fd55b82752b63d873b4f0502e12f676b3e71cda` | 905,151 / `edde8208502d4af5c060e556ece1650518893c7274487cca2283c02f63322f98` |

    PR #137은 `782589a4ef02209703e882a09cc0d8b0c7940218`로 squash merge됐고 feature branch는
    삭제됐다. squash merge 뒤 exact-main [workflow run `33676873412`](https://github.com/jihoon22-lee/ici/actions/runs/33676873412),
    Pages build API run `1190632325`, Pages workflow run
    [`33677689026`](https://github.com/jihoon22-lee/ici/actions/runs/33677689026)이 모두 성공했다.
    main artifact와 main Pages copy도 byte-identical이며 merged main `source_commit`, 정확한 title,
    UTF-8, Zero-CDN을 통과했다.

    | Report | Main HTML bytes / SHA-256 | Main JSON bytes / SHA-256 |
    | --- | ---: | ---: |
    | ici | 5,188,748 / `7d9a23d5eb47bcf0ab82f074a85e65eb264869f8f0333318673890d75b0c4eaf` | 15,288,649 / `99d5c208a30518e0c356c4e9a26b2306a99468d51369dd91e9eaa19b71a22e19` |
    | viewer | 363,788 / `223c027a6cbbef5aa08c464f210286c6a90ae2a702451739aa94bf704648188f` | 905,152 / `152e6c2f6d2b53728f39680b3198b5fb46d1c28e915731c6a7693f85c0175557` |

    이 acceptance는 narrow TU-local compiler-diagnostic slice만 닫는다. broader
    linker/whole-program dead-symbol analysis와 C++ AST/semantic duplicate analysis, behavioral
    equivalence, I4-3 aggregate/I4 checkpoint는 계속 pending이다. 버전은 `0.10.2`로 유지하며 이
    slice를 위한 release는 만들지 않는다.
- [ ] duplicate는 generated/moc/vendor code를 기본 제외하고 token/region fingerprint를 통합한다.
  - [x] 공통 intake의 generated/moc/vendor 기본 제외와 두 independent literal-boolean opt-in을
    적용하고, overlapping classification은 두 opt-in이 모두 켜져야 포함한다. owned C/C++
    headers를 포함하고 standalone `.moc`는 discoverable하지만 기본 제외하며, Python과 C/C++
    matching을 언어별로 격리하고 당시의 `sha256/type2-region-v1` fingerprint와 PASS 위치 target을
    보존한다. unique excluded file count와 reason별 overlapping count를 구분하며 현재
    source-intake slice의 token/region 분석은 `ESTIMATED`/`token-region-heuristic`이었다.
  - [x] language-aware lexical tokenization slice를 구현하고 remote acceptance를 완료한다. Python과 C/C++를 language
    key로 격리하고, Python의 line-preserving token/AST context와 C/C++의 line-preserving lexer,
    line-splicing/directive 처리를 사용한다. function/class/import 또는 function/preprocessor
    region boundary를 넘지 않는 shared normalized-window seed, rolling-hash 뒤의 exact equality
    확인, bounded extension과 maximal-match deduplication을 적용한다. 현재 fingerprint는
    `sha256/type2-region-v2`이고 결과 provenance는
    `language-lexical-region-heuristic`/`ESTIMATED`다.
  - [x] **bounded Python AST-shape duplicate slice**를 `[engines.dup].python_semantic =
    "auto" | "required" | "off"` 정책으로 추가했다. Python 3.10 AST의 named function,
    async function, method, class region을 line-preserving source target으로 추출하고,
    nested named scope는 parent shape에서 prune한 뒤 별도 region으로 보존한다. local binding은
    alpha-normalize하지만 source-spelled import/name/attribute anchors, operators,
    control-flow, literals, defaults,
    annotations, decorators와 recursion anchor는 보존하며 canonical shape를
    `sha256/semantic-shape-v1`로 fingerprint한다. lexical group과 중복되는 occurrence는
    억제하고, callable region만 bounded semantic group으로 추가한다.
  - [x] Python semantic shape는 `eval`/`exec` 호출과 그 이름의 literal `getattr` lookup,
    `global`/`nonlocal`, star import,
    lambda/comprehension scope, malformed 또는 unsupported AST를 보수적으로 제외한다.
    제외 위치와 이유는 `extra`에 남기고 `required` 오류는 위치 있는 `InspectionTarget`으로
    만든다. `auto`는 usable region이 있으면 `partial`, `required`는 exclusion을
    `ERROR`/`NOT_RUN`, `off`는 `off`로 보고한다. 파일
    256개, named region 20,000개, AST node 500,000개, serialized shape 16 MiB 한도를 넘으면
    partial region을 내지 않고 fail-closed한다. 이 slice는 C++ AST/semantic duplicate analysis,
    near-clone edit equivalence 또는 behavioral equivalence를 주장하지 않는다.
  - [ ] C++ AST/semantic duplicate analysis, behavioral equivalence와 broader
    whole-program/linker-backed dead-symbol evidence는 아직 pending이다. bounded Python AST
    shape와 target-local GNU ELF 증거만으로 I4-3 전체를 완료로 표시하지 않는다.
- [x] heuristic parser는 tool 없는 fallback으로 남기고 confidence를 낮춘다.

이 source-evidence slice는 PR #133으로 `main` (`fdc797a0c71c46d9301db2569928468ff42e24af`)에
squash merge됐다.
2026-09-02 Python 3.10 focused evidence는 source-input 79 tests passed, 직접 관련
config/dead/dup bundle 238 tests passed다. 전체 local suite는 `1764 passed, 2 skipped`
(1766 collected)로 green이다. Ruff check/format과 mypy(98 source files)는 clean이고, 두 번의
`dist/ici.pyz` 빌드는
`2240881` bytes와 SHA-256
`715bddd5d76540f97d6f78c9349a5177ce5935a80925a5761ea39fb0988d9b0d`로 byte-identical이며
packaged smoke wrapper는 PASS다. self verify는 exit 0의 WARN(Pass 7, Warn 5, Fail 0,
Error 0, Skip 1)이며 test `1764/1766`, TEM `4.84`, line/function/branch `89.1%/96.8%/81.5%`,
HTML `7763578` bytes, 정확한 title 및 외부 resource 0개를 확인했다. 버전은 `0.10.2`로
유지하고 release는 만들지 않는다.

구현 PR [#133](https://github.com/jihoon22-lee/ici/pull/133)은 `fix(analysis): make heuristic
source evidence bounded and deterministic` 제목으로 squash merge됐다. 첫 implementation
[workflow run `33605000619`](https://github.com/jihoon22-lee/ici/actions/runs/33605000619)은
`Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`, `Publish PR Report &
Sticky Comment`, `Merge Gate`를 포함한 모든 required check가 green이었다. [sticky comment](https://github.com/jihoon22-lee/ici/pull/133#issuecomment-5506324653)는
`github-actions` marker/current-run 댓글 정확히 하나를 유지한다. PR artifact에서 추출한 HTML과
PR Pages는 byte-identical이며, 두 Pages 응답은 UTF-8 exact title과 Zero-CDN을 통과했다.

| Report | HTML bytes | SHA-256 | Pages/title |
|---|---:|---|---|
| ici | 7,701,814 | `071d83ef1fac4d39102bcb8eecad68d614dda736d74a6b3a93b210c9feecf38b` | [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/133/) — `ici Verification Report — ici` |
| viewer | 358,047 | `9e7e295e8d28fe0633039f58099c82a5914d30cb6fcd8c9f2ba82d25e84c4305` | [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/133/) — `ici Verification Report — viewer` |

병합 결과 main은 [`fdc797a0c71c46d9301db2569928468ff42e24af`](https://github.com/jihoon22-lee/ici/commit/fdc797a0c71c46d9301db2569928468ff42e24af)이며,
exact-main [run `33607859423`](https://github.com/jihoon22-lee/ici/actions/runs/33607859423)의
모든 required check가 성공했다. main artifact와 Pages는 byte-identical이고 UTF-8 exact title과
Zero-CDN을 통과했다.

| Report | HTML bytes | SHA-256 | Main Pages/title |
|---|---:|---|---|
| ici | 7,701,815 | `dc2f0c83206881eccb83a41dde336c1656ab78bb7858675090319079a9ab212a` | [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/) — `ici Verification Report — ici` |
| viewer | 358,047 | `a212609c54fe6fa10cd8f6abe3318c0094f9b3fd23ba9b7570f59f46612d1d30` | [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/) — `ici Verification Report — viewer` |

PR #133의 local/remote branch는 병합 후 삭제됐다. 이 evidence는 source-evidence 구현 delivery를
닫지만 broader whole-program/linker-backed dead-symbol evidence와 C++ AST/semantic 또는 behavioral
duplicate analysis는 여전히 pending이므로 I4-3 또는 I4 전체 checkpoint를 닫지 않는다. 버전은
`0.10.2`로 유지하고 새 release는 만들지 않는다.

### Language-aware duplicate tokenization — PR #135 accepted (2026-09-03)

이번 duplicate slice는 PR #135 run `33647055492`와 historical squash merge `b09af5e`의 exact-main run
`33648359498`을 모두 통과했다. single sticky comment, artifact/Pages byte match, exact UTF-8
title, Zero-CDN, PR synthetic merge 및 exact-main `source_commit`을 독립 확인했다. 정확한
local test, DiskMap/BuildScope/LogLens/ici 측정값, 원격 artifact hash는
[`bounded language-aware duplicate workthrough`](../../../workthrough/2026-09-02-bounded-language-aware-duplicate.md)에
고정한다. 이 acceptance는 bounded lexical/token-region 하위 범위만 닫으며 C++ AST/semantic
duplicate 및 broader whole-program/linker-backed dead-symbol evidence는 계속 pending이다. 이
후속 combined slice의 bounded Python AST-shape 결과는 PR #151에 병합되어 현재 `main`에
포함됐으며, C++ AST/semantic duplicate와 behavioral equivalence는 여전히 pending이다. 버전은
`0.10.2`로 유지하고 이 slice에 대한 release는 만들지 않는다.

### Combined maintainability slices — PR #151/#152 merged; current main accepted (2026-09-04)

PR #151은 `6f2ba70`의 GNU ELF target-local linker discarded-function evidence와 `5c0224e`의
bounded Python AST-shape duplicate analysis를 `e6b20a6` CLI regression 계약 및 `36f30cc`
Linux-only 실행 경계와 함께 병합했다. PR #151은
[`c87e4075634fceea0770435e7a513787c10122ed`](https://github.com/jihoon22-lee/ici/commit/c87e4075634fceea0770435e7a513787c10122ed)로
완료됐고 PR CI [`33746819016`](https://github.com/jihoon22-lee/ici/actions/runs/33746819016)과
exact-main [`33747985638`](https://github.com/jihoon22-lee/ici/actions/runs/33747985638)이
성공했다. 이어 PR #152는 project-respecting Python tool policy, bounded security/resource AST,
configured runtime compatibility와 display-only issue projection을 보강해
[`2113b5ddc21905660afa4002ee8b25af6a8f5fcf`](https://github.com/jihoon22-lee/ici/commit/2113b5ddc21905660afa4002ee8b25af6a8f5fcf)로
병합됐고 PR CI [`33763034968`](https://github.com/jihoon22-lee/ici/actions/runs/33763034968)과
exact-main [`33763926843`](https://github.com/jihoon22-lee/ici/actions/runs/33763926843)이
성공했다. 따라서 두 slice와 #152의 보강은 현재 main에 반영됐으며, 더 이상 local-only 또는
PR/CI pending으로 표시하지 않는다. 현재 두 head를 대상으로 한 Quality Zoo candidate run은
없으므로 acceptance는 위 1.4절의 exact-head 경계를 따른다.

- GNU linker slice는 `[engines.dead].cpp_linker`를 기존 `cpp_unused`와 독립적으로 적용하며,
  CMake direct-object executable, GNU `ld`, local/hidden uniquely mapped function section과
  compiler/source-owned location만 exact로 인정한다.
- Python slice는 `[engines.dup].python_semantic`을 기존 lexical detector와 병행하고,
  deterministic canonical AST shape와 bounded exclusions/limits를 보존한다. aggregate duplicate
  status/evidence는 기존 보수적 계약을 유지한다.
- 전체 I4-3 aggregate, C++ AST/semantic duplicate analysis, behavioral equivalence,
  whole-program deadness/linker reachability 및 I4 checkpoint는 닫지 않는다.
- Pre-merge local evidence: related focused bundle `362 passed`; full Python 3.10 suite `2295 passed,
  7 skipped`; Ruff check/format pass. 버전은 `0.10.2`로 유지하며 release/tag/version bump는
  없다.

PR #130의 historical compiler-boundary baseline은 두 번 byte-identical인 candidate SHA
`7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`와 Python 3.10
`1,626 passed, 2 skipped`를 남겼다. 이후 local `feat/cpp-function-scope-policy` candidate는
두 번 byte-identical인 `dist/ici.pyz` SHA
`2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7`이며, real extracted
`clang-tidy-21`을 사용한 Python 3.10 full suite `1,656 passed, 2 skipped`, Ruff check/format,
mypy와 packaged smoke가 통과했다. 최초 PR run에서 드러난 1,031-pure-code-line self gate는
parser/source mapping helper 628줄과 process runner compatibility facade 487줄로 분리해
닫았고, 집중 회귀 89개와 전체 suite가 같은 결과를 유지했다. 이 SHA를 fresh clean
`toy-projects` `main`에 주입한 BuildScope deep `auto`/`required`, DiskMap `auto`, LogLens
`auto`의 local cross-repo candidate evidence와 JSON/HTML report, 4/4 title·Zero-CDN checker
pass는 [scope-policy workthrough](../../workthrough/2026-09-02-cpp-function-scope-policy.md)에
기록한다.

PR #131 `feat(complexity): classify C++ function scopes and metric provenance`는
`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`로 squash merge됐다. PR CI run `33592482495`와
exact-main run `33593218450`은 성공했으며, PR run은 exactly one sticky marker/current run을
남겼다. PR ici/viewer Pages는 HTTP/title/Zero-CDN과 artifact byte-match를 통과하고
`7,454,995`/`356,598` bytes였고, exact-main JSON/main `source_commit`은 같은 SHA와 일치했다.
main Pages도 같은 검사를 통과하고 ici `7,454,995` bytes/SHA `182a0d05…5adbb75`, viewer
`356,598` bytes/SHA `fb772d4a…c0c4794`로 byte-match됐다. 두 run에서 skip된 것은 예상된
PR/main publish job뿐이다. 이 evidence는 I4-3 aggregate, broader dead/unused-symbol reachability,
C++ AST/semantic/behavioral duplicate analysis, 남은 I4-4 또는 I4 전체 checkpoint를 닫지 않는다.

### I4-4. C++ safety

**Sanitizer normalization accepted slice:** `feat/sanitizer-diagnostic-normalization`은 PR #142로
병합됐고 exact-main 및 sanitizer candidate acceptance를 완료했다.

**TSan accepted slice:** `feat/thread-sanitizer-deep-profile`은 PR #146에서
`cfd706605bad57cf5476de9af06ed98322605d13`으로 병합됐다. PR run `33717584710`, exact-main run
`33718399268`과 아래 exact candidate Quality Zoo acceptance가 완료됐다. TSan은 `-tsan`
shadow와 `-fsanitize=thread -fno-omit-frame-pointer -g`, CMake/qmake adapter 및 generic
`-pthread` link를 사용하고, Python scope는 unsupported다. 기존 `TSAN_OPTIONS`는 보존하면서
`halt_on_error=1`을 추가하며, bounded `WARNING`/`SUMMARY` parser·project location 검증·외부
frame redaction·stable known/unknown defect rule을 적용한다. 실제 g++ race regression과 별도
category/Qt candidate evidence도 각각 수용됐지만 broader I4-4와 version `0.10.2` 및 release
상태는 변경하지 않는다.

- [x] ASan/UBSan/LSan 결과를 structured sanitizer kind와 defect, 검증된 project-owned primary
  location, related stack-frame locations(프로젝트 밖은 `[external]`로 redacted), 그리고 연결된
  process evidence로 정규화한다. bounded/private transport, timeout·truncation·unlocated
  diagnostic은 fail-closed한다.
- [x] TSan은 별도 deep profile과 build variant로 제공하며 PR #146, exact-main CI, toy PR #56,
  exact candidate Quality Zoo acceptance까지 완료했다. 이 체크는 TSan sub-scope만 닫는다.
- [ ] resource/lifetime/security는 clang analyzer·clang-tidy·clazy 결과를 category별로 매핑한다.
- [x] sanitizer가 build됐지만 테스트가 실행되지 않은 경우 ERROR로 구분한다.
- [x] Quality Zoo의 ASan UAF, LSan leak, UBSan signed-overflow 및 sanitizer-clean fixture가
  exact candidate acceptance에서 expected rule/status/evidence/confidence/path/line 계약을
  검증한다.
- [ ] Qt lifetime/ownership scenario와 broader resource/lifetime/security taxonomy를 검증한다.

**TSan exact candidate acceptance (별도 완료 evidence):** ici SHA
`6ee08b14fa598a19074af7afed4368fd79b19b2b`에서 생성한 candidate artifact `9884927798`의 raw
ZIP SHA-256은
`9a50972a5cb4ad96b2b0cf912e27c17a600fc19d6d899c6e33028d4449b1122d`이며, exact toy-projects
target SHA는 `d0b84d376d3f736da86308a49d21d8600297eb27`이다. [Quality Zoo workflow run
`33737405098`](https://github.com/jihoon22-lee/ici/actions/runs/33737405098)은 `success`로
완료됐고, 8/8 scenario contracts가 `PASS`, `errors`는 `0`이었다. 별도 acceptance artifact
`9886336618`의 ZIP SHA-256은
`70f298a33a251241033882a5bd1eea1a7f863dd86c1939321d531cee39b32bf3`이다. 이 evidence는 TSan
candidate consumer의 exact contract만 닫으며, 현재 ici `main@2113b5d`에 대한 candidate 재실행이나
broader I4-4 전체 완료를 주장하지 않는다.

**C++ diagnostic category slice (PR/main and candidate acceptance complete):** normalized
`family`/`tool_rule_id`만 사용하는 isolated `_cpp_diagnostic_categories.py`의 보수적
`tool-rule-v1` projection을 병합된 구현에 담는다. free-form message는 입력하지 않고,
family별 ordered rules와 bounded clazy stems 뒤에 보수적인 fallback을 적용한다. 정확한 rule
목록과 precedence는 [사용자 가이드](../../user-guide.md#c-diagnostic-category-policy)를
canonical reference로 삼는다. lint `extra`에는 policy ID와 모든 category count가 포함되고,
분류 helper source는 cache identity에 반영된다. focused C++ lint/tidy/clazy `160 passed`, cache
identity/store `51 passed`, Ruff PASS를 확인했다. PR #145는 `e7a9f55`로 병합됐고 PR run
`33713591229`, exact-main run `33714515219`, six-scenario category/Qt candidate run
`33718024450`이 통과했다. 이는 category projection과 해당 Qt scenario의 exact scope만 닫으며,
broader resource/lifetime/security mapping과 release는 여전히 별도다.

---

## 11. I5 — Python 정밀 분석과 호환성

현재 내장 pipeline은 16종 descriptor를 제공한다. `fast`는 12종, `standard`는 14종,
`deep`는 16종을 선택하며, `python_compat`는 세 profile 모두에 포함된다. 아래 I5 구현은
이 profile count를 바꾸지 않고 Python 범위와 reporter contract만 확장한다.

### I5-1. Ruff와 mypy의 프로젝트 설정 존중

**브랜치:** `fix/python-tool-config`

> 2026-09-01 BuildScope B0 preflight에서 hybrid source root 전체를 mypy에 넘겨 exit 2가
> 발생하고, PATH의 Python tool과 선택 interpreter module이 불일치할 수 있음이 재현됐다.
> v0.7.1 선행 수정은 mypy 대상을 Python-containing root로 제한하고 pytest/coverage/mypy
> capability를 선택 interpreter의 `-m` probe로 통일한다. 아래 project-config 존중 작업을
> 완료한 것으로 표시하지는 않는다.

- [x] Ruff JSON output과 rule code를 직접 파싱한다. JSON format capability가 없는 formatter는
  strict legacy parser로만 제한하고 임의 출력을 성공으로 인정하지 않는다.
- [x] project `pyproject.toml`/ruff config의 select, ignore, per-file policy를 보존한다. ici는
  scoped relative path와 structured output format만 지정하고 rule policy를 override하지 않는다.
- [x] mypy의 project config를 project-root working directory에서 자동 발견하게 하고 전역
  `--ignore-missing-imports` 강제를 제거한다.
- [x] opt-in `mypy_profile = "ici"`를 별도 argv overlay로 명시하고 기본은 `project`로 둔다.
- [x] self project에서 mypy를 required로 만들고 `check_untyped_defs`, redundant-cast,
  unused-ignore 검사를 켰으며 드러난 진단을 source에서 정리했다.
- [x] tool note와 actual error를 구분하고 위치가 있는 note/error의 line/column을 보존한다.

### I5-2. 내장 AST 규칙 재설계

**브랜치:** `refactor/python-rules`

- [x] security 탐지를 call/assignment/import-aware AST 규칙으로 구현한다. weak crypto/random,
  dynamic execution, pickle, command processor와 constant `shell=True`를 구조적으로 구분하고
  comment-token `# nosec`, bounded no-follow source input, invalid syntax fail-closed를 포함한다.
- [x] secret detector는 이름 context, 알려진 credential prefix, 길이/entropy와 bounded exact
  name allowlist를 지원하며 source value를 target/message/snippet/extra 어디에도 보존하지 않는다.
- [x] resource engine이 bounded intraprocedural flow로 `close()`/`aclose()`, context manager,
  alias/branch/`try-finally`, `ExitStack`, ownership transfer와 escaping return을 구분한다.
- [x] mutable literal/constructor default는 native correctness category와 exact AST 위치로 분리한다.
- [x] exception, dead, cognitive와 Ruff/mypy 진단의 중복 후보를 보수적인 display-only
  canonical Python rule projection으로 표현한다. precise line+column overlap과 trusted semantic
  context가 없으면 merge하지 않으며, JSON/baseline 원본 inventory와 provenance는 변경하지 않는다.
- [x] security/resource rule의 source/evidence bound, limitation과 confidence를 engine extra와
  engine reference에 문서화한다. cross-tool canonical display projection은 confidence와
  provenance를 보존하며, 원본 JSON/baseline inventory를 변경하지 않는다.

### I5-3. Python runtime compatibility

**브랜치:** `feat/python-compatibility`

- [x] configured interpreter마다 `-VV`, compileall, import smoke를 argv로 실행한다. 현재
  runtime은 `interpreters = []`일 때 기본 required이며, import smoke는 `imports`에 명시된
  모듈에만 opt-in한다(top-level code 실행 경계).
- [x] `requires-python`과 실제 interpreter version을 비교한다.
- [x] 3.10 하한 위반 문법/API를 위치와 함께 보고한다.
- [x] interpreter별 optional/required 정책과 unavailable 상태를 구분한다.
- [x] envlens를 Python 3.10.21과 최신 설치 interpreter인 Python 3.14.7에서 각각
  feature candidate `ici.pyz python-compat`로 검증한다.

현재 구현은 Python source가 선택된 프로젝트에서 bounded `pyproject.toml` metadata를 읽고,
각 resolved runtime의 `-VV` version, `python -B -m compileall -q -f`, 선택 import smoke를
`ToolEvidence`로 보존한다. `target_version` 또는 `requires-python`에서 추론한 floor는 syntax
`feature_version`과 문서화된 standard-library API inventory에 적용하며, 위반에는 precise
line/column target을 남긴다. optional runtime의 unavailable/incompatible는 `WARN`, required
runtime의 unavailable는 `ERROR`/`NOT_RUN`, version/compile/import 불일치는 `FAIL`이다. 자동
발견 import는 실행하지 않고, 명시적 import만 contained subprocess에서 실행한다. 외부
interpreter가 설정으로 교체될 수 있으므로 이 engine의 cache는 의도적으로 비활성화한다.
정상 실행 evidence는 `MEASURED`이다. envlens는 Python 3.10.21과 3.14.7에서 모두 통과했지만,
이는 아직 I5-4 이후 작업이나 I5 전체 완료를 의미하지 않는다.

### I5-4. packaging과 환경 무결성

**브랜치:** `feat/python-package-analysis`

- [x] pyproject metadata, src layout, package discovery와 entry point를 검증한다.
- [x] configured wheel input을 build/extract하지 않고 tag, included files, native extension과
  `direct_url.json`/`build-details.json` provenance file의 존재를 관측한다.
- [x] pure-Python 정책과 일반 프로젝트의 native wheel 허용 정책을 분리한다.
- [x] import name/distribution name 불일치와 누락 package data를 finding으로 만든다.
- [ ] envlens와 ici pyz build를 서로 다른 packaging 사례로 사용한다.

2026-09-04 local implementation은 import/build/extract 없이 bounded pyproject/wheel을 읽고,
WHEEL/METADATA/RECORD identity·tag·package file·entry-point 일치와 RECORD hash/size를 검증한다.
모든 pyproject/wheel 입력은 PASS/FAIL target을 남기며 손상된 wheel은 해당 경로의
`ici.package.wheel-invalid`로 닫힌다. 마지막 envlens/ici 실물 교차 검증은 toy candidate
acceptance와 함께 수행한다.

---

## 12. I6 — test와 coverage의 의미를 깊게 검증

### I6-1. gcov JSON 이관

**브랜치:** `fix/gcov-json-coverage`

- [x] 지원 GCC에서는 `gcov --json-format`을 우선 사용한다.
- [x] function start/end line, demangled name, line, branch, call을 파싱한다.
- [x] format version과 GCC version을 검증한다.
- [x] gzip corruption, missing data, path relocation을 ERROR로 구분한다.
- [x] 오래된 GCC는 text fallback과 제한을 명시한다.
- [x] 기존 throw branch 정책을 JSON 결과와 재검증한다.

### I6-2. coverage policy

**브랜치:** `feat/coverage-policy`

- [x] 전체, 파일, 함수, changed-line threshold를 분리한다.
- [x] generated, entry point, test, vendor 제외 근거를 report에 남긴다.
- [x] Python coverage contexts와 C++ test/binary coverage mapping을 검토한다.
- [x] uncovered function은 정확한 symbol location과 관련 test scope를 표시한다.
- [x] baseline 대비 coverage regression을 source-located delta finding으로 만든다.

### I6-3. test quality deep profile

**브랜치:** `feat/test-quality`

- [x] test count, pass/fail, collection evidence와 coverage를 계속 분리한다.
- [x] retry/repeat를 통한 flaky test 탐지를 opt-in으로 제공한다.
- [x] timeout과 slow-test inventory를 제공한다.
- [x] Python mutation 도구와 C++ mutation 가능성을 spike하고, 재현 가능한 범위만 deep profile로 채택한다.
- [x] mutation unavailable은 기본 test gate를 왜곡하지 않는다.

Deep quality는 총 실행 3회, slow inventory 1,000개와 subprocess timeout/output 상한을 지키며
`report`와 `warn`을 분리한다. Runtime outcome·timing은 cache하지 않는다. mutation spike에서는
Python/C++ 모두 프로젝트 빌드·테스트 명령을 변형마다 재현 가능하게 격리하고, 등가 mutant와
timeout, tool/version provenance를 동일한 bounded evidence 계약으로 판정할 수 있는 내장
pure-Python provider가 없음을 확인했다. 따라서 실제 mutation score는 현재 profile에 채택하지
않고 Python 도구의 capability probe만 유지한다. unavailable은 품질 점수나 기본 test gate를
바꾸지 않으며, 실제 score는 위 격리·bounded·provenance 계약이 충족되는 별도 기능 제안 전까지
의도적으로 범위 밖이다.

2026-09-04 local implementation은 strict gzip gcov JSON v2를 GCC version과 함께 검증하고,
source relocation·완전성·function geometry·branch/call record를 bounded parser로 정규화한다.
지원 도구의 JSON 손상이나 불완전 evidence는 text로 조용히 강등하지 않으며, JSON capability가
없는 구형 gcov에만 제한된 text fallback을 허용한다. coverage policy는 aggregate/file/function/
caller-declared changed-line gate를 분리하고, 실제 regular source와 canonical project-relative
path만 받는다. v3 baseline은 aggregate 및 per-file regression delta를 별도 finding으로 만들며
`--fail-on-new`일 때만 baseline delta가 gate가 된다. real GCC 15.2 Qt fixture는 5개 JSON report,
3개 exact function과 line/function/branch 100%를 확인했다. 이 완료 표시는 구현·local gate 범위며,
remote PR/main 및 candidate acceptance는 아래 delivery 기록에서 별도로 확정한다. 버전은
`0.10.2`로 유지하고 이 범위를 위한 release는 만들지 않는다.

---

## 13. I7 — Makefile, artifact, binary와 hybrid integration

### I7-1. 수제 Makefile adapter

**브랜치:** `feat/make-adapter`

- [x] 암묵적으로 임의 target을 추측하지 않고 config에 build/test/clean argv 계약을 둔다.
- [x] shadow 또는 out-of-tree 지원 여부를 사전 진단한다.
- [x] parallel jobs, coverage/sanitize flag 주입 방식을 명시한다.
- [x] build target 0개, test target 0개, ignored failure를 구분한다.
- [ ] abilens의 실제 Makefile로 build/test/sanitize/coverage를 검증한다.

### I7-2. artifact manifest

**브랜치:** `feat/artifact-manifest`

- [ ] executable, shared/static library, Python wheel, report artifact를 typed record로 남긴다.
- [x] hash, size, mode, producing target/command와 build variant를 기록한다.
- [ ] artifact glob이 빈 결과거나 project 밖으로 나가면 ERROR로 처리한다.
- [x] downstream binary/integration engine은 manifest만 소비한다.

현재 v2 manifest는 executable/shared/static library에 stable id, filename-derived target label,
redacted producer argv와 build identity를 기록하고 v1 reader 호환성을 유지한다. Python wheel과
report artifact의 typed producer, configurable artifact glob 계약은 아직 남아 있으므로 첫째와
셋째 항목은 닫지 않는다.

### I7-3. binary compatibility

**브랜치:** `feat/binary-compatibility`

- [x] ELF class, machine, NEEDED, RPATH/RUNPATH를 `readelf` evidence로 읽는다.
- [x] GLIBC, GLIBCXX, CXXABI maximum required version을 계산한다.
- [ ] static requirement, forbidden dependency/path와 configured floor를 정책화한다.
- [ ] stripped/malformed/non-ELF를 구분한다.
- [ ] abilens의 executable/shared library와 viewer static CLI를 실측한다.

### I7-4. hybrid integration contract

**브랜치:** `feat/hybrid-integration`

- [x] shell 없는 argv case와 typed placeholder를 제공한다.
- [x] Python interpreter와 artifact id를 manifest에서 안전하게 해석한다.
- [x] stdout/stderr substring, exit code, timeout, output artifact assertion을 지원한다.
- [x] 빈 required case, unknown placeholder, missing artifact를 config ERROR로 처리한다.
- [ ] buildscope의 Python analyzer → C++/Qt consumer E2E를 검증한다.

---

## 14. I8 — report와 viewer를 품질 조사 워크벤치로 만든다

### I8-1. reporter parity와 SARIF

**브랜치:** `feat/reporter-parity`

- [ ] 모든 reporter가 v3 finding, related location, confidence, suppression, delta를 보존하는지 contract test를 만든다.
- [ ] SARIF 2.1.0 export를 추가하고 rule/result/location/fix mapping을 검증한다.
- [ ] GitHub annotation은 new/high-priority finding만 제한적으로 발행한다.
- [x] HTML은 full inventory를 검색/필터할 수 있지만 초기 DOM 크기를 제한한다.

SARIF 2.1.0의 deterministic rule/result/location, suppression, duplicate occurrence와 baseline
location mapping은 구현됐지만 source fix model은 아직 없으므로 SARIF 전체 항목은 닫지 않는다.
HTML은 2,000 actionable finding 초과 시 초기 50행과 bounded inline inventory를 사용하며
100,000 finding 회귀 fixture를 통과한다. 실제 browser startup/memory benchmark는 I8-4에 남는다.

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

### I9-0. Candidate artifact provenance producer (producer sub-slice)

**브랜치:** producer `chore/candidate-artifact-provenance`; consumer `feat/candidate-quality-zoo-acceptance`
**상태:** producer contract/local implementation and remote producer evidence complete; released-artifact Q0 accepted; exact sanitizer, category/Qt, and separate TSan candidate contracts accepted; broader Qt lifetime/ownership, resource/lifetime/security taxonomy, Q1–Q5 and release boundaries remain pending

이 절은 released `ici v0.10.2` Q0와 exact-head candidate known-answer subset을
완료 처리한다. candidate acceptance는 exact rule/status/evidence/confidence/path/line
contract와 ASan/LSan/UBSan/clean runtime, category/Qt, 별도 TSan evidence에 한정되며,
broader Qt lifetime·ownership, resource/lifetime/security taxonomy, Q1–Q5·I4 aggregate·
version/release는 완료 처리하지 않는다.

- [x] `refs/heads/main`에서만 수동 dispatch하고 full lowercase target SHA가 producer workflow를
  공급한 exact protected-main dispatch commit과 같으며 fetched main ancestry에 남아 있는지 검증한다.
- [x] newest exact successful `Merge Gate`만 선택하고 canonical successful main-push
  `CI Quality Gate (Dogfooding)` run과 선택된 `Merge Gate` job을 각각 독립 Actions Runs/Jobs
  응답으로 검증하는 bounded auditor를 둔다. job/run/attempt, target SHA, name/workflow,
  main branch, status/conclusion과 canonical job/run/check URL이 모두 서로 묶여야 한다.
- [x] 두 번의 reproducible build와 smoke를 거친 exact three-file bundle 및 provenance
  manifest(`ici.candidate/v1`)를 정의한다.
- [x] workflow의 `candidate_bundle.py create/verify` 호출과 helper CLI를 정렬하고, exact
  three-file/mode/bounded canonical JSON/checksum 검증을 focused regression으로 고정한다.
  manifest는 candidate run ID/attempt와 선택된 Merge Gate `check_run_id`, `job_id`, `run_id`,
  `run_attempt`, canonical `job_url`/`run_url`을 각각 `merge_gate_check_run_id`,
  `merge_gate_job_id`, `merge_gate_run_id`, `merge_gate_run_attempt`,
  `merge_gate_job_url`, `merge_gate_url`로 기록한다. focused 111-test suite와 live API
  verifier, Python 3.10 full suite(2,061 passed, 환경 의존 7 skipped), Ruff/mypy/actionlint,
  reproducible build/smoke와 실제 built-pyz bundle round trip이 통과했다.
  `ici.candidate/v1`은 stable `v0.10.2` version/tag/release와는 별개다.
- [x] 실제 remote dispatch producer evidence를 exact source SHA
  `7872a7b80899cbd3d40d92d18e7920cd7e2283e7`에 대해 기록했다. [Main run `33688279264`](https://github.com/jihoon22-lee/ici/actions/runs/33688279264)는
  all green이고 [Merge Gate check/job `100442919168`](https://api.github.com/repos/jihoon22-lee/ici/check-runs/100442919168)은
  attempt 1이다. Main [ici Pages](https://jihoon22-lee.github.io/ici/ici/main/)와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
  는 main artifact bytes와 byte-match되고 exact source SHA, correct titles 및 Zero-CDN을 통과했다.
  Candidate [run `33689056008`](https://github.com/jihoon22-lee/ici/actions/runs/33689056008)는 success이며,
  [artifact ID `9869395069`](https://github.com/jihoon22-lee/ici/actions/artifacts/9869395069), name
  `ici-candidate-7872a7b80899cbd3d40d92d18e7920cd7e2283e7`, API/raw ZIP digest
  `sha256:640e50ecf5b099174c16f1ef5d2b5b87945329711e96f926d94c3cc04109081e`, size `2,277,109`,
  expiry `2026-09-16T22:14:38Z`를 남겼다. ZIP은 정확히 `candidate-provenance.json` (`0644`, 859),
  `ici.pyz.sha256` (`0644`, 74), `ici.pyz` (`0755`, 2,275,786) 세 entry였고, pyz SHA-256은
  `53fc75f0a073a74689babfe9ef8a4b2378995002d7d563bdc52da548fdbb9ee8`, bundled version은
  `ici 0.10.2`다. Candidate manifest는 independent verifier와 byte-match됐으며 check/job/run
  canonical API identities, `workflow_name`, `head_branch`, attempts 및 모든 canonical URL이
  일치했다. 실제 v7 upload ZIP은 요구 mode를 보존했으므로 generic mode-loss assumption은 적용되지 않는다.
- [x] 후속 문서 PR #140이 main SHA
  `cc73531ca33d5e781f027a2c55d341d29034990f`로 병합됐고, exact-main [run `33691782482`](https://github.com/jihoon22-lee/ici/actions/runs/33691782482)가
  green이었다. [verification artifact `9870465295`](https://github.com/jihoon22-lee/ici/actions/artifacts/9870465295)의
  digest는 `sha256:e4b59ff4a88290049b537efe573a820a09e6d953b850bcde2d9ff06239f72bea`, size는
  `2,396,261` bytes이며, main ici/viewer Pages는 trusted artifact와 byte-identical이고
  source/title/Zero-CDN audit을 통과했다.
- [x] ici-hosted `candidate-quality-zoo.yml` manual workflow의 local contract를 구현한다.
  workflow는 exact ici/toy `main` SHA, candidate artifact ID와 원본 ZIP digest를 binding하고,
  candidate provenance의 Actions run/check/job evidence를 독립 API 응답으로 재검증한 뒤
  검증된 local `ici.pyz`를 Quality Zoo runner에 전달한다. candidate preflight와 실행은
  publication/OIDC/runtime credential 없이 수행하고, preflight/intake/API evidence/runner
  결과는 별도 14일 acceptance artifact로 남긴다. broader Qt lifetime/C++ static-analysis
  scenario를 실행할 수 있도록 hosted runner에 `clang`, `clang-tidy`, `clazy`, `cmake`, `g++`,
  `pkg-config`, `qt6-base-dev`도 준비한다. CI provisioning과 별개로 candidate preflight/실행에는
  `GH_TOKEN`/`GITHUB_TOKEN`을 사용하지 않으며, workflow는 `publish`, Pages, PR comment,
  `<!-- ici-report -->` marker 또는 version/release를 만들지 않는다.
- [x] 위 workflow를 historical exact ici workflow-main `6df011f98be1a19092b112cb56c596dc35bcae4d`와
  exact toy `main` `2d0d7c0b2dcc137a782d6042438fc287bffdf570`에 실제 dispatch하고, [acceptance
  run `33710695336`](https://github.com/jihoon22-lee/ici/actions/runs/33710695336)/[artifact
  `9876797536`](https://github.com/jihoon22-lee/ici/actions/artifacts/9876797536)를 독립 감사했다.
  candidate target `9d470edca7ab037a24dcd6594531a822f116548b`의 producer [run
  `33706057540`](https://github.com/jihoon22-lee/ici/actions/runs/33706057540)/[artifact
  `9875319095`](https://github.com/jihoon22-lee/ici/actions/artifacts/9875319095)는 raw ZIP
  SHA-256 `4aec084b3a30ac01a1df5124fa3b42b7f51d23f66c12b490194a84549be9db27` (2,285,368 bytes),
  contained `ici.pyz` SHA-256 `e7f1a2ce7147057538873a802715c7bf2b12e530a85070af862e02e378caceb8`
  (2,284,045 bytes)로 확인됐다. Acceptance ZIP은 SHA-256
  `e66ae2b65988abe10fc5ddb92a5c3bb6fc238ec2f77b7fd27ccfe75c24194a5f` (1,104,307 bytes)다.
  `quality-zoo.suite/v1`는 5/5 contract `PASS`, runner error 0이며 ASan UAF
  `FAIL`/`MEASURED`/`exact` `src/fault.cpp:5`, LSan leak `FAIL`/`MEASURED`/`exact`
  `src/fault.cpp:3`, UBSan signed overflow `FAIL`/`MEASURED`/`exact` `src/fault.cpp:3`,
  sanitizer-clean `PASS`/`MEASURED`/`high` `tests/test_clean.cpp:1`, Python existing case `WARN`을
  기록했다. 각 toy PR의 normal gate는 계속 released ici `v0.10.2` pin을 유지하며, 이
  workflow는 Pages/comment/publish/tag/release/version side effect를 만들지 않는다.
- [x] 별도 TSan candidate acceptance는 ici SHA
  `6ee08b14fa598a19074af7afed4368fd79b19b2b`, candidate artifact `9884927798`, raw ZIP SHA-256
  `9a50972a5cb4ad96b2b0cf912e27c17a600fc19d6d899c6e33028d4449b1122d`, exact toy SHA
  `d0b84d376d3f736da86308a49d21d8600297eb27`에 바인딩됐다. [workflow run
  `33737405098`](https://github.com/jihoon22-lee/ici/actions/runs/33737405098)은 `success`,
  scenario contracts `8/8 PASS`, `errors = 0`이며, acceptance artifact `9886336618`의 ZIP
  SHA-256은
  `70f298a33a251241033882a5bd1eea1a7f863dd86c1939321d531cee39b32bf3`이다. 이는 exact TSan
  candidate contract만 닫으며 feature PR/main 및 I4-4 전체 checkpoint의 근거는 아니다.
- [x] category taxonomy와 Qt tool provisioning을 포함한 exact feature head로 candidate를 만들고,
  Qt parent/lifetime expectation이 포함된 exact toy `main`에 dispatch해 expected
  rule/location 및 clean-path contract를 감사했다. ici target
  `e7a9f55be8893d91497a6e1d0bff6e2e5f4af5f3`와 toy target
  `a59461acaf0f2e967e6ba51e07e56ac7e73acbc6`에 묶인 [Quality Zoo run
  `33718024450`](https://github.com/jihoon22-lee/ici/actions/runs/33718024450)의 six scenario
  contracts는 모두 `PASS`, runner `errors`는 `0`이었다. 이는 category projection과 해당 Qt
  scenario의 exact scope만 닫으며 현재 `main@2113b5d`, broader Qt lifetime/ownership,
  resource/lifetime/security taxonomy, I4 aggregate 또는 release를 주장하지 않는다.
- [x] released ici `v0.10.2`를 사용하는 Q0 quality-zoo scenario runner와 report/artifact
  contract를 toy-projects PR #49에서 수락했다. [PR run `33693241255`](https://github.com/jihoon22-lee/toy-projects/actions/runs/33693241255)는
  green이고 [artifact `9870829400`](https://github.com/jihoon22-lee/toy-projects/actions/artifacts/9870829400)는
  `contract_verdict: PASS`, stable `python.dead-private-function` 1개 scenario, released ici
  SHA `8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`, observed suite
  `WARN`, exit `0`, empty `errors`를 기록했다. 당시 PR에는 sticky `<!-- ici-report -->`
  comment/marker가 정확히 하나였고 product HTML link가 3개였다. PR #49는
  `ed5fea2e881da77ac95482cf665e4e40bfe172f1`로 squash merge됐으며, exact-main [run `33694452357`](https://github.com/jihoon22-lee/toy-projects/actions/runs/33694452357)와
  stable [artifact `9871249913`](https://github.com/jihoon22-lee/toy-projects/actions/artifacts/9871249913)도
  각각 green과 contract `PASS`/observed `WARN`/empty `errors`/exit `0`를 확인했다. product
  Pages는 trusted artifact와 byte-identical이었다. 이 check는 released-artifact Q0 경계이며,
  candidate sanitizer consumer evidence는 위의 별도 exact dispatch 기록으로 관리한다.

### I9-1. quality-zoo contract runner — Q0, sanitizer, category/Qt, and TSan candidate acceptance complete; Q1–Q5 pending

**브랜치:** `test/quality-zoo-contract`
**상태:** released-artifact Q0 known-answer acceptance, exact candidate sanitizer 및
category/Qt acceptance와 별도 exact TSan candidate acceptance가 완료됐다. broader Qt
lifetime/ownership, resource/lifetime/security taxonomy와 Q1–Q5 scenario/support matrix는
pending이다.

- [x] Q0 released-artifact path의 toy manifest schema와 ici v3 report matcher가 PR #49의
  `quality-zoo-contract` artifact에 기록되고 exact-main run에서도 재검증됐다.
- [x] exact candidate run에서 expected rule id, status/evidence/confidence, path, line 범위를
  검증한다. UAF/LSan/UBSan defect는 각각 `FAIL`/`MEASURED`/`exact`와
  `src/fault.cpp:5`, `src/fault.cpp:3`, `src/fault.cpp:3`를 만족했고, clean은
  `PASS`/`MEASURED`/`high` 및 `tests/test_clean.cpp:1` completion target을 만족했다.
- [ ] expected absence를 지원해 false positive도 고정한다. Narrow sanitizer-clean absence는
  위 candidate runtime evidence에 포함되지만, broader false-positive corpus는 pending이다.
- [x] ici-hosted candidate workflow가 verified candidate pyz를 local path로 주입하는 계약을
  갖춘다. workflow 자체는 read-only/manual이며, 실제 원격 candidate acceptance는 위 exact
  sanitizer dispatch evidence와 별개로 broader scenario coverage를 계속 요구한다.
- [x] exact ici/toy `main` revision과 candidate artifact를 binding한 원격 workflow를 dispatch하고
  candidate report의 expected rule/status/evidence/confidence/path/line을 검증한다. 기존
  sanitizer(`33710695336`), category/Qt(`33718024450`) 및 TSan(`33737405098`) acceptance는
  각각의 exact feature head에 한정되며, 현재 `ici main@2113b5d`에 대한 Quality Zoo 재실행이나
  broader Qt lifetime/ownership 또는 Q1–Q5 acceptance를 포함하지 않는다.
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

- 체크박스·roadmap key·하위 절은 추적 단위이지 PR 경계가 아니다. 관련 model, engine,
  reporter, fixture, 문서와 acceptance를 하나의 응집된 PR에 함께 넣어 CI·리뷰·Pages 검증을
  반복하지 않는다.
- 구현 커밋은 의미 있는 단위로 유지하되, 독립 rollback이 꼭 필요하거나 선행 계약이 아직
  준비되지 않은 경우에만 PR을 분리한다. 단순히 파일이나 엔진이 다르다는 이유로 쪼개지 않는다.
- 현재 combined maintainability PR 이후 ici의 남은 범위는 다음 **3개 큰 delivery PR**로
  묶는 것을 목표로 한다. PR 제목은 결과를 설명하고 괄호형 roadmap code를 primary/sole 제목으로 쓰지 않는다.
  1. Python 도구 설정·AST 안전성·runtime/package 호환성과 coverage/test-quality를 함께 닫는다.
  2. configured Makefile·typed artifact·ELF/hybrid integration과 reporter/viewer 소비 계약을
     end-to-end로 함께 닫는다.
  3. expanded Quality Zoo·self dogfood·support contract를 검증한다. 모든 gate가 끝난 뒤에만
     이 delivery와 별개로 신중한 version/release 결정을 검토한다.
- toy-projects도 대응 마스터 계획에서 기존 앱 완성, AbiLens/Quality Zoo 확장, path-aware
  최종 CI의 3개 큰 delivery로 묶는다. 세부 기능마다 별도 PR을 만들지 않는다.
- 서로 무관한 변경을 무작정 합치지는 않되, 같은 사용자 결과와 같은 acceptance를 공유하는
  engine·viewer·문서 변경은 한 PR의 범위로 본다.
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

### 16.4 버전 cadence와 릴리스 경계

- `feature`, `test`, `refactor`, `docs` PR은 버전을 자동으로 올리거나 stable release를 만들지 않는다. PR 병합과 릴리스 결정은 별개다.
- `patch`는 이미 공개된 stable artifact의 defect, security, compatibility 수정에만 사용한다.
- `minor`는 사용자에게 보이는 응집된 roadmap checkpoint에만 사용한다. ici 전체 gate, 실제 도구 E2E, candidate cross-repo/toy 검증, PR/main CI·Pages, docs/CHANGELOG 동기화가 모두 끝난 뒤에만 릴리스한다.
- PR 제목/요약은 `I4-3`, `T0`, `B1`, `D2` 같은 roadmap 코드만으로 작성하지 않으며, 사용자에게 보이는 결과나 기술적 결과를 설명한다. roadmap key는 필요할 때 body의 mapping 항목으로만 덧붙인다.
- pre-release/candidate artifact는 stable이 아니며, 하나의 PR이 하나의 릴리스를 의미하지 않는다.
- 현재 `v0.10.2`는 공개된 corrective stabilization이다. 다음 minor는 I4-3과 I4-4, 그리고 real toy-projects/quality-zoo 검증이 완료될 때까지 deferred 상태로 둔다.

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
- [x] I3: I3-1 compilation model/검증 게이트와 PR·CI·Pages evidence 완료; I3-2 canonical
  CMake generation, PR·CI·Pages evidence, local viewer/LogLens checks, and the v0.8.0 public
  projection target comparison complete; I3-3 implementation/local E2E/quality gates와
  PR·CI·Pages evidence complete; I3-4 implementation/focused local tests, existing PR·CI·Pages
  evidence, the new same-basename actual-process local test, and its PR·CI·Pages remote evidence
  complete. The I3 checkpoint is closed; next is I4.
- [ ] I4: C++/Qt tool-backed analyzer와 safety profile 완료 (I4-1 및 B4 precondition, I4-2
  code/local contract와 ici PR/main acceptance, v0.10.2 public release evidence 완료;
  다음 minor는 BuildScope B5와 real toy-projects/quality-zoo 검증 및 I4-3/I4-4 완료 뒤로
  deferred)
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
`6a0eadb20464569be9573d41ab72a27bd96d58a7`로 병합됐다. I3-1은 [PR #99](https://github.com/jihoon22-lee/ici/pull/99)의
squash merge commit [`64c4f7b57826e088e9b74b5950c7f3d8091188b9`](https://github.com/jihoon22-lee/ici/commit/64c4f7b57826e088e9b74b5950c7f3d8091188b9),
[CI run `33380721019`](https://github.com/jihoon22-lee/ici/actions/runs/33380721019), [sticky comment](https://github.com/jihoon22-lee/ici/pull/99#issuecomment-5476836988),
[ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/99/)와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/99/)까지
완료됐다. I3-2 canonical CMake context의 PR·CI·Pages evidence도 [PR #101](https://github.com/jihoon22-lee/ici/pull/101),
[CI run 33386134812](https://github.com/jihoon22-lee/ici/actions/runs/33386134812),
[sticky comment](https://github.com/jihoon22-lee/ici/pull/101#issuecomment-5477565364),
[ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/101/), [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/101/)까지
완료됐다. I3-4 compiler-backed lint/include graph도 [PR #105](https://github.com/jihoon22-lee/ici/pull/105),
[CI run 33409862110](https://github.com/jihoon22-lee/ici/actions/runs/33409862110),
[sticky comment](https://github.com/jihoon22-lee/ici/pull/105#issuecomment-5480770505),
[ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/105/)와
[viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/105/)까지 완료됐다. 새 same-basename
actual-process test의 PR #113·exact-main PR/CI/Pages evidence도 완료되어 I3 전체가 complete됐다.
I3-2의
BuildScope target-by-target define·standard·include 대조는 v0.8.0 public projection에서
16 unit·6 target·14 field group mismatch 0으로 완료됐다. I3-3 qmake exact capture는 PR #103의
CI·Pages evidence까지 완료됐다.

I4-2의 local 구현은 위 여섯 조건과 exact-context security/budget 계약을 모두 포함한다.
full local run은 `1513 passed, 4 skipped`였으며, skip은 당시 환경에서
`clang-tidy`·`clazy`·`clang++`가 설치되지 않았기 때문이다. PR #122 head
`c3a8fe21639cecef395f0bc28777066401927da0`의 run `33499500259`와 squash merge commit
`9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327` 뒤 exact-main run `33500281653`에서 실제
tool E2E·Qt5/Qt6·dogfood·publication·sticky comment·Merge Gate가 모두 통과해 ici I4-2
remote acceptance는 완료됐다. v0.10.2의 exact-main/tag/release evidence는 위 1.3절에
확정했고, 다음 인수인계자는 toy-projects BuildScope B5가 `.ui`/`.qrc`/Q_OBJECT 실물 경로를
released ici로 검증하도록 진행한다. I4-3/I4-4와 I4 전체 checkpoint는 그 이후의 별도 완료
조건을 따른다. 다음 minor release는 I4-3/I4-4와 real toy-projects/quality-zoo 검증이 끝날
때까지 만들지 않는다.
