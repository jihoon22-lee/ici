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
