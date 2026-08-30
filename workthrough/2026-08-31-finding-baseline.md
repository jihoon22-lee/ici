# I1-3 v3 baseline과 finding delta gate

## Overview

ici.result/v3 finding 계약 위에서 이전 분석 결과를 baseline으로 재사용하고, 현재 결과와의
변화를 전체 inventory와 PR gate 대상으로 나누어 보여 주도록 구현했다. 안정적인 finding
fingerprint를 기본 identity로 사용하되 같은 fingerprint의 여러 occurrence를 보존하고,
위치와 severity 변화까지 포함한 delta를 계산한다. baseline 비교는 기존 엔진 결과를
대체하거나 가짜 엔진을 추가하지 않고 suite 판정에 정책적으로만 반영한다.

## Context

기존 verify는 매 실행의 현재 상태만 보여 주므로 새로 생긴 finding과 이미 존재하던 finding,
해결된 finding을 구분할 수 없었다. fingerprint가 같은 finding도 여러 위치에 나타날 수 있어
단순 set 비교는 clone 또는 반복 진단을 잃을 수 있었다. 또한 baseline 파일의 경로와
메타데이터를 신뢰하지 않으면 다른 프로젝트의 결과를 비교하거나 출력 경계를 통해 민감한
문자열을 노출할 위험이 있었다.

I1-1에서 canonical location, suppression, severity, fingerprint를 v3에 정의했으므로
I1-3은 그 계약을 소비하는 계층으로 한정했다. console의 출력량 제한과 공통 grouping 정책은
I1-4의 별도 경계로 남겼다.

## Decisions

### Delta identity와 inventory

- finding은 engine name과 fingerprint를 묶은 occurrence 단위로 비교한다.
- 같은 fingerprint가 여러 번 나오면 multiset으로 유지한다. 위치가 달라진 unmatched
  occurrence는 moved, 현재에만 있는 occurrence는 new, baseline에만 있는 occurrence는
  resolved로 분류한다.
- 현재와 baseline 위치가 같아도 severity 상승 또는 suppression 해제가 있으면 regressed로
  표시한다. 새 finding은 actionable 여부에 따라 gate 대상으로 분리한다.
- 전체 new/unchanged/moved/resolved inventory와 actionable new/regressed gated subset을
  하나의 비교 객체에 함께 보존한다.

### Gate와 호환성

- fail-on-new가 켜졌고 gated occurrence가 하나 이상이면 suite 상태를 FAIL로 승격한다.
  baseline 비교는 결과 목록에 별도 engine을 삽입하지 않는다.
- producer version, fingerprint version, analysis policy digest, 실제 프로젝트 scope와
  active/fallback mode·evidence를 포함한 tool policy digest가
  baseline과 다르면 비교 결과에 warning을 기록한다. warning이 있어도 inventory를 버리지
  않지만 사용자가 동일한 분석 조건인지 판단할 수 있게 한다.
- baseline reader와 writer는 ici.result/v3만 대상으로 한다. baseline comparison 자체는
  suite JSON에서 선택적이며 null과 필드 부재는 구형 보고서 호환 경로로 취급한다.

### 출력과 publish

- console, Markdown, zero-CDN HTML은 issues-first delta 요약과 gated 상세를 제공하고
  JSON은 전체 entries를 보존한다.
- GitHub single/multi-project sticky comment는 baseline이 유효할 때 new, regressed, gated
  수와 gate 상태를 짧게 요약한다. publish loader는 v3 comparison의 필수 필드와 summary
  count/entry 일치를 확인한 뒤에만 이 값을 사용한다.

## Changes Made

### 1. Baseline model, loader, comparison

Files: src/ici/core/models.py, src/ici/core/baseline.py

- BaselineComparison, FindingDelta, DeltaState, AnalysisMetadata를 연결했다.
- v3 baseline JSON을 root-contained 파일로 읽고 schema version, finding fingerprint,
  canonical project-relative location, source region, suppression과 metadata identity를
  검증한다.
- 같은 위치의 finding, 이동한 finding, 새 finding, 해결된 finding을 deterministic order로
  계산하며 duplicate occurrence를 합치지 않는다.
- severity 상승과 suppression 해제를 regression으로 분류하고, info 또는 suppressed
  finding은 actionable gate에서 제외한다.
- baseline 기록은 report와 output 경로 충돌을 막고 고유 임시 파일 뒤 atomic replace로
  완료한다. fail-on-new gate가 실패하면 같은 입력 baseline을 덮어쓰지 않는다.

### 2. Verify orchestration and CLI

Files: src/ici/engines/verify.py, src/ici/__main__.py,
tests/test_verify_orchestrator.py, tests/test_cli.py

- verify가 현재 suite의 analysis metadata를 만든 뒤 baseline comparison을 붙인다.
- fail-on-new 조건에서만 suite FAIL을 승격하며, 기존 engine status와 engine 목록은 보존한다.
- baseline, fail-on-new, write-baseline 옵션을 추가했다.
- fail-on-new 단독 사용, project root 밖 경로, report와 baseline output 충돌, baseline
  오류를 각각 명시적 CLI 오류로 처리한다.

### 3. Reporter와 JSON contract

