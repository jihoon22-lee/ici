# ici 엔진 지원·기능 매트릭스

## Overview

`feat/engine-support-matrix`의 I1-2 작업은 13개 엔진의 Python/C++ 지원 범위를 실행 가능한
선언으로 만들고, 프로젝트별 적용 여부와 실제 실행 증거를 같은 계약으로 내보내는 기능이다.
`119823d`부터 `fc43a37`까지의 core/report/doctor/HTML 변경에 viewer commit `5c1d2a2`의
통합을 연결해 총 26개 엔진·언어 행을 `ici.result/v3`, `ici doctor --json`, HTML 및 C++
viewer에서 같은 필드로 볼 수 있게 했다. 최종 self-verify에서 doctor capability renderer의
복잡도 초과가 발견되어 `e65c742`로 렌더링을 분리한 뒤 재검증했다. 이 매트릭스는 설치된
도구를 새로 탐지하는 inventory가 아니라, 엔진이 주장할 수 있는 범위와 관찰된 결과를
과장 없이 구분하는 capability contract다.

## Context and purpose

기존 엔진 결과만으로는 다음을 구분하기 어려웠다.

- Python과 C++ 중 어떤 소스 scope가 실제 프로젝트에 존재하는지
- 엔진이 exact, heuristic, tool-backed 중 어떤 방식으로 분석하는지
- 필요한 도구와 선택 도구, 도구 부재 시 fallback이 무엇인지
- 해당 행이 프로젝트에 적용되지 않은 것인지, 비활성/미실행인지, 제한된 fallback으로
  추정된 것인지

특히 C++ 전용 프로젝트에서 Python 전용 엔진이 읽을 대상이 없는 경우를 실행 실패와
혼동하면 정상 프로젝트의 gate가 불필요하게 빨간색이 된다. 반대로 적용 가능한 엔진을
실제로 실행하지 않았는데 PASS처럼 보이면 검증 공백을 숨긴다. I1-2는 `NOT_APPLICABLE`와
`NOT_RUN`을 분리하고, `ESTIMATED` fallback에는 별도의 active mode와 confidence를 붙여
이 두 문제를 동시에 해결한다.

초기 matrix 표시는 동작했지만 `ici doctor`의 큰 렌더 함수가 self-verify complexity gate를
넘는 문제가 남았다. `e65c742`는 capability 행의 language/state/tools/detail 계산과 table
렌더링을 `_support_language`, `_support_state`, `_support_tools`, `_support_detail`,
`_render_support_table`로 분리해 doctor shell의 복잡도를 낮췄다.

## Architecture and data flow

```text
SupportDeclaration registry (13 engines × Python/C++)
        │
        ├─ project discovery: source files, headers, configured type, Qt markers
        ├─ effective policy: enabled + required/optional tool promotion
        └─ observed EngineResult.evidence (when verify/standalone has run)
                │
                ▼
       SupportMatrix(project scope + 26 evaluated entries)
                │
     ┌──────────┼───────────┬────────────┬──────────────┐
     ▼          ▼           ▼            ▼              ▼
  v3 JSON    doctor       HTML       Qt viewer      docs table
  writer     --json      support tab  parser/UI     registry-generated
```

`VerifyOrchestrator`는 활성 엔진을 실행한 뒤 `evaluate_support_matrix()`를 한 번 호출해
suite에 붙인다. 단독 엔진 CLI는 같은 함수를 `engine_names={raw_result.engine_name}`로
제한하므로 해당 엔진의 Python/C++ 두 행만 갖는다. 이후 reporter는 matrix를 재계산하지
않고 동일한 `VerificationSuiteResult`를 소비한다.

## Changes made

### 1. Core model and declaration registry

`src/ici/core/models.py`에 다음 공통 타입을 추가했다.

- `SupportLanguage`: `python`, `cpp`
- `AnalysisMode`: `exact`, `heuristic`, `tool-backed`, `unsupported`
- `EngineSupport`: 선언 mode와 active mode, `applicable`/`enabled`, evidence/confidence,
  framework scope, required/optional tools, fallback, limitations, reason
