# 실패가 빈 PASS가 되지 않게

| | |
|---|---|
|상태|**구현 시작됨 (WP07 PR A).** 결과 분류와 provider 계약. 취소·process tree는 PR B, output manifest·lock은 PR C.|
|근거 이슈|[WP07 #205](https://github.com/jihoon22-lee/ici/issues/205) 작업 1·2·6|
|구현|[`src/ici/execution/process.py`](../../../src/ici/execution/process.py)|
|검증|[`tests/test_execution_process.py`](../../../tests/test_execution_process.py)|

## 다섯 가지가 PASS가 되면 안 된다

#205의 첫 인수 기준은 목록이다 — **timeout·signal·긴 출력·잘못된 출력·취소**. 공통점이 하나
있고, 잘못 다뤄지는 방식도 하나다.

공통점: **도구가 발견한 것을 다 말하지 못하고 끝났다.**
잘못 다뤄지는 방식: `returncode == 0`을 보는 호출자는 다섯 개를 전부
*"잘 돌았고 아무것도 못 찾았다"*로 읽는다 — **깨끗한 실행과 구별되지 않는다.**

그래서 결과는 **숫자가 아니라 이유**이고, 숫자는 그 안의 데이터다.

|이유|뜻|
|---|---|
|`FINISHED`|끝까지 갔다. **exit code를 읽을 가치가 있다**|
|`TIMED_OUT` · `SIGNALLED` · `OUTPUT_TRUNCATED` · `START_FAILED` · `CANCELLED`|끝까지 가지 **못했다**|

`FINISHED`가 아닌 것은 **무엇도 답이 될 수 없다.** 테스트가 목록을 따로 적지 않고
**enum 전체를 훑어** 확인하므로, 나중에 추가되는 이유도 빠뜨릴 수 없다.

### 이 한 줄이 요점이다

```
flood   output-truncated   exit=0   ->  did-not-run
```

**exit code는 0인데 답이 잘렸다.** 순진하게 보면 깨끗한 통과다. 여기서는 실행되지 않은 것으로
친다 — **잘려나간 부분이 findings가 있던 부분일 수 있기** 때문이다.

parser에게는 **끝까지 간 실행의 출력만** 준다. 잘린 스트림을 넘겨받은 parser는 **진짜와 똑같이
생긴 findings**를 만들어 내고, 잘린 만큼이 빠져 있다. 사람이 볼 로그는 그대로 남긴다.

## 반대 방향: findings는 실패가 아니다

작업 6이 요구하는 구분이고 칼날이 반대쪽을 향한다. **violation을 찾아서** exit 1을 내는 linter는
**완벽하게 동작한 것**이다. exit code가 그 도구의 **답**이다.

- 그걸 process failure로 읽으면 **멀쩡한 도구를 고장 났다고** 보고한다.
- 성공으로 읽으면 **violation이 없다고** 보고한다.

그래서 provider가 **어느 exit code가 답인지**를 `ExitContract`로 말하고, 해석은 관례가 아니라
**그 계약에 대해** 이뤄진다.

|계약|exit 0|exit 1|exit 2|
|---|---|---|---|
|`ExitContract(success=(0,), findings=(1,))`|succeeded|**found-findings**|failed|
|`ExitContract()` (기본)|succeeded|**failed**|failed|

같은 exit 1이 계약에 따라 다르게 읽힌다 — **관례가 아니라는 것이 핵심이다.**
한 코드가 두 뜻을 가질 수 없다(`success`와 `findings`가 겹치면 생성 자체가 거절된다).

## 기존 runner를 다시 쓰지 않았다

#205가 *"기존 core runner의 검증된 timeout/출력 제한 기능을 재사용한다"*고 적었다. bounded
capture, 파이프가 가득 차도 교착하지 않게 하는 drain 스레드, POSIX process group 정리는
**이미 동작하는 것**이고, 다시 쓰면 그 동작을 **다시 벌어야** 한다. 이 계층은 그것을 감싸고
**결과를 분류**한다.

## 환경은 명시이고 상속이 아니다

`TaskSpec.environment`의 기본값은 **빈 매핑**이지 `None`이 아니다. `None`은 "상속"을 뜻하고,
상속은 task가 **보고하는 것과 다른 것을 분석**하게 되는 경로다.

두 번째 인수 기준(*"동시에 다른 env/cwd를 쓰는 두 task가 서로 오염되지 않는다"*)은 두 task를
**실제로 동시에** 돌려 확인한다. task 실행이 이 프로세스의 cwd나 환경을 바꾸지 않는 것도
함께 고정했다.

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|process group 단위 취소, grace 종료 후 남은 자식 정리, 로그 경계|PR B (작업 3)|
|output manifest·exclusive lock·원자적 publish, 실패/취소 결과의 cache 승격 거부|PR C (작업 4)|
|첫 provider/probe 경로를 공통 executor로 이관|PR C (작업 5)|
|`CANCELLED`의 실제 생성|PR B. **의미는 여기서 이미 고정**돼 있어서, 구현이 다른 뜻을 들고 올 수 없다|
