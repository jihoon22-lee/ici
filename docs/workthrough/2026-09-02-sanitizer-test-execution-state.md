# Sanitizer/test execution state

## Objective

Sanitizer builds can succeed while a build system collects a test that is disabled,
filtered, or otherwise never executed. A boolean `passed` value cannot distinguish that
case from an executed failure, so the adapter, engine, and reporter contract needed an
explicit execution state. This slice also closes the I4-4 requirement that a sanitizer
build with no executed tests must not become a false clean result.

## Implemented

### 1. Shared test-case contract

`TestCaseResult` in `src/ici/core/cmake.py` now has the fields
`name`, `passed`, `message`, and a trailing `executed` field. `executed` defaults to
`True`, preserving existing three-argument positional construction. Its invariant rejects
the impossible `passed=True, executed=False` combination. An executed failure therefore
remains distinct from a collected case that did not run.

```python
TestCaseResult("filtered", False, "disabled by platform", executed=False)
```

### 2. CTest and QtTest normalization

- CTest JUnit `<skipped>` and `status="notrun"`, `skip`, `skipped`, `disabled`, or
  `blacklisted` become `passed=False, executed=False`.
- The bounded CTest stdout fallback treats `Not Run`, `Disabled`, and `Skipped` verdicts
  as not executed and keeps the bounded verdict in `message`.
- Unknown CTest status is retained as `passed=False, executed=True`, so an unrecognized
  framework state cannot silently pass.
- QtTest `skip` and explicit `<skipped>` are not executed. `xfail` is an executed,
  expected failure and therefore passes; `xpass` is an executed failure. Unknown
  `result` values are executed failures (fail-closed).
- The QtTest parser reads these states per XML `<testcase>`. In qmake, the `make check`
  transcript remains authoritative at one scope per test binary; QtTest XML only enriches
  that binary's failure detail. It therefore does not claim every function-level skip as a
  separate qmake scope (with case-level XML as a limited fallback only when binary calls cannot
  be recovered).
- Pytest verbose output and terminal summaries map `SKIPPED` to a collected but non-executed
  case, `XFAIL` to an executed expected failure that passes, and `XPASS` to an executed failure.
  All of those per-test markers and summary tokens count as parseable result evidence, even when
  a run has no ordinary `passed` case. If pytest emits a terminal summary, its last authoritative
  count for each state is used; repeated collection/interruption/detail lines do not inflate the
  test total or failure count. A summary-only `N skipped` result is retained as `[Python] Skipped
  (N)` rather than a clean run.
- The unittest fallback preserves the same execution distinction: `... ok` is an executed pass,
  `... skipped` is a collected but non-executed skip, `... expected failure` is an executed
  expected pass, and `... unexpected success`, `... FAIL`, or `... ERROR` are executed failures.

### 3. Engine and HTML behavior

The `test` engine emits `SKIP` targets for non-executed cases, adds
`extra.skipped_tests`, and records `skipped` in every affected `test_suites` entry.
The HTML Tests & Coverage view renders skipped cases in their own amber rows instead of
putting them in the failure list.

For an applicable C++ sanitizer scope whose build completed, the aggregate policy is:

| Case pattern | `required = true` | `required = false` |
|---|---|---|
| All cases not executed | `ERROR` / `NOT_RUN` | `SKIP` / `ESTIMATED` |
| Clean executed cases plus non-executed cases | `ERROR` / `NOT_RUN` | `WARN` / `ESTIMATED` |
| Executed failure plus non-executed cases | `ERROR` / `NOT_RUN` | `FAIL` / `ESTIMATED` |

Required missing execution promotes the sanitizer engine even when another case ran;
the executed failure target is still retained as `FAIL`. Non-executed cases do not count
as measured sanitizer scopes or sanitizer issues. A project with no applicable scope at
all remains the separate explicit non-applicable/skip path.

The test engine applies the same all-collected-skipped rule across Python and C++ cases:
required execution is `ERROR`/`NOT_RUN`, while optional execution is `SKIP`/`ESTIMATED`.
The all-skipped shortcut is used only when no actual test or collection failure exists; a real
failure remains the higher-precedence `FAIL`/`ERROR` result even when another language contributes
only skipped cases. Coverage generation or a pre-existing coverage artifact cannot substitute for
execution evidence or promote an all-skipped run to `MEASURED`/`PASS`.

