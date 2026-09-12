# R01~R15 추적표

| | |
|---|---|
|상태|**골격 완성 (WP00)**. `evidence` 열은 각 WP가 실제 근거로 채운다.|
|근거 이슈|[WP00 #198](https://github.com/jihoon22-lee/ici/issues/198) 구현 순서 6|
|요구사항 원문|[roadmap.md](roadmap.md) 「추적 가능한 요구사항」|

이 표는 `요구사항 → SPEC → WP → 테스트/PR → evidence`를 연결한다.
**빈 evidence 칸은 "아직 근거가 없다"는 뜻이며 채워진 것처럼 취급하지 않는다.**

## 추적표

|ID|SPEC|주 담당 WP|현행 상태 (WP00 측정)|테스트/근거|
|---|---|---|---|---|
|R01 셸 초기화 파일 탐색·source 금지|[architecture §1·§6](architecture.md), [spec-02 §2](spec-02-distribution-execution.md)|[#199](https://github.com/jihoon22-lee/ici/issues/199), [#204](https://github.com/jihoon22-lee/ici/issues/204)|**부분 위반**: `devenv.csh` 해석은 없으나 NAS 경로 하드코딩(`core/env.py:83`)과 `nas_shared` 상위 탐색(`:21`)이 있다|[execution-flow §4](inventory/execution-flow.md) 4·5·8행|
|R02 반복 설치·경로 보정 축소|[spec-02 §3](spec-02-distribution-execution.md), [spec-03 §7](spec-03-analysis-engines.md)|[#202](https://github.com/jihoon22-lee/ici/issues/202), [#204](https://github.com/jihoon22-lee/ici/issues/204), [#215](https://github.com/jihoon22-lee/ici/issues/215)|**위반 지점 확인**: ruff·mypy를 프로젝트 `.venv`에서 탐색(`lint.py:529`, `type_check.py:155`)|[execution-flow §4](inventory/execution-flow.md) 2·3행|
|R03 Python 코어 + 전용 런타임 압축 배포|[architecture §1](architecture.md), [spec-02 §1](spec-02-distribution-execution.md)|[#202](https://github.com/jihoon22-lee/ici/issues/202)|현행은 `dist/ici.pyz` + 시스템 인터프리터 탐색. bundle 미제작|[ADR-0002](adr/0002-standalone-runtime-bundle.md), [baseline §3.2](inventory/baseline-measurements.md)|
|R04 Python/C++ 묶음 분리, 공통 CLI/결과|[architecture §7](architecture.md), [spec-03 §2](spec-03-analysis-engines.md)|[#208](https://github.com/jihoon22-lee/ici/issues/208), [#215](https://github.com/jihoon22-lee/ici/issues/215)~[#217](https://github.com/jihoon22-lee/ici/issues/217)|언어별 `AnalysisMode` 선언은 이미 존재(19×2행). 패키지 분리는 없음|[current-engines §2](inventory/current-engines.md)|
|R05 부분 실행을 전체 통과로 표시 금지|[spec-01 §6](spec-01-workspace-config-cli.md), [spec-04 §2](spec-04-results-integration.md)|[#210](https://github.com/jihoon22-lee/ici/issues/210), [#219](https://github.com/jihoon22-lee/ici/issues/219)|`--python`/`--cpp`/`--component` 옵션 자체가 없어 부분 실행 개념 미존재. scope 축 없음|[execution-flow §1](inventory/execution-flow.md)|
|R06 루트 `ici.toml` 하나 기본, 기존 도구 설정 존중|[spec-01 §2·§3·§7](spec-01-workspace-config-cli.md)|[#203](https://github.com/jihoon22-lee/ici/issues/203)|**충돌 확인**: XDG 전역·`dev.toml`·`ICI_CONFIG`가 품질 정책을 덮을 수 있다|[execution-flow §3](inventory/execution-flow.md)|
|R07 workspace/component/analysis unit/build unit 구분|[architecture §4](architecture.md), [spec-01 §1](spec-01-workspace-config-cli.md)|[#207](https://github.com/jihoon22-lee/ici/issues/207), [#211](https://github.com/jihoon22-lee/ici/issues/211)~[#213](https://github.com/jihoon22-lee/ici/issues/213)|`ProjectModel` 하나가 네 역할을 겸함(`core/context.py:113`)|[architecture §4](architecture.md) 주석|
|R08 check와 provider 분리, 실행 공유|[spec-03 §1](spec-03-analysis-engines.md), [spec-02 §4](spec-02-distribution-execution.md)|[#208](https://github.com/jihoon22-lee/ici/issues/208), [#218](https://github.com/jihoon22-lee/ici/issues/218)|DAG 노드가 엔진 단위. 프로세스 공유 키 없음. read-only 엔진 8개가 실제로 프로세스 실행|[current-engines §4](inventory/current-engines.md), [ADR-0004](adr/0004-check-provider-separation.md)|
|R09 19개 descriptor 근거 있는 disposition|[spec-03 §3](spec-03-analysis-engines.md)|[#218](https://github.com/jihoon22-lee/ici/issues/218), [#220](https://github.com/jihoon22-lee/ici/issues/220)|**잠정 disposition 19/19 완성**. 최종 근거(precision/recall)는 미측정|[current-engines §7·§8](inventory/current-engines.md)|
|R10 수행 상태·신뢰도·판정·게시 상태 분리|[spec-04 §2](spec-04-results-integration.md)|[#200](https://github.com/jihoon22-lee/ici/issues/200), [#219](https://github.com/jihoon22-lee/ici/issues/219)|**부분 충족**: `EvidenceState` 4값과 `NOT_APPLICABLE`/`NOT_RUN` 구분이 이미 판정에 반영됨. Gate의 `INCOMPLETE`와 selected/workspace 이원화는 없음|[execution-flow §5](inventory/execution-flow.md), [spec-04 §2](spec-04-results-integration.md) 주석|
|R11 폐쇄망: 다운로드·pip·CDN 요구 금지|[spec-02 §1](spec-02-distribution-execution.md), [spec-05 §7](spec-05-verification-transition.md)|[#202](https://github.com/jihoon22-lee/ici/issues/202), [#226](https://github.com/jihoon22-lee/ici/issues/226)|AGENTS가 시스템 CA 존중·root 배제·Zero-CDN HTML을 이미 불변식으로 강제. 오프라인 실행 시험은 **미수행**|[AGENTS.md](../../../AGENTS.md) §3·§5, [baseline §4](inventory/baseline-measurements.md)|
|R12 idk와 CLI·JSON·JSONL·취소 계약|[spec-04 §6](spec-04-results-integration.md)|[#224](https://github.com/jihoon22-lee/ici/issues/224)|이벤트 스트림 전체가 신규. ici가 idk에 의존하지 않는 상태는 유지 중|[spec-04 §6](spec-04-results-integration.md) 주석|
|R13 toy 특수 구조 요구 금지, corpus는 ici 소유|[spec-05 §3](spec-05-verification-transition.md)|[#201](https://github.com/jihoon22-lee/ici/issues/201), [#227](https://github.com/jihoon22-lee/ici/issues/227)|ici 소유 corpus 없음. 현행 fixture 10개에 manifest 없음|[baseline §5](inventory/baseline-measurements.md) 1·2행|
|R14 단계적 전환·호환성·복구 경로|[spec-05 §5](spec-05-verification-transition.md)|[#225](https://github.com/jihoon22-lee/ici/issues/225), [#227](https://github.com/jihoon22-lee/ici/issues/227)|**migration 필수 항목 7개 식별 완료**. AGENTS 개정 완료|[spec-05 §5](spec-05-verification-transition.md) migration 표, [ADR-0003](adr/0003-agents-invariant-scoping.md)|
|R15 사내 소스·로그 외부 반출 요구 금지|[spec-05 §1·§7](spec-05-verification-transition.md)|[#226](https://github.com/jihoon22-lee/ici/issues/226)|`core/redaction.py`가 민감 값을 제거. 이번 inventory에 사내 경로·로그를 포함하지 않았다|[spec-04 §5](spec-04-results-integration.md) 주석|

## WP00에서 확인한 요구사항별 위반·충족 요약

| 구분 | 요구사항 |
|---|---|
|**현행이 이미 충족하거나 같은 방향** (보존 대상)|R11 (시스템 CA·root 배제·Zero-CDN), R10 부분 (Evidence 4축), R15 (redaction), R12 부분 (idk 비의존)|
|**현행에 명확한 위반 지점이 있음** (수정 대상)|R01 (NAS 경로 하드코딩), R02 (`.venv` 도구 탐색), R06 (개인 설정이 정책 덮음)|
|**현행에 개념 자체가 없음** (신규 구현)|R05 (부분 실행 scope), R07 (component/analysis unit), R08 (check/provider), R12 (이벤트 스트림)|
|**측정이 필요한데 아직 못 함**|R03 (bundle), R09 최종 근거, R13 (corpus), R14 (실제 전환 검증)|

## 이 표를 갱신하는 규칙

1. `현행 상태` 열은 **측정된 사실만** 적는다. 추정은 "미확인"으로 남긴다.
2. `테스트/근거` 열에는 실제 파일·테스트·PR 링크만 넣는다. 이슈 링크만으로는 evidence가 아니다.
3. WP가 완료되면 해당 행의 `현행 상태`를 갱신하고 evidence를 추가한다. 행을 지우지 않는다.
4. 한 요구사항이 "충족"으로 바뀌는 조건은 담당 WP의 인수 기준이 정한다. 이 표가 단독으로
   충족을 선언하지 않는다.
