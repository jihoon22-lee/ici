# ici 시스템 아키텍처 및 상세 설계 (System Architecture Guide)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; **🏛️ 시스템 아키텍처** &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

이 문서는 `ici` (Integrated CI)의 시스템 설계, 내부 아키텍처, 런타임 수명 주기, 엔진 파이프라인 및 확장 방법을 상세히 기술합니다.

---

## 1. 시스템 개요 및 하이레벨 구조

`ici`는 로컬 개발 머신, 사내 폐쇄망 서버, GitHub Actions 가상머신 등 서로 다른 환경에서 **같은 정책·결과 계약과 엄격한 품질 게이트**를 적용하도록 설계되었습니다. OS, 컴파일러, Python, 외부 도구의 가용성과 버전은 실행 증거로 보존되며 환경이 다르면 검증 결과도 달라질 수 있습니다.

### 1.1 컴포넌트 아키텍처 다이어그램

```text
+-----------------------------------------------------------------------------------+
|                                  CLI Layer (__main__.py)                          |
|    - CLI Parsing (Typer)         - Signal Handling        - Exit Code Mapping     |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                             VerifyOrchestrator Layer                              |
|    - Config Deep-Merge (ici.toml)                         - Engine Registry       |
|    - Sequential Engine Execution + Exception Isolation    - TEM Scoring Engine   |
+------------------------------------------+----------------------------------------+
                                           |
       +-----------------------------------+-----------------------------------+
       |                                   |                                   |
       v                                   v                                   v
+---------------+                   +---------------+                   +---------------+
|  Line Engine  |                   |  Test Engine  |                   |  Dup Engine   |
| (Line/Tree)   |                   | (Coverage/TEM)|        ...        | (Maximal Cln) |
+-------+-------+                   +-------+-------+                   +-------+-------+
        |                                   |                                   |
        +-----------------------------------+-----------------------------------+
                                           |
                                           | (InspectionTarget & EngineResult)
                                           v
+-----------------------------------------------------------------------------------+
|                                Multi-Reporter Layer                               |
|  +---------------------+  +---------------------+  +----------------------------+ |
|  | RichConsoleReporter |  |     HtmlReporter    |  |      MarkdownReporter      | |
|  | - Color Table       |  | - 9 Dedicated Tabs  |  | - GitHub Step Summary      | |
|  | - file:// Links     |  | - Hierarchical Tree |  | - Optional Trusted Publish | |
|  | - Summary Banners   |  | - Source Previews   |  | - Inline Annotations       | |
|  +---------------------+  +---------------------+  +----------------------------+ |
+-----------------------------------------------------------------------------------+
```

---

## 2. 코드베이스 디렉토리 구조

```text
ici/
├── dist/
│   └── ici.pyz                      # 1.9MB 단일 실행 ZipApp 산출물
├── docs/                            # 프로젝트 문서화
│   ├── architecture.md              # [본 문서] 시스템 아키텍처 및 내부 설계
│   ├── user-guide.md                # 사용자 가이드 및 빠른 시작
│   ├── engine-reference.md          # 검증 엔진 레퍼런스 및 수식
│   └── ci-integration.md            # GitHub Actions & 폐쇄망 CI 연동 가이드
├── scripts/                         # 빌드 및 런처 스크립트
│   ├── launcher.sh                  # ZipApp polyglot 프리앰블 런처
│   ├── build-pyz.sh                 # 재현 가능한 ZipApp 빌드 파이프라인
│   └── smoke.sh                     # 독립 환경 스모크 테스트
├── src/
│   └── ici/
│       ├── __init__.py              # 패키지 메타데이터 (동적 프로젝트 버전)
│       ├── __main__.py              # CLI 엔트리포인트 및 서브커맨드 라우터
│       ├── config.py                # 전사 기본 정책(DEFAULT_CONFIG) 및 toml 로더
│       ├── core/                    # 코어 도메인 로직
│       │   ├── env.py               # 파이썬 탐색 및 시스템 환경 진단
│       │   ├── models.py            # EngineResult, InspectionTarget 데이터 모델
│       │   ├── project.py           # 소스 파일 탐색 및 프로젝트 루트 감지
│       │   └── runner.py            # 서브프로세스 격리 실행기
│       ├── engines/                 # 표준 검증 엔진 + 퍼블리셔
│       │   ├── base.py              # BaseEngine 인터페이스 & evaluate_status()
│       │   ├── verify.py            # VerifyOrchestrator (검증 오케스트레이터)
│       │   ├── line.py              # 코드/주석/공백 분석 및 트리 구조 생성
│       │   ├── lint.py              # Ruff 및 g++ 문법 린터
│       │   ├── test.py              # 테스트 실행 & TEM 스코어링 (coverage.py/gcov 실측)
│       │   ├── type_check.py        # mypy/AST 타입 검사 (C++은 명시적 SKIP)
│       │   ├── complexity.py        # Cyclomatic & Nesting 복잡도 분석기
│       │   ├── sanitize.py          # ASan/UBSan & Python 누수 검증
│       │   ├── dead.py              # 미사용 심볼 & 데드코드 탐지기
│       │   ├── dup.py               # 연결 컴포넌트 클러스터링 기반 중복 감지기
│       │   ├── exception.py         # 예외 삼킴 및 소멸자 throw 방지
│       │   └── publish.py           # GitHub HTML 리포트 퍼블리셔 (gh-pages/hub)
│       └── reporters/               # 다중 리포터 계층
│           ├── console.py           # Rich 터미널 대시보드 & file:// 링크
│           ├── html.py              # 6개 전용 탭 Zero-CDN HTML 대시보드
│           ├── html_assets.py       # HTML 내장 CSS 및 JavaScript 자산 모듈
│           ├── markdown.py          # GitHub Actions Summary & PR 코멘트
│           └── json_rep.py          # JSON 리포트 직렬화기
├── tests/                           # Pytest 단위 테스트 스위트
├── AGENTS.md                        # 개발 규약, 브랜칭 전략, 커밋 룰
├── CHANGELOG.md                     # 변경 이력 및 릴리스 노트
└── ici.toml                         # 전사 공용 표준 품질 게이트 설정
```

