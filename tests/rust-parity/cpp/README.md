# C/C++ native evidence corpus

The source and lexical oracle files were captured from the frozen Python product
`6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188` by the development-only
`capture_source_oracle.py`. The Rust product does not invoke this script or Python.

- `source-oracle.json`: 25 function/exception cases, including constructors,
  operators, templates, raw strings, control flow, unbraced statements and nested
  lambdas. The native function ownership and cyclomatic/cognitive values match
  the frozen source algorithm; the Unicode identifier omission is an explicit
  correction in `known-corrections.json`.
- `lexical-oracle.json`: 57 token and directive cases covering physical line
  provenance, C++ line splicing, literal classes/suffixes, Qt/API anchors,
  digraphs, malformed opaque literals and ordinary identifier renaming.
- `clang-boundaries.cpp` / `.json`: actual Clang 21.1.8 AST output, with the input
  path normalized to `/fixture/main.cpp`. Compiler source ranges must agree with
  the immutable source syntax. Macro-generated and lambda call-operator bodies
  are excluded; repeated template instantiations share one source boundary.
- `nested-response-observation.json`: actual GCC response-file resolution. Nested
  response paths resolve from the compiler working directory.

The crate's process corpus also builds actual CMake, Qt 5 and Qt 6 projects,
including moc, rcc and uic inputs and recorder paths containing spaces. Other
fixtures run GCC compilation/unused-function/include probes, dynamic/static ELF
inspection, ABI policy, task transaction cancellation, and user-file preservation.
The live Clang fixture accepts `ICI_TEST_CLANG` and optional
`ICI_TEST_CLANG_LIBRARY_PATH`. Missing Clang is reported as a skip unless
`ICI_REQUIRE_BUILD_ADAPTERS=1`, which makes it a failure. The frozen actual AST
parser fixture always runs.

Source syntax and lexical complexity/cognitive/include-cycle results remain
**estimated**. A successful compiler AST probe establishes a source boundary,
not an exact lexical complexity score. Unused-function diagnostics require
agreement across every selected compile configuration and make no whole-program
reachability claim. Empty dependency lists do not prove static ELF linkage.

The GNU linker corpus builds two CMake executables sharing an object. A hidden
function discarded by both is reported; a function used by either target is
retained. Global default-visible, weak, COMDAT, LTO, cloned and multiply aliased
sections are excluded. The parser requires an approved GCC driver, observed GNU
ld identity, ELF `EXEC`, complete function-section compile coverage, direct
objects, stable source identity and a located binutils section/symbol record.
CMake's linker dependency-file output is removed from diagnostic relinks.

Coverage/sanitizer/thread-sanitizer build variants select Debug and carry their
instrumentation through both native CMake and qmake capture phases. Actual CMake,
Qt 5 and Qt 6 coverage builds preserve a project macro and produce `.gcno` notes.
Additional analysis flags are bounded; qmake output/install/global-setting hooks
are refused in the extra argument interface. The native project still owns its
compiler/toolchain configuration.

Compiler boundary policy (`off`/`auto`/`required`) is checked against every
captured configuration and the immutable source digest. A function present in
only some configurations is not promoted. Compiler include cycles are computed
within each observed configuration, so opposite edges from different variants
cannot invent a cycle. Source recovery remains visible and requires compiler
confirmation to establish complete boundary geometry.

Line counting does not invoke the grammar and keeps its separate snapshot bound.
LF, CRLF and CR-only locations are covered. Active line and cached duplicate
normalization cancellation are tested against the two-second cancellation budget.

The dedicated clang-tidy boundary probe uses an inline configuration independent
of project `.clang-tidy` files. Frozen and live Clang 21.1.8 function-size notes
preserve per-configuration lines/statements/parameters; suppressed or unknown
records refuse exact evidence. The selected GCC driver's C++ and C include
searches are probed separately. Their observed difference supplies
`-nostdinc++` / `-isystem` arguments for Clang tooling; no GCC version or private
library path is guessed. Missing active includes retain their located warning
and do not create a dependency edge.

Unity/aggregate source coverage is confirmed from complete compiler include traces,
not from a generated TU's filename. A real CMake unity fixture covers both original
sources only after observation; an unrelated unity-like filename leaves the
selection incomplete. Compiler unused-internal-function ownership through unity
remains explicitly unsupported, matching the prior contract's narrow claim.

