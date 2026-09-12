# SPEC-03 — 분석 기능의 구성과 이관

| | |
|---|---|
|상태|**채택된 목표 계약 (adopted target contract)**|
|원문 이슈|[SPEC-03 #195](https://github.com/jihoon22-lee/ici/issues/195)|
|상위|[roadmap.md](roadmap.md) / [architecture.md](architecture.md) / 설정 [spec-01](spec-01-workspace-config-cli.md) / 실행 [spec-02](spec-02-distribution-execution.md)|
|요구사항|R02, R04, R07~R10, R13~R14|
|주 담당 WP|[#215](https://github.com/jihoon22-lee/ici/issues/215)~[#220](https://github.com/jihoon22-lee/ici/issues/220)|

> **분석 관점을 줄이는 계획이 아니라 중복 구현·실행·설정을 줄이는 계약이다.**
> 현행 19개 엔진의 실측 상태는 [inventory/current-engines.md](inventory/current-engines.md)에 있다.

## 1. Check / Provider / Task

- Check는 검사 목적·언어·입력·정책을 선언한다. 예: lint, security, complexity, coverage.
- Provider는 실제 구현이다. 예: Ruff, mypy, ty, compiler, clang-tidy, clazy, ici 자체 분석.
- Task는 고정 입력·환경·인자로 provider를 한 번 수행하는 단위이다.
- 한 provider observation은 여러 check에 사용 가능하다. 동일 finding은 canonical id 하나를 갖고
  category/tag로 다중 표시한다. 같은 위치라도 서로 다른 rule/provider 결과를 근거 없이 삭제하지
  않는다.
- raw observation에는 native rule id, provider/parser 버전, source location, source scope,
  evidence/confidence, 원본 메시지와 normalization 근거를 보존한다.
- 도구가 없는 경우 parser/heuristic으로 조용히 대체해 같은 PASS를 내지 않는다. 대체 모드를 설정한
  경우 active mode/limitations와 원래 필수 검사 완료 여부를 분리한다.

Provider 계약: `supports(unit, capabilities)`, `requirements(unit)`,
`plan(resolved_inputs) -> TaskSpec[]`, `collect(task_outputs) -> Observation[]`.
실제 process·tool resolution은 공통 실행기에 맡긴다. 자체 분석도 동일 observation/result 계약을
사용하며 필요 시 worker로 격리한다.

> **현행 fallback 지점 (측정됨)**: `fallback_mode=heuristic`을 선언한 엔진이 6개 있다
> (`lint`, `compile_db`, `test`, `type`, `cycle`, `complexity`). §1의 "조용히 대체해 같은 PASS를
> 내지 않는다"를 만족하는지는 이 6개의 활성 조건과 결과 표기를 확인해야 판정할 수 있고,
> **이번 조사에서 확인하지 못했다.** → [WP21 #219](https://github.com/jihoon22-lee/ici/issues/219)

## 2. 언어 묶음과 기본 도구

- **Python pack**: 소스 입력, Ruff, 선택 type provider(mypy 기본 후보, ty 선택), Python 자체 규칙,
  pytest/coverage/compatibility.
- **C++ pack**: compilation inputs, compiler diagnostics, clang-tidy 등 지원 provider, C++ 자체
  규칙, build/test/coverage/compatibility.
- **Qt extension**: C++ pack의 capability·generated-input·Qt-specific analyzer 지원. qmake와 Qt는
  동일 개념이 아니며 CMake+Qt도 표현한다.
- **공통 primitive**: source inventory, token/AST caching interface, duplication/graph/metrics
  aggregation. 언어별 parser·semantic 차이를 보존한다.
- language pack 로드가 무관한 도구 설치/probe를 요구하지 않는다. 초기에는 내장 registry 하나와
  동일 release로 관리한다.
- type checker는 프로젝트가 하나를 선택한다. 비교 목적의 복수 선택은 명시적으로 허용하고 결과
  출처를 보존한다.

## 3. 현행 19개 descriptor의 목표 처리표

현행 registry(`src/ici/core/pipeline.py:92`)를 기준으로 누락 없이 매핑한다. 아래는 **초기
disposition**이며 개별 언어별 현행 정확도/도구/규칙은 WP inventory에서 코드·fixture 근거로
확정한다.

|현행|목표 위치|유지/정리 원칙|기본 실행 방향|
|---|---|---|---|
|line|source metrics check|규모/파일 성격별 지표 보존, 공유 소스 중복 합산 금지|fast/standard|
|lint|언어별 provider check|Ruff/C++ provider 연결, 중복 환경 탐색 제거|fast/standard, 입력이 있는 범위|
|compile_db|prepare/input validation + compilation coverage|품질 finding과 context 미완료 구분; 삭제가 아니라 계층 이동|C++ consumer 필요 시|
|test|test task + evidence collector|테스트 코드는 활용, 레거시 `make test`에 구현 위임 안 함|설정된 standard|
|type|언어별 type/correctness check|Python provider 선택, C++ 지원 범위 정확히 선언|설정된 fast/standard|
|python_compat|선택 호환성 check|실제 지원 runtime evidence와 syntax 추정 구분|프로젝트 지원 버전 설정 시|
|cognitive|maintainability metric|complexity와 parse 공유, 지표 정의는 구분|deep 또는 명시 선택|
|resource|resource ruleset|외부 규칙과 자체 규칙 비교 후 통합, 위치·증거 유지|성숙도/언어별 선택|
|security|security ruleset|Ruff 등과 중복 검토, 보안 관점 보존|성숙도/언어별 선택|
|cycle|architecture graph check|선언 scope와 graph 한계 보존|설정된 standard|
|complexity|maintainability metric|함수 경계/parse 공유, 추정값임을 표시|설정된 standard|
|sanitize|선택 동적 check|ASan/UBSan build variant·실행 증거|deep/명시 선택|
|thread_sanitize|선택 동적 check|TSan 별도 variant, 다른 sanitizer와 무리한 통합 금지|deep/명시 선택|
|dead|dead-code ruleset|전체 프로그램 미사용 확정과 휴리스틱 후보 구분|참고/선택|
|dup|duplication check|token/source 공유; 복제된 finding/coverage 집계 방지|설정된 standard|
|exception|exception ruleset|외부 중복 규칙 대체/자체 고유 규칙 보존|성숙도/언어별 선택|
|build|prepare + artifact contract check|빌드 실행과 산출물 품질 검증을 분리|consumer prerequisite/선택 계약|
|binary_compat|artifact compatibility check|정확한 artifact provenance/target ABI 요구|deep/명시 선택|
|integration|선택 실행 check|산출물·환경·외부 서비스 requirement 명시|deep/명시 선택|

coverage와 TEM은 현재 19개 descriptor의 독립 엔진이라고 가정하지 않는다. 새 구조에서는 수집
evidence에서 파생하는 check/metric으로 명시한다. 기존 명령/설정 이름의 alias·deprecation·결과
차이를 migration 표에 넣는다.

> **확인됨**: 현행 코드에서 TEM은 `test` 엔진의 `score`를 suite로 승격한 값이고
> (`engines/verify.py:330`) 독립 엔진이 아니다. coverage도 `test` 엔진이
> `coverage-report` artifact로 생산한다. 위 문단의 가정이 코드와 일치한다.

폐기/대체 조건: 같은 결함+정상 대조군에서 precision/recall/지원 범위·성능·유지 비용을 비교하고
승인한다. 도구가 유명하다는 이유나 새 구조로 옮기기 어렵다는 이유만으로 자체 기능을 삭제하지
않는다. inventory의 각 행은 최종 WP/PR/검증 근거를 가져야 한다.

→ [ADR-0004 Check/Provider 분리](adr/0004-check-provider-separation.md)

## 4. 프로필과 정책

- **fast**: 입력이 이미 있는 소스 중심 검사. 숨은 configure/build/test 실행 없음. 필요한 입력이
  없으면 해당 check blocked.
- **standard**: 프로젝트가 선택한 일상 품질 검사와 구성된 테스트/coverage/TEM. sanitizer를 무조건
  포함하지 않는다.
- **deep**: 프로젝트가 명시한 고비용/동적/호환성 검사. 존재하는 모든 engine을 강제로 켜는 모드가
  아니다.
- `enabled`(관찰 여부), `required`(게이트 필수), severity/confidence policy(위반 처리)를 별도로
  관리한다.
- 기본 init는 line/lint 중심 안전한 출발점을 제공하고 테스트 구성/필수성은 명시한다. test가
  필수인데 정의가 없으면 미완료로 처리하며 자동으로 끄지 않는다.
- raw metrics의 전체 기준값을 이 리팩토링에서 임의 변경하지 않는다. 신규/기존 부채 정책과 참고
  지표를 구분한다.

> **확인됨**: 현행 `fast` 프로필은 `exec=build` 엔진(`test`/`sanitize`/`thread_sanitize`/`build`/
> `integration`)을 하나도 포함하지 않는다. §4의 fast 정의와 이미 일치한다.
> 다만 `fast`에 포함된 엔진 중 8개가 외부 프로세스를 실행하므로(compile probe 등),
> "숨은 configure/build 없음"의 정확한 경계는 [WP12 #210](https://github.com/jihoon22-lee/ici/issues/210)이
> plan 출력으로 드러내야 한다.

## 5. Python 정적 검사

Ruff는 bundle/external provider로 실행하고 기존 `pyproject`/`ruff` config의 탐색을 존중한다.
style/security/exception 분류를 위해 동일 argv 실행을 반복하지 않는다. format 검사는
format-check이지 자동 수정이 아니다. 일반 verify에서 source를 고치지 않는다.

mypy/ty는 선택 provider를 기록한다. 분석기 runtime과 대상 Python·language version·package/stub
discovery를 분리한다. custom type plugin이 별도 module을 요구하면 그 provider 환경/지원 한계를
명시한다. 프로젝트 전체 dependency를 core 환경으로 무작정 복사하지 않는다. JSON 등 안정 출력이
있는 버전은 우선 사용하고, 텍스트 parser는 버전별 fixture·malformed-output 검증을 둔다.

ici 자체 AST 분석이 대상 문법을 읽지 못하면 parser failure/지원 한계를 보고한다. 새로운 ici
Python이 모든 과거/미래 문법을 자동 지원한다고 가정하지 않는다.

> **관련 현행 사례**: `tests/test_python_compat.py:157`이
> `host AST cannot parse except-star syntax` 사유로 skip한다. 즉 "ici가 돌아가는 Python의 AST가
> 대상 문법을 못 읽는" 상황이 이미 실제로 발생하고 있고, 올바르게 skip으로 처리된다.
> 이는 §5 마지막 문단의 요구가 현행에서 부분적으로 지켜지고 있음을 보여준다.
> → [inventory/baseline-measurements.md §3.3](inventory/baseline-measurements.md)

## 6. C++/Qt 입력과 provider

- 기존 valid compile DB → 선언된 수집 prepare 순서. `.pro`/`.pri`의 qmake 의미를 자체 범용 파서로
  재구현하지 않는다.
- qmake SUBDIRS의 `.subdir`/`.file`/`.depends`와 실제 build outputs를 연결한다. shared build와
  component scope를 분리한다.
- compile entry의 cwd/argv/compiler/macros/includes/standard/response files/variant/generated
  paths를 보존한다.
- compiler wrapper/ccache 등 prefix가 있으면 원본 실행 체인과 실제 compiler identity를 구분한다.
- Clang 계열 provider용 flag 변환은 whitelist와 변환 로그를 가진다. GCC-specific flag를 무조건
  삭제해 분석 성공만 만들지 않는다.
- moc/uic/rcc 등 생성 입력, project 외부 헤더/sysroot, 여러 Qt 설치를 시험한다.
  unavailable header/unsupported dialect/TU 누락을 코드 위반 없음으로 처리하지 않는다.
- 파일별/variant별 compilation coverage를 제공한다. shared header finding의 component/variant
  관계를 보존한다.

> **미검증**: qmake·Qt6·clazy가 이번 측정 환경에 없어 이 절의 어떤 항목도 실제 도구로
> 확인하지 못했다. → [inventory/baseline-measurements.md §4](inventory/baseline-measurements.md)

## 7. 테스트·coverage·TEM

### Python

기본은 준비된 프로젝트 Python의 pytest/coverage를 사용한다. ici core/Ruff/type checker를 project
venv에 설치하도록 요구하지 않는다. 정확한 Python 실행 경로와 module 버전/플러그인 선택을
기록한다. pytest plugin 자동 탐색을 무조건 끄지 않으며 기존 동작을 존중하고, 격리가 필요한 공식
모드는 명시 policy와 tested option으로 제공한다.

### C++

정의된 build/test executable·test suite를 ici가 실행하고 결과를 수집한다. test executable 자체는
기존 코드를 활용한다. 레거시 `make lint/test/verify/cov`를 새 검사 구현으로 감싸는 것은 기본
경로가 아니다. build를 위한 make 호출은 별도 허용된다. 현장의 `make cov`(Coverity)를 coverage로
오해하지 않는다.

### 공통 evidence

`TestRun`에는 suite/case outcome, expected count, exit, runtime, source/variant identity가
포함된다. 0개 수집·수집 오류·all skipped·expected fail·timeout은 구별한다. required suite의 결과
누락은 INCOMPLETE. 측정 가능한 테스트 assertion failure는 코드 품질 FAIL 근거다.

coverage는 executed/total raw count, line/branch 종류, source scope, instrumented artifact와
compiler/gcov 또는 runtime identity를 기록한다. C++는 instrumented compiler와 coverage reader의
호환을 확인한다. 외부 coverage 결과 import는 명시 run/source/variant provenance와 schema를
검증한다. timestamp만으로 신선함을 판정하지 않는다.

동일 조건이면 테스트를 coverage와 함께 1회 실행하고 test/coverage/TEM이 그 evidence를 공유한다.
별도 instrumented variant가 필요한 경우 독립 실행으로 계획한다. component coverage 백분율을 단순
평균하지 않고 병합 가능한 원자료만 합친다. 중복 소스/이질적인 branch metric은 별도로 표시한다.

### TEM 수식 고정

TEM의 현행 수식·가중치·단위·적용 조건을 inventory에서 추출하여 `formula_version`으로 고정한다.
**이 문서에서 새 수식을 발명하지 않는다.** 누락된 값을 0/100으로 채우거나 TEM 점수로 실패한 필수
테스트를 상쇄하지 않는다.

추출된 현행 수식 원문과 결측 처리는
[inventory/current-engines.md §5](inventory/current-engines.md)에 있다. 요약:

```
tem_score = clamp(round(cov_factor * (func_cov/100) * pass_rate * 5.0, 2), 0, 5)
```

고정해야 할 사실 3가지:

1. 곱셈 3항 구조 — 한 항이 0이면 TEM 0.
2. 커버리지 항만 80%에서 포화(`min(80,x)/80`)하고 `func_cov`는 선형(`/100`) — 스케일 기준이 다르다.
3. branch만 있을 때 `* 1.25` 환산 — **근거가 코드·주석·문서 어디에도 없다.**
   → [ADR-0005](adr/0005-tem-formula-freeze.md)의 보류 항목

## 8. 인수 및 비교

- [ ] 19개 descriptor에 지원 언어·provider·입력·결과·profile·최종 disposition·migration·fixture가
      매핑된다. (현재: 지원 언어·profile·입력·출력·잠정 disposition·fixture까지 매핑됨.
      최종 disposition과 migration은 각 WP가 확정 →
      [inventory/current-engines.md §7](inventory/current-engines.md))
- [ ] Python/C++ 선택 시 무관한 provider 초기화/설치가 없다.
- [ ] 동일 provider의 여러 관점은 1회 실행·1개 canonical finding이고 출처를 보존한다.
- [ ] 실제 결함과 정상 대조군으로 오탐/미탐·parser failure·지원 한계를 검증한다.
- [ ] test/coverage/TEM 공유 증거와 incomplete 처리, instrumentation variant를 실제 도구로
      시험한다.
- [ ] 휴리스틱 규칙을 exact gate로 잘못 승격하지 않는다.
      (현행 `heuristic` 모드 엔진 목록 →
      [inventory/current-engines.md §2](inventory/current-engines.md))

공식 참고: [Ruff 규칙](https://docs.astral.sh/ruff/rules/),
[pytest plugin 동작](https://docs.pytest.org/en/stable/how-to/plugins.html),
[qmake 변수/SUBDIRS](https://doc.qt.io/qt-6/qmake-variable-reference.html),
[Clang compile DB](https://clang.llvm.org/docs/JSONCompilationDatabase.html).
