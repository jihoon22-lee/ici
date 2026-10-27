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
|2 static selection|첫 구현|RunRequest.static_only, 실행 검사 제외 및 PARTIAL 범위|
|3 configless discovery|첫 구현|Python/C++ 메모리 구성, 기존 설정 우회 금지|
|4 analyze 진입점|첫 구현|run_checks 공유, verify 호환 테스트 진행|
|5 일반 package 설치|미완료|wheel 및 격리 설치 E2E|
|6 report UX|미완료|범위·baseline·suppression·관련 진단|
|7 Rust provider|미완료|실제 fixture, 컴파일 부수작용 명시|
|8 TS/JS provider|미완료|실제 fixture 및 설정 attribution|
|9 보조자료 importer|미완료|출처·시점·범위 보존|
|10 stable/next 수렴|미완료|호환 adapter와 회귀 우선|
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
