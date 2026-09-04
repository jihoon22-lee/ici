# ici (Integrated CI) 사용자 가이드

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; **🚀 사용자 가이드** &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬 개발 환경(WSL/Linux), 사내 폐쇄망(RHEL 8.10/CentOS, tcsh/bash), 그리고 GitHub Actions CI/CD 파이프라인에서 같은 정책·결과 계약을 적용하는 단일 실행형 CI 통합 엔진입니다. OS·컴파일러·Python·검증 도구의 가용성과 버전은 실행 증거로 기록되며, 환경이 다르면 실제 결과도 달라질 수 있습니다.

## 현재 공개 릴리스와 검증된 artifact

현재 공개 stable 릴리스는 [v0.10.2](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)다.
`v0.10.2` tag는 exact `main` commit
[`3b50dd4c485ddab212beb23ff820e82286a06e77`](https://github.com/jihoon22-lee/ici/commit/3b50dd4c485ddab212beb23ff820e82286a06e77)을
가리키며, [exact-main CI run `33541134010`](https://github.com/jihoon22-lee/ici/actions/runs/33541134010)의
Verify, Qt 5/Qt 6, `Publish Main Verification Report`, `Merge Gate`가 성공했다. [release run
`33541928666`](https://github.com/jihoon22-lee/ici/actions/runs/33541928666)의 provenance와
publish job도 성공했고, 공개 release는 non-draft/non-prerelease와 정확히 9개 asset을
포함한다. `ici.pyz` SHA-256은
`8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`다. ici/viewer main
Pages는 독립 확인에서 HTTP 200·`text/html`·각각 `ici Verification Report — ici`와
`ici Verification Report — viewer` title·외부 resource URL 0건을 만족했다. asset 목록과
검증 명령/결과는 [`v0.10.2 public evidence workthrough`](workthrough/2026-09-02-public-v0.10.2-evidence.md)에
고정한다.

---

## 1. 빠른 시작 (Quick Start)

### 1.1 설치 및 실행 권한 부여
별도의 가상환경 구성이나 `pip install` 없이, 단일 파일 `dist/ici.pyz`를 실행 경로에 복사하여 사용합니다.

```bash
# 사용자 로컬 실행 경로에 복사
mkdir -p ~/.local/bin
cp dist/ici.pyz ~/.local/bin/ici && chmod +x ~/.local/bin/ici

# PATH 환경변수 등록 (필요 시 ~/.bashrc 또는 ~/.cshrc에 추가)
export PATH="$HOME/.local/bin:$PATH"
```

### 1.2 실행 환경 진단 (`ici doctor`)
현재 시스템의 OS, glibc 버전, Python 런타임과 `doctor`에 포함된 제한된 도구 목록을 점검합니다.
전체 검증 엔진은 자신이 실제로 호출한 도구에 대한 `ToolEvidence`를 결과에 남깁니다.

```bash
ici doctor --brief
```

```text
ici <version> brief
os      <os_id>-<os_version>  glibc=<glibc>  arch=<arch>  wsl=<yes|no>
shell   <shell>  TERM=<term>  LANG=<lang>
python  running=<major.minor.micro>  path=<executable>
tools   gcc=<version-or->  g++=<version-or->  clang=<version-or->  make=<version-or->  cmake=<version-or->
        ruff=<version-or->  mypy=<version-or->  pytest=<version-or->  git=<version-or->
```

`ici doctor`를 `--brief` 없이 실행하면 동일한 데이터를 OS/Python/도구/경로별 Rich 표로
확장해서 보여줍니다. 위 출력의 버전과 경로는 실행 환경에 따라 달라지며 고정된 결과로
해석해서는 안 됩니다. qmake/Qt, Ninja, binutils 전체 capability 검증과 프로젝트 정의 기반
빌드 어댑터도 같은 bounded inventory·evidence 정책 안에서 동작하며, 실제 지원 범위와
제약은 실행 시점의 capability snapshot으로 확인합니다.

---

## 2. 검증 실행 (`ici verify`)

### 2.0 설정 파일과 적용 순서

`ici`는 실행할 때 다음 순서로 설정을 읽고 뒤에 읽은 값이 앞의 값을 덮어씁니다.

1. 내장된 `DEFAULT_CONFIG`
2. 전역 설정: `$XDG_CONFIG_HOME/ici/ici.toml` (기본값 `~/.config/ici/ici.toml`)
3. 프로젝트 설정: 현재 프로젝트의 `ici.toml`, 이어서 `dev.toml`
4. `ICI_CONFIG` 환경 변수로 지정한 명시 설정 파일

각 파일은 부분 설정만 포함해도 되며, 테이블 값은 깊게 병합됩니다. 예를 들어 프로젝트의
`ici.toml`에는 다음처럼 라인 엔진 정책만 둘 수 있습니다.

```toml
[engines.line]
warn_limit = 400
fail_limit = 900
```

C++ `complexity` 함수 경계 정책은 별도로 지정할 수 있습니다.

```toml
[engines.complexity]
cpp_boundaries = "auto"  # auto | required | off
```

Python runtime 호환성도 같은 설정 파일에서 선택합니다.

```toml
[engines.python_compat]
enabled = true
mode = "pass_warn_fail"
required = false            # engine-level gate policy
interpreters = []           # empty: current interpreter, required
required_interpreters = []  # configured entries that must pass
imports = []                # explicit import-smoke opt-in
target_version = ""         # empty: infer from requires-python
wheel_globs = []            # project-relative *.whl paths; inspection is opt-in
wheel_required = false
wheel_policy = "allow-native" # allow-native | pure
check_entrypoints = true
check_package_files = true
max_wheels = 32
max_wheel_members = 8192
max_wheel_uncompressed_bytes = 67108864
```

`interpreters = []`인 기본 경로는 `ici`를 실행 중인 `sys.executable`을 required runtime으로
검사합니다. 목록을 명시하면 각 interpreter는 optional이고, `required_interpreters`에 포함한
entry만 required가 됩니다. optional runtime이 없거나 검사에 실패하면 `WARN`, required runtime이
없으면 `ERROR`/`NOT_RUN`, version mismatch·compile/import failure면 `FAIL`입니다. required 목록은
configured 목록의 부분집합이어야 합니다.

각 runtime은 shell 없이 `-VV`로 version을 확인하고 선택된 Python source root에
`python -B -m compileall -q -f`를 실행합니다. compileall bytecode는 임시 cache prefix로 보내므로
project에 `__pycache__`를 남기지 않습니다. `pyproject.toml`의 `project.requires-python`은
PEP 440 specifier로 검증해 실제 runtime version과 비교합니다. `target_version`을 지정하면
그 Python minor를 syntax/API floor로 사용하고, 비워 두면 `requires-python`이 허용하는 가장
이른 지원 minor를 추론합니다. floor 위반은 1-indexed line과 가능한 line/column 범위를 가진
target으로 기록됩니다.

Import smoke는 안전한 기본 동작이 아닙니다. 모듈 import가 top-level code를 실행할 수 있으므로
자동 발견한 import 이름은 metadata로만 남기고 실행하지 않으며, `imports = ["package",
"package.cli"]`처럼 사용자가 명시한 dotted module만 `-I -B` contained subprocess에서 실행합니다.
이 subprocess는 shell 없이 실행되지만 sandbox는 아닙니다. 정상 결과는 `MEASURED`이고, 각
`-VV`·compileall·선택 import 호출의 path/version/argv/return code/timeout/truncation이
`ToolEvidence`에 남습니다. 외부 interpreter 교체가 결과를 바꿀 수 있으므로 이 engine은 cache
key/entry를 만들거나 재사용하지 않고 매번 fresh 검사합니다.

`pyproject.toml`이 있으면 package metadata도 정적으로 검사합니다. distribution/import package,
entry-point의 `module:attribute` target과 package file 목록을 비교하며, pyproject와 wheel 입력 및
각 wheel에는 PASS/FAIL 위치 target을 남깁니다. `wheel_globs`를 지정한 경우에만 wheel을 읽으며,
WHEEL/METADATA/RECORD의 존재·identity·완전성과 RECORD의 sha256/sha384/sha512 hash·size를
검증합니다. symlink/special member, portable-name collision, 누락된 METADATA identity, pyproject와
불일치하는 wheel `entry_points.txt`는 실패로 닫습니다. entry-point는 bounded AST/read로 callable
선언 또는 imported symbol을 확인하고 단순 대입을 호출 가능한 함수로 오인하지 않습니다. 손상된
wheel 구조는 해당 wheel의 `ici.package.wheel-invalid` 실패로 남습니다. package code를 import하거나
wheel을 build/extract하지 않으며, native extension은 `wheel_policy = "pure"`일 때만 거부합니다.

`dead`와 `dup`의 heuristic source 분석은 동일한 bounded UTF-8 intake를 사용합니다. generated/
autogen 및 moc 산출물과 vendor/dependency 디렉터리는 소유한 제품 코드가 아니므로 기본 제외하며,
필요한 경우 두 엔진에서 각각 opt-in할 수 있습니다.

```toml
[engines.dead]
cpp_unused = "auto"  # auto | required | off (C++ compiler-backed unused-function probe)
cpp_linker = "off"   # auto | required | off (Linux GNU ELF CMake section-GC; opt-in)
include_generated = false
include_vendor = false

[engines.dup]
python_semantic = "auto"  # auto | required | off (bounded Python 3.10 AST-shape clones)
include_generated = false
include_vendor = false
```

`include_generated`와 `include_vendor`의 기본값은 `false`이며 두 설정은 서로 독립적입니다.
직접 엔진 설정에서는 값이 literal `true`인 경우에만 opt-in으로 인정됩니다. generated와
vendor가 겹치는 path는 두 값을 모두 `true`로 해야 포함됩니다. 선택 path는 lexical
normalization/deduplication과 deterministic sorting을 거친 뒤 최대 8,192개의 unique
candidate와 2,048개의 owned/analyzed 파일, 파일당 8 MiB, 전체 64 MiB UTF-8 bytes로
제한됩니다. 정책으로 제외된 파일은 owned cap을 소비하지 않으며 excluded file count는 unique
path 기준입니다. `dup`는 `.h`/`.hh`/`.hpp`/`.hxx` owned header를 포함하고 standalone
`.moc`는 discoverable하지만 generated 기본 제외를 따릅니다.

프로젝트 밖 경로·symlink·사라진 파일·비 UTF-8/NUL 파일·지원하지 않는 확장자·상한 초과는
무시하지 않고 위치가 있는 `ERROR`/`NOT_RUN`으로 닫습니다. fallback reader도 component
symlink precheck와 double-read identity/content stability 검사를 수행하며 잘못 주입된 limit은
fail-closed합니다. 분석이 실제로 실행되면 Python `dead`와 `dup`의 휴리스틱 결과는 각각
`ESTIMATED` evidence로 보고되며, clean 파일도 PASS 위치 target으로 남습니다. C++ `dead`의
compiler-backed 정확성 정책은 아래와 엔진 레퍼런스에 별도로 설명합니다. 자세한 source scope와
fingerprint 계약은 [엔진 레퍼런스 §2.7](engine-reference.md#27--dead-죽은-코드-및-미사용-심볼)과
[§2.8](engine-reference.md#28--dup-코드-복제-및-중복률-감지기)을 참고하세요.

`dup`는 Python과 C/C++를 별도 line-preserving lexer로 정규화합니다. Python `tokenize`와 AST
context는 주석·multiline import-first statement를 제외하고 `match`/`case` soft keyword와
import/API anchor를 보존하며 identifier, 숫자·문자열 계열, 들여쓰기·연산자 category를
구분합니다. 기본 `python_semantic = "auto"` 경로는 여기에 Python 3.10 AST-shape 분석을
추가해 nested named scope를 품은 parent가 아닌 leaf function/method를 비교합니다. local
binding은 alpha-renaming하고 AST 위치/물리적 줄 배치는 무시하지만 control flow, operator,
literal 종류·값, source-spelled imported name·attribute anchor는 보존하며, exact canonical
shape가 같은 경우만 semantic group을 만듭니다. C/C++ lexer는 comments/directives를 제거하고
C++ backslash-newline splice의 physical line을 보존하며 punctuator, literal, UDL과 Qt anchor를
구분합니다. normalized-window seed의 exact token verification과 function/class/import/directive
region, semantic-signal policy를 통해 값만 다른 data table은 억제하고 실제 control-flow clone은
유지합니다. lexical fingerprint는 `sha256/type2-region-v2`, AST-shape fingerprint는
`sha256/semantic-shape-v1`로 기록하지만 두 경로 모두 compiler/linker 실측이 아니므로 결과는
`ESTIMATED`/heuristic이며 behavioral equivalence를 뜻하지 않습니다. 동일 occurrence가 이미
lexical group에 있으면 AST-shape group은 중복 보고하지 않습니다. 내부 tokenizer/matching와
lexical tokenizer/matching budget 초과는 엔진 전체를 `ERROR`/`NOT_RUN`으로 닫습니다.
AST-shape budget 초과는 semantic partial을 버리고 `auto`에서는 lexical 결과만 유지하며,
`required`에서는 `ERROR`/`NOT_RUN`으로 닫습니다. 해당 budget은 사용자 설정 키가 아닙니다.
Python tokenizer가 malformed marker를 안정적으로 만들더라도 AST
region을 확정할 수 없으면 엔진은 그 partial lexical 결과를 정상 분석으로 채택하지 않습니다.

모든 파일을 병합한 뒤 엔진 이름, 설정 키, 자료형, 평가 모드와 임계값 관계를 검사합니다.
알 수 없는 키, 잘못된 TOML, 잘못된 임계값은 조용히 기본값으로 대체되지 않고 설정 오류로
실패합니다. `ICI_CONFIG`가 존재하지 않는 파일을 가리키는 경우에도 동일하게 실패합니다.

### 2.0.1 분석 프로필과 실행 스케줄

`fast`, `standard`, `deep`은 검증 비용과 실행 범위를 선택하는 profile입니다.
기본값은 `standard`이며, 프로젝트 설정 또는 이번 실행의 CLI 옵션으로 고를 수 있습니다.

~~~toml
[ici]
profile = "deep"
~~~

~~~bash
ici verify --profile fast
ici verify --profile standard
ici verify --profile deep
~~~

현재 registry에는 19개 descriptor가 있으며, 기본 정책에서 선택되는 범위는 다음과 같습니다.
`python_compat`는 fast·standard·deep 모두에서 선택됩니다. `build`, `binary_compat`,
`integration`은 deep-only opt-in이고 세 엔진을 모두 활성화하면 dependency closure를 포함해
deep이 19종이 됩니다.

| profile | 선택되는 내장 엔진 | 용도 |
|---|---|---|
| `fast` | read-only 엔진 12종(`compile_db` 포함) | 빠른 편집·pre-commit 피드백 |
| `standard` | 기본 엔진 14종(`compile_db`/`test`/`sanitize` 포함) | 일반 로컬·CI 검증 |
| `deep` | 기본 엔진 16종(`compile_db`/`cognitive`/`thread_sanitize` 포함) | 가장 넓은 기본 분석 범위 |
| `deep` + release contract opt-in | 최대 19종(`build`/`binary_compat`/`integration` 포함) | 산출물·ABI·혼합 프로세스까지 검증 |

profile은 engine set만 바꿉니다. 예를 들어 line·complexity의 설정 임계값, test의
coverage 정책 등 동일 rule의 threshold와 판정 의미는 profile에 따라 낮아지거나 높아지지
않습니다. 프로젝트가 개별 엔진을 `enabled = false`로 명시하면 해당 profile에서도 그 엔진은
제외됩니다. `test`/`sanitize`/`thread_sanitize`/`build`/`integration`처럼 build session을 소유하는 선택 엔진은 서로
겹치지 않도록 직렬화되고, 나머지 read-only 엔진은 내부적으로 최대 4개까지만 병렬 실행됩니다.
이 제한은 결과의 재현성을 위해 두며 사용자가 worker 수를 조정할 필요는 없습니다.

선택된 profile은 `ici doctor`의 요약/JSON과 verify JSON의 `analysis_context.profile`에
표시됩니다. 이 JSON field는 optional이므로 profile이 없던 기존 `ici.result/v3` archive도
그대로 읽을 수 있습니다.

### 2.0.2 분석 결과 캐시

`ici verify`는 기본적으로 엔진별로 완료된 결과를 사용자 로컬 캐시에 저장하고, 다음 실행에서
동일한 입력 identity를 확인하면 다시 사용합니다. 기본 캐시는 네트워크나 프로젝트 공유
디렉터리를 사용하지 않는 사용자 로컬 저장소입니다. 기본 위치는 `~/.cache/ici/analysis/`이며
`XDG_CACHE_HOME` 또는 `ICI_CACHE_DIR`로 위치를 바꿀 수 있습니다. `ICI_CACHE_DIR`가 지정되면
그 경로가 우선하므로, override를 사용할 때도 checkout과 분리된 사용자 전용 로컬 경로를
지정해야 합니다.

캐시 key(`ici.analysis-cache-key/v3`)는 단순한 파일 timestamp가 아니라 다음 입력을 모두
포함한 SHA-256 identity입니다.

| key 구성 요소 | 의미 |
|---|---|
| 프로젝트 루트 | canonical project root 경로 |
| 소스·build 설정 내용 | project source와 인식된 build/config 파일의 경로·내용·권한 digest |
| effective ici 설정 | 기본·전역·프로젝트·`ICI_CONFIG` 병합 후 profile이 적용된 설정 digest |
| toolchain | capability inventory의 도구 경로·버전·세부 정보 digest |
| 엔진 구현 | engine descriptor, engine class source digest, 그리고 `CACHE_IMPLEMENTATION_MODULES`로 엔진이 명시적으로 선언한 helper/dependency module source digest 목록 (C++ lint에는 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._clang_tidy`, `ici.engines._clazy`, `ici.engines._cpp_diagnostic_categories`, `ici.engines._cpp_diagnostics`, `ici.engines._cpp_lint`, `ici.engines._cpp_tooling`, `ici.engines._qt_codegen`, `ici.engines.lint`; cycle에는 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_include_graph`, `ici.engines._cpp_include_trace`, `ici.engines.cycle`; complexity에는 `ici.core._compile_db_paths`, `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_function_boundaries`, `ici.engines._cpp_tooling`, `ici.engines.cpp_text` 포함) |
| build variant | `release`, `coverage`, `sanitize`, `thread-sanitize` 또는 해당 없는 엔진의 `none` |
| compilation context | 선택된 compile database의 project-relative path·바이트 digest, loader version, 정규화된 unit configuration/metadata와 parse diagnostics. digest는 preflight가 immutable context로 캡처한 snapshot identity이며 live-file lease가 아님 |
| producer | ici 버전과 cache key schema 버전 |

엔진 implementation identity는 engine class의 module/qualname와 class source digest를 포함하고,
class가 `CACHE_IMPLEMENTATION_MODULES`로 명시한 helper/dependency module 이름의 sorted unique
목록과 각 module source digest도 포함합니다. import tree 전체를 암묵적으로 수집하지 않고
명시적으로 선언된 구현 의존성만 cache identity에 반영합니다. 따라서 프로젝트 루트, 소스 또는
build 설정, 유효 설정, 도구 버전, 엔진 구현, build variant,
compile database의 내용·선택 경로·parse state, ici 버전 중 하나라도 달라지면 다른 key가 되어
cache miss가 됩니다. 이 digest는 실행 중 context가 원본 DB 파일을 계속 감시한다는 뜻이 아니라
preflight가 캡처한 immutable snapshot을 식별하는 값입니다. DB mutation은 현재 실행에 섞이지
않고 다음 preflight에서 새 bytes와 context로 반영됩니다. 캐시 저장소를 지우지 않아도 이
identity 경계가 이전 결과의 재사용을 막습니다.

project source digest는 선언된 source와 함께 인식된 build/config suffix 및 이름을 읽습니다.
clang-tidy 설정 파일 이름 `.clang-tidy`도 이 입력 목록에 포함되므로 내용·권한이 바뀌면
`project_source_digest`와 해당 cache key가 달라집니다. 반대로 인식 목록에 없는 임의의 hidden
file은 분석 입력에서 제외되며, `.git`/cache/build 디렉터리와 ici가 생성한 report JSON도
제외됩니다.

모든 엔진 결과를 저장하지는 않습니다.

| 결과 | 캐시 정책 |
|---|---|
| 완료된 `PASS`/`WARN`/`FAIL` | 재사용 가능. `WARN`/`FAIL`도 완전한 증거라면 저장할 수 있음 |
| `ERROR`/`SKIP` 또는 evidence `NOT_RUN` | 저장·재사용하지 않음 |
| timeout, 출력 truncation, `ToolEvidence.error` | 저장·재사용하지 않음 |
| 검증되지 않거나 변경된 artifact manifest | 저장·재사용하지 않음 |

artifact manifest가 있는 결과는 저장·읽기 경계에서 variant, config/toolchain identity와 실제
파일 내용·크기·권한을 다시 검증합니다. 캐시 파일이 없거나 손상·오래된 경우에도 검증은
실패하지 않고 cache miss로 처리되어 엔진이 정상 경로로 실행됩니다.

cache entry는 신뢰할 수 없는 입력으로 취급합니다. reader는 symlink/비정규 파일을
`O_NOFOLLOW`와 regular-file 검사로 거부하고, 32 MiB를 넘는 entry, JSON duplicate key,
`NaN`/`Infinity` 같은 non-finite 값을 허용하지 않습니다. 새 cache directory/file은 각각
`0700`/`0600` 권한으로 만들고, entry는 임시 파일에 flush·`fsync`한 뒤 atomic replace로
발행합니다. `verify_report.json`과 engine별 `*_report.json`처럼 ici가 생성하는 report JSON
이름은 source digest 대상에서 제외됩니다.

```bash
# 이번 실행만 cache read/write를 끔
ici verify --no-cache

# 사용자 로컬 cache inventory와 key contract를 확인
ici cache

# 정확히 ici cache entries-v1 아래의 JSON/TMP entry만 정리
ici cache --clear
```

`ici cache --clear`는 프로젝트의 소스, `build/`, `.ici/` 또는 다른 경로를 삭제하지 않습니다.
cache entry는 사용자 로컬 디렉터리 안에서 임시 파일에 쓰고 flush·`fsync`한 뒤 atomic replace로
교체하며, 프로젝트 파일은 digest 계산을 위해 읽기만 합니다. 입력 파일이 hash 중 바뀌었다고
감지되면 해당 실행에서는 cache를 비활성화해 일관되지 않은 결과를 저장하지 않습니다.

`ici.result/v3`의 engine object에는 cache 상태를 나타내는 선택적·하위 호환 필드가 있습니다.
새 writer는 `cache_hit`(boolean)과 nullable `cache_key`(`sha256:...`)를 기록하며, cache hit이면
`cache_hit: true`가 됩니다. `--no-cache`, 초기화 오류 또는 cache miss 결과는 hit가 아니며,
오래된 v3 archive는 두 필드가 없을 수 있으므로 소비자는 필드 부재를 허용해야 합니다.

`dead` 엔진은 C++ compiler probe가 읽는 external/generated include closure와 compiler binary
content가 cache v3에 완전히 표현되지 않아 결과 cache 재사용을 지원하지 않습니다. `test` 엔진도
outcome·timing·flaky rerun 같은 실행 시점 관측을 source digest로 재현할 수 없으므로 cache하지
않습니다. 두 엔진은 verify에서 cache key를 만들거나 결과를 저장하지 않고 매번 fresh run을
수행합니다. 이 결과의 `cache_hit`는 `false`, `cache_key`는 `null`로 직렬화됩니다.

로컬 검증 snapshot은 다음과 같습니다. 전체 Python 3.10 실행은 935 tests passed였고,
현재 cache identity/store/orchestrator/CLI/purity targeted 테스트도 통과했습니다. 동일한
standard 입력에서 첫 실행은 118.49초·cache hits 0, 두 번째 실행은 2.38초·hits 12였으며,
두 실행의 normalized results SHA-256은 `95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`,
finding 수는 각각 3,497건이었습니다. 생성 HTML은 4,095,550 bytes이고 외부 참조는
0건이었습니다. `scripts/verify-reproducibility.sh`는 두 build 모두
`6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`을 만들었고 project
source status unchanged를 확인했습니다. I2-4는 PR #97의 merge commit
`ef30059522729b376c5409e5bb49164aa538b128`, CI run `33345993304`, sticky comment
`5472411964`의 ici/viewer Pages 게시까지 완료됐습니다.

### 2.0.3 Python runtime compatibility

`python_compat`는 Python source가 있을 때 현재 runtime과 선택한 추가 interpreter의 호환성을
검사합니다. 기본 `interpreters = []`는 `ici`를 실행 중인 `sys.executable`을 하나의 required
runtime으로 선택합니다. 명시 목록의 entry는 optional이며, `required_interpreters`에 포함한
entry만 required입니다. engine-level `required`는 이 runtime entry 정책과 별개의 suite gate
설정입니다.

각 resolved interpreter는 shell 없이 `-VV`로 실제 version을 확인하고, 선택된 Python source
root에 `python -B -m compileall -q -f`를 실행합니다. compileall은 임시
`PYTHONPYCACHEPREFIX`로 bytecode를 보내 project에 `__pycache__`를 남기지 않습니다.
`project.requires-python`은 `pyproject.toml`에서 읽어 PEP 440 specifier로 파싱하고, 각 `-VV`
version이 허용 범위에 있는지 비교합니다. `target_version`을 지정하면 그 minor를 syntax/API
floor로 사용하고, 빈 값이면 `requires-python`이 허용하는 가장 이른 지원 Python 3 minor를
추론합니다. 현재 ici runtime이 해석 가능한 syntax와 bounded standard-library API inventory를
검사하며, 위반은 정확한 1-indexed line/column target으로 남깁니다.

Import smoke는 명시적 opt-in입니다. import는 module top-level code를 실행할 수 있으므로
자동 발견한 import name은 metadata로만 기록하고 실행하지 않습니다. 사용자가
`imports = ["package", "package.cli"]`로 지정한 dotted module만 `-I -B` contained subprocess와
명시적 project/source path로 실행합니다. 이 프로세스는 shell 없이 실행되지만 sandbox는 아닙니다.
정상 runtime 검사는 `MEASURED` evidence이며 `-VV`, compileall, 선택 import 호출의 executable
path/version/argv/return code/timeout/truncation을 `ToolEvidence`에 보존합니다. optional runtime의
unavailable/incompatible는 `WARN`, required runtime의 unavailable는 `ERROR`/`NOT_RUN`,
version mismatch·compile/import failure는 `FAIL`입니다. 외부 interpreter를 설정으로 교체할 수
있어 `python_compat`는 cache key/entry를 만들거나 재사용하지 않고 매번 fresh 검사합니다.

### 2.0.4 Deep 테스트 품질 관측

`test.quality`는 `deep` profile에서 Python 테스트의 실행 품질을 추가로 관측합니다. 기본값은
기존 test gate를 바꾸지 않는 `report` 모드이며, 반복 실행·slow inventory·flaky verdict·mutation
도구 capability probe를 명시적으로 bounded하게 설정할 수 있습니다.

```toml
[engines.test.quality]
enabled = true
mode = "report"             # report | warn
repeat_runs = 1              # 총 실행 횟수, 최대 3
timeout = 300.0              # quality subprocess별 최대 초
slow_test_threshold = 1.0    # pytest duration 기준 초
max_slow_tests = 50

[engines.test.quality.mutation]
enabled = false              # capability probe만 수행; mutation gate 자체는 만들지 않음
tool = "auto"               # auto | mutmut | cosmic-ray | mutpy
```

`repeat_runs`가 1보다 크면 동일한 pytest 수집 집합의 verdict를 비교해 변동한 case를
`ici.test.flaky-test`로 기록합니다. pytest duration 결과에서 threshold를 넘은 case는
`ici.test.slow-test`로 기록하며, slow/flaky target과 inventory는 base test target 및 test 수를
덮어쓰지 않습니다. `mode = "report"`에서는 관측이 보고서에만 반영되고, `mode = "warn"`에서는
해당 quality warning이 test engine 상태에 영향을 줍니다. mutation 설정은 도구를 실제로
실행해 변이를 평가하지 않고 capability만 확인하며 기본 test gate를 바꾸지 않습니다. 반복,
timeout, 출력 및 slow 목록에는 고정 상한이 적용되고, 불완전한 evidence는 PASS로 축약되지
않습니다. Test runtime 결과와 timing은 analysis cache에서 재사용하지 않으며,
`slow_tests_observed`는 `max_slow_tests`로 잘라낸 inventory보다 먼저 threshold를 넘은 고유
행 전체를 셉니다. 비유한·과도하게 긴 duration token은 관측에서 제외됩니다. 정식 config
loader는 malformed mode/mutation selector를 `ConfigError`로 거부하고, 직접 engine 호출
경계에서만 report/auto로 안전하게 정규화합니다.

### 2.1 로컬 전체 검증
현재 프로젝트 디렉토리에서 선택된 profile의 엔진을 일괄 수행하고 터미널 컬러 대시보드를
출력합니다. registry에는 19개 descriptor가 있고 기본 `fast`/`standard`/`deep`은 각각
12/14/16종을 선택합니다. `build`/`binary_compat`/`integration`을 명시적으로 켜면 deep
선택 수가 19종까지 늘어나며, `thread_sanitize`는 deep profile에서만 선택됩니다.

```bash
ici verify
```

### 2.2 인터랙티브 HTML 리포트 생성 및 자동 브라우저 열기
```bash
ici verify --report --html verify_report.html --open
```
- `--html <path>`: Zero-CDN 기반의 독립형 인터랙티브 HTML 리포트를 생성합니다.
- `--open`: 검증 완료 후 기본 브라우저(`firefox`, `chrome`, `xdg-open` 등)로 리포트를 즉시 띄웁니다.
- `--report`: 파이프라인 데이터 연동용 `verify_report.json`을 `ici.result/v3` 형식으로 저장합니다.
  기존 `targets`와 canonical `findings`를 함께 포함하며, 기계 검증용 JSON Schema는
  [`src/ici/schemas/ici-result-v3.schema.json`](../src/ici/schemas/ici-result-v3.schema.json)입니다.
  프로젝트 언어·Qt 발견 결과와 엔진별 mode/tool/fallback/evidence/confidence를 담은
  `support_matrix`도 함께 저장됩니다. 단독 엔진 리포트에는 그 엔진의 두 언어 행만 들어갑니다.
- `--sarif <path>`: 같은 canonical finding inventory를 SARIF 2.1.0으로 저장합니다. source-relative
  위치 URI(공백·유니코드·예약 문자는 percent-encoding), severity, fingerprint, related location, suppression과 baseline 상태를 결정적으로
  보존하며 100,000 result/10,000 rule 상한을 넘으면 실패합니다.
- issues가 2,000개를 초과하는 대형 HTML은 초기 50개 row만 server-render하고, 나머지는
  `ici.html-report/v1` inline JSON을 browser가 50개 단위로 검색·페이지네이션합니다. embedded
  JSON은 64 MiB에서 fail-closed하며 JSON report의 전체 inventory는 유지됩니다.
- HTML 파일은 같은 디렉터리의 임시 파일을 flush·`fsync`한 뒤 atomic replace합니다. 기존 출력이
  symlink이면 referent를 따라 덮어쓰지 않고 symlink directory entry 자체를 완성된 report로 교체합니다.

### 2.2.0 공유 분석 맥락과 산출물

`verify`는 프로젝트를 엔진마다 다시 발견하지 않고 하나의 immutable `AnalysisContext`를
만들어 모든 엔진과 리포터에 전달합니다. context에는 project facts, compile invocation
snapshot, source commit·config digest·toolchain digest, 요청된 `release`/`coverage`/
`sanitize` variant가 들어갑니다. build/test/sanitize가 만든 결과는 `ArtifactManifest`로
기록되며 variant, producer, identity, SHA-256, size, mode와 project/shadow root를 함께
보존합니다.

`--report`의 `ici.result/v3` JSON에는 다음 선택적 확장이 추가됩니다.

- `analysis_context`: `ici.analysis-context/v1` — project/source/header/compile 경로는
  project-relative POSIX 경로입니다. project root 자체는 JSON에 넣지 않습니다. `profile`은
  선택 필드이며 engine set 선택 결과만 기록합니다.
- engine의 `artifact_manifests`: producer metadata가 있는 신규 산출물은 `ici.artifacts/v2`,
  legacy record는 `ici.artifacts/v1` — project/shadow root, 상대 경로, SHA-256/size/mode와
  variant/config/toolchain identity를 기록합니다. v2에는 stable artifact `id`, target, redacted
  producer command를 추가해 build→ABI/integration 소비 경계를 추적합니다.

외부 include/search path처럼 호스트 절대 경로가 섞일 수 있는 값은 `analysis_context` JSON
projection에서 `-I[external]`로 치환됩니다. HTML의 로컬 editor-link용 absolute path와 기존
tool evidence는 각 리포터의 기존 redaction 계약을 그대로 따르며, 이 확장이 그 계약을
변경하지는 않습니다. 두 확장이 없는 기존 `ici.result/v3` archive도 계속 읽고 migration할
수 있습니다.

#### Standalone compilation context export

검증 엔진을 실행하지 않고 컴파일 데이터베이스의 측정 맥락만 전달하려면
`export-compilation-context`를 사용합니다. 기본 모드는 현재 프로젝트의 메타데이터와
선택된 `compile_commands.json`을 읽는 process-free/read-only 경로입니다. compiler, shell,
subprocess 또는 재귀 source scan을 실행하지 않으며, export 명령 때문에 전역 기본 설정
파일을 새로 만들지도 않습니다. 성공한 stdout에는 JSON과 마지막 개행만 남습니다.

```bash
# 루트의 compile_commands.json 또는 build/compile_commands.json을 발견해 stdout으로 출력
ici export-compilation-context

# 프로젝트 내부 DB를 명시하고, 결과는 checkout 밖의 파일에 예쁘게 저장
ici export-compilation-context \
  --database build/compile_commands.json \
  --output /tmp/ici-compilation-context.json --pretty

# 기존 DB가 없을 때만 CMake/qmake 준비를 허용
ici export-compilation-context --prepare \
  --output /tmp/ici-compilation-context.json
```

옵션의 경계는 다음과 같습니다.

| 옵션 | 계약 |
|---|---|
| `--database PATH` | 프로젝트 루트 아래의 project-relative POSIX 경로만 허용합니다. 절대 경로, Windows drive/backslash, `..` 탈출과 root 밖 symlink 해석은 exit 2로 거부합니다. |
| `--prepare` | 명시적으로 선택·설정한 DB와 auto-discovered DB가 모두 없을 때만 루트 `CMakeLists.txt` 또는 `*.pro`에 해당하는 adapter가 configure/build를 수행합니다. CMake는 `build/ici-cmake-build`, qmake는 `build/ici-qmake-build` shadow를 사용할 수 있습니다. 명시 DB가 누락·손상됐으면 canonical DB로 대체하지 않고 그 오류가 우선합니다. 이 옵션만 외부 도구 실행과 build 파일 변경을 허용합니다. |
| `--output PATH` / `-o` | 기본값 `-`은 stdout입니다. 상대 경로는 프로젝트 루트 기준이고 절대 경로도 허용됩니다. 대상은 regular file 또는 교체 가능한 symlink여야 하며, DB·`ici.toml`·`dev.toml`·`pyproject.toml` 및 hardlink/symlink alias는 보호됩니다. |
| `--pretty` | 결정론적 JSON에 2칸 들여쓰기를 추가합니다. 데이터 내용과 digest는 바꾸지 않습니다. |

파일 출력은 대상 디렉터리의 임시 regular file에 쓰고 flush·`fsync`한 뒤 atomic replace와
디렉터리 동기화를 수행합니다. 허용된 기존 symlink를 출력 대상으로 지정하면 symlink
자체를 교체하며 referent에는 쓰지 않습니다. 출력은 프로젝트 밖에도 둘 수 있지만 부모
디렉터리는 미리 존재해야 합니다. 출력 JSON은 32 MiB를 넘을 수 없습니다.

입력 loader도 자원 상한을 적용합니다. DB는 최대 32 MiB·200,000 entries, row별 `arguments`는
최대 32,768개·총 1 MiB, DB 전체 expanded arguments는 1,000,000개·32 MiB, `command`는
4 MiB입니다. response file은 프로젝트 내부 regular file만 읽고 깊이 4, 파일별·aggregate
4 MiB, 같은 per-row argument bound를 적용합니다.
`arguments`가 `command`보다 우선하며 POSIX `shlex` 또는 Windows CRT 규칙으로만 argv를
분해합니다. duplicate JSON key, `NaN`/`Infinity`, 비정상 파일, symlink 탈출, malformed row,
읽는 중 변경은 실행하거나 추측하지 않고 bounded diagnostic으로 처리합니다.

출력 계약은 [`ici-compilation-export-v1.schema.json`](../src/ici/schemas/ici-compilation-export-v1.schema.json)의
`ici.compilation-export/v1`입니다. key를 정렬한 UTF-8 JSON으로 `evidence`는 실제 DB를 읽거나
준비했다는 `MEASURED`를 뜻합니다. `source_bytes_digest`는 선택된 DB 원본 바이트의 SHA-256이고,
`semantic_digest`와 unit별 `configuration_digest`는 redacted·정규화된 origin, generator,
source/target/configuration 정보를 canonical하게 해시합니다. 같은 의미의 unit은 안정적으로
정렬되지만, 원본 바이트가 달라지면 `source_bytes_digest`는 달라질 수 있습니다. DB digest는
preflight가 immutable context로 캡처한 snapshot identity이며 live-file lease가 아닙니다. DB가
변경되면 실행 중 context를 바꾸지 않고 다음 preflight에서 새 bytes와 context를 반영합니다.

raw `argv`와 `command`는 공개하지 않습니다. 내부 경로는 project-relative POSIX로 투영하고,
외부 include/sysroot와 host 경로는 `[external]`, credential과 안전하게 공개할 수 없는 표준·정의·
generator 값은 `***REDACTED***`로 치환합니다. 치환되거나 외부 값, unknown compiler,
unmodeled option, unit/context diagnostic 또는 unity build가 있으면 root/unit
`comparison_state`가 `inconclusive`가 될 수 있습니다. 치명적인 error-level diagnostic은
payload를 만들지 않고 exit 1로 닫힙니다. 그 밖의 측정 결과는 추정으로 바뀌는 것이 아니므로
`evidence`는 여전히 `MEASURED`일 수 있습니다.

종료 코드는 다음과 같습니다.

| 코드 | 의미 |
|---:|---|
| `0` | export 성공 |
| `1` | fatal compilation diagnostic 또는 직렬화·쓰기 오류 |
| `2` | 옵션/경로 검증 실패, 측정된 DB 또는 usable unit 부재, 보호된 출력 대상 |

stdout 모드에서 오류 메시지는 stderr로만 출력되고, `--output` 파일 모드에서는 성공 stdout이
비어 있습니다. schema는 `src/ici/schemas/` 아래 package data로 wheel/ZipApp에 함께 포함되며,
`scripts/build-pyz.sh`가 ZipApp 구성 전에 두 공개 schema의 존재를 확인합니다.

#### C++ compile database gate

C++ production translation unit이 발견되면 `compile_db` 엔진이 `compile_commands.json`을
분석 맥락의 단일 입력으로 사용합니다. 자동 선택 순서는 프로젝트 루트의
`compile_commands.json`, `build/compile_commands.json`이며, `project.compile_database`로
project-relative 경로를 명시할 수도 있습니다. Python-only 프로젝트에는 이 엔진이
`SKIP`/`NOT_APPLICABLE`입니다.

loader는 compiler를 실행하거나 shell을 거치지 않습니다. JSON의 `arguments`가 `command`보다
우선하며, command는 POSIX/Windows 플랫폼 규칙으로 argv로 분해됩니다. response file은
project 내부 regular file만 제한된 깊이·크기·인자 수로 읽고, 외부 경로·symlink escape·malformed
row·stale source·missing include directory·읽기 중 변경은 위치가 있는 diagnostic으로 남깁니다.
동일 source의 여러 configuration도 각각 검사합니다.

기본적으로 DB가 없으면 각 production unit에 `WARN`이 생성됩니다. CI에서 DB를 반드시 요구하려면
다음처럼 설정합니다. `required_flags`는 각 compile argv에 반드시 있어야 하는 exact token이고,
`forbidden_flags`가 발견되면 해당 configuration을 `FAIL`로 판정합니다.

```toml
[engines.compile_db]
enabled = true
mode = "pass_warn_fail"
database_required = true
required_flags = ["-Wall", "-Wextra"]
forbidden_flags = ["-fpermissive"]
```

report에는 source별 coverage target과 loader/configuration diagnostic이 함께 남으며,
`coverage_percent`, production/covered unit 수, configuration 수와 선택된 database path는
engine `extra`에 기록됩니다. compile argv와 외부 SDK/include 경로는 JSON·HTML·Markdown 출력
경계에서 redaction되어 host 경로가 그대로 공개되지 않습니다.

#### C++ lint와 cycle의 compilation-context 동작

`compile_commands.json`이 선택되어 `CompilationContext`가 있으면 C++ `lint`와 `cycle`은
각 production translation unit의 모든 covered configuration을 그대로 사용합니다. context의
normalized direct GCC/Clang argv는 `CapabilityInventory`가 probe한 실행 파일과 대조한 뒤
재생되며, source·working directory 경계를 다시 확인합니다. `-c`·출력·dependency 생성,
plugin/wrapper/toolchain 주입 등 안전하지 않은 flag는 replay에서 제거하거나 거부합니다.
보존되는 option은 positive allowlist와 허용된 value에 한정하고, allowlist 밖의 option은
fail-closed로 거부합니다. compiler에는 inherited override가 없는 minimal replacement environment를
주며 stdin은 빈 입력으로 닫습니다.
따라서 DB가 있는데도 고정 `g++ -std=c++17` 명령이나 suffix heuristic으로 바뀌지 않습니다.

lint는 compiler가 돌려준 위치 있는 `error`/`warning`/`note:`와 진단 없는 PASS를 source·line
target으로 보존합니다. error-level context/unit diagnostic, context coverage 누락, unsafe
replay, malformed 출력, timeout·truncation, spawn 실패 또는 검증할 수 없는 nonzero 결과는
`ERROR`/`NOT_RUN`으로 fail-closed 처리됩니다. warning-level context/unit diagnostic은 위치 있는
`WARN` target으로 보존하고 replay를 계속하므로 다른 오류가 없으면 exact evidence는
`MEASURED`입니다. 이 실행 정보는 `ToolEvidence`에 남습니다.

##### C++ dead unused-function 정책

`[engines.dead].cpp_unused`는 `auto`(기본값), `required`, `off` 중 하나입니다. 이 설정은
Python AST dead-code 휴리스틱과 독립적으로, immutable exact compilation database가 덮는 모든
owned project C/C++ source translation unit의 compiler-backed unused-function 검사를 제어합니다.
`project.cpp_external_build_dirs`로 지정한 external build directory 안의 owned source도 해당
database unit/configuration이 있으면 포함하며, build/link engine의 self-link 제외 정책은 이 scan에
적용하지 않습니다. 각 selected `CompilationUnit`의 explicit `language`는 `c` 또는 `c++`여야 하고
다른 값이나 빈 값은 compiler 실행 전에 거부합니다.

전체 `ici verify`와 독립 `ici dead` 명령은 같은 project/tool/compilation preflight로 immutable
context를 준비합니다. `cpp_unused = auto|required`인 standalone은 `dead` support scope에 필요한 capability만 scoped probe하면서
설정된 `[doctor].required_tools`도 함께 probe/기록하며, `dead` compiler capability만 고정적으로
요청하는 경로가 아닙니다. compile database가 없고 canonical CMake/qmake context를 만들 수 있는
프로젝트라면 `verify`와 동일하게 ici 소유 shadow에서 context를 준비할 수 있습니다.

- exact `CompilationContext`가 덮는 각 owned project C/C++ source translation unit을 모든 알려진
  canonical configuration으로 재생하고, approved direct GCC/Clang driver 또는 capability-approved
  alias에 `-Wunused-function`을 추가합니다. GCC가 `-fsyntax-only`에서 이 진단을 생략할 수 있어
  probe는 `-S -o os.devnull`로 discarded assembly만 만들며 object, 실행 파일, 링크 결과는 만들지
  않습니다. 승인되지 않은 compiler family나 wrapper는 허용하지 않습니다. source operand는
  canonical path로 정규화하되 원래 compile argv의 positional slot에
  정확히 한 번 유지해 `-x` 등 뒤따르는 option의 의미를 바꾸거나 source를 suffix로 재배치하지
  않습니다. option separator 뒤의 추가 operand는 `-w`나 두 번째 `--`를 포함해 모두 거부합니다.
- compiler diagnostic의 위치 범위를 selected TU source에 compiler가 귀속하고 rule ID가 정확히
  `-Wunused-function`일 때만 후보로 인정합니다. 같은 source의 모든 configuration에서
  필터된 `-Wunused-function` 위치 집합이 일치해야 exact finding이 되며, clean source도
  configuration 수를 기록한 PASS target으로 보고됩니다. `Compiler:-Wunused-function` target과
  `tool_rule_id`가 위치와 함께 report에 남고, compiler/version/configuration 상세는 JSON의
  `extra` 및 top-level `tool_evidence`에 남습니다. 등록된 `gcc`/`g++` probe는 `--version` banner에서
  관측된 GCC 또는 Clang family를 확인한 경우에만 capability를 complete로 인정합니다. 진단 형식은 관측된 approved compiler family가
  GCC이고 version 9 이상이면 `-fdiagnostics-format=json`, older GCC와 Clang(그 capability로
  resolve되는 alias 포함)은 `-fdiagnostics-parseable-fixits` text입니다. 중립 이름이나 Apple alias도
  executable spelling이 아니라 기록된 family를 따르며, 같은 executable에서 관측된 Clang family는
  `g++` alias spelling보다 우선합니다. project의 진단 rule 표시 설정은
  제거하고 controlled `-fdiagnostics-show-option`을 강제하므로 rule ID를 숨길 수 없습니다.
- `cpp_unused_non_tu_diagnostics_excluded`는 정확히 `-Wunused-function` rule의 warning이면서
  선택된 TU source 밖에 compiler가 귀속한 경우만 세고, 다른 rule의 warning이나 note/error는 이
  count에 포함하지 않습니다. 이 non-TU/header/external 진단은 finding으로 만들지 않습니다.
  matching warning 자체에 위치가 없으면 source 귀속을 증명할 수 없으므로 clean PASS로 제외하지
  않고 전체 C++ probe를 `ERROR`/`NOT_RUN`으로 닫습니다.
  macro-generated definition은 compiler-attributed expansion 위치를 사용합니다. 그 logical path가
  selected TU와 정확히 같고 line/column 범위가 immutable source snapshot 안에 있을 때만
  보존합니다. 범위를 벗어난 `#line`/macro remapping은 fail-closed하며, physical origin은 ici가
  별도로 재구성하지 않습니다. external-linkage symbol, template, inline/COMDAT definition,
  linker reachability, dynamic lookup/plugin entry point, Qt meta-object reachability는 이 TU-local
  probe가 분류하지 않습니다.

`context.compilation.unity_build=true`가 명시된 context는 source ownership을 증명할 수 없어
`ERROR`/`NOT_RUN`으로 닫습니다. `false` 또는 `null` 자체를 거부하지는 않지만, source coverage와
configuration identity 등 나머지 exact preflight는 계속 요구합니다. 각 unit identity는
`directory`/`argv`/`output` payload의 canonical digest를 replay 전에 다시 계산해 대조합니다. pure C++
scope에서 exact context/database 또는 approved compiler가 unavailable 또는 not-applicable이고 실제
analysis/context/intake error가 없을 때만 `auto`가 required gate를 완화합니다. 이때 C++는
`SKIP`/`NOT_RUN`, `required = false`가 되어 suite에는 `WARN`만 기여합니다. hybrid에서는 Python
분석을 계속하면서 C++ 미실행을 명시하고, Python scope가 정상 완료됐다면 전체 evidence를 `ESTIMATED`로
유지합니다. `required`는 unavailable 상태도 `ERROR`/`NOT_RUN`으로 승격합니다. auto/required 모두
context가 존재한 뒤 드러난 invalid context/coverage/configuration, unsafe replay, compiler 실패,
unlocated matching warning, timeout, truncated/malformed output, source/compiler/working-directory
identity 변경 등 실제 오류는 휴리스틱 폴백 없이 `ERROR`/`NOT_RUN`으로 fail-closed합니다. `off`는 C++ path discovery로 scope 존재 여부만 판별하고 후보 bytes를
source intake/snapshot에서 제외하며 C++ context/compiler probe와 tool evidence를 만들지 않습니다.
Python 입력을 읽거나 분석하는 일을 막지 않습니다. pure C++ dead gate도
활성화하지 않고 hybrid의 Python 정책은 그대로 적용합니다.

engine `extra.language_evidence`에는 `python`과 `cpp`의 상태가 각각 기록됩니다. C++ probe는 모든
selected source/configuration을 끝까지 확인한 뒤에만 findings를 commit하므로, 뒤늦은 C++ replay
실패나 configuration disagreement가 있으면 이미 관찰한 C++ unused findings를 모두 폐기하는
atomic 결과가 됩니다. 이미 성공적으로 완료·기록된 compiler observation의 source에는 exact PASS/WARN 대신 위치가 있는
`C++UnusedFunctionsInvalidated` `SKIP` target을 남겨 폐기된 실행 범위를 추적합니다. Python 분석이 먼저 정상 완료된 hybrid에서는 그 Python findings는 그대로
남습니다. Python finding은 heuristic confidence(`MEDIUM`)와 빈 tool attribution을, accepted C++
finding은 `FindingConfidence.EXACT`, `tool_rule_id = "-Wunused-function"`, compiler name/version을
각각 보존합니다. 예를 들어 두 scope가 정상 완료된 hybrid에서 Python은 `ESTIMATED`, C++는
`MEASURED`이지만, 두 언어를 합친 dead engine evidence는 보수적으로 `ESTIMATED`입니다. support
matrix와 report consumer는 이 언어별 metadata를 사용해 C++ tool-backed 상태를 Python 휴리스틱과
혼동하지 않습니다.

이 구현의 compiler exact 주장은 internal-linkage 함수의 TU-local `-Wunused-function`과 아래
target-local GNU ELF section-GC 계약으로 한정됩니다. 여러 object/library/plugin과 dynamic
lookup까지 판단하는 whole-program/dynamic dead reachability는 아직 지원 범위가 아니며, 후자의
target-local evidence와 구분됩니다. 최종 viewer standalone `dead` evidence는 `PASS`/`MEASURED`이며, 정확히 8개 source,
8개 configuration, 8개 target, 8개 `tool_evidence` 행, 0개 unused function, `cache_key = null`을
기록했습니다. 이 slice는 PR #137 required CI, 단일 sticky comment의 ici/viewer
링크, PR·main artifact/Pages byte match, exact-main CI와 Pages 배포까지 수락됐습니다.
상세 provenance와 해시는
[`compiler-backed C/C++ unused-function workthrough`](workthrough/2026-09-03-compiler-backed-cpp-unused-functions.md)에
기록합니다. target-local GNU ELF section-GC 증거는 아래 정책으로 지원하지만, whole-program/
dynamic dead reachability와 full C++ semantic/behavioral duplicate equivalence는 여전히
지원 범위가 아닙니다. 버전은 `0.10.2`로 유지하고 이 feature PR로 새 release를 만들지 않습니다.

##### C++ GNU ELF target-local discarded-function 정책

`[engines.dead].cpp_linker = "auto" | "required" | "off"`의 기본값은 `off`이며 `cpp_unused`와
독립된 opt-in 경로입니다. 지원 범위는 Linux root CMake project의 별도 Release shadow,
`Unix Makefiles`, direct-object ELF executable link target입니다. `cmake`/`readelf`/`addr2line`과
capability-approved GCC driver가 GNU `ld`를 사용한다는 증거, function-section/section-GC
flags 및 CMake `link.txt`를 모두 확인합니다. GNU `ld`가 버린 section 중 direct project object의
uniquely mapped `LOCAL`/`HIDDEN`/`INTERNAL` function만 source 위치가 있는
`ici.dead.gnu-elf-discarded-function` `EXACT` target-local finding으로 기록합니다.

지원 context/tool/target이 없으면 `auto`는 `SKIP`/`NOT_RUN`, `required`는 `ERROR`/`NOT_RUN`으로
닫습니다. 준비된 context에서 malformed command, relink/ELF/binutils/source identity 오류,
timeout/truncation 또는 link/object/section/tool/time 한도 초과가 발생하면 partial finding 없이
atomic fail-closed합니다. archives/shared/LTO/PIE/COMDAT/dynamic/export/whole-archive/linker
script와 clone/모호한 mapping은 제외하며, 이 결과는 whole-program deadness나 behavioral
unreachability 주장이 아닙니다. 주요 한도는 link file 256개, direct object 4,096개/target,
discarded section 16,384개, 전체 900초입니다.

##### Python AST-shape duplicate 정책

`[engines.dup].python_semantic`은 `auto`(기본값), `required`, `off` 중 하나입니다. Python 3.10
AST에서 nested named scope를 품은 parent를 제외한 leaf function/method만 대상으로 하며, local
binding은 alpha-renaming하고 물리적 layout은 무시합니다. control flow·operator·literal 종류·값과
source-spelled imported-name/attribute anchor는 보존하고, `sha256/semantic-shape-v1`
canonical shape가 정확히
같은 경우만 group으로 만듭니다. lexical occurrence 집합과 동일한 AST group은 dedup합니다.

malformed/unsupported AST, lambda/comprehension, `global`/`nonlocal`, star import,
`eval`/`exec` 호출과 그 이름의 literal `getattr` lookup, nested parent 및 trivial region은
보수적으로 제외합니다.
`auto`는 제외 사유를 metadata로 남기고, `required`는 `ERROR`/`NOT_RUN`, `off`는 skip합니다.
파일 256개·named region 20,000개·AST node 500,000개·serialized shape 16 MiB 한도를 넘으면
semantic partial을 버리고 `auto`는 lexical 결과만 유지하며, `required`는 `ERROR`/`NOT_RUN`으로
닫습니다. 결과는 구조적 clone 신호로서 계속 `ESTIMATED`이며 behavioral
equivalence나 전체 semantic duplicate 분석을 뜻하지 않습니다.

##### C++ clang-tidy 정책

clang-tidy는 exact `CompilationContext`가 있고 capability inventory가 승인한 direct executable이
있을 때만 covered production translation unit을 검사합니다. `[engines.lint]`에서 정책과 check를
설정할 수 있습니다.

```toml
[engines.lint]
clang_tidy = "auto"  # auto | required | off
clang_tidy_checks = ["-*", "bugprone-*", "performance-*"]
# clang_tidy_config = "config/.clang-tidy"
```

`clang_tidy_checks`는 1~128개의 중복 없는 non-empty check glob 목록입니다. 하나의 항목에 여러
glob을 쉼표로 넣지 않고 목록의 별도 항목으로 적으면 ici가 `--checks=a,b` 형태로 결합합니다.
check 목록을 지정하면 config 파일의 `Checks`나 built-in default보다 우선합니다.

config 선택 우선순위는 다음과 같습니다.

| 우선순위 | 대상 | 실행 시 전달되는 값 |
|---|---|---|
| 1 | `clang_tidy_config`로 명시한 project-contained regular file | `--config-file=<resolved path>` |
| 2 | source 디렉터리에서 project root까지만 올라가 발견한 가장 가까운 `.clang-tidy` | `--config-file=<resolved path>` |
| 3 | config가 없을 때의 built-in defaults | `--config={}` 및 기본 `-*,bugprone-*,clang-analyzer-*,performance-*` |

project root 밖의 config, root 밖으로 향하는 symlink, 비정규 파일, NUL/크기 제한을 넘은 파일은
거부합니다. `.clang-tidy`의 `ExtraArgs`와 `ExtraArgsBefore`는 compiler argument injection으로,
`InheritParentConfig`는 project 밖 parent 설정 상속으로 간주해 실행 전에 거부합니다.
source의 parent-of-project에 있는 config는 탐색하지 않으며,
config가 없는 경우의 `--config={}`가 clang-tidy의 암묵적인 parent config lookup도 차단합니다.

clang-tidy 명령은 이미 loader가 만든 immutable `CompilationContext`의 normalized unit command를
`build_replay_command`로 안전하게 재생한 뒤, 그 compiler의 허용된 tooling argument만 `--` 뒤에
전달합니다. compilation database를 직접 다시 읽거나 `-p`를 사용하지 않고, `-c`·output/dependency
생성·plugin/wrapper 주입·allowlist 밖 option은 제거하거나 fail-closed로 거부합니다. 명령에
`--fix`를 넣지 않으며 source와 context를 읽기만 하므로 fix-it은 report의 remediation 제안으로만
남습니다. 각 unit은 최대 120초, 전체 실행은 최대 600초의 global budget을 공유합니다.

Compiler 진단은 완전하게 probe된 실제 GCC family version 9 이상이면
`-fdiagnostics-format=json`을 사용하고, older GCC와 approved Clang-family driver/alias는
`-fdiagnostics-parseable-fixits` text를 사용합니다. family/version을 확정하지 못한 capability는
replay 전에 거부합니다. JSON/text parser는 malformed output 일부를 성공 결과와 합치지 않고
atomic하게 거부하며, project-relative/external 위치·rule ID·child/note·fix-it 범위를 보존합니다.
#### C++ diagnostic category policy

Compiler, clang-analyzer, clang-tidy, clazy finding의 category는 isolated
`_cpp_diagnostic_categories.py`의 `tool-rule-v1` 정책으로 결정됩니다. 비교 대상은 parser가
정규화한 `family`와 `tool_rule_id`뿐이며, diagnostic의 free-form message·caret text·note prose는
category에 영향을 주지 않습니다. rule은 case-fold한 뒤 아래 순서를 적용하고, 명시되지 않은
경우에는 family별 안전한 fallback을 사용합니다.

| 우선순위 | normalized family / `tool_rule_id` | v3 category |
|---|---|---|
| 1 | `family = compiler` | `CORRECTNESS` |
| 2 | Analyzer `clang-analyzer-security.*`, `clang-analyzer-alpha.security.*`, `clang-analyzer-optin.taint.*`; tidy `cert-*`, `android-cloexec-*`, `bugprone-command-processor`, `bugprone-signal-handler`, `bugprone-unsafe-functions`, `concurrency-mt-unsafe` | `SECURITY` |
| 3 | Analyzer exact resource IDs and `clang-analyzer-alpha.webkit.*`/`clang-analyzer-webkit.*` prefixes; tidy exact resource IDs listed below | `RESOURCE` |
| 4 | `family = clang-analyzer`의 나머지 rule | `CORRECTNESS` |
| 5 | `family = clang-tidy`의 `portability-*` 또는 정확히 `modernize-deprecated-headers` | `COMPATIBILITY` |
| 6 | `family = clang-tidy`의 security/resource 예외를 제외한 모든 `bugprone-*` 또는 `concurrency-*` | `CORRECTNESS` |
| 7 | `family = clang-tidy`의 나머지 rule | `MAINTAINABILITY` |
| fallback | `compiler`/`clang-analyzer`/`clang-tidy`/`clazy`가 아닌 unknown family | `CORRECTNESS` |

Analyzer resource exact IDs는 `clang-analyzer-alpha.core.danglingptrderef`,
`clang-analyzer-alpha.core.useafterlifetimeend`, `clang-analyzer-alpha.cplusplus.smartptr`,
`clang-analyzer-cplusplus.arraydelete`, `clang-analyzer-cplusplus.innerpointer`,
`clang-analyzer-cplusplus.newdelete`, `clang-analyzer-cplusplus.newdeleteleaks`,
`clang-analyzer-fuchsia.handlechecker`, `clang-analyzer-osx.cocoa.retaincount`,
`clang-analyzer-osx.cocoa.runloopautoreleaseleak`,
`clang-analyzer-osx.corefoundation.cfretainrelease`, `clang-analyzer-unix.malloc`,
`clang-analyzer-unix.mismatcheddeallocator`, `clang-analyzer-unix.stream`입니다. Tidy resource
exact IDs는 `bugprone-dangling-handle`, `bugprone-dangling-reference`,
`bugprone-multiple-new-in-one-expression`, `bugprone-shared-ptr-array-mismatch`,
`bugprone-suspicious-realloc-usage`, `bugprone-unique-ptr-array-mismatch`,
`bugprone-unused-raii`, `bugprone-use-after-move`, `cppcoreguidelines-owning-memory`,
`misc-new-delete-overloads`입니다. 따라서 새로운 임의 rule이나 message의 단어만으로
resource/security가 되지 않습니다. 결과
`extra.cpp_diagnostic_category_policy`에는 정책 ID가, `extra.cpp_diagnostic_categories`에는
`FindingCategory`의 모든 category별 primary diagnostic count가 기록됩니다.
compiler/clang-tidy가 출력한 fix-it replacement는 최대 bounded suggestion으로 기록되지만 자동
적용하지 않습니다. clang-tidy의 rule-less `note:` 또는 primary와 같은 rule을 가진 설명 note는
출력 순서상 바로 앞의 primary와 같은 contiguous group에만 `related_diagnostics`로 결합되고,
다음 primary가 시작되면 그 group을 닫습니다. orphan note나 다른 check rule을 가진 note는
일부 finding을 남기지 않고 atomic하게 거부합니다. lint의 target/finding과 warning·violation·
family 집계는 primary만 세며, 관련 note의 위치·메시지는 `Finding.related_locations`로,
fix-it은 primary remediation과 `extra` metadata로 보존합니다. Finding을 report로 내보낼 때
related location은 canonical project-relative path와 1-indexed region으로 정규화한 다음
path, 시작/끝 line·column, label 순으로 deterministic하게 정렬합니다. JSON과 HTML은 전체
related location을 보존하고, GitHub Markdown은 informational/suppressed finding을 제외한
related row를 engine당 최대 100개까지만 렌더링하며 생략 안내와 full report 참조를 표시합니다.
두 adapter는 각각 최대 2,048 translation units, unit당 120초, 전체 600초
예산을 사용합니다. compilation context 자체에 error diagnostic이 있으면 compiler replay도
시작하지 않으며, 위치가 없는 GCC command-line/ICE diagnostic은 `[external]`:1 target으로
보존합니다.

`auto`에서 clang-tidy가 없거나 context/database가 없으면 명령 없이 optional `WARN`과 missing
`ToolEvidence`를 남깁니다. `required`에서는 같은 조건이 `ERROR`가 됩니다. `off`는 명령과
evidence를 만들지 않습니다. timeout·truncation·nonzero·spawn/malformed output·context/coverage
불일치·replay 오류·translation-unit 또는 600초 budget 초과는 heuristic으로 조용히 대체하지 않고
`ERROR`/`NOT_RUN`으로 fail-closed 처리합니다. 정상 실행은 argv·path·version·return code가 있는
`ToolEvidence`와 `MEASURED` evidence를 남기며, 위치 있는 compiler/clang-tidy warning과 error는
각각 finding의 severity와 전체 lint gate에 반영됩니다.

실제 빌드가 `-Werror`, `-Werror=<rule>`, pedantic-errors 계열을 사용해도 diagnostic-only
clang-tidy/clazy 실행에서는 각각 제거, `-W<rule>`, `-pedantic`으로 낮춰집니다. 이 변환은
warning 선택과 standard/define/include/ABI context를 보존하며 `-Wno-error*`도 변경하지 않습니다.
따라서 분석 finding은 위치 있는 결과로 수집되고, 실제 syntax 오류·비정상 종료·파서 오류만
tool execution failure로 처리됩니다. 변환 결과가 `-Wp`/`-Wa`/`-Wl` forwarding처럼 replay
안전 경계를 벗어나면 도구를 실행하지 않고 context error로 fail-closed합니다.

##### C++ complexity 함수 경계 정책

`complexity`의 `cpp_boundaries`는 `[engines.lint].clang_tidy`와 독립적인 전용 probe입니다.
exact `CompilationContext`/compilation database와 capability-approved direct `clang-tidy`가
있을 때만 `readability-function-size` diagnostic의 AST 결과로 함수 경계 geometry를 확정합니다.
경계 안의 CC/nesting은 여전히 ici의 masked source token/brace metric이며
`metric_confidence = "medium"`입니다. tool의 lines/statements/parameters notes는 별도 metadata로
보존됩니다. 공유 clang-tidy parser가 이 notes를 primary의 `related_diagnostics`로 보존하므로,
function-boundary consumer는 primary 뒤의 related stream을 다시 순서대로 읽어 lines/
statements/parameters evidence를 body geometry에 매핑합니다. 이 consumer 전용 확장은 lint의
primary-only target/finding 집계와 reporter의 related-location 보존을 변경하지 않습니다.

AST boundary target은 source-spelled named function이며 function template, conversion/call/subscript
operator, literal operator를 포함합니다. `function_kind`, `function_template`, `function_origin`으로
kind/template/provenance를 보존합니다. lambda는 독립 함수 target으로 만들지 않고 lambda body는
enclosing function의 CC/nesting에서 제외합니다. Macro-generated function이 expansion site에서
진단되면 해당 scope는 명시적으로 제외하고 `extra.cpp_scope_exclusions.macro_generated_function`에
개수를 남깁니다. 파일의 다음 brace를 그 함수의 body로 매핑하지 않으며, fallback scanner는
operator 이름을 보존하고 multiline preprocessor definition/continuation과 standalone macro
invocation을 skip합니다. lambda 제외 개수는 `extra.cpp_scope_exclusions.lambda`에서
확인할 수 있습니다.

`auto`는 context/database 또는 approved tool이 없을 때만 source scanner로 폴백하고
`ESTIMATED`/heuristic 경계를 남깁니다. 빈/미보고 source-spelled definition은 heuristic으로 남을 수
있지만 macro-generated expansion은 target에서 제외됩니다. 성공한 각 configuration의 boundary는
geometry뿐 아니라 name, kind, provenance가 일치해야 promotion됩니다. configuration별
clang-tidy lines/statements/parameters는 `configuration_metrics`에 보존됩니다. geometry가 다르면
boundary promotion을 보류하고, function-size metric 값이 configuration마다 다르거나 body에
conditional preprocessor branch가 있으면 run은 `partial`, 해당 target의 `metric_confidence`는
`low`가 됩니다. compiler-backed function metrics 또는 configuration coverage가 partial/low-confidence로
남으면 `required`에서는 `ERROR`/`NOT_RUN`으로 fail-closed합니다. 시도된 tool·replay·parser·timeout·truncation·coverage·budget 오류는 조용한 폴백 없이
`ERROR`/`NOT_RUN`으로 fail-closed합니다. 단, clang-tidy가 visible project diagnostics와 함께
정확한 `Suppressed N warnings (N in non-user code).`를 보고하는 경우는 외부/system 진단만 억제한
정형 회계로 허용합니다. NOLINT/project/mixed/malformed/count-mismatch suppression은 계속
`ERROR`/`NOT_RUN`으로 fail-closed합니다. `required`는 unavailable 또는 partial/estimated boundary도
`ERROR`로 올리고, `off`는 probe 없이 의도적으로 heuristic 경로를
사용합니다. probe는 caller가 고정한 bounded source snapshot과 mapped-source cache를 사용하며
replay 전과 도구 완료 후 source identity를 재검증합니다. C++ 전체 source inventory는 최대 2,048
source files와 64 MiB aggregate UTF-8 source bytes cap을 넘지 않도록 수집합니다. 성공한 configuration
coverage가 누락되거나 configuration-dependent geometry가 관찰되면 partial warning이고 `required`에서는
오류입니다. 한 실행의 한도는 2,048 units, source당 8 MiB, run source bytes
64 MiB, mapped-source cache bytes 16 MiB, output 1,000,000자, parser 10초, unit당 120초, 전체
600초입니다. same-line/overload, constructor와 braced parameter/default/noexcept/trailing
`requires` expression, function-try/catch, `<%`/`%>` digraph body를 regression mapping으로
검증하고 assigned `[]`/`+[]` lambda initializer의 phantom fallback 함수를 배제합니다. approved
tool executable은 매 process 실행 직전에 다시 resolve하고
device/inode/mode/size/mtime/ctime identity를 확인하며, 변경·부재는 fail-closed입니다.
`dir_fd`/`O_DIRECTORY`를 쓸 수 없는 fallback도 read 뒤 resolved named path의
containment 및 device/inode/size/mtime identity를 재검사해 intermediate symlink/TOCTOU를
fail-closed합니다.

PR #130의 historical compiler-boundary baseline은 두 번 byte-identical인 candidate SHA
`7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`와 Python 3.10
`1,626 passed, 2 skipped`를 남겼습니다. 이후 local
`feat/cpp-function-scope-policy` candidate는 두 번 byte-identical인 `dist/ici.pyz` SHA
`2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7`이며, real extracted
`clang-tidy-21`을 사용한 Python 3.10 full suite `1,656 passed, 2 skipped`, Ruff check/format,
mypy와 packaged smoke가 통과했습니다. Parser/source mapping(628 pure code lines)과 process
runner facade(487 pure code lines)는 분리되어 self line gate도 통과합니다. Fresh clean toy
`main`의 BuildScope `auto`/`required`, DiskMap `auto`, LogLens `auto` 교차 probe와 4/4
title·Zero-CDN 검사도 완료됐으며 상세는 [C++ function-scope policy workthrough](workthrough/2026-09-02-cpp-function-scope-policy.md)에
기록합니다.

[PR #131](https://github.com/jihoon22-lee/ici/pull/131) `feat(complexity): classify C++ function
scopes and metric provenance`는
[`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7)로
squash merge됐습니다. PR CI [run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495)은
성공했고 sticky marker/current run은 정확히 하나였습니다. PR ici/viewer Pages는 HTTP/title/
Zero-CDN과 artifact byte-match를 통과했으며 각각 `7,454,995`/`356,598` bytes였습니다.
Exact-main [run `33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450)도
성공했고 main JSON `source_commit`이 같은 SHA와 일치했습니다. main ici/viewer Pages 역시
HTTP/title/Zero-CDN과 byte-match를 통과했으며 ici는 `7,454,995` bytes/SHA
`182a0d05…5adbb75`, viewer는 `356,598` bytes/SHA `fb772d4a…c0c4794`였습니다. 두 run의
skip은 예상된 PR/main publish job뿐입니다. 이 acceptance는 scope-policy slice에 해당하며
target-local GNU ELF section-GC를 넘어서는 whole-program/dynamic dead reachability, full
semantic duplicate, 남은 I4-4, I4 전체 checkpoint를 닫지 않습니다. 버전은 `0.10.2`로 유지하고
release는 만들지 않습니다.

##### C++ Qt clazy 및 생성 단계 정책

Qt 분석은 exact `CompilationContext`가 있고 capability inventory가 승인한 `clazy` 실행 파일이
있을 때 covered production translation unit을 검사합니다. canonical probe는
`clazy-standalone`을 먼저 선택하고, 배포판의 compiler-wrapper인 `clazy`를 두 번째 provider로
기록합니다. `[engines.lint]`에서 정책과 profile/check를 설정할 수 있습니다.

```toml
[engines.lint]
clazy = "auto"             # auto | required | off
clazy_profile = "level0"   # level0 | level1; ici.profile과 독립
# level2 또는 특정 noisy check를 의도적으로 선택할 때만 사용
# clazy_checks = ["qdatetime-utc", "qcolor-from-literal"]
```

`auto`는 도구 또는 exact context가 없을 때 명령을 실행하지 않고 optional `WARN`을 남기며,
`required`는 같은 조건을 `ERROR`로 승격합니다. `off`는 clazy를 실행하지 않습니다.
`clazy_profile`의 기본값은 `level0`이고 global `ici.profile`이 `fast`/`standard`/`deep`으로
바뀌어도 clazy rule semantics는 바뀌지 않습니다. `clazy_checks`는 1~128개의 중복 없는
bounded check 이름 목록이며, 지정하면 profile보다 우선하므로 level2/manual noisy check는
명시적으로 opt-in해야 합니다.

standalone command는 approved executable에 `--checks=<checks>`, `--only-qt`, 원본 source와
`--` 뒤의 sanitized compiler arguments를 전달합니다. wrapper command는 capability inventory가
승인한 `clang++`를 `CLANGXX`로 고정하고 replacement environment의 `CLAZY_CHECKS`로 선택을
전달합니다. 두 provider 모두 loader가 만든 immutable context만 재생하고 compilation database를
다시 읽지 않으며 `-p`, `--fix`, shell, source/context 변경을 사용하지 않습니다. stdin은 닫힌
빈 입력이고 argv·path·version·return code·timeout/truncation은 `ToolEvidence`로 보존됩니다.

clazy text parser는 `-Wclazy-<check>`와 함께 섞인 일반 compiler warning을 bounded 문법으로
원자 검증합니다. 일반 warning은 별도 compiler lint의 중복 보고를 피하려고 제외하며, malformed
또는 알 수 없는 출력은 부분 성공과 합치지 않고 atomic `ERROR`로 닫습니다. located clazy
diagnostic과 parent rule을 따르는 note는
project-relative 파일·1-indexed line/column target으로 보존하고 `family = "clazy"`와
`clazy-<check>` rule ID를 기록합니다. Ubuntu Noble clazy 1.11의 legacy raw-source/caret/
replacement context도 located diagnostic의 project source line과 raw text가 exact match일 때만
허용하고, 뒤따르는 bounded replacement preview는 하나로 제한합니다. source mismatch,
forged/extra preview와 그 밖의 malformed legacy context는 partial finding 없이 atomic `ERROR`로
처리합니다. finding category는 다음과 같이 안정적으로 매핑됩니다.

legacy context가 Qt와 같은 외부 header를 가리킬 수 있으므로 parser는 exact sanitized compiler
argv의 명시적 include root만 추가 read authority로 사용합니다(최대 512개 bounded directory와
project root). 외부 root의 source preview도 허용되지만 diagnostic target과 report 위치는
항상 `[external]`로 투영됩니다. source 파일은 `O_NOFOLLOW` regular-file descriptor로 열고
열기 전후의 device/inode/size/mtime identity를 비교합니다. 읽는 동안 바뀌거나 symlink·비정규
파일이면 거부하며, source-context 누적은 1,000,000 bytes, 한 줄은 8,192 characters 이하일
때만 검증합니다. root 부재, identity mismatch, exact source mismatch, forged/extra preview와
bound 초과는 finding 일부를 남기지 않고 fail-closed합니다.

clang-tidy/clazy 또는 complexity function-boundary probe가 Clang 기반이고
compilation context의 compiler가 capability-approved `g++`인
경우에는 선택 GCC의 libstdc++를 별도로 고정합니다. replay compiler가 선택된 `g++`와 resolved
file identity가 같은지 먼저 확인하고, 그 GCC를 `c++`와 `c`로 각각 한 번씩 `-E -x <lang> -v -`
bounded probe합니다. probe에는 sanitized `-m*`와 `--sysroot`/`-isysroot` selector만 보존합니다.
C++ search roots에서 C search roots를 빼고 남은 디렉터리를 compiler가 보고한
순서 그대로 `-nostdinc++`와 `-isystem <root>` 쌍으로 두 도구에 투영합니다. 각 probe는 최대
5초, 합계는 최대 10초이고 131,072 output characters·64 directories 범위입니다. identity가
다르면 projection 대상이 아니며, 일치한 GCC의 malformed/timeout/truncated/nonzero probe 또는
C++ 표준 라이브러리 경로 미확인은 analyzer 실행 전 `ERROR`입니다.
각 probe는 `g++ stdlib include search` `ToolEvidence`로 기록되고 동일 replay key에서는 캐시됩니다.
projection cache와 probe는 resolved compiler의
`device/inode/mode/size/mtime/ctime` 및 working-directory의 `device/inode/mode/mtime/ctime`
identity에 함께 묶이며, probe 전후 identity가 달라지면
atomic `ERROR`가 됩니다. C translation unit에는 이 projection을 적용하지 않고, GCC 또는 working
directory가 교체되면 cache identity가 달라져 다시 probe합니다.

clazy process 자체가 nonzero로 종료되면 출력에 warning만 있어도 parser를 시도하지 않고
atomic engine `ERROR`를 발행합니다. `ToolEvidence.error`와 오류 target에는 bounded exit code와
`fatal`/`error`/`warning`/`note`/`remark` kind count, processing/output 여부만 남기며 raw
stdout/stderr prose와 host path를 복사하지 않습니다.

| clazy normalized rule group | v3 category |
|---|---|
| `clazy-lifetime`, `clazy-ownership`, `clazy-parent-less`, `clazy-qobject-cast` 자체 또는 `-`/`.` child | `RESOURCE` |
| exact resource rules: `clazy-connect-3arg-lambda`, `clazy-ctor-missing-parent-argument`, `clazy-lambda-in-connect`, `clazy-post-event`, `clazy-returning-data-from-temporary`, `clazy-temporary-iterator` | `RESOURCE` |
| `clazy-qt6`, `clazy-deprecated`, `clazy-qstring-arg`, `clazy-qt-keyword` 자체 또는 `-`/`.` child | `COMPATIBILITY` |
| exact compatibility rules: `clazy-modernize-overloaded-connects`, `clazy-no-module-include`, `clazy-old-style-connect`, `clazy-qenums`, `clazy-qstring-ref`, `clazy-use-chrono-in-qtimer` | `COMPATIBILITY` |
| `clazy-qobject`, `clazy-connect`, `clazy-signal`, `clazy-slot`, `clazy-qevent-cast` 자체 또는 `-`/`.` child | `CORRECTNESS` |
| exact correctness rules: `clazy-assert-with-side-effects`, `clazy-base-class-event`, `clazy-child-event-qobject-cast`, `clazy-const-signal-or-slot`, `clazy-copyable-polymorphic`, `clazy-ifndef-define-typo`, `clazy-incorrect-emit`, `clazy-install-event-filter`, `clazy-jni-signatures`, `clazy-lambda-unique-connection`, `clazy-missing-qobject-macro`, `clazy-missing-typeinfo`, `clazy-mutable-container-key`, `clazy-overloaded-signal`, `clazy-overridden-signal`, `clazy-qhash-with-char-pointer-key`, `clazy-qproperty-type-mismatch`, `clazy-qproperty-without-notify`, `clazy-qstring-varargs`, `clazy-rule-of-three`, `clazy-rule-of-two-soft`, `clazy-signal-with-return-value`, `clazy-skipped-base-method`, `clazy-thread-with-slots`, `clazy-unexpected-flag-enumerator-value`, `clazy-virtual-call-ctor`, `clazy-virtual-signal`, `clazy-writing-to-temporary`, `clazy-wrong-qevent-cast` | `CORRECTNESS` |
| 그 밖의 clazy rule (container detach/temporary 및 stem을 임의 substring으로만 포함하는 rule 포함) | `MAINTAINABILITY` |

행 순서가 precedence입니다. 특히 `clazy-qobject-cast`는 `clazy-qobject`보다 먼저
`RESOURCE`로 분류됩니다. `extra.cpp_diagnostic_category_policy`는 `tool-rule-v1`이고,
`extra.cpp_diagnostic_categories`는 모든 v3 category의 count를 포함합니다.

clazy adapter는 최대 2,048 translation units, unit당 120초, 전체 600초 global budget과
1,000,000자 output bound를 적용합니다. context/coverage/replay/parse/process 오류와 timeout,
truncation, budget 초과는 heuristic으로 숨기지 않고 `ERROR`/`NOT_RUN`으로 기록합니다.

같은 lint 실행에서 source scope의 `.ui`, `.qrc`, `Q_OBJECT` 선언도 bounded하게 찾습니다.
`ui_<stem>.h`의 bounded indirect translation-unit include linkage, `qrc_<stem>.cpp`의
generated compilation unit, 그리고
`moc_<stem>.cpp`·`<stem>.moc`·`mocs_compilation.cpp`의 Q_OBJECT 연결을 exact compilation
database로 검증하고, 누락 시 원본 `.ui`/`.qrc`/헤더 파일과 선언 라인에 FAIL target을 남깁니다.
exact context의 include/define와 successful compiler replay에서 Qt 5/Qt 6 major를 식별하며,
성공 replay가 확인된 경우에만 generated linkage와 `QtCompatibility:Qt5`/`QtCompatibility:Qt6`
PASS를 기록합니다. major가 불명확하거나 replay가 없거나 generated stem이 중복되면 WARN으로
남깁니다. 이 검증은 CMake AUTOMOC/AUTOUIC/
AUTORCC와 qmake의 direct generated unit 양쪽을 다룹니다.

#### v0.10.2 공개 전 release boundary (historical snapshot)

아래 구현·CI 수치는 v0.10.2 공개 전 release sequence를 보존한 historical snapshot이다.
현재 공개 경계와 stable artifact는 이 문서 상단의 [현재 공개 릴리스와 검증된 artifact](#현재-공개-릴리스와-검증된-artifact)를 따른다.

v0.10.2 corrective source의 full local contract는 `1,565 passed, 4 skipped`였고, skip은 로컬 환경의
`clang-tidy`·`clazy`·`clang++` 미설치에 따른 것입니다. CI와 release workflow는 clazy를 설치하고
`ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`을 설정해 실제 clazy/Qt process E2E가 조용히 skip되지
않게 합니다. I4-2 PR #122의 head `c3a8fe21639cecef395f0bc28777066401927da0`은 [run
`33499500259`](https://github.com/jihoon22-lee/ici/actions/runs/33499500259)에서 1,517/1,517 테스트(네 개 actual compiler/clang-tidy/clazy process E2E
포함), Qt 5/Qt 6, self/viewer dogfood, publisher/sticky comment, Merge Gate를 통과했고,
squash merge 뒤 [exact-main run `33500281653`](https://github.com/jihoon22-lee/ici/actions/runs/33500281653)도 같은 tool/matrix/dogfood/Merge Gate와 trusted
main publication 및 ici/viewer Pages 감사를 통과했습니다. 따라서 ici의 I4-2 PR/main remote acceptance는 완료됐습니다.
v0.10.0 release workflow run `33503441322`도 provenance와 9개 artifact 감사를 통과했습니다.
첫 toy-projects BuildScope B5 run이 production `-Werror`의 diagnostic-tool 승격 결함을 드러내
v0.10.1 보정과 released-artifact 재검증이 후속 gate가 됐습니다.

다중 GCC 회귀는 Ubuntu 24.04에서 GCC 13과 GCC 14를 함께 설치해 재현했습니다. toy-projects
PR #38의 run `33531285208`은 Qt 5와 Qt 6 deep에서 clazy가 compile database의 선택 GCC가
아닌 최신 libstdc++ header를 선택해 실패했습니다. fixed local `dist/ici.pyz`는 선택 GCC의
projection으로 `/usr/include/c++/13`, `/usr/include/x86_64-linux-gnu/c++/13`,
`/usr/include/c++/13/backward`를 `-nostdinc++` 뒤 ordered `-isystem`으로 전달했고, 2 probes와
12 sources에서 clazy exit 0을 기록하면서 expected warnings를 보존했습니다.

최종 기능 PR #126의 [run `33537139817`](https://github.com/jihoon22-lee/ici/actions/runs/33537139817)은
actual compiler/clang-tidy/clazy process E2E를 포함한 1,569/1,569 테스트, Qt 5/Qt 6,
self/viewer dogfood, 단일 sticky comment, Zero-CDN Pages와 Merge Gate를 통과했습니다. 같은
exact source를 사용한 toy-projects PR #38의 [run `33537952439`](https://github.com/jihoon22-lee/toy-projects/actions/runs/33537952439)도
Qt5/Qt6 BuildScope deep 검증과 release contract를 포함한 21개 job 전체를 통과했습니다. squash
merge commit `b1b3cc149c72eef6f71370364ab7eaf24d48ca40`의 [exact-main run
`33538985765`](https://github.com/jihoon22-lee/ici/actions/runs/33538985765)은 trusted main
publication과 Merge Gate까지 성공했고, main ici/viewer Pages는 HTTP 200, 정확한 report title,
외부 resource 0개였습니다. 이 공개 전 release-prep sequence는 historical evidence로 보존한다.
현재 `v0.10.2` tag와 공개 artifact는 상단 release evidence와
[`v0.10.2 public evidence workthrough`](workthrough/2026-09-02-public-v0.10.2-evidence.md)를 따른다.

cycle은 configuration별로 compiler `-E -H` trace를 실행해 실제 active include edge와 resolved
path를 수집하고 `project`/`generated`/`system`/`third_party` scope를 집계합니다. 각 configuration
graph를 독립적으로 분석하고 동일 cycle component만 중복 제거하며 configuration 간 edge는
union하지 않습니다. 같은 component가 여러 configuration에서 확인되면 configuration 목록은
metadata로만 보존됩니다. compiler가 active missing include를 보고하면 include 위치의
`CppIncludeUnresolved` `WARN`으로 남기고 해당 edge는 연결하지 않습니다. trace가 malformed,
truncated, timed out이거나 검증할 수 없는 nonzero 종료·replay/spawn 실패이면
`ERROR`/`NOT_RUN`입니다.

실제로 compilation context/database가 없는 경우에만 C++ lint가 `g++ -fsyntax-only -std=c++17
-Wall -Wextra` 휴리스틱 폴백을, cycle이 unique project path-suffix 휴리스틱을 사용합니다.
lint fallback도 ready capability의 direct `g++`를 우선하고 exact replay와 같은 positive allowlist,
argument bound, project/compiler 경계, minimal replacement environment와 closed stdin을 적용합니다.
unsafe package/include flag나 project-contained/non-canonical driver는 실행 전에 거부됩니다.
도구를 실행할 수 있었던 두 폴백 결과는 `ESTIMATED`이며, g++ 자체가 없거나 폴백 실행이
실패하면 `ERROR`/`NOT_RUN`입니다. cycle의 ambiguous/unresolved include는 위치와 후보를
함께 보고합니다.

환경과 지원 범위를 실행 전에 확인하려면 `ici doctor`를 사용합니다. 일반 출력은 설치된 도구와
프로젝트별 엔진 matrix를 표로 보여주고, `--brief`는 프로젝트 언어·프레임워크 한 줄만 더하며,
`--json`은 verify report와 같은 support matrix 구조를 자동화에 제공합니다. doctor는 분석 엔진을
실행하지 않으므로 적용 가능한 활성 행도 이 시점에는 정확히 `NOT_RUN`/active mode 없음으로 표시됩니다.

### 2.2.1 기준선 비교와 delta gate

기준선은 이전 실행의 finding inventory를 저장한 `ici.result/v3` JSON입니다. 현재 실행의
finding을 기준선과 비교하면 새로 생긴 문제뿐 아니라 위치 이동, 해결, 심각도와 suppression
변경까지 한 번에 확인할 수 있습니다.

```bash
# 최초 기준선 생성 또는 의도적인 기준선 갱신
ici verify --write-baseline .ici/baseline.json

# 비교 결과만 확인하고 기존 엔진 gate 정책은 그대로 둠
ici verify --baseline .ici/baseline.json \
  --report --html verify_report.html --github-summary

# 새 actionable finding 또는 regression이 있으면 exit 1
ici verify --baseline .ici/baseline.json --fail-on-new \
  --report --html verify_report.html --github-summary
```

`--fail-on-new`는 `--baseline` 없이 사용할 수 없으며, 이 조합은 exit 2입니다. `--baseline`은
기준선의 `schema_version`이 정확히 `ici.result/v3`인지 확인하므로 v2 또는 다른 JSON을
기준선으로 사용할 수 없습니다. baseline 입력과 `--write-baseline` 출력은 프로젝트 루트에
canonical하게 포함되는 경로여야 합니다. `..`로 루트를 벗어나거나 프로젝트 안의 symlink가
루트 밖으로 해석되는 경로는 읽기·쓰기를 모두 거부합니다. 기준선 finding이 가리키는
primary/related location도 같은 프로젝트 내부 규칙을 따릅니다.

기존 기준선과 출력 경로를 같게 지정하는 것은 허용됩니다. 예를 들어
`--baseline .ici/baseline.json --write-baseline .ici/baseline.json`은 기존 파일을 먼저
읽고 비교한 뒤 새 v3 파일로 교체합니다. 반면 `--report`의 고정 출력
`verify_report.json`과 `--write-baseline` 경로를 같게 지정하면 report를 덮어쓸 수 있으므로
exit 2로 거부됩니다. 또한 `--fail-on-new`가 실제로 실패한 실행에서는 입력 baseline과 같은
경로를 덮어쓰지 않습니다. 그렇지 않으면 실패한 finding이 다음 실행의 기준선으로 자동
승격되어 regression을 숨길 수 있기 때문입니다. 새 snapshot이 필요하면 다른 출력 경로에
기록해 리뷰한 뒤 의도적으로 교체합니다.

#### Delta 상태와 gate 판정

| 상태 | 의미 | `--fail-on-new`에서의 처리 |
|---|---|---|
| `new` | 현재 실행에만 있는 finding | `info`가 아니고 suppressed가 아니면 gate |
| `unchanged` | 같은 엔진·fingerprint·위치로 매칭된 finding | severity가 높아지거나 suppression이 해제된 regression이면 gate |
| `moved` | 같은 엔진·fingerprint의 매칭되지 않은 occurrence가 새 위치와 기준선 위치로 대응됨 | severity/suppression regression이면서 현재 finding이 actionable이면 gate |
| `resolved` | 기준선에만 있고 현재 실행에는 없는 finding | 해결된 항목이므로 gate하지 않음 |

`regressed`는 severity가 더 심각해졌거나, 기준선에서는 suppressed였는데 현재 실행에서
suppression이 해제된 경우입니다. 같은 위치의 severity 변경은 `unchanged` 상태에서도
regression이 될 수 있습니다. 현재 finding이 `info`이거나 suppressed이면 새 항목이어도
gate에서 제외됩니다. 반대로 suppression은 현재 finding 하나를 의도적으로 조치 대상에서
제외하는 표시이고, baseline은 과거 inventory의 snapshot입니다. baseline 비교는 suppressed
항목도 delta에 남기며, baseline을 suppression 설정 대신 사용할 수 없습니다. suppressed
finding을 다시 활성화하면 suppression regression으로 표시될 수 있습니다.

baseline metadata의 producer/fingerprint/policy/tool policy가 현재 실행과 다르면
호환성 warning으로 표시됩니다. warning 자체만으로 `gate_failed`가 되거나 exit 1이 되지는
않습니다. 다만 엔진 자체의 `FAIL`/`ERROR` 등 기존 suite gate 결과는 별도로 적용됩니다.

비교 결과는 다음 경로에서 같은 계약으로 확인할 수 있습니다.

- `--report`: `baseline_comparison` 안에 네 상태의 전체 count와 각 delta의 현재·기준선 위치,
  severity, `regressed`/`suppressed`/`gated`를 보존하는 v3 JSON을 씁니다.
- `--html`: `Baseline Delta` 탭에서 gate 상태, 호환성 warning, issues-first 상세를 보여줍니다.
  화면은 길이를 제한할 수 있지만 JSON에는 전체 inventory가 남습니다.
- `--github-summary`: Markdown Summary에 네 상태 count와 이슈 우선 delta 표를 추가합니다.
- 콘솔: `Baseline Finding Delta` 패널과 gate 우선 상세를 출력합니다.
- sticky PR 댓글: `report-pr`/신뢰된 `publish` job이 JSON을 소비해 새 finding·regression·gated
  count와 compatibility warning을 요약합니다. 전체 delta 위치와 메시지는 HTML/JSON 및
  Markdown 상세에서 확인합니다.

### 2.2.2 Issues-first 콘솔

콘솔은 전체 inventory를 보존하면서 동일한 원인을 반복해서 펼치지 않는 issues-first projection을
제공합니다.

| 옵션 | 동작 |
|---|---|
| `--verbose` | `ici verify`에서만 사용하는 상세 표시 모드이며 console cap을 해제 |
| `--max-findings N` | 엔진별 console display group 상한. 기본값은 엔진별 5건이며 `0`은 summary만 표시 |
| `--group-by engine\|severity\|category\|file\|rule` | v3 finding의 engine, severity, category, canonical primary file 또는 rule 기준 표시 그룹 선택 |

사용 형태는 다음과 같습니다.

```bash
# 엔진별 최대 5건, 파일 기준 표시 그룹
ici verify --group-by file

# summary만 출력
ici verify --max-findings 0

# 상세 표시 모드: cap 해제
ici verify --verbose --max-findings 10 --group-by severity
```

console cap만 console 전용이며, canonical Python rule grouping은 console·HTML·Markdown이
공유하는 표시 projection이다. Review된 rule family만 canonical id로 표시하고, broad Ruff
rule은 bounded AST/tool context가 같은 operation을 증명할 때만 native AST rule과 묶는다.
cross-producer merge에는 같은 canonical project-relative Python path, 양쪽의 `end_line`·
`start_column`·`end_column`을 포함한 1-indexed precise region, 실제 source overlap이 모두
필요하다. line-only 위치, column이 빠진 finding, 인접하지만 겹치지 않는 위치, 다른 파일,
unknown rule은 별도 그룹으로 남긴다. 그룹은 `original_finding_count`, producer별 count와
engine/rule/tool version provenance를 표시한다.

`--report` JSON과 baseline은 이 projection을 적용하지 않는다. 각 producer의 원본
`targets`/`findings`, fingerprint, 정확한 line+column location, tool identity와 delta
occurrence를 그대로 보존하며 projection이 suite나 baseline을 변경하지 않는다. HTML `Issues`
탭은 전체 display projection, Markdown은 bounded canonical table, console은 cap이 적용된
projection을 보여준다. duplicate의 별도 clone-group 병합도 같은 표시 원칙을 따르며 원본
occurrence와 fingerprint를 유지한다. 80-column 터미널에서 표·링크·상세가 한 글자씩 세로로
깨지지 않도록 회귀 테스트로 고정했다.

다음은 issues-first console을 도입한 PR #89 당시의 고정된 acceptance 기록이다
(`814679c` + `d80a027`). 당시 로컬 Python 3.10 전체 품질 게이트는 756/756 tests,
focused console 테스트는 16개였다. Ruff check/format,
pure-Python 10-distribution·no-certifi·2.0 MiB pyz, smoke 전체 검증도 통과했다. 최종 안정
self verify에서 built `dist/ici.pyz`는 exit 0, suite는 WARN이었다. self verify 출력은 144
lines/15,288 bytes, HTML은 3,383,523 bytes였고, 해당 출력에 내장된 test engine 수치는
756/756이다. local self verify line/function/branch coverage는 87.8%/96.6%/78.8%, TEM 4.83을 확인했다.
engines는 Pass 8, Warn 4, Fail 0,
Error 0, Skip 0이며 complexity는 최대 23·이슈 64건, duplicate는 16.2%·338 groups·
1,006 actionable occurrences였다. 콘솔 측정은 actionable 1,088건, visible 21/420 display
groups, represented 34, hidden 1,054 findings/399 groups였다. HTML에는 clone group card
338개와 issue engine row 1,088개가 유지됐고 external script/stylesheet reference는 0개였다.

Merge evidence (PR #89): [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
[`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
병합됐다. [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의
모든 required checks가 green(756 tests)이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/89#issuecomment-5470778278)에
결과가 기록됐다. CI report stats는 ici WARN(TEM 4.83, Pass 8, Warn 4, line 87.8%,
function 96.6%, branch 78.9%), viewer PASS(TEM 4.89, 7/7 tests)였다. [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/89/)
는 HTTP 200·external script/stylesheet refs 0, [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/89/)
는 HTTP 200·external refs 0이었다.

### 2.3 신뢰된 실행에서 HTML 리포트 배포 (`--publish`)
`--publish`는 일반 PR 검증의 기본 동작이 아닙니다. 권한을 명시적으로 부여한 신뢰된
`main` push 또는 수동 실행에서만 `verify_report.html`을 `gh-pages` 등 설정된 경로로
배포합니다. 이 저장소의 PR workflow는 읽기 전용 verify가 JSON/HTML 아티팩트를 만든 뒤,
신뢰된 base 코드를 실행하는 별도 게시 job이 sticky 댓글을 작성하고 실제 HTML URL까지
확인합니다 (자세한 설정은
[CI/CD 연동 가이드](ci-integration.md#3-신뢰된-html-publish---publish) 참조):

```bash
dist/ici.pyz verify --report --html verify_report.html --github-summary --publish
```

이 기능은 GitHub Contents API와 쓰기 토큰이 필요하므로, 실행 job의 `contents: write`와
토큰 범위를 대상 저장소 정책에 맞게 검토해야 합니다.

### 2.4 빌드 산출물 (`ici build`)

`ici build`는 프로젝트 루트 안에서만 릴리스 트리를 만들고, 실제로 생성된 산출물이
있을 때만 `env.sh`/`env.csh`를 함께 생성합니다. 출력은
`vX.Y.Z/x86_64/{lib,bin}` 아래에 놓이며, 환경 스크립트는 산출물 개수에 포함되지
않습니다. Python `.py` library가 하나 이상 있으면 launcher 없이도 library 빌드가
성공할 수 있지만, 산출물이 전혀 없으면 `FAIL`입니다.

Python launcher는 임의의 `main.py`를 자동 선택하지 않습니다. 다음 중 하나를
명시해야 합니다.

```toml
[build.python]
entrypoint = "pkg.cli:main"
```

명시적 entrypoint가 없으면 `pyproject.toml`의 `[project.scripts]`에 있는 모든
`script = "dotted.module:callable"` 항목을 launcher로 만들며, script 이름·target
문법과 selected source directory 안의 실제 `.py` 또는 package `__init__.py`를
검증합니다. 잘못된 metadata, entrypoint, launcher 경로, 기존 symlink는 traceback
대신 구조화된 `ERROR`/`NOT_RUN`으로 처리됩니다.

Python library는 `project.source_dirs`에 설정된 모든 source directory의 프로젝트
내부 non-symlink `.py`만 복사하며 source tree에 `compileall` 또는 `.pyc`를 만들지
않습니다. C++는 루트 빌드 디스크립터에 따라 경로가 갈립니다(§2.5). generic `g++`
경로에서는 `int main(...)` 정의가 정확히 하나인 단순 executable만 허용하며, g++
timeout·절단·signal·spawn 오류와 rc 0인데 regular binary가 없는 경우는 `ERROR`입니다.

### 2.5 C++ 빌드 경로는 여러 가지다

`ici`는 **프로젝트 루트**의 빌드 디스크립터를 보고 경로를 고릅니다.

| 루트에 있는 것 | `build`와 `test`가 하는 일 |
|---|---|
| `CMakeLists.txt` | CMake로 configure·build하고 CTest로 테스트한다 |
| `*.pro` | qmake로 configure·build하고 `make check`로 테스트한다 |
| `Makefile`/`makefile`/`GNUmakefile` + `[build.make].enabled=true` | 설정한 Make direct argv로 configure·build·test한다 |
| `Makefile` 계열 + Make 설정 비활성화 | backend를 추측하지 않고 명시적 오류로 거부한다 |
| 루트 디스크립터 없음 | 지원 가능한 source를 generic `g++` 경로로 처리한다 |

CMake/qmake adapter는 각자의 variant shadow 디렉터리를 씁니다 —
`build/ici-<backend>-build`(계측 없음), `build/ici-<backend>`(coverage),
`build/ici-<backend>-asan`(ASan/UBSan), `build/ici-<backend>-tsan`(TSan). configured Make는
설정한 하나의 `shadow_dir`를 variant 간 공유하므로 `clean_argv`와 각 build command가 실행
전에 shadow를 정리·재생성해야 합니다. generic g++ 경로는 자체 release output과 임시 test
경로를 사용합니다.

Make backend는 다음처럼 명령을 명시해야 합니다. `workdir`와 `shadow_dir`는 project root
안에 있어야 하고, build/test/coverage/sanitize/TSan variant별 direct argv를 독립적으로
지정할 수 있습니다. `build_argv`는 필수이며, coverage/sanitize/TSan variant의 build 명령은
해당 override가 없으면 명시적 오류로 거부합니다(variant test 명령은 기본 `test_argv`로
fallback할 수 있습니다).

```toml
[build.make]
enabled = true
workdir = "."
shadow_dir = "build/ici-make"
out_of_tree = "allow"       # allow | required
configure_argv = []
build_argv = ["make", "all", "-j", "{jobs}"]
coverage_build_argv = ["make", "coverage", "-j", "{jobs}"]
sanitize_build_argv = ["make", "sanitize", "-j", "{jobs}"]
thread_sanitize_build_argv = ["make", "thread-sanitize", "-j", "{jobs}"]
test_argv = ["make", "check"]
clean_argv = ["make", "clean"]
jobs = 2
# 위 target 이름은 프로젝트의 실제 Make target으로 바꿉니다. 모든 variant가 이 shadow를
# 공유하므로 clean/recreate가 각 command에 보장되어야 합니다.
```

각 명령은 최대 64 token/32 KiB 범위의 shell-free argv여야 하며 `$()`·backtick·`sh -c`와
같은 shell 실행이나 `{jobs}`를 다른 문자열에 삽입하는 방식은 허용하지 않습니다. Build adapter는
toolchain 발견을 위해 ici process 환경과 variant 계측 환경을 전달하므로 sandbox가 아닙니다. 재현성
검증에는 환경이 고정된 CI runner를 사용해야 합니다.
명령·backend 선택·workdir·shadow 경계는 `ToolEvidence`에 기록되고, 성공한 binary/library는
producer target과 redacted command provenance를 포함한 `ici.artifacts/v2` manifest가 됩니다.

**정적 링크를 쓰는 프로젝트는 주의가 필요합니다.** `-static`과 `-fsanitize=address`는
함께 쓸 수 없으므로, 정적 링크를 sanitizer 빌드에서는 끄도록 빌드 정의에 조건을 두어야
합니다. `viewer/CMakeLists.txt`가 그 예입니다.

하위 디렉터리의 디스크립터는 보지 않습니다. `src/gui/CMakeLists.txt`만 있는
프로젝트는 g++ 경로를 씁니다. 둘 다 있으면 CMake를 고르고, **왜 그 백엔드가
선택됐는지는 리포트의 도구 증거에 남습니다.**

`viewer`처럼 GUI를 선택적으로 제공하는 프로젝트는 루트 CMake 옵션으로 툴킷
의존성을 경계 짓습니다.

```bash
# Qt가 없는 환경에서도 정적 CLI만 구성·빌드
cmake -S viewer -B viewer/build-cli -DICIRV_BUILD_GUI=OFF
cmake --build viewer/build-cli --target icirv

# 설치된 Qt 6(또는 Qt 5) GUI와 QtTest를 구성
cmake -S viewer -B viewer/build-gui -DICIRV_BUILD_GUI=ON
cmake --build viewer/build-gui --target icirv-gui test_main_window
```

`ICIRV_BUILD_GUI=ON`은 `find_package(QT NAMES Qt6 Qt5 ...)`로 Qt 6을 우선
탐색하고 Qt 5로 폴백합니다. Qt 5 호환성을 명시적으로 확인하려면
`-DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON`을 추가합니다. `icirv` CLI는 두 구성 모두
Qt를 링크하지 않으며, 정적 배포가 필요하면 `ICIRV_STATIC=ON`을 유지합니다.

어댑터 경로에서 달라지는 것이 셋 있습니다.

- **`project.cpp_external_build_dirs`가 무시됩니다.** 이 설정은 "moc가 필요해 ici가
  직접 빌드할 수 없는 소스를 링크 대상에서 뺀다"는 뜻인데, 어댑터 경로에서는 빌드
  시스템이 moc를 돌리므로 그 전제가 사라집니다. 바이너리를 만드는 네 엔진
  (`build`·`test`·`sanitize`·`thread_sanitize`)이 모두 어댑터를 쓰므로, 빌드 시스템이 빌드한
  전부가 대상입니다. 디스크립터가 없는 g++ 경로에서는 네 엔진 모두 이 설정을 그대로 따릅니다.
- **`-std=c++17` 고정이 사라집니다.** C++ 표준을 프로젝트의 빌드 정의가 정합니다.
- **`Q_OBJECT` 클래스를 단위 테스트할 수 있습니다.** g++ 경로에서는 moc 실행 단계가
  없어 vtable 미해결로 링크에 실패합니다.

커버리지 계측 플래그(`--coverage`)는 CMake/qmake adapter 경로에서 ici가 주입합니다. configured
Make는 자동 주입하지 않으므로 `coverage_build_argv`를 프로젝트가 선언해야 합니다. CMake/qmake
adapter는 프로젝트의 빌드 트리를 건드리지 않고 `build/ici-cmake` 또는 `build/ici-qmake`에
variant별로 별도 빌드합니다. configured Make는 하나의 설정된 shadow를 사용하므로 command가
clean/recreate를 담당해야 합니다.

이 네 엔진은 같은 `AnalysisContext`의 project/capability snapshot을 읽고, adapter를
호출할 때 각각 release·coverage·sanitize·thread-sanitize variant를 명시합니다. configure/build/test 중
변하는 상태는 mutable `BuildSession`에만 남고, 성공한 파일은 이후 엔진이 수정할 수 없는
frozen `ArtifactManifest`로 발행됩니다. CMake/qmake는 variant별 shadow로 서로의 산출물을
덮어쓰지 않습니다. configured Make는 pipeline에서 직렬 실행되지만 한 shadow를 공유하므로
각 command의 clean/recreate 계약이 stale 혼합을 막아야 하며, manifest는 각 실행 시점의
identity와 digest를 보존합니다.

**두 어댑터 모두 테스트 바이너리 하나를 1건으로 셉니다.** CTest는 원래 그렇고, qmake
경로는 `make check`가 실행한 명령을 기준으로 세어 같은 단위를 씁니다. QtTest 바이너리가
낸 함수 단위 결과는 버리지 않고, 실패했을 때 그 바이너리의 실패 메시지에 함수 이름과
사유로 붙습니다. Make backend의 test 결과도 동일하게 configured `test_argv` transcript를
기준으로 bounded하게 집계합니다.

### 2.5.1 ELF binary compatibility

`binary_compat`는 `deep`에서만 선택되는 release-contract engine이며 기본 비활성입니다.
`build`가 현재 실행에서 발행한 manifest 중 `executable`/`shared-library` artifact만 선택해
`readelf`로 검사합니다. 바이너리를 실행하거나 로드하지 않으므로 runtime 동작 또는 호스트
배포 성공 자체를 증명하지는 않습니다.

```toml
[engines.binary_compat]
enabled = true
required = true
expected_class = "ELF64"
expected_machine = "Advanced Micro Devices X86-64"
max_glibc = "2.35"
max_glibcxx = "3.4.30"
max_cxxabi = "1.3.13"
forbid_absolute_rpath = true
forbid_build_paths = true
forbidden_needed = ["libasan.so"]
allowed_needed = []
allow_non_elf = false
max_artifacts = 64
```

ELF class/machine, GLIBC·GLIBCXX·CXXABI version 상한, DT_NEEDED, RPATH/RUNPATH, build path
leak 및 non-ELF 정책을 확인합니다. manifest가 없거나 `readelf` evidence가 불완전하면
partial PASS가 아니라 `SKIP`/`ERROR`/`NOT_RUN`으로 남고, Python source에는
`unsupported`입니다.

### 2.5.2 Typed Python/C++ integration contracts

`integration`도 deep-only·기본 비활성이고 현재 run의 build manifest를 소비합니다. 설정한
각 case는 shell 없이 argv를 실행하며 `{python:id}` 또는 `{artifact:id}` placeholder는 반드시
하나의 token이어야 합니다. `python_targets`와 상속할 환경변수 이름을 명시한 경우 외에는
프로세스 환경을 넘기지 않습니다.

```toml
[engines.integration]
enabled = true
required = true
max_cases = 32
max_output_bytes = 65536
python_targets = { current = ".venv/bin/python" }
cases = [
  { name = "smoke", argv = ["{artifact:release:shadow:bin/app}"], expected_exit = 0,
    stdout_contains = ["ready"], stderr_not_contains = ["Traceback"], timeout_seconds = 30.0,
    required = true },
]
```

case는 expected exit code, stdout/stderr contains·not-contains, 0.1~300초 timeout, bounded
output artifact(프로젝트 내부 regular file·최소 크기)를 검사할 수 있습니다. 최대 32 case와
64 argv token, 1 KiB~8 MiB output bound를 적용하며 timeout·truncation·unknown/non-whole
placeholder·symlink output 같은 process/configuration 오류는 partial PASS가 아닌
`ERROR`/`NOT_RUN`으로 닫습니다. output artifact가 없거나 최소 크기를 충족하지 못한
assertion은 required case의 `FAIL`, 선택 case의 `WARN`으로 기록합니다. 이 엔진은
sandbox가 아니며 broad end-to-end behavior 또는 arbitrary shell workflow를 대신하지 않습니다.

#### ThreadSanitizer deep profile

`thread_sanitize`는 `deep` profile에서만 선택되는 C++ 전용 엔진입니다. `ici verify --profile deep`
에서는 일반 `sanitize`와 별도 build owner로 실행되고, 필요한 경우 다음 direct command로
단독 실행할 수 있습니다.

```bash
ici thread-sanitize
```

`BuildVariant.THREAD_SANITIZE`의 canonical value는 `thread-sanitize`이며, CMake/qmake
adapter는 `-tsan` shadow에 C++ compile flag `-fsanitize=thread -fno-omit-frame-pointer -g`와
link flag `-fsanitize=thread`를 전달합니다. build descriptor가 없는 generic g++ 경로도 같은
compile instrumentation과 debug/frame-pointer flag를 사용하고 generic `-pthread` link를
추가합니다. 이 variant는 ASan/LSan/UBSan `sanitize` variant와 절대 혼합하지 않습니다.

실행 환경은 기존 `TSAN_OPTIONS` 문자열을 보존하면서 `halt_on_error=1`을 추가합니다. parser는
`WARNING: ThreadSanitizer:` 또는 `SUMMARY: ThreadSanitizer:`의 complete report signature만
진단으로 인정하며, bounded transcript·diagnostic count·stack frame·source read 경계를
적용합니다. 알려진 defect prefix는 deterministic rule ID로 정규화하고, 알 수 없는 TSan
문구는 `ici.sanitize.tsan.thread-safety-defect`로 안정적으로 수렴합니다. project-owned
위치만 primary로 채택하고 외부 stack frame은 `[external]`로 redacted합니다. Python scope는
이 엔진에서 unsupported이며, Python ResourceWarning을 대신 실행하지 않습니다.

실제 `g++` data-race fixture를 포함한 local regression, PR #146 run `33717584710`, exact-main
run `33718399268`, toy PR #56과 exact candidate run `33737405098`의 8/8 contract가 통과했습니다.
이는 TSan sub-scope를 닫지만 broader resource/lifetime/security taxonomy, `I4-4` 전체
checkpoint, version bump 및 release는 이 결과로 닫히지 않습니다.

CMake/CTest 경로가 지원하는 경우 CTest는 실행 전에 예정된 shadow JUnit 파일을 제거한 뒤
`--output-junit`으로 새 report를 만들고, adapter는 그 report만 최대 1,000,000 bytes까지
stable regular-file/no-follow 방식으로 읽습니다. 파일이 없거나 malformed·oversized·읽는 중
변경이면 bounded CTest stdout 결과로 폴백하므로 stale 또는 무제한 XML을 읽지 않습니다.
JUnit의 `failure`/`error`와 `system-out`/`system-err`에서 LeakSanitizer, AddressSanitizer,
UndefinedBehaviorSanitizer marker를 찾으면 nominal PASS status라도 해당 executed case를
실패로 보존하고 public `message`에는 `LeakSanitizer diagnostic` 같은 bounded 분류만 남깁니다.
raw transcript는 sanitizer engine 전용 private `TestCaseResult.diagnostic_output`으로 분리해
UTF-8 최대 65,536 bytes와 truncation flag를 적용합니다. engine은 이를 deterministic
`kind`/`defect`, `ici.sanitize.*` detail rule, related stack-frame locations, 관측/프로젝트
frame count와 process evidence link를 가진 normalized detail로 변환하고, 검증 가능한 경우
project-owned primary location과 native finding을 제공합니다. native finding은 호환 `rule_id`인
`ici.legacy.sanitize.target`을 유지하고
상세 sanitizer identity는 `tool_rule_id`에 두며, 프로젝트 밖의 frame path는 `[external]`로
redacted합니다. timeout,
process-output truncation, malformed/oversized diagnostic, 정규화 오류 또는 project location이
없는 진단은 clean result로 축약하지 않고 `ERROR`/`NOT_RUN` 또는 위치 오류 target으로
fail-closed합니다. 완전한 project-owned location을 가진 signal failure만 measured `FAIL`로
보존하며, 일반 실패 메시지와 test name도 512 characters로 제한됩니다.

adapter 결과의 public `TestCaseResult` 계약은 `name`, `passed`, `message`, `executed`를 가지며,
마지막 네 번째 필드 `executed`는 기본값 `true`인 하위 호환 확장입니다. sanitizer engine만
읽는 private `diagnostic_output`과 `diagnostic_output_truncated`는 raw transport를 bounded하게
전달하기 위한 내부 필드이며 일반 test reporter에는 노출하지 않습니다. 따라서 기존 세 인자
positional 생성은 계속 동작하고, 실행되지 않은 case를 `passed = false`인 일반 실패와
구분할 수 있습니다. `passed = true`와 `executed = false` 조합은 허용하지 않습니다.

CTest JUnit의 `<skipped>`와 `status="notrun"`/`skip`/`skipped`/`disabled`/`blacklisted`는
`executed = false`가 됩니다. JUnit의 알 수 없는 status는 `executed = true`, `passed = false`인
실패로 fail-closed합니다. CTest stdout 폴백에서도 `Not Run`, `Disabled`, `Skipped` verdict를
같은 상태로 보존하고 bounded 사유를 `message`에 남깁니다. QtTest에서는 `skip`과
`<skipped>`가 미실행, `xfail`이 실행된 예상 실패이자 통과, `xpass`가 실행된 실패로
처리됩니다. 알 수 없는 `result`도 `executed = true`, `passed = false`로 처리해 조용한
통과를 막습니다.

pytest도 verbose per-test 출력과 terminal summary를 같은 실행 상태로 정규화합니다.
`SKIPPED`는 수집됐지만 실행되지 않은 case, `XFAIL`은 실행된 예상 실패이자 PASS, `XPASS`는
실행된 unexpected pass이자 FAIL입니다. case별 줄이 없고 summary에 `N skipped`만 있어도
`[Python] Skipped (N)` target으로 미실행 evidence를 보존합니다. 수집된 Python/C++ test가
전부 skip이면 `[engines.test].required = true`인 test engine은 `ERROR`/`NOT_RUN`,
`false`인 선택 engine은 `SKIP`/`ESTIMATED`입니다.

이 상태는 테스트 수와 sanitizer 측정 범위에 각각 다음처럼 반영됩니다.

| 결과 | `test` 엔진 | `sanitize` 엔진 (`required = false`) |
|---|---|---|
| 실행된 case | PASS/FAIL 및 `total_tests`에 포함 | 측정 scope 및 PASS/FAIL에 포함 |
| 미실행 case | `SKIP`, `extra.skipped_tests`·`test_suites[*].skipped`에 포함, 실패 수에는 미포함 | 측정 scope·issue 수에는 미포함 |
| 모두 미실행 | 테스트 target은 SKIP으로 보존 | `SKIP`/`ESTIMATED` |
| clean 실행 + 미실행 혼합 | 실행 결과와 SKIP을 각각 보존 | `WARN`/`ESTIMATED` |
| 실제 failure + 미실행 혼합 | 실행 failure와 SKIP을 각각 보존 | `FAIL`/`ESTIMATED` |

`sanitize`의 기본 required 정책에서 적용 가능한 C++ test가 하나라도 미실행이면 모두
`ERROR`/`NOT_RUN`으로 승격됩니다. 따라서 sanitizer build가 성공했다는 사실만으로 테스트가
실행됐다고 간주하지 않습니다. HTML `Tests & Coverage` 탭은 SKIP case를 amber 색상의 별도
행으로 렌더링해 failure 목록과 섞지 않습니다.

coverage.py/gcov 산출물은 별도의 측정 결과일 뿐 테스트 실행 증거가 아닙니다. Coverage pass나
기존 coverage JSON이 all-skipped pytest run을 `MEASURED`/`PASS`로 승격하거나, 수집됐지만
실행되지 않은 case를 실행된 것으로 바꾸지 않습니다.

qmake 경로에서 `-xunitxml`을 신뢰하지 않는 이유가 있습니다. 그 인자는 **QtTest
바이너리에만** 의미가 있고, 실제 프로젝트는 QtTest와 자체 `main()` 테스트를 섞어
갖습니다. XML만 읽으면 QtTest가 아닌 테스트가 보고에서 조용히 사라집니다. 따라서
QtTest parser는 `-xunitxml`의 각 `<testcase>`에서 skip/xfail/xpass/unknown 상태를 읽어
`TestCaseResult`로 정규화하지만, qmake adapter의 권위 있는 집계는 `make check` transcript의
테스트 바이너리 단위입니다. XML은 해당 바이너리의 failure detail을 보강할 뿐이며, qmake가
모든 function-level skip을 개별 scope로 집계한다고 의미하지 않습니다. make transcript에서
바이너리 호출을 복구할 수 없는 경우에만 case-level XML parser가 제한적인 fallback으로
사용됩니다.

---

## 3. 개별 엔진 단독 실행

특정 검증 항목만 빠르게 진단하고 싶을 때 단독 명령어를 사용합니다:

| 명령어 | 설명 | 상세 레퍼런스 |
|---|---|---|
| `ici line` | 순수 코드/주석/공백 라인 수 및 500/1000줄 규칙 검사 | [Line 엔진 상세](engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 코드 스타일 정렬 검사 | [Lint 엔진 상세](engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 Branch/Function 커버리지, TEM 5.0 스코어링 | [Test 엔진 상세](engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | Mypy 정적 타입 및 AST 부분 폴백 (C++ 타입 검증은 명시적 SKIP) | [Type 엔진 상세](engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici python-compat` | Python runtime `-VV`·compileall·선택 import 및 `requires-python`/syntax/API floor 검증 | [Python 호환성 엔진 상세](engine-reference.md#215--python_compat-python-runtime-호환성) |
| `ici complexity` | 함수별 CC/중첩 분석; C++ 경계는 clang-tidy AST 우선, unavailable 시 heuristic evidence | [Complexity 엔진 상세](engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 및 Python 리소스 누수 검증 | [Sanitize 엔진 상세](engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici thread-sanitize` | deep profile 전용 C++ ThreadSanitizer thread-safety 검증 | [ThreadSanitizer 엔진 상세](engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | Python 도달 불능/미사용 코드 휴리스틱 및 C++ `-Wunused-function` compiler 증거 | [Dead 엔진 상세](engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 언어별 line-preserving Type-2 lexical normalization과 exact seed/region 확장 기반 Copy-Paste 코드 중복률 산출 | [Dup 엔진 상세](engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 삼킴(`except: pass`) 및 소멸자 throw 차단 | [Exception 엔진 상세](engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |

`binary_compat`와 `integration`은 독립 direct command가 아니라 `ici verify --profile deep`의
release-contract dependency graph에서 실행됩니다. `ici build`는 release artifact producer를
직접 실행하는 standalone command이며, 세 release-contract 엔진을 verify에서 사용하려면
해당 설정을 명시적으로 활성화하고 build manifest가 없는 상태를 성공으로 해석하지 않아야 합니다.

모든 검증 단독 명령과 `verify`/`build`는 공통 종료 코드 정책을 사용합니다. `PASS`/`WARN`은
`0`, `FAIL`/실행 `ERROR`는 `1`, 검증을 수행하지 못한 `SKIP`은 `2`를 반환합니다.

---

## 4. 유니버설 에디터 연동 및 gvim/터미널 경로 복사

`ici` HTML 리포트는 특정 에디터(VS Code)를 강제하지 않고, 개발자가 선호하는 툴에 맞춰 자유롭게 이동할 수 있는 **유니버설 에디터 선택기(Open With)**를 제공합니다.

### 4.1 에디터 링크 선택기 (우측 상단 `🛠️ Open With`)
리포트 상단 드롭다운에서 선호하는 액션을 선택하면 브라우저(`localStorage`)에 저장되어 다음 검증 시에도 유지됩니다:
- **📋 Copy Path (Vim/gvim/CLI)**: 클릭 시 `src/ici/core/models.py:45` 경로를 클립보드에 복사하여 `gvim <path> +<line>` 또는 터미널에서 즉시 사용
- **🚀 VS Code**: `vscode://file/<abs_path>:<line>` 스키마로 열기
- **⚡ Cursor**: `cursor://file/<abs_path>:<line>` 스키마로 열기
- **🐍 PyCharm / IntelliJ**: `idea://open?file=<path>&line=<line>` 스키마로 열기
- **🪟 Sublime Text**: `subl://<path>:<line>` 스키마로 열기
- **🌐 Browser**: 로컬 파일 URL(`file://`)로 새 탭에서 열기

### 4.2 원클릭 빠른 경로 복사 버튼 (`📋`)
에디터 설정과 무관하게 모든 파일 링크 옆에 위치한 **`📋` 버튼을 누르면 언제든지 상대 경로(`file:line`)가 클립보드에 즉시 복사**됩니다.

---

> **다음 단계**: [📏 검증 엔진 레퍼런스 (Engine Reference)](engine-reference.md)에서 각 엔진별 상세 수식과 `ici.toml` 설정법을 확인하세요.
