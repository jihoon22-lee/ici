# Compiler-backed C/C++ TU-local unused internal functions

## Overview

This workthrough records the narrow compiler-backed C/C++ unused-function slice currently present
on `feat/compiler-backed-cpp-unused-functions`. The `dead` engine can replay the exact production
translation-unit configurations recorded in the compilation database and retain only an
`-Wunused-function` diagnostic that the compiler attributes to that translation unit and that is
stable across every known configuration of the source. Replay is limited to capability-approved
GCC/Clang drivers and approved aliases.

The implementation is intentionally a translation-unit-local diagnostic contract. It does not
claim whole-program, linker, dynamic-loading, plugin, or Qt meta-object reachability. The public
version remains `0.10.2`; no release is created for this slice.

The implementation work is represented by:

- `85eef44` — `feat(dead): add exact compiler unused-function evidence`
- `0d4ceb8` — `fix(dead): preserve atomic compiler evidence`
- `18f907f` — `fix(dead): harden compiler evidence boundaries`
- `0d018a0` — `fix(dead): identify compiler diagnostic families exactly`
- `5055027` — `fix(dead): retain invalidated compiler targets`
- `e538d63` — `fix(dead): reject unlocated compiler warnings`
- `a427ba0` — `test(cpp): align real compiler diagnostic replay`
- `b87cc76` — `refactor(dead): lower compiler probe complexity`
- `95252bf` — `test(cpp): prove compiler family evidence precedence`
- `61f6370` — `test(dead): harden compiler evidence boundaries`
- `ff078de` — `test(dead): cover approved C compiler replay`
- `8f8f4d0` — `fix(toolchain): identify compiler aliases by observed family`
- `7522fe7` — `fix(cpp): reject operands after option separators`
- `88c18da` — `test(toolchain): cover compiler family aliases`
- `3a38997` — `fix(cpp): bind include probes to directory identity`
- `ea1d4b5` — `chore(toolchain): apply project formatting`
- `2b7ff41` — `fix(dead): align automatic C++ analysis scope`
- `13099ca` — `fix(dead): reject unsupported compiler unit languages`

## Context

The existing Python `dead` path uses AST reachability and name-reference heuristics. That evidence
remains `ESTIMATED` and `python-ast-heuristic`. C/C++ needs a compiler-owned signal for the smaller
question “did the selected compiler diagnose this internal-linkage function as unused in this
source-owned translation unit?” The compiler already has the language and configuration context;
the adapter therefore consumes the canonical compilation database instead of guessing defines,
include paths, unity-build membership, or linker reachability.

The project model and common source intake are still the ownership boundary. They provide bounded,
strict UTF-8 snapshots of project sources, while the immutable `AnalysisContext` supplies the
project identity, approved compiler capabilities, and CMake/qmake compilation context. A missing
or unusable exact context is represented as unavailable/not-run rather than promoted to an exact
result.

## Changes Made

### 1. Configurable C/C++ scope policy

`[engines.dead].cpp_unused` is independent of the Python dead-code heuristic and accepts three
literal values:

```toml
[engines.dead]
cpp_unused = "auto"      # auto | required | off
```

`auto` is the default. If exact C++ context is unavailable, the C++ scope is transparently
`SKIP`/`NOT_RUN`; `required` escalates that unavailable scope to `ERROR`/`NOT_RUN`; when a C++ scope
is present, `off` omits C++ candidates from source intake and compiler probing, emits an explicit
C++ scope skip, and leaves Python dead-code analysis enabled. A C++-only project with the scope
disabled is not made required solely by this feature. More specifically, a C++-only project with
`cpp_unused = "auto"` and no exact context remains non-required (`required = false`), reports a
`SKIP`/`NOT_RUN` C++ scope, and contributes a suite `WARN` rather than an automatic required error;
an explicit `required` policy still escalates unavailable context. Once a context exists, invalid
context, coverage, configuration, replay, or identity errors remain errors in both modes.

