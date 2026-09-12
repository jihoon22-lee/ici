# 현행 19개 엔진 전수 조사

- 상태: **조사 완료 (measured)**. 이 문서는 관찰 기록이며 목표 설계가 아니다.
- 조사 대상 커밋: `20c417cc8ec84aa490d0138782bf9fe38374fb5d` (= 조사 시점 `main` HEAD, 차이 없음)
- 근거 이슈: [WP00 #198](https://github.com/jihoon22-lee/ici/issues/198) 구현 순서 1
- 목표 처리 방향: [spec-03-analysis-engines.md](../spec-03-analysis-engines.md) §3
- 추출 방법: `src/ici/core/pipeline.py`의 `ENGINE_DESCRIPTORS`와 `src/ici/core/support.py`의
  `_DECLARATIONS`를 로드하여 기계적으로 덤프했다. 손으로 옮긴 값은 없다.

## 0. registry 일치 확인

| 항목 | 값 |
|---|---|
| `ENGINE_DESCRIPTORS` 개수 | 19 |
| `ENGINE_NAMES` 개수 | 19 |
| `_DECLARATIONS`가 덮는 엔진 | 19 / 19 (각 엔진이 `python`·`cpp` 두 행을 모두 선언) |
| inventory 행 개수 | 19 |

이 일치는 [`tests/test_ici_next_inventory.py`](../../../../tests/test_ici_next_inventory.py)가
기계적으로 검증한다. registry에 엔진을 추가하거나 제거하면 그 테스트가 깨진다.

## 1. 스케줄링·데이터 흐름 (pipeline.py 원본)

`EngineDescriptor`의 필드를 그대로 옮긴 표다. `exec`는 `EngineExecution`,
`variant`는 `BuildVariant`이며 `build` 실행 엔진만 variant를 갖는다.

|엔진|factory|선행|produces|consumes|profiles|exec|variant|
|---|---|---|---|---|---|---|---|
|line|`LineCountEngine`|—|`findings:line`|`analysis-context`|fast/standard/deep|read-only|—|
|lint|`LintEngine`|—|`findings:lint`|`analysis-context`|fast/standard/deep|read-only|—|
|compile_db|`CompileDatabaseEngine`|—|`findings:compile-db`, `compilation-coverage`|`analysis-context`|fast/standard/deep|read-only|—|
|test|`TestEngine`|—|`findings:test`, `test-results`, `coverage-report`, `build:coverage`|`analysis-context`, `capability-inventory`|standard/deep|**build**|coverage|
|type|`TypeCheckEngine`|—|`findings:type`|`analysis-context`|fast/standard/deep|read-only|—|
|python_compat|`PythonCompatibilityEngine`|—|`findings:python-compat`, `python-runtime-evidence`|`analysis-context`|fast/standard/deep|read-only|—|
|cognitive|`CognitiveEngine`|—|`findings:cognitive`|`analysis-context`|**deep**|read-only|—|
|resource|`ResourceEngine`|—|`findings:resource`|`analysis-context`|fast/standard/deep|read-only|—|
|security|`SecurityEngine`|—|`findings:security`|`analysis-context`|fast/standard/deep|read-only|—|
|cycle|`CycleEngine`|—|`findings:cycle`|`analysis-context`|fast/standard/deep|read-only|—|
|complexity|`ComplexityEngine`|—|`findings:complexity`|`analysis-context`|fast/standard/deep|read-only|—|
|sanitize|`SanitizeEngine`|—|`findings:sanitize`, `sanitizer-results`, `build:sanitize`|`analysis-context`, `capability-inventory`|standard/deep|**build**|sanitize|
|thread_sanitize|`ThreadSanitizeEngine`|—|`findings:thread-sanitize`, `thread-sanitizer-results`, `build:thread-sanitize`|`analysis-context`, `capability-inventory`|**deep**|**build**|thread-sanitize|
|dead|`DeadCodeEngine`|—|`findings:dead`|`analysis-context`|fast/standard/deep|read-only|—|
|dup|`DuplicateEngine`|—|`findings:dup`|`analysis-context`|fast/standard/deep|read-only|—|
|exception|`ExceptionSafetyEngine`|—|`findings:exception`|`analysis-context`|fast/standard/deep|read-only|—|
|build|`BuildEngine`|—|`findings:build`, `artifact-manifests`, `build:release`|`analysis-context`, `capability-inventory`|**deep**|**build**|release|
|binary_compat|`BinaryCompatibilityEngine`|**build**|`findings:binary-compat`, `elf-facts`|`analysis-context`, `capability-inventory`, `artifact-manifests`|**deep**|read-only|—|
|integration|`IntegrationEngine`|**build**|`findings:integration`, `integration-results`|`analysis-context`, `artifact-manifests`|**deep**|**build**|release|

관찰:

- 19개 중 **선행 의존이 있는 엔진은 `binary_compat`과 `integration` 둘뿐**이고, 둘 다 `build`에
  의존한다. 나머지 17개는 `analysis-context` 하나만 소비하는 평평한 구조다.
- 빌드를 수행하는(`exec=build`) 엔진은 `test`/`sanitize`/`thread_sanitize`/`build`/`integration`
  5개이고 variant는 `coverage`/`sanitize`/`thread-sanitize`/`release` 4종이다.
  `build`와 `integration`이 같은 `release` variant를 공유한다.
- `fast` 프로필은 `exec=build` 엔진을 하나도 포함하지 않는다. 이는
  [spec-03 §4](../spec-03-analysis-engines.md)의 fast 정의(숨은 configure/build 없음)와 이미 일치한다.

## 2. 언어별 active mode (support.py 원본)

`SupportDeclaration`의 `mode`/`confidence`/`fallback_mode`/`required_tools`/`optional_tools`/
`frameworks`를 그대로 옮겼다. `fw=qt`는 Qt 프레임워크 지원을 선언한 행이다.

|엔진|Python mode|Python conf|Python fallback|C++ mode|C++ conf|C++ fallback|Qt 선언|
|---|---|---|---|---|---|---|---|
|line|exact|exact|—|exact|exact|—|cpp|
|lint|tool-backed|high|heuristic|tool-backed|high|heuristic|cpp|
|compile_db|**unsupported**|low|—|exact|exact|heuristic|cpp|
|test|tool-backed|high|heuristic|tool-backed|high|heuristic|cpp|
|type|tool-backed|high|heuristic|**unsupported**|low|—|—|
|python_compat|tool-backed|high|—|**unsupported**|low|—|—|
|cognitive|heuristic|medium|—|**unsupported**|low|—|—|
|resource|heuristic|medium|—|**unsupported**|low|—|—|
|security|heuristic|medium|—|**unsupported**|low|—|—|
|cycle|heuristic|high|—|tool-backed|high|heuristic|cpp|
|complexity|heuristic|high|—|tool-backed|medium|heuristic|cpp|
|sanitize|tool-backed|high|—|tool-backed|high|—|cpp|
|thread_sanitize|**unsupported**|low|—|tool-backed|high|—|cpp|
|dead|heuristic|medium|—|tool-backed|**exact**|—|cpp|
|dup|heuristic|medium|—|heuristic|medium|—|cpp|
|exception|heuristic|medium|—|heuristic|medium|—|cpp|
|build|tool-backed|high|—|tool-backed|high|—|cpp|
|binary_compat|**unsupported**|low|—|tool-backed|**exact**|—|cpp|
|integration|tool-backed|high|—|tool-backed|high|—|cpp|

선언된 도구 요구(`required_tools` / `optional_tools`):

|엔진|언어|required|optional|
|---|---|---|---|
|lint|python|—|`ruff`|
|lint|cpp|—|`gcc`, `g++`, `clang`, `clang++`, `clang-tidy`, `clazy`, `pkg-config`|
|test|python|`python3`|`coverage`, `pytest`|
|test|cpp|`g++`|`cmake`, `qmake`, `make`, `gcov`, `pkg-config`|
|type|python|—|`mypy`|
|python_compat|python|`python3`|—|
|cycle|cpp|—|`gcc`, `g++`, `clang`, `clang++`, `cmake`, `readelf`, `addr2line`|
|complexity|cpp|—|`clang-tidy`|
|sanitize|python|`python3`, `pytest`|—|
|sanitize|cpp|`g++`|`cmake`, `qmake`, `make`, `pkg-config`|
|thread_sanitize|cpp|`g++`|`cmake`, `qmake`, `make`, `pkg-config`|
|dead|cpp|—|`gcc`, `g++`, `clang`, `clang++`|
|build|python|`python3`|—|
|build|cpp|`g++`|`cmake`, `qmake`, `make`, `pkg-config`|
|binary_compat|cpp|`readelf`|—|
|integration|python|`python3`|—|

관찰:

- **Python `unsupported`가 5개**(compile_db, thread_sanitize, binary_compat + type/python_compat의
  역방향), **C++ `unsupported`가 5개**(type, python_compat, cognitive, resource, security)다.
  spec-03이 요구하는 "Python-only가 무관한 C++ 도구를 요구하지 않는다"를 판정할 때
  이 표가 기준선이다.
- `heuristic` 모드가 Python 쪽에 8개(cognitive, resource, security, cycle, complexity, dead, dup,
  exception) 몰려 있다. spec-03 §3의 "자체 규칙"군과 정확히 겹치며, 이들이
  [WP20 #218](https://github.com/jihoon22-lee/ici/issues/218)의 주 대상이다.
- `fallback_mode=heuristic`을 선언한 엔진은 lint/compile_db/test/type/cycle/complexity 6개다.
  spec-03 §1은 "도구가 없을 때 조용히 heuristic으로 대체해 같은 PASS를 내지 않는다"를
  요구하므로, 이 6개의 fallback 활성 조건과 결과 표기가
  [WP21 #219](https://github.com/jihoon22-lee/ici/issues/219) 판정 계약의 입력이다.

## 3. 사용자 설정 키와 CLI 진입점

설정 기본값 원본은 `src/ici/config.py`의 `DEFAULT_CONFIG["engines"]`다.
모든 엔진이 `enabled`와 `mode`를 갖고, `mode`는 `pass_warn_fail` / `pass_fail` / `pass_warn`
세 값 중 하나다(`src/ici/engines/base.py:116` `evaluate_status`).

|엔진|기본 enabled|기본 mode|엔진 고유 설정 키|`ici <cmd>` 단독 실행|
|---|---|---|---|---|
|line|true|pass_warn_fail|`warn_limit`, `fail_limit`, `gate_dirs`, `include_dirs`, `exclude_dirs`|`line`|
|lint|true|pass_warn_fail|`ruff_required`, `clang_tidy`, `clazy`, `clazy_profile`|`lint`|
|compile_db|true|pass_warn_fail|`database_required`, `required_flags`, `forbidden_flags`|**없음**|
|test|true|**pass_fail**|`min_tem_score`, `min_line_cov`, `min_file_cov`, `min_file_statements`, `min_branch_cov`, `min_func_cov`, `coverage_required`, `quality.*`|`test`|
|type|true|**pass_warn**|`fail_on_error`, `warn_on_missing_annotation`, `mypy_required`, `mypy_profile`|`type`|
|python_compat|true|pass_warn_fail|`required`, `interpreters`, `required_interpreters`, `imports`, `target_version`, `wheel_globs`, `wheel_required`, `wheel_policy`, `check_entrypoints`, `check_package_files`, `max_wheels`, `max_wheel_members`, `max_wheel_uncompressed_bytes`|`python-compat`|
|cognitive|true|pass_warn|`warn`, `fail`, `warn_nesting`, `cpp_boundaries`|`cognitive`|
|resource|true|pass_warn|(고유 키 없음)|`resource`|
|security|true|pass_warn|`scan_tests`, `secret_name_allowlist`|`security`|
|cycle|true|pass_warn_fail|`max_reported`|`cycle`|
|complexity|true|pass_warn_fail|`warn_cc`, `fail_cc`, `warn_nesting`, `cpp_boundaries`|`complexity`|
|sanitize|true|pass_fail|(고유 키 없음)|`sanitize`|
|thread_sanitize|true|pass_fail|(고유 키 없음)|`thread-sanitize`|
|dead|true|pass_warn|`cpp_unused`, `cpp_linker`, `include_generated`, `include_vendor`|`dead`|
|dup|true|pass_warn|`warn_pct`, `fail_pct`, `min_window`, `python_semantic`, `include_generated`, `include_vendor`|`dup`|
|exception|true|pass_fail|(고유 키 없음)|`exception`|
|build|**false**|pass_warn_fail|`required`|`build` (전용 명령)|
|binary_compat|**false**|pass_warn_fail|`required`, `artifacts`, `expected_class`, `expected_machine`, `max_glibc`, `max_glibcxx`, `max_cxxabi`, `forbid_absolute_rpath`, `forbidden_needed`, `allowed_needed`, `forbid_build_paths`, `allow_non_elf`, `max_artifacts`|**없음**|
|integration|**false**|pass_warn_fail|`required`, `max_cases`, `max_output_bytes`, `python_targets`, `cases`|**없음**|

관찰:

- **기본 비활성 엔진은 `build`/`binary_compat`/`integration` 3개**다. 나머지 16개는 기본 활성이다.
- 단독 실행 CLI 서브커맨드는 `src/ici/__main__.py:272`의 `_ENGINE_COMMANDS`에서 생성되며
  **15개만 등록**된다. `compile_db`·`binary_compat`·`integration`은 단독 명령이 없고,
  `build`는 별도 `ici build` 명령을 갖는다. spec-01 §8의 migration 표에 이 차이를 넣어야 한다.
- 엔진 외부 설정으로 `project.source_dirs`, `build.make.*`(19개 키), `doctor.required_tools`,
  `ici.profile`이 있다. `build.make`는 기본 `enabled=false`이고 모든 argv 벡터가 빈 배열이다.

## 4. 탐색·빌드 side effect 지점

`exec=build`로 선언된 5개 엔진 외에도, 엔진이 프로세스를 띄우거나 환경을 보정하는 지점이 있다.
`ici.core.runner`를 import하는 모듈은 다음과 같다(엔진 본체와 헬퍼 모듈 모두).

- 엔진: `binary_compat`, `build`, `cognitive`, `complexity`, `cycle`, `dead`, `integration`,
  `lint`, `python_compat`, `sanitize`, `test`, `type_check`
- 헬퍼: `_clang_tidy`, `_clazy`, `_cpp_cognitive`, `_cpp_function_boundaries`, `_cpp_include_graph`,
  `_cpp_linker_dead_symbols`, `_cpp_lint`, `_cpp_tooling`, `_cpp_unused_functions`,
  `_sanitize_python_scope`, `test_interpreter`, `test_output`, `test_quality`
- core: `cmake`, `context`, `project`, `toolchain`

즉 **read-only로 선언된 엔진 중에서도 `cognitive`/`complexity`/`cycle`/`dead`/`lint`/
`python_compat`/`type`/`binary_compat`은 실제로 외부 프로세스를 실행한다.** read-only는
"프로젝트 산출물을 변경하지 않는다"는 뜻이고 "프로세스를 띄우지 않는다"는 뜻이 아니다.
spec-02 §4는 모든 프로세스를 `TaskSpec`으로 표현하도록 요구하므로, 이 구분을 문서와 이름에
명시해야 한다.

프로젝트별 경로 보정이 필요한 지점은 [execution-flow.md](execution-flow.md) §4에 따로 정리했다.

## 5. TEM 수식 (코드에서 추출)

원본: `src/ici/engines/coverage_support.py:945` `calculate_tem`.
이 수식은 **이번 작업에서 변경하지 않았다**. spec-03 §7이 요구하는 `formula_version` 고정의
입력으로만 기록한다.

```
pass_rate = passed_tests / total_tests           # total_tests == 0 이면 0.0

cov_factor, cov_label 결정 순서:
  1) coverage_totals["cover"] (line) 가 있으면
       cov_factor = min(80.0, line_cov) / 80.0 ,  label="Line"
  2) 아니면 coverage_totals["branch_cover"] 가 있으면
       cov_factor = min(80.0, branch_cover * 1.25) / 80.0 ,  label="Branch"
  3) 둘 다 없으면 (추정 입력)
       cov_factor = min(80.0, branch_cov) / 80.0 ,  label="Line", cov_suffix=" (est)"

tem_score = round(cov_factor * (func_cov / 100.0) * pass_rate * 5.0, 2)
tem_score = max(0.0, min(5.0, tem_score))        # [0, 5] 클램프
```

수식에서 읽어야 할 사실:

- **가중치는 곱셈 3항**이다. 가산 가중치나 항별 계수는 없다. 세 항 중 하나가 0이면 TEM은 0이다.
- **커버리지 항만 80%에서 포화**한다(`min(80, x)/80`). 즉 line coverage 80% 이상은 추가 점수가
  없다. 반면 `func_cov`는 `/100`으로 선형이며 포화점이 없다. 두 항의 스케일 기준이 다르다.
- **branch만 있을 때 1.25배로 올려 line 스케일에 맞춘다**(`branch * 1.25`). 이 환산 계수의
  근거는 코드에 주석으로 남아 있지 않다 → [ADR-0005](../adr/0005-tem-formula-freeze.md)의 보류 항목.
- **결측 처리**: `total_tests == 0`이면 `pass_rate = 0` → TEM 0. coverage 원자료가 전혀 없으면
  추정 `branch_cov`를 쓰고 `cov_suffix=" (est)"`로만 표시한다. 점수 자체는 측정값과 같은
  필드에 들어간다. spec-04 §2가 요구하는 evidence 축 분리가 TEM 점수에는 아직 반영되지 않았다.
- **최대값 5.0**은 `src/ici/engines/verify.py:358`에서 `max_tem_score=5.0`으로 하드코딩된다.
  게이트 하한은 `engines.test.min_tem_score` 기본 `4.0`이다.
- 집계 위치: `verify.py:330`이 `engine_name == "test"`인 결과의 `score`를 그대로 suite의
  `tem_score`로 승격한다. 즉 **현행 TEM은 `test` 엔진 하나의 점수이고 독립 엔진이 아니다.**
  spec-03 §7의 "coverage와 TEM은 독립 엔진이라고 가정하지 않는다"가 코드와 일치한다.

## 6. 테스트 fixture 매핑

이름으로 매칭되는 테스트 파일이다. 한 파일이 여러 엔진을 덮는 경우가 있어 중복이 있다.

|엔진|테스트 파일|
|---|---|
|line|`test_line.py`|
|lint|`test_lint_engine.py`, `test_lint_compilation_context.py`|
|compile_db|`test_compile_db_engine.py`|
|test|`test_test_engine.py`, `test_test_quality.py`|
|type|`test_type_engine.py`|
|python_compat|`test_python_compat.py`|
|cognitive|`test_cognitive.py`, `test_cpp_cognitive.py`|
|resource|`test_resource.py`|
|security|`test_security.py`|
|cycle|`test_cycle.py`, `test_cycle_compiler_context.py`|
|complexity|`test_complexity.py`|
|sanitize|`test_sanitize_engine.py`, `test_sanitizer_diagnostics.py`|
|thread_sanitize|`test_thread_sanitize_engine.py`|
|dead|`test_dead_engine.py`, `test_cpp_linker_dead_symbols.py`, `test_cpp_linker_dead_e2e.py`|
|dup|`test_dup_*.py` 13개|
|exception|`test_exception_engine.py`|
|build|`test_build_engine.py`, `test_build_adapter.py`, `test_build_adapter_e2e.py`, `test_build_variants.py`, `test_build_manifest_integration.py`|
|binary_compat|`test_elf_binary_compat.py`|
|integration|`test_integration_contract.py`, `test_make_elf_integration_e2e.py`|

실제 도구 fixture는 `examples/cpp-fixtures/` 아래 **10개 프로젝트**다:
`asan_overflow`, `clean_baseline`, `clone_pair`, `cmake_elf_dead`, `cmake_project`,
`complexity_hot`, `cycle_pair`, `dtor_throw`, `oversized_file`, `qmake_project`.

spec-05 §3이 요구하는 ici 소유 corpus는 아직 없다. 현행 fixture는
`tests/fixtures/ici-next/`가 아니라 `examples/`에 있고, fixture manifest(목적·지원 도구·
예상 finding·실행 비용)도 없다. → [WP03 #201](https://github.com/jihoon22-lee/ici/issues/201)

## 7. 잠정 disposition

spec-03 §3의 목표 처리표에 이번 조사 결과를 붙인 것이다. **확정이 아니다.**
각 행의 최종 근거는 담당 WP가 만든다.

|엔진|담당 WP|잠정 disposition|이번 조사에서 확인한 근거|
|---|---|---|---|
|line|[#206](https://github.com/jihoon22-lee/ici/issues/206), [#218](https://github.com/jihoon22-lee/ici/issues/218)|유지 (source metrics)|양 언어 `exact`. 도구 의존 없음. 이관 위험 낮음|
|lint|[#214](https://github.com/jihoon22-lee/ici/issues/214), [#215](https://github.com/jihoon22-lee/ici/issues/215)|유지 (provider 위임)|`.venv` ruff 탐색 의존 존재(§4·execution-flow §4). heuristic fallback 선언됨|
|compile_db|[#211](https://github.com/jihoon22-lee/ici/issues/211)|계층 이동 (입력 검증)|Python `unsupported`. C++ `exact`. 품질 finding과 입력 미완료가 한 엔진에 섞임|
|test|[#216](https://github.com/jihoon22-lee/ici/issues/216), [#217](https://github.com/jihoon22-lee/ici/issues/217)|분해 (Python/C++ 분리)|`sys.executable` fallback 존재. TEM 집계까지 겸함. 가장 많은 책임을 가진 엔진|
|type|[#215](https://github.com/jihoon22-lee/ici/issues/215)|유지 (provider 선택)|C++ `unsupported` 명시. `.venv` mypy 탐색 의존|
|python_compat|[#220](https://github.com/jihoon22-lee/ici/issues/220)|선택 제공|설정 키 13개로 19개 중 최다. wheel 검사까지 포함|
|cognitive|[#218](https://github.com/jihoon22-lee/ici/issues/218)|통합 후보 (complexity와)|Python `heuristic`, C++ `unsupported`. deep 전용. `cpp_boundaries` 키를 complexity와 공유|
|resource|[#218](https://github.com/jihoon22-lee/ici/issues/218)|비교 후 판단|Python `heuristic` 단독. C++ `unsupported`. 고유 설정 키 없음|
|security|[#218](https://github.com/jihoon22-lee/ici/issues/218)|비교 후 판단 (Ruff 중복 검토)|Python `heuristic` 단독. C++ `unsupported`|
|cycle|[#218](https://github.com/jihoon22-lee/ici/issues/218)|유지 (graph check)|Python `heuristic`/C++ `tool-backed`로 언어별 구현이 갈림|
|complexity|[#218](https://github.com/jihoon22-lee/ici/issues/218)|유지 + cognitive 통합 검토|1027행으로 엔진 중 최대. C++는 clang-tidy optional|
|sanitize|[#220](https://github.com/jihoon22-lee/ici/issues/220)|선택 제공 (동적)|Python required가 `python3`+`pytest`. NAS 경로 의존 존재|
|thread_sanitize|[#220](https://github.com/jihoon22-lee/ici/issues/220)|선택 제공 (별도 variant)|본체 57행. `sanitize`에 위임하는 얇은 래퍼. Python `unsupported`|
|dead|[#218](https://github.com/jihoon22-lee/ici/issues/218)|유지 (확정/휴리스틱 분리)|C++만 `exact`. Python은 `heuristic`. 두 신뢰도를 한 이름으로 보고함|
|dup|[#218](https://github.com/jihoon22-lee/ici/issues/218)|유지 (token 공유)|양 언어 `heuristic`. 테스트 파일 13개로 최다|
|exception|[#218](https://github.com/jihoon22-lee/ici/issues/218)|비교 후 판단|양 언어 `heuristic`|
|build|[#220](https://github.com/jihoon22-lee/ici/issues/220), prepare는 [#212](https://github.com/jihoon22-lee/ici/issues/212)/[#213](https://github.com/jihoon22-lee/ici/issues/213)|분리 (prepare / 산출물 검증)|기본 비활성. NAS 경로 의존. `integration`과 `release` variant 공유|
|binary_compat|[#220](https://github.com/jihoon22-lee/ici/issues/220)|선택 제공|기본 비활성. `build` 선행 필수. C++ `exact`|
|integration|[#220](https://github.com/jihoon22-lee/ici/issues/220)|선택 제공|기본 비활성. `build` 선행 필수. 임의 명령 실행 계약|

## 8. 후속 WP로 넘기는 미확인 항목

| # | 미확인 항목 | 상태 | 후속 WP |
|---|---|---|---|
|1|`fallback_mode=heuristic` 6개 엔진의 실제 활성 조건과 결과 표기|미확인|[#219](https://github.com/jihoon22-lee/ici/issues/219)|
|2|`branch * 1.25` 환산 계수의 근거|근거 없음 (코드·주석·문서 모두)|[#219](https://github.com/jihoon22-lee/ici/issues/219)|
|3|heuristic 자체 규칙 8종의 precision/recall|미측정|[#218](https://github.com/jihoon22-lee/ici/issues/218)|
|4|`cognitive`/`complexity` 지표 정의의 실제 중복 범위|미확인|[#218](https://github.com/jihoon22-lee/ici/issues/218)|
|5|`security`와 Ruff 규칙의 중복 범위|미확인|[#218](https://github.com/jihoon22-lee/ici/issues/218)|
|6|`compile_db` 부분 DB에서의 TU 누락 보고 정확도|미확인|[#211](https://github.com/jihoon22-lee/ici/issues/211)|
|7|`integration`의 임의 명령 실행 신뢰 경계|미확인|[#220](https://github.com/jihoon22-lee/ici/issues/220)|
|8|각 엔진의 실제 언어별 지원 하한(GCC/Qt/Python 버전)|미측정|[#226](https://github.com/jihoon22-lee/ici/issues/226)|
