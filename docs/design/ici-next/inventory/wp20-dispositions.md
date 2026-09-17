# WP20 ruleset 이관 disposition 확정

- 상태: **PR A–C 구현 완료, 본 표는 확정 disposition이다.** 잠정 표는
  [current-engines.md §7](current-engines.md#7-잠정-disposition)에 남아 있다.
- 근거 이슈: [WP20 #218](https://github.com/jihoon22-lee/ici/issues/218)
- 대상: `line`, `complexity`, `cognitive`, `dup`, `cycle`, `resource`,
  `security`, `exception`, `dead`의 자체 구현과 대체 경로.

## 판정 원칙

1. stable 엔진과 next check이 **같은 분석 함수**를 호출하면 두 구현의
   정확도 차이는 존재할 수 없다 — 대조군 비교는 "함수 공유 여부"로 확정한다.
2. heuristic 결과는 `ESTIMATED`/낮은 confidence로 표시하며 exact로 승격하지
   않는다 (#218 항목 5).
3. 외부 도구(Ruff 등)와 겹치는 규칙도 ici 자체 규칙이 다른 근거(메시지·
   remediation·위치 정밀도)를 제공하면 유지한다 — 무단 삭제 금지 (인수 기준 5).

## 확정 표

|엔진|next check|소유 provider|확정 disposition|공유 구현|근거|
|---|---|---|---|---|---|
|line|`python.line`,`cpp.line`|`ici.line`|**유지 (이관 완료)**|`languages/` line 카운터|양 언어 exact, 도구 무의존 — 위험 없음|
|complexity|`*.complexity`|`ici` 내부|**유지 (이관 완료)**|`engines/_python_metrics.py` — stable 엔진이 위임 호출|수식 동일성이 구조적으로 보장됨|
|cognitive|`*.cognitive`|`ici` 내부|**유지, complexity와 parse 공유**|같은 `MetricRequest.cache`가 한 스캔을 공급|잠정 안의 "통합 후보"는 *구현* 통합으로 해소 — check·수식은 분리 유지|
|dup|`*.dup`|`ici` 내부|**유지 (이관 완료)**|stable 엔진의 tokenizer·매처·클러스터링|Type-2 의미 보존. Python AST-shape clustering은 미이관 — limitation으로 보고|
|cycle|`*.cycle`|`ici` 내부|**유지 (이관 완료)**|Tarjan SCC, import/include 그래프|cpp include 해석은 heuristic → ESTIMATED|
|security|`python.security`|`ici` 내부|**유지 (이관 완료)**|`analyze_python_security` 공유|Ruff `S` 규칙과 부분 겹침 — pickle/eval/secret-name 규칙은 ici가 remediation과 allowlist(`secret_name_allowlist`)를 소유하므로 유지|
|resource|`python.resource`|`ici` 내부|**유지 (이관 완료)**|`analyze_python_resources` 공유|C++ `unsupported` 원래대로. mutable-default·acquisition 흐름은 대체 도구 없음|
|exception|`python.exception`,`cpp.exception`|`ici` 내부|**유지 (이관 완료)**|`engines/_exception_rules.py` — stable 엔진이 위임 호출|scope-alias 해석·LostTraceback·소멸자 throw는 자체 규칙. Ruff E722/BLE001과 부분 겹치나 범위가 다름|
|dead|`python.dead`|`ici` 내부|**분할 이관**|`analyze_python_dead_code` 공유|Python cross-file 휴리스틱만 이관(ESTIMATED). C++ 미사용-함수 replay·linker GC는 사용자 컴파일러/링커를 실행하므로 도구 제공자 경로(#220)로 — stable 엔진이 그 역할 유지|

## 대조군 기록 (인수 기준 3)

|check|결함 fixture|정상 대조군|검증 위치|
|---|---|---|---|
|`*.complexity`/`*.cognitive`|중첩 hot 함수|calm 함수(지표만, finding 없음)|`tests/test_cli_next_metrics.py`|
|`*.cycle`|import/include 루프|비순환 그래프|같은 파일|
|`*.dup`|Type-2 클론 블록|고유 파일 쌍|같은 파일|
|`python.security`|`pickle.loads`+`eval`|정상 호출|`tests/test_cli_next_hygiene.py`|
|`python.resource`|`open()` without `with`|정상 함수|같은 파일|
|`python.exception`|베어 `except:`+`pass` 핸들러|정상 함수|같은 파일|
|`cpp.exception`|소멸자 `throw`|—|같은 파일|
|`python.dead`|미참조 private 함수|참조된 함수|같은 파일|

## 명시적 미이관 (인수 기준 5 — 조용한 삭제 없음)

|기능|이유|대체 수단|
|---|---|---|
|C++ dead (unused-function replay, linker GC)|사용자 컴파일러·링커를 실행 — in-process check의 계약 밖|stable `dead` 엔진 + #220 도구 제공자 경로|
|Python dup AST-shape semantic clustering|의미 보존 이관이 아직 안 됨|check이 limitation으로 명시 보고|
|`security`의 `scan_tests`/`secret_name_allowlist` 설정|next check 설정 스키마는 WP27에서 통합|stable 엔진 설정이 그대로 유효|

## 사용자 영향

- **제거된 기능 없음.** 모든 규칙은 stable 엔진 또는 next check으로 계속 실행된다.
- next check의 finding은 `native_rule_id`에 원래 규칙 이름(`Security:PickleLoad`,
  `BareExcept`, `Resource:OpenWithoutWith` 등)을 보존한다 — 기존 보고서와의
  대응이 끊기지 않는다.
- next에서 새로 생긴 check(`python.security` 등)를 끄려면 `ici.toml`에
  `[checks."<id>"] enabled = false`를 둔다.
