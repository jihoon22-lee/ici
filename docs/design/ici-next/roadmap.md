# ici-next 로드맵

| | |
|---|---|
|상태|**채택된 계획 (adopted plan)**. 진행 상태는 이슈가 담당한다.|
|원문 이슈|[PLAN #191](https://github.com/jihoon22-lee/ici/issues/191)|
|마일스톤|[ici-next — Modular analysis & offline-first execution](https://github.com/jihoon22-lee/ici/milestone/1)|
|기준 소스|`20c417cc8ec84aa490d0138782bf9fe38374fb5d`|

> **계획 등록·문서 채택은 구현 완료·실제 RHEL 검증·릴리스 승인을 의미하지 않는다.**

## 문서 지도

규범 원문은 이 디렉터리에 있다. 진행 상태는 GitHub 이슈가 갖는다.

|순서|문서|원문 이슈|내용|
|---|---|---|---|
|1|[architecture.md](architecture.md)|[#192](https://github.com/jihoon22-lee/ici/issues/192)|기술 스택·시스템 경계·모듈 구조·공통 모델·데이터 흐름|
|2|[spec-01-workspace-config-cli.md](spec-01-workspace-config-cli.md)|[#193](https://github.com/jihoon22-lee/ici/issues/193)|workspace/component·루트/선택 하위 TOML·설정 우선순위·CLI 선택|
|3|[spec-02-distribution-execution.md](spec-02-distribution-execution.md)|[#194](https://github.com/jihoon22-lee/ici/issues/194)|독립 bundle·환경 보존·도구 선택·DAG 실행·취소·artifact/cache|
|4|[spec-03-analysis-engines.md](spec-03-analysis-engines.md)|[#195](https://github.com/jihoon22-lee/ici/issues/195)|Check/Provider/Task·언어 묶음·19개 엔진 disposition·test/coverage/TEM|
|5|[spec-04-results-integration.md](spec-04-results-integration.md)|[#196](https://github.com/jihoon22-lee/ici/issues/196)|결과/이벤트 스키마·gate/exit·baseline·HTML·GHES·idk|
|6|[spec-05-verification-transition.md](spec-05-verification-transition.md)|[#197](https://github.com/jihoon22-lee/ici/issues/197)|지원표·독립 corpus·실제 도구/배포/현장 검증·비교·이전·릴리스|

현행 구현 측정 기록 (WP00 산출물):

- [inventory/current-engines.md](inventory/current-engines.md) — 19개 엔진 전수 조사
- [inventory/execution-flow.md](inventory/execution-flow.md) — 실행 흐름·환경 보정 지점
- [inventory/baseline-measurements.md](inventory/baseline-measurements.md) — 게이트 실행 기준선

결정 기록:

- [adr/](adr/) — 채택한 결정과 보류 항목
- [requirements-traceability.md](requirements-traceability.md) — R01~R15 추적표

모든 SPEC의 TOML/CLI/스키마는 **ici-next 목표 계약**이며 현행 stable에서 이미 지원되는 문법이라는
뜻이 아니다. 정확한 field/option은 executable contract tests와 함께 채택한다.

## 제품 목표

**ici 설치 → 기본 설정 생성 → 프로젝트별 설정 조정 → 이미 준비된 개발 환경에서 검증**한다.
다양한 분석 관점을 유지하면서 엔진별 의존성 설치·환경 보정·설정 중복·반복 빌드를 줄인다.
ici는 독립 품질 분석 제품이며, idk는 실행 환경·작업 UI를 제공하는 선택적 소비자다.

## 추적 가능한 요구사항

전체 추적표(SPEC·WP·테스트·evidence 연결)는
[requirements-traceability.md](requirements-traceability.md)에 있다.

|ID|요구사항 및 금지사항|
|---|---|
|R01|이미 준비된 프로세스 환경을 사용한다. `devenv.csh` 등 초기화 파일 탐색·해석·source 기능을 만들지 않는다.|
|R02|도구 설치와 정상적인 프로젝트 설정은 허용한다. 이를 무설치·무설정 요구로 오해하지 않는다. ici 본체/정적 검사 도구를 각 프로젝트 `.venv`에 반복 설치하는 의존은 줄인다.|
|R03|Python 코어와 전용 런타임 압축 배포를 기본 방향으로 삼는다. 정확한 런타임·도구 버전·ABI 지원은 배포 시험 후 고정한다.|
|R04|Python/C++ 내장 엔진 묶음을 분리한다. 공통 CLI·실행기·결과·품질 정책은 하나다. Qt는 C++ 추가 기능이다.|
|R05|`verify`는 설정된 기본 범위, `--python/--cpp/--component`는 이번 실행 범위 선택이다. 부분 성공을 전체 게이트 성공으로 표시하지 않는다.|
|R06|루트 workspace `ici.toml` 하나가 기본이다. 구성요소 설정은 명시적 참조로만 선택 분리한다. 기존 Ruff/pytest/type checker/qmake 설정 의미를 존중한다.|
|R07|workspace/component/analysis unit/build unit를 구분한다. qmake SUBDIRS·공유 빌드·생성 파일·외부 헤더·혼합 저장소를 기존 구조대로 연결한다.|
|R08|검사 관점(check)과 분석 제공자(provider)를 분리한다. 동일 실행을 공유하되 서로 다른 규칙·환경·variant를 잘못 합치지 않는다.|
|R09|line/lint/type/test/coverage/TEM 및 유용한 기존 관점을 보존한다. 19개 descriptor의 유지·통합·선택 제공·폐기는 근거와 이전 경로를 남긴다.|
|R10|검사 수행 상태·분석 신뢰도·품질 판정·게시 상태를 분리한다. 필수 검사 누락, 오래된 결과, 축소된 범위, 도구의 heuristic fallback을 통과로 숨기지 않는다.|
|R11|폐쇄망에서는 실행 중 다운로드·pip 설치·자동 업데이트·외부 CDN을 요구하지 않는다. root 불필요, 시스템 CA 존중, 출력·명령·소스·artifact의 신뢰 경계를 유지한다.|
|R12|idk와는 CLI·버전 있는 JSON 결과/JSONL 이벤트·취소 계약으로 연결한다. ici가 idk에 의존하거나 셸을 다시 초기화하지 않는다.|
|R13|toy-projects에 ici용 `devenv.csh`·특수 구조를 추가하지 않는다. ici의 기본 회귀 corpus와 릴리스 기준은 ici가 소유한다.|
|R14|단계적 전환, 기존 결과/설정/viewer 호환성, 비교 근거, 복구 경로를 제공한다. PR 완료와 stable release는 분리한다.|
|R15|실제 사내 소스·환경파일·원본 로그를 외부 반출하도록 요구하지 않는다. 공개 합성 fixture와 폐쇄망 내부 인수 체크리스트를 구분한다.|

## 결정 수준

- **합의된 원칙**: R01~R15, 단일 제품/언어 묶음, 루트 정책+선택 구성요소 파일, 레거시
  `make lint/test/cov`에 검사 구현 위임 금지. `make cov`는 해당 현장의 Coverity 타깃이다.
- **채택할 구현 방향**: Python/Typer/Rich, 타입 있는 모델, TOML, 공통 subprocess 실행기, 정적
  HTML, 실행별 파일 저장. Rust 전면 재작성·서버·DB·외부 플러그인 생태계는 이번 범위 밖.
  → [ADR-0001](adr/0001-keep-python-core.md)
- **증거 후 고정**: CPython 3.13 일반 빌드 후보와 배포본, GCC/Qt/Python/pytest/coverage 지원 하한,
  네이티브 도구 조합, pytest 도구 overlay의 제공 여부, 캐시 성능 예산, GHES/runner 호환표.
  숫자나 지원 결과를 추정으로 확정하지 않는다. → [ADR-0002](adr/0002-standalone-runtime-bundle.md)
- **구버전 전환**: 기존 AGENTS의 Python 3.10/pure wheel/단일 pyz/toy release invariant는
  WP00에서 명시적으로 개정했다. → [ADR-0003](adr/0003-agents-invariant-scoping.md)

## 단계와 종료 게이트

|단계|주요 작업|종료 조건|
|---|---|---|
|P0|[#198](https://github.com/jihoon22-lee/ici/issues/198) [#199](https://github.com/jihoon22-lee/ici/issues/199) [#201](https://github.com/jihoon22-lee/ici/issues/201)|현행/19개 엔진·규약·위험 가정·독립 corpus 기반을 확인하고 미검증 항목을 분리|
|P1|[#200](https://github.com/jihoon22-lee/ici/issues/200) [#202](https://github.com/jihoon22-lee/ici/issues/202) 및 [#203](https://github.com/jihoon22-lee/ici/issues/203) [#204](https://github.com/jihoon22-lee/ici/issues/204) [#205](https://github.com/jihoon22-lee/ici/issues/205)의 최소 PR → [#206](https://github.com/jihoon22-lee/ici/issues/206)|전용 런타임으로 init→line/Ruff→JSON/HTML까지 실제 bundle E2E|
|P2|[#203](https://github.com/jihoon22-lee/ici/issues/203) [#204](https://github.com/jihoon22-lee/ici/issues/204) [#205](https://github.com/jihoon22-lee/ici/issues/205) 완성, [#207](https://github.com/jihoon22-lee/ici/issues/207) [#208](https://github.com/jihoon22-lee/ici/issues/208) [#209](https://github.com/jihoon22-lee/ici/issues/209) [#210](https://github.com/jihoon22-lee/ici/issues/210)|구성요소/언어 선택, 공통 환경·실행·계획·진단·실패 전파·cache 계약 완성|
|P3|[#211](https://github.com/jihoon22-lee/ici/issues/211) [#212](https://github.com/jihoon22-lee/ici/issues/212) [#213](https://github.com/jihoon22-lee/ici/issues/213) [#214](https://github.com/jihoon22-lee/ici/issues/214)|qmake SUBDIRS·공유 빌드·기존 compile inputs·C++/Qt/mixed 실제 검증|
|P4|[#215](https://github.com/jihoon22-lee/ici/issues/215) [#216](https://github.com/jihoon22-lee/ici/issues/216) [#217](https://github.com/jihoon22-lee/ici/issues/217) [#218](https://github.com/jihoon22-lee/ici/issues/218) [#219](https://github.com/jihoon22-lee/ici/issues/219) [#220](https://github.com/jihoon22-lee/ici/issues/220)|Python·자체 규칙·test/coverage/TEM·선택 확장의 이관과 19개 disposition 근거|
|P5|[#219](https://github.com/jihoon22-lee/ici/issues/219) 완성, [#221](https://github.com/jihoon22-lee/ici/issues/221) [#222](https://github.com/jihoon22-lee/ici/issues/222) [#223](https://github.com/jihoon22-lee/ici/issues/223) [#224](https://github.com/jihoon22-lee/ici/issues/224)|부분/전체 gate·baseline·offline HTML·GHES 게시·idk 계약 완성|
|P6|[#225](https://github.com/jihoon22-lee/ici/issues/225) [#226](https://github.com/jihoon22-lee/ici/issues/226) [#227](https://github.com/jihoon22-lee/ici/issues/227)|명시 이전·비교/보안/성능/현장 인수·기본 전환·릴리스/복구 기준|

단계는 추가 GitHub 마일스톤이 아니다. WP의 상세 선행 조건이 실제 순서를 결정한다. P1–P2에 걸친
WP는 최소 계약 PR을 먼저 병합하여 #206을 검증하고 전체 조건 충족 후 닫는다. P4–P5의 #219도 순수
판정 테스트를 일찍 시작하고 실제 evidence 통합 후 완료한다. 결과 스키마·보안·회귀 검증을
P5/P6까지 미루지 않는다.

## 주요 경로

```
#207 workspace → #208 DAG → #209 identity/cache
                           → #210 CLI/doctor/plan
#211 compile inputs → #212 qmake / #213 CMake·Make → #214 C++/Qt
#208 + resolver → #215 Python static / #216 Python tests
#212·#213 + runner → #217 C++ tests
provider 근거 → #218 native 규칙 → #219 policy/TEM
#219 → #221 baseline → #222 HTML → #223 GHES
#210 + events/runner + #219 → #224 idk 계약
#225 migration + #226 전체 인수 → #227 최종 전환
```

이는 문서상 작업 의존 관계이며 GitHub native sub-issue/dependency 기능을 설정했다는 뜻은 아니다.
각 WP 본문이 필수 선행/병행 가능/최종 완료 조건의 정확한 기준이다. mock/interface로 선행 PR을
개발하는 것과 실제 dependency가 완성된 통합을 구분한다.

**처음에는 #198부터 시작한다.** 이어 #199(위험 시험), #200(모델/스키마), #201(corpus)을 각 의존
계약에 맞춰 병행한다. #202~#205의 첫 PR을 연결하여 #206의 실제 사용 흐름을 통과시킨다. 그 전에
19개 엔진 파일을 일괄 이동하는 대규모 변경부터 시작하지 않는다.

## 기존 엔진의 담당 작업

|현행 descriptor/영역|주 담당 WP|
|---|---|
|line|[#206](https://github.com/jihoon22-lee/ici/issues/206), 공통 지표 정리 [#218](https://github.com/jihoon22-lee/ici/issues/218)|
|lint/type|[#214](https://github.com/jihoon22-lee/ici/issues/214) C++, [#215](https://github.com/jihoon22-lee/ici/issues/215) Python|
|compile_db|[#211](https://github.com/jihoon22-lee/ici/issues/211), 입력 수집 [#212](https://github.com/jihoon22-lee/ici/issues/212) [#213](https://github.com/jihoon22-lee/ici/issues/213)|
|test|[#216](https://github.com/jihoon22-lee/ici/issues/216) Python, [#217](https://github.com/jihoon22-lee/ici/issues/217) C++|
|complexity/cognitive/dup/cycle/resource/security/exception/dead|[#218](https://github.com/jihoon22-lee/ici/issues/218), 외부 provider 대체 근거 [#214](https://github.com/jihoon22-lee/ici/issues/214) [#215](https://github.com/jihoon22-lee/ici/issues/215)|
|python_compat/sanitize/thread_sanitize/build/binary_compat/integration|[#220](https://github.com/jihoon22-lee/ici/issues/220); prepare 부분 [#212](https://github.com/jihoon22-lee/ici/issues/212) [#213](https://github.com/jihoon22-lee/ici/issues/213)|
|coverage/TEM|[#216](https://github.com/jihoon22-lee/ici/issues/216) [#217](https://github.com/jihoon22-lee/ici/issues/217) evidence, [#219](https://github.com/jihoon22-lee/ici/issues/219) 계산/판정|

현행 언어별 지원과 수식·규칙의 실측값은
[inventory/current-engines.md](inventory/current-engines.md)에 있다. 엔진 개수만 줄이는 것이
목표가 아니며 모든 기존 항목의 최종 disposition/이전/근거를 남긴다.

## 실행·검토 규약

1. WP의 선행 이슈와 SPEC을 읽고, 시작할 때 현재 main과 기준 SHA의 차이를 확인한다.
2. 목적별 브랜치/PR을 사용한다. PR 제목은 사용자/기술 결과를 설명하고 WP 번호만 제목으로 쓰지
   않는다. main 직접 변경·자동 버전 상승·자동 릴리스는 금지한다.
3. 모델·실행 구조 변경과 분석 규칙/임계값 변경을 가능한 한 분리한다. 기존 gate를 무조건 낮추거나
   expected 결과를 일괄 재생성하지 않는다.
4. 인수 기준, 실제 명령/입력 digest/도구 버전/테스트 결과를 PR 근거로 남긴다. 테스트 미실행은
   명시한다.
5. 큰 WP는 본문의 PR 경계로 나누고 전체 기준 충족 시 닫는다. 후속 PR 전에는 `Closes`로 조기
   종료하지 않는다.
6. 규범 문서는 이 디렉터리에 모으고 기존 문서의 현행/next 상태를 표시한다. 이슈 상태와 문서
   규칙을 중복 관리하지 않는다.
7. idk 실제 UI/소스 변경은 [#224](https://github.com/jihoon22-lee/ici/issues/224)의 handoff로
   해당 저장소에 연결한다. toy-projects는 일반 소비자이며 이 마일스톤을 위해 특수 구조를 추가하지
   않는다.

## 완료의 의미

|대상|완료 조건|
|---|---|
|ARCH / SPEC 이슈|문서 채택 + 계약 테스트 연결|
|WP 이슈|구현 + 검증 완료|
|PLAN 이슈|**전체 인수** 시|

**설계 이슈 완료율이 제품 완료율을 뜻하지 않는다.** 문서 PR 채택과 구현 WP 완료는 별개다.

## 최종 인수 체크리스트

- [ ] R01~R15 각각 구현/테스트/문서 근거가 연결되었다.
- [ ] 지원 환경에서 오프라인 압축 설치와 경로 이동·병행 버전·읽기 전용 배포 디렉터리 실행을
      검증했다.
- [ ] Python-only는 무관한 C++ 도구를, C++-only는 무관한 Python 검사 도구를 요구하지 않는다.
- [ ] qmake SUBDIRS/혼합 저장소에서 공유 빌드와 component별 결과가 실제 도구 테스트를 통과했다.
- [ ] 필수 미완료·0개 테스트·잘못된/오래된 coverage·부분 선택·게시 실패가 전체 PASS로 오인되지
      않는다.
- [ ] 19개 기존 descriptor와 사용자 명령/설정/리포트의 처리·이전 표가 완성되었다.
- [ ] ici 내부 corpus로 검증 가능하며 toy 최신 main에 릴리스가 종속되지 않는다.
- [ ] GHES/idk 계약과 외부 저장소 변경 경계를 확인했다.
- [ ] 기존 결과 비교, 보안·성능 측정, 실제 RHEL/GHES 현장 확인 상태, rollback/runbook을 기록했다.
- [ ] stable 버전과 공개는 별도 결정했다. 실제 수행하지 않은 릴리스·현장 검증을 완료로 적지 않았다.
