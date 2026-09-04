# CHANGELOG

모든 주요 변경 사항은 이 문서에 기록됩니다.
이 프로젝트는 [Semantic Versioning](https://semver.org/lang/ko/) 규약을 따릅니다.

---

## [Unreleased]

### Added

- **Deeper native analysis and gcov JSON coverage (unreleased):** C++ cognitive analysis now uses
  compiler-backed function boundaries when available, but the CC/nesting value inside that boundary
  is always a bounded lexical estimate (`bounded-cpp-tokens`), never an exact compiler metric;
  boundary provenance and metric confidence remain separate. Native lint diagnostics recognize
  additional compiler/analyzer, clang-tidy, and clazy lifetime, ownership, iterator, allocation,
  and Qt detach categories by normalized rule id. On supported GCC, test coverage now prefers
  bounded gzip JSON, validates the advertised format/tool versions, preserves exact function
  line/column geometry and demangled names, filters exceptional throw edges, verifies the complete
  expected production-source set, and records source-mapping and explicit scope/exclusion
  provenance. A successful legacy `gcov --help` probe that does not advertise JSON uses the
  documented lower-fidelity text fallback; indeterminate probes or malformed JSON never fall back
  silently. Directory aggregation additionally caps report count, cumulative compressed/
  decompressed bytes, and cumulative file/function/line/branch/call records. Statement attributes
  before control flow and lambda bodies written with C++ brace digraphs retain the same cognitive
  semantics as their ordinary spellings. gcov JSON v1 reports without basic-block IDs use
  source-line branch order with explicit provenance. Coverage policy targets now cover
  overall line, per-file line (with a minimum statement
  floor), aggregate/function scope, and caller-declared changed lines; PASS targets are retained,
  JSON uncovered functions keep exact locations, and baseline coverage regression is opt-in. The real
  CMake/Qt fixture under GCC 15.2 reached 100% line/function/branch coverage with three exact
  functions; its JSON evidence contained five reports for one expected production source and 19
  ignored generated/external records. Mutation remains capability probing only and contributes no
  mutation score. This consolidated feature work retains stable version `0.10.2` and does not
  authorize a release. See [native analysis and coverage depth workthrough](docs/workthrough/2026-09-04-native-analysis-and-coverage-depth.md).

- **LLVM 18 multi-pair clang-tidy diagnostics:** The strict C++ diagnostic parser now retains one
  `bugprone-easily-swappable-parameters` primary while LLVM emits multiple bounded empty-note and
  conversion-note pairs for that range. Malformed or detached empty notes remain atomic parse
  errors. This fixes a stable `0.10.2` interoperability defect without changing the version or
  authorizing a release.

- **Unreleased analysis-platform contracts:** The deep profile now has opt-in `build`,
  `binary_compat`, and `integration` descriptors. Configured root Make projects use bounded,
  shell-free direct argv and variant-specific plans; successful outputs carry `ici.artifacts/v2`
  provenance (`id`, target, redacted producer command, digest, size, mode, and build identity),
  while legacy manifests remain readable as v1. The checked-in v3 JSON Schema now accepts and
  distinguishes both record versions. Artifact discovery and hashing have explicit entry/count,
  per-file, and aggregate byte ceilings, and suffix-shaped text files or object intermediates are
  not counted as linked outputs. `binary_compat` consumes only manifest-declared
  executable/shared-library artifacts by default (or explicitly selected manifest records) and
  inspects ELF/ABI, loader paths, dependencies, and build path leaks with `readelf` without executing
  binaries. `integration` runs typed whole-token
  `{python:id}`/`{artifact:id}` process cases with bounded output, timeout, environment, and output
  artifact assertions. These contracts are implemented on the feature branch, remain opt-in, and
  do not change stable version `0.10.2` or authorize a release.

- **Test quality and report scalability (unreleased):** Deep Python test runs can optionally record
  bounded slow-test inventory and repeat-run flaky verdicts as native `ici.test.slow-test` and
  `ici.test.flaky-test` findings. `quality.mode = "report"` is informational; `"warn"` feeds the
  quality warning into the test engine policy. Runtime test outcomes and timings are never reused
  from the analysis cache; `slow_tests_observed` counts all unique threshold-matching rows before
  the capped inventory is retained, while non-finite or overlong duration tokens are excluded from
  observation. Repeat mode records unavailable evidence and suppresses flaky claims when the base
  run has no per-test outcomes. Mutation configuration is capability-only. The validated config loader rejects
  malformed mode/tool values; direct engine callers normalize them safely without replacing the
  base test gate. `ici verify --sarif <path>` now emits deterministic SARIF 2.1.0
  from the canonical finding projection with bounded rule/result counts, occurrence-safe duplicate
  baseline matching, moved-from locations, and percent-encoded source URIs. HTML files are published
  by fsynced same-directory atomic replacement so an existing output symlink is replaced rather than
  followed. HTML reports with more than
  2,000 actionable findings server-render 50 rows and hydrate the remaining pages from bounded
  inline `ici.html-report/v1` JSON; when `--report` is also requested, the complete inventory remains
  in `ici.result/v3` (SARIF-only runs do not create a separate JSON file). No version
  bump or release is implied; final CI/toy-project acceptance remains separate.

- **Flow-sensitive Python resource ownership rules (unreleased language-analysis bundle):**
  Replaced the former unconditional `open()` warning with bounded, intraprocedural AST flow that
  distinguishes context managers, direct `close()`/`aclose()`, aliases, branch exits,
  `try/finally`, returned ownership, attribute/subscript transfers, and resources registered with
  an `ExitStack`. Supported import aliases cover built-in/`io` file handles, temporary files,
  sockets, and `Path(...).open()`. A finding is retained whenever an open outcome remains after
  branch merging, with closed/transferred outcome evidence recorded separately. Mutable literal
  and supported constructor defaults now produce a native `correctness` finding instead of being
  mislabeled as a resource defect. The engine uses bounded no-follow source snapshots, reports
  syntax/input/AST-limit failures as located `ERROR`/`NOT_RUN`, emits per-file PASS targets, and
  reports `NOT_APPLICABLE` when no Python source exists. This remains in the consolidated
  language-analysis work and does not change version `0.10.2` or authorize a release.
- **Bounded, redaction-safe Python security AST rules (unreleased language-analysis bundle):**
  Replaced line-oriented regular-expression matching with Python AST rules that resolve supported
  lexical-scope-aware import aliases and distinguish calls, assignments, dictionary keys, and
  literal values. The
  engine now detects weak hashes and randomness, dynamic execution, unsafe pickle loads, command
  processors, constant `subprocess(..., shell=True)`, private-key markers, and context-sensitive
  hardcoded secrets without retaining or reporting source secret values. Secret detection combines
  name context, known credential prefixes, length, and entropy; exact case-insensitive names can be
  exempted with the bounded `secret_name_allowlist`. Only real comment tokens can apply `# nosec`.
  Source reads retain ici's bounded, symlink-safe snapshot policy, and unreadable or invalid Python
  fails closed with located `ERROR` targets instead of silently skipping analysis. Per-file PASS
  targets, counters, evidence mode, and explicit limitations make the inspected scope auditable.
  This remains part of the consolidated language-analysis work and does not change version
  `0.10.2` or authorize a release.
- **Project-respecting Python tool policy (unreleased language-analysis bundle):** Ruff continues
  to consume its structured JSON diagnostics and rule codes while leaving the project's discovered
  select/ignore/per-file configuration in control. Mypy no longer receives a global
  `--ignore-missing-imports` override: the default `mypy_profile = "project"` runs from the project
  root and preserves Mypy's normal config discovery. The explicit `mypy_profile = "ici"` overlay
  adds `--check-untyped-defs`, redundant-cast warnings, and unused-ignore warnings without changing
  import policy. Mypy error/note targets now retain reported columns. Ici's own dogfood policy
  requires Mypy and uses the overlay; the newly exposed checks were cleaned rather than suppressed.
  This remains part of the consolidated language-analysis work and does not change version
  `0.10.2` or authorize a release.
- **Configured Python runtime compatibility checks (unreleased language-analysis bundle):** Added
  the `python_compat` engine to all profiles; the registry now contains 19 descriptors while the
  default profile selections remain fast 12, standard 14, and deep 16. Enabling the deep-only
  `build`, `binary_compat`, and `integration` release-contract engines brings deep to 19. With
  `interpreters = []`, the interpreter currently running ici is checked as a required runtime;
  configured entries are optional unless repeated in
  `required_interpreters`. Each resolved executable is invoked directly with `-VV` and
  `python -B -m compileall -q -f`, while import smoke is an explicit `imports` opt-in because importing
  a module executes its top-level code. The engine validates PEP 440 `project.requires-python`,
  applies the configured or inferred Python syntax/API floor, and records precise source locations
  for floor violations. Successful checks are `MEASURED` with per-command `ToolEvidence`; optional
  unavailable runtimes warn, required unavailable runtimes are `ERROR`/`NOT_RUN`, and runtime
  incompatibilities fail required entries. Because an external configured interpreter can be
  replaced independently of ici's current process, this engine deliberately disables result cache
  key creation and reuse. The feature candidate passed EnvLens compatibility checks when invoked by
  Python 3.10.21 and Python 3.14.7. Package metadata and optional wheels are inspected without
  import/build: pyproject and each wheel retain PASS/FAIL targets, wheel filename/METADATA/WHEEL/
  RECORD identity and completeness are checked, RECORD sha256/sha384/sha512 hashes and sizes are
  verified, and symlink/special members, portable-name collisions, missing METADATA identity, and
  mismatched wheel entry points fail closed. Entry-point resolution uses bounded AST/read, accepts
  callable declarations or imported symbols, and does not treat plain assignments as callables.
  Non-canonical member paths, file/directory aliases, non-empty directory records, mismatched or
  multiple `.dist-info` roots, and duplicate singleton metadata headers are rejected. Malformed
  wheel structure becomes a located `ici.package.wheel-invalid` failure instead of losing the
  offending path in an engine-wide error. This remains unreleased and does not change version
  `0.10.2`.
- **Conservative canonical Python issue display (unreleased language-analysis bundle):** Console,
  HTML, and Markdown now share a display-only canonical rule projection for reviewed overlapping
  Python rule families. Cross-producer grouping requires the same canonical project-relative path,
  precise 1-indexed end-line/start-column/end-column locations, and an actual overlapping region;
  broad Ruff aliases require trusted semantic context, and line-only or ambiguous occurrences stay
  separate. Groups expose original finding counts, producer counts, and engine/rule/tool-version
  provenance. JSON `findings`/`targets` and baseline inventories continue to retain every original
  producer record, fingerprint, precise location, and tool identity; projection never mutates the
  suite or baseline. This remains unreleased and does not change version `0.10.2`.
- **GNU ELF target-local discarded-function evidence (local feature PR):** Added the opt-in
  `[engines.dead].cpp_linker = "auto" | "required" | "off"` policy, defaulting to `off` and
  independent of `cpp_unused`. On Linux root-CMake projects, the probe creates an isolated Release
  shadow with the `Unix Makefiles` generator, validates direct-object executable `link.txt` targets,
  proves the capability-approved GCC driver delegates to GNU `ld`, and combines `cmake`, `readelf`,
  and `addr2line` evidence. Only a uniquely mapped local/hidden function section explicitly
  discarded by section GC is emitted as an `EXACT` target-local finding. Archives, shared links,
  LTO, PIE, COMDAT/grouped sections, dynamic/export/whole-archive roots and whole-program claims
  remain outside the contract. Link-command/object/section/tool-output/time budgets and malformed,
  timeout, truncation, prevalidated command/tool-evidence, or relink failures fail closed without
  partial findings. `auto`
  records an unavailable linker scope as an optional `SKIP` target; `required` promotes it to
  `ERROR`/`NOT_RUN`.
  The final implementation also separates the existing Python dead-code heuristic into a dedicated
  helper and decomposes linker orchestration/tool-policy branches so ici's own line, complexity,
  and mypy gates validate the combined analyzer code without changing the evidence contract.
  The feature keeps version `0.10.2` and authorizes no release.
- **Bounded Python AST-shape duplicate groups (local feature PR):** Added
  `[engines.dup].python_semantic = "auto" | "required" | "off"`, defaulting to `auto`. Python 3.10
  AST shapes are collected for leaf functions/methods; local bindings are alpha-renamed and source
  layout is ignored, while control flow, operators, literal kinds/values, and source-spelled
  imported-name/attribute anchors remain exact. Groups require exact
  `sha256/semantic-shape-v1` canonical-shape equality
  and are deduplicated against identical lexical clone occurrence sets. Malformed/unsupported AST,
  lambda/comprehension, global/nonlocal, star-import, `eval`/`exec` calls or their literal
  `getattr` lookup, nested parent, trivial regions, and bounded file/region/node/serialization
  overages are conservatively excluded; `required` fails closed and `off` skips only the Python
  AST-shape pass while lexical duplicate analysis remains enabled. Results remain `ESTIMATED`
  structural clone evidence, not behavioral equivalence or full C++/whole-program semantic
  analysis. Canonical field encoding is split into typed helpers so Python 3.10 mypy and ici's
  self-complexity gate cover the shipped path. The feature keeps version `0.10.2` and authorizes
  no release.
- **Candidate Quality Zoo open-PR revision mode:** The manual candidate consumer can now validate
  an exact open same-repository toy-projects PR head before checking out its Quality Zoo
  expectations. It keeps `main` as the backward-compatible default, while PR mode requires the
  exact number and SHA, an unmerged open state, `main` as the base branch, and matching canonical
  base/head repository names and IDs; fork heads are rejected. The trusted audit helper is checked
  out separately, the exact accepted toy revision is retained as bounded machine-readable evidence,
  and candidate preflight/execution remain credential-free. No version bump or
  release is implied; ici remains at `v0.10.2`.
- **Candidate Quality Zoo manifest selection:** The read-only candidate consumer now prefers a
  checked-out `quality-zoo/candidate-manifest.json` when the exact toy-projects revision provides
  one, and falls back to the stable `manifest.json` only when the candidate manifest is absent.
  Both choices must be regular non-symlink files, the selected manifest is SHA-256 checked before
  and after candidate execution, and `quality-zoo.manifest-selection/v1` evidence records the
  selected path, source, and digest in the acceptance artifact. This keeps candidate-only
  expectations separate from the released-artifact toy gate without weakening exact toy SHA or
  candidate provenance binding. No version bump or release is implied; ici remains at `v0.10.2`.
- **Deep ThreadSanitizer profile and exact acceptance:** Added the
  deep-only `thread_sanitize` engine and direct `ici thread-sanitize` command for C++ thread-safety
  checks. Its isolated `BuildVariant.THREAD_SANITIZE` (`thread-sanitize`) uses a `-tsan` shadow and
  exact `-fsanitize=thread`, `-fno-omit-frame-pointer`, and `-g` instrumentation. CMake and qmake
  adapters receive the same TSan compile/link variant, while the generic g++ path adds a generic
  `-pthread` link. The TSan environment preserves existing `TSAN_OPTIONS` entries and appends
  `halt_on_error=1`; it never mixes TSan with the ASan/LSan/UBSan `sanitize` variant, and Python is
  explicitly unsupported for this engine. Only complete `WARNING: ThreadSanitizer:` or
  `SUMMARY: ThreadSanitizer:` signatures enter the bounded normalizer. Known defect prefixes map to
  stable rule IDs, unknown TSan wording falls back to `ici.sanitize.tsan.thread-safety-defect`,
  project locations are bounded and validated, and external frames are redacted as `[external]`.
  TSan defect prefixes are isolated from the ASan/LSan/UBSan taxonomy. A complete aggregate
  CTest/qmake sanitizer report also overrides an otherwise passing case and zero process exit, so
  runtime exit-code policy cannot turn observed race evidence into a clean result. If a framework
  reports no executed case, the aggregate diagnostic is retained as a synthetic process case rather
  than discarded.
  The real g++ race regression passes locally. PR #146 merged as `cfd7066`; its PR run
  `33717584710` and exact-main run `33718399268` passed. Toy PR #56 and candidate run
  `33737405098` then accepted all 8/8 contracts with zero runner errors. This closes the TSan
  sub-scope only: broader I4-4 resource/lifetime/security work remains open, and ici stays at
  `v0.10.2` with no release.
- **Rule-only C++ diagnostic category projection and acceptance:**
  C++ compiler, clang-analyzer, clang-tidy, and clazy findings now use the isolated
  `_cpp_diagnostic_categories.py` `tool-rule-v1` policy. The projection reads only normalized
  `family` and `tool_rule_id`; free-form diagnostic messages cannot change the category. Analyzer
  security namespaces include `security.*`, `alpha.security.*`, and `optin.taint.*`; tidy security
  includes CERT/Android CLOEXEC plus exact `bugprone-command-processor`, `bugprone-signal-handler`,
  `bugprone-unsafe-functions`, and `concurrency-mt-unsafe` rules. Explicit analyzer/tidy resource IDs and analyzer WebKit prefixes take
  precedence, analyzer fallback is `CORRECTNESS`, tidy portability/deprecated-header rules map to
  `COMPATIBILITY`, all remaining `bugprone-*`/`concurrency-*` tidy rules map to `CORRECTNESS`, and
  other tidy rules safely fall back to `MAINTAINABILITY`. Clazy combines bounded stems with stable
  exact resource/compatibility/correctness rule sets and keeps maintainability as the fallback.
  Lint `extra` records the policy ID and a count for every v3 category; the isolated helper is part
  of lint cache implementation identity. The focused C++ lint, clang-tidy, and clazy regression set
  passes `160` tests, the cache
  identity/store focused set passes `51`, and Ruff passes locally.
  PR #145 merged as `e7a9f55`; PR run `33713591229`, exact-main run `33714515219`, and the
  six-scenario category/Qt candidate run `33718024450` succeeded. No version or release change is
  implied; later feature heads require their own exact evidence.
- **Candidate Quality Zoo C++/Qt tool provisioning and acceptance:** The manual
  `candidate-quality-zoo.yml` consumer job installs `clang`, `clang-tidy`,
  `clazy`, `cmake`, `g++`, `pkg-config`, and `qt6-base-dev` so future Qt lifetime/C++ static-analysis
  scenarios can execute instead of being skipped for missing tools. Provisioning and candidate
  preflight/execution remain credential-free; local purity coverage is `31 passed` and actionlint
  passes. The provisioned six-scenario category/Qt run `33718024450` succeeded. No version bump or
  release is implied, and that evidence remains valid only for its exact scope.
- **Runtime sanitizer diagnostic normalization and acceptance:** ASan, LSan, and UBSan output is
  recognized only from bounded report signatures and normalized into deterministic `kind`, `defect`,
  detail rule identity, related stack-frame locations, and observed/project frame counts, plus a
  project-owned primary location when validation succeeds. `SanitizeEngine` preserves the finding's
  `tool_name`/`tool_rule_id` and links each
  diagnostic detail to the recorded sanitizer process evidence. Project paths are validated with
  the bounded no-follow stable-file reader; external frames are retained only as `[external]`
  related locations, while diagnostics without a valid owned location remain explicit errors. The CTest
  adapter keeps raw diagnostic transport private, bounds it to 65,536 UTF-8 bytes, and marks a
  truncated transport instead of publishing partial evidence. It removes a stale JUnit file before
  each CTest run, rejects sanitizer output attached to a nominally passing case, and treats timeout,
  process-output truncation, malformed/oversized transcripts, or diagnostics without a validated
  project location as `ERROR`/`NOT_RUN` rather than a clean result. Signal termination with a complete
  located report remains a measured `FAIL`. Real `g++` ASan/UBSan/LSan regression projects pass in
  the focused 132-test suite; the current full local Python 3.10 suite is `2,088 passed, 7 skipped`.
  PR #142 merged as `9d470ed`; PR run `33704709734`, exact-main run `33705500603`, and exact
  sanitizer candidate run `33710695336` succeeded. The separate TSan acceptance is recorded above.
  Broader safety mappings, the wider I4-4 checkpoint, and any release remain pending; ici stays at
  `v0.10.2`.
- **Candidate artifact producer (local contract and remote producer evidence complete):** Added the main-only `workflow_dispatch`
  contract and bounded provenance helpers for a non-release `ici.pyz` bundle. The workflow
  requires a full target SHA equal to the protected-main dispatch commit and verifies that commit
  remains in `main` ancestry, selects the newest exact successful `Merge Gate`, and independently
  verifies both the canonical main-push Actions run and the selected `Merge Gate` job through
  separate Actions API responses. The job/run/attempt, target SHA, name/workflow, main branch,
  completed/success conclusion, and canonical job/run/check URLs must all bind. It then performs
  two reproducible builds and an isolated smoke check. The artifact contract contains exactly
  `ici.pyz`, `ici.pyz.sha256`, and `candidate-provenance.json`, is retained for 14 days without
  overwrite, and does not change the `v0.10.2` stable tag, release, or version. Its canonical
  provenance records `candidate_run_id`, `candidate_run_attempt`, `merge_gate_check_run_id`,
  `merge_gate_job_id`, `merge_gate_run_id`, `merge_gate_run_attempt`, `merge_gate_job_url`, and
  `merge_gate_url`. The candidate reports its target commit's package version but never changes or
  publishes that version. The focused 111-test suite, live API verifier, full Python 3.10 suite,
  static/type/workflow checks, reproducible build, smoke, and real built-pyz bundle round trip pass.
  The remote producer audit is now complete: protected-main source SHA
  `7872a7b80899cbd3d40d92d18e7920cd7e2283e7` passed [main run `33688279264`](https://github.com/jihoon22-lee/ici/actions/runs/33688279264)
  with every job green, including [Merge Gate check `100442919168`](https://api.github.com/repos/jihoon22-lee/ici/check-runs/100442919168)
  and [job `100442919168`](https://api.github.com/repos/jihoon22-lee/ici/actions/jobs/100442919168), attempt 1. Main
  [ici Pages](https://jihoon22-lee.github.io/ici/ici/main/) and [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
  matched their extracted main artifact bytes, retained the exact source SHA and report titles, and passed
  the Zero-CDN audit. Candidate [run `33689056008`](https://github.com/jihoon22-lee/ici/actions/runs/33689056008)
  succeeded and published artifact ID `9869395069`, name
  `ici-candidate-7872a7b80899cbd3d40d92d18e7920cd7e2283e7`,
  [artifact API metadata](https://api.github.com/repos/jihoon22-lee/ici/actions/artifacts/9869395069),
  ZIP/API digest `sha256:640e50ecf5b099174c16f1ef5d2b5b87945329711e96f926d94c3cc04109081e`,
  size `2,277,109` bytes, and expiry `2026-09-16T22:14:38Z`. The downloaded v7 ZIP contained exactly
  `candidate-provenance.json` (`0644`, 859 bytes), `ici.pyz.sha256` (`0644`, 74 bytes), and
  `ici.pyz` (`0755`, 2,275,786 bytes), whose SHA-256 is
  `53fc75f0a073a74689babfe9ef8a4b2378995002d7d563bdc52da548fdbb9ee8`; the bundled version is
  `ici 0.10.2`. The candidate manifest byte-matched the independent verifier, and the check/job/run
  canonical API identities, workflow name, main branch, attempts, and URLs all matched. The observed
  upload ZIP preserved the required file modes; the earlier generic mode-loss assumption does not
  apply to this v7 artifact. See [`candidate artifact provenance workthrough`](docs/workthrough/2026-09-03-candidate-artifact-provenance.md).
  The follow-up documentation PR #140 is now merged at main SHA
  `cc73531ca33d5e781f027a2c55d341d29034990f`. Its exact-main
  [run `33691782482`](https://github.com/jihoon22-lee/ici/actions/runs/33691782482) was green, and the
  [verification artifact `9870465295`](https://github.com/jihoon22-lee/ici/actions/artifacts/9870465295)
  and main ici/viewer Pages were audited for trusted report contents, exact source/title identity,
  Zero-CDN behavior, and byte identity. This closes the current producer/artifact/Pages audit;
  candidate-pyz consumer injection remains a separate follow-up.
- **Candidate-to-Quality-Zoo acceptance evidence (narrow runtime slice):** The read-only
  `candidate-quality-zoo.yml` dispatch was independently audited at [run `33710695336`](https://github.com/jihoon22-lee/ici/actions/runs/33710695336)
  (attempt 1, success, exact ici workflow-main SHA
  `6df011f98be1a19092b112cb56c596dc35bcae4d`) against candidate target
  `9d470edca7ab037a24dcd6594531a822f116548b` and exact toy-projects main
  `2d0d7c0b2dcc137a782d6042438fc287bffdf570`. The producer [run `33706057540`](https://github.com/jihoon22-lee/ici/actions/runs/33706057540)
  and [candidate artifact `9875319095`](https://github.com/jihoon22-lee/ici/actions/artifacts/9875319095)
  were independently downloaded: raw ZIP SHA-256
  `4aec084b3a30ac01a1df5124fa3b42b7f51d23f66c12b490194a84549be9db27` (2,285,368 bytes),
  containing `ici.pyz` SHA-256
  `e7f1a2ce7147057538873a802715c7bf2b12e530a85070af862e02e378caceb8` (2,284,045 bytes).
  The acceptance [artifact `9876797536`](https://github.com/jihoon22-lee/ici/actions/artifacts/9876797536)
  raw ZIP/API digest is
  `e66ae2b65988abe10fc5ddb92a5c3bb6fc238ec2f77b7fd27ccfe75c24194a5f` (1,104,307 bytes).
  Its `quality-zoo.suite/v1` contains five scenarios, all `contract_verdict: PASS` with no
  runner errors: ASan UAF `FAIL`/`MEASURED`/`exact` at `src/fault.cpp:5`, LSan leak
  `FAIL`/`MEASURED`/`exact` at `src/fault.cpp:3`, UBSan signed overflow
  `FAIL`/`MEASURED`/`exact` at `src/fault.cpp:3`, sanitizer-clean `PASS`/`MEASURED` at
  `tests/test_clean.cpp:1`, and the existing Python case `WARN`. This closes only the exact
  remote dispatch and rule/status/evidence/confidence/path/line contract plus runtime
  ASan/LSan/UBSan/clean evidence. It does not close Qt lifetime, static taxonomy candidate,
  broader Q1-Q5, I4 aggregate, TSan, version, or release work. The workflow produced no Pages,
  PR-comment, publish, tag, release, or version side effect; stable `v0.10.2` remains unchanged.
- **Quality Zoo Q0 released-artifact acceptance:** [toy-projects PR #49](https://github.com/jihoon22-lee/toy-projects/pull/49)
  passed [run `33693241255`](https://github.com/jihoon22-lee/toy-projects/actions/runs/33693241255) and
  published [artifact `9870829400`](https://github.com/jihoon22-lee/toy-projects/actions/artifacts/9870829400).
  The artifact’s contract verdict was `PASS` for the stable `python.dead-private-function` scenario,
  using released ici `v0.10.2` at SHA
  `8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`; observed suite status was
  `WARN`, with exit code `0` and an empty error list. At that time the PR had exactly one sticky
  `<!-- ici-report -->` comment/marker and three product HTML links. The PR was squash-merged as
  `ed5fea2e881da77ac95482cf665e4e40bfe172f1`; exact-main [run `33694452357`](https://github.com/jihoon22-lee/toy-projects/actions/runs/33694452357)
  was green and its stable [Quality Zoo artifact `9871249913`](https://github.com/jihoon22-lee/toy-projects/actions/artifacts/9871249913)
  repeated contract `PASS`, observed `WARN`, empty errors, and exit code `0`. The product Pages
  were byte-identical to the trusted artifacts. This closes Q0 for the released-artifact boundary;
  candidate-pyz injection and Q1–Q5 remain pending. No version bump or release is implied.
- **Compiler-backed C/C++ translation-unit unused-function evidence**: `dead` now exposes
  `[engines.dead].cpp_unused = "auto" | "required" | "off"` as a C++-scope policy independent of
  the Python AST dead-code heuristic. The full verifier and standalone `ici dead` command use the
  same shared immutable project/tool/compilation preflight and context model. The registered `gcc`
  and `g++` probes use `--version` and mark the capability complete only when the observed banner
  identifies a supported GCC or Clang family. Neutral and Apple aliases follow that recorded family
  rather than their executable spelling. The compilation database digest identifies the immutable
  context captured by preflight, not a live-file lease; a database mutation is incorporated by the
  next preflight. Standalone `ici dead`
  scopes capability probes to the tools selected by `dead` plus configured
  `[doctor].required_tools`, while preserving the compilation database as the source of truth.
  With `cpp_unused = "off"`, C++ candidates are omitted from source intake and the C++ probe is
  skipped; Python dead-code analysis remains independent. Exact mode selects all owned and configured
  external, compilable C/C++ translation units in the project source inventory and requires every
  known canonical `sha256:` source configuration; contexts with `unity_build=true` and missing
  coverage are rejected rather than guessed through. Every selected compilation unit must declare
  language exactly `c` or `c++`; unknown, empty, and Objective-C-family units are rejected before
  compiler execution.
  Each selected source/configuration replays a sanitized compiler command with warning-as-error
  policy projected for diagnostics, `-Wunused-function`, `-Wno-error=unused-function`, and
  discarded `-S -o os.devnull` output, so no project object, executable, or linker result is
  produced. Only capability-approved GCC/Clang drivers and approved aliases are replayed. The
  observed compiler family wins over an alias spelling (including a `g++` alias backed by Clang):
  GCC >= 9 uses structured JSON diagnostics, while older GCC and Clang use bounded parseable text.
  Project diagnostic-option visibility flags are replaced by ici's controlled
  `-fdiagnostics-show-option`, so exact rule matching cannot be disabled by the compile command.
  Source, working-directory, and approved compiler identity are revalidated around
  execution; each unit configuration digest is recomputed from its directory/argv/output and must
  match its canonical identity. Missing or invalid context, unsafe replay, ingestion/coverage/
  configuration gaps, compiler failure, and unlocated matching warnings with no source attribution,
  malformed/nonzero/timeout/truncated output, identity changes, and configuration disagreement all
  fail closed. The unused-function replay rejects every extra operand after its `--` separator,
  including `-w` and a second `--`, before invoking the compiler. The C++ probe is atomic:
  replay/process/parser errors are fail-fast, so completed C++
  observations/findings are discarded and remaining compiler units are not run; configuration
  disagreement discovered during the final merge also discards every C++ finding. A located
  `C++UnusedFunctionsInvalidated` `SKIP` target remains for
  each previously completed and recorded compiler observation, so discarded work is traceable without presenting it as
  exact evidence. No heuristic fallback is presented as exact. For a C++-only project, unavailable
  exact context/tool state in `auto` remains `SKIP`/`NOT_RUN` with `required = false`, so suite
  aggregation reports an optional `WARN`; an explicit `required` policy remains `ERROR`/`NOT_RUN`.
  Once an exact context exists, invalid context, coverage, configuration, replay, parser, and
  identity failures remain errors in both policies. In a hybrid result,
  completed Python findings are retained with `ESTIMATED`/`python-ast-heuristic` evidence. When both
  scopes complete, native C++ findings retain exact confidence and compiler/tool-rule attribution
  (`tool_name` plus `tool_rule_id = "-Wunused-function"`) while the aggregate remains conservatively
  `ESTIMATED`; a C++ failure instead leaves the aggregate status/evidence reflecting that failure.
  Only a matching `-Wunused-function` warning whose location is outside the selected TU is counted
  in `cpp_unused_non_tu_diagnostics_excluded`; other non-TU/header/external diagnostics are ignored.
  Accepted source positions retain the compiler's logical path and line/column range only when the
  path equals the selected TU and the range fits its immutable source snapshot. Out-of-range
  `#line` or macro remapping fails closed; the physical macro-definition origin is not reconstructed.
  External-linkage symbols, templates, inline/COMDAT definitions, linker
  reachability, dynamic lookup, plugins, Qt meta-object reachability, and generated/moc/vendor inputs
  outside the source-ownership policy are not classified by this probe. `dead` result cache-key
  generation, load, and store are disabled for every result, including Python-only and hybrid
  results, until external/generated include closure and compiler binary content are modeled.
  Clang-based tooling's GCC standard-library projection binds both its bounded probes and cache to
  the resolved compiler and working-directory identities; replacement during probing fails closed.
  A local self-quality run initially exposed a FAIL in the new probe functions' complexity; the
  probe was refactored below the existing fail threshold, and the real-GCC E2E expectation was
  updated to match compiler diagnostic replay. Final local gates recorded focused regression
  `607 passed/6 skipped`, Python 3.10 full `1,966 passed/7 skipped`, Ruff check/format PASS on
  184 files, mypy PASS on 104 files, and two byte-identical 2,273,944-byte pyz builds with SHA-256
  `2a3c8b011e53d21529ee03e20b0f7eeafbf7fbfaf6b8a9e35f5445b166c88d28`; smoke PASS and packaged
  verify exit 0. ici self deep `--no-cache` was WARN with 14 engines (8 PASS, 5 WARN, 0 FAIL,
  0 ERROR, 1 SKIP), TEM 4.84, HTML `5,526,617` bytes/SHA-256
  `159ba3db668127541c4ff56ebc535138fbd5541ad86eccad45879e606e50742d`, JSON `15,590,867`
  bytes/SHA-256 `0d38d3b9daa92977b3933dd3b2bbf58531b52134d87f462d7a9271b659affe1a`, exact title
  `ici Verification Report — ici`, and Zero-CDN PASS. Viewer standalone `dead --report` under its
  configured `cpp_unused = "required"` policy was PASS/MEASURED with an exact 8/8
  source/configuration scan, 0 unused functions, a null
  `cache_key`, and 8 successful compiler evidence rows. Viewer deep `--no-cache` was WARN only
  because clang-tidy/clazy were unavailable, with 14 engines (12 PASS, 1 WARN, 0 FAIL, 0 ERROR,
  1 SKIP), TEM 4.89, HTML `355,996` bytes/SHA-256
  `9098bec837b61d2ed08c15cdb21b4b4f59741a160eb0a09dfb74d8163bb33d8c`, JSON `743,422` bytes/
  SHA-256 `069eb0dced6c835c2690b8d45da2216ffb15af233b5dc7a3f92e609fd90d67ad`, exact title
  `ici Verification Report — viewer`, and Zero-CDN PASS. Detailed local report evidence is
  recorded in the compiler-backed unused-function workthrough below.
  This closes only the narrow TU-local compiler-diagnostic slice: whole-program/linker dead-symbol
  analysis, full duplicate semantics, and the complete I4-3 checkpoint remain pending. The slice was
  accepted remotely through descriptive PR #137, `feat(dead): add compiler-backed C/C++
  unused-function evidence`. Its required PR checks passed in
  [workflow run `33675765436`](https://github.com/jihoon22-lee/ici/actions/runs/33675765436) for
  head `9c9d83cdaae02384bbc58e7cb79b4bbb098b86d3` and synthetic merge
  `f2cfce8b8a7ebc90308bb442f3a323e01ed9ef34`. Exactly one current-run sticky comment
  ([comment `5515582296`](https://github.com/jihoon22-lee/ici/pull/137#issuecomment-5515582296))
  remained and contained exactly two report links. The PR artifact and PR Pages copies were
  byte-identical, with the synthetic merge as `source_commit`, exact titles, valid UTF-8, and
  Zero-CDN checks passing:

  | Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
  | --- | --- | --- |
  | ici | 5,188,748 / `8648d7ac06fded3afaa004568a9665bb3bc2b10c7e41f1da06af41b0eb3952f8` | 15,288,643 / `f9401da10828ab3d0c1c6b9430789d25b4ef4ac15e8dbe410f0f244a584aefef` |
  | viewer | 363,787 / `0123db7d6e5c820fc0bd952a0fd55b82752b63d873b4f0502e12f676b3e71cda` | 905,151 / `edde8208502d4af5c060e556ece1650518893c7274487cca2283c02f63322f98` |

  PR #137 was squash-merged as `782589a4ef02209703e882a09cc0d8b0c7940218`, and its feature
  branch was deleted. Exact-main verification run
  [`33676873412`](https://github.com/jihoon22-lee/ici/actions/runs/33676873412), Pages build API run
  `1190632325`, and Pages workflow run
  [`33677689026`](https://github.com/jihoon22-lee/ici/actions/runs/33677689026) all succeeded.
  Main artifacts and their Pages copies were byte-identical, with `source_commit` equal to the
  merged main SHA and the same title, UTF-8, and Zero-CDN checks:

  | Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
  | --- | --- | --- |
  | ici | 5,188,748 / `7d9a23d5eb47bcf0ab82f074a85e65eb264869f8f0333318673890d75b0c4eaf` | 15,288,649 / `99d5c208a30518e0c356c4e9a26b2306a99468d51369dd91e9eaa19b71a22e19` |
  | viewer | 363,788 / `223c027a6cbbef5aa08c464f210286c6a90ae2a702451739aa94bf704648188f` | 905,152 / `152e6c2f6d2b53728f39680b3198b5fb46d1c28e915731c6a7693f85c0175557` |

  This remote acceptance does not create a release or change the public version: `0.10.2`
  remains stable while broader linker/whole-program dead-symbol analysis and full semantic
  duplicate analysis remain pending.

- **Bounded language-aware duplicate tokenization and matching**: `dup` now uses dedicated,
  line-preserving lexical normalizers for Python and C/C++ before building language-isolated Type-2
  clone windows. Python tokenization removes comments and import-first logical statements (including
  multiline imports), uses Python AST context for `match`/`case` soft keywords and imported/API names,
  and keeps identifier, integer/float/complex, string/bytes/f-string, indentation, and operator
  categories distinct while normalizing values. The C++ lexer removes comments/directives, applies
  translation-phase backslash-newline splicing with physical-line provenance, preserves punctuator
  boundaries and literal categories, and keeps Qt semantic anchors stable. Matching indexes rolling
  normalized-window seeds and verifies exact token equality while extending regions within the
  language/function/class/import/directive policy. A semantic-signal policy suppresses low-information
  Python tables and C++ array/enum-style data without suppressing real control-flow clones. Internal
  tokenizer, normalized-character, aggregate indexed-record, occurrence, seed-pair, extension, and
  raw-match budgets fail closed deterministically as `ERROR`/`NOT_RUN` with `SourceTokenizationError` or
  `DuplicateComparisonLimit` targets; they are not user configuration keys. Clone metadata now
  records `sha256/type2-region-v2`, `cpp-lexical-v1`/`python-lexical-v1`,
  `language-function-scope-v1`, and `minimum-semantic-lines-v1`. Successfully completed results remain
  `ESTIMATED` with `language-lexical-region-heuristic` provenance, while bounded failures are
  `NOT_RUN`; broader whole-program/linker-backed dead-symbol semantics and the complete I4-3
  checkpoint remain pending. This slice keeps version `0.10.2`
  and creates no release. Historical [PR #135](https://github.com/jihoon22-lee/ici/pull/135) passed
  required PR CI, its single sticky report comment, and artifact/Pages byte-match checks, then
  merged as historical `b09af5e0f0dd5f5d1ecbc33f73ab23a96f520882`. Historical exact-main run
  `33648359498` and its refreshed main
  Pages passed the same source-commit, UTF-8 title, Zero-CDN, and byte-match audit.

- **C++ function-scope classification and configuration disclosure**: compiler-backed complexity
  boundaries now retain source-spelled named functions, including function templates, conversion/
  call/subscript operators, and literal operators, with `function_kind`, `function_template`, and
  `function_origin` metadata. Lambdas are not independent function targets; their masked bodies are
  excluded from the enclosing CC/nesting metric and the exclusion count is disclosed in
  `extra.cpp_scope_exclusions.lambda`. A macro-generated function diagnosed at its expansion site
  is explicitly excluded, counted in `extra.cpp_scope_exclusions.macro_generated_function`, and is
  never mapped to the next brace in the file. The fallback scanner preserves operator names and
  blanks multiline preprocessor definitions/continuations and skips standalone macro invocations.
  Uppercase inline constructors remain source-spelled functions instead of being guessed as
  external macro calls.
  Successful configurations must agree on boundary geometry, name, kind, and provenance. Each
  configuration's clang-tidy lines/statements/parameters remain in `configuration_metrics`;
  differing values, or a conditional-preprocessor body, makes the boundary run `partial` and the
  metric confidence `low`; compiler-backed function metrics or configuration coverage that remains
  partial/low-confidence fails closed in `required`. This documentation and policy slice does not
  bump the version or create a release.

- **Compiler-backed C++ complexity function boundaries**: `complexity.cpp_boundaries` now accepts
  `auto` (default), `required`, or `off`. With an exact `CompilationContext`/compilation database
  and capability-approved direct `clang-tidy`, the dedicated `readability-function-size` check
  supplies function-boundary geometry. Cyclomatic and nesting values inside that geometry remain
  ici's masked token/brace metrics with `metric_confidence=medium`; clang-tidy line/statement/
  parameter notes are separate evidence. `auto` uses the source scanner only when context or the
  tool is unavailable; empty/unreported source-spelled definitions may remain heuristic, while
  macro-generated expansion diagnostics are excluded and counted rather than mapped. Attempted-tool,
  replay, parser, timeout, truncation, coverage, or budget failures are `ERROR`/`NOT_RUN`, not
  silent fallback. The only accepted suppression summary is when clang-tidy emits
  `Suppressed N warnings (N in non-user code).` alongside visible project diagnostics, which
  accounts for external/system diagnostics only; NOLINT, project, mixed, malformed, or
  count-mismatched suppression remains `ERROR`/`NOT_RUN`. `required` also errors on unavailable or
  partial/estimated boundaries, while `off` intentionally remains heuristic. The run uses a bounded
  caller source snapshot and mapped-source cache, revalidates source identity before replay and after
  the tool, and requires the same geometry, name, kind, and provenance to be present in every
  successfully checked configuration; missing or differing configuration coverage remains partial.
  The C++ source inventory is capped at
  2,048 source files and 64 MiB of aggregate UTF-8 source bytes. The per-run limits cover 2,048 units, 8 MiB
  per source, 64 MiB source bytes, 16 MiB mapped-source cache bytes, 1,000,000 output characters,
  10 seconds of parser time, 120 seconds per unit, and 600 seconds globally. The approved tool
  executable is re-resolved and its device/inode/mode/size/mtime/ctime identity is checked immediately
  before each process; changes fail closed. Same-line, braced declarator/default/noexcept/
  trailing-requires, function-try, and `<%`/`%>` bodies are mapped as regression cases; assigned
  `[]`/`+[]` lambda initializers cannot create phantom fallback functions. Descriptor
  reads also revalidate the resolved named path when `dir_fd`/`O_DIRECTORY` is unavailable, so
  intermediate-symlink and TOCTOU changes fail closed.

  Historical PR #130 baseline evidence is recorded in the compiler-boundary workthrough: its
  candidate builds were byte-identical at SHA
  `7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`, and its Python 3.10
  full suite was `1,626 passed, 2 skipped`. The subsequent local
  `feat/cpp-function-scope-policy` candidate had two byte-identical builds at SHA
  `2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7`; with real extracted
  `clang-tidy-21`, its Python 3.10 full suite was `1,656 passed, 2 skipped`, and Ruff check/format,
  mypy, and packaged smoke passed. The parser/source-mapping responsibilities now live in a
  dedicated helper (628 pure code lines) while the process runner remains a compatibility facade
  (487 pure code lines); this removed the initial PR run's 1,031-line self-dogfood failure without
  changing the boundary contract or established imports. Because `README.md` is embedded in
  `dist-info/METADATA`, the exact local candidate SHA and cross-repo details remain in package-
  external documentation; the local BuildScope deep `auto`/`required`, DiskMap `auto`, and LogLens
  `auto` probes produced JSON/HTML and 4/4 title·Zero-CDN checker passes, with exact/partial counts
  and the required error recorded in the [scope-policy workthrough](docs/workthrough/2026-09-02-cpp-function-scope-policy.md).

  [PR #131](https://github.com/jihoon22-lee/ici/pull/131), titled `feat(complexity): classify C++
  function scopes and metric provenance`, merged squash as
  [`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7).
  PR CI [run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495) succeeded
  with exactly one sticky marker/current run. PR ici/viewer Pages passed HTTP/title/Zero-CDN and
  artifact byte-match checks at `7,454,995` and `356,598` bytes. Exact-main [run
  `33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450) also succeeded;
  main JSON `source_commit` matched the same SHA, and main ici/viewer Pages passed the same checks
  with byte-matched artifacts: ici `7,454,995` bytes (`182a0d05…5adbb75`) and viewer `356,598`
  bytes (`fb772d4a…c0c4794`). Only the expected PR/main publish jobs were skipped. This records
  the scope-policy acceptance, not completion of broader whole-program/linker dead analysis, full
  duplicate semantics, remaining I4-4, or the broader I4 checkpoint. The version remains `0.10.2`;
  no release is created.

### Changed

- **Consolidated local acceptance record:** The final analysis-platform workthrough records the
  Python 3.10 suite, strict type/lint/action checks, reproducible ZipApp checksum, packaged smoke,
  deep self-dogfood inventory, and JSON/HTML/SARIF contract validation. It distinguishes four
  visible structural-debt warnings and two inapplicable C++ skips from failures, and explicitly
  defers cross-repository candidate acceptance and any release decision.

- **Documentation status reconciliation:** The canonical master plan and handover now record merged
  PRs #151/#152, their successful PR/exact-main CI, and exact-head-scoped Quality Zoo acceptance.
  Future Python packaging, Make/ABI/hybrid integration, reporting, broader I4/I9 work, and stable
  version `0.10.2` remain unchanged.

### Fixed

- **Make-backed sanitizer process evidence:** Sanitizer and ThreadSanitizer adapter runs now
  recognize the actual `make test` invocation as process evidence, so clean Make projects with
  successful test execution report `PASS`/`MEASURED` instead of a false `ERROR`/`NOT_RUN`.

- **Bounded clang-tidy text state transitions:** LLVM 18 multi-pair structural notes, diagnostic
  context, and generated/suppressed summaries now pass through focused state-transition helpers.
  The parser preserves atomic rejection and the existing evidence contract while its coordinator
  drops from cognitive complexity 73 to 6 (cyclomatic complexity 5); every extracted helper also
  remains below the configured warning thresholds. Generated-warning summaries and header-filter
  hints now terminate structural-note context, so a detached empty note after either boundary is
  rejected atomically; focused tests also pin multi-pair related-note order and locations. Stable
  version `0.10.2` remains unchanged.

- **Dogfood type-policy cleanup:** The extracted test interpreter and deep-quality mixins no longer
  carry eight stale `attr-defined` suppressions. The stricter project Mypy overlay now reports zero
  type findings across all 133 source files, while the ordinary project profile remains clean.
  This is validation cleanup for the unreleased analysis-platform work; stable version `0.10.2`
  remains unchanged.

- **Self-analysis type and cancellation cleanup regressions:** The unreleased SARIF, artifact
  provenance, Python packaging, CLI, and ELF compatibility paths now retain explicit result types
  under the project Mypy policy instead of leaking ambiguous local/container inference. The
  trusted candidate Merge Gate helper also types its mixed JSON output explicitly and passes a
  direct Mypy run. HTML
  atomic publication uses unconditional `finally` cleanup, so ordinary failures and process
  cancellation both remove the unpublished temporary file without catching control-flow
  exceptions. This is unreleased validation hardening only; version `0.10.2` remains unchanged.

- **Test-engine import direction:** Coverage probing now calls a runner hook supplied by
  `TestEngine` instead of importing its owning module from `test_interpreter` at runtime. Existing
  patchability and bounded process behavior are preserved while removing the
  `test -> test_interpreter -> test` dependency cycle reported by ici's own cycle engine.

- **Integration configuration maintainability:** Typed integration-case parsing now separates
  case identity, scalar policy, environment, and assertion construction. The same bounds and error
  paths are preserved while the execution-contract parser stays below ici's own critical
  cyclomatic threshold.

- **Wheel-inspection module boundary:** Source-package discovery and untrusted wheel archive
  inspection now live in separate cache-identified modules. Both modules remain below the hard
  file-size policy, the wheel coordinator's cyclomatic complexity falls from 57 to 5, and static
  entry-point resolution cognitive complexity falls from 76 to 9 without weakening
  canonical-path, metadata, RECORD, entry-point, or byte-bound checks.

- **Test-quality module boundary:** Deep-profile slow/flaky/mutation observations now live in a
  dedicated cache-identified mixin while `TestEngine` retains the execution, coverage, and TEM
  orchestration. The bounded runner is injected through the existing patchable engine binding;
  ici's own pure-code count drops to 956 lines for `test.py` and 547 for `test_quality.py`.

- **Configuration validation boundaries:** Public `ici.config_schema` imports remain compatible,
  while dependency-free primitives, opt-in analysis-contract rules, and project path containment
  now live in focused internal modules. `config_schema.py` drops from 1,179 to 589 lines, and the
  formerly critical integration/Python-package/path validators are split below the cyclomatic hard
  limit without weakening exact dotted errors, shell-free argv rules, or containment checks.

- **Binary and integration execution coverage:** Focused engine tests now exercise a
  manifest-backed ELF success path, allowed non-ELF evidence, and the required-empty-manifest
  error state in addition to parser and policy rules. Integration coverage now also distinguishes
  optional assertion warnings, optional/required empty suites, and invalid interpreter targets;
  focused line coverage reaches 87% for `binary_compat.py` and 81% for `integration.py`.

- **Deterministic ZipApp bootstrap entry ordering and acceptance:** The packaging entrypoint
  now delegates to a small `scripts/run_shiv.py` wrapper inside the selected Python 3.10+ helper
  interpreter. The wrapper sorts shiv 1.0.8's private bootstrap resource entries by their archive
  name before invoking shiv, closing a cross-checkout divergence caused by filesystem-dependent
  `importlib.resources` iteration. `scripts/verify-reproducibility.sh` now rejects duplicate
  archive members and requires the canonical `site-packages/`, `_bootstrap/`, `environment.json`,
  and `__main__.py` entry order. This scope fixes archive entry ordering only; it does not claim
  platform- or zlib-independent byte identity. PR #150 merged as `6ee08b1`; PR run `33731740155`
  and exact-main run `33732817172` passed, and candidate producer run `33733780877` emitted the
  audited artifact used by later Quality Zoo acceptance. No version bump or release is implied;
  ici remains at `v0.10.2`.

- **Hermetic and reproducible ZipApp builds:** `scripts/build-pyz.sh` now exports two
  independently scoped, frozen requirement files from `uv.lock`: the shipped runtime graph
  (`--no-dev`) and the packaging-tool group (`--only-group package`, currently `hatchling` and
  `shiv==1.0.8`). Both installs require lock-provided hashes and wheels, use copied files, and target
  Python 3.10; packaging tools stay outside the shipped runtime graph. The build resolves one
  selected Python 3.10+ helper interpreter and uses it consistently for package/build,
  cleanup, and assembly helpers rather than relying on a caller's bare `python3`. It forces the
  canonical epoch `SOURCE_DATE_EPOCH=1700000000` (2023-11-14 22:13:20 UTC), `PYTHONHASHSEED=0`,
  Python UTF-8, the C locale, `TZ=UTC`, and `umask 022`, removes machine-specific metadata and
  target locks, normalizes installed archive inputs to `0644` and directories to `0755`, and
  rejects symlinks or other unsupported filesystem entries. CI and the build entrypoint require uv
  `0.12.5`.
  A bounded no-follow assembler pre-checks inputs with nonblocking `lstat`/open semantics, so FIFO
  and other special entries are rejected without blocking. It rejects symlink/special output
  targets, stages each executable in the opened output directory, and creates hard-link backups
  for every existing output before publication. Each name is replaced atomically in its own
  directory; if a replacement or post-publication check fails, the previous consistent output set
  is restored (or names absent before the build are removed). Final `dist/ici.pyz` and `dist/ici`
  contents and modes are checked for byte identity and `0755`. The reproducibility verifier builds
  twice under adversarial umasks, source epochs, hash seeds, and time zones, then checks canonical
  ZipApp timestamps/modes and unchanged source status. No version bump or release is implied; ici
  remains at `v0.10.2`.

- **Sanitizer clean-result compatibility:** Restored the pre-ThreadSanitizer generic C++ clean
  message (`AddressSanitizer and UndefinedBehaviorSanitizer completed`) after the shared sanitizer
  refactor accidentally changed it to an abbreviated label. ThreadSanitizer retains its own
  explicit clean message. This keeps digest-bound Quality Zoo expectations compatible without a
  version bump or release; ici remains at `v0.10.2`.

- **Clang-tidy explanatory-note aggregation**: ordinary `clang-tidy` and
  `clang-analyzer-*` explanation notes are now attached only to the immediately preceding primary
  in the same contiguous diagnostic stream through `CppDiagnostic.related_diagnostics`; a new
  primary starts a new group. Their project-relative locations and messages are exported through
  `Finding.related_locations`, and note fix-its remain available to the primary finding's
  remediation and `extra` metadata. Finding canonicalization orders related locations
  deterministically by canonical path, line/column region, and label. JSON and HTML retain the
  complete related-location inventory; the GitHub Markdown view renders non-informational,
  unsuppressed rows with a 100-row-per-engine bound and an omission notice. Warning, violation,
  diagnostic-family, and finding counts therefore include only actionable primary diagnostics. A
  note with a conflicting check rule or without a preceding primary diagnostic is rejected
  atomically. Compiler diagnostics and Clazy's rule-owned `ClazyNote` behavior are unchanged.
  The compiler-backed function-boundary parser consumes the nested function-size notes as
  structural evidence without flattening lint findings. Native-only related evidence is rendered
  even when an engine has no legacy target rows. Duplicate native occurrences remain a multiset
  when fingerprints collide; external locations remain non-links, while HTML and Markdown retain
  accessible labels and exact line/column coordinates. `e1a665d` refactors Markdown detail rendering
  into bounded target, related-location, and snippet helpers without changing that contract.
  Focused Python 3.10 verification across five related test files passed (`177 passed, 6 skipped`);
  Ruff check/format and mypy (`98` source files) also passed. An earlier full local attempt
  (`1750 passed, 7 skipped, 10 failed`) exposed the boundary-consumer mismatch; `e86c982` resolves
  that downstream contract and the subsequent reporter follow-up preserves the same evidence
  across outputs.

  The final Python 3.10 gate passed with `1768 passed, 2 skipped`; the two expected environment
  skips require unavailable `clang++` and `clazy`, while real LLVM 21 clang-tidy tests ran. Ruff
  check/format and mypy (`98` source files) passed. Two reproducible package builds are
  byte-identical at `2,242,724` bytes with SHA-256
  `3602c2cb1b6998a54f00bf809a88d81617bec58c891bfaf12bf22bc882e71890`, and packaged smoke passed.
  An intermediate no-cache self-check correctly failed when the newly expanded Markdown function
  reached critical complexity 31; `e1a665d` split that path without changing output. The final
  no-cache self-check exits 0 with suite WARN, 7 PASS/5 WARN/0 FAIL/0 ERROR/1 SKIP, test
  `1768/1770`, TEM `4.84`, line/function/branch coverage `89.1%/96.8%/81.5%`, complexity 25, exact
  UTF-8 title, and Zero-CDN HTML. [PR #134](https://github.com/jihoon22-lee/ici/pull/134)
  subsequently merged as `b5ebeaecee1737973b407d328bd5d655eca7256a`; PR run
  [`33616285870`](https://github.com/jihoon22-lee/ici/actions/runs/33616285870), exact-main run
  [`33617482194`](https://github.com/jihoon22-lee/ici/actions/runs/33617482194), the single sticky
  report comment, and independent artifact/Pages byte, title, and Zero-CDN checks all passed. The
  version remains `0.10.2`; no release is created.

- **Bounded heuristic source evidence for `dead` and `dup`**: both engines now consume the same
  stable, project-contained UTF-8 source snapshot instead of independently opening files. The
  intake lexically normalizes and sorts unique project-relative paths, rejects escaped paths,
  symlinks, unsupported extensions, invalid UTF-8/NUL text, missing files, and unsafe reads, and
  fails closed with a located `ERROR`/`NOT_RUN` result. It caps each intake at 8,192 unique
  candidate paths, then at 2,048 owned/analyzed source files, 8 MiB per file, and 64 MiB of
  aggregate source bytes; policy-excluded files do not consume the owned-file cap.
  - Generated/autogen and moc forms (`moc_`, `qrc_`, `ui_`, `mocs_compilation`, `.moc`) plus
    common vendor/dependency directories are excluded by default. Owned C/C++ headers (`.h`,
    `.hh`, `.hpp`, `.hxx`) are discoverable for `dup`; standalone `.moc` is discoverable but
    remains generated and therefore needs `include_generated = true`. Each engine exposes
    independent literal-boolean `include_generated` and `include_vendor` opt-ins; a path with
    both properties remains excluded until both switches are literally `true`, and the defaults
    remain `false`. Exclusion file counts are unique paths even when reason counts overlap.
  - The bounded reader prechecks every path component for symlinks on platforms without
    directory-relative open support, then verifies file identity and performs a second content
    read to reject changes during intake. Injected resource limits must be positive integers;
    malformed values fail closed.
  - `dead` captures Python source discovery once and reuses that snapshot for ordering and intake.
  - `dead` preserves PASS location targets for clean Python sources and reports its AST
    reachability/name-reference result as `ESTIMATED` with `python-ast-heuristic` provenance.
    `dup` keeps PASS location targets for analyzed files, isolates Python and C/C++ matching, and
    records stable `sha256/type2-region-v1` clone fingerprints while remaining
    `ESTIMATED`/`token-region-heuristic` evidence.
  - At this source-intake-only stage, broader whole-program/linker-backed dead-symbol evidence and robust
    language tokenization for full duplicate semantics remained pending; the later bounded
    language-aware lexical follow-up is recorded above. That earlier slice did not close I4-3 or
    create a release.
  Local Python 3.10 focused evidence for this slice is 79 source-input tests and 238 directly
  related config/dead/dup tests passed. The final complete local suite is green at
  `1764 passed, 2 skipped` out of 1,766 collected. Ruff check/format and mypy (98 source files)
  are clean. Two reproducible package builds are byte-identical at 2,240,881 bytes with SHA-256
  `715bddd5d76540f97d6f78c9349a5177ce5935a80925a5761ea39fb0988d9b0d`, and the packaged smoke
  wrapper passes. Source self-verify exits 0 with WARN(Pass 7, Warn 5, Fail 0, Error 0, Skip 1),
  test `1764/1766`, TEM `4.84`, line/function/branch `89.1%/96.8%/81.5%`, HTML `7763578`
  bytes, the exact title, and zero external resource references. The version remains `0.10.2`;
  no release is created.

  [PR #133](https://github.com/jihoon22-lee/ici/pull/133), titled `fix(analysis): make heuristic
  source evidence bounded and deterministic`, has its first implementation workflow green in
  [run `33605000619`](https://github.com/jihoon22-lee/ici/actions/runs/33605000619): `Verify &
  Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`, `Publish PR Report & Sticky
  Comment`, and `Merge Gate` all succeeded. The [sticky comment](https://github.com/jihoon22-lee/ici/pull/133#issuecomment-5506324653)
  contains exactly one `github-actions` marker/current-run comment. Its extracted artifact HTML
  and PR Pages are byte-identical; both Pages responses have UTF-8 exact titles and zero external
  resource URLs:

  | Report | HTML bytes | SHA-256 | Pages |
  |---|---:|---|---|
  | ici | 7,701,814 | `071d83ef1fac4d39102bcb8eecad68d614dda736d74a6b3a93b210c9feecf38b` | [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/133/) — `ici Verification Report — ici` |
  | viewer | 358,047 | `9e7e295e8d28fe0633039f58099c82a5914d30cb6fcd8c9f2ba82d25e84c4305` | [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/133/) — `ici Verification Report — viewer` |

  PR #133 was subsequently squash-merged into `main` at
  [`fdc797a0c71c46d9301db2569928468ff42e24af`](https://github.com/jihoon22-lee/ici/commit/fdc797a0c71c46d9301db2569928468ff42e24af).
  Exact-main [run `33607859423`](https://github.com/jihoon22-lee/ici/actions/runs/33607859423)
  passed all required checks. The merged main artifact and Pages remained byte-identical and
  passed UTF-8 exact-title and Zero-CDN checks:

  | Report | HTML bytes | SHA-256 | Main Pages/title |
  |---|---:|---|---|
  | ici | 7,701,815 | `dc2f0c83206881eccb83a41dde336c1656ab78bb7858675090319079a9ab212a` | [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/) — `ici Verification Report — ici` |
  | viewer | 358,047 | `a212609c54fe6fa10cd8f6abe3318c0094f9b3fd23ba9b7570f59f46612d1d30` | [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/) — `ici Verification Report — viewer` |

  The merged PR branch was deleted locally and remotely. This closes the bounded source-evidence
  implementation delivery recorded at that point; the later bounded language-aware duplicate
  follow-up is recorded above. Broader whole-program/linker-backed dead-symbol evidence, full duplicate
  semantics, and the rest of I4-3 remain pending, so no new release is authorized.

- **Python function metric scope boundaries**: cyclomatic and cognitive complexity now measure each
  named function independently instead of charging nested function, class, and lambda bodies to
  the enclosing function. Definition-time decorators, defaults, annotations, class bases, and
  class keywords remain in the enclosing metric, while nested named functions and methods continue
  to receive their own file/line targets. Async nested loops, inherited loop state, lambdas, class
  bodies, definition expressions, and comprehensions have explicit regression coverage.

- **Explicit test execution state across adapters and reporters**: `TestCaseResult` now has a
  backward-compatible trailing fourth field, `executed: bool = true`, so `passed = false` can
  distinguish an executed failure from a collected test that never ran. CTest JUnit `<skipped>`
  and `status="notrun"`/skip/disabled/blacklisted, together with stdout `Not Run`/`Disabled`,
  become `executed = false`. The QtTest parser applies the same state contract per XML
  `<testcase>`: skip and explicit skipped cases are not executed; `xfail` is an executed pass,
  while `xpass` and unknown result states remain executed failures (fail-closed). In qmake, the
  `make check` transcript is authoritative at one scope per test binary; QtTest XML only enriches
  that binary's failure detail, so qmake does not claim every function-level skip as an individual
  aggregate scope.
  The test engine exposes `skipped_tests` and per-suite skipped counts, and the HTML test view
  renders skipped cases separately. Pytest verbose and terminal-summary fallbacks preserve
  `SKIPPED`, treat `XFAIL` as an executed expected failure and pass, and fail closed on `XPASS`.
  Per-test markers and terminal-summary counts for all three states count as parseable pytest
  evidence, including runs with no ordinary `passed` case. When a terminal summary is present,
  its counts are authoritative; repeated collection/interruption lines are not summed as extra
  failures. The unittest fallback maps `ok` to an executed pass, `skipped` to a collected but
  non-executed case, `expected failure` to an executed expected pass, and `unexpected success`,
  `FAIL`, or `ERROR` to executed failures. A test-engine run in which every collected Python or
  C++ test was skipped is `ERROR`/`NOT_RUN` when required and `SKIP`/`ESTIMATED` when optional;
  an actual test or collection failure takes precedence over that all-skipped classification.
  Coverage output is not accepted as test execution evidence.
  Sanitizer policy now treats required all/mixed missing test
  execution as `ERROR`/`NOT_RUN`; optional all-missing as `SKIP`/`ESTIMATED`; optional mixed clean
  execution plus missing cases as `WARN`/`ESTIMATED`; and optional actual failure plus missing
  cases as `FAIL`/`ESTIMATED`. The version remains `0.10.2`; no release is created.

- **PR #132 CI-derived report and tool-path hardening**: Python test-output parsing is isolated in
  `test_output.py` so the `test` engine remains below the repository line gate without changing
  its compatibility facade. GitHub Markdown now caps each engine's target table at 100 rows and
  bounds the appended Step Summary to 900,000 UTF-8 bytes, preserving valid multibyte text and
  explicitly directing readers to the complete JSON/HTML reports when rows or bytes are omitted.
  Workflow annotations are capped at 50 entries in deterministic severity order, with FAIL/ERROR
  targets selected before WARN/SKIP and one omission notice. CI also persists the project
  `.venv/bin/python` as `ICI_PYTHON` and `.venv/bin` on `GITHUB_PATH` before standalone
  self-verification, so the installed ruff/pytest tools remain discoverable by ici. The first PR
  CI run exposed the unbounded summary/annotation and tool-path issues. The post-fix local
  source-checkout self-verification exits `0` (`WARN`: 7 passed, 5 warnings, 1 skipped), its
  valid UTF-8 Step Summary is `100,609` bytes, and both `ruff` and `pytest` are reported ready.
  Python 3.10 has `1,686` collected tests with `1,684` passed and `2` skipped; Ruff check/format
  and mypy (`97` source files) pass; two builds of the `2,235,838` byte package are identical at
  SHA-256 `a6e437ba08336d4ced2eb02752be3ec5849d029fa8bff2cbca182956b6b31e9f`; packaged smoke
  passes. Final PR run `33601774411`, its single sticky comment, and Merge Gate passed. PR
  artifact/Pages pairs are byte-identical: ici `7,513,806` bytes at SHA-256
  `f6b39e7e852a5ca2039bef9287e09359ee082dca9d7dbccc644db1bf0fae0406`, viewer `356,773`
  bytes at SHA-256 `5bbd432739ccbecf3f36afd882beabff042889a371c12b42e6551e0617bcad82`;
  both are valid UTF-8 with exact titles and Zero-CDN. PR #132 was squash-merged at `5a7a23f`,
  and exact-main run `33602697235` passed. This closes the corrective PR, not I4-4 or a new
  release. The version remains `0.10.2`; no release is created.

## [0.10.2] - 2026-09-02

### Release discipline

- `feature`, `test`, `refactor`, `docs` PR은 버전을 자동으로 올리거나 stable release를 만들지
  않는다. `v0.10.1`은 공개된 v0.10.0의 production warning-policy 결함을 보정하는 corrective
  stabilization이며, pre-release/candidate artifact는 stable이 아니다.
- `patch`는 이미 공개된 stable artifact의 defect·security·compatibility 수정에만 사용한다.
  다음 `minor`는 사용자에게 보이는 응집된 roadmap checkpoint로서 ici 전체 gate, 실제 도구
  E2E, candidate cross-repo/toy 검증, PR/main CI·Pages, docs/CHANGELOG, I4-3/I4-4와 real
  toy-projects/quality-zoo 검증이 끝날 때까지 미룬다. 하나의 PR이 하나의 릴리스를 의미하지 않는다.

### C++ tool evidence corrections

- **Approved external source previews**: exact, sanitized compiler include roots may be used to
  validate Ubuntu clazy 1.11 Qt macro source previews, but external locations are always exported
  as `[external]`. The reader follows only approved roots, opens regular files with no-follow
  semantics, checks the file identity before and after the read, and enforces a 1,000,000-byte
  (1 MB) aggregate source-context budget plus an 8,192-character line bound. A root, file, identity, preview,
  or budget violation fails closed without retaining a partial diagnostic.
- **Atomic clazy process failures**: every nonzero clazy exit remains an atomic engine `ERROR`;
  no parsed diagnostics or partial clean result is retained. The bounded evidence summary reports
  only exit status, per-kind counts (`fatal`, `error`, `warning`, `note`, `remark`), processing/output
  flags, and a bounded source label; raw tool prose and host paths are not copied into the error.
- **Bounded CTest JUnit evidence**: CTest JUnit files are read through a stable regular-file,
  no-follow boundary up to 1,000,000 bytes (1 MB), with the bounded CTest stdout parser as fallback. LeakSanitizer,
  AddressSanitizer, and UndefinedBehaviorSanitizer markers are classified as bounded diagnostic
  messages, while raw stacks and source paths are omitted.

### Verification boundary

- Local Python 3.10 verification passed the focused C++/CTest regression set (`161 passed`) and the
  full suite (`1,538 passed, 4 skipped`).
- Exact Ubuntu 24.04 + Qt 5 + clazy 1.11 evidence recorded 12/12 full-lint units, an accepted
  targeted external macro note rendered at `[external]`, and an unsuppressed CTest 8 run with
  9 cases reporting a LeakSanitizer diagnostic.
- Any suppression work belongs to the toy repository experiment only; it is not an ici policy or
  an ici suppression contract.

### Selected GCC standard-library replay

- **Clang-based tooling follows the selected GCC**: when an exact compilation replay uses the
  capability-approved `g++`, ici verifies the replay executable by resolved file identity, probes
  that same driver once as `c++` and once as `c` with only sanitized `-m*`/sysroot selectors,
  and subtracts the C search roots from the C++ search roots. The remaining libstdc++ directories
  are appended in compiler-reported order as `-nostdinc++` followed by ordered `-isystem` pairs for
  both clang-tidy and clazy. Probe output is bounded and malformed, missing, timed out, truncated,
  or unresolved projections fail closed before the analyzer runs; the two probe records are retained
  as `ToolEvidence`. Projection applies only to C++ translation units, and a compiler file identity
  change invalidates the cache or fails the in-flight probe atomically.
- **Dual-GCC regression evidence**: the toy-projects PR #38 run
  [33531285208](https://github.com/jihoon22-lee/toy-projects/actions/runs/33531285208) failed its
  Qt 5/Qt 6
  deep checks because Clang-based clazy selected the newest installed libstdc++ instead of the
  compilation database's GCC. On Ubuntu 24.04 with GCC 13 and GCC 14 installed, the fixed local
  `dist/ici.pyz` projected `/usr/include/c++/13`, `/usr/include/x86_64-linux-gnu/c++/13`, and
  `/usr/include/c++/13/backward`; clazy exited 0 for all 12 sources, with 2 include-search probes,
  while the expected warnings remained present.

### Public release evidence

- **v0.10.2 is public**: the [release](https://github.com/jihoon22-lee/ici/releases/tag/v0.10.2)
  is non-draft/non-prerelease, and its tag resolves to exact `main` commit
  `3b50dd4c485ddab212beb23ff820e82286a06e77`. The [exact-main CI run
  33541134010](https://github.com/jihoon22-lee/ici/actions/runs/33541134010) passed verification,
  Qt 5/Qt 6, trusted main publication, and `Merge Gate`; the PR publisher was skipped as expected
  for a `main` push.
- [Release run 33541928666](https://github.com/jihoon22-lee/ici/actions/runs/33541928666) passed
  both `Validate Release Provenance` and `Build & Publish Release`. The release contains exactly
  nine assets: `ici.pyz`, `ici.pyz.sha256`, `ici-self-report.html`, `ici-self-report.json`,
  `viewer-report.html`, `viewer-report.json`, `icirv`, `icirv-gui`, and `icirv-gui.README.txt`.
  The published `ici.pyz` SHA-256 is
  `8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4`.
- Independent main Pages checks returned HTTP 200 and `text/html` with the exact ici/viewer report
  titles and zero external resource URLs. The complete asset table and command-level evidence are
  recorded in [`v0.10.2 public evidence workthrough`](docs/workthrough/2026-09-02-public-v0.10.2-evidence.md).

## [0.10.1] - 2026-09-01

### Fixed
- **Diagnostic tooling warning policy**: exact C++ compilation contexts may keep production
  warning gates such as `-Werror`, `-Werror=<rule>`, and `-pedantic-errors`, but clang-tidy and
  clazy now consume a diagnostic-only projection that cannot turn an ordinary finding into an
  adapter process failure. Plain `-Werror` is removed, rule-specific escalation is demoted to
  `-W<rule>`, and `-pedantic-errors`/`--pedantic-errors` become `-pedantic`. GCC's legacy
  `-Werror-implicit-function-declaration` alias is likewise demoted without disabling its warning;
  `-Wno-error*`, semantic compile flags, include paths, defines, and other warning selections remain
  exact. Every generated argument is revalidated against the replay safety policy, so crafted
  suffixes cannot resurrect rejected preprocessor, assembler, or linker forwarding such as
  `-Wp,-MD`, `-Wa,...`, or `-Wl,...`.
- The clazy compiler-wrapper provider now uses the same projected compiler arguments as
  `clazy-standalone` and clang-tidy before restoring ici's controlled `-Wall -Wextra
  -fsyntax-only <source>` suffix. This closes the provider-specific bypass that would otherwise
  retain a production `-Werror` even after the shared projection was corrected.

### Verification
- The regression contract covers shared flag demotion, malformed replay rejection, clang-tidy,
  clazy standalone, and the clazy compiler wrapper. Adversarial nested error forms and
  preprocessor/assembler/linker forwarding projections fail closed before any tool invocation.
  Linux actual-process fixtures now carry
  `-Werror` in both clang-tidy and Qt/clazy compilation commands, so CI and release jobs with
  `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1` must prove real findings remain parseable diagnostics.
- The final local Python 3.10 suite passed `1,526` tests with four expected missing-tool skips;
  focused adapter tests, Ruff check, and Ruff format check also passed. The trusted PR, exact-main,
  and release evidence are recorded after their respective remote gates complete.

### Integration status
- The public v0.10.0 artifact passed its release provenance and artifact audit. The first
  toy-projects BuildScope B5 PR run then exposed this warning-policy defect on both Qt 5 and Qt 6:
  all 12 clazy translation units exited nonzero even though compilation-context and test evidence
  were exact. v0.10.1 shipped the corrective warning-policy projection from exact main
  `326a12abd4ac56cd88949c15c7748877e713531c`; exact-main CI run `33519475182`, release run
  `33521155513`, and the nine-asset public release audit passed. The later BuildScope rerun exposed
  the separate external macro-context and CTest evidence defects recorded under `[Unreleased]`.

## [0.10.0] - 2026-09-01

### Added
- **I4-2 Qt analysis**: C++ `lint` now probes the canonical `clazy` capability using
  `clazy-standalone` first and the distribution `clazy` wrapper as a recorded fallback provider.
  `clazy = "auto" | "required" | "off"` controls optional, required, and disabled execution;
  the explicit `clazy_profile = "level0" | "level1"` default is independent of the global
  analysis profile, while bounded `clazy_checks` enables intentional level2/manual noisy checks.
- The clazy adapter replays only covered production units from the immutable compilation context.
  Standalone invocations use `--checks`/`--only-qt`; wrapper invocations pin the approved `clang++`
  through `CLANGXX` and pass `CLAZY_CHECKS`. Both providers use a replacement environment, closed
  stdin, no shell, no compilation-database reread, no `-p`, and no `--fix`.
- Strict clazy diagnostic parsing preserves `-Wclazy-*` rule IDs, source/line locations, and notes
  as `family = "clazy"`. QObject/connect/signal/slot, lifetime/ownership, Qt compatibility/API,
  and remaining checks map to correctness, resource, compatibility, and maintainability finding
  categories respectively; source edits are never applied automatically. Mixed ordinary compiler
  warnings are bounded and validated atomically, then excluded because the compiler lint replay
  reports them separately; they no longer turn otherwise valid clazy output into a parser error.
  Ubuntu Noble's clazy 1.11 legacy raw-source/caret/replacement context is accepted only when the
  raw source line exactly matches the project source line at the located diagnostic and is followed
  by at most one bounded replacement preview. Source mismatches, forged or extra previews, and all
  other malformed legacy context are rejected atomically without retaining partial findings. Rule
  selection, diagnostic construction, and context-state consumption are separated so the strict
  parser remains below the critical self-analysis complexity threshold.
- Qt generated-code verification inspects bounded source-scope `.ui`, `.qrc`, and `Q_OBJECT` inputs
  and proves `ui_<stem>.h`, `qrc_<stem>.cpp`, and moc forms (`moc_<stem>.cpp`, `<stem>.moc`, or
  `mocs_compilation.cpp`) through exact compilation-context linkage. Qt 5/Qt 6 major evidence is
  reported from exact include/define/compiler replay and is a compatibility PASS only after a
  successful replay. Project-contained indirect includes are followed with a bounded traversal;
  definitely disabled `Q_OBJECT` declarations are ignored, duplicate generated stems fail closed
  as warnings, and structural linkage is never a PASS without a successful compiler replay. Exact
  compiler analysis also covers Qt-generated moc/rcc compilation units from the database.
- Clazy execution is bounded to 2,048 translation units, 120 seconds per unit, 600 seconds global,
  and 1,000,000 output characters. The analysis cache now includes the clazy/tooling/codegen helper
  implementations and `.ui`/`.qrc` source inputs. CI and release workflows install clazy and set
  `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1` so actual-tool E2E coverage cannot silently skip.
- Capability probing continues through declared executable aliases when an earlier candidate cannot
  execute or report a valid version, while preserving the first failure if every candidate fails.
- Self-dogfood maintainability now splits candidate probing, C++ source masking, clazy parse state,
  and per-kind Qt generated linkage into focused helpers. The four new critical-complexity findings
  (31/35/27/36) are eliminated, generated-linkage branch variables are independently typed, and a
  repository test prevents I4 analysis helpers from exceeding complexity 25 again.

### Verification
- **I4-2 PR acceptance**: [PR #122](https://github.com/jihoon22-lee/ici/pull/122) exact head
  `c3a8fe21639cecef395f0bc28777066401927da0` passed
  [run 33499500259](https://github.com/jihoon22-lee/ici/actions/runs/33499500259) with
  1,517/1,517 tests, all four actual compiler/clang-tidy/clazy process E2Es, Qt 5 and Qt 6 builds,
  self/viewer dogfood, report publication, and Merge Gate. Exactly one current sticky comment linked
  both reports. PR ici/viewer Pages were 6,583,501/356,366 bytes with SHA-256
  `b651fab1a528ae3b82f0db195322eae6038a4d5cc9492a4fbe86ae2171c9a465` and
  `2d26731cadbb6e83fa6b7f5a8fe99ae4eb830df2851905e45e130d66e3fbcc13`; both returned HTTP 200,
  the expected title, and zero external executable/display assets.
- **I4-2 exact-main acceptance**: squash merge commit
  `9b3a88f7b216a9a82a988fe2d6d1ba7b35cc2327` passed
  [run 33500281653](https://github.com/jihoon22-lee/ici/actions/runs/33500281653) with the same
  1,517 actual-tool tests, Qt matrices, dogfood gates, trusted main publication, and Merge Gate.
  Main ici/viewer Pages were 6,056,629/345,254 bytes with SHA-256
  `9acda9e39efd6e084e6d7b36c1bffa1f8eca5fc27709fd77f91f935a6b466238` and
  `7bedb81c24f2cea10178377ae0280a596b16191039e5e8d9f978eb5b1eae666a`; both passed the same
  HTTP/content/title/Zero-CDN audit. The local final gate passed 1,513 tests with four expected
  missing-tool skips, Ruff, mypy over 93 source files, pure-Python pyz build, and packaged smoke.

### Documentation and integration status
- v0.9.1의 릴리스 provenance·9개 artifact 독립 감사와 toy-projects BuildScope B4 교차 검증
  (PR #36, released ici v0.9.1, PR/main CI·sticky comment·Zero-CDN Pages)을 인수인계와
  실행 계획에 기록했다. I4-1 release boundary와 B4 precondition은 닫혔으며, I4-2 ici 구현은
  local·PR·exact-main acceptance까지 완료됐다. 이 릴리스 시점에는 v0.10.0 release artifact와
  toy-projects BuildScope B5 교차 검증, I4-3/I4-4가 pending이었으며 I4 전체 checkpoint도
  미완료였다. B5는 이후 BuildScope 0.5.0 공개와 함께 완료됐다.

## [0.9.1] - 2026-09-01

### Fixed
- **LLVM 18 clang-tidy structural-note parsing**: the bounded clang-tidy adapter now recognizes
  the located, message-less separator note emitted by
  `bugprone-easily-swappable-parameters`, preserves the following concrete note under its parent
  rule, and still rejects an empty note that has no diagnostic context. This prevents valid Qt/C++
  analysis from being promoted to an atomic parser `ERROR` without weakening unknown-output or
  diagnostic-accounting checks.

## [0.9.0] - 2026-09-01

### Added
- **I4-1 compiler and clang-tidy analysis**: C++ lint now replays only approved, exact,
  sanitized compilation-context commands without rereading the compilation database directly.
  GCC 9+ uses structured JSON diagnostics, while Clang and unknown-version fallbacks use bounded
  parseable fix-it text; parsing is atomic and preserves normalized locations, rule IDs, analyzer
  families, child diagnostics, and read-only fix-it suggestions.
- Clang-tidy supports `auto`, `required`, and `off`, with explicit checks taking precedence over
  built-in defaults and an explicit project config taking precedence over bounded `.clang-tidy`
  discovery. Config files are contained by the project root; parent-of-project discovery and
  `ExtraArgs`/`ExtraArgsBefore` compiler-argument injection and `InheritParentConfig` parent
  inheritance are rejected. Compiler, clang-tidy,
  and Clang Static Analyzer diagnostics retain separate families; analyzer correctness findings are
  distinct from ordinary clang-tidy maintainability findings.
- Missing or malformed context/output, compile mismatches, timeouts, and translation-unit or
  global execution-budget violations fail closed. Cache identity now includes project `.clang-tidy`
  inputs and the clang-tidy/diagnostic helper implementations.

### Verification
- **Clang-tidy suppression-accounting regression**: PR #115's second real-tool run exposed that
  `--quiet` retains the generated-warning count while hiding the corresponding suppressed-warning
  count, so clean Qt translation units failed the atomic parser. The adapter now keeps suppression
  accounting enabled, recognizes LLVM 18's bounded extended system-header hint, and still rejects
  an unaccounted generated-warning summary. An unprivileged LLVM 18 reproduction parsed the actual
  15,780-generated/15,780-suppressed clean output, and both required real-tool E2Es passed locally.
- **Clang-tidy coalesced diagnostic accounting**: a subsequent viewer run proved that Clang's
  generated-warning counter, clang-tidy's suppression counter, and rendered messages do not share
  one cardinality: `report_model.cpp` emitted 13,004 generated, 13,000 suppressed, and three
  rendered diagnostics. The parser now requires visible or suppression accounting without falsely
  equating those counters, while duplicate summaries, quiet-only generated summaries, malformed
  lines, and unbounded output remain atomic errors. It also accepts both LLVM 18 and current
  bounded header-filter hints. Summary splitting, family normalization, and accounting validation
  are isolated helpers, returning the repository maximum complexity to the warning-only 25.
- **Viewer dogfood remediation**: the five real clang-tidy findings exposed after parsing was
  restored were fixed: two unnecessary string copies, an allocation-heavy concatenation, an
  unchecked optional access, and the CLI's uncaught top-level exception boundary. The exact local
  LLVM 18 viewer verification is now PASS with lint at zero violations, 7/7 C++ tests, 94.4% line,
  97.7% function, and 80.0% branch coverage, and TEM 4.89/5.0.
- **I4-1 self-dogfood maintainability**: the clang-tidy orchestration preflight, source validation,
  capability selection, unit selection, and bounded execution were split into focused helpers after
  the first PR run exposed cyclomatic complexity 30. The adapter entry point is now complexity 10
  and every helper is at most 11; the repository maximum returned to the existing warning-only 25.
- **Deterministic Zero-CDN smoke**: `scripts/smoke.sh` now inspects a generated HTML report even
  when the quality verdict makes `verify` return nonzero, rejects external executable/display
  assets, requires a non-empty report, and removes its temporary HTML/JSON through an exit trap.
- **I4-1 actual-process gate**: a Linux E2E now loads a real compilation database and exercises
  the production compiler and clang-tidy adapters without source mutation. It asserts GCC 9+ JSON
  rule/location evidence and the exact sanitized clang-tidy argv (no `-p`, `--fix`, dependency, or
  output flags). PR and release workflows install clang-tidy and set
  `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`, so missing or incompletely probed tools fail instead of
  silently skipping. The local GCC path and a temporarily extracted LLVM 18 clang-tidy path both
  passed. PR #115 and exact-main CI both ran the installed-tool gate without skips.
- **I4-1 remote completion**: [PR #115](https://github.com/jihoon22-lee/ici/pull/115) final head
  `b7ed26c68aa61f2d3f3f8e58afb4556a16c681cd` passed
  [run 33469332734](https://github.com/jihoon22-lee/ici/actions/runs/33469332734) with 1,417/1,417
  tests, both required actual-tool E2Es, Qt5·Qt6, self/viewer dogfood, report publication, and Merge
  Gate, then was squash-merged as `973cf2423728f9d808873f548bc00c7878cceadd`. The sticky comment
  retained one marker and two report links. PR ici/viewer Pages were 6,034,768/345,256 bytes with
  SHA-256 `f26b34d75a0e0561b48106cf4aaea122f1cd6a558ecc154f02299ac039f38075` and
  `ae40367d35b7db172b37698422185d3dacf64db83f344860eca6c3a3754c1936`; both returned HTTP 200
  `text/html`, exact titles, and zero external references.
- **I4-1 exact-main evidence**: [run 33469789628](https://github.com/jihoon22-lee/ici/actions/runs/33469789628)
  repeated 1,417/1,417 tests, both required tool E2Es, viewer PASS with lint zero and 7/7 tests,
  Qt5·Qt6, main report publication, and Merge Gate. Main ici/viewer Pages were
  5,691,036/345,176 bytes with SHA-256
  `048421ca94e83250da1a4411900a4748b239d2da211b84dd5e4fb9f1ab057af4` and
  `6f0e2e10e4a075651c6b893341ab6d2e70798513766c7420179529fe798ed758`; both passed the same
  HTTP/content/title/Zero-CDN audit. I4-1's ici checkpoint and the v0.9.0 release boundary are
  complete; toy-projects B4 remains pending, so the overall I4 checkpoint and I4-2 remain pending.
- **v0.9.0 release evidence**: the annotated `v0.9.0` tag resolves to exact `main` commit
  `061950834a135a30bd5d4e974ec1dfce33df68a9`. [Release workflow 33472668716](https://github.com/jihoon22-lee/ici/actions/runs/33472668716)
  passed `Validate Release Provenance` and `Build & Publish Release`; the published release is
  non-draft and non-prerelease. Exactly nine assets were independently downloaded and each matched
  the GitHub API size and SHA-256 digest: `ici.pyz`, `ici.pyz.sha256`, `ici-self-report.html`,
  `ici-self-report.json`, `viewer-report.html`, `viewer-report.json`, `icirv`, `icirv-gui`, and
  `icirv-gui.README.txt`. The package reports `ici 0.9.0`, its checksum manifest passes, both JSON
  reports parse as `ici.result/v3`, both HTML reports have the expected titles and zero external
  asset references, and the downloaded static `icirv` parses a report (`ldd`: `not a dynamic executable`).
- **I3 same-basename active-header local compiler edge**: the existing
  `test_trace_uses_compiler_selected_same_basename_without_ambiguity` keeps its mocked
  `run_process` regression coverage. The new
  `test_real_compiler_trace_selects_the_first_same_basename_header` invokes
  `build_compiler_cpp_graph` with the actual `run_process` runner and a probed GCC/Clang
  compiler. In the current local Python 3.10 run, the mocked case and the actual `g++` case
  passed (2 passed); `clang++` was skipped because it is unavailable. This closes the edge
  locally; the follow-up PR/CI/Pages evidence is recorded below. The latest local full gate reports
  Python 3.10 pytest `1,334 passed, 1 skipped`, Ruff check/format PASS for 148 files, and mypy
  PASS for 88 source files. `build-pyz` and smoke also passed; the current artifact is 2,166,828
  bytes with SHA-256 `0f82aa95eb940072a735c591737f5b77d9dd16b32751aa03600ad3c5978bb158`.

## [0.8.0] - 2026-09-01

### Added
- **Standalone compilation-context export**: `ici export-compilation-context` now emits a
  deterministic, redacted `ici.compilation-export/v1` JSON snapshot for downstream consumers.
  The packaged contract is [`ici-compilation-export-v1.schema.json`](src/ici/schemas/ici-compilation-export-v1.schema.json).

### Security and compatibility
- The default export path is process-free and read-only: it parses the selected compilation
  database without invoking a shell, compiler, or recursive source scan. `arguments` takes
  precedence over `command`; project-contained response files remain bounded and are never shell
  expanded.
- `--prepare` is an explicit opt-in for the CMake/qmake configure/build adapters and their owned
  `build/ici-*` shadows. Public output omits raw `argv`/`command`, redacts credentials and external
  host paths, and marks redaction, external inputs, unknown options, and diagnostics as
  `inconclusive` while retaining `MEASURED` provenance. An explicitly configured missing or
  malformed database remains authoritative; `--prepare` does not silently replace it.
- `--output` uses bounded, same-directory atomic replacement and protects the database and project
  policy files. Input and output bounds include database-wide expanded argument count/size limits;
  duplicate-key/non-finite JSON rejection and project containment keep malformed, repeated, or
  oversized compilation metadata from becoming execution input or unbounded retained argv state.

### Verification
- Python 3.10 passed 1,333 tests in 51.99s; Ruff check/format and mypy over 88 source files were
  clean. The quoted relative define path regression is covered: values are resolved from the unit
  directory and external escapes remain redacted. Two pyz builds matched at SHA-256
  `d9d83b20832ca8d0133653e00b1f7a20861c2ee855b06d0de1f0328137a382ca`,
  with 10 pure-Python distributions, no certifi/native extension, both public schemas packaged,
  and smoke/Zero-CDN passing.
- Packaged self-verification was WARN (8/4/0/0/1 for pass/warn/fail/error/skip; 1,333/1,333 tests;
  line/function/branch 89.2%/96.8%/80.6%; TEM 4.84; cache hits 0). Engine duration was 121.72s
  (wall 125.09s). HTML was 5,696,688 bytes with SHA-256
  `adc9a49c78c2f5ea5666c58a96555cd73b281587f891e11175654a7ac973b3d5`, the expected title, and
  zero external references. The export/compile-DB change scope had zero line, module-coverage,
  type, high-complexity, or exception findings.
- Final candidate BuildScope verification was WARN (11/2/0/0/0 for pass/warn/fail/error/skip;
  45/45 tests; line/function/branch 95.2%/100%/84.3%; compile DB 7/7 production units,
  16 configurations, 0 issues; TEM 5.00). Engine duration was 20.52s (wall 21.22s). HTML was
  490,420 bytes with SHA-256
  `faf4646b27b2e2c50501fb96280aa70741254dba8e7b383e5ede033ab519cb85`, the expected title, and
  zero external references.
- The BuildScope v2 native snapshot SHA-256 was
  `ee0e59f484a82cbdb09d8085a241929e15b0130e2c51f824c361f808f6c611f5`; the deterministic ici v1
  export SHA-256 was `6f0e99872ab0041f174f9b708cb2a0bd5e60569ce06fe825644541c0ae2162c9`, with
  semantic digest `sha256:a7db541ae2daa0c19365f80c1bdbe5090049c86b423000fdf9b6f8e85a857a48`.
  The same public projection compared 16 units, 6 targets, and 14 field groups with zero mismatch,
  checkout leak, or raw `argv`/`command` exposure.
- These are local/candidate measurements; the exact remote PR, main, release, and public artifact
  evidence is recorded below. The v0.8.0 release snapshot is historical; its same-basename
  active-header confirmation was pending at that point, while the unreleased follow-up now has
  both local and remote evidence.

### Remote evidence
- Feature [PR #113](https://github.com/jihoon22-lee/ici/pull/113) had head
  [`61f613f6cd264327956f65db1dc81d5fe5ef5be7`](https://github.com/jihoon22-lee/ici/commit/61f613f6cd264327956f65db1dc81d5fe5ef5be7).
  [PR workflow run 33458308024](https://github.com/jihoon22-lee/ici/actions/runs/33458308024) completed
  all checks green, including `Merge Gate`. Its [sticky comment](https://github.com/jihoon22-lee/ici/pull/113#issuecomment-5487193195)
  contained exactly one marker and exactly two report links; ici reported 1,335/1,335 tests with
  TEM 4.84, and viewer reported 7/7 tests with TEM 4.89.
- Independent PR Pages audits returned HTTP 200 `text/html`, exact titles, and zero external
  references: [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/113/) was 5,691,035 bytes with
  SHA-256 `4118bd7f42aa16e6082b56ce65a874d668b23c18a20d3c31876d81885e859561`; [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/113/)
  was 345,176 bytes with SHA-256
  `22aff0be7894b4f416169f547ee9862e133ceca55e8caa3bef201e8f924bc2d0`.
- PR #113 was squash-merged to exact main
  [`c78b40a15a64423f742aa2e75b09d35cc09a5e62`](https://github.com/jihoon22-lee/ici/commit/c78b40a15a64423f742aa2e75b09d35cc09a5e62).
  [Exact-main run 33458962715](https://github.com/jihoon22-lee/ici/actions/runs/33458962715) was
  SUCCESS, including main `Publish` and `Merge Gate`. Independent main Pages audits returned HTTP
  200 `text/html`, exact titles, and zero external references: [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/)
  was 5,690,362 bytes with SHA-256
  `ef9c2869adebf596ab257a19c30ad1f61352d531ec30fa8df8e0a7ec3020e93f`; [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
  was 345,176 bytes with SHA-256
  `8ba214c4c019db341a44719191a721de8c2aa144743f1b2484d60b7021556dd9`.
- These PR #113 and exact-main records close the I3 checkpoint after the local actual-process
  same-basename edge and the v0.8.0 public projection BuildScope comparison (16 units, 6 targets,
  14 field groups, zero mismatch). At that historical snapshot, the release/version remained
  v0.8.0; no version bump was made, and the next planned stage was I4.
- Feature [PR #110](https://github.com/jihoon22-lee/ici/pull/110) from head `3ce564a` was merged as
  `6b44f32869944a0941cab63eb94489b92c543a58`. [CI run 33448847117](https://github.com/jihoon22-lee/ici/actions/runs/33448847117)
  completed every required check and `Merge Gate`; its sticky comment retained one marker and two
  report links. Independent PR ici/viewer Pages returned HTTP 200 `text/html`, the expected titles,
  and zero external resource references.
- Release [PR #111](https://github.com/jihoon22-lee/ici/pull/111) from head
  [`13d870f`](https://github.com/jihoon22-lee/ici/commit/13d870f6bd8c6bd9ddc89b703e40b1d22b7567f4) was
  merged as exact main commit
  [`27574109e0f3fc24d6e96eca05bfded4e041d3fa`](https://github.com/jihoon22-lee/ici/commit/27574109e0f3fc24d6e96eca05bfded4e041d3fa).
  [PR CI run 33450379770](https://github.com/jihoon22-lee/ici/actions/runs/33450379770) completed all
  jobs green, and the [sticky comment](https://github.com/jihoon22-lee/ici/pull/111#issuecomment-5486185531)
  records marker 1 and two report links. Independent [PR ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/111/)
  and [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/111/) returned HTTP 200 `text/html`,
  the correct titles, and zero external references: ici 5,690,362 bytes with SHA-256
  `862c72443ca80040e0bc4524d31c5f5f7e8adb26292faf665f125ce09a9e53af`, viewer 345,176 bytes with
  SHA-256 `e6c86558ce00666e8151c1b4020abd26115f3dd6846dca06b275d5b7b75366ff`.
- The exact main commit `27574109e0f3fc24d6e96eca05bfded4e041d3fa` passed [CI run 33450906375](https://github.com/jihoon22-lee/ici/actions/runs/33450906375)
  all green, including `Merge Gate` and `Publish Main`. Independent [main ici Pages](https://jihoon22-lee.github.io/ici/ici/main/)
  and [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/main/) retained HTTP 200 `text/html`,
  the correct titles, zero external references, and the recorded hashes: ici 5,690,362 bytes with
  SHA-256 `99445ff8da2458d6bd5d861d63ae9318db374dfbc60a66bc6cc60ff5cc05894d`, viewer 345,176 bytes
  with SHA-256 `4626e354eba2638e07c3c6a254e4ae5cb95291a86c13f4bebe92bef1d892696d`.
- The annotated [`v0.8.0` tag](https://github.com/jihoon22-lee/ici/releases/tag/v0.8.0) resolves exactly
  to the main SHA above. [Release run 33451310453](https://github.com/jihoon22-lee/ici/actions/runs/33451310453)
  completed both `Validate Release Provenance` and `Build & Publish Release` green. The published
  release is non-draft/non-prerelease and has exactly nine assets: `ici.pyz`, `ici.pyz.sha256`,
  `ici-self-report.html`, `ici-self-report.json`, `viewer-report.html`, `viewer-report.json`,
  `icirv`, `icirv-gui`, and `icirv-gui.README.txt`.
- The downloaded `ici.pyz` reports version `0.8.0`; `sha256sum --check ici.pyz.sha256` passed and
  matched the GitHub API digest `sha256:bb723a30b0ed07936fcf81c7e2b4425832fd86210286b0e6b1b619e1b434142e`.
  Release `ici-self-report.html` and `viewer-report.html` have SHA-256
  `ccfbb3709864c7bf578a0635d66a63b82448304aefd616e1b57a3d9d59038539` and
  `6ee8d2e5b29453155af5e84323a8d829c1bcb3be80c345ab6d99d27b6560412a`, respectively; both have
  the correct titles and zero external references, and both JSON reports are valid.
- Public v0.8.0 BuildScope verification was WARN (Pass 11, Warn 2; tests 45/45; TEM 5.00); its
  HTML was SHA-256 `567957be0fcf978d756116262b4075f1655050902227b0b9d1428fe7a1080b6b`. Public
  export SHA-256 was `f1d7e1297c773f55777d939a552c11f300a5f59652839f59495037ac227e83d`, semantic
  digest `sha256:68f86ddf572ba781573f24d8a7319c6abd0f606b980ea1594e9f0616da71e95f`, and native v2
  snapshot SHA-256 `085f70450cd89171d3fd4011d35ccc35e8658ab5308b64e398ea0b0793c45d8a`. Schema
  validation passed; the public projection compared 16 units, 6 targets, and 14 field groups with
  zero mismatch, checkout leak, or raw `argv`/`command` keys.
- The release and public artifact evidence remains the v0.8.0 record above. The same-basename
  active-header comparison was pending at that release snapshot and is now complete locally and
  remotely in the unreleased follow-up above; I3-5 is complete and the next stage is I4.

## [0.7.1] - 2026-09-01

### Fixed
- **Hybrid mypy scope isolation**: the type engine now derives mypy argv only from configured
  source roots that actually contain discovered Python files. A hybrid project with separate
  `python`, `src`, and `include` roots no longer passes the C++-only roots to mypy and turns a
  valid analysis into tool exit code 2.
- **Selected-interpreter Python tool capabilities**: capability probes for `pytest`, `coverage`,
  and `mypy` now run as bounded `INTERPRETER -m MODULE --version` commands through the same
  project `.venv` or current interpreter used by the engines. A PATH executable belonging to a
  different Python installation can no longer make the shared capability snapshot claim a module
  is ready when the selected runtime cannot import it. Type analysis reuses the immutable mypy
  module capability from the shared analysis context.
- **Portable release checksum manifest**: release generation now runs `sha256sum` inside `dist`,
  so the published manifest names `ici.pyz` rather than the internal build path `dist/ici.pyz` and
  consumers can use `sha256sum --check ici.pyz.sha256` directly.

### Verification
- BuildScope reproduced both defects against the public v0.7.0 asset: with the full isolated
  Python tool environment, capability inventory was READY but mypy received `include python src`
  and exited 2; the downloaded checksum contained `dist/ici.pyz`. The v0.7.1 focused regression
  suite covers the exact selected-interpreter argv, unsafe module-name rejection, hybrid Python
  root filtering, and portable release workflow command.

## [0.7.0] - 2026-09-01

### Fixed
- **Release viewer test completeness**: the release workflow now builds the complete configured
  viewer graph before running CTest. Building only `icirv-gui` left the six core/CLI test
  executables and the MainWindow test absent, so the otherwise green v0.7.0 release candidate
  failed closed with seven `Not Run` tests before asset publication.
- **Pages publication latency tolerance**: the PR report gate now waits up to ten minutes for
  legacy GitHub Pages to serve newly committed ici/viewer reports. It still fails closed unless
  every sticky-comment URL returns HTML, while avoiding false failures when consecutive report
  commits leave the final Pages deployment queued longer than the previous 90-second window.

### Added
- **I3-3 qmake exact compilation context**: qmake projects without an
  explicit or discovered `compile_commands.json` can now produce a canonical Release context in
  `build/ici-qmake-build`; an existing database remains authoritative. The capture spike compared
  qmake verbose/trace output, external capture tooling, and a compiler wrapper. The selected
  wrapper records the exact compiler `argv` and working directory for each `-c` invocation in a
  bounded JSONL journal, then executes the original compiler directly without shell parsing or
  replaying captured recipes.
  - DiskMap exposed that injecting wrapper text during the first qmake pass can collapse nested
    `$$` expressions. The preflight therefore starts by resetting the owned canonical shadow,
    recursively configures once to materialize nested Makefiles, and performs a bounded metadata
    probe before recursively configuring again with a wrapper pinned to the selected
    `sys.executable` and literal absolute C/C++ compiler paths. The adapter's recorded
    `make clean` then runs before the capture build, so stale metadata and artifacts are not reused.
  - The first-stage probe accepts only one consistent, safe `CC`/`CXX` pair from recursively found
    `Makefile*` files: each value must be a single recognized gcc/g++/clang driver resolving to an
    executable regular file. Each Makefile is capped at 4 MiB and the walk at 4,096 Makefiles;
    ambiguous, multiword, unavailable, symlinked, or unsafe metadata fails closed.
  - The capture journal is capped at 32 MiB and 200,000 records. Journal writes use no-follow
    regular-file/ownership/permission checks and locking; the generated wrapper is 0700 and the
    journal 0600. The resulting database is written inside the owned shadow through a temporary
    file and atomic replace. Non-POSIX hosts return an explicit warning lower-confidence mode
    rather than claiming exact capture.
  - Captured source coverage is checked against production translation units; missing units emit
    the `qmake-capture-incomplete` compilation diagnostic. `CompilationContext` records qmake
    provenance (`origin = "qmake"`, `generator = "qmake"`, `unity_build = null`), v3 schema
    accepts the new origin, and compilation identity/cache contracts include the v2 context. The
    verification orchestrator routes qmake projects through this preflight and CMake projects
    through the existing CMake path.
  - Local E2E evidence: the real qmake fixture produced 3 units on both Qt5 and Qt6,
    including the generated moc unit. Actual DiskMap Qt5 and Qt6 runs covered 20 configurations,
    all 9/9 production units, with no compilation diagnostics; temporary capture shadows were
    cleaned. Python 3.10 finished with 1,112 tests passing; Ruff check/format covered 134 files and
    focused mypy passed 7 source files. Two current-source pyz builds matched at SHA-256
    `5610617022a6accaf0b8fa0313ee0fd6c414317e839d23e2c879fa8b4c918d23` with 10 pure-Python
    distributions and no certifi, and smoke passed Python 3.10 execution, artifact integrity, and
    Zero-CDN. Packaged self-verify returned WARN (8 pass, 4 warn, 0 fail/error, 1 skip; 1,112 tests;
    line/function/branch 88.8%/96.5%/79.9%; TEM 4.82; complexity 25; 117.25s). Its 4,722,391-byte
    HTML had a title and zero external script/link/image dependencies.
  - I3-3 was completed through [PR #103](https://github.com/jihoon22-lee/ici/pull/103), squash-merged
    as [`e97d6d4502232bf7bc5b36a21f3b031306f43554`](https://github.com/jihoon22-lee/ici/commit/e97d6d4502232bf7bc5b36a21f3b031306f43554).
    [CI run 33394395321](https://github.com/jihoon22-lee/ici/actions/runs/33394395321) reported
    `Verify & Dogfood ici`, `Viewer GUI Qt5`, `Viewer GUI Qt6`, `Publish PR Report & Sticky Comment`,
    and `Merge Gate` as SUCCESS; `Publish Main` was expectedly SKIPPED. The [sticky comment](https://github.com/jihoon22-lee/ici/pull/103#issuecomment-5478744238)
    reported ici WARN (Pass 8, Warn 4, Fail 0, Error 0, Skip 1, TEM 4.82, tests 1,112,
    line/function/branch 88.9%/96.5%/80.1%) and viewer PASS (Pass 11, Warn 0, Fail 0,
    Error 0, Skip 2, TEM 4.89, tests 7, compile DB 5/5 production units, 20 configurations).
    Independent [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/103/) and
    [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/103/) checks returned HTTP 200
    `text/html` with titles `ici Verification Report — ici` and `ici Verification Report — viewer`,
    observed sizes 4,716,032 and 337,918 bytes, and zero external `script`/`link`/`img` references
    in both reports. At this historical 0.7.0 snapshot, I3-3 was complete while I3-2 BuildScope
    target-by-target validation, I3-4, and I3 as a whole remained pending; the later v0.8.0 public
    projection and current follow-up records above supersede that status.
  - Self-dogfood first exposed an inline qmake dispatch branch that raised
    `VerifyOrchestrator.run_all` complexity from 25 to 26/FAIL; typed dispatch extraction restored
    25/WARN. Moving qmake argv construction into its own module also reduced `cmake.py` from 512
    to 495 code lines, removing the line warning reintroduced by this slice.
- **I3-4 compiler-backed C++ lint and include graph**: C++ lint now replays every covered
  translation-unit configuration from the shared compilation context using a sanitized direct
  GCC/Clang argv selected from the capability inventory. Compile-only/output/dependency options and
  plugin, wrapper, or other unsafe injection flags are rejected or removed; located diagnostics and
  PASS targets are preserved. A positive option allowlist is used: unknown or unsafe options fail
  closed, while only explicitly safe options and values survive replay. Compiler processes receive a
  minimal replacement environment with closed stdin, so inherited override hooks are not reused.
  Error-level context/unit diagnostics, context coverage, replay, malformed-output, timeout,
  truncation, spawn, and unknown nonzero failures are fail-closed `ERROR`/`NOT_RUN` results;
  warning-level context/unit diagnostics remain located `WARN` targets and, without another error,
  exact evidence remains `MEASURED`.
  - C++ cycle analysis runs compiler `-E -H` traces per configuration, records active resolved edges,
    and counts `project`/`generated`/`system`/`third_party` scopes. Each configuration graph is
    analyzed independently; only an identical cycle component is deduplicated, and configuration
    edges are never unioned. When the same component appears in several configurations, their names
    are retained as report metadata only. Active missing includes are located
    `CppIncludeUnresolved` warnings; malformed or otherwise untrusted traces fail closed, and no
    suffix fallback is used when context exists.
  - Only a genuinely absent database uses a heuristic: lint builds a c++17 command through the same
    bounded replay adapter and cycle uses unique project path suffix resolution. Fallback lint
    prefers the ready probed `g++` capability, rejects project-contained/non-canonical drivers and
    unsafe package/include flags before execution, and still uses a minimal environment with closed
    stdin. Successful fallback runs are `ESTIMATED`; unavailable or rejected tools and failed runs
    are `ERROR`/`NOT_RUN`.
  - Include-trace parsing is isolated in `ici.engines._cpp_include_trace`. Missing-include traces,
    include-guard trailers, pseudo frames, stale paths, entry count, and depth are bounded and
    fail-closed without inventing edges.
    Engine cache identity is `ici.analysis-cache-key/v3` and includes source digests for
    helper/dependency modules explicitly declared by the engine, including
    `ici.core._cpp_replay_policy` and `ici.engines._cpp_include_trace` for these C++ engines.
  - Local source revalidation on 2026-09-01 passed: the focused implementation bundle had 308
    tests; Python 3.10 full pytest had 1,275 passed in 48.61s; Ruff check passed for all files, Ruff
    format covered 142 files, mypy passed 83 source files, every new source passed the line gate,
    and no new helper had a complexity issue. The remote evidence is complete through
    [PR #105](https://github.com/jihoon22-lee/ici/pull/105),
    squash-merged as [`183b2d83421cd3173fb2e6f745c0e39bd5c36a78`](https://github.com/jihoon22-lee/ici/commit/183b2d83421cd3173fb2e6f745c0e39bd5c36a78).
    [CI run `33409862110`](https://github.com/jihoon22-lee/ici/actions/runs/33409862110) reported
    `Verify & Dogfood ici` (3m58s), `Viewer GUI build (Qt5)` (45s),
    `Viewer GUI build (Qt6)` (1m15s),
    `Publish PR Report & Sticky Comment` (1m16s), and `Merge Gate` (3s) as SUCCESS; `Publish Main`
    was expectedly SKIPPED for the PR. The [sticky comment](https://github.com/jihoon22-lee/ici/pull/105#issuecomment-5480770505)
    contains both ici and viewer links/tables: ici WARN (Pass 8, Warn 4, Fail 0, Error 0, Skip 1,
    TEM 4.84, tests 1,275/1,275, branch 80.4%) and viewer PASS (Pass 11, Warn 0, Fail 0, Error 0,
    Skip 2, TEM 4.89, tests 7/7). Independent [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/105/)
    and [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/105/) both returned HTTP/2 200,
    `text/html;charset=utf-8`, correct titles, and zero external `script`/`link`/`img`/`iframe`/`import`
    references; observed sizes were 5,458,757 and 344,868 bytes respectively. At that historical
    0.7.0 snapshot, BuildScope target-by-target comparison and I3 as a whole remained pending; the
    later v0.8.0 public projection and current follow-up records above supersede that status.
- **I3-2 canonical CMake compilation context**: CMake projects without an existing
  compilation database now receive a deterministic Release analysis preflight in
  `build/ici-cmake-build`. Every CMake configure exports `compile_commands.json`;
  the analysis configure also forces unity off so source-level coverage can be
  interpreted exactly.
  - Only single-configuration `Ninja` and `*Makefiles` generators are accepted.
    The bounded, no-follow `CMakeCache.txt` reader records generator, export and
    unity metadata without exposing raw tool output. Unsupported or ambiguous
    metadata remains a location-bearing compilation diagnostic.
  - Generated sources under the canonical shadow are detected on the first load;
    exactly one full build is performed and the database is reloaded before the
    immutable context is published. Ordinary stale source entries do not trigger
    an unrelated build. CMake subdirectory output paths are reconciled against
    both the entry working directory and database parent only when they resolve
    to the same output, preserving project containment checks.
  - `CompilationContext`/`CompilationUnit` report and cache identity now retain
    `origin`, generator, unity state and CMake target metadata alongside the
    database digest, normalized argv and diagnostics. The target is derived from
    CMake's `CMakeFiles/<target>.dir` convention, while redaction still keeps
    external paths out of reports.
  - Local evidence: Python 3.10 `pytest` 1,074 passed (46.32s), Ruff check/format
    130 files, focused mypy clean for 11 source files, reproducible pyz SHA-256
    `2874e081cc27e0fc7f77e1285229c5fd0ba2803a149ddf1c6e4a3c4fb4d6db90`, 10
    pure-Python distributions with no certifi, and smoke/Zero-CDN PASS. The
    self report was WARN (Pass 8, Warn 4, Skip 1; tests 1,074; line/function/
    branch 88.7%/97.2%/79.7%; TEM 4.86; 113.38s; HTML 4,697,480 bytes; external
    dependencies 0). Candidate validation was viewer PASS (5/5 production
    units, 20 configurations, 0 issues, 23.27s) and LogLens PASS (14/14, 40
    configurations, 0 issues, 32.27s). Self-dogfood initially exposed an
    unnecessary silent CMake inspection `OSError` path; the dead inspection was
    removed and the final exception path passed.
  - I3-2 was merged through [PR #101](https://github.com/jihoon22-lee/ici/pull/101)
    as squash commit [`459abbaa5d6c80d91dfe07e54403c9bf88e63602`](https://github.com/jihoon22-lee/ici/commit/459abbaa5d6c80d91dfe07e54403c9bf88e63602).
    [CI run 33386134812](https://github.com/jihoon22-lee/ici/actions/runs/33386134812)
    reported `Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`,
    `Publish PR Report & Sticky Comment`, and `Merge Gate` as SUCCESS; `Publish Main`
    was expectedly SKIPPED for the PR. The [sticky comment](https://github.com/jihoon22-lee/ici/pull/101#issuecomment-5477565364)
    contains both the ici and viewer report links. CI stats were ici WARN (Pass 8,
    Warn 4, Fail 0, Error 0, Skip 1, TEM 4.86, 1,074 tests, branch 79.8%) and
    viewer PASS (Pass 11, Warn 0, Fail 0, Error 0, Skip 2, TEM 4.89, 7 tests,
    compile_db 5/5 production units, 20 configurations, 0 issues). Independent
    [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/101/) and
    [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/101/) checks both
    returned HTTP/2 200 `text/html` with a title, zero external dependencies, and
    observed sizes of 4,574,483 and 337,918 bytes respectively. At that historical 0.7.0 snapshot,
    BuildScope target-by-target validation remained pending; I3-3 was complete, while I3-4 and I3
    as a whole were not complete. The later v0.8.0 public projection and current follow-up records
    above supersede that status.
- **I3-1 compiler-exact compilation context와 `compile_db` 품질 게이트**: root 또는 `build/compile_commands.json`(또는 명시적 project-relative 설정)을 immutable `CompilationContext`로 한 번 읽어 모든 엔진과 리포터가 공유합니다. `arguments` 우선, POSIX/Windows command tokenizer, bounded project-contained response-file 확장으로 shell/compiler를 실행하지 않고 compiler, language, standard, defines, include/search path, sysroot, output과 동일 source의 여러 configuration을 보존합니다.
  - database와 response file은 `O_NOFOLLOW`·`O_NONBLOCK` descriptor, regular-file `fstat`, 크기 제한 읽기, device/inode/size/mtime 재검증을 거칩니다. duplicate JSON key, non-finite/과대 입력, symlink·foreign path escape, malformed row, source/output 불일치와 stale/missing path는 전체 검증을 crash시키지 않고 위치가 있는 진단으로 변환됩니다.
  - GCC/Clang의 `-std`, `-x`, `-D`, `-I`/`-isystem`/`-iquote`, sysroot, `-o`와 MSVC/clang-cl의 `/std:`, `/D`, `/I`, `/external:I`, `/Fo`, `/TC`·`/TP`를 구조화합니다. 중앙/JSON redaction은 module/search/linker/rpath/forced-include/response-file 및 define 안의 embedded absolute POSIX·Windows 경로도 `[external]`로 투영합니다.
  - 새 기본 활성 `compile_db` 엔진은 C/C++ production translation unit coverage, loader/unit diagnostic, 누락 include·working directory, stale source, required/forbidden flag를 `InspectionTarget`/v3 finding으로 승격합니다. Python-only scope는 `NOT_APPLICABLE`, 자동 탐색 DB 부재는 기본 WARN이며 `database_required = true`로 FAIL 정책을 선택할 수 있습니다.
  - cache key를 `ici.analysis-cache-key/v2`로 올리고 database path/digest, loader identity, normalized unit와 parse diagnostic state를 포함해 `build/compile_commands.json` 변경 뒤 stale engine result가 재사용되지 않게 했습니다. v3 schema도 unit/argv/diagnostic 크기와 output·search path·sysroot scope 조합을 제한합니다.
  - loader facade를 `src/ici/core/compile_db.py`에 두고 `_compile_db_paths.py`, `_compile_db_commands.py`, `_compile_db_metadata.py`로 책임을 분리했습니다. 네 모듈은 각각 순수 코드 500줄 미만이며, compile_db 범위의 최종 line·type·high-complexity 이슈는 0건입니다.
  - I3-1 최종 로컬 증거는 focused 109 passed, Python 3.10 full 1,032 passed(46.29s), Ruff check/format 127 files, focused mypy clean, reproducible pyz 두 빌드 동일 SHA-256 `408fcd0fcf153b5e63927d10d34d55cea680eb472dc6f0e95bf174efcf6e8b36`, pure-Python 10 distributions/no certifi, smoke·Zero-CDN PASS입니다. 최종 `--no-cache` self verify는 WARN(13 total: Pass 8, Warn 4, Fail 0, Error 0, Skip 1), compile_db `SKIP`/`NOT_APPLICABLE`(Python-only), test 1,032/1,032, coverage line/function/branch 88.6%/97.1%/79.6%, TEM 4.86, cache hits 0, 109.26s, HTML 4,627,454 bytes였고 compile_db-specific high-complexity/line-threshold/type issues는 0건입니다. 이는 로컬 측정값이다.
  - I3-1 원격 병합 증거도 완료됐다. [PR #99](https://github.com/jihoon22-lee/ici/pull/99)는 squash로 병합되어 commit [`64c4f7b57826e088e9b74b5950c7f3d8091188b9`](https://github.com/jihoon22-lee/ici/commit/64c4f7b57826e088e9b74b5950c7f3d8091188b9)가 되었고, [CI run 33380721019](https://github.com/jihoon22-lee/ici/actions/runs/33380721019)의 `Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`, `Publish PR Report & Sticky Comment`, `Merge Gate`가 모두 SUCCESS였다(`Publish Main`은 PR에서 expected skipped). [sticky comment](https://github.com/jihoon22-lee/ici/pull/99#issuecomment-5476836988)는 ici와 viewer를 함께 포함했으며, CI stats는 ici WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1, TEM 4.86, tests 1,032, branch 79.7%), viewer WARN(Pass 10, Warn 1, Fail 0, Error 0, Skip 2, TEM 4.89, tests 7)였다. 독립적으로 fetch한 [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/99/)와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/99/)는 각각 HTTP/2 200, `Content-Type: text/html; charset=utf-8`, title present, 외부 `script`/`link`/`img`/`iframe` dependency 0건이었고 관측 bytes는 각각 4,496,996와 344,663이었다. 이로써 I3-1은 완료됐으며 다음 단계는 I3-2 CMake compile DB 생성이다. 이는 당시 0.7.0 snapshot의 상태이며, 이후 v0.8.0 public projection과 현재 follow-up 기록에서 I3 전체가 complete됐다.
- **I2-1 tool capability probes (first slice)**: 추가한 선언형 bounded·shell-free registry가 compiler/clang tools, CMake/CTest, qmake6→qmake, Make, Ninja, gcov, binutils, pkg-config, Qt5·Qt6 및 Python tooling을 탐지합니다. vendor suffix·multiline stdout/stderr version tuple parser와 compiler target triple, qmake Qt metadata, CMake generator capabilities를 기록하며, 5초/64KiB 제한·redacted immutable evidence와 available/complete 구분을 제공합니다. 이 registry는 Slice 2의 doctor inventory와 Slice 3의 verify/report snapshot에서 공통으로 사용됩니다.
- **I2-1 shared policy-aware capability inventory (second slice)**: `ToolRequirement`와 immutable `CapabilityInventory`가 전체 registry probe 결과에 required/optional provenance를 붙입니다. required 도구의 missing/incomplete 상태는 명시적으로 WARN health로 집계하고 optional 부재는 healthy 상태를 유지합니다. `ici doctor`와 `ici doctor --json`은 같은 전체 registry를 사용하며, JSON의 `capability_inventory`에 status·counts·도구별 version/path/details/evidence와 `engine:language` 또는 `doctor.config` requirement source를 남깁니다. argv와 metadata는 공통 redaction 경계를 거치고, 기존 `tools` map은 호환용으로 계속 제공합니다. Slice 3에서는 같은 정책과 snapshot을 verify/report까지 확장합니다.
- **I2-1 verify/report shared capability snapshot (third slice)**: `ici verify`가 유효 support matrix를 엔진 실행 전에 평가하고, `applicable`·`enabled` 범위에 `doctor.config`를 합친 required/optional tool policy를 계산한 뒤 bounded registry를 정확히 한 번 수집합니다. verify 결과와 모든 reporter는 suite-level immutable `CapabilityInventory`를 공유하며 reporter가 도구를 다시 probe하지 않습니다. required provenance가 optional보다 우선하지만 모든 소비자 provenance는 보존하고, 공통 redaction은 capability 이름·경로·버전·오류·details·probe argv·evidence까지 적용합니다. `ici.result/v3`에는 기존 리포트와 호환되는 선택적 root `capability_inventory`와 checked-in schema가 추가됐습니다. 콘솔은 compact health, Markdown은 접힌 complete inventory, zero-CDN HTML은 Support & Capabilities 탭의 전체 tool rows를 표시합니다. 최종 로컬 검증은 Python 3.10 pytest 807 passed(40.32s), Ruff check/format 103 files 및 smoke PASS, reproducible pyz SHA-256 `0d91f4ab698aed53781669125200e5ae2291484c4083d2c181aacee06d5c80e2`를 기록했습니다. self verify는 exit 0(WARN; 12 engines 8 PASS/4 WARN, TEM 4.84, 105.98s)였고 capability inventory는 30 tools 중 21 ready/0 incomplete/9 unavailable, required ruff·pytest·python3 READY, health PASS였습니다. import cycle은 `redaction_values` 추출로 수정했으며, 원격 CI·PR/Pages 증거는 main 통합 후 기록합니다.
- **I2-2 shared analysis context와 artifact manifest**: 한 번 발견한 프로젝트 사실을 immutable `ProjectModel`에 고정하고, 이미 수집된 immutable `CapabilityInventory`와 함께 immutable `AnalysisContext`로 엔진·리포터에 전달합니다. `CompilationContext`는 compile invocation snapshot을 보유하고, build adapter의 실행 중 상태는 mutable `BuildSession`에만 남깁니다. 빌드가 성공하면 `ArtifactManifest`가 project/shadow root 아래의 regular output을 variant와 producer, source/config/toolchain identity, SHA-256·size·mode와 함께 frozen record로 발행합니다.
  - 프로젝트·shadow 경계는 canonical path와 symlink escape 검사를 거치며, context와 manifest의 JSON 투영은 project-relative POSIX 경로만 노출합니다. 외부 include/search path처럼 호스트 경로가 섞일 수 있는 값은 report redaction 경계를 통과해 절대 경로를 외부로 내보내지 않습니다.
  - `RELEASE`, `COVERAGE`, `SANITIZE` variant를 명시적으로 요청하고 각 variant의 shadow suffix·instrumentation flags를 분리합니다. build/test/sanitize는 각각 release/coverage/sanitize variant를 adapter에 전달하며, 하나의 context snapshot을 공유합니다.
  - `ici.result/v3`의 선택적 `analysis_context`(`ici.analysis-context/v1`)와 engine-level `artifact_manifests`(`ici.artifacts/v1`)가 project facts, compilation units, requested variants와 전체 provenance를 보존합니다. 기존 v3 report는 두 확장 필드 없이도 계속 읽고 migrate할 수 있습니다. 정확한 전체 테스트 수는 작업 중인 PR의 CI artifact를 기준으로 고정하며, 병합 조건은 full suite green입니다.
- **I2-3 선언형 verification pipeline**: hardcoded engine loop를 immutable `EngineDescriptor` registry와 DAG executor로 교체했습니다.
  - descriptor가 `name`, `dependencies`, `produces`/`consumes`, `profiles`,
    `execution`, `build_variant`를 선언하며, startup에서 dependency DAG·artifact producer
    ownership·profile closure를 검증해 잘못된 graph를 분석 전에 거부합니다.
  - 독립적인 read-only engine은 기본 최대 4개까지 병렬 실행하고, build node는 read-only 작업 및
    다른 build node와 겹치지 않게 직렬 실행합니다. 결과는 registry 선언 순서로 수집해 완료
    시점에 영향을 받지 않습니다.
  - `fast`/`standard`/`deep`은 engine selection만 조정하며 동일 rule의 threshold나
    의미를 변경하지 않습니다. verify CLI와 `[ici] profile` 설정을 지원하고, `analysis_context.profile`은
    optional JSON field로 추가해 기존 v3 archive와 호환합니다.
  - I2-4의 cache key·invalidation·reproducibility 구현은 다음 항목으로 기록합니다.
- **I2-4 user-local analysis cache**: `feat/analysis-cache`에 engine result cache를 추가했습니다.
  - cache key는 canonical project root, source와 build/config content digest, effective ici
    config, capability/toolchain versions, engine descriptor·implementation, build variant,
    ici producer version을 포함하므로 입력 identity가 달라지면 자동으로 miss가 됩니다.
  - 완료된 `PASS`/`WARN`/`FAIL` 중 evidence가 완전하고 artifact manifest가 유효한 결과만 저장할
    수 있습니다. `ERROR`/`SKIP`/`NOT_RUN`, timeout·truncated output·tool error, invalid/stale
    artifact는 성공 cache로 저장·재사용하지 않습니다.
  - 기본 user-local 경로(`~/.cache/ici/analysis`, `XDG_CACHE_HOME`/`ICI_CACHE_DIR` override),
    `ici verify --no-cache`, `ici cache`, `ici cache --clear`를 제공합니다. entry는 local
    temp file + flush/`fsync` + atomic replace로만 발행하고 project source는 읽기만 하므로
    remote cache나 project-file mutation이 없습니다.
  - 현재 `dead` engine은 Python-only와 hybrid를 포함한 모든 result의 cache key 생성·load·store를
    비활성화합니다. external/generated include closure와 compiler binary content가 모델링될
    때까지 `dead` 결과를 cache에서 재사용하거나 저장하지 않습니다.
  - v3 engine JSON에 optional `cache_hit`와 nullable `cache_key`를 추가해 hit identity를
    표시하면서 기존 archive 소비자와 호환합니다. Python 3.10 전체 935 tests, Ruff
    check/format, pyz 이중 빌드 SHA-256
    `6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16` 일치와 source status
    unchanged, smoke 전체를 통과했습니다. 표준 프로필은 118.49초·0 hit에서 2.38초·12 hit로
    줄었고 정규화된 결과 hash와 finding 3,497건이 일치했습니다. 이 기능의 PR/CI Merge Gate·
    Pages·release evidence는 아직 기록하지 않았습니다.
- **I1-3 baseline/delta gate**: 이전 v3 finding report를 프로젝트 내부 baseline으로 읽고, stable fingerprint와 위치 보조 정보로 finding occurrence를 new·unchanged·moved·resolved로 분류합니다.
  - 전체 inventory는 보존하면서 PR gate는 actionable한 new 또는 regressed finding만 대상으로 분리합니다. fail-on-new 정책을 켜면 gated count가 있을 때 suite가 FAIL이 되고, baseline 비교 자체가 engine 결과를 가짜로 추가하지 않습니다.
  - producer/fingerprint/analysis policy/tool policy identity가 다르면 compatibility warning으로 남기며, duplicate fingerprint도 occurrence 단위(multiset)로 비교합니다.
  - baseline은 v3 baseline 계약과 canonical project-relative location을 요구하고, 현재 project root 밖 경로·절대경로·비정규 separator·symlink escape를 거부합니다. baseline 기록은 고유 임시 파일과 atomic replace를 사용해 부분 파일을 남기지 않으며, 실패한 fail-on-new gate가 입력 baseline을 같은 경로에서 덮어써 regression을 숨기는 것도 차단합니다.
  - verify에 --baseline, --fail-on-new, --write-baseline을 추가했습니다. --fail-on-new은 baseline 없이는 실행하지 않고, report와 baseline output 경로 충돌도 거부합니다.
  - console, Markdown, zero-CDN HTML은 issues-first delta와 compatibility warning을 보여 주고 JSON은 전체 delta inventory를 보존합니다. GitHub sticky comment도 single/multi-project report에 new·regressed·gated count와 baseline gate 상태를 요약하며, legacy report와 null baseline은 계속 읽습니다.

- **I1-4 issues-first console과 공통 grouping**: `ici verify` 전용 `--verbose`,
  `--max-findings N`(엔진별 기본 5개 display group, `0`은 summary만), `--group-by
  engine|severity|category|file|rule`을 구현했습니다. verbose는 cap을 해제하고, grouping과
  cap은 console-only projection으로 제한해 JSON·HTML·Markdown·baseline 원본 inventory를
  그대로 보존합니다.
  - duplicate는 같은 실행의 같은 clone group 안에서 같은 파일의 겹치는 region만 표시상
    병합하며 원본 occurrence와 fingerprint를 유지합니다. HTML `Issues` 탭도 native v3
    finding inventory를 기반으로 전체 결과를 표시합니다.
  - 로컬 Python 3.10 전체 품질 게이트 756/756 tests, focused console 16 tests, Ruff
    check/format, pure-Python 10-distribution 2.0 MiB pyz(no certifi), smoke 전체 및
    80-column 안정성 검증을 통과했습니다. 최종 안정 self verify의 built `dist/ici.pyz`
    실행은 exit 0, suite는 WARN이었고 self verify 출력은 144 lines/15,288 bytes였습니다.
    해당 출력에 내장된 test engine 수치는 756/756이며 local self verify coverage는
    line/function/branch 87.8%/96.6%/78.8%, TEM 4.83이었습니다. engines는 Pass 8/Warn 4/
    Fail 0/Error 0/Skip 0, complexity는 최대 23·이슈 64건, duplicate는 16.2%·338 groups·
    1,006 actionable occurrences였습니다.
  - 콘솔은 actionable 1,088건, visible 21/420 display groups, represented 34,
    hidden 1,054 findings/399 groups를 기록했습니다. HTML은 3,383,523 bytes이며 clone
    group card 338개와 issue engine row 1,088개를 유지했고 external script/stylesheet
    reference는 0개였습니다. 초기 측정의 lint 실패는 에이전트 파일 작성 경합에 따른 참고
    기록이며, 위 최종 안정 self verify를 기준으로 삼습니다.
  - [PR #89](https://github.com/jihoon22-lee/ici/pull/89)는 squash commit
    [`cc0ad469afe7c5d2713ef768610791a394a66f0b`](https://github.com/jihoon22-lee/ici/commit/cc0ad469afe7c5d2713ef768610791a394a66f0b)로
    병합됐습니다. [CI run 33330722781](https://github.com/jihoon22-lee/ici/actions/runs/33330722781)의
    모든 required checks가 green(756 tests)이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/89#issuecomment-5470778278)에
    결과가 기록됐습니다. CI report stats는 ici WARN(TEM 4.83, Pass 8, Warn 4,
    line 87.8%, function 96.6%, branch 78.9%), viewer PASS(TEM 4.89, 7/7 tests)였습니다.
    [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/89/)는 HTTP 200·external
    script/stylesheet refs 0, [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/89/)는
    HTTP 200·external refs 0이었습니다.

- **실행 가능한 엔진 support/capability matrix**: 14개 엔진의 Python/C++ 지원을 `exact`, `heuristic`, `tool-backed`, `unsupported` mode와 Qt 호환성, 필수·선택 도구, fallback, 신뢰도 및 알려진 한계로 중앙 선언합니다. 프로젝트 소스와 유효 정책을 적용해 `applicable`/`enabled`/`active_mode`를 계산하며, 실행 증거를 `MEASURED`·`ESTIMATED`·`NOT_RUN`·`NOT_APPLICABLE`과 일관되게 연결합니다.
  - 전체 verify와 단독 엔진의 `ici.result/v3` JSON, `ici doctor --json`은 동일한 구조를 사용합니다. v3 schema의 matrix 필드는 이전 v3 archive를 깨뜨리지 않도록 선택 사항이고 새 writer는 항상 object 또는 `null`을 기록합니다.
  - `doctor`, zero-CDN HTML, Qt viewer가 같은 행을 표시하며, 미지원 또는 실행하지 못한 범위를 PASS로 보이지 않게 합니다. 문서의 지원 표는 중앙 선언에서 생성되고 회귀 테스트로 정확히 일치하는지 검증합니다.
- **`ici.result/v3` finding 계약**: 모든 엔진의 기존 `InspectionTarget`을 안정적인 ici rule id, category/severity/confidence, canonical project-relative 위치, 관련 위치, 설명·개선안, suppression 근거, 단위가 붙은 숫자 metric과 SHA-256 fingerprint를 가진 `Finding`으로 함께 직렬화합니다. 기존 `targets`는 이행 기간 동안 그대로 보존하며 v2→v3 migration helper와 배포되는 JSON Schema를 제공합니다.
  - fingerprint는 checkout root와 Windows/POSIX separator에 무관하며 symbol이 있으면 줄 이동에도 유지되고, symbol이 없으면 정확한 region 변화에 반응합니다. native v3 finding도 출력 경계에서 동일한 경로·fingerprint 규칙으로 정규화합니다.
  - viewer는 새 v3와 보관된 v2 리포트를 모두 읽습니다. 현재 UI는 호환용 `targets`를 계속 표시하므로 writer 전환과 viewer 전환을 한 번에 강제하지 않습니다.
  - 중앙 redaction 경계가 콘솔, JSON, HTML, Markdown, GitHub annotation/sticky comment 및 CLI 반환 결과의 credential 형태를 마스킹합니다. message·snippet·raw tool output·argv·error·extra·설명·개선안·suppression reason뿐 아니라 source path와 finding metric 이름·단위도 같은 정책을 사용하며, 일반 경로는 유지합니다.
  - JSON writer는 키를 정렬해 같은 결과를 재현 가능하게 기록하고 NaN/Inf를 파일을 만들기 전에 거부합니다. legacy metric adapter도 boolean·비숫자·비유한 값을 finding metric으로 오인하지 않으며, 비밀 키 두 개가 같은 마스킹 키가 되더라도 suffix를 붙여 metadata 항목 수를 보존합니다.
  - writer는 schema의 non-empty string, finite/non-negative number, boolean, 1-indexed source region 불변식을 직접 검사해 잘못된 producer가 schema-invalid JSON을 만들지 못하게 합니다. v2 migration은 누락된 suite count와 engine 기본 필드를 canonical 값으로 채우면서 producer extension은 보존합니다.
- **CI의 최종 판정을 `Merge Gate` 하나로 고정**: Python/ici self-dogfood, Qt viewer GUI, PR HTML 게시가 모두 성공해야 PR용 최종 체크가 통과합니다. 기존 ruleset은 self-dogfood만 필수여서 GUI나 댓글 게시가 실패해도 병합할 수 있었습니다.
  - `report-pr`은 quality 결과가 WARN/FAIL이어도 생성된 아티팩트를 게시할 수 있도록 `always()`로 실행하되, 최종 gate는 원래 verify 결과를 별도로 검사합니다.
  - gh-pages 쓰기는 concurrency group으로 직렬화합니다. 서로 다른 PR이 Contents API의 같은 branch head를 동시에 갱신해 한쪽 리포트를 잃지 않습니다.
  - 게시 직후 실제 sticky 댓글에서 두 HTML 링크를 다시 읽고 GitHub Pages 비동기 배포가 완료될 때까지 cache-busting URL을 확인합니다. 파일 업로드 API 성공만으로 게시 완료를 선언하지 않습니다.
- **`viewer` Qt 셸 회귀 테스트**: 정상 리포트를 연 뒤 missing 또는 malformed 리포트를 열 때 `MainWindow`의 모델, suite 상태, 게이트·점수 라벨, 창 제목이 이전 보고서 데이터를 남기지 않는지 QtTest로 검증합니다. QtTest 실행은 프로젝트 루트 CTest에 등록되어 Qt 5와 Qt 6에서 같은 경로를 확인합니다.
- **I0-4 self-quality 기준선을 기록하고 floor를 현실화**: 2026-08-31 `origin/main@fa3ad28` snapshot에서 self verify를 세 번 반복해 634/634 테스트, TEM 4.78, line/branch/function 85.9%/77.9%/95.691%, console 2,276줄, duplicate 237 groups를 확인했습니다. 변동 여유를 둔 floor를 TEM 4.5, branch 70%, function 90%로 올리고 측정값·ratchet 조건을 `ici.toml` 주석과 구조화된 기준선에 남겼습니다.

### Changed
- **릴리스를 태그 이름이 아닌 정확한 main 검증 commit에 묶음**: 태그 commit이 `origin/main`의 조상이고 동일 SHA의 `Merge Gate`가 성공한 경우에만 쓰기 권한을 가진 build/release job이 시작됩니다. 수동 실행도 선택한 branch가 아니라 이미 존재하는 태그 commit을 detached checkout하며, 패키지 버전·CHANGELOG section을 같이 검증합니다.
  - provenance job은 `contents: read`/`checks: read`만 사용하고, 검증된 SHA를 인수한 build job에만 `contents: write`를 부여합니다.
  - 릴리스 candidate에서 Ruff, Python 3.10 전체 테스트, reproducible pyz/smoke, ici self verify, viewer C++/Qt verify와 GUI CTest/headless smoke를 다시 실행합니다. self/viewer HTML·JSON 검증 리포트도 release asset으로 보존합니다.
- **`viewer` GUI를 루트 CMake에 통합**: `ICIRV_BUILD_GUI` 옵션으로 Qt GUI를 선택적으로 구성하고 `icirv_gui` 정적 라이브러리와 `icirv-gui` 실행 파일을 분리했습니다. `ICIRV_BUILD_GUI=OFF`에서는 Qt를 탐색하지 않고 정적 `icirv` CLI만 configure/build할 수 있습니다.
- **Qt 버전 탐지를 설치 환경에 맞춤**: 기본 구성은 Qt 6을 우선하고 Qt 5로 폴백하며, `CMAKE_DISABLE_FIND_PACKAGE_Qt6=ON`으로 Qt 5를 강제하는 빌드도 지원합니다. CI는 Qt 5·Qt 6 각각에서 4개 CTest와 headless report open을 실행하고, Qt package를 모두 비활성화한 configure에서 정적 CLI 계약도 확인합니다. 릴리스 워크플로의 GUI 경로도 새 루트 타깃에 맞췄습니다.

### Fixed
- **I2-1 capability snapshot import cycle**: `capabilities -> redaction -> models -> capabilities` 순환을 model-independent `redaction_values` 모듈로 분리해 제거했습니다. 수정 후 focused cycle 검사에는 기존 `test/test_interpreter` 순환만 남습니다.
- **`lint`가 설정된 Python 소스 범위 밖까지 검사하던 문제**: C++ 전용 프로젝트의 `benchmarks/out.py` 같은 파일이 존재한다는 이유만으로 Python lint와 AST 폴백이 활성화되고, Python 프로젝트에서도 Ruff가 프로젝트 루트 전체(`.`)를 검사해 `project.source_dirs` 계약을 무시했습니다. 이제 공유 source inventory에 선택된 Python 파일이 있을 때만 Python lint를 실행하고, Ruff check/format과 AST syntax fallback 모두 그 목록의 project-relative 경로만 사용합니다. 선택된 소스가 전혀 없으면 `LintScope`의 `SKIP`/`NOT_APPLICABLE` 결과로 분석하지 않았음을 명시하며, C++ 소스와 범위 밖 Python 파일은 서로의 판정에 영향을 주지 않습니다.
- **qmake shadow build가 stale 실행 파일과 coverage 산출물을 재사용하던 문제**: qmake가
  생성한 Makefile은 정적 라이브러리를 링크하는 소비자 실행 파일에 항상 명시적인
  dependency를 남기지 않을 수 있습니다. 재사용한 shadow에서 core archive만 다시
  빌드되면 test executable은 이전 archive를 계속 링크하고, 그 실행이 만든 `.gcda`의
  stamp가 새 `.gcno`와 달라 gcov가 coverage를 0%로 보고할 수 있었습니다. 이제 qmake
  configure 뒤 deterministic `make clean`을 실행하고 evidence로 기록한 뒤 parallel
  build를 시작합니다. clean 실패는 stale 결과를 숨기지 않도록 명시적인 build 실패로
  남기며, CMake adapter 경로에는 이 규칙을 적용하지 않습니다. rebase된
  `1098a62`/`f692a3c` 기준 candidate `ici.pyz`로 DiskMap의 실제 qmake
  test/sanitize를 재검증해 두 경로 모두 `/usr/bin/make clean` 성공 evidence와
  `Suite PASS`를 확인했습니다. 원격 CI·PR·Pages 검증과 main 반영은 아직 남아
  있으므로 이 로컬 증거만으로 병합 완료를 간주하지 않습니다.
- **self verify의 mypy `annotation-unchecked` note 제거**: `sanitize`, `exception`, `dead`, `test` 엔진의 생성자 네 곳이 `*args/**kwargs` untyped body였고, 변수 annotation마다 동일 note를 냈습니다. BaseEngine과 동일한 Python 3.10 호환 `project_root`/`config` 시그니처와 반환형을 적용해 동작은 유지하면서 mypy note를 0건으로 만들었습니다.
- **HTML은 올라갔지만 PR 댓글이 실패해도 `ici publish`가 성공하던 문제**: PR publish의 성공 조건에 sticky comment URL을 포함했습니다. 단일·다중 리포트 모두 `pull-requests: write` 실패를 0이 아닌 종료 코드로 전달하며, 업로드 실패 시 아직 존재하지 않는 Pages URL을 만들지 않습니다. 다중 리포트 댓글 footer의 경로도 `/`로 이어 붙인 가짜 경로 대신 쉼표로 구분합니다.
- **`cycle`이 directory-qualified C++ include의 정보를 버리던 문제**: `core/format.hpp`와 `gui/format.hpp`가 함께 있을 때 `#include "core/format.hpp"`도 basename `format.hpp`만 비교해 모호하다고 버렸고, 실제 include cycle을 놓쳤습니다. 이제 include가 지정한 전체 path suffix가 프로젝트 파일 하나와 유일하게 일치할 때만 간선을 연결합니다.
  - bare `#include "format.hpp"`처럼 실제로 여러 후보가 있는 경우는 계속 추측하지 않습니다.
  - 유일한 후보가 없는 quoted include와 여러 후보가 있는 include는 파일·행·후보와 함께 `CppIncludeUnresolved`/`CppIncludeAmbiguous` 타깃으로 남고, `extra`에 전체 개수와 잘린 진단 개수를 기록합니다. generated header나 실제 compiler `-I` 순서는 아직 알지 못하므로 결과에는 `unique_project_path_suffix` 휴리스틱임을 명시합니다.
- **viewer가 실패한 리포트 교체 뒤 이전 데이터를 표시하던 문제**: 파일을 읽지 못하거나 JSON/schema 검증에 실패하면 suite와 트리를 비우고, 게이트·점수 라벨과 loaded title을 초기화한 뒤 오류 원인을 status label에 남깁니다.
- **headless CI의 viewer dogfood가 QWidget 테스트에서 abort하던 문제**: 별도 Qt matrix만 아니라 ici가 CTest를 실행하는 C++ gate에도 `QT_QPA_PLATFORM=offscreen`을 적용합니다. GUI 테스트를 root build에 통합한 후 display가 없는 GitHub runner에서 `test`/`sanitize` 엔진이 동시에 실패하던 경로를 고정했습니다.

## [0.6.0] - 2026-08-30

### Added
- **CMake·qmake 프로젝트를 실제 빌드 정의로 빌드합니다 (`build`)**: 루트에 `CMakeLists.txt` 나 `*.pro` 가 있으면 `build` 엔진이 거부하는 대신 그 빌드 시스템에 configure·build 를 위임합니다. 지금까지 정상적인 CMake/qmake 프로젝트는 `ici build` 를 아예 쓸 수 없었습니다.
  - 백엔드 선택 근거를 `ToolEvidence` 로 남깁니다 — 조용히 정하면 "이 빌드가 왜 이렇게 돌았나" 를 리포트만 보고 알 수 없습니다.
  - 손으로 쓴 `Makefile` 만 있는 프로젝트는 여전히 거부되며, 메시지가 **어느 어댑터가 없어서인지**를 말하도록 바뀌었습니다.
  - CTest·QtTest 가 낸 XML 은 DTD 를 담고 있으면 거부합니다. `ElementTree` 는 내부 엔티티를 확장하는데 이 문서의 내용은 **검증 대상 프로젝트가 정합니다** — PR 소스를 실행하는 게이트에서는 그것만으로 billion-laughs 문서를 넘길 수 있습니다. 엔티티는 DTD 에서만 선언되므로 DOCTYPE 을 막으면 확장 자체가 사라지고, 두 도구 모두 DTD 를 내지 않습니다.
  - configure 가 사유 없이 실패해도 그 사실을 타깃으로 남깁니다. 그러지 않으면 "산출물이 생성되지 않았다" 는 엉뚱한 실패로 끝나 원인을 짚을 수 없습니다.
- **`Q_OBJECT` 클래스를 단위 테스트할 수 있습니다 (`test`)**: C++ 테스트를 `g++ -std=c++17` 로 직접 컴파일·링크하는 대신 프로젝트의 CMake/qmake 정의에 위임합니다. moc 가 빌드 시스템 쪽에서 돌므로 `Q_OBJECT` 클래스가 더 이상 vtable 미해결로 링크에 실패하지 않고, `-std` 고정도 사라져 C++20/23 프로젝트를 검증할 수 있습니다.
  - 커버리지 계측 플래그는 ici 가 주입합니다. 프로젝트가 커버리지 빌드를 선언하도록 요구하면 설정을 빠뜨렸을 때 측정이 조용히 사라지고, TEM 점수가 그 측정 위에 서 있습니다.
  - 어댑터 경로에서는 `project.cpp_external_build_dirs` 가 무시됩니다. 이 설정은 ici 가 moc 를 돌리지 못한다는 전제 위에 있었고, 어댑터가 그 전제를 없앱니다. 바이너리를 만드는 세 엔진이 모두 어댑터를 쓰므로 예외가 없습니다.
  - CTest·QtTest 는 테스트 **이름**만 주고 소스 파일을 주지 않습니다. `tests/` 에서 stem 이 일치하는 `.cpp` 를 찾아 붙여 모든 타깃이 파일 위치를 갖게 합니다 (`AGENTS.md` 5-1).
  - 커버리지 스코프에서 **진입점(`main()` 을 정의한 번역 단위)은 제외됩니다.** g++ 경로는 링크에서 `main.cpp` 를 빼 왔으므로 애초에 gcov 에 닿지 않았습니다. 어댑터가 그걸 세면 **코드가 하나도 바뀌지 않았는데 CMake 로 옮겼다는 이유만으로 커버리지가 떨어집니다** — 실제로 `viewer` 에서 branch 71.2%, function 84.5% 로 임계값 아래로 내려갔습니다. 제외 후 전환 전과 같은 TEM 4.94 로 돌아왔습니다.
- **`sanitize` 도 어댑터를 씁니다**: `sanitize` 는 `tests/**/*.cpp` 를 각각 plain g++ 로 컴파일하므로 Qt 테스트가 하나라도 있으면 헤더를 찾지 못해 깨졌습니다. 이제 프로젝트의 빌드 정의로 같은 타깃을 `-fsanitize=address,undefined` 로 빌드해 실행합니다.
  - 세 엔진이 **각자의 shadow 디렉터리**를 씁니다 (`-build`, 없음, `-asan`). 하나를 공유하면 엔진이 돌 때마다 상대의 오브젝트를 다른 플래그로 다시 빌드합니다.
  - **`build` 는 더 이상 계측 플래그를 주입하지 않습니다.** 릴리스 산출물에 `--coverage` 가 들어가고 있었습니다.
  - qmake 경로는 `make check` 의 전사를 기준으로 결과를 읽습니다. `-xunitxml` 은 **QtTest 바이너리에만** 의미가 있고 실제 프로젝트는 QtTest 와 자체 `main()` 테스트를 섞어 쓰므로, XML 만 읽으면 **QtTest 가 아닌 테스트가 보고에서 조용히 사라지거나** 전부 통과한 스위트가 "테스트 0건" 이 됩니다. QtTest 의 함수 단위 실패 정보는 해당 바이너리의 메시지에 붙습니다.
  - 그 결과 **두 백엔드가 같은 단위(테스트 바이너리)를 셉니다.** TEM 의 통과/전체 비율을 경로가 다른 프로젝트끼리 비교할 수 있습니다.
  - qmake 프로젝트의 **line 커버리지가 통째로 유실되던 문제**를 고쳤습니다. gcov 는 컴파일러가 본 경로를 기록하는데, CMake 는 절대 경로로 컴파일하지만 qmake 는 shadow 트리 안에서 상대 경로로 컴파일합니다. 그러면 `.gcov` 파일명이 `^#^#^#src#format.cpp` 가 되고(`-p` 가 `/` 를 `#` 로, `..` 를 `^` 로 바꿉니다) 파일명만으로는 원래 경로를 복원할 수 없습니다. 이제 파일 안의 `Source:` 헤더를 읽습니다.
    - 게이트는 이 상황에서 조용히 통과하지 않고 `ESTIMATED` 로 낮춰 WARN 을 냈습니다. 설계대로 동작한 것이지만, 측정 자체가 깨져 있었습니다.

### Changed
- **C++ 빌드 경로가 두 갈래가 되었습니다**: 루트 빌드 디스크립터 유무로 어댑터 경로와 기존 g++ 경로가 갈립니다. 어느 경로에서 `project.cpp_external_build_dirs` 와 `-std` 고정이 적용되는지, 그리고 **두 어댑터의 테스트 카운트 단위가 달라 TEM 을 서로 비교할 수 없다**는 점을 `docs/user-guide.md` §2.5 에 정리했습니다.
- **마크다운을 `ruff format` 대상에서 제외했습니다**: ruff 는 `.md` 안의 Python 코드 블록도 포맷하는데, 설계 문서는 클래스 메서드를 문맥째 인용합니다. 포맷되면 들여쓰기가 벗겨져 메서드가 모듈 함수로 바뀌고, 문서가 컴파일되지 않을 코드를 설명하게 됩니다.

### Fixed
- **ext4 작업 트리에서 개발 스크립트를 직접 실행할 수 없던 문제**: Windows 드라이브에서 생성된 Git 인덱스에 실행 비트가 기록되지 않아 `./scripts/build-pyz.sh`, `./scripts/smoke.sh`, `./tools/ici` 같은 문서화된 명령이 WSL 내부 ext4 clone에서 `Permission denied`로 실패했습니다. 빌드·스모크·런처·개발 래퍼의 실행 비트를 저장소에 기록하고, 이 개발 규약의 CHANGELOG 링크도 특정 머신의 절대 경로 대신 저장소 상대 경로를 사용합니다.
- **AST 폴백이 무엇을 검사했는지 보고하지 않았다 (`lint`)**: ruff 가 없을 때 내려가는 폴백은 `SyntaxError` 가 났을 때만 타깃을 남깁니다. 그래서 **깨끗한 프로젝트와 아무것도 들여다보지 않은 프로젝트가 똑같이 "타깃 0건"** 으로 보였습니다 — 도그푸딩으로 찾은 조용한 검증 실패들과 같은 모양입니다.
  - 폴백이 파싱한 파일 수를 기록하고(`extra.python_files_parsed`), 스코프를 타깃으로 남깁니다: `Ruff was unavailable; parsed 2 Python file(s) for syntax errors only. Style and lint rules were not checked.`
  - 무엇을 검사했는지뿐 아니라 **무엇을 검사하지 않았는지**도 말합니다. 그러지 않으면 읽는 사람이 lint 가 돌았다고 가정합니다.

## [0.5.5] - 2026-08-27

### Fixed
- **리포트가 자기 판정 사유를 말하지 않았다**: 콘솔은 `Pass/Warn/Fail/Error` 카운트를 출력하는데 스위트 상태는 `aggregate_suite_status` 의 다른 규칙(required 엔진의 SKIP·NOT_RUN 이 전체를 승격)으로 정해집니다. 그래서 **`Error: 0` 인데 스위트는 `ERROR`** 인 리포트가 나왔고, 화면 어디에도 이유가 없었습니다.
  - `gate_reason()` 을 추가해 요약 패널이 판정과 **그 사유**를 함께 출력합니다: `Suite: ERROR — required engine 'dead' reported SKIP`. 집계 규칙과 같은 함수에 두어 둘이 어긋나지 않게 했습니다.
- **`test` 엔진이 실패 사유를 어디에도 남기지 않았다**: 임계값 판정이 불리언만 반환해, **테스트 전부 통과 · 비-PASS 타깃 0건 · 요약에 판정 근거 없음** 인 채로 FAIL 이 나왔습니다. 원인을 알려면 JSON 의 `extra.branch_coverage` 를 직접 까야 했습니다.
  - 미달한 임계값마다 `InspectionTarget` 을 발행합니다 — `Threshold: Branch coverage — 88.4% is below the configured minimum 99.0%`. `AGENTS.md` 5-1(위치 추적 필수)이 게이트 판정이라고 예외일 이유가 없습니다.
  - 요약에도 branch 를 포함합니다. TEM 이 branch 로 계산된 경우에만 보이던 값이라, 판정을 뒤집은 수치가 정작 요약에서 빠져 있었습니다.
- **`sanitize` 가 테스트 없는 C++ 프로젝트에서 게이트 사유를 가로챘다**: 실행할 테스트가 없어 SKIP 하는데 그것이 required SKIP 으로 스위트를 ERROR 로 만들어, **진짜 이유("테스트가 없음", `test` 엔진이 이미 보고)** 대신 sanitize 가 지목됐습니다. 이제 스코프가 아예 없으면 `NOT_APPLICABLE` 입니다.
  - 다만 **스코프는 있었는데 측정이 안 된 경우**(예: 테스트가 전부 skip)는 `ESTIMATED` 로 남겨 계속 게이트를 막습니다. 그건 검증에 실제로 뚫린 구멍입니다.

### Fixed
- **C++ 테스트 바이너리의 실행 디렉터리가 엔진마다 달랐다**: `test` 엔진은 gcov 가 있으면 `build/tests` 에서, 없으면 프로젝트 루트에서 실행했고, `sanitize` 는 프로젝트 **밖** 임시 디렉터리에서 실행했습니다. 데이터 파일을 상대 경로로 읽는 테스트는 **어느 엔진이 띄웠는지, 그리고 gcov 가 설치돼 있는지에 따라** 통과하거나 실패했습니다. 재현 가능한 게이트가 아니었습니다.
  - 이제 **항상 프로젝트 루트**에서 실행합니다. pytest 가 하는 것과 같고, 테스트를 쓰는 사람이 기대하는 곳입니다.
  - 커버리지는 영향받지 않습니다. gcov 는 컴파일 시점에 오브젝트의 절대 경로를 기록하므로 `.gcda` 는 실행 CWD 와 무관하게 오브젝트 옆에 생성됩니다 — 실험으로 확인했습니다.
  - 전후 대비(프로젝트 루트 기준 상대 경로로 픽스처를 읽는 테스트): 수정 전 `test` FAIL 0/1·커버리지 0%, `sanitize` ERROR, suite ERROR → 수정 후 모두 PASS. 회귀 테스트 `test_cpp_tests_run_from_the_project_root` 추가.

### Fixed
- **순수 C++ 프로젝트가 기본 설정으로 초록불이 될 수 없던 문제**: `SKIP` 이 서로 다른 두 상황을 뭉뚱그리고 있었습니다 — **"이 프로젝트에 적용되지 않음"** 과 **"돌았어야 하는데 못 돌았음"**. `aggregate_suite_status` 는 required 엔진의 SKIP 을 후자로만 보고 스위트를 `ERROR` 로 승격시켰는데, `dead` 는 Python 전용이라 C++ 프로젝트에서 **항상** SKIP 합니다. 코드 품질과 무관하게 영구히 빨간불이었고, 게이트로 쓸 수 없었습니다.
  - `EvidenceState.NOT_APPLICABLE` 을 추가했습니다. 엔진이 분석하는 언어가 프로젝트에 아예 없을 때 쓰며, 게이트 판정(ERROR·WARN 양쪽)에서 제외됩니다. 도구 부재를 뜻하는 `NOT_RUN` 과는 구분됩니다 — 그쪽은 계속 게이트를 막아야 합니다.
  - `dead`·`exception` 이 소스 부재로 건너뛸 때 이 상태를 씁니다. 기존에는 `ESTIMATED` 였는데, **추정한 것이 없으므로** 잘못된 표현이었습니다.
  - `type` 도 같은 처리를 받습니다. C++ 전용 프로젝트에서는 mypy 가 읽을 Python 도 없고 C++ 타입 검사는 미구현이라, 모든 C++ 프로젝트가 **고칠 수 없는 WARN** 을 영구히 달고 있었습니다(ICI-GAPS C-5). 이제 SKIP/NOT_APPLICABLE 이며, 어느 파일이 검사되지 않았는지는 타깃으로 그대로 남습니다.
  - 실측: 우회 설정 없는 순수 C++ 프로젝트가 `suite_status = PASS`, exit 0. `viewer`·`diskmap`·`loglens` 모두 PASS. **Python 프로젝트(ici 자신)는 변화 없음** — WARN 5건, `type` 은 여전히 MEASURED.

`ici.result/v2` 스키마에 값이 하나 추가되는 것이라 기존 소비자와 호환됩니다.

## [0.5.4] - 2026-08-27

### Added
- **릴리스에 뷰어 바이너리 추가**: `icirv`(CLI)와 `icirv-gui`(Qt 셸)가 `ici.pyz` 와 함께 올라갑니다. 둘의 성격이 다르므로 나눠서 설명합니다.
  - **`icirv` — 정적 링크, 어디서나 동작.** Qt 무의존이라 정적 링크가 가능합니다. glibc 는 전방 호환이 안 되고 반입 대상은 RHEL 8(glibc 2.28)인데 CI 러너는 Ubuntu(2.39)라, 동적 링크였다면 기동조차 못 합니다. 정적으로 만들면 그 질문 자체가 사라집니다. **폐쇄망에서 터미널 요약(게이트 사유·엔진 표·조치 항목)은 지금 바로 쓸 수 있습니다.** 릴리스 워크플로가 실제로 정적인지 검사하고 실제 리포트를 파싱시켜 봅니다 — 실패하면 릴리스가 멈춥니다.
  - **`icirv-gui` — 개발 환경용.** 런타임에 Qt 6 와 glibc 2.34+ 가 필요합니다. **RHEL 8 에서는 동작하지 않습니다**(Qt 6 패키지 없음, glibc 2.28). 요구사항을 `icirv-gui.README.txt` 로 함께 올려, 대상 머신에서 시행착오로 알아내지 않아도 되게 했습니다.
  - GUI 의 반입용 빌드는 `almalinux:8` 컨테이너에서 Qt 를 직접 빌드해 번들하거나 정적 링크해야 합니다. 의도적으로 이번 범위에서 제외했습니다.
- **`viewer/CMakeLists.txt`**: Qt 무의존 CLI 전용 빌드. GUI 는 `src/gui/` 에 자기 CMakeLists 를 유지합니다 — 분리해 두는 것이 CLI 를 정적으로 링크해 Qt 없는 머신에 보낼 수 있게 하는 조건입니다. 저장소 루트가 아니라 `viewer/` 아래에 두는 이유는 ici 의 build 엔진이 루트의 빌드 디스크립터를 거부하기 때문입니다.

### Changed
- **`type` 엔진에 전용 탭 (`🏷️ Static Types`)**: 전용 탭이 없는 엔진은 요약 탭에서 `<details open>` 로 **모든 비-PASS 타깃을 펼친 채** 나열합니다. C++ 프로젝트에서 `type` 은 소스 파일마다 SKIP 타깃을 내므로, 그 "검사 안 함" 목록이 요약을 뒤덮고 정작 볼 것이 묻혔습니다.
  - 새 탭은 **실제 발견 사항과 검사되지 않은 파일을 분리**합니다. 후자는 접힌 채로 사유별 개수와 함께 요약되므로 정보는 남되 조치 목록을 오염시키지 않습니다.
  - 요약 탭의 `type` 행은 다른 엔진들처럼 탭 이동 버튼 하나로 바뀝니다.

### Fixed
- **C++ 중첩 깊이가 Python 보다 한 단계 깊게 측정됐다 (`complexity`)**: C++ 쪽은 함수 본문 자체를 깊이 1로 셌고 Python 쪽(`_max_nesting_depth`)은 본문 안의 블록만 셌습니다. **동일한 3중 중첩 코드가 C++ 4, Python 3** 으로 나와, 같은 `warn_nesting` 임계값이 C++ 에만 한 단계 엄격하게 적용됐습니다.
  - 도그푸딩한 C++ 프로젝트 3종의 복잡도 경고가 **전부 이 중첩에서 나왔고 CC 초과는 0건**이었습니다(최대 CC 9~15, 한도 15). 임계값이 아니라 측정이 문제였습니다.
  - 이제 두 언어가 같은 코드에 같은 값을 냅니다. `diskmap`/`loglens`/`viewer` 의 복잡도 경고 9건이 사라지고 최대 중첩은 3(한도 4 미만)입니다. **ici 자신은 그대로 경고합니다** — 최대 CC 23, 최대 중첩 7, 경고 54건.
- **중복률이 100% 를 넘을 수 있었다 (`dup`)**: 분모 `total_code_lines` 는 빈 줄·주석·import 를 제외한 코드 라인만 세는데, 분자는 클론 구간의 **모든 물리 라인**을 셌습니다. 단위가 달라서 실측에서 **145%** 까지 나왔습니다. 비율이 아닌 값에는 임계값을 걸 수 없습니다.
  - 이제 분모가 센 라인만 분자에 들어갑니다.
- **`dup` 의 `warn_pct` 가 도달 불가능한 설정이었다**: 판정이 `dup_pct > warn_pct or len(clone_groups) > 0` 이라, 중복률과 무관하게 **클론이 하나라도 있으면 WARN** 이었습니다. 5% 정책을 세운 프로젝트가 1.8% 에서 경고를 받았습니다.
  - 이제 `warn_pct` 가 판정합니다. 정책 아래인 클론 그룹은 위치·크기·스니펫을 그대로 담아 리포트에 남되 조치 대상으로 세지 않습니다 — 엔진이 PASS 인데 자기 타깃은 WARN 이라고 말하는 모순을 피합니다.
  - 실측 영향: `viewer` 4.9%→PASS(4.7%), `loglens` 1.8%→PASS(1.7%), `diskmap` 변화 없음(0.0%), **`ici` 는 여전히 WARN**(17.4%→15.2%, 한도 5%).

**기본 임계값은 하나도 바꾸지 않았습니다.** `warn_cc = 15`, `warn_nesting = 4`, `warn_pct = 5.0` 그대로입니다. 고친 것은 그 숫자들이 실제로 무엇을 재는지입니다.

### Changed
- **main 브랜치 게시도 두 리포트를 함께 올립니다**: `publish-main` 은 루트 리포트만 게시하고 있어 PR 댓글에서 고친 것과 같은 비대칭이 main 에 남아 있었습니다. 이제 verify 잡의 아티팩트를 받아 `--report-dir ici=. --report-dir viewer` 로 게시합니다.
  - verify 를 재실행하지 않고 아티팩트를 소비합니다. push 는 main 이라 그 산출물이 이미 신뢰된 코드에서 나왔고, 재실행하면 이 실행이 이미 가진 결과를 위해 C++ 게이트(Qt·gcov·새니타이저)를 한 번 더 돌리게 됩니다.
  - main 의 게시 경로는 프로젝트 접두사가 없는 `main/index.html` 이라, 루트와 뷰어를 따로 게시하면 **서로 덮어씁니다.** `--report-dir` 가 필요한 이유입니다.
  - `test_purity.py` 의 게시 검증을 CLI 철자 하나(`--publish`)가 아니라 **게시 행위**를 보도록 일반화했습니다. 권한·토큰 검증은 그대로이고, "게시를 아예 없앤" 변이는 **이전에 통과하던 것이 이제 잡힙니다**(`count == count` 가 0 == 0 으로 성립했었습니다).

### Added
- **C++ 탐지 픽스처와 E2E 테스트 (`examples/cpp-fixtures/`, `tests/test_cpp_e2e.py`)**: 의도적으로 망가뜨린 소형 C++ 프로젝트 7종을 각각 잡아내야 할 엔진에 통과시킵니다. 단위 테스트는 파싱을 덮지만 실제 프로젝트가 지나는 경로는 덮지 않았습니다.
  - `cycle_pair`(헤더 순환) · `complexity_hot`(CC 17) · `clone_pair`(Type-2 클론) · `dtor_throw`(소멸자 throw) · `oversized_file`(560줄) · `asan_overflow`(힙 오버플로, ASan 이 실제로 잡음) · `clean_baseline`(**거짓 양성 방지** — 5개 엔진 모두 조용해야 함)
  - `tests/` 가 아니라 `examples/` 아래에 둡니다. ici 의 test 엔진이 저장소 루트에서 `tests/**/*.cpp` 를 글롭하므로, 거기 두면 자체 검증 때 이 픽스처들을 **ici 자신의 C++ 테스트로 컴파일·실행**하려 듭니다.

### Fixed
- **`cycle` 만 `project.source_dirs` 를 무시하고 있었다**: 다른 C++ 엔진(`lint`/`dup`/`complexity`/`exception`)은 모두 `get_all_cpp_sources()` 를 거치는데 `cycle` 은 저장소 전체를 `os.walk` 했습니다. 소스 디렉터리 밖에 의도적인 C++ 가 놓이기 전까지는 드러나지 않던 불일치였는데, `examples/` 의 순환 픽스처가 **이 프로젝트 자체 검증에서 진짜 결함으로 보고**되면서 표면화됐습니다 — 다른 엔진은 아무도 못 보는 파일을 이 엔진만 봤기 때문입니다.
  - 이제 소스 디렉터리와 최상위 `include/` 만 훑습니다. 헤더를 포함해야 하므로 `get_all_cpp_sources()`(구현 파일만 반환)를 그대로 쓸 수는 없고, `get_all_cpp_includes()` 가 공개 헤더를 찾는 위치와 같은 곳을 봅니다.

## [0.5.3] - 2026-08-27

### Changed
- **ici 자신의 PR 댓글이 두 리포트를 함께 보여줍니다**: 이 저장소는 루트의 Python 패키지와 `viewer/` 의 C++ 프로젝트를 둘 다 검증하는데, 댓글에는 앞의 것만 나와 게이트의 절반이 보이지 않았습니다. `report-pr` 잡을 `--report-dir ici=. --report-dir viewer` 로 전환했습니다.
  - `publish-main` 은 그대로입니다. 그 잡은 main 에서 verify 를 재실행해 `--publish` 로 인라인 게시하므로, 뷰어까지 담으려면 C++ 게이트 재실행이나 아티팩트 소비 방식으로의 전환이 필요합니다. 후자가 더 싸지만 `test_purity.py` 가 고정한 토큰 격리 의도를 손대게 되어 따로 다룹니다.

### Added
- **`ici publish --report-dir` — 모노repo의 여러 프로젝트 리포트를 하나의 sticky 댓글로**: 서브프로젝트 디렉터리를 반복 지정하면 각 `<dir>/verify_report.{html,json}` 을 **디렉터리 이름으로 네임스페이스된 gh-pages 경로**에 게시하고, 프로젝트별 행과 링크를 담은 **댓글 하나**를 남깁니다.
  - 그 전에는 모노repo 지원이 두 지점에서 막혀 있었습니다. (1) self 모드의 게시 경로에 프로젝트 접두사가 없어(`prefix = ""`) 모든 프로젝트가 같은 `pr/<N>/index.html` 에 써서 마지막 것만 남았고, (2) sticky 마커가 `<!-- ici-report -->` 하나로 고정이라 두 번째 publish 가 첫 번째 댓글을 덮어썼습니다.
  - 업로드는 의도적으로 순차 실행합니다. Contents API 는 덮어쓰기에 현재 blob sha 가 필요해서, 병렬로 같은 브랜치에 쓰면 경쟁하다 하나가 유실됩니다.
  - 단일 프로젝트 동작은 그대로입니다 — 라벨이 없으면 경로도 댓글도 이전과 동일합니다.
  - `label=path` 형식도 받습니다. 저장소 루트는 디렉터리 이름이 `.` 이라 경로 조각으로 쓸 수 없으므로 `--report-dir ici=.` 처럼 이름을 명시합니다.
  - 이 저장소의 워크플로 자체를 바꾸는 것은 후속 작업입니다. `report-pr` 잡은 **base(main) 소스를 체크아웃해 pyz 를 빌드**하므로 — PR 코드가 쓰기 토큰에 닿지 않게 하려는 의도된 설계입니다 — 새 플래그를 추가하는 PR 은 자기 CI 에서 그 플래그를 쓸 수 없습니다. 머지되어 main 에 들어간 뒤에야 가능합니다.

## [0.5.2] - 2026-08-27

### Added
- **`project.cpp_pkg_config` — C++ 컴파일 플래그를 설정으로 주입**: 나열한 pkg-config 패키지의 `--cflags` 가 C++ 컴파일 플래그에 추가됩니다. 그 전에는 `get_all_cpp_includes()` 가 `include/` 와 `<source_dir>/include` 만 보았기 때문에, Qt 같은 툴킷을 쓰는 소스는 **파싱조차 되지 않아** 검증 대상에서 통째로 빠질 수밖에 없었습니다. 경로를 설정 파일에 박으면 다른 머신에서 깨지므로, 프로젝트는 패키지 이름만 선언하고 경로는 호스트가 제공합니다.
- **`project.cpp_external_build_dirs` — 분석은 하되 컴파일은 하지 않는 디렉터리**: `Q_OBJECT` 클래스는 moc 가 생성한 소스가 있어야 링크되고, CMake 로 구동되는 코드는 생성 헤더가 필요합니다. 맨 `g++` 호출로는 만들 수 없는 그런 소스를 여기에 선언하면, 바이너리를 만드는 엔진(`test`/`sanitize`/`build`)만 건너뛰고 **텍스트·AST 기반 엔진은 그대로 읽습니다.**

### Fixed
- **`lint` 가 설정을 무시하고 있었다**: `get_all_cpp_includes(self.project_root)` 를 config 인자 없이 호출해, 설정된 include 경로가 컴파일러에 전달되지 않았습니다.

### Changed
- **`viewer/gui/` → `viewer/src/gui/`**: GUI 도 프로젝트 소스이므로 `src/` 아래로 옮겼습니다. `src/` 옆에 따로 두는 배치는 관례도 아니고, 무엇보다 **"검증할 필요 없는 코드"라는 잘못된 신호**를 줍니다. 위 두 설정으로 이제 GUI 가 `lint`·`line`·`complexity`·`dup`·`exception`·`cycle`·`security` 의 검증을 받습니다 — 검증 엔진 **0개에서 8개로**. 실측(`viewer/`): 코드 라인 1,020 → 1,378, 측정 함수 96 → 118개, GUI 소스 3개에 대해 Qt 플래그를 포함한 g++ 진단이 실제로 실행됩니다.
  - GUI 진입점은 `src/main.cpp` 와 `int main` 이 겹치지 않도록 `gui_main.cpp` 로 이름을 바꿨습니다.
  - CI 의 verify job 에 `qt6-base-dev` 설치를 추가했습니다. GUI 를 빌드하기 위해서가 아니라 **파싱하기 위해서**입니다.

### Added
- **`viewer/gui/` — 리포트 뷰어의 Qt6 셸**: `icirv-gui [report.json]` 으로 실행합니다. 화면에서 가장 큰 글씨가 `gateReason()` 결과입니다 — 콘솔이 `Error: 0` 을 출력하면서 스위트는 `ERROR` 인 상황(요약 카운트는 엔진 상태를 세고, 스위트 상태는 `aggregate_suite_status` 의 별도 규칙으로 정해지는데 그 규칙이 출력에 없음)을 문장으로 설명하는 것이 이 앱의 존재 이유이기 때문입니다.
  - 엔진 → 타깃 2단계 트리, "Issues only" 토글(정상 실행에서는 타깃 대부분이 PASS라 기본값), 엔진 행 툴팁에 `evidence` 와 `required` 표시(MEASURED 와 ESTIMATED 는 결과를 얼마나 믿을 수 있는지에 대해 전혀 다른 의미입니다), 타깃 더블클릭 시 파일 열기.
  - 스키마 불일치나 손상된 리포트는 조용히 빈 창을 띄우지 않고 사유를 표시합니다.
  - 코어는 Qt 무의존을 유지하므로 CI 의 C++ 게이트에는 영향이 없습니다. GUI 는 코어를 평범한 정적 라이브러리로 링크합니다.
- **CI 에 `viewer-gui` 잡 추가**: Qt6 를 설치해 빌드하고, `QT_QPA_PLATFORM=offscreen` 으로 실제 리포트를 열어 헤드리스 실행합니다. `gui/` 는 모든 엔진의 스코프 밖이라 이 잡이 없으면 **깨진 GUI 빌드를 아무것도 잡지 못합니다.**

### Fixed
- **C++ 함수 경계 탐지가 세 가지 방식으로 어긋나 있었다 (`complexity`)**: 기존 구현은 `(`, `)`, `{` 와 몇 개의 반환 타입 키워드(`int `/`void `/`bool `/`auto `/`double `)가 한 줄에 같이 있으면 함수 정의로 간주했습니다. 그 결과:
  - **한 줄 정의가 닫히지 않았습니다.** `void Stats::add(const T& r) { v_.push_back(r); }` 같은 정의는 시그니처 줄의 중괄호를 세지 않고 넘어가므로 영영 닫히지 않고 **뒤따르는 함수들의 본문을 흡수**했습니다. 실측: 한 줄짜리 `spanPrecedes()` 에 중첩 깊이 4 가 붙었는데, 실제로는 그 아래 함수의 값이었습니다.
  - **`for (int i = 0; i < n; ++i) {` 가 함수로 잡혔습니다.** 괄호·중괄호·`int ` 가 모두 있기 때문입니다. `for` 라는 이름의 유령 함수가 생기고 진짜 함수는 그 줄에서 잘렸습니다.
  - **여러 줄에 걸친 시그니처는 아예 탐지되지 않았습니다.** 본문이 앞 함수에 귀속됐습니다.
  - 이제 중괄호 깊이를 추적하고 시그니처를 여는 중괄호까지 누적해 판정하며, `(` 앞 토큰이 제어 키워드면 함수가 아닌 것으로 처리합니다. 문자열·주석은 `mask_cpp_literals` 로 중립화해 리터럴 안의 `if (` 나 `&&` 가 분기로 세어지지 않습니다.
  - **영향**: 탐지되는 함수 수가 실측 프로젝트에서 크게 늘었습니다 — `loglens` 44 → 93, `viewer` 72 → 96, `diskmap` 33 → 51. 절반 가까운 함수가 측정 대상에서 빠져 있었다는 뜻입니다. 거짓 경고가 사라진 대신 가려져 있던 진짜 중첩 위반이 드러납니다.
  - C++ 경로에는 테스트가 하나도 없었습니다. 세 결함 각각에 대한 회귀 테스트와 리터럴·중첩 측정 테스트를 추가했습니다.

### Refactored
- **`mask_cpp_literals` 를 `engines/cpp_text.py` 로 분리**: `build` 와 `exception` 이 각자 구현을 갖고 있었고 `complexity` 가 세 번째를 추가할 참이었습니다. `build` 쪽 구현(raw string·블록 주석까지 처리)을 공용 모듈로 옮기고 `build` 와 `complexity` 가 함께 씁니다. `exception` 의 구현은 line-splice 처리가 달라 이번에는 건드리지 않았습니다.

### Changed
- **`viewer/` 를 관례적인 C++ 레이아웃으로 재배치**: 공개 헤더를 `viewer/include/icirv/`, 구현을 `viewer/src/` 로 분리했습니다. ici 의 `get_all_cpp_includes()` 가 `include/` 와 그 하위 디렉터리를 `-I` 로 넘겨주므로, 테스트가 쓰던 `#include "../src/core/json_parser.hpp"` 같은 상대 경로가 `#include "icirv/json_parser.hpp"` 로 정리됐습니다. 검증 결과는 동일합니다(exit 0, TEM 4.94).
- **CI 가 Python 과 C++ 검증 리포트를 모두 제공**: `viewer/` 게이트 스텝에 `--html` 과 `--github-summary` 를 추가했습니다. `--github-summary` 는 `$GITHUB_STEP_SUMMARY` 에 append 하므로 Actions 실행 요약에 두 결과가 나란히 남고, 아티팩트에도 `viewer/verify_report.{html,json}` 이 함께 담깁니다. 그 전에는 Python 자체 검증 리포트만 업로드돼 C++ 검증 결과를 볼 방법이 없었습니다.
  - 두 리포트를 **하나로 합치는** 것은 아직 불가능합니다. `source_dirs` 에 `viewer/src` 를 넣으면 C++ 소스는 잡히지만 (1) `engines/test.py` 가 C++ 테스트를 `<root>/tests` 에서만 찾아 `viewer/tests` 를 보지 못하고, (2) `get_all_cpp_includes()` 가 `<source_dir>/include` 만 보므로 `viewer/include` 를 `-I` 에 넣지 못합니다. 두 제약을 걷어내는 것은 별도 작업입니다.

### Added
- **`viewer/` — ici 리포트 네이티브 뷰어의 C++17 코어와 CLI(`icirv`)**: `ici verify --report` 가 만드는 `ici.result/v2` JSON 을 읽어 게이트 사유·엔진 표·조치 필요 항목을 출력합니다. 손으로 작성한 재귀 하강 JSON 파서(`json_value`/`json_parser`), 스키마 검증 매퍼(`report_model`), 파생 뷰(`summary`) 로 구성되며 외부 의존성이 없습니다.
  - `gateReason()` 이 이 뷰어의 존재 이유입니다. ici 콘솔은 `Error: 0` 을 출력하면서 `suite_status` 는 `ERROR` 일 수 있는데, 요약 카운트는 엔진 상태를 세는 반면 스위트 상태는 `aggregate_suite_status` 의 별도 규칙(required 엔진의 SKIP / evidence NOT_RUN 이 스위트를 승격)으로 정해지고 그 규칙이 출력 어디에도 없기 때문입니다. `gateReason()` 은 그 규칙을 재현해 `"ERROR — required engine 'dead' was SKIPPED"` 처럼 사유를 문장으로 돌려줍니다.
  - 스키마 불일치·필수 필드 누락·타입 불일치는 조용히 기본값으로 넘어가지 않고 `LoadError` 로 명시됩니다.
- **CI 에 C++ 게이트 추가**: `viewer/` 를 대상으로 `ici verify` 를 실행하는 스텝을 넣었습니다. 그 전까지 ici 의 C++ 경로(`lint` 의 g++ 진단, `test` 의 gcov 커버리지, `sanitize` 의 ASan/UBSan, `cycle` 의 include 순환)는 **단위 테스트로만 덮여 있었고 실제 C++ 프로젝트로 검증된 적이 없었습니다.** 코어가 Qt 무의존이라 CI 에 Qt 설치가 필요 없습니다.
  - `viewer/` 는 기본 source_dir 이 아니고 ici 의 `tests/` 아래도 아니며 루트에 빌드 디스크립터를 추가하지도 않으므로, ici 자체 검증 결과는 변하지 않습니다(확인: TEM 4.72 유지).
  - 측정: 12 엔진 중 9 PASS, exit 0, TEM 4.94 / line 95.0% / branch 85.2% / function 98.9%.

### Fixed
- **CI 에서 `lint` 엔진이 한 번도 실제로 실행되지 않고 있었다**: 워크플로의 린트 단계는 `uvx ruff check .` 를 쓰는데, `uvx` 는 ruff 를 임시로 내려받아 실행할 뿐 `PATH` 에 남기지 않습니다. ruff 는 dev 의존성에도 없어 `.venv` 에도 설치되지 않았습니다. 그 결과 뒤이은 도그푸딩 단계에서 `_find_ruff_command()` 가 ruff 를 찾지 못해 `lint` 가 AST 문법 폴백으로 강등됐고, **검사 대상을 하나도 보고하지 않은 채**(`targets: []`) `evidence = ESTIMATED` / `WARN` 으로 게이트를 통과했습니다. 개발자 로컬에는 ruff 가 전역 설치돼 있어 이 차이가 드러나지 않았습니다.
  - `ruff>=0.16,<0.17` 을 dev 의존성으로 선언해 `.venv` 에 설치되도록 하고(엔진의 `find_project_executable` 경로가 이를 찾습니다), CI 린트 단계를 `uv run` 으로 바꿔 엔진과 CI 가 같은 바이너리를 쓰도록 통일했습니다. format 규칙이 마이너 버전에서 바뀌면 `--check` 가 갑자기 깨지므로 상한을 둡니다.
  - 저장소 정책 `ici.toml` 의 `engines.lint.ruff_required` 를 `true` 로 올렸습니다. 이제 ruff 를 찾지 못하면 `lint` 가 `ERROR`(evidence `NOT_RUN`)가 되고 스위트가 `ERROR`, `verify` 는 exit 1 로 끝납니다. 배포 기본값(`config.py` DEFAULT_CONFIG)은 기존대로 `false` 이므로 사용자 프로젝트의 동작은 바뀌지 않습니다.
  - 확인: 수정 후 자체 검증에서 `lint` 가 `evidence = MEASURED` 로 보고됩니다(이전 CI: `ESTIMATED`).

## [0.5.1] - 2026-08-26

### Fixed
- **C++ 브랜치 커버리지 대폭 과소 집계 (`test` / `coverage_support.parse_gcov_dir`)**: gcc 는 예외를 던질 수 있는 거의 모든 호출(예외 활성 상태에서는 사실상 모든 STL 할당) 주위에 `(throw)` 로 표시된 분기 arm 을 추가로 방출합니다. 이는 사람이 작성한 분기가 아니라 예외 unwind 엣지이며, `bad_alloc` 등을 인위적으로 일으키지 않는 한 어떤 테스트로도 탈 수 없습니다. 기존 파서는 이 arm 들을 `taken 0%` 로 보고 미커버로 집계해 C++ 브랜치 커버리지를 실제보다 약 20%p 낮게 보고했습니다.
  - 실측(외부 C++ 프로젝트 `diskmap`, 5개 테스트 바이너리 / 총 338 분기): **67.8% → 88.4%**. 특히 `treemap.cpp` 는 `never executed` 분기가 **0개**, 즉 모든 분기점에 도달했는데도 73.1% 로 보고되고 있었습니다.
  - 이제 `(throw)` arm 은 분자·분모 양쪽에서 제외됩니다. `taken at least once` 라는 기존 판정 기준은 그대로 유지하므로 신호는 보존됩니다 — 같은 프로젝트에서 에러 경로가 실제로 덜 검증된 `fs_source.cpp` 는 65.8% 로 남습니다. lcov 2.x 가 동일한 엣지를 필터링하는 것과 같은 접근입니다.
  - 영향: 이 버그로 인해 C++ 프로젝트가 기본 임계값(`min_branch_cov = 80`)을 넘기지 못해 사용자가 임계값을 낮추도록 유도되고 있었습니다. 회귀 테스트 `test_parse_gcov_dir_excludes_exception_unwind_arms` 추가.

## [0.5.0] - 2026-08-24

### Added
- **리소스 누수 (`resource`)**: `open()` 후 close 누락, 가변 기본 인자 등 리소스 누수 AST 패턴을 탐지.
- **보안 위생 (`security`)**: 하드코딩 시크릿, 프라이빗 키, `hashlib.md5/sha1`, `random`, `eval/exec`, `pickle`, `shell=True` 등을 Python AST 규칙으로 탐지. `scan_tests` 설정 지원.
- **인지 복잡도 (`cognitive`)**: SonarQube S3776 스타일 인지 복잡도를 함수별로 계산. 중첩 깊이에 따라 가중치를 더함. `warn/fail/warn_nesting` 설정 지원. 자체 검증 baseline 대비 오탐을 줄이기 위해 **기본 비활성(`enabled = false`)**, 임계값은 warn 30 / fail 60으로 조정.
- **순환 참조 탐지 (`cycle`)**: Python `import` 그래프와 C++ `#include` 그래프를 Tarjan SCC로 분석해 순환을 탐지. `max_reported` 설정 지원.
- **PR sticky 리포트 댓글 복원 (`report-pr` + `ici publish`)**: v0.4.0 권한 분리 이후 중단됐던 PR 리포트 댓글을 아티팩트 기반으로 재도입. 검증 job은 계속 읽기 전용이고, 새 `report-pr` job(`pull_request` 전용, `contents:write`+`pull-requests:write`)이 업로드된 `verify_report.html/json`을 받아 gh-pages에 게시하고 `<!-- ici-report -->` 마커로 sticky 댓글을 갱신합니다. 댓글은 배지형 링크·통계 표·접을 수 있는 엔진 상세로 리디자인됐습니다. 신규 CLI `ici publish --html --json`으로 기존 리포트를 단독 게시할 수 있습니다.

### Changed
- **HTML 리포트 UI/UX 개선 (8탭 재구성)**:
  - 탭 구조를 성격별로 재편: `📋 Summary · 📏 Line · 🧪 Tests · 🧩 Complexity(+🧠 cognitive 통합) · 📦 Clones · 🔁 Cycles · 🔐 Security & Resources · ⚠️ Issues`.
  - **Line 듀얼 모드**: 엔진은 프로젝트 전체를 스캔하되 기본 표시·게이트는 소스 스코프만. Line 탭의 "All files" 토글로 전체 프로젝트 파일 트리·차트·Top5 조회.
  - **Cycles 독립 탭**: 순환 체인을 칩(chip)+화살표 유연 레이아웃으로 시각화, 전체 경로는 접기로 제공.
  - **Tests & Coverage 압축**: 커버리지 테이블을 디렉터리별 접기 그룹(문제 폴더만 자동 펼침), 함수 커버리지 테이블 접기, 테스트 스위트는 실패 케이스만 항상 표시하고 통과 케이스는 한 줄 요약+접기, "Toggle All Cases" 일괄 토글 지원.
  - "Engines Run" 카드 Pass/Warn/Fail/Error/Skip **헬스 바**, N/A(SKIP) 엔진 회색 접힘 행은 유지.
- **`line` 소스 전용 게이트 + 전체 스캔 병행**: 임계값 판정은 소스 디렉터리(`src/include/lib/app` + 설정 추가 경로)에서만 수행하고, `include_dirs`는 재정의가 아닌 **추가** 동작으로 변경. 전체 프로젝트 수치는 `extra.all`로 별도 집계.
- **Dogfood 품질 강화 1차 (자체 검증 기반)**:
  - CLI 엔진 커맨드 17종을 데이터 주도 레지스트리+팩토리로 통합해 `__main__.py`의 반복 보일러플레이트를 제거하고, 엔진 클래스는 호출 시점에 모듈 어트리뷰트로 조회해 기존 monkeypatch 호환을 유지 (dup 최대 클론 제거).
  - `type`: 동일 파일·동일 문구의 Mypy note를 첫 위치 1건으로 병합(`metrics.repeats`)해 리포트 노이즈 축소.

### Refactored
- **HTML 리포터 모듈화**: 1070줄 단일 파일 `src/ici/reporters/html.py`를 `html/report.py` + `html/sections/{summary,line,test,complexity,dup,issues,static_analysis}.py` + `html/utils.py` + `html/assets/{style.css,app.js}` + `html/assets_loader.py` 구조로 분해하고, `html_assets.py`는 하위 호환 shim으로 유지. Zero-CDN 인라인 동작은 `importlib.resources` 기반 로더로 보존하며, 신규 엔진 탭 추가 시 섹션 모듈만 추가하면 되도록 확장성을 확보했습니다. (`_get_status_theme` 등 레거시 헬퍼는 `html/__init__.py`에서 re-export)
- **Runner/Path 모듈화**: `src/ici/core/runner.py`(640줄)에서 Windows Job Object 관련 상수·구조체·저수준 헬퍼를 `runner_win.py`(147줄)로 분리하고, 공통 경계 검증 `resolve_project_path` 중복을 `core/path_utils.py`로 통합. `config_schema.py`와 `core/project.py`는 해당 모듈을 re-export하여 기존 import 경로를 유지합니다. POSIX/Windows 분리에 따른 순환 참조 없이 `run_process`의 timeout·출력 제한·프로세스 그룹 정리 동작을 보존했습니다.
- **Test 엔진 인터프리터 분리**: 1000줄 `src/ici/engines/test.py`에서 인터프리터 해석(`_resolve_python`, `_find_pytest_cmd`, `_build_python_test_env`, `_find_coverage_cmd`, `_interpreter_from_command`)을 `test_interpreter.py`의 `TestInterpreterMixin`으로 분리하고 `TestEngine`이 다중 상속하도록 변경. `run_process` 패치 호환성(`ici.engines.test.run_process`)을 유지하며 `test_test_engine.py` 55개 테스트가 통과하도록 검증했습니다.

### Fixed
- **`cycle` 경로 표기**: 순환 참조 대상 파일 경로가 러너 절대경로로 노출되던 문제를 수정하고 다른 엔진과 동일하게 프로젝트 루트 상대경로로 보고.
- **프로젝트 정책 버전 싱크**: `ici.toml`의 `ici.version`을 패키지 `__version__`(`0.4.2`)과 동기화하고, 드리프트를 방지하는 `test_repository_ici_version_matches_package_version` 회귀 테스트를 추가했습니다.
- **엔진 경량 보강**: `dup` Type-2 해시 충돌 방지를 위해 윈도우 해시를 `"\x00"` 구분자로 생성, `type`의 `__private` 함수 오탐 방지를 위해 `__` 시작·끝 dunder만 스킵, `line`의 symlink 파일이 라인 집계에서 제외되도록 `is_symlink()` 가드 추가.
- **문서·환경·리포터 정합성**: `README.md` TEM 공식을 LineCov 기반(`min(Line,80)/80*Func/100*PassRate*5`, Branch는 `*5/4` 보정)으로 정정하고 HTML 대시보드가 신규 엔진도 요약/Issues에 자동 집계됨을 명시. `config.py` 전역 설정 생성 로그를 `stderr`로 이동해 `--json` 출력을 방해하지 않도록 수정하고, `scripts/smoke.sh`에 `dist/ici` 일치 및 Zero-CDN 검증 단계를 추가.
- **`security` 시크릿 마스킹**: `HardcodedSecret`/`PrivateKey` 발견 사항이 실제 시크릿 값을
  그대로 message/snippet에 담아, `--publish`로 gh-pages에 게시되는 HTML 리포트가 스캐너가
  찾아낸 시크릿을 그대로 노출하던 문제를 수정. 값은 `***REDACTED***`로 치환하며, 전체가
  주석인 줄은 스캔에서 제외해 오탐도 줄였습니다. 마스킹은 **줄 단위로 한 번** 수행한 뒤 그
  줄의 모든 발견 사항에 재사용하므로, 한 줄이 시크릿 패턴과 비(非)시크릿 패턴(`eval` 등)에
  동시에 걸려도 비시크릿 쪽 결과가 시크릿 원문을 흘리지 않습니다. `scan_tests=true`가 아무
  효과도 없던 문제도 함께 수정 — 프로젝트 최상위 `tests/`를 실제로 스캔하도록 별도 경로를
  추가했습니다.
- **`cycle` 재귀 한도 초과로 인한 스위트 전체 크래시**: 재귀 Tarjan SCC 구현이 큰(수백~
  수천 노드) import/include 체인에서 `RecursionError`로 죽어 `verify` 전체가 `ERROR`로
  종료되던 문제를 반복(iterative) Tarjan으로 교체해 해결. 다른 신규 휴리스틱 엔진과 달리
  `cycle`만 `required=true`가 기본값이어서 이 크래시가 전체 게이트를 막았던 점도 함께
  `required=false`로 정정. 부수적으로 리포트에 표시되는 순환 체인을 SCC 멤버의 임의 정렬
  목록이 아닌 실제 간선을 따라간 경로로 교체하고, 표준 라이브러리 모듈명과 겹치는 프로젝트
  모듈(`import html` vs 자체 `ici.reporters.html`)을 오탐하던 suffix 매칭과, 같은 파일명이
  여러 디렉터리에 존재하는 C++ 헤더를 임의로 하나 골라 잘못된 순환을 만들던 문제도 수정.
- **`line`이 `project.source_dirs`를 무시**: 소스 스코프가 `src/include/lib/app`으로 고정돼
  있어 `project.source_dirs`로 다른 레이아웃을 지정한 프로젝트는 파일 0개로 스캔되고
  500/1000줄 게이트가 조용히 무력화되던 문제를 수정. 이제 기본 스코프·게이트 모두
  `project.source_dirs`를 포함하며, `gate_dirs`를 명시적으로 좁힌 설정은 그대로 존중합니다.
- **`ici publish` 실패가 항상 종료 코드 0**: `PublishResult`에 `success` 필드를 추가해
  업로드 실패와 의도된 스킵(예: `GITHUB_ACTIONS` 밖 로컬 실행)을 구분하고, `ici publish`는
  실패 시 0이 아닌 종료 코드를 반환하도록 수정. `report-pr` job의 유일한 역할이 게시이므로
  실패가 조용히 사라지지 않아야 합니다. 기존 댓글 검색이 첫 30개만 확인해 그보다 긴 PR에서
  마커를 못 찾고 매번 중복 댓글을 남기던 문제도 페이지네이션(`per_page=100`, 최대 2000개)
  으로 수정.
- **`report-pr`이 PR 코드를 신뢰된 권한으로 실행**: `contents:write`+`pull-requests:write`를
  가진 `report-pr` job이 PR head/merge ref를 체크아웃해 `dist/ici.pyz`를 빌드하고 있어,
  문서가 명시한 "PR 코드를 이 job에서 다시 실행하지 않는다"는 불변식과 실제 워크플로가
  어긋나 있던 문제를 수정. 이제 PR의 base commit만 체크아웃합니다. `pages: read` 권한 누락
  으로 `_check_pages` 조회가 항상 실패해 Pages가 켜져 있어도 뷰어 링크 배지가 절대 뜨지
  않던 문제도 `report-pr`/`publish-main` 양쪽에 함께 수정.
- **`required_tools` 설정이 항상 config 오류**: `[engines.toolchain] required_tools`가
  #40에서 `toolchain` 엔진과 함께 제거됐지만 `doctor.py`는 여전히 그 경로를 읽고 있어,
  이 설정을 쓰면 "engines.toolchain is an unknown configuration key"로 항상 실패하던
  문제를 수정. `doctor`는 검증 게이트가 아닌 진단 전용 커맨드이므로 `engines` 바깥의
  전용 `[doctor] required_tools` 테이블로 복원했습니다.
- **`type` note 반복 횟수가 화면에 보이지 않음**: 동일 위치·문구의 Mypy note를 병합할 때
  `metrics.repeats`만 갱신되고 콘솔/HTML/Markdown이 실제로 출력하는 `message`에는 반영되지
  않아, N건이 조용히 1건처럼 보이던 문제를 수정. 이제 message에 `(xN)` 접미사가 붙습니다.
- **`cognitive` 기본 임계값 불일치**: 엔진 자체 fallback과 config 검증 fallback이
  `warn=15/fail=25`였던 반면 실제 배포 정책(`DEFAULT_CONFIG`)은 `warn=30/fail=60`이라,
  독립·부분 설정으로 엔진을 돌리면 실제 정책과 다른 기준이 적용되던 문제를 정정해
  세 곳 모두 `warn=30/fail=60`으로 통일.
- **HTML N/A(SKIP) 행의 잘못된 CSS**: `var(--text-muted)44`처럼 `var()` 참조에 직접 알파
  값을 붙이는 문법 오류로 SKIP 배지 테두리가 렌더링되지 않던 문제를 수정.

### Removed
- **CI 부적합 엔진 7종 일괄 제거**: `cmake_lint`, `pyproject_lint`, `file_hygiene`, `python_compat`, `build_definition`, `compile_db`, `static_hygiene` 및 `build_adapters`/`core/compile_db` 공유 인프라를 `verify` 스위트에서 제거. `file_hygiene`의 `bash -n` 셸 검사는 폐쇄망 `csh` 미지원으로 함께 폐기.
- **toolchain `doctor`로 흡수**: `verify` 엔진 `toolchain`을 제거하고 `src/ici/core/toolchain.py:41` `collect_tool_capability`를 `src/ici/doctor.py:25` `collect_diagnostics`가 재사용하도록 통합. `[doctor] required_tools` 위반 시 `doctor` 테이블에 `[yellow]Missing (required) WARN[/yellow]`로 표시.

## [0.4.2] - 2026-08-20

### Fixed
- **Ruff 0.15.17 mixed formatter summaries**: legacy `ruff format --check`
  output containing both `Would reformat:` paths and an `already formatted`
  suffix is parsed as a policy `WARN` with one location target per path.
- **Atomic Ruff formatter parsing**: malformed legacy output is rejected as
  `ERROR` without retaining partially parsed format targets.

## [0.4.1] - 2026-08-20

### Fixed
- **Ruff 0.15.17 formatter compatibility**: recognized Ruff `warning:` blocks on
  `check` and `format` stderr are retained as tool warnings instead of turning a
  valid lint/format result into `ERROR`.
- **Ruff formatter capability detection**: locally probes `ruff format --help` and
  uses the JSON formatter output when supported by Ruff 0.16+, while retaining the
  strict legacy `Would reformat:` grammar for older versions. Probe and validation
  failures remain `ERROR`/`NOT_RUN` with complete `ToolEvidence`.

## [0.4.0] - 2026-08-20

### Changed
- **CI 권한 분리 및 Action 공급망 고정**:
  - PR/main 검증 `verify` job은 `contents: read`만 사용하고 checkout의
    `persist-credentials`를 비활성화했으며, `GITHUB_TOKEN`·`--publish`·PR 댓글 쓰기를
    검증 경로에서 제거
  - `main` push에서 검증 성공 후에만 실행되는 `publish-main` job을 별도 구성하고
    `contents: write`를 해당 job에만 부여
  - checkout/setup-python/upload-artifact/setup-uv/release Action을 Node 24 릴리스의
    immutable 40자리 commit SHA로 고정
- **CI 및 사용자 문서 정합성 보강**:
  - 로컬·CI·폐쇄망이 같은 정책·결과 계약을 사용하되 OS/컴파일러/Python/도구 버전과
    실행 결과는 달라질 수 있음을 명시
  - Typer, 순차 엔진 실행과 예외 격리, PASS/WARN/FAIL/ERROR/SKIP, `ici.result/v2`,
    Rich `file://` 링크 및 6개 HTML 탭으로 아키텍처 설명을 현행화
  - PR은 Step Summary·annotation·JSON/HTML 아티팩트를 사용하고, trusted main 또는
    명시적 수동 실행에서만 `--publish`를 사용하도록 CI 가이드를 정정
  - 배포된 ZipApp은 오프라인 실행 가능하지만 빌드에는 사전 준비된 Python·wheel/cache
    또는 내부 미러가 필요하다는 폐쇄망 안내를 추가
- **제품 버전 및 릴리스 태그 안전성 보강**:
  - 제품 버전을 `ici.__version__`으로 단일화하고 CLI, `doctor --brief`, 기본 설정이 같은
    버전 값을 사용하도록 정리
  - 수동 릴리스의 `version_tag` 입력을 필수화하고 SemVer-like 형식과 패키지 버전의 정확한
    일치를 검증하여 브랜치 이름·이전 버전으로의 fallback을 제거
- **신규 CI 검증 기능 보류**:
  - Toolchain, CMake/qmake build adapter, compile DB, Python compatibility, ELF/ABI 및
    C++/Python 통합 엔진은 v0.4.0에 포함하지 않고 별도 미래 계획으로 남김
- **리포터·CLI 결과 계약과 출력 안전성 강화**:
  - suite 및 단독 엔진 JSON을 ici.result/v2로 통일하고 required/evidence/raw_output/extra/InspectionTarget의 snippet·metrics/전체 ToolEvidence를 보존하며, 기존 FAIL+ERROR 의미의 failed_count와 순수 error_count/skipped_count를 분리
  - HTML 위치 링크는 동적 JavaScript 인자 대신 escaped data-* 속성과 정적 delegated listener를 사용하고, ERROR/SKIP도 Issues 뷰와 상태 뱃지에 표시
  - Markdown 표·코드 fence·GitHub Actions annotation, Rich 콘솔 경로/요약을 문맥별 escaping 및 안전한 file URI로 보호
  - 모든 단독 엔진과 verify/build가 PASS/WARN=0, FAIL/ERROR=1, SKIP=2 종료 코드를 공유하고 ERROR를 성공 아이콘으로 출력하지 않음
- **build 엔진의 metadata·산출물 안전성 강화**:
  - top-level `[build.python].entrypoint`와 `pyproject.toml [project.scripts]`를 엄격히 검증하고, 모든 configured source directory의 non-symlink `.py` library·검증된 callable launcher·실제 C++ regular binary만 산출물로 인정
  - source tree를 변경하지 않고, destination/path symlink·충돌·복사 오류·unsafe metadata를 구조화된 `ERROR`/`NOT_RUN`으로 처리하며 산출물이 없으면 `FAIL`, 실제 산출물과 오류 없는 경우에만 env scripts 생성
  - CMake/qmake/Makefile descriptor에서는 generic g++를 호출하지 않고 adapter 필요 `ERROR`를 반환하며, descriptor 없는 C++는 정확히 하나의 `int main(...)`과 실제 regular binary를 확인
- **sanitize/dead/exception 엔진의 실행·분석 증거 강화**:
  - sanitize Python 검증은 Task 5와 동일한 대상 인터프리터의
    `-W error::ResourceWarning -m pytest -o addopts= tests`를 실행하고, 0개 테스트·pytest 부재·timeout·출력 절단·spawn/신호 종료·파싱 불가능한 성공을 `ERROR`/`NOT_RUN` 또는 명시적 선택 scope `SKIP`/`ESTIMATED`로 기록
  - C++ sanitizer 컴파일·실행 실패를 허위 `PASS`로 처리하지 않으며, 종료 코드와 무관한 ASan/UBSan 진단을 `FAIL`/`MEASURED`로 보존하고 실행 시 기존 `ASAN_OPTIONS`/`UBSAN_OPTIONS`에 leak/halt 정책을 추가
  - Python/C++ hybrid의 부분 scope는 `WARN`/`ESTIMATED`로 남기고, 적용 대상이 없는 프로젝트는 명시적 `SKIP`으로 표시
  - C++ sanitizer timeout·출력 절단은 `ERROR`/`NOT_RUN`, 완전한 ASan/UBSan 진단을 동반한 signal 종료는 `FAIL`/`MEASURED`, 진단 없는 signal 종료는 `ERROR`로 구분하며 테스트 외부 symlink를 제외하고 Windows drive/공백 ResourceWarning 경로와 라인을 보존
  - sanitizer는 `ERROR`/`SUMMARY` 또는 위치 있는 UBSan `runtime error` 서명만 실제 진단으로 인정하고, `test_*.py`/`*_test.py`를 모두 선택하며 전부 skipped/deselected인 pytest 실행은 측정 PASS로 승격하지 않음. configured C++ source/include와 기존 PYTHONPATH·WSL `/tmp` 환경을 실행에 전달
  - dead는 private module-level Python 함수 정의와 모듈 내·cross-module `from`/attribute 참조를 분리해 수집하며 package `__init__.py` 상대 import, source directory 우선순위, 동일 alias 복수 후보, 모든 statement-list의 unreachable 경로를 처리하고 decorator·`__all__`·메서드·중첩 callback 함수 오탐을 제외
  - exception은 명시적으로 import된 `builtins` alias만 인정하고 `del`, BoolOp/IfExp walrus, match capture, 복수 with context의 실행 순서와 transient handler binding을 보수적으로 처리하며, C++ 표준 raw prefix와 line-splice 주석을 마스킹. 기존 `BaseException`/traceback·destructor·구문상 비어 있는 catch 정책은 유지
  - 모든 엔진 설정 테이블이 공통 `required` boolean 정책을 사용하고, `sanitize`/`dead`/`exception` 단독 명령은 `ERROR`를 exit 1, `SKIP`을 exit 2로 반환
  - 선택 엔진(`required = false`)의 `FAIL`/`ERROR`/`SKIP` 및 `MEASURED`가 아닌 결과는 suite를 `WARN`으로 낮춰 허위 `PASS`를 방지하며, 필수 엔진의 `ERROR`/`FAIL` 우선순위는 유지
- **lint/type 실행 증거 및 도구 정책 강화**:
  - Ruff, Mypy, g++의 모든 실행 시도와 미설치 상태를 `ToolEvidence`에 기록하고 timeout·출력 절단·spawn/신호 종료·도구 크래시·잘못된 성공/진단 출력을 `ERROR`/`NOT_RUN`으로 분류
  - `[engines.lint].ruff_required`와 `[engines.type].mypy_required`를 추가해 필수 도구 누락은 오류로, 선택 도구 누락은 AST 부분 폴백 `WARN`/`ESTIMATED`로 표시
  - Mypy 종료 코드 `1`의 실제 타입 진단은 `mode` 정책을 따르고 `2` 이상은 진단 문자열이 있어도 도구 오류로 처리
  - Mypy 성공 출력은 마지막 단일 success summary 앞의 검증된 `note` 진단을 `WARN`으로 보존하며, 정크·오류 진단·잘못된 summary는 계속 `ERROR`로 처리
  - C++ lint는 발견된 각 소스의 g++ 문법 진단 위치를 안전하게 보존하며, type 엔진은 미구현 C++ 검증을 `SKIP`/`WARN`/`ESTIMATED`로 명시
  - Ruff/Mypy는 직접 실행 가능한 PATH 도구 또는 프로젝트 `.venv/bin`·`.venv/Scripts`만 사용하고 `uvx`/`uv run` 패키지 해석을 시도하지 않음
  - Ruff format의 빈 성공 출력, 위치 있는 C++ `note:` 보조 진단, Python 0-source Mypy skip을 명시적으로 처리하며, C++ skip을 Missing Annotations로 오표기하지 않음
  - rc>=2·파싱 실패를 포함한 최종 도구 오류 원인을 각 `ToolEvidence.error`에 보존
  - `type = "cpp"`의 빈 C/C++ 적용 범위도 명시적 `SKIP`/`WARN`/`ESTIMATED`로 표시하고, Python-only hybrid에는 불필요한 C++ skip을 추가하지 않음
  - 실제 g++ template context(`In instantiation of ...`, 위치 있는 `required from here`)만 제한적으로 허용하고 알 수 없는 문맥은 계속 도구 오류로 처리
- **테스트 실행·커버리지 증거 강화**:
  - 설정된 Python → 프로젝트 `.venv` → `sys.executable` 순으로 단일 인터프리터를 선택하고
    pytest/coverage/unittest를 모두 `-m` 모듈 호출로 실행
  - pytest 5 종료 코드와 0개 수집을 `FAIL`로 기록하고, 실행기·timeout·도구 오류는
    `ERROR`/`NOT_RUN`으로 분리
  - `coverage_required` 정책에서 Python coverage JSON 또는 C++ gcov 실측이 없거나 잘못되면
    통과를 금지하며, 선택적 커버리지는 `ESTIMATED`/`WARN`으로만 표시
  - 반복 실행 사이에 커버리지·도구 증거를 초기화해 이전 측정값이 재사용되지 않도록 보장
  - `python`/`cpp`/`hybrid` 소스별 테스트 시도를 기록하고 hybrid의 언어별 0개 테스트를 `FAIL`로
    표시하며, pytest 모듈 부재일 때만 동일 인터프리터의 unittest fallback을 허용
  - coverage JSON의 수량·라인 배열 일관성을 검증하고, 0 statement·probe/컴파일/실행 signal 오류와
    프로젝트 내부 pytest 임시 디렉토리 강제를 허위 측정·통과로 처리하지 않음
  - 소스·테스트가 모두 없는 빈 프로젝트도 generic zero-test `FAIL`과 누락 커버리지 증거로 기록하고,
    pytest가 collection만 보고 성공한 경우(per-test/terminal 결과 증거 없음) `ERROR`/`NOT_RUN`으로 분류
- **Dogfood 품질 게이트 유지보수성 개선**:
  - 프로세스 실행 및 lint/test/type 검증 흐름을 명시적·저복잡도 헬퍼로 분리해 CI 복잡도 임계값과 Mypy 타입 검사를 통과하도록 정리
- **서브프로세스 결과 신뢰성 강화**:
  - stdout/stderr를 동시 스트리밍하면서 설정된 상한 이후 데이터를 폐기해 대용량 출력이 메모리를 고갈시키지 않도록 개선
  - POSIX parent 종료 이후에도 descendant가 파이프를 보유하면 전체 monotonic deadline으로 process group을 종료하고 drain을 bounded cleanup
  - Windows는 `CREATE_SUSPENDED` 상태에서 stdlib ctypes Job Object를 먼저 할당한 뒤 primary thread를 resume하여 descendant race를 차단하고 핸들을 항상 닫음
  - Ruff, Mypy와 빌드/테스트/sanitize 엔진이 timeout·출력 절단·도구 출력 파싱 실패를 `ERROR`/`NOT_RUN`으로 기록
  - Mypy 성공 grammar와 coverage JSON의 필수 totals/files 구조를 엄격히 검증해 불완전한 도구 결과를 통과시키지 않음
- **서브프로세스 실행 제한 및 엔진 예외 격리**:
  - `run_process`가 구조화된 `ProcessResult`를 반환하고 기본 300초 timeout과 출력 상한(100만 문자)을 적용
  - POSIX 프로세스 그룹 종료와 Windows Job Object로 timeout 이후 자식 프로세스가 남지 않도록 정리
  - 개별 검증 엔진 예외를 `ERROR`/`NOT_RUN` 결과로 기록한 뒤 나머지 엔진 실행을 계속
- **증거 인식 결과 계약 및 검증 게이트 강화**:
  - `EngineResult`에 필수 엔진 여부(`required`), 증거 상태(`MEASURED`/`ESTIMATED`/`NOT_RUN`),
    도구 실행 증거(`ToolEvidence`)를 추가
  - 필수 검증 누락(`SKIP`/`NOT_RUN`)과 빈 검증 집합을 `ERROR`로 처리해 허위 `PASS` 방지
  - `pass_fail` 모드에서 경고(`WARN`)도 `FAIL`로 승격
- **Dogfood 테스트 게이트 baseline 보정**:
  - 실행기별 coverage 편차를 허용하되 strict `pass_fail` 의미는 유지하도록 프로젝트 정책 floor를
    TEM `2.0` / Branch `35%` / Function `60%`로 조정
  - 실제 테스트 실행 실패는 임계값과 무관하게 계속 `FAIL`로 처리
- **설정 계층 병합 및 스키마 검증 강화**:
  - 내장 기본값 → XDG 전역 → 프로젝트 `ici.toml`/`dev.toml` → `ICI_CONFIG` 순서로 모든 설정을
    결정적으로 깊게 병합
  - 알 수 없는 키, 잘못된 자료형·평가 모드·임계값, TOML 오류와 누락된 명시 파일을
    `ConfigError`로 보고하여 암묵적 기본값 폴백 방지
  - 과도하게 중첩된 배열·점 키로 발생하는 TOML 파서 재귀 오류도 `ConfigError`로 정규화하여
    CLI가 traceback 없이 종료 코드 `2`를 반환
  - 모든 CLI 엔진과 `verify` 오케스트레이터가 동일한 유효 설정을 사용
- **프로젝트 경계·메타데이터 파싱 강화**:
  - 소스 디렉토리와 재귀 파일 탐색에 canonical 경로 경계를 적용해 `..`·탈출 symlink를
    차단하고, 소스 내부 symlink 파일/디렉토리는 검사 대상에서 제외
  - `ici.toml` top-level 및 `pyproject.toml` `[project]` 메타데이터를 `tomli`로 파싱하고
    프로젝트 이름·버전의 경로 안전 문자를 검증

## [0.3.3] - 2026-08-18

### Added
- **Function Coverage 실측 (gcov 호출 기준)**:
  - 기존 하드코딩 추정치(95%/50%)를 실측으로 대체 — 함수 본문이 한 번 이상 실행되면 커버로 간주
  - Python: coverage.json `executed_lines` × AST 함수 라인 범위 교차 계산
  - C++: gcov 산출물의 `function ... called N` 라인 파싱
  - HTML `🧪 Tests & Coverage` 탭에 **Function Coverage Table** 추가 (함수별 실행 여부·위치·missing 라인, 미실행 함수에 호버 시 상세)
  - 측정 불가 환경에서만 추정치 폴백

## [0.3.2] - 2026-08-18

### Fixed
- **TEM 5.0 공식 정정**: 기존 `min(Branch,80)/80 * Func * 5`에서 사내 표준 공식으로 교체
  - Line Coverage 측정 가능 시: `min(LineCov, 80)/80 * FuncCov * PassRate * 5` (기본)
  - Branch만 측정 가능 시: `min(BranchCov*5/4, 80)/80 * FuncCov * PassRate * 5`
  - PassRate(테스트 통과율)를 TEM에 반영, HTML KPI 카드를 Line Coverage 기준으로 전환 (Branch는 폴백 표시)

## [0.3.1] - 2026-08-18

### Fixed
- **전역 `ici.toml` 자동 생성이 `verify`에서만 동작하던 문제**: CLI 콜백 레벨로 이동하여
  `doctor`/`line` 등 **어떤 명령이든 최초 실행 시** `~/.config/ici/ici.toml`이 생성되도록 수정

## [0.3.0] - 2026-08-18

### Added
- **소스 레이아웃 통합 (src 외 lib/app/packages/python 지원)**:
  - `core/project.py`에 `get_source_dirs()` 도입 — 기본 후보 `["src", "lib", "app", "packages", "python"]` 중 존재하는 디렉토리 + `ici.toml` `[project] source_dirs`로 오버라이드
  - `dup`/`complexity`/`dead`/`exception`/`sanitize`/`lint`/`type`/`test`/`build` 전 엔진 및 `detect_project_type`이 통합 헬퍼 사용 — 기존 `src/` 하드코딩 제거
  - `test` 엔진의 `PYTHONPATH`·`coverage --source`가 모든 소스 디렉토리 반영, `type` 엔진의 mypy 대상도 소스 디렉토리 기준
- **dup 엔진 Type-2 클론 검출 강화**:
  - 토큰 정규화(식별자→`ID`, 리터럴→`LIT`, 구조 키워드 보존)로 **변수명/리터럴만 다른 복사-붙여넣기 검출**
  - 교차 파일: `SequenceMatcher` 기반 갭 허용 블록 매칭 / 동일 파일: 비중첩 시드 + 그리디 확장
  - 중복 라인 집계를 **고유 라인 위치 합집합** 방식으로 전환해 과대 집계 방지, 최대 클론 우선 필터 강화
- **complexity 엔진 Python 보강**: `match` 케이스 guard, comprehension `if` 카운트 추가
- **coverage 모듈 레벨 프로브**: pytest와 **동일 인터프리터**의 `<python> -m coverage` 탐지 추가 — `.venv`가 공용 파이썬 site-packages를 상속하는 환경에서도 실측 테이블 생성 (`--version` 프로브로 검증)
- **공용 UV 경로 인식 (`find_uv()`)**: `$ICI_UV` → `~/.local/bin/uv` → `nas_shared/bin/uv` → `infra_root/bin/uv` → PATH 순 탐색, 전 엔진·doctor 연동
- **전역 `ici.toml` 최초 실행 자동 생성**: 설정 파일이 하나도 없을 때 `~/.config/ici/ici.toml`(XDG 존중)에 기본 정책 자동 생성 — 실패 시 무해하게 폴백
- **line 엔진 게이트/통계 분리**:
  - `[engines.line]`에 `gate_dirs`(기본 `src,include,lib,app`), `include_dirs`, `exclude_dirs` 추가
  - 임계값(500/1000) 검증은 게이트 디렉토리만 적용, tests/docs/scripts는 통계·트리 뷰에만 포함

### Fixed
- 콘솔 이슈 패널이 소스 스니펫의 `[...]` 문자를 Rich 마크업으로 오해석해 크래시하던 문제 — 동적 문자열 마크업 이스케이프 (`rich.markup.escape`)

## [0.2.0] - 2026-08-18

### Added
- **모듈별 실측 커버리지 테이블 (Module Coverage Table) — Python/C++ 동일 지원**:
  - `test` 엔진이 프로젝트 환경의 `coverage.py`(Python)와 `gcov`(C++, `g++ --coverage` 2단계 컴파일)로 **파일별 Stmts/Miss/Cover/Branch 실측값**을 수집 (기존 하드코딩 추정치 대체)
  - HTML `🧪 Tests & Coverage` 탭에 `coverage report` 형태의 **시각화된 모듈별 커버리지 표** 추가: 커버리지 낮은 순 정렬, 색상 임계값 바/미실행 라인 툴팁, 토탈 행
  - Branch KPI가 실측값으로 대체되어 TEM 스코어가 실제 품질을 반영, 커버리지 80% 미만 모듈은 `Coverage:Module` WARN으로 Issues 탭/PR 어노테이션 노출
  - 도구 부재 시 추정치 + 설치 안내로 폴백 (기존 동작 유지)
- **HTML 리포트 GitHub 배포 및 Sticky PR 코멘트 (`ici verify --publish`)**:
  - 생성된 `verify_report.html`을 GitHub Contents API로 `gh-pages` 브랜치에 푸시하는 퍼블리셔 엔진 추가 (`src/ici/engines/publish.py`)
  - **self 모드(기본)**: `GITHUB_TOKEN`만으로 자기 레포에 배포 — 추가 시크릿/외부 액션 불필요 (폐쇄망 GHES 호환)
  - **hub 모드(옵션)**: `ICI_PUBLISH_REPO`/`ICI_PUBLISH_TOKEN` 설정 시 중앙 리포트 허브 레포로 배포 (`<project>/pr/<n>/index.html`)
  - PR별 경로 네임스페이스(`pr/<n>/index.html`, `main/index.html`)로 다중 PR 동시 실행 시에도 충돌 없음
  - Pages 활성 여부를 매 실행마다 확인하여 스티키 PR 코멘트에 뷰어 링크 또는 1회성 Pages 설정 안내 표시 (마커 기반 갱신/생성)

## [0.1.0] - 2026-08-17

### Added
- **`ici` (Integrated CI) 단일 실행 ZipApp(`ici.pyz`) 아키텍처 구축**:
  - `shiv` + `scripts/launcher.sh` polyglot 프리앰블 결합을 통해 1.9MB 단일 파일 바이너리로 패키징
  - Python 3.10 하한 호환 및 시스템 인터프리터 자동 감지 (`$ICI_PYTHON`, `python3.14` ~ `python3.10`)
  - `build-pyz.sh` 재현 가능 빌드(Reproducible Build) 파이프라인 구축
- **전사 공용 표준 품질 게이트 설정 시스템 (`ici.toml` & `src/ici/config.py`)**:
  - `DEFAULT_CONFIG` 내장 표준 정책 및 `ici.toml` 중앙 정책 적용
  - 각 엔진별 `mode` (`pass_warn_fail`, `pass_fail`, `pass_warn`) 및 수치 기반 임계치(Thresholds) 설정 지원
- **9대 핵심 검증 엔진 및 커스텀 분석기**:
  - `line`: 500줄 초과 WARN / 1000줄 초과 FAIL 규칙 + 디렉토리 트리 데이터 추출
  - `lint`: `ruff check` + `ruff format --check` 및 C++ `g++` / `clang-format` 스타일 검사
  - `test`: 단위 테스트 전수 검증 + Branch/Function 커버리지 기반 TEM 5.0 스코어링 공식 산출
    $$\text{TEM Score} = \left( \frac{\min(80, \text{Branch Coverage})}{80} \right) \times \left( \frac{\text{Function Coverage}}{100} \right) \times 5.0$$
  - `type`: Mypy 정적 타입 분석 및 AST 어노테이션 검사 (노이즈 방지 0-Errors 요약 지원)
  - `complexity`: Cyclomatic 복잡도($> 15$ WARN, $> 25$ FAIL) 및 중첩 깊이($\ge 4$) 분석 + 원본 소스 코드 스니펫 추출
  - `sanitize`: C++ `-fsanitize=address,undefined` (ASan/UBSan) 및 Python 리소스 누수 검증
  - `dead`: 도달 불능 코드 및 미사용 심볼 검출
  - `dup`: 최대 클론 블록 병합(Maximal Clone Merging) 알고리즘 기반 코드 복제율 산출
  - `exception`: `except: pass` 에러 삼킴 및 소멸자 throw 차단
- **6개 전용 탭 인터랙티브 Zero-CDN HTML 대시보드 (`verify_report.html`)**:
  - **Tab 1 `📋 Verification Suites`**: 종합 상태 뱃지, TEM 게이지, 엔진 요약 및 각 전용 탭 원클릭 점프 버튼
  - **Tab 2 `📊 Line Analysis & Explorer`**: 전폭(Full-Width) 계층형 파일 트리 테이블 + 실시간 검색 필터 + 코드 분포 바
  - **Tab 3 `🧪 Tests & Coverage`**: 4대 커버리지 KPI 카드(TEM, Branch, Function, Pass Rate) + 파일별 테스트 스위트 및 개별 테스트 케이스 상세 뷰
  - **Tab 4 `🧩 Complexity`**: Top 15 복잡도 함수 리더보드 + **접고 펼칠 수 있는 소스 코드 블록 (Toggle All Code 지원)**
  - **Tab 5 `📦 Clone Groups`**: 연결 컴포넌트 클러스터링 기반 중복 코드 카드 + 원본 들여쓰기 보존 코드 블록
  - **Tab 6 `⚠️ Issues`**: 전체 엔진의 조치 필요(WARN/FAIL) 항목 통합 뷰 + **접고 펼칠 수 있는 문제 코드 스니펫 (Toggle All Code 지원)**
- **유니버설 에디터 연동 및 1-클릭 클립보드 복사 (`🛠️ Open With`)**:
  - 특정 에디터(VS Code) 강제 탈피: 드롭다운으로 `Copy Path (gvim/Vim/CLI)`, `VS Code`, `Cursor`, `PyCharm/IntelliJ`, `Sublime Text`, `Browser File` 중 선택 가능 (브라우저 `localStorage`에 상태 기억)
  - 모든 파일 위치 링크 옆에 빠른 `📋` 클립보드 복사 버튼 제공
  - 터미널 OSC 8 하이퍼링크 및 GitHub Step Summary permalink(`blob/...#L10`) 지원
- **GitHub Actions 개밥먹기(Dogfooding) CI & 자동 릴리스 파이프라인**:
  - `.github/workflows/ci.yml`: PR 생성 및 커밋 푸시 시 `dist/ici.pyz`를 빌드하여 `ici` 자체를 전수 검증하는 Dogfooding CI 게이트 및 리포트 아티팩트 업로드
  - `.github/workflows/release.yml`: 버전 태그(`v*.*.*`) 푸시 및 `workflow_dispatch` 수동 실행 시, `CHANGELOG.md`에서 해당 버전 노트를 자동 추출하여 `dist/ici.pyz` 바이너리와 함께 GitHub Release 발행
  - GitHub Ruleset(`ici-main-quality-gate`): CI 검증 미통과 시 `main` 브랜치 머지 원천 차단
- **빌드 및 환경 진단 도구**:
  - `ici build`: Python 바이트코드 컴파일(`compileall`), 릴리스 트리 패키징(`vX.Y.Z/x86_64/lib`), `env.sh` 및 `env.csh` 생성
  - `ici doctor`: glibc, WSL, 컴파일러, 린터, 파이썬 진단 테이블 출력
  - `ici env`: 셸 스크립트 소싱용 환경변수 스니펫 출력

### Removed
- **Coverity 및 SAM 엔진 제거**:
  - 프로젝트 경량화 및 9대 핵심 품질 게이트 집중을 위해 `cov` (Coverity) 및 `sam` (SAM) 엔진과 CLI 서브커맨드, 설정 스키마 전면 제거
