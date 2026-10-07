# ici 아키텍처 리뷰 후속 구현 — 9개 항목 작업 기록

## Overview

`ici` 전체 코드 리뷰(`/home/jihoon/.devin/plans/plan-d147afd99f9a9d59.md`)에서 도출된
9개 항목을 순서대로 구현했다. 전면 재개발 대신 이미 진행 중인 ici-next 전환을 완성하는
방향을 택했고, stable 경로의 게이트와 분석 코어는 보존하면서 next 경로의 정확성·계약을
강화했다. 8개 PR이 머지됐고(PR #269~#275, #277), 테스트 계층화 PR #276은 CI 통과 후
머지 예정이다.

## Context

- stable 경로(기존 `src/ici/` + `dist/ici.pyz`)와 next 경로(`domain/`/`application/`/
  `execution/`/`languages/`/`adapters/`/`toolchain/`)가 공존하는 전환기 구조.
- 본질 목표: fail-closed·오프라인·결정적 검증 게이트 — skip/부분 실행을 PASS로 보고하지
  않고, 증거와 게이트 의미를 정직하게 유지.
- 브랜치 보호가 최신 상태를 요구해(squash 머지 + up-to-date 강제) PR은 직렬로
  리베이스→CI→머지했다. CHANGELOG가 매 PR에서 충돌 지점이었다.

## Changes Made

### 1. next 스케줄러 per-task 예외 격리 (PR #269, merged)

`application/schedule.py`의 `_perform()`이 provider `parse()`/analysis 예외를 catch하지
않아 analyzer 하나의 버그가 `ici next verify` 전체를 traceback+exit 1로 죽이고 완료된
unit의 증거까지 버렸다. 예외를 해당 unit의 FAILED observation(`internal error:
{type}: {message}` limitation)으로 변환해 게이트는 INCOMPLETE가 되고 다른 unit의
증거는 보존된다. cache `identify` 콜백 예외도 같은 방식으로 uncached 실행으로
격리했다. `tests/test_schedule.py`(→ `tests/next/`)에 회귀 테스트 4건.

### 2. stable 엔진 팩토리 명시 레지스트리 (PR #270, merged)

`globals()[descriptor.factory_name]`와 `getattr(sys.modules[__name__], ...)` 문자열
디스패치를 `src/ici/engines/registry.py`의 `ENGINE_FACTORIES`로 대체했다. 레지스트리가
import 시점에 19개 descriptor의 factory_name 해석을 자체 검증하고 미등록 이름은
`PipelineDefinitionError`로 거절한다. `ici build`와 `_ENGINE_COMMANDS`가 같은
레지스트리를 공유하며, 테스트의 검증용 클래스 주입 seam이 모듈 속성→레지스트리
엔트리로 이동. `test_verification_pipeline`에 양방향 일치 계약 테스트 추가.

### 3. 캐시 구현 식별자 = 엔진 import 클로저 해시 (PR #271, merged)

수동 `CACHE_IMPLEMENTATION_MODULES` 목록을 폐기하고 엔진 모듈의 정적 `ici.*` import
클로저(전이 포함)를 해시한다. 소스는 `__loader__.get_source`로 읽어 pyz(zipimport)
안에서도 동작하고, `TYPE_CHECKING` 블록은 제외·함수 내부 지연 import는 포함한다.
키 계약이 `ici.analysis-cache-key/v4`로 상승해 v3 엔트리는 stale hit 없이 재계산된다.
이관 중 `sanitize`의 수동 목록이 자기 모듈 자체를 누락하고 있었음을 확인 — 수동 관리의
허점이 실재했다.

### 4. `ici/toolchain/` Resolver 라이브 배선 (PR #272, merged)

배포물에서 완전히 미연결이던 `ici/toolchain/`(901행: Resolver/candidates/environment/
launch)을 라이브 경로에 연결했다.

- `cli/next_testing`(현 `application/tooling`)의 `locate_tool`/`python_interpreter`가
  `Resolver`를 거치며, `live_resolver(probe=False)`가 plan/verify의 no-process 모드
  불변식(#206)을 지킨다 — `test_plan_starts_no_process` 계약 유지.
- 선언된 interpreter는 검사되고, 존재하지 않는 선언 값은 convention fallback 없이
  `Unresolved`(UNAVAILABLE)가 된다.
- UNAVAILABLE·UNSUPPORTED·BROKEN 구분이 doctor 출력에 반영되고, `next_common`의
  `_version_of`(직접 `subprocess.run(timeout=5)`)는 제거됐다.
- 버그 수정: 선언 값이 workspace-root 상대로 정규화되는데 `_anchor`가 component.root를
  끼워 넣던 문제를 workspace root 기준으로 교정.

### 5. cli/ 계획 로직 → application/ 이관 (PR #277, merged)

- `application/tooling.py` — `live_resolver`, `resolve_tool`, `resolve_python`,
  `python_interpreter` 등 도구/인터프리터 해석
- `application/testing.py` — `plan_test`, `expand_*`, `component_targets` 등
  테스트/커버리지 확장
- `application/gates.py` — `_gate_cpp`/`_gate_python`/diagnostics 계열
- `application/planning.py` — `_plans`/`_planner`/inventory 헬퍼
- `application/internal_checks.py` ← `cli/next_checks.py` 전체
- `application/integration.py` ← `cli/next_integration.py` 전체
- `cli/`는 옵션·명령·출력만 남아 3,520행→1,520행. `application`이 `cli`를 import하지
  않는 방향 유지.
- **이관 중 발견한 버그 수정**: `expand_cpp_sanitizer`의 qtest 분기가 ctest 반복의
  `marked` 변수를 참조하던 결함을 `marked_binary`로 수정(첫 suite가 qtest이면
  `NameError`, ctest 선행 시 stale 메시지). 회귀 테스트 추가.

### 6. 읽기 명령 부수효과 제거 + 네임스페이스 경계 (PR #274, merged)

- `ici env`·`ici cache`가 더 이상 정책 로드를 거치지 않아 PATH 조회/캐시 목록만으로
  `~/.config/ici/ici.toml`이 생성되던 부수효과 제거. `test_cli.py`에 회귀 테스트.
- 신규 `tests/test_namespace_boundaries.py`: stable/next 교차 네임스페이스 충돌 12개의
  목록 고정·신규 교차 충돌 금지·`ici.config_schema`↔`ici.config` 그림자 고정·
  next↔stable 교차 import를 허용 목록으로 제한(신규 커플링은 목록 편집을 강제하는
  tripwire). 리베이스 과정에서 `engines/registry.py`↔`languages/registry.py` 신규
  충돌을 실제로 잡아 12개로 갱신됐다.

### 7. `EngineResult.extra` 키 계약 (PR #273, merged)

엔진↔리포터 간 ~150개 최상위 매직 키를 `src/ici/core/result_keys.py`의 `EXTRA_KEYS`
레지스트리에 등록. `tests/test_result_key_contract.py`가 AST 스캔으로 `extra.get`/subscript
읽기·dict-literal 쓰기·같은 모듈 헬퍼 반환 키(1단계)를 감시해 미등록 키 사용을 거절하고,
등록됐으나 생산·소비되지 않는 키의 drift도 역방향으로 거절한다. 중첩 payload의 내부 키는
엔진별 하위 스키마로 계약 범위 밖.

### 8. tests/ 계층화 (PR #276, CI 통과 후 머지 예정)

next 경로 테스트 66개를 `tests/next/`로 기계적 이동. `tests/fixtures`, `conftest.py`,
`cache_fixtures.py`, `fixture_manifest.py`는 루트에 유지. 이동된 파일의
`Path(__file__).parents[N]` 계산과 `docs/design/ici-next/`의 테스트 링크를 한 단계
깊이로 교정. pytest `testpaths=["tests"]` 유지로 CI 명령 변경 없음.

### 9. stable 삭제 예비 inventory (PR #275, merged — 삭제 자체는 #265 대기)

`docs/design/ici-next/inventory/stable-removal.md`: 삭제 대상(엔진 클래스 19개·reporters·
stable 전용 core), 유지 대상(`engines/_*.py` 분석 코어·core 기반 타입 — adapters/languages가
공유 중), 전제(#265 현장 인수, #227 전환 승인), 분할 PR 경계를 기록.

## Verification Results

- 각 PR마다 `uv run --python 3.10 pytest` 전체 통과, `uvx ruff check .`·
  `ruff format --check .` 통과.
- stable 경로 변경(PR #270/#271/#274)은 `./scripts/build-pyz.sh` + `./scripts/smoke.sh`
  통과 (Ubuntu 26.04 / glibc 2.43 / WSL / Python 3.10 직접 실행 / Zero-CDN 검증 포함).
- GitHub CI: 머지된 8개 PR 전부 Verify & Dogfood·Viewer Qt5/Qt6 포함 전 체크 통과.

### 관찰된 flake

리베이스 후 전체 스위트를 두 개 동시에 돌렸을 때 `test_every_component_runs_and_keeps_
its_own_task_ids`에서 mypy exit 2가 한 번 발생. 단독/파일/prefix/단독 전체 스위트 재실행은
모두 통과했고 CI도 통과 — 동시 pytest 실행 간 리소스 경합 플레이크로 판정(회귀 아님).

## Errors and Fixes

- CHANGELOG 충돌: 매 squash 머지 후 모든 후속 브랜치에서 발생 → 두 항목 모두 유지로 해결.
- `main` 직접 커밋 사고 1건(`f64d330`, inventory 등록 테스트) → 즉시 전용 브랜치로
  이동(`7d3ac42`, 후에 275로 머지)하고 로컬 main 원복.
- `plans` 이름 충돌(F823): `next_path.py`에서 함수 import와 지역변수 충돌 → `all_plans`.
- `ClangTidyProvider` 잘못된 import 경로 → 올바른 provider 모듈로 교정.
- RUF002(en-dash) → ASCII 하이픈으로 교정.
- 이동된 테스트 경로 계산·문서 링크·CI 참조 일괄 교정(item 8).

## Next Steps

- PR #276 머지 대기(CI 진행 중).
- 항목 9의 실제 삭제는 #265 현장 인수·#227 전환 승인 후 inventory 문서의 분할 PR 경계대로 진행.
- 잠재 후속: application/cli import 경계 계약 테스트, tests/next fixture 발견 경로 계약.

---

# 두 번째 리뷰 후속 — plan 순수성·공유 분석 코어·레지스트리 통합 (PR #281)

## Overview

두 번째 전체 리뷰(`plan-6e9c54579d22a87c.md`)에서 도출된 Phase 0~1을 `fix/next-plan-side-effects`
브랜치(PR #281)에 구현했다. plan-time 파일시스템 쓰기 3곳을 실행 시점으로 이동하고,
이중 구현이던 분석/파서 코어 3종(dup 클러스터링, ruff 출력, mypy 출력)을 `ici.analysis`에
공유화했으며, 손작성 provider dispatch dict를 self-keying 팩토리로 통합했다.
공유화 과정에서 실제 결함 3건(next dup fingerprint 충돌, ruff end_column off-by-one,
next parser 관용 엣지)을 발견·수정했다.

## Changes Made

### 1. `ici next plan` 무쓰기 계약 복원 — `fix(next)`

계획 경로가 `.ici/cache/{coverage,pycache/compat,gcov/<component>}`를 계획 시점에
생성하던 문제(읽기 전용 트리에서 `OSError`)를 수정.

- `domain.tasks.TaskSpec.work_dirs` 필드 추가 — 필요 디렉터리를 선언 (`share_key` 제외:
  디렉터리는 실행 중에만 의미)
- `execution.process.TaskSpec.work_dirs` + `run_task`가 spawn 직전에 `mkdir -p`
  (상대경로는 task `cwd` 기준, `OSError` → `START_FAILED` → DID_NOT_RUN)
- 선언자: `pytest`(coverage data-file parent), `coverage`(report parent),
  `gcov`(work_dir=cwd), `compileall`(pycache_prefix)
- 계획 측 `mkdir` 제거: `gates.py`(coverage_dir), `testing.py`(pycache, gcov work_dir)
- `plan --json`의 각 check가 선언된 `work_dirs` 노출
- 부수 수정: `output_limit_bytes`가 `_executable_spec`에서 버려지던 dead 필드 배선 —
  `or DEFAULT`는 0을 삼키므로 명시적 `None` 체크

### 2. dup 클러스터링 공유 코어 — `refactor(analysis)` + `fix(dup)`

- `ici/analysis/_dup_clustering.py` 신설: adjacency 구축 → connected component →
  deterministic ordering → representative 선정 → 그룹 fingerprint → duplicated-line
  위치 집계를 단일 `cluster_matches(matches, files_data)`로 제공
- `engines/dup.py::_cluster_matches`와 `languages/duplicates.py::_cluster`+`_duplicated_lines`
  (후자는 같은 adjacency를 두 번 구축해 BFS 2회) 삭제 → 공유 코어 소비
- **발견된 drift**: next의 그룹 fingerprint 라벨이 `sha256/type2-region-v1`으로 stale —
  해시 입력은 동일했으므로 `v2`로 정정 (v2는 PR #135의 region-bounded 매칭 도입 시 부여된 라벨)
- **실제 버그 수정**: next finding fingerprint `dup-{group_fp16}-{file}`가 같은 파일 내
  같은 클론의 두 occurrence를 붕괴 → 결과 수집이 하나를 버림. 해시 입력을
  `(provider, group_fp, file, start, end)`로 바꿔 다른 provider와 같은 `sha256:` 형태로 통일
- `FINGERPRINT_VERSION` → `ici.next.fingerprint.v2` — 해시 입력이 바뀌었으므로 v1 baseline은
  잘못 delta하지 않고 비교 거부 (설계된 fail-closed 계약)
- 등가성 테스트 `test_cpp_clone_occurrences_are_identical_across_paths`가 이 충돌을 잡아냄

### 3. ruff 출력 파서 통합 — `refactor(analysis)`

- `ici/analysis/_ruff_output.py` 신설: `parse_check_json`(strict JSON) /
  `parse_format_text`(lenient union: `unformatted:`+`-->` + legacy `Would reformat:`) /
  `parse_legacy_format_text`(stable strict grammar: 개수 검증된 마지막 summary) /
  `parse_stderr_warnings` / `is_format_success_output` / `format_supports_json`
- `engines/lint.py`의 `_ruff_coordinate`/`_ruff_source_range`/`_parse_ruff_warning_blocks`/
  `_append_reformat_targets`/`_is_valid_format_success`/`_RUFF_*_RE` 삭제 → 공유 소비
- **drift 수정**: next의 `end_column`이 exclusive 값을 그대로 쓰던 것 → 공유 코어가
  `max(1, col-1)`로 inclusive 변환 (stable과 동일)
- **fail-closed 강화**: next의 `code`/`message` 누락 관용(`RUFF`/`(no message)` 기본값) →
  strict parse로 parse 실패 처리
- `format --check` span 좌표 regex를 `[1-9]\d*`로 — `0:0` 좌표가 SourceSpan에서 깨지던 경로 차단
- 등가성 테스트 `test_ruff_diagnostics_report_identical_locations_across_paths` 추가

### 4. mypy 출력 파서 통합 — `refactor(analysis)`

- `ici/analysis/_mypy_output.py` 신설: `parse_mypy_line`(severity 필터 파라미터),
  `parse_mypy_stream`(next의 전체-스트림 문법), `MYPY_SUCCESS_LINE_RE`/`MYPY_FOUND_SUMMARY_RE`/
  `MYPY_CONTEXT_RE` 상수
- severity 집합은 소비자 정책: stable은 `("error","note")` 유지(`warning:` 라인 →
  malformed → 전체 parse 실패), next는 전체 수용
- stable의 `[code]` 보존: `MypyDiagnostic.full_message`가 원래 라인 텍스트를 재구성해
  stable의 message/fingerprint 동작 불변
- stderr 병합·note 병합(`_merge_note_target`)·success 문법은 stable 정책으로 유지
- next tighten: `0` 좌표·빈 message 라인이 parse 실패로

### 5. provider 레지스트리 단일화 — `refactor(providers)`

- `adapters/providers/__init__.py`에 `builtin_providers(root, build_roots)` 팩토리 —
  16개 인스턴스의 `.name`으로 self-keying한 dispatch dict 반환
- `cli/next_path.py`의 손작성 16쌍 dict 삭제 → 팩토리 호출
- `tests/next/test_provider_registry.py` 신설 — 키 세트 고정 + name 일치 검증
- `gates.py`의 `TYPE_CHECKERS`(mypy/ty)는 config 기반 타입체커 선택이라 별개 관심사 → 유지

## Code Examples

### work_dirs 선언 → 실행 시점 생성

```python
# domain/tasks.py — 선언만 한다
task = TaskSpec(..., work_dirs=(str(coverage_dir),))

# execution/process.py — runner가 spawn 직전에 생성
for work_dir in spec.work_dirs:
    (cwd / work_dir).mkdir(parents=True, exist_ok=True)  # OSError → START_FAILED
```

### dup fingerprint 충돌 수정

```python
# before: dup-{group_fp16}-{file} — 같은 파일의 두 occurrence가 동일 fingerprint
# after: sha256(provider \0 group_fp \0 file \0 start \0 end)
digest = hashlib.sha256(
    "\x00".join((PROVIDER_NAME, group.fingerprint, file_path, str(start), str(end)))
).hexdigest()
fingerprint = f"sha256:{digest}"
```

## Verification Results

- `uv run --python 3.10 pytest tests/` 전체 통과 (ruff/mypy/registry 변경 후 전량)
- `uvx ruff check .` + `ruff format --check .` 통과
- 신규 테스트: `TestADeclaredWorkDir`(실행 시점 생성/취소 시 무쓰기),
  `test_plan_names_the_run_state_without_creating_it`(실제 interpreter fixture),
  dup·ruff cross-path 등가성 2건, `test_provider_registry.py`
- 발견 보고: 등가성 테스트가 설계된 대로 실제 결함(same-file occurrence 붕괴)을 잡음

## Deferred / Known Gaps

- **ctest 텍스트 verdict 의미론**: stable `_cmake_test_results.py`는 `Not Run`/`Disabled`를
  `executed=False`로 기록하지만 next `cpptest.py`의 `_CTEST_RE`는 `Passed|*verdict`만 인식해
  미실행 테스트를 조용히 누락(전량 미실행 시 "no verdicts"로 parse 실패에는 귀결).
  문법 공유가 아니라 "next가 미실행 케이스를 어떻게 표현할지" 의미 결정이 필요 → 후속 PR.
- Phase 2 잔여: `analysis/` `_` 네이밍 정리, `next_path.py` 렌더 헬퍼 분리,
  `TaskSpec` 이름 충돌(domain vs execution), 반복 스캔 최적화.
- Phase 3: `load_config` 무쓰기화, assert 감사, 플레이크 조사, 상태 대수 문서, 골든 등가성.
- `tide`/`mypy` 외 추가 파서 중복 조사: coverage/gcov/pytest는 next가 `engines/` 함수를
  직접 위임 중 — `analysis/`로의 재배치는 stable-removal inventory의 연장.

# 세 번째 리뷰 후속 — 모델 경계 정리·감사·문서화 (Phase 2/3)

## Overview

`refactor/next-model-boundaries` 브랜치(#281 스택). 실행 모델 명명 충돌 해소, CLI 출력
경계 분리, analysis 관례 명문화, 상태 대수 문서화, 그리고 Phase 3 감사 항목 2건의
조사 결과(load_config, assert)를 기록한다.

## Changes Made

### 1. `TaskSpec` → `ProcessSpec` 개명 — `refactor(execution)`

- `execution/process.py`의 `TaskSpec`(실행 프로세스 스펙)이 `domain.tasks.TaskSpec`
  (선언적 계획 모델)과 이름이 충돌했다. 설계 문서(first-complete-path, spec-02)는
  `TaskSpec`을 도메인 쪽에 배정하므로 실행 쪽을 `ProcessSpec`으로 개명 — `_executable_spec`
  경계의 언어와 일치. 21개 파일 일괄 변경(소비자 import·별칭·테스트).

### 2. `cli/next_render.py` 분리 — `refactor(cli)`

- `next_path.py`에서 출력 형성만 담당하던 6개 헬퍼(`_linked_builds`, `_build_line`,
  `_plan_json`, `_planned_dict`, `_plan_text`, `_verify_text` ~170줄)를
  `cli/next_render.py`로 이동. 명령 모듈은 request→scope→plan→run 흐름만 남고,
  문서/텍스트 형태는 render 모듈이 소유. 공개명(`plan_json` 등)으로 개명 —
  모듈 이름이 경계를 설명하므로 `_` 접두사 불필요.

### 3. `analysis/` `_` 접두사 관례 명문화

- 대량 개명 대신 `analysis/__init__.py` docstring에 관례를 기록: `_`는 "ici 패키지
  내부 전용" 신호이며 서브패키지 경계를 넘는 import에도 유지, 공용 API 승격 시에만
  개명한다.

### 4. 상태 대수 조합 표 — `docs(spec-04)`

- spec-04 §2에 "2.1 조합 규칙" 표 추가: `domain/result.py`가 `__post_init__`에서
  강제하는 허용/금지 조합을 표로 정리(PASS+violations 금지, FAIL/INCOMPLETE는
  reasons 필수, FULL scope는 required 충족·omission 불가, INCOMPATIBLE baseline은
  delta 불가 등 9개 규칙).

## Audit Results (코드 변경 없이 종결)

### `load_config` 전역 시드 — **계약이므로 유지**

`load_config(create_global_default=True)`가 `~/.config/ici/ici.toml`을 자동 생성하는
것은 숨은 쓰기가 아니라 문서화된 계약: `bundle-installation.md` §no-installs smoke가
빈 HOME에서의 유일한 생성물로 이 파일을 실측 검증한다. 다만 *읽기 전용* 명령
(`env`/`cache`)은 이미 `load_config` 자체를 우회하며, next 경로는 stable config를
읽지 않는다. `doctor`/`verify` 등 stable 명령에선 시드를 유지 — defaults 내용을
그대로 쓰는 파일이라 의미론적 no-op이며 설계된 first-run 편의다.

### assert 감사 — clean

`src/ici`의 모든 `assert`가 `x is not None` 타입좁힘 형태. `python -O`로 제거돼도
직후 역참조/비교가 AttributeError/TypeError를 내므로 무음 PASS 전환은 불가능.
검증 자체를 assert에 의존하는 위치는 없음(대조: `grep "assert " src/ici`).

### 스캔 최적화 — 구조상 중복 없음

`plans()`는 `inventory.take` 결과(`stock`)만으로 파일을 해석하며 재glob하지 않는다.
`verify`의 2차 `take`는 의도된 drift 감지. 컴포넌트 파일 해싱은 무결성 근거라 비용
자체가 계약. `_expand`의 per-match `is_file()`/`resolve()` 이중 syscall 정도만 미세
최적화 여지 — 측정 후 결정할 일로 보류.

## Verification Results

- `uv run --python 3.10 pytest tests/next` — 1303 passed
- `uv run --python 3.10 pytest tests --ignore=tests/next` — 2854 passed
- `uvx ruff check .` + `ruff format --check .` — 통과
- `./scripts/build-pyz.sh` — dist/ici.pyz 2.8M 생성 성공(재현성 정규화 포함)
- `./scripts/smoke.sh` — verify --html 실검증 포함 실행

## Deferred / Known Gaps

- ctest 텍스트 verdict 의미론(미실행 케이스 표현) — 위 §Deferred에 기록된 의미 결정 필요.
- 플레이크 조사, 골든 등가성(golden equivalence) — 후속.
- `load_config` 시드를 *읽기 명령*(doctor)에서도 분리할지는 stable UX 결정 — 현행 유지.

# 네 번째 후속 — Python defect parity·ctest verdict·플레이크 감사

## Overview

같은 브랜치(`refactor/next-model-boundaries`, PR #282)에 이어서 진행한 Phase 3 잔여:
golden 등가성의 Python 절반 보강, ctest 미실행 verdict 오보고 수정, 플레이크 조사.

## Changes Made

### 1. Python defect parity — `test(differential)`

- `examples/python-fixtures/defect_bed/` 신설: 내부 Python check 8개 각각에 극단적
  결함을 심은 시드(`src/` 레이아웃 — stable 엔진의 DEFAULT_SOURCE_DIRS가 요구).
- `test_python_defects_the_stable_engines_found_all_surface`: next verify를 한 번
  돌려 stable 엔진 8개의 per-file parity를 한 테스트로 검사. planted 파일이 stable
  히트에 먼저 포함되는지 어설션 — 엔진이 결함을 조용히 못 보는 상태도 여기서 걸린다.
- `test_python_clone_occurrences_are_identical_across_paths`: stable은 clone을
  informational PASS target으로 보고하므로 파일-상태 parity 대신 occurrence 좌표
  비교로(C++ 케이스와 같은 강도).
- fixture는 quality-zoo와 같은 이유로 ruff lint/format 제외 대상(pyproject 주석).

### 2. ctest 미실행 verdict — `fix(next)`

- **발견한 실제 버그**: `_CTEST_RE`의 `\*+\S+` 캡처가 `***Not Run (Disabled)`를
  `***Not`로 절단 → disabled 테스트가 severity=high "test x: Not" 실패 finding.
- 수정: verdict 구문 전체(`sec` 컬럼까지)를 캡처하고 `not run`/`disabled`/`skipped`
  계열을 비실행으로 분류 — 총 케이스에는 포함, `ParsedOutput.limitations`에 이름 명시,
  finding 아님. stable의 `executed=False` 축과 같은 의미.
- 회귀 테스트 2건: 미실행 전량, 실패+미실행 혼합.

### 3. 플레이크 감사 — clean

- `test_execution_{cancellation,locks,process,tree}` 85개 × 3회 연속 실행 전부 통과,
  시간 분산 없음(~13.5s). 타이트 마진(`grace=0.1`)은 존재하나 대기 쪽 deadline이
  20s로 충분. CI에 retry/xfail 마킹 없음.

## Verification Results

- `pytest tests/test_next_differential.py` — 12 passed (Python parity 8 cases + clone
  occurrence 좌표 + 기존 C++ 케이스)
- `pytest tests/next` — 통과 (ctest 회귀 2건 포함)
- `ruff check .` + `ruff format --check .` — 통과

## Deferred / Known Gaps

- ctest 의미 결정이었던 "미실행을 어떻게 표현하는가"는 limitations+측정 분모로
  해소됨 — suite completeness 축을 명시할지(예: `ctest.executed` measurement)는
  선택적 후속.

# 다섯 번째 후속 — PR 통합·현장 인수 종결·저장소 정리

## Overview

사용자 요청 "모든 작업이 마무리된 상태로"에 따라 열린 PR 4개와 이슈 1개의
종결, 작업 브랜치·worktree 정리, 문서 최신화를 수행한다. 코드로 완료할 수
없는 항목(사내 실환경 인수)은 완료로 표기하지 않고 정직하게 park한다.

## Changes Made

### 1. PR 통합 순서

- **#281**(plan 무쓰기·공유 파서·provider 레지스트리): CI green 확인 후 머지.
- **#279**(공유 분석 심볼 추출): 스택드 베이스(`analysis-relocation`)가 이미
  머지돼 base를 main으로 retarget + 리베이스(CHANGELOG 충돌 해결 — 양쪽 항목
  모두 유지) → CI green → 머지.
- **#282**(ProcessSpec·render 분리·ctest fix·Python parity): main 리베이스
  후 CI green → 머지.

### 2. #280(stable shell 삭제) — 머지하지 않고 종결

- 실행 전제는 현장 인수(field-acceptance.md R/G/C 계열, 이슈 #265) — 사내
  RHEL 8.10/GHES/idk 실환경 접근이 필요해 이 환경에서 수행 불가. 로컬 CI를
  현장 근거로 쓰지 않는다는 이슈 자체 규칙에 따라 unmerged로 닫되, 원격
  브랜치 `refactor/stable-shell-removal`은 보존해 재개 가능 상태로 둔다.
- 준비된 작업의 위치는 stable-removal.md와 field-acceptance.md §6 D-1에
  기록했다.

### 3. #265(현장 인수) — closed/park 처리

- 체크리스트 수행 항목은 모두 사내 실환경 전용 — 미수행 항목을 완료로
  표기하지 않는 규칙에 따라 "완료"가 아닌 park(closed-not-planned)로 닫는다.
- 추적 정본을 이슈에서 `field-acceptance.md`로 이관: C(idk)·D(최종 결정)
  계열이 문서에 없어 §5·§6로 추가하고, docs 9곳의 "#265로 추적" 표현을
  checklist 지시로 교정. 이슈는 현장 접근 확보 시 재개.

### 4. 문서 관례 교정

- spec-04 조합 표를 `### 2.1` 번호 소제목에서 §2의 `>` 구현 노트 관례로 이동.
- defect_bed README를 한국어 표에서 기존 fixture README 관례(영문 산문)로 재작성.
- CHANGELOG 카테고리를 기존 목록(추가/수정/변경/구조/문서)에 맞춤 — 신조
  `검증`을 `추가`로 교정.
- `analysis/__init__.py`의 명명 관례 문단을 실제 규칙(이관 코어=공개 이름,
  전환기 추출 프리미티브=`_` 접두사)으로 정정.

## Verification Results

- #279 리베이스 후 로컬 전체 pytest 통과 + CI Quality Gate success(33m48s).
- #282 리베이스 후 로컬 전체 pytest 통과(100% 진행, 실패 없음) + CI
  success(19m31s).
- 충돌 마커·CHANGELOG 정합·ruff clean 확인.

## Deferred / Known Gaps

- RHEL 8.10·GHES·idk 현장 인수(field-acceptance.md R/G/C 계열) — 사내 환경
  전용, 이 저장소 작업이 아님. 이슈 #265 park.
- stable 경로 물리적 제거(#280 보존 브랜치) — D-1 승인 후 재개.
