# AGENTS.md — ici 개발 규약

이 문서는 `ici` 프로젝트에 기여하는 사람 및 LLM 에이전트가 **반드시 지켜야 하는 불변식(Invariants)**을 정의한다.

---

## 1. 커밋 규약 및 문서화 불변식 (Strict Rules)

- **작업 단위별 즉각 커밋 의무**: 의미 있는 기능 구현, 버그 수정, 리팩토링, UI 개선 단위 작업이 완료될 때마다 즉시 Git 커밋을 수행해야 한다.
- **Conventional Commits 준수**:
  - `feat:` 새로운 검증 엔진, 리포터, CLI 옵션, 핵심 기능 추가
  - `fix:` 버그 수정, 린트/타입 에러 수정, 예외 처리 개선
  - `refactor:` 복잡도 감소, 모듈 분리, 클론 코드 제거 등 구조 개선 (동작 변경 없음)
  - `docs:` 문서화, `CHANGELOG.md`, `README.md`, 가이드 업데이트
  - `test:` 테스트 케이스 추가 및 검증 로직 보강
  - `chore:` 빌드 스크립트, 패키징 설정, 패키지 메타데이터 변경
- **CHANGELOG 및 문서 동기화 의무**:
  - 기능 변경, UI/UX 개선, 정책 추가, 버전 변경이 일어날 때마다 **반드시 [`CHANGELOG.md`](file:///mnt/e/projects/ici/CHANGELOG.md)에 상세 변경 내역을 기록**하고 필요한 경우 `README.md`도 즉시 동기화한다.

---

## 2. 런타임 제약

- **Python 3.10 하한**: 개발은 최신 Python에서 하더라도 산출물은 Python 3.10에서 동작해야 한다.
  - 3.11+ 문법(예: `tomllib`, `ExceptionGroup`, `match-case` 등 3.10 지원 여부 확인) 사용 금지.
  - TOML 파싱은 `tomli` / `tomli-w` 사용 (`tomllib` 사용 금지 — ruff TID251로 강제).
- **순수 파이썬 의존성만 허용**: 네이티브 확장(`*.so`, `*.pyd`, `*.dylib`)이 포함된 패키지 추가 금지.
  - 모든 의존성 휠은 `py3-none-any` 태그여야 한다 (`build-pyz.sh`에서 기계적으로 검사).
- **시스템 CA 및 stdlib 사용**: `requests`, `httpx`, `certifi` 등 사내 TLS 인터셉션을 깨뜨리는 라이브러리 사용 금지.

---

## 3. 패키징 및 단일 파일 배포

- **산출물**: `dist/ici.pyz` 단일 파일로 빌드된다.
- **Polyglot 런처**: `scripts/launcher.sh` 프리앰블이 shebang 자리에 붙어, 시스템 내 `ICI_PYTHON` 또는 3.10+ 인터프리터를 스스로 탐색하여 실행한다.
- **불변식 동기화**: `scripts/launcher.sh`의 후보 목록과 `src/ici/core/env.py`의 `PYTHON_CANDIDATES`는 순서까지 일치해야 한다 (`tests/test_launcher.py`로 강제).
- **재현성 (Reproducible Builds)**: `build-pyz.sh`는 타임스탬프와 빌드 흔적(`direct_url.json`, `uv_cache.json`, `RECORD`)을 정규화하여 동일 소스에서 항상 동일한 체크섬을 생성한다.

---

## 4. 코드 설계 원칙

1. **위치 추적 필수**: 모든 검증 엔진은 PASS/FAIL 여부와 무관하게 검사된 모든 대상의 파일 경로와 라인 번호(`InspectionTarget`)를 반환해야 한다.
2. **다중 리포터 분리**: 검증 엔진 로직은 출력(터미널/Markdown/HTML/JSON)과 분리되어야 하며, `EngineResult` 객체를 생성하여 리포터 계층에 전달한다.
3. **루트 권한 배제**: 어떤 명령어도 `sudo`나 루트 권한을 요구하지 않아야 한다.
4. **Zero-CDN HTML**: HTML 리포터는 외부 네트워크 연결 없이 100% 로컬 인라인 CSS/JS/SVG로 동작해야 한다.
5. **노이즈 최소화**: 통과(PASS)된 수많은 정상 항목이 리포트를 도배하지 않도록 스마트 그룹핑 및 이슈 중심(Issues-First) 뷰를 기본으로 한다.

---

## 5. 테스트 및 품질 게이트

```bash
uv run --python 3.10 pytest
uvx ruff check .
uvx ruff format --check .
./scripts/build-pyz.sh
./scripts/smoke.sh
```
