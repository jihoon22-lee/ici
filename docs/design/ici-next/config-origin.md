# 설정 값의 출처: `_deep_merge` 이후 사라지던 것

| | |
|---|---|
|상태|**구현 중 (WP05 PR A·B).** schema v1 읽기·출처 보존(PR A), 계층 합성·정책 보호(PR B). 탐색과 `init`은 PR C 이후.|
|근거 이슈|[WP05 #203](https://github.com/jihoon22-lee/ici/issues/203) 작업 1·3·4, [SPEC-01 #193](https://github.com/jihoon22-lee/ici/issues/193) §2~§3|
|구현|[`src/ici/config/`](../../../src/ici/config) — `origin.py`, `paths.py`, `reader.py`, `documents.py`, `schema.py`, `layers.py`, `composition.py`, `overlay.py`|
|검증|[`tests/test_config_schema.py`](../../../tests/test_config_schema.py), [`tests/test_config_composition.py`](../../../tests/test_config_composition.py), [`tests/fixtures/config/`](../../../tests/fixtures/config)|

## 출발점: 품질 게이트가 조용히 뒤집힌다

SPEC-01 §3은 현행 로더의 문제를 **측정된 충돌**로 기록해 뒀다. 실제로 재현되는지 확인했다.

```python
(base / "ici.toml").write_text('[engines.lint]\nrequired = true\n')
(base / "dev.toml").write_text('[engines.lint]\nrequired = false\n')
load_config(base)["engines"]["lint"]["required"]
# → False
```

`dev.toml` 하나가 프로젝트가 **필수로 선언한 검사를 필수가 아니게 만들었다.** 그 자체는 의도된
우선순위일 수 있다. 문제는 그다음이다 — **반환된 dict에는 누가 그렇게 만들었는지가 없다.**
`_deep_merge`가 값을 덮어쓸 때 출처를 남기지 않기 때문이다
([`src/ici/config/__init__.py`](../../../src/ici/config/__init__.py) `_deep_merge`).

게이트가 왜 통과했는지 물어보는 사람에게 지금 줄 수 있는 답이 없다. 파일 네 개
(XDG 전역, `ici.toml`, `dev.toml`, `ICI_CONFIG`)를 직접 열어 보라는 것뿐이다.

**그래서 출처는 나중에 붙이는 메타데이터가 아니다.** `Sourced[T]`는 값과 출처를 같이 들고,
출처를 말할 수 없는 값은 이 스키마에서 **표현 자체가 불가능하다.**

```
root.toml: components[0].build: no build named release (declared builds: native)
```

메시지가 파일과 키를 말하는 이유도 같다. 사용자가 실제로 자기 파일에서 grep 할 문자열이어야
하므로, 키는 `components[0].build`이지 `<document>.components[0].build`가 아니다.

## schema v1이 거절하는 네 가지

#203이 이름을 댄 것들이고, 전부 **키와 출처를 포함해** 보고한다.

|거절|예|
|---|---|
|unknown key|`workspace.porfile: unknown key (did you mean profile?)`|
|duplicate id|`components[1].id: duplicate id shared (already declared at components[0].id)`|
|dangling reference|`components[0].build: no build named release`|
|inline + `config` 동시 정의|`components[0]: defined here and referenced by config at once (languages, root)`|

**unknown key를 따로 관리하는 목록은 없다.** 섹션이 아는 키는 "물어본 키"이고,
`Table.done()`이 남은 것을 전부 unknown으로 보고한다. 목록을 따로 두면 섹션이 추가될 때
목록만 갱신을 잊고, 그때부터 오타가 조용히 무시된다.

한 번에 **전부** 보고한다. 오타 세 개짜리 파일을 고치는 데 세 번 돌릴 이유가 없다.

## 경로처럼 생겼지만 같지 않은 세 가지

SPEC-01 §3이 나눈 구분이고, 나눈 이유는 각각 **엉뚱한 파일을 분석하게 되는 경로**가 있어서다.

|타입|기준|왜|
|---|---|---|
|`DeclaredPath`|**선언한 파일의 디렉터리**|root 파일을 child 파일로 쪼개도 경로 의미가 바뀌면 안 된다. 기준이 선언과 함께 움직여야 성립한다|
|`SourceGlob`|**component root**|component root는 소스가 있는 곳이다. glob을 선언 파일에 묶으면 `**/*.py`가 root 파일과 child 파일에서 **다른 집합**을 뜻하게 된다|
|`Executable`|구분자 유무|`python`은 PATH 탐색, `.venv/bin/python`은 경로. 파일이 존재하는지로 판단하지 않는다 — 그러면 설정의 의미가 **읽는 기계마다 달라진다**|

### cwd는 답에 들어가지 않는다

SPEC-01 §3은 *"실행 cwd가 달라도 선택 결과가 바뀌지 않아야 한다"*를 요구한다. 이 계층은
**파일시스템을 읽지 않고 `os.getcwd()`를 부르지 않는다.** 기준 디렉터리는 인자로 받고,
상대 경로가 기준으로 들어오면 거절한다. 의도가 아니라 **타입이 보장한다.**

`.`과 `..`도 문자열로 접는다. `Path.resolve`는 symlink를 따라가고 디스크를 보므로,
같은 설정이 기계마다 다른 답을 낼 수 있다.

### `${env:NAME}` 하나만

`$HOME`, `${HOME}`, `$(pwd)`, `` `pwd` ``는 **문자 그대로 둔다.** 셸 expansion과 command
substitution을 제공하지 않는다는 §3 요구이자, 설정 파일이 셸이 되지 않게 하는 선이다.

없는 변수는 **빈 문자열이 아니라 문제로 보고한다.** `${env:X}/bin`을 `/bin`으로 치환하는 것은
대부분의 기계에서 **존재하는 경로**가 되므로, 가능한 실패 중 가장 나쁘다.

## SPEC-01의 예시가 이제 실제 파서를 통과한다

SPEC-01 §4의 root/child 예시는 그동안 **TOML로 파싱되는지만** 검증됐다(완료 조건에 그렇게
적혀 있었다). 이제 사용자 파일과 **같은 reader**를 통과한다. 계약 문서의 예시와 구현이
어긋나면 어느 쪽이 틀렸는지 읽는 사람이 알 수 없기 때문이다.

## fixture는 자기가 무엇을 시험하는지 말한다

`tests/fixtures/config/invalid/*.toml`은 각자 헤더에 기대하는 문제를 적는다.

```toml
# kind: root
# expect: components[0].build: no build named release
```

테스트는 **그 문제가 보고됐는지**를 확인한다. 그냥 "오류가 났는지"만 보면, fixture가
**다른 이유로** 실패하기 시작해도 계속 통과한다 — 검사가 사라진 줄 모르는 상태가 된다.
이 WP가 존재하는 이유와 같은 고장이다.

## 계층 합성 (PR B)

순서는 SPEC-01 §3 그대로 defaults → root → component → local → CLI이고,
[`layers.py`](../../../src/ici/config/layers.py)의 **선언 순서가 곧 우선순위**다.
따로 적어 둔 우선순위 표와 코드가 어긋날 자리를 만들지 않는다.

합성된 값은 `Decided[T]` — 값, 출처, **어느 계층이 이겼는지**를 같이 든다.

```
True from root.toml: checks.lint.required (root)
```

### 파일을 어디에 썼는지는 의미를 바꾸지 않는다

인수 기준이 요구하는 것: *root-only와 child 분리 설정이 같은 effective config를 만든다.*

**두 경로를 비교해서 맞추는 것이 아니라, 경로가 하나다.** component body는 어디에 쓰였든
PR A의 같은 reader가 읽고, 합성은 그것이 어느 파일에서 왔는지를 **기록할 때 말고는 묻지 않는다.**
테스트는 두 배치의 결과를 출처만 빼고 비교하고, child 파일 쪽 glob 하나를 일부러 바꿔
**실제로 실패하는 것을 확인**했다.

child 파일은 자기 이름을 모른다. id는 root의 reference 항목이 준다 — 그래서 component 파일이
혼자 발견돼도 workspace로 승격되지 않는다(SPEC-01 §2).

### 두 가지 거절

|거절|왜|
|---|---|
|component가 root의 `required`를 **낮추는 것**|component가 낮출 수 있으면 *"이 workspace는 lint를 필수로 한다"*가 **아무도 의지할 수 없는 문장**이 된다. 확인하려면 모든 component 파일을 읽어야 한다|
|local overlay가 **검사 내용을 바꾸는 것**|한 기계의 결과가 CI와 다르면서 **양쪽 다 초록**일 수 있고, 그 차이가 CI가 읽지 않는 파일에 있다|

**올리는 것은 허용한다.** 자기를 root보다 엄격하게 다루겠다는 component는 정책 구멍이 아니다.

완화가 정말 필요하면 **root가 이름과 사유를 대서 허가한다** (SPEC-01 §3):

```toml
[checks.lint]
required = true

[checks.lint.exemptions.gui]
reason = "Qt moc output is not lintable; tracked in #123"
```

**사유는 필수다.** 사유 없는 예외는 조용한 완화에 단계를 하나 더한 것일 뿐이다.
예외는 이름 댄 component에만 적용되고, 완화된 검사는 그 사유를 계속 들고 다닌다.

### local overlay는 경로만 옮긴다

허용 목록은 전부 경로이거나 실행 파일이다. 필드별 정책 판단이 아니라 **규칙 하나** —
local overlay는 *무엇이 어디 있는지*를 조정하지 *무엇을 검사하는지*는 건드리지 않는다.

목록 밖의 키는 **무시하지 않고 이름을 대서 거절한다.** 무시하면 쓴 사람은 적용됐다고 믿는다.
와일드카드는 **키 한 칸**이지 임의 깊이가 아니다 — `builds.*.directory`는
`builds.native.directory`를 허용하고 `builds.a.b.directory`를 허용하지 않는다.

공식 CI는 overlay 없이 도는 것이 기본이다. 그건 호출자의 선택이라 이 모듈이 강제하지 않고
문서로 남긴다.

### policy digest는 표시 설정을 담지 않는다

인수 기준: *개인 표시 설정은 품질 policy digest를 바꾸지 않는다.*

digest는 **무엇이 검사되는지**만 덮는다 — checks와 component scope. workspace 이름, profile
레이블, local overlay가 옮길 수 있는 모든 경로는 **의도적으로 뺐다.** 빌드 디렉터리가 다른 두
기계는 **같은 정책을 돌리고 있다.** digest가 다르다고 말하면 모든 로컬 차이가 정책 차이처럼
보인다.

component를 root에 인라인으로 쓰든 child 파일로 쪼개든 digest는 같다.

## 아직 하지 않은 것

|항목|어디서|
|---|---|
|CLI 계층(`--python`/`--component`/`--profile`)|[#210](https://github.com/jihoon22-lee/ici/issues/210). `Layer.CLI` 자리는 비워 두었다|
|이 설정으로 실제 분석 실행|후속 WP. 지금은 **읽고 합성하는 데까지**다|
|기존 `ici.config` 로더 교체|[WP27 #225](https://github.com/jihoon22-lee/ici/issues/225). 이 PR들은 **옆에 두었을 뿐 건드리지 않았다**|

탐색(`--config`·ancestor 탐색·VCS 경계·nested workspace·unregistered child), `ici init`/preview,
그리고 XDG/`dev.toml`/`ICI_CONFIG` migration 보고서는 **PR C에서 끝났다** —
[config-usage.md](config-usage.md). 위 §1의 재현이 그 보고서가 내놓는 첫 항목이다.

`src/ici/config.py`는 `src/ici/config/__init__.py`가 됐다. 내용은 한 줄도 바뀌지 않았고
18개 `from ici.config import ...`도 그대로다. 아키텍처 문서가 next 경로에 지정한 `config/`
이름을 쓰기 위한 이동이며, **stable 로더의 동작은 이 PR의 범위가 아니다.**
