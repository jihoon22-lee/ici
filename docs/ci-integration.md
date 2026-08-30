# ici CI/CD 연동 가이드 (GitHub Actions & Self-Hosted Runner)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; **⚙️ CI/CD 연동 가이드** &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬, GitHub Actions, 사내 러너에서 같은 정책·결과 계약을 적용합니다. 결과가
항상 동일하다는 뜻은 아닙니다. OS, 컴파일러, Python, Ruff/Mypy/pytest 등의 가용성과
버전은 `doctor` 및 각 엔진의 `ToolEvidence`에 기록되고, 도구 체인이나 소스 환경이
다르면 실제 결과도 달라질 수 있습니다. 현재 버전은 실행별 시스템 정보와 현재 엔진이 실제로
확인·호출한 도구의 증거만 기록하며, 전체 툴체인 capability를 자동으로 판정하지 않습니다.

## 1. GitHub Actions 워크플로우

저장소의 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)은 PR과 `main` push에서
검증을 수행합니다. 검증과 리포트 배포는 권한이 서로 다른 job으로 분리되어 있습니다.

### 1.1 PR 및 main 검증 (`verify`)

`verify` job은 기본 권한과 job 권한 모두 `contents: read`만 사용합니다.
checkout은 `persist-credentials: false`로 설정되어 작업 디렉터리에 쓰기용 인증 정보를
남기지 않습니다. 이 job은 `GITHUB_TOKEN`, `--publish`, PR 댓글 API를 사용하지 않습니다.

검증 순서는 다음과 같습니다.

1. Ruff 포맷·린트
2. Python 3.10 pytest
3. `dist/ici.pyz` 빌드 및 독립 스모크 테스트
4. `dist/ici.pyz verify --report --html verify_report.html --github-summary`
5. 저장소 루트와 `viewer/`의 JSON/HTML 결과 아티팩트 업로드

별도 `viewer-gui` job은 Qt6 GUI를 빌드하고 실제 report를 headless로 엽니다. 마지막
`Merge Gate` job은 verify, viewer GUI, PR report 게시 결과를 모두 집계합니다. branch
protection은 개별 job 이름 대신 이 안정적인 최종 체크를 필수로 사용합니다.

PR에서 확인할 수 있는 결과는 다음과 같습니다.

- GitHub Step Summary: 엔진별 상태와 위치 링크
- GitHub Actions annotations: FAIL/ERROR/WARN/SKIP 위치 진단
- `ici-verification-report` 아티팩트: `verify_report.json`, `verify_report.html`
- **sticky PR 댓글** (`report-pr` job): HTML 리포트 링크 + 엔진 상세 (아래 1.3)

### 1.2 PR 리포트 sticky 댓글 (`report-pr`)

`report-pr`은 PR 이벤트에서만 실행되며, 검증 job이 업로드한 **아티팩트만** 소비합니다.
이 job은 `contents: write` + `pull-requests: write`를 가진 신뢰된 job이므로, 여기서
빌드해 실행하는 `dist/ici.pyz`는 반드시 신뢰된 코드에서 나와야 합니다 — 그래서 PR
head/merge ref가 아니라 **PR의 base commit**(`github.event.pull_request.base.sha`)을
체크아웃한 뒤 그 소스로 `ici.pyz`를 빌드합니다. PR이 실제로 검증된 내용
(`verify_report.html/json`)은 별도로 업로드된 아티팩트로만 받으므로, PR 변경분이 이 job의
권한 있는 동작을 유도할 수 없습니다.

```yaml
if: ${{ always() && github.event_name == 'pull_request' }}
needs: [verify, viewer-gui]
permissions:
  contents: write       # gh-pages 업로드 (Contents API)
  pull-requests: write  # sticky 댓글 작성/갱신
  pages: read           # Pages 활성화 여부 조회 (뷰어 링크 계산용)
```

동작은 다음과 같습니다.

1. PR의 base commit을 체크아웃하고 `dist/ici.pyz`를 빌드합니다(신뢰된 코드만 실행).
2. `actions/download-artifact`로 루트와 `viewer/`의 `verify_report.html/json`을 받습니다.
3. `dist/ici.pyz publish --report-dir ici=. --report-dir viewer`를 실행해
   gh-pages의 `ici/pr/<번호>/`와 `viewer/pr/<번호>/`에 리포트를 올립니다.
4. `<!-- ici-report -->` 마커로 기존 댓글을 찾아 **갱신(PATCH)** 하고, 없으면 생성합니다.
   댓글 검색은 페이지네이션(`per_page=100`, 최대 2000개)을 사용하므로 30개를 넘는 기존
   댓글 뒤에 있는 마커도 놓치지 않습니다.
