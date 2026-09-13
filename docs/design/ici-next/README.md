# ici-next — 설계 문서 시작점

**ici-next의 규범 문서는 이 디렉터리에 있다.** 진행 상태는
[GitHub 마일스톤](https://github.com/jihoon22-lee/ici/milestone/1)의 이슈가 갖는다.
문서와 이슈가 충돌하면 이 디렉터리의 문서가 규범 원문이고, 이슈는 진행 상태다.

| | |
|---|---|
|기준 소스|`20c417cc8ec84aa490d0138782bf9fe38374fb5d`|
|마일스톤|[ici-next — Modular analysis & offline-first execution](https://github.com/jihoon22-lee/ici/milestone/1)|
|문서 채택 WP|[WP00 #198](https://github.com/jihoon22-lee/ici/issues/198)|

> **문서 채택은 구현 완료가 아니다.** 각 SPEC의 완료 조건과 담당 WP를 확인한다.
> 현행 구현이 각 절을 어디까지 만족하는지는 [inventory/](#현행-구현-측정-기록-wp00)가 측정값으로
> 기록한다.

## 무엇을 찾는가

| 알고 싶은 것 | 읽을 문서 |
|---|---|
|전체 계획·단계·요구사항·착수 순서|[roadmap.md](roadmap.md)|
|기술 스택·모듈 경계·공통 모델·데이터 흐름|[architecture.md](architecture.md)|
|`ici.toml` 구조·설정 우선순위·CLI 옵션|[spec-01-workspace-config-cli.md](spec-01-workspace-config-cli.md)|
|배포 bundle·환경 보존·도구 선택·DAG·캐시|[spec-02-distribution-execution.md](spec-02-distribution-execution.md)|
|check/provider 구조·19개 엔진 처리·TEM|[spec-03-analysis-engines.md](spec-03-analysis-engines.md)|
|결과 JSON 스키마·종료 코드·게이트·GHES·idk|[spec-04-results-integration.md](spec-04-results-integration.md)|
|지원 환경표·corpus·구신 비교·릴리스 인수|[spec-05-verification-transition.md](spec-05-verification-transition.md)|
|어떤 결정이 왜 내려졌고 무엇이 보류인가|[adr/](adr/README.md)|
|요구사항 R01~R15이 어디로 연결되는가|[requirements-traceability.md](requirements-traceability.md)|
|v3 리포트와 `ici.next.run`이 서로를 어떻게 읽는가|[compatibility-v3-next.md](compatibility-v3-next.md)|
|**현행 코드가 실제로 어떻게 동작하는가**|[inventory/](#현행-구현-측정-기록-wp00)|
|**위험 가정을 실제로 시험한 결과**|[spikes/](#spike-측정-기록)|

## 읽는 순서

처음 보는 경우:

1. [roadmap.md](roadmap.md) — 목표·요구사항·단계. **여기서 시작한다.**
2. [architecture.md](architecture.md) — 시스템 경계와 모듈 구조
3. 담당하는 WP가 참조하는 SPEC (아래 「WP별 참조 문서」)
4. [inventory/](#현행-구현-측정-기록-wp00) — 바꾸려는 코드의 현재 상태

WP를 시작하는 경우:

1. 해당 WP 이슈 본문의 선행 조건·변경 대상·인수 기준
2. 그 WP가 참조하는 SPEC 절
3. [inventory/current-engines.md](inventory/current-engines.md) — 건드리는 엔진의 실측 상태
4. [inventory/execution-flow.md](inventory/execution-flow.md) §4 — 환경 보정 지점에 해당하는지
5. [adr/README.md](adr/README.md) 보류 항목 — 내가 추정으로 확정하려는 값이 여기 있는지

## 현행 구현 측정 기록 (WP00)

이 세 문서는 **목표 설계가 아니라 `20c417c` 시점의 관찰 기록**이다.
추정 없이 코드·실행 결과에서 추출했다.

|문서|내용|
|---|---|
|[inventory/current-engines.md](inventory/current-engines.md)|19개 descriptor 전수 표 — 스케줄링·언어별 mode·설정 키·CLI·side effect·TEM 수식·fixture·잠정 disposition|
|[inventory/execution-flow.md](inventory/execution-flow.md)|CLI 진입 → config → context → tool → runner → result → publisher 매핑, **환경 보정 지점 10개**, 종료 코드 대조|
|[inventory/baseline-measurements.md](inventory/baseline-measurements.md)|AGENTS 게이트 5개 실행 결과, 사용 가능 도구, **측정하지 못한 것 목록**|

## spike 측정 기록

WP가 "가정을 시험한다"고 정의한 작업의 실행 기록이다. 재실행 가능한 스크립트가 함께 있다.

|문서|WP|내용|
|---|---|---|
|[spikes/wp01-runtime-environment.md](spikes/wp01-runtime-environment.md)|[#199](https://github.com/jihoon22-lee/ici/issues/199)|PBS 런타임 manifest와 glibc 하한, bundle 이동·read-only·clean HOME·offline 실행, core/project 양방향 분리, symlink launch path, 테스트 도구 경로와 overlay 판정, compile DB 컴파일러 준수|

스크립트: [`scripts/spikes/wp01/`](../../../scripts/spikes/wp01) — `build-bundle.sh`,
`smoke-environment.sh`, `probe-test-tools.sh`, `probe-compiler.sh`.
**배포 기본값·프로젝트 `.venv`·공용 Python을 건드리지 않는다.**

## WP별 참조 문서

|WP|단계|주 참조|
|---|---|---|
|[#198](https://github.com/jihoon22-lee/ici/issues/198) WP00|P0|전체 (이 문서 세트를 채택한 WP)|
|[#199](https://github.com/jihoon22-lee/ici/issues/199) WP01|P0|[spec-02 §2·§3](spec-02-distribution-execution.md), [execution-flow §4](inventory/execution-flow.md) → 결과 [spikes/wp01](spikes/wp01-runtime-environment.md)|
|[#200](https://github.com/jihoon22-lee/ici/issues/200) WP02|P1|[spec-04 §1·§2](spec-04-results-integration.md), [architecture §4](architecture.md)|
|[#201](https://github.com/jihoon22-lee/ici/issues/201) WP03|P0|[spec-05 §2·§3](spec-05-verification-transition.md), [baseline §5](inventory/baseline-measurements.md)|
|[#202](https://github.com/jihoon22-lee/ici/issues/202) WP04|P1|[spec-02 §1](spec-02-distribution-execution.md), [ADR-0002](adr/0002-standalone-runtime-bundle.md)|
|[#203](https://github.com/jihoon22-lee/ici/issues/203) WP05|P1–P2|[spec-01 §2·§3·§4](spec-01-workspace-config-cli.md), [execution-flow §3](inventory/execution-flow.md)|
|[#204](https://github.com/jihoon22-lee/ici/issues/204) WP06|P1–P2|[spec-02 §3](spec-02-distribution-execution.md), [execution-flow §4](inventory/execution-flow.md)|
|[#205](https://github.com/jihoon22-lee/ici/issues/205) WP07|P1–P2|[spec-02 §4·§5](spec-02-distribution-execution.md)|
|[#206](https://github.com/jihoon22-lee/ici/issues/206) WP08|P1|[spec-05 §2](spec-05-verification-transition.md) 계층 4 (bundle E2E)|
|[#207](https://github.com/jihoon22-lee/ici/issues/207) WP09|P2|[spec-01 §1](spec-01-workspace-config-cli.md), [architecture §4](architecture.md)|
|[#208](https://github.com/jihoon22-lee/ici/issues/208) WP10|P2|[spec-03 §1·§2](spec-03-analysis-engines.md), [ADR-0004](adr/0004-check-provider-separation.md)|
|[#209](https://github.com/jihoon22-lee/ici/issues/209) WP11|P2|[spec-02 §6](spec-02-distribution-execution.md)|
|[#210](https://github.com/jihoon22-lee/ici/issues/210) WP12|P2|[spec-01 §5·§6](spec-01-workspace-config-cli.md)|
|[#211](https://github.com/jihoon22-lee/ici/issues/211) WP13|P3|[spec-03 §6](spec-03-analysis-engines.md)|
|[#212](https://github.com/jihoon22-lee/ici/issues/212) WP14|P3|[spec-03 §6](spec-03-analysis-engines.md), [spec-02 §4](spec-02-distribution-execution.md)|
|[#213](https://github.com/jihoon22-lee/ici/issues/213) WP15|P3|[spec-03 §6](spec-03-analysis-engines.md)|
|[#214](https://github.com/jihoon22-lee/ici/issues/214) WP16|P3|[spec-03 §2·§6](spec-03-analysis-engines.md)|
|[#215](https://github.com/jihoon22-lee/ici/issues/215) WP17|P4|[spec-03 §5](spec-03-analysis-engines.md), [spec-01 §7](spec-01-workspace-config-cli.md)|
|[#216](https://github.com/jihoon22-lee/ici/issues/216) WP18|P4|[spec-03 §7](spec-03-analysis-engines.md) Python|
|[#217](https://github.com/jihoon22-lee/ici/issues/217) WP19|P4|[spec-03 §7](spec-03-analysis-engines.md) C++|
|[#218](https://github.com/jihoon22-lee/ici/issues/218) WP20|P4|[spec-03 §3](spec-03-analysis-engines.md), [current-engines §7·§8](inventory/current-engines.md)|
|[#219](https://github.com/jihoon22-lee/ici/issues/219) WP21|P4–P5|[spec-03 §7](spec-03-analysis-engines.md), [ADR-0005](adr/0005-tem-formula-freeze.md), [spec-04 §2](spec-04-results-integration.md)|
|[#220](https://github.com/jihoon22-lee/ici/issues/220) WP22|P4|[spec-03 §3](spec-03-analysis-engines.md) 선택 제공군|
|[#221](https://github.com/jihoon22-lee/ici/issues/221) WP23|P5|[spec-04 §4](spec-04-results-integration.md)|
|[#222](https://github.com/jihoon22-lee/ici/issues/222) WP24|P5|[spec-04 §5](spec-04-results-integration.md)|
|[#223](https://github.com/jihoon22-lee/ici/issues/223) WP25|P5|[spec-04 §7](spec-04-results-integration.md)|
|[#224](https://github.com/jihoon22-lee/ici/issues/224) WP26|P5|[spec-04 §6](spec-04-results-integration.md)|
|[#225](https://github.com/jihoon22-lee/ici/issues/225) WP27|P6|[spec-05 §5](spec-05-verification-transition.md) migration 표|
|[#226](https://github.com/jihoon22-lee/ici/issues/226) WP28|P6|[spec-05 §1·§6·§7](spec-05-verification-transition.md)|
|[#227](https://github.com/jihoon22-lee/ici/issues/227) WP29|P6|[spec-05 §8](spec-05-verification-transition.md), [ADR-0003](adr/0003-agents-invariant-scoping.md) 보류 항목|

## 기존 문서와의 관계

| 문서 | 상태 |
|---|---|
|[docs/architecture.md](../../architecture.md)|**현행(stable) 구현** 설명. next 설계는 이 디렉터리|
|[docs/engine-reference.md](../../engine-reference.md)|**현행** 19개 엔진 사용자 참조|
|[docs/user-guide.md](../../user-guide.md)|**현행** CLI 사용법|
|[docs/ci-integration.md](../../ci-integration.md)|**현행** CI 연동|
|[docs/design/ci-validation-roadmap.md](../ci-validation-roadmap.md)|**현행** CI 검증 로드맵|
|[docs/design/self-verification-debt.md](../self-verification-debt.md)|**현행** 자기검증 부채|
|[docs/superpowers/](../../superpowers/)|인수인계 기록. 시점 문서이며 갱신 대상이 아니다|
|[AGENTS.md](../../../AGENTS.md)|**두 경로 모두.** 적용 범위 절이 stable/next를 구분한다|

## 문서 규칙

1. **규범 원문은 이 디렉터리, 진행 상태는 이슈.** 두 곳에서 같은 것을 중복 관리하지 않는다.
2. `inventory/`는 **측정된 사실만** 담는다. 추정은 "미확인"으로 남기고 후속 WP를 적는다.
3. 목표 설계 문서가 현행과 다른 지점은 인용 블록(`>`)으로 표시하고 inventory를 링크한다.
4. 결정에는 ADR을 만든다. 보류 항목은 [adr/README.md](adr/README.md)의 종합 표에 모은다.
5. **측정하지 않은 것을 지원한다고 쓰지 않는다.** `planned`/`tested`/`supported`/`limited`/
   `unsupported`를 구분한다([spec-05 §1](spec-05-verification-transition.md)).
6. 문서 링크와 registry 일치는
   [`tests/test_ici_next_inventory.py`](../../../tests/test_ici_next_inventory.py)가 기계 검증한다.