The in-repository viewer acceptance target sets `cpp_unused = "required"` in
`viewer/ici.toml`, making missing compilation coverage visible there without changing the public
stable version.

### 2. Shared immutable context for `verify` and standalone `ici dead`

`prepare_analysis_context` in `src/ici/engines/verify.py` now owns the common preflight:

```text
project discovery
    -> support/tool policy and capability inventory
    -> CMake or qmake compilation context
    -> immutable AnalysisContext
```

The full `verify` orchestration uses this helper for the selected engine set. `DeadCodeEngine`
declares `ANALYSIS_CONTEXT_ENGINES = {"dead"}`. The standalone command path in
`src/ici/__main__.py` sees that declaration, prepares the same project/tool/compilation context
model, but scopes capability probes to the tools selected by `dead` plus configured
`[doctor].required_tools`. It injects that context into the engine and passes the same project
model to support-matrix evaluation. This removes the old standalone path in which `ici dead`
constructed the engine without a compilation database. When `cpp_unused = "off"`, the selector
requests no standalone context probe for the C++ scope.

The context remains the source of truth for:

- project-root identity and discovered compilable C/C++ sources;
- canonical compilation-database path and digest;
- every selected `CompilationUnit`, including its source, working directory, argv, language,
  and configuration identity;
- approved, probed compiler path/version and CMake/qmake ingestion diagnostics;
- the `unity_build` guard and working-directory identity.

The compilation-database digest is the identity of this immutable captured-context snapshot. During
preflight, the database is parsed into copied, frozen `CompilationUnit` values and the engine
replays those values; the engine does not reread a live `compile_commands.json` while executing a
run. If the database is mutated after the snapshot is prepared, that mutation is intentionally
observed by the next preflight, which constructs a new digest and a new immutable context.

### 3. Exact source and translation-unit selection

`DeadCodeEngine` snapshots the selected Python and C/C++ sources through
`src/ici/engines/_source_inputs.py`. The C++ adapter then restricts replay to production
project C/C++ sources with matching compilation-database units, including sources in configured
external build directories that are part of the project source inventory. It no longer drops an
external-build source merely because a build/link engine excludes it from self-linking. Headers may
be present in the bounded inventory, but a header/non-TU diagnostic is outside this source-owned
finding contract.

Before any compiler runs, the adapter requires all of the following:

- an `AnalysisContext` with a compilation database;
- a canonical `sha256:` database digest and a canonical `sha256:` identity on every selected
  source configuration, with each unit configuration digest recomputed from its directory, argv,
  and output;
- a matching project root and no compilation-context ingestion errors;
- `unity_build` not set to `true` (a context with `unity_build=true` is rejected because a unity
  command cannot prove which source owns a diagnostic);
- at least one selected unit and coverage for every requested production source;
- every selected unit has language exactly `c` or `c++`; an unknown language or another language
  such as `objective-c++` is rejected before any compiler process executes;
- no duplicate `(source, configuration)` pair and no more than 2,048 selected configurations.

The selected source snapshot is checked against the context snapshot before preparation and again
around each replay. The approved compiler must be a regular executable outside the project. Its
device, inode, mode, size, mtime, and ctime identity is captured before execution and compared
again before and after the otherwise-successful replay. The replay working directory is likewise
required to remain the same contained directory with the same device, inode, and mode identity.

### 4. Diagnostic-only compiler replay

`src/ici/core/cpp_replay.py` adds the `unused-functions` replay operation and centralizes warning
policy projection for the existing C++ tooling adapters. The sanitized command keeps semantic
compile arguments while removing project output/dependency-generation operands, requires exactly
one canonical source operand, rejects unsafe/unbounded command shapes, and uses a clean replay
environment.

For the unused-function operation, warning suppression and warning-as-error policy are projected
for diagnostics. The operation appends the compiler-owned rule and discarded assembly output:

