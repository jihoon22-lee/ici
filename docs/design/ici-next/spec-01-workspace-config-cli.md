# SPEC-01 — 프로젝트 연결과 사용자 인터페이스

| | |
|---|---|
|상태|**채택된 목표 계약 (adopted target contract)**|
|원문 이슈|[SPEC-01 #193](https://github.com/jihoon22-lee/ici/issues/193)|
|상위|[roadmap.md](roadmap.md) / 아키텍처 [architecture.md](architecture.md)|
|요구사항|R01, R04~R07, R10, R14|
|주 담당 WP|[#203](https://github.com/jihoon22-lee/ici/issues/203) (TOML), [#207](https://github.com/jihoon22-lee/ici/issues/207) (workspace), [#210](https://github.com/jihoon22-lee/ici/issues/210) (CLI)|

> **이 문서의 TOML/CLI는 ici-next 목표 계약이며 현행 v0.11 명령 지원을 주장하지 않는다.**
> 구현 전 WP의 executable examples와 JSON Schema로 확정한다.
> 현행 CLI·설정과의 차이는 [inventory/execution-flow.md §1·§3](inventory/execution-flow.md)에 있다.

## 1. 논리 모델

- Workspace: 전체 정책과 등록 component를 소유한다.
- Component: 독립 분석/테스트 범위. 디렉터리와 1:1이 아니며 동일 폴더의 C++/Python 혼합과
  공통 소스를 허용한다.
- BuildUnit: qmake/CMake/명시 빌드 구성·variant·산출물을 소유한다. 여러 component가 참조 가능하다.
- AnalysisUnit: component×language×variant/runtime×source scope. source 파일만을 키로 삼아 서로
  다른 컴파일 조건을 합치지 않는다.
- component 의존성과 task 의존성을 구분한다. Python 테스트가 특정 C++ 산출물을 요구해도 모든
  C++ 분석 check에 의존하는 것은 아니다.

## 2. 설정 발견과 파일 분리

1. 명시적 `--config PATH`가 있으면 그 파일을 시작점으로 한다.
2. 없으면 cwd에서 상위로 탐색하되 VCS root 또는 명시 workspace 경계를 넘지 않는다.
   `[workspace]`가 있는 가장 가까운 `ici.toml`을 찾는다.
3. `[component]` 파일을 발견해도 자동으로 독립 workspace로 바꾸지 않는다. 상위 workspace에
   명시적으로 등록됐는지 확인한다. 등록되지 않았으면 진단한다.
4. root 등록에 없는 하위 `ici.toml`을 재귀 병합하지 않는다. 중첩 workspace는 자동 편입하지 않는다.
5. 단독 component 파일을 `--config`로 명시하면 단일-component 독립 실행을 허용하되
   `scope.kind=standalone`으로 기록한다. 상위 workspace 전체 통과로 표시할 수 없다.
6. root file의 inline component와 `config=...` 참조 중 하나만 선택한다. 참조 항목에는
   `id`/`config`만 허용하고 양쪽 중복 정의는 오류로 처리한다.

root 하나가 기본이다. 각 폴더에 `ici.toml`을 만드는 것은 필수가 아니다. 파일 분리는 보관 위치만
바꾸며 동일한 effective config를 생성해야 한다.

## 3. 설정 계층과 경로 규칙

- 순서: built-in defaults → root 공통 기본값 → component의 차이 → 명시 local path overlay →
  CLI의 실행 범위/운영 옵션.
- 품질 정책은 root가 소유한다. component는 기본값을 조정할 수 있지만 root의 필수 검사·강제
  하한을 낮추지 못한다. 완화가 필요하면 root에서 해당 component의 예외와 사유를 명시한다.
- 개인 XDG 설정은 표시/편집기 기본값만 허용한다. 품질 기준·엔진 선택에 암묵적으로 섞이지 않는다.
- `--local-config PATH`는 선택적이며 실행 파일·build/output/cache 경로 등 allowlist 필드만
  조정한다. 검사/규칙/기준/제외 변경은 거부한다. 공식 CI는 기본적으로 local overlay 없이
  실행한다.
- 일반 path-valued 필드(root, config, build project/directory, tool config/path)는 **선언된 파일
  디렉터리 기준**이다. `sources`/`include`/`exclude`/`test_paths`의 source glob은 **해당
  component root 기준**이다. 이 둘을 schema에서 서로 다른 타입으로 표현한다.
- bare executable `python`/`qmake`는 실행 PATH 검색 대상이다. `/`가 들어간 실행 경로는 선언 파일
  기준으로 정규화한다. 실행 cwd가 달라도 선택 결과가 바뀌지 않아야 한다.
- `${env:NAME}`은 명시된 경로값에만 제한적으로 허용한다. 누락 변수는 진단하고 셸 expansion/
  command substitution은 제공하지 않는다.
- unknown key·중복 id·dangling build/component reference·cycle·허용되지 않은 scope 밖 쓰기는
  오류다. source의 외부 헤더 읽기는 별도 declared external input으로 허용하고 쓰기 권한과
  분리한다.
- 각 최종 값의 source file/key와 effective policy digest를 유지한다. 진단 출력은 민감 경로를
  줄일 수 있지만 실제 실행 입력과 혼동하지 않는다.

> **현행 충돌 (측정됨)**: 지금은 XDG 전역 파일과 `dev.toml`이 품질 정책 전체를 덮을 수 있다
> (`src/ici/config.py:233` `load_config`). 이는 §3의 "개인 설정이 품질 기준에 암묵적으로
> 섞이지 않는다"와 직접 충돌하므로, [WP05 #203](https://github.com/jihoon22-lee/ici/issues/203)이
> migration 보고서에서 영향을 드러내야 한다. 또한 각 값의 출처가 `_deep_merge` 후 사라진다.

## 4. 목표 설정 예시

```toml
schema_version = 1

[workspace]
name = "product"
profile = "standard"

[checks.line]
enabled = true
required = false

[checks.lint]
enabled = true
required = true

[tools.ruff]
source = "bundle"

[tools.mypy]
source = "bundle"

[builds.native]
system = "qmake"
project = "product.pro"
directory = "build/ici-default"
variant = "default"
prepare = "explicit"

[[components]]
id = "gui"
root = "apps/gui"
languages = ["cpp"]
build = "native"
sources = ["**/*.cpp", "**/*.h"]

[components.cpp]
qt = true

[[components]]
id = "core"
root = "libs/core"
languages = ["cpp"]
build = "native"

[[components]]
id = "tool-a"
root = "python/tool-a"
languages = ["python"]

[components.python]
executable = "python"
type_provider = "mypy"
test_tools = "project"
test_paths = ["tests"]

[[components]]
id = "tool-b"
config = "python/tool-b/ici.toml"
```

하위 파일 예시:

```toml
schema_version = 1
[component]
root = "."
languages = ["python"]
[python]
executable = ".venv/bin/python"
test_tools = "project"
test_paths = ["tests"]
```

`prepare = "explicit"`는 자동 qmake 실행을 허락한다는 뜻이 아니다. 구성된 prepare argv와
출력/자원 계약을 [spec-02](spec-02-distribution-execution.md)에 맞게 제공해야 한다. 기존 유효
compile DB가 있으면 이를 읽는 경로를 우선 사용한다. 이 예시는 전체 test/build 명령을 생략한
구조 예시다.

이 두 예시는 [`tests/test_ici_next_inventory.py`](../../../tests/test_ici_next_inventory.py)가
TOML로 파싱 가능한지 기계적으로 검증한다. **파싱 가능성만 검증하며, 필드 의미의 구현은
[WP05 #203](https://github.com/jihoon22-lee/ici/issues/203)이 JSON Schema와 함께 확정한다.**

## 5. CLI 계약

|명령/옵션|동작|
|---|---|
|`ici init [--python/--cpp]`|구조 후보를 읽고 기본 설정 작성. 설치/source/build/test 없음. 기존 파일 덮어쓰기 기본 금지, preview 제공|
|`ici doctor`|선택 scope의 도구·버전·필수 입력과 해결할 config key를 제시. probe만 수행|
|`ici plan`|선택/전체 범위, provider 작업, prepare, deps, 비용 분류, known blocker 표시. 빌드 실행 없음|
|`ici verify`|root의 기본 profile/범위로 검증|
|`--python`, `--cpp`|구성요소 모델을 유지하고 해당 언어 검사만 필터링. 둘 다 주면 union|
|`--component ID`|반복 지정 가능, component 집합을 필터링. 언어 옵션과는 intersection|
|`--profile fast/standard/deep`|[spec-03](spec-03-analysis-engines.md) 비용/검사 selection. 설정된 root 정책을 지우지 않음|
|`--require-full`|전체 root 필수 범위 충족이 아니면 최종 gate를 INCOMPLETE로 처리. 공식 CI 사용|
|`--config`, `--local-config`|명시적 설정 선택. local은 경로 allowlist만|
|`--json`, `--events PATH`, `--output PATH`|기계 출력/이벤트/산출물 경로. 로그와 JSON을 섞지 않음|
|`ici report RESULT`|저장된 JSON으로 HTML/SARIF 재생성. 도구 실행 없음|
|`ici publish RESULT`|별도 인증/게시 계약. 분석 재실행 없음|

선택 결과가 0 component/language/check면 오류로 알린다. 실수로 아무것도 검사하지 않고 exit 0을
반환하지 않는다. 언어별 단축 옵션은 검사 범위 필터이며 프로젝트 종류를 강제 재판별하지 않는다.

> **현행 차이 (측정됨)**: `init`·`plan`·`report` 명령이 없고, `--python`/`--cpp`/`--component`/
> `--require-full`/`--events`/`--config`/`--local-config` 옵션이 없다. `--profile`은 있다.
> 엔진 단독 서브커맨드 15개가 있는데 목표 CLI에는 대응 항목이 없으므로, 이들의 alias·
> deprecation을 [WP27 #225](https://github.com/jihoon22-lee/ici/issues/225) migration 표에서
> 결정한다. → [inventory/current-engines.md §3](inventory/current-engines.md)

## 6. 부분 실행과 실행 위치

- `--python` 성공은 `selected scope PASS`이지 workspace PASS가 아니다. 결과에
  requested/selected/required/omitted scope를 보존한다.
- Python 테스트에 C++ 산출물이 필요한 경우 prerequisite prepare만 포함한다. 무관한 C++
  lint/type/sanitizer는 선택하지 않는다.
- fast가 금지한 mutable prepare가 필요하면 실행하지 않고 blocked 상태를 반환한다.
- workspace 안의 하위 폴더에서 실행해도 cwd만으로 검사 범위를 조용히 바꾸지 않는다. 선택한
  root와 scope를 출력하며 부분 실행은 옵션으로 명시한다.
- build dir/source mapping이 모호하면 후보와 config key를 제시한다. 첫 `.pro`를 임의 선택하는
  동작은 공식 검증에서 금지한다.

## 7. 기존 도구 설정

Ruff/pytest/mypy/ty 설정의 자체 탐색·상속·cwd 의미를 provider별로 보존한다. root `ici.toml`이
있다고 기존 `pyproject.toml`/`ruff.toml`/pytest 설정을 자동 flatten하지 않는다. 명시 tool config를
주면 그 영향과 출처를 기록한다. 관련 tool 설정 파일과 실제 선택 결과를 digest에 포함한다. tool
사용자 전역 설정이 결과에 영향을 주는 경우 공식 모드에서는 지원되는 격리 옵션 또는 명시 config를
적용하고, 지원되지 않으면 재현성 한계를 표시한다.

## 8. 오류·테스트·이전

- CLI/config 구조 오류는 [spec-04](spec-04-results-integration.md) exit 2. 실행 중 필수 입력/도구
  미완료는 exit 3. 없는 테스트를 정상 통과로 처리하지 않는다.
- old schema는 명시 migration preview로 변환한다. 원본을 보존하고 자동 실행 중 덮어쓰지 않는다.
  old XDG/`dev.toml`/`ICI_CONFIG` 영향은 변환 보고서에서 드러낸다.
- 필수 계약 테스트: root/child inline 동등성, 서로 다른 cwd, nested workspace, duplicate id,
  undefined env variable, symlink/external read/write 구분, unknown field, 정책 완화 거부,
  CLI union/intersection, empty selection, partial + require-full, Python→C++ prerequisite.

> **현행 종료 코드 충돌 (측정됨)**: 지금 exit 2는 "설정 오류"와 "엔진 SKIP" 두 의미를 겸하고
> 있고 exit 3·130은 없다. 자세한 대조표는
> [inventory/execution-flow.md §5](inventory/execution-flow.md)에 있다.

## 완료 조건

- [ ] 모든 예시가 실제 parser/schema 테스트로 검증된다.
      (현재: TOML 파싱 가능성만 검증됨. schema 검증은 WP05)
- [ ] precedence/origin/path/glob 규칙이 문서·구현·테스트에서 동일하다.
- [ ] init/plan/doctor가 빌드·설치·source하지 않는 회귀 테스트가 있다.
- [ ] legacy config migration과 user-facing 오류 코드/메시지 예시가 있다.

참고: [Ruff 설정](https://docs.astral.sh/ruff/configuration/),
[pytest 설정](https://docs.pytest.org/en/stable/reference/customize.html).
실제 provider별 버전 옵션은 고정 tool 계약 테스트에서 검증한다.
