# 회귀 corpus 등록부

| | |
|---|---|
|상태|**PR A 완료**. 등록부와 계약 harness가 존재하고, 기존 fixture 17개가 전부 등록됐다. real seed 확충은 PR B.|
|근거 이슈|[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201) 작업 1·2·4|
|구현|[`tests/fixtures/manifest.toml`](../../../tests/fixtures/manifest.toml), [`tests/fixture_manifest.py`](../../../tests/fixture_manifest.py), [`tests/toolcontract.py`](../../../tests/toolcontract.py)|
|검증|[`tests/test_fixture_corpus.py`](../../../tests/test_fixture_corpus.py)|

## 왜 등록부가 필요했나 — 실제로 있던 결함

`examples/cpp-fixtures/cmake_project/CMakeLists.txt:8`이 `find_package(Qt6 REQUIRED)`를 부른다.
이 fixture를 쓰는 두 테스트는 이렇게 가드하고 있었다.

```python
_require("cmake", "ctest", "gcov")
```

**cmake가 설치돼 있다는 사실은 Qt6에 대해 아무것도 말해 주지 않는다.** 가드는 자기가 모르는
것을 skip할 수 없으므로, Qt6가 없는 기계에서 "빠진 게 없다"고 판단하고 테스트를 실행했고,
cmake가 configure 단계에서 실패했다. 정직한 답이 "여기서는 실행되지 않음"인 자리에
**실패가 보고됐다.**

대조군이 이 결함의 성격을 분명히 한다. `qmake_project`도 Qt를 쓰지만 같은 문제가 없다 —
**qmake는 Qt와 함께 배포되므로 프로그램을 찾은 것이 곧 라이브러리가 있다는 뜻이다.**
한쪽 툴체인은 이름으로 탐지되고 다른 쪽은 안 된다. 그래서 요구사항이 관례가 아니라
**데이터**여야 한다.

## 요구사항은 이름이 아니라 probe다

"Qt6가 cmake에서 쓸 수 있는가"는 Qt6라는 이름의 파일을 찾아서 답할 수 없다.

|probe|의미|
|---|---|
|`executable = "cmake"`|PATH에 그 프로그램 하나|
|`any_executable = ["qmake6", "qmake"]`|여러 철자 중 아무거나|
|`cmake_package = "Qt6"`|**cmake에게 `find_package`가 성공하는지 직접 묻는다**|

`cmake_package` probe는 임시 디렉터리에 두 줄짜리 프로젝트를 만들어 configure만 해 본다.
빌드하지 않고, 세션당 한 번만 수행한다. 이것이 유일하게 정직한 답이다.

## real-tool과 data를 가른다

|kind|뜻|
|---|---|
|`real-tool`|네이티브 툴체인을 실행한다. 없으면 **명시적으로 미실행**이며 릴리스 지원 근거에 포함하지 않는다|
|`data`|테스트가 읽는 파일. 외부 요구가 없어 항상 실행된다|

등록부 테스트가 이 구분을 강제한다 — `data`는 `requires`가 비어 있어야 하고,
`real-tool`은 최소 하나를 선언해야 한다.

## skip은 조용해선 안 된다

ici는 lint가 CI에서 한 번도 실제로 돌지 않은 채 여러 릴리스 동안 녹색 게이트를 냈다(C-6).
등록부가 CI에서 툴체인 부재를 조용한 skip으로 바꾸면 정확히 그 사고를 반복한다.

`ICI_REQUIRE_BUILD_ADAPTERS=1`이면 미충족 요구가 **실패**가 된다. ici 자신의 CI가 이 변수를
켠다. 등록부에 없는 id를 요구하면 skip이 아니라 `ManifestError`다 — 오타 하나로 테스트가
조용히 꺼지지 않는다.

## 계약 harness: 출력이 아니라 호출을 검사한다

엔진은 외부 프로세스를 부른다. **출력**에 대한 단언은 파서를 검사하지만, **호출**에 대한
단언은 계약을 검사한다 — ici가 의도한 argv를 의도한 디렉터리에서 넘겼는지, 환경이
보존됐는지, 그리고 도구를 세 번이 아니라 한 번 실행했는지.

기존 스텁은 `#!/bin/sh\nexit 0`이라 이 중 어느 것도 답하지 못한다. `toolcontract.ToolBox`가
만드는 가짜 도구는 호출마다 argv·cwd·env를 JSONL 한 줄로 기록한다.

두 가지가 의도적이다.

1. **shebang이 현재 테스트를 돌리는 인터프리터다.** PATH에서 `python3`를 찾는 recorder는
   기계의 PATH를 시험하게 되는데, 이 테스트들은 바로 **ici가 PATH에 무엇을 하는지**를
   주장하려고 존재한다.
2. **셸 초기화 파일을 읽지 않는다.** #201이 harness에 이를 요구하고, R01이 ici 자신에게
   금지한다. 프로파일이 필요한 harness는 그 규칙의 회귀를 탐지할 수 없다.

`only_call()`은 호출이 정확히 하나가 아니면 실패한다. "ici는 이걸 한 번 부른다"고 주장하는
테스트가 두 번째 호출에서 조용히 첫 번째만 읽지 않도록 이름을 따로 뒀다.

## 등록 현황

|구분|개수|
|---|---|
|C++ data fixture (결함 seed 5 + 정상 대조군 1)|6|
|C++ real-tool fixture|4|
|`ici.next` 결과/이벤트 문서|7|
|**합계**|**17**|

등록부 테스트가 디스크와 등록부를 양방향으로 대조하므로, 등록하지 않은 fixture를 추가하면
테스트가 실패한다.

## 다음 (PR B)

이슈 작업 3·5·6이 남았다 — real Python/qmake seed(root+2 component, 공유 qmake SUBDIRS,
C++ 산출물에 의존하는 Python 테스트), 구 경로 snapshot 수집과 normalization/비교 도구,
그리고 toy Quality Zoo 의존 대체 mapping. 비교 도구는 **미실행을 해결된 finding이나 PASS로
오인하지 않아야 한다**(인수 기준).