- `SupportMatrix`: project languages/frameworks와 평가된 entry 목록
- `EngineResult.support_matrix`와 `VerificationSuiteResult.support_matrix`의 optional 연결
- 기존 `EvidenceState.NOT_APPLICABLE`을 matrix 평가에 재사용하고 gate 집계 예외 처리. 적용되지 않은 required 엔진은
  `ERROR`/`WARN` 승격의 원인이 되지 않는다.

`src/ici/core/support.py`의 immutable `SupportDeclaration` tuple이 13개 엔진을 항상
Python 다음 C++ 순서로 선언한다. 문서 표도 이 registry에서 생성한다.

| Engine | Python | C++ / Qt |
|---|---|---|
| `line` | exact | exact (Qt) |
| `lint` | tool-backed → heuristic fallback | tool-backed (Qt) |
| `test` | tool-backed → heuristic fallback | tool-backed (Qt) → heuristic fallback |
| `type` | tool-backed → heuristic fallback | unsupported |
| `cognitive` | heuristic | unsupported |
| `resource` | heuristic | unsupported |
| `security` | heuristic | unsupported |
| `cycle` | heuristic | heuristic (Qt) |
| `complexity` | heuristic | heuristic (Qt) |
| `sanitize` | tool-backed | tool-backed (Qt) |
| `dead` | heuristic | unsupported |
| `dup` | heuristic | heuristic (Qt) |
| `exception` | heuristic | heuristic (Qt) |

`(Qt)`는 C++ 행의 framework compatibility metadata다. Qt 전용 semantic analyzer가
구현됐다는 뜻이 아니며, Qt 프로젝트의 C++ 소스 또는 CMake/qmake 기반 테스트 경로를
지원한다는 의미로 문서화했다. C++ semantic type, cognitive, resource, security, dead-code
분석은 아직 `unsupported`로 명시하고 compiler syntax 검사는 `lint`에 둔다.

### 2. Project and policy evaluation

`evaluate_support_matrix(project_root, config, results=None, engine_names=None)`는 다음을
결정한다.

1. `get_all_python_sources()`/`get_all_cpp_sources()`와 header suffix를 사용해 source scope를
   찾는다. 설정된 `type`/`project.type`은 비어 있는 프로젝트의 선언 scope도 보충하며,
   `pyproject.toml`, `CMakeLists.txt`, root `*.pro`는 아직 파일이 없는 프로젝트의 build
   identity를 보충한다. 표시 순서는 Python, C++로 고정했다.
2. `project.cpp_pkg_config`의 `Qt5*`/`Qt6*` 또는 root CMake/qmake의
   `find_package(Qt5/6)`, `qt_add_`, `QT +=`를 읽어 `project_frameworks=["qt"]`를 계산한다.
   이는 capability inventory의 실제 binary probe가 아니다.
3. effective engine policy의 `enabled`를 적용한다. `ruff_required`, `mypy_required`,
   `coverage_required`가 켜지면 각각 Ruff, mypy, Python coverage(C++는 gcov)를 optional에서
   required로 승격한다.
4. 선언과 project scope, 관찰된 `EngineResult`를 합쳐 evidence/reason을 정한다.
5. 실행이 관찰된 경우에만 active mode를 채우고, estimated 결과에는 선언된 fallback을
   active mode로 선택한다. 관찰되지 않은 행에는 active mode를 넣지 않는다.

Evidence와 confidence 규칙은 다음과 같다.

| 조건 | evidence | active mode | confidence |
|---|---|---|---|
| 언어가 없거나 선언 mode가 `unsupported` | `NOT_APPLICABLE` | 없음 | `low` |
| 적용 가능하지만 disabled 또는 결과가 아직 없음 | `NOT_RUN` | 없음 | `low` |
| 실행 결과가 측정됨 | `MEASURED` | 선언 mode | 선언 confidence |
| fallback 결과만 추정됨 | `ESTIMATED` | fallback mode (없으면 선언 mode) | exact/high는 medium으로 하향 |

### 3. Shared v3 output contract and redaction

