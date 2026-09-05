# ici CI/CD 연동 가이드 (GitHub Actions & Self-Hosted Runner)

> **네비게이션**: [🏠 홈 (README)](../README.md) &bull; [🚀 사용자 가이드](user-guide.md) &bull; [📏 검증 엔진 레퍼런스](engine-reference.md) &bull; **⚙️ CI/CD 연동 가이드** &bull; [🏛️ 시스템 아키텍처](architecture.md) &bull; [📋 CHANGELOG](../CHANGELOG.md)

---

`ici`는 로컬, GitHub Actions, 사내 러너에서 같은 정책·결과 계약을 적용합니다. 결과가
항상 동일하다는 뜻은 아닙니다. OS, 컴파일러, Python, Ruff/Mypy/pytest 등의 가용성과
버전은 `doctor` 및 각 엔진의 `ToolEvidence`에 기록되고, 도구 체인이나 소스 환경이
다르면 실제 결과도 달라질 수 있습니다. 현재 실행은 bounded capability snapshot과 생성된
support matrix를 공통 맥락으로 사용하고, feature별 외부 도구는 해당 엔진이 실제로 호출한
경우에만 `ToolEvidence`를 기록합니다. 이 snapshot은 전체 배포 환경의 성공을 보증하지 않으며,
최종 feature acceptance와 release 결정은 별도 gate입니다.

## 현재 공개 release evidence

