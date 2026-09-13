# Quality Zoo 의존 대체 mapping

| | |
|---|---|
|상태|**경계 확인 완료, 이전 미실행.** 아래 표는 ici 저장소에서 보이는 것만으로 유도했다.|
|근거 이슈|[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201) 작업 6, 요구사항 R13|
|관련|[corpus-register.md](corpus-register.md)|

## 먼저 정정: 고정은 이미 충족돼 있다

이슈는 "latest toy main을 필수 입력으로 사용하지 않는다"를 요구한다.
`.github/workflows/candidate-quality-zoo.yml`을 읽으면 **이미 충족돼 있다.**

- `toy_target_sha`가 **필수 입력**이다.
- `toy_revision_mode: main`에서도 `git ls-remote`로 SHA를 확정해 기록하고
  (`ici.quality-zoo-toy-revision/v1`), checkout 후 `rev-parse HEAD`가 그 SHA와 같은지 검사한다.
- 이 워크플로는 `workflow_dispatch` 전용이고, 일상 CI(`ci.yml`)는 여기에 의존하지 않는다.

**미충족인 것은 고정이 아니라 소유다.** R13과 #201 인수 기준
"corpus가 ici 저장소와 고정 외부 tool만으로 재실행 가능하다"가 걸리는 지점은 거기다.

## 소유 경계

|자산|현재 소유|R13 관점|
|---|---|---|
|`.github/workflows/candidate-quality-zoo.yml`|**ici**|이전 불필요|
|`scripts/candidate_merge_gate.py` — candidate/Merge Gate 신원 사슬 검증|**ici**|이전 불필요. **provenance 쪽은 이미 ici 소유다**|
|`scripts/candidate_bundle.py` — candidate 빌드|**ici**|이전 불필요|
|`quality-zoo/manifest.json` (`candidate-manifest.json`) — 무엇을 돌리고 무엇을 기대하는가|toy-projects|**이전 대상.** 이것이 곧 corpus의 정의다|
|`quality-zoo/runner/` (`runner.candidate_intake`) — 실행 기계|toy-projects|**별도 판단.** corpus가 아니라 실행기다. ici가 소유해도 되고, 고정 외부 tool로 취급해도 된다|
|`quality-zoo/` 아래의 대상 프로젝트들|toy-projects|**미확인** — 이 세션의 접근 범위 밖이라 구조·규모·라이선스를 읽지 못했다|

## 미확인을 비워 두지 않는다

toy-projects 저장소는 이 세션의 접근 범위 밖이다. 그래서 위 표의 마지막 행과, manifest의
실제 스키마·기대치 형식은 **"미확인"**이며, 추정으로 채우지 않았다.
[등록부](corpus-register.md)와 [추적표](requirements-traceability.md)가 쓰는 규칙과 같다 —
빈 칸은 "근거 없음"이지 "해당 없음"이 아니다.

이전 계획을 실제로 세우려면 먼저 다음을 읽어야 한다.

1. `quality-zoo/manifest.json`의 스키마 — 프로젝트 목록·기대 결과·임계값이 어떤 형태인가
2. 대상 프로젝트들의 출처와 라이선스 — ici가 소유하려면 재배포 가능해야 한다
3. `runner.candidate_intake`가 manifest에 대해 요구하는 계약

## 이전할 때 쓸 형식은 이미 있다

manifest를 ici로 옮길 때 새 형식을 발명할 필요가 없다.
[`tests/fixtures/manifest.toml`](../../../tests/fixtures/manifest.toml)이 이미 fixture당
**요구 도구(probe), 언어, 기대 결과, 비용, 안전 조건, 출처, 라이선스**를 담고,
등록부 테스트가 디스크↔등록부를 양방향 대조한다. Quality Zoo 항목은 그 등록부의 행이 되면 된다.

위생 기준도 이미 검사된다 — [`tests/test_corpus_hygiene.py`](../../../tests/test_corpus_hygiene.py)가
등록된 fixture에 절대 경로·사내 이름·호스트/주소·자격증명 모양이 없는지 확인한다(#201 인수 기준).
이전해 오는 자산은 그 검사를 자동으로 통과해야 한다.

## 이 문서를 갱신하는 규칙

1. toy-projects를 실제로 읽을 수 있게 되면 **"미확인" 행부터** 채운다.
2. 이전이 일어나면 해당 자산의 `현재 소유`를 바꾸고 등록부에 행을 추가한다. 행을 지우지 않는다.
3. "고정은 충족, 소유가 미충족"이라는 구분을 흐리지 않는다. 둘은 다른 요구다.
