# 도구 선택: 못 찾으면 다른 걸로 돌리지 않는다

| | |
|---|---|
|상태|**구현 시작됨 (WP06 PR A).** 순수 선택 규칙과 환경 스냅샷. 실제 프로세스 시험은 PR B, 첫 engine 연결은 PR C.|
|근거 이슈|[WP06 #204](https://github.com/jihoon22-lee/ici/issues/204) 작업 1~4·6, [SPEC-02](spec-02-distribution-execution.md)|
|구현|[`src/ici/toolchain/`](../../../src/ici/toolchain) — `resolution.py`, `resolver.py`, `environment.py`|
|검증|[`tests/test_toolchain_resolution.py`](../../../tests/test_toolchain_resolution.py)|

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

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|실제 프로세스로 core/project 환경 분리·symlink launch 확인|PR B|
|bundle/project 실제 process tests|PR B|
|첫 engine의 기존 resolver 연결 제거|PR C|
|NAS/부서 라이브러리 하드코딩 제거와 migration 경고|PR C (작업 7)|
|`doctor` 연결|[#210](https://github.com/jihoon22-lee/ici/issues/210). 선택 이유와 config key는 **이미 구조화돼 있다**|
