# R0 결정표와 고정 비교 기준

2026-10-28 실행 승인 후 확정한 설계 입력이다. R0 완료는 설계·비교 입력의 준비를 뜻한다.
제품의 Rust parity와 release 인수는 R1–R9 원장이 실제 증거로 닫힐 때까지 미완료다.

|결정|고정 내용|근거/남은 제품 검증|
|---|---|---|
|규약|ADR-0008, AGENTS §10; Python gate는 R9까지 유지|기존 ADR-0001/2·SPEC-05에 대체 범위 표시|
|합집합|[inventory](inventory.md), 고정 Git 객체에서 19 descriptor/36 check/CLI 37 nodes·115 parameters/config 322 keys 및 별도 계약 수집|1138 requirement/source-audit 행 초기 미완료; 행별 실제 증거 없이 closure 금지|
|parser 공급|Ruff 0.16.4 revision `11c76bf48fdac06b2f240cba502eda96da4dce77`, parser/AST crate 0.0.10, Rust 1.99.1|[37-case + CPython 3.10–3.14 비교 및 static 실행](parser-evaluation.md); 제품 scope/decoder/resource 한도는 R4|
|구문·위치|기본 grammar 3.14, 명시 project target 3.10–3.14 검사. parse/unsupported/semantic 오류를 모두 처리|AST는 UTF-8 byte range; domain display는 1-based Unicode 문자 열. 비UTF8 원본 map은 adapter에서 보존; 범위 미확정은 incomplete|
|probe|ADR-0008 version/sysconfig/import-only allowlist; qmake recorder는 Rust 내부 mode|프로젝트 Python 선택, -I/고정 script/argv/timeout/output cap; AST 규칙의 Python 우회 금지|
|native 도구|Ruff 0.16.4·ty 0.0.93 musl archives와 실행 파일 digest 고정|[8개 native 실행/trace](tool-transport-evaluation.md), 전이 license·CPU/kernel 최종 인수 R8|
|타입 기본값|mypy native 설정은 mypy, ty 설정은 ty. 양쪽 명시 충돌은 오류/명시 선택으로 해결|무설정은 ty advisory: 원래 severity/finding 보존, required gate를 암묵적으로 강화하지 않음. 누락/실패는 상태·limitation에 표시|
|identity|frozen fingerprint v3는 호환이 증명된 native rule에만 보존. 새로운 core/tool/policy/scope digest namespace로 cache 분리|미확정 ESLint plugin/extends/Cargo/TS closure는 reusable/resolve 불가; 읽기 호환과 비교 가능성 분리|
|wire/legacy|run v2/event v1/auxiliary v1; result v1/legacy v3 reader와 공통 결과의 legacy projection|[고정 domain/CLI golden](../../../tests/rust-parity/); all PASS targets와 raw metric 분모를 내부 AnalysisOutput에 보존|
|배포|Rust native core + 고정 native tool pack, rootless archive/installer, `.ici/rust-v1` 상태|R8에서 실제 core libc/ISA·archive/license/업데이트/rollback 확인 전 supported 추정 금지|
|TLS|reqwest 0.12.28 `default-features=false`, blocking + rustls-tls-native-roots; custom CA/proxy/no-proxy|[12개 시스템/사설 CA·CONNECT·실패 fixture](tool-transport-evaluation.md); 인증/토큰 redaction/게시 상태 분리는 R7|
|이전|ICI_PYTHON core fallback 없음, doctor에서 이전 설치/PATH 충돌 안내; config/result 원본 유지|migration preview/new output, rollback은 과거 공개 artifact의 명시 선택이며 사용자 데이터 복원 아님|
|레거시 프로젝트 Python|레거시 스키마에서 `engines.test.python`과 `.venv`가 없으면 v0.13.0 pyz launcher가 고르던 인터프리터(`$ICI_PYTHON`, python3.14…python3.10, python3 중 PATH에서 처음 찾은 3.10+)를 *프로젝트* 인터프리터로 사용하고 출처를 결과에 기록. ici core의 Python fallback이 아님. 새 스키마는 선언 executable/컴포넌트 `.venv`만 사용|기존 pyz 소비자 호환(AGENTS §9) vs §8 환경 분리; 2026-11-01 root 결정, validation fragment fixture|

