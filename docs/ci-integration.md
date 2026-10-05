# ici CI/CD 연동 가이드

> **현행 기준: flat CLI (`ici verify|plan|doctor|report|publish|diff`).** 이전
> 개정판의 `--report`/`--html`/`--github-summary` 플래그와 `--report-dir`
> publish는 stable 셸과 함께 제거됐다 — 리포트 생성은 `ici report`,
> 게시는 `ici publish`가 담당한다.

## 1. GitHub Actions 워크플로우

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)은 PR과 `main` push에서
검증을 수행한다. 검증과 리포트 게시는 권한이 다른 job으로 분리된다.

### 1.1 `verify` job (PR/main 게이트)

`contents: read`만 쓰고, checkout은 `persist-credentials: false`다. 이 job은
`GITHUB_TOKEN`·PR 댓글 API를 사용하지 않는다.

순서:

1. Ruff 포맷·린트, `pytest` (C++ 어댑터 E2E가 skip되지 않도록 Qt6/cmake/clang-tidy/clazy를
   먼저 설치한다 — `ICI_REQUIRE_BUILD_ADAPTERS=1`·`ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`)
2. `dist/ici.pyz` 재현 빌드 + isolated smoke (`scripts/verify-reproducibility.sh`, `scripts/smoke.sh`)
3. **Repo dogfood**: `dist/ici.pyz verify --result verify_report.json` → `report` →
   `scripts/check_next_floors.py` — stable의 warn/fail 밴드는 결과 문서의 측정값에 대한
   ratchet floor로 이 스크립트가 계속 강제한다
4. **Viewer dogfood**: job이 `viewer/build/ici-main`(coverage 계측)과 `build/ici-static`
   (Qt-free static)을 cmake로 소유·빌드한 뒤 `viewer/ici.toml` / `ici-static-cli.toml`
   workspace에 대해 `verify` + `report` + `--set=viewer` floors + `readelf` Qt-NEEDED 부정
   검사를 수행한다 — **ici는 빌드하지 않는다**
5. **Next path from artifact**: `tests/fixtures/next-self-verify/`에서 `plan → verify →
   report`가 `dist/ici.pyz`로 exit 0까지 도달하는지 검증하고, 생성 HTML의 Zero-CDN을
   grep으로 확인한다
6. 결과 JSON/HTML/SARIF을 `ici-verification-report` 아티팩트로 업로드

별도 `viewer-gui` job이 Qt5/Qt6 매트릭스로 GUI를 빌드·ctest하고, Qt-free static CLI가
정말 Qt 없이 구성되는지를 증명한다. `Merge Gate` job이 전체를 집계하고 branch
protection은 이 최종 체크 하나를 요구한다.

### 1.2 `report-pr` job (PR 리포트 게시)

`contents: write` + `pull-requests: write`를 가진 유일한 job이다. **base(병합 대상)
main**을 체크아웃해 pyz를 신뢰된 코드로 빌드하고, verify job의 아티팩트만 소비한다 —
PR 소스는 이 job에서 실행되지 않는다.

```yaml
dist/ici.pyz publish --result verify_report.json --out verify_report.html
(cd viewer && ../dist/ici.pyz publish --result verify_report.json --out verify_report.html)
```

각 workspace는 자기 라벨(`ici`, `icirv`)의 `<!-- ici-next:<label> -->` sticky
comment를 하나씩 갖고, 목적지는 `ici/<label>/pr-<N>/<sha>/index.html`이다 — 지연된
재실행이 새 run의 리포트를 덮지 않는다. 이후 단계가 실제 comment의 `report:` URL을
Pages가 serve할 때까지 폴링한다 — Contents API 성공만으로는 게시 완료로 간주하지 않는다.

### 1.3 `publish-main` job

`main` push에서 같은 게시를 수행한다. 대상은 `ici/<label>/<ref>/<sha>/index.html` —
워크스페이스 라벨이 분리되므로 root/viewer가 서로를 덮지 않는다.

### 1.4 Release workflow

`release.yml`은 같은 dogfood(pyz + viewer 빌드 + floors)를 release candidate에
수행한 뒤 `dist/ici.pyz`, `dist/icirv`, 소스 아카이브를 태그한다.

### 1.5 Candidate → Quality Zoo 수용 (수동·읽기 전용)

`candidate-quality-zoo.yml`은 `workflow_dispatch` 전용이다. candidate pyz를 이 저장소가
소유한 `quality-zoo/` corpus에 주입해 수용을 판정한다 — stable release 경계와
분리된 별도 acceptance artifact로 감사된다.

> **⚠ 전환 대기**: corpus의 16개 scenario는 stable `verify --report` 명령과
> `ici.result/v3` 문서를 기대한다. flat CLI는 `verify --result`와
> `ici.next.run`을 산출하므로 corpus·runner·expectation의 이관이 별도 작업으로
> 필요하다 — 다음 candidate 수용 전까지의 전환 조건이다
> ([inventory/stable-removal.md](design/ici-next/inventory/stable-removal.md)).

## 2. 리포팅

### 2.1 결과 문서

`ici verify --result <path>`가 `schema_id="ici.next.run"` JSON을 쓴다. 다섯 축이
분리된다 — 실행 완료, 증거 수준, 선택 범위, finding, 게시 상태. `metrics[]`는 측정값,
`limitations[]`는 판정 불가 항목을 명시한다.

### 2.2 HTML / SARIF

`ici report --result R --out page.html --sarif out.sarif`. HTML은 외부 에셋
0개(Zero-CDN)로 닫힌 네트워크에서도 연다 — smoke.sh가 생성물을 grep으로 검증한다.

### 2.3 Baseline / delta

```bash
# 기준선 생성/갱신은 사유를 확인한 별도 작업에서 수행
ici verify --result .ici/baseline.json
# PR 검증: 신규 finding을 gate
ici verify --result out.json --baseline .ici/baseline.json
ici diff .ici/baseline.json --result out.json
```

Fingerprint는 checkout root·경로 구분자에 무관하므로 baseline 비교가 안정적이다.
수치 회귀 floor는 `scripts/check_next_floors.py`가 결과 문서에서 강제한다 —
메트릭이 빠져 있으면 skip이 아니라 실패다.

### 2.4 대형 report 성능 추세

`report_benchmark.json`은 trend artifact로 업로드한다. shared runner의 wall clock은
관계 없는 이유로 움직이므로 gate가 아니라 기록이다(`scripts/benchmark_report.py`).

## 3. Action 버전 고정

모든 `uses:`는 전체 commit SHA + 버전 주석으로 고정한다 (`actions/checkout@<sha> # v7.0.1`).
태그만 믿으면 재태그된 액션이 조용히 바뀐다.