### 4. CI-derived report and tool-path hardening

The first PR #132 CI run exposed a separate integration boundary: a large self-verification
could write more than GitHub's 1 MiB Step Summary limit and emit thousands of workflow
annotations, while the standalone `dist/ici.pyz` invocation did not inherit the project virtual
environment's installed `ruff`/`pytest` path. The follow-up keeps the complete JSON and HTML
artifacts as the source of truth and bounds only the GitHub presentation surfaces:

- `generate_markdown_report()` shows at most 100 target rows per engine in deterministic severity
  order and records omitted rows with an explicit full-inventory notice.
- `write_github_step_summary()` appends at most 900,000 UTF-8 bytes, truncating only at a valid
  code-point boundary and including an honest notice that the JSON/HTML reports retain all
  details.
- `emit_github_actions_annotations()` emits at most 50 annotations, deterministically selecting
  FAIL/ERROR before WARN/SKIP, followed by one `::notice::` omission message when needed.
- The CI style step verifies the project `.venv/bin/python`, persists it as `ICI_PYTHON`, and
  adds `.venv/bin` to `GITHUB_PATH` before standalone self-verification.

These limits apply to GitHub's bounded display surfaces only; the JSON and HTML report contracts
remain complete. The first CI run failed on the discovered integration boundary. PR #132 was
subsequently squash-merged into `main` at
[`5a7a23f032b6b56c737ad7124d24646763cf10d1`](https://github.com/jihoon22-lee/ici/commit/5a7a23f032b6b56c737ad7124d24646763cf10d1),
and exact-main [run `33602697235`](https://github.com/jihoon22-lee/ici/actions/runs/33602697235)
completed successfully. Final PR
[run `33601774411`](https://github.com/jihoon22-lee/ici/actions/runs/33601774411) and Merge Gate
were green, and the [sticky comment](https://github.com/jihoon22-lee/ici/pull/132#issuecomment-5505498518)
is the sole `github-actions` marker/current-run comment. Extracted PR artifact and Pages HTML are
byte-identical for ici (`7,513,806` bytes,
`f6b39e7e852a5ca2039bef9287e09359ee082dca9d7dbccc644db1bf0fae0406`) and viewer
(`356,773` bytes, `5bbd432739ccbecf3f36afd882beabff042889a371c12b42e6551e0617bcad82`);
both pass UTF-8 exact-title and Zero-CDN checks. The post-fix local source-checkout
self-verification exits `0` with
`WARN` (7 passed, 5 warnings, 0 failed, 0 errors, 1 skipped across 13 engines), and its Step
Summary is valid UTF-8 and `100,609` bytes. The local package and smoke evidence below remain
historical for this sanitizer slice; the remote merge evidence is now complete.

## Files

The implementation commit updates these code and regression files:

- `src/ici/core/cmake.py`
- `src/ici/engines/test.py`
- `src/ici/engines/test_output.py`
- `src/ici/engines/sanitize.py`
- `src/ici/reporters/markdown.py`
- `src/ici/reporters/html/sections/test.py`
- `.github/workflows/ci.yml`
- `tests/test_execution_state.py`
- `tests/test_reporter_hardening.py`
- `tests/test_purity.py`

This documentation update covers:

- `CHANGELOG.md`
- `docs/engine-reference.md`
- `docs/user-guide.md`
- `docs/architecture.md`
- `docs/superpowers/2026-08-30-handover.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `docs/workthrough/2026-09-02-sanitizer-test-execution-state.md`

## Key decisions

- Keep `executed` as a fourth, defaulted field so existing adapter callers remain source
  compatible while new callers can state execution explicitly.
- Treat skip/not-run/disabled as absence of measurement, not as a passing test and not as
  an executed failure. Preserve locations and bounded reasons in `InspectionTarget`.
- Map `xfail` to a genuine executed pass, but map `xpass` and unknown framework states to
  executed failures. The parser must fail closed when it cannot interpret a state.
- Treat pytest per-test markers and the authoritative terminal summary (`SKIPPED`, `XFAIL`, and
  `XPASS` included) as execution evidence, and use only the terminal summary's final counts when
  collection output repeats the same error.
- Keep unittest's verbose-state mapping aligned with pytest: skipped cases are non-executed,
  expected failures are executed passes, and unexpected successes are executed failures.
- Give actual test/collection failures precedence over the all-collected-skipped shortcut during
  cross-language aggregation.
- Keep required and optional sanitizer policy separate: required missing execution blocks
  with `ERROR`/`NOT_RUN`; optional missing execution remains visible through
  `SKIP`/`WARN` and `ESTIMATED` evidence.
- Keep GitHub presentation bounded and deterministic: cap target rows and annotations while
  preserving FAIL/ERROR before lower-severity annotations, and direct readers to complete JSON/
  HTML artifacts whenever a display surface omits content.
- Bound Step Summary by UTF-8 bytes rather than characters, cut only at valid code-point
  boundaries, and persist the project's installed Python/bin path before standalone dogfooding.
- Keep the release boundary unchanged. The version remains `0.10.2`, and this slice does
  not create a release.

## Verification

| Check | Result |
|---|---|
| Runtime | Python `3.10.21` |
| Focused execution-state and real-CMake regression | `24 passed` |
| Combined related suites | `196 passed`: `test_build_adapter`, `test_build_adapter_e2e`, `test_sanitize_engine`, `test_test_engine`, `test_reporters`, and `test_execution_state` |
| Real QtTest fixture | Qt `6.10.2` fixture built and ran with `-xunitxml`; QSKIP emitted `<skipped message>`, XFAIL emitted no failure and passed, and XPASS emitted `<failure type="xpass">`; temporary fixture was deleted |
| Full local Python 3.10 run | With real extracted clang-tidy 21: `1,686 collected; 1,684 passed, 2 skipped`. The skips require unavailable `clazy` or `clang++`; neither an unavailable tool nor a skipped test was silently counted as passed. This is local evidence, not PR CI or release acceptance. |
| Ruff | `uvx ruff check .` passed; `uvx ruff format --check .` reports `165 files already formatted` |
| Mypy | `Success: no issues found in 97 source files` |
| Source-checkout self-verification | Exit `0`; `WARN` with 13 engines (`7` passed, `5` warnings, `0` failed, `0` errors, `1` skipped). The generated HTML is `7,580,686` bytes, has the expected title and no external asset URLs. Capability inventory is healthy (`31` tools, `24` ready, `0` incomplete, `7` unavailable) and required `ruff`/`pytest` are ready. |
| GitHub Step Summary bound | Valid UTF-8, `100,609` bytes (`<= 900,000`); line summary and ready tool rows are present, while the complete JSON/HTML reports remain unbounded source-of-truth artifacts. |
| Reproducible package | Two consecutive builds produced byte-identical `dist/ici.pyz`, `2,235,838` bytes, SHA-256 `a6e437ba08336d4ced2eb02752be3ec5849d029fa8bff2cbca182956b6b31e9f`. |
| Smoke | Version/help, doctor, shell environments, Python 3.10 launch, artifact integrity, and Zero-CDN HTML checks passed; packaged smoke exited `0`. |
| Documentation hygiene | `git diff --check` passed |
| PR #132 integration acceptance | First CI run exposed the >1 MiB summary, unbounded annotation, and standalone-tool-path issues. Final PR run `33601774411`, single sticky comment, Merge Gate, artifact/Pages byte-match, exact titles and Zero-CDN passed; PR #132 was squash-merged at `5a7a23f`, then exact-main run `33602697235` succeeded. |

The focused and related-suite rows remain local evidence; the separate final paragraph and table
row record remote PR/main acceptance. This corrective slice did not create a release or close the
remaining I4-4 roadmap scope.

## Follow-ups

- Keep the merged PR #132 and exact-main run evidence linked above when comparing future changes;
  a later source/reporting follow-up still needs its own PR CI, Pages, and artifact evidence.
- Keep the remaining I4-4 items (TSan profile, resource/lifetime/security mapping, and
  quality-zoo UAF/leak/UB/Qt lifetime scenarios) pending.
- Revisit the release decision only after the repository-wide gate and required cross-repo
  evidence are complete; do not bump the version for this documentation/contract slice.
