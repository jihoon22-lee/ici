# 범용 정적 분석과 설치

## 설치와 실행

Python 3.10 이상, Linux/WSL/CI에서 GitHub Release의 wheel을 격리 설치한다.
배포 채널은 기존 GitHub Release다. 별도 PyPI registry 공개는 하지 않는다.

```bash
# 같은 release에서 wheel과 ici-package.sha256을 내려받은 뒤
sha256sum --ignore-missing --check ici-package.sha256
uv tool install ./ici-<version>-py3-none-any.whl
ici --version
ici analyze --plan
ici analyze --result result.json --events events.jsonl --json
ici report --result result.json --out report.html
```

uv 대신 `python3 -m venv ~/.local/share/ici-venv`를 만들고 그 환경의
`bin/python -m pip install ./ici-<version>-py3-none-any.whl`로 설치할 수 있다.
프로젝트 venv와 ici 설치 환경은 분리한다. wheel 의존성은 설치 때 내려받을 수 있다.
오프라인 설치는 wheel과 의존성을 사전 준비하여 `pip --no-index --find-links`로 설치한다.
분석 중 pip/npm/uv 설치는 호출하지 않는다. pyz는 기존 단일 파일 소비자를 위해 계속 배포한다.

## 선택과 설정

`analyze`는 ici.toml 없이 실행한다. VCS root가 있으면 checkout을 범위로 삼고,
없으면 현재 디렉터리를 사용한다. node_modules, target, build, dist, .venv, .ici는
자동 범위에서 제외한다. 기존 ici.toml은 무시하지 않는다. legacy 설정은
`ici next migrate`의 preview를 검토해 next 형식으로 이전하거나 기존 verify를 사용한다.
다중 component/다른 source root/정밀한 exclude가 필요하면 `ici init --preview`로
시작해 `[workspace]` 및 `[[components]]`를 선언한다. 자동 발견은 단일 `project`
component이며, 중첩 package별 별도 실행이 필요하면 각 package에서 명시적 설정을 쓴다.

```bash
ici analyze --language rust --plan --json
ici analyze --language python --language cpp --profile fast
ici analyze --component backend --baseline baseline.json --result current.json
ici diff baseline.json --result current.json
```

프로필은 비용, 언어/component는 범위다. deep도 analyze에서 테스트를 켜지 않는다.
`verify`의 기존 Python/C++ 선택은 유지되어 명시된 테스트/coverage/통합 실행을 사용할 수 있다.
기존 설정의 Rust 선언은 이전에는 미지원이었지만 이제 Clippy를 선택한다.
필요한 도구/manifest/lock/dependency가 없으면 incomplete이며, 기존의 미지원 limitation과 다르다.

`analyze` 결과는 static subset이므로 workspace 전체 PASS로 표시하지 않는다.
`--require-full`은 선언된 component/언어 범위 누락을 검사하며 runtime suite를 추가하지 않는다.

|언어|검사/도구|실행 조건과 한계|
|---|---|---|
|Python|기존 AST 검사, Ruff lint/format, mypy 또는 선언된 ty|원래 tool config 사용. 누락 도구는 blocked, 프로젝트 Python을 ici runtime으로 대체하지 않음|
|C/C++|기존 정적 검사·compiler diagnostics·clang-tidy|compile context는 기존 compile DB/명시 설정 사용. prepare가 필요한데 없으면 incomplete|
|Rust|Cargo Clippy|Cargo.toml/Cargo.lock·의존성과 toolchain을 사전 준비. `--offline --locked`, jobs=2, `.ici/cargo-target` 사용. build.rs/proc-macro 실행 가능. fast에서 blocked|
|JavaScript|ESLint|project `node_modules/.bin` 우선, 없으면 PATH. 원래 설정/plugin 사용. JS 설정/plugin 실행 가능|
|TypeScript|ESLint와 tsc noEmit|tsconfig/jsconfig 사용. ESLint TS parser/plugin은 프로젝트 책임. 참조 프로젝트의 미생성 output을 자동 build하지 않음|

ESLint가 파일을 무시하거나 설정하지 못한 경우 이를 정상 진단 없는 PASS로 바꾸지 않는다.
Cargo/tsc의 native target/project closure는 아직 완전히 열거하지 않으므로 두 provider의
이전 finding을 baseline에서 resolved로 확정하지 않는다. 실행 진단과 baseline 증거 완료는
독립적이다. parser가 알 수 없는 출력이나 도구 실패는 incomplete로 남긴다.
기존 분석 descriptor는 제거하지 않았으며 [분석 인벤토리](design/ici-next/inventory/current-engines.md)에
원래 기능의 위치가 있다. 신규 언어에서 Python/C++ 전용 metric을 흉내 내지 않는다.

