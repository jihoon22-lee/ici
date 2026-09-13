# 첫 완성 경로: 선언에서 판정까지

> #206 PR A. **모든 엔진을 옮기기 전에 실제 사용 흐름이 성립하는지** 증명하는 P1 종료 작업이다.
> 이 문서는 **line/Ruff 하나의 경로**가 config → resolver → executor → provider → gate를
> 어떻게 지나는지 정한다. HTML과 bundle E2E는 PR B.

## 지나는 곳

```
ici.toml (component)        config/        선언
   ↓ selection              application/   무엇을 할지 — 도구를 찾아보지 않는다
Plan                        application/   못 하는 것도 들어 있다
   ↓ verify                 application/   실행과 판정
TaskSpec → run_task         execution/     #205의 "이유" 분류
   ↓ observe                adapters/      숫자가 아니라 관찰
Observation                 domain/
   ↓ gate                   domain/result  #196 진리표
GateOutcome → exit code
```

**임시 데이터 모델을 만들지 않았다.** task는 WP02의 `domain.TaskSpec`, 관찰은
`domain.Observation`, 판정은 `domain.GateOutcome`이다. 후속 DAG는 `depends_on`을 채우는
것이지 task가 무엇인지를 바꾸는 것이 아니다.

## 두 개의 "없음"은 다르다

|무엇|plan에|gate에|
|---|---|---|
|component가 **끈** check|**없다** — 요청된 적 없다|영향 없음|
|도구가 **없는** check|**있다**, blocked로|required면 **INCOMPLETE**|

SPEC-04가 한 줄로 적는다 — *"미선택과 적용 불가를 혼동하지 않는다."*

못 하는 것을 plan에서 빼면 **요청된 것보다 작은 실행을 설명**하게 되고, 그 차이가 보이지
않는다. `plan`이 실행 없이 "무엇을 할 것인가"에 답하는 명령인 이유가 이것이다.

**planner는 기계를 뒤지지 않는다.** 도구의 위치는 호출자가 한 번 물어 넘겨준다. planner가
직접 probe하면, *무엇이 일어날지*를 말하는 것이 목적인 명령이 **말하는 동안 설치된 것에
의존**하게 된다.

## 질문의 순서가 전부다

1. **필수 check가 전부 완료됐는가?** 아니면 INCOMPLETE. **위반을 같이 찾았어도 INCOMPLETE다.**
2. **위반이 있는가?** 있으면 FAIL.
3. 아니면 PASS.

반대로 물으면 스펙이 이름 붙인 실패가 된다.

- 끝내지 못했는데 아무것도 못 찾은 실행 → **PASS로 보고된다.** 안 봐서 깨끗한 것이다.
- 끝내지 못했는데 뭔가 찾은 실행 → **FAIL로 보고된다.** 도달한 적 없는 완결 판정이다.

그래서 **INCOMPLETE가 exit code에서 FAIL보다 우선**하고, finding은 어느 쪽이든 보존된다 —
*"미완료가 있다는 이유로 확인한 문제를 버리지 않는다."*

|실행|gate|exit|
|---|---|---|
|전부 완료, 위반 0|PASS|0|
|전부 완료, 위반 있음|FAIL|1|
|check/설정 선택 오류|(실행 안 함)|2|
|필수 미완료 — 위반 유무와 무관|**INCOMPLETE**|**3**|

**0개 선택은 설정 오류다.** 아무것도 검사하지 않고 PASS를 내는 실행은 **본 적 없다는
근거로 코드가 괜찮다고** 말한 것이다.

## 증거는 "성공했다"가 아니라 "끝까지 봤다"

`Observation.evidence_is_complete`가 판정에 쓰인다. **성공했지만 잘렸거나 시간이 넘은 실행은
부분 증거**이고, 거기 기댄 check는 완료가 아니다. #205의 `Outcome.ran_to_completion`이
한 겹 위에서 같은 말을 한다.

`observe()`의 첫 판이 **완료된 실행의 Observation에서 `truncated`·`timed_out`을 떨어뜨렸다** —
`evidence_is_complete`가 읽는 바로 그 필드다. 테스트가 잡았다. executor가 지금은 잘린 실행을
FINISHED로 분류하지 않지만, **gate가 그 사실에 의존해서는 안 된다.**

## Ruff: 두 가지를 할 수 없어야 한다

### 코드를 바꿀 수 없다

검증 도구가 자기가 검증하는 트리를 편집하면 **보고 대상 자체가 사라진다** — 다음 실행은
편집을 재고, 사람이 리뷰하는 diff는 검사받은 diff가 아니다.

argv는 **설정에서 조립되지 않고 여기서 고정**된다. 보호하는 것은 두 가지이고 **역할이 다르다**:

- **고정 head** (`check --output-format json --quiet`) — position 1이 항상 `check`이므로
  `ruff format`에 **닿을 수 없다.**