Files: src/ici/reporters/console.py, src/ici/reporters/markdown.py,
src/ici/reporters/html/report.py, src/ici/reporters/html/sections/baseline.py,
src/ici/reporters/baseline_view.py, src/ici/reporters/json_rep.py,
src/ici/schemas/ici-result-v3.schema.json

- summary count와 gate 상태를 console, Markdown, HTML에 표시하고, HTML은 외부 CDN 없이
  동작한다.
- gated delta를 먼저 보여 주고 unchanged 상세는 제한해 issues-first 흐름을 유지한다.
- JSON writer는 baseline comparison 전체 inventory, warning, metadata, gate와 count를
  canonical v3 object 또는 null로 기록한다.
- writer 경계에서 count, boolean, location, delta state 불변식을 검사해 schema-invalid
  결과를 만들지 않도록 했다.

### 4. GitHub publish compatibility

Files: src/ici/engines/publish.py, src/ici/engines/publish_baseline.py

- saved v2/v3 suite를 읽되 baseline comparison이 없거나 null인 구형 보고서는 그대로
  publish한다.
- 유효한 v3 baseline comparison은 12개 필드 존재, non-empty source/warning, non-negative
  integer count, entries와 summary count의 완전 일치, gate 판정 일치를 확인한다.
- 알 수 없는 extension field는 무시해 미래 producer와의 읽기 호환성을 유지한다.
- malformed summary는 전체 publish를 중단시키지 않고 baseline 요약만 생략해 거짓 count나
  거짓 gate를 댓글에 쓰지 않는다.
- 기존 sticky marker를 유지하며 single/multi-project comment 모두에 같은 요약을 넣는다.
  warning은 redaction 후 한 줄로 정규화하고 HTML/Markdown 특수문자를 escape한다.

## Security and safety

- baseline 자체는 v3 schema version·비교 관련 계약과 현재 project root 안의 canonical
  path만 허용한다. 절대경로,
  parent traversal, Windows separator, canonicalization으로 드러나는 symlink escape는
  거부한다.
- source region은 1-indexed 규칙과 line/column 관계를 검사하고, fingerprint가 rule과
  location으로 재계산한 값과 다르면 baseline을 거부한다.
- baseline 입력 크기와 JSON parse 오류를 제한해 malformed 또는 과대한 입력이 조용히
  비교되지 않게 한다.
- 결과·warning·metadata·publish comment는 공통 redaction 경계를 통과한다. publish
  summary는 타입이 검증된 정수와 고정된 gate label만 출력하고, warning 텍스트는 한 줄
  HTML/Markdown escape 후 표시한다.
- baseline output은 부분 파일을 남기지 않도록 atomic write를 사용한다. report output을
  덮어쓰는 조합은 CLI에서 사전에 차단한다. 실패한 fail-on-new 실행이 입력 baseline과
  같은 출력 경로를 지정해도 원본을 보존하므로 다음 실행에서 regression이 숨지 않는다.

## Verification

### Focused tests

다음 baseline 및 출력 경로를 함께 실행해 delta 분류, gate 승격, CLI 오류, reporter parity,
publish comment 호환성을 확인했다.

    uv run --python 3.10 pytest tests/test_baseline.py tests/test_verify_orchestrator.py tests/test_baseline_reporters.py tests/test_cli.py tests/test_publish.py

    uvx ruff check .
    uvx ruff format --check .
    git diff --check

publish summary 회귀 테스트는 legacy report, null comparison, 필수 필드 누락, 음수·불리언·
실수 count, warning 타입, entries mismatch, gate contradiction, delta flag invariant와
single/multi sticky comment의 escape를 포함한다.

### Final measurement placeholders

로컬 release candidate에서 전체 품질 게이트와 실제 baseline 왕복을 확인했다.

- PR: TODO — GitHub PR 번호와 URL
- CI/Merge Gate: TODO — workflow run ID와 모든 required job 결과
- 전체 테스트: Python 3.10에서 732개 통과, Ruff check/format 통과
- 패키징: pure-Python 10개 배포판, certifi 없음, 재현 가능한 2.0 MiB pyz와 smoke 통과
- self-verify: WARN(Pass 8/Warn 4/Fail 0/Error 0), 732/732, TEM 4.82,
  line/branch/function 87.6%/78.6%/96.5%
- baseline 왕복: 2,783 unchanged, new/moved/resolved/regressed/gated 0,
  fail-on-new PASSED, compatibility warning 0
- 결과 소비: Draft 2020-12 schema 검증, 0 external reference zero-CDN HTML,
  saved-JSON publish summary와 기존 C++ viewer parse 통과

## I1-4 boundary and next steps

I1-3은 baseline inventory와 gate 근거를 구조화했지만, 기본 console의 공통 상위 N 제한,
engine 간 finding grouping, clone occurrence 병합, group-by/verbose UX는 구현 범위에 넣지
않았다. I1-4에서 이 구조화 delta와 기존 finding을 공통 grouping 계층으로 연결하고, JSON과
HTML의 전체 inventory 보존을 console cap과 분리해야 한다. I1-4가 끝나기 전에는 I1 전체
checkpoint를 완료로 표시하지 않는다.
