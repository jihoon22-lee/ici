# 현행 실행 흐름과 환경 보정 지점

- 상태: **조사 완료 (measured)**. 관찰 기록이며 목표 설계가 아니다.
- 조사 대상 커밋: `20c417cc8ec84aa490d0138782bf9fe38374fb5d`
- 근거 이슈: [WP00 #198](https://github.com/jihoon22-lee/ici/issues/198) 구현 순서 2
- 목표 설계: [architecture.md](../architecture.md) §2·§5, [spec-02](../spec-02-distribution-execution.md)

## 1. CLI 진입점

`src/ici/__main__.py`는 Typer 앱 하나(`app`, line 55)를 만들고 여기에 명령을 등록한다.

|명령|정의 위치|역할|
|---|---|---|
|`verify`|`__main__.py:168` `cmd_verify`|전체 스위트 실행. 유일한 게이트 명령|
|`build`|`__main__.py:260` `cmd_build`|`BuildEngine` 단독 실행|
|`publish`|`__main__.py:472` `cmd_publish`|저장된 HTML/JSON을 gh-pages와 sticky comment로 게시|
|`doctor`|`__main__.py:494` `cmd_doctor`|도구·환경 진단|
|`env`|`__main__.py:512`|환경 정보 출력|
|`cache`|`__main__.py:527`|분석 캐시 관리|
|`export-compilation-context`|`__main__.py:165`|compile context 내보내기|
|엔진 15개|`__main__.py:272` `_ENGINE_COMMANDS` → `:426` 루프 등록|엔진 단독 실행|

전역 콜백 `main_callback`(`:112`)이 설정을 먼저 읽고, 설정 오류면 **exit 2**로 끝낸다(`:132`).

관찰: `init` / `plan` 명령이 없다. [spec-01 §5](../spec-01-workspace-config-cli.md)의
`ici init` / `ici plan` / `ici report`는 모두 신규다. `--python` / `--cpp` / `--component` /
`--require-full` / `--events` 옵션도 현행에 없다. `--profile`은 있다(`_VERIFY_PROFILE_OPTION`, `:78`).

## 2. verify 실행 흐름

실제 함수 호출 순서다. 파일:행으로 추적했다.

```
cmd_verify                              __main__.py:168
 └ _effective_config(ctx)               ← main_callback이 load_config로 읽어둔 설정
 └ VerifyOrchestrator(root, config)     engines/verify.py:139
    └ run_all(...)                      engines/verify.py:205
       1) apply_analysis_profile(config, profile)          → effective_config, selected_profile
       2) descriptors_for_profile(ENGINE_DESCRIPTORS, ...) core/pipeline.py:279
            · profile 필터 + engines.<name>.enabled 필터
            · 비활성 선행 의존이 있으면 여기서 오류
       3) requested_variants = 선택된 descriptor의 build_variant 모음
       4) prepare_analysis_context(...)                    engines/verify.py:92
            a) discover_project_model(root, config)        core/context.py:165
            b) evaluate_support_matrix(...)                core/support.py:642
            c) derive_tool_policy(declared_support, doctor.required_tools)
            d) collect_capability_inventory(probes, ...)   core/toolchain.py  ← 프로세스 실행
            e) _prepare_compilation_context(...)           verify.py:82
                 · backend == qmake → prepare_qmake_compilation_context
                 · 그 외          → prepare_cmake_compilation_context
            f) create_analysis_context(...)                core/context.py:713
       5) AnalysisCache() + project_source_digest(project)  ← use_cache일 때만
       6) PipelineExecutor(descriptors).run(execute)        core/pipeline.py:312
            · _topological_layers로 층을 만들고
            · 층 안에서 read-only는 ThreadPool 병렬, build는 순차
       7) tem_score = results 중 engine_name=="test"의 score            verify.py:330
       8) aggregate_suite_status(results)                   core/models.py:321
       9) _apply_baseline / _write_baseline                 verify.py:146 / :169
      10) reporters: console / json / html / sarif / github summary / publish
 └ _exit_for_safety_status(suite.suite_status)              __main__.py:575
```

### 이 흐름과 목표 설계의 차이

|목표 (architecture.md §5)|현행|
|---|---|
|config → workspace/component → analysis unit|`ProjectModel` 하나. component/analysis unit 개념 없음|
|선택한 검사에 필요한 도구만 선택|`probe_all_tools=True`가 기본. `DEFAULT_TOOL_PROBES` 30여 개를 모두 probe|
|필요한 입력에서 DAG 생성, 빌드는 별도 prepare 노드|DAG는 엔진 단위. 빌드는 엔진 안에서 일어남|
|같은 도구·인자·입력이면 실행 공유|엔진별로 각자 실행. 공유 키 없음|
|scope/completeness 계산 후 policy 적용|`aggregate_suite_status` 하나가 상태·evidence·required를 동시에 본다|
|결과 atomic write, report/publish는 저장된 결과만|`publish`는 이미 저장된 결과만 읽는다 (일치)|

`probe_all_tools`는 `prepare_analysis_context`의 기본값이 `True`이고(`verify.py:99`),
`run_all`은 이 인자를 넘기지 않는다. 즉 **verify는 선택 범위와 무관하게 전체 도구를 probe한다.**
spec-02 §3이 요구하는 "선택한 검사에 필요한 도구만"과 어긋나는 지점이다.

## 3. 설정 해석

원본: `src/ici/config.py:233` `load_config`, `src/ici/_config_paths.py`.

병합 순서(뒤가 우선):

1. `DEFAULT_CONFIG` (`config.py:15`) — 코드에 내장된 기본 정책
2. XDG 전역 정책 파일
3. 프로젝트 `ici.toml`
4. 프로젝트 `dev.toml`
5. `ICI_CONFIG` 환경변수가 가리키는 파일

규칙:

- 존재하는 모든 파일을 읽는다. 파싱 실패와 "명시 요청한 파일의 부재"는 `ConfigError`다.
- 아무 파일도 없고 `ICI_CONFIG`도 없으면 전역 기본 파일을 만든다(`_ensure_global_default_config`).
- 병합 후 `validate_config` + `validate_config_paths`로 검증한다.
  경로 필드가 프로젝트 루트 밖으로 나가면 거부한다(`_config_validation.py:93`).

### 목표 설계와의 차이

|목표 (spec-01 §2·§3)|현행|
|---|---|
|`[workspace]` 있는 가장 가까운 `ici.toml`을 상위 탐색|cwd의 `ici.toml`만. 상위 탐색 없음|
|component별 선택적 분리 파일 (`config=...` 참조)|없음. 단일 파일|
|`--config` / `--local-config` 옵션|없음. `ICI_CONFIG` 환경변수만|
|각 값의 출처(source file/key) 보존|없음. `_deep_merge` 후 출처가 사라짐|
|개인 XDG 설정은 표시/편집기 기본값만|**XDG 전역 파일이 품질 정책 전체를 덮을 수 있다**|
|`${env:NAME}` 제한적 허용|없음|
|path 필드는 선언 파일 기준, source glob은 component root 기준|두 타입이 구분되지 않음|

`dev.toml`과 XDG 전역 파일이 품질 기준을 바꿀 수 있는 점은 spec-01 §3의
"개인 설정이 품질 기준에 암묵적으로 섞이지 않는다"와 직접 충돌한다.
spec-05 §5는 이 영향을 migration 보고서에서 드러내도록 요구한다.
→ [WP05 #203](https://github.com/jihoon22-lee/ici/issues/203)

## 4. 프로젝트별 설치·경로 보정이 필요한 지점

#198이 별도 표로 요구한 항목이다. 각 행은 "사용자가 ici를 쓰려고 프로젝트나 환경을
건드려야 하는" 지점이다.

| # | 지점 | 코드 위치 | 사용자가 해야 하는 일 | 위반하는 요구사항 |
|---|---|---|---|---|
|1|프로젝트 테스트 인터프리터가 `.venv` 또는 `sys.executable`로 결정|`engines/test_interpreter.py:13` `_resolve_python`|프로젝트 Python을 `.venv`에 두거나 `engines.test.python`을 직접 지정. 아니면 **ici core의 인터프리터로 테스트가 돌아간다**|R01, R02 / [spec-02 §3](../spec-02-distribution-execution.md)|
|2|Ruff를 프로젝트 `.venv`에서 찾음|`engines/lint.py:529` → `core/env.py:64` `find_project_executable`|각 프로젝트 `.venv`에 ruff 설치|R02|
|3|mypy를 프로젝트 `.venv`에서 찾음|`engines/type_check.py:155` → 동일|각 프로젝트 `.venv`에 mypy 설치|R02|
|4|NAS 공용 C++ 라이브러리 경로가 코드에 하드코딩|`core/env.py:83` `get_nas_cpp_lib_dir` → `libs/cpp/ips-core-lib/v1.2.3/x86_64`|해당 경로 구조를 맞추거나 `NAS_SHARED_DIR`을 설정|R01 ("특정 NAS·사내 라이브러리 경로를 알지 않는다")|
|5|infra 루트를 `nas_shared` 디렉터리 존재로 상위 탐색|`core/env.py:21` `find_infra_root`|워크스페이스를 특정 구조로 배치하거나 `ICI_INFRA_ROOT`/`DEVOPS_INFRA_ROOT` 설정|R01|
|6|`PYTHONPATH`에 source dir를 앞쪽으로 주입|`engines/test_interpreter.py:35` `_build_python_test_env`|프로젝트가 기대하는 import 경로와 충돌 시 우회 필요|[spec-02 §2](../spec-02-distribution-execution.md)|
|7|WSL에서 `TMPDIR`/`TMP`/`TEMP`를 `/tmp`로 강제|`engines/test_interpreter.py:39`|의도한 임시 경로가 무시됨|spec-02 §2|
|8|`uv`를 NAS·infra 경로에서 탐색|`core/env.py:46` `find_uv`|`ICI_UV` 설정 또는 해당 경로에 uv 배치|R01, R11|
|9|런처가 시스템에서 3.10+ 인터프리터를 탐색|`scripts/launcher.sh:5`, `core/env.py:11` `PYTHON_CANDIDATES`|`ICI_PYTHON` 설정 (없으면 후보 6개 순차 탐색)|R03 (전용 런타임으로 대체 대상)|
|10|`build.make.*` argv 벡터가 모두 빈 배열|`config.py:26`|Make 프로젝트는 configure/build/test argv를 전부 직접 작성|—(설계상 의도. 기록만)|

`get_nas_cpp_lib_dir`의 호출 지점은 5곳이다: `engines/sanitize.py:783`, `engines/test.py:535`,
`engines/build.py:533`, `doctor.py:83`, `core/project.py:430`.
`find_project_executable`은 2곳(`lint`, `type_check`)이다.

이 10개 행은 **"ici 사용을 위한 반복적인 의존성 설치·경로 보정·엔진별 연결 작업을 줄인다"**
(마일스톤 핵심 설계 원칙)의 측정 대상이다. WP01([#199](https://github.com/jihoon22-lee/ici/issues/199))이
1·9번의 위험 가정을 시험하고, WP06([#204](https://github.com/jihoon22-lee/ici/issues/204))이
2·3·4·5·8을 ToolResolver로 대체한다.

## 5. 상태 판정과 종료 코드

### 현행 상태 축

|축|값|원본|
|---|---|---|
|`EngineStatus`|PASS / WARN / FAIL / ERROR / SKIP|`core/models.py:14`|
|`EvidenceState`|MEASURED / ESTIMATED / NOT_RUN / NOT_APPLICABLE|`core/models.py:22`|
|`FindingConfidence`|(exact / high / medium / low)|`core/models.py:56`|
|`AnalysisMode`|exact / tool-backed / heuristic / unsupported|`core/models.py:86`|

`aggregate_suite_status`(`models.py:321`)의 실제 판정 순서:

1. 결과가 없으면 `ERROR`
2. `required` 이고 `evidence != NOT_APPLICABLE` 이며 (`status in {ERROR, SKIP}` 또는
   `evidence == NOT_RUN`)인 엔진이 하나라도 있으면 → `ERROR`
3. `required` 이고 `status == FAIL`이면 → `FAIL`
4. `evidence != NOT_APPLICABLE` 이고 (`WARN` 또는 (선택 엔진인데 PASS가 아니거나
   evidence가 MEASURED가 아님))이면 → `WARN`
5. 그 외 `PASS`

**`NOT_APPLICABLE`을 `NOT_RUN`과 구분하는 처리가 이미 있다.** 코드 주석이 그 이유를 남겨 두었다
(적용 대상 언어가 없는 프로젝트를 영구 red로 만들지 않기 위함). 이는 spec-04 §2의
"미선택과 적용 불가를 혼동하지 않는다"와 같은 방향이고, 새 설계로 옮길 때 보존해야 하는 자산이다.

### 현행 종료 코드

`core/models.py:452` `exit_code_for_status`:

| 상태 | 현행 코드 |
|---|---|
| PASS, WARN | 0 |
| FAIL, ERROR | 1 |
| SKIP | **2** |

설정/CLI 오류도 2다(`__main__.py:132`, `:147`, `:161`, `:252`).
`publish` 실패는 1이다(`__main__.py:491`).

### spec-04와의 충돌 (migration 표 입력)

| 의미 | 현행 | [spec-04 §3](../spec-04-results-integration.md) 목표 |
|---|---|---|
| 통과 | 0 | 0 |
| 품질 위반 | 1 | 1 |
| CLI/config 오류 | 2 | 2 |
| 엔진 SKIP | **2** | — (SKIP 축 자체가 없어짐) |
| 필수 미완료 / `--require-full` 미충족 | **1 또는 2로 흩어짐** | **3 (신규)** |
| 사용자 취소 | 정의 없음 | **130 (신규)** |

즉 **현행 2는 "설정 오류"와 "엔진 SKIP" 두 의미를 겸하고 있고, 목표의 3·130은 아직 없다.**
이 차이는 사용자에게 보이는 계약 변경이므로 spec-01 §8과 spec-05 §5가 요구하는
migration 표에 반드시 들어가야 한다. → [WP27 #225](https://github.com/jihoon22-lee/ici/issues/225)

## 6. 결과와 게시

|단계|모듈|산출물|
|---|---|---|
|콘솔|`reporters/console.py`|Issues-First 뷰, TEM 점수 줄(`:372`)|
|JSON|`reporters/json_rep.py`|`verify_report.json`. `tem_score`/`max_tem_score` 포함(`:670`)|
|HTML|`reporters/html/`|정적 HTML. TEM 카드(`html/report.py:123`)|
|SARIF|`reporters/sarif.py`|`--sarif PATH`|
|Markdown|`reporters/markdown.py`|GitHub summary|
|baseline|`core/baseline.py`, `reporters/baseline_view.py`|v3 baseline 비교|
|게시|`engines/publish.py` `ReportPublisher`|gh-pages + sticky PR comment|

`reporters/json_rep.py:833`에 legacy payload 변환 경로(`migrated`)가 이미 있다.
spec-04 §5의 "legacy JSON viewer는 reader adapter 또는 명시 unsupported schema 진단으로 처리"를
설계할 때 이 코드가 출발점이다.

`redact_engine_result` / `core/redaction.py`가 결과에서 민감 값을 지운다.
spec-04 §5의 redaction 정책은 이 자산을 보존해야 한다.

## 7. 이 조사에서 확인하지 못한 것

| 항목 | 상태 | 후속 |
|---|---|---|
|`prepare_qmake_compilation_context`의 SUBDIRS 실제 처리 범위|미확인 (qmake 미설치 환경)|[WP14 #212](https://github.com/jihoon22-lee/ici/issues/212)|
|`collect_capability_inventory`의 전체 probe 비용|미측정|[WP06 #204](https://github.com/jihoon22-lee/ici/issues/204), [WP28 #226](https://github.com/jihoon22-lee/ici/issues/226)|
|`AnalysisCache` 키가 실제로 무엇을 덮는지|부분 확인 (`core/cache_identity.py` 존재)|[WP11 #209](https://github.com/jihoon22-lee/ici/issues/209)|
|`ReportPublisher`의 GHES 동작|미확인 (GHES 없음)|[WP25 #223](https://github.com/jihoon22-lee/ici/issues/223)|
|취소(SIGINT) 시 자식 프로세스 정리 실제 동작|미측정|[WP07 #205](https://github.com/jihoon22-lee/ici/issues/205)|