```text
... -Wunused-function -Wno-error=unused-function -S -o <os.devnull>
```

`-S` completes the compiler front-end phase that owns this GCC diagnostic without invoking the
linker or creating a project object/executable. An observed GCC family at version 9+ uses structured
JSON diagnostics; older GCC and Clang/approved aliases use bounded parseable-text diagnostics. If
`g++` and Clang capabilities resolve to the same executable, the observed Clang family wins over
the alias spelling. Project rule-visibility flags are replaced with controlled
`-fdiagnostics-show-option`. No compiler family outside the approved GCC/Clang set is replayed. The shared
`src/ici/engines/_cpp_tooling.py` helper selects the diagnostic format from the approved compiler
capability, and `src/ici/engines/_cpp_diagnostics.py` normalizes both forms.

The capability probe records the compiler family from the bounded version banner, so a neutral
executable or a `g++`-named Clang alias cannot be classified from its filename or numeric version
alone. The replay argument parser also treats the option separator strictly: after `--`, exactly
the canonical source operand is accepted; an extra operand, a second separator, or a warning
option in that operand position is rejected with `extra-compiler-operand` before execution.

GCC standard-library include projection is bound to both the approved compiler identity and a
replacement-sensitive working-directory identity. The cache key includes the directory device,
inode, mode, modification time, and change time. The helper validates the directory before lookup,
rechecks it before returning a cached projection, and rechecks it after the include probes. A
replacement or metadata change therefore causes a cache miss or fails closed with
`gcc-include-probe-cwd-changed`; a projection is never reused across an unverified working-directory
identity.

The replay is bounded to 120 seconds per translation-unit configuration, 600 seconds globally,
1,000,000 diagnostic-output characters, 32,768 replay arguments, and 1 MiB of total replay
argument characters. A timeout, truncation, process failure, nonzero exit, parse failure, compiler
error, source change, or compiler identity change fails the probe closed.

### 5. Located filtering and atomic configuration agreement

Only a normalized diagnostic satisfying all of these conditions is retained:

1. its rule is exactly `-Wunused-function`;
2. it is a warning, not a note or compiler error;
3. its primary file is exactly the selected source path;
4. its line and optional columns are inside the immutable source snapshot; and
5. its source location is not repeated in the same compiler output.

An otherwise matching `-Wunused-function` warning with no primary source location cannot be
attributed to the selected source and fails the exact probe closed; it is neither a finding nor a
non-TU exclusion count.

Only an exact `-Wunused-function` warning whose primary location is outside the selected TU is
counted in `cpp_unused_non_tu_diagnostics_excluded`; other non-TU/header/external diagnostics are
ignored and do not affect that count or become findings. If the compiler attributes a
macro-generated definition to an expansion location, that compiler-reported logical location is
preserved only when its path equals the selected TU and its range fits the immutable snapshot.
Out-of-range `#line` or macro remapping fails closed; the physical macro-definition origin is not
reconstructed.

Observations are grouped by source. A source is exact only when every known configuration produced
the same set of located diagnostic ranges. A clean, agreed source receives a located PASS target;
an agreed warning receives a `Compiler:-Wunused-function` WARN target and a
`CppUnusedFunction` detail containing configuration identities, compiler names/versions, and the
original normalized diagnostic message.

The C++ probe is atomic. Replay/process/parser errors are fail-fast: already collected C++
observations/findings are discarded, no remaining compiler unit is run, and the outcome becomes
`ERROR`. Configuration disagreement discovered during the final merge also discards every C++
finding. Each previously completed and recorded compiler observation retains a located
`C++UnusedFunctionsInvalidated` `SKIP` target instead of an exact PASS/WARN, making the discarded
execution scope traceable. No partial C++ finding is presented as exact. The `DeadCodeEngine` still
retains completed Python findings in a hybrid result. Python evidence and confidence remain
`ESTIMATED`/`python-ast-heuristic`; when both scopes complete, native C++ findings remain exact with
compiler/tool-rule attribution (`tool_name` plus `tool_rule_id = "-Wunused-function"`) and the
aggregate remains conservatively `ESTIMATED`. If the C++ probe fails, aggregate status/evidence
reflects that failure while completed Python findings
remain. Every inspected source and every error has a path/line `InspectionTarget`.

