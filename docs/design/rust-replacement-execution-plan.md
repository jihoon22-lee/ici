# ici Rust 전면 교체 실행 계획

### Historical commands and publication composition (2026-10-29)

- `5e83831` connects both doctor routes and legacy brief/JSON to shared configuration/planning,
  deduplicated bounded version probes, explicit required tool policy and actual scope guidance.
  Eleven command cases passed in 1.09 s, including no-project-Python, disabled checks, runtime
  matrix, recorded C++ context without replay, timeout/truncation/cancellation and no state writes;
  transitive strict Clippy also passed (`doctor-gates-final.log`).

- Historical verify/family commands use the shared native runner and stored projections. Output
  alias/unowned-file checks precede execution; incompatible historical baselines preserve both
  original baseline and native result, without writing the requested replacement. Native-rich
  baselines and diff retain completed-check scope. Browser opening is an explicit detached user
  action; GitHub summaries append safely and annotations come from the stored result.
- Legacy return annotation inspection is a distinct optional estimated check beside the actual
  type provider. Missing type tools stay incomplete. The first CLI test exposed an empty raw
  finding identity in the annotation analyzer; `91d74d1` fixes it and validates the frozen
  annotation corpus through the shared Observation contract.
- Publication uploads stored bytes only, uses explicit HTTPS destinations/system roots, separates
  upload status from code verdict, and guards sticky comments using current PR head and run order.
  Hub branch creation uses the hub's own default commit; large diagnostics use bounded summaries
  while preserving saved artifacts. `native-command-composition-final.log` records five legacy,
  five publication and three setup command regressions, followed by strict transitive Clippy.
- `24a12f3` preserves ten frozen compilation-export v1 payloads (producer version excluded),
  including MSVC/wrapper/digest/privacy contracts. Eight ordinary export cases and actual
  CMake/Qt5/Qt6 preparation passed; default export does not recursively discover project sources.
- `0b0bd68` adds held-lease linker probes and a separate link-reachability variant. Actual GCC
  multi-target execution preserves only the intersection of discarded hidden owned functions,
  keeps full input integrity, and rejects forged overlays without a matching held lease.
  Full inventory closure, final candidate acceptance and public cutover remain open.

### Native distribution acceptance checkpoint (2026-10-29)

- `485b93e` adds the static musl build wrapper, verified isolated SDK/source inputs, full
  Rust/musl/LLVM notices, ELF dependency rejection, and permanent transport/archive harnesses.
  Eight distribution tests, strict Clippy/fmt, shellcheck and harness Ruff passed.
- Internal static archive SHA-256 `023bda385c0321aacb0c819d9830b31817ea121c7895900c37e9ee29bb825ed5`
  passed 28 actual bootstrap/update/rollback/failure/private-CA/no-Python offline cases, then
  stopped at the candidate's unimplemented doctor. This historical candidate is not final source
  acceptance. Expanded transport's 14 cases also passed.
- `c44e4f5` records six actual loopback HTTPS publication cases: trusted/untrusted private CA,
  GHES prefix/GitHub-style endpoints, verified request credentials/base64 payload, 403, malformed
  response and post-upload comment failure. Analysis result and HTML bytes remain unchanged.
- Current debug core SHA-256 `8466942511522a1724aa11aee6ff610c2dca914685293b6ed1a9bc887a193769`
  passed 12 setup calls across XDG-only and conflicting-HOME environments. Default install,
  inspect, update and rollback select the same XDG root without touching HOME's default tree;
  `distribution/current-setup-defaults.log` records the actual run. Final candidate/public bytes
  and the complete migration inventory remain separate open gates.

### Explicit preparation integration evidence (2026-10-29)

- The common run lifecycle prepares leased native build contexts, then measures compiler outputs
  and publishes them through the shared artifact writer. Frozen user inputs remain immutable;
  only declared generated roots may change during preparation. Tool replacements invalidate
  completion evidence. A binary-specific 256 MiB allowance retains ordinary 16 MiB source and
  512 MiB aggregate limits, with no-follow binary intake.
- `cli-preparation-e2e.log`: actual CMake/GCC build through `execute(build)` passed, including
  `v1.2.3/x86_64/bin/suite`, unchanged user source and complete artifact check evidence (9.52 s).
  `binary-snapshot-gates.log`: six snapshot regressions and CLI check passed.
- `preparation-composition-gates.log`: ordinary application/CLI/execution suites and strict
  transitive Clippy passed. Explicit external-tool cases remain separate ignored acceptance
  targets; this does not close the full inventory or authorize a partial stable release.

2026-10-28 사용자는 ici를 Rust로 전면 교체하고, 세 제품을 간편한 사용성 → 독립 제품 완성도 → 상호 연계 순으로 개선하기로 결정했다. 일반 프로젝트는 좋은 기본값으로 동작하고 설정 파일 하나에서 필요한 예외만 선언한다. 기존의 다양한 분석 기능과 증거 수준을 유지한다.

2026-10-28 사용자가 이 계획 R0–R9의 실제 구현·검증·문서·PR 병합·버전/태그·공개·공개 bytes 검증·소유 자원 정리를 승인했다. 기준 소스는 main 77554433209ceaecd0782fc4d3cb6c2ed9b0b1c2, 공개판은 v0.13.0이다. clean 준비 branch c39f37e에서 계획 commit을 포함한 feat/rust-native-core를 생성해 실행을 시작했다. 부분 완료를 전체 전환 완료로 표시하지 않는다.

## 1. 전면 교체의 경계

- 새 ici의 CLI, 설정, 발견, 실행기, 자체 분석, 결과 처리, report, publish, 설치·업데이트 로직은 Rust로 구현한다.
- 최종 제품에 ici 소유 Python 분석 엔진, Python worker, embedded CPython, 설정에 따른 Python backend fallback을 남기지 않는다. 단순 Rust launcher로 기존 Python 제품을 감싸는 구현은 완료가 아니다.
- 프로젝트 Python·pytest·coverage·명시한 mypy, Cargo, Node/ESLint/tsc, compiler·Qt는 해당 프로젝트를 검사하는 외부 도구다. ici 자체의 런타임과 구분한다.
- 명시적인 Python runtime/compatibility/import 검증에 필요한 짧고 제한된 interpreter probe는 허용한다. R0에서 허용 목적·argv·입출력·부작용·timeout을 열거한다. AST·resource·dup·TEM 등의 자체 분석을 이런 probe로 우회 실행하지 않는다. 자체 .py 모듈·상주 worker·qmake Python recorder는 제품에 남기지 않는다.
- 개발 과정의 PR·커밋·내부 후보는 나눌 수 있다. 최종 공개 제품은 전체 인수 조건을 만족한 뒤 한 번에 Rust 경로로 전환한다. 부분 Rust 제품이나 Python/Rust 선택형 제품을 stable로 내지 않는다.
- Python 기준 구현은 전환 중 비교 시험에만 사용한다. 최종 Python 제품 소스·dispatch·packaging/gate의 제거는 cutover 변경에 포함하고, 과거 공개 artifact와 Git history는 보존한다. 테스트용 Python 소스 fixture와 외부 oracle harness는 제품 runtime이 아니다.

## 2. 유지할 사용자 경험과 기본 설정

### 2.1 일반 사용

