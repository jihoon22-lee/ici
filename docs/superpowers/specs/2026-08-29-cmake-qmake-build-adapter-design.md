# CMake/CTest · qmake/Make 빌드 어댑터 설계

- 상태: 설계 승인됨, 구현 전
- 대상 릴리스: v0.6.0
- 확장하는 문서: [`docs/design/ci-validation-roadmap.md`](../../design/ci-validation-roadmap.md) §5.2
- 관련 결함: `toy-projects/ICI-GAPS.md` 의 A-2, A-3

## 1. 목적

`ici` 가 프로젝트의 실제 빌드 시스템(CMake, qmake)으로 configure·build·test 를 수행하게
한다. 지금은 모든 C++ 파일을 하나의 `g++` 명령으로 직접 컴파일·링크하며, 그 결과 두 가지가
불가능하다.

- **`Q_OBJECT` 클래스의 단위 테스트** — moc 실행 단계가 없어 vtable 이 미해결로 남는다
- **CMake/qmake 프로젝트의 `ici build`** — 루트에 빌드 디스크립터가 있으면 거부한다

이 문서는 그 둘을 함께 해소하는 어댑터의 설계를 정의한다.

## 2. 왜 지금인가

`ci-validation-roadmap.md` §7 은 어댑터 스펙의 착수 조건을 "실물 Qt 프로젝트에서 A-2·A-3 이
재현될 것" 으로 걸어두었다. 근거는 축 A 이후 발견한 결함 17건이 **전부 만들다 나왔고 코드를
읽어서 찾은 것은 하나도 없다**는 관찰이었다.

착수 조건을 문서 작성 전의 별도 단계로 두는 대신, **측정과 구현을 같은 루프에서 수행한다.**
먼저 실패하는 진짜 테스트를 쓰고, 그 테스트가 어댑터 구현을 끌고 간다. 요구사항이 실측에서
나온다는 §7 의 의도는 그대로 지켜지며, 달라지는 것은 그 빨간 상태를 커밋으로 남기지 않는다는
점뿐이다.

실측 대상은 세 프로젝트다. 하나는 이 저장소 안에 있다.

| 프로젝트 | 빌드 경로 | 이 문서에서 검증하는 것 |
|---|---|---|
| `toy-projects/loglens` | CMake/CTest | moc, Qt Test 링크, CTest 결과 계약 |
| `toy-projects/diskmap` | qmake/Make | moc, `make check` 결과 계약 |
| `ici/viewer` | 기존 g++ | 어댑터가 기존 경로를 깨뜨리지 않았다는 회귀 증거 |

세 경로가 각각 실물 프로젝트 하나씩으로 덮인다. 어느 경로도 픽스처만으로 검증되지 않는다.

## 3. 설계

### 3.1 백엔드 프로토콜

`src/ici/core/cmake.py` 에 백엔드 프로토콜과 두 구현을 둔다. 위치는 `runner.py`·`toolchain.py`
와 같은 성격(외부 도구 호출 래퍼)이고, `project.py` 처럼 여러 엔진이 함께 import 한다.

```
BuildBackend (protocol)        CMakeBackend            QMakeBackend
  configure(root, shadow)  →   cmake -S -B             qmake6 (shadow cwd)
  build(session)           →   cmake --build           make
  run_tests(session)       →   ctest                   make check
```

`build` 와 `test` 두 엔진이 이 모듈을 공유한다. 엔진별로 각자 구현하지 않는 이유는 두 엔진이
모두 configure 를 필요로 하기 때문이다. 각자 돌리면 같은 shadow 디렉터리에 두 번 configure
하거나 서로 다른 플래그로 갈라진다. 엔진마다 규칙이 다른 문제는 이 저장소가 B-1(`cycle` 만
`source_dirs` 무시)과 C-9(엔진마다 다른 테스트 CWD)에서 이미 두 번 겪었다. 새 빌드 경로는
처음부터 한 곳에 모은다.

### 3.2 백엔드 선택

프로젝트 루트의 빌드 디스크립터로 정한다.