### 6. Dead-engine evidence and support-matrix integration

`src/ici/engines/dead.py` keeps Python and C/C++ evidence separate:

```json
{
  "analysis_provenance": "cpp-compiler-unused-function",
  "language_evidence": {"python": "NOT_APPLICABLE", "cpp": "MEASURED"},
  "cpp_unused_mode": "exact",
  "cpp_unused_details": [
    {
      "file_path": "src/main.cpp",
      "start_line": 1,
      "start_column": 13,
      "tool_rule_id": "-Wunused-function"
    }
  ]
}
```

An exact C++ scope is `MEASURED`, and each C++ finding is `FindingConfidence.EXACT` with
`tool_rule_id = "-Wunused-function"`. When both scopes complete, a hybrid result remains aggregate
`ESTIMATED`, while its language evidence records C++ `MEASURED` and Python `ESTIMATED`; if the C++
probe fails, completed Python findings remain and the aggregate records the C++ `ERROR`/`NOT_RUN`
outcome. `src/ici/core/support.py` reads this language-specific evidence and
advertises C++ `dead` as tool-backed/exact without upgrading the Python declaration.
`src/ici/core/models.py` preserves `NOT_APPLICABLE` for an absent language scope so unrelated
projects do not turn a non-applicable engine into a false failure.

`dead` result cache-key generation, load, and store are disabled for every result, including
Python-only and hybrid results, until the external/generated include closure and compiler binary
content are modeled in the cache identity. No current cache key is issued or used for this engine.

### 7. Final scope and safety corrections

The final correction makes compiler-backed dead analysis cover the full C/C++ source inventory
selected by `project_cpp_sources()`: both ordinary project-owned sources and sources under
configured external build directories are passed to exact translation-unit selection. The
external-build distinction remains relevant to engines that build or link artifacts, but it is not
a reason for a source-reading dead analysis to omit a source. Each included source still needs
coverage from the canonical compilation database before replay can start.

The automatic policy was also relaxed at the suite boundary. For a C++-only project, an
`cpp_unused = "auto"` run with unavailable exact context remains `SKIP`/`NOT_RUN` and
`required = false`, so suite aggregation reports `WARN` rather than converting an unavailable
optional scope into a required error. An explicit `required` policy still fails closed. This
relaxation applies only to unavailable automatic scope; once context exists, invalid context,
coverage, configuration, replay, parser, or identity failures remain `ERROR`/`NOT_RUN`.

The replay preparation now rejects every selected compilation unit whose language is not exactly
`c` or `c++`, including an empty language or `objective-c++`, before invoking a compiler. This
guard prevents an unsupported driver/language combination from being mistaken for a valid C/C++
observation.

### 8. Files and components

The feature commits touch these repository-relative files:

