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

## Files

The implementation commit updates these code and regression files:

- `src/ici/core/cmake.py`
- `src/ici/engines/test.py`
- `src/ici/engines/sanitize.py`
- `src/ici/reporters/html/sections/test.py`
- `tests/test_execution_state.py`

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
- Keep the release boundary unchanged. The version remains `0.10.2`, and this slice does
  not create a release.

## Verification

| Check | Result |
|---|---|
| Runtime | Python `3.10.21` |
| Focused execution-state and real-CMake regression | `24 passed` |
| Combined related suites | `196 passed`: `test_build_adapter`, `test_build_adapter_e2e`, `test_sanitize_engine`, `test_test_engine`, `test_reporters`, and `test_execution_state` |
| Real QtTest fixture | Qt `6.10.2` fixture built and ran with `-xunitxml`; QSKIP emitted `<skipped message>`, XFAIL emitted no failure and passed, and XPASS emitted `<failure type="xpass">`; temporary fixture was deleted |
| Full local Python 3.10 run | With real extracted clang-tidy 21: `1,682 collected; 1,680 passed, 2 skipped` in 63.81s. The skips require unavailable `clazy` or `clang++`; neither an unavailable tool nor a skipped test was silently counted as passed. This is local evidence, not PR CI or release acceptance. |
| Ruff | check and format checks passed |
| Mypy | `96` source files passed with no issues |
| Reproducible package | Two consecutive builds produced byte-identical `dist/ici.pyz`, SHA-256 `9c2a240d3f6be3f13f7bc514baae6ad373bb97b6222da64af3bd2f91f6cf8739`. |
| Smoke | Version/help, doctor, shell environments, Python 3.10 launch, artifact integrity, and Zero-CDN HTML checks passed; the self-verify exit was `1` because findings remain visible rather than being hidden |
| Documentation hygiene | `git diff --check` passed |

The focused and related-suite results are local evidence. This workthrough does not claim
PR CI, sticky-comment publication, Pages readiness, extracted-artifact HTML matching, or a
full release gate.

## Follow-ups

- Run the branch through PR CI and record Merge Gate, sticky comment, Pages, and extracted
  artifact evidence before treating the follow-up as remotely accepted.
- Keep the remaining I4-4 items (TSan profile, resource/lifetime/security mapping, and
  quality-zoo UAF/leak/UB/Qt lifetime scenarios) pending.
- Revisit the release decision only after the repository-wide gate and required cross-repo
  evidence are complete; do not bump the version for this documentation/contract slice.
