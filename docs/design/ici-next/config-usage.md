# next 설정 사용 안내: 만들기, 찾기, 옮기기

| | |
|---|---|
|상태|**next 경로 전용.** 현재 `ici verify`는 아직 이 파일을 읽지 않는다 — 연결은 [WP27](https://github.com/jihoon22-lee/ici/issues/225).|
|근거 이슈|[WP05 #203](https://github.com/jihoon22-lee/ici/issues/203) 작업 2·5·6·7|
|구현|[`discovery.py`](../../../src/ici/config/discovery.py), [`scaffold.py`](../../../src/ici/config/scaffold.py), [`migration.py`](../../../src/ici/config/migration.py)|
|검증|[`tests/test_config_discovery.py`](../../../tests/test_config_discovery.py), [`tests/test_config_migration.py`](../../../tests/test_config_migration.py)|
|설계 배경|[config-origin.md](config-origin.md)|

## 1. 만들기 — `init`은 제안하고, 바꾸지 않는다

`init`은 **읽기만 한다.** 저장소를 보고 설정 파일 내용을 만들어서 돌려주고, **쓰는 것은 별개
호출**이다. 이 분리가 "init은 아무것도 바꾸지 않는다"를 약속이 아니라 **확인 가능한 사실**로
만든다 — 읽는 쪽에는 쓸 방법이 아예 없다.

```python
proposal = propose(Path("."))
print(proposal.text)            # 쓸 내용
proposal.would_overwrite        # 기존 파일이 있으면 True
write(proposal, Path("ici.toml"))                  # 기존 파일이 있으면 거절
write(proposal, Path("ici.toml"), overwrite=True)  # 명시적으로 한 번 더
```

**하지 않는 것** (#203 작업 5): 자동 overwrite, 패키지 설치, source, qmake configure, 테스트 실행.

### 모르는 것은 모른다고 쓴다

빌드 파일이 **둘 이상**이면 `init`은 고르지 않는다.

```toml
# UNDECIDED: more than one build file was found, so no [builds] entry was written: a/a.pro, b/b.pro
# Add a [builds.<id>] entry naming the one you want.
```

SPEC-01 §6이 *"첫 `.pro`를 임의 선택하는 동작은 공식 검증에서 금지한다"*고 적은 이유가 이것이다.
디렉터리 순서로 정해진 빌드는 **아무도 결정하지 않은 빌드**이고, 거기서 나온 분석은 누군가
결정한 것과 **똑같이 생겼다.** 빌드 파일이 하나뿐이면 그건 선택이 아니라 유일한 후보라 적는다.

### 남의 도구 설정은 가리키기만 한다

`pyproject.toml`·`ruff.toml`·`mypy.ini`·`pytest.ini`가 있으면 **있다는 사실과 위치만** 적는다.
설정 값은 **한 줄도 옮기지 않는다** (SPEC-01 §7). 복사본은 **두 번째 진실**이 되고, 원본을
처음 고치는 순간부터 갈라진다.

## 2. 찾기 — 어디에 서 있든 같은 scope

하위 디렉터리에서 실행해도 **같은 workspace를 찾는다.** cwd가 검사 범위를 조용히 바꾸지 않는다
(SPEC-01 §6).

```
apps/gui/ 에서 ici 실행 → <repo>/ici.toml 을 찾는다
```

### 탐색이 하지 않는 네 가지

전부 **엉뚱한 것을 분석하게 되는 경로**를 막는다.

|하지 않는다|왜|
|---|---|
|`[component]` 파일을 workspace로 **승격**|승격하면 *"workspace가 통과했나"*가 **어느 디렉터리에 서 있었는지**에 달린다|
|root가 등록하지 않은 하위 `ici.toml`을 **병합**|파일이 디렉터리에 생겼다는 이유만으로 빌드에 조용히 합류하게 된다|
|중첩 workspace를 **흡수**|안쪽은 자기 것을 답하고, 바깥쪽은 자기 것만 센다|
|VCS root **위로** 탐색|홈 디렉터리나 여러 체크아웃의 공통 부모에 있는 `ici.toml`이 프로젝트를 **가로챌** 수 있다|

찾지 못하면 **무엇을 봤는지** 말한다. 등록되지 않은 component 파일을 지나쳤다면 그 사실을
따로 말한다 — *"여기 설정이 없다"*와 *"당신이 생각하는 그 설정이 어디에도 등록돼 있지 않다"*는
고치는 방법이 다른 문제다.

### `--config`로 명시하면 그대로 따른다

component 파일을 직접 지목하는 것도 허용한다. 파일 이름을 대는 것은 **어느 것을 말하는지에 대한
분명한 진술**이기 때문이다 — 추측해야 하는 탐색과 다르다.

단, 그 실행은 `scope.kind = standalone`으로 표시된다. SPEC-01 §2가 단일 component 통과를 상위
workspace 통과로 쓰는 것을 금지한다.

## 3. root 하나로 시작한다

폴더마다 `ici.toml`을 만들 필요가 없다. **root 하나가 기본**이고, 파일을 쪼개는 것은 **보관
위치만** 바꾼다 — 같은 effective config가 나온다. component를 자기 파일로 옮겨도 **빌드가 바뀌지
않는다**는 뜻이고, 테스트가 그것을 고정한다.

child 파일은 **자기 이름을 모른다.** id는 root의 등록 항목이 준다.

## 4. `--local-config`는 경로만 옮긴다

개인 빌드 디렉터리나 인터프리터를 가리키라고 있는 파일이다. **검사·규칙·기준·제외는 바꾸지
못한다.** 허용 목록 밖의 키는 무시하지 않고 **이름을 대서 거절한다** — 무시하면 쓴 사람은
적용됐다고 믿는다.

이 금지가 이 파일이 존재하는 이유다. 없으면 한 기계의 결과가 CI와 다르면서 **양쪽 다 초록**일 수
있고, 그 차이는 **CI가 읽지 않는 파일**에 있다. 공식 CI는 overlay 없이 도는 것이 기본이다.

## 5. 지금 쓰는 설정에서 옮기기

`migration.report()`는 **바꾸지 않고 설명한다.** 현행 로더가 읽는 파일들을 같은 순서로 읽고,
**둘 이상이 정한 키마다 이긴 쪽과 진 쪽을 같이** 댄다.

```
project ici.toml: /repo/ici.toml
dev.toml: /repo/dev.toml

These keys are set in more than one file. The stable loader keeps
the last one and records nothing about the others:
  engines.lint.required: dev.toml=False overrides project ici.toml=True
```

이 목록이 곧 migration이다. **사용자가 놀랄 값의 집합**이고, 현행 로더의 출력으로는 **얻을 수
없다** — `_deep_merge` 뒤에는 누가 이겼는지가 남지 않기 때문이다.

`policy_is_contested`는 그중 **게이트 판정을 바꿀 수 있는** 것(`required`/`enabled`/`mode`/
`threshold`)이 있는지 따로 말한다. 빌드 디렉터리가 기계마다 다른 것은 편의지만, `required`가
기계마다 다른 것은 SPEC-01 §3이 이름 댄 결함이다.

**변환은 하지 않는다.** 변환은 별개 결정이고, 보고서 안에서 하면 #203 작업 7이 금지한
*"새 경로에 조용히 섞기"*가 된다.

## 6. 이 단계가 하지 않는 것

|항목|어디서|
|---|---|
|`ici init`/`plan`/`verify` CLI 명령 연결|[#210](https://github.com/jihoon22-lee/ici/issues/210)|
|CLI 계층(`--python`/`--component`/`--profile`)의 우선순위 적용|[#210](https://github.com/jihoon22-lee/ici/issues/210). `Layer.CLI` 자리는 비어 있다|
|이 설정으로 실제 분석 실행|후속 WP. 지금은 **읽고 합성하는 데까지**다|
|기존 `ici.config` 로더 교체|[WP27 #225](https://github.com/jihoon22-lee/ici/issues/225). 두 경로는 **나란히 있을 뿐 섞이지 않는다**|
