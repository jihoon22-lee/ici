# ici native 설치·업데이트·복구 가이드

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; **📦 native 설치·복구** &bull; [🔁 pyz/uv/pip에서 이전](migration-to-native.md)

Rust native ici([ADR-0008](design/ici-next/adr/0008-rust-native-replacement.md))의 설치,
업데이트, 되돌리기, 제거 방법을 설명한다. 이 배포물은 Python 없이 실행되는 단일 정적
실행 파일과 검증된 native Ruff/ty, 모든 라이선스 고지를 한 archive로 묶는다. 설치와
실행에는 root 권한이 필요하지 않다.

## 1. 지원 범위

|항목|상태|근거|
|---|---|---|
|Linux x86_64 / WSL2 x86_64|`limited`|실측 호스트(WSL2 x86_64)와 GitHub-hosted Ubuntu runner에서 build·설치·분석을 실행한 범위|
|target|`x86_64-unknown-linux-musl` 정적 실행 파일|ELF interpreter와 공유 라이브러리 의존성이 없음(`readelf`로 확인)|
|CPU|`x86-64` baseline으로 컴파일|더 오래된 CPU에서의 실행은 시험하지 않았음|
|aarch64, macOS, native Windows, 기타 kernel|지원하지 않음|실제 검증 없음|

`limited`는 측정한 환경에서만 확인했다는 뜻이다. 정적으로 링크되었다는 사실만으로 다른
kernel·CPU·배포판의 지원을 주장하지 않는다. 각 archive의 `manifest.json`에 build host의
kernel, CPU, compiler·linker digest와 Rust toolchain이 기록된다.

## 2. 배포 파일

GitHub Release `vX.Y.Z`에는 다음 파일이 있다.

|파일|내용|
|---|---|
|`ici-X.Y.Z-x86_64-unknown-linux-musl.tar.gz`|native archive|
|`ici-X.Y.Z-x86_64-unknown-linux-musl.tar.gz.sha256`|archive SHA-256|
|`install-native.sh`|POSIX `sh` bootstrap installer (설치 전에 archive digest를 확인)|
|`ici-X.Y.Z-THIRD-PARTY-NOTICES.txt`|archive 안의 `licenses/THIRD-PARTY-NOTICES.txt`와 같은 bytes|
|`ici-X.Y.Z-release-check.json`|release workflow가 같은 bytes에 실행한 검사 결과|
|`ici-X.Y.Z-supporting.sha256`|installer와 고지 파일의 SHA-256|

archive 구성:

```text
bin/ici                     Rust native core (정적 실행 파일)
tools/bin/ruff, tools/bin/ty 검증된 native 분석 도구 (버전·digest는 tools/manifest.json)
tools/manifest.json
licenses/                   ici MIT, THIRD-PARTY-NOTICES.txt, licenses.json, 도구 라이선스, 보존 소스
manifest.json               파일별 digest·크기·mode와 build 입력 (ici.native-distribution v1)
README.txt
```

archive에는 Python 인터프리터, pyz, wheel, Python 모듈이 들어 있지 않다. release 검사는
이 사실을 member 목록에서 확인한다.

## 3. 설치

```bash
version=X.Y.Z
base="https://github.com/jihoon22-lee/ici/releases/download/v$version"
archive="ici-$version-x86_64-unknown-linux-musl.tar.gz"
curl --fail --location --proto '=https' -O "$base/$archive"
curl --fail --location --proto '=https' -O "$base/$archive.sha256"
curl --fail --location --proto '=https' -O "$base/install-native.sh"
sha256sum --check --strict "$archive.sha256"

sh install-native.sh --archive "$archive" --sha256 "$(cut -d' ' -f1 "$archive.sha256")"
export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/ici/native/bin:$PATH"
ici --version
ici doctor
```

- `curl`은 시스템 CA 저장소와 `HTTPS_PROXY`/`NO_PROXY`를 사용한다. TLS 검증을 끄지 않는다.
- `--sha256`에는 신뢰할 수 있는 경로로 받은 digest를 넣는다. 사내 배포라면 release
  페이지에서 확인한 값을 작업 정의에 고정하는 편이 안전하다.
- installer는 archive를 private 임시 디렉터리로 복사(최대 256 MiB)하고 digest와 x86_64
  ELF header를 확인한 다음, 그 안의 `bin/ici setup`이 archive 전체(manifest, 파일별 digest,
  mode, 경로 안전성, 도구 version probe)를 다시 검증한 뒤에야 설치를 완료한다.
- 기본 설치 위치(`--prefix` 생략 시)는 `$XDG_DATA_HOME/ici/native`, `XDG_DATA_HOME`이 없으면
  `$HOME/.local/share/ici/native`다. 둘 다 없으면 `--prefix`를 요구한다.
