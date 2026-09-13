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

## 취소는 예외가 아니라 사실이다 (PR B)

취소는 **기다리고 있는 스레드가 아닌 곳**에서 온다 — Ctrl-C, 답이 더는 필요 없어진 상위
consumer, batch를 걷어내는 scheduler. 그래서 `Cancellation`은 **설정되고 관찰되는 값**이지,
마침 쳐다보고 있던 스레드에 던져지는 예외가 아니다.

값인 것이 첫 인수 기준에 직결된다. 예외로 전달된 취소는 스택 어딘가의 `except`에 걸려
**"findings 없음"**이 되고, 그게 기준이 금지하는 빈 PASS다. 사실로 쥔 취소는 결과와 함께
이동하고 실행이 끝난 뒤에도 읽히므로, 호출자가 *"도구가 아무것도 못 찾았다"*와
*"말하기 전에 우리가 멈췄다"*를 구별할 수 있다.

한 번 요청된 취소는 **되돌릴 수 없다.** 되돌릴 수 있다면, 답이 필요 없다고 이미 판단된 뒤에
그 실행이 완료로 보고될 수 있다.

### 리더가 아니라 group을 본다

취소된 프로세스는 SIGTERM이나 SIGKILL로 죽는다. 순진하게 읽으면 **"signal 15로 죽음"** —
일부러 한 일에 대한 crash report다. 그래서 취소는 signal보다 **먼저** 분류된다. 원인이
증상을 이긴다.

`Cleanup.interrupted`는 **리더가 아니라 group에 대해** 묻는다. 이 구별은 테스트가 잡아낸
실제 결함이다: 리더는 즉시 exit 0으로 끝나고 자식이 stdout을 붙잡고 있는 실행을 취소하면,
리더의 poll()만 보는 구현은 **"이미 끝났으니 진짜 결과를 유지"**하기로 결정하고 그 실행을
**깨끗한 통과(FINISHED, exit 0)**로 보고했다. 리더가 끝났다는 것은 도구가 **할 말을 다 했다는
뜻이 아니다.**

pid를 하나씩 signal하는 것은 **자식을 계속 만들어 내는 것이 일인 프로세스와의 경주**다.
group으로 signal하면 교체된 자식에게도 닿는다 — 자식은 부모의 process group을 물려받기
때문이다(#205가 말하는 "자식 재생성" 사례).

### 정리했다 / 확인하지 못했다

세 번째 인수 기준의 핵심 단어는 **"확인"**이다. 예외 없이 돌았다고 `True`를 돌려주는 정리
루틴은 한 계층 아래의 **빈 PASS**다. 아무것도 확인하지 않았고, 에러가 없다는 것이 남은
자식이 없다는 뜻으로 읽혔다.

그래서 `Cleanup.survivors`의 상태는 둘이 아니라 **셋**이다.

|`survivors`|뜻|`verified`|`is_clean`|
|---|---|:---:|:---:|
|`frozenset()`|봤고, 아무것도 안 남았다|O|**O**|
|`frozenset({...})`|봤고, 남아 있다|O|X|
|`None`|**볼 수 없었다**|X|**X**|

`None`이 빈 집합이 **아닌 것이 요점이다.** group을 열거할 수 없는 플랫폼은 조용히 성공을
보고하는 대신 "미확인"을 보고한다.

### grace는 형식이 아니다

SIGTERM 먼저 보내는 이유는, 스스로 끝낼 기회를 받은 도구가 **자기 임시 파일을 지우기**
때문이다. grace 후 SIGKILL을 보내는 이유는, SIGTERM을 무시하는 도구가 **자기를 멈추라고 한
취소보다 오래 살아남으면 안 되기** 때문이다.

### watchdog

#205의 위험 항목이 요구한다 — *"test harness가 멈추면 별도 watchdog과 강제 정리를 둔다."*
task 자신의 timeout 너머에 **한 겹 더 마감**을 둔다. timeout 처리 **자체가** 멈춘 경우를
위해서다. 상한 없는 대기는 멈춘 자식이 멈춘 CI job이 되는 경로다.

### 로그의 경계도 로그의 일부다

실행에는 이유를 **하나만** 붙일 수 있다. 그래서 pipe를 넘치게 하면서 동시에 시간도 넘긴
실행은 timeout으로 보고되고, 그러면 **잘렸다는 사실이 사라진다.** `truncated`는 선택된
이유와 별개의 사실로 남고, `log`는 잘린 지점을 **본문 안에** 적는다.

그냥 끊긴 로그는 **그냥 멈춘 도구처럼 읽힌다.** 바닥까지 스크롤한 사람이 나머지가 어디
갔는지 알 방법이 없다.

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|output manifest·exclusive lock·원자적 publish, 실패/취소 결과의 cache 승격 거부|PR C (작업 4)|
|첫 provider/probe 경로를 공통 executor로 이관|PR C (작업 5)|
