# AGENTS.md — ici 개발 규약

이 문서는 `ici` 프로젝트에 기여하는 사람 및 LLM 에이전트가 **반드시 지켜야 하는 불변식(Invariants)**을 정의한다.

---

## 1. 브랜치 및 GitHub PR 기반 개발 전략 (Branching & PR Workflow)

- **`main` 브랜치 직접 푸시/작업 금지**: 모든 변경 사항은 반드시 목적에 맞는 별도 브랜치에서 작업한 후, **GitHub Pull Request(PR)**를 통해 `main`에 병합(Merge)해야 한다.
- **브랜치 네이밍 규칙**:
  - `feat/<feature-name>`: 새로운 엔진, 리포터, CLI 옵션, 핵심 기능 개발
  - `fix/<issue-name>`: 버그 수정, 린트/타입 오류 해결, 예외 처리 개선
  - `refactor/<target>`: 기능 변경 없는 구조 개선, 복잡도 분리, 모듈화
  - `docs/<doc-name>`: 문서 추가, `CHANGELOG.md` 및 가이드 작성
  - `test/<test-name>`: 테스트 케이스 추가 및 검증 로직 보강
  - `chore/<task-name>`: 빌드 스크립트, 의존성, CI/CD 설정 변경
- `I4-3`, `T0`, `B1`, `D2` 같은 roadmap 코드를 PR 제목/요약의 primary 또는 sole 내용으로
  삼지 않는다. 제목은 사용자에게 보이는 결과나 기술적 결과를 설명해야 하며, roadmap key는
  필요할 때만 body의 mapping 항목으로 덧붙인다.
- **GitHub PR 병합 절차 (Standard GitHub PR Workflow)**:
  1. 작업 브랜치 생성: `git checkout -b <type>/<description>`
  2. 코드 구현 및 품질 게이트 검증 (`pytest`, `ruff`, `./scripts/build-pyz.sh`)
  3. Conventional Commit 메시지로 작업 브랜치에 커밋: `git commit -m "<type>(<scope>): <summary>"`
  4. GitHub 원격 저장소로 브랜치 푸시: `git push -u origin <type>/<description>`
  5. GitHub CLI로 PR 생성: `gh pr create --title "<type>(<scope>): <summary>" --body "..."`
  6. PR 머지 수행: `gh pr merge --squash --delete-branch`
  7. 로컬 `main` 브랜치 동기화: `git checkout main && git pull origin main`

---

## 2. 커밋 규약 및 문서화 불변식 (Strict Rules)

- **작업 단위별 즉각 커밋 의무**: 의미 있는 기능 구현, 버그 수정, 리팩토링, UI 개선 단위 작업이 완료될 때마다 즉시 Git 커밋을 수행해야 한다.
- **Conventional Commits 준수**:
  - `feat:` 새로운 검증 엔진, 리포터, CLI 옵션, 핵심 기능 추가
  - `fix:` 버그 수정, 린트/타입 에러 수정, 예외 처리 개선
  - `refactor:` 복잡도 감소, 모듈 분리, 클론 코드 제거 등 구조 개선 (동작 변경 없음)
  - `docs:` 문서화, `CHANGELOG.md`, `README.md`, 가이드 업데이트
  - `test:` 테스트 케이스 추가 및 검증 로직 보강
  - `chore:` 빌드 스크립트, 패키징 설정, 패키지 메타데이터 변경
- **CHANGELOG 및 문서 동기화 의무**:
  - 기능 변경, UI/UX 개선, 정책 추가, 버전 변경이 일어날 때마다 **반드시 [`CHANGELOG.md`](CHANGELOG.md)에 상세 변경 내역을 기록**하고 필요한 경우 `README.md`도 즉시 동기화한다.

---

## 3. 런타임 제약

