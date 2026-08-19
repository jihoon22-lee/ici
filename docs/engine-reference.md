# ici 검증 엔진 레퍼런스 (Engine Reference)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; **📏 검증 엔진 레퍼런스** &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 소프트웨어 공학적 품질과 보안을 보장하기 위해 9대 핵심 검증 엔진과 엔터프라이즈 도구 인터페이스를 제공합니다.

---

## 1. 품질 정책 설정 (`ici.toml`)

전사 공용 정책 또는 프로젝트별 기준을 `ici.toml`을 통해 중앙에서 관리합니다.
설정 파일이 하나도 없는 최초 실행 시에는 `~/.config/ici/ici.toml`(XDG 존중)에
기본 정책 파일이 자동 생성되므로, 이를 열어 조직 기준으로 수정하면 됩니다.

### 1.0 프로젝트 레이아웃 (`[project]`)
소스 디렉토리가 `src`가 아닌 프로젝트는 `source_dirs`로 지정할 수 있습니다
(미지정 시 `src`, `lib`, `app`, `packages`, `python` 중 존재하는 디렉토리 자동 탐색):

```toml
[project]
source_dirs = ["lib"]
```

### 1.1 평가 모드 (`mode`)
각 엔진별로 결과 평가 방식을 설정할 수 있습니다:
- `"pass_warn_fail"`: FAIL 조건 시 FAIL, WARN 조건 시 WARN, 그 외 PASS (기본값)
- `"pass_fail"`: 경고(WARN)를 허용하지 않고 즉시 FAIL 처리
- `"pass_warn"`: 실패(FAIL) 없이 정보성 경고(WARN)로만 관리

```toml
[engines.line]
enabled = true
mode = "pass_warn"
warn_limit = 500
fail_limit = 1000
gate_dirs = ["src", "include", "lib", "app"]
exclude_dirs = ["docs"]

[engines.test]
enabled = true
mode = "pass_fail"
min_tem_score = 4.0
min_branch_cov = 80.0
min_func_cov = 90.0
# Use the project interpreter for pytest, coverage.py, and unittest.
# python = "/workspace/.venv/bin/python"
coverage_required = false

[engines.lint]
enabled = true
mode = "pass_warn_fail"
# Optional by default: missing Ruff is reported as WARN/ESTIMATED.
ruff_required = false

[engines.type]
enabled = true
mode = "pass_warn"
# Optional by default: missing Mypy is reported as WARN/ESTIMATED.
mypy_required = false

[engines.complexity]
enabled = true
mode = "pass_warn_fail"
warn_cc = 15
fail_cc = 25
warn_nesting = 4

[engines.dup]
enabled = true
mode = "pass_warn"
min_window = 6
warn_pct = 5.0
fail_pct = 15.0
```

이 저장소의 dogfood 정책(`ici.toml`)은 현재 CI·로컬 측정값의 실행기별 편차를 고려해
TEM `2.0`, Branch `35%`, Function `60%`를 floor로 사용합니다. `mode = "pass_fail"`은
그 floor 미달 경고와 테스트 실행 실패를 모두 게이트 실패로 승격하며, 도구·테스트가
개선되면 floor를 다시 높일 수 있습니다.

### 1.2 결과 상태와 게이트 계약

모든 엔진은 `EngineResult`를 반환하며, 결과 상태와 증거 상태를 함께 기록합니다.

- `PASS`: 정책 기준을 통과했습니다.
- `WARN`: 조치가 권장되지만 게이트를 차단하지 않는 경고입니다.
- `FAIL`: 필수 엔진의 정책 기준을 충족하지 못했습니다.
- `ERROR`: 실행 오류 또는 필수 검증을 완료하지 못한 상태입니다.
- `SKIP`: 검증이 실행되지 않은 상태입니다. 필수 엔진의 `SKIP`은 게이트에서 `ERROR`로 승격됩니다.

증거 상태(`EvidenceState`)는 다음과 같습니다.

- `MEASURED`: 도구를 실제 실행해 측정한 결과입니다.
- `ESTIMATED`: 도구를 실행할 수 없어 추정한 결과입니다.
- `NOT_RUN`: 검증이 실행되지 않았음을 명시합니다.

`EngineResult.required`의 기본값은 `true`이므로, 별도 설정이 없는 엔진은 필수 엔진으로
간주됩니다. `aggregate_suite_status()`는 빈 결과 집합, 필수 엔진의 `ERROR`/`SKIP`, 또는
필수 엔진의 `NOT_RUN` 증거를 `ERROR`로 처리합니다. 그 외에는 필수 엔진의 `FAIL`, 모든
엔진의 `WARN`, 마지막으로 `PASS` 순서로 전체 게이트 상태를 결정합니다. 도구 실행 정보가
있는 경우 `ToolEvidence`에 도구 이름·경로·버전·인자·반환 코드를 기록할 수 있습니다.

