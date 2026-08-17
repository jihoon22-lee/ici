# CHANGELOG

## [0.1.0] - 2026-08-17
### Added
- `ici` (Integrated CI) 초기 아키텍처 및 단일 실행형 ZipApp(`ici.pyz`) 패키징 시스템 구축
- 9대 핵심 품질 검증 엔진 구현:
  - `line`: 파일당 500줄 초과 WARN, 1000줄 초과 FAIL 규칙
  - `lint`: `ruff check` + `ruff format --check` 및 `g++` / `clang-format` 린팅
  - `test`: 단위 테스트 전수 통과 + Branch/Function 커버리지 기반 TEM 5.0 스코어링 공식
  - `type`: Mypy 및 C++ strict 타입 안전성 검사
  - `complexity`: 함수별 Cyclomatic 복잡도 및 중첩 깊이 검사
  - `sanitize`: C++ AddressSanitizer/UBSan 및 Python 리소스 누수 검증
  - `dead`: 죽은 코드 및 미사용 심볼 검출
  - `dup`: 6줄 이상 중복 코드(Copy-Paste) 감지
  - `exception`: 예외 삼킴(`except: pass`) 및 소멸자 throw 차단
- Coverity 및 SAM 사내 전용 도구 연동 인터페이스(`cov`, `sam`)
- `build`: Python Bytecode 컴파일, C++17 컴파일, `env.sh` 및 `env.csh` 생성기
- `doctor`: 시스템/툴체인/파이썬 환경 종합 진단기
- `env`: 셸 환경 스니펫 생성기
- 4대 다중 리포터 계층:
  - `RichConsoleReporter`: 터미널 컬러 대시보드 + OSC 8 IDE 원클릭 점프 링크
  - `MarkdownReporter`: GitHub `$GITHUB_STEP_SUMMARY` + Sticky PR 코멘트 + 인라인 오류 어노테이션
  - `HtmlReporter`: 폐쇄망 Zero-CDN 단일 독립 HTML 뷰어 및 인터랙티브 소스 코드 인스펙터
  - `JsonReporter`: `verify_report.json` 저장기
- 스마트 런처 `scripts/launcher.sh` 및 재현 빌드 파이프라인 `scripts/build-pyz.sh`
