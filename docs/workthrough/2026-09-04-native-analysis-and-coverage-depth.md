# 네이티브 분석과 커버리지 깊이 강화 기록

## 개요

이 문서는 `2e979e5` 이후 feature branch에서 진행한 C++ cognitive 분석, gcov JSON coverage
수집, coverage policy와 baseline regression 보강을 기록한다. 구현은 아직 stable release에
포함되지 않은 feature branch의 결과이며, 공개 stable version `0.10.2`와 release artifact는
변경하지 않았다.

## 배경과 범위

기존 coverage 경로는 C++ 테스트의 gcov 출력에서 line/branch/function 정보를 충분히 구분하지
못했고, 전체 coverage 숫자만으로는 어떤 source와 changed line을 검사했는지 재현하기 어려웠다.
또한 C++ cognitive 분석은 함수 경계를 source scanner에 의존할 때 macro, lambda, overload와
같은 구문에서 범위를 잘못 잡을 수 있었다. 이번 범위는 다음 세 가지를 하나의 bounded evidence
계약으로 연결한다.

- compiler-backed C++ function boundary와 bounded lexical cognitive estimate
- strict `.gcov.json.gz` 파서와 legacy text fallback의 명시적 차이
- overall/file/function/changed-line coverage target 및 opt-in baseline regression

Mutation은 이 범위에서도 실제 mutation score를 산출하지 않고 capability 관측으로만 남긴다.

### 자체 검증에서 발견한 구조 부채

원격 dogfood가 새 coverage policy를 실제 ici 코드에 적용하면서 기존 대형 모듈과 복잡한
오케스트레이션을 정확히 드러냈다. 기능 임계값을 낮춰 숨기지 않고 다음처럼 구조를 분리했다.

- `core/cmake.py`에서 CTest/QtTest/qmake 결과 파싱과 sanitizer evidence 정규화를
  `core/_cmake_test_results.py`로 옮겼다. 기존 `ici.core.cmake` import와 monkeypatch 지점은
  re-export로 유지한다.
- `engines/test.py`에서 coverage 실행·집계·policy projection을 `TestCoverageMixin`으로 옮기고
  새 모듈을 cache implementation identity에 포함했다. 외부 실행은 기존 `test.run_process`
  patch 지점을 통과한다.
- `_cpp_cognitive.py`의 단순 statement parser와 전체 분석 orchestration을 작은 bounded 단계로
  나눴다. 관련 C++ metric과 exact/estimated boundary 결과는 변경하지 않았다.

Ici 자체 `[engines.test]`에는 aggregate line `80%`, file line `10%`와 statement floor `5`를
명시했다. `10%`는 배포 기본값(`80%`)을 낮춘 값이 아니라 현재 최소 파일 실측 `12.9%`를
기록하는 별도의 self-debt floor다. 세 번의 연속 실측을 근거로 한 단계씩 올리고, 기본 정책은
그대로 유지한다.

## 변경 사항

### C++ cognitive: 경계와 metric을 분리

[`_cpp_cognitive.py`](../../src/ici/engines/_cpp_cognitive.py)와
[`cognitive.py`](../../src/ici/engines/cognitive.py)는 `cpp_boundaries = "auto" | "required" |
"off"` 정책을 사용한다.

- exact compilation context/database와 승인된 clang-tidy가 있으면 `clang-tidy-ast`가
  source-spelled function의 start/end line·column geometry를 확정한다.
- geometry가 exact여도 body의 CC/nesting은 `bounded-cpp-tokens`로 계산하는 lexical
  estimate다. `boundary_source`/boundary provenance와 `metric_confidence`를 별도로
  보존하며, 이 숫자를 compiler가 계산한 exact cognitive metric이라고 주장하지 않는다.
- literal/comment, nested lambda body와 preprocessor directive를 masking하고, initializer
  brace·`do/while`·unbraced control·statement-prefix attribute·digraph brace와 digraph lambda 등
  경계 사례를 bounded parser로 처리한다.
  C++ 전체 grammar 또는 runtime semantics를 증명하는 parser는 아니다.
- `auto`는 context/tool이 unavailable일 때에만 source scanner의 `ESTIMATED` 경계를 사용한다.
  tool을 시도한 뒤의 process/parser/replay/timeout/truncation/budget 오류는 estimate로
  숨기지 않는다. `required`는 partial/estimated를 `ERROR`/`NOT_RUN`으로 닫고, `off`는
  compiler probe 없이 의도적으로 bounded lexical 경로를 사용한다.

### gcov JSON evidence

[`core/cmake.py`](../../src/ici/core/cmake.py)는 coverage 수집 전에 `gcov --help`를 확인한다.
성공한 help가 `--json-format`을 광고하면 [`gcov_json.py`](../../src/ici/engines/gcov_json.py)의
`.gcov.json.gz` 경로를 선택한다.

- gzip member, UTF-8, duplicate JSON key, non-finite/floating number, object depth/cardinality와
  각 nested count를 bounded하게 검증한다.
