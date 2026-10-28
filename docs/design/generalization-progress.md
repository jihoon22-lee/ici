# 범용 정적 분석 전환 진행 원장

2026-10-28 사용자가 전체 구현, 문서, PR 병합, 버전·태그·공개 릴리스와 작업 소유 자원 정리를 승인했다. 폐쇄망 전제는 환경별 지원 조건으로 전환하며 기존 데이터 보호·회귀 계약은 유지한다.

## 시작 상태와 보존 대상

- 시작 main: `627844909d8c93459d2779a4dfa60a4fe08149a9`, clean, 버전 0.12.1.
- 기존 worktree: `/home/jihoon/projects/ici` 하나. 기존 로컬 branch: main.
- 기존 원격 branch: main, gh-pages, refactor/stable-shell-removal. 마지막 branch는 이번 작업 소유가 아니므로 보존한다.
- 최신 공개 release: v0.12.1. 기존 태그/asset은 변경하지 않는다.
- 작업 branch: feat/general-static-analysis. 공통 heavy lock은 `/tmp/paseo-generalization-20261028-heavy.lock`이며 삭제하지 않는다.
- 기존 임시파일·공유 cache·사용자 자료는 삭제하지 않는다.

## 마일스톤

|항목|상태|근거|
|---|---|---|
|1 제품·지원 정책|구현|AGENTS §9에 최신 사용자 결정 적용|
|2 static selection|구현·회귀 검증|CheckDefinition.static 선언, 실행 검사 제외 및 PARTIAL 범위|
|3 configless discovery|구현·회귀 검증|5개 언어 메모리 구성, 기존 설정 우회 금지|
|4 analyze 진입점|구현·회귀 검증|run_checks/plan 공유, verify 호환 테스트|
|5 일반 package 설치|구현·로컬 설치 통과|wheel/sdist 및 격리 설치 E2E; 공개 asset 확인 대기|
|6 report UX|구현·회귀 검증|범위·baseline·suppression·관련 진단, v1 읽기 유지|
|7 Rust provider|구현·실제 fixture 통과|Clippy/build.rs, PREPARE/fast 차단, 입력 attribution|
|8 TS/JS provider|구현·실제 fixture 통과|ESLint/tsc native config·진단 attribution|
|9 보조자료 importer|구현·회귀 검증|SARIF/JUnit/LCOV subset, 출처·시점·범위 보존|
|10 stable/next 수렴|구현·회귀 검증|공통 application·주 명령, legacy verify/pyz/migrate 호환 유지|
|검증·문서·릴리스|미완료|고정 revision/asset 다운로드 검증|
|병합·정리|미완료|소유 자원만 정리|

## 지원 원칙

Linux/WSL/CI를 우선한다. RHEL/GHES/idk 인수는 각각의 지원 근거이며 일반 릴리스의 일괄 선행 조건이 아니다. macOS/native Windows의 동등 지원은 이번 구현 약속에 포함하지 않는다. quality-zoo는 ici 소유이며 외부 저장소 최신 main을 필수 gate로 요구하지 않는다. 결과 ici.next.run v2, 이벤트 ici.next.event v1 및 종료 코드 계약을 유지한다.

## 2026-10-28 구현 묶음 1

- `analyze` 명령과 공통 실행 함수, 정적 선택, configless Python/C++ 발견 구현.
- 신규 테스트 4개 통과(Python 3.10). 기존 next 선택/CLI/cutover 포함 54개 통과(15.06초).
- 전체 ruff check 및 format --check 통과. 전체 5개 gate는 아직 미실행.
- 다음: 기존 선택 회귀 확인 후 커밋, Rust/TS provider와 실제 fixture, report/importer,
  package 배포 경로를 이어서 구현. 공개 버전·PR·릴리스·정리는 아직 미완료.
- 작업 소유 임시 스크립트: `/tmp/ici-general-edit.py` (배포 확인 후 삭제).

## 2026-10-28 구현 묶음 2

- Rust Clippy, JavaScript ESLint, TypeScript ESLint/tsc pack/provider 구현. native rule,
  경로·라인 및 component/task attribution은 공통 결과 경로로 전달한다.