| File | Role |
| --- | --- |
| `ici.toml` | Default project policy exposes `engines.dead.cpp_unused = "auto"`. |
| `viewer/ici.toml` | Viewer acceptance target requires exact C++ dead scope. |
| `src/ici/config.py` | Default configuration value. |
| `src/ici/config_schema.py` | Validation for `auto`, `required`, and `off`. |
| `src/ici/core/_cpp_replay_policy.py` | Positive replay policy and controlled diagnostic-option projection. |
| `src/ici/core/cpp_replay.py` | Sanitized unused-function replay and warning-policy projection. |
| `src/ici/core/toolchain.py` | Compiler-family detection from version banners and capability completeness. |
| `src/ici/core/models.py` | Applicability/evidence aggregation wording and behavior. |
| `src/ici/core/support.py` | C++ dead support declaration and per-language evidence/policy. |
| `src/ici/engines/_cpp_diagnostics.py` | Shared GCC JSON and GCC/Clang text diagnostic normalization. |
| `src/ici/engines/_cpp_unused_functions.py` | Exact TU-local adapter, filtering, merge, budgets, and evidence. |
| `src/ici/engines/_cpp_tooling.py` | Shared compiler capability/diagnostic-format and unit-selection helpers. |
| `src/ici/engines/_cpp_lint.py` | Reuses the shared compiler capability/diagnostic helpers. |
| `src/ici/engines/_source_inputs.py` | Documents and reuses source snapshots as exact-probe anchors. |
| `src/ici/engines/dead.py` | Combines Python heuristic and C++ compiler-backed scopes. |
| `src/ici/engines/verify.py` | Shared project/tool/compilation context preparation. |
| `src/ici/__main__.py` | Standalone engine command context injection. |
| `tests/test_config.py` | Policy acceptance/rejection contracts. |
| `tests/test_support_matrix.py` | Hybrid evidence and `off` policy contracts. |
| `tests/test_cpp_replay.py` | Replay sanitization and discarded-assembly command contracts. |
| `tests/test_cpp_unused_functions.py` | Adapter, parser, location, configuration, identity, fail-closed, and real-GCC process contracts. |
| `tests/test_cpp_tooling.py` | Compiler-family alias and controlled diagnostic-format contracts. |
| `tests/test_toolchain_capabilities.py` | Compiler-family, alias, target-triple, and capability-probe contracts. |
| `tests/test_clazy.py` | GCC include-projection cache, compiler identity, and working-directory identity contracts. |
| `tests/test_cpp_tool_e2e.py` | Existing real-tool suite expectation aligned with shared diagnostic replay. |
| `tests/test_dead_engine.py` | Dead facade status/evidence/finding integration contracts. |
| `tests/test_cli.py` | Standalone `ici dead` shared-context injection contract. |
| `tests/test_verify_orchestrator.py` | Full verify preflight and language-scope orchestration contracts. |
| `CHANGELOG.md` | Current-slice behavior and release-policy record. |
| `README.md` | Public usage and capability documentation. |
| `docs/architecture.md` | Engine/context, provenance, and reporter architecture updates. |
| `docs/engine-reference.md` | `dead` C/C++ scope and evidence reference. |
| `docs/user-guide.md` | User-facing configuration and result guidance. |
| `docs/superpowers/2026-08-30-handover.md` | Cross-session status, decisions, and pending remote acceptance. |
| `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md` | Roadmap task state and acceptance gate. |
| `workthrough/2026-09-02-cpp-function-scope-policy.md` | Historical policy boundary and current follow-up pointer. |
| `workthrough/2026-09-03-compiler-backed-cpp-unused-functions.md` | This implementation and evidence record. |

## Code Examples

### Compiler-visible scope fixture

The focused real-GCC fixture deliberately mixes supported and unsupported claims:

```cpp
static int unused_internal() { return 1; }       // TU-local candidate
namespace { int unused_anonymous() { return 2; } } // TU-local candidate
static int used_internal() { return 3; }
[[maybe_unused]] static int intentional() { return 4; }
inline int unused_inline() { return 5; }
template <typename T> int unused_template() { return 6; }
int external_unreferenced() { return 7; }
int main() { return used_internal(); }
```

The test contract expects only the first two compiler-owned internal-linkage diagnostics to be
retained. The used, explicitly `maybe_unused`, inline/template/COMDAT, and external-linkage cases
are not presented as this adapter's exact findings.

### Normalized diagnostic examples

GCC structured output and Clang/approved-alias parseable text converge on the same located rule, for
example:

```text
src/main.cpp:1:13: warning: unused function 'unused_internal' [-Wunused-function]
```

