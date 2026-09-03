# Candidate-to-Quality-Zoo 수동 수용 경계 구현

## Overview

ici candidate artifact를 toy-projects Quality Zoo에 주입해 확인하는 별도 수용 경계를 구현하고 문서화했다.
이 경계는 일반 toy PR의 released-artifact 검증과 stable release를 대체하지 않는다. 현재 작업은
`.github/workflows/candidate-quality-zoo.yml`의 local/manual workflow contract를 설명하는
단계이며, exact current ici/toy `main`을 대상으로 한 원격 candidate acceptance run은 아직
수행하지 않았다.

## Context

이전 Q0 evidence는 released ici `v0.10.2`를 사용하는 toy CI 경로만 수락했다. candidate
producer는 별도 workflow에서 provenance-bound ZIP을 만들지만, 이를 소비하는 workflow가
없으면 후보 빌드가 실제 Quality Zoo 기대값을 만족하는지 확인할 수 없다. 반대로 후보 실행에
쓰기 권한이나 PR publisher를 연결하면 candidate-controlled 코드가 게시 경계를 넘을 수 있다.
따라서 후보 consumer를 ici 저장소의 수동·읽기 전용 workflow로 분리하고, provenance/API
evidence와 runner 결과도 일반 PR comment/Pages와 분리했다.

## Changes Made

### 1. 읽기 전용 수용 workflow

- `.github/workflows/candidate-quality-zoo.yml`
  - 네 개의 필수 입력으로 exact ici/toy main SHA와 candidate artifact ID/raw ZIP digest를
    결합한다.
  - candidate provenance가 지목한 Actions run/check/job evidence를 별도 API 응답으로 다시
    검증한다.
  - candidate preflight와 Quality Zoo 실행에서 GitHub/OIDC/runtime credential을 제거한다.
  - PASS 계약을 만족한 결과만 별도 14일 evidence artifact로 업로드하며 Pages, PR comment,
    stable release 경로는 호출하지 않는다.
- `tests/test_purity.py`
  - workflow가 manual-only인지, 입력·exact-main binding·최소 권한·credential isolation·API
    evidence·별도 artifact naming 계약을 유지하는지 정적 회귀 테스트로 고정한다.
  - checkout/setup-python/upload-artifact action도 저장소 전체 immutable pin audit에 포함한다.

### 2. 사용자 문서

- `README.md`
  - candidate producer와 consumer를 분리하고, Q0 released-artifact acceptance가 candidate
    acceptance를 의미하지 않음을 명시했다.
  - `workflow_dispatch` 입력 네 개와 수동 실행 예시를 추가했다.
  - exact SHA/digest binding, provenance API 재검증, credential scrub, 별도 evidence artifact,
    publish/comment/Pages/version 변경 없음의 사용자 관점 계약을 기록했다.
- `docs/ci-integration.md`
  - 후보 수용 workflow의 입력·검증 순서·권한·성공 조건·보존 artifact를 CI 운영 절차로
    정리했다.
- `docs/architecture.md`
  - candidate producer와 consumer의 trust boundary, 인증 API 조회 단계와 무인증 candidate
    실행 단계, report publisher와의 분리 관계를 architecture에 추가했다.

### 3. 계획·인수인계 상태

- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
  - I9-0/I9-1에 workflow local/manual contract 구현을 표시했다.
  - 실제 remote dispatch와 expected finding/location audit은 미완료로 유지했다.
- `docs/superpowers/2026-08-30-handover.md`
  - Q0 released-artifact evidence와 candidate consumer evidence를 분리하고, 다음 작업을
    exact current ici/toy revisions에 대한 remote dispatch 및 결과 audit으로 고정했다.

### 4. 버전·릴리스 경계

문서 변경은 `v0.10.2`를 유지한다. candidate artifact는 release/tag/Pages/PR comment를
생성하는 경로가 아니며, toy PR의 normal gate도 계속 released ici `v0.10.2`에 고정된다.

## Workflow contract

```bash
gh workflow run candidate-quality-zoo.yml --ref main \
  -f ici_target_sha=<ici-main-sha> \
  -f candidate_artifact_id=<artifact-id> \
  -f candidate_archive_sha256=<archive-sha256> \
  -f toy_target_sha=<toy-main-sha>
```

1. ici `main`과 toy-projects `main`의 exact SHA, artifact ID, 원본 ZIP digest를 검증한다.
2. candidate ZIP과 manifest를 가져오고, candidate run/check/job/attempt 및 canonical URL을
   독립 Actions API 응답과 대조한다.
3. candidate intake preflight와 Quality Zoo 실행에서는 `GH_TOKEN`, `GITHUB_TOKEN`,
   OIDC/runtime token을 제거한다.
4. 검증된 local `ici.pyz`만 Quality Zoo runner에 전달하고
   `quality-zoo.suite/v1`, scenario count, `contract_verdict: PASS`, 빈 runner error를 요구한다.
5. preflight/intake/API evidence/Quality Zoo 결과를 별도 uncompressed 14일 artifact로 업로드한다.

## Verification Results

### Local gates

```text
uv run --python 3.10 pytest -o addopts=''
2092 passed, 7 skipped

uvx ruff check .
All checks passed!

uvx ruff format --check .
190 files already formatted

actionlint .github/workflows/candidate-quality-zoo.yml
exit 0

./scripts/build-pyz.sh
exit 0

./scripts/smoke.sh
all smoke checks passed, including Python 3.10 and Zero-CDN HTML

git diff --check
exit 0
```

No release, remote candidate acceptance dispatch, PR comment, Pages publication, or candidate
expected-result evidence is claimed here.

## Next Steps

- Merge candidate expectation scenarios into an exact toy-projects `main` revision.
- Dispatch this workflow from the exact ici `main` revision with the producer artifact coordinates.
- Audit the acceptance artifact and expected rule/status/evidence/path/line results before marking
  candidate consumer acceptance complete.
