# ici CI/CD 연동 가이드 (GitHub Actions & Self-Hosted Runner)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; **⚙️ CI/CD 연동 가이드** &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬 터미널에서 실행했을 때와 **GitHub Actions / Self-Hosted Runner** 환경에서 실행했을 때 100% 동일한 검증 결과와 품질 게이트를 보장합니다.

---

## 1. GitHub Actions 워크플로우 구성

`ici` 저장소는 자체 품질 검증을 위한 **개밥먹기(Dogfooding) CI 워크플로우**와 태그 푸시 시 자동으로 바이너리를 배포하는 **릴리스 워크플로우**를 갖추고 있습니다.

### 1.1 Dogfooding CI 워크플로우 (`.github/workflows/ci.yml`)
PR 생성 및 커밋 푸시 시, 프로젝트 소스로 빌드된 `dist/ici.pyz`가 `ici` 코드베이스 전체를 직접 전수 검증합니다:

```yaml
name: CI Quality Gate (Dogfooding)

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  verify:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
      issues: write
      checks: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - uses: astral-sh/setup-uv@v5

      # 1. 린팅 & 단위 테스트
      - run: uvx ruff check . && uv run --python 3.10 pytest -v

      # 2. 산출물 빌드 & 스모크 테스트
      - run: ./scripts/build-pyz.sh && ./scripts/smoke.sh

      # 3. 개밥먹기(Dogfooding) 자체 검증 게이트 + HTML 리포트 배포
      - run: dist/ici.pyz verify --report --html verify_report.html --github-summary --publish
        env:
          GITHUB_TOKEN: ${{ github.token }}

      # 4. 아티팩트 업로드
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ici-verification-report
          path: |
            verify_report.html
            verify_report.json
```

### 1.2 자동 릴리스 워크플로우 (`.github/workflows/release.yml`)
- **트리거**: 버전 태그(`v*.*.*`) 푸시 또는 GitHub Actions 탭에서 수동 실행(`workflow_dispatch`)
- **동작**:
  1. `dist/ici.pyz` 단일 실행 파일 빌드 및 SHA256 체크섬 생성
  2. [`CHANGELOG.md`](../CHANGELOG.md)에서 해당 버전(`[0.3.2]`)의 변경 내역을 정규식으로 자동 추출
  3. GitHub Release를 생성하고 `dist/ici.pyz` 바이너리를 첨부하여 릴리스 발행

---

## 2. CI 리포팅 기능

### 2.1 GitHub Step Summary 자동 생성 (`--github-summary`)
워크플로우가 완료되면 GitHub Actions 실행 결과 페이지 상단에 직관적인 요약 테이블과 TEM 품질 게이지를 렌더링합니다:

- 종합 합격 여부 배지 (`PASS` / `WARN` / `FAIL`)
- TEM 스코어 (`4.75 / 5.0`)
- 각 엔진별 검증 요약 및 GitHub 영구 링크(Permalink: `blob/<commit_sha>/path/file.py#L10-L25`)

### 2.2 인라인 에러 어노테이션 (`::error file=...::`)
코드에 문제(타입 에러, 린트 위반, 복잡도 초과 등)가 발생하면 GitHub PR의 **Files Changed** 탭의 해당 코드 라인에 자동으로 에러/경고 마커가 달립니다.

### 2.3 HTML 리포트 배포 및 Sticky PR 코멘트 (`--publish`)
`verify`에 `--publish`를 붙이면 생성된 `verify_report.html`을 **GitHub Contents API**로
레포지토리의 `gh-pages` 브랜치에 푸시하고, PR에 **스티키 댓글**을 남겨 원클릭 인터랙티브
뷰어 링크를 제공합니다:

```bash
dist/ici.pyz verify --report --html verify_report.html --github-summary --publish
```

- **배포 경로 네임스페이스**: PR 실행 → `pr/<number>/index.html`, `main` 푸시 → `main/index.html`.
  각 PR이 자기 경로에만 쓰므로 **여러 PR이 동시에 실행돼도 충돌이 없습니다** (같은 PR은 최신 런이 덮어씀).
- **self 모드 (기본)**: `GITHUB_TOKEN`만으로 자기 레포의 `gh-pages`에 배포. 추가 시크릿 불필요.
- **hub 모드 (옵션)**: `ICI_PUBLISH_REPO` / `ICI_PUBLISH_TOKEN`을 설정하면 중앙 리포트 허브
  레포지토리로 배포 (`<project>/pr/<n>/index.html`). Pages를 켜기 어려운 팀들을 위한 조직 공용 방식.
- **Pages 최초 1회 설정**: GitHub 페이지 설정에서 **Source를 `gh-pages` 브랜치**로 선택하면 끝.
  `--publish`가 매 실행마다 Pages 활성 여부를 확인하며, 미활성 상태에서는 스티키 댓글에
  설정 안내를 표시합니다 (그 전에도 리포트 파일은 계속 쌓입니다).
- **뷰어 링크 예시**:
  ```
  https://<org>.github.io/<repo>/pr/123/                (self 모드)
  https://<org>.github.io/ci-reports/<project>/pr/123/  (hub 모드)
  ```
- **워크플로우 권한**: `contents: write`(배포), `issues: write`(PR 코멘트),
  `pull-requests: write`(어노테이션)가 필요합니다.

> 참고: 폐쇄망 GHES에서도 외부 액션 없이 `GITHUB_TOKEN`과 표준 REST API만으로 동작합니다.

---

## 3. 사내 폐쇄망 Self-Hosted Runner 설정 팁

1. **Python 버전 호환성**:
   - 시스템 기본 `python3`가 3.6/3.8인 RHEL 8 환경이라도, `scripts/launcher.sh`가 사내 표준 파이썬 경로 또는 `ICI_PYTHON` 환경변수를 자동 탐색하여 3.10+ 엔진으로 구동됩니다.
2. **보안/방화벽 무관**:
   - 외부 PyPI 통신, CDN 자바스크립트 호출, certifi 인증서 가로챔 이슈가 전혀 없는 순수 독립 바이너리로 실행됩니다.

---

> **다음 단계**: [🏛️ 시스템 아키텍처 가이드 (Architecture Guide)](architecture.md)에서 `ici`의 내부 엔진 파이프라인과 패키징 메커니즘을 확인하세요.
