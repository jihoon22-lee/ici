# WP01 — 독립 런타임·환경 보존·테스트 도구 연결 시험 결과

| | |
|---|---|
|상태|**측정 완료 (measured)**. 목표 설계가 아니라 실제 실행 기록이다.|
|근거 이슈|[WP01 #199](https://github.com/jihoon22-lee/ici/issues/199)|
|기준 커밋|`153ab28f77099cd7bce04774c22cbc24dca3a8ac`|
|재실행|[`scripts/spikes/wp01/`](../../../../scripts/spikes/wp01) — 4개 스크립트|
|측정 환경|원격 개발 컨테이너 (Ubuntu 24.04 계열, x86_64, glibc 2.39). **사내 RHEL 8.10이 아니다.**|

> 이 문서는 **개발 환경 증거**다. [SPEC-05 §1](../spec-05-verification-transition.md)이 요구한
> 대로, 여기서 `tested`로 올릴 수 있는 항목과 현장 확인이 남은 항목을 구분한다.
> **측정하지 못한 것을 동작한다고 적지 않았다.**

## 재실행 방법

```bash
./scripts/spikes/wp01/build-bundle.sh      /tmp/wp01/bundle
./scripts/spikes/wp01/smoke-environment.sh /tmp/wp01/bundle /tmp/wp01/smoke
./scripts/spikes/wp01/probe-test-tools.sh  /tmp/wp01/bundle /tmp/wp01/tools
./scripts/spikes/wp01/probe-compiler.sh    /tmp/wp01/cc
```

스크립트는 `dist/ici.pyz`, `scripts/build-pyz.sh`, 프로젝트 `.venv`, 공용 Python을
**건드리지 않는다.** 산출물은 전부 인자로 받은 작업 디렉터리 안에만 만든다.

## 1. 런타임 (시험 1)

`uv python install 3.13.7`로 python-build-standalone 빌드를 받아
`runtime/python/`으로 **복사**했다(symlink 아님 — 이동 가능성을 증명하는 게 목적이므로).

|항목|측정값|
|---|---|
|버전|`Python 3.13.7 (main, Sep 2 2025, 14:21:46) [Clang 20.1.4]`|
|출처|python-build-standalone via uv|
|runtime tree digest|`sha256:13dc5b785bc1c54547f8582f39aa631bbb0863326cb6c5aa75a26c7c74a69b0c`|
|app tree digest|`sha256:2d9e0e0584ea6ee8098417cc37456191706df5975bbce6af93436b7c5ecdbaed`|
|번들 도구|`ruff 0.15.8` (`sha256:3753396d...`)|
|OpenSSL|3.0.16 (2025-02-11), **정적 링크**|
|bundle 총 크기|155 MB (trim 후. runtime 원본 99 MB)|

### glibc 요구 — 가장 중요한 결과

```
2.2.5 2.3 2.3.2 2.3.3 2.3.4 2.4 2.5 2.6 2.7 2.8 2.9 2.10 2.13 2.14 2.15 2.17
최대: GLIBC_2.17
```

**최대 요구가 2.17이다.** RHEL 8.10은 glibc 2.28, RHEL 7.9는 2.17이므로 이 런타임 바이너리
자체는 두 환경 모두에서 심볼 요구를 만족한다. 이것은 ADR-0002 보류 항목 2번의
**핵심 근거**이지만, 그것만으로 "RHEL 지원"이 되지는 않는다 — 아래 §5의 현장 확인 참조.

### 동적 의존

```
libc.so.6  libdl.so.2  libm.so.6  libpthread.so.0  librt.so.1  libutil.so.1
```

특이 라이브러리가 없다. 확장 모듈 전체를 훑어도 외부 의존은 위 목록 + `libtcl8.6`/`libtk8.6`
뿐이고, 그 둘은 **tkinter 전용**이다. ici는 tkinter를 쓰지 않으므로 bundle 제작 시 제거했고,
그 결과 bundle의 외부 요구에서 Tcl/Tk가 빠진다.

`lib-dynload`에 남는 `.so`는 2개뿐이다. PBS는 확장 모듈을 인터프리터 바이너리에 정적
링크하며, **`_ssl`도 built-in이다** (`import _ssl; _ssl.__file__` → 없음).

### 정적 OpenSSL의 결과 — CA 신뢰 (R11)

```
SSL_CERT_FILE 있음 : cafile = /root/.ccr/ca-bundle.crt
SSL_CERT_FILE 없음 : cafile = None,  capath = /etc/ssl/certs
```

`SSL_CERT_FILE`을 지우면 **cafile이 `None`이 된다.** 컴파일에 박힌 기본값은
`/etc/ssl/cert.pem`(PBS)인데, 시스템 python 3.11은 `/usr/lib/ssl/cert.pem`이었다 —
빌드마다 다르다.

따라서 **core 격리를 위해 환경을 비울 때 `SSL_CERT_FILE`/`SSL_CERT_DIR`을 지우면 안 된다.**
지우면 사내 TLS 인터셉션 환경에서 조용히 검증이 깨진다. AGENTS §3의 "시스템 CA 존중"이
next 경로에서 구체적으로 뜻하는 바가 이것이다. 프로토타입 런처는 이 두 변수를 보존하며,
`smoke-environment.sh` 시험 10이 그것을 검증한다.

## 2. 환경 보존과 격리 (시험 2~4)

`smoke-environment.sh`: **18 PASS / 0 FAIL / 0 BLOCKED**

### 배포 형태

|시험|결과|
|---|---|
|제자리 실행|PASS — `ici 0.11.0`|
|다른 절대 경로로 복사 후 실행|PASS|
|**공백이 포함된 경로**|PASS|
|clean HOME + 별도 XDG|PASS|
|**진짜 read-only 설치 디렉터리**|PASS|
|모든 proxy 환경변수 제거|PASS|

read-only 시험은 `chmod`만으로는 의미가 없다 — root는 DAC 쓰기 검사를 우회하므로 통과해도
잘못된 이유로 통과한다. 그래서 `unshare --mount --map-root-item`으로 private mount namespace를
만들고 `mount -o remount,bind,ro`로 **실제 읽기 전용 바인드 마운트**를 걸었다. 쓰기 probe가
성공하면 시험 자체를 실패로 처리하도록 했고, 그 위에서 bundle이 정상 실행됐다.

proxy 제거 시험은 "ici가 네트워크를 **요구하지** 않는다"는 계약만 확인한다.
**프로세스가 네트워크 syscall을 하지 않았다는 증명이 아니다** — netns 격리 실행은 현장 확인 항목이다.

### core 격리

|시험|결과|
|---|---|
|프로젝트 환경변수가 설정된 상태에서 core가 ici import|PASS|
|core가 `-I`로 격리 실행|PASS|
|프로젝트 `PYTHONPATH`가 core `sys.path`로 누출되지 않음|PASS|
|**core가 프로젝트 전용 모듈을 import할 수 없음**|PASS|

마지막 항목이 결정적이다. 프로젝트 `PYTHONPATH`에만 있는 모듈(`ici_project_marker`)을 만들고
core로 import를 시도해 실패함을 확인했다. 경로 목록 비교가 아니라 실제 import 시도다.

### 프로젝트 child의 환경 보존

런처는 진입 환경을 `ICI_ENTRY_*`로 먼저 보존한 뒤 core를 격리하고, child에게는 보존값을
복원해 넘긴다.

|변수|결과|
|---|---|
|`VIRTUAL_ENV`|PASS — 보존|
|`PYTHONPATH`|PASS — 보존|
|`LD_LIBRARY_PATH`|PASS — 보존|
|`QTDIR`|PASS — 보존|
|child 인터프리터|PASS — 프로젝트의 **3.10.20** (core의 3.13.7이 아니라)|
|child `PATH`|PASS — bundle 경로가 앞에 삽입되지 않음|

즉 **[ARCH §6](../architecture.md)의 양방향 분리가 프로토타입에서 성립한다.**
core는 프로젝트 패키지에 오염되지 않고, 프로젝트 child는 원래 환경을 그대로 받는다.

### symlink venv Python: launch path 대 realpath

```
launch path : <project>/.venv/bin/python  -> sys.prefix = <project>/.venv
realpath    : /usr/bin/python3.10         -> sys.prefix = /usr
```

**prefix가 다르다.** [SPEC-02 §3](../spec-02-distribution-execution.md)이
"venv Python symlink를 realpath로 치환하여 실행하면 환경이 달라질 수 있으므로 실행 경로를
보존한다"고 요구한 근거가 이 측정값이다. `ResolvedTool`의 `launch_path`/`real_path` 분리는
가정이 아니라 관측된 차이에 대응한다.

### 새로 발견한 제약 — clean HOME에 파일을 쓴다

`--version`은 설정을 읽기 전에 끝나므로 HOME에 아무것도 만들지 않았다. 그런데
`doctor --brief`를 clean HOME에서 돌리면:

```
[ici] 기본 전역 설정을 생성했습니다: <HOME>/.config/ici/ici.toml
clean HOME에 생성된 파일: 1개
```

`load_config`의 `_ensure_global_default_config`가 설정이 하나도 없을 때 XDG 전역 기본 파일을
**쓴다**. 결과:

- **읽기 전용 HOME에서는 설정 로드가 실패할 수 있다.** 설치 디렉터리를 읽기 전용으로 만드는
  것은 통과했지만 HOME은 별개다.
- 첫 실행이 사용자 설정 디렉터리를 변경한다. [SPEC-01 §3](../spec-01-workspace-config-cli.md)이
  "개인 XDG 설정은 표시/편집기 기본값만 허용한다"로 가려는 방향과 맞물린다 — 자동 생성되는
  전역 파일이 품질 정책을 담는 현행 구조 자체가 바뀌어야 한다.

담당: [#203](https://github.com/jihoon22-lee/ici/issues/203).

## 3. 테스트 도구 경로 (시험 5)

`probe-test-tools.sh`. 프로젝트 인터프리터는 3.10.20, core는 3.13.7.

### project 경로 — 동작한다

프로젝트 venv의 `pytest 9.1.1`로 스위트가 통과했다. 기준선 확보.

### 서브프로세스 커버리지 — 침묵 과소 집계가 실재한다

자식 프로세스에서만 import되는 모듈(`child_only.py`)로 측정했다.

|조건|child-only 문장 커버리지|
|---|---|
|`COVERAGE_PROCESS_START` 없음|**0 / 2**|
|`COVERAGE_PROCESS_START` 설정|**2 / 2**|

환경변수 하나로 0%와 100%가 갈린다. 이 차이는 TEM 수식의 커버리지 항으로 바로 들어가므로
([ADR-0005](../adr/0005-tem-formula-freeze.md)) 점수를 조용히 낮춘다.

**그리고 이를 켜기 위해 프로젝트를 수정할 필요가 없다.** coverage 7.16.0은 설치 시
자기 자신의 `a1_coverage.pth`를 site-packages에 넣는다:

```
import sys; exec('import os\n\nif os.getenv("COVERAGE_PROCESS_START") ...
```

즉 ici는 **환경변수만 설정하면 된다.** site-packages에 쓰지 않으므로 R02가 유지된다.
단, coverage가 프로젝트 venv에 설치돼 있어야 그 `.pth`가 존재한다.

측정 중 걸린 함정 하나를 남긴다: `rm -f .coverage*` 글롭은 **`.coveragerc`까지 지운다.**
그러면 `COVERAGE_PROCESS_START`가 없는 파일을 가리키게 되고, 그 인터프리터의 **모든 이후
서브프로세스가 stderr로 traceback을 뱉는다.** 도구 출력 parser에 쓰레기가 섞이는 경로다
([SPEC-02 §5](../spec-02-distribution-execution.md), [SPEC-03 §5](../spec-03-analysis-engines.md)).

### overlay 경로 — 구조적으로 안전하지 않다

overlay는 ici가 bundle의 pytest/coverage를 프로젝트 인터프리터에 `PYTHONPATH`로 주입하는
방안이다. R02(프로젝트별 반복 설치 축소) 관점에서 매력적이다. **두 가지가 측정으로 부정됐다.**

**1) 인터프리터 버전별 의존 집합이 다르다.** overlay를 core의 3.13으로 설치한 뒤 프로젝트의
3.10에 주입하면:

```
ModuleNotFoundError: No module named 'exceptiongroup'
```

`BaseExceptionGroup`은 3.11+ 내장이므로 3.13용 해석에는 `exceptiongroup` 백포트가 빠진다.
3.10은 그것을 필요로 한다. **즉 overlay는 대상 Python 버전별로 따로 해석해 만들어야 한다.**
하나의 overlay를 모든 프로젝트에 주입하는 그림은 성립하지 않는다.

**2) 프로젝트가 이미 가진 pytest를 덮는다.** 프로젝트 venv에 pytest가 있는 상태에서
overlay를 `PYTHONPATH`에 올리면 해석 결과는 overlay 쪽이다:

