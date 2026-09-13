# SPEC-04 — 결과와 외부 연동

| | |
|---|---|
|상태|**채택된 목표 계약 (adopted target contract)**|
|원문 이슈|[SPEC-04 #196](https://github.com/jihoon22-lee/ici/issues/196)|
|상위|[roadmap.md](roadmap.md) / [architecture.md](architecture.md) / 입력·실행·분석 [spec-01](spec-01-workspace-config-cli.md) [spec-02](spec-02-distribution-execution.md) [spec-03](spec-03-analysis-engines.md)|
|요구사항|R05, R08~R12, R14~R15|
|주 담당 WP|[#200](https://github.com/jihoon22-lee/ici/issues/200) (스키마), [#221](https://github.com/jihoon22-lee/ici/issues/221)~[#224](https://github.com/jihoon22-lee/ici/issues/224)|

> 신규 envelope는 기존 finding v3와 이름/버전을 구분한다. 실제 스키마 파일은 구현 WP에서 기계
> 검증하며 breaking change는 명시 version 증가로 처리한다.

## 1. RunResult envelope

새 형식 식별자는 `schema_id="ici.next.run"`, `schema_version=1`을 초기 계약으로 한다.
기존 JSON의 version 숫자를 재사용하여 호환된 것처럼 보이게 하지 않는다.

```json
{
  "schema_id": "ici.next.run",
  "schema_version": 1,
  "run_id": "opaque-run-id",
  "producer": {"ici_version": "candidate", "bundle_digest": "sha256:..."},
  "identity": {"source_digest": "sha256:...", "policy_digest": "sha256:...", "toolchain_digest": "sha256:..."},
  "scope": {"kind": "partial", "selected_components": ["tool-a"], "selected_languages": ["python"], "full_required_satisfied": false},
  "execution": {"state": "COMPLETED", "required_complete": true},
  "gate": {"selected": "PASS", "workspace": "NOT_EVALUATED", "has_violations": false},
  "checks": [],
  "findings": [],
  "metrics": [],
  "artifacts": [],
  "limitations": []
}
```

이는 모양을 설명한 축약 예시다. `sha256:...`는 실제 schema에 유효한 digest가 아니며 테스트에서는
완전한 값으로 대체한다. **WP02 PR A가 이것을 강제한다** — `require_digest`가 축약 digest를 거부하고
테스트가 그 사실을 고정한다.

> **발행됨 (WP02 PR B)**: 정식 스키마는
> [`src/ici/schemas/ici-next-run-v1.schema.json`](../../../src/ici/schemas/ici-next-run-v1.schema.json)이고
> 코덱은 [`ici/domain/serialization.py`](../../../src/ici/domain/serialization.py)다.
> 완전한 fixture 5종(성공·코드 FAIL·필수 미완료·부분 선택·취소)이
> [`tests/fixtures/ici-next/`](../../../tests/fixtures/ici-next)에 있으며 **축약 예시를 golden
> 파일로 쓰지 않았다**. 스키마와 코드의 enum·required·additionalProperties 일치는
> [`tests/test_next_schema_contract.py`](../../../tests/test_next_schema_contract.py)가
> `jsonschema` 없이도 기계 검증한다 — 이 저장소에도 CI에도 `jsonschema`가 없어서
> 기존 v3 스키마는 사실상 검증되지 않고 있었다. 실행 시각·duration·request/profile·expected/selected/omitted scope·
source path map·tool evidence·normalization/parser version·policy exceptions·result digest도 정식
스키마에 포함한다.

## 2. 서로 독립적인 상태

|축|상태와 의미|
|---|---|
|Task execution|SUCCEEDED/FAILED/BLOCKED/CANCELLED; tool 실행 완료와 코드 위반은 다름|
|Check execution|COMPLETED/ERROR/BLOCKED/CANCELLED/NOT_SELECTED/NOT_APPLICABLE; 필요 시 NO_TESTS/UNSUPPORTED_VERSION 등 reason code|
|Evidence|MEASURED/ESTIMATED/NOT_RUN/NOT_APPLICABLE; 기존 의미 보존|
|Confidence/mode|exact/high/medium/low 및 tool-backed/heuristic 등 실제 지원 범위|
|Gate|PASS/FAIL/INCOMPLETE/NOT_EVALUATED; selected와 workspace 각각|
|Publication|별도 publish-result의 SUCCESS/FAILED/NOT_CONFIGURED; 원래 분석 verdict를 변경하지 않음|

필수 check가 완료되지 않았거나 요구 scope를 충족하지 못하면 `gate=INCOMPLETE`이다. 동시에 확인된
violation이 있으면 `has_violations=true`와 finding을 함께 보존한다. 미완료가 있다는 이유로 확인한
문제를 버리지 않는다. 완료된 필수 검사에서 위반이면 FAIL. 참고 check의 오류도 보여주되 required
policy에 없는 기능으로 전체 검증을 무조건 막지 않는다.

0개 source/check selection은 config 오류다. 테스트 0개 수집·수집 오류·expected suite 누락은 기본
INCOMPLETE이며 사유 있는 root 정책으로 허용한 empty suite만 별도로 표시한다. 미선택과 적용 불가를
혼동하지 않는다.

> **구현 시작됨 (WP02 PR A)**: 이 6개 축이 [`ici/domain/enums.py`](../../../src/ici/domain/enums.py)에
> 독립 enum으로, 판정 불변식이 [`ici/domain/result.py`](../../../src/ici/domain/result.py)에
> 있다. 강제되는 것: 통과한 scope는 violation을 동시에 보고할 수 없고, FAIL·INCOMPLETE는 이유를
> 반드시 갖고, 미완료 run은 PASS를 낼 수 없고, **INCOMPLETE가 FAIL보다 exit code에서 우선한다**
> (미완료를 완료된 판정으로 보고하지 않기 위해). 게시 상태는 판정에 영향을 주지 않는다.
> **외부 JSON Schema는 아직 없다** — #200 PR B.
>
> **보존할 현행 자산 (확인됨)**: `EvidenceState`의 4개 값(MEASURED/ESTIMATED/NOT_RUN/
> NOT_APPLICABLE)이 이미 존재하고(`src/ici/core/models.py:22`), `aggregate_suite_status`(`:321`)가
> `NOT_APPLICABLE`을 `NOT_RUN`과 구분해 판정에 반영한다. 코드 주석이 그 이유를 남겨 두었다 —
> 적용 대상 언어가 없는 프로젝트를 영구 red로 만들지 않기 위함. Evidence 축은 그대로 이관하고,
> Gate 축(PASS/FAIL/**INCOMPLETE**/NOT_EVALUATED)과 selected/workspace 이원화가 신규다.

## 3. CLI 종료 코드

|코드|verify 의미|
|---|---|
|0|요청 scope의 필수 검사가 완료되고 위반 없음. 부분 실행인 경우 workspace PASS를 뜻하지 않음|
|1|요청 scope 검증은 완료했으나 품질 위반으로 FAIL|
|2|명령/config/schema/선택 오류로 실행 계약을 구성할 수 없음|
|3|필수 tool/input/task/result가 미완료이거나 `--require-full` 범위를 충족하지 못함|
|130|사용자 취소; 가능한 partial result 보존|

불완전 상태와 violation이 동시에 있으면 exit 3과 `has_violations=true`를 함께 낸다.
`--require-full`이 있으면 root 전체 필수 coverage가 맞아야 exit 0/1을 허용한다. `publish`는 자체
성공 0/게시 실패 1/입력 설정 오류 2를 사용하고 verify 결과 파일을 수정하지 않는다. 구 CLI 종료
코드와 차이는 migration 표에 명시한다.

> **현행 충돌 (측정됨)**: `exit_code_for_status`(`src/ici/core/models.py:452`)는 0/1/2만 쓴다.
> PASS·WARN → 0, FAIL·ERROR → 1, **SKIP → 2**. 설정/CLI 오류도 2다. 즉 **현행 2는 "설정 오류"와
> "엔진 SKIP" 두 의미를 겸하고 있고, 목표의 3·130은 존재하지 않는다.** 이것은 사용자에게 보이는
> 계약 변경이므로 migration 표 필수 항목이다.
> 대조표 → [inventory/execution-flow.md §5](inventory/execution-flow.md)
> / 담당 → [WP27 #225](https://github.com/jihoon22-lee/ici/issues/225)

## 4. Finding·지표·baseline

- canonical finding: stable fingerprint, provider id/native rule id, rule version, message,
  severity, confidence/mode, primary/related locations, component/analysis unit/variant 관계,
  suppression, optional fix, originating task.
- 위치·메시지가 같다는 이유만으로 다른 도구 결과를 삭제하지 않는다. 검증된 rule equivalence가
  있는 경우 그룹으로 표시하되 raw evidence 유지.
- `InspectionTarget`/분석 대상·측정 범위를 기존 자산과 연결한다. PASS 항목을 화면에서 접어도 실제
  검사 범위를 잃지 않는다.
- shared source의 line/dup count는 logical file identity 기준으로 중복 제거. 다른 variant의 의미
  있는 finding은 보존.
- coverage는 compatible raw numerator/denominator만 합치고 백분율 단순 평균 금지. TEM은
  `formula_version`과 원자료를 보존.
- baseline은 source/policy/provider/rule/analysis scope identity를 검증한다. 비교 불가능한 설정
  변경을 resolved로 처리하지 않는다.
- 부분 분석에서 실행하지 않은 component의 기존 finding을 해결된 것으로 표시하지 않는다. provider
  전환/fingerprint 변경은 migration map 또는 새 baseline 승인 필요.
- suppression은 이유·출처를 보존한다. 필수 검증 미완료를 suppression/baseline으로 숨기지 않는다.

> 보존할 현행 자산: `InspectionTarget`(`core/models.py:139`)은 AGENTS §5의 "위치 추적 필수"
> 불변식으로 이미 강제되고 있다. `Finding`·`FindingSuppression`·`FindingDelta`·
> `BaselineComparison`·`FindingFix`가 모두 존재한다(`core/models.py:175`~`:298`).
> `core/baseline.py`가 v3 baseline 비교를 구현한다. §4는 이 자산 위에 scope identity 검증과
> "부분 분석에서 미실행 finding을 resolved로 만들지 않는다"를 추가하는 것이다.

## 5. 저장과 reporter

기본 `.ici/runs/<run_id>/result.json`, `events.jsonl`, `report.html`, `artifacts/`, `logs/`
구조를 사용하고 configurable output을 지원한다. result는 schema 검증 후 atomic write한다.
failed/partial run도 진단 가능한 envelope를 남기며 source snippet/log 공개는 opt-in/redaction
정책을 따른다.

HTML 첫 화면: selected/workspace verdict와 incomplete 이유 → 신규 문제 →
component/language/variant → test/coverage/TEM → 미수행 범위 → 상세 tool evidence.
체크 개수로 품질 점수를 부풀리지 않는다. 외부 CDN/fonts/badges/tracking 없음.
source/HTML/XML/Markdown 내용은 데이터로 취급하고 escape한다. file link는 workspace root와
허용 path map에 제한한다.

`report`는 saved result만 읽고 도구/프로젝트 코드를 실행하지 않는다. legacy JSON viewer는 reader
adapter 또는 명시 unsupported schema 진단으로 처리한다. 잘못 읽어 빈 PASS를 표시해서는 안 된다.

> **구현됨 (WP02 PR B)**: [`ici/execution/results.py`](../../../src/ici/execution/results.py)가
> `.ici/runs/<run_id>/`, atomic write, 진단 있는 read를 구현한다. 임시 파일을 **대상
> 디렉터리 안에** 만든다 — `os.replace`는 같은 파일시스템 안에서만 원자적이라
> `/tmp`를 쓰면 조용히 복사로 격하된다. 쓰기가 중간에 죽어도 이전 결과가 그대로 읽히고
> `.partial` 파일이 남지 않는 것을 테스트가 고정한다.
> legacy 결과를 읽으면 무음 빈 PASS가 아니라 **찾은 값을 이름으로 말하는 오류**가 난다.
>
> 현행 자산: `reporters/json_rep.py:833`에 legacy payload 변환 경로(`migrated`)가 이미 있어
> reader adapter의 출발점이 된다. `core/redaction.py`와 `redact_engine_result`가 민감 값을
> 제거한다. Zero-CDN HTML은 AGENTS §5 불변식으로 이미 강제된다. 현행 출력 경로는
> `.ici/runs/<run_id>/`가 아니라 `verify_report.json`/`verify_report.html`(cwd 기준)이다.

## 6. 이벤트/idk

이벤트는 `schema_id=ici.next.event`, `schema_version=1`, `run_id`, 단조증가 `seq`, timestamp,
`event_type`, optional task/component ids, bounded payload를 갖는다.
유형: `run.started`, `plan.ready`, `task.started`, `task.progress`, `task.completed`,
`diagnostic`, `run.completed`. 최종 result가 authoritative이고 events의 일부 누락이 결과를
덮어쓰지 않는다.

일반 stdout/stderr 로그와 JSONL을 섞지 않는다. explicit event file 또는 전용 stream을 사용한다.
consumer는 unknown optional event를 무시할 수 있지만 지원하지 않는 major schema는 알린다.
truncated event 마지막 줄·중복 seq·취소를 검증한다.

idk는 준비된 환경에서 ici를 실행하고 로그/이벤트/종료/result를 수신한다. ici는 idk에 의존하지
않는다. 취소는 ici가 자식 task까지 정리한다. 소스 열기는 결과의 논리 위치와 idk workspace
mapping을 이용한다. 실제 idk 코드 수정은 idk 저장소의 별도 이슈/승인으로 연결한다. 여기서는 ici
producer와 fixture consumer 계약을 완료한다.

> **구현 시작됨 (WP02 PR B)**: 스키마는
> [`ici-next-event-v1.schema.json`](../../../src/ici/schemas/ici-next-event-v1.schema.json),
> 모델은 [`ici/domain/events.py`](../../../src/ici/domain/events.py), 코덱은
> [`eventstream.py`](../../../src/ici/domain/eventstream.py)다.
> `RunEvent`는 **gate도 finding도 갖지 않는다** — 최종 result가 authoritative이므로,
> 스트림에서 통과/실패를 판단하려는 소비자는 result를 읽으러 가야 한다.
> 검증되는 것: 잘린 마지막 줄은 그 앞 이벤트를 살린 채 skip으로 보고, unknown event_type은
> skip하되 **지원하지 않는 major schema는 거부**, seq 간격·중복은 보고하되 **고치지 않는다**.
> `--events PATH` CLI 옵션과 실제 방출은 여전히 신규다.
> → [WP26 #224](https://github.com/jihoon22-lee/ici/issues/224)

## 7. GHES publisher

- analyze job과 credential-bearing publish job을 분리한다. PR 소스를 실행한 job에 게시용 권한을
  불필요하게 주지 않는다.
- 신뢰된 base 코드가 result/artifact schema·크기·경로·repository·PR 번호·head SHA·run/attempt·
  artifact provenance를 검증한 후 게시한다.
- API/server URL은 설정/실행 환경에서 받고 시스템 CA를 사용한다. 외부 GitHub.com/Pages/CDN을
  기본 가정하지 않는다.
- backend는 GHES Pages·승인된 사내 웹 저장소·artifact 링크 중 명시 선택. 다운로드 artifact와
  바로 열리는 HTML 링크를 동일 기능처럼 표시하지 않는다.
- sticky marker는 repo+PR+workspace/context 기준으로 안정화한다. rerun은 갱신하고 중복 댓글을
  만들지 않는다. 늦은 이전 run이 현재 head의 댓글을 덮어쓰지 않도록 최신 head와 run ordering을
  확인한다.
- analysis FAIL/INCOMPLETE도 결과를 게시 가능해야 한다. 게시 실패는 별도 status이며 분석 재실행
  없이 재시도 가능하다.
- GHES/runner/action 호환은 실제 사내 버전으로 검증한다. 공개 github.com CI의 artifact action
  major를 그대로 복사하지 않는다.

> 현행 자산: `engines/publish.py` `ReportPublisher`가 gh-pages + sticky PR comment를 구현하고
> `ici publish`가 이미 "분석 재실행 없이 저장된 결과만" 읽는다(§7 마지막 항목과 일치).
> `__main__.py:491`이 게시 실패를 exit 1로 만들어 분석 verdict와 분리한다.
> **GHES 환경에서의 실제 동작은 검증하지 못했다.**
> → [WP25 #223](https://github.com/jihoon22-lee/ici/issues/223)

## 8. 계약 테스트와 완료

- [ ] schema roundtrip·지원하지 않는 버전·필수 필드 누락·비정상 큰 입력·안전한 legacy 변환을
      검증한다.
- [ ] PASS/FAIL/INCOMPLETE×partial/full×required/advisory×0개 테스트의 truth table 테스트가 있다.
- [ ] 부분 baseline에서 미실행 finding이 resolved가 되지 않는다.
- [ ] HTML과 JSON·console의 verdict/수행 범위가 일치하고 offline/XSS/path-link 테스트가 있다.
- [ ] publish duplicate/stale head/out-of-order attempt/권한 실패/재시도가 분석 결과를 바꾸지
      않는다.
- [ ] idk fixture consumer가 old/new/partial/cancelled 이벤트와 result를 처리한다.

공식 참고: [GitHub artifact action의 GHES 제약](https://github.com/actions/upload-artifact),
[GitHub Actions 보안](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions).
도입 버전별 실제 기능은 호환성 시험에서 확정한다.
