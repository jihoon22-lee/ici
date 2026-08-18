# ici 검증 엔진 레퍼런스 (Engine Reference)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; **📏 검증 엔진 레퍼런스** &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 소프트웨어 공학적 품질과 보안을 보장하기 위해 9대 핵심 검증 엔진과 엔터프라이즈 도구 인터페이스를 제공합니다.

---

## 1. 품질 정책 설정 (`ici.toml`)

전사 공용 정책 또는 프로젝트별 기준을 `ici.toml`을 통해 중앙에서 관리합니다.

### 1.1 평가 모드 (`mode`)
각 엔진별로 결과 평가 방식을 설정할 수 있습니다:
- `"pass_warn_fail"`: FAIL 조건 시 FAIL, WARN 조건 시 WARN, 그 외 PASS (기본값)
- `"pass_fail"`: 경고(WARN)를 허용하지 않고 즉시 FAIL 처리
- `"pass_warn"`: 실패(FAIL) 없이 정보성 경고(WARN)로만 관리

```toml
[policy.line]
enabled = true
mode = "pass_warn"
warn_limit = 500
fail_limit = 1000

[policy.test]
enabled = true
mode = "pass_fail"
min_tem_score = 4.0
min_branch_cov = 80.0
min_func_cov = 90.0

[policy.complexity]
enabled = true
mode = "pass_warn_fail"
warn_cc = 15
fail_cc = 25
warn_nesting = 4

[policy.dup]
enabled = true
mode = "pass_warn"
min_window = 6
warn_pct = 5.0
fail_pct = 15.0
```

---

## 2. 9대 핵심 검증 엔진 상세

### 2.1 📏 `line` (코드 라인 및 파일 크기 분석기)
- **검증 규칙**:
  - 파일당 순수 코드(코드 라인) 기준으로 크기 과대화 진단
  - `warn_limit` (기본 500줄): 모듈 분리 검토 권고
  - `fail_limit` (기본 1000줄): 단일 파일 과대화 실패
- **출력 메트릭**: 코드 라인, 주석 라인, 공백 라인 수 및 디렉토리 계층 트리 ([HTML 뷰어 지원](user-guide.md#22-인터랙티브-html-리포트-생성-및-자동-브라우저-열기))

### 2.2 🧹 `lint` (문법 및 코드 스타일 린터)
- **Python**: `ruff check` (에러/버그) + `ruff format --check` (코드 포맷팅 일치 여부)
- **C++**: `g++ -Wall -Wextra -Werror` 컴파일 검사 + `clang-format` 정렬 검사

### 2.3 🧪 `test` & TEM 스코어링 (단위 테스트 및 테스트 효과성 지표)
- **동작**: 프로젝트 내 pytest 또는 C++ 테스트 바이너리를 실행하여 단위 테스트 전수 통과 여부 검증
- **모듈별 실측 커버리지 수집** (HTML `🧪 Tests & Coverage` 탭의 Module Coverage Table):
  - **Python**: 프로젝트 환경의 `coverage.py`가 있으면 `coverage run --branch`로 테스트를 재실행하고 `coverage json`을 파싱하여 파일별 Stmts/Miss/Cover/Branch 실측값 수집
  - **C++**: `gcov`가 있으면 `g++ --coverage` 2단계 컴파일(객체별 `-c` 후 링크)로 테스트를 빌드·실행하고 `gcov -b -p` 산출물을 파싱하여 동일한 파일별 테이블 생성 (테스트 파일 제외)
  - 둘 다 없으면 KPI는 추정치로 표시되며, HTML에 설치 안내가 노출됨
- **TEM (Test Effectiveness Metric) 5.0 만점 산출 공식**:
  $$\text{TEM} = \left( \frac{\min(80, \text{Branch Coverage})}{80} \right) \times \left( \frac{\text{Function Coverage}}{100} \right) \times 5.0$$
- **평가 기준**:
  - 모든 테스트 케이스 통과 필수
  - TEM 스코어 $\ge 4.0$, Branch Coverage $\ge 80\%$, Function Coverage $\ge 90\%$ 충족 시 PASS
  - 실측 커버리지가 있는 경우 Branch Coverage는 실측값(coverage.py/gcov)으로 대체됨
  - 커버리지 80% 미만 모듈은 `Coverage:Module` WARN 타깃으로 Issues 탭/PR 어노테이션에 노출

### 2.4 🏷️ `type` (정적 타입 안정성 검사기)
- **Python**: `mypy`를 통한 엄격한 정적 타입 검사 + AST 기반 함수 시그니처 분석
- **C++**: strict typing 컴파일 플래그 검사
- **노이즈 제로**: 오류가 없을 경우 개별 함수 통과 로그를 숨기고 `✅ Static Type Check Passed (0 Errors)` 단일 행으로 축약 요약

### 2.5 🧩 `complexity` (순환 복잡도 및 블록 중첩도)
- **Cyclomatic Complexity (CC)**: 조건문(`if`, `while`, `for`, `match`, 삼항 연산자)에 따른 선형 독립 경로 수 계산
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
- **알고리즘**: 연속 슬라이딩 윈도우 해싱 + **최대 클론 블록 병합(Maximal Clone Merging)**
  - 단순 윈도우 분할로 인한 중복 그룹 파편화 방지
  - 원본 인덴트와 줄바꿈을 완벽히 보존한 소스 코드 프리뷰 제공
- **평가 기준**: 전체 코드베이스 대비 중복 라인 비율이 `warn_pct`(5%) 초과 시 경고

### 2.9 ⚠️ `exception` (예외 처리 안전성 검출기)
- `except: pass` (예외 무시/삼킴 패턴) 검출
- `except BaseException:` (시스템 종료 신호 등 비정상 가로챔) 차단
- C++ 소멸자(`destructor`) 내부 throw 감지

---

> **다음 단계**: [⚙️ CI/CD 연동 가이드](ci-integration.md)에서 GitHub Actions 및 폐쇄망 러너 연동법을 확인하세요.
