# stable 셸 삭제 + 문서 통폐합 + flat CLI cutover 작업 기록

## Overview

`docs/design/ici-next/inventory/stable-removal.md`의 삭제 계획을 실행했다:
`src/ici`의 stable 구현(엔진/리포터/stable CLI/설정 로더)과 전용 테스트 90개를
삭제하고, CLI를 평면 명령으로 전환했으며, 문서를 현행 코드 기준으로 통폐합했다.
PR-C = [#280](https://github.com/jihoon22-lee/ici/pull/280), 브랜치
`refactor/stable-shell-removal`(PR-A/B 위에 스택).

## Changes Made

### 삭제(79,552줄)

- `src/ici/engines/` 전체(엔진 클래스), `src/ici/reporters/` 전체(v3 리포터),
  stable CLI(`cli/cutover.py`, `doctor.py`, `compilation_export_cli.py`),
  `config_schema.py`, `config/__init__.py`의 stable 로더 표면(`DEFAULT_CONFIG`,
  `load_config`, `load_suite_from_json` — 유일 소비자인 stable publish도 삭제),
  stable 전용 `core/` 모듈군(baseline/cache/pipeline/cmake·qmake support 등),
  next가 도달하지 않는 `analysis/` 모듈 14개(clang-tidy/clazy/패키징/링커
  dead-symbol 등 — disposition은 migration matrix에 이미 기록).
- stable 전용 테스트 ~90개 + `cache_fixtures.py`. `tests/next/`는 유지·갱신.

### 유지된 것

import 클로저가 keep-set을 결정했다 — `ici.core`는 stable 네임스페이스가
아니라 양쪽이 쓰는 기반(process runner, env, compile-db 리더, path utils)으로
남았고, `ici.analysis`는 재배치된 순수 코어다. `test_namespace_boundaries.py`가
post-deletion 불변식을 고정한다.

### 컷오버

- `__main__.py` 평면화: `ici verify|plan|doctor|report|publish|diff|migrate|init`,
  `ici next …`는 별칭.
- 루트·viewer `ici.toml` → `schema_version = 1`.
- CI/release dogfood → `verify --result` + `report` + `check_next_floors.py`;
  viewer 빌드 트리(`build/ici-main`, `build/ici-static`)는 CI job이 소유.
  publish는 workspace 라벨별 호출 2회로 분리.
- 문서: `architecture/engine-reference/user-guide/ci-integration.md` 현행으로
  재작성, superseded 문서 2개와 v3 baseline JSON 삭제, README·AGENTS 정리.

## 삭제 실행 중 발견·수정한 next 버그

1. **pytest provider**: `-v`로 호출해 pytest 9의 per-node verdict를 얻지 못해
   `pytest.cases`가 비고 `tem.*=0.0`이었다. `-vv` + 판정 불가 출력의
   `failed_to_parse` fail-closed 처리.
2. **dead-code/cycle 모듈명**: `source_dirs=component_root`로 `src.ici.x`가
   생성돼 cross-module 참조가 모두 실패했다. `python_source_roots`가
   `__init__.py` 체인으로 import root를 유도 — dead-code는 과탐지(33→0),
   cycle은 과소탐지(edge 누락, fail-open)였던 것이 정정됐다.
3. **orphan 제거**: 수정된 dead check이 발견한 진짜 orphan 6개 함수 삭제.
4. **mypy 5건**: resolver의 None-probe 가드 assert, `python_request`의
   `workspace_root` 타입, `ResolvedTool` narrowing, Optional callback 가드.

## 게이트 의미 변경(명시적)

next는 required check의 measured finding을 violation으로 센다 — stable의
warn/fail 밴드(건수가 아니라 비율·점수)와 다르다. `python.dup/complexity/
cognitive/line`과 `cpp.dup`을 `required=false`로 두고, 수치 계약은
`scripts/check_next_floors.py`가 결과 문서의 `metrics[]`에 대해 강제한다:
repo(lines≥85/branches≥65/functions≥83/TEM≥4.0, dup≤12%),
viewer(cpp lines≥60/branches≥38/functions≥62/TEM≥2.4, ctest 존재).
메트릭 부재는 skip이 아니라 실패다.

## 검증 결과

- pytest 전체 스위트 통과(1970 collect, skip 5, 실패 0), ruff/mypy 클린.
- `build-pyz.sh` + `smoke.sh` 통과 — pyz가 flat CLI로 동작.
- repo 자체 dogfood: `ici verify` PASS, `pytest.cases=1963/1969`,
  `tem.ici=4.24`, coverage.lines=87.9. floors 통과.
- viewer dogfood: 로컬 cmake 빌드 후 `verify` PASS — ctest 7/7, gcov
  coverage, ELF readelf 증거, artifact manifest, 비어 있지 않은 limitation
  목록(unresolved include heuristic, ABI floor 미선언)까지 정직하게 기록.

## Known limitations / 후속

- **Quality Zoo**: 16 scenario가 stable `verify --report`/`ici.result/v3`
  계약이다. `ici.next.run` 이관이 다음 candidate 수용 전 필요.
- `cpp.binary-compat`: ELF/readelf 측정은 되나 배포 floor(ELF class/machine,
  max glibc)는 선언 불가 — 미판정 limitation으로 보고, CI는 readelf로 직접
  검사한다.
- coverage 측정이 stable 계측보다 낮게 나온다(87.9 vs 89.2) — 측정 방법이
  달라 생긴 차이로 추정, floor는 next 실측값을 ratchet했다.
- `ici report`에 `--github-summary` 상당물이 없다 — Actions 요약은 현재
  미구현(필요하면 SARIF 또는 이벤트 스트림 기반으로 별도 설계).
