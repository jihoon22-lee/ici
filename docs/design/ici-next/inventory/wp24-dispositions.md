# WP24 결과 중심 reporting disposition

- 상태: **PR A(view model 분리)·PR B(필터/접근성)·PR C(report CLI·viewer 경계)
  구현 완료.**
- 근거 이슈: [WP24 #222](https://github.com/jihoon22-lee/ici/issues/222)
- 대상: `ici.reporting` 계층 — `view_model.py`, `offline_html.py`,
  `sarif.py` — 와 `next report`/`next diff` CLI.

## 판정 원칙

1. **render는 저장된 결과만 안다.** view model이 `RunResult`를 한 번
   투영하고 renderer는 그 데이터만 소비한다. `next report`는 JSON을 읽을
   뿐 프로세스를 시작하지 않는다 — `test_report_starts_no_process`가
   `run_process`를 monkeypatch로 거부해 이를 고정한다.
2. **불완전은 첫 화면의 사실이다.** 취소·blocked·failed·required 미완료를
   verdict 옆과 Execution 섹션에 이름으로 나열한다. 초록 개수로 가리지
   않는다.
3. **canonical count는 result의 finding 수 하나다.** view가 여러
   category/표시를 만들어도 `Findings (N)`은 `len(result.findings)`이고
   필터는 행을 숨길 뿐 삭제하지 않는다.
4. **모든 기록 문자열은 데이터다.** `_t()`(`html.escape(quote=True)`)가
   text·attribute 모두를 통과시키며, 악성 message/path가 markup을 만들지
   못함이 테스트됐다. 링크 mapping은 생성하지 않는다 — 어떤 finding
   문자열도 `<a>`가 되지 않으므로 "안전한 링크"의 정책은 "링크 없음"이다.
5. **민감 경로 정책.** 표시되는 경로는 `SourceSpan.path` — 모델이
   workspace 상대 경로만 허용하므로 절대 경로·환경 경로가 화면에 나올
   통로가 없다. 원문 로그·snippet은 저장 모델에 없으므로 렌더링 대상이
   아니다.
6. **모르는 schema는 빈 PASS가 아니다.** `loads`→`check_envelope`가
   `ici.next.run` 외 문서를 `UnsupportedSchemaError`로 거부하고,
   v3 리더(`execution/legacy_reader.py`)도 next 문서를 거부한다 —
   양방향 모두 기존 테스트가 고정한다.

## 명시적 결정

|주제|결정|근거|
|---|---|---|
|variant 범위 표시|별도 축으로 저장하지 않음 — task id(`component.variant` 명명)가 Execution 섹션과 finding의 component 표시에 variant를 실어 나른다|`ScopeSelection`에는 variant 필드가 없고 스키마 추가 대신 task 명명 계약을 노출하는 것이 사실에 충실하다|
|지연 렌더링/페이지 분할|도입하지 않음|2000 findings 43ms·554KiB, 10000 findings 206ms·2.7MiB — 선형이고 페이로드가 결과 자체 크기에 비례한다. 임계값 도입은 finding을 버리는 선택이라 #222 item 6의 금지에 걸린다|
|JS 필터|인라인 단일 `<script>` 하나|Zero-CDN 유지 — `src=`/`url(`/외부 URL 없음이 grep 테스트로 고정. 필터는 표시만 바꾸며 저장 결과를 건드리지 않는다|
|legacy viewer|변환기가 아니라 경계|`legacy_reader`가 v3→`LegacyReport`를 읽고 next 문서를 거부한다. v3 viewer에 next 결과를 넣는 경로는 없고, 넣더라도 `schema_version` 부재로 거부된다. stable reporter 자체의 기능 추가는 #222 범위 밖 명시다|
|console/JSON/HTML/SARIF 일치|네 출력 모두 같은 저장 `RunResult`를 읽는다|`verify`의 console 요약, `result.json`, `report`의 HTML, `--sarif`의 SARIF이 같은 policy 결과에서 파생 — 회귀는 `test_rendering_a_result_that_was_saved_and_reloaded_is_identical`과 SARIF round-trip 테스트가 묶는다|

## 측정

`tests/test_reporting_offline_html.py::
test_a_large_result_renders_every_finding_within_budget`이 2000건 렌더를
시간 상한(10s tripwire)과 함께 고정한다. 이 트리에서의 실측:

|findings|render 시간|HTML 크기|
|---|---|---|
|500|11 ms|141 KiB|
|2000|43 ms|554 KiB|
|10000|206 ms|2765 KiB|
