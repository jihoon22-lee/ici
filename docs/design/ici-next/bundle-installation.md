# bundle 설치 안내: pyz와 bundle의 역할 구분

| | |
|---|---|
|상태|**bundle은 candidate.** stable 배포는 `dist/ici.pyz`이며 이 문서가 그것을 바꾸지 않는다.|
|근거 이슈|[WP04 #202](https://github.com/jihoon22-lee/ici/issues/202) 작업 7|
|구현|[`scripts/bundle/launcher.sh`](../../../scripts/bundle/launcher.sh), [`scripts/bundle/smoke.sh`](../../../scripts/bundle/smoke.sh)|
|검증|[`tests/test_bundle_smoke.py`](../../../tests/test_bundle_smoke.py), [`tests/test_offline_execution.py`](../../../tests/test_offline_execution.py), [`.github/workflows/bundle-smoke.yml`](../../../.github/workflows/bundle-smoke.yml)|

## 1. 두 배포물은 서로를 대체하지 않는다

|                | `dist/ici.pyz` (stable)                                   | bundle (candidate)                                      |
|----------------|-----------------------------------------------------------|---------------------------------------------------------|
|형태            |단일 파일, sh preamble + zipapp                            |디렉터리 트리 (`bin/ runtime/ app/ tools/ licenses/`)    |
|호스트 Python   |**필요하다.** 3.10 이상을 찾지 못하면 실행되지 않는다      |**필요 없다.** CPython 3.13.7을 자기 안에 들고 있다      |
|크기            |수 MB                                                      |164 MB (측정값)                                          |
|analyzer 도구   |PATH나 프로젝트 `.venv`에서 찾는다                         |`tools/python-static/`에 함께 들어간다 (현재 ruff)       |
|시스템 요구     |Python 3.10+                                               |linux x86_64, glibc 2.17 이상                            |
|상태            |릴리스 경로. tag/version이 여기를 가리킨다                 |**candidate 전용.** stable tag를 자동으로 바꾸지 않는다  |

`ici.pyz`의 런처는 `$ICI_PYTHON`과 `python3.14`~`python3.10`을 차례로 찾는다
([`scripts/launcher.sh`](../../../scripts/launcher.sh)). 그래서 호스트에 3.10 이상이
없거나, 있어도 그 인터프리터가 사이트 패키지 때문에 오염돼 있으면 거기서 막힌다.
bundle은 그 탐색 자체를 없앤다 — **호스트에 Python이 하나도 없어도 된다**는 것이
bundle이 존재하는 이유이고, 그 외의 상황에서는 pyz가 여전히 더 작고 더 단순하다.

**둘 중 무엇을 쓸지는 호스트가 정한다:** 3.10 이상이 있으면 pyz, 없거나 믿을 수 없으면
bundle. 두 가지를 같은 호스트에 함께 두는 것도 막지 않는다 — 서로의 파일을 건드리지 않는다.

## 2. 설치

bundle은 압축을 푸는 것이 설치의 전부다. 설치 스크립트도, 패키지 관리자도 없다.

```sh
tar xzf ici-bundle-<version>-linux-x86_64.tar.gz -C ~/opt/
~/opt/ici-bundle-<version>/bin/ici --version
```

PATH에 넣으려면 symlink 하나면 된다. 런처는 `readlink -f`로 자기 실제 위치를 찾으므로
symlink를 통해 불려도 자기 bundle을 정확히 찾는다 (smoke `symlink` 케이스).

```sh
ln -sfn ~/opt/ici-bundle-<version>/bin/ici ~/.local/bin/ici
```

### root도, 시스템 Python 변경도 요구하지 않는다

`#202` 작업 7이 명시한 조건이다. 근거는 다음 세 가지이고, 전부 측정된 것이다.

|주장|근거|
|---|---|
|설치 디렉터리 밖에 아무것도 쓰지 않는다|smoke `no-writes` — 분석 1회 실행 후 install 디렉터리의 6107개 파일이 **바이트·mtime 모두 그대로**|
|사용자 실행 중 패키지를 설치하지 않는다|smoke `no-installs` — 네트워크 없는 네임스페이스 + 빈 HOME에서 실행 후 HOME에 생긴 것은 `~/.config/ici/ici.toml` 하나뿐. site-packages·dist-info·wheel·pip/uv 캐시 **0개**|
|시스템 Python을 쓰지도 바꾸지도 않는다|런처가 `bundle/runtime/python/bin/python3`를 `-I`로 직접 exec 한다 ([launcher-contract.md](launcher-contract.md))|

쓰기 가능한 디렉터리에 풀 수 있으면 그곳이 어디든 설치 위치다. `/opt`든 `$HOME`이든
NFS 홈이든 같다 — read-only 마운트에서도 돈다 (smoke `read-only`).

출력·캐시는 설치 디렉터리 밖으로 간다. 캐시 기본값은 `$XDG_CACHE_HOME/ici/analysis`,
없으면 `~/.cache/ici/analysis`이고 `ICI_CACHE_DIR`로 바꾼다
([`core/cache_identity.py:default_cache_dir`](../../../src/ici/core/cache_identity.py)).

## 3. 버전 선택

bundle은 버전마다 **독립된 디렉터리**다. 공유 상태가 없으므로 몇 개를 나란히 두든
서로를 가리지 않는다 (smoke `side-by-side`).

```sh
~/opt/
  ici-bundle-0.11.0/
  ici-bundle-0.12.0/
  ici-current -> ici-bundle-0.11.0      # 바꾸고 싶을 때 이 링크만 바꾼다
```

|하고 싶은 것|방법|
|---|---|
|기본 버전을 바꾼다|`ln -sfn ~/opt/ici-bundle-0.12.0 ~/opt/ici-current`|
|한 번만 다른 버전으로 돌린다|`~/opt/ici-bundle-0.12.0/bin/ici verify` — 전체 경로로 부른다|
|프로젝트마다 다른 버전을 쓴다|프로젝트 스크립트에서 해당 bundle의 절대 경로를 쓴다|
|무엇이 돌고 있는지 확인한다|`ici --version`, 그리고 `<bundle>/manifest.json`의 `source_commit`|

**자동 업데이트는 없다.** 실행 경로에 네트워크 호출이 없다는 것이
[`tests/test_offline_execution.py`](../../../tests/test_offline_execution.py)에서
두 방향으로 고정돼 있다 — 트리 전체의 정적 audit(허용 목록은 `engines/publish.py` 하나,
사용자가 이름을 대서 부르는 업로드다)과, 실제 분석 실행 중 CPython audit hook이
`socket.connect`·`urllib.Request`를 한 번도 보지 못한다는 런타임 확인.
**새 버전이 나왔는지 ici가 스스로 확인하는 일은 없다.**

## 4. 제거

```sh
rm -rf ~/opt/ici-bundle-<version>
rm -f  ~/.local/bin/ici            # symlink를 걸었다면
```

bundle 자체는 이걸로 끝이다. 설치 디렉터리 밖에 bundle이 남기는 것이 없다는 근거가
§2의 `no-writes` 측정이다.

**ici를 쓴 흔적은 bundle과 별개로 남는다.** 아래 둘은 pyz로 돌렸을 때도 같은 자리에
생기므로, bundle만 지우려는 것이라면 건드리지 않는다.

|경로|무엇|
|---|---|
|`~/.config/ici/ici.toml`|첫 실행이 만드는 기본 설정. smoke `no-installs`가 빈 HOME에서 실제로 확인한 유일한 생성물이다|
|`~/.cache/ici/analysis`|분석 캐시. `XDG_CACHE_HOME`·`ICI_CACHE_DIR`로 위치가 바뀐다|

## 5. 복구

**bundle이 깨졌을 때 복구 대상은 bundle이지 호스트가 아니다.** 이 구분이 #202가 명시한
조건이다 — *"공용 Python을 복구 대상으로 만들지 않는다."* bundle은 시스템 Python을
읽지도 쓰지도 않으므로, bundle을 아무리 망가뜨려도 호스트의 Python은 그대로다.

|증상|복구|
|---|---|
|새 bundle이 실행되지 않는다|symlink를 이전 bundle로 되돌린다. 이전 디렉터리는 그대로 있다|
|bundle 전체를 믿을 수 없다|`dist/ici.pyz`로 돌아간다. 호스트에 3.10+ 가 있으면 바로 된다|
|어느 쪽이 문제인지 모르겠다|`bash scripts/bundle/smoke.sh <bundle-dir>` — 10개 케이스가 어디서 깨지는지 이름을 댄다|
|파일이 유실됐는지 확인하고 싶다|`manifest.json`의 `runtime.tree_digest`/`application.tree_digest`와 대조한다|

돌아갈 곳이 항상 있는 이유는 bundle이 stable 경로를 **치우지 않기** 때문이다.
`dist/ici.pyz`, `scripts/build-pyz.sh`, `scripts/smoke.sh`, `scripts/launcher.sh`는
그대로 있고 [`tests/test_ici_next_inventory.py`](../../../tests/test_ici_next_inventory.py)가
그것을 고정한다.

## 6. 측정된 것과 측정되지 않은 것

`scripts/bundle/smoke.sh`가 빌드된 bundle에 대해 세는 10개 케이스다.
아래 결과는 개발 컨테이너(linux x86_64, glibc 2.39)에서 갓 빌드한
bundle에 대해 실행한 것이다.

|케이스|무엇을 주장하는가|결과|
|---|---|---|
|`in-place`|빌드한 자리에서 version/help/doctor/분석이 된다|PASS|
|`relocated`|공백이 든 다른 경로로 옮겨도 된다|PASS|
|`symlink`|PATH의 symlink를 통해 불려도 자기 bundle을 찾는다|PASS|
|`clean-home`|HOME이 비어 있어도 된다|PASS|
|`read-only`|read-only 마운트에서 된다|PASS|
|`offline`|네트워크 인터페이스가 **하나도 없는** 네임스페이스에서 된다|PASS|
|`combined`|offline + clean HOME + read-only를 동시에|PASS|
|`no-writes`|설치 디렉터리에 아무것도 쓰지 않는다|PASS|
|`no-installs`|사용자 실행 중 패키지를 설치하지 않는다|PASS|
|`side-by-side`|두 버전이 서로를 가리지 않는다|PASS|

### 측정하지 못한 것

|항목|상태|
|---|---|
|**RHEL 실기 확인**|**미확인.** glibc 하한 2.17은 `runtime/python`의 심볼에서 읽은 값이고, RHEL 호스트에서 실제로 돌려본 결과가 아니다|
|CPU ISA 요구|미확인. WP01 spike가 주장하지 않았고 여기서도 주장하지 않는다|
|x86_64 외 아키텍처|해당 없음. 현재 bundle은 linux x86_64만 만든다|
|C++ 도구 조합|후속 provider WP. 지금 bundle의 `tools/`에는 ruff만 있다|

namespace 권한이 없는 호스트에서는 `read-only`/`offline`/`combined`/`no-installs`가
PASS가 아니라 **BLOCKED**로 보고된다. 이건 통과가 아니라 *재지 못했다*는 뜻이고,
CI는 잴 수 있는 러너에서 `ICI_SMOKE_STRICT=1`로 돌려 BLOCKED를 실패로 취급한다.
잴 수 없는 러너에서는 step summary가 어느 케이스를 재지 못했는지 이름을 댄다 —
8개 PASS 표는 10개 PASS 표와 똑같이 생겼기 때문이다.
