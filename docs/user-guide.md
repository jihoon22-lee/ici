# ici (Integrated CI) 사용자 가이드

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; **🚀 사용자 가이드** &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; [⚙️ CI/CD 연동 가이드](ci-integration.md) &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬 개발 환경(WSL/Linux), 사내 폐쇄망(RHEL 8.10/CentOS, tcsh/bash), 그리고 GitHub Actions CI/CD 파이프라인에서 **완벽히 동일한 검증 결과와 리포트**를 제공하는 단일 실행형 CI 통합 엔진입니다.

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
현재 시스템의 OS, glibc 버전, 툴체인(gcc/g++, cmake, make, ruff, mypy, pytest) 및 Python 런타임을 점검합니다.

```bash
ici doctor
```

```text
╭───────────── ici 0.3.3 Environment Diagnostics ─────────────╮
│ Category   Item            Status   Details                  │
├──────────────────────────────────────────────────────────────┤
│ OS         Platform        OK       Linux (ubuntu-26.04)     │
│ OS         WSL             INFO     WSL Environment Detected │
│ OS         glibc           OK       2.43                     │
│ Python     Candidate       OK       Python 3.10+ Available   │
│ Toolchain  g++             OK       15.2.0                   │
│ Toolchain  ruff            OK       0.16.3                   │
│ Toolchain  pytest          OK       9.0.2                    │
╰──────────────────────────────────────────────────────────────╯
```

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

### 2.1 로컬 전체 검증
현재 프로젝트 디렉토리에서 9대 핵심 품질 검증을 일괄 수행하고 터미널 컬러 대시보드를 출력합니다.

```bash
ici verify
```

### 2.2 인터랙티브 HTML 리포트 생성 및 자동 브라우저 열기
```bash
ici verify --report --html verify_report.html --open
```
- `--html <path>`: Zero-CDN 기반의 독립형 인터랙티브 HTML 리포트를 생성합니다.
- `--open`: 검증 완료 후 기본 브라우저(`firefox`, `chrome`, `xdg-open` 등)로 리포트를 즉시 띄웁니다.
- `--json <path>`: 파이프라인 데이터 연동용 `verify_report.json`을 저장합니다.

### 2.3 GitHub Actions에서 HTML 리포트 배포 (`--publish`)
CI에서 `--publish`를 붙이면 `verify_report.html`이 `gh-pages` 브랜치로 자동 배포되고,
PR에 원클릭 뷰어 링크가 담긴 스티키 댓글이 달립니다 (자세한 설정은
[CI/CD 연동 가이드](ci-integration.md#23-html-리포트-배포-및-sticky-pr-코멘트---publish) 참조):

```bash
dist/ici.pyz verify --report --html verify_report.html --github-summary --publish
```

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
않습니다. C++는 root에 CMake/qmake/Makefile descriptor가 있으면 generic `g++`를
호출하지 않고 adapter 필요 `ERROR`를 반환합니다. descriptor가 없는 경우에도
`int main(...)` 정의가 정확히 하나인 단순 executable만 허용하며, g++ timeout·절단·
signal·spawn 오류와 rc 0인데 regular binary가 없는 경우는 `ERROR`입니다.

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

`ici sanitize`, `ici dead`, `ici exception` 단독 명령은 `FAIL` 또는 실행 `ERROR`일 때
종료 코드 `1`, 검증을 수행하지 못한 `SKIP`일 때 종료 코드 `2`를 반환합니다. 다른 단독
엔진의 종료 코드 정책은 기존 동작을 따릅니다.

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
