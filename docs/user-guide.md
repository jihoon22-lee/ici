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
╭───────────── ici 0.1.0 Environment Diagnostics ─────────────╮
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

---

## 3. 개별 엔진 단독 실행

특정 검증 항목만 빠르게 진단하고 싶을 때 단독 명령어를 사용합니다:

| 명령어 | 설명 | 상세 레퍼런스 |
|---|---|---|
| `ici line` | 순수 코드/주석/공백 라인 수 및 500/1000줄 규칙 검사 | [Line 엔진 상세](engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 코드 스타일 정렬 검사 | [Lint 엔진 상세](engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 Branch/Function 커버리지, TEM 5.0 스코어링 | [Test 엔진 상세](engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | Mypy 정적 타입 및 C++ strict 플래그 검사 | [Type 엔진 상세](engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici complexity` | 함수별 Cyclomatic 복잡도 및 블록 중첩 깊이 분석 | [Complexity 엔진 상세](engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 및 Python 리소스 누수 검증 | [Sanitize 엔진 상세](engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | 도달 불능 코드 및 미사용 심볼 검출 | [Dead 엔진 상세](engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 최대 클론 블록 병합 기반 Copy-Paste 코드 중복률 산출 | [Dup 엔진 상세](engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 삼킴(`except: pass`) 및 소멸자 throw 차단 | [Exception 엔진 상세](engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |

---

## 4. IDE 원클릭 파일/라인 점프

- **터미널 (OSC 8 Hyperlinks)**:
  - 검증 결과 터미널 테이블의 파일 경로(`src/pkg/core.py:45`)를 `Ctrl + Click`하면 VS Code / Cursor IDE에서 해당 라인으로 즉시 이동합니다.
- **HTML 리포트 (`verify_report.html`)**:
  - 파일 탐색기 트리, 복잡도 리더보드, 중복 코드 위치 태그의 링크를 클릭하면 `vscode://file/...` 스키마를 통해 에디터가 즉시 열립니다.

---

> **다음 단계**: [📏 검증 엔진 레퍼런스 (Engine Reference)](engine-reference.md)에서 각 엔진별 상세 수식과 `ici.toml` 설정법을 확인하세요.