## 공유 코드 경계

`ici-domain`은 wire 타입, `TaskSpec`, `Observation`, `AnalysisRequest/AnalysisOutput`을 제공한다.
`ici-config`는 resolve/discover/scaffold/migrate, `ici-execution`은 argv/process/DAG/storage를
맡는다. `ici-analysis`는 snapshot bytes를 읽는 자체 분석, `ici-providers`는 외부 도구
준비/argv/진단 변환이다. application만 policy/attribution/aggregation을 적용한다.
report/legacy projection은 같은 output을 읽고 분석을 재구현하지 않는다.

TaskSpec sharing은 id/consumer analysis-unit 외의 모든 실행·한도·variant·의존·작업 디렉터리
필드를 포함하며 실제 inherited environment digest도 포함한다. Python의 더 느슨한 share
key를 그대로 옮겨 서로 다른 timeout/output cap의 결과를 재사용하지 않는다.

## 고정 oracle과 측정

- source `6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`, 공개 v0.13.0 pyz
  `c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde`.
- 개발용 oracle checkout은 detached source이고 product runtime과 섞지 않는다.
- [`capture_cli_oracle.py`](../../../tests/rust-parity/capture_cli_oracle.py)는 source SHA를
  확인하고 plan/doctor/publish JSON, cold/warm analyze 및 report를 실제 실행한다.
  [`capture.json`](../../../tests/rust-parity/oracle/capture.json)에 Python 3.10.21,
  실행 argv/exit/wall/RSS/output size를 보존한다. tmp path와 run id만 정규화하며
  finding/위치/severity/분모/evidence/gate/exit는 정규화하지 않는다.
- 현재 단일 함수 fixture의 실제 cold/warm 수치는 이 JSON이 근거이며 전체 제품의 성능이나
  다른 CPU에 일반화하지 않는다. Ruff 0.16.4 및 프로젝트 mypy는 기록된 native 실행 경로다.
- R8 비교 예산: 같은 고정 fixture/도구/host에서 5회 중앙값 cold/warm 각각
  `max(oracle × 2, 2초)` 이하, peak RSS `max(oracle × 2, 128 MiB)` 이하,
  동일 정보의 report size `max(oracle × 2, 64 KiB)` 이하. 취소 후 자손 정리는 2초 이내.
  이는 **수용 목표**이며 Rust 제품이 통과했다는 측정값이 아니다. 큰 report와 대형·malformed
  소스 corpus도 별도로 실행하고 정보 누락으로 예산을 맞추지 않는다.
- 첫 parser 입력 상한은 파일 16 MiB, aggregate snapshot 512 MiB이며 초과는 미완료다.
  R4/R8 stress·취소 fixture로 타당성을 확인하고 실제 시험 전 성능 개선을 주장하지 않는다.

## 교정 기대값 (oracle의 결함을 복제하지 않는 항목)

