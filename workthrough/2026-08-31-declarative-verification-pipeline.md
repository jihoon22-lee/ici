# I2-3 선언형 verification pipeline

## Overview

검증 엔진의 hardcoded 순차 loop를 immutable descriptor registry와 DAG executor로 전환한
I2-3 구현을 문서화한다. 엔진 실행 범위, artifact 계약, profile 선택, read-only 병렬성과
build 직렬화를 하나의 실행 계약으로 고정하고, 결과 순서와 예외 격리를 명시했다.

## Context

기존 오케스트레이터는 엔진을 고정된 순서로 호출했기 때문에 엔진 간 의존성·산출물 소유권과
실행 profile을 코드의 분기에서 추론해야 했다. build/test/sanitize처럼 mutable shadow tree를
사용하는 엔진과 read-only 엔진을 구분하지 않으면 병렬화 시 결과와 산출물이 서로 영향을 줄
수 있었다. I2-3은 이 경계를 descriptor와 startup validation으로 드러내는 단계다.

## Changes Made

### 1. Declarative engine descriptors

파일: `src/ici/core/pipeline.py`

- `EngineDescriptor`가 `name`, `dependencies`, `produces`, `consumes`, `profiles`,
  `execution`, `build_variant`를 frozen 값으로 보유한다.
- registry validation이 중복 이름·artifact producer, unknown dependency, profile closure,
  소비 artifact의 producer 연결, cycle을 분석 시작 전에 거부한다.
- `AnalysisProfile`은 `fast`, `standard`, `deep`을 제공한다. profile은 engine selection만
  바꾸며 rule threshold나 판정 의미를 변경하지 않는다.

### 2. Bounded DAG execution

- topological layer를 registry 순서로 계산한다.
- 독립 read-only node는 기본 최대 4개 worker로 실행한다.
- build node는 read-only 작업 및 다른 build node와 overlap하지 않도록 직렬 실행한다.
- future 완료 시점과 무관하게 최종 결과를 descriptor registry 순서로 반환한다.
- 초기화 또는 실행 예외는 해당 engine의 `ERROR`/`NOT_RUN` 결과로 격리한다.

### 3. CLI, context, and JSON contract

- `ici verify --profile fast|standard|deep`와 `[ici].profile` 설정을 제공한다.
- 선택된 profile을 immutable `AnalysisContext`에 기록한다.
- `ici.result/v3`의 `analysis_context.profile`은 optional이며, context 또는 profile이 없는
  기존 archive와의 읽기 호환성을 유지한다.

### 4. Documentation synchronized

- `CHANGELOG.md`: I2-3 범위와 남은 I2-4를 기록했다.
- `docs/architecture.md`: descriptor, validation, scheduler와 context 계약을 갱신했다.
- `docs/user-guide.md`: profile 선택과 실행 semantics를 추가했다.
- master plan과 handover: I2-3 완료 범위 및 I2-4 잔여 작업을 갱신했다.

## Code Examples

### Build-owning descriptor

~~~python
# src/ici/core/pipeline.py
EngineDescriptor(
    name="test",
    factory_name="TestEngine",
    produces=("findings:test", "test-results", "coverage-report", "build:coverage"),
    consumes=("analysis-context", "capability-inventory"),
    profiles=_STANDARD_DEEP,
    execution=EngineExecution.BUILD,
    build_variant=BuildVariant.COVERAGE,
)
~~~

### Profile selection

~~~bash
ici verify --profile fast
ici verify --profile deep
~~~

## Verification Results

- Python 3.10 전체 회귀: `898 passed`
- Ruff check/format: 전 범위 통과
- packaged `ici.pyz --profile fast`: 10 engines, 6 PASS/4 WARN, 0 FAIL/ERROR,
  약 4초, requested build variant 0개
- packaged `ici.pyz --profile deep`: 13 engines, 8 PASS/5 WARN, 0 FAIL/ERROR,
  cognitive 포함, coverage/sanitize variant, TEM 4.84
- fast/deep HTML: external script/stylesheet reference 0개
- 문서: `git diff --check`, fenced code block, I2-3/I2-4 용어 일치 확인

최종 reproducible pyz·smoke와 원격 CI/PR/Pages 증거는 branch의 Merge Gate에서 확정한다.

## Next Steps

- I2-4 cache key, invalidation, failure reuse 방지, cache hit report와 reproducible build 계약을
  구현한다.
- I2-3 branch의 full-suite CI와 PR Merge Gate 결과를 handover/CHANGELOG에 추가한다.
