# ici 시스템 아키텍처 및 상세 설계 (System Architecture Guide)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; **🏛️ 시스템 아키텍처** &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

이 문서는 `ici` (Integrated CI)의 시스템 설계, 내부 아키텍처, 런타임 수명 주기, 엔진 파이프라인 및 확장 방법을 상세히 기술합니다.

---

## 1. 시스템 개요 및 하이레벨 구조

`ici`는 로컬 개발 머신, 사내 폐쇄망 서버, GitHub Actions 가상머신 등 어떠한 환경에서도 **동일한 실행 환경과 엄격한 품질 게이트**를 보장하기 위해 설계되었습니다.

### 1.1 컴포넌트 아키텍처 다이어그램

```text
+-----------------------------------------------------------------------------------+
|                                  CLI Layer (__main__.py)                          |
|    - CLI Parsing (argparse)      - Signal Handling        - Exit Code Mapping     |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                             VerifyOrchestrator Layer                              |
|    - Config Deep-Merge (ici.toml)                         - Engine Registry       |
|    - Concurrent Execution (ThreadPoolExecutor)            - TEM Scoring Engine    |
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
|  | - Color Table       |  | - 5 Dedicated Tabs  |  | - GitHub Step Summary      | |
|  | - OSC 8 IDE Links   |  | - Hierarchical Tree |  | - Sticky PR Comments       | |
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
│   ├── engine-reference.md          # 9대 검증 엔진 레퍼런스 및 수식
│   └── ci-integration.md            # GitHub Actions & 폐쇄망 CI 연동 가이드
├── scripts/                         # 빌드 및 런처 스크립트
│   ├── launcher.sh                  # ZipApp polyglot 프리앰블 런처
│   ├── build-pyz.sh                 # 재현 가능한 ZipApp 빌드 파이프라인
│   └── smoke.sh                     # 독립 환경 스모크 테스트
├── src/
│   └── ici/
│       ├── __init__.py              # 패키지 메타데이터 (v0.3.0)
│       ├── __main__.py              # CLI 엔트리포인트 및 서브커맨드 라우터
│       ├── config.py                # 전사 기본 정책(DEFAULT_CONFIG) 및 toml 로더
│       ├── core/                    # 코어 도메인 로직
│       │   ├── env.py               # 파이썬 탐색 및 시스템 환경 진단
│       │   ├── models.py            # EngineResult, InspectionTarget 데이터 모델
│       │   ├── project.py           # 소스 파일 탐색 및 프로젝트 루트 감지
│       │   └── runner.py            # 서브프로세스 격리 실행기
│       ├── engines/                 # 9대 표준 검증 엔진 + 퍼블리셔
│       │   ├── base.py              # BaseEngine 인터페이스 & evaluate_status()
│       │   ├── verify.py            # VerifyOrchestrator (검증 오케스트레이터)
│       │   ├── line.py              # 코드/주석/공백 분석 및 트리 구조 생성
│       │   ├── lint.py              # ruff/g++/clang-format 린터
│       │   ├── test.py              # 테스트 실행 & TEM 스코어링 (coverage.py/gcov 실측)
│       │   ├── type_check.py        # mypy & C++ 타입 안정성 검사
│       │   ├── complexity.py        # Cyclomatic & Nesting 복잡도 분석기
│       │   ├── sanitize.py          # ASan/UBSan & Python 누수 검증
│       │   ├── dead.py              # 미사용 심볼 & 데드코드 탐지기
│       │   ├── dup.py               # 연결 컴포넌트 클러스터링 기반 중복 감지기
│       │   ├── exception.py         # 예외 삼킴 및 소멸자 throw 방지
│       │   └── publish.py           # GitHub HTML 리포트 퍼블리셔 (gh-pages/hub)
│       └── reporters/               # 다중 리포터 계층
│           ├── console.py           # Rich 터미널 대시보드 & OSC 8 링크
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
  - `status`: `EngineStatus` (`PASS`, `WARN`, `FAIL`, `SKIP`, `INFO`)
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

- **`VerificationSuiteResult`**: 전체 검증 스위트의 최종 집계 결과입니다.
  - `suite_status`: 전체 최고 위험도 상태 (FAIL > WARN > PASS)
  - `results`: 각 엔진 결과 목록
  - `tem_score`: TEM 종합 품질 점수

### 4.2 오케스트레이터 및 병렬 실행 (`VerifyOrchestrator`)
- `VerifyOrchestrator`는 활성화된 엔진 목록을 확인하고, I/O 및 AST 파싱이 병렬로 처리될 수 있도록 `ThreadPoolExecutor`를 통해 검증 엔진을 병렬로 수행합니다.

---

## 5. 다중 리포터 계층 설계

모든 리포터는 동일한 `VerificationSuiteResult`를 소비하여 각 플랫폼에 최적화된 출력을 만듭니다:

1. **`RichConsoleReporter`**: 터미널 환경 최적화
   - ANSI 컬러 및 Rich 테이블/패널
   - 터미널 OSC 8 하이퍼링크 (`\033]8;;vscode://...\033\\`)를 통한 터미널 내 에디터 원클릭 점프
2. **`HtmlReporter`**: 브라우저 독립형 대시보드
   - Zero-CDN 인라인 CSS/JS 구조
   - 6개 전용 탭: `Summary`, `Line & Tree`, `Tests & Coverage`, `Complexity`, `Clone Groups`, `Issues`
   - 계층형 디렉토리 트리 뷰 (depth별 들여쓰기 및 언어별 아이콘)
   - 원본 소스 코드 블록 인스펙터
3. **`MarkdownReporter`**: CI/CD 파이프라인 최적화
   - GitHub Step Summary 테이블 및 TEM 게이지
   - GitHub Blob 영구 링크 (`blob/<sha>/file#L10-L25`)
   - PR 인라인 에러 어노테이션 (`::error file=...::`)
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
