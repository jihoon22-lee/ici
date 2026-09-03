# Candidate Quality Zoo 매니페스트 선택 경계

## Overview

후보 ici를 toy-projects Quality Zoo로 수용할 때 candidate 전용 시나리오를 stable 시나리오와
분리할 수 있도록 manifest 선택 경계를 보강했다. exact toy `main` commit에
`quality-zoo/candidate-manifest.json`이 있으면 이를 우선 사용하고, 아직 candidate 파일을
제공하지 않는 기존 commit에서는 `manifest.json`을 그대로 사용한다. 선택 결과와 digest도
acceptance artifact에 남겨 원격 수용 결과가 실제로 어느 기대값 집합을 실행했는지 감사 가능하게
했다.

## Context

기존 `.github/workflows/candidate-quality-zoo.yml`은 항상 `manifest.json`만 실행했다. 이
구조에서는 TSan처럼 새 ici candidate에서만 제공되는 시나리오를 Quality Zoo에 추가하려면
stable toy PR gate와 같은 manifest를 변경해야 하므로, released ici `v0.10.2` 경계를
오염시키거나 candidate-only 시나리오를 일반 gate에서 실행할 위험이 있었다. workflow는
여전히 exact toy SHA와 candidate provenance를 먼저 검증하고, 이 변경은 그 이후의 local
manifest 선택만 확장한다.

## Changes Made

### 1. Candidate consumer workflow

File: `.github/workflows/candidate-quality-zoo.yml`

- `Select Candidate Quality Zoo Manifest` 단계를 추가했다.
- `candidate-manifest.json`을 먼저 확인하고, 파일이 없을 때만 `manifest.json`으로 fallback한다.
- 두 입력 모두 regular non-symlink 파일인지 확인한다. candidate 파일이 malformed이거나
  비정상 파일이면 stable 파일로 조용히 우회하지 않고 실패한다.
- 선택 manifest의 SHA-256을 step output으로 전달하고, runner 실행 전후에 재확인한다.
- runner에는 고정된 manifest 상대 경로만 전달해 step output이 임의 경로로 확장되지 않도록
  한다.
- `results/manifest-selection.json`에 다음 계약을 기록한다.

```json
{
  "path": "candidate-manifest.json",
  "schema": "quality-zoo.manifest-selection/v1",
  "sha256": "<64 lowercase hex>",
  "source": "candidate"
}
```

`candidate-manifest.json`이 없을 때 같은 구조에서 `path`는 `manifest.json`, `source`는
`stable-fallback`이 된다.

### 2. Static regression contract

File: `tests/test_purity.py`

`test_candidate_quality_zoo_prefers_candidate_manifest_with_stable_fallback`가 다음을
고정한다.

- candidate branch가 stable fallback보다 먼저 평가되는지
- 두 경로의 정규 파일·non-symlink 검사와 digest 계산
- exact 허용 경로 조합만 실행되는지
- runner가 선택 경로를 사용하고 selection evidence schema를 생성하는지

### 3. Documentation synchronization

- `README.md`: candidate consumer의 선택 우선순위, digest 재검증, evidence 계약을 기록했다.
- `docs/ci-integration.md`: 운영 순서와 malformed candidate 처리 정책을 기록했다.
- `docs/architecture.md`: exact toy SHA/provenance와 manifest 선택·감사 evidence의 경계를
  기록했다.
- `CHANGELOG.md`: Unreleased에 버전 변경 없는 candidate manifest 기능을 기록했다.

## Verification Results

```text
uv run --python 3.10 pytest -o addopts='' tests/test_purity.py
32 passed
```

추가 검증은 다음을 실행했고, 결과를 커밋 전에 확인한다.

```text
actionlint .github/workflows/candidate-quality-zoo.yml
git diff --check
```

이 변경은 version/tag/release, candidate artifact producer, toy-projects 파일, PR 또는
GitHub 원격 상태를 변경하지 않는다. ici stable version은 `v0.10.2`로 유지한다.

## Next Steps

- toy-projects에서 candidate-only TSan Quality Zoo manifest와 fixture를 추가한다.
- 새 ici candidate producer와 exact toy main SHA를 사용해 consumer workflow를 dispatch하고,
  acceptance artifact의 `manifest-selection.json` 및 suite 결과를 독립 감사한다.