```
resolved module: <overlay>/pytest/__init__.py
```

`PYTHONPATH`가 site-packages보다 앞서므로 당연한 결과지만, 결과적으로 **프로젝트의 테스트
도구를 조용히 교체한다.** [SPEC-03 §7](../spec-03-analysis-engines.md)의
"기존 동작을 존중한다"와 정면으로 충돌한다.

→ **결론: overlay는 기본값이 아니며 보류한다.** [ADR-0007](../adr/0007-project-test-provider.md).

### core fallback은 관측 가능하고, 다행히 조용하지 않다

|항목|값|
|---|---|
|core 런타임|3.13.7|
|프로젝트 런타임|3.10.20|
|core에 pytest 존재|**없음**|

현행 `_resolve_python`이 `sys.executable`로 fallback하면
([inventory/execution-flow.md §4](../inventory/execution-flow.md) 1번 행) 테스트가 3.10.20
대신 3.13.7에서 돌아간다 — 두 값이 다르므로 **관측 가능한 오류**다. 그리고 bundle의 core
런타임에는 pytest가 없으므로 fallback은 성공한 척하지 않고 **크게 실패한다.** 이는
[SPEC-02 §3](../spec-02-distribution-execution.md)의 "core Python으로 fallback 금지"를 강제하기
쉽게 만드는 조건이다.

