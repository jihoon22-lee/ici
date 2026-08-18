# ici — Integrated CI Engine

개발 환경(WSL/Linux)과 **사내 폐쇄망**(RHEL 8.10/CentOS, tcsh/bash), **GitHub Actions**에서 **완벽히 동일하게 동작하는** C++/Python CI/CD 통합 검증·빌드 엔진.  
단일 ZipApp 실행 파일(`ici.pyz`, 1.9MB) 하나로 배포됩니다.

```bash
$ ici verify --report --html verify_report.html --open
$ ici doctor
```

---

## 📚 문서 허브 (Documentation Hub)

| 문서 | 설명 | 바로가기 |
|---|---|---|
| **🚀 사용자 가이드** | 빠른 시작, 설치, 전체 CLI 사용법 및 IDE 원클릭 점프 | [docs/user-guide.md](docs/user-guide.md) |
| **📏 검증 엔진 레퍼런스** | 9대 품질 검증 엔진, TEM 스코어링 공식, `ici.toml` 정책 설정 | [docs/engine-reference.md](docs/engine-reference.md) |
| **⚙️ CI/CD 연동 가이드** | GitHub Actions, Step Summary, PR 어노테이션, 사내 폐쇄망 러너 | [docs/ci-integration.md](docs/ci-integration.md) |
| **🏛️ 시스템 아키텍처** | ZipApp 패키징, Polyglot 런처, 오케스트레이터 및 리포터 계층 설계 | [docs/architecture.md](docs/architecture.md) |
| **📋 변경 이력 (CHANGELOG)** | 버전별 상세 릴리스 노트 및 마일스톤 | [CHANGELOG.md](CHANGELOG.md) |
| **📜 개발 및 기여 규약** | 브랜칭 전략, 커밋 룰, 런타임 제약 및 불변식 | [AGENTS.md](AGENTS.md) |

---

## 🚀 핵심 특징

1. **단일 ZipApp 배포 (`ici.pyz`)**:
   - 가상환경 설치나 `pip` 없이 실행 파일 하나만 복사(`~/.local/bin/ici` 또는 `nas_shared/bin/ici`)하면 끝.
2. **스마트 런처 (Smart Polyglot)**:
   - 시스템 기본 `python3`가 3.6/3.8인 구버전 환경에서도 `ICI_PYTHON` 또는 3.10+ 설치 경로를 스스로 찾아 실행.
3. **9대 핵심 품질 검증 엔진**:
   - `line`: 파일당 순수 코드 500줄 초과 경고, 1000줄 초과 실패 + **계층형 디렉토리 트리 뷰**
   - `lint`: 문법 검사 + 코드 스타일/포맷팅 정렬 검증 (`ruff`, `g++`, `clang-format`)
    - `test` & `tem`: 단위 테스트 전수 통과 + Branch/Function 커버리지 기반 **TEM 5.0 스코어링** (모듈별 실측 커버리지: Python `coverage.py` / C++ `gcov`)
   - `type`: Mypy 및 C++ strict 타입 안전성 검사 (0-Noise 요약 지원)
   - `complexity`: 함수별 순환 복잡도(Cyclomatic) 및 중첩 깊이 분석 + **원본 소스 코드 블록 프리뷰**
   - `sanitize`: C++ AddressSanitizer/UBSan 메모리 안전성 및 Python 리소스 누수 검증
   - `dead`: 죽은 코드, 도달 불가능 코드, 미사용 심볼 검출
   - `dup`: **연속 중복 블록 병합(Maximal Clone Merging)** 및 원본 인덴트 보존 중복률 산출
   - `exception`: 예외 삼킴(`except: pass`), Traceback 유실, 소멸자 throw 차단