권장 설치 한 번 뒤 프로젝트 폴더에서 ici analyze를 실행하면 언어·파일·기존 도구 설정을 발견한다. 기본 출력에 중요한 finding의 파일·행·설명과 미완료 이유를 표시하고 결과와 오프라인 HTML을 찾는 동선을 제공한다. 내부 cache·schema·engine 용어는 기본 결과를 압도하지 않도록 상세 설명으로 보낸다.

analyze는 정적 분석을 선택하며 테스트·배포를 자동 실행하지 않는다. Cargo build.rs/proc-macro나 TS metadata처럼 분석에 수반되는 실행 효과는 plan에 드러낸다. verify·test·build·publish의 풍부한 기존 기능은 명시적으로 사용할 수 있어야 한다. 필수 분석 도구 부재를 숨겨 PASS를 만들지 않고 가능한 검사는 수행한 뒤 누락 범위와 준비 방법을 알려준다.

### 2.2 한 설정 모델

ici.toml과 Python 프로젝트의 pyproject.toml [tool.ici]를 같은 설정 모델의 두 표현으로 제공한다. 둘 다 있을 때는 ici.toml을 우선하고 사용한 출처를 표시한다. 명시 --config는 선택한 파일과 내장 기본값으로 실행한다. 사용자 전역 선호는 출력·cache·로컬 도구 경로 등에 제한하며 팀 검사 정책을 은밀히 바꾸지 않는다.

새 sparse 설정 형식의 제안은 다음과 같다. 기존 next schema_version=1과 legacy [ici]/[engines] 형식을 혼동하지 않도록 R0에서 새 schema 버전과 호환 reader를 고정한다.

    schema_version = 2
    profile = "standard"
    extend_exclude = ["generated/**"]

pyproject.toml에서는 같은 키를 [tool.ici] 아래에 둔다. Ruff/ty/mypy/Cargo/TS 설정은 원래 도구가 읽는다. ici 설정에 복제하거나 하나의 임의 merge 규칙으로 다른 도구의 discovery 의미를 바꾸지 않는다.

설정 규칙:

1. components 생략은 자동 발견이다. 명시 components는 선언 범위를 따른다. 명시적인 빈 components는 검사하지 않는 오류이며 자동 발견으로 되돌리지 않는다.
2. no-config, 새 최소 설정, 새 init 출력은 같은 대상·기본 제외·검사 선택을 만든다. init은 같은 resolver의 최소 선언을 저장한다. 검사 범위를 별도 알고리즘으로 추정하지 않는다.
3. 사용자 scalar만 교체하고 map은 key별 합성한다. exclude 전체 교체와 extend_exclude 추가를 구분한다. 인자·순서 있는 목록을 무조건 concatenate하지 않는다.
4. 프로젝트 root·하위 cwd·중첩 workspace 경계와 상대경로 기준을 고정한다. node_modules/build/target/.venv 같은 기본 제외를 config 파일 존재 여부로 잃지 않는다.
5. monorepo의 Cargo workspace/package, JS/TS package, Python package와 C/C++ compile context를 발견하되 서로 다른 build variant를 디렉터리 순서로 선택하지 않는다. 복수의 의미 있는 선택만 사용자 선언을 요구한다.
6. 기존 명시 component의 범위를 사용자의 의도 없이 확장하지 않는다. 구 init이 생성한 설정의 문제는 설명·migration preview로 드러내며 원본을 자동 수정하지 않는다.
7. 설정 설명에서 최종 값·파일·행·기본/발견/명시 여부를 확인할 수 있게 한다. 생성·수정·migration은 주석·알 수 없는 미래 schema·사용자 원본을 보존한다.

## 3. R0에서 먼저 고정할 설계 결정

사용자의 Rust 전면 교체 결정은 확정이다. 아래는 담당자가 근거를 만들어 결정할 엔지니어링 checkpoint이며, Rust 여부를 다시 묻는 단계가 아니다. 각 행에 대체 방식·검증 근거·영향을 받는 PR·완료 상태를 기록한 뒤 의존 작업을 시작한다.

| 항목 | 결정할 내용과 완료 조건 |
|---|---|
| 규약 대체 | ADR-0001의 Python 유지, ADR-0002의 CPython bundle, AGENTS §3/4/6/8/9, SPEC-05의 gate 이행을 새 Rust ADR로 명시적으로 supersede. 시스템 CA·no-root·원본 보존·증거 축·PR 규칙은 유지 |
| 전체 기능 대응표 | 현재 19 descriptor·36 check와 모든 명령/옵션/config key/report/비검사 기능의 합집합을 기계적으로 수집. 단순 36개 구현 완료를 전체 parity로 주장하지 않음 |
| Python parser/tokenizer | ruff_python_parser 계열을 우선 평가하고 실제 구문·source map·scope corpus, API 공급 방식, revision pin, license, 정적 빌드 가능성 검증. RustPython/Parser README의 대체 안내를 반영. 기억에 의존해 parser를 확정하지 않음 |
| 언어·위치 의미 | 지원 Python 구문 범위와 프로젝트 target version, encoding/BOM/CRLF, Unicode byte/문자 column, f-string·match·except-star·type syntax, 구문 오류·자원 상한의 처리 고정 |
| 자체 코드와 외부 probe | Python import-only/버전/sysconfig 등 허용 probe 목록 작성. importlib import와 python -m 실행 차이 검증. qmake recorder는 같은 Rust binary의 내부 진입점으로 대체 |
| 도구 준비 | 표준 설치에서 native Ruff/ty 준비를 우선 목표로 삼고 버전·license·target·CPU/libc·digest를 실제 검증. 프로젝트 compiler·Python·Node plugin은 프로젝트 의존성. 도구 동봉과 ici Python backend를 혼동하지 않음 |
| identity와 이전 baseline | fingerprint·policy/toolchain/scope/cache digest의 호환 표와 check별 비교 가능 조건을 고정. 읽기 호환과 resolved 판정 가능성을 구분 |
| CLI/config/report 호환 | legacy 명령과 결과 v3의 의미를 Rust 공통 application에서 제공할 projection으로 매핑. 기존 기능을 alias만 만들고 누락하지 않음 |
| 배포·상태·복구 | Linux/WSL x86_64 target, archive·native tool pack·installer, 새 state/cache namespace, 구 config/result 보존, PATH 충돌 안내, ICI_PYTHON 처분 고정 |
| publish 네트워크 | 시스템 CA·사설 CA·proxy·TLS 실패·GHES endpoint를 지원하는 transport 선택. 로컬 사설 CA 서버 fixture와 게시 실패 축 분리 검증 |
| 성능 기준 | 고정 corpus의 cold/warm 시간·peak RSS·output size·취소 반응·report 열기 비용을 측정하고 수용 예산 설정. Rust라는 이유로 성능 향상을 확정하지 않음 |

하나의 Cargo workspace에서 domain/config/discovery/execution/provider/analysis/reporting/CLI 경계를 먼저 둔다. crate 수, 함수 이름, 자료구조는 의존성을 보고 담당자가 정한다. 동적 plugin ABI, 새 daemon, ici MCP/LSP/TUI, 공통 세 제품 framework는 이번 목표가 아니다.

## 4. 기능 대응표의 필수 범위

현재 소스에서 직접 확인한 registry는 다음과 같다. 이 개수는 누락 방지 입력이며 완료 점수가 아니다.