- 디렉터리 집계는 report 4,096개, compressed 64 MiB, decompressed 256 MiB와 누적
  file/function/line/branch/call record 상한을 추가로 적용한다. 각 report의 실제 입력 크기를
  파서가 반환하므로 남은 누적 byte budget보다 큰 다음 report는 merge 전에 거부한다.
- format version `1`/`2`와 numeric GCC version을 허용하고, file/line/count, branch의
  fallthrough/throw, call, function의 name/demangled name·execution count·start/end
  line/column을 immutable tuple dataclass로 보존한다.
- v1 gcov가 basic-block ID를 생략하는 경우 branch를 source line 내 출력
  순서로 구분하고 `basic-block-or-line-order` provenance를 남긴다. ID가 있는 report는
  source/destination basic-block identity를 우선 사용하며, 한 branch에서 ID 한쪽만 빠진
  evidence는 거부한다. 동일 디렉터리의 format/GCC version이 섞이면 stale report가 branch
  identity를 이중 계산할 수 있으므로 전체 report set을 거부한다.
- throw unwind edge는 branch coverage 계산에서 제외한다. integration은 기록된 compilation
  directory 또는 project root를 통해 source를 매핑하고, 모든 expected production source가
  관찰되지 않으면 incomplete evidence로 거부한다.
- capability probe가 실패·timeout·truncation이거나 JSON report가 malformed/incomplete이면
  legacy text로 조용히 재시도하지 않는다. JSON을 광고하지 않는 성공한 legacy probe만
  `gcov-text`로 fallback한다.

Legacy text는 `function_geometry = "line-1-fallback"`과
`source_mapping = "legacy-header-suffix"`를 provenance로 남긴다. 이 경로에는 function
column/end-line과 JSON의 nested evidence가 없으므로 JSON과 같은 정확도로 해석하지 않는다.
공개 coverage row에는 changed-line 계산에 필요한 complete `executable_lines`/
`covered_lines` 배열을 노출하지 않고, 내부 policy 경로에서만 소비한다.

### Coverage policy와 baseline

[`coverage_policy.py`](../../src/ici/engines/coverage_policy.py)와
[`test.py`](../../src/ici/engines/test.py)는 다음 `[engines.test]` 설정을 source-located
target으로 투영한다.

```toml
[engines.test]
min_line_cov = 80.0
min_file_cov = 80.0
min_file_statements = 5
min_func_cov = 90.0
min_changed_line_cov = 100.0
changed_lines = ["src/a.cpp:12-14", "src/module.py:20"]
max_coverage_regression = 2.0
```

- `min_line_cov`는 aggregate executable line, `min_file_cov`는 statement floor를 넘은
  file별 line coverage, `min_func_cov`는 Python/C++ function aggregate를 검사한다.
- `changed_lines`는 caller가 `project/relative.py:10` 또는 `src/a.cpp:12-14`처럼 canonical
  project-relative POSIX path와 1-based line/range를 직접 지정한다. absolute path, `..` 탈출,
  빈 path, 역순·중복·겹침 범위, regular project file이 아닌 path는 거부한다. ici는 `git diff`를
  추측해 목록을 만들지 않는다.
- `min_changed_line_cov`는 caller가 선언한 범위에만 적용된다. 선언된 range에 측정된
  executable line이 하나도 없으면 `Coverage:Changed lines` `ERROR` target을 만든다.
- 측정된 모든 scope에는 `PASS` target도 남긴다. JSON function evidence에서 uncovered function은
  정확한 file/start/end line·column을 가진 `WARN` target이며
  `test_scope = "aggregate-project-suite"`를 기록한다. legacy text fallback은 line-1
  geometry만 제공하므로 column/end-line 정밀성은 보장하지 않는다.
- `coverage_provenance.scope`에는 included source, tests/generated-vendor-cache/entry-point
  exclusion rule, C++ entry-point 제외 목록과 Python `aggregate-project-suite`/C++
  `aggregate-test-binaries` context를 남긴다. JSON parser provenance에는 source mapping,
  expected/covered source와 ignored record, throw branch exclusion이 별도로 남는다.

[`core/baseline.py`](../../src/ici/core/baseline.py)의 coverage regression은
`max_coverage_regression`이 현재 실행 설정에 있을 때만 활성화된다. `ici.result/v3` baseline의
test coverage snapshot과 aggregate line/branch/function/changed-line 및 file line 값을
비교하고, 허용 폭을 넘는 하락은 `ici.test.coverage-regression.*` source-located delta로
기록한다. baseline에 coverage snapshot이 없으면 warning만 남기며 regression gate를 만들지
않는다. `--fail-on-new`가 적용될 때만 이 delta가 gate에 반영된다.

### Mutation 범위

Deep test-quality의 mutation 설정은 설치된 `mutmut`, `cosmic-ray`, `mutpy` capability를
probe한다. mutant를 만들거나 실행하지 않고 kill ratio/score를 계산하지 않으며, mutation
결과를 coverage·TEM·기본 test gate에 채택하지 않는다. 따라서 mutation counters는 도구
가용성 관측으로만 해석한다.

## 검증 결과

