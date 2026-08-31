# ici (Integrated CI) 사용자 가이드

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; **🚀 사용자 가이드** &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬 개발 환경(WSL/Linux), 사내 폐쇄망(RHEL 8.10/CentOS, tcsh/bash), 그리고 GitHub Actions CI/CD 파이프라인에서 같은 정책·결과 계약을 적용하는 단일 실행형 CI 통합 엔진입니다. OS·컴파일러·Python·검증 도구의 가용성과 버전은 실행 증거로 기록되며, 환경이 다르면 실제 결과도 달라질 수 있습니다.

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

현재 내장 descriptor 기준으로 선택되는 범위는 다음과 같습니다.

| profile | 선택되는 내장 엔진 | 용도 |
|---|---|---|
| `fast` | read-only 엔진 10종 | 빠른 편집·pre-commit 피드백 |
| `standard` | 기본 엔진 12종(`test`/`sanitize` 포함) | 일반 로컬·CI 검증 |
| `deep` | 내장 엔진 13종(`cognitive` 포함) | 가장 넓은 분석 범위 |

profile은 engine set만 바꿉니다. 예를 들어 line·complexity의 설정 임계값, test의
coverage 정책 등 동일 rule의 threshold와 판정 의미는 profile에 따라 낮아지거나 높아지지
않습니다. 프로젝트가 개별 엔진을 `enabled = false`로 명시하면 해당 profile에서도 그 엔진은
제외됩니다. `test`/`sanitize`처럼 build session을 소유하는 선택 엔진은 서로
겹치지 않도록 직렬화되고, 나머지 read-only 엔진은 내부적으로 최대 4개까지만 병렬 실행됩니다.
이 제한은 결과의 재현성을 위해 두며 사용자가 worker 수를 조정할 필요는 없습니다.

선택된 profile은 `ici doctor`의 요약/JSON과 verify JSON의 `analysis_context.profile`에
표시됩니다. 이 JSON field는 optional이므로 profile이 없던 기존 `ici.result/v3` archive도
그대로 읽을 수 있습니다.

### 2.1 로컬 전체 검증
현재 프로젝트 디렉토리에서 13종 핵심 품질 검증 (기본 12종 활성)을 일괄 수행하고 터미널 컬러 대시보드를 출력합니다.

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
- engine의 `artifact_manifests`: `ici.artifacts/v1` — project/shadow root와 각 artifact의
  상대 경로 및 전체 provenance를 기록합니다.

외부 include/search path처럼 호스트 절대 경로가 섞일 수 있는 값은 `analysis_context` JSON
projection에서 `-I[external]`로 치환됩니다. HTML의 로컬 editor-link용 absolute path와 기존
tool evidence는 각 리포터의 기존 redaction 계약을 그대로 따르며, 이 확장이 그 계약을
변경하지는 않습니다. 두 확장이 없는 기존 `ici.result/v3` archive도 계속 읽고 migration할
수 있습니다.

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

cap과 grouping은 console-only projection이다. `--report` JSON, HTML, Markdown 및 baseline의
원본 inventory·target·finding·delta occurrence는 상한과 무관하게 전체를 보존해야 한다.
duplicate는 같은 실행의 같은 clone group 안에서 같은 파일의 겹치는 line region만 표시상
병합한다. 인접하지만 겹치지 않는 region과 서로 다른 clone group은 병합하지 않으며, 병합 전
원본 occurrence와 fingerprint를 모두 유지한다. HTML `Issues` 탭도 native v3 finding inventory를
기반으로 전체 결과를 표시한다. 80-column 터미널에서도 표·링크·상세가 한 글자씩 세로로
깨지지 않도록 회귀 테스트로 고정했다.

현재 로컬 구현·테스트 기준은 `814679c` + `d80a027`이며 로컬 Python 3.10 전체 품질 게이트는
756/756 tests, focused console 테스트는 16개다. Ruff check/format,
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

### 2.5 C++ 빌드 경로는 두 가지다

`ici`는 **프로젝트 루트**의 빌드 디스크립터를 보고 경로를 고릅니다.

| 루트에 있는 것 | `build`와 `test`가 하는 일 |
|---|---|
| `CMakeLists.txt` | CMake로 configure·build하고 CTest로 테스트한다 |
| `*.pro` | qmake로 configure·build하고 `make check`로 테스트한다 |

