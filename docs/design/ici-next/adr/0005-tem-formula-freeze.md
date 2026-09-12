# ADR-0005 — TEM 현행 수식을 그대로 동결하고 버전을 붙인다

- 상태: **accepted (evidence pending)** — 동결은 결정. `× 1.25` 계수의 근거는 미확인.
- 결정 시점: WP00 ([#198](https://github.com/jihoon22-lee/ici/issues/198)) 구현 순서 1
- 근거 이슈: [SPEC-03 #195 §7](https://github.com/jihoon22-lee/ici/issues/195)
- 관련 요구사항: R09, R10
- 담당 WP: [#219](https://github.com/jihoon22-lee/ici/issues/219)

## 결정

현행 TEM 수식을 **한 글자도 바꾸지 않고** ici-next로 이관하고, `formula_version`을 붙여
고정한다. **이 전환에서 새 수식을 발명하지 않는다.**

원본: `src/ici/engines/coverage_support.py:945` `calculate_tem`

```
pass_rate = passed_tests / total_tests           # total_tests == 0 이면 0.0

cov_factor, cov_label 결정 순서:
  1) coverage_totals["cover"] (line) 가 있으면
       cov_factor = min(80.0, line_cov) / 80.0 ,  label="Line"
  2) 아니면 coverage_totals["branch_cover"] 가 있으면
       cov_factor = min(80.0, branch_cover * 1.25) / 80.0 ,  label="Branch"
  3) 둘 다 없으면 (추정 입력)
       cov_factor = min(80.0, branch_cov) / 80.0 ,  label="Line", cov_suffix=" (est)"

tem_score = round(cov_factor * (func_cov / 100.0) * pass_rate * 5.0, 2)
tem_score = max(0.0, min(5.0, tem_score))        # [0, 5] 클램프
```

부수 사실:

- `max_tem_score = 5.0`이 `src/ici/engines/verify.py:358`에 하드코딩되어 있다.
- 게이트 하한은 `engines.test.min_tem_score` 기본 `4.0`이다.
- suite의 `tem_score`는 `engine_name == "test"`인 결과의 `score`를 그대로 승격한 값이다
  (`verify.py:330`). **TEM은 독립 엔진이 아니다.**

## 함께 결정한 것

| 항목 | 결정 |
|---|---|
|수식 자체|동결. `formula_version` 부여|
|`max_tem_score = 5.0`|동결. 하드코딩을 스키마 필드로 옮기되 값은 유지|
|`min_tem_score = 4.0` 기본값|동결|
|결측값 처리|**0/100으로 채우지 않는다.** `total_tests == 0` → `pass_rate = 0` → TEM 0을 그대로 유지|
|추정 입력 표기|현행은 `cov_suffix=" (est)"` 문자열 하나뿐이다. next에서는 **evidence 축(`ESTIMATED`)으로 분리**해 표현한다|
|필수 테스트 실패와의 관계|**TEM 점수로 실패한 필수 테스트를 상쇄하지 않는다**|

## 대안

| 대안 | 기각 사유 |
|---|---|
|이번에 수식을 개선한다 (예: 가중합으로 변경, 포화점 통일)|SPEC-03 §7이 명시적으로 금지한다 — "이 이슈에서 새 수식을 임의 발명하지 않는다". 구조 변경과 규칙/임계값 변경을 섞으면 differential 비교에서 회귀와 의도한 변경을 구분할 수 없다([PLAN 실행 규약 3](../roadmap.md))|
|TEM을 폐기한다|R09가 TEM을 보존 대상으로 명시한다. 현장에서 쓰이는 지표를 근거 없이 제거하지 않는다|
|`× 1.25` 계수만 제거한다|근거를 모르는 상태에서 제거하면 branch-only 프로젝트의 점수가 바뀐다. 근거 확인이 선행되어야 한다|
|결측 커버리지를 0으로 채운다|SPEC-03 §7이 금지한다. 현행도 0으로 채우지 않고 추정값을 쓴다|

## 근거

1. **구조 변경과 규칙 변경을 분리해야 한다.** ici-next의 핵심 검증 수단은 구·신 differential
   비교다([SPEC-05 §4](../spec-05-verification-transition.md)). 같은 입력에 대해 새 경로가 다른
   TEM을 내면, 그것이 구조 이관의 버그인지 의도한 수식 개선인지 구분할 수 없다. **수식을
   고정하면 TEM 차이는 곧 이관 버그다.**
2. **수식을 코드에서 정확히 추출했다.** 추정이나 문서 기억이 아니라 실제 함수 본문에서 읽었다.
   따라서 동결 대상이 명확하다.
3. **결측 처리가 이미 안전한 방향이다.** `total_tests == 0`일 때 `pass_rate = 0`이 되어 TEM이
   0이 된다. 테스트가 없는데 높은 점수가 나오는 구조가 아니다. 이 성질을 보존한다.

## 수식에서 확인된 설계 특성 (동결하되 기록)

1. **곱셈 3항 구조** — `cov_factor × (func_cov/100) × pass_rate × 5.0`. 가산 가중치가 없다.
   세 항 중 하나가 0이면 TEM은 0이다.
2. **커버리지 항만 80%에서 포화** — `min(80, x)/80`. line coverage 80% 이상은 추가 점수가 없다.
   반면 `func_cov`는 `/100`으로 선형이고 포화점이 없다. **두 항의 스케일 기준이 다르다.**
   의도된 설계인지 확인되지 않았으나, 이번에 바꾸지 않는다.
3. **branch → line 환산 계수 `× 1.25`** — branch coverage만 있을 때 1.25배로 올려 line 스케일에
   맞춘다. `1/0.8 = 1.25`이므로 "branch 80%를 line 100%에 대응시킨다"는 의도로 읽히지만,
   **코드·주석·문서·CHANGELOG 어디에도 근거가 없다.** → 보류 항목 5번

## 호환 영향

| 대상 | 영향 |
|---|---|
|TEM 점수 값|**없음.** 수식이 같으므로 같은 입력에 같은 점수|
|`tem_score`/`max_tem_score` JSON 필드|`formula_version` 필드가 추가된다. 기존 필드 의미는 유지|
|추정 커버리지 표기|`" (est)"` 접미사가 evidence 축(`ESTIMATED`)으로 이동한다. **사용자에게 보이는 표기 변경이므로 migration 표 항목**|
|`min_tem_score` 설정 키|유지. 위치가 `engines.test` → check 설정으로 이동할 수 있다([#203](https://github.com/jihoon22-lee/ici/issues/203))|

## 복구

수식을 바꾸지 않았으므로 복구 대상이 없다. 이관 후 TEM 값이 달라지면 **이관 버그로 취급하고
새 경로를 고친다.** 수식을 조정해 값을 맞추는 방향은 금지한다.

## 보류 항목

| # | 항목 | 결정 조건 | blocker |
|---|---|---|---|
|5|`branch × 1.25` 환산 계수의 근거|수식 소유자 확인, 또는 `1/0.8` 가설을 문서로 재도출·승인|아니오 — 문서화로 충족 가능|
|—|두 항의 스케일 기준 차이(80% 포화 vs 선형)가 의도인지|동일. 근거 확인 후 문서화|아니오|
|—|`formula_version` 식별자 형식|[#219](https://github.com/jihoon22-lee/ici/issues/219)가 스키마와 함께 결정|아니오|

**보류 항목 5번이 해결되기 전에 `× 1.25`를 제거·변경하지 않는다.** 근거를 찾지 못하더라도
"근거 미확인"을 문서에 남기고 값은 유지한다. 그것이 값을 임의로 바꾸는 것보다 안전하다.
