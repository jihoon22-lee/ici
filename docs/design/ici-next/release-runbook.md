# ici-next 릴리스 runbook (WP29 PR C)

> 상태: **runbook 초안** — 자동화된 근거는 연결됐고, RHEL/GHES 현장 인수는
> 별도 이슈에서 수행된다. 이 문서는 candidate가 stable이 아니라는 구분 위에
> 쓰인다 — feature PR merge는 release를 만들지 않는다(AGENTS §7).

## 1. 검증 계층과 필수 여부

|계층|워크플로|트리거|소유|release 필수|
|---|---|---|---|---|
|PR 게이트|[ci.yml](../../../.github/workflows/ci.yml)|pull_request|ici 저장소|**예** — 단위·디퍼렌셜·보안·fixture corpus·pyz 빌드·stable+next dogfood|
|번들 smoke|[bundle-smoke.yml](../../../.github/workflows/bundle-smoke.yml)|정기 + bundle 경로 변경|ici 저장소|**예** — 오프라인·read-only·이동·symlink·두 버전 병행|
|candidate artifact|[candidate-artifact.yml](../../../.github/workflows/candidate-artifact.yml)|수동 dispatch|ici 저장소|release 판정 시 — 고정 소스에서 재조립·재검증|
|toy 소비자 검증|[candidate-quality-zoo.yml](../../../.github/workflows/candidate-quality-zoo.yml)|수동 dispatch|toy-projects(정확한 SHA 고정)|**명시적 소비자 검증** — 필수 게이트가 아니다|

필수 게이트는 전부 ici가 소유한다 — 어떤 필수 검증도 toy-projects의 동시
변경이나 최신 `main`을 요구하지 않는다(#227 항목 2). ici 소유 회귀 corpus는
`tests/fixtures/manifest.toml` 등록부에 고정돼 있고 디스크↔등록부 양방향
대조가 CI에서 걸린다(R13). toy quality-zoo는 released/candidate ici의
소비자 관점 검증으로 남는다 — ici용 특수 구조를 요구하지 않는다.

## 2. R01~R15 최종 체크리스트

요구사항별 근거는 [requirements-traceability.md](requirements-traceability.md)의
추적표가 원본이다. 아래는 release 판정 시점의 상태 요약이다 — `tested`는
자동화 근거가 있음, `현장 대기`는 사내 환경에서 수행할 항목이다.

|요구사항|개발 상태|남은 확인|
|---|---|---|
|R01 셸 초기화 금지|tested — 프로세스 계약·smoke clean HOME|—|
|R02 반복 설치 축소|tested — bundle이 도구 동봉, no-installs 케이스|현장: 실제 프로젝트 설정에서 반복 workaround 여부|
|R03 전용 런타임 번들|tested — glibc 2.17 실측, 이동/read-only 통과|현장: RHEL 8.10에서 추출→실행|
|R04 언어별 묶음·공통 결과|tested — 단일 결과 모델|—|
|R05 부분 실행 표시|tested — scope 축·INCOMPLETE 게이트|—|
|R06 루트 `ici.toml`|tested — `[workspace]` discovery, 개인 설정 분리|현장: 정상 설정 편집 흐름|
|R07 도메인 구분|tested — Workspace/Component/BuildUnit/AnalysisUnit|—|
|R08 check/provider 분리|tested — DAG·공유 계약|—|
|R09 descriptor disposition|tested — 디퍼렌셜 corpus가 회귀 2건을 잡아 수정|현장: precision/recall 최종 근거는 미측정으로 남긴다|
|R10 상태 축 분리|tested — 6축 모델·스키마·v3 리더 실측|—|
|R11 폐쇄망|tested — `unshare -n` 오프라인 증명, Zero-CDN|—|
|R12 idk 계약|tested — `ici.next.event` v1·fixture consumer|외부: 실제 idk 소비자 검증(별도 저장소)|
|R13 ici 소유 corpus|tested — 등록부·양방향 대조·dogfood fixture|—|
|R14 단계적 전환|tested — migrate·cutover dispatch·양방향 호환표|현장: 실제 프로젝트 전환 rehearsal|
|R15 외부 반출 금지|tested — 토큰 누설·경로 정규화·redaction|—|

`현장 대기`/`외부` 항목은 전부 현장 인수 이슈(→ `field-acceptance.md`)에서
수행한다. 자동화 통과를 그 근거로 쓰지 않는다.

## 3. Rollback

전환은 세 층에서 각각 되돌릴 수 있다 — 어느 층도 공용 Python이나 project
`.venv`를 복구 대상으로 만들지 않는다.

|층|되돌림|
|---|---|
|설정|`ici next migrate --write`가 남긴 `ici.toml.stable`을 `ici.toml`로 복사한다. dry-run이 기본이라 원본이 먼저 남는다|
|실행 경로|설정을 되돌리면 bare `ici verify`가 다시 stable 엔진을 탄다 — cutover dispatch는 `[workspace]` 선언을 보고 판단하므로 설정이 곧 스위치다|
|배포물|새 bundle이 문제면 symlink를 이전 bundle로 되돌리거나 `dist/ici.pyz`로 돌아간다 — 이전 디렉터리·pyz·빌드 스크립트는 제거하지 않고 남아 있다|
|결과|next 결과(`.ici/next/result.json`)와 v3 리포트는 스키마가 다르며 서로를 덮지 않는다. v3 reader·migration fixture는 전환 후에도 보존된다|

cutover 후 회귀가 발견되면: 설정을 `ici.toml.stable`로 되돌려 stable 경로로
즉시 운용하고, 결과 비교는 `ici next diff`로 두 실행의 finding 차이를
남긴다.

## 4. Risk register

|위험|현재 완화|잔여|
|---|---|---|
|디퍼렌셜이 잡지 못한 동작 차이|시드 corpus 디퍼렌셜(회귀 2건 실제 검출)|현장 corpus 확대 — R09 근거 미측정으로 명시|
|bundle이 현장 환경에서 실행 불가|glibc 2.17 상한 측정·오프라인/read-only smoke|RHEL 8.10 실측 — field-acceptance R-계열|
|GHES 게시 실패|mock transport 계약 테스트 19건|실제 GHES G-1~G-6 — field-acceptance|
|idk 소비자 불일치|fixture consumer·JSONL 코덱 계약|실제 idk 저장소 검증 — 외부 이슈|
|사용자가 stable 플래그를 next 설정에 사용|dispatch가 대체 수단과 함께 거절|—|
|cutover 후 회귀|설정 복사 + 이전 artifact로 즉시 복귀(§3)|—|
|candidate를 release로 오인|artifact 명명·manifest의 candidate 표기|릴리스 결정은 별도 소유자|

## 5. Release 결정 절차

1. §1 필수 계층이 고정 소스·고정 runtime source에서 green인지 확인한다.
2. 현장 인수 이슈가 닫혔는지 확인한다 — 열려 있으면 release는 `limited`
   지원 표기로만 진행하거나 보류한다.
3. `manifest.json`의 build input lock digest를 두 번 재조립해 재현성을
   확인한다([bundle-reproducibility.md](bundle-reproducibility.md)).
4. 지원/제한표·CHANGELOG·사용 가이드·migration 문서가 동기화됐는지 확인한다.
5. 버전·태그·공개 시점은 별도 소유자가 결정한다 — 이 runbook은 근거를
   나열할 뿐 결정하지 않는다.