5. 실제 댓글에서 두 HTML URL을 추출하고, Pages 비동기 배포가 완료돼 두 URL 모두 HTML을
   반환할 때까지 확인합니다. 이 단계까지 성공해야 `report-pr`이 통과합니다.

댓글 본문에는 프로젝트별 "HTML 리포트 열기" 링크, Pass/Warn/Fail/Error/Skip/TEM 통계 표,
접을 수 있는(`<details>`) 엔진별 상세 결과가 포함됩니다. 업로드·댓글·실제 HTML 확인 중
하나라도 실패하면 `ici publish` 또는 `report-pr`이 실패하고 `Merge Gate`가 병합을 막습니다.
Fork PR의 기본 `GITHUB_TOKEN`은 쓰기 권한이 없으므로 이 저장소의 내부 branch와 같은 게시
계약을 충족하지 못합니다. 외부 fork는 maintainer-owned branch로 옮겨 전체 gate를 다시
통과시키기 전에는 병합하지 않습니다.

### 1.3 신뢰된 main publish (`publish-main`)

`publish-main`은 아래 조건을 모두 만족할 때만 실행됩니다.

```yaml
if: github.event_name == 'push' && github.ref == 'refs/heads/main'
needs: [verify, viewer-gui]
permissions:
  contents: write
  pages: read       # Pages 활성화 여부 조회 (뷰어 링크 계산용)
```

이 job은 검증이 통과한 `main`에서 신뢰된 `ici.pyz`를 빌드하고, verify job의 기존
아티팩트를 내려받은 뒤 명시적으로 `GITHUB_TOKEN`을 주입하여 다음 명령을 실행합니다.

```bash
dist/ici.pyz publish --report-dir ici=. --report-dir viewer
```

`publish`는 Contents API를 사용하는 권한 있는 동작입니다. 일반 검증 job에 이 권한을
확장하지 않고, 신뢰된 게시 job에서만 사용하도록 한 것이 기본 정책입니다. 별도의
수동/신뢰된 workflow에서 이 기능을 사용할 때도 대상 저장소와 토큰 권한을 명시적으로
검토해야 합니다.

### 1.4 Action 버전 고정

CI와 release workflow의 외부 Action은 Node 24 기반 릴리스 라벨을 주석으로 남기되,
실제 `uses:` 값은 다음 40자리 커밋 SHA에 고정되어 있습니다.

| Action | 릴리스 | 커밋 SHA |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact` | v4.3.0 | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `astral-sh/setup-uv` | v10.0.1 | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d` |
| `softprops/action-gh-release` | v3.0.2 | `3d0d9888cb7fd7b750713d6e236d1fcb99157228` |

### 1.5 Release workflow

`.github/workflows/release.yml`은 `v*.*.*` 태그 push 또는 `workflow_dispatch`에서
두 job으로 릴리스를 수행합니다. 먼저 읽기 전용 `validate-release`가 태그를
commit으로 해석해 detached checkout한 뒤 다음을 모두 검증합니다.

- 태그 commit이 `origin/main`에서 도달 가능한다.
- 그 정확한 commit SHA의 `Merge Gate`가 성공했다. PR head의 같은 이름 check는
  squash merge SHA와 다르므로 대체 증거가 될 수 없다.
- `vX.Y.Z`, `src/ici/__init__.py`의 `__version__`, `CHANGELOG.md`의 release section이
  서로 일치한다.

검증된 SHA를 출력으로 받은 `build-release`만 `contents: write`를 가집니다. 그 job은
Ruff, Python 3.10 전체 테스트, ZipApp 빌드·스모크·SHA-256, ici self verify,
viewer C++/Qt verify, GUI CTest·headless smoke를 candidate에서 다시 실행합니다. GitHub
Release에는 `ici.pyz`, 체크섬, CLI/GUI viewer와 함께 self/viewer HTML·JSON 검증
리포트를 첨부합니다. 수동 실행의 `version_tag`는 필수이며 태그가 이미
존재해야 하므로, 선택한 workflow branch의 소스가 릴리스로 대체되지 않습니다.

## 2. 리포팅과 위치 추적

### 2.1 GitHub Step Summary (`--github-summary`)

`--github-summary`는 Markdown Summary에 전체 상태, TEM, 엔진별 결과와
`blob/<commit>/<path>#Lx-Ly` 위치 링크를 기록합니다. JSON 결과는 `ici.result/v2` 계약으로
엔진 상태, required/evidence, 대상 snippet·metrics, 외부 도구 실행 증거를 보존합니다.

### 2.2 Workflow annotations

FAIL/ERROR는 `::error`, WARN/SKIP은 `::warning` annotation으로 변환됩니다. 파일 경로,
줄 번호, 메시지는 GitHub workflow command escaping을 거쳐 출력되므로 소스 내용이
annotation 문법을 깨뜨리지 않습니다.