세 엔진은 각자의 shadow 디렉터리를 씁니다 — `build/ici-<backend>-build`(계측 없음),
`build/ici-<backend>`(`--coverage`), `build/ici-<backend>-asan`(`-fsanitize`). 하나를
공유하면 엔진이 돌 때마다 상대의 오브젝트를 다른 플래그로 다시 빌드하게 됩니다.

**정적 링크를 쓰는 프로젝트는 주의가 필요합니다.** `-static`과 `-fsanitize=address`는
함께 쓸 수 없으므로, 정적 링크를 sanitizer 빌드에서는 끄도록 빌드 정의에 조건을 두어야
합니다. `viewer/CMakeLists.txt`가 그 예입니다.
| `Makefile`만 | 어댑터가 없어 거부한다 |
| 없음 | 모든 소스를 `g++`로 직접 컴파일·링크한다 |

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
  시스템이 moc를 돌리므로 그 전제가 사라집니다. 바이너리를 만드는 세 엔진
  (`build`·`test`·`sanitize`)이 모두 어댑터를 쓰므로, 빌드 시스템이 빌드한 전부가
  대상입니다. 디스크립터가 없는 g++ 경로에서는 세 엔진 모두 이 설정을 그대로 따릅니다.
- **`-std=c++17` 고정이 사라집니다.** C++ 표준을 프로젝트의 빌드 정의가 정합니다.
- **`Q_OBJECT` 클래스를 단위 테스트할 수 있습니다.** g++ 경로에서는 moc 실행 단계가
  없어 vtable 미해결로 링크에 실패합니다.

커버리지 계측 플래그(`--coverage`)는 어느 경로에서든 `ici`가 주입합니다. 프로젝트가
커버리지 빌드를 따로 선언할 필요는 없습니다. 어댑터는 프로젝트의 빌드 트리를 건드리지
않고 `build/ici-cmake` 또는 `build/ici-qmake`에 별도로 빌드합니다.

이 세 엔진은 같은 `AnalysisContext`의 project/capability snapshot을 읽고, adapter를
호출할 때 각각 release·coverage·sanitize variant를 명시합니다. configure/build/test 중
변하는 상태는 mutable `BuildSession`에만 남고, 성공한 파일은 이후 엔진이 수정할 수 없는
frozen `ArtifactManifest`로 발행됩니다. 이 구조로 coverage나 sanitizer 산출물이 release
shadow를 덮어쓰거나, 리포터가 결과를 표시하는 과정에서 분석 입력을 바꾸는 일을 막습니다.

**두 어댑터 모두 테스트 바이너리 하나를 1건으로 셉니다.** CTest는 원래 그렇고, qmake
경로는 `make check`가 실행한 명령을 기준으로 세어 같은 단위를 씁니다. QtTest 바이너리가
낸 함수 단위 결과는 버리지 않고, 실패했을 때 그 바이너리의 실패 메시지에 함수 이름과
사유로 붙습니다.

qmake 경로에서 `-xunitxml`을 신뢰하지 않는 이유가 있습니다. 그 인자는 **QtTest
바이너리에만** 의미가 있고, 실제 프로젝트는 QtTest와 자체 `main()` 테스트를 섞어
갖습니다. XML만 읽으면 QtTest가 아닌 테스트가 보고에서 조용히 사라집니다.

---

## 3. 개별 엔진 단독 실행

특정 검증 항목만 빠르게 진단하고 싶을 때 단독 명령어를 사용합니다:

| 명령어 | 설명 | 상세 레퍼런스 |
|---|---|---|
| `ici line` | 순수 코드/주석/공백 라인 수 및 500/1000줄 규칙 검사 | [Line 엔진 상세](engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 코드 스타일 정렬 검사 | [Lint 엔진 상세](engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 Branch/Function 커버리지, TEM 5.0 스코어링 | [Test 엔진 상세](engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | Mypy 정적 타입 및 AST 부분 폴백 (C++ 타입 검증은 명시적 SKIP) | [Type 엔진 상세](engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici complexity` | 함수별 Cyclomatic 복잡도 및 블록 중첩 깊이 분석 | [Complexity 엔진 상세](engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 및 Python 리소스 누수 검증 | [Sanitize 엔진 상세](engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | 도달 불능 코드 및 미사용 심볼 검출 | [Dead 엔진 상세](engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 최대 클론 블록 병합 기반 Copy-Paste 코드 중복률 산출 | [Dup 엔진 상세](engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 삼킴(`except: pass`) 및 소멸자 throw 차단 | [Exception 엔진 상세](engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |

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