| 축 | 현재 항목 |
|---|---|
| legacy descriptor 19개 | line, lint, compile_db, test, type, python_compat, cognitive, resource, security, cycle, complexity, sanitize, thread_sanitize, dead, dup, exception, build, binary_compat, integration |
| Python check 16개 | line, lint, format, type, test, coverage, complexity, cognitive, cycle, dup, security, resource, exception, dead, compat, compat-runtime |
| C/C++ check 15개 | line, compile, diagnostics, tidy, test, coverage, complexity, cognitive, cycle, dup, exception, artifact, sanitize, tsan, binary-compat |
| 추가 check 5개 | rust.clippy, javascript.lint, typescript.lint, typescript.type, integration |

대응표의 한 행은 기존 진입점·설정·언어/조건·rule/metric·provider·실행 효과·evidence/gate/exit·출력·Rust 구현·양성/음성/실패 fixture를 연결한다. status는 미착수/구현/검증/차이 검토/완료로 구분한다.

특히 다음 기능을 빠뜨리지 않는다.

- 19→36 대응에서 Python sanitize, C++ linker 기반 dead code, compile database와 build 산출물 검사, compiler diagnostics/tidy/clazy/Qt 생성 입력을 실제 실행 경로로 대조한다. check 이름이 없다는 이유로 제거하지 않는다.
- Python AST/token 기반 자체 metrics, cognitive, resource lifetime, security scope, exception, dead code, import cycle, semantic duplicate, packaging/wheel/버전 호환 규칙을 Rust로 옮긴다.
- C/C++ compile argv/cwd/response file, CMake/qmake/Make, generated/external include, build variant, linker/ELF 근거와 기존 bounded heuristic 표기를 보존한다. 이식 과정에서 추정 분석을 exact로 승격하지 않는다.
- Python/C++ test·coverage·TEM·test quality, sanitizer/TSan, packaging/build, ABI, declared integration, runtime compatibility/import 검증을 실제 외부 도구로 검증한다.
- config discovery/migration/doctor/env/cache, 계획·DAG·resource lock·process groups·timeout·취소·자손 cleanup·부분 결과·redaction·원자 저장을 포함한다.
- console/JSON/Markdown/offline HTML/SARIF, baseline/suppression, imported SARIF/JUnit/LCOV, publish/GHES/보고서 게시를 포함한다.
- ici.next.run v2, event v1, auxiliary v1, 기존 result v3/이전 reader, compilation export의 계약을 매핑한다. schema 파일이 없는 plan/doctor/publish JSON은 고정 golden으로 먼저 기록한다.
- C++/Qt viewer는 Python 엔진 교체 대상이 아니므로 기존 기능과 입력·배포 계약을 유지한다. runner_win처럼 현재 지원 범위 밖의 구현도 처분 이유를 기록하며 조용히 삭제하지 않는다.

같은 분석 의미를 legacy와 새 경로에서 두 번 구현하지 않는다. 공통 Rust rule/provider/application 결과에 legacy config·command·serialization projection을 둔다. 36개 check만으로 표현되지 않는 기존 동작은 대응표에 추가하고 적절한 공통 실행 경계에 넣는다.

## 5. 도구와 native 분석 구현 조건

### 5.1 도구 선택과 설치

명시 tool 경로 → 프로젝트에 준비된 동일 도구 → 검증된 ici native tool pack → 문서화된 PATH fallback 순으로 후보를 평가한다. 명시 후보가 깨졌을 때 다른 도구로 조용히 바꾸지 않는다. project-local Ruff와 그 버전 제약을 동봉본으로 덮지 않는다.

프로젝트의 mypy.ini/[tool.mypy]/setup.cfg [mypy]는 타입 검사 선택의 근거로 존중한다. ty 설정도 동일하게 취급하며 충돌은 설명 가능한 선택을 요구한다. 기존 mypy를 ty로 바꾸는 것은 provider 변경이므로 결과·baseline 동등성을 가정하지 않는다. 설정 없는 새 프로젝트의 권장 ty 정책은 R0에서 노이즈·심각도·advisory/gate 기준을 fixture로 정한다. 초기 권고는 발견 사항을 보존한 advisory이며 검사 미실행을 통과로 만들지 않는다.

표준 설치가 기본 native 분석 도구를 준비하도록 한다. 분석 중 암묵적인 pip/npm/uv 설치는 하지 않는다. 설치·명시 setup 단계에서 선택한 pack을 검증하고 버전·digest·license·target을 기록한다. Python이 없는 환경에서도 Rust core와 기본 자체 Python 소스 분석을 실행할 수 있어야 한다. 프로젝트 runtime 검증만 해당 외부 도구를 요구한다.

### 5.2 의미와 위치

parser가 만든 AST만으로 끝내지 않고 binding·import alias·shadowing·global/nonlocal·comprehension/async scope를 현재 규칙의 의미에 맞게 구현한다. 구문 오류의 recovery 결과를 온전한 파일 분석으로 표시하지 않는다. CPython 버전별 oracle 차이는 지원 범위와 의도한 개선으로 별도 기록한다.

Python tokenize, regex lookaround, fnmatch 경로 매칭, round/부동소수점·TEM 경계, JSON 문자열 escaping/key 정렬, fingerprint normalization은 Rust 기본 라이브러리와 의미가 같다고 가정하지 않는다. 비ASCII 위치, malformed/대형 입력, 양성·음성 규칙 corpus로 검증한다. PEP 440/508/wheel metadata는 검증된 Rust 구현을 평가하며 Lens를 제품 라이브러리로 강제 의존하지 않는다.

### 5.3 baseline과 저장

가능한 finding fingerprint normalization은 보존한다. 비교는 check별로 fingerprint·rule/provider 정책 호환성을 먼저 확인한다. 호환되는 항목은 new/unchanged 비교를 유지할 수 있지만, 이전 scope 증거가 부족하면 사라진 항목을 carried로 남기고 resolved를 확정하지 않는다. fingerprint 자체가 비교 불가한 검사는 대량 new로 실패시키지 않고 비교 제한과 새 baseline 생성 경로를 제공한다. --fail-on-new의 상태·exit와 전체/부분 비교의 v2 표현은 R0에서 golden으로 고정한다.

같은 경로의 도구 교체, ESLint/TS/Cargo 설정·확장 설정·plugin/lockfile 변화, native tool pack 변경을 identity에 반영한다. 의존 입력을 전부 확인할 수 없으면 재사용·resolved 판정을 제한한다. Rust binary의 build identity를 cache 입력으로 쓰며 현재 Python 소스를 AST로 읽어 자체 cache key를 만드는 방식을 유지하지 않는다.

기존 config·baseline·suppression·saved result를 새 형식으로 자동 덮어쓰지 않는다. 새 state/cache는 분리하고 migration은 preview와 새 출력/백업을 제공한다. 오래된 결과의 읽기 호환과 비교 가능한 증거 여부는 별개다.

## 6. 작업 묶음과 의존성

