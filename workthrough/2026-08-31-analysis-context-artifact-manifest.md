# I2-2 공유 분석 맥락과 Artifact Manifest

## Overview

I2-2는 한 번의 `ici verify` 실행에서 프로젝트·도구·컴파일·빌드 산출물의 기준이
엔진별로 달라지는 문제를 해결한다. `ProjectModel`, `CapabilityInventory`,
`CompilationContext`, `AnalysisIdentity`를 immutable `AnalysisContext`로 묶고, adapter의
실행 중 상태는 mutable `BuildSession`에 한정한다. 성공한 파일은 검증 가능한 frozen
`ArtifactManifest`로 발행해 이후 엔진과 리포터가 같은 입력과 provenance를 보도록 했다.

## Context

이전 구조에서는 엔진이 source directory, backend, tool availability를 각자 다시 찾고,
coverage·sanitize·release 빌드가 같은 shadow tree를 사용할 가능성이 있었다. 그 결과
다음 실행에서 stale binary나 다른 instrumentation 결과를 정상 결과로 오인할 수 있고,
reporter가 결과를 표시하는 과정에서 실행 입력을 바꿀 경계도 분명하지 않았다.

이번 경계의 원칙은 다음과 같다.

- discovery와 capability probe는 실행 초기에 한 번만 수행한다.
- immutable context는 엔진·reporter가 읽을 수만 있고, 새 상태는 새 snapshot으로 발행한다.
- build adapter만 mutable `BuildSession`을 소유한다.
- release, coverage, sanitize는 서로 다른 variant와 shadow suffix를 사용한다.
- 산출물은 project/shadow root 안의 regular file만 manifest에 기록한다.

## Changes Made

### 1. Immutable analysis ownership

`ProjectModel`은 canonical root와 project-relative POSIX source/header/include 범위, metadata,
backend 선택 결과를 tuple로 보존한다. 기존 bounded `CapabilityInventory` 객체는
`AnalysisContext`에 그대로 전달하고, `CompilationContext`는 compile unit의 source,
directory, argv, output과 database path를 immutable snapshot으로 보유한다.

backend descriptor 탐지는 `core/backend.py`로 분리했다. 따라서 project discovery가 mutable
build session adapter인 `core/cmake.py`를 역참조하지 않으며, context·model·build adapter
사이의 import graph도 단방향을 유지한다.

`AnalysisIdentity`는 다음 세 identity를 함께 기록한다.

```text
source_commit   = Git HEAD 또는 unavailable
config_digest   = canonical JSON SHA-256
toolchain_digest = capability snapshot SHA-256
```

### 2. Mutable session과 frozen manifest 분리

configure/build/test 동안 누적되는 tool evidence와 error는 mutable `BuildSession`에만
남긴다. 성공한 산출물은 `ArtifactManifest`로 변환하며 각 record는 variant, scope/path,
kind, producer, SHA-256, byte size, file mode를 포함한다.

```text
AnalysisContext
├── ProjectModel
├── CapabilityInventory
├── AnalysisIdentity
├── CompilationContext
└── requested_variants: RELEASE | COVERAGE | SANITIZE

BuildSession (mutable, adapter-owned)
└── ArtifactManifest (frozen, validated output)
```

### 3. Containment and redaction boundary

- project root와 shadow root는 canonical path로 확인한다.
- 상대 path의 `..`, 절대 path, root 밖 경로와 root 밖으로 해석되는 symlink는 거부한다.
- manifest는 regular file만 허용하고, 재검증 시 size·mode·SHA-256이 바뀌면 거부한다.
- context/report JSON은 project-relative POSIX 경로만 투영한다.
- 외부 include/search path처럼 machine-specific root가 포함될 수 있는 값은 reporter의
  redaction 경계를 통과한다. 외부 경로와 credential은 HTML·JSON·Markdown·console에
  그대로 남기지 않는다.

### 4. Variant isolation and reporting contract

`build`는 `RELEASE`, `test`는 `COVERAGE`, `sanitize`는 `SANITIZE`를 명시적으로 요청한다.
coverage와 sanitizer flags 및 shadow suffix는 중앙 `ConfigureOptions`에서 결정해 서로의
object와 report를 재사용하지 않는다.

`ici.result/v3`에는 기존 소비자와의 호환을 위해 다음 optional projection을 추가한다.

- `analysis_context`: `ici.analysis-context/v1`
- engine `artifact_manifests`: `ici.artifacts/v1`

두 확장은 project facts, compilation snapshot, requested variants, source/config/toolchain
identity와 artifact provenance를 보존한다. 기존 v3 payload가 확장 필드를 갖지 않아도
계속 읽고 migration할 수 있다.

## Files Updated

- `CHANGELOG.md` — Unreleased I2-2 변경·보안·호환성 요약
- `docs/architecture.md` — shared context 소유권과 reporter projection 설계
- `docs/user-guide.md` — JSON context/manifest 사용 및 variant/shadow 설명
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md` — I2-2
  다섯 체크박스 완료, I2-3/I2-4 잔여 상태
- `docs/superpowers/2026-08-30-handover.md` — 다음 세션용 현재 상태와 보안 경계
- `workthrough/2026-08-31-analysis-context-artifact-manifest.md` — 이 작업의 rationale와
  검증 경계

## Verification

계약 테스트는 project discovery, artifact filesystem boundary, variant flags/session,
orchestrator context identity, JSON/schema projection과 legacy v3 migration을 각각
검증한다. 최종 PR은 정확한 테스트 수를 문서에 고정하지 않고 Python 3.10 full suite green,
Ruff, reproducible pyz, smoke와 self-verify를 Merge Gate의 근거로 사용한다.

첫 독립 self-verify는 단위 테스트가 놓친 두 전용 엔진 생성자의 context 인자 회귀와 새
context↔build adapter import cycle, manifest 속성의 타입 annotation 누락을 검출했다.
`DeadCodeEngine`·`ExceptionSafetyEngine`의 전달 계약 테스트를 추가하고 backend discovery를
분리했으며, manifest 목록은 최초 선언에만 명시 타입을 두도록 정리했다. 수정 후 로컬
Python 3.10 전체 회귀는 `872 passed`, Ruff check/format은 전 범위 통과했고 mypy도 64개
source file에서 issue 0을 확인했다. 최종 pyz·smoke·self-verify와 CI 증거는 PR 검증 결과로
확정한다.

문서 전용 변경에 대해 다음 sanity check를 수행한다.

```bash
rg -n "I2-2|AnalysisContext|ArtifactManifest|ici\.analysis-context/v1|ici\.artifacts/v1" \
  CHANGELOG.md docs/architecture.md docs/user-guide.md \
  docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md \
  docs/superpowers/2026-08-30-handover.md \
  workthrough/2026-08-31-analysis-context-artifact-manifest.md
git diff --check
```

I2-3 engine DAG와 I2-4 cache/invalidation은 이 작업에 포함하지 않으며 다음 checkpoint로
남긴다.
