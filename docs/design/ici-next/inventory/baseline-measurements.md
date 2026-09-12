# 기준선 측정 기록

- 상태: **측정 완료 (measured)**. 이 문서는 실제로 실행한 것과 실행하지 못한 것을 구분해
  기록한다. 실패를 숨기기 위해 expected 값이나 threshold를 변경한 곳은 없다.
- 측정 커밋: `20c417cc8ec84aa490d0138782bf9fe38374fb5d`
- 근거 이슈: [WP00 #198](https://github.com/jihoon22-lee/ici/issues/198) 구현 순서 3,
  「검증 / PR 분할 / 복구」
- 측정 환경: 이 저장소의 원격 개발 컨테이너 (Linux x86_64, glibc 기반).
  **사내 RHEL 8.10이 아니다.** spec-05 §1이 요구하는 대로, 이 기록은 개발 환경 증거이며
  현장 지원 선언의 근거가 아니다.

## 1. 코드 기준선 확인

| 항목 | 값 |
|---|---|
| 조사 시점 `main` HEAD | `20c417cc8ec84aa490d0138782bf9fe38374fb5d` |
| PLAN(#191)이 지정한 기준 SHA | `20c417cc8ec84aa490d0138782bf9fe38374fb5d` |
| 차이 | **없음** (`git log 20c417c..HEAD` 가 빈 결과) |
| HEAD 커밋 제목 | `docs: fill the v0.11.0 evidence blocks with measured values (#190)` |
| 릴리스 상태 | v0.11.0 직후 |

기준 SHA와 조사 시점이 같으므로, 이 inventory는 기준 SHA 그 자체에 대한 관찰이다.
#198 선행 조건의 "main과 기준 SHA의 차이를 기록한다"는 **차이 없음**으로 충족된다.

### 보존 확인

이번 작업에서 다음을 **변경하지 않았다**:

- `src/` 전체 (코드 동작 변경 없음)
- `examples/cpp-fixtures/` 10개 fixture
- `tests/` 기존 테스트 파일 (신규 파일만 추가)
- `pyproject.toml`의 `requires-python`, ruff `target-version`
- `.github/workflows/` 4개 워크플로
- `scripts/` 전체
- 기존 엔진의 threshold·expected 값

## 2. 환경에서 사용 가능한 도구

| 도구 | 상태 | 버전/경로 |
|---|---|---|
| `python3` | 있음 | 3.11.15 |
| `python3.10` | 있음 | `/usr/bin/python3.10` |
| `uv` | 있음 (**버전 불일치**) | 0.8.17 |
| `ruff` | 있음 | 0.15.8 |
| `gcc` / `g++` | 있음 | `/usr/bin/` |
| `make` / `cmake` / `ctest` | 있음 | `/usr/bin/` |
| `gcov` / `readelf` / `nm` | 있음 | `/usr/bin/` |
| `clang-tidy` | 있음 | `/usr/bin/clang-tidy` |
| `qmake` / `qmake6` | **없음** | — |
| `clazy` | **없음** | — |
| Qt6 (CMake 패키지) | **없음** | `find_package(Qt6)` 실패 |
| `jsonschema` (Python 모듈) | **없음** | — |

## 3. AGENTS 품질 게이트 실행 결과

AGENTS.md §6이 정의한 5개 명령을 순서대로 실행했다.

| # | 명령 | 결과 | 근거 |
|---|---|---|---|
|1|`uv run --python 3.10 pytest`|**2 failed, 2779 passed, 5 skipped** (88.44s)|아래 §3.1|
|2|`uvx ruff check .`|**PASS** — `All checks passed!`|ruff 0.15.8로 실행|
|3|`uvx ruff format --check .`|**PASS** — `257 files already formatted`|ruff 0.15.8로 실행|
|4|`./scripts/build-pyz.sh`|**환경 미준비로 미실행**|아래 §3.2|
|5|`./scripts/smoke.sh`|**환경 미준비로 미실행 (연쇄)**|아래 §3.2|

`uvx ruff`가 아니라 컨테이너에 설치된 `ruff` 0.15.8을 직접 실행했다. `uvx`는 네트워크에서
버전을 가져오므로, 폐쇄망 계약(R11)을 확인하는 목적에서도 설치본 실행이 적절하다.
다만 **CI가 쓰는 ruff 버전과 0.15.8이 같은지는 이 측정으로 확인되지 않았다.**

### 3.1 pytest 실패 2건 — 환경 미준비이지만 skip이 아니라 fail

두 실패 모두 `tests/test_build_adapter_e2e.py`다.

```
FAILED tests/test_build_adapter_e2e.py::test_cmake_fixture_builds_and_tests_a_q_object
FAILED tests/test_build_adapter_e2e.py::test_cmake_fixture_reports_exact_gcov_values_and_geometry
```

실패 메시지:

```
cmake configure failed: CMake Error at CMakeLists.txt:8 (find_package):
  By not providing "FindQt6.cmake" in CMAKE_MODULE_PATH this project has
  asked CMake to find a package configuration file provided by "Qt6", but ...
```

**근본 원인은 테스트의 요구 도구 가드 누락이다.** 두 테스트는 다음과 같이 선언한다.

```python
_require("cmake", "ctest", "gcov")      # tests/test_build_adapter_e2e.py:51, :74
```

그런데 이들이 사용하는 fixture `examples/cpp-fixtures/cmake_project/CMakeLists.txt:8`은

```cmake
find_package(Qt6 REQUIRED COMPONENTS Core Test)
```

를 요구한다. 이 컨테이너에는 cmake·ctest·gcov가 **모두 있고** Qt6만 없다. 따라서 `_require`는
통과하고, 이후 실제 configure 단계에서 Qt6 부재로 실패한다.

같은 파일의 다른 테스트는 `_require_qmake()`로 qmake 부재를 올바르게 감지해 skip된다
(§3.3의 skip 2건). 즉 **qmake 경로에는 가드가 있고 Qt6 경로에는 없다.**

판정: 이것은 ici 분석 로직의 회귀가 아니라 **테스트 가드의 결함**이다. 근거:

- 실패 지점이 ici 코드가 아니라 fixture의 CMake configure다.
- 같은 파일의 docstring이 이미 이 의도를 명시한다 — "These tests need cmake/qmake/Qt and are
  skipped when those are missing". Qt6이 그 목록에 있는데 가드에는 없다.
- `ICI_REQUIRE_BUILD_ADAPTERS=1`이 설정된 CI에서는 도구 부재를 **의도적으로 실패**로 만든다.
  이 컨테이너에는 그 변수가 없으므로, 설계 의도대로라면 skip이어야 한다.

**이번 작업에서 이 테스트를 수정하지 않았다.** #198은 코드 동작을 바꾸지 않는 작업이고,
테스트 가드를 고치는 것은 corpus/harness를 소유하는
[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201)의 범위다. 해당 WP가 처리해야 하는
구체적 결함으로 §5에 등록했다.

동시에 이 사례는 spec-05 §2가 요구하는 계층 구분의 실증이다 — "mock 통과를 compiler/Qt 실제
검증으로 표기하지 않는다". 여기서는 그 반대 방향의 사고, **환경 미준비가 실패로 보고되는**
경우가 발생했다.

### 3.2 build-pyz / smoke 미실행 사유

```
$ ./scripts/build-pyz.sh
uv 0.12.5가 필요합니다: uv 0.8.17
(exit 1)
```

`scripts/build-pyz.sh:38`이 `EXPECTED_UV_VERSION="0.12.5"`를 고정하고, `:41`에서 정확히
일치하지 않으면 거부한다. 이 컨테이너의 uv는 0.8.17이다.

```
$ ./scripts/smoke.sh
산출물이 없습니다: /home/user/ici/dist/ici.pyz (먼저 ./scripts/build-pyz.sh 실행)
(exit 1)
```

smoke는 build-pyz의 산출물에 의존하므로 연쇄적으로 미실행이다.

**두 게이트는 "실패"가 아니라 "미실행"으로 기록한다.** uv 버전 고정은 재현 가능한 빌드를 위한
의도된 불변식(AGENTS §4 "재현성")이고, 그것이 제대로 작동해서 잘못된 버전으로 빌드하는 것을
막았다. 따라서:

- 이 저장소의 pyz 재현성 불변식은 **검증되지 않았다** (측정 못 함).
- 동시에 uv 0.12.5 고정은 spec-02 §1의 bundle manifest가 요구하는
  "build input lock digest"의 현행 대응물로 기록해 둘 가치가 있다.
  → [ADR-0002](../adr/0002-standalone-runtime-bundle.md)

### 3.3 skip 5건 (모두 정상 동작)

| 테스트 위치 | 사유 |
|---|---|
|`test_build_adapter_e2e.py:35`|`build adapter tools unavailable: qmake6` |
|`test_build_adapter_e2e.py:35`|`build adapter tools unavailable: qmake6, qmake` |
|`test_context_reporting.py:652`|`jsonschema is unavailable` |
|`test_cpp_tool_e2e.py:38`|`required Linux tool(s) unavailable: clazy` |
|`test_python_compat.py:157`|`host AST cannot parse except-star syntax` |

5건 모두 도구/기능 부재를 명시적으로 알리고 skip한다. **가드가 있는 경로는 설계대로 동작한다.**
§3.1의 Qt6 경로만 예외다.

## 4. 이 측정으로 확정할 수 **없는** 것

spec-05 §1이 요구한 `planned/tested/supported/limited/unsupported` 구분에서, 이번 측정이
`tested`로 올릴 수 있는 것은 매우 제한적이다.

| 축 | 이번 측정 상태 | 이유 |
|---|---|---|
|Python 정적 검사 경로 (ruff/pytest)|**tested (개발 환경)**|실제 실행하고 결과를 확인함|
|C++ CMake 경로|**limited**|cmake는 있으나 Qt6이 없어 fixture E2E 미완주|
|C++ qmake 경로|**untested**|qmake 부재|
|Qt 지원|**untested**|Qt6 부재|
|clazy provider|**untested**|clazy 부재|
|pyz 패키징·재현성|**untested**|uv 버전 불일치로 미실행|
|RHEL 8.10|**untested**|해당 환경 아님|
|GHES 게시|**untested**|GHES 없음|
|오프라인 실행|**untested**|이 측정에서 네트워크 차단 시험을 하지 않음|
|성능 (cold/warm, peak RSS)|**unmeasured**|spec-05 §6의 고정 corpus가 아직 없음|

`uv run --python 3.10 pytest`가 통과한 2779건은 대부분 순수 단위·process contract 계층이다.
spec-05 §2의 계층 구분에 따르면 이것을 "실제 도구 검증"이나 "현장 인수"로 표기해서는 안 된다.

## 5. 후속 WP로 넘기는 구체적 항목

| # | 항목 | 담당 WP |
|---|---|---|
|1|`test_build_adapter_e2e.py`의 Qt6 요구 가드 누락 — cmake/ctest/gcov는 있고 Qt6만 없는 환경에서 skip이 아니라 fail이 된다|[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201)|
|2|fixture manifest 부재 — `examples/cpp-fixtures/` 10개에 목적·요구 도구·예상 finding·실행 비용 선언이 없다|[WP03 #201](https://github.com/jihoon22-lee/ici/issues/201)|
|3|CI가 쓰는 ruff 버전과 로컬 0.15.8의 일치 여부 미확인|[WP29 #227](https://github.com/jihoon22-lee/ici/issues/227)|
|4|pyz 재현성 불변식 미검증 (uv 0.12.5 필요)|[WP04 #202](https://github.com/jihoon22-lee/ici/issues/202)|
|5|고정 성능 corpus와 cold/warm 예산 부재|[WP28 #226](https://github.com/jihoon22-lee/ici/issues/226)|
|6|오프라인·read-only install·clean HOME 실행 시험 부재|[WP04 #202](https://github.com/jihoon22-lee/ici/issues/202), [WP28 #226](https://github.com/jihoon22-lee/ici/issues/226)|

## 6. 재측정 방법

이 기록을 다른 환경에서 재현하려면:

```bash
git rev-parse HEAD                       # 20c417cc8ec84aa490d0138782bf9fe38374fb5d 확인
ruff check . && ruff format --check .
uv run --python 3.10 pytest -p no:cacheprovider --tb=no -rsN
./scripts/build-pyz.sh && ./scripts/smoke.sh
```

`ICI_REQUIRE_BUILD_ADAPTERS=1`을 설정하면 도구 부재가 skip 대신 fail이 된다. 그 모드에서는
qmake·clazy·Qt6 부재로 §3.3의 skip 건들도 실패하므로, **이 컨테이너는 그 모드의 대상이 아니다.**
