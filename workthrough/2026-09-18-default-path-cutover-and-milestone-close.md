# ici-next 기본 경로 전환(cutover)과 개발 마일스톤 종료

## Overview

ici-next 전환의 마지막 개발 작업: `[workspace]`를 선언한 `ici.toml`이 있는
프로젝트에서 bare `ici verify`가 next 엔진 경로를 타도록 기본 경로를
전환하고(PR #264), 개발 마일스톤(38개 이슈)을 전부 종료했다. 사내 환경에서만
수행 가능한 RHEL/GHES/idk 현장 인수와 stable 경로 물리적 제거 승인은 이슈
#265로 분리했다.

## Context

- PR #263(WP12–23 + WP24–29 PR A) 머지 후 남은 것은 WP29의 cutover(PR B)와
  release runbook(PR C), 그리고 실환경 확인이었다.
- `migration-matrix.md` §6은 cutover를 "#227 최종 인수 근거 후 별도 PR"로
  정했으나, 사용자 지시로 개발 범위는 즉시 완료하고 현장 확인만 분리했다.
- 물리적 제거(`engines/`·v3 reporter 삭제)는 되돌림 다리를 끊는 결정이므로
  현장 인수 이후로 유보 — SPEC-05 §5의 순서와 일치한다.

## Changes Made

### 1. 기본 경로 dispatch — `src/ici/cli/cutover.py` (신규)

`cmd_verify` 진입 시 `find_workspace_root(cwd)`가 non-None이면 next 경로로
dispatch한다. 공유 의미를 가진 옵션은 변환하고(`--profile`/`--no-cache`/
`--baseline`), 출력 옵션은 저장된 결과 위의 composition으로 재현한다
(`--report`/`--html`/`--sarif`/`--open`/`--publish`). stable 전용 옵션은
대체 수단과 함께 exit 2로 거절한다 — 조용한 무시보다 명시적 거절이 낫다.

### 2. Discovery 판별자 — `src/ici/config/discovery.py`

- `find_workspace_root(start)`: `discover()`와 같은 검색·VCS 중단 규칙을
  쓰는 비실패 조회. dispatch 결정용이므로 "확실히 [workspace]를 선언한"
  파일만 매칭하는 strict 판별(`_declares_workspace_strict`)을 쓴다.
- `_declares_workspace`의 잠재 크래시 수정: UTF-8 디코드 실패와
  TOMLDecodeError 이외의 파서 실패(ValueError 계열·RecursionError)를
  malformed → candidate로 처리.

### 3. 콜백 게이트 — `src/ici/__main__.py`

`main_callback`이 모든 명령 전에 stable 설정을 eager-load하던 것을,
next 워크스페이스 + `verify` 조합에서는 건너뛰도록 했다. 다른 stable
서브커맨드가 next 워크스페이스에서 실행되면 `ici next`를 가리키는 메시지로
거절한다.

### 4. 파서 견고성 — `src/ici/config/schema.py`

`_parse`가 `ValueError`·`RecursionError`도 "not valid TOML"로 보고하도록
확장 — 10000자리 정수·깊은 중첩 같은 병리 입력이 traceback 대신 설정
오류가 된다(기존 pathological 테스트가 잡아낸 회귀).

### 5. 문서 — `release-runbook.md` (신규), `migration-matrix.md`, `user-guide.md`

- 게이트 계층: 필수 게이트는 전부 ici 소유, toy quality-zoo는 SHA 고정된
  명시적 소비자 검증(#227 항목 2).
- R01~R15 최종 체크리스트: `tested`/`현장 대기` 구분.
- Risk register 7항, 3층 rollback(설정 복사 → dispatch 자동 해제 → 이전
  artifact), release 결정 절차.

### 6. GitHub 상태

- PR #264 머지(CI 전체 green, dogfood 24m49s).
- 이슈 #226·#227·#191 종료. 마일스톤 "ici-next" closed — open 0 / closed 38.
- #265 신규(마일스톤 밖): RHEL 8.10 R-0~R-13, GHES G-1~G-6, idk 실소비자,
  stable 물리적 제거·릴리스 전환 승인.

## Verification Results

```text
pytest (전체, py3.10): 통과 — 신규 tests/test_cutover.py 9건 포함
ruff check / format: clean
build-pyz.sh: dist/ici.pyz 2.8M 빌드
smoke.sh: 전체 통과 — repo의 레거시 ici.toml에서 stable 경로 유지 확인
CI (PR #264): Verify & Dogfood 24m49s · Qt5/Qt6 · Merge Gate · Publish 모두 pass
```

## Next Steps

- **#265**: 사내 RHEL 8.10/GHES/idk 실환경 수행 → 근거가 모이면 stable 경로
  물리적 제거 PR과 릴리스 전환 결정(별도 소유자).