`src/ici/reporters/json_rep.py`는 suite와 standalone engine 양쪽의 top-level
`support_matrix`를 동일한 object shape으로 직렬화한다. entry에는 engine/language, declared
`mode`, nullable `active_mode`, 적용/활성 상태, evidence/confidence, framework와 도구 목록,
fallback, limitations, reason을 모두 보존한다. writer는 다음 모순을 파일 생성 전에
거부한다.

- 중복 project language/framework/tool 또는 중복 engine/language pair
- unsupported인데 applicable인 행
- inactive/unobserved 행의 active mode
- observed applicable 행의 누락된 active mode
- required와 optional 양쪽에 동시에 있는 도구

`src/ici/schemas/ici-result-v3.schema.json`은 `supportEntry`와 `supportMatrix`의 내부
필드를 strict하게 정의한다. 다만 기존 v3 archive를 깨뜨리지 않기 위해 suite/engine의
`support_matrix` property는 required 목록에 넣지 않고 `object | null`로 선택화했다. 새
writer는 항상 key를 쓰며 matrix가 없으면 `null`을 쓴다. 따라서 예전 v3의 field 누락,
새 writer의 `null`, 실제 object를 모두 읽을 수 있고, v2 migration은 matrix를 임의로
만들지 않는다.

공통 redaction 경계(`src/ici/core/redaction.py`)는 matrix의 project framework와 entry의
engine name, framework, required/optional tool, limitation, reason을 다른 report 문자열과
같이 마스킹한다. reporter는 원본 모델을 다시 계산하거나 직접 노출하지 않는다.

### 4. Reporter parity

- `src/ici/doctor.py`: 엔진을 실행하지 않고 `evaluate_support_matrix()`를 직렬화한다.
  `doctor --json`은 verify와 같은 JSON shape을 제공하며, 적용 가능한 enabled row는 이
  시점에 `NOT_RUN`/active 없음이다. 표 출력은 scope, state, declared/active, evidence/
  confidence, tools, fallback, limitation 일부를 함께 보여주고 `--brief`는 scope 한 줄만
  추가한다.
- `src/ici/reporters/html/sections/support.py`와 `report.py`: matrix가 있는 report에만
  `Support & Capabilities` tab을 만들고, 주의가 필요한 applicable row를 먼저 펼친다.
  모든 field는 `html.escape()`를 거치며 reason/limitation은 details 내부에 보존한다. CSS는
  `src/ici/reporters/html/assets/style.css`에 inline으로 추가되어 zero-CDN 조건을 유지한다.
- 문서 표는 `render_support_markdown()` 출력과
  `<!-- ici:support-matrix:start/end -->` 블록을 exact-match test로 묶었다.

### 5. Doctor renderer hardening (`e65c742`)

`src/ici/doctor.py`의 capability table을 작은 helper와 `_render_support_table()`로 분리했다.
matrix 출력 내용은 바꾸지 않고 `render_doctor_table()`은 시스템/도구/경로 shell과 support
renderer를 연결하는 역할만 맡는다. 최종 측정에서 helper 함수 complexity는 3–12, render
shell은 10으로 self-quality 기준을 통과했다.

### 6. C++/Qt viewer integration (`5c1d2a2`)

viewer는 동일 v3 vocabulary를 C++ string model로 읽고 표시한다.

- `viewer/include/icirv/report_model.hpp`의 `SupportEntry`/`SupportMatrix`는 Python enum에
  종속되지 않고 string을 보존하며, suite/engine 모두 `std::optional<SupportMatrix>`다.
- `viewer/src/report_model.cpp`는 known language/mode/evidence/confidence enum, bool, array,
  nullable active/fallback mode와 required fields를 검증한다. matrix가 없거나 `null`인
  legacy v3 report는 계속 로드하고, malformed matrix는 `LoadError`로 거부한다.
- `viewer/src/main.cpp`의 CLI summary는 project scope와 각 capability row를 출력한다.
- `viewer/include/icirv/gui/main_window.hpp`와 `viewer/src/gui/main_window.cpp`는 scope label과
  read-only 9-column `QTableWidget`(engine/language/mode/active/state/evidence/confidence/
  tools/fallback)을 추가한다. limitation과 reason은 row tooltip으로 제공하고, omitted/null
  matrix 및 load failure에서는 이전 matrix를 지운다.