- **금지 옵션** (`--fix`, `--select`, `--config` …) — argv 어디에도 없다.

첫 판은 이 둘을 한 목록에 섞었고, 그래서 **`format`이라는 이름의 디렉터리를 lint하기를
거부**했다. 경로는 위험하지 않다 — 이름 때문에 디렉터리 검사를 거부하는 건 **조심으로
분장한 버그**다. 테스트가 잡았다.

프로젝트의 `pyproject.toml`/`ruff.toml`은 **건드리지 않는다.** 프로젝트 것이 아닌 규칙으로
돌린 linter는 **아무도 하지 않은 질문에 답한다.**

### 못 읽은 출력을 깨끗한 파일로 보고할 수 없다

exit 1은 **위반을 찾았다는 뜻이고 도구는 동작한 것**(#205 작업 6), exit 2는 **일을 못 했다는
뜻**이다. 그 사이에 셋째가 있다 — **exit 1인데 parser가 못 읽는 출력.**

거기서 빈 finding 목록을 돌려주면 **이해하지 못했다는 근거로 코드가 깨끗하다고** 보고하는
것이고, 진짜 깨끗한 실행과 **구별되지 않는다.** 그래서 `ParsedOutput.failed_to_parse`가
빈 목록과 별개로 있다.

끝까지 가지 못한 실행은 **parser에 닿지도 않는다.** 잘린 스트림을 받은 parser는 진짜와
똑같이 생긴 finding을 만들고 잘린 만큼이 빠져 있다.

## 실제 도구로 확인한 것

단위 테스트만으로는 부족했고, 그게 요점이다. **손으로 쓴 fixture는 상대 경로를 썼고 전부
통과했다.** Ruff는 **절대 경로**를 낸다. 진짜 도구에 처음 붙인 순간 **모든 finding이
깨졌다.** fixture를 실제 출력 모양으로 바꿨다.

```
결함 seed    gate=FAIL        exit=1  findings=2   원본 그대로
깨끗한 코드   gate=PASS        exit=0  findings=0   원본 그대로
ruff 없음    gate=INCOMPLETE  exit=3
```

## 결과를 저장하고, 저장된 것을 보여준다 (PR B)

작업 5가 한 줄로 요구한다 — *"분석과 render 호출은 분리한다."*

```
verify()  →  Verification      실행과 판정
assemble()→  RunResult         저장할 수 있는 것
dumps()   →  result.json       저장된 것
loads()   →  RunResult
render()  →  result.html       저장된 것을 보여준 것
```

**reporter에서 executor로 가는 경로가 없다.** reporter가 도구를 돌릴 수 있으면
**결과를 보는 행위가 결과를 바꿀 수 있게** 되고, "같은" 리포트를 읽는 두 사람이 **다른
실행을 읽고** 있을 수 있다. import 그래프로 확인한다.

HTML은 **저장된 것에서 파생되고 다시 재지 않는다.** 세어 보기라도 다시 하면 옆에 있는
JSON과 **서로 어긋날 수 있고, 둘 다 똑같이 공식적**이다. 그래서 테스트는 저장했다가 **다시
읽은 것**으로 render한 결과가 같은지를 본다.

### 외부 URL이 없다

CDN이 느려서가 아니다. ici는 **거기 닿을 수 없는 기계**를 위한 것이고, 거기서 다르게
렌더되는 리포트는 **가장 중요한 자리에서 자기 자신에 대해 거짓말**을 한다.

테스트는 알려진 CDN 목록을 확인하지 않는다 — **scheme을 가진 URL 전부**를 정규식으로 찾는다.
목록 방식은 **누군가 다른 것을 쓰는 날 통과**한다.

### 떨어뜨리지 않는다

INCOMPLETE 실행은 **이유를 페이지에 적는다.** 이유를 말하지 않는 INCOMPLETE는 **리포트판
빈 PASS**다. 기록된 limitation도 마찬가지다 — 실행 중 기록됐는데 페이지에서 빠지면, 독자가
원했을 사실이고 **이제 물어볼 수도 없다.**

finding 메시지는 **도구가 남의 소스를 읽고 만든 텍스트**이므로 escape한다.

## 검증하는 트리에 아무것도 쓰지 않는다

테스트가 잡았다. Ruff는 내버려 두면 **검사 중인 트리에 `.ruff_cache/`를 떨군다.**
**검증하는 대상에 쓰는 검증 도구는 보호된 checkout을 아예 볼 수 없다** — #206이 read-only
설치를 요구하는데도.

기본값은 `--no-cache`다. 보고되는 내용은 어느 쪽이든 같고, 속도를 되찾고 싶은 호출자는
**자기 디렉터리를 지정**한다. cache 옵션은 금지 목록에 없다 — cache 위치는 lint 규칙이
아니고, **여기서 의도적으로 정한다.**

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|type/test/C++|이 WP는 주장하지 않는다|
|CLI 연결과 bundle E2E|작업 6의 나머지|