| 루트에 있는 것 | 결과 |
|---|---|
| `CMakeLists.txt` | CMake 백엔드 |
| `*.pro` | qmake 백엔드 |
| 둘 다 | CMake 백엔드 |
| `Makefile` 만 | 어댑터 없음 — 거부 유지 |
| 없음 | 기존 g++ 경로 |

지금 A-2 가 **거부하는 바로 그 조건**을 어댑터 진입 조건으로 뒤집는다. 결함 수정 지점과 기능
진입 지점이 같아진다.

검사 범위는 루트 한 단계다. 하위 디렉터리는 보지 않는다. 이 규칙 덕분에 아직 전환하지 않은
프로젝트(`diskmap` 전환 전, `ici/viewer`)는 `src/gui/CMakeLists.txt` 를 갖고 있어도 동작이
바뀌지 않는다.

**어느 백엔드를 왜 골랐는지는 `ToolEvidence` 에 남긴다.** 조용히 정하면 나중에 "이 빌드가 왜
이렇게 돌았나" 를 추적할 수 없다. 손으로 쓴 `Makefile` 을 거부할 때의 메시지도 고친다 — 현재
문구("C++ build descriptor requires an adapter")는 어댑터가 하나도 없던 시절의 문장이라,
CMake·qmake 어댑터가 생긴 뒤에는 어느 어댑터가 없어서인지를 말해야 한다.

### 3.3 명령

모든 명령은 argv 배열이며 shell 을 거치지 않는다. shadow 빌드 디렉터리는 `build/ici-cmake`
(qmake 는 `build/ici-qmake`)로, 두 저장소 모두 `build/` 를 이미 gitignore 한다.

**CMake**

```
cmake -S <root> -B <shadow>
      -DCMAKE_BUILD_TYPE=Debug
      -DCMAKE_CXX_FLAGS=--coverage
      -DCMAKE_EXE_LINKER_FLAGS=--coverage
cmake --build <shadow> --parallel
ctest --test-dir <shadow> --output-on-failure --output-junit <shadow>/ici-ctest.xml
```

**qmake** (shadow 디렉터리를 cwd 로)

```
qmake6 <root>/<name>.pro QMAKE_CXXFLAGS+=--coverage QMAKE_LFLAGS+=--coverage
make --jobs=<N>
make check TESTARGS=-xunitxml
```

`CMAKE_BUILD_TYPE=Debug` 는 `-O0 -g` 를 주기 위한 것이다. 최적화가 켜지면 gcov 의 라인·브랜치
매핑이 뭉개져 커버리지 수치를 신뢰할 수 없다.

`CMAKE_CXX_FLAGS` 는 타깃별 플래그에 **덧붙는** 자리이므로 프로젝트 설정을 덮어쓰지 않는다.

`CMAKE_EXPORT_COMPILE_COMMANDS` 는 넣지 않는다. §5.3(Compile Commands 검증)이 이번 범위 밖이라
소비하는 곳이 없다.

### 3.4 커버리지

**ici 가 `--coverage` 를 주입한다.** 프로젝트가 커버리지 빌드를 선언하도록 요구하지 않는다.
설정을 빠뜨리면 측정이 조용히 사라지는데, TEM 점수가 그 측정 위에 서 있어 게이트의 근거가
없어진다.

수집은 두 백엔드가 공유한다. 둘 다 shadow 트리에 `.gcno`/`.gcda` 를 남긴다.

1. 테스트 실행 **이후에** 수행한다 — `.gcda` 는 바이너리가 실행돼야 생긴다
2. shadow 디렉터리에서 `.gcno` 를 재귀로 모아 부모 디렉터리별로 묶는다
3. 묶음마다 `gcov -b -p -o <objdir> <gcno...>` 를 돌리되, **cwd 를 `<shadow>/ici-gcov` 하나로
   고정한다**

gcov 는 `.gcov` 를 자기 cwd 에 쓰므로 출력이 평면으로 쌓이고, `-p` 가 경로를 파일명에 보존해
이름 충돌을 막는다. 덕분에 `coverage_support.py` 의 `parse_gcov_dir` 은 손대지 않는다 — 현재
구현은 `cov_dir.glob("*.gcov")` 로 평면 디렉터리만 본다.

