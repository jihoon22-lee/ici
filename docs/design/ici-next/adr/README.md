# ici-next ADR

각 ADR은 **결정·대안·근거·호환 영향·복구**를 기록한다
([architecture.md §10](../architecture.md)의 요구).

`상태`는 다음 중 하나다:

- **accepted** — 결정했고 근거가 있다.
- **accepted (evidence pending)** — 방향은 결정했으나 구체 값·버전은 시험 후 고정한다.
  이 상태의 ADR은 **release blocker 목록을 반드시 포함한다.**
- **deferred** — 결정을 미뤘다. 미룬 이유와 결정 시점을 적는다.
- **superseded by ADR-nnnn** — 다른 ADR이 대체했다.

| ADR | 제목 | 상태 |
|---|---|---|
|[0001](0001-keep-python-core.md)|Python 코어와 Typer/Rich를 유지한다|accepted|
|[0002](0002-standalone-runtime-bundle.md)|전용 런타임을 포함한 압축 디렉터리로 배포한다|accepted (evidence pending)|
|[0003](0003-agents-invariant-scoping.md)|AGENTS 불변식을 stable 경로 전용으로 범위 지정한다|accepted|
|[0004](0004-check-provider-separation.md)|검사 관점(check)과 분석 제공자(provider)를 분리한다|accepted|
|[0005](0005-tem-formula-freeze.md)|TEM 현행 수식을 그대로 동결하고 버전을 붙인다|accepted (evidence pending)|

## 보류 항목 종합

각 ADR의 보류 항목을 모은 것이다. **이 목록의 항목은 추정으로 확정하지 않는다.**

| # | 보류 항목 | 결정 조건 | 담당 WP | release blocker |
|---|---|---|---|---|
|1|CPython 정확한 버전·패치·배포 digest|실제 bundle 제작·실행 시험|[#202](https://github.com/jihoon22-lee/ici/issues/202)|예|
|2|RHEL 8.10에서 runtime·네이티브 도구 호환|현장 확인|[#226](https://github.com/jihoon22-lee/ici/issues/226)|예|
|3|배포 CPU/ISA 하한|현장 CPU 확인|[#226](https://github.com/jihoon22-lee/ici/issues/226)|예|
|4|GCC/Qt/Python/pytest/coverage 지원 하한|real-tool contract 시험|[#214](https://github.com/jihoon22-lee/ici/issues/214), [#216](https://github.com/jihoon22-lee/ici/issues/216), [#217](https://github.com/jihoon22-lee/ici/issues/217)|예|
|5|TEM `branch × 1.25` 환산 계수의 근거|수식 소유자 확인 또는 재도출|[#219](https://github.com/jihoon22-lee/ici/issues/219)|아니오 (문서화로 충족 가능)|
|6|type provider 기본값 (mypy vs ty)|두 provider 비교|[#215](https://github.com/jihoon22-lee/ici/issues/215)|아니오|
|7|pytest 도구 overlay 제공 여부|격리 옵션의 실제 동작 시험|[#216](https://github.com/jihoon22-lee/ici/issues/216)|아니오|
|8|캐시 성능 예산|고정 corpus cold/warm 측정|[#209](https://github.com/jihoon22-lee/ici/issues/209), [#226](https://github.com/jihoon22-lee/ici/issues/226)|아니오|
|9|GHES/runner/action 호환표|실제 GHES 확인|[#223](https://github.com/jihoon22-lee/ici/issues/223)|예|
|10|기존 pyz 경로의 제거 시점|새 bundle이 지원표를 충족한 뒤|[#227](https://github.com/jihoon22-lee/ici/issues/227)|아니오|