- `viewer/CMakeLists.txt`는 support JSON fixture를 대상으로 scope/header/framework row의
  CTest 계약을 추가했고, QtTest는 parser lossless read, omitted/null compatibility,
  malformed rejection 및 GUI row/clear behavior를 다룬다. Qt 5/Qt 6 공통 API만 사용해
  framework major 차이에 의존하지 않는다.

## Files

실제 I1-2 diff와 viewer commit `5c1d2a2`에서 관여한 파일은 다음과 같다.

### Core and output

- `src/ici/core/models.py`
- `src/ici/core/support.py`
- `src/ici/core/redaction.py`
- `src/ici/engines/verify.py`
- `src/ici/__main__.py`
- `src/ici/doctor.py`
- `src/ici/reporters/json_rep.py`
- `src/ici/reporters/html/sections/support.py`
- `src/ici/reporters/html/report.py`
- `src/ici/reporters/html/assets/style.css`
- `src/ici/schemas/ici-result-v3.schema.json`

### Python tests

- `tests/test_support_matrix.py`
- `tests/test_doctor.py`
- `tests/test_reporter_hardening.py`
- `tests/test_cli.py`
- `tests/test_verify_orchestrator.py`

### Documentation synchronized by the feature commits

- `CHANGELOG.md`
- `README.md`
- `docs/architecture.md`
- `docs/ci-integration.md`
- `docs/engine-reference.md`
- `docs/user-guide.md`

### Viewer integration files (`5c1d2a2`)

- `viewer/CMakeLists.txt`
- `viewer/include/icirv/report_model.hpp`
- `viewer/src/report_model.cpp`
- `viewer/src/main.cpp`
- `viewer/include/icirv/gui/main_window.hpp`
- `viewer/src/gui/main_window.cpp`
- `viewer/tests/fixtures.hpp`
- `viewer/tests/data/support_matrix_v3.json`
- `viewer/tests/test_report_model.cpp`
- `viewer/tests/test_main_window.cpp`

## Representative code snippets

### Declaration versus evaluated result

```python
# src/ici/core/support.py
SupportDeclaration(
    "lint",
    SupportLanguage.PYTHON,
    AnalysisMode.TOOL_BACKED,
    FindingConfidence.HIGH,
    optional_tools=("ruff",),
    fallback_mode=AnalysisMode.HEURISTIC,
    limitations=("Without Ruff, fallback validates AST syntax only, not style or lint rules.",),
)
```

The declaration says what the engine can provide. It does not claim that Ruff ran. The evaluator
adds `active_mode`, `evidence`, `applicable`, and `reason` only after applying project scope,
policy, and observed result evidence.

```python
# src/ici/core/support.py — evaluate_support_matrix()
if not language_present:
    evidence = EvidenceState.NOT_APPLICABLE
    reason = f"project has no discovered {declaration.language.value} source scope"
elif not supported:
    evidence = EvidenceState.NOT_APPLICABLE
    reason = f"{declaration.engine_name} does not support {declaration.language.value}"
elif not enabled:
    evidence = EvidenceState.NOT_RUN
    reason = "engine is disabled by effective policy"
elif result is None:
    evidence = EvidenceState.NOT_RUN
    reason = "applicable engine has not been run"

if applicable and enabled and evidence == EvidenceState.MEASURED:
    active_mode = declaration.mode
elif applicable and enabled and evidence == EvidenceState.ESTIMATED:
    active_mode = declaration.fallback_mode or declaration.mode
```

### JSON boundary

```python
# src/ici/reporters/json_rep.py
return {
    "engine_name": entry.engine_name,
    "language": entry.language.value,
    "mode": entry.mode.value,
    "active_mode": entry.active_mode.value if entry.active_mode is not None else None,
    "applicable": entry.applicable,
    "enabled": entry.enabled,
    "evidence": entry.evidence.value,
    "confidence": entry.confidence.value,
    "required_tools": required_tools,
    "optional_tools": optional_tools,
    "fallback_mode": entry.fallback_mode.value if entry.fallback_mode is not None else None,
    "limitations": entry.limitations,
    "reason": entry.reason,
}
```

