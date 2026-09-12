# ADR-0004 — 검사 관점(check)과 분석 제공자(provider)를 분리한다

- 상태: **accepted**
- 결정 시점: WP00 ([#198](https://github.com/jihoon22-lee/ici/issues/198))
- 근거 이슈: [SPEC-03 #195 §1](https://github.com/jihoon22-lee/ici/issues/195),
  [ARCH #192 §7](https://github.com/jihoon22-lee/ici/issues/192)
- 관련 요구사항: R08, R09
- 담당 WP: [#208](https://github.com/jihoon22-lee/ici/issues/208),
  [#218](https://github.com/jihoon22-lee/ici/issues/218)

## 결정

현행의 단일 **엔진(engine)** 개념을 세 층으로 분리한다.

| 층 | 정의 | 예 |
|---|---|---|
|**Check**|검사 목적·언어·입력·정책의 선언|lint, security, complexity, coverage|
|**Provider**|실제 구현|Ruff, mypy, ty, compiler, clang-tidy, clazy, ici 자체 분석|
|**Task**|고정 입력·환경·인자로 provider를 한 번 수행하는 단위|`ruff check --output-format=json <files>`|

Provider 계약: `supports(unit, capabilities)`, `requirements(unit)`,
`plan(resolved_inputs) -> TaskSpec[]`, `collect(task_outputs) -> Observation[]`.

핵심 규칙:

- 한 provider observation은 **여러 check에 사용 가능**하다. 관점이 다르다는 이유로 동일 실행을
  반복하지 않는다.
- 동일 finding은 **canonical id 하나**를 갖고 category/tag로 다중 표시한다. 관점별 표시를 위해
  복제하지 않는다.
- 같은 위치라도 **서로 다른 rule/provider 결과를 근거 없이 삭제하지 않는다.**
- raw observation에 native rule id, provider/parser 버전, source location/scope,
  evidence/confidence, 원본 메시지, normalization 근거를 보존한다.

## 대안

| 대안 | 기각 사유 |
|---|---|
|현행 엔진 개념 유지|"검사 관점"과 "구현"이 한 이름에 묶여 있어, 관점을 추가하려면 구현을 복제하거나 같은 도구를 다시 실행해야 한다. R08이 금지하는 구조다|
|Check만 두고 provider를 감춘다|finding의 출처(native rule id, provider 버전)가 사라진다. SPEC-03 §1과 SPEC-04 §4가 출처 보존을 요구한다|
|Provider만 두고 check를 없앤다|`required` 정책·언어별 지원 선언·게이트 판정의 주체가 없어진다. "Ruff가 통과했다"는 "lint 검사가 완료됐다"와 다르다|
|Task를 두지 않고 provider가 직접 프로세스를 띄운다|**현행 구조 그 자체다.** 실행 공유 키가 없어 중복 실행을 막을 수 없고, 취소·timeout·lock을 provider마다 재구현해야 한다|

## 근거

1. **현행 DAG 노드가 프로세스 단위가 아니다.** `EngineDescriptor`는 엔진 하나를 노드로 갖고
   (`src/ici/core/pipeline.py:40`), 실제 프로세스 실행은 엔진 내부에서 일어난다. 그래서
   **`exec=read-only`로 선언된 엔진 중 8개가 실제로 외부 프로세스를 실행한다**
   (`cognitive`, `complexity`, `cycle`, `dead`, `lint`, `python_compat`, `type`, `binary_compat`).
   read-only는 "산출물을 변경하지 않는다"는 뜻이고 "프로세스를 띄우지 않는다"가 아니다.
   → [inventory/current-engines.md §4](../inventory/current-engines.md)
2. **중복 실행을 막을 수단이 없다.** 현행 `produces`/`consumes`는 finding artifact 단위이고
   (`findings:lint` 등), 도구 실행 단위의 공유 키가 없다. 같은 Ruff 실행을 여러 관점이 필요로
   해도 공유할 방법이 registry에 표현되지 않는다.
3. **관점과 구현의 비대칭이 이미 데이터에 있다.** 측정된 언어×mode 표를 보면:
   - `dead`는 C++에서 `tool-backed`/`exact`인데 Python에서 `heuristic`/`medium`이다.
     **한 이름이 두 신뢰도를 갖는다.** check는 하나여도 provider가 둘이어야 한다는 증거다.
   - `cycle`도 Python `heuristic` / C++ `tool-backed`로 갈린다.
   - `cognitive`와 `complexity`가 `cpp_boundaries` 설정 키를 공유한다. 두 check가 같은
     parse 결과를 쓸 수 있다는 뜻이다.
   → [inventory/current-engines.md §2·§3](../inventory/current-engines.md)
4. **heuristic 자체 규칙이 Python 쪽에 8개 몰려 있다** (cognitive, resource, security, cycle,
   complexity, dead, dup, exception). 이들을 외부 provider와 비교하려면 "같은 check를 다른
   provider로 수행"하는 표현이 필요하다. R09가 요구하는 근거 있는 disposition의 전제 조건이다.

## 호환 영향

| 대상 | 영향 |
|---|---|
|`engines.<name>.enabled`/`mode` 설정 키|check 단위로 이어질지 provider 단위로 갈라질지 [#203](https://github.com/jihoon22-lee/ici/issues/203)·[#218](https://github.com/jihoon22-lee/ici/issues/218)이 결정. **migration 표 필수 항목**|
|엔진 단독 CLI 15개 (`ici lint` 등)|check 이름과 1:1이 아닐 수 있다. alias/deprecation을 [#225](https://github.com/jihoon22-lee/ici/issues/225)가 결정|
|`findings:<name>` artifact 이름|`Observation` → canonical `Finding` 경로로 바뀐다|
|finding fingerprint|provider/rule id가 finding에 포함되면 fingerprint가 바뀔 수 있다. SPEC-04 §4가 migration map 또는 새 baseline 승인을 요구한다|

## 복구

이 결정은 구조 변경이므로 단일 revert 대상이 아니다. 복구 경로는:

- 이관을 **작은 vertical slice로** 진행한다([#206](https://github.com/jihoon22-lee/ici/issues/206)이
  line/Ruff 하나로 전체 경로를 먼저 증명한다).
- 기존 엔진 registry를 **먼저 지우지 않는다.** 새 구조가 differential 비교
  ([SPEC-05 §4](../spec-05-verification-transition.md))를 통과한 뒤 별도 PR로 제거한다.
- 19개 descriptor 각 행이 최종 disposition·WP·PR·검증 근거를 갖기 전에는 제거하지 않는다.

## 보류 항목

| # | 항목 | 결정 조건 | 담당 |
|---|---|---|---|
|1|check 이름 목록의 최종 확정|19개 disposition 완료 후|[#218](https://github.com/jihoon22-lee/ici/issues/218)|
|2|`fallback_mode=heuristic` 6개의 처리 — provider 대체인지 별도 mode인지|각 fallback의 실제 활성 조건 확인 후|[#219](https://github.com/jihoon22-lee/ici/issues/219)|
|3|`cognitive`/`complexity` 통합 여부|지표 정의 중복 범위 측정 후|[#218](https://github.com/jihoon22-lee/ici/issues/218)|
|4|`security`/`exception`/`resource`의 Ruff 대체 가능 범위|정상 대조군 + 결함 corpus 비교 후|[#215](https://github.com/jihoon22-lee/ici/issues/215), [#218](https://github.com/jihoon22-lee/ici/issues/218)|
|6|type provider 기본값 (mypy vs ty)|두 provider 비교|[#215](https://github.com/jihoon22-lee/ici/issues/215)|
