# ADR-0003 — AGENTS 불변식을 stable 경로 전용으로 범위 지정한다

- 상태: **accepted**
- 결정 시점: WP00 ([#198](https://github.com/jihoon22-lee/ici/issues/198)) 구현 순서 5
- 근거 이슈: [SPEC-05 #197 §5](https://github.com/jihoon22-lee/ici/issues/197),
  [ARCH #192 §10](https://github.com/jihoon22-lee/ici/issues/192)
- 관련 요구사항: R03, R11, R13, R14
- 개정 대상: [`AGENTS.md`](../../../../AGENTS.md)

## 결정

[`AGENTS.md`](../../../../AGENTS.md)에 **적용 범위 절을 추가**하고, 전환기의 두 경로를
명시적으로 구분한다.

|경로|의미|적용 절|
|---|---|---|
|stable|현재 릴리스되는 `dist/ici.pyz`와 그 CI·릴리스 체계|§1 §2 **§3 §4** §5 §6 §7|
|next|`docs/design/ici-next/`가 정의하는 새 배포·실행 구조|§1 §2 §5 §6 §7 **§8**|

애매한 경우는 **stable로 취급**한다(더 엄격한 §3·§4 적용).

### 범위를 지정한 불변식 4개

| 불변식 | 개정 전 | 개정 후 |
|---|---|---|
|Python 3.10 하한 (§3)|무조건 적용|**stable 경로 전용**. `requires-python`·ruff `target-version`을 next 작업 때문에 바꾸지 말라고 명시|
|순수 wheel `py3-none-any` (§3)|무조건 적용|**stable 경로 전용**. next는 bundle에 네이티브 정적 도구 동봉 가능|
|pyz 단일 파일 + polyglot 런처 (§4)|무조건 적용|**stable 경로 전용**|
|launcher ↔ `PYTHON_CANDIDATES` 순서 일치 (§4)|무조건 적용|**stable 경로 전용**. 단 `tests/test_launcher.py`는 **삭제하지 않는다**고 명시|

### 유지한 불변식

개정에서 **완화하거나 제거하지 않은** 항목:

- §1 브랜치·PR 기반 개발 (main 직접 푸시 금지, roadmap 코드를 제목으로 쓰지 않기)
- §2 Conventional Commits, CHANGELOG·README 동기화
- §3 **시스템 CA 및 stdlib 사용** — 폐쇄망 요구(R11)에서 직접 나오므로 **전 경로 적용**으로 명시
- §4 **재현성** — 전 경로 적용 원칙으로 승격. next에서는 bundle manifest의
  build input lock digest로 이어진다고 연결
- §5 코드 설계 원칙 5개 전부 (위치 추적 `InspectionTarget`, 리포터 분리, **root 권한 배제**,
  **Zero-CDN HTML**, 노이즈 최소화)
- §7 릴리스 cadence (자동 버전 상승 금지, candidate ≠ stable, PR ≠ 릴리스)

### toy release gate (§7)

삭제하지 않고 **전환 조건을 명시**했다:

1. ici-owned corpus 완성 전까지 현행 toy 검증을 릴리스 근거로 계속 쓴다.
2. [#201](https://github.com/jihoon22-lee/ici/issues/201)이 corpus를 완성하고
   [#227](https://github.com/jihoon22-lee/ici/issues/227)이 전환을 승인한 뒤에야
   toy 최신 `main` 종속 필수 게이트를 해제한다.
3. toy-projects에 ici 전용 환경파일·특수 구조를 요구하지 않는다(R13).
4. toy 제품 검증 자체는 released/candidate ici의 소비자 검증으로 유지 가능하다.

### §6 게이트에 추가한 규율

- 앞의 3개(pytest, ruff check, ruff format)는 **next 경로도 필수**.
- **실행하지 못한 게이트를 "통과"로 적지 않는다.** 사유를 PR 근거에 남긴다.
- 도구 부재 skip은 조용해서는 안 된다(`ICI_REQUIRE_BUILD_ADAPTERS=1`).

### 신설한 §8

next 경로 규약 9개: 런타임, 환경 분리, 셸 초기화 금지, 폐쇄망, 상태 축 분리, 결과 스키마,
분석 관점 보존, 측정 규율, PR 규약. 규범 원문과 충돌하면 SPEC 문서가 우선한다고 명시했다.

## 대안

| 대안 | 기각 사유 |
|---|---|
|AGENTS를 그대로 두고 next는 예외로 취급|SPEC-05 §5가 "초기 문서 PR에서 새 범위/전환 조건으로 개정한다"를 명시적으로 요구한다. 암묵적 예외는 §3·§4와 §8이 모순된 상태로 남는다|
|Python 3.10 하한을 그냥 올린다|stable 경로의 pyz가 아직 지원 배포물이다. 하한을 올리면 현재 사용자의 실행 환경이 깨진다. next bundle이 지원표를 충족하기 전에 할 수 없다|
|pyz·launcher·`test_launcher.py`를 삭제|**SPEC-05 §5와 #198이 명시적으로 금지한다** — "CI 테스트를 먼저 지우고 새 설계를 주장하지 않는다". 복구 경로도 사라진다|
|AGENTS를 두 파일로 분리 (AGENTS.md / AGENTS-next.md)|기여자가 어느 파일을 봐야 하는지 판단해야 한다. 한 파일 안의 범위 표가 판별을 더 쉽게 만든다|

## 근거

1. **#198이 명시적으로 요구한다.** 구현 순서 5: "AGENTS의 Python 3.10 하한·pure wheel·pyz 단일
   파일·launcher 동기화·toy 필수 release gate를 기존 경로 전용/next 경로 규약으로 명시 개정한다."
2. **개정하지 않으면 모순이 남는다.** ADR-0002가 전용 런타임 bundle을 채택했는데 §3이 무조건
   Python 3.10 하한과 순수 wheel을 요구하면, next 경로 작업자는 어느 쪽을 따라야 할지 알 수 없다.
3. **모든 불변식이 같은 이유에서 나온 것이 아니다.** 측정해 보면 §3의 세 항목은 근거가 다르다 —
   Python 3.10 하한과 순수 wheel은 "pyz가 시스템 인터프리터에서 돈다"는 배포 전제에서 나오고,
   시스템 CA 조항은 폐쇄망 요구(R11)에서 나온다. 전자는 전제가 바뀌면 적용되지 않지만
   **후자는 경로와 무관하게 유지된다.** 이 차이를 문서가 드러내야 한다.
4. **stable CI와 모순이 없어야 한다.** #198의 인수 기준이 요구하는 조건이다. 이번 개정은
   `.github/workflows/` 4개, `scripts/` 10개, `tests/` 기존 파일, `pyproject.toml`의
   `requires-python`·`target-version`을 **하나도 바꾸지 않았다.** 따라서 개정된 AGENTS가
   요구하는 것과 CI가 강제하는 것이 그대로 일치한다.

## 호환 영향

| 대상 | 영향 |
|---|---|
|기존 CI 워크플로|없음. 변경하지 않았다|
|기존 테스트|없음. 삭제·수정하지 않았다|
|`pyproject.toml`|없음. `requires-python = ">=3.10"`, `target-version = "py310"` 유지|
|스크립트|없음. `build-pyz.sh`·`launcher.sh`·`smoke.sh` 등 유지|
|기여자|AGENTS를 읽을 때 자기 작업의 경로를 먼저 판별해야 한다. 판별 기준 3줄이 적용 범위 절에 있다|

## 복구

이 ADR의 복구는 **문서 PR revert**다. AGENTS.md를 되돌리면 개정 전 상태로 돌아가며,
코드·CI·테스트는 애초에 건드리지 않았으므로 부수 효과가 없다.

next 경로 자체가 중단되면 §8과 적용 범위 절을 제거하고 §3·§4의 범위 표기를 되돌린다.
그때도 stable 경로 불변식은 이미 온전하므로 배포·릴리스 능력이 손상되지 않는다.

## 보류 항목

| # | 항목 | 결정 조건 | 담당 |
|---|---|---|---|
|1|Python 3.10 하한을 언제 올릴지|next bundle이 지원표를 충족하고 pyz 경로를 제거할 때|[#227](https://github.com/jihoon22-lee/ici/issues/227)|
|2|`tests/test_launcher.py`의 최종 처리|pyz 경로 제거 시점에 별도 PR로 결정|[#227](https://github.com/jihoon22-lee/ici/issues/227)|
|3|toy 종속 게이트 해제 시점|corpus 완성 + 전환 승인|[#201](https://github.com/jihoon22-lee/ici/issues/201), [#227](https://github.com/jihoon22-lee/ici/issues/227)|
