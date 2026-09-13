# 도구 선택: 못 찾으면 다른 걸로 돌리지 않는다

| | |
|---|---|
|상태|**구현 중 (WP06 PR A·B·C).** 선택 규칙·환경 스냅샷(A), 실제 프로세스 확인(B), 새 경로 candidate와 migration 경고(C). **live engine cutover는 남아 있다 — 아래 §마지막.**|
|근거 이슈|[WP06 #204](https://github.com/jihoon22-lee/ici/issues/204) 작업 1~4·6, [SPEC-02](spec-02-distribution-execution.md)|
|구현|[`src/ici/toolchain/`](../../../src/ici/toolchain) — `resolution.py`, `resolver.py`, `environment.py`, `launch.py`, `candidates.py`, `assumptions.py`|
|검증|[`tests/test_toolchain_resolution.py`](../../../tests/test_toolchain_resolution.py), [`tests/test_toolchain_processes.py`](../../../tests/test_toolchain_processes.py), [`tests/test_toolchain_candidates.py`](../../../tests/test_toolchain_candidates.py)|

## 출발점: 프로젝트가 자기 인터프리터 없이 테스트된다

아키텍처 문서가 `engines/test_interpreter.py`를 위반 지점으로 적어 뒀다. 재현했다.

```
running ici under : /usr/bin/python3
project has no .venv
tests would run on: /usr/bin/python3
```

`_resolve_python`은 프로젝트의 `.venv`를 찾지 못하면 **`sys.executable`** — 즉 **ici 자신이
돌고 있는 인터프리터** — 를 돌려준다. 그래서 프로젝트는 **자기 것이 아닌 Python으로** 테스트되고,
결과는 **그 사실을 말하지 않는다.** 다른 통과와 똑같이 생겼다.

#204의 첫 인수 기준이 이것이다: *"project interpreter를 찾지 못하면 core `sys.executable`로
테스트하지 않는다."*

**그래서 "못 찾았다"는 실행되지 않는 값이다.** `Unresolved`는 `usable`이 항상 False이고,
`availability`가 `AVAILABLE`이면 생성 자체가 거절된다. 호출자가 **결정해야 하고**, 돌아가는
무언가를 받지 못한다.

명시한 선택이 실패했을 때도 같다 — **다른 것을 고르라는 초대가 아니다**(작업 3).
대체 후보는 보고되지 않을 뿐 아니라 **probe조차 되지 않는다.** 한 번 재본 것은
"이미 되는 걸 아니까"라는 이유로 나중에 대체될 자리를 만든다.

## launch path는 identity가 아니다

`.venv/bin/python`과 그것이 가리키는 실제 파일은 **같은 파일이고 같은 도구가 아니다.**
realpath로 실행하면 가상환경을 건너뛰어서, 프로젝트가 선언한 패키지가 사라지고 시스템에 있는
것으로 돌아간다.

|필드|무엇|
|---|---|
|`launch_path`|**실행하는 것.** 쓰인 그대로|
|`identity_path`|realpath. digest와 "이 둘이 같은 인터프리터인가" 판별용|

probe도 **launch path로** 한다. realpath를 재면 **실행될 일 없는 인터프리터**를 보고하게 된다.

## 없음·너무 낡음·고장은 세 가지 답이다

현행 경로는 셋을 falsy 하나로 뭉갠다. 그래서 *"mypy가 없다"*와 *"mypy가 시작되지 않는다"*가
사용자에게 **같은 문장**으로 도착하는데, 고치는 방법은 다르다.

|답|뜻|고치는 법|
|---|---|---|
|`UNAVAILABLE`|그 이름·경로에 아무것도 없다|설치한다|
|`UNSUPPORTED`|돌았고, 버전이 낮거나 필요한 옵션이 없다|올린다|
|`BROKEN`|있는데 물어볼 수 없다 — timeout, 비정상 종료, 버전이 아닌 출력|무엇이 잘못됐는지 본다|

후보가 여럿일 때 **구체적인 이유를 잃지 않는다.** 하나가 timeout이고 나머지가 없으면
"쓸 수 있는 게 없다"가 아니라 **timeout이라고** 말한다 — 처음 구현은 이걸 뭉갰고, 자기 문서에
적어 둔 규칙을 자기 코드가 지키지 않는다는 것을 테스트가 잡았다.

## 기능은 버전으로 추측하지 않고 물어본다

작업 6이 요구하는 것이고, 이유는 단순하다 — **버전은 릴리스를 설명하지 빌드를 설명하지 않는다.**
버전이 아무리 높아도 그 빌드에 옵션이 빠져 있을 수 있다.

`required_capabilities`마다 **실제로 그 옵션을 넣어 실행해 본다.** 확인할 방법을 주지 않은
capability는 **있다고 가정하지 않고** 거절한다.

## 필요한 것만 잰다

세 번째 인수 기준: *"Python-only는 qmake/GCC를, C++-only는 Ruff/pytest를 무관하게 probe하지
않는다."*

`Resolver.probed`가 **실제로 버전을 물어본 경로**를 순서대로 들고 있다. 그래서 "재지 않았다"가
**확인 가능한 주장**이 된다 — 부재는 기록이 있어야만 검사할 수 있다.

이긴 후보 **뒤의** 후보도 재지 않고, 같은 요청을 두 번 해도 한 번만 잰다.

## 환경은 전역이 아니라 값이다

두 번째 인수 기준: *"다른 shell 초기화 파일 없이 env mapping만으로 동일 선택 결과가 나온다."*
쉘 rc 파일에 의존하는 선택은 **기록된 실행에서 재현할 수 없다.**

- `for_core()` — **Python 변수 3개만** 지운다. WP01이 `SSL_CERT_FILE`을 지우면 내부 서버의 CA를
  잃는다고 측정했으므로, "격리"는 거기서 멈춘다.
- `for_project()` — 진입 환경을 그대로 주고, overlay는 **복사본에** 적용한다. `PYTHONPATH`가
  필요했던 작업이 **다음 작업에 그것을 남기지 않는다** — 누가 되돌리기를 기억하지 않아도.
- `without_stale_virtualenv()` — `PATH`와 맞지 않는 `VIRTUAL_ENV`를 버린다. 리빌드를 가로질러
  열려 있던 터미널은 **돌고 있지 않은 인터프리터**를 보고하게 만든다.

스냅샷은 수정할 수 없다(`MappingProxyType`).

## probe를 주입하는 이유

작업 이름 그대로 *"probe executor를 주입하여 runner 구현과 불필요한 순환 의존을 만들지 않는다"*.
효과가 하나 더 있고 그 값이 같다 — **모든 규칙이 가짜 실행 파일 행렬로 시험된다.** stale
`VIRTUAL_ENV`, PATH에 도구 둘, 명시 경로 누락, 낮은 버전, 읽을 수 없이 긴 출력, 공백이 든 경로가
전부 **디스크 fixture가 아니라 단위 테스트**다.

## 실제 프로세스로 확인한 것 (PR B)

PR A의 규칙은 가짜 실행 파일로 시험했다. 그래서 **증명할 수 없는 것이 하나 남았다** —
`.venv/bin/python`으로 실행하는 것과 그것이 가리키는 파일로 실행하는 것이 **사실로** 다른가.

진짜 venv를 만들어 쟀다:

|실행|`sys.prefix`|프로젝트가 선언한 모듈|
|---|---|---|
|`.venv/bin/python`|`<project>/.venv`|import 됨|
|그 realpath|`/usr`|**import 안 됨**|

다섯 번째 인수 기준이 요구하는 것이고, **단위 테스트를 아무리 늘려도 할 수 없는 주장**이다.
venv의 site-packages에 모듈을 **직접 써넣어** 만들므로 네트워크가 필요 없다.

`launch.py`가 세 가지를 거절한다:

|거절|왜|
|---|---|
|**상속**|`subprocess`에 스냅샷 매핑만 준다. 스냅샷에 없는 변수는 자식에 닿을 수 없다 — 상속된 `PYTHONPATH`는 보고하는 것과 **다른 트리를 분석**하게 만드는 바로 그 경로다|
|**조용히 포기**|timeout과 출력 폭주를 결과의 **사실로** 돌려준다. 예외로 던지면 첫 `except`에서 "없음"과 합쳐진다|
|**셸**|argv는 리스트다. 공백이 든 경로는 공백이 든 경로다|

> 자식의 `os.environ`이 스냅샷과 **같지는 않다.** Python 자식은 PEP 538 locale coercion으로
> `LC_CTYPE`을 자기 환경에 추가한다 — `env={}`로 아무것도 주지 않아도 나타나는 것을 측정했다.
> 그래서 테스트가 주장하는 것은 "같다"가 아니라 **"이 프로세스에서 아무것도 건너가지 않았다"**이다.
> 부모에만 있는 sentinel이 자식에 보이지 않는 것으로 확인한다.

현행 fallback은 **테스트로 고정해 뒀다.** 새 코드의 테스트가 아니라 **오늘 stable 경로가 무엇을
하는지의 기록**이고, PR C가 engine을 옮길 때 diff가 "바뀌었다는 주장"이 아니라 **바뀐 동작**을
보여주게 한다.

## 후보는 설정에서만 나온다 (PR C, 작업 7)

두 가지 하드코딩이 새 경로에서 빠졌고, **같은 실수의 다른 옷**이다 —
**아무도 넣지 않았는데 답에 들어온 위치.**

|하드코딩|무엇|대신 쓰는 것|
|---|---|---|
|`core/env.py` `get_nas_cpp_lib_dir`|**한 부서의** C++ 라이브러리를, **한 버전에 고정해서**, ici 안에 컴파일해 넣었다|`ici.toml`의 선언된 build/tool 경로|
|`find_project_executable`|프로젝트가 말했든 안 했든 `.venv`를 본다 — **ici 자신의 인터프리터로 끝나는 추측 사슬의 첫 고리**|`[components.<id>.python] executable`|

**아무것도 선언하지 않은 component는 후보가 0개다.** resolver에 **되돌아갈 것이 없다.**
#204의 첫 인수 기준은 *"대체하지 않기를 기억해서"*가 아니라 **대체할 것이 없어서** 달성된다.

analyzer는 **bundle이거나 명시된 external**이고 **PATH는 후보가 아니다.** PATH에서 주운 linter는
결과를 *그 기계에 또 무엇이 깔려 있는지*에 달리게 만든다.

### migration 경고는 썩지 않는다

경고가 지고 있는 값은 *"당신 빌드가 이것에 의존하고, 대신 이렇게 쓰라"*인데, **설명하는 코드가
옮겨지는 순간 그 값이 거짓이 된다.** 그래서 각 항목이 파일과 본문 표식을 들고 있고,
`stale()`이 그것을 **다시 읽는다** — 없어진 코드를 설명하는 경고는 1년 뒤 누군가를 오도하는
대신 **테스트를 실패시킨다.**

> 새 경로에 하드코딩이 없다는 검사는 **구문 트리**로 한다. 처음 쓴 검사는 소스를 grep 했다가
> *"새 경로가 무엇을 피하는지 설명하는 docstring"*에 걸려 실패했다 — 산문을 읽는 검사는
> **산문만 맞고 코드는 틀린 모듈도 통과시킨다.**

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|**live engine cutover**|아래 참조. **이 PR에서 제외했다**|
|`doctor` 연결|[#210](https://github.com/jihoon22-lee/ici/issues/210). 선택 이유와 config key는 **이미 구조화돼 있다**|

### live cutover를 이 PR에서 하지 않은 이유 (측정)

`_resolve_python`을 새 resolver로 갈아끼우면서 자동 `.venv` 우선선택도 함께 빼면 —
작업 7이 요구하는 그대로 — **ici 자신의 `test` 게이트가 깨진다.**

ici의 `ici.toml`은 인터프리터를 **선언하지 않는다.** `[engines.test]`에 `python` 키가 없고,
지금은 전적으로 자동 `.venv` 탐색에 의존한다. 새 규칙 아래에서 그 프로젝트는 후보 0개,
즉 `Unresolved`가 된다 — **규칙이 옳게 동작한 결과**이지만, 설정이 먼저 따라오지 않으면
ici가 자기 자신을 검증하지 못한다.

순서가 있다: **설정이 인터프리터를 선언할 수 있게 된 뒤에** engine을 옮긴다. 그 연결이
[#210](https://github.com/jihoon22-lee/ici/issues/210)이다. 현행 fallback은
[`tests/test_toolchain_processes.py`](../../../tests/test_toolchain_processes.py)에
**고정돼 있어서**, 옮기는 PR의 diff가 "바뀌었다는 주장"이 아니라 **바뀐 동작**을 보여준다.
