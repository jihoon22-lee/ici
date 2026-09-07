# ici 자체 검증의 남은 WARN과 SKIP

> **네비게이션**: [🏠 홈 (README)](../../README.md) &bull; [🚀 사용자 가이드](../user-guide.md) &bull; [📏 검증 엔진 레퍼런스](../engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](../ci-integration.md)

ici는 자기 자신을 게이트로 검증합니다(dogfood). 그 실행이 초록불이 아니라면, 남은 노란불
하나하나가 **무엇이고 왜 받아들여져 있는지**가 여기 적혀 있어야 합니다. 설명 없는 WARN은
게이트가 아니라 소음이고, 소음은 시간이 지나면 아무도 읽지 않습니다.

이 문서는 `deep` profile 실행의 모든 non-PASS를 설명합니다. 여기에 없는 non-PASS가
나타났다면 그것이 회귀입니다.

## 측정 기준

2026-09-06, `./dist/ici.pyz verify --profile deep --report`, 로컬 기준 워크스테이션.

```
Total Engines: 16  (Pass: 11, Warn: 3, Fail: 0, Error: 0, Skip: 2)
TEM Score: 4.78 / 5.0   Suite: WARN — 3 engine(s) warned: line, cognitive, complexity
```

`ici.toml`이 이 저장소의 정책을 고정합니다. 아래 수치는 모두 그 정책 기준입니다.

## SKIP 2건 — 둘 다 범위 부재이며, 해당 경로는 다른 곳에서 실행됩니다

| 엔진 | 메시지 | 설명 |
|---|---|---|
| `compile_db` | No production C/C++ translation units are in scope. | ici 코어는 순수 Python입니다. C++ compile context 경로는 같은 저장소의 `viewer/`에서 실제로 실행되며, CI의 "Dogfooding — C++ Gate via ici (viewer/)" 스텝이 그 리포트를 따로 남깁니다. |
| `thread_sanitize` | ThreadSanitizer skipped: no applicable C++ checks were executed | 위와 같은 이유입니다. TSan은 `deep` profile의 compiled C/C++ 테스트만 대상으로 하고 Python scope는 선언상 `unsupported`입니다. |

두 SKIP은 `NOT_APPLICABLE` 증거로 남습니다. **범위가 있었는데 측정하지 못한 SKIP은 이와
다르며 `ESTIMATED`로 남고 게이트를 계속 막습니다.** 두 상태를 구분하지 않으면 "테스트가 없다"가
"검사가 통과했다"로 읽힙니다.

## WARN 3건 — 모두 코드 규모/복잡도이며, 승인된 부채입니다

### `line` — 500라인을 넘는 파일 35개 (최대 965, FAIL 한계 1000)

| | 값 |
|---|---|
| 정책 | `warn_limit = 500`, `fail_limit = 1000` |
| 실측 | 141 파일 중 35개가 WARN, 최대 965라인 |
| 최대 파일 | `_cpp_linker_dead_symbols.py` 965, `_cpp_diagnostics.py` 958, `complexity.py` 925, `ici-result-v3.schema.json` 922 |

**승인된 부채입니다.** 상위 파일들은 하나의 외부 도구 계약을 통째로 다루는 어댑터이거나
(`_cpp_linker_dead_symbols`, `_cpp_diagnostics`), 스키마 그 자체입니다. 이들을 파일 크기만을
이유로 쪼개면 한 계약이 두 파일에 걸쳐 읽히게 되어, 지금 한 곳에서 확인되는 경계 검사가
흩어집니다.

부채인 이유는 따로 있습니다. 최대값이 FAIL 한계에 **가깝다**는 것입니다. 2026-09-06 이전에는
`sanitize.py`가 정확히 1000라인, 즉 한 줄 차이로 자기 게이트를 빨간불로 만드는 자리에
있었습니다. 그날 C++ sanitizer replay와 Python ResourceWarning scope를 분리해
(`_sanitize_python_scope.py`) 그 자리를 벗어났습니다. 같은 일이 다시 일어나면 같은 방식으로
해결합니다 — **분리할 이음매가 실제로 있을 때만** 분리합니다.

#### 이 게이트가 실제로 막았습니다 (2026-09-07)

위 표에 `ici-result-v3.schema.json` 998라인, 여유 2라인이라고 적어 둔 그 다음 작업에서
SARIF `fixes` 계약을 추가하며 그 파일에 42라인을 더했습니다. 1038라인, CI에서
`Suite: FAIL — required engine 'line' failed`. **문서에 적어 둔 위험에 그대로 걸어 들어갔고,
게이트가 잡았습니다.**

고친 방식이 중요합니다. 세 가지 유혹이 있었습니다.

- **`.json`을 line 게이트에서 제외한다** — `EXT_MAP`에 `.json`이 `.toml`·`.md`와 함께 의도적으로
  들어 있습니다. 자기 PR을 통과시키려고 의도된 설계를 바꾸는 것이라 하지 않았습니다.
- **스키마를 여러 파일로 쪼갠다** — `$ref` resolver가 필요해지고, 계약 하나를 읽으려면 여러
  파일을 열어야 합니다. 게이트 자신의 기준으로도 더 나빠집니다.
- **포맷을 압축한다** — 택했습니다. 6~16개짜리 `required` 배열 17개가 각각 8~18줄을 쓰고
  있었습니다. 100자 안에 들면 한 줄로, 넘으면 100자에서 줄바꿈해 1038 → 922라인이 됐습니다.
  파싱 결과가 HEAD와 완전히 동일한지 확인했으므로 계약은 한 글자도 바뀌지 않았습니다.

