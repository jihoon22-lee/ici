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

## 다중 코드리뷰 (5개 독립 에이전트)

컷오버 완료 후 5개 서브에이전트가 서로 다른 렌즈로 리뷰했다:
fail-closed/provider 경로, CI/publication/config, 문서/corpus,
toolchain/offline, 주석·주장 대비 실측. 아래는 확인된 이슈와 처리다.

### 수정된 것 (commit f36c444, 3e953c3, 8853d8f)

**Critical — fail-open / 진짜 손실:**

1. `report --sarif`가 HTML `--out` 작성 전에 early return → 둘 다 쓰게 수정.
   pyz로 `report --result X --out h.html --sarif s.sarif` 재검증, 두 산출물
   모두 생성 확인.
2. `viewer/ici-static-cli.toml`: `cpp.binary-compat` required인데 유일한
   producer `cpp.artifact`가 disabled → required check가 영구 blocked.
   `cpp.artifact`를 advisory-but-enabled로 정정.
3. CI가 삭제된 `tests/test_cpp_e2e.py`를 호출 → 스텝 제거, known-answer
   fixture는 `tests/next/test_corpus_fixtures.py`(7개, 신설)가 next 경로
   분석 함수를 직접 검증. manifest의 `exercised_by` 갱신 — 이관 안 된
   real-tool fixture(sanitizer/linker/cmake/qmake)는 빈 목록으로 정직 표기.
4. pytest 파서: findings-class exit인데 stdout이 비면 빈 `ParsedOutput`으로
   PASS할 수 있었음(pytest 부재가 stderr만 쓰는 경우 포함). 빈 출력·
   초록 0개+위반 0개를 `failed_to_parse`로. 같은 fail-open 패턴을
   mypy/ty/ruff-format에도 빈출력 가드로 추가(세 도구는 항상 요약줄을
   출력한다).
5. `publish`: `pull_request` 이벤트에서 `GITHUB_SHA`는 merge ref라
   stale-head 비교가 속을 수 있음 → event head SHA 우선.
6. 그래프: 컴포넌트당 동명 capability(`compile-inputs` 등)가
   duplicate-producer GraphError를 냈음 → `_with_scope`로
   컴포넌트 한정.
7. `ExecutionSummary`: `cancelled + required_complete` 조합을
   불변식 위반으로 거부했음 — 취소 요청과 완료 증거는 공존 가능하므로
   완화하고 report 조립이 blocked task id를 일관되게 유지.
8. `python_source_roots`가 `__init__.py` 체인을 따라 workspace 경계 밖까지
   올라갈 수 있었음 → 경계 인자 추가.

**결정론/toolchain:**

9. `[tools.*]`는 파싱되지만 아무것도 소비하지 않았음 — 선언하면 조용히
   무시되는 함정. compose에서 ConfigProblem으로 거부하고 overlay
   allowlist의 `tools.*.path`도 제거. spec-01 예제에 예약 표면임을 명시.
10. bare-name `[python] executable`/target이 cwd 기준 `is_file()`로
    검사되던 버그 → `_runnable`이 PATH로 해석. bundle 모드의
    `{python:NAME}` bare-name은 `ICI_BUNDLE_ROOT` 하에서 거부
    (analyzer 규칙과 동일하게).
11. `ici plan`이 `.ici/cache/coverage`를 mkdir로 생성했음 — `mutate` 플래그로
    plan은 read-only, verify만 생성.
12. verify 태스크 환경이 `os.environ`을 통째 상속 — `EnvironmentSnapshot`
    의 `without_stale_virtualenv`를 배선.

**문서/수치:**

13. README: pyz "약 2MB"→2.4MB 실측, "C++ 16종"→15종, 죽은 앵커 수정.
    CHANGELOG의 없는 spec-02 링크 → `task-execution.md`. engine-reference에
    integration 도메인 check와 qtest 추가.
14. floor 스크립트에 stable 실패 밴드 상당의 ceiling 추가
    (`max_complexity≤25`, `max_cognitive≤60`, repo 실측 24/48).

### 확인됐으나 이번 범위 밖 / 후속으로 남김

- **verify 태스크 환경**: stale VIRTUAL_ENV만 제거했다. 완전한
  `for_project` 격리(ici 변수 차단)는 project child가 PATH·Qt·CA 변수를
  필요로 해서 별도 설계가 필요 — env snapshot 차이는 result에 기록되는
  게 다음 단계.
- **`_identifier`의 which fallback**: argv[0]이 bare name일 때 실제 실행될
  바이너리를 해시하는 것으로 정책 우회가 아니라 정확한 동작 — 유지.
- **compiler provider**: 성공한 컴파일은 출력이 없는 게 정상이라 빈출력
  가드를 두지 않음(구조적으로 다른 계약).
- **real-tool fixture 이관**: sanitizer/linker/cmake/qmake 시나리오의
  `exercised_by`가 비어 있음 — manifest에 명시했고 후속 포팅이 필요.
- **per-file coverage floor**: next result는 per-file coverage 메트릭을
  싣지 않아 집계값+findings로만 보강됨 — per-file 밴드를 되살리려면
  결과 스키마 확장이 필요.
- **quality-zoo**: stable v3 계약 그대로 — 별도 포팅 작업으로 유지.
- **주석 정리 잔여**: superpowers 시점 문서 안의 깨진 경로는 "시점 기록"
  정책상 그대로 둔다(docs README가 갱신 비대상으로 명시).

### 최종 검증 (f36c444 시점)

- pytest 전체: 통과 (1676 collect, skip 6, 실패 0)
- ruff check/format, mypy(158 files): 클린
- `build-pyz.sh` + `smoke.sh`: 통과, pyz 2.4MB
- 리빌드 pyz dogfood: `ici verify` PASS (844 findings, 모두 advisory),
  floor+ceiling 통과 — `pytest.cases=1670/1676`, `tem.ici=4.4`,
  `coverage.lines=89.7`, dup=10.1%
- `ici report --sarif + --out`: 두 산출물 동시 생성 확인
