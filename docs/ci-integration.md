# ici CI/CD 연동 가이드 (GitHub Actions & Self-Hosted Runner)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; **⚙️ CI/CD 연동 가이드** &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬, GitHub Actions, 사내 러너에서 같은 정책·결과 계약을 적용합니다. 결과가
항상 동일하다는 뜻은 아닙니다. OS, 컴파일러, Python, Ruff/Mypy/pytest 등의 가용성과
버전은 `doctor` 및 각 엔진의 `ToolEvidence`에 기록되고, 도구 체인이나 소스 환경이
다르면 실제 결과도 달라질 수 있습니다.

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
5. JSON/HTML 결과 아티팩트 업로드

PR에서 확인할 수 있는 결과는 다음과 같습니다.

- GitHub Step Summary: 엔진별 상태와 위치 링크
- GitHub Actions annotations: FAIL/ERROR/WARN/SKIP 위치 진단
- `ici-verification-report` 아티팩트: `verify_report.json`, `verify_report.html`

기본 PR 검증은 sticky PR 댓글이나 브랜치 쓰기를 수행하지 않습니다. 따라서 저장소의
PR 검증은 최소 권한으로 동작하며, 결과는 실행 화면의 Summary와 아티팩트에서 확인합니다.

### 1.2 신뢰된 main publish (`publish-main`)

`publish-main`은 아래 조건을 모두 만족할 때만 실행됩니다.

```yaml
if: github.event_name == 'push' && github.ref == 'refs/heads/main'
needs: verify
permissions:
  contents: write
```

이 job은 검증이 통과한 `main`을 다시 빌드하고, 명시적으로 `GITHUB_TOKEN`을 주입하여
다음 명령을 실행합니다.

```bash
dist/ici.pyz verify --html verify_report.html --publish
```

`--publish`는 Contents API를 사용하는 권한 있는 동작입니다. PR 검증 job에 이 권한을
확장하지 않고, 신뢰된 `main` push에서만 사용하도록 한 것이 기본 정책입니다. 별도의
수동/신뢰된 workflow에서 이 기능을 사용할 때도 대상 저장소와 토큰 권한을 명시적으로
검토해야 합니다.

### 1.3 Action 버전 고정

CI와 release workflow의 외부 Action은 Node 24 기반 릴리스 라벨을 주석으로 남기되,
실제 `uses:` 값은 다음 40자리 커밋 SHA에 고정되어 있습니다.

| Action | 릴리스 | 커밋 SHA |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `astral-sh/setup-uv` | v10.0.1 | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d` |
| `softprops/action-gh-release` | v3.0.2 | `3d0d9888cb7fd7b750713d6e236d1fcb99157228` |

### 1.4 Release workflow

`.github/workflows/release.yml`은 `v*.*.*` 태그 push 또는 `workflow_dispatch`에서
테스트·ZipApp 빌드·스모크 테스트·SHA-256 생성 후 GitHub Release에
`dist/ici.pyz`와 체크섬을 첨부합니다. 릴리스 job은 배포를 위해 `contents: write`를
사용하며, release용 Action도 위의 SHA 고정 정책을 따릅니다.

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

`--html verify_report.html`은 외부 CDN 없이 동작하는 6개 탭 HTML을 생성합니다. PR에서는
파일을 업로드만 하고, 브랜치나 PR 댓글에 자동 게시하지 않습니다. 다운로드한 HTML은
폐쇄망에서도 로컬로 열 수 있습니다.

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
  보관하여 Python, gcc/g++, cmake/qmake, pytest 등의 실제 버전을 확인합니다.

### 4.2 Python 런타임

시스템 기본 `python3`가 구버전이어도 `scripts/launcher.sh`가 `ICI_PYTHON` 또는 설치된
Python 3.10 이상 후보를 탐색합니다. 폐쇄망 배포 전에는 대상 OS에서 실행 가능한 Python
인터프리터와 pure-Python wheel을 준비하고 `scripts/smoke.sh`로 확인합니다.

### 4.3 도구 체인 증거

`gcc/g++`, `cmake`, `qmake`, `ruff`, `mypy`, `pytest`가 설치되지 않았거나 버전이 다르면
엔진은 이를 PASS로 숨기지 않고 `SKIP`, `WARN`, `ERROR`와 `ToolEvidence`로 구분합니다.
따라서 CI 결과를 비교할 때 상태와 함께 도구 경로·버전·argv를 확인해야 합니다.

---

> **다음 단계**: [🏛️ 시스템 아키텍처 가이드](architecture.md)에서 엔진 파이프라인과
> `ici.result/v2` 데이터 모델을 확인하세요.
