# pyz·uv·pip 설치에서 native ici로 이전하기

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [📦 native 설치·복구](native-install.md) &bull; **🔁 pyz/uv/pip에서 이전**

v0.13.x까지의 ici는 Python 제품(`ici.pyz`, Python wheel)이었다. 그다음 공개판부터 ici는
Rust native 단일 제품이다([ADR-0008](design/ici-next/adr/0008-rust-native-replacement.md)).
이 문서는 기존 설치를 정리하고 같은 프로젝트를 native ici로 계속 검사하는 순서를 설명한다.

## 1. 무엇이 바뀌고 무엇이 유지되나

|항목|v0.13.x Python 제품|native ici|
|---|---|---|
|실행 파일|`ici.pyz`(Python 3.10+ 필요) 또는 wheel의 `ici` entrypoint|정적 실행 파일 `bin/ici`. ici 자체에는 Python이 필요 없음|
|설치|파일 복사, `uv tool install`, `pip install`|`install-native.sh`와 `ici setup`([설치 가이드](native-install.md))|
|Ruff/ty|프로젝트 환경이나 PATH의 도구|검증된 native Ruff/ty 동봉. 프로젝트에 같은 도구가 준비돼 있으면 그쪽을 존중|
|Python의 역할|ici 런타임이자 검사 대상|프로젝트를 검사하는 외부 도구(pytest, coverage, mypy, runtime 호환 검사)일 뿐|
|명령·설정|`verify`, 엔진별 명령, `next …`, `analyze`, `[ici]/[engines]`·`[workspace]`·`schema_version = 2`|같은 명령과 설정 형식을 그대로 읽음|
|결과 계약|`ici.result/v3`, `ici.next.run` v2, event v1, auxiliary v1|같은 계약. 새 결과는 `.ici/rust-v1/` namespace|
|Python 배포물|wheel/sdist/pyz 공개|더 이상 발행하지 않음. 과거 공개판은 보존|

기존 명령은 이름만 남은 alias가 아니라 같은 공통 분석·실행 경로의 projection이다. 기능별 동등성
근거와 의도한 차이는 [이전 대응표](design/rust-replacement/inventory.md)에 있다.

## 2. 설치

