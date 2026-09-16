# WP22 동적·호환성 검증 이관 disposition

- 상태: **PR A(build/artifact contract 분리) 구현 완료. 나머지 슬라이스는
  이 문서의 disposition이 잠정이며 각 PR에서 확정한다.** 잠정 표는
  [current-engines.md §7](current-engines.md#7-잠정-disposition)에 있다.
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

## 잠정 disposition — 이후 PR 슬라이스

|엔진|잠정 disposition|방향|
|---|---|---|
|`build`|준비는 `prepare` 권한 모델로, 산출물 계약은 `cpp.artifact`로 **분리 완료**. 링크 산출물 탐지·매니페스트 생성은 실행 경로가 없으므로 stable 유지|PR A 완료. release-variant 매니페스트는 #224(idk) 소비자와 함께|
|`sanitize`|ASan/UBSan을 별도 variant로 — 사용자가 계측 빌드·실행한 산출물(로그·진단)을 읽는 check으로 분해. ici가 -fsanitize 빌드를 실행하지 않음|PR B: sanitizer 출력 파서 + fixture. 진단/crash/테스트 실패 분리|
|`thread_sanitize`|sanitize와 동일 — TSan variant|PR B 동일 슬라이스|
|`binary_compat`|실제 ELF 읽기(magic/CLASS/needed libs)를 in-process check으로 — `readelf` 의존 대신 직접 파싱 가능 여부를 PR C에서 확정|PR C: checked-target vs unknown 구분 필수|
|`python_compat`|문법 검사(`ast.parse` in-process)와 지정 런타임 실행을 분리 — 전자는 즉시 이관 가능, 후자는 `executable` 선언 하에만|PR C|
|`integration`|명시 산출물·서비스 요구·명령·timeout을 선언으로 — offline 기본 실행에서 자동 호출 금지, `deep`+opt-in|PR D: `needs`의 artifact 소비 + 외부 서비스 표기|

## 명시적 미이관 (조용한 삭제 없음)

|기능|이유|대체 수단|
|---|---|---|
|ici가 sanitizer/계측 빌드를 직접 실행|실행 계약 밖 — 프로젝트 툴체인 변형 금지|사용자 빌드의 variant 산출물을 check이 읽음|
|stable `build` 엔진의 shadow-tree 링크 산출물 탐지|ici next는 빌드하지 않으므로 link 산출물이 없음|선언 기반 `artifacts` 계약이 그 역할|
|per-artifact digest 매니페스트|Observation 채널에 맞는 필드 없음|#224 소비자 계약과 함께 도입 예정|
