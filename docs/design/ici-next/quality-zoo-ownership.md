# Quality Zoo 의존 대체 mapping

| | |
|---|---|
|상태|**이전 완료.** `quality-zoo/`는 이 저장소가 소유한다 (toy-projects @ `195de9b`에서 이관). corpus revision은 `candidate-quality-zoo.yml`의 `corpus_sha`/`corpus_revision_mode` 입력으로 고정된다.|
|근거 이슈|[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201) 작업 6, 요구사항 R13|
|관련|[corpus-register.md](corpus-register.md)|

## 먼저 정정: 고정은 이미 충족돼 있다

이슈는 "latest toy main을 필수 입력으로 사용하지 않는다"를 요구한다.
이전된 `.github/workflows/candidate-quality-zoo.yml`에서도 동일하게 충족된다.

- `corpus_sha`가 **필수 입력**이다.
- `corpus_revision_mode: main`에서도 `git ls-remote`로 SHA를 확정해 기록하고
  (`ici.quality-zoo-corpus-revision/v1`), checkout 후 `rev-parse HEAD`가 그 SHA와 같은지 검사한다.
- 이 워크플로는 `workflow_dispatch` 전용이고, 일상 CI(`ci.yml`)는 여기에 의존하지 않는다.

이관으로 바뀐 것은 corpus의 출처다: 이전에는 toy-projects를 exact SHA로 checkout했고,
이제는 이 저장소의 `quality-zoo/`를 exact SHA로 sparse checkout한다.
`pull_request` 모드도 같은 검증을 이 저장소의 PR에 적용한다.

## 소유 경계

|자산|현재 소유|R13 관점|
|---|---|---|
|`.github/workflows/candidate-quality-zoo.yml`|**ici**|완료|
|`scripts/candidate_merge_gate.py` — candidate/Merge Gate 신원 사슬 검증|**ici**|완료. `verify-pr`는 이 저장소 PR을 검증한다|
|`scripts/candidate_bundle.py` — candidate 빌드|**ici**|완료|
|`quality-zoo/manifest.next.json` — 무엇을 돌리고 무엇을 기대하는가 (next 계약; v3 매니페스트는 삭제됨)|**ici**|**이관 완료.** corpus의 정의다|
|`quality-zoo/runner/` (`runner.candidate_intake`) — 실행 기계|**ici**|**이관 완료.** corpus와 함께 옮겨 실행기와 기대값이 같은 revision을 공유한다|
|`quality-zoo/` 아래의 대상 프로젝트들|**ici**|**이관 완료.** 전부 이 저장소용으로 작성된 시나리오이며 재배포 가능하다|

## 이관하면서 확인한 것

이전 세션에서 "미확인"으로 비워 뒀던 행을 실측으로 채웠다.

1. `quality-zoo/manifest.next.json` 스키마 — 시나리오 id+path 목록이고, 각
   `scenario.json`(schema 3)은 단일 `expectation: expectations/next.json`을
   가리킨다. schema 2의 digest-keyed 기대값은 ici.next.run 계약으로 대체됐다
   (버전 문자열/digest가 아니라 result envelope의 producer가 선택 근거다).
2. 대상 프로젝트의 출처와 라이선스 — 16개 시나리오 전부 이 corpus용으로 작성된
   miniature 프로젝트다(cpp 11, python 5). 제삼자 vendored 소스가 없으므로
   "same as repository"로 등록했다.
3. `runner.candidate_intake` 계약 — archive SHA-256, repository, target sha,
   GitHub evidence 디렉터리를 요구한다. workflow만 바꾸면 되고 runner 계약은
   저장소 위치와 무관하다.

## 등록부 연동

16개 시나리오를 [`tests/fixtures/manifest.toml`](../../../tests/fixtures/manifest.toml)에
`quality-zoo/<scenario-id>` 행으로 등록했다(그중 15개가 `manifest.next.json`에
선언돼 돌고, `qt-missing-parent`는 next Qt 규약 check 부재로 주차됐다).
`requires`는 시나리오가 실제로 부르는 도구 — `prepare` argv, 빌드 시스템이
암묵적으로 호출하는 컴파일러, 활성 check가 띄우는 분석 도구 — 를 반영하고,
실행 파일 이름이 라이브러리를 표현하지 못하는 Qt 시나리오에는
`cmake_package = "Qt6"` probe를 썼다.

위생 검사([`tests/test_corpus_hygiene.py`](../../../tests/test_corpus_hygiene.py))는
등록된 corpus 전체를 스캔한다. `python.security-resource-correctness`의 bait는
의도적인 credential-shaped assignment이므로 등록 행의 `hygiene_allow`로
그 패턴만 exempt한다 — 나머지 패턴은 그 fixture에도 계속 적용된다.

## 이 문서를 갱신하는 규칙

1. toy-projects를 실제로 읽을 수 있게 되면 **"미확인" 행부터** 채운다.
2. 이전이 일어나면 해당 자산의 `현재 소유`를 바꾸고 등록부에 행을 추가한다. 행을 지우지 않는다.
3. "고정은 충족, 소유가 미충족"이라는 구분을 흐리지 않는다. 둘은 다른 요구다.
