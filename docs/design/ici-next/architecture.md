# ici-next 아키텍처 v1

| | |
|---|---|
|상태|**채택된 목표 설계 (adopted target design)**|
|원문 이슈|[ARCH #192](https://github.com/jihoon22-lee/ici/issues/192)|
|상위 계획|[roadmap.md](roadmap.md) / [PLAN #191](https://github.com/jihoon22-lee/ici/issues/191)|
|요구사항|[R01~R15](requirements-traceability.md)|
|기준 소스|`20c417cc8ec84aa490d0138782bf9fe38374fb5d`|
|현행 구현|[inventory/current-engines.md](inventory/current-engines.md), [inventory/execution-flow.md](inventory/execution-flow.md)|

> 이 문서는 **목표 설계**다. 현재 구현이 완료되었다는 뜻도, 사내 실행이 검증되었다는 뜻도
> 아니다. 각 절이 현행과 어떻게 다른지는 inventory 문서가 측정값으로 기록한다.

## 1. 경계와 기술 결정

사용자·runner·idk가 프로젝트 환경을 준비한다. ici는 실행 당시 환경과 선언된 설정을 받는다.
셸 초기화 파일이나 특정 NAS·사내 라이브러리 경로를 알지 않는다. 정상적인 도구 설치와 프로젝트
설정은 허용하고 반복 연결 작업을 줄인다.

|영역|채택 방향|결정 조건|
|---|---|---|
|본체|Python, Typer/Rich 유지|현재 분석·모델·검증 자산을 단계적으로 이관|
|배포|전용 CPython 포함 압축 디렉터리|CPython 3.13 일반 빌드와 python-build-standalone은 초기 시험 후보, 정확한 버전·ABI는 실제 시험 후 고정|
|모델|dataclass·Enum·Protocol·명시적 validation|domain은 파일 탐색·도구 실행·CLI와 독립|
|설정|버전 있는 TOML|기존 파서 유지 가능, 프로젝트 도구 설정의 의미를 복제하지 않음|
|작업|공통 subprocess 실행기·제한 병렬 DAG|현재 runner/scheduler 자산 재사용|
|도구|별도 프로세스 adapter 기본|본체에서 native analyzer를 직접 로딩하는 결합은 우선 피함|
|결과|JSON Schema 기반 JSON, 이벤트 JSONL|현재 finding v3 자산을 보존하되 신규 envelope와 legacy 변환 구분|
|HTML|정적 HTML/CSS/JS·Jinja2 방향|외부 CDN·별도 서버·Node 실행기 요구 없음|
|개발/빌드|Hatchling 유지·uv 잠금|사용자 verify 중 패키지 설치/다운로드 없음|
|보관|run별 파일·content-addressed cache|초기 서버·DB·daemon 불필요|

Rust 전면 재작성·다중 언어 코어·외부 플러그인 생태계·언어별 독립 제품은 이번 범위 밖이다.
배포 시험이 실패하면 원인을 근거로 ADR을 수정하며, 구현 언어를 자동 전환하지 않는다.

→ [ADR-0001 Python 코어 유지](adr/0001-keep-python-core.md),
[ADR-0002 전용 런타임 bundle](adr/0002-standalone-runtime-bundle.md)

## 2. 데이터 흐름

```
준비된 환경 + ici.toml + 기존 도구/빌드 설정
  → config/workspace → immutable inputs → tool resolution
  → scope selection → prerequisite plan → task DAG
  → Python/C++ provider 실행 또는 ici 자체 분석
  → observation/finding/metric 정규화
  → 범위·완료도·품질 정책 평가
  → JSON → console / HTML / SARIF / GHES / idk
```

`init/plan/doctor`는 설치·빌드·테스트·환경 초기화를 실행하지 않는다. doctor의 제한된
version/capability probe는 공개하고 timeout을 적용한다. qmake configure는 읽기 전용 탐색으로
위장하지 않고 명시적 prepare 작업으로 분류한다.

> 현행과의 차이: 현재 `verify`는 `probe_all_tools=True`가 기본이라 선택 범위와 무관하게
> 전체 도구를 probe한다. `init`/`plan` 명령 자체가 없다.
> → [inventory/execution-flow.md §2](inventory/execution-flow.md)

## 3. 모듈 구조

```
src/ici/
  cli/                  # 명령과 사용자 표현
  application/          # init/doctor/plan/verify/report/publish 흐름
  domain/               # 공통 입력·작업·관찰·결과 모델
  config/               # 스키마, 검증, 출처 있는 설정 해석
  workspace/            # 구성요소, 소스, 분석 단위, 빌드 단위
  toolchain/            # 프로젝트/분석 도구 선택·기능 탐지
  execution/            # 프로세스, DAG, lock, artifacts, cache
  languages/python/     # Python 입력·등록·지원 범위
  languages/cpp/        # C++ 입력·등록·지원 범위
  languages/cpp/qt/     # Qt 추가 지원, 별도 언어 아님
  adapters/build/       # qmake/CMake/명시적 빌드 연결
  adapters/providers/   # 도구 실행·버전·결과 해석 계약
  adapters/testing/     # Python/C++ 테스트·coverage
  engines/              # ici 자체 알고리즘·공통 분석 primitive
  policy/               # 기준·baseline·최종 판정
  reporting/            # 결과를 표현하는 reporter
  integrations/github/  # 저장된 결과 게시
```

의존 방향은 `cli → application → domain/service interfaces`이며 adapter가 interface를 구현한다.
engine/provider는 설정 로더·전역 환경·도구 탐색을 직접 사용하지 않는다. reporter는 provider를
실행하지 않고 publisher는 판정을 다시 계산하지 않는다. language pack import만으로 관련 도구를
probe하지 않는다. 이 규칙을 import/side-effect 테스트로 검증한다.

> 현행과의 차이: 현재 구조는 `src/ici/{core,engines,reporters,schemas}` + 루트 모듈
> (`config.py`, `doctor.py`, `__main__.py` 등)이다. `BaseEngine`이 `ici.config`와
> `ici.core.project`를 직접 import하므로 "engine이 설정 로더·도구 탐색을 직접 쓰지 않는다"는
> 규칙을 아직 만족하지 않는다 (`src/ici/engines/base.py:9-23`).

## 4. 공통 모델과 불변식

|모델|계약|
|---|---|
|Workspace|canonical root, stable id, 등록 component, 공통 정책, 전체 요구 범위|
|Component|id, root/source sets, languages, build/test 참조; 폴더와 1:1 강제 안 함|
|BuildUnit|build system·정의·작업 디렉터리·variant·환경/도구 식별·산출물; 여러 component 공유 가능|
|AnalysisUnit|component+language+variant/runtime+source scope; 같은 파일의 여러 컴파일 조건 보존|
|SourceSnapshot|분석 파일과 생성 입력의 상태·digest; commit만으로 dirty/untracked 상태를 대표하지 않음|
|EnvironmentSnapshot|런처 진입 시 상속 환경; 실행용으로만 보존, 원문 보고서 출력 금지|
|ResolvedTool|project/analyzer/test 역할, 명령·경로·버전·기능·선택 근거·지원 조건|
|CheckDefinition|검사 관점·언어·입력·규칙·required 정책; provider와 일대일 아님|
|ProviderSpec|제공자 id·adapter 버전·검사 목록·입력·parser·지원 한계|
|TaskSpec|id/key, kind, argv list, cwd, env overlay, 제한, inputs/outputs, deps, resource locks, cache policy|
|ExecutionPlan|선택 범위·전체 요구 범위·DAG·blocker·mutable 작업·identity|
|Observation|도구 실행 증거·측정값·finding 후보; 정책 적용 전의 관찰|
|RunResult|scope, execution/completeness, evidence, finding/metric, verdict, identity, limitations|

입력 객체는 실행 중 수정하지 않는다. BuildSession만 실행기 내부 가변 상태를 갖고 성공 산출물은
검증된 immutable manifest로 전달한다. 실패/취소 중간 파일을 정상 결과로 승격하지 않는다.

> 현행과의 차이: 현재는 `ProjectModel` 하나가 workspace·component·analysis unit의 역할을
> 겸한다(`src/ici/core/context.py:113`). `AnalysisContext`·`CompilationContext`·
> `ArtifactManifest`·`AnalysisIdentity`는 이미 frozen dataclass로 존재하므로 이관 자산이다.

## 5. 실행 계약

1. root/config를 확정하고 각 값의 출처를 보존한다. CLI selection은 품질 정책 자체를 변경하지 않는다.
2. 소스와 component/analysis unit를 생성한다. 모호함은 진단하고 구조 개조를 기본 해결책으로
   요구하지 않는다.
3. 선택한 검사에 필요한 도구만 선택한다. 지정 도구가 없으면 다른 도구로 조용히 대체하지 않는다.
4. 필요한 입력에서 DAG를 생성한다. 빌드/생성은 별도 prepare 노드로 표시한다.
5. 같은 도구·인자·입력·환경·규칙이면 실행을 공유한다. 다른 variant나 의미를 바꾸는 rule union은
   합치지 않는다.
6. runner가 프로세스 생명주기와 동시성을 관리한다. 실패는 의존 작업에만 전파하며 독립 작업은
   계속할 수 있다.
7. scope/completeness를 계산한 뒤 policy를 적용한다. 참고 추정·미측정·범위 축소를 통과로
   숨기지 않는다.
8. 결과를 atomic write한다. report/publish는 분석 재실행 없이 저장된 결과로 동작한다.

## 6. 두 실행 환경의 분리

프로젝트 Python/GCC/qmake/Qt는 준비된 환경과 프로젝트 정의를 기준으로 한다. ici 분석 도구는
bundle 또는 명시된 external provider에서 온다. bundle 경로를 프로젝트 PATH/라이브러리 경로 앞에
전역 삽입하지 않는다. ici core 격리를 위해 수정한 환경이 프로젝트 child로 유출되지 않도록 런처
진입 환경을 먼저 보존한다.

ici core는 프로젝트 site-packages에 오염되지 않고, 테스트는 실제 프로젝트 Python과 패키지를
사용해야 한다. `sys.executable`은 ici core의 경로이며 프로젝트 Python의 fallback이 아니다.
전역 `os.environ`을 병렬 작업마다 수정하지 않는다. alias/function을 파싱하거나 셸 초기화
파일을 호출하지 않는다. 필요한 실행 파일은 정상적인 프로젝트 설정으로 명시할 수 있다.

> **현행 위반 지점 (측정됨)**: `src/ici/engines/test_interpreter.py:13` `_resolve_python`이
> `.venv` 후보 실패 시 `sys.executable`로 fallback한다. 같은 파일 `:35`가 `os.environ`을
> 복사해 `PYTHONPATH`를 앞쪽에 주입하고, `:39`가 WSL에서 `TMPDIR`을 강제한다.
> `src/ici/core/env.py:83`은 NAS 라이브러리 경로를 하드코딩한다.
> 전체 목록 → [inventory/execution-flow.md §4](inventory/execution-flow.md)

## 7. 언어·검사·빌드의 분리

Python/C++ pack은 선언 registry와 adapter를 제공한다. Qt는 C++ capability이다. line/dup/graph
같은 primitive는 공유하되 구문 의미를 언어별로 처리한다. lint/security/exception 관점이 다르다는
이유로 동일 Ruff 실행을 반복하지 않는다. finding은 provider/rule을 보존하고 관점별 표시를 위해
복제하지 않는다.

하나의 qmake SUBDIRS BuildUnit이 여러 C++ AnalysisUnit을 공급할 수 있다. Python 테스트가 C++
산출물에 의존하면 `--python`에서도 필요한 prepare만 포함하며 C++ 품질 검사를 자동 추가하지
않는다. fast는 mutable prerequisite를 실행하지 않고 blocker를 설명한다. 부분 compile DB는 누락된
TU 범위와 함께 보고한다.

## 8. 품질·보안·재현성

- 실행 완료·증거 수준·선택 범위·코드 위반·게시 상태를 독립적으로 표현한다.
- 동일 관련 입력의 정규화 finding/판정 일치를 목표로 한다. 실행 시간의 일치나 동적 테스트의
  영구 결정성을 보장하지 않는다.
- 기본 프로세스 실행은 argv 배열로 처리한다. untrusted 프로젝트 테스트는 제한된 runner에서
  실행하고 게시 권한과 분리한다.
- 원문 환경·토큰·사내 소스를 공개 산출물에 자동 포함하지 않는다. JSON/XML/HTML/경로/archive 입력
  검증과 출력량 제한을 유지한다.
- offline은 ici가 자동 네트워크를 요구하지 않는다는 계약이다. 별도 OS 격리 없이 사용자 테스트의
  모든 네트워크를 차단했다고 주장하지 않는다.
- 읽기 전용 배포 디렉터리·별도 쓰기 경로·공유 build lock·실패한 cache/artifact 재사용 방지를
  검증한다.

> 보존할 현행 자산: `EvidenceState.NOT_APPLICABLE`이 `NOT_RUN`과 이미 구분되어 있고
> (`src/ici/core/models.py:22`), `aggregate_suite_status`가 그 구분을 판정에 반영한다
> (`:321`). `core/redaction.py`의 redaction도 §8의 요구와 같은 방향이다.

## 9. 현재 구현의 이관

|현행 위치|이관 방향|
|---|---|
|[`core/models.py`](../../../src/ici/core/models.py)|finding/evidence/support 자산 보존|
|[`core/context.py`](../../../src/ici/core/context.py)|단일 `ProjectModel`을 workspace/component/analysis unit으로 분해|
|[`core/pipeline.py`](../../../src/ici/core/pipeline.py)|descriptor/DAG를 check/provider/task 구조로 발전|
|[`engines/test_interpreter.py`](../../../src/ici/engines/test_interpreter.py)|interpreter fallback 제거·공통 선택 계층 사용|
|runner/cache/config/qmake/reporting|현행 inventory에서 파일·테스트를 매핑하여 이관. 규칙 변경과 구조 변경은 별도 검토|

각 파일의 실제 규모·책임·테스트는 [inventory/current-engines.md](inventory/current-engines.md)와
[inventory/execution-flow.md](inventory/execution-flow.md)에 측정값으로 있다.

## 10. 문서·완료 조건

규범 문서는 이 디렉터리(`docs/design/ici-next/`)에 모으고 기존 architecture/user-guide/
superpowers 문서에는 현행/next 상태와 링크를 명시한다. ADR에는 결정·대안·근거·호환 영향·복구를
기록한다.

- [x] AGENTS의 Python 3.10/순수 wheel/zipapp/toy gate 규약을 새 요구에 맞게 명시적으로 개정했다.
      → [AGENTS.md](../../../AGENTS.md) §3·§4·§8
- [x] 모델·모듈 경계와 금지 side effect에 계약 테스트가 있다.
      → [`tests/test_ici_next_inventory.py`](../../../tests/test_ici_next_inventory.py)
      (registry coverage·문서 링크·설정 예시). 모듈 경계 import 테스트는 신규 패키지가
      만들어지는 [WP02 #200](https://github.com/jihoon22-lee/ici/issues/200) 이후에 붙는다.
- [x] SPEC/WP에 이 설계가 연결되고 미확인 선택은 release blocker 또는 증거 있는 결정으로
      기록된다. → [requirements-traceability.md](requirements-traceability.md), [adr/](adr/)
- [x] 문서 PR 채택과 구현 WP 완료를 구분한다. → [roadmap.md](roadmap.md) 「완료의 의미」

공식 참고: [uv의 관리형 Python/PBS 설명](https://docs.astral.sh/uv/concepts/python-versions/),
[Python 격리 옵션](https://docs.python.org/3/using/cmdline.html#cmdoption-I),
[Clang compilation database](https://clang.llvm.org/docs/JSONCompilationDatabase.html).
실제 지원 버전은 고정 artifact 시험으로 확인한다.
