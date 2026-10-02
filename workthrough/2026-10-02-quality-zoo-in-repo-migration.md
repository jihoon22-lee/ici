# Quality Zoo corpus의 ici 저장소 이관

## Overview

#201/R13의 소유 요구를 실행했다: known-answer corpus `quality-zoo/`를
toy-projects(@ `195de9b`)에서 이 저장소로 이관하고, candidate 수용
워크플로가 교차 저장소 checkout 대신 이 저장소의 exact revision을
검증하도록 바꿨다.

## Context

- toy-projects는 ici 결합을 제거하고 제품 중심으로 재편된다. corpus가 그곳에
  남으면 corpus가 아닌 저장소가 corpus의 호스트가 되는 모순이 된다.
- `docs/design/ici-next/quality-zoo-ownership.md`가 이미 "고정은 충족,
  소유가 미충족"으로 진단하고 있었고, 이관 시 등록부(`manifest.toml`)에
  행을 추가하라는 규칙을 명시했다.

## Changes Made

1. **Corpus 이관**: `quality-zoo/` 전체(시나리오 16, runner, corpus 테스트,
   두 manifest)를 바이트 단위로 복사. 시나리오·기대값·runner 계약은 저장소
   위치와 무관하다.
2. **`candidate-quality-zoo.yml` 재배선**: `toy_target_sha` → `corpus_sha`.
   corpus checkout이 same-repo의 exact SHA sparse checkout으로 바뀌고,
   `pull_request` 모드는 이 저장소 PR을 검증한다. revision 기록 스키마는
   `ici.quality-zoo-corpus-revision/v1`.
3. **`candidate_merge_gate.py`**: `verify-toy-pr` → `verify-pr`
   (함수·dataclass·메시지 모두 저장소 비특정으로). 계약은 동일: 열린
   same-repo PR, base=main, head SHA 일치, fork 거부.
4. **등록부 연동**: 16개 시나리오를 `manifest.toml`에 `quality-zoo/*` 행으로
   등록. `requires`는 각 시나리오 `ici.toml`의 `[doctor] required_tools`를
   그대로 미러하고(순수 소스분석 시나리오는 빈 요구가 정직한 값), Qt
   시나리오만 `cmake_package = "Qt6", components = ["Core"]` probe로
   표현했다 — CMakeLists가 `find_package(Qt6 REQUIRED COMPONENTS Core)`를
   실제로 요구한다.
5. **위생 검사 확장**: `hygiene_allow` 필드를 등록부 스키마에 추가해,
   credential-shaped bait를 품은 security 시나리오에 그 패턴만 exempt.
   알려지지 않은 키는 테스트가 실패로 답한다.
6. **Dogfood 격리**: corpus는 분석 대상이 아니다. `ici.toml`의
   `engines.line exclude_dirs`와 ruff `extend-exclude`로 격리하고,
   `ci.yml`에 corpus unittest 스텝을 추가했다.
7. **문서 동기화**: `ci-integration.md`, `architecture.md`의 계약 기술,
   `quality-zoo-ownership.md`의 소유 표와 미확인 행, traceability의 R13
   행, CHANGELOG.

## 검증

- `pytest`(test_candidate_merge_gate, test_purity, test_corpus_hygiene 포함),
  `ruff check`, `ruff format --check` 실행 결과를 PR 근거로 남긴다.
- corpus 단위 테스트는 `cd quality-zoo && python3.10 -m unittest discover
  -s tests -v`로 로컬 확인한다.

## 남은 것

- toy-projects 쪽 `quality-zoo/` 삭제는 이 PR 머지 후 별도 작업.
- toy 제품의 released-ici 소비자 검증은 ici.toml 없는 toy에서도
  auto-detection으로 계속 가능하다(SPEC-05 §3의 방향과 일치).