### 2.3 HTML 아티팩트

`--html verify_report.html`은 외부 CDN 없이 동작하는 9개 탭 HTML을 생성합니다. `verify` job
자체는 파일을 아티팩트로 업로드만 하고 브랜치나 PR 댓글에 직접 게시하지 않습니다 — sticky
댓글 게시는 별도 권한을 가진 `report-pr` job이 그 아티팩트를 소비해 수행합니다 (1.2 참조).
다운로드한 HTML은 폐쇄망에서도 로컬로 열 수 있습니다.

## 3. 신뢰된 HTML publish (`--publish`)

`--publish`를 명시하면 생성된 HTML을 GitHub Contents API를 통해 설정된 publish 경로에
업로드합니다. 이는 일반 PR 검증의 기본 동작이 아니며, `contents: write`를 포함한 신뢰된
workflow 또는 명시적 수동 실행에서만 사용해야 합니다.

```bash
dist/ici.pyz verify --report --html verify_report.html --github-summary --publish
```

publish 대상의 Pages 설정, 브랜치 정책, 토큰 범위는 조직 정책에 맞춰 별도로 검토합니다.
사내 GHES처럼 GitHub API 정책이 다른 환경에서는 API endpoint와 권한을 먼저 확인하고,
자동 PR 댓글을 기본 전제로 삼지 않습니다.

## 4. 사내 폐쇄망 및 Self-Hosted Runner

### 4.1 실행과 빌드의 차이

- 배포된 `dist/ici.pyz`는 런타임에 외부 CDN이나 PyPI를 조회하지 않는 단일 파일입니다.
- `ici.pyz`를 **빌드**하려면 Python 3.10, `uv`/패키지 캐시와 순수 Python 의존성 wheel이
  사전에 준비되어 있어야 합니다. 캐시가 없다면 build 단계에서 네트워크 또는 내부
  패키지 미러가 필요합니다.
- RHEL 7.9/8.10 및 이후 OS 전환기에는 `ici doctor` 결과와 엔진의 도구 증거를 함께
  보관합니다. `doctor`는 현재 구현에 포함된 git, gcc, g++, clang, clang-format, make, cmake,
  ruff, mypy, pytest, uv를 확인하고, 각 엔진은 실제로 호출한 도구의 경로·argv·버전·종료
  상태를 기록합니다.
- qmake/Qt·Ninja·binutils 전체 capability inventory와 도구별 버전 정책은 아직 별도
  capability 계약이 아니다. 반면 프로젝트 정의 기반 CMake/qmake build adapter는
  `v0.6.0`에서 지원하며 `build`·`test`·`sanitize` 엔진이 실제 CMake/CTest 또는 qmake/Make
  경로를 사용한다([adapter 구현 PR #76](https://github.com/jihoon22-lee/ici/pull/76)).
  adapter의 실행 증거는 해당 프로젝트의 `ici verify` 결과에서 확인하고, 전체 toolchain
  inventory를 자동 판정한다고 가정하지 않는다. 남은 toolchain·compile DB·Python
  compatibility·ELF/ABI·hybrid 범위는
  [`2026-08-19-ci-validation-features.md`](superpowers/plans/2026-08-19-ci-validation-features.md)의
  역사 설계와 [마스터 계획](superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md)
  I2·I3·I5·I7을 따른다.

### 4.2 Python 런타임

시스템 기본 `python3`가 구버전이어도 `scripts/launcher.sh`가 `ICI_PYTHON` 또는 설치된
Python 3.10 이상 후보를 탐색합니다. 폐쇄망 배포 전에는 대상 OS에서 실행 가능한 Python
인터프리터와 pure-Python wheel을 준비하고 `scripts/smoke.sh`로 확인합니다.

### 4.3 도구 실행 증거

현재 기능은 모든 도구를 일괄 검사하지 않는다. 기존 엔진이 `gcc/g++`, `ruff`, `mypy`,
`pytest`, `coverage`, `gcov` 등을 실제로 호출한 경우에만 실행 증거를 남기며, 해당 엔진의
필수/선택 정책에 따라 누락·실패를 `SKIP`, `WARN`, `FAIL`, `ERROR`로 구분한다. qmake나
binutils capability를 현재 `ici`가 자동 판정한다고 해석해서는 안 된다. 각 실행의 상태와
함께 실제 도구 경로·버전·argv를 확인하고, 전체 toolchain 정책이 필요하면 미래 계획을
별도 PR로 진행한다.

---

> **다음 단계**: [🏛️ 시스템 아키텍처 가이드](architecture.md)에서 엔진 파이프라인과
> `ici.result/v2` 데이터 모델을 확인하세요.
