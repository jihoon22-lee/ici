# ADR-0006 — 런타임 후보로 PBS CPython 3.13.7을 채택하고 glibc 하한을 2.17로 기록한다

- 상태: **accepted (evidence pending)** — 방향과 하한은 실측. 최종 버전 고정은 현장 실행 후.
- 결정 시점: WP01 ([#199](https://github.com/jihoon22-lee/ici/issues/199))
- 근거: [spikes/wp01-runtime-environment.md §1·§2](../spikes/wp01-runtime-environment.md)
- 관련 요구사항: R01, R03, R11
- 상위 결정: [ADR-0002](0002-standalone-runtime-bundle.md) (bundle 배포 방향)

## 결정

ici core의 전용 런타임 **시험 후보를 python-build-standalone(PBS) CPython 3.13.7로 확정**하고,
다음을 측정된 배포 요구로 기록한다.

|항목|값|
|---|---|
|런타임|PBS CPython 3.13.7 (`[Clang 20.1.4]`)|
|runtime tree digest|`sha256:13dc5b785bc1c54547f8582f39aa631bbb0863326cb6c5aa75a26c7c74a69b0c`|
|**glibc 최대 심볼 요구**|**GLIBC_2.17**|
|동적 의존|`libc` `libdl` `libm` `libpthread` `librt` `libutil` 뿐|
|OpenSSL|3.0.16, **정적 링크** (`_ssl`이 인터프리터 내장)|
|tkinter|**bundle에서 제외** — `libtcl8.6`/`libtk8.6`를 요구하는 유일한 모듈이고 ici는 쓰지 않는다|

그리고 **런처는 `SSL_CERT_FILE`/`SSL_CERT_DIR`을 절대 지우지 않는다.**

## 대안

| 대안 | 기각 사유 |
|---|---|
|시스템 인터프리터 탐색 유지 (현행 pyz)|R03이 요구하는 전용 런타임이 아니다. 대상 환경의 Python 버전에 계속 종속되고, `PYTHON_CANDIDATES` 6개 순차 탐색은 여러 Python이 있는 환경에서 선택이 불투명하다|
|CPython을 직접 소스 빌드|재현 가능한 재배포 빌드를 우리가 유지해야 한다. PBS가 이미 그 일을 하고 digest를 제공한다|
|3.14 (또는 free-threaded 빌드)|`uv python list`에 3.14.0rc2가 있으나 rc다. 폐쇄망 배포의 기반을 rc로 두지 않는다. free-threaded는 확장 모듈 호환이 별개 문제이고 이번 범위에 필요가 없다|
|3.12 이하|3.13이 지원 기간이 더 길고, 이번 시험에서 3.13.7이 glibc 2.17 요구를 만족했다. 더 낮출 이유가 측정으로 나오지 않았다|
|tkinter 포함 유지|bundle의 외부 의존에 Tcl/Tk를 추가한다. ici가 쓰지 않는 모듈 때문에 폐쇄망 설치 요구를 늘리는 것은 손해다|

## 근거

1. **glibc 2.17이 결정적이다.** 바이너리의 최대 심볼 요구가 `GLIBC_2.17`이다. RHEL 8.10은
   glibc 2.28, RHEL 7.9는 2.17이므로 심볼 요구 측면에서 두 환경 모두 만족한다.
   추정이 아니라 `objdump -T` 실측이며 manifest에 전체 심볼 목록이 들어간다.
2. **외부 의존이 거의 없다.** 동적 의존이 표준 6개뿐이고, 확장 모듈 전체를 훑어도 추가되는
   것은 Tcl/Tk(tkinter 전용)뿐이다. 그것을 제거하면 bundle의 외부 요구가 표준 libc 계열로
   닫힌다. 폐쇄망 설치에서 이 성질이 핵심이다.
3. **실제로 돌았다.** 이 런타임을 uv 저장소 밖으로 **복사**한 bundle에서 `ici 0.11.0`이
   실행됐고, 경로 이동·공백 경로·clean HOME·실제 읽기 전용 바인드 마운트·proxy 전면 제거
   조건을 모두 통과했다. ADR-0002가 가정으로 세운 항목이 측정으로 바뀌었다.
4. **정적 OpenSSL이 CA 제약을 만든다.** `SSL_CERT_FILE`을 지우면 cafile이 `None`이 된다.
   컴파일 기본값(`/etc/ssl/cert.pem`)은 빌드마다 다르다. 따라서 core 격리를 위해 환경을
   비우는 구현은 **사내 TLS 검증을 조용히 깨뜨린다.** AGENTS §3의 "시스템 CA 존중"이
   next 경로에서 구체적으로 뜻하는 바가 이것이고, 런처 계약으로 고정해야 한다.

## 호환 영향

| 대상 | 영향 |
|---|---|
|stable pyz 경로|없음. 이 ADR은 next 경로 전용이며 `PYTHON_CANDIDATES`·launcher를 바꾸지 않는다|
|`ICI_PYTHON`|next bundle에서는 불필요해진다. pyz 경로에서는 계속 유효|
|bundle 크기|155 MB (trim 후 측정값). 배포 허용 크기는 미정 → 보류 5번|
|tkinter 사용 코드|없음. 제거가 기능을 줄이지 않는다|

## 복구

이 ADR은 **후보 선정**이므로 되돌리는 비용이 낮다. 현장 실행이 실패하면:

- `build-bundle.sh`의 런타임 인자만 다른 PBS 버전으로 바꿔 재측정한다
  (스크립트가 런타임 디렉터리를 인자로 받는다).
- 실패 원인이 glibc/CPU/ABI면 그 값을 기록하고 하한을 올린다. **구현 언어를 바꾸지 않는다**
  ([ADR-0001](0001-keep-python-core.md)).
- stable pyz 경로는 그대로 유지되므로 배포 능력을 잃지 않는다.

## 보류 항목

| # | 항목 | 결정 조건 | blocker |
|---|---|---|---|
|1|최종 CPython 버전·패치 고정|사내 RHEL 실행 확인 후|**예**|
|2|RHEL 8.10 실제 실행|현장 확인. **심볼 요구 충족은 동작 확인이 아니다**|**예**|
|3|CPU/ISA 하한|PBS 바이너리의 `.note.gnu.property`가 비어 있어 정적 판정이 불가했다. 현장 CPU 실행으로만 확정|**예**|
|4|RHEL의 CA 경로|PBS 기본값은 `/etc/ssl/cert.pem`. RHEL은 `/etc/pki/tls`를 쓴다. `/etc/ssl/certs` 심볼릭 링크 존재 여부를 현장 확인|**예**|
|5|bundle 크기 예산|폐쇄망 배포 허용 크기 합의|아니오|
|8|재현 가능한 bundle 빌드|같은 입력 2회 빌드 digest 비교. 이번에 하지 않았다|아니오|
