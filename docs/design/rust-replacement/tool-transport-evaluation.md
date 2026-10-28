# R0 native tool pack·TLS transport 실행 근거

2026-10-28 `feat/rust-native-core`, 기준 계획 commit `c39f37e8`에서 수행했다.
이는 R0 의존성 선택 근거이며 R3 provider, R7 publish, R8 배포 또는 전체 Rust parity
완료 판정이 아니다. 제품 코드는 아직 이 독립 실험을 호출하지 않는다.

## 1. 고정한 공급 입력

기존 공개 oracle의 Ruff 버전과 비교하기 위해 Ruff 0.16.4를 선택했다. ty는 조사 시 실제
공개된 0.0.93을 평가했다. 자동 `latest` 조회는 이 기록 작성에만 사용했으며 제품 pack은
아래 immutable URL·archive digest·실행 파일 digest로 고정해야 한다.

|도구|공식 archive|archive SHA-256|실행 파일 SHA-256|
|---|---|---|---|
|Ruff 0.16.4|[x86_64-unknown-linux-musl](https://github.com/astral-sh/ruff/releases/download/0.16.4/ruff-x86_64-unknown-linux-musl.tar.gz)|`0c2a1616b0782dbf7ee04239576887c4c894277189e04ba94ff3160eba9c1863`|`7e400fc9f00bb64f0cf90824c3a17364530f0455a5d3295eb8eb9003cdc4a225`|
|ty 0.0.93|[x86_64-unknown-linux-musl](https://github.com/astral-sh/ty/releases/download/0.0.93/ty-x86_64-unknown-linux-musl.tar.gz)|`7b5818953dc37b90bb032298702538ad5cc971024ba3c1bebe3a2795e6761272`|`b7884067495f28d7595ef68e9becf447880d46df7edd1b47d5faf5c485a0096a`|

두 archive를 실제 익명 HTTPS로 내려받고 GitHub asset API digest와 upstream `.sha256`에
모두 대조했다. 크기, license URL 및 license SHA-256까지
[`native-tool-inputs.json`](../../../tests/rust-parity/transport/native-tool-inputs.json)에 보존했다.
공식 [Ruff 설치 문서](https://docs.astral.sh/ruff/installation/)와
[ty 설치 문서](https://github.com/astral-sh/ty/blob/0.0.93/docs/installation.md)는 standalone
배포 경로를 제공한다. 실행 시 Python package 설치 경로를 사용하지 않았다.

[Ruff 0.16.4 LICENSE](https://github.com/astral-sh/ruff/blob/0.16.4/LICENSE)와
[ty 0.0.93 LICENSE](https://github.com/astral-sh/ty/blob/0.0.93/LICENSE)는 MIT다.
실제 내려받은 root license를 확인한 것이며 모든 전이 의존성의 재배포 notice 검사가
끝났다는 의미는 아니다. R8 pack에는 해당 버전의 root license와 third-party notices,
버전·target·원본 URL·두 digest를 포함하고 실제 archive 설치 검사로 닫는다.

`file`은 두 실행 파일을 `ELF 64-bit LSB pie executable, x86-64, static-pie linked`로
보고했다. `readelf -l`의 PT_INTERP와 `readelf -d`의 DT_NEEDED가 모두 없었다.
WSL2 x86_64/Linux에서 실제 실행했다. 도구가 host glibc/OpenSSL을 동적으로 요구하지
않음을 확인했지만 다른 kernel, RHEL, 하위 CPU ISA의 실행을 시험한 것은 아니다.
CPU 최소 하한·기타 플랫폼은 R8의 별도 실측 전까지 `limited`다.

## 2. Python 없는 PATH·native 설정·실행 trace

[`verify_native_tools.py`](../../../tests/rust-parity/transport/verify_native_tools.py)는
새 프로젝트와 비어 있는 PATH에서 절대 경로의 두 native binary를 직접 실행한다.
Python은 개발용 fixture harness이며 제품 fallback이 아니다. `strace -f -e
trace=process,network`로 각 invocation을 관찰했다.

|실제 fixture|관측 결과|
|---|---|
|Ruff/ty `--version`|각각 0.16.4/0.0.93, exit 0|
|Ruff native `ruff.toml` select/exclude, 명시 파일 + `--force-exclude`|F821 1개, 행 1/열 1, excluded 파일 진단 없음, exit 1|
|Ruff native `required-version ==0.0.1` 불일치|명시 오류, exit 2; 다른 실행 파일로 바꾸지 않음|
|ty `value: int = "bad"`|SARIF `invalid-assignment`, 행 1, exit 1|
|`[tool.ty.rules] invalid-assignment="ignore"`|같은 코드에서 native 설정을 읽어 진단 0, exit 0|
|ty 잘못된 severity 설정|설정 오류, exit 2|
|ty 외부 dependency 부재|`unresolved-import`, native severity error, exit 1|

8개 invocation 모두 execve가 지정 native binary 1회뿐이었다. 자식 Python 실행과
network connect syscall은 0이었다. 이것은 이 fixture의 관측 범위이며 ty의 모든 환경
탐색 경로에서 interpreter probe가 절대 없다는 주장은 아니다. 프로젝트 환경을 명시한
경로의 별도 probe·부작용 계약은 R3에서 검증해야 한다.

실제 ty 0.0.93 CLI에는 `--output-format json`이 없다. provider는 검증한 SARIF 2.1.0
출력을 파싱하거나 별도 고정한 format을 써야 한다. SARIF URI는 absolute `file:` URI였으며
product location 변환에서 project root 안으로 검증·상대화해야 한다.

### 제품 선택·판정 계약

R3의 resolver는 `명시 경로 → 프로젝트 동일 도구 → 검증 pack → 문서화된 PATH`를 따른다.
이 실험은 native 도구의 실패를 관측했으며 product resolver의 그 순서를 아직 구현하거나
검증하지 않았다. 깨진 명시 경로·required-version 불일치는 설명 가능한 실패로 남기고
pack으로 조용히 바꾸지 않는 fixture를 R3에서 추가한다.

기존 mypy 설정은 mypy 선택 근거다. mypy와 ty 설정이 충돌하면 선택을 설명하거나 명시
override를 요구한다. ty를 넣었다고 기존 mypy 결과·fingerprint와 비교 가능하다고 표시하지
않는다. 새 no-config 프로젝트의 ty는 advisory로 시작하되 native severity/rule/location은
보존한다. 실제 `invalid-assignment`와 환경 부재의 `unresolved-import`가 모두 exit 1이므로
native exit만으로 검사 준비 실패와 위반을 합치지 않는다. 깨진 config/도구/출력의 exit 2는
advisory라는 이유로 성공으로 바꾸지 않는다. 제품 gate 매핑은 R3/R7 acceptance 항목이다.

## 3. TLS transport 선택과 실제 시험

R0 선택은 `reqwest = 0.12.28`, `default-features=false`,
`features=["blocking", "rustls-tls-native-roots"]`다. 실험의
[`Cargo.lock`](../../../tests/rust-parity/transport/Cargo.lock)에 전이 입력을 고정했다.
[Rustls native-roots feature](https://docs.rs/crate/reqwest/0.12.28/features)는 이 조합에서
`rustls-native-certs 0.8.5`를 사용한다. 실제 `cargo tree -e features`에 `webpki-roots`,
`native-tls`, `openssl` 연결은 없었다. `openssl-probe 0.2.2`는 시스템 CA 위치 탐색이며
OpenSSL TLS 라이브러리 연결이 아니다.

[rustls-native-certs](https://docs.rs/rustls-native-certs/0.8.5/rustls_native_certs/)
소스와 실제 실행에서 기본 platform trust store를 사용하고 `SSL_CERT_FILE` 또는
`SSL_CERT_DIR`가 지정되면 그 위치로 trust store를 **대체**함을 확인했다. 사설 CA를
system store에 root 권한으로 설치하지 않고 fixture 범위의 명시 PEM 경로로 시험했다.
사용자 설정의 추가 CA를 지원할 때는 system roots에 더하는지 대체하는지 명확히 구분하고,
기존 환경 변수 의미를 조용히 바꾸지 않는다.

Rust client는 HTTPS-only, redirect 없음, connect 2초/전체 3초 timeout, 최대 response
4096 bytes로 구성했다. URL/credential을 포함할 수 있는 Error Display를 그대로 출력하지
않는다. 별도 JSON의 publication 상태로 결과를 보고하며 분석 결과를 생성하거나 바꾸지
않는다. 이 JSON은 실험용 출력이며 제품 publish schema를 새로 정의한 것이 아니다.

|실제 fixture|관측 결과|
|---|---|
|신뢰되지 않은 사설 CA|transport failure, exit 2|
|명시 사설 CA + `/api/v3/repos/example/project`|HTTP 200, 정확한 GHES prefix 보존, exit 0|
|SAN과 hostname 불일치|transport failure, exit 2|
|HTTPS_PROXY CONNECT 경유|proxy CONNECT 1회 및 HTTP 200, exit 0|
|도달할 수 없는 명시 proxy|transport failure, exit 2|
|같은 proxy + NO_PROXY loopback 예외|직접 연결 HTTP 200, exit 0|
|서버 HTTP 403|publication FAILED, HTTP status 403 보존, exit 2|
|응답 지연|3초 timeout, exit 2|
|5000-byte 응답|size bound 실패, exit 2|
|302 redirect|따라가지 않음, HTTP status 302, exit 2|
|평문 HTTP URL|HTTPS-only 오류, exit 2|
|기본 system CA + 공개 `https://api.github.com`|HTTP 200, exit 0|

12/12 통과. private CA key/cert와 server/proxy는 fixture temporary directory/loopback에만
생성하고 종료·삭제했다. 사용자 CA store·전역 proxy·다른 제품은 수정하지 않았다.
`ldd`에서 이 GNU-host debug prototype은 libc/libgcc를 동적으로 사용했고 OpenSSL/CPython은
연결하지 않았다. **ici core 정적 배포 증명은 아니다.** 최종 target build는 R8 책임이다.

이 실험은 GET transport만 다룬다. PAT auth·POST/PUT body·upload streaming·idempotency·
retry·redaction·GHES 실제 서버·corporate interception proxy는 R7의 실제 publish fixture와
R8 지원 근거로 남는다. 임의 TLS 검증 해제 fallback은 추가하지 않는다.

## 4. identity·배포로 전달할 조건

- 경로/버전 문자열만으로 도구 동일성을 판정하지 않는다. canonical executable path,
  byte digest, version probe 결과, provider parser version, tool pack manifest digest,
  target, argv, 관련 환경과 native config 내용/closure를 입력에 넣는다.
- Ruff extend/nested config, mypy/ty config, Cargo/Node lock/plugin/extends 등 간접 입력을
  전부 식별하지 못하면 cache reuse와 baseline resolved 증거를 제한한다.
- 명시한 도구가 바뀌거나 같은 경로의 bytes가 바뀌면 toolchain identity가 바뀐다.
  provider 변경(mypy→ty), 규칙 설정 변경, advisory→required 변경도 비교 가능성에 반영한다.
- 설치·명시 setup만 pack을 다운로드한다. `analyze/verify/plan`은 설치 명령을 호출하지 않는다.
  archive traversal/symlink 검증, digest 검증 후 atomic activation, versioned cache namespace,
  user data를 덮지 않는 rollback은 R8 구현·실패 주입으로 닫는다.
- 준비된 tool pack은 자체 Python engine이나 worker가 아니다. 기본 자체 Python 분석은
  Rust parser/규칙으로 동작해야 하며 도구가 없을 때 Python backend로 대체하지 않는다.

## 5. 재실행 명령과 검증 한계

```bash
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/tool-spike/target \
  cargo build --locked --manifest-path tests/rust-parity/transport/Cargo.toml
.venv/bin/python tests/rust-parity/transport/verify_transport.py \
  /tmp/ici-rust-replacement-20261028/tool-spike/target/debug/ici-transport-spike \
  --public-system-ca
.venv/bin/python tests/rust-parity/transport/verify_native_tools.py \
  --ruff <verified-ruff-binary> --ty <verified-ty-binary>
```

공개 system-CA 요청은 선택 옵션이다. 기본 transport fixture는 loopback만 사용한다.
실측 환경은 Rust/Cargo 1.99.1, Python 3.10.21 개발 harness, WSL2 kernel 6.18.33.2,
Ubuntu glibc 2.43, OpenSSL 3.5.5 fixture certificate generator였다.
Cargo build 21.91초, fixture Clippy `-D warnings`, Cargo fmt, 두 harness의 Ruff check/format을
실행했다. 전체 제품 pytest/pyz/corpus는 이 실험에서 반복하지 않았으며 통과로 계산하지 않는다.
제품 전체 완료와 license/platform closure는 해당 R3/R7/R8/R9 원장으로 판단한다.
