# Candidate-to-Quality-Zoo 수용 경계 — 원격 acceptance audit

## Overview

ici candidate artifact를 toy-projects Quality Zoo에 주입해 확인하는 별도 수용 경계를 구현하고,
exact ici/toy revisions에 대한 원격 acceptance를 독립 감사했다. 이 경계는 일반 toy PR의
released-artifact 검증과 stable release를 대체하지 않으며, 이번 acceptance도 기존 sanitizer
corpus의 rule/status/evidence/confidence/path/line 계약과 runtime ASan/LSan/UBSan/clean
증거만 닫는다.

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
  - exact remote dispatch와 expected rule/status/evidence/confidence/path/line audit을 완료로
    기록하고 Qt lifetime 및 broader checkpoints는 pending으로 유지했다.
- `docs/superpowers/2026-08-30-handover.md`
  - Q0 released-artifact evidence와 candidate consumer evidence를 분리하고, exact current
    ici/toy revisions에 대한 remote dispatch 및 결과 audit을 기록했다.

### 4. 버전·릴리스 경계

문서 변경은 `v0.10.2`를 유지한다. candidate artifact는 release/tag/Pages/PR comment를
생성하는 경로가 아니며, toy PR의 normal gate도 계속 released ici `v0.10.2`에 고정된다.

### 5. 독립 원격 acceptance audit

다음 exact binding을 GitHub Actions API와 raw artifact 다운로드로 다시 확인했다.

| 항목 | 확인값 |
|---|---|
| ici exact-main | [run `33707430378`](https://github.com/jihoon22-lee/ici/actions/runs/33707430378), `success`, attempt 1, `6df011f98be1a19092b112cb56c596dc35bcae4d` |
| candidate producer | [run `33706057540`](https://github.com/jihoon22-lee/ici/actions/runs/33706057540), target `9d470edca7ab037a24dcd6594531a822f116548b`, [artifact `9875319095`](https://github.com/jihoon22-lee/ici/actions/artifacts/9875319095) |
| candidate ZIP | `2,285,368` bytes, SHA-256 `4aec084b3a30ac01a1df5124fa3b42b7f51d23f66c12b490194a84549be9db27` |
| candidate `ici.pyz` | `2,284,045` bytes, SHA-256 `e7f1a2ce7147057538873a802715c7bf2b12e530a85070af862e02e378caceb8` |
| toy-projects input | exact `main` `2d0d7c0b2dcc137a782d6042438fc287bffdf570` ([commit](https://github.com/jihoon22-lee/toy-projects/commit/2d0d7c0b2dcc137a782d6042438fc287bffdf570)) |
| acceptance | [run `33710695336`](https://github.com/jihoon22-lee/ici/actions/runs/33710695336), job `100509326331`, `success`, head `6df011f98be1a19092b112cb56c596dc35bcae4d` |
| acceptance artifact | [ID `9876797536`](https://github.com/jihoon22-lee/ici/actions/artifacts/9876797536), `1,104,307` bytes, SHA-256 `e66ae2b65988abe10fc5ddb92a5c3bb6fc238ec2f77b7fd27ccfe75c24194a5f` |

Acceptance ZIP의 `quality-zoo.suite/v1`는 5개 scenario 모두 `contract_verdict: PASS`와
runner error 0을 기록했다. 후보 digest가 선택한 expected contract와 report evidence는
다음과 같다.

| Scenario | Expected rule / status / evidence / confidence / location |
|---|---|
| `cpp.asan-use-after-free` | `asan.heap-use-after-free` / `FAIL` / `MEASURED` / `exact` / `src/fault.cpp:5` |
| `cpp.lsan-memory-leak` | `lsan.memory-leak` / `FAIL` / `MEASURED` / `exact` / `src/fault.cpp:3` |
| `cpp.ubsan-signed-overflow` | `ubsan.signed-integer-overflow` / `FAIL` / `MEASURED` / `exact` / `src/fault.cpp:3` |
| `cpp.sanitizer-clean` | informational completion / `PASS` / `MEASURED` / `high` / `tests/test_clean.cpp:1`; active defect forbidden |
| `python.dead-private-function` | existing known-answer / `WARN` / `ESTIMATED` |

The acceptance workflow uploaded only the separate 14-day evidence artifact. It did not publish
Pages, write a PR comment/marker, invoke the normal publisher, create a tag/release, or change the
stable `v0.10.2` version. The audited temp directory was removed after verification.

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

No release, Pages publication, PR comment, or version change is claimed here; the remote candidate
acceptance evidence is recorded above.

## Next Steps

- Add and independently accept the Qt lifetime/ownership Quality Zoo scenario.
- Keep broader Q1–Q5, TSan, static taxonomy candidate, I4 aggregate, version, and release work
  pending until their separate evidence exists.