- 셸 시작 파일에 PATH를 추가할 때는 native `bin` 디렉터리를 기존 `ici`보다 앞에 둔다.
  충돌 확인은 [이전 가이드](migration-to-native.md#3-path-충돌-확인과-기존-설치-정리)를 참조한다.

설치 위치의 구조:

```text
<prefix>/versions/<version>-<archive sha256>/   검증된 설치본 (archive receipt 포함)
<prefix>/current                                현재 선택 (상대 symlink)
<prefix>/previous                               직전 선택 (상대 symlink)
<prefix>/bin/ici -> ../current/bin/ici          PATH에 넣는 entrypoint
```

새 설치본은 staging 디렉터리에서 전체 검증을 마친 뒤에만 `current`를 원자적으로 바꾼다.
ici가 만들지 않은 `<prefix>/bin/ici`는 덮어쓰지 않는다. 동시에 실행된 설치는 filesystem
lock으로 거부된다.

### 오프라인·폐쇄망

archive, `.sha256`, `install-native.sh` 세 파일을 반입한 뒤 같은 명령을 실행한다. 설치와
분석은 네트워크를 사용하지 않는다. `analyze`/`verify`/`doctor`/`plan`/`report`는 도구를
내려받거나 설치하지 않는다.

## 4. 업데이트와 상태 확인

이미 native ici가 있으면 새 archive를 같은 core로 설치한다.

```bash
ici setup --archive "ici-$version-x86_64-unknown-linux-musl.tar.gz" --sha256 "$sha256"
# 또는 명시 HTTPS 다운로드 (리다이렉트 최대 5회, 최종 bytes를 digest로 확인)
ici setup --url "$base/ici-$version-x86_64-unknown-linux-musl.tar.gz" --sha256 "$sha256"

ici setup --inspect          # 현재/직전 선택, 실행 파일, PATH 충돌
ici setup --inspect --json
```

`--url`은 시스템 CA를 사용한다. 사설 CA는 `SSL_CERT_FILE`/`SSL_CERT_DIR`로 지정하고
proxy는 `HTTPS_PROXY`/`https_proxy`와 `NO_PROXY`를 따른다. 업데이트가 실패하면 현재
선택은 바뀌지 않는다. `--prefix`를 지정해 설치했다면 `setup`에도 같은 `--prefix`를 준다.

## 5. 되돌리기

되돌리기에는 두 가지가 있으며 서로 다르다.

### 5.1 native 설치본 사이의 되돌리기

```bash
ici setup --rollback                     # 직전에 선택했던 검증된 설치본으로
ici setup --activate <version>-<sha256>  # 남아 있는 특정 설치본으로
```

선택(`current`)만 바꾼다. 프로젝트 설정, `.ici/rust-v1` 결과, baseline, suppression, 사용자
선호는 건드리지 않는다. 새 데이터를 옛 snapshot으로 덮어쓰지 않는다.

### 5.2 이전 공개판(v0.13.0 pyz)으로 명시 복귀

native 제품에서 문제가 생겨 Python 제품으로 돌아가야 할 때는 공개 v0.13.0 artifact를
명시적으로 내려받아 쓴다. ici는 이 복귀를 자동으로 하지 않는다.

```bash
base=https://github.com/jihoon22-lee/ici/releases/download/v0.13.0
curl --fail --location --proto '=https' -O "$base/ici.pyz" -O "$base/ici.pyz.sha256"
sha256sum --check --strict ici.pyz.sha256   # c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde
install -m 0755 ici.pyz "$HOME/.local/bin/ici-0.13.0"
"$HOME/.local/bin/ici-0.13.0" --version     # Python 3.10+ 필요 (ICI_PYTHON 또는 PATH의 python3.10+)
```

- 별도 이름(`ici-0.13.0`)으로 두면 PATH의 native `ici`와 섞이지 않는다. `ici`라는 이름으로
  되돌리려면 native `bin` 디렉터리를 PATH에서 빼거나 뒤로 옮긴다.
- 과거 공개 release와 tag는 그대로 보존된다. v0.13.0의 wheel 설치(`uv tool install`/`pip`)도
  같은 release의 파일과 `ici-package.sha256`으로 다시 설치할 수 있다.
- native가 만든 `.ici/rust-v1/` 아래 결과는 v0.13.0이 사용하지 않으며 지우지 않아도 된다.
  native는 기존 설정·baseline·suppression을 자동으로 고치거나 새 형식으로 덮어쓰지 않는다.
  다만 사용자가 같은 경로를 지정한 출력(예: `ici verify --report`의 `verify_report.json`,
  `--write-baseline`의 대상 파일)은 마지막으로 실행한 제품이 쓴 파일이다. 복귀 후 비교에는
  v0.13.0으로 만든 baseline을 사용한다.
- 0.13.x 일반 유지보수는 native cutover로 끝난다. 복귀는 문제를 보고하고 수정판을 기다리는
  동안의 명시적 우회다.

## 6. 데이터 위치

|위치|내용|비고|
|---|---|---|
|`<project>/.ici/rust-v1/result.json`, `result.html`|`ici analyze`의 기본 결과(`ici.next.run` v2)와 오프라인 HTML|`--result`로 변경|
|`<project>/.ici/rust-v1/observations/`|재사용 가능한 분석 관측 cache|`ici cache`로 확인·정리, `--no-cache`로 우회|
|`<project>/.ici/rust-v1/details/`, `runs/`|저장 결과의 상세·보고서 입력|`report`가 읽음|
|`$XDG_CONFIG_HOME/ici/preferences.toml`|개인 선호(출력 등). 팀 검사 정책은 바꾸지 않음|없으면 `~/.config`|
|`$XDG_DATA_HOME/ici/native/`|native 설치본|§3|

`.ici/`는 프로젝트의 생성물이다. 저장소에서는 `.gitignore`에 `.ici/`를 추가한다. 이전 Python
제품의 cache·결과는 새 namespace와 분리되며 native가 자기 cache인 것처럼 읽거나 덮어쓰지 않는다.

## 7. 제거

```bash
rm -rf -- "${XDG_DATA_HOME:-$HOME/.local/share}/ici/native"
```

그다음 셸 시작 파일에서 PATH 항목을 지운다. 프로젝트의 `.ici/rust-v1/`과 설정 파일은 프로젝트
자료이므로 필요할 때 따로 정리한다.

## 8. 라이선스와 고지

archive의 `licenses/`에는 다음이 들어 있다.

- `ici-MIT.txt`: ici 자체 라이선스.
- `THIRD-PARTY-NOTICES.txt`와 `licenses.json`: 실제 build의 Cargo metadata로 만든 core 의존성
  고지, Rust 1.99.1 표준 라이브러리 catalogue와 참조 조항, 정적으로 포함되는 musl 1.2.5
  COPYRIGHT, LLVM libunwind·compiler-rt 라이선스와 contributor credits. `licenses.json`은
  고지 파일의 SHA-256을 기록한다. upstream이 고지를 제공하지 않은 의존성은
  `missing_upstream_notice`로 표시하며 저작권자·연도를 지어내지 않는다.
- `ruff.txt`, `ty.txt`와 각 `*-third-party.txt`: 동봉 도구의 upstream 라이선스(고정 digest)와
  해당 도구 build의 의존성 고지. `third-party/` 아래에 각 도구 compiler의 Rust 표준 라이브러리
  조항이 있다.
- `sources/`: MPL-2.0 `version-ranges` 0.1.1의 원본 소스 패키지와 라이선스(바이트 그대로).
- `Unicode-V3.txt`, `upstream-source-lock.json`, `upstream-notices.json`: Unicode 데이터 라이선스와
  고지 원본의 URL·revision·digest.

release 검사(`scripts/check-native-release.sh`)는 `packaging/native/sources`와
`packaging/native/third-party`의 모든 파일이 archive에 바이트 그대로 들어 있는지, 도구 라이선스와
binary가 고정 digest와 같은지, 위 Rust/musl/LLVM 고지 절이 있는지 확인한다.

## 9. 배포물의 검증 근거

- native candidate는 `candidate-artifact.yml`에서 같은 exact source를 독립 build 디렉터리로 두 번
  build해 bytes가 같은지 확인하고, release 검사와 배포 artifact corpus
  (`tests/rust-parity/distribution/verify_artifact.py`: bootstrap, update/rollback, 손상·위조 archive
  거부, 사설 CA HTTPS setup, Python 없는 오프라인 분석)를 실행한다.
- `release-native.yml`은 다시 build하지 않는다. 같은 commit의 candidate bytes를 out-of-band
  SHA-256으로 확인하고 release 검사를 다시 실행한 뒤 그 bytes를 게시한다.
- 두 build의 동일성은 같은 runner 안의 비교다. build host의 kernel·CPU·compiler digest가
  manifest에 기록되므로 다른 host에서 다시 build한 archive가 같은 digest를 갖는다고 주장하지 않는다.

직접 확인하려면 release tag를 checkout한 저장소에서 다음을 실행한다(네트워크·root 불필요). 고지 파일 비교의 기준이 그 checkout의 `packaging/native/`이다.

```bash
scripts/check-native-release.sh --archive "ici-$version-x86_64-unknown-linux-musl.tar.gz" \
  --expect-version "$version" --evidence /tmp/ici-release-check
```
