# stable 경로 제거 — 예비 inventory

- 상태: **계획 (planned)**. 이 문서는 현장 인수(RHEL 8.10·GHES·idk 실환경 검증 —
  [field-acceptance.md](../field-acceptance.md) checklist)가 끝난 뒤 실행할 stable
  껍데기 삭제의 대상을 미리 분류한다. **지금은 아무것도 삭제하지 않는다.**
  추적 이슈 [#265](https://github.com/jihoon22-lee/ici/issues/265)는 현장 접근
  확보까지 park 상태이며, 준비된 제거 작업은 PR
  [#280](https://github.com/jihoon22-lee/ici/pull/280)의 보존 브랜치
  `refactor/stable-shell-removal`에 있다 — 재개 시 그 브랜치에서 계속한다.
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

1. **현장 인수 승인**([field-acceptance.md](../field-acceptance.md); 추적 이슈
   #265는 park — 현장 접근 시 재개) — 그 전에는 이 문서까지만.
2. ~~`engines/_*.py` → `ici/analysis/` 재배치 PR~~ — 완료 (#278).
3. ~~§2 부분 추출 7개 모듈의 분할 PR~~ — 완료 (#279). 남은 공유 코어 추출은
   #281·#282로 이어졌다.
4. Engine 클래스 + reporters + stable 전용 core 삭제 PR — 준비물은 PR #280의
   보존 브랜치 `refactor/stable-shell-removal`.
5. `__main__.py`/cutover/config_schema 정리 PR + `AGENTS.md` §3·§4 개정
   (ADR-0003 프로세스대로 불변식 자체를 개정).
6. pyz/launcher/CI 워크플로 정리는 stable 산출물의 공식 종료와 함께.

각 단계는 독립 PR로 — 삭제 PR에 이동을 섞지 않는다(diff 추적 가능성).
