# CHANGELOG

모든 주요 변경 사항은 이 문서에 기록됩니다.
이 프로젝트는 [Semantic Versioning](https://semver.org/lang/ko/) 규약을 따릅니다.

---

## [Unreleased]

### Changed
- **ici 자신의 PR 댓글이 두 리포트를 함께 보여줍니다**: 이 저장소는 루트의 Python 패키지와 `viewer/` 의 C++ 프로젝트를 둘 다 검증하는데, 댓글에는 앞의 것만 나와 게이트의 절반이 보이지 않았습니다. `report-pr` 잡을 `--report-dir ici=. --report-dir viewer` 로 전환했습니다.
  - `publish-main` 은 그대로입니다. 그 잡은 main 에서 verify 를 재실행해 `--publish` 로 인라인 게시하므로, 뷰어까지 담으려면 C++ 게이트 재실행이나 아티팩트 소비 방식으로의 전환이 필요합니다. 후자가 더 싸지만 `test_purity.py` 가 고정한 토큰 격리 의도를 손대게 되어 따로 다룹니다.

### Added
- **`ici publish --report-dir` — 모노repo의 여러 프로젝트 리포트를 하나의 sticky 댓글로**: 서브프로젝트 디렉터리를 반복 지정하면 각 `<dir>/verify_report.{html,json}` 을 **디렉터리 이름으로 네임스페이스된 gh-pages 경로**에 게시하고, 프로젝트별 행과 링크를 담은 **댓글 하나**를 남깁니다.
  - 그 전에는 모노repo 지원이 두 지점에서 막혀 있었습니다. (1) self 모드의 게시 경로에 프로젝트 접두사가 없어(`prefix = ""`) 모든 프로젝트가 같은 `pr/<N>/index.html` 에 써서 마지막 것만 남았고, (2) sticky 마커가 `<!-- ici-report -->` 하나로 고정이라 두 번째 publish 가 첫 번째 댓글을 덮어썼습니다.
  - 업로드는 의도적으로 순차 실행합니다. Contents API 는 덮어쓰기에 현재 blob sha 가 필요해서, 병렬로 같은 브랜치에 쓰면 경쟁하다 하나가 유실됩니다.
  - 단일 프로젝트 동작은 그대로입니다 — 라벨이 없으면 경로도 댓글도 이전과 동일합니다.
  - `label=path` 형식도 받습니다. 저장소 루트는 디렉터리 이름이 `.` 이라 경로 조각으로 쓸 수 없으므로 `--report-dir ici=.` 처럼 이름을 명시합니다.
  - 이 저장소의 워크플로 자체를 바꾸는 것은 후속 작업입니다. `report-pr` 잡은 **base(main) 소스를 체크아웃해 pyz 를 빌드**하므로 — PR 코드가 쓰기 토큰에 닿지 않게 하려는 의도된 설계입니다 — 새 플래그를 추가하는 PR 은 자기 CI 에서 그 플래그를 쓸 수 없습니다. 머지되어 main 에 들어간 뒤에야 가능합니다.

## [0.5.2] - 2026-08-27

### Added
- **`project.cpp_pkg_config` — C++ 컴파일 플래그를 설정으로 주입**: 나열한 pkg-config 패키지의 `--cflags` 가 C++ 컴파일 플래그에 추가됩니다. 그 전에는 `get_all_cpp_includes()` 가 `include/` 와 `<source_dir>/include` 만 보았기 때문에, Qt 같은 툴킷을 쓰는 소스는 **파싱조차 되지 않아** 검증 대상에서 통째로 빠질 수밖에 없었습니다. 경로를 설정 파일에 박으면 다른 머신에서 깨지므로, 프로젝트는 패키지 이름만 선언하고 경로는 호스트가 제공합니다.
- **`project.cpp_external_build_dirs` — 분석은 하되 컴파일은 하지 않는 디렉터리**: `Q_OBJECT` 클래스는 moc 가 생성한 소스가 있어야 링크되고, CMake 로 구동되는 코드는 생성 헤더가 필요합니다. 맨 `g++` 호출로는 만들 수 없는 그런 소스를 여기에 선언하면, 바이너리를 만드는 엔진(`test`/`sanitize`/`build`)만 건너뛰고 **텍스트·AST 기반 엔진은 그대로 읽습니다.**

### Fixed
- **`lint` 가 설정을 무시하고 있었다**: `get_all_cpp_includes(self.project_root)` 를 config 인자 없이 호출해, 설정된 include 경로가 컴파일러에 전달되지 않았습니다.

### Changed
- **`viewer/gui/` → `viewer/src/gui/`**: GUI 도 프로젝트 소스이므로 `src/` 아래로 옮겼습니다. `src/` 옆에 따로 두는 배치는 관례도 아니고, 무엇보다 **"검증할 필요 없는 코드"라는 잘못된 신호**를 줍니다. 위 두 설정으로 이제 GUI 가 `lint`·`line`·`complexity`·`dup`·`exception`·`cycle`·`security` 의 검증을 받습니다 — 검증 엔진 **0개에서 8개로**. 실측(`viewer/`): 코드 라인 1,020 → 1,378, 측정 함수 96 → 118개, GUI 소스 3개에 대해 Qt 플래그를 포함한 g++ 진단이 실제로 실행됩니다.
  - GUI 진입점은 `src/main.cpp` 와 `int main` 이 겹치지 않도록 `gui_main.cpp` 로 이름을 바꿨습니다.
  - CI 의 verify job 에 `qt6-base-dev` 설치를 추가했습니다. GUI 를 빌드하기 위해서가 아니라 **파싱하기 위해서**입니다.

### Added
- **`viewer/gui/` — 리포트 뷰어의 Qt6 셸**: `icirv-gui [report.json]` 으로 실행합니다. 화면에서 가장 큰 글씨가 `gateReason()` 결과입니다 — 콘솔이 `Error: 0` 을 출력하면서 스위트는 `ERROR` 인 상황(요약 카운트는 엔진 상태를 세고, 스위트 상태는 `aggregate_suite_status` 의 별도 규칙으로 정해지는데 그 규칙이 출력에 없음)을 문장으로 설명하는 것이 이 앱의 존재 이유이기 때문입니다.
  - 엔진 → 타깃 2단계 트리, "Issues only" 토글(정상 실행에서는 타깃 대부분이 PASS라 기본값), 엔진 행 툴팁에 `evidence` 와 `required` 표시(MEASURED 와 ESTIMATED 는 결과를 얼마나 믿을 수 있는지에 대해 전혀 다른 의미입니다), 타깃 더블클릭 시 파일 열기.
  - 스키마 불일치나 손상된 리포트는 조용히 빈 창을 띄우지 않고 사유를 표시합니다.
  - 코어는 Qt 무의존을 유지하므로 CI 의 C++ 게이트에는 영향이 없습니다. GUI 는 코어를 평범한 정적 라이브러리로 링크합니다.
- **CI 에 `viewer-gui` 잡 추가**: Qt6 를 설치해 빌드하고, `QT_QPA_PLATFORM=offscreen` 으로 실제 리포트를 열어 헤드리스 실행합니다. `gui/` 는 모든 엔진의 스코프 밖이라 이 잡이 없으면 **깨진 GUI 빌드를 아무것도 잡지 못합니다.**

### Fixed
- **C++ 함수 경계 탐지가 세 가지 방식으로 어긋나 있었다 (`complexity`)**: 기존 구현은 `(`, `)`, `{` 와 몇 개의 반환 타입 키워드(`int `/`void `/`bool `/`auto `/`double `)가 한 줄에 같이 있으면 함수 정의로 간주했습니다. 그 결과:
  - **한 줄 정의가 닫히지 않았습니다.** `void Stats::add(const T& r) { v_.push_back(r); }` 같은 정의는 시그니처 줄의 중괄호를 세지 않고 넘어가므로 영영 닫히지 않고 **뒤따르는 함수들의 본문을 흡수**했습니다. 실측: 한 줄짜리 `spanPrecedes()` 에 중첩 깊이 4 가 붙었는데, 실제로는 그 아래 함수의 값이었습니다.
  - **`for (int i = 0; i < n; ++i) {` 가 함수로 잡혔습니다.** 괄호·중괄호·`int ` 가 모두 있기 때문입니다. `for` 라는 이름의 유령 함수가 생기고 진짜 함수는 그 줄에서 잘렸습니다.
  - **여러 줄에 걸친 시그니처는 아예 탐지되지 않았습니다.** 본문이 앞 함수에 귀속됐습니다.
  - 이제 중괄호 깊이를 추적하고 시그니처를 여는 중괄호까지 누적해 판정하며, `(` 앞 토큰이 제어 키워드면 함수가 아닌 것으로 처리합니다. 문자열·주석은 `mask_cpp_literals` 로 중립화해 리터럴 안의 `if (` 나 `&&` 가 분기로 세어지지 않습니다.
  - **영향**: 탐지되는 함수 수가 실측 프로젝트에서 크게 늘었습니다 — `loglens` 44 → 93, `viewer` 72 → 96, `diskmap` 33 → 51. 절반 가까운 함수가 측정 대상에서 빠져 있었다는 뜻입니다. 거짓 경고가 사라진 대신 가려져 있던 진짜 중첩 위반이 드러납니다.
  - C++ 경로에는 테스트가 하나도 없었습니다. 세 결함 각각에 대한 회귀 테스트와 리터럴·중첩 측정 테스트를 추가했습니다.

### Refactored
- **`mask_cpp_literals` 를 `engines/cpp_text.py` 로 분리**: `build` 와 `exception` 이 각자 구현을 갖고 있었고 `complexity` 가 세 번째를 추가할 참이었습니다. `build` 쪽 구현(raw string·블록 주석까지 처리)을 공용 모듈로 옮기고 `build` 와 `complexity` 가 함께 씁니다. `exception` 의 구현은 line-splice 처리가 달라 이번에는 건드리지 않았습니다.

### Changed
- **`viewer/` 를 관례적인 C++ 레이아웃으로 재배치**: 공개 헤더를 `viewer/include/icirv/`, 구현을 `viewer/src/` 로 분리했습니다. ici 의 `get_all_cpp_includes()` 가 `include/` 와 그 하위 디렉터리를 `-I` 로 넘겨주므로, 테스트가 쓰던 `#include "../src/core/json_parser.hpp"` 같은 상대 경로가 `#include "icirv/json_parser.hpp"` 로 정리됐습니다. 검증 결과는 동일합니다(exit 0, TEM 4.94).
- **CI 가 Python 과 C++ 검증 리포트를 모두 제공**: `viewer/` 게이트 스텝에 `--html` 과 `--github-summary` 를 추가했습니다. `--github-summary` 는 `$GITHUB_STEP_SUMMARY` 에 append 하므로 Actions 실행 요약에 두 결과가 나란히 남고, 아티팩트에도 `viewer/verify_report.{html,json}` 이 함께 담깁니다. 그 전에는 Python 자체 검증 리포트만 업로드돼 C++ 검증 결과를 볼 방법이 없었습니다.
  - 두 리포트를 **하나로 합치는** 것은 아직 불가능합니다. `source_dirs` 에 `viewer/src` 를 넣으면 C++ 소스는 잡히지만 (1) `engines/test.py` 가 C++ 테스트를 `<root>/tests` 에서만 찾아 `viewer/tests` 를 보지 못하고, (2) `get_all_cpp_includes()` 가 `<source_dir>/include` 만 보므로 `viewer/include` 를 `-I` 에 넣지 못합니다. 두 제약을 걷어내는 것은 별도 작업입니다.

### Added
- **`viewer/` — ici 리포트 네이티브 뷰어의 C++17 코어와 CLI(`icirv`)**: `ici verify --report` 가 만드는 `ici.result/v2` JSON 을 읽어 게이트 사유·엔진 표·조치 필요 항목을 출력합니다. 손으로 작성한 재귀 하강 JSON 파서(`json_value`/`json_parser`), 스키마 검증 매퍼(`report_model`), 파생 뷰(`summary`) 로 구성되며 외부 의존성이 없습니다.
  - `gateReason()` 이 이 뷰어의 존재 이유입니다. ici 콘솔은 `Error: 0` 을 출력하면서 `suite_status` 는 `ERROR` 일 수 있는데, 요약 카운트는 엔진 상태를 세는 반면 스위트 상태는 `aggregate_suite_status` 의 별도 규칙(required 엔진의 SKIP / evidence NOT_RUN 이 스위트를 승격)으로 정해지고 그 규칙이 출력 어디에도 없기 때문입니다. `gateReason()` 은 그 규칙을 재현해 `"ERROR — required engine 'dead' was SKIPPED"` 처럼 사유를 문장으로 돌려줍니다.
  - 스키마 불일치·필수 필드 누락·타입 불일치는 조용히 기본값으로 넘어가지 않고 `LoadError` 로 명시됩니다.
- **CI 에 C++ 게이트 추가**: `viewer/` 를 대상으로 `ici verify` 를 실행하는 스텝을 넣었습니다. 그 전까지 ici 의 C++ 경로(`lint` 의 g++ 진단, `test` 의 gcov 커버리지, `sanitize` 의 ASan/UBSan, `cycle` 의 include 순환)는 **단위 테스트로만 덮여 있었고 실제 C++ 프로젝트로 검증된 적이 없었습니다.** 코어가 Qt 무의존이라 CI 에 Qt 설치가 필요 없습니다.
  - `viewer/` 는 기본 source_dir 이 아니고 ici 의 `tests/` 아래도 아니며 루트에 빌드 디스크립터를 추가하지도 않으므로, ici 자체 검증 결과는 변하지 않습니다(확인: TEM 4.72 유지).
  - 측정: 12 엔진 중 9 PASS, exit 0, TEM 4.94 / line 95.0% / branch 85.2% / function 98.9%.

### Fixed
- **CI 에서 `lint` 엔진이 한 번도 실제로 실행되지 않고 있었다**: 워크플로의 린트 단계는 `uvx ruff check .` 를 쓰는데, `uvx` 는 ruff 를 임시로 내려받아 실행할 뿐 `PATH` 에 남기지 않습니다. ruff 는 dev 의존성에도 없어 `.venv` 에도 설치되지 않았습니다. 그 결과 뒤이은 도그푸딩 단계에서 `_find_ruff_command()` 가 ruff 를 찾지 못해 `lint` 가 AST 문법 폴백으로 강등됐고, **검사 대상을 하나도 보고하지 않은 채**(`targets: []`) `evidence = ESTIMATED` / `WARN` 으로 게이트를 통과했습니다. 개발자 로컬에는 ruff 가 전역 설치돼 있어 이 차이가 드러나지 않았습니다.
  - `ruff>=0.16,<0.17` 을 dev 의존성으로 선언해 `.venv` 에 설치되도록 하고(엔진의 `find_project_executable` 경로가 이를 찾습니다), CI 린트 단계를 `uv run` 으로 바꿔 엔진과 CI 가 같은 바이너리를 쓰도록 통일했습니다. format 규칙이 마이너 버전에서 바뀌면 `--check` 가 갑자기 깨지므로 상한을 둡니다.
  - 저장소 정책 `ici.toml` 의 `engines.lint.ruff_required` 를 `true` 로 올렸습니다. 이제 ruff 를 찾지 못하면 `lint` 가 `ERROR`(evidence `NOT_RUN`)가 되고 스위트가 `ERROR`, `verify` 는 exit 1 로 끝납니다. 배포 기본값(`config.py` DEFAULT_CONFIG)은 기존대로 `false` 이므로 사용자 프로젝트의 동작은 바뀌지 않습니다.
  - 확인: 수정 후 자체 검증에서 `lint` 가 `evidence = MEASURED` 로 보고됩니다(이전 CI: `ESTIMATED`).

## [0.5.1] - 2026-08-26

### Fixed
- **C++ 브랜치 커버리지 대폭 과소 집계 (`test` / `coverage_support.parse_gcov_dir`)**: gcc 는 예외를 던질 수 있는 거의 모든 호출(예외 활성 상태에서는 사실상 모든 STL 할당) 주위에 `(throw)` 로 표시된 분기 arm 을 추가로 방출합니다. 이는 사람이 작성한 분기가 아니라 예외 unwind 엣지이며, `bad_alloc` 등을 인위적으로 일으키지 않는 한 어떤 테스트로도 탈 수 없습니다. 기존 파서는 이 arm 들을 `taken 0%` 로 보고 미커버로 집계해 C++ 브랜치 커버리지를 실제보다 약 20%p 낮게 보고했습니다.
  - 실측(외부 C++ 프로젝트 `diskmap`, 5개 테스트 바이너리 / 총 338 분기): **67.8% → 88.4%**. 특히 `treemap.cpp` 는 `never executed` 분기가 **0개**, 즉 모든 분기점에 도달했는데도 73.1% 로 보고되고 있었습니다.
  - 이제 `(throw)` arm 은 분자·분모 양쪽에서 제외됩니다. `taken at least once` 라는 기존 판정 기준은 그대로 유지하므로 신호는 보존됩니다 — 같은 프로젝트에서 에러 경로가 실제로 덜 검증된 `fs_source.cpp` 는 65.8% 로 남습니다. lcov 2.x 가 동일한 엣지를 필터링하는 것과 같은 접근입니다.
  - 영향: 이 버그로 인해 C++ 프로젝트가 기본 임계값(`min_branch_cov = 80`)을 넘기지 못해 사용자가 임계값을 낮추도록 유도되고 있었습니다. 회귀 테스트 `test_parse_gcov_dir_excludes_exception_unwind_arms` 추가.

## [0.5.0] - 2026-08-24

### Added
- **리소스 누수 (`resource`)**: `open()` 후 close 누락, 가변 기본 인자 등 리소스 누수 AST 패턴을 탐지.
- **보안 위생 (`security`)**: 하드코딩 시크릿, 프라이빗 키, `hashlib.md5/sha1`, `random`, `eval/exec`, `pickle`, `shell=True` 등을 정규식으로 탐지. `scan_tests` 설정 지원.
- **인지 복잡도 (`cognitive`)**: SonarQube S3776 스타일 인지 복잡도를 함수별로 계산. 중첩 깊이에 따라 가중치를 더함. `warn/fail/warn_nesting` 설정 지원. 자체 검증 baseline 대비 오탐을 줄이기 위해 **기본 비활성(`enabled = false`)**, 임계값은 warn 30 / fail 60으로 조정.
- **순환 참조 탐지 (`cycle`)**: Python `import` 그래프와 C++ `#include` 그래프를 Tarjan SCC로 분석해 순환을 탐지. `max_reported` 설정 지원.
- **PR sticky 리포트 댓글 복원 (`report-pr` + `ici publish`)**: v0.4.0 권한 분리 이후 중단됐던 PR 리포트 댓글을 아티팩트 기반으로 재도입. 검증 job은 계속 읽기 전용이고, 새 `report-pr` job(`pull_request` 전용, `contents:write`+`pull-requests:write`)이 업로드된 `verify_report.html/json`을 받아 gh-pages에 게시하고 `<!-- ici-report -->` 마커로 sticky 댓글을 갱신합니다. 댓글은 배지형 링크·통계 표·접을 수 있는 엔진 상세로 리디자인됐습니다. 신규 CLI `ici publish --html --json`으로 기존 리포트를 단독 게시할 수 있습니다.

### Changed
- **HTML 리포트 UI/UX 개선 (8탭 재구성)**:
  - 탭 구조를 성격별로 재편: `📋 Summary · 📏 Line · 🧪 Tests · 🧩 Complexity(+🧠 cognitive 통합) · 📦 Clones · 🔁 Cycles · 🔐 Security & Resources · ⚠️ Issues`.
  - **Line 듀얼 모드**: 엔진은 프로젝트 전체를 스캔하되 기본 표시·게이트는 소스 스코프만. Line 탭의 "All files" 토글로 전체 프로젝트 파일 트리·차트·Top5 조회.
  - **Cycles 독립 탭**: 순환 체인을 칩(chip)+화살표 유연 레이아웃으로 시각화, 전체 경로는 접기로 제공.
  - **Tests & Coverage 압축**: 커버리지 테이블을 디렉터리별 접기 그룹(문제 폴더만 자동 펼침), 함수 커버리지 테이블 접기, 테스트 스위트는 실패 케이스만 항상 표시하고 통과 케이스는 한 줄 요약+접기, "Toggle All Cases" 일괄 토글 지원.
  - "Engines Run" 카드 Pass/Warn/Fail/Error/Skip **헬스 바**, N/A(SKIP) 엔진 회색 접힘 행은 유지.
- **`line` 소스 전용 게이트 + 전체 스캔 병행**: 임계값 판정은 소스 디렉터리(`src/include/lib/app` + 설정 추가 경로)에서만 수행하고, `include_dirs`는 재정의가 아닌 **추가** 동작으로 변경. 전체 프로젝트 수치는 `extra.all`로 별도 집계.
- **Dogfood 품질 강화 1차 (자체 검증 기반)**:
  - CLI 엔진 커맨드 17종을 데이터 주도 레지스트리+팩토리로 통합해 `__main__.py`의 반복 보일러플레이트를 제거하고, 엔진 클래스는 호출 시점에 모듈 어트리뷰트로 조회해 기존 monkeypatch 호환을 유지 (dup 최대 클론 제거).
  - `type`: 동일 파일·동일 문구의 Mypy note를 첫 위치 1건으로 병합(`metrics.repeats`)해 리포트 노이즈 축소.

### Refactored
- **HTML 리포터 모듈화**: 1070줄 단일 파일 `src/ici/reporters/html.py`를 `html/report.py` + `html/sections/{summary,line,test,complexity,dup,issues,static_analysis}.py` + `html/utils.py` + `html/assets/{style.css,app.js}` + `html/assets_loader.py` 구조로 분해하고, `html_assets.py`는 하위 호환 shim으로 유지. Zero-CDN 인라인 동작은 `importlib.resources` 기반 로더로 보존하며, 신규 엔진 탭 추가 시 섹션 모듈만 추가하면 되도록 확장성을 확보했습니다. (`_get_status_theme` 등 레거시 헬퍼는 `html/__init__.py`에서 re-export)
- **Runner/Path 모듈화**: `src/ici/core/runner.py`(640줄)에서 Windows Job Object 관련 상수·구조체·저수준 헬퍼를 `runner_win.py`(147줄)로 분리하고, 공통 경계 검증 `resolve_project_path` 중복을 `core/path_utils.py`로 통합. `config_schema.py`와 `core/project.py`는 해당 모듈을 re-export하여 기존 import 경로를 유지합니다. POSIX/Windows 분리에 따른 순환 참조 없이 `run_process`의 timeout·출력 제한·프로세스 그룹 정리 동작을 보존했습니다.
- **Test 엔진 인터프리터 분리**: 1000줄 `src/ici/engines/test.py`에서 인터프리터 해석(`_resolve_python`, `_find_pytest_cmd`, `_build_python_test_env`, `_find_coverage_cmd`, `_interpreter_from_command`)을 `test_interpreter.py`의 `TestInterpreterMixin`으로 분리하고 `TestEngine`이 다중 상속하도록 변경. `run_process` 패치 호환성(`ici.engines.test.run_process`)을 유지하며 `test_test_engine.py` 55개 테스트가 통과하도록 검증했습니다.

### Fixed
- **`cycle` 경로 표기**: 순환 참조 대상 파일 경로가 러너 절대경로로 노출되던 문제를 수정하고 다른 엔진과 동일하게 프로젝트 루트 상대경로로 보고.
- **프로젝트 정책 버전 싱크**: `ici.toml`의 `ici.version`을 패키지 `__version__`(`0.4.2`)과 동기화하고, 드리프트를 방지하는 `test_repository_ici_version_matches_package_version` 회귀 테스트를 추가했습니다.
- **엔진 경량 보강**: `dup` Type-2 해시 충돌 방지를 위해 윈도우 해시를 `"\x00"` 구분자로 생성, `type`의 `__private` 함수 오탐 방지를 위해 `__` 시작·끝 dunder만 스킵, `line`의 symlink 파일이 라인 집계에서 제외되도록 `is_symlink()` 가드 추가.
- **문서·환경·리포터 정합성**: `README.md` TEM 공식을 LineCov 기반(`min(Line,80)/80*Func/100*PassRate*5`, Branch는 `*5/4` 보정)으로 정정하고 HTML 대시보드가 신규 엔진도 요약/Issues에 자동 집계됨을 명시. `config.py` 전역 설정 생성 로그를 `stderr`로 이동해 `--json` 출력을 방해하지 않도록 수정하고, `scripts/smoke.sh`에 `dist/ici` 일치 및 Zero-CDN 검증 단계를 추가.
- **`security` 시크릿 마스킹**: `HardcodedSecret`/`PrivateKey` 발견 사항이 실제 시크릿 값을
  그대로 message/snippet에 담아, `--publish`로 gh-pages에 게시되는 HTML 리포트가 스캐너가
  찾아낸 시크릿을 그대로 노출하던 문제를 수정. 값은 `***REDACTED***`로 치환하며, 전체가
  주석인 줄은 스캔에서 제외해 오탐도 줄였습니다. 마스킹은 **줄 단위로 한 번** 수행한 뒤 그
  줄의 모든 발견 사항에 재사용하므로, 한 줄이 시크릿 패턴과 비(非)시크릿 패턴(`eval` 등)에
  동시에 걸려도 비시크릿 쪽 결과가 시크릿 원문을 흘리지 않습니다. `scan_tests=true`가 아무
  효과도 없던 문제도 함께 수정 — 프로젝트 최상위 `tests/`를 실제로 스캔하도록 별도 경로를
  추가했습니다.
- **`cycle` 재귀 한도 초과로 인한 스위트 전체 크래시**: 재귀 Tarjan SCC 구현이 큰(수백~
  수천 노드) import/include 체인에서 `RecursionError`로 죽어 `verify` 전체가 `ERROR`로
  종료되던 문제를 반복(iterative) Tarjan으로 교체해 해결. 다른 신규 휴리스틱 엔진과 달리
  `cycle`만 `required=true`가 기본값이어서 이 크래시가 전체 게이트를 막았던 점도 함께
  `required=false`로 정정. 부수적으로 리포트에 표시되는 순환 체인을 SCC 멤버의 임의 정렬
  목록이 아닌 실제 간선을 따라간 경로로 교체하고, 표준 라이브러리 모듈명과 겹치는 프로젝트
  모듈(`import html` vs 자체 `ici.reporters.html`)을 오탐하던 suffix 매칭과, 같은 파일명이
  여러 디렉터리에 존재하는 C++ 헤더를 임의로 하나 골라 잘못된 순환을 만들던 문제도 수정.
- **`line`이 `project.source_dirs`를 무시**: 소스 스코프가 `src/include/lib/app`으로 고정돼
  있어 `project.source_dirs`로 다른 레이아웃을 지정한 프로젝트는 파일 0개로 스캔되고
  500/1000줄 게이트가 조용히 무력화되던 문제를 수정. 이제 기본 스코프·게이트 모두
  `project.source_dirs`를 포함하며, `gate_dirs`를 명시적으로 좁힌 설정은 그대로 존중합니다.
- **`ici publish` 실패가 항상 종료 코드 0**: `PublishResult`에 `success` 필드를 추가해
  업로드 실패와 의도된 스킵(예: `GITHUB_ACTIONS` 밖 로컬 실행)을 구분하고, `ici publish`는
  실패 시 0이 아닌 종료 코드를 반환하도록 수정. `report-pr` job의 유일한 역할이 게시이므로
  실패가 조용히 사라지지 않아야 합니다. 기존 댓글 검색이 첫 30개만 확인해 그보다 긴 PR에서
  마커를 못 찾고 매번 중복 댓글을 남기던 문제도 페이지네이션(`per_page=100`, 최대 2000개)
  으로 수정.
- **`report-pr`이 PR 코드를 신뢰된 권한으로 실행**: `contents:write`+`pull-requests:write`를
  가진 `report-pr` job이 PR head/merge ref를 체크아웃해 `dist/ici.pyz`를 빌드하고 있어,
  문서가 명시한 "PR 코드를 이 job에서 다시 실행하지 않는다"는 불변식과 실제 워크플로가
  어긋나 있던 문제를 수정. 이제 PR의 base commit만 체크아웃합니다. `pages: read` 권한 누락
  으로 `_check_pages` 조회가 항상 실패해 Pages가 켜져 있어도 뷰어 링크 배지가 절대 뜨지
  않던 문제도 `report-pr`/`publish-main` 양쪽에 함께 수정.
- **`required_tools` 설정이 항상 config 오류**: `[engines.toolchain] required_tools`가
  #40에서 `toolchain` 엔진과 함께 제거됐지만 `doctor.py`는 여전히 그 경로를 읽고 있어,
  이 설정을 쓰면 "engines.toolchain is an unknown configuration key"로 항상 실패하던
  문제를 수정. `doctor`는 검증 게이트가 아닌 진단 전용 커맨드이므로 `engines` 바깥의
  전용 `[doctor] required_tools` 테이블로 복원했습니다.
- **`type` note 반복 횟수가 화면에 보이지 않음**: 동일 위치·문구의 Mypy note를 병합할 때
  `metrics.repeats`만 갱신되고 콘솔/HTML/Markdown이 실제로 출력하는 `message`에는 반영되지
  않아, N건이 조용히 1건처럼 보이던 문제를 수정. 이제 message에 `(xN)` 접미사가 붙습니다.
- **`cognitive` 기본 임계값 불일치**: 엔진 자체 fallback과 config 검증 fallback이
  `warn=15/fail=25`였던 반면 실제 배포 정책(`DEFAULT_CONFIG`)은 `warn=30/fail=60`이라,
  독립·부분 설정으로 엔진을 돌리면 실제 정책과 다른 기준이 적용되던 문제를 정정해
  세 곳 모두 `warn=30/fail=60`으로 통일.
- **HTML N/A(SKIP) 행의 잘못된 CSS**: `var(--text-muted)44`처럼 `var()` 참조에 직접 알파
  값을 붙이는 문법 오류로 SKIP 배지 테두리가 렌더링되지 않던 문제를 수정.

### Removed
- **CI 부적합 엔진 7종 일괄 제거**: `cmake_lint`, `pyproject_lint`, `file_hygiene`, `python_compat`, `build_definition`, `compile_db`, `static_hygiene` 및 `build_adapters`/`core/compile_db` 공유 인프라를 `verify` 스위트에서 제거. `file_hygiene`의 `bash -n` 셸 검사는 폐쇄망 `csh` 미지원으로 함께 폐기.
- **toolchain `doctor`로 흡수**: `verify` 엔진 `toolchain`을 제거하고 `src/ici/core/toolchain.py:41` `collect_tool_capability`를 `src/ici/doctor.py:25` `collect_diagnostics`가 재사용하도록 통합. `[doctor] required_tools` 위반 시 `doctor` 테이블에 `[yellow]Missing (required) WARN[/yellow]`로 표시.

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
