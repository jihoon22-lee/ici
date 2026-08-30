# ici.result/v3 finding 계약과 출력 경계 보호

## Overview

`ici.result/v3`를 도입해 도구별 legacy target을 안정적인 품질 finding으로 표현한다. v3
writer는 기존 `targets`를 보존하면서 canonical location, ici rule id, category, severity,
confidence, fingerprint, related location, remediation, suppression과 단위 metric을 가진
`findings`를 함께 출력한다. v2 archive migration과 v2/v3 viewer 호환을 제공하고, 모든
리포터 앞의 공통 redaction 경계가 credential 유출을 막는다.

## Context

`InspectionTarget`은 파일과 행을 보존하지만 rule identity, 신뢰도, 관련 위치, 개선안과
stable fingerprint가 없다. 따라서 엔진 간 중복을 설명하거나 baseline/delta를 계산하기
어렵고, reporter마다 자유 형식 문자열을 다르게 다루면 tool output이나 snippet의 credential이
JSON·HTML·Markdown·console 중 한 곳으로 새어 나갈 수 있었다. 동시에 이미 보관된 v2 report와
현재 viewer를 한 번에 폐기할 수 없으므로 writer와 consumer를 독립적으로 전환할 계약이
필요했다.

## Changes Made

### 1. Canonical finding model and schema

- `src/ici/core/models.py`에 `Finding`, `SourceLocation`, `FindingMetric`,
  `FindingSuppression`과 안정 enum을 추가했다. `InspectionTarget`은 1-indexed start/end column을
  선택적으로 보존한다.
- `src/ici/schemas/ici-result-v3.schema.json`은 suite와 standalone engine 결과 양쪽을
  JSON Schema Draft 2020-12로 정의한다. 이 파일은 pyz package data에도 포함된다.
- v3 engine은 `targets`와 `findings`를 모두 요구한다. 기존 소비자는 `findings`를 무시하고
  v2 모양을 계속 사용할 수 있다.

### 2. Path and fingerprint invariants

- `src/ici/core/findings.py`가 Windows/POSIX separator를 `/`로 통일하고 checkout root를
  제거한다. 절대경로는 명시한 project root 안에 있어야 하며 `..` escape는 거부한다.
- fingerprint는 ici rule id, canonical path, symbol 또는 exact region을 canonical JSON으로
  만든 뒤 SHA-256으로 계산한다. 유일한 legacy symbol은 줄 이동에도 같은 identity를 유지한다.
- legacy의 unqualified symbol이 한 파일에서 반복되면 overload·pytest parameter·clone
  occurrence를 잘못 합치지 않도록 region identity를 사용한다. 실제 self report에서 기존
  `2,243 targets`가 정확히 `2,243 findings`로 보존되는 것을 확인했다.
- native v3 producer가 동일 fingerprint를 발행할 때만 adapter를 대체하며, native 위치도
  같은 canonicalization과 fingerprint 계산을 다시 거친다.

### 3. Migration and compatibility

- `src/ici/reporters/json_rep.py` writer를 v3로 전환하고 unknown extension을 보존하는
  `migrate_report_payload()`를 추가했다. v2 target과 tool evidence는 손실 없이 v3 copy로
  이동한다.
- `viewer`는 현재 writer schema인 v3와 archive schema인 v2를 모두 허용한다. UI는 전환
  기간 동안 보존된 `targets`를 표시하며, v1과 잘못된 field type은 계속 명시적으로 거부한다.

### 4. One redaction boundary for every output

- `src/ici/core/redaction.py`는 assignment/flag/Bearer/GitHub·OpenAI·AWS token과 완전하거나
  잘린 private-key block을 마스킹한다.
- summary, message, snippet, raw output, recursive extra key/value, tool identity/path/version,
  argv/error, finding explanation/remediation/tool metadata와 suppression reason을 모두 copy-on-write
  방식으로 처리한다. source location path는 탐색 정확성을 위해 유지한다.
- verify/단독 engine/build 반환값과 console, JSON, HTML, Markdown, Actions annotation,
  publish comment가 같은 안전한 suite/result를 사용한다.

## Code Examples

```json
{
  "schema_version": "ici.result/v3",
  "targets": [{"file_path": "src/a.py", "start_line": 8}],
  "findings": [{
    "rule_id": "ici.legacy.security.target",
    "category": "security",
    "severity": "high",
    "confidence": "high",
    "fingerprint": "sha256:<64 lowercase hex>",
    "primary_location": {"path": "src/a.py", "start_line": 8},
    "related_locations": [],
    "suppression": {"suppressed": false, "kind": "none", "reason": ""}
  }]
}
```

```text
C:\checkout-a\src\a.cpp + project root C:\checkout-a
/tmp/checkout-b/src/a.cpp + project root /tmp/checkout-b
                         -> canonical path src/a.cpp -> same fingerprint
```

## Verification Results

### Python and packaging gates

```text
uv run --python 3.10 pytest                    647 passed
uv run --python 3.10 mypy src/ici              success; pre-I0-4 notes only
uvx ruff check .                               passed
uvx ruff format --check .                      passed
./scripts/build-pyz.sh                         passed; Python 3.10, pure Python
./scripts/smoke.sh                             passed; reproducible and Zero-CDN
```

The built `dist/ici.pyz` contains
`site-packages/ici/schemas/ici-result-v3.schema.json`.

### Real v3 dogfood

```text
dist/ici.pyz verify --report --html /tmp/ici-v3-self.html
suite WARN; 7 PASS / 5 WARN; TEM 4.79
targets 2,243; findings 2,243; every nested engine schema ici.result/v3
console 2,426 lines; HTML contains no external CDN URL
```

### Native viewer

```text
CMake build + CTest                            4/4 passed
icirv verify_report.json                       parsed real v3 report
QT_QPA_PLATFORM=offscreen icirv-gui ...        opened until controlled timeout
v2 fixture / v3 fixture / invalid v1           accepted / accepted / rejected
```

Redaction tests pass the same secrets through JSON, Markdown, HTML, Rich console and the returned
suite. They cover quoted values with spaces, short Bearer values, argv neighbors, secrets in
recursive metadata keys, known token prefixes, tool metadata, remediation/suppression text, and
complete or truncated private-key blocks.

## Next Steps

- I1-2 should declare engine/language/tool capability and confidence modes instead of inferring
  them from engine names.
- I1-3 can now use v3 fingerprints for baseline/delta, but must define multiset handling for exact
  duplicate legacy regions until every engine emits native semantic findings.
- I1-4 should consume native/adapter findings for issues-first grouping while keeping the full
  JSON inventory uncapped.