---

## 3. ZipApp 패키징 및 Polyglot 런처 수명 주기

`ici`는 배포 단순화를 위해 단일 실행형 ZipApp(`ici.pyz`)으로 패키징됩니다.

```text
[ici 실행] 
    ↓
[scripts/launcher.sh 프리앰블 해석] (Bash/Sh 실행)
    ↓
[파이썬 인터프리터 탐색] ($ICI_PYTHON -> python3.14 ~ python3.10 -> 사내 표준 경로)
    ↓
[Python 실행 및 ZipApp 부팅] (shiv 런타임이 ~/.shiv 캐시에 압축 해제 후 진입)
    ↓
[src/ici/__main__.py:main() 진입]
```

### 3.1 결정론적 재현 빌드 (Deterministic Reproducible Builds)
`scripts/build-pyz.sh`는 다음 4단계 파이프라인을 거칩니다:
1. `uv pip install`로 Python 3.10 타깃 순수 파이썬(`py3-none-any`) 의존성만 격리 설치
2. `*.dist-info` 내 `direct_url.json`, `uv_cache.json`, `RECORD` 등 빌드 머신 의존 메타데이터 제거
3. `shiv`를 통해 ZipApp 아카이브 생성 (고정 타임스탬프 `SOURCE_DATE_EPOCH=1700000000` 적용)
4. 생성된 아카이브 앞단에 `scripts/launcher.sh` 셸 프리앰블 결합

---

## 4. 엔진 파이프라인 및 데이터 모델

### 4.1 핵심 데이터 모델 (`src/ici/core/models.py`)

- **`InspectionTarget`**: 검사 대상의 정밀 위치와 메트릭을 추적합니다.
  - `file_path`: 대상 파일 상대 경로
  - `start_line` / `end_line`: 소스 코드 라인 범위
  - `target_name`: 함수명, 클래스명, 또는 규칙 심볼명
  - `status`: `EngineStatus` (`PASS`, `WARN`, `FAIL`, `ERROR`, `SKIP`)
  - `message`: 진단 메시지
  - `snippet`: 원본 소스 코드 블록 (포맷팅 보존)
  - `metrics`: 세부 수치 데이터 (`complexity`, `nesting`, `duplicate_lines` 등)

- **`EngineResult`**: 단일 검증 엔진의 종합 결과입니다.
  - `engine_name`: 엔진 식별자 (`line`, `lint`, `test`, ...)
  - `status`: 엔진 종합 평가 상태
  - `summary`: 인간 친화적 한 줄 요약
  - `score` / `max_score`: 점수 (예: TEM 4.75 / 5.0)
  - `targets`: 세부 `InspectionTarget` 목록
  - `extra`: 엔진별 추가 구조화 데이터 (`files_data`, `top_complex_funcs`, `clone_groups` 등)
  - `required`: 결과가 품질 게이트에 필수인지 여부
  - `evidence`: 결과가 실측(`MEASURED`), 추정(`ESTIMATED`) 또는 미실행(`NOT_RUN`)인지 여부
  - `tool_evidence`: 호출한 외부 도구의 경로·인자·버전·종료 상태·오류 증거
  - `findings`: v3의 안정적인 issue/inventory 목록. legacy `targets`도 adapter를 통해 전부
    finding으로 노출되며 native finding이 같은 fingerprint를 제공하면 더 풍부한 native 정보가
    우선합니다.

