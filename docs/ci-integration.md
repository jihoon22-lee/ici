# ici CI/CD 연동 가이드 (GitHub Actions & Self-Hosted Runner)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; **⚙️ CI/CD 연동 가이드** &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬 터미널에서 실행했을 때와 **GitHub Actions / Self-Hosted Runner** 환경에서 실행했을 때 100% 동일한 검증 결과와 품질 게이트를 보장합니다.

---

## 1. GitHub Actions 워크플로우 구성 예시

`.github/workflows/ci.yml` 파일에 다음과 같이 단일 단계로 연동합니다:

```yaml
name: CI Verification Quality Gate

on:
  pull_request:
    branches: [ main, develop ]
  push:
    branches: [ main ]

jobs:
  verify:
    runs-on: self-hosted # 또는 ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run ici Verification Suite
        run: |
          ./dist/ici.pyz verify \
            --report \
            --html verify_report.html \
            --json verify_report.json \
            --github-summary "$GITHUB_STEP_SUMMARY" \
            --github-repo "${{ github.repository }}" \
            --github-commit "${{ github.sha }}"

      - name: Upload HTML Quality Report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ici-verification-report
          path: verify_report.html
```

---

## 2. CI 리포팅 기능

### 2.1 GitHub Step Summary 자동 생성 (`--github-summary`)
워크플로우가 완료되면 GitHub Actions 실행 결과 페이지 상단에 직관적인 요약 테이블과 TEM 품질 게이지를 렌더링합니다:

- 종합 합격 여부 배지 (`PASS` / `WARN` / `FAIL`)
- TEM 스코어 (`4.75 / 5.0`)
- 각 엔진별 검증 요약 및 GitHub 영구 링크(Permalink: `blob/<commit_sha>/path/file.py#L10-L25`)

### 2.2 인라인 에러 어노테이션 (`::error file=...::`)
코드에 문제(타입 에러, 린트 위반, 복잡도 초과 등)가 발생하면 GitHub PR의 **Files Changed** 탭의 해당 코드 라인에 자동으로 에러/경고 마커가 달립니다.

### 2.3 Orphan 브랜치를 통한 HTML 리포트 배포 및 Sticky PR 코멘트
사내 사서버나 GitHub Pages를 활용하여 생성된 `verify_report.html`을 별도 orphan 브랜치(예: `gh-pages` 또는 `reports`)로 자동 푸시하고, PR 코멘트에 원클릭 뷰어 링크를 업데이트할 수 있습니다:

```bash
# PR 코멘트에 게시될 링크 예시
https://<org>.github.io/<repo>/reports/pr-123/verify_report.html
```

---

## 3. 사내 폐쇄망 Self-Hosted Runner 설정 팁

1. **Python 버전 호환성**:
   - 시스템 기본 `python3`가 3.6/3.8인 RHEL 8 환경이라도, `scripts/launcher.sh`가 사내 표준 파이썬 경로 또는 `ICI_PYTHON` 환경변수를 자동 탐색하여 3.10+ 엔진으로 구동됩니다.
2. **보안/방화벽 무관**:
   - 외부 PyPI 통신, CDN 자바스크립트 호출, certifi 인증서 가로챔 이슈가 전혀 없는 순수 독립 바이너리로 실행됩니다.

---

> **다음 단계**: [🏛️ 시스템 아키텍처 가이드 (Architecture Guide)](architecture.md)에서 `ici`의 내부 엔진 파이프라인과 패키징 메커니즘을 확인하세요.