### 프로젝트 venv 격리

`--system-site-packages` 없이 만든 venv는 시스템 site-packages를 보지 않는다 (PASS).
처음 작성한 검사는 `/usr/lib/python3` 문자열을 찾아 **venv의 stdlib를 site-packages로 오인**해
false positive를 냈다. `site.getsitepackages()`를 venv prefix와 비교하는 방식으로 고쳤다.

## 4. 컴파일러 신원 (시험 6)

`probe-compiler.sh`. 이 컨테이너에는 GCC 13.3.0과 clang 18.1.3이 있고 **qmake·Qt는 전무**하다.

### 현행 구현이 이미 compile DB의 컴파일러를 지킨다

`compile_commands.json`이 `clang++`를 지정한 상태에서 `g++`를 **PATH 앞쪽**에 두고
(호출을 기록하는 shim으로) `ici verify --profile fast`를 돌렸다. 기록된 호출:

```
clang++ -std=c++17 -I<src> ... -fsyntax-only ...        ← 실제 분석
clang++ -std=c++17 -I<src> ... -E -H -o /dev/null ...   ← include 추적
clang++ -std=c++17 -I<src> ... -S -o /dev/null ...      ← unused-function
clang++ --version / -dumpmachine                        ← 능력 probe
g++     --version / -dumpmachine                        ← 능력 probe만
gcc     --version / -dumpmachine                        ← 능력 probe만
```

