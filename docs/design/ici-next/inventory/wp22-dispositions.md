# WP22 동적·호환성 검증 이관 disposition

- 상태: **PR A(build/artifact contract)·PR B(sanitizer 이관)·
  PR C(compatibility 이관) 구현 완료. PR D(integration)가 남아 있다.**
  잠정 표는 [current-engines.md §7](current-engines.md#7-잠정-disposition)에 있다.
- 근거 이슈: [WP22 #220](https://github.com/jihoon22-lee/ici/issues/220)
- 대상: `build`, `binary_compat`, `integration`, `python_compat`,
  `sanitize`, `thread_sanitize` — 전부 variant·도구·실행 의존인 동적
  검증군이다.

## 판정 원칙

1. **ici는 빌드하지 않는다.** `prepare = "explicit"`은 권한 단어이며 명령이
   아니다. next check은 프로젝트 자신의 빌드·테스트가 남긴 산출물만 읽는다.
2. **계약과 준비를 분리한다.** build 준비(`prepare` 권한)와 산출물 계약
   (`artifacts` 선언)은 다른 사실이다 — 준비 없이도 계약 검증은 가능하고,
   선언 없는 검증을 성공으로 표시하지 않는다.
3. **동적 검증은 프로파일이 결정한다.** sanitizer·integration처럼 별도
   런타임·실행을 요구하는 check은 `deep`에서만 선택되며 `normal`/`fast`
   프로파일이 암묵적으로 켜지 않는다 (#220 인수 기준).
4. **부분 준비를 PASS로 보고하지 않는다.** 미빌드·미계측·서비스 부재는
   `blocked`→INCOMPLETE이며 조용한 skip이 아니다.
5. **계약 위반은 advisory가 기본이다.** `cpp.artifact`는 `required=False` —
   선언 자체가 check의 적용 조건이고, 게이트 실패로 만들지는 워크스페이스가
   `[checks."cpp.artifact"] required = true`로 결정한다.

## PR A — build/artifact contract 분리 (완료)

|항목|결정|
|---|---|
|선언|`[builds.<id>] artifacts = [<glob>, ...]` — build `directory` 기준 상대 glob. 절대·`..`·비정규 패턴은 composition이 ConfigProblem으로 거부|
|check|`cpp.artifact` (`tool=None`, `required=False`, `provides=("artifact-contract",)`) — linked build가 계약을 선언할 때만 실행, 아니면 blocked|
|검증|glob 해석 → workspace-contained 정규 파일만 산출물. 무일치·디렉터리-only 매치 = `artifact.missing`(MEASURED). workspace escape = `artifact.invalid`(MEASURED)|
|측정|`artifacts.satisfied`(numerator=충족 glob, denominator=선언 수), `artifacts.produced`(매치 파일 수)|
|경계|per-file digest 매니페스트(identity record)는 Observation 채널에 맞지 않아 소비자 슬라이스(통합·게시)로 미룸 — 조용히 버린 것이 아니라 이 문서가 경계다|
|대조군|`tests/test_cli_next_artifacts.py` — 충족/미충족/미빌드/디렉터리-only/escape/필수 게이트|

## PR B — sanitizer 이관 (완료)

|항목|결정|
|---|---|
|check|`cpp.sanitize` / `cpp.tsan` — `Profile.DEEP` 전용, required 기본. standard/fast는 생략(asked-for가 아니므로 blocked도 아님)|
|실행 단위|variant(`"sanitize"`/`"thread-sanitize"`)로 필터된 linked build의 suite별 task — ctest는 `--test-dir`, qtest는 바이너리 직접 실행|
|계측 증명|`src/ici/workspace/instrumentation.py` — 바이너리의 ELF magic + 런타임 마커(`libasan`/`__asan_init`/`__tsan_init` 등) 스캔. 마커 없음·미빌드·suite 부재·검증 불가(대형/판독불가)는 전부 blocked|
|환경|stable `_append_option` 계약 그대로 — `ASAN_OPTIONS`+=`detect_leaks=1`, `UBSAN_OPTIONS`+=`halt_on_error=1`, TSan은 `halt_on_error=0`. plan 시점의 상속 env에 append해 env_overlay로 고정|
|파싱|`engines/_sanitizer_diagnostics.py`의 bounded 파서를 그대로 호출 — 마커 regex(`_ERROR_RE`/`_SUMMARY_RE`/`_TSAN_WARNING_RE`/`_RUNTIME_RE`)도 동일 모듈에서 재사용. transcript는 stderr→stdout 순(stable과 동일)|
|판정|마커+진단 → MEASURED finding(stable rule id·primary/related 위치 보존, `[external]`은 redact+limitation). 마커 있는데 진단 0개/파싱 실패 → `failed_to_parse`. 마커 없이 exit≠0 → `ici.sanitize.suite-failure` finding. 미완주(타임아웃·시그널·취소) → parse 안 함|
|격리|`cacheable=False` — 바이너리 내용은 선언 input에 없어 캐시 verdict가 stale 계측을 가리킬 수 있음. variant가 다른 build는 suite·task_id·env 모두 분리|
|대조군|`tests/test_cli_next_sanitize.py` — plan 게이팅 7종·마커 스캔 3종·파싱 7종 + 실제 `g++ -fsanitize=address` E2E(g++ 있을 때)|

## PR C — compatibility 이관 (완료)

|항목|결정|
|---|---|
|check|`python.compat`(정적 floor)·`python.compat-runtime`(선언 런타임 `-VV`+`compileall`)·`cpp.binary-compat`(ELF/ABI)|
|정적 floor|`languages/compat.py`가 stable `analyze_static_compatibility`를 그대로 호출 — 컴포넌트 `pyproject.toml`이 우선, 없으면 workspace root의 `requires-python`을 계승. floor 부재는 finding 없음+limitation이며 pass로 위장하지 않는다|
|런타임 증거|선언 인터프리터(`[python] executable` 또는 `.venv`)만 실행 — ici 인터프리터 fallback 없음. floor는 plan 시점에 확정해 `ICI_REQUIRES_PYTHON` env로 task에 실음 — 판정은 plan이 본 선언 기준|
|판정|미설정 인터프리터·무소스·메타데이터 판독 실패는 blocked. `-VV` 비정상 종료·버전 파싱 실패·compileall이 실패 파일을 특정 못함은 `failed_to_parse`. floor 밖 런타임은 `python.compat.runtime-version` finding, compile 실패는 파일+라인을 가진 `python.compat.compile-failure`|
|바이너리 계약|`cpp.binary-compat`은 `needs=("artifact-contract",)` — glob이 지명한 ELF에만 readelf task 1개씩. 발견·실행·스캔한 바이너리는 없다. stable argv(`--file-header --sections --dynamic --version-info --wide`)와 `_elf.parse_readelf`, `_abi_violations` 정책을 그대로 사용|
|정책 범위|선언 없이 안전한 기본만 적용: 절대 RPATH/RUNPATH(`ici.binary.forbidden-rpath`)와 build 경로 누출(`ici.binary.build-path-leak`). class/machine·max glibc/glibcxx/cxxabi·정적 링크·NEEDED 허용목록 등 배포 floor는 `[checks.*]` 스키마에 선언 통로가 없어 **미판정** — 측정된 ABI 사실은 artifact별 limitation으로 기록|
|대조군|`tests/test_cli_next_compat.py` — 정적 floor 4종·런타임 파싱 7종·readelf 파싱 4종·plan 게이팅 5종 + 실제 g++/readelf E2E|

## 잠정 disposition — 이후 PR 슬라이스

|엔진|잠정 disposition|방향|
|---|---|---|
|`build`|준비는 `prepare` 권한 모델로, 산출물 계약은 `cpp.artifact`로 **분리 완료**. 링크 산출물 탐지·매니페스트 생성은 실행 경로가 없으므로 stable 유지|PR A 완료. release-variant 매니페스트는 #224(idk) 소비자와 함께|
|`sanitize`|PR B 완료 — deep 프로파일 check + 계측 마커 게이팅으로 이관|완료|
|`thread_sanitize`|PR B 완료 — `cpp.tsan`(thread-sanitize variant)|완료|
|`binary_compat`|PR C 완료 — readelf per-artifact task + stable 정책 재사용. 배포 floor 선언 스키마는 별도 확장 필요|완료(정책 선언은 deferred)|
|`python_compat`|PR C 완료 — 정적/런타임 분리 이관|완료|
|`integration`|명시 산출물·서비스 요구·명령·timeout을 선언으로 — offline 기본 실행에서 자동 호출 금지, `deep`+opt-in|PR D: `needs`의 artifact 소비 + 외부 서비스 표기|

## 명시적 미이관 (조용한 삭제 없음)

|기능|이유|대체 수단|
|---|---|---|
|ici가 sanitizer/계측 빌드를 직접 실행|실행 계약 밖 — 프로젝트 툴체인 변형 금지|사용자 빌드의 variant 산출물을 check이 읽음|
|stable sanitize의 프로브 컴파일 경로(테스트 소스를 `-fsanitize`로 임시 컴파일)|ici는 컴파일하지 않는다 — 계측은 프로젝트의 variant 빌드가 만든다|`variant = "sanitize"`/`"thread-sanitize"` 빌드 + 바이너리 마커 게이팅|
|stable `build` 엔진의 shadow-tree 링크 산출물 탐지|ici next는 빌드하지 않으므로 link 산출물이 없음|선언 기반 `artifacts` 계약이 그 역할|
|per-artifact digest 매니페스트|Observation 채널에 맞는 필드 없음|#224 소비자 계약과 함께 도입 예정|
|stable python_compat의 import smoke(`imports` opt-in)|프로젝트 모듈 top-level 실행은 임의 부수효과 — 선언 런타임 검증과 분리|미이관. 필요하면 명시 opt-in 명령으로만|
|python_compat의 wheel/packaging 검사(`_python_packaging`)|next에 wheel 산출물 선언 통로가 없음|산출물 계약이 Python 패키지를 선언하게 되는 슬라이스와 함께|
|binary_compat 배포 floor 정책(expected class/machine·max glibc 등)|`[checks.<id>]` 스키마가 enabled/required/exemptions만 지원 — 정책 필드 통로 없음|정책 스키마 확장 시 `ici.binary.*-floor` 등 활성화|