| Facet | Native API / retained contract | Evidence |
|---|---|---|
| Recorded compile input | `load_compilation_context`, original/expanded argv, cwd, response identities, defines/includes/sysroot/variant, explicit ambiguity | Context fixtures and actual GCC nested response observation |
| Native build capture | `plan_build`, `BuildLease`, same-binary qmake recorder, CMake/qmake/Make owned shadows | Real CMake, explicit Make, Qt 5/6 moc/rcc/uic and coverage builds; cancellation/ownership fixtures |
| Source measurements | `CppAnalysisSession`, all inspected PASS targets, separate line and rule bounds | 25 frozen metric/exception cases, 57 lexical cases, Unicode correction and newline/cancellation fixtures |
| Compiler structure | `compiler_probe_task`, AST/tidy boundary readers and per-configuration policy | Frozen/live Clang 21.1.8 AST and size notes, macro/lambda/template cases |
| Include scope | `parse_include_trace`, missing-include intake, per-configuration cycle analysis and aggregate coverage confirmation | Real GCC local/generated/external/missing header probes, variant isolation and real CMake unity membership |
| Selected GCC library | C++/C include tasks and observed search difference | Real GCC probes; malformed/duplicate/root directory refusal |
| Unused internal functions | `unused_function_consensus`, agreement across every selected configuration | Real GCC variant fixture; no whole-program or unity ownership claim |
| GNU section GC | Explicit shadow build/relink tasks, source-bound object/binutils facts and cross-target intersection | Real two-target CMake build and local/hidden/default-visible/weak/COMDAT/LTO/clone exclusions |
| Binary ABI | `parse_readelf`, `ElfPolicy`, `analyze_elf` | Real static/dynamic ELF and dependency/version/loader-path policy |

Application/CLI task orchestration, runtime test/coverage/sanitizer verdicts, artifact
publication and the final migration inventory are verified by their owning layers;
these pure adapters do not independently claim whole-product acceptance.

`compilation-export-oracle.json` contains ten complete standalone v1 payloads
captured from the same frozen Python commit by `capture_compilation_export.py`.
The native CLI test compares every field and digest after normalizing only the
producer version. Cases retain compiler wrappers, MSVC command style, ordered
undefines, opaque flags, target triples, secret definitions and external
include/sysroot limitations. Additional native tests prove relocation-stable
semantic digests, process-free reading even with unavailable component sources,
input-preserving atomic output, explicit database failure, and actual canonical
CMake/Qt 5/Qt 6 preparation. The Rust command never invokes the oracle script.

The native CLI's explicit legacy `dead` command requests an isolated
`link-reachability` variant when GNU linker inspection is enabled. Its build,
identity probes, relinks, ELF/object observations and bounded section-scoped
`addr2line` effects are visible in the preparation/final plans. Relinks require
the matching held `BuildLease`; serialized overlay metadata cannot authorize a
write. The combined unused-function/linker mode keeps a separate primary build.
Ordinary `analyze` does not prepare this variant, and disabled modes spawn no
tools. The live CLI acceptance checks the two-target intersection, original
source preservation and final input stability. Immutable object/link-command
inputs enter the common snapshot; relink-produced executable files are declared
outputs observed only after their producing step completes.

ABI defaults classify observed declared bytes and select executable/shared ELF
artifacts, deduplicating contained SONAME aliases to their regular backing file.
Archives, relocatable objects and reports do not become implicit ABI targets.
Static-only scopes emit located `NOT_APPLICABLE` without resolving readelf;
explicit artifact selection and required-policy failures remain enforced. The
native legacy `type` descriptor likewise preserves a located C/C++ applicability
result without reading an unrelated compilation database or spawning tools.

Compilation scopes hash parsed configuration identities for databases and
response files, and validated ordered command semantics for GNU link metadata.
Absolute project paths normalize through the same compiler argument rules used
by TU identities; external roots, options, ordering and variants remain distinct.
Raw bytes remain frozen for input drift. Source/object/ELF/archive contents are
code evidence and do not become policy hashes. Actual `.clang-tidy` configuration
bytes enter the snapshot and policy scope. CLI regressions prove unchanged scope
across relocated DB/response/GNU-link checkouts, changed scope for changed response
flags, unchanged scope for code fixes, and preserved original artifacts when
explicit preparation switches inspection to an owned shadow.