### 자동화된 계약 테스트

다음 focused tests가 구현 계약을 고정한다.

- [`tests/test_cpp_cognitive.py`](../../tests/test_cpp_cognitive.py): masking, control-flow
  nesting, malformed input, exact boundary provenance, required/off 정책과 bounded source
  intake
- [`tests/test_gcov_json.py`](../../tests/test_gcov_json.py): gzip/UTF-8/JSON strictness,
  duplicate key·non-finite number·count/type bounds, versions 1/2, geometry와 immutable
  dataclass 결과
- [`tests/test_coverage_policy.py`](../../tests/test_coverage_policy.py) 및
  [`tests/test_coverage_policy_config.py`](../../tests/test_coverage_policy_config.py): changed
  range validation, deterministic status merge, all-scope PASS target, uncovered function
  location, no-executable-line error, config defaults와 public-row redaction
- [`tests/test_baseline.py`](../../tests/test_baseline.py): source-located aggregate/file
  regression, opt-in behavior와 malformed snapshot rejection
- [`tests/test_build_adapter_e2e.py`](../../tests/test_build_adapter_e2e.py) 및
  [`tests/test_test_engine.py`](../../tests/test_test_engine.py): adapter selection, JSON
  consumption, expected-source completeness와 throw branch policy

### 실제 CMake/Qt fixture evidence

`examples/cpp-fixtures/cmake_project`를 CMake/Qt와 GCC 15.2로 실행한 실제 evidence는 다음과
같다.

| 항목 | 관찰 결과 |
|---|---|
| 실행 상태 | `PASS`, coverage evidence `MEASURED` |
| coverage | line `100.0%`, function `100.0%`, branch `100.0%` |
| function geometry | `counter.cpp`의 exact function 3개 (start line `3`, `5`, `9`) |
| gcov JSON inventory | report 5개, expected production source 1개, covered source 1개 |
| ignored scope | generated/external record 19개 (`ignored_file_records`) |

이 표는 해당 fixture와 GCC/Qt toolchain의 재현 evidence다. 다른 compiler version, build
generator, generated-source layout에서는 capability와 source mapping 결과가 달라질 수 있다.

## 한계

- C++ cognitive의 compiler 지원은 함수 경계와 source geometry까지다. CC/nesting은 bounded
  lexical estimate이고, full C++ grammar, macro expansion의 의미, template/runtime behavior를
  exact하게 증명하지 않는다.
- gcov JSON parser는 현재 format version 1/2 계약만 수용한다. legacy text fallback은 정밀한
  function geometry가 없고, parser 자체는 source path를 resolve하지 않으며 integration 계층의
  mapping과 expected-source completeness에 의존한다.
- coverage는 테스트가 실행되었다는 증거와 별개인 측정값이다. all-skipped test를 coverage
  JSON이 `MEASURED`/`PASS`로 승격하지 않는다.
- changed-line gate는 caller 선언 범위만 검사한다. ici가 Git diff의 의도나 변경 범위를
  판정하지 않으므로 CI/PR caller가 올바른 목록을 공급해야 한다.
- baseline regression은 opt-in이며, mutation score는 아직 채택하지 않는다. 이 범위만으로
  release readiness나 전체 I4 checkpoint 완료를 의미하지 않는다.

## 보안 경계

- gcov parser는 report당 compressed 8 MiB/decompressed 64 MiB와 디렉터리 누적 compressed
  64 MiB/decompressed 256 MiB, report 4,096개, JSON depth/cardinality 및 누적 file/line/
  function/branch/call/count를 bounded하게 제한한다. path 입력은 `O_NOFOLLOW` regular-file
  descriptor로 읽고 read 전후 identity를 확인하며, gzip trailing member와 malformed UTF-8/
  JSON을 거부한다.
- C++ cognitive source intake는 최대 2,048 source와 64 MiB UTF-8 bytes, function당 1,000,000
  tokens, control nesting 128을 사용한다. tool replay는 승인된 direct argv/context를 사용하고
  tool/source identity 변경, timeout, truncation, parser 오류를 fail-closed한다.
- changed-line path는 project-relative canonical path와 regular-file containment를 확인한다.
  외부/generated/test/entry-point record는 production evidence와 섞지 않고 exclusion/context
  provenance로 남긴다.
- baseline snapshot은 `ici.result/v3` 구조와 canonical project path/coverage number를
  검증한다. malformed snapshot이나 unsafe path를 새 기준선으로 허용하지 않는다.
- HTML/JSON에 public coverage row를 만들 때 policy 계산용 내부 line 배열을 그대로 노출하지
  않아 report 크기와 불필요한 경로 노출을 제한한다.

## 릴리스 상태와 다음 단계

이번 문서화와 feature branch 변경은 version/tag/GitHub release를 만들지 않는다. stable
`0.10.2`는 유지되고 release 승인은 없다. 다음 release 판단에는 전체 ici gate, 실제 C++/Qt와
toy-projects candidate 검증, PR/main CI·Pages와 문서/CHANGELOG 동기화를 별도로 완료해야 한다.