- **Python 3.10 하한**: 개발은 최신 Python에서 하더라도 산출물은 Python 3.10에서 동작해야 한다.
  - 3.11+ 문법(예: `tomllib`, `ExceptionGroup`, `match-case` 등 3.10 지원 여부 확인) 사용 금지.
  - TOML 파싱은 `tomli` / `tomli-w` 사용 (`tomllib` 사용 금지 — ruff TID251로 강제).
- **순수 파이썬 의존성만 허용**: 네이티브 확장(`*.so`, `*.pyd`, `*.dylib`)이 포함된 패키지 추가 금지.
  - 모든 의존성 휠은 `py3-none-any` 태그여야 한다 (`build-pyz.sh`에서 기계적으로 검사).
- **시스템 CA 및 stdlib 사용**: `requests`, `httpx`, `certifi` 등 사내 TLS 인터셉션을 깨뜨리는 라이브러리 사용 금지.

---

## 4. 패키징 및 단일 파일 배포

- **산출물**: `dist/ici.pyz` 단일 파일로 빌드된다.
- **Polyglot 런처**: `scripts/launcher.sh` 프리앰블이 shebang 자리에 붙어, 시스템 내 `ICI_PYTHON` 또는 3.10+ 인터프리터를 스스로 탐색하여 실행한다.
- **불변식 동기화**: `scripts/launcher.sh`의 후보 목록과 `src/ici/core/env.py`의 `PYTHON_CANDIDATES`는 순서까지 일치해야 한다 (`tests/test_launcher.py`로 강제).
- **재현성 (Reproducible Builds)**: `build-pyz.sh`는 타임스탬프와 빌드 흔적(`direct_url.json`, `uv_cache.json`, `RECORD`)을 정규화하여 동일 소스에서 항상 동일한 체크섬을 생성한다.

---

## 5. 코드 설계 원칙

1. **위치 추적 필수**: 모든 검증 엔진은 PASS/FAIL 여부와 무관하게 검사된 모든 대상의 파일 경로와 라인 번호(`InspectionTarget`)를 반환해야 한다.
2. **다중 리포터 분리**: 검증 엔진 로직은 출력(터미널/Markdown/HTML/JSON)과 분리되어야 하며, `EngineResult` 객체를 생성하여 리포터 계층에 전달한다.
3. **루트 권한 배제**: 어떤 명령어도 `sudo`나 루트 권한을 요구하지 않아야 한다.
4. **Zero-CDN HTML**: HTML 리포터는 외부 네트워크 연결 없이 100% 로컬 인라인 CSS/JS/SVG로 동작해야 한다.
5. **노이즈 최소화**: 통과(PASS)된 수많은 정상 항목이 리포트를 도배하지 않도록 스마트 그룹핑 및 이슈 중심(Issues-First) 뷰를 기본으로 한다.

---

## 6. 테스트 및 품질 게이트

```bash
uv run --python 3.10 pytest
uvx ruff check .
uvx ruff format --check .
./scripts/build-pyz.sh
./scripts/smoke.sh
```

---

## 7. 릴리스 버전 및 cadence 불변식

- `feature`, `test`, `refactor`, `docs` PR은 버전을 자동으로 올리거나 stable release를 자동으로
  만들지 않는다. PR 병합과 릴리스 결정은 별개다.
- `patch` 버전은 이미 공개된 stable artifact의 defect, security, compatibility 수정에만 사용한다.
- `minor` 버전은 사용자에게 보이는 하나의 응집된 roadmap checkpoint에만 사용하며, ici 전체
  gate, 실제 도구 E2E, candidate의 cross-repo/toy 검증, PR/main CI·Pages, 문서·CHANGELOG
  동기화가 모두 끝난 뒤에만 결정한다.
- pre-release와 candidate artifact는 stable release가 아니며 stable artifact의 근거로 사용하지 않는다.
- 하나의 PR이 하나의 릴리스를 의미하지 않는다. 여러 PR을 하나의 릴리스로 묶을 수 있고,
  릴리스가 필요 없는 PR은 별도 릴리스 없이 병합할 수 있다.