|조건|기존 근거|Rust 기대값과 담당|
|---|---|---|
|ESLint rule off/config/plugin/lockfile 변경|check_scope `_configuration_digests`가 Python config 이름만 해시함|R3/R7: 정책·도구 closure 변경/미확정은 disappeared finding을 resolved로 만들지 않음|
|같은 경로의 실행 도구 bytes 변경|경로/버전만 같아도 구현은 달라질 수 있음|R2/R3: 실제 executable digest/identity 변경으로 cache 재사용 거부|
|무설정과 init 후 scope/exclude 차이|automatic과 `_candidate_components`의 별도 알고리즘|R1: 같은 resolver로 root source/default excludes/검사 선택 동등; 기존 명시 범위는 자동 확장 안 함|
|SARIF uriBaseId 존재|기존 importer의 상대 URI 단독 처리|R7: 명시 base mapping 후 containment 검증, 알 수 없는 base/외부는 명시 거부|
|제한/variant가 다른 task의 공유|기존 share_key에 timeout/output cap 등 없음|R2: 서로 다른 실행 계약은 공유하지 않음, bounded output/truncation 보존|
|doctor가 내부 검사로 기술한 외부 runtime/test|기존 doctor golden에 in-process 표기|R1/R3/R6: 실제 resolver/plan과 같은 실행 효과·도구 요구를 표시|
|`pull_request` 이벤트의 게시 commit|`application/publish.py`가 `GITHUB_SHA`(합성 merge commit)를 우선해 PR head와 비교하는 순서 guard가 모든 PR 실행을 stale로 판정|R7: `pull_request.head.sha` 우선, 없으면 `GITHUB_SHA`. 원격 경로·viewer URL도 PR head 기준(`publish_commands.rs::pull_request_publication_uses_the_pr_head_for_the_path_and_the_ordering_guard`, `publish/mod.rs` tests)|
|clang-tidy/Clazy probe와 실행 환경|`_cpp_lint`가 도구 probe는 전체 환경으로, 실제 실행은 최소 환경으로 해 `LD_LIBRARY_PATH`가 필요한 Clazy가 probe를 통과한 뒤 모든 실행에서 실패|R3/R6: probe와 실행은 같은 환경을 사용. probe 자체가 실패(loader 오류·exit 127)하면 v0.13.0처럼 `Unavailable`(lint WARN, ESTIMATED), probe 통과 후 실행 실패만 실행 오류(cpp fragment fixture)|
|clang-tidy 다중 소스 진행 줄|clang-tidy 18+가 여러 소스를 분석하며 stderr에 내는 `[n/N] Processing file <src>.` 줄 때문에 `providers/tidy.py`가 실행 전체를 해석 불가로 거부|R3: 정확히 그 형식(1부터 증가하는 번호, 소스 수와 같은 N, 소스 목록의 경로)만 무시하고 다른 출력은 계속 거부. CLI `cpp.tidy`는 소스마다 별도 실행이라 provider 경로에서만 발생(cpp fragment fixture)|
|clang-tidy 설정의 escape된 인자 키|`analysis/_clang_tidy.py`와 next 경로가 `ExtraArgs`/`ExtraArgsBefore`/`InheritParentConfig`를 원문 텍스트로만 찾아, LLVM YAML이 디코딩하는 큰따옴표 escape 키(`"\x45xtraArgs"`, `"Extra\u0041rgs"`)나 UTF-16 설정은 통과|보안 교정: 원문 키 일치에 더해 큰따옴표(`"`)와 backslash가 함께 있는 설정과 NUL 바이트가 있는 설정도 같은 거부 문구로 거부(분석 중 `-fplugin` 주입 차단). LLVM은 큰따옴표 scalar에서만 escape를 디코딩하므로 둘 중 하나가 없으면 원문이 곧 디코딩된 키이며, 따옴표 추적기를 쓰지 않아 작은따옴표·주석 속 따옴표로 우회할 수 없음. 큰따옴표가 없는 설정의 정규식 값은 계속 허용. fixture: `crates/ici-cli/src/cpp.rs::clang_tidy_config_tests`|
|읽을 수 없는 production source|line engine이 켜져 있으면 `ValueError: … end_line must be a 1-indexed integer: 0`으로 crash해 보고서 없이 종료 코드 1, 꺼져 있으면 lint가 단계별 `C++SyntaxContextError`·`QtMocInspectionError`(PermissionError)·`ClangTidyContextError`·`ClazyContextError`로 ERROR NOT_RUN|결함 교정: 입력 snapshot 단계에서 읽을 수 없는 파일과 사유를 밝히고 실행 전체를 0이 아닌 종료 코드로 거부한다. 일부 검사만 근거 없이 PASS/NOT_RUN으로 섞이거나 보고서 없이 crash하지 않는다(R05/R10 상태 축 분리). 근거: `w-cpp-unreadable-frozen-line.log`, cpp.json 해당 행|
|native C++ shadow build 위치|legacy CMake/qmake/Make shadow build와 그 산출물(`compile_commands.json`, manifest)이 프로젝트의 `build/ici-cmake-build` 등 고정 경로에 생김|의도한 계약 변경: R0에서 승인한 `.ici/rust-v1/build/legacy-<16 hex>/<variant>/` 아래에 둔다. 그 결과 `database_path`, manifest `shadow_root`·digest, `C++Syntax:<12 hex>` 접미어와 이에 따른 lint finding 순서가 frozen과 다르다. quality-zoo는 `cpp.static-build-context`의 `database_path` 하나만 `runner/candidate_overrides.py`의 검토된 native pattern으로 비교한다. 근거: cpp.json 해당 행|
|C++ 도구 증거는 실제 실행 기록|legacy test/sanitize/thread-sanitize가 `cmake --version` probe를 따로 실행하고 object 디렉터리마다 `gcov`를 한 번씩 실행해 그 process들을 tool evidence로 기록|의도한 차이: native는 실제로 실행한 명령만 tool evidence로 기록한다(직접 g++ evidence와 같은 규칙). `cmake --version` probe를 하지 않고 GCOV 보고서를 한 `gcov` process로 만들며, 비교 대상은 보고서 내용과 판정이다. 근거: cpp.json `check:cpp.coverage|sanitize|tsan`, `descriptor:test|sanitize|thread_sanitize`|
|component 밖 `external` 항목|component root 밖을 가리키는 `external` 항목을 outside-project FAILED로 보고하고 분석하지 않음|frozen 유지: native도 프로젝트 밖 파일을 분석하지 않고 같은 FAILED로 보고한다(scope 안전성). 구현과 fixture는 C++ closure가 연결|
|legacy 종료 코드 3의 범위|불완전한 필수 검사도 legacy `verify`는 종료 코드 1|정교화: legacy view가 불완전한 native 검사를 frozen 판정으로 바꾼 뒤에도 필수 엔진의 증거가 NOT_RUN으로 남을 때만 3으로 끝난다. 필수 SKIP 때문에 suite가 ERROR인 경우는 1, frozen에도 없던 선택 도구(coverage)는 WARN과 0으로 frozen을 따른다. 근거: `crates/ici-cli/src/legacy_execute.rs`, `crates/ici-cli/tests/legacy_engine_extras.rs`의 capture별 종료 코드 비교|
|legacy `extra` 안 native 증거 키|legacy 엔진 보고서 `extra`에는 frozen 키만 있음|의도한 추가: frozen 키·값·자료형은 그대로 두고, native 증거(native checks, target scopes, finding identity map, parser/source identity, Python 분석 counter, process 단계, C++ include graph/trace·boundary·compile context·exact dead details)를 native 이름의 별도 키로 덧붙인다. 기존 키를 바꾸거나 지우지 않으므로 알려진 키만 읽는 소비자와 호환된다. 전체 결과 비교 harness는 이 키들을 경로별 R10/R11 허용으로만 통과시키며 쓰이지 않는 허용은 실패한다|
|pytest는 있으나 coverage가 없는 legacy test|"Coverage capability" SKIP 대상 없음, frozen의 pytest case 줄, `coverage --version`/`pytest`/"pytest resource warnings" tool evidence|native가 덧붙이던 "Coverage capability" 대상을 없애고 `start_line`을 frozen과 같게 맞춘다. tool evidence는 "C++ 도구 증거는 실제 실행 기록"과 같은 규칙으로 native가 실제 실행한 process만 기록한다. frozen의 resource warning 수집에 해당하는 실행을 native가 하지 않는다면 그것은 허용 차이가 아니라 기능 누락으로 다룬다|
|`verify --baseline`의 line 집계|`--baseline` 입력 JSON이 프로젝트 안에 있으면 extra-scope JSON 파일로 줄 수(`all`·`files_data`·`top_files_all`)에 포함|결함 교정: 명령 자신의 입력·출력 파일(`--baseline`, `--result`, 보고서 경로)은 프로젝트 내용이 아니므로 세지 않는다(`planning::is_command_output`). 같은 프로젝트의 줄 수가 CLI 옵션에 따라 바뀌지 않는다. 다른 파일의 집계는 frozen과 같다|

교정 행은 R0에서 기대값을 고정한 것이며 해당 구현/회귀 fixture는 담당 WP의 closure에
반드시 연결한다. golden을 무조건 재생성해 기존 결함이나 새로운 회귀를 숨기지 않는다.
