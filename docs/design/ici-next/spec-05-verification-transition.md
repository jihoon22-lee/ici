# SPEC-05 — 검증 가능한 전환

| | |
|---|---|
|상태|**채택된 목표 계약 (adopted target contract)**|
|원문 이슈|[SPEC-05 #197](https://github.com/jihoon22-lee/ici/issues/197)|
|상위|[roadmap.md](roadmap.md) / [architecture.md](architecture.md) / 계약 [spec-01](spec-01-workspace-config-cli.md) [spec-02](spec-02-distribution-execution.md) [spec-03](spec-03-analysis-engines.md) [spec-04](spec-04-results-integration.md)|
|요구사항|R01~R15 전체|
|주 담당 WP|[#201](https://github.com/jihoon22-lee/ici/issues/201) (corpus), [#225](https://github.com/jihoon22-lee/ici/issues/225)~[#227](https://github.com/jihoon22-lee/ici/issues/227)|

> **테스트를 많이 통과하는 것과 실제 지원 환경에서 쓸 수 있다는 것을 구분한다.**
> 이번 WP00 측정이 무엇을 확인했고 무엇을 확인하지 못했는지는
> [inventory/baseline-measurements.md](inventory/baseline-measurements.md)에 있다.

## 1. 지원표와 증거 수준

|축|초기 대상/결정|등록 시 상태|
|---|---|---|
|사내 OS|RHEL 8.10|사용자 환경, 이번 작업에서 실행 검증하지 않음|
|배포 CPU|Linux x86_64 보수적 ISA 우선 후보|현장 CPU/ABI와 포함 tool별 조건 확인 필요|
|개발 OS|WSL2/Ubuntu 개발 경로 유지|RHEL 호환 증거의 대체가 아님|
|ici core|독립 CPython 일반 빌드, 3.13/PBS 시험 후보|패치/배포 digest는 spike 후 고정|
|프로젝트 Python|실제 프로젝트 runtime과 상속 site-packages/외부 venv|버전 하한을 추정하지 않고 현장·공개 fixture별 지원표 작성|
|테스트 도구|project pytest/coverage 기본, 명시 선택|버전별 기능/플러그인/출력 parser 실제 시험|
|C++/Qt|프로젝트 GCC/qmake/Qt와 여러 설치 경로·SUBDIRS|Qt/GCC 하한과 analyzer 조합은 시험으로 결정|
|GHES|실제 GHES/runner/action/artifact 조합|실제 버전·정책 현장 확인 전 미확인|
|idk|CLI/result/event consumer 계약|ici 쪽 fixture로 검증, idk 실제 UI 변경은 별도 범위|

각 행에는 `planned`/`tested`/`supported`/`limited`/`unsupported` 상태, exact version/digest,
command, fixture, result, limitation, evidence 위치를 기록한다. tested 최소 조합 없이 광범위 지원을
광고하지 않는다. 사내 실제 버전 확인 때문에 공개 fixture 기반 구현 전체를 멈출 필요는 없지만 최종
supported 선언에는 근거가 필요하다.

> **실측 상태** — WP00이 첫 데이터를, WP01이 런타임·환경·테스트 도구 축을 채웠다. 근거는
> [inventory/baseline-measurements.md §4](inventory/baseline-measurements.md)와
> [spikes/wp01-runtime-environment.md §5](spikes/wp01-runtime-environment.md).
>
> |축|WP00 상태|WP01 이후|
> |---|---|---|
> |Python 정적 검사 (ruff/pytest)|`tested` (개발 컨테이너 한정)|`tested`|
> |**독립 런타임이 core를 실행**|`planned`|**`tested`** — PBS CPython 3.13.7|
> |**런타임 이동·공백 경로·clean HOME**|`planned`|**`tested`**|
> |**read-only 설치 디렉터리**|`planned`|**`tested`** (실제 ro 바인드 마운트)|
> |**core / project 양방향 분리**|`planned`|**`tested`**|
> |**glibc 하한**|추정 금지|**2.17 실측**|
> |**프로젝트 pytest/coverage 경로**|`planned`|**`tested`**|
> |**ici-managed overlay**|`planned`|**`unsupported` (보류)** — 구조적 실패|
> |**compile DB 컴파일러 준수**|미인식|**`tested`** (이미 충족)|
> |C++ CMake 경로|`limited` — cmake/ctest/gcov는 있으나 Qt6 부재로 fixture E2E 미완주|`limited`|
> |C++ qmake 경로|`untested` — qmake 부재|`untested`|
> |Qt 지원|`untested` — Qt6 부재|`untested`|
> |clazy provider|`untested` — clazy 부재|`untested`|
> |pyz 패키징·재현성|`untested` — uv 버전 고정(0.12.5) 불일치로 미실행|`untested`|
> |CPU/ISA 하한|`untested`|`untested` — 정적 판정 실패|
> |RHEL 8.10|`untested`|`untested`|
> |GHES|`untested`|`untested`|
> |오프라인 실행|`untested`|`untested` — proxy 제거는 계약 확인일 뿐|
>
> WP01의 전체 측정 기록: [spikes/wp01-runtime-environment.md](spikes/wp01-runtime-environment.md)

## 2. 검증 계층

1. **순수 단위**: config precedence, path/source scope, graph/DAG, identity,
   finding normalization, policy truth table.
2. **process contract**: 가짜 실행파일로 argv/cwd/env/호출횟수/실패/timeout/취소를 결정적으로
   확인한다.
3. **real-tool contract**: 고정 tool 버전의 작은 정상/결함 fixture와 expected diagnostic/exit/
   parser 경계를 확인한다.
4. **bundle E2E**: 제작한 압축물 자체로 init→plan→verify→JSON/HTML, clean HOME/offline/
   read-only install을 확인한다.
5. **differential**: 기존 stable/기준 SHA와 새 경로를 같은 입력으로 비교한다.
6. **현장 인수**: 실제 폐쇄망 내부에서 지원표·설정·환경·결과 상태만 확인한다. 사내 코드와 원본
   로그를 외부 전송할 것을 요구하지 않는다.

각 계층은 역할이 다르다. mock 통과를 compiler/Qt 실제 검증으로, Ubuntu container 통과를 실제 RHEL
실행으로, fixture consumer를 idk UI 통합으로 표기하지 않는다.

> **WP00의 실증 사례**: 이번 측정에서 `uv run --python 3.10 pytest`가 2779건 통과했는데, 이는
> 대부분 1·2계층이다. 동시에 3계층(real-tool contract)에서 Qt6 부재가 **skip이 아니라 fail로**
> 보고되는 가드 결함이 드러났다. 계층을 섞어 보고하면 이런 결함이 "테스트 2건 실패"로만 보이고
> 원인이 가려진다. → [inventory/baseline-measurements.md §3.1](inventory/baseline-measurements.md)

## 3. ici 소유 corpus

권장 신규 위치 `tests/fixtures/ici-next/`, 실제 tool harness `tests/integration/ici_next/`;
기존 테스트 구조를 조사한 후 중복을 피한다. fixture manifest는 목적·지원 language/tool·입력·예상
finding/위치/완료 상태·expected failure·실행 비용·안전 조건을 가진다.

필수군:

- Python 단일·여러 component·동일 디렉터리 혼합 소스, 활성 Python이 root `.venv`와 다른 경우,
  공용 site-packages 상속·외부 venv·symlink interpreter.
- 여러 PATH GCC/qmake/Qt, `.pro`와 별도 build dir, SUBDIRS의 복수 계층·의존·동일 소스 다중 target,
  생성 헤더/moc/uic, 외부 공용 include/sysroot.
- CMake compile DB와 명시 Make build, 기존 DB 없는 경우, 오래된/일부/잘못된 DB,
  response file·wrapper command.
- Python 테스트가 C++ 산출물에 의존, 언어 selection과 prepare 분리, 같은 build 공유·다른 variant
  분리.
- root-only 설정과 명시 child 설정의 동등성, nested workspace, 개인 설정 영향, 정책 완화·scope
  축소.
- missing tool/unsupported version/malformed output/huge output/timeout/signal/0 tests/
  all skipped/stale coverage/cancelled build.
- 실제 결함과 정상 대조군, heuristic 한계를 드러내는 사례, 중복 provider 실행 counter.
- 결과/HTML/publisher의 악성 문자열·경로 이탈·비정상 artifact·stale PR head·부분 baseline.

환경 테스트는 harness가 env와 실행파일 배치를 만들어 수행한다. fixture에 `devenv.csh`라는 제품
요구를 넣지 않는다. toy-projects에 테스트 편의를 위한 환경파일·폴더 구조 변경을 요구하지 않는다.

Quality Zoo/기존 examples를 재사용할 경우 코드·라이선스·목적·expected output을 검토하고
ici-owned fixture로 옮기거나 고정 commit snapshot으로 사용한다. toy latest main과 동시 변경해야
ici가 통과하는 gate는 전환한다. 실제 toy 제품 검증은 released/candidate ici의 명시 소비자로 유지할
수 있다.

> **현행 corpus 상태 (측정됨)**: ici 소유 corpus는 아직 없다. 현행 fixture는
> `examples/cpp-fixtures/` 아래 **10개**(`asan_overflow`, `clean_baseline`, `clone_pair`,
> `cmake_elf_dead`, `cmake_project`, `complexity_hot`, `cycle_pair`, `dtor_throw`,
> `oversized_file`, `qmake_project`)이고 **fixture manifest가 없다.** 목적·요구 도구·예상 finding·
> 실행 비용 선언이 없어서, 위 §3의 "missing tool" 군이 제대로 표현되지 않는다.
> 그 결과가 §2에서 언급한 Qt6 가드 결함이다.
> → [WP03 #201](https://github.com/jihoon22-lee/ici/issues/201)

## 4. 구·신 비교 규칙

baseline 실행은 exact source/config/tool/command를 기록한다. 비교 대상은 검사 scope,
native/canonical finding, 위치/심각도/신뢰도, metrics 원자료, evidence/completeness, verdict이다.
시간·임시경로·출력 순서 차이는 정규화한다.

각 차이는 `의도한 구조 수정`/`도구 버전 변경`/`규칙 변경`/`범위 변경`/`회귀`/`미판정`으로
분류한다. 사라진 finding과 NOT_RUN 전환에는 이유가 필요하다. expected 값을 일괄 재생성해 회귀를
지우거나 줄 수 gate를 맞추려고 문서를 압축하지 않는다. 분석 관점은 보존하고 자체 규칙을 대체할
때는 정상 대조군과 결함 corpus 비교를 요구한다.

> WP00 기준선: 비교의 기준점은 `20c417cc8ec84aa490d0138782bf9fe38374fb5d`이고, 그 시점의 게이트
> 실행 결과는 [inventory/baseline-measurements.md §3](inventory/baseline-measurements.md)에
> 기록되어 있다. differential 비교(계층 5)를 할 때 이 기록이 "구" 쪽 데이터다.

## 5. 이전 전략

- 안정 배포물과 기존 JSON/config fixture를 고정 보존한다. 새 코드는 개발 중 명시 next 경로에서
  실행하고 작은 vertical slice부터 이관한다.
- old config 읽기 → 명시 migration preview → 새 파일 출력. 원본 보존, backup/diff 제공, 실행 중
  자동 overwrite 금지.
- old command/profile/exit/result/schema/viewer 호환 매트릭스를 작성한다. heuristic 또는 삭제된
  규칙은 대체/제약/사용자 조치를 설명한다.
- 기존 AGENTS의 3.10·pure wheel·pyz·toy release invariant는 초기 문서 PR에서 새 범위/전환 조건으로
  개정한다. CI 테스트를 먼저 지우고 새 설계를 주장하지 않는다.
- 단계 완료 후 새 core를 기본으로 바꾸되 구·신 엔진 두 벌을 영구 유지하지 않는다. 제거는 사용
  경로·호환 reader·문서·rollback 확인 후 별도 PR로 한다.

> **AGENTS 개정 완료 (WP00)**: 4번째 항목을 이 PR에서 수행했다.
> [AGENTS.md](../../../AGENTS.md) §3·§4가 "현행 stable 경로 전용"으로 범위가 명시되고, §8에
> next 경로 규약이 추가됐다. **CI 워크플로·테스트·스크립트는 하나도 삭제하거나 완화하지 않았다.**
> 개정 전후 대조 → [ADR-0003](adr/0003-agents-invariant-scoping.md)

### migration 표에 반드시 들어가야 하는 항목 (WP00·WP01에서 식별됨)

| 항목 | 현행 | 목표 | 담당 |
|---|---|---|---|
|exit 2의 의미|설정 오류 + 엔진 SKIP 겸용|설정 오류 전용, 미완료는 3|[#225](https://github.com/jihoon22-lee/ici/issues/225)|
|exit 3 / 130|없음|미완료 / 취소|[#225](https://github.com/jihoon22-lee/ici/issues/225)|
|엔진 단독 서브커맨드 15개|`ici line`, `ici lint`, … 존재|목표 CLI에 대응 항목 없음 → alias/deprecation 결정 필요|[#225](https://github.com/jihoon22-lee/ici/issues/225)|
|`ICI_CONFIG` / `dev.toml` / XDG 전역|품질 정책 전체를 덮을 수 있음|개인 설정은 표시/편집기 기본값만|[#203](https://github.com/jihoon22-lee/ici/issues/203)|
|결과 파일 위치|cwd의 `verify_report.{json,html}`|`.ici/runs/<run_id>/`|[#224](https://github.com/jihoon22-lee/ici/issues/224)|
|결과 schema|finding v3|`ici.next.run` v1 (별도 식별자)|[#200](https://github.com/jihoon22-lee/ici/issues/200)|
|TEM 수식|`coverage_support.calculate_tem`|동일 수식 + `formula_version` 고정|[#219](https://github.com/jihoon22-lee/ici/issues/219)|
|`schema_version` 키|**현행 검증기가 거부한다** (`unknown configuration key`)|SPEC-01의 버전 있는 TOML. drop-in이 아니라 migration 필요|[#203](https://github.com/jihoon22-lee/ici/issues/203), [#225](https://github.com/jihoon22-lee/ici/issues/225)|
|서브프로세스 커버리지|`COVERAGE_PROCESS_START` 미설정 → 자식 코드가 미커버로 집계|명시 설정. **커버리지가 올라가므로 TEM 점수가 변한다**|[#216](https://github.com/jihoon22-lee/ici/issues/216), [#219](https://github.com/jihoon22-lee/ici/issues/219)|

## 6. 성능과 자원

고정 소/중/대 합성 corpus에서 cold/warm wall time, peak RSS, 실행 provider 수, build 횟수,
cache size, HTML 크기, 취소 지연을 기록한다. 지원 runner 자원에서 실제 측정 후 한도를 승인한다.
근거 없이 속도 배수나 임의 수치를 약속하지 않는다. 최적화 전후 finding/scope가 같아야 하고
persistent cache의 correctness가 우선이다.

> WP00 상태: 고정 성능 corpus가 없어 **아무 성능 수치도 측정하지 않았다.** 참고로 이번 환경에서
> 전체 pytest는 88.44초였으나, 이는 ici 자신의 테스트 스위트 시간이고 분석 대상 프로젝트에 대한
> 분석 성능이 아니다. 두 값을 혼동하면 안 된다.

## 7. 보안/오프라인 인수

manifest checksum/provenance·재배포 라이선스·정적 자산 외부 URL·시스템 CA·비공개 run 파일 권한·
path/archive/XML/JSON/HTML 검증·provider output bounds·credential 없는 PR 분석·신뢰된 publisher
분리를 확인한다. 사용자 테스트의 임의 코드는 OS sandbox/runner 정책 대상이며 ici 자체만으로 완전한
격리를 주장하지 않는다.

> 현행 자산: AGENTS §3이 시스템 CA 존중(`requests`/`httpx`/`certifi` 금지)과 root 권한 배제를
> 이미 불변식으로 강제한다. `core/runner.py`의 bounded capture가 provider output bounds에
> 해당한다. `core/redaction.py`가 민감 값 제거를 담당한다. 이 4개는 개정된 AGENTS에서도
> **그대로 유지**했다.

## 8. 릴리스·현장 체크리스트

- bundle SHA/source/tools/lockfiles를 고정하고 동일 input의 rebuild 결과를 비교한다. 재현 불가
  항목은 원인과 integrity 기준을 명시한다.
- candidate는 stable이 아니다. feature PR의 merge가 릴리스를 자동 생성하지 않는다.
- actual RHEL에서 일반 사용자 압축 해제 → 기본 설정 생성/정상 수정 → Python/C++/혼합 검증 →
  offline HTML을 확인한다. 필요한 현장별 정상 설정과 반복 workaround를 구분하여 기록한다.
- GHES에서 실패/미완료/성공 결과 게시·sticky 갱신·stale run 차단·권한 실패를 확인한다.
  실제 미실행은 미완료 표기.
- 이전 안정 bundle 경로로 복귀하는 방법과 config/result backup을 runbook에 담는다. 공용
  Python/project venv를 복구해야 하는 방식은 기본 rollback이 아니다.

## 완료 기준

- [ ] R01~R15 → SPEC → WP/PR → 자동/현장 test → evidence의 추적표가 완성된다.
      (골격 완성 → [requirements-traceability.md](requirements-traceability.md).
      evidence 열은 각 WP가 채운다)
- [ ] 19개 엔진 disposition과 사용자 기능의 이전/선택 제공 결정에 빈칸이 없다.
      (잠정 disposition 완성 →
      [inventory/current-engines.md §7](inventory/current-engines.md). 최종은 WP별)
- [ ] 정확도·false positive·coverage 누락·필수 미완료·부분 gate 검증 근거가 있다.
- [ ] ici-only 회귀/패키징 gate로 전환하고 toy 제품은 특수 개조 없이 독립한다.
- [ ] 지원/제한/미확인 환경과 release blocker를 공개 문서와 내부 현장 checklist에서 구분한다.
      (§1의 WP00 실측 표가 첫 데이터)
- [ ] 문서·CHANGELOG·지원표·candidate evidence·rollback을 검토한 뒤 별도 stable 결정을 한다.