4. **6개 전용 탭 인터랙티브 Zero-CDN HTML 대시보드 (`--html`)**:
   - `📋 Verification Suites`: 종합 품질 게이지, TEM 스코어, 9대 엔진 상태 및 전용 탭 점프 버튼
   - `📊 Line Analysis & Explorer`: 전체 너비(Full-Width) 계층형 파일 트리 탐색기 + 실시간 파일 검색 + 코드 분포 차트
    - `🧪 Tests & Coverage`: 4대 커버리지 KPI 게이지 + **모듈별 실측 커버리지 테이블 (Module Coverage Table)** + 파일별 테스트 스위트 & 개별 테스트 케이스 상세 뷰
   - `🧩 Complexity`: 순환 복잡도 리더보드 + **접고 펼칠 수 있는 소스 코드 블록 (Toggle All Code 지원)**
   - `📦 Clone Groups`: 연결 컴포넌트 클러스터링 기반 중복 코드 카드 + 원본 들여쓰기 보존 코드 블록
   - `⚠️ Issues`: 전체 조치 필요(WARN/FAIL) 항목 통합 뷰 + **접고 펼칠 수 있는 문제 코드 스니펫**
5. **전체 파일·라인 원클릭 점프 네비게이션**:
   - **로컬 터미널**: 터미널 OSC 8 하이퍼링크로 `Ctrl+Click` 시 VS Code / Cursor IDE로 즉시 이동
    - **GitHub Actions**: `$GITHUB_STEP_SUMMARY` 및 Sticky PR 코멘트의 GitHub Permalinks + 인라인 에러 어노테이션
    - **`--publish`**: 인터랙티브 HTML 리포트를 `gh-pages`에 자동 배포하고 PR 스티키 댓글에 원클릭 뷰어 링크 제공 (self/hub 모드)
   - **단일 HTML 리포터**: `vscode://file/...` 링크 연동

---

## 💻 빠른 설치 및 사용법

### 1. 단일 파일 실행
```bash
# 산출물 복사 및 실행 권한 부여
mkdir -p ~/.local/bin
cp dist/ici.pyz ~/.local/bin/ici && chmod +x ~/.local/bin/ici
export PATH="$HOME/.local/bin:$PATH"

# 환경 진단
ici doctor

# 전체 검증 실행
ici verify --report --html verify_report.html --open
```

### 2. 소스에서 빌드
```bash
./scripts/build-pyz.sh    # dist/ici.pyz 생성
./scripts/smoke.sh        # 격리 환경 스모크 테스트
```

---

## 📋 명령어 일람

| 명령어 | 설명 | 상세 가이드 |
|---|---|---|
| `ici verify` | 9대 검증 엔진 일괄 실행 및 종합 대시보드 출력 (`--report`, `--html`, `--github-summary`, `--publish`) | [사용자 가이드](docs/user-guide.md#2-검증-실행-ici-verify) |
| `ici line` | 코드/주석/공백 분석 및 500/1000 라인 과대화 검증 | [엔진 레퍼런스](docs/engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 스타일/포맷팅 검증 | [엔진 레퍼런스](docs/engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 커버리지/TEM 스코어 산출 | [엔진 레퍼런스](docs/engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | 정적 타입 검사 | [엔진 레퍼런스](docs/engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici complexity` | 순환 복잡도 및 중첩 깊이 분석 | [엔진 레퍼런스](docs/engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 검증 | [엔진 레퍼런스](docs/engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | 죽은 코드 및 미사용 심볼 검출 | [엔진 레퍼런스](docs/engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 중복 코드 / Copy-Paste 감지 | [엔진 레퍼런스](docs/engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 처리 안전성 검출 | [엔진 레퍼런스](docs/engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |
| `ici build` | 아티팩트 컴파일, 패키징 및 `env.sh`/`env.csh` 생성 | [사용자 가이드](docs/user-guide.md) |
| `ici doctor` | 시스템/툴체인/파이썬 환경 종합 진단 | [사용자 가이드](docs/user-guide.md#12-실행-환경-진단-ici-doctor) |
| `ici env` | 셸 환경 설정 스니펫 생성 (`--sh` / `--csh`) | [사용자 가이드](docs/user-guide.md) |
