# ici — Multi-language Static Analysis

Python, C/C++, Rust, JavaScript/TypeScript 프로젝트의 정적 진단을 공통 결과와 오프라인
HTML로 확인하는 분석 도구입니다. Linux/WSL/CI가 우선 지원 경로입니다. 실행 완료,
검사 범위, 코드 위반, baseline, 게시 상태를 별도로 기록합니다.

```bash
ici analyze --plan                  # 실행할 검사와 준비 작업 확인
ici analyze --json --events events.jsonl --result result.json
ici report --result result.json --out report.html
```

`ici.toml`이 없으면 checkout 또는 현재 디렉터리의 소스를 읽어 메모리에서 구성합니다.
프로젝트 설정 파일을 생성하지 않고 원래 Ruff/mypy/Cargo/ESLint/TypeScript 설정을
사용합니다. 테스트·coverage·sanitizer·배포는 `analyze`의 기본 선택에 포함되지 않습니다.
Cargo Clippy는 build.rs/proc-macro를 실행할 수 있는 **prepare 작업**이며 fast에서 차단됩니다.

ici는 Rust native 단일 제품입니다([ADR-0008](docs/design/ici-next/adr/0008-rust-native-replacement.md)).
일반 설치는 [GitHub Release](https://github.com/jihoon22-lee/ici/releases)의
`ici-<version>-x86_64-unknown-linux-musl.tar.gz`를 `.sha256`으로 확인한 뒤 함께 게시된
`install-native.sh`로 root 없이 설치합니다. ici 자체는 Python 없이 실행되는 정적 실행 파일이며
검증된 native Ruff/ty와 모든 라이선스 고지를 동봉합니다. 지원 상태는 측정한 Linux/WSL2 x86_64에
한정된 `limited`입니다. 설치·업데이트·되돌리기는 [native 설치 가이드](docs/native-install.md),
기존 pyz·uv·pip 설치에서 옮기는 방법(PATH 충돌, `ICI_PYTHON`, 종료 코드 3, baseline 재생성)은
[이전 가이드](docs/migration-to-native.md), 언어별 도구·지원 범위는
[범용 분석 가이드](docs/general-analysis.md)에 있습니다. PyPI의 같은 이름 패키지를 가정하지 않으며,
Python wheel/sdist/pyz는 더 이상 발행하지 않습니다. v0.13.x Python 공개판은 그대로 보존되며 명시적인
복귀 대상입니다.

기존 `ici verify`, 엔진별 명령, `[ici]`/`[engines]` 설정은 native 제품의 호환 경로로 유지됩니다.
`[workspace]` 설정의 verify와 `analyze`는 공통 application을 사용합니다. `ici next …` 명령도 계속
제공됩니다. 결과는 `ici.next.run` v2, 이벤트는 `ici.next.event` v1, 종료 코드는 0/1/2/3/130을
유지합니다. legacy 명령에서 필수 증거가 불완전하면 v0.13.0의 1 대신 3으로 끝납니다. 새 결과와 cache는
프로젝트의 `.ici/rust-v1/`에 쓰므로 `.ici/`를 `.gitignore`에 둡니다.
보조자료 importer는 별도 `ici.auxiliary` v1을 쓰며 run gate를 변경하지 않습니다.

RHEL/GHES 현장 인수와 전용 bundle은 환경별 지원 경로입니다. 현장 미검증을 일반
Linux package 릴리스의 일괄 blocker로 사용하지 않습니다. macOS/native Windows,
PE/Mach-O와 새 플랫폼의 동등 지원은 아직 약속하지 않습니다.
quality-zoo는 ici가 소유합니다. 소비자 [Lens](https://github.com/jihoon22-lee/lens-diagnostics)는
기존 toy-projects 저장소의 새 이름이며, 과거 검증 SHA와 출처 표기는 역사로 보존합니다.

버전별 변경과 각 항목의 CI·Pages 실측 증거는 아래에 있다. README는 그 증거를 복사하지
않는다.

- [CHANGELOG](CHANGELOG.md) — 버전별 변경과 릴리스 증거
- [ici-next 설계](docs/design/ici-next/README.md) — **전환의 계획·설계·결정 기록.** 개발 마일스톤은 완료됐고 RHEL/GHES 현장 인수는 [field-acceptance checklist](docs/design/ici-next/field-acceptance.md)가 추적한다
- [인수인계 문서](docs/superpowers/2026-08-30-handover.md) — 2026-09-04 시점의 맥락과 결정 이유 *(시점 기록)*
- [workthrough](workthrough/) — 개별 작업의 실측 기록
- [CI/CD 연동 가이드의 candidate 채널](docs/ci-integration.md#5-candidate-채널-stable-release가-아님) — candidate artifact와 Quality Zoo 인수 절차

### 릴리스 정책

- `feature`·`test`·`refactor`·`docs` PR은 버전 변경이나 stable release를 자동으로 만들지 않습니다.
- `patch`는 이미 공개된 stable artifact의 defect·security·compatibility 수정에만 사용합니다.
- `minor`는 사용자에게 보이는 응집된 roadmap checkpoint이며, ici 전체 gate·실제 도구 E2E·candidate cross-repo/toy 검증·PR/main CI·Pages·문서/CHANGELOG가 모두 끝난 뒤에만 정합니다.
- pre-release/candidate artifact는 stable이 아니며, 하나의 PR이 하나의 릴리스를 뜻하지 않습니다. `v0.10.1`과 공개된 `v0.10.2`는 공개 결함에 한정한 corrective stabilization입니다.
- `v0.11.0`은 I4-4, real toy-projects/quality-zoo 검증(I5·I6·I7·I8·I9)을 닫은 뒤 냅니다. **I4-3은 두 항목이 열린 채로 남습니다** — C++ complexity/cognitive를 AST 기반으로 바꾸는 것과 whole-program dead-symbol reachability입니다. 둘 다 없는 같은 인프라(C++ AST 접근)를 필요로 하고, 둘 다 **없는 기능이 아니라 이미 내보내는 값의 정밀도** 문제라, 한계를 명시적으로 표시한 채 배포합니다: 해당 metric은 `bounded-cpp-tokens` estimate로 표기되고 `metric_confidence`로 등급이 함께 나가며, 전체 목록은 [한계 인벤토리](docs/engine-reference.md)에 있습니다. 이 두 항목이 닫히기 전까지 그 숫자를 exact 측정값으로 인용하면 안 됩니다.

---

## 📚 문서 허브 (Documentation Hub)

| 문서 | 설명 | 바로가기 |
|---|---|---|
| **🧭 ici-next 설계** | **전환의 canonical source.** 목표 아키텍처·SPEC 5종·ADR·실측 기록. 개발 완료, 현장 인수는 [field-acceptance checklist](docs/design/ici-next/field-acceptance.md) | [docs/design/ici-next/](docs/design/ici-next/README.md) |
| **📦 native 설치·복구** | archive 확인, rootless 설치, `ici setup` 업데이트·되돌리기, 공개 v0.13.0 명시 복귀, XDG 경로, 라이선스 | [docs/native-install.md](docs/native-install.md) |
| **🔁 pyz/uv/pip에서 이전** | PATH 충돌과 `ici doctor`, `.venv/bin/ici` 작업 갱신, `ICI_PYTHON`, 종료 코드 3, `.ici/rust-v1`, baseline 재생성 | [docs/migration-to-native.md](docs/migration-to-native.md) |
| **🚀 사용자 가이드** | 빠른 시작, 설치, 전체 CLI 사용법 및 IDE 원클릭 점프 | [docs/user-guide.md](docs/user-guide.md) |
| **📏 검증 엔진 레퍼런스** | 19개 엔진 descriptor (기본 fast 12 / standard 14 / deep 16, release contract opt-in 시 deep 19), TEM 스코어링 공식, `ici.toml` 정책 설정 *(현행 stable)* | [docs/engine-reference.md](docs/engine-reference.md) |
| **⚙️ CI/CD 연동 가이드** | GitHub Actions, Step Summary, PR 어노테이션, 사내 폐쇄망 러너 *(현행 stable)* | [docs/ci-integration.md](docs/ci-integration.md) |
| **🏛️ 시스템 아키텍처** | ZipApp 패키징, Polyglot 런처, 오케스트레이터 및 리포터 계층 설계 *(현행 stable)* | [docs/architecture.md](docs/architecture.md) |
| **🐶 자체 검증 부채** | ici 자신의 `deep` 실행에 남은 WARN·SKIP과 각각을 받아들인 이유 | [docs/design/self-verification-debt.md](docs/design/self-verification-debt.md) |
| **🗂️ 품질 분석기 실행 계획** | *(superseded — ici-next가 대체)* v0.11.0까지의 로드맵 기록 | [ici 마스터 계획](docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md) · [toy-projects 마스터 계획](https://github.com/jihoon22-lee/toy-projects/blob/main/docs/superpowers/plans/2026-08-30-product-portfolio-master-plan.md) |
| **📋 변경 이력 (CHANGELOG)** | 버전별 상세 릴리스 노트 및 마일스톤 | [CHANGELOG.md](CHANGELOG.md) |
| **📜 개발 및 기여 규약** | 브랜칭 전략, 커밋 룰, 런타임 제약 및 불변식 | [AGENTS.md](AGENTS.md) |

---

## 기존 verify 호환 경로의 특징

1. **단일 native archive 배포**:
   - Python·가상환경·`pip` 없이 digest를 확인한 archive를 rootless로 설치(`$XDG_DATA_HOME/ici/native`). 정적 실행 파일이므로 시스템 Python 버전과 무관합니다.
   - `src` 외 `lib`/`app` 등 소스 레이아웃을 자동 탐색합니다. 사용자 전역 `~/.config/ici/ici.toml`의 팀 정책은 새 schema에서 암묵적으로 합치지 않고 limitation으로 알립니다.
2. **프로젝트 Python은 외부 도구**:
   - pytest·coverage·mypy·runtime 호환 검사는 프로젝트 인터프리터로 실행합니다. legacy 설정에서는 v0.13.0 launcher 규칙대로 `ICI_PYTHON`·프로젝트 `.venv`·`python3.14`…`python3.10`이 프로젝트 인터프리터 후보이며, ici core는 그 인터프리터로 실행되지 않습니다.
3. **19개 품질 검증 엔진 descriptor (fast 12 / standard 14 / deep 기본 16)**:
    - `line`: 파일당 순수 코드 500줄 초과 경고, 1000줄 초과 실패 + **계층형 디렉토리 트리 뷰** (`project.source_dirs` + 기본 소스 디렉터리 전용 스캔, `include_dirs`로 확장)
    - `lint`: Python Ruff 및 C/C++ compiler 진단, optional clang-tidy I4-1와 Qt-aware clazy I4-2
      adapter (`auto`/`required`/`off`, exact compilation-context replay, 도구 미설치·부분 폴백
      증거 포함). clazy는 standalone/wrapper provider, level0/level1 profile, Q_OBJECT·signal/slot·
      lifetime·container·Qt compatibility 진단을 보존합니다.
    - `compile_db`: C/C++ production translation unit coverage, 실제 compiler flag/search path와 stale build context 검증
      - root CMake 프로젝트에 DB가 없으면 `build/ici-cmake-build`에서 Release·`CMAKE_EXPORT_COMPILE_COMMANDS=ON`·unity OFF로 canonical DB를 생성합니다. `Ninja` 또는 `*Makefiles` 단일 구성만 exact context로 인정하고, generated source는 필요한 경우 한 번 build한 뒤 DB를 다시 읽습니다.
      - report/cache에는 DB origin·generator·unity 상태·CMake target과 digest가 남으며, subdirectory output 경로도 working directory와 DB 기준을 일치할 때만 안전하게 보정합니다.
    - `test` & `tem`: 단위 테스트 전수 통과 + Line/Branch/Function 커버리지 및 PassRate 기반 **TEM 5.0 스코어링** (`min(Line,80)/80 * Func/100 * PassRate *5`, Branch는 `*5/4` 보정; 모듈별 실측: Python `coverage.py` / C++ `gcov`). `deep`에서만 선택적으로 bounded repeat, slow-test inventory, flaky verdict 및 mutation-tool capability를 관측하며 `quality.mode = "report" | "warn"`으로 gate 영향을 구분합니다.
    - `type`: Mypy 정적 타입 검사 및 AST 부분 폴백 (C++ 타입 검증은 명시적 SKIP)
    - `python_compat`: 현재 실행 중인 Python을 기본 필수 runtime으로 확인하고, 설정한
      interpreter의 `-VV`·`compileall`·선택적 import smoke를 실행합니다. `requires-python`과
      설정된 syntax/API floor를 위치와 함께 검사하며, import는 모듈 top-level code를 실행하므로
      `[engines.python_compat].imports`에 명시한 모듈만 opt-in합니다. runtime 호출 증거는
      `MEASURED`/정확한 `ToolEvidence`로 남고, 외부 interpreter 경로가 바뀔 수 있어 결과 cache는
      사용하지 않습니다. `wheel_globs`를 지정하면 pyproject와 wheel filename/METADATA/WHEEL/
      RECORD/tag, package file 및 entry point 일관성을 import/build 없이 bounded하게 검사합니다.
    - `complexity`: Python AST와 exact context/tool이 있을 때 C++ clang-tidy
      `readability-function-size`로 함수 경계를 정하고, 경계 내부 CC/중첩은 masked token/brace
      metric으로 계산 + **원본 소스 코드 블록 프리뷰**
    - `sanitize`: C++ ASan/LSan/UBSan 구조화 진단을 포함한 메모리 안전성 및 Python 리소스 누수 검증
    - `thread_sanitize`: deep profile 전용 C++ ThreadSanitizer 실행과 bounded thread-safety 진단
    - `dead`: 죽은 코드, 도달 불가능 코드, 미사용 심볼 검출. 공통 bounded UTF-8 source intake를 사용하며 generated/vendor는 기본 제외(`include_generated`/`include_vendor` literal-boolean opt-in)합니다. Python AST reachability/name-reference는 `ESTIMATED`/heuristic이고, 승인된 GCC/Clang(및 그 capability-approved alias)이 선택된 owned C/C++ translation unit에 귀속한 internal-linkage 함수 `-Wunused-function` 진단은 `[engines.dead].cpp_unused = "auto" | "required" | "off"` 정책으로 재생할 수 있습니다. 별도 `[engines.dead].cpp_linker = "auto" | "required" | "off"`(기본값 `off`)는 Linux root CMake/Unix Makefiles/Release direct-object ELF executable에서 GCC GNU ld가 버린 uniquely mapped local/hidden function section을 `cmake`/`readelf`/`addr2line`으로 확인합니다. linker finding은 target-local `MEASURED`/`EXACT`이며 whole-program 주장이 아닙니다. 모든 알려진 configuration에서 같은 위치 범위의 compiler 진단이 확인된 경우에만 C++ 결과를 `MEASURED`/`EXACT`로 기록하며, intake는 8,192개 unique candidate와 2,048개 owned/analyzed 파일·파일당 8 MiB·aggregate 64 MiB로 제한하고, 제외된 파일은 owned 한도에 포함하지 않습니다.
    - `dup`: **Type-2 클론 검출** (변수명/리터럴만 다른 복사-붙여넣기도 감지) + 최대 클론 병합 및 원본 인덴트 보존 중복률 산출. Python/C/C++는 전용 line-preserving lexer로 정규화해 언어별로 격리한다. Python `tokenize`/AST context는 주석·multiline import와 `match`/`case` soft keyword를 처리하고 identifier, 숫자·문자열 계열, 들여쓰기·연산자 category를 보존하며, `python_semantic = "auto"` 기본 정책은 leaf function/method의 bounded AST shape도 canonicalize한다. local alpha renaming과 layout insensitivity를 적용하되 control flow·operator·literal·source-spelled imported-name/attribute anchor를 보존하고 `sha256/semantic-shape-v1`가 exact하게 같은 경우만 semantic-shape group을 보고한다. C/C++ lexer는 comments/directives를 제거하고 C++ backslash-newline splice의 physical line을 보존하며 punctuator, literal, UDL과 Qt anchor를 구분한다. normalized-window seed의 exact token verification과 function/class/import/directive region, semantic-signal policy를 통해 값만 다른 data table은 억제하고 실제 control-flow clone은 유지한다. lexical fingerprint는 `sha256/type2-region-v2`, AST-shape fingerprint는 `sha256/semantic-shape-v1`로 기록하지만 두 경로 모두 compiler/linker 실측이 아니므로 결과는 `ESTIMATED`/heuristic이며 behavioral equivalence를 뜻하지 않는다. lexical tokenizer/matching budget은 엔진 전체를 `ERROR`/`NOT_RUN`으로 닫고, AST-shape budget은 semantic partial을 버린 뒤 `auto`에서는 lexical 결과만 유지하며 `required`에서는 `ERROR`/`NOT_RUN`으로 닫는다. generated/moc/vendor는 기본 제외하고 owned C/C++ header도 검사하며, standalone `.moc`는 `include_generated = true`일 때만 포함한다.
    - `exception`: 예외 삼킴(`except: pass`), Traceback 유실, 소멸자 throw 차단
    - `cycle`: Python import / C++ include **순환 참조 탐지** (Tarjan SCC, C++ path-suffix 해석의 미해결·모호 위치도 보고)
    - `security`: 하드코딩 시크릿, 약한 해시, `eval`/`pickle`/`shell=True` 등 위험 패턴 탐지
    - `resource`: 파일·네트워크 리소스 누수 AST 패턴 검출
    - `cognitive`: SonarQube S3776 스타일 **인지 복잡도** (기본 비활성, 옵트인)
    - `build`: deep 전용 release artifact producer. CMake/qmake와 명시적으로 활성화한 shell-free
      Make plan을 별도 shadow에서 실행하고, linked output을 SHA-256·size·mode·target·producer
      command provenance와 함께 `ici.artifacts/v2` manifest로 발행합니다.
    - `binary_compat`: 기본적으로 build manifest의 executable/shared-library record만 실행 없이
      `readelf`로 ELF class/machine, GLIBC/GLIBCXX/CXXABI 상한, DT_NEEDED, RPATH/RUNPATH 및
      build-path leak를 검사합니다. `artifacts`를 명시하면 manifest id/path로 선택한 record
      (static library 포함)도 대상으로 삼을 수 있습니다. 기본 비활성입니다.
    - `integration`: build manifest와 현재 runtime에서 해석한 typed `{artifact:id}`/
      `{python:id}` placeholder를 이용해 bounded argv process contract를 실행합니다. shell,
      임의 환경 상속, partial output은 허용하지 않으며 exit/stdout/stderr/output artifact assertion을
      결과에 보존합니다. 기본 비활성입니다.
4. **기본 10개(기준선 비교 시 11개) 전용 탭 인터랙티브 Zero-CDN HTML 대시보드 (`--html`)**:
     - `📋 Verification Suites`: 종합 품질 게이지, TEM 스코어, 전체 엔진 상태 및 전용 탭 점프 버튼 (N/A 엔진은 회색 접힘 행 표시)
     - `🧭 Support & Capabilities`: 발견된 언어·Qt scope와 엔진별 지원 mode, 실행 증거, 도구, fallback 및 알려진 한계를 issues-first 접힘 행으로 표시
     - `🧭 Baseline Delta`(비교 시): new·unchanged·moved·resolved 및 regression/gate를 issues-first로 표시
     - `📊 Line Analysis & Explorer`: 소스 스코프 기본 표시 + **All-files 토글로 전체 프로젝트 라인 탐색** + 계층형 파일 트리 + 실시간 검색 + 코드 분포 차트
     - `🧪 Tests & Coverage`: 4대 커버리지 KPI 게이지 + **모듈별 실측 커버리지 테이블 (Module Coverage Table)** + 파일별 테스트 스위트 & 개별 테스트 케이스 상세 뷰
     - `🏷️ Static Types`: 실제 타입 finding과 검사되지 않은 파일을 분리한 정적 타입 뷰
     - `🧩 Complexity`: 순환 복잡도 리더보드 + 🧠 인지 복잡도 통합 뷰 + 토글 코드 블록
     - `📦 Clone Groups`: 연결 컴포넌트 클러스터링 기반 중복 코드 카드 + 원본 들여쓰기 보존 코드 블록
     - `🔁 Cycles`: 순환 참조 체인을 **칩(chip) 시각화**로 표시 + 전체 경로 접기
     - `🔐 Security & Resources`: security/resource 발견 사항 카드 뷰
     - `⚠️ Issues`: 전체 조치 필요(WARN/FAIL/ERROR/SKIP) 항목 통합 뷰 + **접고 펼칠 수 있는 문제 코드 스니펫**
     - Issues가 2,000개를 초과하면 초기 HTML은 50개 행만 server-render하고 나머지는 안전한
       inline JSON(`ici.html-report/v1`)을 browser가 50개 단위로 검색·페이지네이션합니다. 전체
       embedded data는 64 MiB에서 fail-closed하며 HTML inventory 자체는 줄어들지 않습니다.
       `--report`를 함께 요청하면 canonical `ici.result/v3` JSON도 전체 inventory를 유지합니다.
5. **전체 파일·라인 원클릭 점프 네비게이션**:
   - **로컬 터미널**: Rich가 안전한 `file://` 링크를 출력하여 지원하는 터미널에서 파일 위치로 이동
   - **GitHub Actions**: `$GITHUB_STEP_SUMMARY`, 아티팩트 및 인라인 에러 어노테이션에 GitHub Permalink 제공. 검증(`verify`) job 자체는 `contents: read`만 사용하며 댓글을 작성하지 않고, 별도의 `report-pr` job이 업로드된 리포트만으로 sticky PR 댓글을 갱신
   - **`--publish`**: 권한을 명시적으로 부여한 신뢰된 실행(예: `main` push)에서 인터랙티브 HTML 리포트를 `gh-pages`에 배포하는 선택 기능. 신규 CLI `ici publish --html --json`으로 기존 리포트를 단독 게시 가능
   - **단일 HTML 리포터**: 브라우저에서 로컬 파일·선호 에디터 링크를 선택해 이동
6. **안정적인 `ici.result/v3` 품질 데이터 계약**:
   - 기존 위치 inventory인 `targets`와 함께 rule/category/severity/confidence, 관련 위치, 개선안, suppression, 단위 metric을 갖춘 `findings`를 제공합니다.
   - project-relative 위치와 symbol/region으로 만든 fingerprint는 checkout 경로와 Windows/Linux separator가 달라도 동일합니다.
   - v2 리포트 migration과 viewer 하위 호환을 제공하며, JSON Schema는 [`src/ici/schemas/ici-result-v3.schema.json`](src/ici/schemas/ici-result-v3.schema.json)에 있습니다.
   - 모든 출력 형식은 공통 redaction 경계를 거쳐 engine message·snippet·도구 argv/output·remediation·metric과 파일 경로에 섞인 credential을 마스킹합니다. 일반 경로는 그대로 유지됩니다.
   - `--write-baseline`으로 현재 finding inventory를 보관하고 `--baseline`으로 다음 실행과 비교할 수 있습니다. `--fail-on-new`는 새 actionable finding 또는 severity/suppression regression만 gate에 반영합니다.
   - 기준선 비교 결과는 JSON·HTML·Markdown Summary·콘솔에 표시되고, 신뢰된 publish job의 sticky PR 댓글에는 새 finding·regression·gate·호환성 warning 요약이 포함됩니다.
   - `--sarif <path>`는 같은 canonical finding projection을 SARIF 2.1.0으로 내보냅니다. rule/result
     순서, source-relative URI, severity level, fingerprint, related location, suppression 및
     baseline state를 결정적으로 보존하며 100,000 results/10,000 rules 상한을 넘으면 조용히
     자르지 않고 실패합니다.
7. **과장 없는 언어·도구 지원 매트릭스**:
   - 19개 엔진 × Python/C++ 범위를 `exact`/`heuristic`/`tool-backed`/`unsupported`로 선언하고 Qt 호환성, 필요 도구, fallback과 한계를 함께 공개합니다.
   - 프로젝트별 적용 여부와 실제 증거 상태를 계산해 doctor, JSON, HTML과 Qt viewer에서 같은 데이터로 표시합니다. 상세 표는 [엔진 레퍼런스 §1.4](docs/engine-reference.md#14-엔진-지원기능-매트릭스)를 참고하세요.
   - `ici doctor`는 전체 tool registry를 한 번의 bounded probe snapshot으로 수집하고, 필요한 이유(`engine:language` 또는 `doctor.config`)와 missing/incomplete 상태를 함께 보여 줍니다. `ici doctor --json`의 `capability_inventory`는 status·counts·version/path/details/evidence를 담는 machine-readable 계약이며, 기존 `tools` map도 유지합니다.
   - `ici verify`도 유효한 support matrix의 `applicable`·`enabled` 범위와 `doctor.config`에서 required/optional 정책을 계산한 뒤, 엔진 실행 전에 같은 registry를 정확히 한 번 수집합니다. suite root의 선택적 `capability_inventory`를 console/Markdown/zero-CDN HTML reporter가 그대로 공유하므로 reporter가 도구를 재탐지하지 않습니다. required provenance 우선 규칙과 모든 provenance, capability 메타데이터·probe argv/evidence redaction을 보존하며, 콘솔은 요약하고 Markdown은 전체 inventory를 접어 보여 주고 HTML은 Support & Capabilities 탭에 전체 행을 표시합니다. 기존 inventory 없는 `ici.result/v3` 리포트도 계속 읽을 수 있습니다.
8. **사용자 로컬 분석 캐시**:
   - `ici verify`는 프로젝트 루트·소스/빌드 설정 내용·effective ici 설정·toolchain 버전·컴파일 DB digest/parse state·엔진 구현·build variant·ici 버전을 포함한 `ici.analysis-cache-key/v3`로 완료된 엔진 결과를 재사용합니다. compilation database digest는 preflight가 immutable context로 캡처한 snapshot을 식별하는 값이며 live-file lease가 아닙니다. DB가 변경되면 다음 preflight에서 새 digest와 context를 캡처합니다. 엔진 구현 identity에는 engine class source digest와 `CACHE_IMPLEMENTATION_MODULES`로 명시적으로 선언한 helper/dependency module source digest 목록이 포함되며, C++ lint는 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._clang_tidy`, `ici.engines._clazy`, `ici.engines._cpp_diagnostic_categories`, `ici.engines._cpp_diagnostics`, `ici.engines._cpp_lint`, `ici.engines._cpp_tooling`, `ici.engines._qt_codegen`, `ici.engines.lint`를, cycle은 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_include_graph`, `ici.engines._cpp_include_trace`, `ici.engines.cycle`을, complexity는 `ici.core._compile_db_paths`, `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_function_boundaries`, `ici.engines._cpp_tooling`, `ici.engines.cpp_text`를 명시합니다. `.ui`/`.qrc`도 선언된 source suffix로 digest되며, 기본 위치는 `~/.cache/ici/analysis/`이고 remote/shared cache는 사용하지 않습니다.
   - 완전한 `PASS`/`WARN`/`FAIL`은 저장할 수 있지만 `ERROR`/`SKIP`/`NOT_RUN`, timeout·truncation·tool error 및 invalid artifact는 저장하지 않습니다. 예외적으로 `dead`는 아직 cache v3에 없는 compiler/include identity 때문에, `test`는 outcome·timing·flaky rerun 같은 실행 시점 관측 때문에 cache key/entry를 만들지 않고 매번 새로 실행합니다. `--no-cache`, `ici cache`, `ici cache --clear`로 실행별 비활성화·inventory·정리를 제어합니다.
   - v3 engine JSON의 optional `cache_hit`/nullable `cache_key`는 기존 archive 소비자와 호환되며, 캐시는 프로젝트 소스를 변경하지 않고 atomic local entry만 씁니다. 새 entry는 0700/0600 권한 경계를 사용하고, symlink·duplicate key·NaN/Infinity·32 MiB 초과 payload를 거부합니다.

---

각 엔진이 무엇을 증거로 인정하고 무엇을 인정하지 않는지 — Python runtime 호환성, C++
compiler/clang-tidy replay, Qt clazy와 생성 단계, compiler-backed unused function, GNU ELF와
Python AST-shape 확장, C++ complexity 함수 경계, opt-in release contract engine — 은
[검증 엔진 레퍼런스의 「엔진 심화 노트」](docs/engine-reference.md#3-엔진-심화-노트)에서
정책 이름·기본값·승격 조건과 함께 다룬다.

## 💻 빠른 설치 및 사용법

### 1. native archive 설치와 실행
```bash
# release에서 archive, .sha256, install-native.sh, ici-<version>-supporting.sha256을 내려받아
# archive와 installer digest를 확인한 뒤 rootless 설치
sha256sum --check --strict ici-<version>-x86_64-unknown-linux-musl.tar.gz.sha256
sha256sum --check --ignore-missing --strict ici-<version>-supporting.sha256
sh install-native.sh --archive ici-<version>-x86_64-unknown-linux-musl.tar.gz --sha256 <trusted sha256>
export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/ici/native/bin:$PATH"
ici --version

# 환경 진단 (PATH에 남은 pyz/uv/pip ici와 ICI_PYTHON도 보고)
ici doctor
ici doctor --brief  # capability status와 준비된 도구 수
ici doctor --json   # 전체 capability inventory, support matrix, executables

# 정적 분석과 저장 결과 렌더링
ici analyze
ici report --result .ici/rust-v1/result.json --out report.html

# 기존 전체 검증
ici verify --report --html verify_report.html --open

# 업데이트·되돌리기 (사용자 설정·결과는 그대로)
ici setup --archive ici-<new>-x86_64-unknown-linux-musl.tar.gz --sha256 <trusted sha256>
ici setup --rollback
```

### 2. 소스에서 빌드
```bash
# packaging/native에 고정된 Ruff/ty, Rust musl std, musl 1.2.5 입력을 digest로 확인해 준비
scripts/prepare-native-inputs.sh --output "$PWD/target/distribution/inputs"
set -a; . target/distribution/inputs/native-build.env; set +a
# 깨끗한 exact-source tree에서 static musl archive와 .sha256 생성
scripts/build-native.sh --tools-dir "$ICI_NATIVE_TOOLS_DIR" \
  --sysroot "$ICI_NATIVE_SYSROOT" --sysroot-sha256 "$ICI_NATIVE_SYSROOT_SHA256"
# 독립 build 비교, 고지·라이선스, static ELF, rootless 설치, 오프라인 분석 검사
scripts/check-native-release.sh --archive dist/ici-<version>-x86_64-unknown-linux-musl.tar.gz \
  --compare <다른 --build-id로 만든 archive> --evidence /tmp/ici-release-check
```

`build-native.sh`는 Rust 1.99.1(`rust-toolchain.toml`)과 `Cargo.lock`(`--locked`)을 요구하고,
`git ls-files` 기준의 정규화된 source snapshot을 digest로 고정한 뒤 `target/distribution/` 아래에서만
compile합니다. source·cargo·sysroot·build 경로는 remap하고 `SOURCE_DATE_EPOCH`는 HEAD commit 시각을
씁니다. 공식 musl std의 파일 트리 digest와 Rust 표준 라이브러리 고지 catalogue가 고정값과 다르면
거부하고, core에 ELF interpreter나 `DT_NEEDED`가 있으면 거부합니다. archive manifest에는 source
revision·tree digest, Cargo.lock digest, compiler·linker·archiver digest와 build host의 kernel·CPU가
기록됩니다. 그래서 두 build의 byte 동일성은 같은 host에서 비교하며, 다른 host에서 다시 build한
archive가 같은 digest를 가진다고 주장하지 않습니다. `--allow-dirty`는 개발 snapshot이며 release가
아닙니다. 빌드는 compiler나 도구를 암묵적으로 설치하거나 내려받지 않습니다.

### 3. 기준선 비교와 delta gate

```bash
# 현재 상태를 다음 비교의 기준선으로 저장
ici verify --write-baseline .ici/baseline.json

# delta를 기록하되 기준선 gate는 강제하지 않음
ici verify --baseline .ici/baseline.json \
  --report --html verify_report.html --github-summary

# CI에서 새 actionable finding과 regression을 gate에 반영
ici verify --baseline .ici/baseline.json --fail-on-new \
  --report --html verify_report.html --github-summary
```

기준선은 `ici.result/v3` JSON만 읽으며, baseline 입력과 새 baseline 출력 경로는 프로젝트
루트 안에 있어야 합니다. 경로가 프로젝트 밖을 가리키거나 프로젝트 내부 symlink를 통해
밖으로 빠져나가면 거부합니다. 기존 기준선과 `--write-baseline`의 출력 경로를 같게 쓰는
것은 기존 파일을 먼저 읽고 새 파일로 교체하므로 허용됩니다. 다만 `--fail-on-new` gate가
실패한 실행은 같은 파일을 덮어쓰지 않아 다음 실행에서 regression을 숨기지 않습니다.
또한 `--report`가 만드는 `verify_report.json`과 `--write-baseline` 경로를 같게 쓸 수는 없습니다.

### 4. 분석 캐시

`ici verify`는 기본적으로 사용자별 로컬 분석 캐시를 사용합니다. 캐시는 프로젝트 안에
생기지 않으며, 동일한 프로젝트 입력·effective 설정·toolchain·엔진 구현·build variant에서만
engine result를 재사용합니다. 결과를 항상 새로 계산해야 하는 CI/release 점검이나 캐시 영향을
분리한 진단에는 다음 옵션을 사용합니다.

```bash
# 이번 verify에서 cache read/write 모두 비활성화
ici verify --no-cache

# cache 위치·유효 entry·손상 entry·크기 확인
ici cache

# ici가 소유한 exact entries-v1 아래 entry만 삭제
ici cache --clear
```

`WARN`/`FAIL`이라도 timeout·truncation·tool error가 없고 artifact identity가 유효한 완료
결과라면 재사용될 수 있습니다. `ERROR`/`SKIP`/`NOT_RUN`과 불완전하거나 invalid한 결과는
성공 cache로 저장하지 않습니다. 캐시 hit 여부와 key digest는 v3 JSON의 optional
engine-level `cache_hit`/`cache_key`로 확인할 수 있고, 기존 v3 JSON은 해당 필드 없이도
계속 읽을 수 있습니다.

cache key(`ici.analysis-cache-key/v3`)는 canonical root, source/build-config content, effective config, toolchain,
engine implementation, build variant와 ici version을 포함합니다. engine implementation identity는
engine class의 module/qualname와 class source digest, 그리고 `CACHE_IMPLEMENTATION_MODULES`로
명시한 helper/dependency module 이름의 sorted unique 목록과 각 module source digest를 포함합니다.
import tree 전체를 암묵적으로 수집하지 않고 명시적으로 선언된 구현 의존성만 반영합니다.
프로젝트 source digest에는 인식된 설정 이름 `.clang-tidy`가 포함되므로 그 내용·권한 변경은
cache miss를 만들지만, 인식 목록에 없는 unrelated hidden file은 포함하지 않습니다.
`verify_report.json`과
engine별 `*_report.json`처럼 ici가 생성하는 report JSON은 source digest에서 제외됩니다.
entry reader는 symlink·비정규 파일, duplicate JSON key, non-finite number와 32 MiB 초과
payload를 신뢰하지 않으며, 손상·stale entry는 miss로 처리합니다. artifact manifest가
있으면 store/load 양쪽에서 경로 containment와 실제 content·size·mode를 재검증합니다.

로컬 cache 검증에서 전체 Python 3.10 run은 935 tests passed였고 targeted 테스트도
통과했습니다. standard 첫 실행은 118.49초·hits 0, 두 번째는 2.38초·hits 12였으며,
두 결과의 normalized SHA-256은 `95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`,
findings는 각각 3,497건이었습니다. HTML은 4,095,550 bytes·외부 참조 0건, 재현성
script 두 build는 SHA-256 `6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`과
project source status unchanged를 확인했습니다. 이 결과는 I2-4의 로컬 증거이며 PR/CI/Pages
또는 release 완료를 뜻하지 않습니다.

### 5. Issues-first 콘솔

`ici verify`는 전체 inventory를 보존하면서 조치가 필요한 원인을 짧게 확인할 수 있는
issues-first 콘솔 projection을 제공합니다.

- `ici verify --verbose`: `verify` 전용 상세 표시 모드이며 console cap을 해제합니다.
- `ici verify --max-findings N`: 엔진별 console display group 상한입니다. 기본값은 엔진별
  5건이며 `0`은 engine summary만 표시합니다.
- `ici verify --group-by engine|severity|category|file|rule`: v3 finding의 engine, severity,
  category, canonical primary file 또는 rule 기준으로 표시 그룹을 선택합니다.

cap과 grouping은 console-only projection입니다. JSON·HTML·Markdown과 baseline의 원본
inventory, target, finding, delta occurrence는 상한과 무관하게 모두 보존합니다. duplicate는
같은 실행의 같은 clone group 안에서 같은 파일의 겹치는 line region만 화면에서 병합하며,
원본 occurrence와 fingerprint를 유지합니다. 인접하지만 겹치지 않는 region이나 서로 다른
clone group은 합치지 않습니다. HTML `Issues` 탭도 native v3 finding inventory를 기반으로
표시하며 전체 결과를 유지합니다.

표시 순서와 줄바꿈은 deterministic하게 유지하고, 80-column 터미널에서도 표와 상세 링크가
한 글자씩 세로로 깨지지 않도록 회귀 테스트로 고정했습니다.

다음 수치는 issues-first console을 도입한 PR #89 당시의 고정된 acceptance 기록입니다
(`814679c` + `d80a027`). 당시 로컬 Python 3.10 전체 품질 게이트는 756/756 tests,
focused console 테스트는 16개였습니다. 최종 안정 self verify에서
built `dist/ici.pyz`가 exit 0으로 실행됐고 suite는 WARN을 반환했습니다. self verify 출력은
144 lines/15,288 bytes, HTML은 3,383,523 bytes였습니다. 해당 self verify 출력에 내장된
test engine 수치는 756/756이며, local self verify line/function/branch coverage는 87.8%/96.6%/78.8%, TEM
4.83, engines Pass 8/Warn 4/Fail 0/Error 0/Skip 0을 확인했습니다. complexity는 최대 23,
이슈 64건이며 duplicate는 16.2%·338 groups·1,006 actionable occurrences였습니다.

콘솔 측정은 actionable 1,088건, visible 21/420 display groups, represented 34,
hidden 1,054 findings/399 groups였습니다. HTML에는 clone group card 338개와 issue engine
row 1,088개가 유지됐고 external script/stylesheet reference는 0개였습니다.

Merge evidence (PR #89): [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
[`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
병합됐습니다. [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의
모든 required checks가 green(756 tests)이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/89#issuecomment-5470778278)에
결과가 기록됐습니다. CI report stats는 ici WARN(TEM 4.83, Pass 8, Warn 4, line 87.8%,
function 96.6%, branch 78.9%), viewer PASS(TEM 4.89, 7/7 tests)였습니다. [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/89/)
는 HTTP 200·external script/stylesheet refs 0, [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/89/)
는 HTTP 200·external refs 0이었습니다.

---

## 📦 컴파일 맥락 내보내기

`ici export-compilation-context`는 검증 스위트를 실행하지 않고, 측정된
`compile_commands.json`을 개인정보 보호형 `ici.compilation-export/v1` JSON으로 내보냅니다.
기본 경로는 프로젝트 메타데이터와 컴파일 데이터베이스를 읽기만 하며 compiler, shell,
subprocess, 재귀 소스 스캔을 사용하지 않고 전역 기본 설정 파일도 새로 만들지 않습니다.

```bash
# 발견된 DB를 stdout으로 출력한다. 성공 시 stdout은 JSON 한 개뿐이다.
ici export-compilation-context

# project-relative POSIX DB를 선택해 checkout 밖의 임시 파일로 예쁘게 저장한다.
ici export-compilation-context \
  --database build/compile_commands.json \
  --output /tmp/ici-compilation-context.json --pretty

# DB가 없을 때만 명시적으로 CMake/qmake 준비를 허용한다.
ici export-compilation-context --prepare \
  --output /tmp/ici-compilation-context.json
```

`--database`는 프로젝트 루트 아래의 POSIX 상대 경로만 허용하며 루트 밖 traversal, 절대 경로,
Windows 경로와 symlink 탈출은 거부합니다. `--prepare`는 명시적으로 선택·설정한 DB와
auto-discovered DB가 모두 없을 때만 루트의 CMake/qmake 어댑터를 사용해
`build/ici-cmake-build` 또는 `build/ici-qmake-build`를 configure/build할 수 있으므로 파일과
외부 도구를 변경할 수 있습니다. 명시한 DB가 누락되거나 손상된 경우에는 다른 DB로 대체하지
않고 해당 오류를 반환합니다. `--output`의 기본값은
`-`(stdout)이고, 파일 출력은 같은 디렉터리의 임시 파일·flush·fsync·atomic replace를
사용합니다. 기존 regular file은 원자적으로 교체하며 허용된 symlink는 링크 자체를 교체해
referent를 쓰지 않습니다. 데이터베이스와 `ici.toml`, `dev.toml`, `pyproject.toml` 및
그 alias/special file은 출력 대상으로 사용할 수 없습니다.

출력은 정렬된 key와 최종 개행을 가진 결정론적 UTF-8 JSON이며 `--pretty`는 들여쓰기만
추가합니다. 데이터베이스 바이트 digest와 정규화된 semantic digest를 분리해 기록하고, 이
digest는 preflight가 immutable context로 캡처한 database snapshot의 identity이지 live-file
lease가 아닙니다. 현재 실행은 캡처한 frozen unit을 계속 사용하고, database 변경은 다음
preflight가 새 digest와 context로 캡처합니다. raw `argv`/`command`는 내보내지 않습니다. 프로젝트 내부
경로는 POSIX 상대 경로로,
외부 경로·sysroot는 `[external]`로, credential과 안전하게 공개할 수 없는 값은
`***REDACTED***`로 투영합니다. 실제 DB를 읽었다는 `evidence`는 `MEASURED`이지만,
외부 경로·redaction·unknown compiler·unmodeled option·diagnostic·unity build가 있으면
`comparison_state`는 `inconclusive`가 될 수 있습니다.

입력 DB는 32 MiB·200,000 entry, 한 argv는 32,768 argument·총 1 MiB, DB 전체의 확장
argument는 1,000,000개·32 MiB, command 문자열은 4 MiB로 제한됩니다. project-contained
response file도 깊이 4, 파일/aggregate 4 MiB와 동일한 per-row argument bound 안에서만 읽습니다.
`arguments`가 `command`보다 우선하고 shell은
호출하지 않으며, duplicate JSON key·비유한 수·비정상 파일·symlink 탈출과 malformed row는
제한된 diagnostic으로 처리합니다. 출력 자체도 32 MiB를 넘으면 쓰지 않습니다.

성공은 exit 0, 입력/경로 검증 실패·측정된 DB/usable unit 부재는 exit 2, fatal diagnostic이나
직렬화·쓰기 오류는 exit 1입니다. stdout 모드의 성공 출력은 JSON만 포함하고 오류는 stderr로
보냅니다. 기계 검증 계약은 배포 패키지에 포함되는
[`ici-compilation-export-v1.schema.json`](src/ici/schemas/ici-compilation-export-v1.schema.json)
이며, `scripts/build-pyz.sh`가 ZipApp 구성 단계에서 공개 schema 포함을 확인합니다.

## 📋 명령어 일람

| 명령어 | 설명 | 상세 가이드 |
|---|---|---|
| `ici analyze` | 테스트·배포 없이 정적 분석, 결과 `.ici/rust-v1/result.json`·오프라인 HTML 저장 (`--plan`, `--json`, `--events`, `--baseline`, `--require-full`) | [범용 분석 가이드](docs/general-analysis.md) |
| `ici report` | 저장된 결과를 다시 렌더링 (분석·도구 실행 없음) | [범용 분석 가이드](docs/general-analysis.md) |
| `ici init` / `ici diff` / `ici import-report` | 최소 설정 생성, 두 저장 결과 비교, SARIF/JUnit/LCOV 보조자료 가져오기 | [범용 분석 가이드](docs/general-analysis.md) |
| `ici setup` | 검증된 native archive 설치·업데이트(`--archive`/`--url` + `--sha256`), `--inspect`, `--rollback`, `--activate` | [native 설치 가이드](docs/native-install.md) |
| `ici verify` | 검증 엔진 일괄 실행 및 종합 대시보드 출력 (`--report`, `--html`, `--github-summary`, 선택적 `--publish`) | [사용자 가이드](docs/user-guide.md#2-검증-실행-ici-verify) |
| `ici export-compilation-context` | 측정된 compile database를 redacted `ici.compilation-export/v1` JSON으로 내보내기 (`--database`, `--prepare`, `--output`, `--pretty`) | [사용자 가이드](docs/user-guide.md#standalone-compilation-context-export) |
| `ici line` | 코드/주석/공백 분석 및 500/1000 라인 과대화 검증 | [엔진 레퍼런스](docs/engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 스타일/포맷팅 검증 | [엔진 레퍼런스](docs/engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 커버리지/TEM 스코어 산출 | [엔진 레퍼런스](docs/engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | 정적 타입 검사 | [엔진 레퍼런스](docs/engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici python-compat` | Python runtime `-VV`·compileall·선택 import 및 `requires-python`/syntax/API floor 검증 | [엔진 레퍼런스](docs/engine-reference.md#215--python_compat-python-runtime-호환성) |
| `ici complexity` | 순환 복잡도 및 중첩 깊이 분석 | [엔진 레퍼런스](docs/engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 검증 | [엔진 레퍼런스](docs/engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici thread-sanitize` | deep profile 전용 C++ ThreadSanitizer thread-safety 검증 | [엔진 레퍼런스](docs/engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | 죽은 코드 및 미사용 심볼 검출 | [엔진 레퍼런스](docs/engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 중복 코드 / Copy-Paste 감지 | [엔진 레퍼런스](docs/engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 처리 안전성 검출 | [엔진 레퍼런스](docs/engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |
| `ici cycle` | Python import / C++ include 순환 참조 탐지 | [엔진 레퍼런스](docs/engine-reference.md#210--cycle-순환-참조-탐지) |
| `ici cognitive` | 인지 복잡도 분석 (기본 비활성, 옵트인) | [엔진 레퍼런스](docs/engine-reference.md#211--cognitive-인지-복잡도) |
| `ici security` | 하드코딩 시크릿·약한 해시 등 보안 위생 패턴 탐지 | [엔진 레퍼런스](docs/engine-reference.md#212--security-보안-위생) |
| `ici resource` | 리소스 누수 및 가변 기본 인자 검출 | [엔진 레퍼런스](docs/engine-reference.md#213--resource-리소스-누수) |
| `ici build` | 아티팩트 컴파일, 패키징 및 `env.sh`/`env.csh` 생성 | [사용자 가이드](docs/user-guide.md) |
| `ici doctor` | 시스템/현재 지원 도구/파이썬 환경 진단 | [사용자 가이드](docs/user-guide.md#12-실행-환경-진단-ici-doctor) |
| `ici env` | 셸 환경 설정 스니펫 생성 (`--sh` / `--csh`) | [사용자 가이드](docs/user-guide.md) |
| `ici cache` | 사용자 로컬 분석 cache inventory 표시 및 `--clear` 정리 | [사용자 가이드](docs/user-guide.md#202-분석-결과-캐시) |
| `ici publish` | 기존 HTML/JSON 리포트를 `gh-pages`에 게시하고 sticky PR 댓글 갱신 | [CI/CD 연동 가이드](docs/ci-integration.md#12-pr-리포트-sticky-댓글-report-pr) |
