# ici I0-1 계획 상태 정리

## Overview

ici와 toy-projects의 장기 계획 및 인수인계 문서를 현재 GitHub `main`과 병합된 PR에 맞춰
정렬했다. 과거 계획의 체크박스가 활성 작업으로 오해되지 않도록 완료·부분 완료·보류·마스터
보정 관계를 상단에 명시하고, 최신 기준선·Qt 환경·후속 작업 순서를 한 곳에서 확인할 수 있게
했다.

## Context

문서에는 2026-08-30 시점의 테스트 수와 viewer 결과가 남아 있었고, adapter가 아직 미래
기능이라는 설명도 현재 `v0.6.0` 구현과 충돌했다. 인수인계서의 계획 표는 #78/#12까지만
기록해 #79~#82와 toy #13~#16의 실제 병합 상태를 놓치고 있었다. 또한 과거 세부 계획의
미완료 체크박스만 읽으면 이미 병합된 작업을 다시 시작하거나 Qt 5 지원 상태를 잘못 판단할
수 있었다.

## Changes Made

### 1. 마스터 계획과 기준선

- File: `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- 문서 기준일과 ici/toy `main` snapshot을 추가했다.
- ici #78~#82와 toy #12~#16을 현재 병합 사실로 기록했다.
- I0-1 네 체크리스트를 완료로 표시하고 실제 기준 브랜치명을 기록했다.
- 기준선을 ici 634/634 pytest, self verify Pass 7 · Warn 5 · TEM 4.78, viewer 4/4 · TEM
  4.86으로 갱신했다.

### 2. 인수인계서 상태 수정

- File: `docs/superpowers/2026-08-30-handover.md`
- 커밋 수·로컬 push 여부가 아니라 GitHub `main`과 Merge Gate를 상태의 근거로 삼는다는
  규칙을 명시했다.
- 최신 ici/toy snapshot, 후속 PR, viewer 4/4 결과를 반영했다.
- Qt 5.15.18이 `/usr/bin/qmake`에 설치되어 있고 ici #81에서 Qt 5/Qt 6이 각각 4/4
  통과했음을 기록했다. diskmap의 Qt5/Qt6 셸 범위는 toy #16 완료로 갱신하고 공통 matrix만
  T0-5에 남겼다.

### 3. 과거 계획의 보존 배너

- Files:
  - `docs/superpowers/plans/2026-08-19-existing-validation-hardening.md`
  - `docs/superpowers/plans/2026-08-19-ci-validation-features.md`
  - `docs/superpowers/plans/2026-08-29-cmake-qmake-build-adapter.md`
  - `docs/superpowers/plans/2026-08-30-viewer-qt-tests-and-include-resolution.md`
- hardening은 v0.4.0 완료, adapter는 PR #76/v0.6.0 완료, CI validation은 adapter 부분
  완료·나머지 마스터 재배치, viewer/cycle은 #79/#81 완료라는 관계를 각 문서 상단에
  표시했다.
- 설계·회귀 근거는 삭제하지 않고 역사 자료로 보존했다.

### 4. 로드맵과 사용자 문서의 현재 기능 반영

- File: `docs/ci-integration.md`
- CMake/qmake adapter가 현재 기능이라는 점과 v0.6.0/PR #76을 반영하고, 전체 toolchain
  capability inventory와 구분했다.
- File: `docs/design/ci-validation-roadmap.md`
- v0.6.0 adapter 완료 상태와 A-2/A-3 해결 상태를 반영했다. 착수 전 실측 근거는 역사
  기록으로 남기고, 남은 capability·compile DB 등은 미래 범위로 구분했다.
- File: `docs/superpowers/specs/2026-08-29-cmake-qmake-build-adapter-design.md`
- adapter 설계 문서의 “구현 전” 상태와 viewer의 과거 3-test 기준선을 역사 기록으로
  명시하고, 현재 v0.6.0/PR #81 근거로 연결했다.
- File: `README.md`
- 문서 허브에 ici·toy master plan 링크를 추가했다.

## Code Examples

### 상태 기준

```text
GitHub main + merged PR + exact Merge Gate
        └─ local commit count / unpushed worktree는 완료 근거가 아님
```

### 과거 계획 배너 형식

```markdown
> **I0-1 상태 보정 (2026-08-31): 완료·역사 보존.**
> 아래 체크박스는 당시 완료 근거와 회귀 설계를 보존하며 활성 작업을 뜻하지 않는다.
```

## Verification Results

### GitHub 병합 상태

```text
ici: #78, #79, #80, #81, #82 — MERGED
toy-projects: #12, #13, #14, #15, #16 — MERGED
ici v0.6.0 — published
```

각 PR의 병합 상태와 가용한 검증 checks, 그리고 #81의 Qt 5.15·Qt 6 4/4 CTest 및
Qt-free CLI 결과를 GitHub에서 확인했다. 최신 `ici origin/main`은 `fa3ad28`,
toy-projects `main`은 `f267695`다.

### Local quality and link checks

```text
uv run --python 3.10 pytest -q              634 passed
uv run --python 3.10 pytest --collect-only  634 collected
dist/ici.pyz verify                         Pass 7 / Warn 5 / TEM 4.78
git diff --check                             passed
```

README와 `docs/superpowers`·`docs/design`의 계획 링크는 35개 로컬 대상이 실제 파일로
존재하고, 20개 외부 GitHub 링크가 HTTP 200을 반환하는지 확인했다. 문서 변경만 포함하므로 pyz를
재빌드하지 않았다.

## Next Steps

- I0-4 `chore/self-quality-baseline`은 아직 열린 작업이므로 self gate 수치를 실측 후 별도
  PR로 병합한다.
- toy T0-3/T0-5는 toy master plan과 각 native/ici 실측을 기준으로 진행한다(T0-4는 #16 완료).
- 새 기능 계획은 이 master plan의 I1 이후 순서를 따르고, 과거 계획의 미완료 체크박스를
  독립 작업 큐로 부활시키지 않는다.