세 번째만이 게이트를 약화시키지도, 구조를 복잡하게 만들지도 않으면서 실제로 읽기 쉬워집니다.
여유는 2라인에서 78라인이 됐습니다.

#### 그리고 같은 날 또 걸렸습니다

`_cpp_linker_dead_symbols.py` 974라인도 위 표에 적어 두었는데, 그날 linker 교차 대상 규칙을
추가하며 1017라인이 됐습니다. **문서에 적어 둔 두 번째 위험에도 그대로 걸어 들어간 것입니다.**

이번엔 대상이 스키마가 아니라 실제 소스라 답이 달랐습니다. 순수 집계 규칙 —
프로세스도 파일시스템도 도구도 없이 "각 링크가 무엇을 버렸는지"만 보고 판단하는 부분 —
을 `_cpp_linker_dead_aggregation.py`로 분리했습니다. 965 + 70라인이 됐고, 규칙을 빌드 없이
읽고 테스트할 수 있게 됐습니다.

분리가 만든 함정도 하나 있었습니다. 그 모듈이 결과를 결정하는데 `CACHE_IMPLEMENTATION_MODULES`
에 없으면 **규칙을 바꿔도 캐시가 옛 finding을 그대로 내줍니다.** 등록하고 테스트로 고정했습니다.

같은 게이트에 하루에 두 번 걸린 것은 우연이 아닙니다. 이 표의 숫자는 읽고 넘길 목록이 아니라
**다음 변경이 어디에 부딪힐지 알려주는 예보**입니다.

### `cognitive` — 인지 복잡도 30 초과 함수 74개 (최대 48, FAIL 한계 60)

| | 값 |
|---|---|
| 정책 | `warn = 30`, `fail = 60`, `warn_nesting = 4`, `mode = "pass_warn"` |
| 실측 | 1,970 함수 중 74개가 WARN, 최대 48 |

**승인된 부채입니다.** 다만 2026-09-06에 실제 리팩터링을 한 번 거쳤습니다. 그 전 최대값은
`parse_gcov_json_dir`의 **66**이었고, 요약문은 `fail threshold 60 exceeded`를 달고 있었습니다.
`mode = "pass_warn"`이라 FAIL로 올라가지 않았을 뿐, 정책상 실패 수준을 넘긴 함수가 자기
저장소에 있었다는 뜻입니다. 17개 누적자를 가진 182라인 함수를 tally·budget·검증·row·provenance로
분리해 66 → 13 아래로 내렸고, 그 다음으로 높던 `tooling_include_roots`(57)와
`_build_python_graph`(53)도 함께 정리해 현재 최대값은 48입니다.

남은 74개는 대부분 외부 도구 출력을 파싱하는 경계 검사입니다. 분기 하나하나가 "이 입력은
신뢰할 수 없다"는 개별 판단이라 합치면 검사가 사라집니다.

### `complexity` — 순환 복잡도 15 초과 함수 147개 (최대 24, FAIL 한계 25)

| | 값 |
|---|---|
| 정책 | `warn_cc = 15`, `fail_cc = 25`, `warn_nesting = 4` |
| 실측 | 1,970 함수 중 147개가 WARN, 최대 24 |

**승인된 부채입니다.** 여기서도 2026-09-06에 경계에서 물러나는 작업을 했습니다. 그 전에는
네 함수가 정확히 CC 25, 즉 `fail_cc`와 같은 값에 있었습니다 — 분기 하나만 늘어도 자기 게이트가
빨간불이 되는 자리입니다. `_locations`, `sanitize.run`, `parse_compiler_include_search`,
`_render_test_section`을 각각 분리해 현재 최대값은 24입니다.

## 지금 상태를 한 줄로

세 WARN 엔진 모두 **FAIL 한계 바로 아래가 아니라, 한 단계 이상 떨어진 곳에** 있습니다.
2026-09-06 이전에는 셋 다 정확히 경계 위에 있었고, 그것은 부채가 아니라 사고 대기 상태였습니다.

| 엔진 | FAIL 한계 | 2026-09-06 이전 | 현재 | 여유 |
|---|---|---|---|---|
| `line` | 1000 | 1000 (경계) | 965 | 35 |
| `cognitive` | 60 | 66 (**초과**) | 48 | 12 |
| `complexity` | 25 | 25 (경계) | 24 | 1 |

`complexity`의 여유 1은 여전히 좁습니다. 다음 리팩터링 대상은 CC 24에 있는 여섯 함수입니다.

## 사람이 검토 가능한가 (I9-2)

`deep` 실행은 actionable finding 11,019건을 냅니다. 그 전부를 콘솔에 붓는 것은 검토가 아니라
포기입니다. 두 표시 경로를 각각 확인했습니다.

- **콘솔 기본 출력**: issues-first projection이 엔진당 5 그룹으로 제한해 20 그룹을 보여주고,
  `Hidden: 385 finding(s) in 385 group(s)`와 재실행 명령을 함께 출력합니다. 숨긴 개수를
  정직하게 세므로 "적게 보여준다"와 "적게 찾았다"가 구분됩니다.
- **전체 리포트**: HTML은 10개 탭과 축별 필터(engine/rule/category/severity/file)를 제공하고,
  2,000건을 넘으면 초기 DOM을 50행으로 제한한 뒤 브라우저에서 채웁니다. JSON은 전체
  inventory를 그대로 보존합니다. 10만 finding 규모의 실측은
  [CI 연동 가이드 2.5절](../ci-integration.md)에 있습니다.

## 이 문서를 갱신하는 시점

- `deep` 실행에 여기 없는 non-PASS가 생겼을 때
- 위 표의 실측값이 바뀌었을 때
- `ici.toml`의 임계값을 조정했을 때 (조정 근거를 함께 남깁니다)
