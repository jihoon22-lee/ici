# AGENTS.md — ici 개발 규약

이 문서는 `ici` 프로젝트에 기여하는 사람 및 LLM 에이전트가 **반드시 지켜야 하는 불변식(Invariants)**을 정의한다.

## 적용 범위 — 두 경로

ici는 [ici-next 전환](docs/design/ici-next/roadmap.md) 중이다. 이 문서의 불변식은 두 경로로
나뉘며, **어느 경로에 있는지 확인한 뒤 해당 규약을 적용한다.**

|경로|의미|적용 규약|
|---|---|---|
|**stable 경로**|현재 릴리스되는 `dist/ici.pyz`와 그 CI·릴리스 체계. v0.11.x 계열|§1, §2, **§3, §4**, §5, §6, §7|
|**next 경로**|`docs/design/ici-next/`가 정의하는 새 배포·실행 구조. 전용 런타임 bundle|§1, §2, §5, §6, §7, **§8**|

경로를 판별하는 기준:

- 기존 `src/ici/` 트리와 `dist/ici.pyz` 산출물을 바꾸는 작업은 **stable 경로**다.
- ici-next WP 이슈([#198](https://github.com/jihoon22-lee/ici/issues/198)~[#227](https://github.com/jihoon22-lee/ici/issues/227))가
  정의한 새 구조·bundle·계약을 만드는 작업은 **next 경로**다.
- 애매하면 stable 경로로 취급한다. 즉 더 엄격한 §3·§4를 적용한다.

**전환 중에도 stable 경로의 테스트·CI·스크립트를 먼저 삭제하거나 완화하지 않는다.**
새 설계를 주장하기 위해 기존 게이트를 지우는 순서는 금지한다
([SPEC-05 §5](docs/design/ici-next/spec-05-verification-transition.md)).

경로와 무관하게 **항상 적용되는** 제약: §1 브랜치·PR 워크플로, §2 커밋·문서 규약,
§5 코드 설계 원칙(위치 추적·리포터 분리·root 배제·Zero-CDN·노이즈 최소화),
§7 릴리스 cadence, §3의 시스템 CA 조항.

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

## 3. 런타임 제약 — **stable 경로 전용** (시스템 CA 조항은 전 경로)

> 아래 Python 3.10 하한과 순수 wheel 제약은 **`dist/ici.pyz`가 시스템 인터프리터에서 실행된다는
> 전제**에서 나온다. next 경로는 전용 런타임을 동봉하므로 그 전제가 성립하지 않는다 →
> §8 및 [ADR-0002](docs/design/ici-next/adr/0002-standalone-runtime-bundle.md).
> **stable 경로에서는 아래 제약이 그대로 유효하며 완화 대상이 아니다.**

- **Python 3.10 하한** *(stable 경로)*: 개발은 최신 Python에서 하더라도 산출물은 Python 3.10에서 동작해야 한다.
  - 3.11+ 문법(예: `tomllib`, `ExceptionGroup`, `match-case` 등 3.10 지원 여부 확인) 사용 금지.
  - TOML 파싱은 `tomli` / `tomli-w` 사용 (`tomllib` 사용 금지 — ruff TID251로 강제).
  - `pyproject.toml`의 `requires-python = ">=3.10"`과 ruff `target-version = "py310"`이 이를 강제한다.
    **이 두 값을 next 경로 작업 때문에 변경하지 않는다.**
- **순수 파이썬 의존성만 허용** *(stable 경로)*: 네이티브 확장(`*.so`, `*.pyd`, `*.dylib`)이 포함된 패키지 추가 금지.
  - 모든 의존성 휠은 `py3-none-any` 태그여야 한다 (`build-pyz.sh`에서 기계적으로 검사).
- **시스템 CA 및 stdlib 사용** *(전 경로 적용)*: `requests`, `httpx`, `certifi` 등 사내 TLS 인터셉션을 깨뜨리는 라이브러리 사용 금지.
  - 이 조항은 폐쇄망 요구(R11)에서 직접 나오므로 next 경로에서도 유지된다.

---

## 4. 패키징 및 단일 파일 배포 — **stable 경로 전용**

> next 경로의 배포 계약은 §8과
> [SPEC-02 §1](docs/design/ici-next/spec-02-distribution-execution.md)에 있다.
> 아래 불변식은 pyz 배포물에 적용되며, **전환 기간 내내 유지한다.**

- **산출물** *(stable 경로)*: `dist/ici.pyz` 단일 파일로 빌드된다.
- **Polyglot 런처** *(stable 경로)*: `scripts/launcher.sh` 프리앰블이 shebang 자리에 붙어, 시스템 내 `ICI_PYTHON` 또는 3.10+ 인터프리터를 스스로 탐색하여 실행한다.
- **불변식 동기화** *(stable 경로)*: `scripts/launcher.sh`의 후보 목록과 `src/ici/core/env.py`의 `PYTHON_CANDIDATES`는 순서까지 일치해야 한다 (`tests/test_launcher.py`로 강제).
  - next 경로는 시스템 인터프리터를 탐색하지 않으므로 이 동기화 요구가 적용되지 않는다.
    **다만 `tests/test_launcher.py`를 삭제하지 않는다.** pyz가 지원 배포물인 동안 계속 강제된다.
- **재현성 (Reproducible Builds)** *(전 경로 적용되는 원칙)*: `build-pyz.sh`는 타임스탬프와 빌드 흔적(`direct_url.json`, `uv_cache.json`, `RECORD`)을 정규화하여 동일 소스에서 항상 동일한 체크섬을 생성한다.
  - `build-pyz.sh`가 고정하는 정확한 `uv` 버전은 의도된 불변식이다. 버전이 다르면 빌드를 거부한다.
  - next 경로에서는 같은 원칙이 bundle `manifest.json`의 build input lock digest로 이어진다.

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

- 위 5개는 **stable 경로의 완전한 게이트**다. next 경로 작업도 앞의 3개(pytest, ruff check,
  ruff format)는 반드시 통과해야 한다. 뒤의 2개는 pyz 산출물을 바꾸는 작업에서 필수다.
- **실행하지 못한 게이트는 "통과"로 적지 않는다.** 도구 부재·버전 불일치로 실행할 수 없으면
  그 사실과 사유를 PR 근거에 남긴다
  ([SPEC-05 §2](docs/design/ici-next/spec-05-verification-transition.md)의 계층 구분).
- 도구 부재로 인한 skip은 조용해서는 안 된다. `ICI_REQUIRE_BUILD_ADAPTERS=1`은 도구 부재를
  skip 대신 failure로 만들며 ici 자신의 CI가 이를 설정한다.
- 기준선 측정 예시와 미실행 사유 기록 형식:
  [inventory/baseline-measurements.md](docs/design/ici-next/inventory/baseline-measurements.md).

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
- **toy/cross-repo 검증 게이트** *(전환 중 범위 조정)*: `minor` 릴리스 조건에 있는
  "candidate의 cross-repo/toy 검증"은 유지한다. 다만 ici-next는 **ici 자신이 소유한 회귀
  corpus로 릴리스를 판정하는 것**을 목표로 한다(R13). 전환 조건은 다음과 같다.
  - ici-owned corpus가 완성되기 전까지는 현행 toy 검증을 그대로 릴리스 근거로 쓴다.
  - ici-owned corpus가 [#201](https://github.com/jihoon22-lee/ici/issues/201)에서 완성되고
    [#227](https://github.com/jihoon22-lee/ici/issues/227)이 전환을 승인한 뒤에야
    toy 최신 `main`에 종속된 필수 게이트를 해제한다.
  - **toy-projects에 ici 전용 환경파일이나 특수 폴더 구조를 요구하지 않는다.**
  - toy 제품 검증 자체는 released/candidate ici의 명시적 소비자 검증으로 계속 유지할 수 있다.

---

## 8. ici-next 경로 규약

> next 경로에서만 적용된다. 규범 원문은 [`docs/design/ici-next/`](docs/design/ici-next/)이고
> 이 절은 그 요약이다. 충돌하면 해당 SPEC 문서가 우선한다.

- **런타임**: 전용 CPython을 동봉한 압축 디렉터리로 배포한다. §3의 Python 3.10 하한과 순수 wheel
  제약이 적용되지 않는 대신, 지원 런타임·도구 버전은 **실제 시험으로 고정하며 추정하지 않는다**
  ([ADR-0002](docs/design/ici-next/adr/0002-standalone-runtime-bundle.md)).
- **환경 분리**: 프로젝트 Python/GCC/Qt와 ici 분석 도구를 섞지 않는다. `sys.executable`은 ici
  core의 경로이며 **프로젝트 Python의 fallback이 아니다.** bundle 경로를 프로젝트 검색 경로 앞에
  전역 삽입하지 않는다.
- **셸 초기화 금지**: `devenv.csh` 등 초기화 파일을 탐색·해석·source하지 않는다. 특정 NAS·사내
  라이브러리 경로를 코드가 알지 않는다(R01).
- **폐쇄망**: `verify`/`init`/`doctor`/`plan`/`report` 실행 중 download·pip·uv 설치를 호출하지
  않는다(R11).
- **상태 축 분리**: 실행 완료·증거 수준·선택 범위·코드 위반·게시 상태를 독립적으로 표현한다.
  부분 실행 성공을 전체 게이트 성공으로 표시하지 않는다(R05, R10).
- **결과 스키마**: 신규 envelope는 `schema_id="ici.next.run"`으로 기존 finding v3와 식별자를
  구분한다. 기존 version 숫자를 재사용해 호환된 것처럼 보이게 하지 않는다.
- **분석 관점 보존**: 19개 기존 descriptor의 유지·통합·선택 제공·폐기는 근거와 이전 경로를
  남긴다. 새 구조로 옮기기 어렵다는 이유만으로 자체 기능을 삭제하지 않는다(R09).
- **측정 규율**: 성능 수치·지원 환경·정확도를 추정으로 확정하지 않는다. 미확인 항목은
  `planned`/`tested`/`supported`/`limited`/`unsupported`로 구분해 기록한다.
- **PR 규약**: WP 번호만 PR 제목으로 쓰지 않는다. 인수 기준·실제 명령·도구 버전·테스트 결과를
  근거로 남기고, 큰 WP는 본문의 PR 경계로 나눈다. 후속 PR이 남아 있으면 `Closes`로 조기 종료하지
  않는다.
