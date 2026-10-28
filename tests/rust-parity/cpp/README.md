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
