# ici — Integrated CI Engine

개발 환경(WSL/Linux)과 **폐쇄망**(RHEL 8.10, tcsh), **GitHub Actions**에서 **동일하게 동작하는** C++/Python CI/CD 통합 검증·빌드 엔진.  
단일 실행 파일(`ici.pyz`) 하나로 배포된다.

```bash
$ ici verify
$ ici doctor
```

---

## 핵심 특징

1. **단일 ZipApp 배포 (`ici.pyz`)**:
   - 가상환경 설치나 `pip` 없이 실행 파일 하나만 복사(`~/.local/bin/ici` 또는 `nas_shared/bin/ici`)하면 끝.
2. **스마트 런처 (Smart Polyglot)**:
   - 시스템 기본 `python3`가 3.6/3.8인 구버전 환경에서도 `ICI_PYTHON` 또는 3.10+ 설치 경로를 스스로 찾아 실행.
3. **9대 핵심 품질 검증 엔진**:
   - `line`: 파일당 순수 코드 500줄 초과 경고, 1000줄 초과 실패
   - `lint`: 문법 검사 + 코드 스타일/포맷팅 정렬 검증
   - `test` & `tem`: 단위 테스트 전수 통과 + Branch/Function 커버리지 기반 TEM 5.0 스코어링
   - `type`: Mypy 및 C++ strict 타입 안전성 검사
   - `complexity`: 함수별 순환 복잡도(Cyclomatic) 및 중첩 깊이 분석
   - `sanitize`: C++ AddressSanitizer/UBSan 메모리 안전성 및 Python 리소스 누수 검증
   - `dead`: 죽은 코드, 도달 불가능 코드, 미사용 심볼 검출
   - `dup`: 6줄 이상 중복 코드(Copy-Paste) 감지 및 중복률 산출
   - `exception`: 예외 삼킴(`except: pass`), Traceback 유실, 소멸자 throw 차단
4. **전체 파일·라인 원클릭 점프 네비게이션**:
   - **로컬 터미널**: 터미널 OSC 8 하이퍼링크로 `Ctrl+Click` 시 VS Code / Cursor IDE로 즉시 이동
   - **GitHub Actions**: `$GITHUB_STEP_SUMMARY` 및 Sticky PR 코멘트의 GitHub Permalinks + 인라인 에러 어노테이션
   - **단일 HTML 리포터 (`--html`)**: 폐쇄망 Zero-CDN 기반의 인터랙티브 소스 코드 인스펙터

---

## 설치 및 사용법

### 1. 단일 파일 실행
```bash
# 산출물 복사 및 실행 권한 부여
cp dist/ici.pyz ~/.local/bin/ici && chmod +x ~/.local/bin/ici
export PATH="$HOME/.local/bin:$PATH"

# 환경 진단
ici doctor

# 전체 검증 실행
ici verify
ici verify --html verify_report.html --open
```

### 2. 소스에서 빌드
```bash
./scripts/build-pyz.sh    # dist/ici.pyz 생성
./scripts/smoke.sh        # 격리 환경 스모크 테스트
```

---

## 명령어 일람

| 명령어 | 설명 |
|---|---|
| `ici verify` | 9대 검증 엔진 일괄 실행 및 종합 대시보드 출력 (`--report`, `--html`, `--github-summary`) |
| `ici line` | 코드/주석/공백 분석 및 500/1000 라인 과대화 검증 |
| `ici lint` | 문법 린팅 및 스타일/포맷팅 검증 |
| `ici test` | 단위 테스트 실행 및 커버리지/TEM 스코어 산출 |
| `ici type` | 정적 타입 검사 |
| `ici complexity` | 순환 복잡도 및 중첩 깊이 분석 |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 검증 |
| `ici dead` | 죽은 코드 및 미사용 심볼 검출 |
| `ici dup` | 중복 코드 / Copy-Paste 감지 |
| `ici exception` | 예외 처리 안전성 검출 |
| `ici cov` | Coverity 확장 인터페이스 |
| `ici sam` | SAM 보안 스캐너 확장 인터페이스 |
| `ici build` | 아티팩트 컴파일, 패키징 및 `env.sh`/`env.csh` 생성 |
| `ici doctor` | 시스템/툴체인/파이썬 환경 종합 진단 |
| `ici env` | 셸 환경 설정 스니펫 생성 (`--sh` / `--csh`) |
