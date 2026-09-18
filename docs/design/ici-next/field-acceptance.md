# ici-next 현장 인수 checklist — RHEL 8.10 / GHES (#226 항목 6·7)

| | |
|---|---|
|상태|**양식 제공 — 실행은 현장 권한 범위**. 이 문서는 폐쇄망 내부에서 수행하는 절차와, 밖으로내도 되는 증거의 형식을 정한다. 실행 결과가 오기 전까지는 어떤 항목도 "현장 검증됨"으로 표기하지 않는다. **수행 추적은 [#265](https://github.com/jihoon22-lee/ici/issues/265)가 담당한다** — 이 문서는 그 절차다.|
|규범|[spec-05](spec-05-verification-transition.md) §2 계층 6(현장 인수), §7·§8|
|전제|ici-next bundle 1부, 검증 대상 저장소 1개, 폐쇄망 내부 RHEL 8.10 호스트 1대, GHES 테스트 저장소·self-hosted runner(선택)|

## 1. 비민감 증거 양식 — 무엇을 밖으로내도 되는가

현장 인수는 **사내 코드·원본 로그·절대경로를 외부로 요구하지 않는다**
(SPEC-05 §2-6). 각 항목의 결과는 아래 형식 한 줄로만 기록한다:

```
{case, status, env, evidence}
case     : 이 문서의 항목 번호 (예: R-3)
status   : pass | fail | blocked | unsupported
env      : 비민감 환경값만 — OS 릴리스 번호, 커널 major, glibc 번호,
           도구 이름+버전. 호스트명·사용자·경로·사내 도메인 제외
evidence : 명령과 요약 수치(종료 코드·건수·초). 원본 로그·소스 경로·
           finding 본문은 붙이지 않는다
```

예시:

```
R-3, pass, rhel-8.10/glibc-2.28, "verify exit=1 findings=4 wall=2.1s"
G-2, blocked, ghes-3.12, "사내 인터셉션 CA 미등록 — runner OS 신뢰 저장소 확인 필요"
```

로그 첨부가 필요하면 첨부 전에 호스트명·사내 도메인·사용자 홈 경로를
마스킹한 **요약본**만 사용한다.

## 2. RHEL 8.10 인수 checklist (R-1 ~ R-12)

사전 확인(기록만, 실패가 아님):

- [ ] **R-0 환경 기록**: `cat /etc/redhat-release`, `ldd --version | head -1`,
      `uname -m`. bundle의 glibc 상한(빌드 manifest 측정값, 예: 2.17)이
      현장 glibc 이하인지 비교한다.

실행(각 항목은 위 양식으로 결과를 기록한다):

- [ ] **R-1 전개**: bundle을 임의 위치에 압축 해제하고 `bin/ici --version`.
      `pip`/`uv`/패키지 설치 없이 시작되어야 한다.
- [ ] **R-2 격리 시작**: `HOME`을 비운 새 디렉터리로 바꾸고
      `env -u XDG_CONFIG_HOME -u XDG_CACHE_HOME HOME=<빈 디렉터리> bin/ici doctor`.
      홈에 기존 설정이 없어도 환경 탐색이 완료되어야 한다.
- [ ] **R-3 폐쇄망 실행**: 네트워크가 없는 상태(또는 `unshare -n` 가능 시
      인터페이스 0개)에서 검증 대상 저장소에 `ici next init/plan/verify`를
      실행한다. download·pip 호출 없이 종료되어야 한다.
- [ ] **R-4 read-only 설치**: bundle 디렉터리를 `chmod -R a-w` 후 재실행.
      사전 컴파일 bundle은 설치 디렉터리에 쓰지 않아야 한다.
- [ ] **R-5 경로 이동**: bundle을 다른 디렉터리로 옮겨 재실행 — launcher가
      자기 위치 기준으로 runtime을 해석해야 한다.
- [ ] **R-6 프로젝트 환경 분리**: 프로젝트 `.venv`/사내 GCC·Qt 환경을
      구성한 상태에서 verify를 실행하고, ici 자체 도구가 프로젝트
      site-packages를 참조하지 않는지 `plan` 출력의 도구 해석 경로로
      확인한다.
- [ ] **R-7 결과·HTML**: verify 후 `next report`로 페이지를 만들고,
      폐쇄망 브라우저에서 연다. 외부 요청 없이 렌더되어야 한다(개발자
      도구 네트워크 탭 0건).
- [ ] **R-8 실제 도구**: 현장의 GCC/qmake/CMake/pytest 버전으로
      real-tool check를 실행하고, 버전 불일치가 traceback이 아니라
      INCOMPLETE/unsupported로 보고되는지 확인한다.
- [ ] **R-8a C++/SUBDIRS/mixed**: qmake `SUBDIRS` 다중 계층, CMake
      compile DB, Python/C++ 혼합 저장소 각각에서 verify를 실행하고
      결과가 component 단위로 나뉘는지 확인한다.
- [ ] **R-8b coverage/TEM**: 준비된 fixture의 coverage 수집과 TEM
      판정(경고 임계 초과의 일시적 실패 → 게이트 축 반영)이 결과에
      기록되는지 확인한다.
- [ ] **R-8c 설정 변경 round-trip**: normal `ici.toml`을 현장 값으로
      수정(언어·component·check on/off)하고 plan→verify가 그 수정을
      반영하는지 확인한다 — 코드 수정 없는 설정 변경이어야 한다.
- [ ] **R-9 취소**: 실행 중 SIGINT → exit 130, 이벤트 스트림이
      `run.completed`까지 닫히는지 확인한다.
- [ ] **R-10 두 버전 병행**: 이전 bundle과 새 bundle을 나란히 설치하고
      각각 `--version`·verify — 서로의 캐시·설정을 오염하지 않아야 한다.
- [ ] **R-11 성능**: 현장 규모 저장소에서 cold/warm 시간과 RSS를 측정해
      §3 기준선과 비교 기록한다(합격선이 아니라 자원 근거).
- [ ] **R-12 지원표 확정**: 위 결과로 language/tool/OS 지원표의 각 행을
      `tested`/`limited`/`unsupported`로 확정한다.
- [ ] **R-13 반복 workaround**: 설치·정상 설정 외에 매 실행마다 필요한
      임시 조치가 있으면 원인과 후속 조치를 기록한다 — 없으면 "없음"으로
      기록한다(인수 기준의 잔여 workaround 조항).

## 3. GHES 인수 checklist (G-1 ~ G-6)

[publish-workflow.md](publish-workflow.md) §3의 미확인 행을 현장에서
확정하는 절차다. mock 통과를 실제 GHES 검증으로 표기하지 않는다.

- [ ] **G-1 버전 고정**: 사내 GHES 버전(`https://<ghes>/api/v3/meta`의
      `installed_version`)을 기록하고, 워크플로의 `actions/*`·
      `upload-artifact`/`download-artifact` pin이 해당 GHES에 존재하는
      버전으로 고정됐는지 확인한다.
- [ ] **G-2 TLS/CA**: runner에서 publish API 호출이 사내 인터셉션 CA를
      통과하는지 확인한다. 실패 시 CA를 runner OS 신뢰 저장소에 등록하고
      재시도 — stdlib `urllib`은 시스템 저장소를 따른다.
- [ ] **G-3 권한 분리**: analyze job은 `contents: read`만, publish job만
      결과 업로드 권한을 갖는지 워크플로에서 확인한다(publish-workflow §1).
- [ ] **G-4 artifact round-trip**: analyze가 올린 result artifact를
      publish job이 download-artifact로 받아 게시까지 완주하는지 확인한다.
- [ ] **G-5 sticky comment**: 같은 PR에 verify를 두 번 실행해 코멘트가
      새로 달리지 않고 갱신되는지 확인한다.
- [ ] **G-6 stale head**: 결과 생성 후 PR head가 밀려난 상황에서 publish가
      기록 없이 덮어쓰지 않고 새 head 기준으로 재평가/거부되는지 확인한다.

## 4. 판정 규칙

- 한 항목이라도 `fail`이면 릴리스 후보 판정을 보류하고 원인을 기록한다.
- `blocked`는 실패가 아니지만 측정되지 않은 항목이다 — 예산 승인이나 지원
  확정의 근거로 쓰지 않는다.
- 이 checklist가 전부 `pass`여야 "RHEL 8.10/GHES 현장 인수 완료"로
  기록할 수 있다. 로컬 mock·Ubuntu CI 결과는 대체 증거가 아니다.
