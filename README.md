# ici — Integrated CI Engine

개발 환경(WSL/Linux)과 **사내 폐쇄망**(RHEL 8.10/CentOS, tcsh/bash), **GitHub Actions**에서 같은 정책·결과 계약으로 동작하는 C++/Python CI/CD 통합 검증·빌드 엔진입니다. OS·컴파일러·Python·검증 도구의 가용성과 버전은 실행 증거로 기록되며, 환경이 다르면 실제 결과도 달라질 수 있습니다.
단일 ZipApp 실행 파일(`ici.pyz`, 약 2MB) 하나로 배포됩니다.

```bash
$ ici verify --report --html verify_report.html --open
$ ici doctor
```

### 현재 릴리스와 진행 상태

현재 공개 stable 릴리스는 [v0.10.2](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)다.
`v0.10.2` tag는 exact `main` commit
[`3b50dd4c485ddab212beb23ff820e82286a06e77`](https://github.com/jihoon22-lee/ici/commit/3b50dd4c485ddab212beb23ff820e82286a06e77)을
가리킨다. [exact-main CI run `33541134010`](https://github.com/jihoon22-lee/ici/actions/runs/33541134010)은
Verify, Qt 5/Qt 6, `Publish Main Verification Report`, `Merge Gate`를 성공시켰고,
push에서 실행되지 않는 PR publisher는 expected `skipped`였다. [release run
`33541928666`](https://github.com/jihoon22-lee/ici/actions/runs/33541928666)의 provenance와
publish job도 모두 성공했으며, [공개 release](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)는
non-draft/non-prerelease와 정확히 9개 asset을 만족한다: `ici.pyz`, `ici.pyz.sha256`,
`ici-self-report.html`, `ici-self-report.json`, `viewer-report.html`, `viewer-report.json`,
`icirv`, `icirv-gui`, `icirv-gui.README.txt`. `ici.pyz`의 SHA-256은
`8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`다.
독립 main Pages audit도 ici/viewer 모두 HTTP 200·`text/html`·정확한 report title·외부
resource URL 0건으로 통과했다. 이 release evidence의 상세 표는
[`v0.10.2 public evidence workthrough`](docs/workthrough/2026-09-02-public-v0.10.2-evidence.md)와
[인수인계 current release evidence](docs/superpowers/2026-08-30-handover.md#v0102-public-release-boundary--current)에
보존한다.
I4-1의 exact compiler/clang-tidy replay에 이어 I4-2에서 Qt-aware clazy와
`moc`/`uic`/`rcc` generated-code linkage, Qt 5/Qt 6 compile evidence를 추가했습니다. 실제
clazy 1.11·Qt matrix·1,517개 테스트·self/viewer dogfood·Zero-CDN Pages가 PR과 exact main에서
통과했습니다. v0.10.1은 production `-Werror`가 clang-tidy/clazy finding을 도구 실패로
승격하지 않도록 경고 선택은 보존하면서 오류 승격만 낮춥니다. v0.10.2는 외부 Qt macro
preview와 CTest sanitizer evidence를 bounded하게 보존하고, clang-tidy/clazy가 compilation
database에서 선택한 GCC의 libstdc++를 정확히 재생하도록 보정합니다. 공개된 v0.10.2를
사용하는 BuildScope B0~B5 최종 검증과 공개 `buildscope-v0.5.0` release audit은 완료됐다.
남은 ici 범위는 I4-3/I4-4이며, 이전 릴리스 증거는 변경 이력과 실행 계획에 보존합니다.
병합된 sanitizer diagnostic normalization은 ASan/LSan/UBSan
출력을 deterministic `kind`/`defect`, bounded related stack-frame locations와 관측/프로젝트
frame count를 갖는 engine detail로 정규화하고, 검증 가능한 경우 project-owned primary
location과 native finding을 제공합니다. 호환 `rule_id`는 유지하면서 상세 sanitizer identity는
`tool_rule_id`로 제공합니다.
프로젝트 밖의 frame path는 `[external]`로 redacted되고 sanitizer process evidence와 연결됩니다.
CTest/QtTest의 raw transcript는 public message와 분리한 private `diagnostic_output`으로만
최대 65,536 UTF-8 bytes를 보존하며, CTest는 실행 전에 stale JUnit을 제거합니다. timeout,
process-output truncation, malformed/oversized diagnostic 또는 project location이 없는 진단은
clean result로 축약하지 않고 `ERROR`/`NOT_RUN`으로 닫습니다. PR #142와 exact-main run
`33705500603`, 별도 sanitizer candidate acceptance run `33710695336`까지 통과했습니다. 이
범위와 release 결정은 별개이며 공개 버전은 계속 `0.10.2`입니다.
병합된 ThreadSanitizer slice는 deep profile에서만 선택되는
`thread_sanitize` engine과 `ici thread-sanitize` direct command를 제공합니다. 별도
`BuildVariant.THREAD_SANITIZE`(`thread-sanitize`)와 `-tsan` shadow에
`-fsanitize=thread -fno-omit-frame-pointer -g`를 적용하고, CMake/qmake adapter와 generic
g++ 경로를 분리합니다. generic 경로는 `-pthread`를 추가하며 `TSAN_OPTIONS`의 기존 값을
보존한 채 `halt_on_error=1`을 덧붙입니다. Python은 unsupported이고 ASan/LSan/UBSan
`sanitize` variant와 절대 섞지 않습니다. `WARNING`/`SUMMARY` report signature만 bounded
normalizer에 넣고 known defect ID와 stable unknown fallback, bounded project location 및
`[external]` frame redaction을 적용합니다. PR #146과 exact-main run `33718399268`, toy PR #56,
candidate acceptance run `33737405098`의 8/8 contract까지 통과했습니다. 이 완료 범위는 TSan
sub-scope이며 broader resource/lifetime/security taxonomy, I4-4 전체와 release는 pending입니다.
공개 버전은 계속 `0.10.2`입니다.
이번 bounded language-aware slice는 `dup`의 lexical token/region/signal 정확도와
fail-closed resource budget을 보강하고, `[engines.dup].python_semantic = "auto" | "required" | "off"`
(기본값 `auto`)로 Python 3.10 AST-shape clone group을 추가합니다. 이름이 다른 local binding과
물리적 줄 배치는 정규화하지만 control flow·operator·literal·source-spelled imported-name/
attribute anchor는 보존하며,
같은 canonical shape일 때만 group을 만듭니다. 이 구조 분석은 계속 `ESTIMATED`이고 behavioral
equivalence를 주장하지 않습니다. `dead`에는 좁은 compiler-backed C/C++ translation-unit
unused-function slice와 별도로 `[engines.dead].cpp_linker = "auto" | "required" | "off"`
(기본값 `off`)인 Linux GNU ELF target-local discarded-function evidence를 추가했습니다.
이는 root CMake/Unix Makefiles/Release의 direct-object executable link에서 `cmake`, `readelf`,
`addr2line`과 GCC GNU ld의 section-GC 증거를 사용할 때만 `MEASURED`/`EXACT`가 됩니다.
whole-program/dynamic dead-symbol reachability, archives/shared/LTO/PIE/COMDAT 범위와 C++
semantic/behavioral duplicate equivalence는 여전히 지원 범위가 아닙니다. 버전은 `0.10.2`로
유지하고 이 feature PR만으로 release를 만들거나 승인하지 않습니다.

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

### 릴리스 정책

- `feature`·`test`·`refactor`·`docs` PR은 버전 변경이나 stable release를 자동으로 만들지 않습니다.
- `patch`는 이미 공개된 stable artifact의 defect·security·compatibility 수정에만 사용합니다.
- `minor`는 사용자에게 보이는 응집된 roadmap checkpoint이며, ici 전체 gate·실제 도구 E2E·candidate cross-repo/toy 검증·PR/main CI·Pages·문서/CHANGELOG가 모두 끝난 뒤에만 정합니다.
- pre-release/candidate artifact는 stable이 아니며, 하나의 PR이 하나의 릴리스를 뜻하지 않습니다. `v0.10.1`과 공개된 `v0.10.2`는 공개 결함에 한정한 corrective stabilization이고, 다음 minor는 I4-3/I4-4와 real toy-projects/quality-zoo 검증 이후로 미룹니다.

---

## 📚 문서 허브 (Documentation Hub)

| 문서 | 설명 | 바로가기 |
|---|---|---|
| **🚀 사용자 가이드** | 빠른 시작, 설치, 전체 CLI 사용법 및 IDE 원클릭 점프 | [docs/user-guide.md](docs/user-guide.md) |
| **📏 검증 엔진 레퍼런스** | 16종 품질 검증 엔진 (fast 12 / standard 14 / deep 16), TEM 스코어링 공식, `ici.toml` 정책 설정 | [docs/engine-reference.md](docs/engine-reference.md) |
| **⚙️ CI/CD 연동 가이드** | GitHub Actions, Step Summary, PR 어노테이션, 사내 폐쇄망 러너 | [docs/ci-integration.md](docs/ci-integration.md) |
| **🏛️ 시스템 아키텍처** | ZipApp 패키징, Polyglot 런처, 오케스트레이터 및 리포터 계층 설계 | [docs/architecture.md](docs/architecture.md) |
| **🧭 품질 분석기 실행 계획** | Python·C++·Qt 분석기 로드맵과 toy-projects 교차 검증 순서 | [ici 마스터 계획](docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md) · [toy-projects 마스터 계획](https://github.com/jihoon22-lee/toy-projects/blob/main/docs/superpowers/plans/2026-08-30-product-portfolio-master-plan.md) |
| **📋 변경 이력 (CHANGELOG)** | 버전별 상세 릴리스 노트 및 마일스톤 | [CHANGELOG.md](CHANGELOG.md) |
| **📜 개발 및 기여 규약** | 브랜칭 전략, 커밋 룰, 런타임 제약 및 불변식 | [AGENTS.md](AGENTS.md) |

---

## 🚀 핵심 특징

1. **단일 ZipApp 배포 (`ici.pyz`)**:
   - 가상환경 설치나 `pip` 없이 실행 파일 하나만 복사(`~/.local/bin/ici` 또는 `nas_shared/bin/ici`)하면 끝.
   - 최초 실행 시 `~/.config/ici/ici.toml`에 전사 기본 정책이 자동 생성되며, `src` 외 `lib`/`app` 등 소스 레이아웃도 자동 탐색
2. **스마트 런처 (Smart Polyglot)**:
   - 시스템 기본 `python3`가 3.6/3.8인 구버전 환경에서도 `ICI_PYTHON` 또는 3.10+ 설치 경로를 스스로 찾아 실행.
3. **16종 품질 검증 엔진 (fast 12 / standard 14 / deep 16)**:
    - `line`: 파일당 순수 코드 500줄 초과 경고, 1000줄 초과 실패 + **계층형 디렉토리 트리 뷰** (`project.source_dirs` + 기본 소스 디렉터리 전용 스캔, `include_dirs`로 확장)
    - `lint`: Python Ruff 및 C/C++ compiler 진단, optional clang-tidy I4-1와 Qt-aware clazy I4-2
      adapter (`auto`/`required`/`off`, exact compilation-context replay, 도구 미설치·부분 폴백
      증거 포함). clazy는 standalone/wrapper provider, level0/level1 profile, Q_OBJECT·signal/slot·
      lifetime·container·Qt compatibility 진단을 보존합니다.
    - `compile_db`: C/C++ production translation unit coverage, 실제 compiler flag/search path와 stale build context 검증
      - root CMake 프로젝트에 DB가 없으면 `build/ici-cmake-build`에서 Release·`CMAKE_EXPORT_COMPILE_COMMANDS=ON`·unity OFF로 canonical DB를 생성합니다. `Ninja` 또는 `*Makefiles` 단일 구성만 exact context로 인정하고, generated source는 필요한 경우 한 번 build한 뒤 DB를 다시 읽습니다.
      - report/cache에는 DB origin·generator·unity 상태·CMake target과 digest가 남으며, subdirectory output 경로도 working directory와 DB 기준을 일치할 때만 안전하게 보정합니다.
     - `test` & `tem`: 단위 테스트 전수 통과 + Line/Branch/Function 커버리지 및 PassRate 기반 **TEM 5.0 스코어링** (`min(Line,80)/80 * Func/100 * PassRate *5`, Branch는 `*5/4` 보정; 모듈별 실측: Python `coverage.py` / C++ `gcov`)
    - `type`: Mypy 정적 타입 검사 및 AST 부분 폴백 (C++ 타입 검증은 명시적 SKIP)
    - `python_compat`: 현재 실행 중인 Python을 기본 필수 runtime으로 확인하고, 설정한
      interpreter의 `-VV`·`compileall`·선택적 import smoke를 실행합니다. `requires-python`과
      설정된 syntax/API floor를 위치와 함께 검사하며, import는 모듈 top-level code를 실행하므로
      `[engines.python_compat].imports`에 명시한 모듈만 opt-in합니다. runtime 호출 증거는
      `MEASURED`/정확한 `ToolEvidence`로 남고, 외부 interpreter 경로가 바뀔 수 있어 결과 cache는
      사용하지 않습니다.
    - `complexity`: Python AST와 exact context/tool이 있을 때 C++ clang-tidy
      `readability-function-size`로 함수 경계를 정하고, 경계 내부 CC/중첩은 masked token/brace
      metric으로 계산 + **원본 소스 코드 블록 프리뷰**
    - `sanitize`: C++ ASan/LSan/UBSan 구조화 진단을 포함한 메모리 안전성 및 Python 리소스 누수 검증
    - `thread_sanitize`: deep profile 전용 C++ ThreadSanitizer 실행과 bounded thread-safety 진단
    - `dead`: 죽은 코드, 도달 불가능 코드, 미사용 심볼 검출. 공통 bounded UTF-8 source intake를 사용하며 generated/vendor는 기본 제외(`include_generated`/`include_vendor` literal-boolean opt-in)합니다. Python AST reachability/name-reference는 `ESTIMATED`/heuristic이고, 승인된 GCC/Clang(및 그 capability-approved alias)이 선택된 owned C/C++ translation unit에 귀속한 internal-linkage 함수 `-Wunused-function` 진단은 `[engines.dead].cpp_unused = "auto" | "required" | "off"` 정책으로 재생할 수 있습니다. 별도 `[engines.dead].cpp_linker = "auto" | "required" | "off"`(기본값 `off`)는 Linux root CMake/Unix Makefiles/Release direct-object ELF executable에서 GCC GNU ld가 버린 uniquely mapped local/hidden function section을 `cmake`/`readelf`/`addr2line`으로 확인합니다. linker finding은 target-local `MEASURED`/`EXACT`이며 whole-program 주장이 아닙니다. 모든 알려진 configuration에서 같은 위치 범위의 compiler 진단이 확인된 경우에만 C++ 결과를 `MEASURED`/`EXACT`로 기록하며, intake는 8,192개 unique candidate와 2,048개 owned/analyzed 파일·파일당 8 MiB·aggregate 64 MiB로 제한하고, 제외된 파일은 owned 한도에 포함하지 않습니다.
    - `dup`: **Type-2 클론 검출** (변수명/리터럴만 다른 복사-붙여넣기도 감지) + 최대 클론 병합 및 원본 인덴트 보존 중복률 산출. Python/C/C++는 전용 line-preserving lexer로 정규화해 언어별로 격리한다. Python `tokenize`/AST context는 주석·multiline import와 `match`/`case` soft keyword를 처리하고 identifier, 숫자·문자열 계열, 들여쓰기·연산자 category를 보존하며, `python_semantic = "auto"` 기본 정책은 leaf function/method의 bounded AST shape도 canonicalize한다. local alpha renaming과 layout insensitivity를 적용하되 control flow·operator·literal·source-spelled imported-name/attribute anchor를 보존하고 `sha256/semantic-shape-v1`가 exact하게 같은 경우만 semantic-shape group을 보고한다. C/C++ lexer는 comments/directives를 제거하고 C++ backslash-newline splice의 physical line을 보존하며 punctuator, literal, UDL과 Qt anchor를 구분한다. normalized-window seed의 exact token verification과 function/class/import/directive region, semantic-signal policy를 통해 값만 다른 data table은 억제하고 실제 control-flow clone은 유지한다. lexical fingerprint는 `sha256/type2-region-v2`, AST-shape fingerprint는 `sha256/semantic-shape-v1`로 기록하지만 두 경로 모두 compiler/linker 실측이 아니므로 결과는 `ESTIMATED`/heuristic이며 behavioral equivalence를 뜻하지 않는다. lexical tokenizer/matching budget은 엔진 전체를 `ERROR`/`NOT_RUN`으로 닫고, AST-shape budget은 semantic partial을 버린 뒤 `auto`에서는 lexical 결과만 유지하며 `required`에서는 `ERROR`/`NOT_RUN`으로 닫는다. generated/moc/vendor는 기본 제외하고 owned C/C++ header도 검사하며, standalone `.moc`는 `include_generated = true`일 때만 포함한다.
    - `exception`: 예외 삼킴(`except: pass`), Traceback 유실, 소멸자 throw 차단
    - `cycle`: Python import / C++ include **순환 참조 탐지** (Tarjan SCC, C++ path-suffix 해석의 미해결·모호 위치도 보고)
    - `security`: 하드코딩 시크릿, 약한 해시, `eval`/`pickle`/`shell=True` 등 위험 패턴 탐지
    - `resource`: 파일·네트워크 리소스 누수 AST 패턴 검출
    - `cognitive`: SonarQube S3776 스타일 **인지 복잡도** (기본 비활성, 옵트인)
4. **기본 10개(기준선 비교 시 11개) 전용 탭 인터랙티브 Zero-CDN HTML 대시보드 (`--html`)**:
     - `📋 Verification Suites`: 종합 품질 게이지, TEM 스코어, 전체 엔진 상태 및 전용 탭 점프 버튼 (N/A 엔진은 회색 접힘 행 표시)
     - `🧭 Support & Capabilities`: 발견된 언어·Qt scope와 엔진별 지원 mode, 실행 증거, 도구, fallback 및 알려진 한계를 issues-first 접힘 행으로 표시
     - `🧭 Baseline Delta`(비교 시): new·unchanged·moved·resolved 및 regression/gate를 issues-first로 표시
     - `📊 Line Analysis & Explorer`: 소스 스코프 기본 표시 + **All-files 토글로 전체 프로젝트 라인 탐색** + 계층형 파일 트리 + 실시간 검색 + 코드 분포 차트
     - `🧪 Tests & Coverage`: 4대 커버리지 KPI 게이지 + **모듈별 실측 커버리지 테이블 (Module Coverage Table)** + 파일별 테스트 스위트 & 개별 테스트 케이스 상세 뷰
     - `🏷️ Static Types`: 실제 타입 finding과 검사되지 않은 파일을 분리한 정적 타입 뷰
     - `🧩 Complexity`: 순환 복잡도 리더보드 + 🧠 인지 복잡도 통합 뷰 + 토글 코드 블록
     - `📦 Clone Groups`: 연결 컴포넌트 클러스터링 기반 중복 코드 카드 + 원본 들여쓰기 보존 코드 블록
     - `🔁 Cycles`: 순환 참조 체인을 **칩(chip) 시각화**로 표시 + 전체 경로 접기
     - `🔐 Security & Resources`: security/resource 발견 사항 카드 뷰
     - `⚠️ Issues`: 전체 조치 필요(WARN/FAIL/ERROR/SKIP) 항목 통합 뷰 + **접고 펼칠 수 있는 문제 코드 스니펫**
5. **전체 파일·라인 원클릭 점프 네비게이션**:
   - **로컬 터미널**: Rich가 안전한 `file://` 링크를 출력하여 지원하는 터미널에서 파일 위치로 이동
   - **GitHub Actions**: `$GITHUB_STEP_SUMMARY`, 아티팩트 및 인라인 에러 어노테이션에 GitHub Permalink 제공. 검증(`verify`) job 자체는 `contents: read`만 사용하며 댓글을 작성하지 않고, 별도의 `report-pr` job이 업로드된 리포트만으로 sticky PR 댓글을 갱신
   - **`--publish`**: 권한을 명시적으로 부여한 신뢰된 실행(예: `main` push)에서 인터랙티브 HTML 리포트를 `gh-pages`에 배포하는 선택 기능. 신규 CLI `ici publish --html --json`으로 기존 리포트를 단독 게시 가능
   - **단일 HTML 리포터**: 브라우저에서 로컬 파일·선호 에디터 링크를 선택해 이동
6. **안정적인 `ici.result/v3` 품질 데이터 계약**:
   - 기존 위치 inventory인 `targets`와 함께 rule/category/severity/confidence, 관련 위치, 개선안, suppression, 단위 metric을 갖춘 `findings`를 제공합니다.
   - project-relative 위치와 symbol/region으로 만든 fingerprint는 checkout 경로와 Windows/Linux separator가 달라도 동일합니다.
   - v2 리포트 migration과 viewer 하위 호환을 제공하며, JSON Schema는 [`src/ici/schemas/ici-result-v3.schema.json`](src/ici/schemas/ici-result-v3.schema.json)에 있습니다.
   - 모든 출력 형식은 공통 redaction 경계를 거쳐 engine message·snippet·도구 argv/output·remediation·metric과 파일 경로에 섞인 credential을 마스킹합니다. 일반 경로는 그대로 유지됩니다.
   - `--write-baseline`으로 현재 finding inventory를 보관하고 `--baseline`으로 다음 실행과 비교할 수 있습니다. `--fail-on-new`는 새 actionable finding 또는 severity/suppression regression만 gate에 반영합니다.
   - 기준선 비교 결과는 JSON·HTML·Markdown Summary·콘솔에 표시되고, 신뢰된 publish job의 sticky PR 댓글에는 새 finding·regression·gate·호환성 warning 요약이 포함됩니다.
7. **과장 없는 언어·도구 지원 매트릭스**:
   - 16개 엔진 × Python/C++ 범위를 `exact`/`heuristic`/`tool-backed`/`unsupported`로 선언하고 Qt 호환성, 필요 도구, fallback과 한계를 함께 공개합니다.
   - 프로젝트별 적용 여부와 실제 증거 상태를 계산해 doctor, JSON, HTML과 Qt viewer에서 같은 데이터로 표시합니다. 상세 표는 [엔진 레퍼런스 §1.4](docs/engine-reference.md#14-엔진-지원기능-매트릭스)를 참고하세요.
   - `ici doctor`는 전체 tool registry를 한 번의 bounded probe snapshot으로 수집하고, 필요한 이유(`engine:language` 또는 `doctor.config`)와 missing/incomplete 상태를 함께 보여 줍니다. `ici doctor --json`의 `capability_inventory`는 status·counts·version/path/details/evidence를 담는 machine-readable 계약이며, 기존 `tools` map도 유지합니다.
   - `ici verify`도 유효한 support matrix의 `applicable`·`enabled` 범위와 `doctor.config`에서 required/optional 정책을 계산한 뒤, 엔진 실행 전에 같은 registry를 정확히 한 번 수집합니다. suite root의 선택적 `capability_inventory`를 console/Markdown/zero-CDN HTML reporter가 그대로 공유하므로 reporter가 도구를 재탐지하지 않습니다. required provenance 우선 규칙과 모든 provenance, capability 메타데이터·probe argv/evidence redaction을 보존하며, 콘솔은 요약하고 Markdown은 전체 inventory를 접어 보여 주고 HTML은 Support & Capabilities 탭에 전체 행을 표시합니다. 기존 inventory 없는 `ici.result/v3` 리포트도 계속 읽을 수 있습니다.
8. **사용자 로컬 분석 캐시**:
   - `ici verify`는 프로젝트 루트·소스/빌드 설정 내용·effective ici 설정·toolchain 버전·컴파일 DB digest/parse state·엔진 구현·build variant·ici 버전을 포함한 `ici.analysis-cache-key/v3`로 완료된 엔진 결과를 재사용합니다. compilation database digest는 preflight가 immutable context로 캡처한 snapshot을 식별하는 값이며 live-file lease가 아닙니다. DB가 변경되면 다음 preflight에서 새 digest와 context를 캡처합니다. 엔진 구현 identity에는 engine class source digest와 `CACHE_IMPLEMENTATION_MODULES`로 명시적으로 선언한 helper/dependency module source digest 목록이 포함되며, C++ lint는 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._clang_tidy`, `ici.engines._clazy`, `ici.engines._cpp_diagnostic_categories`, `ici.engines._cpp_diagnostics`, `ici.engines._cpp_lint`, `ici.engines._cpp_tooling`, `ici.engines._qt_codegen`, `ici.engines.lint`를, cycle은 `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_include_graph`, `ici.engines._cpp_include_trace`, `ici.engines.cycle`을, complexity는 `ici.core._compile_db_paths`, `ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_function_boundaries`, `ici.engines._cpp_tooling`, `ici.engines.cpp_text`를 명시합니다. `.ui`/`.qrc`도 선언된 source suffix로 digest되며, 기본 위치는 `~/.cache/ici/analysis/`이고 remote/shared cache는 사용하지 않습니다.
   - 완전한 `PASS`/`WARN`/`FAIL`은 저장할 수 있지만 `ERROR`/`SKIP`/`NOT_RUN`, timeout·truncation·tool error 및 invalid artifact는 저장하지 않습니다. 단, `dead` 엔진은 C++ compiler probe의 외부/generated include closure와 compiler binary content가 cache v3에 완전히 표현되지 않으므로 exact 결과 재사용을 항상 비활성화합니다. `dead`는 cache key/entry를 만들지 않고 매번 새로 실행되며, hybrid/Python-only 실행도 같은 경계를 따릅니다. `--no-cache`, `ici cache`, `ici cache --clear`로 실행별 비활성화·inventory·정리를 제어합니다.
   - v3 engine JSON의 optional `cache_hit`/nullable `cache_key`는 기존 archive 소비자와 호환되며, 캐시는 프로젝트 소스를 변경하지 않고 atomic local entry만 씁니다. 새 entry는 0700/0600 권한 경계를 사용하고, symlink·duplicate key·NaN/Infinity·32 MiB 초과 payload를 거부합니다.

---

### Python runtime compatibility

`python_compat`은 Python source가 있는 프로젝트에서 현재 runtime과 선택한 추가 runtime이
프로젝트 metadata·source floor와 함께 동작하는지 확인합니다. 기본 설정은 다음과 같습니다.

```toml
[engines.python_compat]
enabled = true
mode = "pass_warn_fail"
required = false             # engine gate policy; runtime selection is described below
interpreters = []            # empty: the interpreter running ici, required
required_interpreters = []   # entries from interpreters that must satisfy the checks
imports = []                 # explicit smoke-import opt-in; never auto-executed
target_version = ""          # empty: infer the earliest minor allowed by requires-python
```

`interpreters`가 비어 있으면 `sys.executable` 하나를 선택하고 해당 runtime은 항상 required로
취급합니다. 목록을 지정하면 각 entry는 optional이며, 같은 문자열을 `required_interpreters`에도
넣은 entry만 unavailable/incompatible일 때 필수 결과가 됩니다. optional runtime의 unavailable
또는 check failure는 `WARN`이고, required runtime의 unavailable은 `ERROR`/`NOT_RUN`,
불일치·실패는 `FAIL`입니다. `required_interpreters`는 `interpreters`의 부분집합이어야 합니다.

각 resolved interpreter는 shell 없이 `-VV`로 version을 확인하고, 선택된 Python source root를
`python -B -m compileall -q -f`로 강제 재컴파일합니다. compileall bytecode는 임시 cache prefix로 보내
프로젝트에 `__pycache__`를 만들지 않습니다. import smoke는 module top-level code를 실행할 수
있으므로 자동 발견한 import 이름은 metadata로만 남기고 실행하지 않으며,
`imports = ["package", "package.cli"]`처럼 사용자가 명시한 모듈만 `-I`/`-B` 격리 subprocess에서
실행합니다.

`pyproject.toml`의 `project.requires-python`은 PEP 440 specifier로 파싱해 실제 `-VV` version과
비교합니다. `target_version`을 지정하면 그 Python minor를 syntax/API floor로 사용하고, 비워
두면 `requires-python`이 허용하는 가장 이른 지원 minor를 추론합니다. 현재 runtime이 파싱할 수
있는 범위에서 AST syntax floor와 문서화된 standard-library API inventory를 검사하며, 위반은
정확한 1-indexed line/column target으로 보고합니다. metadata가 없으면 runtime/compile 검사는
계속하되 inferred source floor는 적용하지 않습니다.

정상 실행의 engine evidence는 `MEASURED`이며, 각 `-VV`·compileall·import smoke 호출은
executable path, version, argv, return code, timeout/truncation을 가진 `ToolEvidence`로 보존됩니다.
외부 interpreter를 설정으로 교체할 수 있어 일반 engine cache identity만으로 실행 capability를
안전하게 고정할 수 없으므로 `python_compat` 결과는 cache key를 만들거나 재사용하지 않고 매번
새로 실행합니다.

### Python finding display projection

console·HTML·Markdown은 원인 중복을 줄이기 위해 보수적인 canonical Python rule projection을
공유합니다. registry에 review된 rule family만 canonical id로 표시하고, broad Ruff rule은
AST-derived semantic context가 같은 경우에만 내부 rule과 합칩니다. 두 finding이 같은 project
path에서 **end line과 start/end column을 모두 가진 1-indexed precise region**으로 서로 겹쳐야
하며, line-only 위치·인접하지만 겹치지 않는 위치·서로 다른 path·알 수 없는 rule은 합치지
않습니다. 그룹에는 `original_finding_count`, producer별 count와 원래 engine/rule/tool version
provenance가 표시됩니다.

이것은 표시 전용 projection입니다. JSON `findings`/`targets`와 baseline inventory/delta는 각
producer의 원본 finding, fingerprint, precise line/column, tool identity를 그대로 보존하며,
projection이 suite나 baseline을 변경하지 않습니다. console은 cap을 적용하고 Markdown은
bounded table을 사용하지만 HTML은 전체 display projection을 유지합니다.

### C++ compiler/clang-tidy

C++ `lint`는 측정된 immutable `CompilationContext`의 normalized translation-unit command를
재생해 approved compiler와 optional clang-tidy를 실행합니다. compile database를 직접 다시 읽거나
`-p`를 사용하지 않으며, `--fix` 없이 source/context를 read-only로 다룹니다. clang-tidy는
`clang_tidy = "auto"`(없으면 `WARN`), `"required"`(없으면 `ERROR`), `"off"`(미실행)을
지원합니다. `clang_tidy_checks = ["-*", "bugprone-*", "performance-*"]`처럼 check glob을
별도 목록 항목으로 적으면 지정 목록이 config/default보다 우선합니다.

config 우선순위는 명시한 `clang_tidy_config`, source에서 project root까지의 가장 가까운
`.clang-tidy`, built-in defaults 순서이며, parent-of-project config는 찾지 않습니다. config가
없으면 `--config={}`로 암묵적인 parent lookup을 막고, `ExtraArgs`/`ExtraArgsBefore`와
`InheritParentConfig`, project 밖 config와 symlink 탈출은 거부합니다. 등록된 `gcc`/`g++` probe는
`--version` banner에서 GCC 또는 Clang family를 확인한 경우에만 capability를 complete로
인정합니다. 완전하게 probe된 실제 GCC 9+ compiler는 JSON diagnostics를, older GCC와 approved
Clang-family driver/alias는 bounded text fix-it 형식을 사용합니다. 중립 이름이나 Apple alias도
실행 파일 spelling이 아니라 기록된 family를 따르며, family/version을 확정하지 못한 capability는 replay 전에 거부하고,
malformed 결과는 atomic error로 처리합니다. clang-tidy의 rule-less 또는 primary와 같은 rule을 가진 `note:`는
출력 순서상 바로 앞의 primary와 같은 contiguous group의 `related_diagnostics`로 결합되고,
다음 primary가 새 group을 시작합니다. orphan/conflicting-rule note는 atomic error이며,
lint의 target/finding/count에는 primary만 포함됩니다. 관련 위치·메시지는
`Finding.related_locations`로, note fix-it은 primary remediation과 `extra` metadata로
보존되고, canonical related-location 순서는 path·region·label 기준으로 결정됩니다. JSON/HTML은
전체 related evidence를 보존하며 GitHub Markdown은 engine당 100개로 bounded하게 표시합니다.
C++ diagnostic category projection은 isolated `_cpp_diagnostic_categories.py`의
`tool-rule-v1`로 고정하며 free-form message가 아니라 normalized `family`와 `tool_rule_id`만
사용합니다. recognized family별 ordered security/resource/compatibility/correctness rules 뒤에
보수적인 fallback을 적용하고, clazy stem은 exact stem 또는 `-`/`.` child만 인정합니다. 결과
`extra`에는 policy ID와 모든 category count가 함께 기록되며, helper source는 lint cache
identity에 포함됩니다. 정확한 precedence와 rule 목록은
[사용자 가이드](docs/user-guide.md#c-diagnostic-category-policy)가 canonical reference입니다.
관련 focused C++ lint/tidy/clazy set은 `160 passed`, cache identity/store set은 `51 passed`인
local evidence입니다.
compiler와 clang-tidy adapter는 각각 최대 2,048 units, unit당 120초, 전체 600초 global budget을 적용하며
초과분은 실행하지 않고 `ERROR`/`NOT_RUN`으로 기록합니다. 자세한 설정과
evidence 계약은 [사용자 가이드](docs/user-guide.md#c-clang-tidy-정책)와
[엔진 레퍼런스](docs/engine-reference.md#22--lint-문법-및-코드-스타일-린터)를 참고하세요.

### Qt clazy·생성 단계 분석

Qt 분석은 I3에서 loader가 고정한 immutable `AnalysisContext`를 그대로 소비합니다. capability
registry의 canonical `clazy` probe는 `clazy-standalone`을 먼저 찾고, 배포판 wrapper인 `clazy`를
두 번째 provider로 인식합니다. `[engines.lint]`의 `clazy = "auto"`는 선택적 실행,
`"required"`는 도구·context 부재를 오류로 승격하고, `"off"`는 실행하지 않습니다.
`clazy_profile`은 global `ici.profile`과 독립적인 명시적 `level0`(기본) 또는 `level1`이며,
level2·manual noisy check는 bounded `clazy_checks` 목록을 명시했을 때만 선택합니다.

```toml
[engines.lint]
clazy = "auto"             # auto | required | off
clazy_profile = "level0"   # level0 | level1; global ici.profile과 독립
# level2 또는 특정 noisy check를 의도적으로 선택할 때만 사용
# clazy_checks = ["qdatetime-utc", "qcolor-from-literal"]
```

standalone command는 approved executable에 `--checks`, `--only-qt`, 원본 source, `--` 뒤의
sanitized compiler arguments를 전달하고, wrapper는 approved `clang++`를 `CLANGXX`로 고정한
replacement environment와 `CLAZY_CHECKS`를 사용합니다. 두 경로 모두 exact context에서만
covered production unit을 replay하며 compilation database를 다시 읽거나 `-p`, `--fix`, shell을
사용하지 않습니다. stdout/stderr는 strict bounded clazy text parser가 일반 compiler warning도
형식 검증 후 중복 보고 없이 제외하고, `-Wclazy-*` rule ID와
child/note 위치를 보존해 `family = "clazy"`, `ToolEvidence`, project-relative target으로
정규화합니다. `tool-rule-v1`은 normalized rule이 exact stable rule이거나 bounded stem 자체,
또는 `-`/`.`로 이어지는 child일 때만 매칭합니다. resource는
`clazy-lifetime`/`clazy-ownership`/`clazy-parent-less`/`clazy-qobject-cast` bounded stem과
`clazy-connect-3arg-lambda`, `clazy-ctor-missing-parent-argument`,
`clazy-lambda-in-connect`, `clazy-post-event`, `clazy-returning-data-from-temporary`,
`clazy-temporary-iterator` exact rules, compatibility는 `clazy-qt6`/`clazy-deprecated`/
`clazy-qstring-arg`/`clazy-qt-keyword` bounded stem과 `clazy-modernize-overloaded-connects`,
`clazy-no-module-include`, `clazy-old-style-connect`, `clazy-qenums`, `clazy-qstring-ref`,
`clazy-use-chrono-in-qtimer` exact rules, correctness는 `clazy-qobject`/`clazy-connect`/
`clazy-signal`/`clazy-slot`/`clazy-qevent-cast` bounded stem과 stable correctness exact rules로
매핑합니다. Resource row가
correctness row보다 우선하며, arbitrary substring만 포함한 rule과 미매칭 rule은
`MAINTAINABILITY`로 안전하게 귀결됩니다. fix-it은 자동 적용하지 않습니다.

clazy adapter는 최대 2,048 translation units, unit당 120초, 전체 600초 global budget과
1,000,000자 output bound를 공유합니다. context/coverage/replay/parse/process 오류와 timeout,
truncation, budget 초과는 heuristic으로 숨기지 않고 `ERROR`/`NOT_RUN`으로 fail-closed합니다.
특히 clazy process가 nonzero로 끝나면 warning처럼 보이는 출력이어도 원자적인 `ERROR`로
닫고, partial diagnostics를 남기지 않습니다. 이때 `ToolEvidence.error`에는 bounded exit code,
`fatal`/`error`/`warning`/`note`/`remark` kind count와 processing/output flag만 남으며 raw
tool prose와 host path는 복사하지 않습니다.

Ubuntu의 legacy raw-source/caret/replacement context를 검증할 때는 exact sanitized compiler
argv에서 얻은 approved include root만 source read 권한으로 추가합니다. project 밖 root도
검증은 가능하지만 결과 위치는 항상 `[external]`로 export됩니다. 각 root는 bounded directory
목록(최대 512개)이어야 하며, source line은 `O_NOFOLLOW` regular-file descriptor로 열고
read 전후 device/inode/size/mtime identity를 확인합니다. source-context 누적 바이트는
1,000,000 byte, 한 줄은 8,192 characters를 넘을 수 없고, root/regular-file/identity 불일치,
symlink, source mismatch, forged/extra preview 또는 budget 초과는 partial finding 없이
atomic `ERROR`가 됩니다.

Clang 기반 clang-tidy/clazy가 exact compilation context의 선택 GCC를 사용할 때는 최신 설치
libstdc++를 임의로 집지 않도록 별도 표준 라이브러리 projection을 먼저 수행합니다. capability가
승인한 `g++`와 replay compiler의 resolved file identity가 같은지 확인하고, 해당 GCC를 `c++`와
`c`로 각각 한 번씩 bounded probe한 뒤 C++ search 결과에서 C search 결과를 뺍니다. 남은 경로를
compiler가 보고한 순서 그대로 `-nostdinc++`와 `-isystem <root>` 쌍으로 clazy와 clang-tidy에
전달합니다. probe는 sanitized `-m*`/sysroot selector만 보존합니다. compiler identity가 다르면
projection 대상이 아니며, compiler와 working-directory identity를 projection cache key와 probe
전후 검증에 함께 묶습니다. identity가 일치한 GCC의 output/timeout/parse/unresolved 오류는
analyzer를 실행하기 전에 fail-closed합니다. 두 probe는 `ToolEvidence`로 기록됩니다.

같은 lint 단계에서 source scope의 `.ui`, `.qrc`, `Q_OBJECT` 선언을 찾아 exact compilation
database와 연결합니다. `ui_<stem>.h`가 bounded project include traversal로 exact translation
unit에 연결되는지, `qrc_<stem>.cpp`가 generated
compilation unit으로 들어가는지, `moc_<stem>.cpp`·`<stem>.moc`·`mocs_compilation.cpp`가
Q_OBJECT를 연결하는지를 원본 입력 파일·라인 target에 기록합니다. exact context의 include,
define, compiler replay로 Qt 5/Qt 6 major를 식별합니다. linkage와 compatibility 모두 성공한
compiler replay가 있어야 `PASS`이며, 식별 불가·replay 미실행·중복 generated stem은 `WARN`입니다.
I4-2 기준 PR/main은 actual tool을 포함한 1,517개 테스트와 Qt matrix를 통과했고 v0.10.1의
release provenance·9개 artifact 감사도 완료됐습니다. v0.10.1 corrective gate는
1,526 passed / 4 environment skips였으며, CI/release workflow는 실제 clazy·Qt fixture가 skip되지
않도록 `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`과 clazy 설치를 강제합니다. 이번 evidence correction의
Python 3.10 local run은 focused C++/CTest 회귀 161 passed, full suite 1,538 passed / 4 skipped다.
정확한 Ubuntu 24.04 + Qt 5 + clazy 1.11 run은 full lint 12/12, approved external macro note
1건, unsuppressed CTest 8의 9 cases와 LeakSanitizer diagnostic을 기록했다. suppression을 넣어
확인한 작업은 toy repository에 한정되며 ici policy로 해석하지 않는다. 이번 correction의 ici
PR/정확한 main gate와 공개 v0.10.2 release evidence는 완료됐다. 공개 v0.10.2를 사용하는
BuildScope B0~B5 최종 검증, exact-main/Pages 확인, `buildscope-v0.5.0` release와 asset audit도
완료됐다. 남은 delivery 범위는 I4-3/I4-4입니다.

테스트 실행 상태는 수집 상태와 분리됩니다. CTest/QtTest와 pytest의 skip은 실패로 세지 않지만
실행 증거로도 세지 않으며, pytest `XFAIL`은 실행된 예상 실패이자 PASS, `XPASS`는 실행된
실패로 정규화합니다. 수집된 Python 또는 C++ 테스트가 전부 skip이면 필수 `test` 엔진은
`ERROR`/`NOT_RUN`, 선택 엔진은 `SKIP`/`ESTIMATED`이고, 이때 생성된 coverage만으로 실행을
증명하지 않습니다. `sanitize`의 필수 C++ scope는 한 case라도 미실행이면 fail-closed합니다.
버전은 계속 `0.10.2`이며 이 보정만으로 release하지 않습니다.

다중 GCC 회귀를 재현한 Ubuntu 24.04에서는 GCC 13/14가 함께 설치된 상태에서 toy-projects PR #38
run `33531285208`의 Qt 5/Qt 6 deep clazy가 실패했습니다. fixed local `dist/ici.pyz`는
`/usr/include/c++/13`, `/usr/include/x86_64-linux-gnu/c++/13`, `/usr/include/c++/13/backward`를
projection하고 2 probes 뒤 12 sources에서 clazy exit 0을 기록했으며, expected warnings도
보존했습니다. 이 경로의 표준 라이브러리 선택은 compile database의 GCC identity에 종속됩니다.

### C/C++ compiler-backed unused internal functions

`dead`의 C/C++ 경로는 shared immutable `AnalysisContext`의 compilation database가 정확히 덮는
모든 owned project C/C++ source translation unit을 대상으로 합니다. `project.cpp_external_build_dirs`로
지정한 external build directory 안의 owned source도 immutable exact database가 해당 unit의 모든
알려진 configuration을 덮으면 포함하며, build/link engine의 self-link 제외 정책은 이 source scan에
적용하지 않습니다. 각 selected `CompilationUnit`의 explicit `language`는 `c` 또는 `c++`여야 하며,
다른 값이나 빈 값은 compiler를 실행하기 전에 거부합니다. 각 configuration에는 canonical `sha256:`
identity가 있어야 합니다. context의 `unity_build=true`가 명시된 경우에는 source ownership을
증명할 수 없어 `ERROR`/`NOT_RUN`으로 닫지만, `false` 또는 `null` 자체를 거부하지는 않습니다.
`cpp_unused = "auto"`는 pure C++ scope에서 exact context/compiler가 unavailable 또는
not-applicable이고 실제 analysis/context/intake error가 없는 경우에만 required gate를 완화합니다.
이때 엔진은 `SKIP`/`NOT_RUN`, `required = false`가 되어 suite에는 `WARN`만 기여합니다. `required`는
unavailable 상태도 `ERROR`/`NOT_RUN`으로 승격하며, auto/required 모두 실제 context·coverage·configuration·
replay·parser·compiler·identity 오류를 휴리스틱으로 대체하지 않고 `ERROR`/`NOT_RUN`으로 fail-closed합니다.
`"off"`는 C++ 후보를 source intake/snapshot에서 제외하고 compiler probe와 tool evidence를 만들지
않으며, Python 입력·분석을 막지 않습니다.

각 unit은 정규화된 compile command를 한 번 더 안전하게 replay합니다. 원래 산출물·linker를
사용하지 않도록 warning-as-error/suppression 정책을 진단용으로 투영한 뒤
`-Wunused-function`, `-Wno-error=unused-function`, `-S`, `-o os.devnull`을 붙이고 rule 식별을
고정하기 위해 `-fdiagnostics-show-option`을 강제합니다. source
operand는 canonical path로 정규화하되 원래 argv의 positional slot에 정확히 한 번 유지하므로
`-x` 같은 뒤따르는 option의 의미를 바꾸거나 source를 suffix로 재배치하지 않습니다. option
separator 뒤의 추가 operand는 `-w`나 두 번째 `--`를 포함해 모두 거부합니다. 따라서 assembly만
폐기하고 assembler/linker와 project artifact를 실행하지 않습니다. 직접 승인된 GCC/Clang driver
또는 그 executable로 resolve되는 capability-approved alias만 허용합니다. 등록된 compiler probe는
`--version`으로 관측한 family를 기록하며, 실제 관측 family가 GCC이고 version 9 이상이면 JSON
diagnostics를 사용하고, older
GCC와 Clang(그 capability로 resolve되는 alias 포함)은 `-fdiagnostics-parseable-fixits` text를
사용합니다. alias의 format은 spelling이 아니라 같은 executable에 대해 관측한 approved
capability/version에 따라 결정됩니다. malformed/nonzero/timeout/truncated output, replay 오류와
budget 초과는 partial finding 없이 fail-closed합니다.

source snapshot은 no-follow regular-file reader가 descriptor identity와 double-read content를
검증하며, replay 직전과 otherwise-successful replay 뒤에 원 snapshot bytes와 다시 비교합니다.
승인된 외부 compiler는 regular executable이어야 하며 device/inode/mode/size/mtime/ctime
identity를 실행 직전과 otherwise-successful 완료 후 비교합니다. working directory도 project
내부 regular directory인지와 device/inode/mode identity를 실행 전에 확인하고, otherwise-successful
replay 뒤에 다시 확인합니다. replay 자체가 실패하면 해당 실패만으로도 즉시 fail-closed합니다.
`ToolEvidence`에는 compiler name/path/version, replay argv, return code와 timeout/truncation 상태가
남고, `dead`의 `cpp_unused_details`에는 source location,
`-Wunused-function`, configuration identity, tool name/version과 원 compiler message가 남습니다.

이 slice의 exact claim은 compiler가 선택된 TU source의 위치 범위에 귀속한 internal-linkage
함수 진단에 한정됩니다. `cpp_unused_non_tu_diagnostics_excluded`는 정확히
`-Wunused-function` rule의 warning이면서 선택된 TU source 밖에 compiler가 귀속한 경우만 세며,
다른 rule의 warning이나 note/error는 이 count에 포함하지 않습니다. 해당 non-TU/header/external
diagnostic은 finding으로 만들지 않으며, macro-generated 정의는 compiler가 귀속한 expansion
위치를 사용합니다. compiler-attributed logical path가 selected TU와 정확히 같고 line/column
범위가 immutable source snapshot 안에 있을 때만 보존합니다. 범위를 벗어난 `#line`/macro
remapping은 fail-closed하며, physical origin은 ici가 별도로 재구성하지 않습니다.
matching `-Wunused-function` warning에 위치가 전혀 없으면 source 귀속을 증명할 수 없으므로
clean PASS로 바꾸지 않고 `ERROR`/`NOT_RUN`으로 닫습니다.
external-linkage 함수, template, inline/COMDAT, linker reachability, dynamic lookup,
plugin, Qt meta-object reachability는 이 `cpp_unused` TU-local probe에서 분류하지 않습니다.
`cpp_linker`의 별도 범위는 아래에 설명하며, generated/moc source도 기본 source ownership
정책 밖에 있습니다.

probe는 최대 2,048 translation units, unit당 120초, 전체 600초, compiler output
1,000,000 characters를 사용합니다. shared source intake의 candidate/file/8 MiB per-file/
64 MiB aggregate bound와 sanitized replay의 최대 32,768 arguments/1 MiB characters bound도
함께 적용합니다.

Python과 C/C++가 함께 있으면 language별 근거와 finding을 섞지 않습니다. C++ probe는 모든
selected source/configuration을 끝까지 확인한 뒤에만 findings를 commit하므로, 뒤늦은 C++ replay
실패나 configuration disagreement가 있으면 이미 관찰한 C++ unused findings를 모두 폐기하는
atomic 결과가 됩니다. 단, 이미 성공적으로 완료·기록된 compiler observation의 source에는 위치가 있는
`C++UnusedFunctionsInvalidated` `SKIP` target을 남겨 폐기된 실행 범위를 추적합니다. Python 분석이
먼저 정상 완료된 hybrid에서는 그 Python findings는 그대로
남습니다. Python finding은 heuristic confidence(`MEDIUM`)와 빈 tool attribution을, accepted
C++ finding은 `FindingConfidence.EXACT`, `tool_rule_id = "-Wunused-function"`, compiler name/version을
각각 보존합니다. 예를 들어 `extra.language_evidence`는 Python `ESTIMATED`, C++ `MEASURED`로
각각 남고 `analysis_provenance`는 `python-ast-heuristic+cpp-compiler-unused-function`이
됩니다. 따라서 전체 `dead` result evidence는 Python scope가 있으면 `ESTIMATED`이고, C++만
exact로 실행된 경우에만 `MEASURED`입니다.

전체 `ici verify`와 standalone `ici dead`는 동일한 project/tool/compilation preflight와 immutable
context를 사용합니다. standalone은 `dead` support scope에 필요한 capability만 scoped probe하면서
설정된 `[doctor].required_tools`도 함께 probe/기록하며, `dead` compiler capability만 고정적으로
사용하는 경로가 아닙니다. compilation database가 없고 canonical CMake/qmake context를 만들 수
있는 프로젝트라면 동일한 preflight가 ici 소유 shadow에서 context를 준비할 수 있습니다.

최종 viewer standalone `dead` evidence는 `PASS`/`MEASURED`이며, 정확히 8개 source, 8개
configuration, 8개 target, 8개 `tool_evidence` 행을 확인했습니다. unused function은 0개이고
`cache_key`는 `null`입니다.

이 slice는 [PR #137 CI](https://github.com/jihoon22-lee/ici/actions/runs/33675765436),
단일 sticky comment의 ici/viewer 링크, PR·main artifact/Pages byte match, exact-main CI와
Pages 배포까지 수락됐습니다. 상세 해시와 run ID는
[`compiler-backed C/C++ unused-function workthrough`](docs/workthrough/2026-09-03-compiler-backed-cpp-unused-functions.md)에
기록합니다. target-local GNU ELF section-GC 증거는 지원하지만, whole-program/dynamic
dead-symbol reachability와 full C++ semantic/behavioral duplicate equivalence는 아직
지원 범위가 아닙니다. 버전은 `0.10.2`로 유지하고 새 release는 만들지 않습니다.

### C++ GNU ELF and Python AST-shape extensions

`[engines.dead].cpp_linker = "auto" | "required" | "off"`(기본값 `off`)는 Linux root CMake/
`Unix Makefiles`/Release의 direct-object ELF executable을 GNU `ld` section-GC로 재링크하고,
`cmake`·`readelf`·`addr2line`이 확인한 uniquely mapped local/hidden discarded function
section만 target-local `MEASURED`/`EXACT` finding으로 기록합니다. archives/shared/LTO/PIE/
COMDAT/dynamic/whole-program 범위와 malformed·timeout·truncation은 제외하거나 fail-closed합니다.
`[engines.dup].python_semantic = "auto" | "required" | "off"`(기본값 `auto`)는 Python 3.10
leaf function/method AST shape를 canonicalize해 local alpha-renaming·layout insensitivity를
적용하면서 control flow/operator/literal/source-spelled imported-name/attribute anchor를
보존하고, exact shape group만
완전한 occurrence 집합이 같은 lexical group과 dedup합니다. 두 기능의 bounded 결과는
구조/target-local 증거이며 behavioral equivalence가 아닙니다. 버전은 `0.10.2`로 유지하고 이
feature PR만으로 release를 만들지 않습니다.

### C++ complexity function boundaries

`complexity`는 `[engines.complexity] cpp_boundaries = "auto" | "required" | "off"`를
지원합니다. exact compilation context/database와 capability-approved direct `clang-tidy`가
있을 때만 `readability-function-size`의 AST diagnostic으로 함수 경계 geometry를 확정합니다.
경계 안의 CC/nesting은 여전히 ici의 masked token/brace metric이며 `metric_confidence`는
`medium`입니다. `auto`는 context/tool 부재에만 source scanner로 fallback하고
`ESTIMATED`를 남깁니다.

AST 경계의 대상은 source-spelled named function이며 function template, conversion/call/subscript
operator, literal operator를 포함합니다. 각 target은 `function_kind`, `function_template`,
`function_origin`으로 분류를 보존합니다. lambda는 독립 함수 target으로 만들지 않으며, lambda
body는 enclosing function의 CC/nesting 계산에서 masked/excluded 됩니다. Macro-generated
function이 expansion site에서 진단되면 해당 scope는 명시적으로 제외하고
`extra.cpp_scope_exclusions.macro_generated_function`에 개수를 남깁니다. 따라서 파일의 다음
brace를 macro-generated function의 body로 추정하지 않습니다. Fallback scanner도 operator 이름을
보존하고 multiline preprocessor definition/continuation과 standalone macro invocation을 skip합니다.
lambda 제외 개수는 `extra.cpp_scope_exclusions.lambda`에서 확인할 수 있습니다.

성공한 각 configuration의 boundary는 geometry뿐 아니라 name, kind, provenance가 일치해야
promotion됩니다. clang-tidy가 보고한 lines/statements/parameters는 configuration별로
`configuration_metrics`에 보존합니다. geometry가 다르면 boundary promotion을 보류하고,
function-size metric이 configuration별로 다르거나 body에 conditional preprocessor branch가
있으면 run은 `partial`, 해당 target의 `metric_confidence`는 `low`가 됩니다. compiler-backed
function metrics 또는 configuration coverage가 partial/low-confidence로 남으면 `required`에서는
`ERROR`/`NOT_RUN`으로 fail-closed합니다. 빈/미보고
source-spelled definition은 fallback에서 heuristic으로 남을 수 있습니다.

시도된 tool·replay·parser·timeout·truncation·coverage·budget 오류는 `ERROR`/`NOT_RUN`으로
닫습니다. 단, clang-tidy가 visible project diagnostics와 함께 정확한
`Suppressed N warnings (N in non-user code).`를 내는 회계는 외부/system 진단만 억제한 경우로
허용합니다. NOLINT/project/mixed/malformed/count-mismatch suppression은 계속
`ERROR`/`NOT_RUN`으로 fail-closed합니다. `required`는 unavailable 또는 partial/estimated
boundary도 오류로 승격하며, `off`는 의도적으로 heuristic 경로를 사용합니다. probe는 호출자가
제공한 bounded source snapshot과 mapped-source cache를 사용하고 replay 전·도구 완료 후 source
identity를 재검증합니다. C++ source inventory도 최대 2,048 source files와 64 MiB aggregate
UTF-8 source bytes cap 아래에서 수집하며,
동일 geometry가 성공한 모든 configuration에 존재할 때만 exact로 승격합니다. 누락 또는 config별
상이한 geometry는 partial로 남고 `required`에서는 오류입니다. 실행 한도는 2,048 units,
source당 8 MiB, run source bytes 64 MiB, mapped-source cache bytes 16 MiB, output 1,000,000자,
parser 10초, unit당 120초, 전체 600초입니다. 같은 줄의 인접/overload 함수, braced declarator와
default/noexcept/trailing `requires` 표현식, function-try/catch, `<%`/`%>` body를 포함해
geometry를 매핑합니다. approved tool executable은 매 process 실행 직전에 다시 resolve하며
device/inode/mode/size/mtime/ctime identity가 바뀌거나 사라지면 fail-closed합니다. assigned
`[]`/`+[]` lambda initializer brace는 fallback에서 phantom 함수로 만들지 않습니다. descriptor 경로는
`dir_fd`/`O_DIRECTORY`를 사용할 수 없는 fallback에서도
resolved named path와 device/inode/size/mtime identity를 다시 검사해 intermediate symlink와
TOCTOU를 fail-closed합니다.

PR #130의 historical compiler-boundary baseline은 두 번 byte-identical인 candidate SHA
`7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`와 Python 3.10
`1,626 passed, 2 skipped`를 남겼습니다. 이후 `feat/cpp-function-scope-policy` candidate는
real extracted `clang-tidy-21`을 사용한 Python 3.10 full suite `1,656 passed, 2 skipped`,
Ruff check/format, mypy와 packaged smoke를 통과했습니다. 최초 PR run에서 드러난
1,031 pure-code-line self gate는 parser/source mapping helper 628줄과 process runner
compatibility facade 487줄로 분리해 해소했습니다. 이 local candidate의 재현 가능한
`dist/ici.pyz` SHA와 fresh clean `toy-projects` `main` 교차 검증 상세는 [C++ function-scope
policy workthrough](docs/workthrough/2026-09-02-cpp-function-scope-policy.md)에 고정합니다.

[PR #131](https://github.com/jihoon22-lee/ici/pull/131) `feat(complexity): classify C++ function
scopes and metric provenance`는
[`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7)로
squash merge됐습니다. [PR CI run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495)은
성공했고 sticky marker/current run은 정확히 하나였습니다. PR ici/viewer Pages는 HTTP/title/
Zero-CDN 검사를 통과하고 각각 `7,454,995`/`356,598` bytes와 artifact HTML byte-match를
기록했습니다. [exact-main run `33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450)도
성공했으며 main JSON `source_commit`이 같은 SHA를 가리켰고, main ici/viewer Pages도 HTTP/title/
Zero-CDN과 artifact byte-match를 통과했습니다(각각 `7,454,995` bytes,
`182a0d05…5adbb75`; `356,598` bytes, `fb772d4a…c0c4794`). 두 run에서 skip된 것은 각
workflow의 예상된 PR/main publish job뿐입니다. 이 acceptance는 scope-policy slice에 해당하며
compiler-backed C/C++ unused-function과 target-local GNU ELF section-GC 범위를 넘어서는
whole-program/dynamic dead 분석, full C++ semantic/behavioral duplicate equivalence, 남은
I4-3/I4-4 및 I4 전체 checkpoint를 닫지 않습니다.
버전은 `0.10.2`로 유지하고 release는 만들지 않습니다.

## 💻 빠른 설치 및 사용법

### 1. 단일 파일 실행
```bash
# 산출물 복사 및 실행 권한 부여
mkdir -p ~/.local/bin
cp dist/ici.pyz ~/.local/bin/ici && chmod +x ~/.local/bin/ici
export PATH="$HOME/.local/bin:$PATH"

# 환경 진단
ici doctor
ici doctor --brief  # capability status와 준비된 도구 수를 한 줄로 확인
ici doctor --json  # 전체 capability inventory와 프로젝트별 support matrix

# 전체 검증 실행
ici verify --report --html verify_report.html --open
```

### 2. 소스에서 빌드
```bash
./scripts/build-pyz.sh    # dist/ici.pyz 생성
./scripts/smoke.sh        # 격리 환경 스모크 테스트
```

`build-pyz.sh`는 `uv.lock`을 단일 의존성 원천으로 사용하는 hermetic 재현 빌드입니다. 잠긴
runtime 그룹은 `--no-dev`로 내보내고, wheel/ZipApp을 만드는 `package` 그룹은 별도로
`--only-group package`로 내보냅니다. 두 requirements 파일 모두 lock에 기록된 hash를
포함하며 `uv pip install --require-hashes --only-binary :all:`로 검증됩니다. sdist를
실행하지 않으며, 빌드 entrypoint와 GitHub workflow는 uv `0.12.5`를 요구합니다. 현재 package 그룹은
`hatchling`과 `shiv==1.0.8`이고, 이 도구들은 `build/package-tools`에만 설치되어
배포 runtime에 들어가지 않습니다. 프로젝트 wheel은 Python 3.10 대상으로 만든 뒤 runtime
site-packages에 의존성 없이 넣습니다. 빌드 스크립트는 선택한 Python 3.10+ helper interpreter
하나를 선택해 package/build, 정리, 조립 단계 전체에 전달하므로 호출자의 bare `python3`
선택에 의존하지 않습니다.

빌드가 호출자의 환경에 좌우되지 않도록 `SOURCE_DATE_EPOCH=1700000000`(UTC
`2023-11-14 22:13:20`), `PYTHONHASHSEED=0`, `PYTHONUTF8=1`, C locale, `TZ=UTC`,
`umask 022`를 고정합니다.
머신별 `direct_url.json`/`uv_cache.json`/`uv_build.json`, target `.lock`, 실행 파일
링크는 제거되며, 설치된 runtime·bootstrap 입력 파일은 `0644`, directory는 `0755`로
정규화됩니다. shiv가 자체 생성하는 top-level `environment.json`과 `__main__.py`는
고정 `0600` metadata를 사용합니다. 입력은 열기 전 nonblocking `lstat`로 regular file임을
확인하고 no-follow descriptor로 읽으므로 FIFO 같은 special file도 block 없이 fail-closed로
거부됩니다. 기존 output의 symlink·special file도 거부됩니다. 최종 조립기는 열린
non-symlink `dist` directory descriptor 안에 bounded payload를 임시 파일로 쓰고 `fsync`한
뒤, 기존 output마다 hard-link backup을 먼저 만든 다음 각 이름을 같은 디렉터리에서
원자적으로 교체합니다. 중간 교체나 사후 byte/mode 검증이 실패하면 이전의 일관된 output
set을 복구하고, 원래 없던 이름은 제거합니다. 쓰기·flush·`fsync` 실패 때 임시 파일도
정리되며, 최종 `dist/ici.pyz`와 `dist/ici`는 byte-identical `0755`인지 확인합니다.

shiv `1.0.8`은 bootstrap 입력을 `importlib.resources`의 filesystem iteration 결과에 따라
읽을 수 있어 checkout 환경에 따라 `_bootstrap/` 내부 archive member 순서가 달라질 수
있습니다. 따라서 `build-pyz.sh`는 선택된 helper Python으로 `scripts/run_shiv.py`를
호출하고, wrapper가 shiv의 bootstrap resource를 archive 이름 기준으로 정렬한 뒤 shiv에
위임합니다. `verify-reproducibility.sh`는 중복 member를 거부하고
`site-packages/` → `_bootstrap/` → `environment.json` → `__main__.py`의 canonical entry
순서를 확인합니다. 이 검사는 archive entry order 계약에 한정되며 zlib 버전이나 플랫폼
전체의 byte identity를 주장하는 검사는 아닙니다.

재현성 검증은 `scripts/verify-reproducibility.sh`가 의도적으로 서로 다른 환경에서 두 번
빌드하는 방식입니다. 첫 빌드는 `umask 077`, `SOURCE_DATE_EPOCH=1`,
`PYTHONHASHSEED=random`, 다른 locale/UTF-8 설정, `TZ=Pacific/Honolulu`, 두 번째 빌드는 `umask 002`,
`SOURCE_DATE_EPOCH=4102444800`, `PYTHONHASHSEED=123`, `TZ=Asia/Seoul`을 사용합니다.
두 SHA-256이 같아야 하며, verifier는 모든 ZipApp member의 canonical epoch/mode, shiv
environment timestamp와 canonical entry order, duplicate member 부재, 두 executable의
byte/mode 일치, `site-packages/.lock` 부재와 git source status 불변도 확인합니다. entry-order
계약과 cross-environment digest는 각각 독립적으로 확인하며, 최종 digest는 현재 작업의
acceptance gate에서 다시 확정합니다.

### 3. 기준선 비교와 delta gate

```bash
# 현재 상태를 다음 비교의 기준선으로 저장
ici verify --write-baseline .ici/baseline.json

# delta를 기록하되 기준선 gate는 강제하지 않음
ici verify --baseline .ici/baseline.json \
  --report --html verify_report.html --github-summary

# CI에서 새 actionable finding과 regression을 gate에 반영
ici verify --baseline .ici/baseline.json --fail-on-new \
  --report --html verify_report.html --github-summary
```

기준선은 `ici.result/v3` JSON만 읽으며, baseline 입력과 새 baseline 출력 경로는 프로젝트
루트 안에 있어야 합니다. 경로가 프로젝트 밖을 가리키거나 프로젝트 내부 symlink를 통해
밖으로 빠져나가면 거부합니다. 기존 기준선과 `--write-baseline`의 출력 경로를 같게 쓰는
것은 기존 파일을 먼저 읽고 새 파일로 교체하므로 허용됩니다. 다만 `--fail-on-new` gate가
실패한 실행은 같은 파일을 덮어쓰지 않아 다음 실행에서 regression을 숨기지 않습니다.
또한 `--report`가 만드는 `verify_report.json`과 `--write-baseline` 경로를 같게 쓸 수는 없습니다.

### 4. 분석 캐시

`ici verify`는 기본적으로 사용자별 로컬 분석 캐시를 사용합니다. 캐시는 프로젝트 안에
생기지 않으며, 동일한 프로젝트 입력·effective 설정·toolchain·엔진 구현·build variant에서만
engine result를 재사용합니다. 결과를 항상 새로 계산해야 하는 CI/release 점검이나 캐시 영향을
분리한 진단에는 다음 옵션을 사용합니다.

```bash
# 이번 verify에서 cache read/write 모두 비활성화
ici verify --no-cache

# cache 위치·유효 entry·손상 entry·크기 확인
ici cache

# ici가 소유한 exact entries-v1 아래 entry만 삭제
ici cache --clear
```

`WARN`/`FAIL`이라도 timeout·truncation·tool error가 없고 artifact identity가 유효한 완료
결과라면 재사용될 수 있습니다. `ERROR`/`SKIP`/`NOT_RUN`과 불완전하거나 invalid한 결과는
성공 cache로 저장하지 않습니다. 캐시 hit 여부와 key digest는 v3 JSON의 optional
engine-level `cache_hit`/`cache_key`로 확인할 수 있고, 기존 v3 JSON은 해당 필드 없이도
계속 읽을 수 있습니다.

cache key(`ici.analysis-cache-key/v3`)는 canonical root, source/build-config content, effective config, toolchain,
engine implementation, build variant와 ici version을 포함합니다. engine implementation identity는
engine class의 module/qualname와 class source digest, 그리고 `CACHE_IMPLEMENTATION_MODULES`로
명시한 helper/dependency module 이름의 sorted unique 목록과 각 module source digest를 포함합니다.
import tree 전체를 암묵적으로 수집하지 않고 명시적으로 선언된 구현 의존성만 반영합니다.
프로젝트 source digest에는 인식된 설정 이름 `.clang-tidy`가 포함되므로 그 내용·권한 변경은
cache miss를 만들지만, 인식 목록에 없는 unrelated hidden file은 포함하지 않습니다.
`verify_report.json`과
engine별 `*_report.json`처럼 ici가 생성하는 report JSON은 source digest에서 제외됩니다.
entry reader는 symlink·비정규 파일, duplicate JSON key, non-finite number와 32 MiB 초과
payload를 신뢰하지 않으며, 손상·stale entry는 miss로 처리합니다. artifact manifest가
있으면 store/load 양쪽에서 경로 containment와 실제 content·size·mode를 재검증합니다.

로컬 cache 검증에서 전체 Python 3.10 run은 935 tests passed였고 targeted 테스트도
통과했습니다. standard 첫 실행은 118.49초·hits 0, 두 번째는 2.38초·hits 12였으며,
두 결과의 normalized SHA-256은 `95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`,
findings는 각각 3,497건이었습니다. HTML은 4,095,550 bytes·외부 참조 0건, 재현성
script 두 build는 SHA-256 `6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`과
project source status unchanged를 확인했습니다. 이 결과는 I2-4의 로컬 증거이며 PR/CI/Pages
또는 release 완료를 뜻하지 않습니다.

### 5. Issues-first 콘솔

`ici verify`는 전체 inventory를 보존하면서 조치가 필요한 원인을 짧게 확인할 수 있는
issues-first 콘솔 projection을 제공합니다.

- `ici verify --verbose`: `verify` 전용 상세 표시 모드이며 console cap을 해제합니다.
- `ici verify --max-findings N`: 엔진별 console display group 상한입니다. 기본값은 엔진별
  5건이며 `0`은 engine summary만 표시합니다.
- `ici verify --group-by engine|severity|category|file|rule`: v3 finding의 engine, severity,
  category, canonical primary file 또는 rule 기준으로 표시 그룹을 선택합니다.

cap과 grouping은 console-only projection입니다. JSON·HTML·Markdown과 baseline의 원본
inventory, target, finding, delta occurrence는 상한과 무관하게 모두 보존합니다. duplicate는
같은 실행의 같은 clone group 안에서 같은 파일의 겹치는 line region만 화면에서 병합하며,
원본 occurrence와 fingerprint를 유지합니다. 인접하지만 겹치지 않는 region이나 서로 다른
clone group은 합치지 않습니다. HTML `Issues` 탭도 native v3 finding inventory를 기반으로
표시하며 전체 결과를 유지합니다.

표시 순서와 줄바꿈은 deterministic하게 유지하고, 80-column 터미널에서도 표와 상세 링크가
한 글자씩 세로로 깨지지 않도록 회귀 테스트로 고정했습니다.

다음 수치는 issues-first console을 도입한 PR #89 당시의 고정된 acceptance 기록입니다
(`814679c` + `d80a027`). 당시 로컬 Python 3.10 전체 품질 게이트는 756/756 tests,
focused console 테스트는 16개였습니다. 최종 안정 self verify에서
built `dist/ici.pyz`가 exit 0으로 실행됐고 suite는 WARN을 반환했습니다. self verify 출력은
144 lines/15,288 bytes, HTML은 3,383,523 bytes였습니다. 해당 self verify 출력에 내장된
test engine 수치는 756/756이며, local self verify line/function/branch coverage는 87.8%/96.6%/78.8%, TEM
4.83, engines Pass 8/Warn 4/Fail 0/Error 0/Skip 0을 확인했습니다. complexity는 최대 23,
이슈 64건이며 duplicate는 16.2%·338 groups·1,006 actionable occurrences였습니다.

콘솔 측정은 actionable 1,088건, visible 21/420 display groups, represented 34,
hidden 1,054 findings/399 groups였습니다. HTML에는 clone group card 338개와 issue engine
row 1,088개가 유지됐고 external script/stylesheet reference는 0개였습니다.

Merge evidence (PR #89): [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
[`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
병합됐습니다. [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의
모든 required checks가 green(756 tests)이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/89#issuecomment-5470778278)에
결과가 기록됐습니다. CI report stats는 ici WARN(TEM 4.83, Pass 8, Warn 4, line 87.8%,
function 96.6%, branch 78.9%), viewer PASS(TEM 4.89, 7/7 tests)였습니다. [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/89/)
는 HTTP 200·external script/stylesheet refs 0, [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/89/)
는 HTTP 200·external refs 0이었습니다.

---

## 📦 컴파일 맥락 내보내기

`ici export-compilation-context`는 검증 스위트를 실행하지 않고, 측정된
`compile_commands.json`을 개인정보 보호형 `ici.compilation-export/v1` JSON으로 내보냅니다.
기본 경로는 프로젝트 메타데이터와 컴파일 데이터베이스를 읽기만 하며 compiler, shell,
subprocess, 재귀 소스 스캔을 사용하지 않고 전역 기본 설정 파일도 새로 만들지 않습니다.

```bash
# 발견된 DB를 stdout으로 출력한다. 성공 시 stdout은 JSON 한 개뿐이다.
ici export-compilation-context

# project-relative POSIX DB를 선택해 checkout 밖의 임시 파일로 예쁘게 저장한다.
ici export-compilation-context \
  --database build/compile_commands.json \
  --output /tmp/ici-compilation-context.json --pretty

# DB가 없을 때만 명시적으로 CMake/qmake 준비를 허용한다.
ici export-compilation-context --prepare \
  --output /tmp/ici-compilation-context.json
```

`--database`는 프로젝트 루트 아래의 POSIX 상대 경로만 허용하며 루트 밖 traversal, 절대 경로,
Windows 경로와 symlink 탈출은 거부합니다. `--prepare`는 명시적으로 선택·설정한 DB와
auto-discovered DB가 모두 없을 때만 루트의 CMake/qmake 어댑터를 사용해
`build/ici-cmake-build` 또는 `build/ici-qmake-build`를 configure/build할 수 있으므로 파일과
외부 도구를 변경할 수 있습니다. 명시한 DB가 누락되거나 손상된 경우에는 다른 DB로 대체하지
않고 해당 오류를 반환합니다. `--output`의 기본값은
`-`(stdout)이고, 파일 출력은 같은 디렉터리의 임시 파일·flush·fsync·atomic replace를
사용합니다. 기존 regular file은 원자적으로 교체하며 허용된 symlink는 링크 자체를 교체해
referent를 쓰지 않습니다. 데이터베이스와 `ici.toml`, `dev.toml`, `pyproject.toml` 및
그 alias/special file은 출력 대상으로 사용할 수 없습니다.

출력은 정렬된 key와 최종 개행을 가진 결정론적 UTF-8 JSON이며 `--pretty`는 들여쓰기만
추가합니다. 데이터베이스 바이트 digest와 정규화된 semantic digest를 분리해 기록하고, 이
digest는 preflight가 immutable context로 캡처한 database snapshot의 identity이지 live-file
lease가 아닙니다. 현재 실행은 캡처한 frozen unit을 계속 사용하고, database 변경은 다음
preflight가 새 digest와 context로 캡처합니다. raw `argv`/`command`는 내보내지 않습니다. 프로젝트 내부
경로는 POSIX 상대 경로로,
외부 경로·sysroot는 `[external]`로, credential과 안전하게 공개할 수 없는 값은
`***REDACTED***`로 투영합니다. 실제 DB를 읽었다는 `evidence`는 `MEASURED`이지만,
외부 경로·redaction·unknown compiler·unmodeled option·diagnostic·unity build가 있으면
`comparison_state`는 `inconclusive`가 될 수 있습니다.

입력 DB는 32 MiB·200,000 entry, 한 argv는 32,768 argument·총 1 MiB, DB 전체의 확장
argument는 1,000,000개·32 MiB, command 문자열은 4 MiB로 제한됩니다. project-contained
response file도 깊이 4, 파일/aggregate 4 MiB와 동일한 per-row argument bound 안에서만 읽습니다.
`arguments`가 `command`보다 우선하고 shell은
호출하지 않으며, duplicate JSON key·비유한 수·비정상 파일·symlink 탈출과 malformed row는
제한된 diagnostic으로 처리합니다. 출력 자체도 32 MiB를 넘으면 쓰지 않습니다.

성공은 exit 0, 입력/경로 검증 실패·측정된 DB/usable unit 부재는 exit 2, fatal diagnostic이나
직렬화·쓰기 오류는 exit 1입니다. stdout 모드의 성공 출력은 JSON만 포함하고 오류는 stderr로
보냅니다. 기계 검증 계약은 배포 패키지에 포함되는
[`ici-compilation-export-v1.schema.json`](src/ici/schemas/ici-compilation-export-v1.schema.json)
이며, `scripts/build-pyz.sh`가 ZipApp 구성 단계에서 공개 schema 포함을 확인합니다.

## 📋 명령어 일람

| 명령어 | 설명 | 상세 가이드 |
|---|---|---|
| `ici verify` | 검증 엔진 일괄 실행 및 종합 대시보드 출력 (`--report`, `--html`, `--github-summary`, 선택적 `--publish`) | [사용자 가이드](docs/user-guide.md#2-검증-실행-ici-verify) |
| `ici export-compilation-context` | 측정된 compile database를 redacted `ici.compilation-export/v1` JSON으로 내보내기 (`--database`, `--prepare`, `--output`, `--pretty`) | [사용자 가이드](docs/user-guide.md#standalone-compilation-context-export) |
| `ici line` | 코드/주석/공백 분석 및 500/1000 라인 과대화 검증 | [엔진 레퍼런스](docs/engine-reference.md#21--line-코드-라인-및-파일-크기-분석기) |
| `ici lint` | 문법 린팅 및 스타일/포맷팅 검증 | [엔진 레퍼런스](docs/engine-reference.md#22--lint-문법-및-코드-스타일-린터) |
| `ici test` | 단위 테스트 실행 및 커버리지/TEM 스코어 산출 | [엔진 레퍼런스](docs/engine-reference.md#23--test--tem-스코어링-단위-테스트-및-테스트-효과성-지표) |
| `ici type` | 정적 타입 검사 | [엔진 레퍼런스](docs/engine-reference.md#24-️-type-정적-타입-안정성-검사기) |
| `ici python-compat` | Python runtime `-VV`·compileall·선택 import 및 `requires-python`/syntax/API floor 검증 | [엔진 레퍼런스](docs/engine-reference.md#215--python_compat-python-runtime-호환성) |
| `ici complexity` | 순환 복잡도 및 중첩 깊이 분석 | [엔진 레퍼런스](docs/engine-reference.md#25--complexity-순환-복잡도-및-블록-중첩도) |
| `ici sanitize` | C++ ASan/UBSan 메모리 안전성 검증 | [엔진 레퍼런스](docs/engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici thread-sanitize` | deep profile 전용 C++ ThreadSanitizer thread-safety 검증 | [엔진 레퍼런스](docs/engine-reference.md#26-️-sanitize-메모리-안전성-및-리소스-누수-진단) |
| `ici dead` | 죽은 코드 및 미사용 심볼 검출 | [엔진 레퍼런스](docs/engine-reference.md#27--dead-죽은-코드-및-미사용-심볼) |
| `ici dup` | 중복 코드 / Copy-Paste 감지 | [엔진 레퍼런스](docs/engine-reference.md#28--dup-코드-복제-및-중복률-감지기) |
| `ici exception` | 예외 처리 안전성 검출 | [엔진 레퍼런스](docs/engine-reference.md#29-️-exception-예외-처리-안전성-검출기) |
| `ici cycle` | Python import / C++ include 순환 참조 탐지 | [엔진 레퍼런스](docs/engine-reference.md#210--cycle-순환-참조-탐지) |
| `ici cognitive` | 인지 복잡도 분석 (기본 비활성, 옵트인) | [엔진 레퍼런스](docs/engine-reference.md#211--cognitive-인지-복잡도) |
| `ici security` | 하드코딩 시크릿·약한 해시 등 보안 위생 패턴 탐지 | [엔진 레퍼런스](docs/engine-reference.md#212--security-보안-위생) |
| `ici resource` | 리소스 누수 및 가변 기본 인자 검출 | [엔진 레퍼런스](docs/engine-reference.md#213--resource-리소스-누수) |
| `ici build` | 아티팩트 컴파일, 패키징 및 `env.sh`/`env.csh` 생성 | [사용자 가이드](docs/user-guide.md) |
| `ici doctor` | 시스템/현재 지원 도구/파이썬 환경 진단 | [사용자 가이드](docs/user-guide.md#12-실행-환경-진단-ici-doctor) |
| `ici env` | 셸 환경 설정 스니펫 생성 (`--sh` / `--csh`) | [사용자 가이드](docs/user-guide.md) |
| `ici cache` | 사용자 로컬 분석 cache inventory 표시 및 `--clear` 정리 | [사용자 가이드](docs/user-guide.md#202-분석-결과-캐시) |
| `ici publish` | 기존 HTML/JSON 리포트를 `gh-pages`에 게시하고 sticky PR 댓글 갱신 | [CI/CD 연동 가이드](docs/ci-integration.md#12-pr-리포트-sticky-댓글-report-pr) |
