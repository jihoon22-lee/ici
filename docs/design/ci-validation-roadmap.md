# ici 검증 신뢰성 및 신규 CI 기능 로드맵

> **Historical / superseded.** 이 문서는 v0.4.0~v0.6.0 시기의 결정과 검증 근거를 보존하기
> 위한 기록이다. 현재 작업 범위와 체크 상태의 canonical source는
> [`Python/C++/Qt 품질 분석기 마스터 계획`](../superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md)이다.
> 아래의 "제외" 또는 "보류" 문구를 현재 상태로 해석하지 않는다.

## 1. 목적

이 문서는 `ici`의 다음 개발 범위를 두 축으로 제한한다.

1. 현재 제공 중인 검증·빌드·리포팅 기능의 결과를 신뢰할 수 있도록 보강한다.
2. C++, Python, C++/Python 혼합 프로젝트에서 실질적인 CI 실패를 조기에 발견하는 신규 검증을 추가한다.

> **v0.4.0 상태 (2026-08-20)**: 개발 축 A(기존 기능 보강)는 완료했다. 개발 축 B(신규 CI
> 검증 기능)는 전부 보류했으며 v0.4.0에 구현하지 않았다. 축 B의 Toolchain, build adapter,
> compile DB, Python compatibility, ELF/ABI, 혼합 통합 엔진은 미래 릴리스에서 별도 승인과 PR로
> 진행한다. 현재 배포물은 기존 9개 검증 엔진, build, 설정, 실행기, 리포터 및 CI 권한 보강만
> 제공한다.

> **v0.5.5 상태 (2026-08-28)**: 축 A 완료 이후에도 신뢰성 보강은 계속됐다. 실물 C++/Qt
> 프로젝트에 적용하면서 결함 17건을 찾아 13건을 고쳤고, 그 과정에서 `NOT_APPLICABLE` 증거
> 상태(§3.2), C++ 커버리지 집계, C++ 함수 경계 탐지, 테스트 CWD 계약이 바로잡혔다.
> 축 B는 **여전히 전부 보류**이며 `verify`에 신규 엔진은 등록되어 있지 않다. 달라진 점은
> 하나다 — **§7이 §5.2 빌드 어댑터의 착수 조건을 정의한다.**

> **v0.6.0 진행 중 (2026-08-29)**: §5.2 빌드 어댑터가 축 B에서 처음으로 착수됐다. 범위는
> CMake/CTest와 qmake/Make이며, 설계는
> [`docs/superpowers/specs/2026-08-29-cmake-qmake-build-adapter-design.md`](../superpowers/specs/2026-08-29-cmake-qmake-build-adapter-design.md)에
> 있다. 축 B의 나머지(§5.1 Toolchain, §5.3 Compile Commands, §5.4~§5.6)는 보류를 유지한다.
> §7의 착수 조건은 충족 방식이 바뀌었다 — 측정을 별도 단계로 두는 대신 구현과 같은 루프에서
> 수행한다. 자세한 것은 §7.4를 본다.