- **`Finding`**: 도구 출력 형식과 독립적인 분석 결과입니다.
  - `rule_id`, `category`, `severity`, `confidence`: ici가 소유하는 안정적인 분류
  - `fingerprint`: canonical project-relative path와 symbol 또는 region을 결합한 SHA-256 identity
  - `primary_location`, `related_locations`: 1-indexed line/column을 가진 위치
  - `message`, `explanation`, `remediation`, tool identity, suppression 근거
  - `metrics`: 숫자 값과 단위를 분리한 측정치. 문자열형 보조 정보는 finding metric으로 승격하지 않습니다.

- **`VerificationSuiteResult`**: 전체 검증 스위트의 최종 집계 결과입니다.
  - `suite_status`: 전체 집계 상태. 필수 `ERROR`/`SKIP`/`NOT_RUN`은 `ERROR`, 필수 `FAIL`은 `FAIL`, 경고 또는 선택 검증의 비측정 결과는 `WARN`으로 집계합니다.
  - `results`: 각 엔진 결과 목록
  - `tem_score`: TEM 종합 품질 점수
  - JSON writer는 `ici.result/v3`를 사용합니다. 기존 `targets`와 엔진·도구 증거를 보존하면서
    `findings`를 추가하므로 점진적으로 소비자를 전환할 수 있습니다. v2 archive는 migration
    helper와 v2/v3 겸용 viewer로 계속 읽을 수 있습니다. 기계 검증 계약은
    [`ici-result-v3.schema.json`](../src/ici/schemas/ici-result-v3.schema.json)입니다.

### 4.2 오케스트레이터 및 예외 격리 (`VerifyOrchestrator`)
- `VerifyOrchestrator`는 활성화된 엔진을 정의된 순서로 순차 실행합니다. 개별 엔진에서 예외가 발생해도 해당 엔진을 `ERROR`/`NOT_RUN`으로 기록하고 나머지 엔진을 계속 실행하여 결과 계약을 완성합니다.
- 단독 명령과 전체 검증은 `PASS`/`WARN`은 0, `FAIL`/`ERROR`는 1, `SKIP`은 2를 반환하는 공통 종료 코드 계약을 사용합니다.

---

## 5. 다중 리포터 계층 설계

모든 리포터는 동일한 `VerificationSuiteResult`를 소비하여 각 플랫폼에 최적화된 출력을 만듭니다:

리포터에 도달하기 전과 개별 reporter API 경계에서 공통 redaction copy를 만들며 원본 분석
객체는 변경하지 않습니다. message, snippet, raw output, tool argv/error, extra, finding 설명·개선안과
suppression reason에 포함된 credential은 마스킹되고, 탐색에 필요한 파일 경로는 유지됩니다.

1. **`RichConsoleReporter`**: 터미널 환경 최적화
   - ANSI 컬러 및 Rich 테이블/패널
   - 안전한 `file://` URI와 Rich 링크 마크업을 통한 터미널 파일 위치 원클릭 이동
2. **`HtmlReporter`**: 브라우저 독립형 대시보드
   - Zero-CDN 인라인 CSS/JS 구조
   - 9개 전용 탭: `Summary`, `Line & Tree`, `Tests & Coverage`, `Static Types`, `Complexity`, `Clone Groups`, `Cycles`, `Security & Resources`, `Issues`
   - 계층형 디렉토리 트리 뷰 (depth별 들여쓰기 및 언어별 아이콘)
   - 원본 소스 코드 블록 인스펙터
3. **`MarkdownReporter`**: CI/CD 파이프라인 최적화
   - GitHub Step Summary 테이블 및 TEM 게이지
   - GitHub Blob 영구 링크 (`blob/<sha>/file#L10-L25`)
   - PR 인라인 에러 어노테이션 (`::error file=...::`); 별도 신뢰 job이 아티팩트로 sticky 댓글과 HTML 링크를 게시
4. **`JsonReporter`**: 데이터 파이프라인 연동
   - `verify_report.json` 포맷 직렬화

---

## 6. 새로운 검증 엔진 확장 가이드

새로운 검증 엔진을 추가하려면 다음 단계를 따릅니다:

1. `src/ici/engines/` 아래에 `BaseEngine`을 상속받는 클래스 작성:
   ```python
   from ici.core.models import EngineResult, EngineStatus
   from ici.engines.base import BaseEngine


   class SecurityEngine(BaseEngine):
       def run(self) -> EngineResult:
           cfg = self.get_config("security")
           # 검증 로직 수행...
           return self.create_result(
               name="security",
               status=EngineStatus.PASS,
               summary="Security scan clean",
               targets=[],
           )
   ```
2. `src/ici/config.py`의 `DEFAULT_CONFIG["engines"]`에 기본 정책 추가
3. `src/ici/engines/verify.py`의 `engine_defs` 목록에 등록
4. `tests/`에 단위 테스트 추가

---

> **기여 및 개발 규약**: [📜 AGENTS.md](../AGENTS.md)에서 브랜칭 전략과 커밋 규약을 확인하세요.
