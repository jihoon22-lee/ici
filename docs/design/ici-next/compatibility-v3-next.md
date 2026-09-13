# `ici.result/v3` ↔ `ici.next.run` 호환성 매트릭스

| | |
|---|---|
|상태|**측정 완료**. 아래 표는 [`src/ici/execution/legacy_reader.py`](../../../src/ici/execution/legacy_reader.py)가 실제로 하는 일이며, [`tests/test_legacy_reader.py`](../../../tests/test_legacy_reader.py)가 둘을 맞물려 둔다.|
|근거 이슈|[WP02 #200](https://github.com/jihoon22-lee/ici/issues/200) PR C|
|관련 규범|[spec-04 §5](spec-04-results-integration.md), [spec-05 §5](spec-05-verification-transition.md)|

이 문서는 **두 방향**을 다룬다. 둘은 대칭이 아니다.

1. **v3 → next**: 기존 리포트를 새 모델로 읽는다. 지어내지 않는 것이 원칙이다.
2. **next → v3 리더**: 새 결과가 기존 리더에 들어갔을 때. **조용한 빈 PASS가 되면 안 된다**는
   #200 인수 기준이 여기에 걸린다.

## 1. 구조적 제약: v3는 자기 신원을 완성할 수 없다

`RunIdentity`는 `SourceSnapshot`을 요구하고, 그 `digest`는 **분석이 실제로 읽은 내용**을 덮는다.
v3에는 그런 다이제스트가 없다. 있는 것은 config digest, toolchain digest, commit id 뿐이고,
[spec-02 §6](spec-02-distribution-execution.md)은 commit id가 dirty 파일·생성 입력·외부 헤더를
설명하지 못한다고 명시한다.

그래서 리더는 `RunResult`를 바로 만들지 않는다. `LegacyReport`를 돌려주고, **트리를 가진
호출자가** snapshot을 공급해 `promote()`로 완성한다. PR A의 `finding_from_legacy(provider=...)`가
provider를 호출자에게 요구하는 것과 같은 형태다 — 아는 쪽이 말하고, 변환은 발명하지 않는다.

가지고 있는 digest 중 아무거나 `SourceSnapshot.digest`에 넣으면 무관한 두 실행이 비교
가능해 보이고, baseline 비교가 "변화 없음"을 보고한다. 그게 이 설계의 이유 전부다.

## 2. v3 → next 필드 매핑

|next 필드|v3 출처|판정|
|---|---|---|
|`Producer.ici_version`|`analysis_metadata.producer_version`|**없으면 거부**. 어느 빌드가 냈는지 모르는 결과다|
|`Producer.bundle_digest`|없음|`None`. v3에 bundle 개념이 없다|
|`RunIdentity.policy_digest`|`analysis_metadata.policy_digest` → `analysis_context.identity.config_digest`|둘 다 없으면 **거부**|
|`RunIdentity.toolchain_digest`|`analysis_context.identity.toolchain_digest` → `analysis_metadata.tool_policy_digest`|둘 다 없으면 **거부**|
|`SourceSnapshot.digest`|**없음**|**호출자가 공급**(§1)|
|`SourceSnapshot.commit`|`analysis_context.identity.source_commit`|`"unavailable"`이면 `None` + limitation. 실측 snapshot의 commit이 우선|
|`SourceSnapshot.files`/`generated`/`external_inputs`|없음|비움|
|`ScopeSelection`|없음|`STANDALONE`, `full_required_satisfied=False`. **`FULL`이 아니다** — 모델이 `FULL`에 `full_required_satisfied=True`를 강제하는데 v3에는 component가 없어 "required를 다 덮었다"고 주장할 근거가 없다 (R05)|
|`ExecutionSummary.required_complete`|엔진 중 `status == "ERROR"`가 있는지|task id가 없어 `blocked_task_ids`/`failed_task_ids`는 비움|
|`GateOutcome.selected`|`suite_status`|§3 표|
|`GateOutcome.workspace`|없음|`NOT_EVALUATED`|
|`findings[]`|engine별 `findings[]`|**`provider = engine_name`**. v3의 `tool_name`은 실행 파일이라 여러 엔진이 공유한다 — 그걸로 귀속하면 서로 다른 provider가 하나로 합쳐진다|
|`finding.native_rule_id` / `rule_version`|`tool_rule_id` / `tool_version`|손실 없음|
|`finding.suppression.origin`|없음|`"legacy-v3"` 고정|
|`finding.component_id` / `analysis_unit_id` / `variant` / `task_id` / `tags`|없음|비움 + limitation|
|`metrics[]`|`tem_score`만|엔진별 `score`는 **버린다**. v3가 unit도 denominator도 주지 않아 `Measurement`로 올리면 없던 정밀도가 붙는다|
|`publication`|없음|`NOT_CONFIGURED`|

## 3. `suite_status` → `GateVerdict`

|v3|next|비고|
|---|---|---|
|`PASS`|`PASS`||
|`WARN`|`PASS`, `has_violations=False`|**유일한 의미 손실**. 모델이 "통과한 scope는 violation을 보고할 수 없다"를 강제하고, v3의 WARN은 정확히 "finding은 있지만 게이트를 깨지는 않는다"다. finding 자체는 `findings[]`에 그대로 남고, 잃는 것은 suite 수준의 3값 구분이다. limitation에 기록된다|
|`FAIL`|`FAIL`, `has_violations=True`||
|`ERROR`|`INCOMPLETE`|**`FAIL`이 아니다**. 돌지 못한 엔진은 코드에 대한 판정이 아니다. exit code도 1이 아니라 3|
|`SKIP`|`NOT_EVALUATED`||
|그 외|**거부**|모르는 상태에 판정을 붙이지 않는다|

## 4. 항상 기록되는 limitation

v3의 구조적 공백이라 특정 리포트의 성질이 아니다. 넷 다 모든 변환에 붙는다.

1. source snapshot 없음 (§1)
2. scope 없음 — STANDALONE이며 workspace 판정을 대신하지 않는다
3. task 신원 없음 — 막힌/실패한 작업을 task 단위로 지목할 수 없다
4. finding에 component·analysis unit 귀속 없음

리포트별로 추가되는 것: `source_commit`이 `unavailable`일 때, `fingerprint_version`이 없을 때
(= 이 fingerprint들이 다른 리포트와 비교 가능한지 알 수 없다), 엔진이 `findings` 목록 자체를
싣지 않았을 때.

## 5. next → 기존 v3 리더 (인수 기준)

> 신규 결과를 legacy reader가 무음 빈 PASS로 해석하지 않는다. — #200

`ici.next.run` 문서를 기존 리더 셋에 실제로 먹여 확인했다.

|리더|결과|
|---|---|
|[`core/baseline.py:356`](../../../src/ici/core/baseline.py)|`BaselineError: unsupported baseline schema_version 1; expected 'ici.result/v3'` — 이유를 말하고 거부|
|[`reporters/json_rep.py:804`](../../../src/ici/reporters/json_rep.py) `migrate_report_payload`|`ValueError: unsupported schema_version: 1` — 거부|
|[`core/cache_codec.py:399`](../../../src/ici/core/cache_codec.py)|`CacheEntryError` — 거부|
|[`engines/publish.py`](../../../src/ici/engines/publish.py) `load_suite_from_json`|**결함이 있었다. PR C에서 고쳤다** ↓|

### `load_suite_from_json`에 있던 조용한 강등

이 함수는 PR 스티키 코멘트를 만드는 요약을 공급한다. `schema_version`을 **전혀 검사하지
않았고**(docstring은 "v2/v3 report file"이라고 주장했다), 파싱 안 되는 엔진을 `continue`로
하나씩 삼켰으며, 읽을 수 없는 `suite_status`를 조용히 `WARN`으로 떨어뜨렸다.

이 빌드보다 새로운 producer가 낸 **진짜 FAIL 리포트**를 먹인 실측 결과:

```
입력:  schema_version "ici.result/v4", suite_status "BLOCKED",
       엔진 2개가 "12 violations" / "3 failed" 보고
출력:  suite_status=WARN  engines=0
```

FAIL이 WARN이 되고 엔진이 전부 사라진다. `ici.next`와 무관하게 **v4 producer 하나면
재현되는, 지금 살아 있던 경로**였다.

PR C는 셋 다 거부로 바꿨다 — 읽을 수 없는 `schema_version`, 파싱 안 되는 `suite_status`,
파싱 안 되는 엔진 항목. `None`은 호출자가 이미 다루는 상태이고(리포트 파일이 없을 때가
그것이다) 코멘트에 "판정 없음"으로 렌더된다. 틀린 판정보다 낫다.

읽을 수 있는 형식은 `ici.result/v2`, `ici.result/v3` 둘로 명시됐다.

## 6. 이 표를 갱신하는 규칙

1. `legacy_reader.py`의 동작이 바뀌면 이 표를 같이 고친다. 테스트가 둘을 맞물려 둔다.
2. 새 거부 지점이 생기면 §2·§3의 "거부" 칸에 적는다. 거부는 기능이지 결함이 아니다.
3. next 쪽에 필드가 늘어도 v3 출처 칸은 "없음"으로 남긴다. 비워 두지 않는다.
