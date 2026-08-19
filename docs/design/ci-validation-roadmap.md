# ici 검증 신뢰성 및 신규 CI 기능 로드맵

## 1. 목적

이 문서는 `ici`의 다음 개발 범위를 두 축으로 제한한다.

1. 현재 제공 중인 검증·빌드·리포팅 기능의 결과를 신뢰할 수 있도록 보강한다.
2. C++, Python, C++/Python 혼합 프로젝트에서 실질적인 CI 실패를 조기에 발견하는 신규 검증을 추가한다.

## 2. 명시적 제외 범위

다음 항목은 현재 로드맵에 포함하지 않는다.

- Rust 전환 또는 Rust 가속기
- SARIF 생성 및 GitHub Code Scanning 연동
- 여러 OS에서 생성된 결과를 하나로 모으는 매트릭스 집계기
- 변경분 전용 검사, 기준선 비교, 장기 추세 대시보드
- 플러그인 SDK 또는 외부 엔진 마켓플레이스

RHEL 7.9, 8.10, 이후 버전은 각자 별도의 CI 실행 환경으로 취급한다. `ici`는 매 실행에서
현재 OS와 툴체인을 정확히 진단하고 그 실행 결과에 포함하지만, 서로 다른 실행 결과를 합치지는
않는다.

## 3. 핵심 결정

### 3.1 Python 오케스트레이터 유지

검증 시간의 대부분은 GCC, CMake, qmake, pytest, mypy와 같은 외부 프로그램에서 발생한다.
따라서 Python 오케스트레이터를 유지하고, 중복 파일 탐색과 불필요한 외부 실행을 줄이는 데
집중한다.

### 3.2 결과와 발견 사항 분리

- 실행 결과: `PASS`, `WARN`, `FAIL`, `ERROR`, `SKIP`
- 발견 사항 심각도: 각 `InspectionTarget.status`
- 증거 상태: `MEASURED`, `ESTIMATED`, `NOT_RUN`
- 게이트 참여 여부: `required = true|false`

필수 검증이 `ERROR`, `SKIP`, `NOT_RUN`이면 전체 검증은 성공할 수 없다. `WARN`의 게이트
영향은 엔진의 `mode` 정책으로 결정한다. 추정치는 보고서에
표시할 수 있지만 실측 임계값을 통과시키는 근거로 사용하지 않는다.

### 3.3 실행 Python과 검증 대상 Python 분리

- `ICI_PYTHON`: `ici.pyz` 자체를 실행하는 Python 3.10+
- Target Python: 프로젝트가 지원한다고 선언한 Python 인터프리터

Python 호환성 검증은 Target Python 경로를 명시적으로 사용한다. `pytest`, `coverage`,
`compileall`도 해당 인터프리터의 `-m` 실행을 우선한다.

### 3.4 OS 이름 대신 실제 툴체인 증거 사용

OS별 CI 작업은 독립적으로 실행한다. 각 실행 결과에는 다음 정보를 포함한다.

- OS ID/버전, 커널, 아키텍처, glibc
- gcc/g++/gcov 경로, 버전, target triple
- CMake/CTest, qmake/Qt, Make/Ninja 경로와 버전
- 실행 Python과 Target Python 경로 및 버전
- `readelf`, `objdump`, `nm` 같은 진단 도구 가용 여부

툴 경로는 설정에서 명시할 수 있어야 하며, 명시한 경로가 없거나 요구 버전을 만족하지 않으면
필수 검증은 `ERROR`가 된다.

## 4. 개발 축 A: 기존 기능 보강

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

## 5. 개발 축 B: 신규 CI 검증 기능

### 5.1 Toolchain 검증

현재 실행 환경의 컴파일러·빌드 도구·Python을 기록하고 프로젝트가 요구하는 최소/최대 버전과
필수 도구를 검사한다. 서로 다른 OS의 결과는 각 실행의 JSON/HTML에서 독립적으로 확인한다.

### 5.2 CMake/CTest 및 qmake/Make 빌드 어댑터

프로젝트의 실제 빌드 시스템을 사용하여 configure, build, test를 수행한다. 기존처럼 모든 C++
파일을 하나의 임의 `g++` 명령으로 연결하지 않는다. 모든 명령은 argv 배열이며 별도 shadow
build 디렉터리를 사용한다.

### 5.3 Compile Commands 검증

`compile_commands.json`을 읽어 다음을 확인한다.

- 프로젝트 C++ 소스가 컴파일 DB에 포함되는지
- 컴파일러 경로, C++ 표준, include 경로가 예상과 일치하는지
- 파일과 작업 디렉터리가 프로젝트 또는 허용된 빌드 루트 안에 있는지
- CMake/qmake 빌드 결과와 컴파일 DB가 일치하는지

### 5.4 Python Runtime/Package 호환성 검증

설정된 각 Target Python에서 다음을 독립적으로 검사한다.

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

사용자가 선언한 argv 기반 시나리오로 Python→C++ 실행, Python 네이티브 모듈 import, C++
실행 파일의 Python 호출을 검증한다. 기대 종료 코드와 stdout/stderr 정규식을 검사하되 shell은
사용하지 않는다.

상세 구현 순서는
[`2026-08-19-ci-validation-features.md`](../superpowers/plans/2026-08-19-ci-validation-features.md)에
정의한다.

## 6. 구현 순서와 릴리스 경계

신규 기능은 기존 기능 보강 계획이 완료된 뒤 시작한다. 최소 릴리스 경계는 다음과 같다.

1. **신뢰성 릴리스**: 개발 축 A 전체 완료
2. **빌드 인식 릴리스**: Toolchain + 빌드 어댑터 + Compile Commands
3. **언어 호환성 릴리스**: Python Runtime/Package + ELF/ABI
4. **혼합 프로젝트 릴리스**: 통합 스모크

각 릴리스는 Python 3.10 품질 게이트, ruff, 재현 가능한 pyz 빌드, smoke 테스트를 통과해야 한다.

## 7. 완료 정의

- 실행하지 않은 필수 검증이 PASS로 표시되지 않는다.
- 모든 FAIL/ERROR는 파일 위치 또는 실행 단계와 명령 증거를 제공한다.
- CMake와 qmake 프로젝트가 실제 빌드 정의로 검증된다.
- Target Python과 `ici.pyz` 실행 Python이 보고서에서 구분된다.
- 각 OS의 독립 실행 결과만으로 사용 툴체인과 실패 원인을 재현할 수 있다.
- C++/Python 혼합 프로젝트의 경계 동작을 CI에서 자동 검증할 수 있다.
