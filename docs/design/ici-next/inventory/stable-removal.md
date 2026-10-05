# stable 경로 제거 — 예비 inventory

- 상태: **계획 (planned)**. 이 문서는 [#265](https://github.com/jihoon22-lee/ici/issues/265)
  현장 인수(RHEL 8.10·GHES·idk 실환경 검증)가 끝난 뒤 실행할 stable 껍데기 삭제의
  대상을 미리 분류한다. **지금은 아무것도 삭제하지 않는다.**
- 근거 원칙: stable 게이트·테스트는 전환 완료까지 유지(SPEC-05 §5). 분석 코어
  (`engines/_*.py` 파서·측정 프리미티브)는 삭제하지 않고 재배치한다 — next의
  `adapters`/`languages`가 이미 공유하고 있는 검증된 자산이다(R09: 새 구조로
  옮기기 어렵다는 이유만으로 자체 기능을 삭제하지 않는다).
- 측정 시점 코드: `main` @ PR #270 머지 후 (`f3d519e` 부근), cli 계획 로직 이관
  (`refactor/planning-to-application`) 반영 전.

## 1. 규모

| 영역 | 파일 | LOC | 처리 |
|---|---|---|---|
| `engines/` Engine 클래스 + 오케스트레이션 | 32 | ~15,900 | 삭제 (7개는 부분 추출 후 삭제) |
| `engines/_*.py` 분석 코어 | 36 | ~17,600 | `ici/analysis/`로 재배치 |
| `reporters/` (terminal/HTML/MD/SARIF stable) | 25 | ~6,100 | 삭제 |
| `core/` stable 전용부 | ~21 | ~9,000 | 대부분 삭제, 일부 재배치 |
| `config_schema.py` + stable `__main__` 명령군 + `cutover.py` | — | ~1,000+ | 삭제 |

## 2. `engines/` 분류

### 재배치 — `ici/analysis/` (또는 `languages/` 내부)로 이동

`engines/_*.py` 36개 전체. 파서·토크나이저·측정 프리미티브이며, next의
`adapters`/`languages` 26개 모듈이 이미 import한다. Engine 클래스 없이도
독립적으로 동작하는 순수 분석 자산이다.

### 부분 추출 후 삭제 — next가 비밀번호 아닌 실제 심볼을 import하는 모듈

| 모듈 | next 소비자 | 살릴 것 | 버릴 것 |
|---|---|---|---|
| `binary_compat.py` | `adapters/providers/binarycompat.py` | `BinaryCompatibilityEngine`의 분석 로직 | Engine 클래스 껍데기 |
| `complexity.py` | `languages/metrics.py` | `_cpp_function_inventory` | 나머지 |
| `coverage_support.py` | `adapters/providers/coverage.py` | coverage 판정 헬퍼 | — |
| `cycle.py` | `languages/cycles.py` | 순환 탐지 헬퍼 | — |
| `gcov_json.py` | `adapters/providers/gcov.py` | `parse_gcov_json_file`, `GcovJsonError` | — |
| `line.py` | `languages/python/lines.py` | `count_lines` | — |
| `test_output.py` | `adapters/providers/pytest.py` | `parse_pytest_outcomes`, `pytest_node_location` | — |

### 그대로 삭제

나머지 25개 shell 모듈 (`base.py`, `verify.py`, `registry.py`,
`pipeline` 관련, 각 엔진의 Engine 클래스). `registry.py`는 PR #270이 만든
명시 레지스트리 — stable 디스패치와 함께 소멸한다.

## 3. `core/` 분류

### next가 이미 쓰는 것 (유지 — 재배치 여부는 별도 결정)

| 모듈 | next 소비자 수 | 비고 |
|---|---|---|
| `models.py` | 15 | 공용 데이터 타입 |
| `context.py` | 7 | 실행 컨텍스트 |
| `toolchain.py` / `runner.py` / `compile_db.py` / `backend.py` | 각 1~3 | — |
| `compile_db`의 `_compile_db_*` 헬퍼 | (전이) | `compile_db`의 내부 의존 |

### stable 전용 (삭제 후보)

`baseline.py`, `cache.py`, `cache_codec.py`, `cache_identity.py`,
`capabilities.py`, `cmake.py`, `cmake_context.py`,
`compilation_export.py` + `_compilation_export_*` + `_cmake_test_results`
+ `_build_paths`, `cpp_replay.py` + `_cpp_replay_policy`, `env.py`,
`findings.py`, `make.py`, `path_utils.py`, `pipeline.py`, `project.py`,
`python_rule_registry.py`, `python_rules.py`, `qmake_context.py` +
`_qmake_commands` + `_qmake_wrapper`, `redaction.py`, `redaction_values.py`,
`runner_win.py`, `support.py`.

주의: 위 목록은 next 소비자 기준의 **직접** 분류다. 실행 시점에는 import
클로저(전이 의존)로 최종 keep-set을 계산한다 — stable 전용 모듈이 유지
모듈의 내부 의존이면 함께 남는다.

## 4. 그 외 삭제 대상

- `src/ici/config_schema.py` — stable 설정 스키마(`ici.config.schema`와 한
  글자 차이의 함정이 사라진다)
- `__main__.py`의 stable 명령군과 `cli/cutover.py` — cutover dispatch는
  stable 엔트리 제거와 함께 소멸
- `reporters/` 25개 — next는 `reporting/`(SARIF)과 `application/report`를
  쓴다
- stable 전용 테스트 및 `tests/fixtures`의 stable 전용 fixture

## 5. 실행 전제와 순서

1. **#265 현장 인수 승인** — 그 전에는 이 문서까지만.
2. `engines/_*.py` → `ici/analysis/` 재배치 PR (삭제 없이 이동만).
3. §2 부분 추출 7개 모듈의 분할 PR (next 소비자가 새 위치를 보게).
4. Engine 클래스 + reporters + stable 전용 core 삭제 PR.
5. `__main__.py`/cutover/config_schema 정리 PR + `AGENTS.md` §3·§4 개정
   (ADR-0003 프로세스대로 불변식 자체를 개정).
6. pyz/launcher/CI 워크플로 정리는 stable 산출물의 공식 종료와 함께.

각 단계는 독립 PR로 — 삭제 PR에 이동을 섞지 않는다(diff 추적 가능성).

---

## 6. 실행 기록 (2026-10, `refactor/stable-shell-removal`)

전제로 적혀 있던 #265 인수·#227 승인은 사용자 지시로 실행이 승인됐다. 실제
삭제는 이 문서의 keep-set보다 **import 클로저 기준**으로 이뤄졌다 — §3의
"stable 전용" 목록 중 next 소비자가 도달하는 모듈(`context.py`,
`capabilities.py`, `compile_db*.py`, `cpp_replay*.py`, `env.py`,
`findings.py`, `models.py`, `path_utils.py`, `project.py`, `runner*.py`,
`toolchain.py` 등)은 살아남았고, `ici.core`는 stable 네임스페이스가 아니라
공유 기반으로 남았다.

삭제됨: `cli/cutover.py`·`cli/doctor.py`·`cli/compilation_export_cli.py` 등
stable 명령, `engines/` 전체, `reporters/` 전체, `config_schema.py`,
`config/__init__.py`의 stable 로더 표면, 그리고 stable 전용 테스트 90개.
`__main__.py`는 평면 명령(`ici verify|plan|doctor|report|publish|diff|
migrate|init`)으로 재작성됐고 `ici next …`는 별칭이다. 루트·viewer의
`ici.toml`은 next 스키마다.

실행 중 발견해 함께 고친 next 버그:

- `pytest` provider가 `-v`로 불러 pytest 9에서 per-node verdict를 못 받아
  테스트 증거가 사라졌다 — `-vv`로 고쳐 `pytest.cases`가 돌아왔고,
  판정 불가 출력은 조용한 성공이 아니라 `failed_to_parse`로 보고한다.
- `_dead_counter`/`_measure_python`이 `component_root`를 import root로
  썼다 — `root="."` + `src/` 레이아웃에서 모듈명이 `src.ici.x`로 매겨져
  cross-module private 함수가 전부 dead로 잡혔다(cycle은 반대로 edge를
  놓쳐 과소탐지). `python_source_roots`가 `__init__.py` 체인으로 import
  root를 유도해 고쳤다.
- stable 셸 삭제로 orphan된 분석 헬퍼 6개(`_analyze_cpp_includes`,
  `_append_*_targets` 3개, `_cpp_metric_details`, `_artifact_id`) 제거.

게이트 의미 변화(명시적 결정): stable의 warn/fail 밴드는 check의 finding이
아니라 **측정값 floor**다. next 게이트는 required check의 measured
finding을 violation으로 세므로, `python.dup`/`complexity`/`cognitive`/`line`
과 `cpp.dup`은 `required=false`(advisory)로 두고 수치 계약은
`scripts/check_next_floors.py`가 결과 문서에 대해 강제한다 — floor는 최초
실측 아래의 ratchet이며 메트릭 부재는 실패다.

남은 것(이 PR 밖):

- **Quality Zoo 포팅**: corpus 16 scenario가 stable `verify --report`와
  `ici.result/v3`를 기대한다. runner·scenario·expectation의
  `ici.next.run` 이관은 다음 candidate 수용 전 전제다.
- `cpp.binary-compat`의 배포 floor(ELF class/machine, max glibc/cxxabi)
  선언 — 현재는 측정만 하고 미판정 limitation을 보고한다; CI가 readelf로
  직접 검사한다.
- coverage 측정치가 stable 계측보다 낮게 나온다(행 87.9 vs 89.2, branch
  68.8 vs 81.0) — 측정 방법 차이로 추정되며 floor는 next 실측을 ratchet했다.
