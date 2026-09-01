# Release cadence 및 stable artifact 경계

## Overview

반복적인 기능 PR과 실제 제품 릴리스를 일대일로 묶지 않도록 버전 cadence와 release evidence
경계를 문서화했다. 현재 `v0.10.1`은 공개 v0.10.0의 production warning-policy 결함을
보정하는 corrective stabilization으로 기록하고, 다음 minor는 I4 maintainability/safety와
실물 교차 검증이 끝난 뒤에만 결정하도록 했다.

## Context

기존 문서는 PR 완료, candidate artifact, stable release, 다음 roadmap checkpoint를 같은
흐름처럼 읽을 여지가 있었다. 과거 v0.9.x와 v0.10.0의 release/provenance evidence는 회귀
근거이므로 유지하고, 현재 정책과 checkpoint 문구만 보강했다.

## Changes Made

- `AGENTS.md`: feature/test/refactor/docs PR의 자동 version bump/release 금지, patch/minor
  조건, candidate 비안정성, no-one-PR-one-release, roadmap 코드만으로 된 PR 제목 금지
  불변식을 추가했다.
- `README.md`: 현재 v0.10.1 corrective stabilization과 다음 minor 보류 조건을 포함한
  사용자용 release policy를 추가했다.
- `CHANGELOG.md`: `[Unreleased]`에 동일한 정책과 현재 corrective stabilization 상태를
  기록했다. 기존 버전별 release evidence는 수정하지 않았다.
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`:
  현재 상태와 section 16.4 release boundary, section 18 I4 checkpoint를 동기화했다.
- `workthrough/2026-09-01-release-discipline.md`: 변경 rationale와 검증 명령을 남겼다.

## Policy excerpt

```text
patch = 이미 공개된 stable artifact의 defect/security/compatibility 수정
minor = 전체 gate와 실제 도구·cross-repo/toy·PR/main CI/Pages·docs evidence가 끝난
        응집된 user-visible roadmap checkpoint
candidate/pre-release = stable 아님
PR title = user-visible/technical outcome; roadmap key는 body mapping에서만 선택적으로 사용
```

## Verification Results

```text
git diff --check
PASS

rg -n "Release discipline|릴리스 정책|16\.4|corrective stabilization|roadmap 코드" \
  AGENTS.md README.md CHANGELOG.md \
  docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md \
  workthrough/2026-09-01-release-discipline.md
PASS (all policy anchors present)
```

코드·설정 변경이 없는 문서 전용 작업이므로 pytest, Ruff, pyz build, smoke gate는 실행하지
않았다.
