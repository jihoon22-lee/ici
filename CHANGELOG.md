# CHANGELOG

모든 주요 변경 사항은 이 문서에 기록됩니다.
이 프로젝트는 [Semantic Versioning](https://semver.org/lang/ko/) 규약을 따릅니다.

---

## [Unreleased]

### Refactored
- **HTML 리포터 모듈화**: 1070줄 단일 파일 `src/ici/reporters/html.py`를 `html/report.py` + `html/sections/{summary,line,test,complexity,dup,issues}.py` + `html/utils.py` + `html/assets/{style.css,app.js}` + `html/assets_loader.py` 구조로 분해하고, `html_assets.py`는 하위 호환 shim으로 유지. Zero-CDN 인라인 동작은 `importlib.resources` 기반 로더로 보존하며, 신규 엔진 탭 추가 시 섹션 모듈만 추가하면 되도록 확장성을 확보했습니다. (`_get_status_theme` 등 레거시 헬퍼는 `html/__init__.py`에서 re-export)

### Fixed
- **프로젝트 정책 버전 싱크**: `ici.toml`의 `ici.version`을 패키지 `__version__`(`0.4.2`)과 동기화하고, 드리프트를 방지하는 `test_repository_ici_version_matches_package_version` 회귀 테스트를 추가했습니다.

## [0.4.2] - 2026-08-20

### Fixed
- **Ruff 0.15.17 mixed formatter summaries**: legacy `ruff format --check`
  output containing both `Would reformat:` paths and an `already formatted`
  suffix is parsed as a policy `WARN` with one location target per path.
- **Atomic Ruff formatter parsing**: malformed legacy output is rejected as
  `ERROR` without retaining partially parsed format targets.

## [0.4.1] - 2026-08-20

### Fixed
- **Ruff 0.15.17 formatter compatibility**: recognized Ruff `warning:` blocks on
  `check` and `format` stderr are retained as tool warnings instead of turning a
  valid lint/format result into `ERROR`.
- **Ruff formatter capability detection**: locally probes `ruff format --help` and
  uses the JSON formatter output when supported by Ruff 0.16+, while retaining the
  strict legacy `Would reformat:` grammar for older versions. Probe and validation
  failures remain `ERROR`/`NOT_RUN` with complete `ToolEvidence`.

## [0.4.0] - 2026-08-20

### Changed
- **CI 권한 분리 및 Action 공급망 고정**:
  - PR/main 검증 `verify` job은 `contents: read`만 사용하고 checkout의
    `persist-credentials`를 비활성화했으며, `GITHUB_TOKEN`·`--publish`·PR 댓글 쓰기를
    검증 경로에서 제거
  - `main` push에서 검증 성공 후에만 실행되는 `publish-main` job을 별도 구성하고
    `contents: write`를 해당 job에만 부여
  - checkout/setup-python/upload-artifact/setup-uv/release Action을 Node 24 릴리스의
    immutable 40자리 commit SHA로 고정
- **CI 및 사용자 문서 정합성 보강**:
  - 로컬·CI·폐쇄망이 같은 정책·결과 계약을 사용하되 OS/컴파일러/Python/도구 버전과
    실행 결과는 달라질 수 있음을 명시
  - Typer, 순차 엔진 실행과 예외 격리, PASS/WARN/FAIL/ERROR/SKIP, `ici.result/v2`,
    Rich `file://` 링크 및 6개 HTML 탭으로 아키텍처 설명을 현행화
  - PR은 Step Summary·annotation·JSON/HTML 아티팩트를 사용하고, trusted main 또는
    명시적 수동 실행에서만 `--publish`를 사용하도록 CI 가이드를 정정
  - 배포된 ZipApp은 오프라인 실행 가능하지만 빌드에는 사전 준비된 Python·wheel/cache
    또는 내부 미러가 필요하다는 폐쇄망 안내를 추가
- **제품 버전 및 릴리스 태그 안전성 보강**:
  - 제품 버전을 `ici.__version__`으로 단일화하고 CLI, `doctor --brief`, 기본 설정이 같은
    버전 값을 사용하도록 정리
  - 수동 릴리스의 `version_tag` 입력을 필수화하고 SemVer-like 형식과 패키지 버전의 정확한
    일치를 검증하여 브랜치 이름·이전 버전으로의 fallback을 제거
- **신규 CI 검증 기능 보류**:
  - Toolchain, CMake/qmake build adapter, compile DB, Python compatibility, ELF/ABI 및
    C++/Python 통합 엔진은 v0.4.0에 포함하지 않고 별도 미래 계획으로 남김
- **리포터·CLI 결과 계약과 출력 안전성 강화**:
  - suite 및 단독 엔진 JSON을 ici.result/v2로 통일하고 required/evidence/raw_output/extra/InspectionTarget의 snippet·metrics/전체 ToolEvidence를 보존하며, 기존 FAIL+ERROR 의미의 failed_count와 순수 error_count/skipped_count를 분리
  - HTML 위치 링크는 동적 JavaScript 인자 대신 escaped data-* 속성과 정적 delegated listener를 사용하고, ERROR/SKIP도 Issues 뷰와 상태 뱃지에 표시
  - Markdown 표·코드 fence·GitHub Actions annotation, Rich 콘솔 경로/요약을 문맥별 escaping 및 안전한 file URI로 보호
  - 모든 단독 엔진과 verify/build가 PASS/WARN=0, FAIL/ERROR=1, SKIP=2 종료 코드를 공유하고 ERROR를 성공 아이콘으로 출력하지 않음
- **build 엔진의 metadata·산출물 안전성 강화**:
  - top-level `[build.python].entrypoint`와 `pyproject.toml [project.scripts]`를 엄격히 검증하고, 모든 configured source directory의 non-symlink `.py` library·검증된 callable launcher·실제 C++ regular binary만 산출물로 인정
  - source tree를 변경하지 않고, destination/path symlink·충돌·복사 오류·unsafe metadata를 구조화된 `ERROR`/`NOT_RUN`으로 처리하며 산출물이 없으면 `FAIL`, 실제 산출물과 오류 없는 경우에만 env scripts 생성
  - CMake/qmake/Makefile descriptor에서는 generic g++를 호출하지 않고 adapter 필요 `ERROR`를 반환하며, descriptor 없는 C++는 정확히 하나의 `int main(...)`과 실제 regular binary를 확인
- **sanitize/dead/exception 엔진의 실행·분석 증거 강화**:
  - sanitize Python 검증은 Task 5와 동일한 대상 인터프리터의
    `-W error::ResourceWarning -m pytest -o addopts= tests`를 실행하고, 0개 테스트·pytest 부재·timeout·출력 절단·spawn/신호 종료·파싱 불가능한 성공을 `ERROR`/`NOT_RUN` 또는 명시적 선택 scope `SKIP`/`ESTIMATED`로 기록
  - C++ sanitizer 컴파일·실행 실패를 허위 `PASS`로 처리하지 않으며, 종료 코드와 무관한 ASan/UBSan 진단을 `FAIL`/`MEASURED`로 보존하고 실행 시 기존 `ASAN_OPTIONS`/`UBSAN_OPTIONS`에 leak/halt 정책을 추가
  - Python/C++ hybrid의 부분 scope는 `WARN`/`ESTIMATED`로 남기고, 적용 대상이 없는 프로젝트는 명시적 `SKIP`으로 표시
  - C++ sanitizer timeout·출력 절단은 `ERROR`/`NOT_RUN`, 완전한 ASan/UBSan 진단을 동반한 signal 종료는 `FAIL`/`MEASURED`, 진단 없는 signal 종료는 `ERROR`로 구분하며 테스트 외부 symlink를 제외하고 Windows drive/공백 ResourceWarning 경로와 라인을 보존
  - sanitizer는 `ERROR`/`SUMMARY` 또는 위치 있는 UBSan `runtime error` 서명만 실제 진단으로 인정하고, `test_*.py`/`*_test.py`를 모두 선택하며 전부 skipped/deselected인 pytest 실행은 측정 PASS로 승격하지 않음. configured C++ source/include와 기존 PYTHONPATH·WSL `/tmp` 환경을 실행에 전달
  - dead는 private module-level Python 함수 정의와 모듈 내·cross-module `from`/attribute 참조를 분리해 수집하며 package `__init__.py` 상대 import, source directory 우선순위, 동일 alias 복수 후보, 모든 statement-list의 unreachable 경로를 처리하고 decorator·`__all__`·메서드·중첩 callback 함수 오탐을 제외
  - exception은 명시적으로 import된 `builtins` alias만 인정하고 `del`, BoolOp/IfExp walrus, match capture, 복수 with context의 실행 순서와 transient handler binding을 보수적으로 처리하며, C++ 표준 raw prefix와 line-splice 주석을 마스킹. 기존 `BaseException`/traceback·destructor·구문상 비어 있는 catch 정책은 유지
  - 모든 엔진 설정 테이블이 공통 `required` boolean 정책을 사용하고, `sanitize`/`dead`/`exception` 단독 명령은 `ERROR`를 exit 1, `SKIP`을 exit 2로 반환
  - 선택 엔진(`required = false`)의 `FAIL`/`ERROR`/`SKIP` 및 `MEASURED`가 아닌 결과는 suite를 `WARN`으로 낮춰 허위 `PASS`를 방지하며, 필수 엔진의 `ERROR`/`FAIL` 우선순위는 유지
- **lint/type 실행 증거 및 도구 정책 강화**:
  - Ruff, Mypy, g++의 모든 실행 시도와 미설치 상태를 `ToolEvidence`에 기록하고 timeout·출력 절단·spawn/신호 종료·도구 크래시·잘못된 성공/진단 출력을 `ERROR`/`NOT_RUN`으로 분류
  - `[engines.lint].ruff_required`와 `[engines.type].mypy_required`를 추가해 필수 도구 누락은 오류로, 선택 도구 누락은 AST 부분 폴백 `WARN`/`ESTIMATED`로 표시
  - Mypy 종료 코드 `1`의 실제 타입 진단은 `mode` 정책을 따르고 `2` 이상은 진단 문자열이 있어도 도구 오류로 처리
  - Mypy 성공 출력은 마지막 단일 success summary 앞의 검증된 `note` 진단을 `WARN`으로 보존하며, 정크·오류 진단·잘못된 summary는 계속 `ERROR`로 처리
  - C++ lint는 발견된 각 소스의 g++ 문법 진단 위치를 안전하게 보존하며, type 엔진은 미구현 C++ 검증을 `SKIP`/`WARN`/`ESTIMATED`로 명시
  - Ruff/Mypy는 직접 실행 가능한 PATH 도구 또는 프로젝트 `.venv/bin`·`.venv/Scripts`만 사용하고 `uvx`/`uv run` 패키지 해석을 시도하지 않음
  - Ruff format의 빈 성공 출력, 위치 있는 C++ `note:` 보조 진단, Python 0-source Mypy skip을 명시적으로 처리하며, C++ skip을 Missing Annotations로 오표기하지 않음
  - rc>=2·파싱 실패를 포함한 최종 도구 오류 원인을 각 `ToolEvidence.error`에 보존
  - `type = "cpp"`의 빈 C/C++ 적용 범위도 명시적 `SKIP`/`WARN`/`ESTIMATED`로 표시하고, Python-only hybrid에는 불필요한 C++ skip을 추가하지 않음
  - 실제 g++ template context(`In instantiation of ...`, 위치 있는 `required from here`)만 제한적으로 허용하고 알 수 없는 문맥은 계속 도구 오류로 처리
- **테스트 실행·커버리지 증거 강화**:
  - 설정된 Python → 프로젝트 `.venv` → `sys.executable` 순으로 단일 인터프리터를 선택하고
    pytest/coverage/unittest를 모두 `-m` 모듈 호출로 실행
  - pytest 5 종료 코드와 0개 수집을 `FAIL`로 기록하고, 실행기·timeout·도구 오류는
    `ERROR`/`NOT_RUN`으로 분리
  - `coverage_required` 정책에서 Python coverage JSON 또는 C++ gcov 실측이 없거나 잘못되면
    통과를 금지하며, 선택적 커버리지는 `ESTIMATED`/`WARN`으로만 표시
  - 반복 실행 사이에 커버리지·도구 증거를 초기화해 이전 측정값이 재사용되지 않도록 보장
  - `python`/`cpp`/`hybrid` 소스별 테스트 시도를 기록하고 hybrid의 언어별 0개 테스트를 `FAIL`로
    표시하며, pytest 모듈 부재일 때만 동일 인터프리터의 unittest fallback을 허용
  - coverage JSON의 수량·라인 배열 일관성을 검증하고, 0 statement·probe/컴파일/실행 signal 오류와
    프로젝트 내부 pytest 임시 디렉토리 강제를 허위 측정·통과로 처리하지 않음
  - 소스·테스트가 모두 없는 빈 프로젝트도 generic zero-test `FAIL`과 누락 커버리지 증거로 기록하고,
    pytest가 collection만 보고 성공한 경우(per-test/terminal 결과 증거 없음) `ERROR`/`NOT_RUN`으로 분류
- **Dogfood 품질 게이트 유지보수성 개선**:
  - 프로세스 실행 및 lint/test/type 검증 흐름을 명시적·저복잡도 헬퍼로 분리해 CI 복잡도 임계값과 Mypy 타입 검사를 통과하도록 정리
- **서브프로세스 결과 신뢰성 강화**:
  - stdout/stderr를 동시 스트리밍하면서 설정된 상한 이후 데이터를 폐기해 대용량 출력이 메모리를 고갈시키지 않도록 개선
  - POSIX parent 종료 이후에도 descendant가 파이프를 보유하면 전체 monotonic deadline으로 process group을 종료하고 drain을 bounded cleanup
  - Windows는 `CREATE_SUSPENDED` 상태에서 stdlib ctypes Job Object를 먼저 할당한 뒤 primary thread를 resume하여 descendant race를 차단하고 핸들을 항상 닫음
  - Ruff, Mypy와 빌드/테스트/sanitize 엔진이 timeout·출력 절단·도구 출력 파싱 실패를 `ERROR`/`NOT_RUN`으로 기록
  - Mypy 성공 grammar와 coverage JSON의 필수 totals/files 구조를 엄격히 검증해 불완전한 도구 결과를 통과시키지 않음
- **서브프로세스 실행 제한 및 엔진 예외 격리**:
  - `run_process`가 구조화된 `ProcessResult`를 반환하고 기본 300초 timeout과 출력 상한(100만 문자)을 적용
  - POSIX 프로세스 그룹 종료와 Windows Job Object로 timeout 이후 자식 프로세스가 남지 않도록 정리
  - 개별 검증 엔진 예외를 `ERROR`/`NOT_RUN` 결과로 기록한 뒤 나머지 엔진 실행을 계속
- **증거 인식 결과 계약 및 검증 게이트 강화**:
  - `EngineResult`에 필수 엔진 여부(`required`), 증거 상태(`MEASURED`/`ESTIMATED`/`NOT_RUN`),
    도구 실행 증거(`ToolEvidence`)를 추가
  - 필수 검증 누락(`SKIP`/`NOT_RUN`)과 빈 검증 집합을 `ERROR`로 처리해 허위 `PASS` 방지
  - `pass_fail` 모드에서 경고(`WARN`)도 `FAIL`로 승격
- **Dogfood 테스트 게이트 baseline 보정**:
  - 실행기별 coverage 편차를 허용하되 strict `pass_fail` 의미는 유지하도록 프로젝트 정책 floor를
    TEM `2.0` / Branch `35%` / Function `60%`로 조정
  - 실제 테스트 실행 실패는 임계값과 무관하게 계속 `FAIL`로 처리
- **설정 계층 병합 및 스키마 검증 강화**:
  - 내장 기본값 → XDG 전역 → 프로젝트 `ici.toml`/`dev.toml` → `ICI_CONFIG` 순서로 모든 설정을
    결정적으로 깊게 병합
  - 알 수 없는 키, 잘못된 자료형·평가 모드·임계값, TOML 오류와 누락된 명시 파일을
    `ConfigError`로 보고하여 암묵적 기본값 폴백 방지
  - 과도하게 중첩된 배열·점 키로 발생하는 TOML 파서 재귀 오류도 `ConfigError`로 정규화하여
    CLI가 traceback 없이 종료 코드 `2`를 반환
  - 모든 CLI 엔진과 `verify` 오케스트레이터가 동일한 유효 설정을 사용
- **프로젝트 경계·메타데이터 파싱 강화**:
  - 소스 디렉토리와 재귀 파일 탐색에 canonical 경로 경계를 적용해 `..`·탈출 symlink를
    차단하고, 소스 내부 symlink 파일/디렉토리는 검사 대상에서 제외
  - `ici.toml` top-level 및 `pyproject.toml` `[project]` 메타데이터를 `tomli`로 파싱하고
    프로젝트 이름·버전의 경로 안전 문자를 검증

## [0.3.3] - 2026-08-18

### Added
- **Function Coverage 실측 (gcov 호출 기준)**:
  - 기존 하드코딩 추정치(95%/50%)를 실측으로 대체 — 함수 본문이 한 번 이상 실행되면 커버로 간주
  - Python: coverage.json `executed_lines` × AST 함수 라인 범위 교차 계산
  - C++: gcov 산출물의 `function ... called N` 라인 파싱
  - HTML `🧪 Tests & Coverage` 탭에 **Function Coverage Table** 추가 (함수별 실행 여부·위치·missing 라인, 미실행 함수에 호버 시 상세)
  - 측정 불가 환경에서만 추정치 폴백

## [0.3.2] - 2026-08-18

### Fixed
- **TEM 5.0 공식 정정**: 기존 `min(Branch,80)/80 * Func * 5`에서 사내 표준 공식으로 교체
  - Line Coverage 측정 가능 시: `min(LineCov, 80)/80 * FuncCov * PassRate * 5` (기본)
  - Branch만 측정 가능 시: `min(BranchCov*5/4, 80)/80 * FuncCov * PassRate * 5`
  - PassRate(테스트 통과율)를 TEM에 반영, HTML KPI 카드를 Line Coverage 기준으로 전환 (Branch는 폴백 표시)

## [0.3.1] - 2026-08-18

### Fixed
- **전역 `ici.toml` 자동 생성이 `verify`에서만 동작하던 문제**: CLI 콜백 레벨로 이동하여
  `doctor`/`line` 등 **어떤 명령이든 최초 실행 시** `~/.config/ici/ici.toml`이 생성되도록 수정

## [0.3.0] - 2026-08-18

### Added
- **소스 레이아웃 통합 (src 외 lib/app/packages/python 지원)**:
  - `core/project.py`에 `get_source_dirs()` 도입 — 기본 후보 `["src", "lib", "app", "packages", "python"]` 중 존재하는 디렉토리 + `ici.toml` `[project] source_dirs`로 오버라이드
  - `dup`/`complexity`/`dead`/`exception`/`sanitize`/`lint`/`type`/`test`/`build` 전 엔진 및 `detect_project_type`이 통합 헬퍼 사용 — 기존 `src/` 하드코딩 제거
  - `test` 엔진의 `PYTHONPATH`·`coverage --source`가 모든 소스 디렉토리 반영, `type` 엔진의 mypy 대상도 소스 디렉토리 기준
- **dup 엔진 Type-2 클론 검출 강화**:
  - 토큰 정규화(식별자→`ID`, 리터럴→`LIT`, 구조 키워드 보존)로 **변수명/리터럴만 다른 복사-붙여넣기 검출**
  - 교차 파일: `SequenceMatcher` 기반 갭 허용 블록 매칭 / 동일 파일: 비중첩 시드 + 그리디 확장
  - 중복 라인 집계를 **고유 라인 위치 합집합** 방식으로 전환해 과대 집계 방지, 최대 클론 우선 필터 강화
- **complexity 엔진 Python 보강**: `match` 케이스 guard, comprehension `if` 카운트 추가
- **coverage 모듈 레벨 프로브**: pytest와 **동일 인터프리터**의 `<python> -m coverage` 탐지 추가 — `.venv`가 공용 파이썬 site-packages를 상속하는 환경에서도 실측 테이블 생성 (`--version` 프로브로 검증)
- **공용 UV 경로 인식 (`find_uv()`)**: `$ICI_UV` → `~/.local/bin/uv` → `nas_shared/bin/uv` → `infra_root/bin/uv` → PATH 순 탐색, 전 엔진·doctor 연동
- **전역 `ici.toml` 최초 실행 자동 생성**: 설정 파일이 하나도 없을 때 `~/.config/ici/ici.toml`(XDG 존중)에 기본 정책 자동 생성 — 실패 시 무해하게 폴백
- **line 엔진 게이트/통계 분리**:
  - `[engines.line]`에 `gate_dirs`(기본 `src,include,lib,app`), `include_dirs`, `exclude_dirs` 추가
  - 임계값(500/1000) 검증은 게이트 디렉토리만 적용, tests/docs/scripts는 통계·트리 뷰에만 포함

### Fixed
- 콘솔 이슈 패널이 소스 스니펫의 `[...]` 문자를 Rich 마크업으로 오해석해 크래시하던 문제 — 동적 문자열 마크업 이스케이프 (`rich.markup.escape`)

## [0.2.0] - 2026-08-18

### Added
- **모듈별 실측 커버리지 테이블 (Module Coverage Table) — Python/C++ 동일 지원**:
  - `test` 엔진이 프로젝트 환경의 `coverage.py`(Python)와 `gcov`(C++, `g++ --coverage` 2단계 컴파일)로 **파일별 Stmts/Miss/Cover/Branch 실측값**을 수집 (기존 하드코딩 추정치 대체)
  - HTML `🧪 Tests & Coverage` 탭에 `coverage report` 형태의 **시각화된 모듈별 커버리지 표** 추가: 커버리지 낮은 순 정렬, 색상 임계값 바/미실행 라인 툴팁, 토탈 행
  - Branch KPI가 실측값으로 대체되어 TEM 스코어가 실제 품질을 반영, 커버리지 80% 미만 모듈은 `Coverage:Module` WARN으로 Issues 탭/PR 어노테이션 노출
  - 도구 부재 시 추정치 + 설치 안내로 폴백 (기존 동작 유지)
- **HTML 리포트 GitHub 배포 및 Sticky PR 코멘트 (`ici verify --publish`)**:
  - 생성된 `verify_report.html`을 GitHub Contents API로 `gh-pages` 브랜치에 푸시하는 퍼블리셔 엔진 추가 (`src/ici/engines/publish.py`)
  - **self 모드(기본)**: `GITHUB_TOKEN`만으로 자기 레포에 배포 — 추가 시크릿/외부 액션 불필요 (폐쇄망 GHES 호환)
  - **hub 모드(옵션)**: `ICI_PUBLISH_REPO`/`ICI_PUBLISH_TOKEN` 설정 시 중앙 리포트 허브 레포로 배포 (`<project>/pr/<n>/index.html`)
  - PR별 경로 네임스페이스(`pr/<n>/index.html`, `main/index.html`)로 다중 PR 동시 실행 시에도 충돌 없음
  - Pages 활성 여부를 매 실행마다 확인하여 스티키 PR 코멘트에 뷰어 링크 또는 1회성 Pages 설정 안내 표시 (마커 기반 갱신/생성)

## [0.1.0] - 2026-08-17

### Added
- **`ici` (Integrated CI) 단일 실행 ZipApp(`ici.pyz`) 아키텍처 구축**:
  - `shiv` + `scripts/launcher.sh` polyglot 프리앰블 결합을 통해 1.9MB 단일 파일 바이너리로 패키징
  - Python 3.10 하한 호환 및 시스템 인터프리터 자동 감지 (`$ICI_PYTHON`, `python3.14` ~ `python3.10`)
  - `build-pyz.sh` 재현 가능 빌드(Reproducible Build) 파이프라인 구축
- **전사 공용 표준 품질 게이트 설정 시스템 (`ici.toml` & `src/ici/config.py`)**:
  - `DEFAULT_CONFIG` 내장 표준 정책 및 `ici.toml` 중앙 정책 적용
  - 각 엔진별 `mode` (`pass_warn_fail`, `pass_fail`, `pass_warn`) 및 수치 기반 임계치(Thresholds) 설정 지원
- **9대 핵심 검증 엔진 및 커스텀 분석기**:
  - `line`: 500줄 초과 WARN / 1000줄 초과 FAIL 규칙 + 디렉토리 트리 데이터 추출
  - `lint`: `ruff check` + `ruff format --check` 및 C++ `g++` / `clang-format` 스타일 검사
  - `test`: 단위 테스트 전수 검증 + Branch/Function 커버리지 기반 TEM 5.0 스코어링 공식 산출
    $$\text{TEM Score} = \left( \frac{\min(80, \text{Branch Coverage})}{80} \right) \times \left( \frac{\text{Function Coverage}}{100} \right) \times 5.0$$
  - `type`: Mypy 정적 타입 분석 및 AST 어노테이션 검사 (노이즈 방지 0-Errors 요약 지원)
  - `complexity`: Cyclomatic 복잡도($> 15$ WARN, $> 25$ FAIL) 및 중첩 깊이($\ge 4$) 분석 + 원본 소스 코드 스니펫 추출
  - `sanitize`: C++ `-fsanitize=address,undefined` (ASan/UBSan) 및 Python 리소스 누수 검증
  - `dead`: 도달 불능 코드 및 미사용 심볼 검출
  - `dup`: 최대 클론 블록 병합(Maximal Clone Merging) 알고리즘 기반 코드 복제율 산출
  - `exception`: `except: pass` 에러 삼킴 및 소멸자 throw 차단
- **6개 전용 탭 인터랙티브 Zero-CDN HTML 대시보드 (`verify_report.html`)**:
  - **Tab 1 `📋 Verification Suites`**: 종합 상태 뱃지, TEM 게이지, 엔진 요약 및 각 전용 탭 원클릭 점프 버튼
  - **Tab 2 `📊 Line Analysis & Explorer`**: 전폭(Full-Width) 계층형 파일 트리 테이블 + 실시간 검색 필터 + 코드 분포 바
  - **Tab 3 `🧪 Tests & Coverage`**: 4대 커버리지 KPI 카드(TEM, Branch, Function, Pass Rate) + 파일별 테스트 스위트 및 개별 테스트 케이스 상세 뷰
  - **Tab 4 `🧩 Complexity`**: Top 15 복잡도 함수 리더보드 + **접고 펼칠 수 있는 소스 코드 블록 (Toggle All Code 지원)**
  - **Tab 5 `📦 Clone Groups`**: 연결 컴포넌트 클러스터링 기반 중복 코드 카드 + 원본 들여쓰기 보존 코드 블록
  - **Tab 6 `⚠️ Issues`**: 전체 엔진의 조치 필요(WARN/FAIL) 항목 통합 뷰 + **접고 펼칠 수 있는 문제 코드 스니펫 (Toggle All Code 지원)**
- **유니버설 에디터 연동 및 1-클릭 클립보드 복사 (`🛠️ Open With`)**:
  - 특정 에디터(VS Code) 강제 탈피: 드롭다운으로 `Copy Path (gvim/Vim/CLI)`, `VS Code`, `Cursor`, `PyCharm/IntelliJ`, `Sublime Text`, `Browser File` 중 선택 가능 (브라우저 `localStorage`에 상태 기억)
  - 모든 파일 위치 링크 옆에 빠른 `📋` 클립보드 복사 버튼 제공
  - 터미널 OSC 8 하이퍼링크 및 GitHub Step Summary permalink(`blob/...#L10`) 지원
- **GitHub Actions 개밥먹기(Dogfooding) CI & 자동 릴리스 파이프라인**:
  - `.github/workflows/ci.yml`: PR 생성 및 커밋 푸시 시 `dist/ici.pyz`를 빌드하여 `ici` 자체를 전수 검증하는 Dogfooding CI 게이트 및 리포트 아티팩트 업로드
  - `.github/workflows/release.yml`: 버전 태그(`v*.*.*`) 푸시 및 `workflow_dispatch` 수동 실행 시, `CHANGELOG.md`에서 해당 버전 노트를 자동 추출하여 `dist/ici.pyz` 바이너리와 함께 GitHub Release 발행
  - GitHub Ruleset(`ici-main-quality-gate`): CI 검증 미통과 시 `main` 브랜치 머지 원천 차단
- **빌드 및 환경 진단 도구**:
  - `ici build`: Python 바이트코드 컴파일(`compileall`), 릴리스 트리 패키징(`vX.Y.Z/x86_64/lib`), `env.sh` 및 `env.csh` 생성
  - `ici doctor`: glibc, WSL, 컴파일러, 린터, 파이썬 진단 테이블 출력
  - `ici env`: 셸 스크립트 소싱용 환경변수 스니펫 출력

### Removed
- **Coverity 및 SAM 엔진 제거**:
  - 프로젝트 경량화 및 9대 핵심 품질 게이트 집중을 위해 `cov` (Coverity) 및 `sam` (SAM) 엔진과 CLI 서브커맨드, 설정 스키마 전면 제거