현재 공개 stable release는 [v0.10.2](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)다.
`v0.10.2` tag는 exact `main` commit
[`3b50dd4c485ddab212beb23ff820e82286a06e77`](https://github.com/jihoon22-lee/ici/commit/3b50dd4c485ddab212beb23ff820e82286a06e77)을
가리킨다. [exact-main run `33541134010`](https://github.com/jihoon22-lee/ici/actions/runs/33541134010)은
검증, Qt 5/Qt 6, `Publish Main Verification Report`, `Merge Gate`를 성공시켰고,
[release run `33541928666`](https://github.com/jihoon22-lee/ici/actions/runs/33541928666)도
provenance와 publish를 성공시켰다. [공개 release](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)는
non-draft/non-prerelease이며 `ici.pyz`, checksum, self/viewer HTML·JSON, `icirv`,
`icirv-gui`, GUI README까지 정확히 9개 asset을 포함한다. `ici.pyz` SHA-256은
`8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`다. main ici/viewer
Pages는 HTTP 200·`text/html`·정확한 title·외부 resource URL 0건으로 독립 감사됐다.
상세 asset 표와 검증 기록은 [`v0.10.2 public evidence workthrough`](../workthrough/2026-09-02-public-v0.10.2-evidence.md)를
참조한다.

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

### 1.1.2 선택적 release-contract 검증

기본 `standard` CI는 기존 분석 범위를 유지합니다. package/wheel은 모든 profile의
`python_compat`에서 `wheel_globs`로 opt-in할 수 있습니다. test quality, configured Make,
ELF ABI, typed integration 검증은 `deep` profile과 해당 정책을 명시적으로 opt-in해야 합니다.
현재 feature branch의 구현을 설명하는 설정 예시는 다음과 같습니다. 이 문서는 해당 설정의
최종 CI·toy-project acceptance 또는 stable release를 주장하지 않습니다.

```toml
[ici]
profile = "deep"

[engines.python_compat]
wheel_globs = ["dist/*.whl"]
wheel_required = true
wheel_policy = "pure"

[engines.test.quality]
enabled = true
mode = "warn"       # report | warn
repeat_runs = 2      # bounded total runs, maximum 3
slow_test_threshold = 1.0

[build.make]
enabled = true
build_argv = ["make", "all", "-j", "{jobs}"]
coverage_build_argv = ["make", "coverage", "-j", "{jobs}"]
sanitize_build_argv = ["make", "sanitize", "-j", "{jobs}"]
thread_sanitize_build_argv = ["make", "thread-sanitize", "-j", "{jobs}"]
test_argv = ["make", "check"]
clean_argv = ["make", "clean"]
jobs = 2

[engines.build]
enabled = true
[engines.binary_compat]
enabled = true
required = true
[engines.integration]
enabled = true
required = true
```

Make 계획과 integration case는 shell-free direct argv/typed whole-token placeholder만
허용하며 project root 밖 경로와 unbounded output을 거부합니다. Integration은 명시되지 않은
환경 상속도 거부하지만, build adapter는 toolchain 발견을 위해 CI process 환경을 사용합니다. build가
발행하는 `ici.artifacts/v2` manifest의 id/target/redacted command provenance는
`binary_compat`와 `integration`의 입력으로 연결됩니다. PR에서 실행한다면 verify job의
읽기 전용 권한과 artifact 전달 경계를 유지하고, 권한 있는 publish/comment job이 신뢰되지
않은 PR 코드를 다시 실행하지 않도록 분리해야 합니다. 위 variant target 이름은 프로젝트의
실제 Make target으로 바꿔야 합니다. configured Make는 모든 variant가 하나의 `shadow_dir`를
공유하므로 `clean_argv`와 각 variant build command가 실행 전에 해당 shadow를 정리·재생성해야
합니다. CMake/qmake adapter는 variant별 별도 shadow를 소유합니다.

test quality 관측은 base test 결과와 별도 target/inventory로 `ici.test.slow-test` 및
`ici.test.flaky-test` finding을 남깁니다. `mode = "report"`는 정보를 보고서에만 추가하고,
`mode = "warn"`은 해당 warning을 test engine gate에 반영합니다. mutation 설정은 capability
probe만 수행하므로 변이 도구가 없다는 이유로 base test gate를 바꾸지 않습니다. Runtime test
결과와 timing은 cache에서 재사용하지 않으며, `slow_tests_observed`는 capped inventory보다
먼저 고유 slow row 전체를 셉니다. 비유한·과도하게 긴 duration token은 관측에서 제외됩니다.
정식 config loader는 malformed mode/mutation selector를 `ConfigError`로 거부하고, 직접 engine
호출 경계에서만 report/auto로 안전하게 정규화합니다.

PR에서 확인할 수 있는 결과는 다음과 같습니다.

- GitHub Step Summary: 엔진별 상태와 위치 링크
- GitHub Actions annotations: FAIL/ERROR/WARN/SKIP 위치 진단
- `ici-verification-report` 아티팩트: `verify_report.json`, `verify_report.html`
- 선택한 경우 `verify_report.sarif`: 동일 canonical finding을 SARIF 2.1.0으로 전달하는
  deterministic 결과(100,000 results/10,000 rules 상한)
- **sticky PR 댓글** (`report-pr` job): HTML 리포트 링크 + 엔진 상세 (아래 1.3)

### 1.1.1 분석 캐시와 CI 실행

`verify`는 기본적으로 분석 cache를 사용하지만, cache는 사용자 로컬 파일 저장소이며
GitHub Actions job 사이에 자동으로 공유되거나 업로드되지 않습니다. hosted runner처럼
실행마다 workspace/home이 새로 만들어지는 환경에서는 보통 job 수명 동안만 존재합니다.
지속형 self-hosted runner가 home을 유지하는 경우에도 project root, source/build-config content,
effective ici config, toolchain versions, engine implementation, build variant, ici version을
포함한 cache key가 모두 같을 때만 재사용됩니다.

완전히 새로운 분석을 강제하거나 release 검증에서 이전 로컬 상태를 배제하려면 다음처럼
lookup과 write를 함께 끕니다.

```bash
dist/ici.pyz verify --no-cache \
  --report --html verify_report.html --github-summary
```

일반 CI 실행에서 cache를 유지하고 싶다면 별도 remote cache 설정은 필요하지 않습니다. runner의
로컬 위치를 점검하거나 정리할 때 `ici cache`와 `ici cache --clear`를 사용합니다. `--clear`는
정확히 ici가 소유한 local `entries-v1` 아래 entry만 지우며 checkout, source, build artifact,
baseline 파일은 건드리지 않습니다. cache entry 쓰기는 임시 파일 + flush/`fsync` + atomic
replace이고, source/config 파일은 읽기 전용으로 digest하므로 cache 사용이 프로젝트 파일을
변경하지 않습니다.

완료된 `PASS`/`WARN`/`FAIL`은 유효한 증거라면 cache될 수 있지만 `ERROR`/`SKIP`/`NOT_RUN`,
timeout·truncated output·tool error 및 invalid artifact는 cache 성공 결과로 저장하지 않습니다.
손상되거나 stale한 local entry는 CI 실패가 아니라 miss로 처리되어 엔진이 다시 실행됩니다.
source digest에서는 `verify_report.json`과 engine별 `*_report.json`처럼 ici가 생성하는 report
JSON 이름을 제외합니다. cache reader는 `O_NOFOLLOW`/regular-file 검사, 32 MiB 상한,
duplicate JSON key 및 `NaN`/`Infinity` 거부로 entry를 신뢰 경계에서 검증하고, 새 directory/file은
0700/0600 권한으로 만듭니다. artifact manifest가 있으면 hit/store 양쪽에서 containment와
content·size·mode를 다시 확인합니다.

I2-4의 아래 수치는 당시 local snapshot으로 보존합니다. 전체 Python 3.10 run은 935 tests
passed였고 targeted 테스트와 reproducibility script도 통과했으며, 이후 PR/CI/Pages 및
release evidence는 현재 release 기록에서 확인할 수 있습니다.

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
viewer C++/Qt verify, GUI CTest·headless smoke를 검증된 tag target에서 다시 실행합니다. GitHub
Release에는 `ici.pyz`, 체크섬, CLI/GUI viewer와 함께 self/viewer HTML·JSON 검증
리포트를 첨부합니다. 수동 실행의 `version_tag`는 필수이며 태그가 이미
존재해야 하므로, 선택한 workflow branch의 소스가 릴리스로 대체되지 않습니다.

### 1.6 Candidate → Quality Zoo 수용 (수동·읽기 전용 경로)

`.github/workflows/candidate-quality-zoo.yml`은 stable release나 일반 toy PR gate와 분리된
ici-hosted `workflow_dispatch` workflow다. 후보 pyz를 Quality Zoo에 주입해 교차 저장소
수용을 수행할 때만 사용한다. 이 경로의 원격 수용은 별도 read-only acceptance artifact로
감사하며, 일반 toy PR gate와 stable release 경계에는 영향을 주지 않는다.

candidate consumer job은 Qt lifetime 및 C++ static-analysis scenario가 도구 부재로 조용히
skip되지 않도록 hosted runner provisioning 단계에서 `clang`, `clang-tidy`, `clazy`, `cmake`,
`g++`, `pkg-config`, `qt6-base-dev`를 준비한다. 이 CI 전용 단계는 ici/Quality Zoo 실행과
분리되며 candidate preflight/실행은 비특권·무인증 상태로 유지되고 `GH_TOKEN`/
`GITHUB_TOKEN`을 사용하지 않는다. 현재 로컬 purity contract는 `32 passed`, `actionlint`도
PASS다. 첫 sanitizer 범위의 exact-revision acceptance에 이어 run `33718024450`이 category
taxonomy와 Qt lifetime expectation을 포함한 6개 scenario를 모두 수용했다(artifact
`9879217928`). ThreadSanitizer도 별도 candidate artifact와 candidate-only manifest를 사용한
run `33737405098`에서 8/8 contract를 수용했다. 앞선 category/Qt evidence나 이 TSan evidence는
다른 feature head에 재사용하지 않는다.

실행 전 toy-projects의 `quality-zoo` 기대값이 포함된 정확한 revision과 ici candidate producer
artifact의 좌표를 별도로 확인한다. 기본 `toy_revision_mode=main`은 기존처럼 보호된
toy-projects `main` tip만 허용한다. 아직 병합되지 않은 Quality Zoo 기대값으로 후보를
검증하려면 `toy_revision_mode=pull_request`와 같은 저장소의 열린 PR 번호를 함께 지정한다.
workflow는 네 개의 필수 입력과 두 개의 선택 입력을 받는다.

| 입력 | 의미와 검증 |
|---|---|
| `ici_target_sha` | candidate가 빌드된 ici `main`의 전체 소문자 40자리 SHA |
| `candidate_artifact_id` | ici Actions candidate ZIP의 양의 정수 artifact ID |
| `candidate_archive_sha256` | API에서 내려받은 원본 ZIP의 전체 소문자 64자리 SHA-256 |
| `toy_target_sha` | 기대값을 포함한 toy-projects `main` 또는 PR head의 전체 소문자 40자리 SHA |
| `toy_revision_mode` | `main`(기본값) 또는 `pull_request`; revision의 출처를 선택 |
| `toy_pr_number` | `pull_request` 모드에서만 필요한 toy-projects 열린 PR 번호 |

ici `main`에서 다음처럼 수동 dispatch한다.

```bash
gh workflow run candidate-quality-zoo.yml --ref main \
  -f ici_target_sha=<ici-main-sha> \
  -f candidate_artifact_id=<artifact-id> \
  -f candidate_archive_sha256=<archive-sha256> \
  -f toy_target_sha=<toy-main-sha>
```

병합 전 toy PR head를 검증할 때는 PR API 응답에서 번호와 head SHA를 읽어 정확히 넣는다.

```bash
gh workflow run candidate-quality-zoo.yml --ref main \
  -f ici_target_sha=<ici-main-sha> \
  -f candidate_artifact_id=<artifact-id> \
  -f candidate_archive_sha256=<archive-sha256> \
  -f toy_revision_mode=pull_request \
  -f toy_pr_number=<open-toy-pr-number> \
  -f toy_target_sha=<exact-open-toy-pr-head-sha>
```

workflow는 실행 ref가 ici `main`인지, `main` 모드에서는 toy SHA가 현재 `main`과 일치하는지,
`pull_request` 모드에서는 지정한 PR이 아직 열려 있고 병합되지 않았는지 확인한다. 후자의
경우 base branch가 `main`이고 base/head repository의 전체 이름과 numeric ID가 모두
`jihoon22-lee/toy-projects`와 일치해야 하며, head SHA도 입력과 정확히 같아야 한다. 따라서
fork PR은 거부되고, 다른 번호·branch·repository·stale head를 재사용할 수 없다. 두 모드 모두
canonical toy repository를 검증된 exact SHA로 checkout한다. 이어 candidate manifest의 target,
candidate run, Merge Gate check/job/run ID와 attempt·URL을 독립 Actions API 응답으로 다시
검증한다. `candidate_intake`는 먼저 토큰 없이 preflight를 수행하고, 그 결과를 바탕으로
별도 읽기 전용 API 조회를 한 뒤 provenance를 완전히 검증한다. Quality Zoo 실행은 검증된
로컬 `ici.pyz` 경로만 사용하며, 선택한 mode/repository/exact SHA와 PR mode의 번호·repository
ID는 acceptance artifact의 `toy-revision.json`에 함께 남긴다. Candidate preflight와 실행 단계에서는
`GH_TOKEN`/`GITHUB_TOKEN`/OIDC·runtime token을 명시적으로 제거한다.

검증된 exact toy revision에 `quality-zoo/candidate-manifest.json`이 있으면 이를 우선
선택하고, 파일이 없을 때만 기존 `quality-zoo/manifest.json`으로 fallback한다. 두 파일 모두
regular non-symlink 파일이어야 하며 candidate가 malformed candidate manifest를 제공한 경우
stable manifest로 조용히 우회하지 않고 실패한다. 선택 직후와 Quality Zoo 실행 직후 SHA-256을
재확인하고, 결과 artifact의 `results/manifest-selection.json`에
`quality-zoo.manifest-selection/v1`, 선택 source/path/digest를 기록한다. 따라서 candidate-only
시나리오를 stable toy PR gate에 섞지 않으면서 어떤 기대값 집합을 실행했는지 독립적으로
감사할 수 있다.

성공 조건은 `quality-zoo.suite/v1`, non-empty scenario 결과, `scenario_count` 일치,
`contract_verdict: PASS`, runner error 없음이다. preflight/intake 결과, API evidence와
Quality Zoo 결과는 별도의 uncompressed 14일 Actions artifact로 업로드한다. 이 workflow는
`publish`, Pages 배포, PR comment 작성·갱신, `<!-- ici-report -->` marker 추가를 하지 않는다.
따라서 기존 toy PR의 normal gate와 released ici `v0.10.2` pin은 그대로 유지되고, Q0의
released-artifact acceptance는 candidate 수용의 근거로 재사용되지 않는다.

#### 독립 감사된 candidate 수용 증거

정확한 revision과 artifact 좌표는 다음과 같다.

| 항목 | 독립 감사 결과 |
|---|---|
| ici exact-main workflow | [run `33707430378`](https://github.com/jihoon22-lee/ici/actions/runs/33707430378), `success`, attempt 1, head `6df011f98be1a19092b112cb56c596dc35bcae4d` |
| ici required jobs | Qt5 GUI, Qt6 GUI, Verify & Dogfood ici, Publish Main Verification Report, Merge Gate 모두 `success`; push에서 PR publisher만 예상 `skipped` |
| candidate producer | [run `33706057540`](https://github.com/jihoon22-lee/ici/actions/runs/33706057540), target `9d470edca7ab037a24dcd6594531a822f116548b`, `success` |
| candidate artifact | [ID `9875319095`](https://github.com/jihoon22-lee/ici/actions/artifacts/9875319095), raw ZIP `2,285,368` bytes, SHA-256 `4aec084b3a30ac01a1df5124fa3b42b7f51d23f66c12b490194a84549be9db27`; contained `ici.pyz` `2,284,045` bytes, SHA-256 `e7f1a2ce7147057538873a802715c7bf2b12e530a85070af862e02e378caceb8` |
| toy-projects input | exact `main` `2d0d7c0b2dcc137a782d6042438fc287bffdf570` ([commit](https://github.com/jihoon22-lee/toy-projects/commit/2d0d7c0b2dcc137a782d6042438fc287bffdf570)) |
| acceptance | [run `33710695336`](https://github.com/jihoon22-lee/ici/actions/runs/33710695336), job `100509326331`, `success`, head `6df011f98be1a19092b112cb56c596dc35bcae4d` |
| acceptance artifact | [ID `9876797536`](https://github.com/jihoon22-lee/ici/actions/artifacts/9876797536), raw ZIP `1,104,307` bytes, SHA-256 `e66ae2b65988abe10fc5ddb92a5c3bb6fc238ec2f77b7fd27ccfe75c24194a5f` |

Acceptance artifact의 `quality-zoo.suite/v1`는 5개 scenario 모두 `contract_verdict: PASS`,
runner error 0을 기록했다. 후보 executable SHA와 각 expected contract의 핵심은 다음과 같다.

| Scenario | Observed / evidence / confidence | Expected rule · location |
|---|---|---|
| `cpp.asan-use-after-free` | `FAIL` / `MEASURED` / `exact` | `asan.heap-use-after-free` · `src/fault.cpp:5` |
| `cpp.lsan-memory-leak` | `FAIL` / `MEASURED` / `exact` | `lsan.memory-leak` · `src/fault.cpp:3` |
| `cpp.ubsan-signed-overflow` | `FAIL` / `MEASURED` / `exact` | `ubsan.signed-integer-overflow` · `src/fault.cpp:3` |
| `cpp.sanitizer-clean` | `PASS` / `MEASURED` / `high` | informational completion · `tests/test_clean.cpp:1`; active sanitizer defect 금지 |
| `python.dead-private-function` | `WARN` / `ESTIMATED` / `medium` | 기존 Python known-answer |

이는 exact remote dispatch와 rule/status/evidence/confidence/path/line contract, 그리고
ASan/LSan/UBSan 및 clean runtime evidence만 닫는다. 별도 TSan exact acceptance는 run
`33737405098`에 기록하며, broader Qt lifetime/ownership, Q1-Q5, I4 aggregate,
version/release는 계속 pending이다. 이 workflow는
별도 acceptance artifact만 업로드하며 Pages 배포, PR comment/marker, normal publisher,
tag/release 또는 version 변경을 수행하지 않는다.

## 2. 리포팅과 위치 추적

### 2.1 GitHub Step Summary (`--github-summary`)

`--github-summary`는 Markdown Summary에 전체 상태, TEM, 엔진별 결과와
`blob/<commit>/<path>#Lx-Ly` 위치 링크를 기록합니다. JSON 결과는 `ici.result/v3` 계약으로
엔진 상태, required/evidence, 기존 대상 snippet·metrics, 외부 도구 실행 증거와 canonical
finding inventory를 보존합니다. v3 finding fingerprint는 checkout root·path separator에
무관하므로 후속 baseline/delta gate에서 같은 문제를 안정적으로 식별할 수 있습니다.

### 2.2 Workflow annotations

FAIL/ERROR는 `::error`, WARN/SKIP은 `::warning` annotation으로 변환됩니다. 파일 경로,
줄 번호, 메시지는 GitHub workflow command escaping을 거쳐 출력되므로 소스 내용이
annotation 문법을 깨뜨리지 않습니다.

### 2.3 HTML·SARIF 아티팩트

`--html verify_report.html`은 외부 CDN 없이 동작하는 기본 10개 탭 HTML을 생성하며, baseline을
함께 비교하면 `Baseline Delta` 탭이 추가됩니다. `verify` job 자체는 파일을 아티팩트로
업로드만 하고 브랜치나 PR 댓글에 직접 게시하지 않습니다 — sticky
댓글 게시는 별도 권한을 가진 `report-pr` job이 그 아티팩트를 소비해 수행합니다 (1.2 참조).
다운로드한 HTML은 폐쇄망에서도 로컬로 열 수 있습니다.

`--sarif <path>`는 JSON/HTML과 같은 canonical v3 finding inventory에서 SARIF 2.1.0을
결정적으로 생성합니다. rule/result 순서, percent-encoded source-relative URI, severity, `ici/v3`
fingerprint, related location, suppression 및 baseline 상태를 보존합니다. `--report`를 함께
요청한 경우에만 원본 전체 inventory가 별도 `ici.result/v3` JSON에도 남으며, SARIF 상한
(100,000 result·10,000 rule)을 넘으면 결과를 자르지 않고 serialization error로 실패합니다.

Actionable finding이 2,000개를 초과하는 HTML은 초기 server-render row를 50개로 제한하고,
`ici.html-report/v1` inline JSON을 browser가 50개 단위로 검색·페이지네이션합니다. embedded JSON
64 MiB 상한을 넘으면 partial HTML을 게시하지 않고 실패합니다. 이 lazy hydration은 표시 비용만
줄이며 JSON/HTML inventory의 finding 개수를 줄이지 않습니다.

### 2.4 CI baseline/delta gate

저장소가 검토한 기준선 파일(예: `.ici/baseline.json`)을 커밋해 두면 PR 검증에서 다음처럼
delta를 확인하고 gate할 수 있습니다.

```bash
# 기준선 생성/갱신은 변경 사유를 확인한 별도 작업에서 수행
dist/ici.pyz verify --write-baseline .ici/baseline.json

# PR 검증: JSON/HTML/Step Summary를 남기고 actionable delta를 gate
dist/ici.pyz verify \
  --baseline .ici/baseline.json \
  --fail-on-new \
  --report --html verify_report.html --github-summary
```

`--fail-on-new`는 baseline 없이 사용할 수 없으며, baseline은 `ici.result/v3`만 허용합니다.
baseline 입력과 출력, 그리고 baseline finding이 가리키는 소스 위치는 프로젝트 루트 안에
canonical하게 있어야 합니다. `..`로 루트를 벗어나거나 프로젝트 내부 symlink를 통해
루트 밖으로 해석되는 경로는 CLI에서 차단됩니다. 이 검사는 읽기와 쓰기 모두에 적용되므로
CI가 작업 디렉터리 밖 파일을 기준선으로 소비하거나 덮어쓸 수 없습니다.

기존 파일을 읽은 뒤 새 파일을 쓰므로 다음처럼 같은 경로를 지정하는 갱신은 허용됩니다.

```bash
dist/ici.pyz verify \
  --baseline .ici/baseline.json \
  --write-baseline .ici/baseline.json \
  --report --html verify_report.html --github-summary
```

단, `--report`가 기본으로 쓰는 `verify_report.json`과 `--write-baseline`을 같은 경로로
지정하면 report를 덮어쓰므로 exit 2로 실패합니다. baseline 파일 자체를 갱신하는 작업과
PR 검증을 분리하면 기준선 변경을 코드 리뷰로 확인하기도 쉽습니다.
`--fail-on-new`가 실패한 실행도 입력 baseline과 같은 경로를 덮어쓰지 않으므로, 새 finding이
다음 실행에서 자동으로 기준선에 편입되어 숨는 일이 없습니다. 실패 상태의 snapshot이
필요하면 다른 경로에 기록해 별도로 리뷰합니다.

비교는 엔진·fingerprint·위치를 기준으로 모든 finding occurrence를 분류합니다. `new`,
`unchanged`, `moved`, `resolved` 네 상태와 현재/기준선 위치를 보존하며, severity 상승 또는
기준선 suppressed → 현재 unsuppressed 전환은 `regressed`로 표시합니다. `--fail-on-new`는
현재 finding이 actionable일 때만 새 항목과 regression을 gate합니다. `info` severity와
suppressed finding은 gate에서 제외되고, `resolved`는 실패시키지 않습니다. 따라서 같은
위치의 severity 변경은 `unchanged + regressed`가 될 수 있고, suppression은 현재 finding을
조치 대상에서 제외하는 표시인 반면 baseline은 과거 finding inventory snapshot이라는
차이가 있습니다.

baseline metadata의 버전·fingerprint·analysis policy·tool policy 불일치는 호환성
warning으로 보고됩니다. 이 warning만으로 baseline gate가 실패하지는 않지만, 같은 실행의
엔진 `FAIL`/`ERROR` 등 기존 suite 결과는 독립적으로 적용됩니다.

결과 노출 위치는 다음과 같습니다.

- `verify_report.json`: `baseline_comparison`에 네 상태 count, 전체 delta, 위치, severity,
  suppression/regression/gate 플래그와 warning을 보존합니다.
- 각 engine 결과의 optional `cache_hit`와 nullable `cache_key`는 cache 재사용 여부와
  identity digest를 보존합니다. 기존 `ici.result/v3` 파일에는 이 필드가 없을 수 있으므로
  report 소비자는 누락을 허용해야 합니다.
- `verify_report.html`: `Baseline Delta` 탭에서 gate와 warning, issues-first delta를 보여줍니다.
- `--github-summary`: Markdown Summary에 count와 이슈 우선 상세를 추가합니다.
- 콘솔: `Baseline Finding Delta` 패널에서 count와 gate 우선 항목을 출력합니다.
- `report-pr`/신뢰된 `publish`: JSON의 baseline 요약을 sticky PR 댓글에 새 finding·regression·
  gated count 및 compatibility warning으로 추가합니다. 댓글은 요약 링크이고, 전체 inventory와
  위치는 HTML/JSON을 확인하도록 유지합니다.

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
- capability snapshot은 qmake/Qt·Ninja·binutils를 무조건 설치하거나 모든 host 조합을
  보증하는 목록이 아니다. support matrix와 각 엔진의 실제 `ToolEvidence`를 기준으로
  적용 가능 여부와 fallback을 확인한다. 프로젝트 정의 기반 CMake/qmake adapter는
  `v0.6.0`부터 지원하며, 현재 feature branch는 root Makefile을 `[build.make]` 설정으로
  명시적으로 실행하는 shell-free Make backend도 제공한다. `build`·`test`·`sanitize`는
  CMake/CTest, qmake/Make 또는 configured Make 경로를 사용하며, 결과에는 backend·argv·variant
  evidence가 남는다.
- 현재 feature branch에는 package/wheel 정적 계약, deep test-quality report/warn 관측,
  deterministic SARIF, 대형 HTML lazy rendering, `ici.artifacts/v2` provenance, readelf 기반
  ELF/ABI 정책, typed Python/C++ integration contract도 구현되어 있다. package/wheel은
  import/build 없이 검사하고, binary compatibility는 binary를 실행하지 않으며, integration은
  shell-free bounded case만 실행한다. 이 문서의 feature 설명은 구현 범위이며, 최종 cross-repo
  toy acceptance·전체 CI gate·stable release 근거로 해석하지 않는다.

### 4.2 Python 런타임

시스템 기본 `python3`가 구버전이어도 `scripts/launcher.sh`가 `ICI_PYTHON` 또는 설치된
Python 3.10 이상 후보를 탐색합니다. 폐쇄망 배포 전에는 대상 OS에서 실행 가능한 Python
인터프리터와 pure-Python wheel을 준비하고 `scripts/smoke.sh`로 확인합니다. 프로젝트 wheel을
검사하려면 `python_compat.wheel_globs`를 명시하고, `wheel_policy = "pure"`를 선택하면 native
extension member가 실패합니다. wheel의 RECORD hash/size와 package/entry-point identity도
bounded하게 확인되며 wheel을 ici가 만들거나 추출하지는 않습니다.

### 4.3 도구 실행 증거

기능별로 필요한 도구만 bounded probe/실행하며 모든 도구를 무조건 검사하지 않는다. 엔진이
`gcc/g++`, `clang`, `clang-tidy`, `clazy`, `cmake`, `qmake`, `make`, `readelf`, `ruff`, `mypy`,
`pytest`, `coverage`, `gcov` 등을 실제로 호출한 경우에만 실행 증거를 남기며, 해당 엔진의
필수/선택 정책에 따라 누락·실패를 `SKIP`, `WARN`, `FAIL`, `ERROR`로 구분한다. qmake/Make와
binutils가 설치됐다는 사실만으로 모든 project target을 검증했다고 해석해서는 안 된다. 각
실행의 상태와 함께 실제 도구 경로·버전·argv·timeout/truncation을 확인하고, feature branch의
최종 acceptance와 release 판단은 별도 gate에서 수행한다.

---

> **다음 단계**: [🏛️ 시스템 아키텍처 가이드](architecture.md)에서 엔진 파이프라인과
> `ici.result/v3` 데이터 모델을 확인하세요.

## 5. Candidate 채널 (stable release가 아님)

candidate는 공개 stable artifact가 아니라, 아직 release로 승인되지 않은 `main` commit을
다른 저장소에서 검증하기 위한 수동 경로다. 두 workflow 모두 `workflow_dispatch` 전용이고
`refs/heads/main`에서만 실행되며, Pages·PR comment·tag·release를 만들지 않는다.
### Candidate artifact (not a release)

The candidate producer is a separate, manual `workflow_dispatch` path. It must be dispatched
from `refs/heads/main` with a required full lowercase 40-character `target_sha`; the target must
equal the protected-main commit that supplies this workflow and remain an ancestor of the fetched
`main`. It selects the newest exact successful `Merge Gate`
check for that SHA from at most ten explicit 100-check pages, refuses fallback to an older success,
and verifies the check→job→run/attempt chain before checking out the exact target. The canonical
`CI Quality Gate (Dogfooding)` main-push run is fetched independently from the Actions Runs API;
the selected `Merge Gate` job is fetched independently from the Jobs API. The run response must
match the repository/head repository, `name`, `path`, `event`, `head_branch`, target `head_sha`,
`status`, `conclusion`, positive `run_attempt`, and canonical run `html_url`. The job response
must match its `id`, `run_id`, `run_attempt`, target `head_sha`, `name`, `workflow_name`,
`head_branch`, `status`, and `conclusion`, plus canonical job `html_url`, API `url`, `run_url`,
and `check_run_url`.

The build runs `scripts/verify-reproducibility.sh` (two byte-identical builds with unchanged
source status) and `scripts/smoke.sh` (version/help, doctor, shell environment, report generation,
and Zero-CDN checks). The immutable Actions artifact contract has exactly these three files:
`ici.pyz`, `ici.pyz.sha256`, and `candidate-provenance.json`. The manifest schema is
`ici.candidate/v1` and records the repository, `target_sha`,
`candidate_workflow_definition_sha`, package version, `candidate_run_id`,
`candidate_run_attempt`, `merge_gate_check_run_id`, `merge_gate_job_id`, `merge_gate_run_id`,
`merge_gate_run_attempt`, `merge_gate_job_url`, `merge_gate_url`, artifact SHA/size, and
`retention_days: 14`; the upload uses
`overwrite: false`, 14-day retention, and no compression. The producer records artifact ID,
digest, and authenticated download coordinates in the Actions summary.

This path does not tag, release, publish Pages, or write a PR comment, and it does not bump the
version: `v0.10.2` remains the stable release. `package_version` describes the selected target and
does not authorize or create a tag/release even if it differs from the stable version in the future.
The validation job has read-only Actions/Checks/
Contents access; candidate-controlled build commands run after publication credentials are
scrubbed and have no publication authority. The bounded helper implements and tests the workflow's
`create` and `verify` subcommands. The focused local suite has 111 passing tests, and the live API
verifier, full Python 3.10 repository suite, static/type/workflow checks, reproducible build, smoke,
and real built-pyz bundle round trip pass. The remote producer evidence is now complete. Exact source
SHA `7872a7b80899cbd3d40d92d18e7920cd7e2283e7` passed [main run `33688279264`](https://github.com/jihoon22-lee/ici/actions/runs/33688279264)
with every job green; [Merge Gate check/job `100442919168`](https://api.github.com/repos/jihoon22-lee/ici/check-runs/100442919168)
was attempt 1. Independent [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/) and [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
matched the extracted main artifact bytes, carried the exact source SHA, used the expected titles
`ici Verification Report — ici` and `ici Verification Report — viewer`, and passed Zero-CDN checks.

Candidate [run `33689056008`](https://github.com/jihoon22-lee/ici/actions/runs/33689056008) succeeded.
Its [artifact ID `9869395069`](https://github.com/jihoon22-lee/ici/actions/artifacts/9869395069) is named
`ici-candidate-7872a7b80899cbd3d40d92d18e7920cd7e2283e7`; the [artifact API](https://api.github.com/repos/jihoon22-lee/ici/actions/artifacts/9869395069)
and raw downloaded ZIP both have digest
`sha256:640e50ecf5b099174c16f1ef5d2b5b87945329711e96f926d94c3cc04109081e`, size `2,277,109` bytes,
and expiry `2026-09-16T22:14:38Z`. The ZIP contains exactly these entries:

| Entry | Mode | Bytes |
|---|---:|---:|
| `candidate-provenance.json` | `0644` | 859 |
| `ici.pyz.sha256` | `0644` | 74 |
| `ici.pyz` | `0755` | 2,275,786 |

The bundled `ici.pyz` SHA-256 is
`53fc75f0a073a74689babfe9ef8a4b2378995002d7d563bdc52da548fdbb9ee8`, and it reports `ici 0.10.2`.
The candidate manifest byte-matched the independent verifier. The check/job/run canonical API
identities all matched, including `workflow_name`, `head_branch`, attempts, and canonical [check](https://api.github.com/repos/jihoon22-lee/ici/check-runs/100442919168),
[job](https://api.github.com/repos/jihoon22-lee/ici/actions/jobs/100442919168), and [run](https://api.github.com/repos/jihoon22-lee/ici/actions/runs/33688279264)
URLs. The observed v7 upload ZIP preserved these modes; an earlier generic assumption that upload
ZIPs lose modes does not apply to this artifact.

This closes the remote producer. The separate ici-hosted `candidate-quality-zoo.yml` workflow
defines the manual consumer path: it injects the verified candidate by local path into a read-only
Quality Zoo run while every toy PR's normal gate remains pinned to released ici `v0.10.2`. The first
exact-revision candidate acceptance completed at run `33710695336` for the existing sanitizer
contract. The follow-up run `33718024450` then accepted the category-taxonomy candidate against the
six-scenario toy main revision, including the Qt parent-ownership expectation; acceptance artifact
`9879217928` records all six contracts as passing. The released-artifact Q0 result may be linked or
added as a section of the existing `<!-- ici-report -->` body, but must preserve exactly one sticky
comment rather than creating a second marker/comment.

The consumer job provisions `clang`, `clang-tidy`, `clazy`, `cmake`, `g++`, `pkg-config`, and
`qt6-base-dev` on its runner so a future Qt lifetime/C++ static-analysis scenario can execute.
Provisioning and candidate preflight/execution do not use GitHub credentials; local purity coverage
is `32 passed` and actionlint passes. Before the candidate run, the consumer prefers
`quality-zoo/candidate-manifest.json` from the exact toy-projects commit when present; otherwise it
uses `quality-zoo/manifest.json`. A selected manifest must be a regular non-symlink file, and its
SHA-256 is checked before and after execution. The acceptance artifact records the selected path,
source (`candidate` or `stable-fallback`), and digest as `quality-zoo.manifest-selection/v1`, so a
candidate-only expectation set is auditable without changing the released-artifact toy gate. The
ThreadSanitizer는 별도 candidate artifact와 candidate manifest로 run `33737405098`의 8/8
contract를 수용했으며, 그 exact evidence는 다른 feature head에 재사용하지 않는다.

### Candidate-to-Quality-Zoo acceptance (manual, not a release)

`.github/workflows/candidate-quality-zoo.yml` is an ici-hosted, `workflow_dispatch`-only path for
cross-repository candidate validation. Dispatch it from `refs/heads/main` only after the toy
`quality-zoo` commit contains the candidate expectations and the candidate artifact has been
independently recorded:

```bash
gh workflow run candidate-quality-zoo.yml --ref main \
  -f ici_target_sha=<40-lowercase-hex-ici-main-sha> \
  -f candidate_artifact_id=<positive-actions-artifact-id> \
  -f candidate_archive_sha256=<64-lowercase-hex-archive-sha256> \
  -f toy_target_sha=<40-lowercase-hex-toy-main-sha>
```

The workflow verifies the exact ici and toy `main` revisions, downloads the named candidate ZIP,
checks its raw archive digest, and rechecks the manifest's provenance against independently fetched
Actions run/check/job evidence. It runs candidate intake once as a no-credential preflight, fetches
the authenticated evidence separately, then runs the Quality Zoo runner with the verified local
`ici.pyz` path and requires `quality-zoo.suite/v1` with `contract_verdict: PASS` and no runner errors.
Candidate-controlled preflight and execution have GitHub publication/OIDC credentials unset; the
read-only Actions/Checks/Contents token is used only for artifact and evidence API reads. The result
is uploaded as a separate, uncompressed 14-day acceptance artifact containing preflight, intake,
GitHub evidence, and Quality Zoo results.

This path does not run `publish`, publish Pages, write or update a PR comment, or alter the stable
version/release. The existing released-artifact Q0 acceptance and its single sticky
`<!-- ici-report -->` comment remain the normal toy CI boundary. Each new candidate scope is
accepted only after a dispatch against its exact ici/toy commits and an independent evidence audit;
an older accepted candidate is not evidence for a newer feature head.
