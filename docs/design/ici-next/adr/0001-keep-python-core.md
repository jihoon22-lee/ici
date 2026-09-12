# ADR-0001 — Python 코어와 Typer/Rich를 유지한다

- 상태: **accepted**
- 결정 시점: WP00 ([#198](https://github.com/jihoon22-lee/ici/issues/198))
- 근거 이슈: [ARCH #192 §1](https://github.com/jihoon22-lee/ici/issues/192),
  [PLAN #191 「결정 수준」](https://github.com/jihoon22-lee/ici/issues/191)
- 관련 요구사항: R03

## 결정

ici-next의 본체는 **Python으로 유지한다.** CLI는 Typer, 콘솔 출력은 Rich를 계속 쓴다.
모델은 dataclass·Enum·Protocol과 명시적 validation으로 표현한다.

Rust 전면 재작성, 다중 언어 코어, 언어별 독립 제품, 외부 플러그인 생태계는 **이번 범위 밖**이다.

## 대안

| 대안 | 기각 사유 |
|---|---|
|Rust 전면 재작성|현행 55,755행의 분석 자산과 2,786개 테스트를 폐기해야 한다. 마일스톤의 목표는 재작성이 아니라 중복 구현·환경 보정의 제거다|
|코어는 Rust, 분석은 Python 혼합|배포·디버깅·기여 경로가 둘로 갈라진다. 폐쇄망 배포에서 네이티브 툴체인 요구가 늘어난다|
|언어별 독립 제품 (ici-python / ici-cpp)|공통 CLI·설정·결과·품질 판정이 하나여야 한다는 R04와 충돌한다|
|argparse 등으로 CLI 교체|얻는 것이 없다. Typer/Rich는 현행 자산이고 폐쇄망 제약과 무관하다|

## 근거

1. **이관할 자산이 크다.** 측정된 현행 규모는 `src/` 55,755행, 엔진 64개 파일, core 40개 파일,
   테스트 2,786건이다. 이 중 finding/evidence 모델(`core/models.py`), 프로세스 실행기
   (`core/runner.py`), compile context(`core/context.py`), redaction(`core/redaction.py`)은
   ici-next의 설계 요구와 같은 방향이어서 그대로 이관 대상이다.
   → [inventory/current-engines.md](../inventory/current-engines.md)
2. **문제의 원인이 언어가 아니다.** 이번 조사에서 확인한 위반 지점은 NAS 경로 하드코딩,
   `.venv` 도구 탐색, `sys.executable` fallback, 개인 설정이 품질 정책을 덮는 문제다.
   모두 구조·계약의 문제이며 구현 언어를 바꿔서 해결되지 않는다.
   → [inventory/execution-flow.md §4](../inventory/execution-flow.md)
3. **폐쇄망 배포에 유리하다.** Python 코어 + 전용 런타임 압축 배포(ADR-0002)는 대상 환경에
   컴파일러나 네이티브 툴체인을 요구하지 않는다.

## 호환 영향

없다. 이 결정은 현행 유지이므로 사용자에게 보이는 변화가 없다.

## 복구

이 결정을 되돌릴 필요는 배포 시험 실패로는 발생하지 않는다.
[ARCH §1](../architecture.md)이 명시한 대로, **배포 시험이 실패하면 원인을 근거로 ADR을
수정하며 구현 언어를 자동 전환하지 않는다.**

## 보류 항목

없다.