| ID | 작업 | 선행 | 완료 기준 |
|---|---|---|---|
| R0 | Rust 전환 ADR·규약 대체, 기능/계약 원장, parser·도구·identity·배포 spike | 없음 | §3 결정표와 §4 대응표 확정, 고정 oracle·known-bug 기대값·지원 범위·성능 예산 준비 |
| R1 | Cargo 기반 domain/config/discovery/CLI/plan | R0 | 최소·부분 설정, init 의미 동등, native config 재사용, 명령/옵션 registry, 설치·실행 없는 plan |
| R2 | 프로세스 실행·DAG·환경·취소·snapshot·identity·결과 저장 | R1 | process group/timeout/cleanup/bounded output/저장 실패/partial result/exit130, v2/v1 계약 fixture |
| R3 | 외부 provider와 도구 준비·진단 | R2 | Ruff/ty/mypy/Clippy/ESLint/tsc/compiler/tidy 등 실제 버전과 replay/E2E, 명시 선택·부재·깨진 출력 처리 |
| R4 | Rust 자체 Python 분석과 호환 규칙 | R1, R2, parser 고정 | scope·위치·각 규칙 양성/음성/실패 fixture, Python 없는 core 실행, 모든 기존 자체 분석 행 증거 |
| R5 | C/C++·Qt·build context와 자체 분석 | R1, R2 | 실제 compiler 입력·qmake recorder·CMake/Make·생성 입력·linker/ELF·variant 및 heuristic 표기 검증 |
| R6 | test/coverage/TEM/sanitizer/build/ABI/integration/runtime 검증 | 필요한 R3–R5 | 실제 도구 E2E, 테스트 0·coverage 결측·분모·중단·산출물 소유권·import-only 의미 검증 |
| R7 | baseline/suppression/cache/report/import/publish/legacy projection | R2부터 시작, R3–R6 통합 | known bug 교정, 위치·판정·redaction·offline HTML·TLS/게시 실패, legacy 명령·config·result reader 동등성 |
| R8 | native package·installer·업데이트/복구·후보·parity closure | R0–R7 | 원장 미판정/회귀/기능 누락 0, 깨끗한 환경 E2E, 재현성·license·실행 trace·cross-repo 후보 확인 |
| R9 | 단일 제품 cutover PR과 공개·정리 | R8, 해당 작업의 공개 승인 | 모든 제품 경로 Rust, Python 제품 source/dispatch/packaging 제거, 동일 검증 bytes 공개, 새 다운로드·설치·복구 확인 |

R3/R4/R5는 R1/R2의 계약 이후 독립 범위로 나눌 수 있다. report의 순수 렌더링과 baseline 설계는 실행기 이후 먼저 진행할 수 있지만 미완성 result를 임의 schema로 고정하지 않는다. parser·source map·identity 같은 공유 계약을 확정하기 전에 규칙 구현을 병렬로 흩어 놓지 않는다.

## 7. 동등성과 사용자 인수 검증

비교 기준은 공개 v0.13.0 source 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188, pyz SHA-256 c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde다. oracle의 interpreter·외부 도구 버전·환경도 고정한다. 기존 Python 기능은 동결하고 긴급 correctness/security 유지보수는 별도 변경으로 기록한다. oracle을 바꿀 때는 태그·digest와 기대값 차이를 명시하며 움직이는 main을 oracle로 쓰지 않는다.

일반 0.13.x 유지보수는 최종 Rust cutover에서 종료하며 영구 병행 제품 라인을 만들지 않는다. 이후 예외적인 보안 수정이 필요하면 이전 태그 기반 별도 작업으로 명시적으로 범위를 정한다. 과거 artifact의 취득·이력 보존은 계속 유지한다.

차이는 보존할 동작, 확인된 결함 교정, 의도한 계약 변경으로 분류한다. 시간·임시 경로 같은 변동 필드만 정규화한다. finding 누락·위치·severity·scope·metric 분모·완결성·exit를 정규화라는 이름으로 지우지 않는다. HTML은 DOM/텍스트·정보 보존·외부 CDN 부재로 검증한다. golden을 무조건 재생성해 회귀를 통과시키지 않는다.

| 인수 항목 | 필수 증거 |
|---|---|
| 기본 사용 | Python/Node/Rust/C++ 대표 프로젝트에서 기본 명령으로 유용한 진단·위치·다음 행동 확인. 특수 설정은 필요한 경우만 요구 |
| 설정 일관성 | 무설정·새 최소 설정·새 init 파일의 scope/check 동등, root source 누락 방지, generated/vendor 오인 방지, 한 값 override의 나머지 보존 |
| 자체 분석 | 모든 규칙의 양성·음성·구문 오류·Unicode 위치·복잡한 scope·중복 코드 golden, 기존 지원 한계 보존 |
| provider | 기록 출력 replay와 실제 도구 fixture 둘 다. 같은 경로 도구 변경, 설정·plugin 변경, 부재·timeout·출력 절단 |
| 전체 verify | Python/C++ tests·coverage·TEM·sanitizer/TSan·build/ABI/integration·Qt variant, 미실행·불완전·0개 테스트 구분 |
| baseline/import | ESLint 규칙 off를 resolved로 오판하지 않음, old identity 보수적 비교, SARIF uriBaseId 매핑/거부, JUnit line 기준·LCOV subset 보존 |
| 결과·게시 | v2/event v1/legacy v3/auxiliary 계약, plan/doctor/publish JSON golden, 오프라인 HTML, local private CA/proxy와 게시 실패 |
| native 제품 증명 | Python 없는 PATH·네트워크 차단 환경에서 자체 분석, 배포 파일 목록과 subprocess trace에 ici Python backend 없음 |
| 이전·복구 | uv/pip/pyz 설치 사용자 PATH 충돌 확인, 기존 .venv/bin/ici를 가리키는 idk task 안내, 구 자료 미변경, 이전 공개판 명시 복귀 |
| consumer | idk 공개판/후보가 v2 결과를 소비하고 Lens evidence는 imported/NOT_EVALUATED로 첨부. consumer가 요구하는 publication/findings 등 producer profile을 fixture로 고정 |

Rust fmt/Clippy/workspace 검사, 실제 도구 corpus, package smoke, 실패 주입을 증거에 연결한다. Python unit test 개수나 새 Rust test 개수는 parity의 대체 지표가 아니다. 전체 원장의 미판정·회귀·기능 누락이 없어야 R8을 닫는다. 새 플랫폼은 별도 실제 검증이 없으면 지원하지 않는다.

## 8. 단일 cutover와 릴리스

Rust 변경은 정상 feature branch→PR→main 경로로 검토한다. cutover 전에는 Rust CI를 추가하고 현재 공개 Python 제품의 필수 gate를 유지한다. Rust 내부 candidate는 공개 stable로 취급하지 않는다. R0에서 기존 규약의 적용 범위를 분리해 Rust 소스에 pyz 전용 제약을 적용하지 않되 기존 제품 gate를 먼저 삭제하지 않는다.

R9 한 cutover 변경에 제품 진입점, installer, release workflow, 문서, 라이선스, active Python source/dispatch 제거와 대응 gate 전환을 포함한다. 전환 직전 모든 지원 기능이 Rust에서 동작해야 한다. 신제품 archive 안에 구 pyz·worker·CPython fallback을 넣지 않는다.

주 배포는 검증한 Linux/WSL x86_64 native archive다. core와 native tool pack의 target·libc·CPU·재배포 조건은 R0/R8 실측으로 고정한다. 검증되지 않은 구성까지 정적 binary라고 표시하지 않는다. 사용자 runtime 준비를 줄이는 rootless 설치·업데이트 경로를 제공하고 기존 archive 설치도 유지한다.

새 Python engine wheel/sdist/pyz는 cutover 이후 발행하지 않는다. native payload만 담은 platform wheel은 별도 편의 배포로 검토할 수 있으나 이번 완전 교체의 필수 조건은 아니다. PyPI의 ici 이름 소유를 가정하지 않는다. uv/pip 설치 제거·PATH 우선순위·ici --version 확인·기존 작업의 절대경로 갱신 안내를 제공한다. doctor는 충돌하는 ici 실행 파일을 확인할 수 있어야 한다.