커버리지 대상 소스 집합은 어댑터 경로에서 넓어진다. 자세한 것은 §3.7 을 본다.

### 3.5 테스트 결과와 `InspectionTarget`

CTest 는 `--output-junit`(CMake 3.21+)으로 JUnit XML 을 낸다. stdlib `xml.etree` 로 읽는다.
로드맵이 RHEL 7.9 를 실행 환경으로 잡고 있어 오래된 cmake 를 무시할 수 없으므로, §3.3 의 CTest
명령은 버전에 따라 두 단계로 내려간다.

| cmake 버전 | 동작 |
|---|---|
| 3.21 이상 | `--test-dir` + `--output-junit`, XML 파싱 |
| 3.20 | `--test-dir` 는 쓰되 XML 없이 stdout 파싱 |
| 3.20 미만 | shadow 디렉터리를 cwd 로 `ctest` 실행, stdout 파싱 |

`--test-dir` 는 CMake 3.20 에 추가됐고 `--output-junit` 은 3.21 에 추가됐다. 실제로 사용한
경로는 `ToolEvidence` 로 남겨, 리포트만 보고도 결과가 XML 에서 왔는지 stdout 에서 왔는지 알 수
있게 한다.

qmake 쪽은 CTest 에 해당하는 것이 없다. `CONFIG += testcase` 가 만드는 `make check` 가
대응물이며, QtTest 바이너리에 `-xunitxml` 을 넘겨 XML 을 받는다. `TEMPLATE = subdirs` 에서는
여러 테스트의 출력이 이어 붙으므로 `<testsuite>` 단위로 쪼개 읽는다.

> **이 절이 이 설계에서 가장 덜 확정된 부분이다.** qmake 의 테스트 결과 계약은 diskmap 을 실제로
> 전환해 봐야 정해진다. §2 의 원칙대로, 여기서 더 정밀하게 적는 것은 실측이 아니라 추측이 된다.
> 구현 중 달라지면 이 절을 고친다.

**파일 경로 문제.** 설계 원칙 1 은 모든 `InspectionTarget` 에 파일 경로를 요구하는데, CTest 와
QtTest 는 테스트 **이름**만 주고 소스 파일을 주지 않는다. `tests/` 아래에서 이름과 stem 이
일치하는 `.cpp` 를 찾아 붙이고, 못 찾으면 빌드 디스크립터를 가리킨다.

**테스트 CWD.** `add_test` 에 `WORKING_DIRECTORY` 로 프로젝트 루트를 명시한다. C-9 에서 고쳐
놓은 "테스트 바이너리는 항상 프로젝트 루트에서 돈다" 는 계약을 어댑터 경로에서 다시 깨뜨리지
않기 위한 것이다.

### 3.6 결과·증거 계약

§3.2 의 계약을 그대로 따른다.

| 상황 | 결과 | 증거 |
|---|---|---|
| configure 실패 | `ERROR` | 명령·종료 코드·stderr |
| build 실패 | `FAIL` | 〃 |
| 테스트 실패 | `FAIL` | 테스트 이름과 출력 |
| cmake/qmake 부재 | `ERROR` | — |
| 커버리지 미측정 | `ESTIMATED` (게이트 차단) | 미측정 사유 |

빌드 도구 부재는 `NOT_APPLICABLE` 이 **아니다.** 그 상태는 "이 엔진이 이 프로젝트에 적용되지
않는다" 가 아니라 "대상이 있었는데 측정하지 못했다" 이므로 게이트를 막아야 한다.

### 3.7 설정 계약의 변화

**`project.cpp_external_build_dirs` 는 어댑터 경로에서 무시된다.** 이 설정은 "moc 가 필요해
ici 가 직접 빌드할 수 없는 소스를 링크 대상에서만 뺀다" 는 뜻이었고, 어댑터가 생기면 그 전제가
사라진다. CMake·qmake 가 전부 빌드하므로 gui 소스도 링크되고 커버리지 대상에 포함된다.

