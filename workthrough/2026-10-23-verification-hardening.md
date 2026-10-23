# 0.12.1 검증 안정성 수정과 배포 근거

검토 기준은 `bd46a85599935495e1453ac9858eabf0740111eb`이다. 계약 대조, 실행 흐름 추적,
최소 사례 재현, 전체 게이트, 대조 실행의 다섯 단계에서 결함 8건을 확인했다.
아래 수정은 같은 조건을 정식 회귀 테스트로 만들고, 빌드된 후보에서도 다시 확인한 결과다.

공개 여부, 최종 main·candidate·Quality Zoo·release 실행 링크와 다운로드 후 체크섬은
[v0.12.1 릴리스](https://github.com/jihoon22-lee/ici/releases/tag/v0.12.1)에 모은다.
이 문서는 로컬 검증과 계약 변경을 기록한다. 후보 빌드 자체를 공개 릴리스로 취급하지 않는다.

## 수정 범위

|검토 항목|수정 후 동작|PR·회귀 근거|
|---|---|---|
|1. Ruff 설정의 자동 수정|stable/next가 fix·fix-only·unsafe-fixes를 명시적으로 끄고 원본 바이트와 F401 진단을 보존|[#287](https://github.com/jihoon22-lee/ici/pull/287), [소스 보존](../tests/test_verify_source_preservation.py)|
|2. AST 분석 미완료 PASS|파싱·읽기 실패와 분석 예산 초과는 필수 검사 INCOMPLETE. 확인한 finding과 측정값은 보존|[#288](https://github.com/jihoon22-lee/ici/pull/288), [분석 완결성](../tests/next/test_analysis_completeness.py)|
|3. 설정 변경 후 과거 PASS 재사용|자동 설정/import 입력을 완전히 식별하지 못하는 Ruff·mypy·ty observation 캐시를 끄고 사유 기록|[#289](https://github.com/jihoon22-lee/ici/pull/289), [실제 설정 변경](../tests/next/test_provider_cache_inputs.py)|
|4. finding 충돌·suppression 전파|소비자의 component·analysis unit·variant를 귀속한 뒤 fingerprint·required·suppression·dedup을 평가|[#292](https://github.com/jihoon22-lee/ici/pull/292), [소비자별 정체성](../tests/next/test_finding_scope_identity.py)|
|5. 다른 component의 exclude가 입력 삭제|component 내부에서 포함/제외를 적용한 다음 소유권을 합집합으로 구성|[#290](https://github.com/jihoon22-lee/ici/pull/290), [입력 소유권](../tests/next/test_workspace_inventory.py)|
|6. hybrid 테스트 capability 충돌|Python/C++ 테스트 증거의 이름을 구분|[#291](https://github.com/jihoon22-lee/ici/pull/291), [테스트 의존성](../tests/next/test_test_evidence_dependencies.py)|
|7. 다중 QTest suite의 중복 producer|suite별 증거를 만들고 coverage가 모든 suite를 요구. 중복 실행·측정 집계 없이 단일 producer 불변식 유지|동일 PR·회귀 파일|
|8. 미검사 finding의 거짓 resolved|같은 검사·입력 범위가 완료된 경우에만 resolved. 미선택·실패·차단·취소·부분 증거·범위 변경은 carried|[#293](https://github.com/jihoon22-lee/ici/pull/293), [저장된 완료 범위](../tests/next/test_baseline_execution_scope.py)|

## 결과·캐시 이전

- writer는 `ici.next.run` **v2**, finding 정규화는 `ici.next.fingerprint.v3`을 사용한다.
  결과 envelope와 fingerprint 정규화, stable finding v3은 서로 다른 버전 축이다.
- 결과 v1의 스키마와 golden fixture를 보존했다. v1은 읽고 다시 저장할 수 있지만, 완료 범위를
  알 수 없어 baseline 비교는 `INCOMPATIBLE`이다. v2의 `checks=null`도 같은 의미다.
- 새 결과의 `checks`는 task/check/component/language, 입력 범위 digest, 실행 상태와 증거
  완결성, nullable analysis unit/variant를 명시한다. 입력 내용의 수정은 문제를 해결할 수
  있지만 입력 목록의 축소는 해결의 근거가 되지 않는다. `verify --baseline`과 `next diff`는
  같은 저장 정보를 사용한다.
- 업그레이드 후 원하는 범위를 실행해 새 baseline을 검토·채택한다. fingerprint를 직접 지정한
  suppression도 확인한다. [이전 안내](../docs/design/ici-next/compatibility-v3-next.md)에 절차가 있다.
- 범용 observation 캐시는 유지한다. 재사용 키에 observation/fingerprint 정규화 버전을 넣고,
  손상된 캐시의 폐기·재실행·finding 보존을 계속 검증한다.

## 실제 검증 결과

개발 회귀 환경은 Python 3.10.21, uv 0.12.5, Ruff 0.16.4이다. `uvx ruff` 게이트는
0.17.2로도 수행했다. 아래 skip을 실행 성공에 합산하지 않았다.

|검증|결과|
|---|---|
|통합 `uv run --python 3.10 pytest -ra`|4,211 passed, 15 skipped, 537.58초|
|`uvx ruff check .`, `uvx ruff format --check .`|통과, 483개 파일|
|`./scripts/verify-reproducibility.sh`|타임스탬프·locale·umask·hash seed를 달리한 두 pyz 빌드의 SHA-256 일치, 소스 상태 불변|
|`./scripts/smoke.sh`|Python 3.10 직접 실행, `dist/ici` 동일성, HTML Zero-CDN 통과. 내부 verify exit 0|
|0.12.1 pyz에서 추출한 모듈의 수정 회귀|56 passed|
|원래 최소 재현 조건을 실제 0.12.1 pyz 명령으로 대조|8건 모두 수정 동작 확인. 결과 envelope 17개가 producer 0.12.1·schema v2임을 확인|
|v2 JSON Schema 검증|jsonschema 4.26.0으로 정상 결과 수용·거짓 완료 거부 확인|
|Quality Zoo 계약/등록부|68개 unit test와 56개 fixture inventory 검사 통과|
|0.12.1 후보의 Python Quality Zoo|5/5 계약 PASS, runner error 0. 결함을 심은 시나리오의 의도된 FAIL/WARN도 기대값과 일치|
|bundle 재조립|7,388개 파일·링크 항목의 바이트/대상 일치, 차이 0|
|실제 bundle strict smoke|12 PASS, 0 FAIL, 0 BLOCKED|
|bundle smoke harness 회귀|28 passed. 잘못된 PASS와 거짓 완료 증거를 거부하는 두 경우 추가|

전체 테스트의 skip 15건은 optional jsonschema 7건, 로컬 clang/clang-tidy/clazy 부재 7건,
Python 3.10 AST의 except-star 미지원 1건이다. CI는 필요한 C++ 도구를 설치하고 엄격 모드로
실행한다. 표의 전체 테스트는 bundle harness 보강 직전의 통합 실행이며, 이후 harness 변경은
28개 대상 테스트로 확인했다. release PR CI가 최종 트리의 전체 게이트를 다시 수행한다.

로컬 0.12.1 후보 `ici.pyz`의 SHA-256:

```text
9fdf6cb1c526d7b25d44c366bc8bf46f1433806d6f6e012bb756e9ffb12f27b1
```

Quality Zoo의 16개 시나리오에 이 digest를 명시적으로 연결했다. 기존 known-answer의
검출·상태·증거·위치 기대값을 유지하고 producer version만 갱신했다. 원격 candidate 검증에서
전체 16개가 이 계약을 만족하는지 판정하며, 로컬 Python 5개 통과를 전체 corpus 통과로 쓰지 않는다.

## bundle smoke 구성 보정

기존 next 샘플은 프로젝트 Python·테스트 스위트·type checker를 제공하지 않으면서 기본
필수 검사를 요청했다. 실제 bundle에서 10 PASS·1 BLOCKED였고, 결과는 test/coverage/type
증거 부재를 올바르게 기록했다. 샘플 구성이 현재 기본 검사와 맞지 않는 상태였다.

분석 샘플은 제공된 도구의 범위를 명시하도록 고쳤다. 별도 샘플은 기본 필수 test/coverage를
그대로 켜고 프로젝트 Python을 제공하지 않는다. exit 3, `required_complete=false`, 차단된
검사 목록, `.venv` 미생성을 모두 요구한다. analyzer 누락의 BLOCKED 판정과 strict 모드의
실패 기준은 유지한다. 네트워크 namespace와 읽기 전용 설치를 포함한 12개 경우를 실제로 측정했다.

bundle 입력은 CPython 3.13.7과 Ruff 0.16.4다. runtime ELF에서 측정한 최대 GLIBC 심볼은
2.17이며, runtime tree digest는
`sha256:c7fa769545724d735a54eefd9af74227921ca569768f0517812c050c9d6c39fa`다.

## 유지한 지원 경계

stable의 Python 3.10 하한, 순수 wheel, 시스템 CA, 단일 pyz, 기존 CI 게이트를 유지했다.
next/bundle은 candidate이고, RHEL 8.10·실제 GHES·실제 idk 소비자 인수는 이번 자동화 결과로
대체하지 않는다. [현장 인수 표](../docs/design/ici-next/field-acceptance.md)의 제한을 유지한다.