과거 공개판은 그대로 보존한다. rollback은 검증된 이전 artifact를 명시적으로 사용하는 절차이며 새 사용자 데이터를 옛 snapshot으로 덮는 동작이 아니다. 기존 config/result/baseline/suppression은 보존하고 새 형식은 별도 출력과 versioned state를 사용한다. ICI_PYTHON을 새 의미로 조용히 재사용하지 않는다.

버전 후보는 0.14.0을 검토할 수 있으나 실제 compatibility·완료 checkpoint에 따라 결정한다. 1.0을 사용하려면 별도의 안정 API·지원 계약 근거가 필요하다. 2026-10-28 후속 실행 지시로 태그·공개까지 승인됐다. R8 전체 인수 후 exact-main 검증 후보와 같은 bytes를 게시하고 새 다운로드·설치·실행을 확인한다. 소유 branch/worktree/tmp만 정리한다.

## 9. 작업 배정과 착수 지시문

ici 책임자는 전체 parity 원장과 R0–R9를 소유한다. 하위 구현을 위임할 때 parser/source map, execution/domain/wire 계약을 먼저 고정하고 서로 다른 모듈·worktree를 지정한다. idk/Lens 저장소를 직접 편집하지 않는다. 통합 책임자는 고정 fixture·버전 조합·공개 bytes 증거를 조정한다. 세 제품의 릴리스를 동시로 묶지 않는다.

준비 branch docs/rust-replacement-plan의 commit c39f37e를 포함한 feat/rust-native-core에서 실행한다. 첫 R0 PR로 계획·새 ADR·규약 적용 범위를 main에 반영한다. 이후 Rust 작업도 main 대상 일반 PR을 사용한다. 무거운 Rust/tool-pack build와 전체 corpus는 다른 두 저장소와 host 자원을 조정하고 독립 계약 이후에만 병렬화한다.

각 작업 보고에는 요구 행, source SHA, 구현 파일, 실제 명령·도구 버전·결과, 의도한 차이, 미실행 항목, 다음 의존성을 남긴다. 시작·중단·재개 시 같은 원장을 갱신하고 일부 parser/provider 완료를 제품 전환 완료로 보고하지 않는다.

다음 착수 지시문은 2026-10-28 사용자의 후속 지시로 실행 승인됐다.

> ici를 이 문서 R0–R9에 따라 Rust 단일 제품으로 전면 교체하라. 사용자 우선순위는 좋은 기본값과 간단한 설정, 자체 완성도, 마지막으로 연계다. AGENTS.md와 과거 ADR을 읽고 R0에서 Rust 전환 규약을 명시적으로 대체하라. 현재 19 legacy descriptor와 36 check를 포함한 기능·명령·설정·출력 합집합의 대응표를 만들고, Python backend/worker 없는 native 분석·실행·report·publish·배포를 구현하라. 기존 규칙의 양성·음성·실패 사례와 고정 oracle을 이용하되 알려진 결함은 교정 기대값으로 검증하라. 개발 PR은 나눌 수 있지만 모든 행이 닫히기 전 stable replacement나 혼합 제품을 내지 말라. 이전 자료와 공개판을 보존하고 새 사용자 데이터의 되돌림을 강제하지 말라. 저장소별 책임과 전달받은 승인 범위 안에서 PR·후보·공개·정리를 완료하라.

## 10. 진행 기록과 근거

| 항목 | 상태 |
|---|---|
| Rust 전면 교체 방향 | 사용자 결정 반영 |
| 현행 registry 19/36과 알려진 결함 | 확인 |
| 독립 계획 검토 | Claude·Codex 교차 검토 및 조정 완료 |
| R0 | 설계·spike 검증 완료, 첫 PR gate 대기 — [결정표](rust-replacement/decisions.md) |
| R1/R2/R4 | 공유 domain 계약을 바탕으로 config/execution/native Python 구현 착수 |
| R3/R5–R9 | 미완료 — 선행 구현/전체 인수 이후 실행 |
| 후보·PR·릴리스 | 미착수 |

근거: [현재 Python 유지 ADR](ici-next/adr/0001-keep-python-core.md), [CPython 배포 ADR](ici-next/adr/0002-standalone-runtime-bundle.md), [이전 engine inventory](ici-next/inventory/current-engines.md), [현재 registry](../../src/ici/languages/registry.py), [기본 설정](../../src/ici/config/automatic.py), [CLI](../../src/ici/cli/next_path.py), [identity](../../src/ici/application/check_scope.py), [qmake recorder](../../src/ici/core/qmake_context.py), [runtime compatibility](../../src/ici/engines/python_compat.py).