이 설정은 g++ 경로에서는 계속 유효하며, 다른 엔진(`lint`·`complexity`·`dup` 등)에는 애초에
영향이 없었다. **엔진마다 스코프 규칙이 다른 것은 B-1 에서 이미 문제가 된 적이 있으므로**,
어느 경로에서 이 설정이 적용되는지를 `user-guide.md` 와 리포트 양쪽에 명시한다.

**`-std=c++17` 고정이 사라진다.** 표준을 프로젝트의 빌드 정의가 정한다. A-3 의 세 번째 증상
(C++20/23 프로젝트 검증 불가)이 함께 해소된다.

**`project.cpp_pkg_config` 는 그대로 둔다.** 어댑터 경로에서 컴파일 플래그는 빌드 시스템이
주지만, `lint` 는 여전히 `-fsyntax-only` 로 Qt 헤더를 직접 파싱해야 한다.

## 4. 검증 대상 프로젝트

### 4.1 `loglens` → CMake

**구조.** 현재 `src/gui/CMakeLists.txt` 가 `loglens_core` 를 정의하고 있어 루트와 타깃 이름이
충돌한다. 역할을 나눈다.

- 루트 `CMakeLists.txt` — `loglens_core`(정적 라이브러리), `loglens` CLI,
  `add_subdirectory(src/gui)`, `enable_testing()`, 테스트 타깃
- `src/gui/CMakeLists.txt` — `loglens_gui` **라이브러리**(`log_model`·`main_window`·
  `timeline_widget`)와 `loglens-gui` 실행 파일

**GUI 를 라이브러리로 빼는 것이 핵심이다.** 지금은 실행 파일 하나뿐이라 테스트가 `LogModel` 에
링크할 대상이 없다. `CMAKE_AUTOMOC ON` 이 moc 를 처리한다.

**기능 — 라이브 팔로우.** `loglens` 인데 `tail -f` 가 없다. `FileTailer` 는 core 에 있지만
`main_window.cpp:94-97` 이 지역 변수로 만들어 한 번 `poll()` 하고 버린다.

- `QTimer` 가 `FileTailer::poll()` 을 주기적으로 돌린다
- 새 레코드 중 필터를 통과한 것만 `beginInsertRows` 구간으로 묶여 모델에 들어간다
- 자동 스크롤은 사용자가 위로 스크롤하면 멈추고, 맨 아래로 돌아오면 재개한다
- `FileTailer` 가 restart 를 보고하면 모델을 리셋한다

**테스트 — `tests/test_log_model.cpp`.** `QAbstractItemModelTester` 를 모델에 붙인 채 팔로우
시나리오를 돌린다. 시그널 계약(`beginInsertRows`/`endInsertRows` 짝, 삽입 중 `rowCount` 일관성)
검증은 순수 함수로 뽑을 수 없어 core 로 우회할 방법이 없다. **이것이 이 테스트가 moc 를
요구하는 정직한 이유다** — 측정을 만들기 위해 억지로 모델에 남긴 로직이 아니다.

### 4.2 `diskmap` → qmake

**구조.** 루트 `diskmap.pro` 를 `TEMPLATE = subdirs` 로 두고 `src/core.pro`(라이브러리),
`src/gui/gui.pro`(앱), `tests/tests.pro`(`CONFIG += testcase`)로 나눈다. GUI 를 라이브러리로
분리해야 테스트가 링크할 수 있는 것은 loglens 와 같다.

**테스트.** `diskmap` 에는 모델이 없고 `treemap_widget.hpp`·`main_window.hpp` 가 `Q_OBJECT` 인
커스텀 페인팅 위젯이다. `QAbstractItemModelTester` 대신 **`QSignalSpy`** 로 히트 테스트와 선택
시그널을 검증한다. Qt Test 링크와 moc 를 동일하게 요구하므로 qmake 쪽 실측 재료로 충분하다.

### 4.3 `ici/viewer` → 기존 g++ 유지

전환하지 않는다. 어댑터가 기존 경로를 깨뜨리지 않았다는 회귀 증거가 필요하고, `viewer` 는 이
저장소 안에 있어 ici CI 가 매 PR 마다 돌린다.

### 4.4 ici 픽스처