---

## 2. 9대 핵심 검증 엔진 상세

### 2.1 📏 `line` (코드 라인 및 파일 크기 분석기)
- **검증 규칙**:
  - 파일당 순수 코드(코드 라인) 기준으로 크기 과대화 진단
  - `warn_limit` (기본 500줄): 모듈 분리 검토 권고
  - `fail_limit` (기본 1000줄): 단일 파일 과대화 실패
- **게이트/통계 분리**: 임계값 검증은 `gate_dirs`(기본 `src`, `include`, `lib`, `app`)에만 적용.
  `tests`/`docs`/`scripts` 등은 라인 통계와 HTML 트리 뷰에만 포함되며 실패를 만들지 않습니다.
  `include_dirs`(스캔 범위 재정의), `exclude_dirs`(제외)로 조직 정책 조절 가능.
- **출력 메트릭**: 코드 라인, 주석 라인, 공백 라인 수 및 디렉토리 계층 트리 ([HTML 뷰어 지원](user-guide.md#22-인터랙티브-html-리포트-생성-및-자동-브라우저-열기))

### 2.2 🧹 `lint` (문법 및 코드 스타일 린터)
- **Python**: `ruff check --output-format=json`과 `ruff format --check`를 실행합니다. Ruff가
  없으면 AST 문법 검사만 수행하는 부분 폴백으로 전환하며, `ruff_required = true`이면
  `ERROR`/`NOT_RUN`, 기본 선택 정책이면 `WARN`/`ESTIMATED`로 기록합니다. Ruff의 종료 코드,
  JSON 진단 구조, 포맷 성공 문법은 엄격히 검증하며 도구 오류와 실제 진단을 구분합니다.
  Ruff는 PATH에 직접 실행 가능한 파일 또는 프로젝트 `.venv/bin`/`.venv/Scripts`의 실행 파일만
  사용합니다. `uvx`/`uv run`을 도구 설치로 간주하거나 패키지 해석을 시도하지 않으므로 폐쇄망에서
  실행 시 부작용이 없습니다. `ruff format --check`가 종료 코드 0과 빈 stdout/stderr를 반환하는
  것도 정상 성공으로 인정합니다.
- **C++**: 발견된 각 `.cpp`/`.cc`/`.cxx`/`.c`에 `g++ -fsyntax-only -std=c++17 -Wall -Wextra`를
  실행합니다. 소스가 있는데 g++가 없거나 timeout·출력 절단·spawn·비정상 종료·진단 파싱 실패가
  발생하면 `ERROR`/`NOT_RUN`입니다. 정상적인 `error`/`warning` 진단은 보고된 파일과 라인에
  `InspectionTarget`으로 남기고 엔진 `mode` 정책을 따릅니다. 위치가 있는 `note:`는 보조
  진단으로 `WARN`에 남기며, 위치 없는 임의 문맥 줄은 성공으로 삼지 않습니다.
- 모든 Ruff/g++ 시도는 `ToolEvidence`에 argv, 반환 코드 및 timeout/절단/실패 사유를 남깁니다.
  종료 코드 2 이상이나 잘못된 성공/진단 출력도 최종 도구 오류 원인을 `error`에 기록합니다.

### 2.3 🧪 `test` & TEM 스코어링 (단위 테스트 및 테스트 효과성 지표)
- **동작**: 프로젝트 내 pytest 또는 C++ 테스트 바이너리를 실행하여 단위 테스트 전수 통과 여부 검증
- **Python 실행기**: `[engines.test].python`이 지정되면 해당 인터프리터를 우선 사용하고,
  없으면 프로젝트 `.venv`의 Python, 마지막으로 `sys.executable` 순서로 선택합니다. pytest,
  coverage.py, unittest는 모두 이 동일한 인터프리터의 `-m` 모듈 호출로 실행하며 PATH에 있는
  `pytest`/`coverage` 스크립트를 섞어 사용하지 않습니다.
- **테스트 수집 증거**: pytest 종료 코드 `5` 또는 수집된 테스트가 0개이면 `total_tests = 0`인
  `FAIL`입니다. 인터프리터/도구 부재, timeout, 출력 절단, 도구 자체 오류는 `ERROR`/`NOT_RUN`으로
  기록되어 추정치로 통과할 수 없습니다. 소스·테스트 대상이 전혀 없는 빈 프로젝트도 일반
  0개 테스트 `FAIL` 대상으로 기록되며, 선택적 커버리지는 `ESTIMATED`, 필수 커버리지는
  `ERROR`/`NOT_RUN`입니다. pytest가 `returncode = 0`을 반환하더라도 collection 줄만 있고
  통과/실패 per-test 또는 terminal summary가 없으면 실행 증거 부족으로 `ERROR`/`NOT_RUN`입니다.
- **언어별 실행 범위**: `python`/`cpp`/`hybrid` 소스가 있으면 해당 언어의 테스트 시도를 기록합니다.
  `hybrid`에서 한 언어의 테스트만 있어도 다른 언어의 0개 수집을 별도 `FAIL`로 표시하며, pytest가
  실제로 0개를 수집한 뒤 unittest로 우회하지 않습니다. pytest 모듈 자체가 명확히 없는 경우에만
  동일 인터프리터의 `-m unittest discover`로 대체합니다.
- **커버리지 정책**: `coverage_required = true`이면 Python `coverage.json` 또는 C++ `gcov`
  산출물이 실제로 생성·파싱되어야 하며, 누락·불완전·잘못된 출력은 `ERROR`/`NOT_RUN`입니다.
  `false`이면 커버리지 수치는 `ESTIMATED`로 표시되고 결과는 최소 `WARN`이며, 추정치는 TEM·
  커버리지 임계값의 `PASS` 근거로 사용되지 않습니다.
- **실측 JSON 검증**: Python JSON은 파일/전체 statement·branch 수의 일관성, 양의 정수인
  `executed_lines`/`missing_lines` 배열의 개수·중복·교집합을 검증합니다. 0 statement 또는
  측정 불가능한 산출물은 실측으로 인정하지 않으며, coverage `--version`의 timeout·절단·spawn·
  signal·기타 오류도 선택적 누락이 아닌 `ERROR`/`NOT_RUN`입니다.
- **모듈별 실측 커버리지 수집** (HTML `🧪 Tests & Coverage` 탭의 Module Coverage Table):
  - **Python**: 프로젝트 환경의 `coverage.py`가 있으면 `coverage run --branch`로 테스트를 재실행하고 `coverage json`을 파싱하여 파일별 Stmts/Miss/Cover/Branch 실측값 수집
  - **C++**: `gcov`가 있으면 `g++ --coverage` 2단계 컴파일(객체별 `-c` 후 링크)로 테스트를 빌드·실행하고 `gcov -b -p` 산출물을 파싱하여 동일한 파일별 테이블 생성 (테스트 파일 제외)
  - 둘 다 없으면 KPI는 추정치로 표시되며, HTML에 설치 안내가 노출됨
- **pytest 임시 파일**: pytest에는 프로젝트 내부 `--basetemp`를 강제로 지정하지 않으므로
  `tmp_path`와 캡처 파일은 시스템 임시 디렉토리를 사용합니다. WSL에서는 Windows 마운트의
  `TMP`/`TEMP` 대신 `/tmp`를 자식 테스트 프로세스에 전달합니다.
- **Function Coverage (gcov 호출 기준) 실측**: 함수 본문이 한 번 이상 실행되면 커버된 것으로 간주
  - Python: coverage.json의 `executed_lines`와 AST 함수 라인 범위를 교차 계산
  - C++: gcov 산출물의 `function ... called N` 라인 파싱
  - HTML `🧪 Tests & Coverage` 탭에 함수별 테이블(Function Coverage Table)로 상세 표시
  - 측정 불가 시에만 기존 추정치(통과 95%/실패 50%)로 폴백
- **TEM (Test Effectiveness Metric) 5.0 만점 산출 공식**:
  - **Line Coverage 측정 가능 시 (기본)**:
    $$\text{TEM} = \frac{\min(80, \text{Line Coverage})}{80} \times \frac{\text{Function Coverage}}{100} \times \text{Pass Rate} \times 5.0$$
  - **Branch Coverage만 측정 가능한 경우** (Branch에 $\times \frac{5}{4}$ 보정 후 동일 공식 적용):
    $$\text{TEM} = \frac{\min(80, \text{Branch} \times 1.25)}{80} \times \frac{\text{Function Coverage}}{100} \times \text{Pass Rate} \times 5.0$$
- **평가 기준**:
  - 모든 테스트 케이스 통과 필수
  - 기본 내장 정책은 TEM 스코어 $\ge 4.0$, Branch Coverage $\ge 80\%$, Function Coverage $\ge 90\%$를 요구
  - 이 저장소 dogfood 정책은 현재 측정 baseline에 맞춘 TEM $\ge 2.0$, Branch $\ge 35\%$, Function $\ge 60\%$ floor를 사용
  - 실측 커버리지가 있는 경우 Branch Coverage는 실측값(coverage.py/gcov)으로 대체됨
  - 커버리지 80% 미만 모듈은 `Coverage:Module` WARN 타깃으로 Issues 탭/PR 어노테이션에 노출

### 2.4 🏷️ `type` (정적 타입 안정성 검사기)
- **Python**: `mypy`의 정상 성공 문법과 위치가 있는 `error`/`note` 진단을 엄격히 파싱합니다.
  종료 코드 `1`의 유효한 진단은 실제 타입 발견 사항으로 `mode` 정책을 따르며, 종료 코드 `2`
  이상·timeout·출력 절단·spawn/신호 종료·잘못된 성공/진단 출력은 진단 문구가 포함되어도
  도구 `ERROR`/`NOT_RUN`입니다. `mypy_required = true`에서 미설치 도구는 `ERROR`/`NOT_RUN`이고,
  기본 선택 정책에서는 AST 함수 어노테이션 폴백을 `WARN`/`ESTIMATED`로 표시합니다. Mypy는
  PATH에 직접 실행 가능한 파일 또는 프로젝트 `.venv/bin`/`.venv/Scripts` 실행 파일만 찾으며,
  `uv`/`uvx`를 통해 설치하거나 네트워크 패키지 해석을 시도하지 않습니다. 적용할 Python 소스가
  0개이면 Mypy를 실행하지 않고 명시적 `SKIP` 대상과 `WARN`/`ESTIMATED`를 남깁니다. 따라서
  `Success: no issues found in 0 source files`도 실측 성공 문법으로 인정하지 않습니다.
- **C++**: 현재 C++ 타입 검증은 구현되어 있지 않습니다. C++ 소스가 발견되면 소스별 `SKIP`
  대상을 남기고 요약에 미구현 범위를 명시하며 `WARN`/`ESTIMATED`로 기록합니다. Python/C++ 혼합
  프로젝트도 C++ 검증 누락 때문에 전체 증거를 `MEASURED`로 승격하지 않습니다.
- 모든 Mypy 시도 및 미설치 상태는 `ToolEvidence`로 기록되며, rc>=2·파싱 실패 등 최종 도구
  오류 원인도 `error`에 보존됩니다. C++ skip은 Missing Annotations가 아닌 일반 type finding/
  warning 요약으로 표시됩니다.
- **노이즈 제로**: 오류가 없을 경우 개별 함수 통과 로그를 숨기고 `✅ Static Type Check Passed (0 Errors)` 단일 행으로 축약 요약

### 2.5 🧩 `complexity` (순환 복잡도 및 블록 중첩도)
- **Cyclomatic Complexity (CC)**: 조건문(`if`, `while`, `for`, `match`(케이스 guard 포함), 삼항 연산자, comprehension `if`, `and`/`or`)에 따른 선형 독립 경로 수 계산
  - $\text{CC} \le 15$: 양호 (PASS)
  - $15 < \text{CC} \le 25$: 주의 및 리팩토링 권고 (WARN)
  - $\text{CC} > 25$: 과도한 복잡도 (FAIL)
- **Max Nesting Depth**: 블록 중첩 깊이 $\ge 4$ 초과 시 경고
- **코드 스니펫**: 고복잡도 함수의 실제 원본 소스 코드를 추출하여 HTML 리포트에 즉시 표시

### 2.6 🛡️ `sanitize` (메모리 안전성 및 리소스 누수 진단)
- **C++**: AddressSanitizer(`-fsanitize=address`) 및 UndefinedBehaviorSanitizer(`-fsanitize=undefined`) 빌드/실행 검증
- **Python**: 열린 파일 핸들, 제너레이터 누수, 리소스 미해제 패턴 검출

### 2.7 💀 `dead` (죽은 코드 및 미사용 심볼)
- 도달할 수 없는 블록(`unreachable code`), 정의 후 참조되지 않는 비공개 함수 및 전역 상수 검출

### 2.8 📦 `dup` (코드 복제 및 중복률 감지기)
- **알고리즘**: 토큰 정규화(식별자/리터럴 치환) 슬라이딩 윈도우 해싱 + **최대 클론 병합**
  - **Type-2 클론 검출**: 변수명/리터럴만 다른 복사-붙여넣기도 동일 구조로 인식
  - 교차 파일은 갭 허용 블록 매칭(`SequenceMatcher`), 동일 파일은 비중첩 그리디 확장
  - 중복 라인 집계는 고유 라인 위치 합집합 기준 (과대 집계 방지)
  - 원본 인덴트와 줄바꿈을 완벽히 보존한 소스 코드 프리뷰 제공
- **평가 기준**: 전체 코드베이스 대비 중복 라인 비율이 `warn_pct`(5%) 초과 시 경고, `fail_pct`(15%) 초과 시 실패

### 2.9 ⚠️ `exception` (예외 처리 안전성 검출기)
- `except: pass` (예외 무시/삼킴 패턴) 검출
- `except BaseException:` (시스템 종료 신호 등 비정상 가로챔) 차단
- C++ 소멸자(`destructor`) 내부 throw 감지

---

> **다음 단계**: [⚙️ CI/CD 연동 가이드](ci-integration.md)에서 GitHub Actions 및 폐쇄망 러너 연동법을 확인하세요.