[native 설치 가이드 §3](native-install.md#3-설치)을 따른다. 설치는 기존 Python 설치를 지우거나
바꾸지 않는다. 두 제품이 한동안 함께 있을 수 있으므로 다음 절에서 어느 `ici`가 실행되는지 확인한다.

## 3. PATH 충돌 확인과 기존 설치 정리

```bash
command -v -a ici      # PATH 순서대로 모든 ici
ici --version          # native는 "ici X.Y.Z" (0.13.x가 아님)
ici doctor             # 다른 ici가 앞서 있으면 "path    shadowing ici: <경로>"
ici doctor --json | jq .executables
```

`ici doctor`는 PATH의 모든 `ici`를 찾는다. 지금 실행 중인 native 실행 파일보다 앞선 다른 파일은
`shadowing`, 뒤에 남은 설치는 `other`로 표시하고 `resolve:` 줄에 조치를 적는다. JSON의
`executables`에는 `running`, `path_ici`, `first_on_path`, `shadowed`, `conflicts`,
`legacy_ici_python`, `guidance`가 들어 있다. `ici setup --inspect`도 설치 직후 PATH 충돌을 알려준다.

기존 설치는 더 이상 필요 없을 때만 지운다.

|기존 설치|확인|정리|
|---|---|---|
|pyz 복사본|`~/.local/bin/ici`, 공유 `bin/ici`가 Python zipapp인지(`head -c 2` 가 `#!`)|파일 삭제 또는 `ici-0.13.0` 등으로 이름 변경|
|`uv tool install`|`uv tool list`|`uv tool uninstall ici`|
|venv의 `pip install`|`<venv>/bin/ici`|그 venv에서 `pip uninstall ici` 또는 venv 정리|
|프로젝트 개발 venv의 `.venv/bin/ici`|`command -v -a ici`에 `.venv/bin/ici`|venv를 activate하면 PATH 앞에 오므로 그 venv에서 제거|

정리 대신 공존시키려면 native `bin` 디렉터리를 PATH 맨 앞에 두고 `ici --version`으로 확인한다.

## 4. 절대경로를 저장한 작업 갱신

PATH를 고쳐도 경로를 직접 적어 둔 작업은 옛 실행 파일을 계속 부른다. 다음을 찾아 갱신한다.

```bash
grep -rn -e '\.venv/bin/ici' -e 'ici\.pyz' -e 'ICI_PYTHON' \
  Makefile justfile .github/ scripts/ 2>/dev/null
```

- idk task, Makefile, CI workflow, shell 스크립트에 `.venv/bin/ici verify`, `python ici.pyz …`,
  `dist/ici.pyz …`처럼 적힌 명령은 `ici …`(PATH) 또는 설치 경로의 entrypoint
  (`${XDG_DATA_HOME:-$HOME/.local/share}/ici/native/bin/ici …`)로 바꾼다. idk 쪽 task 정의 형식은
  idk 문서를 따른다. 명령 인자는 그대로 둔다.
- CI에서 release asset `ici.pyz`를 내려받던 단계는 native archive와 digest 확인으로 바꾼다
  ([CI 연동 가이드 §0](ci-integration.md#0-native-ici-설치-r9-cutover-이후)).
- `python3 ici.pyz`처럼 인터프리터로 실행하던 호출은 native 실행 파일에 맞지 않는다. 실행 파일을
  직접 호출한다.

## 5. `ICI_PYTHON`의 의미

- native core는 `ICI_PYTHON`으로 실행되지 않으며 core 런타임 선택에 쓰지 않는다.
- legacy schema(`[ici]`/`[engines]`) 명령은 v0.13.0 launcher 규칙과 같이 `ICI_PYTHON`을 **프로젝트
  인터프리터 후보**로 읽는다. 새 schema(`[workspace]`, `schema_version = 2`)는 읽지 않고,
  `ICI_PYTHON`이 설정돼 있으면 설정 설명에 그 사실을 limitation으로 남긴다.
- 프로젝트 Python은 설정으로 명시하는 것이 정확하다. 새 schema는 `python.executable`
  (component별 `components[].python.executable`), legacy schema는 `engines.test.python` 등
  엔진 설정을 사용한다. 프로젝트의 `.venv`도 후보다.
- `ici doctor`는 설정된 값을 `legacy  ICI_PYTHON=…` 줄로 보여 준다. 더 쓰지 않는다면 셸 시작 파일과
  CI 환경에서 지운다.

## 6. 종료 코드

|코드|의미|
|---|---|
|0|요청한 검사가 완료되었고 gate 통과|
|1|완료된 검사에서 gate 위반(FAIL)|
|2|`analyze`/`next` 명령의 요청·설정 오류, legacy 엔진 명령의 SKIP(둘 다 v0.13.0과 같음)|
|3|필수 증거가 불완전(INCOMPLETE): 도구 부재, 범위 누락, 실행 실패 등|
|130|취소(부분 결과 보존)|

**변경점**: legacy `ici verify`나 엔진 명령에서 필수 증거가 불완전하면 native는 **3**으로 끝난다.
v0.13.0은 같은 suite ERROR에 1을 반환했다. 화면과 legacy 결과의 ERROR 판정은 같고 0이 아닌 종료라는
점도 같다. 완료된 검사의 위반(FAIL), 테스트 파일 없음, coverage 정책 거부처럼 v0.13.0이 측정한
판정은 계속 1이다. `ici analyze`도 필요한 도구가 없으면 찾은 finding을 보존한 채 3으로 끝난다.

CI에서 `exit 1`만 실패로 다루던 스크립트는 0이 아닌 모든 코드를 실패로 다루고, 3은 "검사가 끝나지
않음(준비 필요)"으로 구분해 안내하도록 고친다. `--fail-on-new`는 미실행·불완전을 통과로 바꾸지 않는다.

## 7. 결과·cache·설정의 위치

- native의 새 결과와 cache는 프로젝트의 `.ici/rust-v1/` 아래에 쓴다(`ici analyze` 기본 결과
  `.ici/rust-v1/result.json`, HTML `.ici/rust-v1/result.html`). 저장소에는 `.ici/`를 `.gitignore`에 둔다.
- 이전 Python 제품의 cache와 결과, `ici.toml`/`pyproject.toml [tool.ici]`, baseline, suppression은
  자동으로 고치거나 새 형식으로 덮어쓰지 않는다. native는 이전 cache를 자기 cache로 읽지 않는다.
- 사용자 전역 `~/.config/ici/ici.toml`의 팀 정책은 새 schema에서 암묵적으로 합치지 않고
  limitation으로 알린다. 필요한 정책은 프로젝트 설정으로 옮긴다. 개인 선호는
  `$XDG_CONFIG_HOME/ici/preferences.toml`에 둔다.
- 설정 형식 변환은 미리보기와 새 출력으로만 한다(`ici next migrate` 등 기존 migration 명령).
  원본은 사용자가 명시할 때만 백업과 함께 바꾼다.

## 8. baseline 다시 만들기

pyz가 만든 baseline(`ici.result/v3` 등)은 native가 **읽을 수 있지만 그대로 비교할 수 있다는 뜻은
아니다**. fingerprint·도구·정책·scope identity가 비교 가능한 check만 new/unchanged를 판정하고, 그렇지
않은 항목은 incompatible이나 비교 제한으로 보고한다. 이전 baseline과 새 결과를 모두 보존하며,
요청한 교체 baseline을 그 상태로 쓰지 않는다. 사라진 항목을 증거 없이 resolved로 만들지 않는다.

비교 기준이 되는 commit(보통 `main`)에서 native로 새 baseline을 한 번 만든 뒤 비교에 쓴다.

```bash
# legacy verify 경로
git switch main
ici verify --write-baseline .ici-baseline/verify-baseline.json
git switch feature-branch
ici verify --baseline .ici-baseline/verify-baseline.json --fail-on-new

# analyze 경로 (ici.next.run 결과가 baseline)
git switch main
ici analyze --result .ici-baseline/analyze-baseline.json
git switch feature-branch
ici analyze --baseline .ici-baseline/analyze-baseline.json
```

baseline 파일 위치는 프로젝트 안이어야 한다. 새 baseline 도입은 변경 사유를 확인한 별도 작업에서
하고, 이전 baseline은 필요할 때까지 보관한다.

## 9. 문제가 있으면 되돌리기

native 설치본 사이의 되돌리기(`ici setup --rollback`)와 공개 v0.13.0으로의 명시 복귀는
[설치 가이드 §5](native-install.md#5-되돌리기)에 있다. 어느 쪽도 사용자의 새 결과나 설정을 옛
상태로 덮어쓰지 않는다.