`examples/cpp-fixtures/` 아래에 `cmake_project/`, `qmake_project/` 를 추가한다. 각각 `Q_OBJECT`
클래스 하나와 그것을 링크하는 테스트 하나를 담는다 — moc 가 실제로 필요해야 픽스처가 의미를
갖는다.

픽스처는 실물 프로젝트를 **대체하지 않는다.** ici 단위 테스트가 외부 저장소 없이 돌게 하는 것이
목적이며, 어댑터의 실측 근거는 §4.1·§4.2 의 두 프로젝트다.

## 5. 테스트 전략

두 층으로 나눈다.

- **순수 로직** — argv 구성, 백엔드 선택, JUnit/QtTest XML 파싱, gcov 묶음 계산.
  외부 도구 없이 항상 실행한다.
- **실제 빌드 E2E** — `examples/cpp-fixtures/` 의 두 픽스처를 실제로 configure·build·test 한다.
  `shutil.which` 로 도구가 있을 때만 실행한다. ici 는 RHEL 7.9 까지 지원 대상이라 cmake/qmake 가
  없는 환경에서도 스위트가 통과해야 한다.

**skip 이 조용해서는 안 된다.** 이 저장소는 C-6 에서 "CI 에서 `lint` 가 한 번도 실행된 적 없는데
게이트는 초록불" 이었던 적이 있다. 어댑터 E2E 가 CI 에서 조용히 skip 되면 어댑터는 검증된 적 없이
릴리스된다. `ICI_REQUIRE_BUILD_ADAPTERS=1` 을 두고 ici CI 에서 켜서, **도구가 없으면 skip 이
아니라 실패**하게 한다.

## 6. CI 변경

- **ici `ci.yml`** — `qmake6` 패키지 추가(`qt6-base-dev` 와 별도 패키지다). cmake 는 GitHub
  러너에 기본 제공된다. 어댑터 E2E job 에 `ICI_REQUIRE_BUILD_ADAPTERS=1` 설정
- **toy-projects `ci.yml`** — `ICI_VERSION` 을 어댑터가 들어간 릴리스로 상향,
  `qt6-base-dev` 와 `qmake6` 설치 추가

toy-projects CI 는 릴리스 에셋을 체크섬 검증해 내려받으므로(소스 빌드가 아니다), **ici 릴리스가
선행해야 toy-projects 의 Qt 테스트가 초록불이 된다.** 작업 순서가 이것으로 강제된다.

## 7. 완료 정의

- `loglens` 가 루트 CMakeLists 로 검증되고, `tests/test_log_model.cpp` 가 `ici verify` 안에서
  **통과한다** — 별도 디렉터리로 숨기거나 게이트를 우회하지 않는다
- `diskmap` 이 루트 `.pro` 로 검증되고, `QSignalSpy` 테스트가 같은 조건으로 통과한다
- `ici/viewer` 의 검증 결과가 전환 전과 동일하다
- 두 어댑터의 커버리지가 `MEASURED` 로 집계되어 TEM 점수의 근거가 유지된다
- 어댑터 E2E 가 ici CI 에서 실제로 실행된다(skip 이 아니다)
- A-3 이 `ICI-GAPS.md` 에서 수정됨으로 이동한다
- A-2 는 **부분 수정**으로 기록한다. CMake·qmake 프로젝트는 더 이상 거부되지 않지만, 손으로 쓴
  `Makefile` 만 있는 프로젝트는 여전히 거부된다(§8). 전부 수정됐다고 적으면 남은 거부 경로가
  문서에서 사라진다

## 8. 명시적 비범위

- **§5.3 Compile Commands 검증** — 별도 작업
- **손으로 쓴 `Makefile` 어댑터** — 실측 대상 프로젝트가 없다
- **gtest/Catch2 링크 경로** — 두 저장소 어느 프로젝트도 쓰지 않는다. 어댑터가 생기면 빌드
  정의가 알아서 링크하므로 ici 쪽 전용 지원이 필요한지 자체가 불분명하다
- **`sanitize` 엔진의 어댑터 전환** — 같은 모듈을 쓸 수 있게 설계하되 이번에는 옮기지 않는다
