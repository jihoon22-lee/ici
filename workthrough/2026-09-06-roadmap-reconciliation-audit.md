# 마스터 플랜 실사 — 2026-09-06

## 왜 했나

마스터 플랜의 미체크 항목이 53개였다. 그대로 진행 순서를 잡으려다, 이미 끝난
항목이 섞여 있다는 정황을 먼저 발견했다. `ICI-GAPS.md`에서 A-2 항목이 실제
상태를 못 따라잡고 있던 것과 같은 종류다.

53개를 코드와 대조했다. 항목 본문이 스스로 단서를 단 경우(예: "broader corpus는
pending")는 구현이 있어도 완료로 옮기지 않았다. 아래는 그 결과이며, 각 판정에는 확인한 증거를 붙였다.
문서가 아니라 코드가 기준이다.

## 요약

미체크 53개는 남은 작업 53건이 아니다.

| 분류 | 개수 | 뜻 |
|---|---|---|
| 이미 완료 | 7 | 코드와 테스트로 확인됨. 체크박스만 뒤처졌다 |
| 부분 완료 | 11 | 절반 이상 구현됨. 남은 조각이 명확하다 |
| 파생 rollup | 6 | 18절 체크포인트. 하위 절이 닫히면 따라 닫힌다 |
| 실제 미착수 | 15 | 뷰어 UI 13, 성능 벤치마크 포함 |
| 조건부·판단 대기 | 14 | I9-2/I9-3의 릴리스 준비 조건 |

## 이미 완료 — 증거

| 항목 | 증거 |
|---|---|
| I5-4 envlens와 ici pyz를 서로 다른 packaging 사례로 사용 | envlens는 Python 3.10/최신 두 런타임에서 CI 검증(같은 절 하위 항목이 이미 `[x]`), ici는 `scripts/verify-reproducibility.sh`로 byte-identical zipapp |
| I7-1 abilens의 실제 Makefile로 build/test/sanitize/coverage 검증 | `abilens/ici-candidate.toml`의 `[build.make]`가 release/coverage/sanitize/thread-sanitize argv를 각각 분리된 `OUT` 트리로 구동. CI `Native product checks (abilens)` green |
| I7-3 stripped/malformed/non-ELF 구분 | `binary_compat.py`의 `ici.binary.non-elf` rule과 `stripped` fact |
| I8-1 SARIF 2.1.0 export와 rule/result/location/fix mapping 검증 | `reporters/sarif.py` + `tests/test_sarif_reporter.py` 9건. field mapping, URI 인코딩, 순서 독립성, baseline delta, rule/result bound을 각각 고정 |
| I9-3 CMake·qmake·configured Makefile green project 전부 PASS | loglens(CMake) · diskmap(qmake) · abilens(Makefile) 모두 main CI green |
| I9-3 pure Python / pure C++·Qt / hybrid 각각 최소 하나 | envlens · diskmap/loglens · buildscope |
| I9-3 quality-zoo stable scenario 전부 expected finding/location 만족 | released 6 scenario + candidate 16/16 원격 인수(run `33950030497`) |

## 부분 완료 — 남은 조각

| 항목 | 된 것 | 남은 것 |
|---|---|---|
| I4-3 boundary를 AST/tool 우선으로 | C++ exact function boundary 전환 완료 | 경계 안의 metric이 `bounded-cpp-statement` lexical estimate. **의도적으로 열어둔 상위 aggregate** |
| I4-3 duplicate generated/moc/vendor 제외 + fingerprint 통합 | `include_generated`/`include_vendor` 기본 False | token/region fingerprint 통합. `min_window` 보정은 증상 완화이지 통합이 아니다 |
| I7-2 artifact typed record | `EngineResult.artifact_manifests` 존재 | glob이 빈 결과이거나 project 밖으로 나갈 때 ERROR 처리 확인 안 됨 |
| I7-3 static requirement·forbidden dependency·floor 정책화 | `forbidden_needed` `allowed_needed` `forbid_build_paths` `forbid_absolute_rpath` `max_glibc/glibcxx/cxxabi` | static linkage를 요구하는 키가 없다 |
| I7-3 abilens executable/shared library와 viewer static CLI 실측 | abilens가 `release/bin/abilens`와 `libabilens-fixture.so`를 고정 | viewer `icirv`에 `binary_compat` 설정이 없다 |
| I8-1 reporter parity contract test | reporter별 테스트는 있다 | v3 finding·related location·confidence·suppression·delta를 **모든 reporter가 동일하게 보존하는지** 하나로 묶는 계약 테스트가 없다 |
| I8-1 GitHub annotation 제한 발행 | `MAX_GITHUB_ANNOTATIONS` bound와 status 우선순위 정렬, 생략 개수 notice | **new(baseline delta) 한정** 필터가 없다. 현재는 FAIL/ERROR/WARN/SKIP 전부가 후보 |
| I9-2 self dogfood ratchet | 이번 세션에서 cognitive 지표 결함 수정, 고복잡도 함수 3개 분할, dup window 보정으로 4 WARN → 3 WARN | `line`·`cognitive`·`complexity` 3개가 남았고 threshold 단계 상승은 미착수 |
| I9-3 release artifact 재현 가능성 | buildscope `0.5.0`이 재현 가능 패키징으로 공개 | envlens·abilens는 `Unreleased` |
| I9-1 expected absence로 false positive 고정 | quality-zoo `report_contract.py`의 `forbidden_findings` 구현. `cpp.quality-coverage`가 사용 중 | 항목 본문이 스스로 단서를 달고 있다 — broader false-positive corpus가 pending |
| I9-3 v2 migration과 v3 schema 안정성 정책 | `migrate_report_payload()`와 체크인된 `ici-result-v3.schema.json` | 안정성 정책 자체를 **발표**하는 문서가 없다 |

## 실제 미착수

**뷰어 UI (13항목)** — I8-2 report diff 5, I8-3 triage/suppression 4, I8-4 성능 4.

`viewer/`는 2,113줄의 Qt 리포트 리더다. JSON 파서, report model, main window, summary가
있다. 그러나 `delta`/`resolved`/`regressed`/`filter`/`suppress`를 언급하는 소스가
**0개 파일**이고, 10만 finding 벤치마크도 없다. 구현이 시작되지 않았다.

이 13개는 항목 수로 미착수의 대부분을 차지하지만 성격이 다르다. 분석 엔진이 아니라
**GUI 제품 개발**이다. 착수 전에 "뷰어를 어디까지 만들 것인가"를 먼저 정하는 편이
낫다.

**분석 깊이 (2항목)** — whole-program/linker-backed dead-symbol reachability,
C++ AST/semantic duplicate analysis. 둘 다 도구 의존적이고 각각 크다.

**C++ safety taxonomy (2항목, I4-4)** — resource/lifetime/security를 clang
analyzer·clang-tidy·clazy category로 매핑. `ICI-GAPS.md` B-3이 가리키는 것과 같은
범위다. `dead`와 `cognitive`는 C++ 지원이 끝났고 `resource`만 Python 전용으로 남았다.

## 파생 rollup

18절의 6개(I4~I9 완료 선언)는 독립 작업이 아니다. 하위 절이 닫히면 따라 닫힌다.
남은 작업량을 셀 때 포함하면 중복 계산이 된다.

## 이 실사가 바꾸지 않는 것

체크박스를 옮긴 것 외에 코드·설정·버전은 건드리지 않았다. "부분 완료"로 분류한
항목은 체크하지 않았다 — 남은 조각을 표에 적어 다음 사람이 크기를 알 수 있게 했다.
"의도적으로 열어둔" 상위 aggregate도 그대로 둔다.