외부 기술 후보의 현재 근거: [Ruff](https://github.com/astral-sh/ruff), [ty](https://github.com/astral-sh/ty), [RustPython Parser의 대체 안내](https://github.com/RustPython/Parser). 이름이나 언어만으로 배포·문법·성능 지원을 확정하지 않고 R0 실측으로 고정한다.


### 실행 체크포인트 — 2026-10-28 착수

- 원본 checkout clean, 준비 HEAD `c39f37e8c2ad07d4637a06246b94316a50b8e517` 확인.
  구현 branch `feat/rust-native-core`; 제품 baseline은 main `7755443`, oracle은 공개 source `6fe3e405`.
- R0 규약/공통 계약은 주 담당자, 기계적 전체 surface inventory, parser spike,
  tool-pack/TLS spike는 서로 다른 파일 범위로 위임했다. 공유 계약 전 rule 구현은 분산하지 않는다.
- 무거운 빌드·전체 suite·corpus는 `flock /tmp/paseo-products-20261028-heavy.lock` 아래에서
  jobs 2로 실행한다. 이 lock과 다른 제품/공유 cache는 개별 작업자의 삭제 대상이 아니다.
- 작업 소유 임시 디렉터리: `/tmp/ici-rust-replacement-20261028`.
  제품 cutover·공개 검증까지 완료한 뒤 지우며, 최종 근거는 이 문서와 PR에 보존한다.
- 현재 R0 진행은 제품 완료가 아니다. R1–R9 및 전체 parity 원장은 여전히 열린 상태다.


### R0 실행 근거와 구현 경계

- 19/36뿐 아니라 37 CLI nodes·115 parameters·322 config keys·271 rule/metric 생성 지점,
  25 별도 계약·6 schema·291 source-file audit를 고정 Git 객체로 수집했다.
  `scripts/rust_migration_inventory.py --check --self-test` 통과. `--require-closed`는
  미완료 1138개 행을 거부한다. 이 거부를 gate 완화로 없애지 않는다.
- parser 37 native fixture 및 CPython 3.10–3.14 oracle 비교, static PIE/Python 없는 PATH
  실행·trace 검증 완료. native Ruff/ty 8개, system/private CA/proxy TLS 12개 fixture 통과.
- 고정 source의 plan/doctor/publish와 cold/warm analyze/report를 실제 실행하고 JSON·시간·RSS를
  보존했다. known-bug 교정 기대값과 R8 수용 목표는 [결정표](rust-replacement/decisions.md)에 있다.
- 후속 구현 branch로 옮길 로컬 Rust domain prototype에서 공개 v1/v2 JSON roundtrip, Unicode fingerprint v3, event gap/unknown kind,
  잘못된 증거 거부 fixture 5개 통과. 이 첫 R0 PR은 해당 제품 코드를 포함하지 않으며
  R1 PR에서 코드와 함께 재검증한다. 전체 domain/제품 parity 완료가 아니다.
- config/discovery, 공통 process/DAG/storage, native Python 분석은 공유 타입·파일 경계를 정해
  병렬 구현한다. R3+ 통합과 R8/R9 release closure는 남아 있다.


### R1 공통 모델 구현 — 후속 branch

- R0 evidence PR: https://github.com/jihoon22-lee/ici/pull/298 (`34f11e0`),
  CI `40692022861` 진행. 제품 개발은 `feat/rust-application`에서 이어간다.
- Rust 1.99.1 Cargo workspace와 순수 `ici-domain`을 구현했다. result/event status 축,
  check evidence, finite metric, all inspected targets, task/observation, snapshot identity를
  공통 타입으로 고정했다. frozen 19/36 catalog는 도구 probe 없이 읽는다.
- `cargo test -p ici-domain --locked` (registry 1 + wire 5),
  `cargo clippy -p ici-domain --all-targets --locked -- -D warnings`, domain fmt 통과.
  v1/v2 정보 보존, malformed/inconsistent evidence 거부, Unicode/astral fingerprint,
  event gap/unknown/truncation 및 task completion/evidence 차이를 확인했다.
- config/discovery, execution/DAG/storage, native Python 분석 crate는 별도 범위에서 구현 중.
  전체 workspace test/CLI E2E는 아직 수행 전이며 R1/R2/R4 전체 완료로 기록하지 않는다.

### 재개 체크포인트 — 도구 선택 기반

- 첫 R0 CI `40692022861`은 새 ADR-0008의 문서 분류 목록 누락 1건으로 실패했다
  (4302 passed / 8 skipped). 목록/ADR index를 갱신한 `5ae4a9c`의 focused 57개 검사는 통과했고
  수정 CI `40693618746`을 확인 중이다. 기존 문서 gate는 유지했다.
- `ici-providers`의 실행 없는 resolver를 구현했다. 명시 경로 → 프로젝트 동일 도구 →
  manifest/digest가 검증된 native pack → PATH 순서이며 깨진 명시 경로는 오류다.
  native executable bytes를 streaming SHA-256으로 식별하며 plan에서 version probe를 실행하지 않는다.
- mypy/ty native 설정을 존중하고 둘 다 선언된 경우 명시 선택을 요구한다.
  `cargo test -p ici-providers --locked`의 선택·도구 교체·pack 변조·부재·타입 설정 5개와
  해당 crate Clippy `-D warnings`, fmt가 통과했다. 진단 parser/실제 도구 실행을 포함한
  R3 전체와 CLI 통합은 계속 미완료다.

### 실행·분석·application 라이브러리 checkpoint

- `7c2c336`: Rust 실행·scheduler·storage library의 process 11 / scheduler 8 / storage 9개
  실제 검증, crate Clippy `-D warnings`, fmt 및 Python 동작 fixture Ruff가 통과했다.
  digest가 알려진 실행만 공유하며 계획 후 executable 교체도 spawn 전에 거부한다.
  [실제 명령·범위](../../tests/rust-parity/execution/README.md)는 process-group 증거와
  의도적으로 새 session으로 이탈하는 프로그램의 sandbox 보장을 구분한다.
- `26c3b17`: native Python decoder·Unicode/raw-byte 위치·lexical scope와 line 및
  cyclomatic/cognitive 공통 계산 기반을 추가했다. 고정 공개 source의 CPython 3.14.7
  oracle metrics 15개와 비교했다. 보안/resource/exception/dead/cycle/dup/compat 규칙의
  전체 이식은 후속 작업이며, 아직 구현되지 않은 check를 PASS로 반환하지 않는다.
- `ici-application`은 pure request/selection/graph/verification/baseline과 bounded immutable
  입력 snapshot을 담당한다. 비활성화·미선택·blocked, optional·advisory·required completion,
  부분 scope·위반·취소를 구분한다. provider/CLI·signal/event lifecycle 및 실제 report와
  묶인 제품 E2E는 별도 통합 단계다.
  shared heavy lock 아래 `RUST_TEST_THREADS=2 cargo test -p ici-application --locked`의
  planning 7 / baseline 4 / snapshot 3 / verification 5개가 통과했고,
  `cargo clippy -p ici-application --all-targets --locked -- -D warnings`와
  `cargo fmt -p ici-application --check`도 통과했다. configuration/native tool bytes가 실제
  어떤 dependency closure로 수집되는지에 대한 시험과 이 순수 비교 함수 시험은 구분한다.
- baseline 비교는 구 result의 읽기와 해결 증명을 구분한다. 일치하는 완료 check scope만
  resolved를 만들고, 변경된 scope는 carried, 알 수 없는 v1 scope나 바뀐 fingerprint/
  policy/toolchain은 incompatible로 남긴다. scope digest 생성 시 실제 plugin/설정 의존성의
  완전한 closure를 측정하는 provider 통합은 아직 남아 있다.
- 새 snapshot은 기본 파일당 16 MiB / 총 512 MiB를 제한하고 project source/config를
  읽을 때 symlink ancestor·leaf 및 FIFO를 거부한다. 자체 분석은 captured bytes를 쓰고
  외부 도구는 프로젝트를 읽으므로 실행 전후 새 inventory 비교가 필요하다. 순간적인 변경
  후 원상 복구까지 검출하는 filesystem transaction 보장을 주장하지 않는다.
- 각 library 시험은 전체 R1/R2/R4/R7 또는 원장 1138행의 완료 근거를 대신하지 않는다.
  R0 PR 수정 CI `40693618746`은 기존 unit/build/smoke 이후 dogfood gate를 진행 중이다.

### Native cache 분리

- `.ici/native/v1/observations`는 이전 Python cache와 분리한다. key에는 native core의
  실제 binary digest·tool bytes·허용된 실제 환경·정책·provider 해석 옵션·모든 입력 digest를
  포함한다. 의존 closure가 미확인·mutating·도구 identity 부재이면 cache key를 만들지 않는다.
- cache read/write는 schema/key/core/완료 증거를 검증하고 16 MiB로 제한한다. corrupt entry는
  재사용하지 않으며, application 통합에서는 원인을 알린 뒤 실제 실행으로 돌아가야 한다.
  clear는 no-follow directory descriptor에서 64자 lowercase hex `.json` regular entry만
  unlink하고 사용자 파일·하위 폴더·symlink/FIFO를 보존한다.
- shared lock 아래 `cargo test -p ici-application --locked`의 기존 19개와 새 cache 4개,
  scoped Clippy `-D warnings`, fmt가 통과했다. 실제 CLI warm-cache/취소/event lifecycle과
  provider별 closure를 통합한 R2/R7 전체 인수는 후속 단계다.
- R0의 path 설정 키 세 개 누락은 `c0ad365`로 교정했다 (322 keys / 1138 open rows).
  inventory check/self-test는 Python 3.10과 현재 interpreter에서 통과했고 closure는 여전히
  미완료를 거부한다. 새 PR head CI `40696941442`를 확인 중이며 앞선 실행과 구분한다.

### Native CLI·결과 조립 기반

- `ici-cli`는 고정 37개 command/group·115개 parameter의 flag/alias/repeatable 인자를
  native Clap 4.6.7로 파싱한다. root 및 모든 하위 `--help`/version, enum/range/필수 인자를
  원장과 비교한다. 이 registry는 명령 구현 parity를 대신하지 않는다.
- `analyze --plan`/`next plan`은 resolved config와 provider argv를 읽기만 하며, 가짜 실행 파일의
  side-effect marker와 state 폴더가 만들어지지 않는 시험을 통과했다. 명시한 깨진 도구는
  PATH의 다른 도구로 fallback하지 않는다. Python 없는 PATH에서 native init preview도 통과했다.
- init/minimal config의 source scope 동등, 기존 설정 거부와 명시 force 백업, migration의
  preview/new-output/명시 write 백업, cache symlink ancestor 거부를 실제 native CLI로 검증했다.
  `cargo test -p ici-cli --locked` 9개와 crate Clippy `-D warnings`, fmt 통과.
- application 결과 조립은 모든 선택 check record와 실제 observation의 ID·상태·완결성을
  확인한다. static pass는 partial로 남고, 실제 전체 범위와 필수 완료가 충족돼야 workspace
  verdict를 만들 수 있다. 입력 drift/cancelled 결과도 얻은 finding을 유지한다.
  `cargo test -p ici-application --locked` 기존 23개 + assembly 3개, scoped Clippy/fmt 통과.
- config `80c8df9`/`d239b23`은 최소 설정·소스 발견·origins·comment 보존 migration과
  nested legacy scope, workspace 안의 native mypy/ty 선택을 검증했다 (28 tests + Clippy/fmt).
  [config 근거](../../tests/rust-parity/config/README.md)의 frozen source fixture와 실제
  Mypy 2.3.1 marker 결과를 일반 설정 동등성과 구분한다.
- runtime/build/compile adapter·실제 analyze/verify lifecycle·전체 report/publish·legacy rich
  projection은 아직 통합 전이다. 해당 개발 경로를 동작한 것처럼 alias만 붙여 완료 처리하지
  않으며 R1/R2/R7 전체와 원장 closure는 열린 상태다.


### Native 정책·단계 계획과 진행 근거

- R0 PR [#298](https://github.com/jihoon22-lee/ici/pull/298)은 `8c5db5b`로 병합됐다.
  최종 PR CI `40696941442`는 기존 Python 4304 passed / 8 skipped, build/smoke,
  Qt5/Qt6, PR Pages와 Merge Gate를 통과했다. Pages `40701907199`의 실제 HTML도 받았다.
  main CI·최종 제품 전환은 이 PR 근거와 구분한다.
- 상태 namespace의 규범은 R0/ADR-0008의 `.ici/rust-v1`이다. 앞선 cache checkpoint의
  `.ici/native/v1` 표기는 내부 중간 구현이며 새 CLI 기본값은 규범으로 바로잡는다.
- policy는 요청 범위나 개인 출력 경로와 분리하고, check scope는 선택 파일/설정/실제 argv·
  환경을 기록하되 소스 내용 hash를 넣지 않는다. suppression은 CPython 3.10 fnmatchcase
  oracle 400개와 비교해 slash·backslash·Unicode·잘못된 range 의미를 고정했다.
- logical check의 ordered native steps는 빈 목록/다른 owner/중복/자기·forward 의존성을
  거부하고, check 사이 capability/step edge와 output 충돌을 함께 검사한다. 준비 단계가
  포함된 fast 검사도 계속 차단한다. scheduler 시작 hook은 실제 resource lease 뒤에 호출한다.
- `driver-gates.log`의 application 34개 / execution 30개 시험이 통과했다. 해당 library의
  scoped Clippy와 fmt도 통과했다. CLI 실제 lifecycle은 통합 중이며 별도 인수한다.
- R3 `9ea68a8`은 actual native diagnostic 좌표/구성/inspected scope를 다룬다.
  R4는 cycle/dead/annotation/static compatibility/Type2 lexical milestone을 진행했고,
  R5 `6980216`/`6e5e8f5`는 source/compiler/ELF/GNU linker 증거를 추가했다.
  R6 `9fd538f`는 명시 프로젝트 Python의 suite/shared coverage/runtime/import-only와
  새 소유 directory artifact를 다룬다. 각 tests/rust-parity 하위 README의 실측 범위를
  전체 기능 closure와 혼동하지 않는다. semantic/package 분석과 나머지 R6·R7–R9는 열린다.

- R0 main CI `40703311431`도 `8c5db5b`에서 success로 완료됐다. 기존 gate·Qt·main
  publication을 유지했으며 아직 native replacement release를 만들지 않았다.
- 실제 CLI의 프로젝트 Python 실행에서 symlink canonicalization이 가상환경을 벗어나
  coverage를 잃는 결함을 발견했다. config executable은 선언 파일 기준 lexical invocation
  경로를 보존하고, tool resolver가 실제 bytes identity를 따로 검증하도록 교정했다.
  config 29개 시험과 nested symlink 회귀 fixture로 확인한다.


### 실제 native CLI lifecycle checkpoint

- `analyze`/`next verify`는 실제 내부 source session과 R3 tool provider, R6 프로젝트 Python
  test/shared coverage/runtime/import-only를 연결한다. `.ici/rust-v1` 결과와 선택 event stream을
  원자 저장한다. 계획은 모든 probe/단계/argv/work directory를 실행 없이 표시한다.
- 입력 capture 실패·선택 범위 누락·지원하지 않는 adapter는 incomplete로 남고, 취소는
  부분 결과와 130을 보존한다. 실행 중 원본 파일/설정/tool bytes가 바뀌면 전체 완료·resolved
  증거를 철회한다. 결과/event/baseline alias와 기존 v3 결과·source/config를 보호한다.
- 논리 test와 coverage는 명시 프로젝트 Python 한 suite를 공유한다. 실제 pytest 실패 exit 1도
  완료된 비어 있지 않은 증거이면 coverage report를 만들고, collection/timeout/cancel은 막는다.
  source와 test 지원 입력을 같은 bounded scanner로 캡처하고 함수 위치는 공통 parser에서 얻는다.
  실제 test node 집합과 run 디렉터리를 정규화한 모든 단계 identity를 scope에 기록한다.
- native cache는 rich AnalysisOutput을 보존한다. observation-only entry는 rich 결과 hit가
  아니며 오류가 있으면 원인을 기록하고 재계산한다. 순수 Python source 분석과 실제 closure가
  확인된 tool만 재사용하며 dynamic test/build/미확정 native closure는 재사용하지 않는다.
  실행된 core의 `/proc/self/exe` bytes를 identity로 사용한다.
- `cache-lifecycle-gates.log`: application 36 / CLI 17개 통과, scoped Clippy `--no-deps
  -D warnings` 통과. 전체 fmt는 당시 작성 중이던 Unicode 파일을 감지했으므로 통과로 적지 않는다.
  `lifecycle-milestone-gates.log`: 앞선 CLI 실제 Python acceptance는 CPython 3.10.21,
  pytest 9.1.1, coverage 7.15.4에서 실패→coverage→수정 후 baseline resolved까지 통과했다
  (한 fixture 안 두 실행, 22.84초). 최종 exact-source 재검증과 전체 native/stable gate는 별도다.
- R4 semantic `6827477`과 Unicode `8c44b7e`는 frozen CPython projection과 Unicode 16 전체
  scalar oracle을 고정한다. R5 library/corpus는 `9f3cb78`까지 44개 실제/frozen 시험을 통과했다.
  R6 `f821b19`의 C++/gcov/integration 실제 도구 근거도 library 범위이며 CLI C++ orchestration은
  후속이다. R7 저장 reader/render/import, TLS/publish, R8 원장 closure·배포와 R9 cutover는 열린다.

- Root 변경을 base `9f3cb78`에서 분리한 검증 tree `c0878bdf5bf33ca4a80e720184aae0a25951b4f0`
  에 그대로 적용했다. 이 tree에서 `--locked` application 36 / CLI 17, 명시 native Python
  acceptance(실패·coverage·수정 후 baseline, 22.69초), transitive strict Clippy와 scoped fmt가
  모두 통과했다. `root-proof/gates.log`의 library 경로도 해당 source를 가리킨다.
  이 근거는 후속 변경이나 전체 R8 exact-main 인수를 대신하지 않는다.

- `1a5bcb3`의 pure stored reader를 workspace에 등록했다. published next v1/v2·legacy v3
  fixture와 unknown/duplicate/depth/byte bound, 안전한 script JSON 3개 시험 및 strict
  Clippy/scoped fmt/locked offline metadata가 통과했다. HTML/legacy projection/import/publish는 후속이다.
- ZIP codec은 실제 wheel 비교를 위한 준비이며 CRC32 직접 의존성을 고정했다. 기존 Python
  compatibility 표면에서 사용하지 않는 PEP 508을 제거해 해당 transitive dependency를 줄였다.

### C++ CLI compilation evidence checkpoint

- Native CLI now reads recorded compilation units/response inputs and replays diagnostics for
  every configuration. Logical aggregation keeps the original unit/variant owner; an aggregate
  check does not pretend to belong to just its first unit. Missing header closure prevents
  reuse/resolved proof without suppressing measured diagnostics.
- `cpp.compile` uses actual include probes for unity membership where needed. Declared artifact
  contracts inspect existing outputs without implicitly building during `analyze`.
- `cpp-cli-final.log` passed application and CLI ordinary tests; the first Clippy pass found one
  cloned-reference warning, corrected and confirmed by `cpp-cli-lint-final.log` (strict transitive
  Clippy and scoped fmt). `cpp-cli-native.log` separately passed the explicit two-configuration
  GCC fixture with Python absent from PATH, including precise failing-unit ownership and events.
- `domain-reader-strict.log` passed domain tests, strict Clippy and fmt after rejecting unknown
  nested saved-run fields. Full C++ validation/tidy/compiler facets and full R8 acceptance remain
  separate work; this checkpoint does not close the inventory.

- `cli-independent-runtime-final.log`: both explicit CLI Python acceptance cases passed in
  24.62 seconds using project CPython 3.10.21/pytest 9.1.1/coverage 7.15.4. A source encoding
  unsupported by the core decoder limits source geometry but does not suppress independently
  completed runtime/import/test evidence. The test also retains failing-suite/shared-coverage
  then fixed-baseline behavior; Python remains an external project tool.

### Declared child environment boundary

- TaskSpec can explicitly select inherited environment keys (`None` retains the ordinary bounded
  default; an empty list inherits none). Filtering occurs before explicit overlays and bundle
  environment isolation. Custom keys are available only when explicitly requested. Runner Debug
  omits environment values. Effective environment and the task sharing identity both change with
  this selection.
- `environment-isolation-gates.log` passed domain/execution tests, strict Clippy and scoped fmt.
  The actual `/usr/bin/env` child verifies an allowed custom key, overlay, omitted private key,
  and empty inheritance. The first regression exposed constructor filtering of custom keys;
  retaining a private environment snapshot for explicit allowlists corrected that failure.

### Shared selection boundary

- Explicit check subsets and additional historical behaviors use the same application planner;
  duplicate/unknown check identities are rejected and subset results retain partial scope. The
  frozen 36-check catalog remains an inventory input, not a limit that deletes other behaviors.
- Legacy global integration cases have one owner (root component, otherwise the first declared
  component). A required empty case list remains selected for an incomplete explanation.
  `components[].integrations[].inherit_env` is an accepted sparse configuration key.
- `cli-report-final.log` passed all 11 application planning cases, including supplemental and
  owner/empty-case regressions, followed by strict transitive CLI/application Clippy. This
  selection boundary is not evidence that every supplemental execution adapter is complete.

### Stored CLI reports and supporting evidence

- Every native execution keeps the unchanged run-v2 envelope and a separate redacted rich legacy
  projection under `runs/<run-id>/details.json` beside the selected result. The projection retains
  PASS targets, producer details and raw metric denominators. Report intake requires its embedded
  run to equal the selected envelope, preventing a stale details archive from being attached.
- `report`/`next report` only read stored artifacts; they can render after source deletion and
  invalidated configuration. Writes use no-follow atomic storage, reject input inode aliases,
  and replace only recognized generated artifacts. Native `analyze` prints JSON/HTML locations;
  `--json` remains pure machine JSON. Artifact write errors identify the saved analysis result.
- `import-report` stores external SARIF/JUnit/LCOV separately with `NOT_EVALUATED` gate; attaching
  supporting HTML evidence leaves the result bytes intact and never silently merges into SARIF.
- `cli-report-boundary-final.log`: all four actual CLI/report storage regressions passed (14.10s),
  strict transitive CLI Clippy and changed-file fmt passed. `cli-report-lifecycle-gates.log` earlier
  passed all CLI/application ordinary tests, then found two in-progress validation-binding lint
  warnings; both were fixed by their owner and the final strict check passed. R7's 28 pure reader,
  projection/render/import/navigation cases are documented in its corpus README; publication,
  historical baseline enforcement and full inventory acceptance remain separate work.

- Actual optional integration acceptance exposed a required-check aggregation bug: producer-level
  advisory findings were still added to the required fingerprint set. The application now honors
  explicit advisory tags per finding while preserving measured evidence. `advisory-case-gates.log`
  passed six verification regressions and strict application Clippy; removing the tag in the new
  regression still fails the required gate. This does not turn missing required executions into PASS.

### Common native preparation and explicit setup CLI

- Rust-owned preparation is declared as `NativePrepare`, with ordinary task inputs/outputs,
  mutation directories and resource leases. The CLI invokes its native handler after the lease,
  using immutable captured inputs, and bypasses analysis cache. Verified generated outputs are
  excluded individually; unknown or modified ownership records keep files as inspected inputs.
- Native C++ facade/tooling/runtime bindings and Python runtime matrices/integration/sanitize
  share the execution lifecycle. Compiler-managed diagnostics/boundaries/include scope do not
  claim reusable or resolved proof while dependency closure remains unproven. Explicitly inactive
  tooling consumes no compiler evidence and retains located NOT_APPLICABLE output.
- `setup` is a native command extension, preserving the frozen 37-command inventory. Offline
  archive/explicit HTTPS plus digest, selection, rollback and inspection are separate from
  analysis. Installed tool discovery checks the version root's `tools/manifest.json`, without
  prepending pack directories to the project's PATH.
- `cli-native-plumbing-final.log` passed all ordinary application and CLI tests (native tool
  fixtures remain separately explicit), then found an obsolete exact key-count assertion in
  config. The test now checks the exact frozen key union plus five named native additions and
  rejects duplicates. `cli-native-plumbing-lint-final.log` passed all 29 config tests, transitive
  strict CLI/application/config Clippy and scoped fmt. `cli-setup-gates.log` separately passed
  three setup argument/no-write/no-HTTP cases and three frozen registry cases.
- These are internal composition milestones. Git-version preparation, explicit C++ preparation
  and export, historical command outputs/publishing, actual full archive bootstrap/reproducibility
  and the complete R8 inventory/cutover acceptance remain open.