**모든 실제 컴파일·분석 호출이 compile DB의 `clang++`와 DB의 정확한 플래그를 썼다.**
`g++`/`gcc`는 `--version`과 `-dumpmachine`만 받았다 — 컴파일이 아니라 능력 탐지다.

즉 [SPEC-02 §3](../spec-02-distribution-execution.md)의
"실제 compiler: compile DB/빌드 정의의 실제 invocation 기준. PATH의 최신 GCC로 덮어쓰지 않음"은
**현행 구현이 compile DB 경로에서 이미 만족한다.** 고쳐야 할 위반이 아니라 **보존해야 할
자산**이다. [requirements-traceability.md](../requirements-traceability.md)의
"이미 충족 — 보존 대상" 분류에 R08 관련 항목으로 추가해야 한다.

남는 주의점 두 개:

- 능력 probe는 선택 범위와 무관하게 돌았다. WP00이 기록한
  `probe_all_tools=True` 기본값과 일치한다 ([execution-flow.md §2](../inventory/execution-flow.md)).
  담당 [#204](https://github.com/jihoon22-lee/ici/issues/204).
- probe로 알아낸 `g++` 신원을 **컴파일 컴파일러로 보고해서는 안 된다.** 결과 스키마에서 두
  역할을 구분해야 한다. 담당 [#211](https://github.com/jihoon22-lee/ici/issues/211).

### 부수 확인 — 현행 설정이 `schema_version`을 거부한다

fixture의 `ici.toml`을 [SPEC-01 §4](../spec-01-workspace-config-cli.md)의 목표 스키마대로
`schema_version = 1`로 쓰자 현행 검증기가 거부했다:

```
Configuration error: schema_version is an unknown configuration key
```

SPEC-01이 스스로 밝힌 "목표 계약이며 현행 지원을 주장하지 않는다"가 실제로 그렇다는 확인이다.
동시에 **버전 있는 TOML 도입은 drop-in이 아니라 migration이 필요하다**는 뜻이다.
담당 [#203](https://github.com/jihoon22-lee/ici/issues/203),
[#225](https://github.com/jihoon22-lee/ici/issues/225).

### qmake — 측정 불가

qmake·qmake6·Qt 개발 패키지가 하나도 없다. **동작한다고 가정하지 않았다.**
현장 확인 항목 (담당 [#212](https://github.com/jihoon22-lee/ici/issues/212),
[#226](https://github.com/jihoon22-lee/ici/issues/226)):

- PATH에 qmake 설치가 두 개일 때 프로젝트 정의가 고르는 쪽이 선택되는지
- SUBDIRS 공유 빌드 디렉터리가 여러 analysis unit에 공급되는지
- moc/uic/rcc 생성 입력이 선언된 입력으로 나타나는지
- `QTDIR`/`QT_PLUGIN_PATH`/`LD_LIBRARY_PATH`가 child로 보존되는지

마지막 항목은 **qmake 없이 이미 측정됐다** — `smoke-environment.sh` 시험 3이 `QTDIR`과
`LD_LIBRARY_PATH` 보존을 확인한다. qmake가 필요한 것은 앞의 세 개다.

## 5. 지원 상태 갱신

[SPEC-05 §1](../spec-05-verification-transition.md) 지원표에 대한 이번 시험의 기여다.
WP00에서 `planned`였던 항목 중 일부가 `tested`로 올라가고, 나머지는 이유와 함께 남는다.

|축|WP00|WP01 이후|근거|
|---|---|---|---|
|독립 런타임이 ici core를 실행|`planned`|**`tested`** (개발 환경)|bundle에서 `ici 0.11.0` 실행|
|런타임 이동·공백 경로·clean HOME|`planned`|**`tested`**|smoke 5·6·7|
|read-only 설치 디렉터리|`planned`|**`tested`**|smoke 8 (실제 ro 바인드 마운트)|
|core / project 양방향 분리|`planned`|**`tested`**|smoke 2·3|
|launch path 보존 필요성|`planned`|**`tested`** (차이 실측)|smoke 4|
|CA 신뢰 보존 요구|미인식|**`tested`** (신규 제약 발견)|§1 CA 절, smoke 10|
|프로젝트 pytest/coverage 경로|`planned`|**`tested`**|tools 1·2|
|ici-managed overlay|`planned`|**`unsupported` (보류)**|tools 3·4 — 구조적 실패|
|compile DB 컴파일러 준수|미인식|**`tested`** (이미 충족)|compiler 2|
|glibc 하한|추정 금지|**2.17 실측**|manifest|
|RHEL 8.10 실행|`untested`|**`untested`**|해당 환경 아님|
|CPU/ISA 하한|`untested`|**`untested`**|§6|
|qmake·Qt|`untested`|**`untested`**|도구 부재|
|오프라인 (syscall 수준)|`untested`|**`untested`**|proxy 제거는 계약 확인일 뿐|

## 6. 여전히 추정으로 확정하지 않은 것

| # | 항목 | 왜 못 정했나 | 담당 |
|---|---|---|---|
|1|CPython 최종 버전·패치|3.13.7은 **시험 후보**다. 사내 환경 실행 전 고정하지 않는다|[#202](https://github.com/jihoon22-lee/ici/issues/202)|
|2|RHEL 8.10 실제 실행|glibc 2.17 심볼 요구는 확인했지만 심볼 요구 충족 ≠ 동작 확인|[#226](https://github.com/jihoon22-lee/ici/issues/226)|
|3|CPU/ISA 하한|PBS 바이너리의 `.note.gnu.property`가 비어 있어 이 방법으로는 판정 불가. 현장 CPU에서 실행 확인 필요|[#226](https://github.com/jihoon22-lee/ici/issues/226)|
|4|RHEL의 CA 경로|PBS 컴파일 기본값은 `/etc/ssl/cert.pem`. RHEL은 `/etc/pki/tls`를 쓰고 `/etc/ssl/certs`가 심볼릭 링크인 경우가 많다. **확인 필요**|[#226](https://github.com/jihoon22-lee/ici/issues/226)|
|5|bundle 크기 예산|155 MB는 trim 전 측정값이다. 폐쇄망 배포 허용 크기를 정하지 않았다|[#202](https://github.com/jihoon22-lee/ici/issues/202)|
|6|Qt·GCC 지원 하한|qmake·Qt 부재|[#214](https://github.com/jihoon22-lee/ici/issues/214), [#226](https://github.com/jihoon22-lee/ici/issues/226)|
|7|overlay를 버전별로 만들 경우의 비용|보류 결정으로 충분했으므로 측정하지 않았다|[#216](https://github.com/jihoon22-lee/ici/issues/216)|
|8|재현 가능한 bundle 빌드|같은 입력으로 두 번 빌드해 digest를 비교하지 않았다|[#202](https://github.com/jihoon22-lee/ici/issues/202)|

## 7. 후속 WP로 넘기는 구체적 항목

| # | 항목 | 담당 |
|---|---|---|
|1|`_ensure_global_default_config`가 clean/read-only HOME에 파일을 쓴다|[#203](https://github.com/jihoon22-lee/ici/issues/203)|
|2|현행 검증기가 `schema_version`을 거부 → 버전 TOML은 migration 필요|[#203](https://github.com/jihoon22-lee/ici/issues/203), [#225](https://github.com/jihoon22-lee/ici/issues/225)|
|3|`COVERAGE_PROCESS_START`를 명시 설정하고 설정했다는 사실을 결과에 기록|[#216](https://github.com/jihoon22-lee/ici/issues/216)|
|4|능력 probe의 컴파일러 신원과 컴파일 컴파일러를 스키마에서 분리|[#211](https://github.com/jihoon22-lee/ici/issues/211)|
|5|`probe_all_tools` 기본값을 선택 범위에 맞게 좁히기|[#204](https://github.com/jihoon22-lee/ici/issues/204)|
|6|`SSL_CERT_FILE`/`SSL_CERT_DIR` 보존을 런처 계약과 테스트로 고정|[#202](https://github.com/jihoon22-lee/ici/issues/202), [#205](https://github.com/jihoon22-lee/ici/issues/205)|
|7|`launch_path`/`real_path` 분리를 `ResolvedTool`에 구현|[#204](https://github.com/jihoon22-lee/ici/issues/204)|
|8|core fallback을 진단으로 바꾸기 (현재 `sys.executable`)|[#204](https://github.com/jihoon22-lee/ici/issues/204), [#216](https://github.com/jihoon22-lee/ici/issues/216)|