The target retains the source-relative path and 1-indexed location. The original normalized warning
text is retained in `cpp_unused_details`; top-level tool evidence separately retains the executed
argv, compiler path/version, return code, and timeout/truncation state rather than compiler output.

## Verification Results

### Known local verification

The repository currently contains focused contracts for:

- approved GCC/Clang driver selection, GCC 9+ structured diagnostics, older-GCC and
  Clang/approved-alias parseable text, and real-GCC filtering of internal-linkage functions;
- Clang parseable-text normalization;
- clean-source PASS targets and exclusion of unrelated warnings, notes, and non-TU locations;
- an unlocated matching `-Wunused-function` warning failing closed as source-unattributable, with no
  finding or non-TU count;
- missing database/digest/coverage, ingestion errors, `unity_build=true`, malformed output, nonzero
  exits, timeouts, truncation, source mutation, compiler replacement, and configuration
  disagreement;
- working-directory identity changes, recomputed unit configuration digests, logical source
  positions, and the rule-specific non-TU exclusion count;
- warning-policy projection and discarded-assembly replay shape;
- hybrid Python/C++ evidence separation, `cpp_unused` policy behavior (including C++ intake being
  skipped by `off`), C++-only automatic required relaxation, full owned/external C/C++ source
  selection, unsupported translation-unit-language rejection, and standalone context injection
  with scoped capability probes.

These contracts are present in `tests/test_cpp_unused_functions.py`, `tests/test_cpp_replay.py`,
`tests/test_cpp_tooling.py`, `tests/test_toolchain_capabilities.py`, `tests/test_clazy.py`,
`tests/test_cpp_tool_e2e.py`, `tests/test_dead_engine.py`, `tests/test_verify_orchestrator.py`,
`tests/test_cli.py`, `tests/test_config.py`, and `tests/test_support_matrix.py`.

The final local verification evidence is:

| Gate | Observed result |
| --- | --- |
| Focused post-refactor regression | `607 passed, 6 skipped, 17.99s` |
| Python 3.10 full suite (`uv run --python 3.10 pytest -ra`) | `1,966 passed, 7 skipped, 68.60s` |
| Ruff check/format | PASS, 184 files |
| Mypy | PASS, 104 files |
| ZipApp reproducibility | Two builds byte-identical; 2,273,944 bytes; SHA-256 `2a3c8b011e53d21529ee03e20b0f7eeafbf7fbfaf6b8a9e35f5445b166c88d28` |
| Smoke and packaged verify | Smoke PASS; packaged verify exit `0` |
| ici self verify (`--no-cache`) | WARN; `8 pass/5 warn/0 fail/0 error/1 skip`; TEM `4.84`; duration `184.31s`; wall `188.75s`; RSS `605,144`; HTML 5,526,617 bytes / `159ba3db668127541c4ff56ebc535138fbd5541ad86eccad45879e606e50742d`; JSON 15,590,867 bytes / `0d38d3b9daa92977b3933dd3b2bbf58531b52134d87f462d7a9271b659affe1a` |
| Viewer standalone `dead --report` | PASS; configured `cpp_unused = "required"`; 8 sources/configurations/targets/tool rows; wall `10.73s`; RSS `360,320`; JSON 22,803 bytes / `3fc04526528490db436bebaa0af12ca2fea47d994afdda48f4795c1ac1914c42` |
| Viewer deep verify (`--no-cache`) | WARN; `12 pass/1 warn/0 fail/0 error/1 skip`; 14 engines; 7/7; TEM `4.89`; duration `20.00s`; wall `20.42s`; RSS `360,556`; HTML 355,996 bytes / `9098bec837b61d2ed08c15cdb21b4b4f59741a160eb0a09dfb74d8163bb33d8c`; JSON 743,422 bytes / `069eb0dced6c835c2690b8d45da2216ffb15af233b5dc7a3f92e609fd90d67ad` |
| Report integrity | ici title `ici Verification Report — ici`, viewer title `ici Verification Report — viewer`; both have Zero-CDN `[]` |