- `analyze --plan`, `--language` 추가. Cargo는 PREPARE로 표시하고 fast에서는 blocked.
  `--offline --locked`, jobs=2 및 rustup 자동 설치 차단. ESLint config/plugin 실행 가능성 명시.
- 실제 Cargo Clippy 0.1.99 (58cf8f9c35 2026-10-13), TypeScript 7.0.2,
  ESLint 10.13.0 fixture 검증. build.rs 실제 실행·진단 검증 포함 19개 통과(1.95초).
- plan/기존 CLI 선택 회귀 포함 32개 통과(8.46초). 전체 gate 아직 미실행.
- 공식 규격 근거: [Cargo JSON](https://doc.rust-lang.org/cargo/reference/external-tools.html),
  [ESLint CLI](https://eslint.org/docs/latest/use/command-line-interface),
  [tsc CLI](https://www.typescriptlang.org/docs/handbook/compiler-options.html),
  [rustup 환경](https://rust-lang.github.io/rustup/environment-variables.html).
- 소유 임시 자원 추가: `/tmp/ici-ecosystem-edit.py`, `/tmp/ici-generalization-20261028/`
  (Rust fixture, npm fixture 및 설치 의존성, 실행 JSON). 릴리스 후 정리.
- 다음: 보조자료 importer, report UX, package/release workflow, 전체 검증.

## 2026-10-28 구현 묶음 3

- report/diff/init 주 명령은 기존 next 함수를 재사용. 기존 verify 호환 유지.
- HTML의 검사별 실행/증거 범위와 관련 진단 위치·task·variant 표시 추가.
- SARIF 2.1.0/JUnit/LCOV importer 및 HTML 보조자료 첨부 구현. 독립 ici.auxiliary v1,
  source SHA-256/imported_at/observed_at/scope 보존, NOT_EVALUATED 고정.
- importer/기존 v1 report 호환/HTML escaping/analyze 45개 통과(1.41초).
- JUnit line은 producer별 0/1 기반 차이가 있어 native_line과 producer-defined로 보존.
  LCOV는 line hit subset 지원, function/branch 합산은 하지 않음. 외부/절대 경로는 현재
  명시 오류로 거부하며 소스 파일을 읽거나 URI를 fetch하지 않음.
- 다음: 표준 wheel/sdist + 격리 설치, 문서/지원표/CI/배포, 전체 gate 및 공개 검증.

## 2026-10-28 구현 묶음 4

- 표준 wheel/sdist build 및 fresh venv 설치 smoke 구현. 설치한 entrypoint에서
  analyze/plan/report, result v2/event v1 동일 run_id 확인 통과(Python 3.10.21).
- CI/release에 package 설치 gate 및 wheel/sdist/checksum 공개 asset 추가. 기존 pyz gate 유지.
- Rust 1.99.1/TS 7.0.2/ESLint 10.13.0 fixture tools를 CI에서 고정 설치하며 누락은 실패.
- Cargo/tsc native target/project source closure 미확정은 baseline resolved 근거로 쓰지 않음.
- README/일반 설치·사용법·지원표·이행 문서 갱신. RHEL/GHES/bundle 조건은 별도 환경 지원으로
  명시하고 기존 증거를 역사로 보존. Lens 현재 URL 반영, quality-zoo provenance 변경 없음.
- 다음 명령: 공통 flock 아래 Python 3.10 전체 pytest → ruff check/format → build-pyz/smoke.
  전체 검증 후 버전 확정/PR/CI/병합/tag/release/download 검증/소유 자원 정리를 진행한다.

## 전체 검증 1차와 수정

- Python 3.10 전체 pytest: 4287 passed, 2 failed, 15 skipped (232.48초).
- 실패 원인: locator 주입 seam 이동과 Rust 지원 추가로 기존 미지원 fixture가 유효하지 않음.
  locator fallback 주입을 복구하고 미지원 fixture는 Fortran으로 변경해 기존 계약을 유지.
- 관련 CLI/선택 회귀 51개 통과(16.69초). 추가 static 선언/plan 무실행/JUnit native line
  보존 포함 29개 통과(1.79초). 정적 여부는 이름 목록이 아닌 CheckDefinition 선언으로 결정.
- jsonschema를 격리 추가한 schema/context 검사 41개 통과(1.06초). 최초 15 skip 중 7개는
  jsonschema 부재, 나머지는 clang/clang-tidy/clazy 부재 및 host syntax 조건이다.
- pyz 빌드 통과, smoke의 verify/Zero-CDN 단계 실행 중. 수정 반영 전체 pytest 재실행 예정.

## PR 및 추가 검증

- 구현 PR: https://github.com/jihoon22-lee/ici/pull/295 (draft; 기능 구현과 배포 경로).
- 새 provider/importer의 mypy untyped-body 검사에서 발견한 8개 타입 오류를 명시적
  argv/env/resource tuple과 XML nullable 값 좁히기로 수정. 6개 신규 모듈 mypy 통과,
  실제 provider/importer 18개 통과(1.89초).
- quality-zoo runner/manifest 자체 테스트 68개 통과(1.079초).
- 수정 후 전체 pytest는 공통 lock에서 대기 중. pyz smoke의 coverage 포함 자체 검증과
  다른 프로젝트가 먼저 요청한 heavy 작업을 추월하거나 병렬 실행하지 않는다.

## 설치/산출물 검증 및 composite 보완

- pyz 빌드·Python 3.10 직접 실행·환경 진단·Zero-CDN smoke 모두 통과.
  smoke의 전체 자체 verify도 exit 0. 이 중간 artifact는 0.12.1 version 표기의 작업
  candidate이며 공개 release/기존 v0.12.1 asset을 바꾸지 않았다.
- TypeScript composite fixture를 추가해 incremental 강제 비활성화의 비호환을 수정.
  `.ici/typescript`에 metadata를 쓰는 PREPARE로 명시하고 fast에서 blocked. 일반/composite
  및 Cargo/ESLint 포함 실제 provider 10개 통과(2.10초), provider mypy 통과.
- 전체 pytest 재실행은 진행 중이며 이 마지막 provider 보완은 focused real-tool 검사와
  최신 PR CI에서도 확인한다. 최종 공개 revision은 별도 release 변경에서 고정한다.

## 전체 검증 2차 및 native 설정 회귀

- Python 3.10 + 격리 jsonschema 전체 pytest: 4299 passed, 8 skipped (204.05초).
  skip 7개는 로컬 clang++/clang-tidy/clazy 부재, 1개는 Python 3.10 AST에서 except-star
  구문을 읽을 수 없는 조건. 해당 실제 도구 검사는 CI의 필수 환경에서 실행한다.
- 실제 Ruff fixture가 명시적 파일 전달 시 native exclude 무시를 재현해 실패했고,
  force-exclude와 중첩 config attribution으로 수정. 설정 내용 변경을 check scope digest에
  반영해 baseline 오판 방지. 관련 provider/analyze/baseline 회귀 94개 통과(3.47초),
  변경 3개 모듈 mypy 통과. 이 후속 변경은 최신 PR CI에서 전체 재검증한다.
- 다음: PR #295 최신 CI 및 Merge Gate 확인 → 구현 PR 병합 → 별도 릴리스 branch에서
  0.13.0 후보 버전/known-answer corpus를 고정 → CI/후보 인수 → 태그/공개 release →
  공개 bytes 다운로드 검사 → 최종 main 및 작업 소유 자원 정리.

## PR CI 1차 결과와 기대 명령 동기화

- 최신 기능 CI `40607462805`: Qt5/Qt6 성공, unit 4301 passed / 1 failed / 8 skipped.
  실패는 Ruff format의 정확한 argv 기대값에 새 `--force-exclude`가 빠진 1건이다.
  무수정/검사 계약은 유지하고 기대 명령을 갱신했다. 실제 lint/format 양쪽 exclude
  fixture를 추가, 해당 provider 회귀 45개 통과(0.21초). gate를 완화하지 않았다.
- CI의 Clang 실제 도구 검사는 실행됐다. CI skip은 선택적 jsonschema/host syntax 조건이다.
  최신 test-only 수정으로 CI를 재실행한다.

## 0.13.0 릴리스 후보 준비

- 기능은 하나의 일반 정적 분석 checkpoint이며 additive CLI/provider/package 변경이다.
  공개 0.12.1의 patch 수정 범위를 넘고 wire schema는 유지하므로 0.13.0 minor 후보로 준비한다.
  후보 준비는 공개 stable 판정이 아니다. 태그/게시 전 main CI와 exact candidate corpus를 확인한다.
- 작업 소유 release worktree: `/tmp/ici-generalization-20261028/release`,
  branch `chore/release-general-analysis`, base `8d4a313` (구현 PR #295의 head).
  구현 PR 병합 후 새 main 위로 release-only 변경을 rebase한다.
- 버전 source/ici.toml/CHANGELOG/README 동기화. 다음은 pyz/wheel 후보 build 및
  정확한 digest에 대한 known-answer corpus 등록, release PR/CI/후보 인수다.

## 0.13.0 후보 산출물과 corpus 등록

- pyz build 및 wheel fresh-venv install/analyze/plan/report smoke 통과(Python 3.10).
- 후보 pyz SHA-256: `c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde`.
- 16개 ici-owned candidate scenario의 기존 0.12.1 기대 판정을 유지하고 producer_version만
  0.13.0으로 옮긴 새 기대 파일을 이 정확한 hash에 등록했다. 기존 selector와 과거 기대값은 보존.
- 다음: Python slice 로컬 실행, release PR 최신 main 재배치, CI 및 protected-main candidate
  workflow에서 16개 전체 실제 도구 corpus 인수. 등록 자체를 인수 통과로 기록하지 않는다.

- 0.13.0 후보 Python corpus slice는 5/5 계약 PASS. 이는 의도된 결함을 정확히 찾는
  known-answer 통과이며 모든 샘플 코드가 깨끗하다는 의미가 아니다.
- corpus unit은 새 hash를 정확한 selector 집합에 추가한 뒤 68개 통과(기존 hash 모두 보존).
  전체 16개 실제 tool corpus 인수는 protected-main workflow에서 수행할 예정이다.

- 동일 0.13.0 candidate pyz를 fresh Python/Rust/JavaScript/TypeScript fixture에서 직접
  실행했다. 4/4에서 의도한 위반과 exit 1을 얻었고, unique run_id·result/event 일치,
  HTML 생성, TS composite metadata 격리/noEmit을 확인했다. 코드를 깨끗하다고 판정한
  PASS가 아니라 결함 검출 및 artifact 실행 계약의 PASS다.

## 구현 PR 병합 및 release-only 재배치

- PR #295가 필수 Merge Gate/PR 보고서/Pages 확인 후 squash 병합됐다.
  구현 main: `20207dcba411a243fb465772725f7efc6f5d3efe`.
  CI: https://github.com/jihoon22-lee/ici/actions/runs/40608155601
  Pages: https://github.com/jihoon22-lee/ici/actions/runs/40611754199
- CI unit 4303 passed, 8 skipped(271.53초), 추가 C++ fixture 16 passed. pyz 재현 build,
  wheel 격리 설치, smoke, Python/C++/next dogfood, 게시 확인이 통과했다.
  자체 report에는 기존 line/type/complexity WARN과 viewer lint WARN이 남는다.
  type의 9개 finding은 이번 변경에서 수정하지 않은 기존 파일들에 있으며 신규/수정 모듈
  별도 mypy는 통과했다. gate 통과를 진단 0건으로 표현하지 않는다.
- `feat/general-static-analysis` local/remote branch는 PR 병합 시 삭제됐다. 기존 원격
  `refactor/stable-shell-removal`, `gh-pages`는 보존했다.
- release-only 3개 commit을 위 main에 rebase했다. src/pyproject/README/lock/scripts의
  내용은 후보를 빌드한 이전 release head와 동일하여 후보 hash는 유지된다.
- 다음: release PR CI/병합, exact-main CI와 candidate archive/corpus 인수, tag/public release.