## 결과와 report UX

- `ici.next.run` v2: 고유 run_id, source/policy/toolchain identity, check별 범위와 완료,
  finding의 native rule·파일·라인·component/task, 독립 gate/scope/publication.
- `ici.next.event` v1: 같은 run_id의 JSONL 진행 이벤트. 최종 결과를 판정 근거로 사용한다.
- `--json`은 stdout에 결과만, 안내는 stderr에 쓴다. exit 0=선택 gate 통과,
  1=위반, 2=요청/설정 오류, 3=미완료, 130=취소다.
- HTML은 외부 CDN 없이 동작한다. findings 검색, 관련 위치, suppression 이유/출처,
  baseline new/unchanged/resolved/carried, 선택 검사별 실행·증거 완료를 보여준다.
- `report`/`diff`/`init` 주 명령은 기존 next 명령과 같은 구현을 쓴다.
  기존 `next` namespace와 legacy verify/pyz는 호환을 위해 유지한다.

## 보조자료 가져오기

```bash
ici import-report tests.xml --format junit --scope 'commit abc, unit suite' --output unit.json
ici import-report coverage.info --format lcov --scope 'commit abc, native tests' --output coverage.json
ici import-report external.sarif --format sarif --scope 'commit abc, security scan' --output security.json
ici report --result result.json --auxiliary unit.json --auxiliary coverage.json --out report.html
```

보조자료는 `ici.auxiliary` v1이며 자체 source SHA-256, imported_at, observed_at, scope를
보존한다. 관측 시점을 알면 `--observed-at 2026-10-28T12:00:00+09:00`을 전달한다.
미지정 시 unknown이며 import 시각을 분석 시각으로 가장하지 않는다. gate는 항상
NOT_EVALUATED다. 원본 run JSON/선택 gate/coverage 완료 여부는 변경하지 않는다.
기존 output은 덮어쓰지 않는다. report의 `--auxiliary`는 HTML에만 적용한다.

지원 subset은 SARIF 2.1.0 진단/관련 위치, JUnit testcase 결과, LCOV DA line hits다.
LCOV function/branch 합산은 하지 않는다. JUnit line은 producer별 기준이 다르므로
native_line/producer-defined로 보존한다. 입력은 UTF-8 32 MiB 이내, 프로젝트 상대
경로여야 한다. 절대/외부 URI, DTD/entity는 명시 오류로 거부한다. 입력의 소스를 읽거나
외부 URI를 fetch하지 않는다. 포맷 원문 근거:
[SARIF](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html),
[LCOV](https://github.com/linux-test-project/lcov/blob/master/docs/man/geninfo.rst).

## 지원 근거와 릴리스

|환경/도구|상태|근거 또는 제한|
|---|---|---|
|Linux x86_64, Python 3.10|tested|pytest, provider 실제 fixture, wheel 격리 설치, pyz smoke를 릴리스 원장에 기록|
|WSL/Linux CI|지원 대상|동일 CLI 계약. 도구와 프로젝트 의존성은 각 환경에서 준비|
|Clippy/Rust|tested|rustc 1.99.1, Clippy 0.1.99, build.rs 실행 fixture|
|Node/TS/ESLint|tested|Node 24.18.0, TypeScript 7.0.2, ESLint 10.13.0|
|RHEL 8.10/GHES|limited|기존 pyz/bundle 계약 및 현장 인수 문서. 일반 package 릴리스 선행 조건 아님|
|macOS/native Windows|planned|실제 OS E2E 미실행, Linux 결과로 동등 지원을 주장하지 않음|
|PE/Mach-O 및 새 플랫폼|unsupported|현재 ABI 경로는 ELF 기반. 별도 parser·native tool fixture와 OS 실행 근거가 필요해 이번 릴리스에서 제외|

전용 bundle은 기존 candidate/환경별 경로다. 일반 package가 bundle의 RHEL 인수를
대신하지 않으며, 그 인수가 없다는 이유로 Linux package 공개를 막지 않는다.
quality-zoo는 ici 소유다. 외부 소비자 Lens의 현재 저장소는
[lens-diagnostics](https://github.com/jihoon22-lee/lens-diagnostics)이며 역사적 toy-projects
SHA/provenance는 변경하지 않는다. 릴리스 원장은
[generalization-progress](design/generalization-progress.md)에 있다.