### Remote acceptance

PR #137, titled `feat(dead): add compiler-backed C/C++ unused-function evidence`, passed the
required remote checks in workflow run [`33675765436`](https://github.com/jihoon22-lee/ici/actions/runs/33675765436)
with head `9c9d83cdaae02384bbc58e7cb79b4bbb098b86d3` and synthetic merge
`f2cfce8b8a7ebc90308bb442f3a323e01ed9ef34`. The PR had one current-run sticky comment
([comment](https://github.com/jihoon22-lee/ici/pull/137#issuecomment-5515582296)) with exactly two
report links. Both report artifacts matched their PR Pages copies byte-for-byte; the JSON
`source_commit` was the synthetic merge SHA, and both reports had their exact titles, valid UTF-8,
and Zero-CDN results.

| Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
| --- | --- | --- |
| ici | 5,188,748 / `8648d7ac06fded3afaa004568a9665bb3bc2b10c7e41f1da06af41b0eb3952f8` | 15,288,643 / `f9401da10828ab3d0c1c6b9430789d25b4ef4ac15e8dbe410f0f244a584aefef` |
| viewer | 363,787 / `0123db7d6e5c820fc0bd952a0fd55b82752b63d873b4f0502e12f676b3e71cda` | 905,151 / `edde8208502d4af5c060e556ece1650518893c7274487cca2283c02f63322f98` |

The PR was squash-merged as `782589a4ef02209703e882a09cc0d8b0c7940218`, and its feature branch
was deleted. Exact-main workflow run [`33676873412`](https://github.com/jihoon22-lee/ici/actions/runs/33676873412)
completed all relevant checks successfully. The Pages build API run `1190632325` and workflow run
[`33677689026`](https://github.com/jihoon22-lee/ici/actions/runs/33677689026) also succeeded. Main
report artifacts matched their Pages copies byte-for-byte; their JSON `source_commit` matched the
merged main SHA `782589a4ef02209703e882a09cc0d8b0c7940218`, and the exact titles, UTF-8 checks, and
Zero-CDN checks passed.

| Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
| --- | --- | --- |
| ici | 5,188,748 / `7d9a23d5eb47bcf0ab82f074a85e65eb264869f8f0333318673890d75b0c4eaf` | 15,288,649 / `99d5c208a30518e0c356c4e9a26b2306a99468d51369dd91e9eaa19b71a22e19` |
| viewer | 363,788 / `223c027a6cbbef5aa08c464f210286c6a90ae2a702451739aa94bf704648188f` | 905,152 / `152e6c2f6d2b53728f39680b3198b5fb46d1c28e915731c6a7693f85c0175557` |

This acceptance closes the remote PR, exact-main, Merge Gate, artifact, and Pages evidence for the
slice. It does not create a release or bump the public version; `0.10.2` remains the stable
release until a separate release decision.

## Limitations and Next Steps

This slice does not classify external-linkage functions, templates, inline/COMDAT definitions,
header-only/non-TU diagnostics, linker reachability, dynamic lookup, plugins, or Qt meta-object
reachability. Generated/moc/vendor inputs remain excluded by the normal source-ownership policy
unless explicitly opted in, and exact compiler coverage is still required. A context with
`unity_build=true` is rejected because source ownership cannot be proven. Compiler diagnostics are
evidence of this specific warning contract, not proof that a symbol is unreachable from the final
program.

The next delivery should prepare a public artifact containing this feature and cross-validate an
intentional unused-function case plus clean and false-positive cases in `quality-zoo`, checking
rule, location, configuration, and evidence contracts. Whole-program/linker-backed dead-symbol
reachability, broader I4-3 completion, full duplicate semantics, and the remaining I4 checkpoint
are separate pending work. The stable version remains `0.10.2` until a deliberate release decision
is made.