> **v0.6.0 완료 상태 (2026-08-31):** 위 진행 기록은 당시 상태를 설명하는 역사 기록이다.
> CMake/CTest·qmake/Make adapter는 [PR #76](https://github.com/jihoon22-lee/ici/pull/76)로
> 완료·출시됐고, viewer의 Qt 셸 회귀는 [PR #81](https://github.com/jihoon22-lee/ici/pull/81)에서
> Qt 5.15와 Qt 6 각각 4/4 CTest로 검증됐다. Toolchain capability inventory, compile DB,
> Python compatibility, ELF/ABI, hybrid integration은 여전히 마스터 계획의 후속 범위다.

## 2. 명시적 제외 범위

다음 항목은 현재 로드맵에 포함하지 않는다.

- Rust 전환 또는 Rust 가속기
- SARIF 생성 및 GitHub Code Scanning 연동
- 여러 OS에서 생성된 결과를 하나로 모으는 매트릭스 집계기
- 변경분 전용 검사, 기준선 비교, 장기 추세 대시보드
- 플러그인 SDK 또는 외부 엔진 마켓플레이스

RHEL 7.9, 8.10, 이후 버전은 각자 별도의 CI 실행 환경으로 취급한다. v0.4.0은 실행별 시스템
정보와 현재 엔진이 실제로 확인·호출한 도구의 증거만 기록하며, 서로 다른 실행 결과를 합치지
않는다. 전체 툴체인 capability inventory와 버전 정책은 개발 축 B의 미래 범위다.

## 3. 핵심 결정

### 3.1 Python 오케스트레이터 유지

검증 시간의 대부분은 외부 compiler, test runner, linter에서 발생한다. 따라서 Python
오케스트레이터를 유지하고, 중복 파일 탐색과 불필요한 외부 실행을 줄이는 데 집중한다.
프로젝트 정의를 읽어 CMake/qmake를 실제로 configure/build/test하는 어댑터는 `v0.6.0`부터
현재 기능이다. Toolchain capability inventory와 compile DB 등 나머지 축 B 기능은 미래 범위로
남긴다.

### 3.2 결과와 발견 사항 분리

- 실행 결과: `PASS`, `WARN`, `FAIL`, `ERROR`, `SKIP`
- 발견 사항 심각도: 각 `InspectionTarget.status`
- 증거 상태: `MEASURED`, `ESTIMATED`, `NOT_RUN`, `NOT_APPLICABLE`
- 게이트 참여 여부: `required = true|false`

필수 검증이 `ERROR`, `SKIP`, `NOT_RUN`이면 전체 검증은 성공할 수 없다. `WARN`의 게이트
영향은 엔진의 `mode` 정책으로 결정한다. 추정치는 보고서에
표시할 수 있지만 실측 임계값을 통과시키는 근거로 사용하지 않는다.

`NOT_APPLICABLE`(v0.5.4에 추가)은 위 규칙의 유일한 예외다. **"검증하지 못했다"와 "검증할
것이 없었다"는 다르다.** `dead`/`resource`/`cognitive`는 Python만 읽으므로 순수 C++
프로젝트에는 읽을 대상 자체가 없다. 이것을 `SKIP`으로 취급하는 동안 순수 C++ 프로젝트는
코드 품질과 무관하게 영구히 게이트를 통과할 수 없었다. 그래서
`aggregate_suite_status()`는 `NOT_APPLICABLE` 결과를 ERROR·WARN 집계 양쪽에서 제외한다.

반대 방향의 오용을 막는 것이 더 중요하다. **대상이 있었는데 측정하지 못한 것은 여전히 검증
구멍이므로 게이트를 막아야 한다.** `sanitize`가 스코프를 발견하고도 측정하지 못했다면 그것은
`NOT_APPLICABLE`이 아니라 `ESTIMATED`다. `NOT_APPLICABLE`은 "이 엔진이 이 프로젝트에
적용되지 않는다"에만 쓰고, "이번에 실행하지 못했다"에는 쓰지 않는다.

### 3.3 실행 Python과 검증 대상 Python 분리

- `ICI_PYTHON`: `ici.pyz` 자체를 실행하는 Python 3.10+
- v0.4.0의 기존 `test`/`sanitize` 엔진: 설정된 Python → 프로젝트 `.venv` → 현재
  `sys.executable` 순으로 하나의 인터프리터를 선택하고, `pytest`, `coverage`, `unittest`를
  각각 해당 인터프리터의 `-m` 모듈 호출로 실행한다.

다중 Target Python의 `compileall`, import smoke, package metadata와 `Requires-Python`을
검증하는 전용 Python 호환성 엔진은 개발 축 B의 미래 기능이다. v0.4.0은 여러 Target Python을
순회하는 호환성 검증을 제공하지 않는다.

### 3.4 OS 이름 대신 실제 실행 증거 사용

OS별 CI 작업은 독립적으로 실행한다. v0.4.0의 현재 범위는 다음과 같다.

- `doctor`는 OS ID/버전, 커널, 아키텍처, glibc와 함께 현재 구현에 포함된 제한된 도구 목록
  (git, gcc, g++, clang, clang-format, make, cmake, ruff, mypy, pytest, uv)을 확인한다.
- 기존 엔진은 실제로 호출한 도구에 한해 경로, argv, 버전, 종료 상태와 오류를 `ToolEvidence`에
  남긴다. 도구를 호출하지 않은 경우에는 해당 도구의 capability를 추정하지 않는다.
- 실행 Python과 엔진이 선택한 프로젝트 Python은 각 결과에서 구분할 수 있다.
- RHEL 7.9, 8.10 또는 이후 OS의 결과는 각각 독립적으로 보며 공통 PASS나 자동 비교를 하지
  않는다.

다음 항목은 현재 기능이 아닌 개발 축 B의 미래 범위다.

- qmake/Qt, Ninja, CTest 및 `readelf`/`objdump`/`nm`을 포함한 전체 capability inventory
- compiler target triple과 도구별 최소/최대 버전 정책

프로젝트 정의 기반 CMake/qmake build adapter는 위 미래 목록에서 제외한다. 이 기능은
`v0.6.0`에서 완료됐으며 [PR #76](https://github.com/jihoon22-lee/ici/pull/76)의 실제
구현·회귀 근거를 따른다. 전용 toolchain 엔진과 전체 capability 정책은 여전히 미래 범위다.

이 미래 범위의 도구 누락·버전 정책·shadow build 검증은
[`2026-08-19-ci-validation-features.md`](../superpowers/plans/2026-08-19-ci-validation-features.md)에
정의되어 있으며 v0.4.0에서는 구현하지 않는다.

## 4. 개발 축 A: 기존 기능 보강 (v0.4.0 완료)

우선순위는 다음과 같다.

1. 결과 상태·증거·게이트 판정 계약 수정
2. 설정 계층 병합, 검증, 모든 CLI 명령의 동일 설정 적용
3. 프로젝트 경로 경계와 메타데이터 파싱 수정
4. 서브프로세스 timeout, 출력 제한, 엔진 예외 격리
5. 테스트 0개, 도구 누락, 커버리지 미측정의 허위 PASS 제거
6. lint/type 엔진의 도구 종료 코드 및 실행 증거 보강
7. sanitize/dead/exception의 미구현 경로 보강 또는 명시적 SKIP
8. build 엔진의 경로·메타데이터·산출물 검증
9. JSON/HTML/Markdown/CLI의 상태 표현 일치 및 HTML 안전성
10. PR 코드 실행과 GitHub 쓰기 권한 분리

상세 구현 순서는
[`2026-08-19-existing-validation-hardening.md`](../superpowers/plans/2026-08-19-existing-validation-hardening.md)에
정의한다.

v0.4.0에서는 위 10개 항목을 모두 구현·검증했다. 결과/증거 계약, 설정·프로젝트 경계,
프로세스 격리, 테스트·커버리지·lint/type·sanitize/dead/exception, build 산출물, 리포터·CLI,
그리고 CI 읽기/쓰기 권한 분리를 포함한다.

## 5. 개발 축 B: 신규 CI 검증 기능 (v0.6.0에서 adapter 부분 완료)

아래 항목 중 CMake/CTest·qmake/Make adapter는 `v0.6.0`에서 구현·출시됐다. 나머지는
설계와 작업 순서만 정의한 미래 범위이며, 현재 `ici`가 제공하는 기능으로 간주해서는 안 된다.
v0.4.0의 `verify`에 신규 엔진이나 신규 CLI가 등록되어 있지 않았다는 설명은 당시 릴리스
경계를 가리킨다.

### 5.1 Toolchain 검증

향후 Toolchain 엔진이 실행 환경의 컴파일러·빌드 도구·Python을 체계적으로 기록하고 프로젝트가
요구하는 최소/최대 버전과 필수 도구를 검사한다. 서로 다른 OS의 결과는 각 실행의 JSON/HTML에서
독립적으로 확인한다.

### 5.2 CMake/CTest 및 qmake/Make 빌드 어댑터

> **v0.6.0에서 완료됨.** 구현 수준의 설계는
> [`2026-08-29-cmake-qmake-build-adapter-design.md`](../superpowers/specs/2026-08-29-cmake-qmake-build-adapter-design.md)에
> 있다. 아래는 그 문서가 확정한 계약의 요약이다.

프로젝트의 실제 빌드 시스템을 사용하여 configure, build, test를 수행한다. 기존처럼 모든 C++
파일을 하나의 임의 `g++` 명령으로 연결하지 않는다. 모든 명령은 argv 배열이며 별도 shadow
build 디렉터리를 사용한다.

확정된 계약은 다음과 같다.

- **백엔드 선택은 프로젝트 루트의 빌드 디스크립터로 정한다.** `CMakeLists.txt`면 CMake,
  `*.pro`면 qmake, 둘 다면 CMake, 손으로 쓴 `Makefile`만 있으면 거부를 유지하고, 아무것도
  없으면 기존 g++ 경로를 쓴다. 지금 A-2가 거부하는 조건이 그대로 어댑터 진입 조건이 된다.
  어느 백엔드를 왜 골랐는지는 `ToolEvidence`에 남긴다.
- **`build`와 `test` 엔진은 하나의 어댑터 모듈을 공유한다.** 두 엔진이 모두 configure를
  필요로 하므로 각자 구현하면 플래그가 갈라진다. 엔진마다 규칙이 다른 문제는 B-1과 C-9에서
  이미 두 번 겪었다.
- **커버리지 계측 플래그는 ici가 주입한다.** 프로젝트가 커버리지 빌드를 선언하도록 요구하지
  않는다. 설정을 빠뜨리면 측정이 조용히 사라지고, TEM 점수가 그 측정 위에 서 있다.
- **빌드 도구 부재는 `NOT_APPLICABLE`이 아니라 `ERROR`다.** 대상이 있었는데 측정하지 못한
  것이므로 §3.2에 따라 게이트를 막아야 한다.
- **`project.cpp_external_build_dirs`는 어댑터 경로에서 무시된다.** 이 설정은 ici가 moc를
  돌리지 못한다는 전제 위에 있었고, 어댑터가 그 전제를 없앤다. g++ 경로에서는 계속 유효하다.
- **`-std=c++17` 고정이 사라진다.** 표준을 프로젝트의 빌드 정의가 정한다.

qmake의 테스트 결과 계약(`make check`와 QtTest 출력 형식)은 가장 덜 확정된 부분이며, `diskmap`을
실제로 qmake로 전환하면서 정한다. §7의 원칙대로 지금 더 정밀하게 적는 것은 추측이 된다.

### 5.3 Compile Commands 검증

`compile_commands.json`을 읽어 다음을 확인한다.

- 프로젝트 C++ 소스가 컴파일 DB에 포함되는지
- 컴파일러 경로, C++ 표준, include 경로가 예상과 일치하는지
- 파일과 작업 디렉터리가 프로젝트 또는 허용된 빌드 루트 안에 있는지
- CMake/qmake 빌드 결과와 컴파일 DB가 일치하는지

### 5.4 Python Runtime/Package 호환성 검증

향후 기능은 설정된 각 Target Python에서 다음을 독립적으로 검사한다.

- 전체 소스 `compileall`
- 설정된 모듈 import smoke
- `Requires-Python`
- console script 진입점 import
- wheel 파일을 제공한 경우 wheel tag와 순수 Python 정책

### 5.5 ELF/ABI 검증

빌드 산출물에 대해 실행 없이 `readelf` 중심으로 다음을 검사한다.

- CPU 아키텍처와 ELF class
- `DT_NEEDED`
- 금지되거나 누락된 RPATH/RUNPATH
- 필요한 GLIBC, GLIBCXX, CXXABI 심볼 버전 상한
- 프로젝트 빌드 절대경로 유출

### 5.6 C++/Python 혼합 통합 스모크

향후 기능은 사용자가 선언한 argv 기반 시나리오로 Python→C++ 실행, Python 네이티브 모듈 import,
C++ 실행 파일의 Python 호출을 검증한다. 기대 종료 코드와 stdout/stderr 정규식을 검사하되
shell은 사용하지 않는다.

상세 구현 순서는
[`2026-08-19-ci-validation-features.md`](../superpowers/plans/2026-08-19-ci-validation-features.md)에
정의한다.

## 6. 구현 순서와 릴리스 경계

신규 기능은 기존 기능 보강 계획이 완료된 뒤 별도 승인과 PR로 시작한다. v0.4.0은 신뢰성
릴리스 경계였고, v0.6.0에서 CMake/qmake adapter가 추가됐다. 나머지 단계는 아직 구현되지
않았다.

1. **완료 — 신뢰성 릴리스 (v0.4.0)**: 개발 축 A 전체 완료
2. **부분 완료 — 빌드 인식 릴리스 (v0.6.0)**: CMake/qmake build adapter 완료; Toolchain + Compile Commands는 미래
3. **미래 — 언어 호환성 릴리스**: Python Runtime/Package + ELF/ABI
4. **미래 — 혼합 프로젝트 릴리스**: 통합 스모크

각 릴리스는 Python 3.10 품질 게이트, ruff, 재현 가능한 pyz 빌드, smoke 테스트를 통과해야 한다.

## 7. 축 B 착수 조건: 요구사항을 추측하지 않는다 (역사 기록)

> **상태 보정 (2026-08-31):** 아래 §7.2~§7.4는 v0.6.0 adapter를 구현하기 전후의
> 실측·설계 근거를 보존한다. A-2/A-3 결함 자체는 [PR #76](https://github.com/jihoon22-lee/ici/pull/76)로
> 해결됐으므로 현재 미해결 목록으로 읽지 않는다.

§5.3 Compile Commands는 **아직 스펙을 쓰지 않는다.** 쓸 수 없어서가 아니라, 지금 쓰면
실측이 아닌 추측이 되기 때문이다. §5.2 CMake/qmake adapter는 이 착수 조건을 거쳐
`v0.6.0`에서 별도 설계·구현됐으며, 아래 §7.2~§7.4는 그 전후의 근거를 역사로 보존한다.

### 7.1 근거: 결함은 읽어서가 아니라 만들다 나왔다

축 A 이후 실물 C++/Qt 프로젝트에 적용하며 발견한 결함 17건은 **전부 만들다 나왔고, 코드를
읽어서 찾은 것은 하나도 없다.** 반복해서 나온 유형은 "게이트는 초록불인데 실제로는 아무것도
검증되지 않은" 경우였다 — CI에서 `lint`가 한 번도 실행된 적 없었고, C++ 함수의 절반이
복잡도 측정에서 누락됐으며, 브랜치 커버리지가 20%p 과소 집계됐다. 이 중 어느 것도 설계
문서를 읽어서는 나오지 않았다.

어댑터도 같은 방식으로 다뤄야 한다. 실물 Qt 프로젝트를 대상으로 실제로 막혀 봐야 요구사항이
추측이 아니라 실측이 된다.

### 7.2 당시 결함과 v0.6.0 해결 상태

| | 항목 | 코드 위치 | 당시 현상 | 현재 상태 |
|---|---|---|---|---|
| **A-2** | 루트 빌드 디스크립터 거부 | `src/ici/engines/build.py` — `_has_build_descriptor()` | 저장소 **루트**에 `CMakeLists.txt`/`Makefile`/`*.pro`가 있으면 `build` 엔진이 빌드를 거부한다. 하위 디렉터리는 검사하지 않는다. | CMake/qmake adapter가 PR #76에서 해결 |
| **A-3** | 테스트 컴파일이 plain g++ 고정 | `src/ici/engines/test.py` — `compile_cmd` 구성부 | `g++ -std=c++17`로 직접 컴파일·링크한다. **moc 실행 단계가 없고**, gtest/Catch2 같은 테스트 프레임워크 링크 경로도 없다. | 프로젝트 build/test adapter와 QtTest 경로가 PR #76에서 해결 |

당시 이 둘이 §5.2의 본체였다. 현재 §5.2의 남은 일은 toolchain·compile DB 등 마스터 계획의
후속 범위이며, 이 표의 과거 현상을 현재 결함으로 다시 보고하지 않는다.

### 7.3 착수 조건

당시 도그푸딩 대상인 세 C++/Qt 프로젝트(`ici/viewer`, 그리고 별도 저장소의 `diskmap`,
`loglens`)가 **A-2와 A-3을 정면으로 만나지 않도록 설계돼 있다는 점**이다. 로직을 Qt 없는
`core`로 밀어냈고(→ moc가 필요한 테스트가 없다), `CMakeLists.txt`를 하위 디렉터리에 두었으며
(→ 루트 거부에 걸리지 않는다), 자체 ASSERT 매크로를 썼다(→ 프레임워크가 아쉬울 일이 없다).

우회 자체는 당시 옳은 선택이었다. 하지만 이 상태에서 어댑터를 설계하면 **실측이 아니라 그
우회에서 역추론한 요구사항**으로 설계하게 된다.

따라서 §5.2 스펙 작성은 다음을 만족한 뒤에 시작한다.

1. moc가 필요한 `Q_OBJECT` 모델이 **단위 테스트 대상으로** 존재하고, 그것을 ici로 검증하려
   할 때 막히는 지점이 재현 조건과 함께 기록됐다.
2. 루트에 빌드 디스크립터를 둔 Qt 프로젝트에서 A-2가 재현되고, 그때 어댑터가 무엇을 대신
   해줘야 하는지가 기록됐다.
3. `.qrc`/`.ui` 생성 단계가 필요한 프로젝트가 존재해, 어댑터가 다뤄야 할 CMake 생성 단계의
   범위가 드러났다.

이 측정은 [toy-projects](https://github.com/jihoon22-lee/toy-projects) 저장소에서 수행하며,
순서와 근거는 그곳의 `ROADMAP.md`에, 발견된 결함은 `ICI-GAPS.md`에 쌓인다. 세 조건이 채워지면
**새 문서를 만들지 말고 §5.2를 이어서 확장한다.**

### 7.4 착수 조건을 실제로 충족한 방식 (2026-08-29)

§7.3은 측정을 스펙 작성 **이전의 별도 단계**로 두었다. v0.6.0에서는 그렇게 하지 않고 **측정과
구현을 같은 루프에서** 수행한다. 먼저 실패하는 진짜 테스트를 쓰고, 그 테스트가 어댑터 구현을
끌고 간다.

요구사항이 실측에서 나온다는 §7.1의 의도는 그대로 지켜진다. 달라지는 것은 빨간 상태를 커밋으로
남기지 않는다는 점뿐이다. 우회를 새로 만들지 않기 위해 **Qt 테스트는 `tests/` 안에 두고 실제로
통과시킨다** — 별도 디렉터리로 옮기거나 게이트에서 제외하지 않는다.

세 조건의 충족 상태는 다음과 같다.

| | 조건 | 상태 |
|---|---|---|
| 1 | moc가 필요한 `Q_OBJECT` 테스트 대상 | `loglens`의 `QAbstractItemModelTester` 테스트, `diskmap`의 `QSignalSpy` 테스트로 충족 |
| 2 | 루트 빌드 디스크립터에서 A-2 재현 | `loglens`(CMake)와 `diskmap`(qmake) 전환으로 충족 |
| 3 | `.qrc`/`.ui` 생성 단계 | **충족되지 않음** |

**조건 3은 이번 범위에 들어오지 않는다.** `loglens`와 `diskmap` 어느 쪽도 `.qrc`/`.ui`를 쓰지
않으므로 CMake 생성 단계(`AUTORCC`/`AUTOUIC`)의 요구사항은 여전히 실측되지 않았다. `AUTOMOC`만
검증된다. `toy-projects/ROADMAP.md`의 3단계(diff/merge 도구)가 이 조건을 위해 `.qrc`/`.ui`를
의도적으로 넣기로 되어 있으므로, 조건 3은 그때까지 미충족으로 남긴다.

qmake는 §7.3을 쓸 당시 실측 대상이 없었다. `diskmap`을 qmake로 전환하는 것은 그 공백을 메우기
위한 것이다 — 실물 프로젝트 없이 qmake 백엔드를 설계하면 §7.1이 경계한 추측이 qmake 쪽에서만
반복된다.

## 8. 완료 정의

### 8.1 v0.4.0에서 달성한 기준

- 실행하지 않은 필수 검증이 PASS로 표시되지 않는다.
- 모든 FAIL/ERROR는 파일 위치 또는 실행 단계와 명령 증거를 제공한다.
- JSON/HTML/Markdown/CLI가 같은 상태·증거 계약을 표현한다.
- 검증 job은 읽기 전용이고 신뢰된 main publish만 `contents: write`를 사용한다.
- Python 3.10, Ruff, mypy, 재현 가능한 ZipApp, smoke 게이트를 통과한다.

### 8.2 축 B의 완료 기준

- [x] CMake와 qmake 프로젝트가 실제 빌드 정의로 검증된다 (`v0.6.0`, PR #76).
- Target Python과 `ici.pyz` 실행 Python이 보고서에서 구분된다.
- 각 OS의 독립 실행 결과만으로 사용 툴체인과 실패 원인을 재현할 수 있다.
- C++/Python 혼합 프로젝트의 경계 동작을 CI에서 자동 검증할 수 있다.
- 신규 엔진은 별도 계획·승인·PR과 동일한 품질 게이트를 통과한다.
