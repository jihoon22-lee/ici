# ADR-0007 — 테스트는 프로젝트의 pytest/coverage로 실행하고 ici-managed overlay는 보류한다

- 상태: **accepted** — overlay 보류는 측정 근거가 있는 결정이다.
- 결정 시점: WP01 ([#199](https://github.com/jihoon22-lee/ici/issues/199))
- 근거: [spikes/wp01-runtime-environment.md §3](../spikes/wp01-runtime-environment.md)
- 관련 요구사항: R02, R09, R10
- 담당 WP: [#216](https://github.com/jihoon22-lee/ici/issues/216)

## 결정

1. **기본 경로는 프로젝트가 준비한 pytest/coverage**를 프로젝트 인터프리터로 실행하는 것이다.
2. **ici-managed overlay(bundle의 pytest를 `PYTHONPATH`로 주입)는 채택하지 않고 보류한다.**
   전체 개발의 전제로 두지 않는다.
3. **서브프로세스 커버리지는 `COVERAGE_PROCESS_START`를 명시적으로 설정하고, 설정했다는
   사실을 결과 증거에 기록한다.** 프로젝트 site-packages에 쓰지 않는다.
4. **core 인터프리터로의 fallback은 금지하고 진단으로 바꾼다.**

## 대안

| 대안 | 기각 사유 |
|---|---|
|overlay를 기본값으로|측정으로 두 번 부정됐다 (§근거 2·3). 인터프리터 버전별로 의존이 갈리고, 프로젝트가 이미 가진 도구를 덮는다|
|overlay를 버전별로 만들어 기본값으로|대상 Python 버전마다 별도 해석·저장·검증이 필요하다. 큰 범위인데 R02가 얻는 이득은 "pytest를 프로젝트에 설치하지 않아도 된다" 하나뿐이고, 프로젝트는 어차피 자기 테스트 의존성을 설치한다. 비용이 이득을 넘는다|
|프로젝트 site-packages에 `.pth`를 써서 서브프로세스 커버리지 활성|불필요하다. coverage 7.x가 설치 시 이미 자기 `.pth`를 넣는다. 환경변수만으로 충분하고, 쓰지 않는 쪽이 R02와 신뢰 경계에 맞다|
|서브프로세스 커버리지를 포기|0/2 대 2/2 차이가 TEM 커버리지 항으로 직접 들어간다. 조용한 과소 집계를 방치하는 것은 R10 위반이다|
|core fallback 유지 (현행)|테스트가 프로젝트 런타임이 아닌 ici 런타임에서 돌아간다. [ARCH §6](../architecture.md)이 명시적으로 금지한다|

## 근거

1. **프로젝트 경로는 문제없이 동작한다.** 프로젝트 venv의 pytest 9.1.1로 스위트가 통과했다.
   기본값으로 삼는 데 추가 근거가 필요하지 않다.
2. **overlay는 인터프리터 버전별 의존 집합 때문에 깨진다.** core의 3.13으로 해석한 overlay를
   프로젝트의 3.10에 주입하면 `ModuleNotFoundError: No module named 'exceptiongroup'`이 난다.
   `BaseExceptionGroup`이 3.11+ 내장이므로 3.13 해석에는 백포트가 포함되지 않고, 3.10은 그것을
   요구한다. **하나의 overlay를 여러 프로젝트에 주입하는 그림 자체가 성립하지 않는다.**
3. **overlay는 프로젝트의 도구를 조용히 교체한다.** 프로젝트 venv에 pytest가 있어도
   `PYTHONPATH`가 site-packages보다 앞서므로 overlay 쪽이 해석된다
   (`resolved module: <overlay>/pytest/__init__.py`). 이것은 편의가 아니라 **프로젝트 테스트
   의미의 변경**이고 [SPEC-03 §7](../spec-03-analysis-engines.md)의 "기존 동작을 존중한다"와
   충돌한다.
4. **서브프로세스 커버리지 차이가 실측됐다.** 자식에서만 import되는 모듈의 문장 커버리지가
   환경변수 없이 **0/2**, 설정 시 **2/2**였다. 그리고 coverage 7.16.0이 설치 시 자기
   `a1_coverage.pth`를 넣으므로 **ici는 환경변수만 설정하면 되고 프로젝트를 수정할 필요가
   없다.** R02를 지키면서 과소 집계를 없앨 수 있다.
5. **core fallback은 다행히 조용하지 않다.** bundle의 core 런타임(3.13.7)에는 pytest가 없다.
   그래서 fallback은 성공한 척하지 않고 크게 실패한다. 이것을 진단으로 승격하기 쉬운 조건이며,
   프로젝트 런타임(3.10.20)과 버전이 달라 차이도 관측 가능하다.

## 호환 영향

| 대상 | 영향 |
|---|---|
|`engines.test.python` 설정 키|의미가 유지된다. 명시 인터프리터가 최우선이라는 점은 그대로|
|`_resolve_python`의 `.venv` 후보 탐색|유지 가능. 바뀌는 것은 **마지막 `sys.executable` fallback을 오류로 만드는 것**뿐|
|프로젝트에 pytest 설치 요구|현행과 같다. overlay를 도입하지 않으므로 새 요구가 생기지 않는다|
|커버리지 수치|`COVERAGE_PROCESS_START`를 켜면 서브프로세스를 쓰는 프로젝트의 커버리지가 **올라간다**. TEM 점수가 변할 수 있으므로 migration 표 항목이며, 변화의 방향과 이유를 결과에 기록해야 한다|
|coverage 미설치 프로젝트|`.pth`가 없으므로 서브프로세스 커버리지를 켤 수 없다. 그 경우 한계로 표시하고 통과로 숨기지 않는다|

## 복구

overlay를 보류하는 결정이므로 되돌릴 구현이 없다. 반대로 overlay가 나중에 필요해지면:

- 대상 Python 버전별로 해석한 overlay를 만들고, §근거 2의 실패가 재현되지 않음을 먼저 증명한다.
- 프로젝트가 이미 가진 도구를 덮지 않는 주입 방식을 찾거나, 덮는다는 사실을 결과에 명시한다.
- `probe-test-tools.sh`가 두 조건을 그대로 재측정하므로 판정 기준은 이미 있다.

## 보류 항목

| # | 항목 | 결정 조건 | 담당 |
|---|---|---|---|
|7|버전별 overlay의 실제 비용|필요성이 다시 제기될 때 측정|[#216](https://github.com/jihoon22-lee/ici/issues/216)|
|—|pytest plugin 자동 탐색을 공식 모드에서 격리할지|지원되는 격리 옵션의 실제 동작 시험 후|[#216](https://github.com/jihoon22-lee/ici/issues/216)|
|—|`COVERAGE_PROCESS_START`를 켰을 때의 TEM 점수 변화 폭|고정 corpus에서 구·신 비교|[#219](https://github.com/jihoon22-lee/ici/issues/219)|