At the v3 suite/engine boundary the same field is optional for compatibility:

```json
{
  "support_matrix": {
    "project_languages": ["python", "cpp"],
    "project_frameworks": ["qt"],
    "entries": [{
      "engine_name": "lint", "language": "python",
      "mode": "tool-backed", "active_mode": "heuristic",
      "applicable": true, "enabled": true,
      "evidence": "ESTIMATED", "confidence": "medium"
    }]
  }
}
```

### Viewer read/display path

```cpp
// viewer/src/report_model.cpp / viewer/src/gui/main_window.cpp
if (suite.support_matrix.has_value()) {
    showSupportMatrix(suite.support_matrix.value());
} else {
    clearSupportMatrix();
}

const QStringList columns = {
    QString::fromStdString(entry.engine_name), supportLanguage(entry),
    QString::fromStdString(entry.mode), optionalMode(entry.active_mode),
    supportState(entry), QString::fromStdString(entry.evidence),
    QString::fromStdString(entry.confidence), toolsSummary, optionalMode(entry.fallback_mode),
};
```

This keeps viewer presentation lossless for the producer's evaluated fields while retaining the
old v2/v3 report loading path.

## Verification results

### Feature-focused Python regression

The focused run completed successfully:

```text
uv run --python 3.10 pytest -q \
  tests/test_support_matrix.py tests/test_doctor.py \
  tests/test_reporter_hardening.py tests/test_cli.py \
  tests/test_verify_orchestrator.py
63 tests collected and passed
```

The covered categories are registry uniqueness and 26-row evaluation, Python/C++/Qt scope
discovery, estimated-versus-measured active mode, effective tool policy, NOT_APPLICABLE versus
NOT_RUN, schema/writer invariants and redaction, doctor JSON/table/brief parity, standalone CLI
matrix publication, and suite-level matrix attachment. This is the focused I1-2 run; it is not
presented as a final full-repository or final viewer count.

The full Python suite also collected 672 tests and exited successfully under Python 3.10. The
remaining local quality checks completed as follows:

```text
uvx ruff check .                 All checks passed
uvx ruff format --check .        90 files already formatted
uv run --python 3.10 mypy src/ici Success: no issues found in 55 source files
```

### Packaged self-verify and artifact checks

The first packaged self-verify exposed one quality regression in the new doctor output path:
the capability renderer reached complexity 30 and caused a doctor `FAIL`. After `e65c742`
split the renderer, the final `dist/ici.pyz` self-verify measured:

```text
Suite status                         WARN (Pass 8 / Warn 4 / Fail 0 / Error 0)
Tests                                672/672
TEM                                 4.81 / 5.0
Coverage (line / branch / function) 87.2% / 78.7% / 96.1%
Doctor helper complexity             3–12
Doctor render shell complexity       10
```

The produced v3 JSON validated against the Draft 2020-12 schema and contained 26 unique support
rows. A field-level comparison found doctor output and the static declaration registry exactly
matched; the HTML support tab remained zero-CDN; and the viewer CLI successfully parsed the
actual generated report.

### Viewer-focused coverage

The final viewer commit `5c1d2a2` was built locally with both installed Qt majors and the GUI tests
run headlessly. Each configuration completed all seven CTests:

```text
Qt 6.10.2 (default configure, QT_QPA_PLATFORM=offscreen)              7/7 passed
Qt 5.15.18 (-DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON, offscreen)           7/7 passed
```

The focused categories are: lossless C++ parsing of language/framework/mode/evidence/tools,
omitted/null matrix compatibility, malformed matrix rejection, CLI summary scope/header/framework
output, Qt table scope/row rendering, stale matrix clearing after omitted/null reports, and
replacement failure clearing.

### PR #86 C++ dogfood remediation

The first PR #86 CI run (`33324633373`) passed the main verification steps, both Qt 5 and Qt 6
viewer jobs, and report publishing. The only failing check was the separate C++ dogfood step for
`viewer/`. Its seven tests passed, but the quality gate reported branch coverage `76.0% < 80%`,
complexity 16 at `readSupportEntry`, and duplication `5.28%` caused by a new 13-line clone. The
failure stopped the merge as required by the PR gate.

The remediation was split into focused commits: `98afa01` separates support-entry identity and
evidence parsing and introduces the generic validated-array helper, `88304ca` adds GUI branch
tests, and `060105f` adds parser-validation branch tests. Together these preserve the strict v3
contract while covering the branches that the C++ dogfood run exposed and removing the duplicated
validation path.

The exact local viewer command now passes:

```text
cd viewer
../dist/ici.pyz verify --report --html verify_report.html
PASS — 7/7 tests
Coverage (line / function / branch) 94.6% / 97.7% / 80.4%
Complexity 15
Duplication 4.43%
Suite TEM 4.89 / 5.0
```

The refreshed PR sticky comment links resolved successfully: both the ici and viewer Pages URLs
returned HTTP 200, the published HTML contained the `Support & Capabilities` tab, and the report
contained no external script or stylesheet references; its CSS and JavaScript remained inline.

### Diff and compatibility checks

- The feature implementation is the nine-commit range `119823d..fc43a37`; no dependency was
  added for the matrix.
- `e65c742` is the post-dogfood doctor complexity refactor; it changes the rendering structure
  without changing matrix fields or semantics.
- The v3 schema keeps `support_matrix` optional at suite/engine level, while matrix internals are
  validated strictly.
- Documentation support rows are generated from the declaration registry and compared exactly in
  `tests/test_support_matrix.py`.
- Viewer sources are already committed in `5c1d2a2` and remain outside this documentation change's
  staging scope; this documentation commit stages only this workthrough and the I1-2 plan section.

## Pitfalls and operational usage

### Interpretation pitfalls

- `mode` is the declared capability; `active_mode` is what the observed evidence actually used.
  A `tool-backed` declaration with `NOT_RUN` has no active mode and is not proof that a tool ran.
- `NOT_APPLICABLE` means no project language or an explicitly unsupported language pair. `NOT_RUN`
  means the applicable engine was disabled or has not produced a result. Do not collapse them in
  dashboards or gate logic.
- `ESTIMATED` means a fallback result was used. Its confidence is deliberately reduced for an
  exact/high declaration. `unsupported` and `NOT_APPLICABLE` are not green evidence.
- Current `EngineResult.evidence` is engine-level. In a hybrid project, one language fallback can
  conservatively lower every applicable language row for that engine to `ESTIMATED`; per-language
  evidence remains a follow-up shared-context/engine-contract task.
- Required/optional tool lists describe policy and declaration. They do not replace the separate
  I2-1 capability inventory or prove that a binary is installed.
- `(Qt)` is compatibility metadata for C++/Qt projects, not a claim that every marked engine
  performs Qt semantic analysis. Qt major selection remains a build/adapter concern.
- Qt detection currently reads the configured package names and root CMake/qmake markers; a
  nested or generated build context may still require the project configuration and compile
  adapter to provide complete flags.

### Operational commands

```bash
# Inspect tools plus project scope/matrix before running engines.
ici doctor
ici doctor --brief
ici doctor --json > doctor.json

# Produce the shared suite JSON and zero-CDN HTML capability tab.
ici verify --report --html verify_report.html

# Standalone command: JSON contains only the selected engine's two language rows.
ici line --report
```

Consumers should read `support_matrix.project_languages`/`project_frameworks` first, then use
each entry's `applicable`, `enabled`, `mode`, `active_mode`, `evidence`, `confidence`, tool lists,
fallback and `reason` before treating any analysis as measured. A legacy v3 HTML report without
the optional matrix simply omits the support tab; an explicit JSON `null` has the same display
behavior.

## Next steps

The separate I2-1 capability inventory should probe interpreter/compiler/CMake/qmake/make/gcov/
clang/Qt/binutils availability and versions, then feed that evidence into future support
evaluation without moving the declaration contract into reporters. Later compile-context and
Qt-specific analyzers can replace `unsupported` rows only when their evidence and limitations are
represented in this same matrix.
