# Native Python rule parity evidence

R4 is in progress. Tests are native Rust except explicit development-only oracle
comparison scripts. No `.py` oracle or Python worker is part of the product.

## Implemented first unit

`ici-analysis` reads immutable `AnalysisRequest` inputs and emits one shared
`AnalysisOutput` for modern findings and legacy inspection targets. `AnalysisSession`
reuses bounded parsed AST/scope/metric data by source bytes, path, target, options and
parser identity. No filesystem or process fallback is used.

- Strict UTF-8/BOM and measured character-codec decoding with original-byte mapping.
  Unknown codecs, malformed data, target grammar violations and semantic syntax
  errors produce incomplete evidence and an inspected error target.
- Domain spans use 1-based Unicode character columns, correcting the old Python
  byte-column display for non-ASCII text. Raw byte offsets stay internal.
- Lexical module/function/class/lambda/comprehension graph with imported aliases,
  parameters, shadowing, global/nonlocal, async scopes and native semantic checks.
- Line counts preserve explicit `options.legacy_projection`, resolved `source_paths`
  and `gated_paths`, nullable limits, all inspected targets and separate all/source
  aggregates. Modern checks gate selected source files.
- Cyclomatic and cognitive formulas use one cached scan. The 15 source fixtures in
  `metrics.json` were measured from public source `6fe3e405` on CPython 3.14.7, and
  preserve nested-scope pruning, signature expressions, elif/sole-else-if AST
  semantics, async-with versus old block-depth rules and decorator-independent
  function locations. Legacy inclusive versus modern strict cognitive thresholds
  remain distinct projections of the same measured numbers.

The crate tests additionally exercise decoder byte maps, Unicode, explicit legacy
line scope, semantic errors, target mismatch, delimiter budget, scope shadowing and
cache reuse. The fixed-source oracle can be checked without regenerating goldens:

```bash
/path/to/python3.14 tests/rust-parity/python/metrics_oracle.py /path/to/frozen-oracle
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/target \
  cargo test --locked -p ici-analysis
```

Security and resource rules now have a native implementation and 42 frozen-source
fixtures, with explicit corrected expectations separate from the original oracle.
Packaging/wheel compatibility remains unfinished. This partial evidence does not satisfy the Rust cutover gate; R8 must close every
functionality row first.


## Security and resource unit

- Native security uses AST literal/call shapes and shared lexical alias resolution;
  secret values never enter target snippets/messages. `nosec` is read only from
  comment tokens. Entropy, private-key markers, exact-name allowlists, crypto
  intent (`usedforsecurity=False`), pickle, randomness and shell-call rules remain.
- Resource analysis retains bounded branch/loop/try/finally ownership states,
  aliases, context managers, explicit close/aclose, return/attribute/subscript
  transfer, ExitStack, mutable defaults and explanatory legacy metadata in
  `extra.resource_issues`. Acquisitions, defaults and CPython-projection AST counts
  match the frozen oracle where behavior is preserved.
- Corrected rows explicitly name false-positive closure/comprehension alias
  handling, defaults evaluated before parameters bind, break skipping loop else,
  and previously skipped except-star resource bodies. No golden is silently
  regenerated to hide these differences.
- Vendor/generated exclusions retain inspected SKIP targets, reason counts and
  limitations; explicit include overrides work. Unanalyzed files remain incomplete.
- Both domain and legacy end columns are 1-based Unicode **inclusive**, preserving
  ASCII legacy endpoints and correcting UTF-8 byte-column display. Parser ranges
  stay half-open bytes. Multiline/zero-width/Unicode endpoint fixtures cover the
  conversion.

`hygiene_oracle.py <frozen-oracle>` verifies the original evidence only. Native tests
compare the explicit corrected expectation when present, so old defects stay visible.


## Exception unit

46 fixed-source handler fixtures preserve alias binding order, conditional paths,
module/class/function lookup, aliases introduced after a deferred function definition,
local future bindings, transient handler names, matching captures, deletes, boolean
and conditional-expression assignments, BaseException/bare handlers, swallowing and
handler-local traceback resets. Handler query work is bounded; an exceeded bound is
incomplete evidence. `exception_oracle.py` verifies original expectations without
rewriting them. Current native tests also cover all prior units and ownership checks.

## Input budget and cancellation unit

R0's input bounds are 16 MiB per file and 512 MiB per snapshot. The strict decoder
accepts the exact per-file boundary and explicitly rejects a larger file. The
inherited 2,048-file source-inventory bound is applied only to the rule families
that already used that bounded inventory; it is not imposed on line/metrics.
Encoding cookies are scanned from raw first-two-line slices and single-byte codec
origin maps use checkpoints. Line iteration does not materialize every line.

`AnalysisSession::analyze_cancellable(request, &AtomicBool)` polls intake, per-file
rules, metric/handler loops, security AST walks and resource statements. Cancellation
keeps completed targets/findings and returns `TaskState::Cancelled`; partial resource
flow does not invent leak findings or a file PASS. A single parser call is not
interruptible, so full parser/rule stress and CLI descendant cleanup remain R8 work.

The development `cancellation_probe` runs a small file followed by a 16 MiB input,
sends cancellation after 20 ms, and checks that the first file's completed target is
retained while the last file is not inspected. The measured WSL debug run returned
CANCELLED in 580 ms with one retained target; this is recorded in
`cancellation-observation.json`. It does not establish the complete CLI/child-process
cancellation budget or the aggregate 512 MiB stress acceptance.

## Import graph unit

`cycle.json` records 16 graph cases from the frozen public implementation, with
corrected edges kept separately. Ordered declared source roots, package-relative
imports, package attributes, deferred/conditional imports, stdlib-name collisions,
self-cycles and the legacy unique-suffix fallback remain visible. Source-root
precedence no longer silently takes the last conflicting module file. Ambiguous
suffixes and relative imports leaving a declared package are incomplete instead
of invented edges. Unique-suffix cycles carry ESTIMATED evidence because the
runtime import search path was not proven. The displayed cycle follows real
graph edges rather than joining the SCC member order as a path.

Every successfully inspected file has a target, shadowed files have SKIP targets,
and graph failures preserve location-bearing error targets. An iterative SCC
fixture traverses 5,001 nodes without recursive graph walking. Twenty native tests
(including the earlier units) passed after this unit. `cycle_oracle.py` verifies
the recorded original graph and does not overwrite corrected expectations.

## Private symbols and unreachable statements

`dead.json` preserves 21 frozen-source cases and the complete original targets.
The native test compares every target field after sorting presentation order;
private-definition targets precede unreachable targets, whose native presentation
is source order rather than Python's breadth-first AST order. The shared lexical
scope graph prevents parameter/local/import shadowing from falsely marking a
module private function used. Declared package attributes take precedence over
same-named child modules, and Unicode columns count characters. Those changes
have explicit corrected fixture fields. Decorators, `__all__`, async/private-only
module definitions, source-root precedence, aliases, relative imports, repeated
function definitions, match bodies and the first unreachable statement per body
remain covered. Incomplete parsing produces ERROR with inspected healthy-file
SKIP targets and no guessed unused-symbol findings. All private-symbol findings
remain ESTIMATED. Twenty-one native tests and scoped Clippy/fmt passed; the
`dead_oracle.py` script verifies the original evidence without regenerating it.

## Explicit legacy annotation coverage

`annotations.json` preserves seven frozen-source cases for the opt-in missing
return annotation warning, including nested, async, decorated and Unicode
functions and dunder exclusions. `python.type` in this native pack requires
`legacy_projection=true` or `annotation_only=true`; it cannot silently stand in
for an external type checker. All inspected files now have coverage targets,
and invalid syntax is incomplete rather than silently ignored by the old
fallback. Warnings preserve their original lines/names/messages. This rule
checks annotation presence only and its findings remain ESTIMATED.

## Static compatibility, shared Type-2 matcher and source facts

`compat-static.json` preserves 21 CPython 3.14.7 syntax/API floor cases and AST
node counts. The native parser checks the current tested grammar before checking
an explicit floor, so newer valid syntax yields a floor warning. Parser-specific
floor locations, character columns and relative imports referring to local code
have explicit corrected expectations. Immutable `pyproject.toml` metadata and
PEP 440 specifiers infer the same bounded first supported minor; invalid metadata
is incomplete. No floor is asserted when none is declared. Syntax-floor caches
use the actual parser target, preventing reuse by a stricter unrelated check.
Packaging and wheel inspection are covered by the separate archive contract below.

`type2.json` freezes 16 old token/region inputs plus exact groups, fingerprints,
locations, snippets and duplicated-line numerators/denominators. The public native
`duplicates` module retains rolling-window hashing, semantic signal filtering,
region barriers, bounded seed extension, overlap suppression and deterministic
connected components. C++ and Python consume this one matcher. Budget exhaustion
and cancellation are incomplete; clone locations remain visible below configured
rate thresholds. The Python lexical intake and additional AST semantic shapes
are not yet connected by this primitive unit.

The public `source_facts`/`function_facts` APIs share bounded native parsing and
return errors for malformed or uninspectable sources. Coverage body ranges start
at the first statement's definition line and end at the function end, inclusive;
nested, decorated, async and Unicode functions retain lexical class/qualified
names for pytest resolution. Twenty-seven native tests, scoped Clippy/fmt and
both fixed-source oracle scripts passed after this unit.

## Python Type-2 lexical intake

The Python pack now feeds the shared matcher from pinned native lexer tokens.
The expanded `type2.json` has 25 fixtures: imported API/builtin anchors, physical
lines, indentation, literals, f-strings/templates, contextual match/case keywords,
long Unicode identifiers, import-first statement exclusion, semicolons, function
regions, decorators and CR/LF variants. Tests compare every normalized record and
the region equivalence partition, then the complete end-to-end clone groups.
Scope identifiers are internal; equality of scope/segment membership is the
matching contract. The 500,000-token bound and inherited source/record/comparison
bounds remain explicit. `min_window` is accepted alongside the shared core's
`window_size` option. AST semantic shapes remain the next separate unit.

## Bounded semantic clone shapes

`semantic.json` records 50 fixed-source shape cases and all canonical field
strings, hashes, region names/spans, node counts, serialized lengths and
exclusions. `semantic-integration.json` records ten full stable-engine group
merges and policy projections. The native implementation projects the pinned
parser into CPython field order and preserves alpha binding order, constants,
API names, annotations/default/decorator scopes, context nodes, nested-scope
pruning, type comments, Unicode and lone UTF-16 surrogate literals. Rust scalar
replacement characters never silently replace the latter in fingerprints.
The source's Python 3.10 semantic grammar and conservative exclusions remain.
Function-local star imports now fail the common semantic syntax gate rather
than reaching an AST helper which accepted an un-compilable tree; that is an
explicit fixture correction.

Semantic groups include only leaf callables, retain canonical-shape collision
checks, suppress already represented lexical occurrence sets, and merge their
counted code positions before calculating the rate. `auto`, `off` and `required`
retain their original behavior and exclusion metadata. File/region/node/string
budgets remain 256/20,000/500,000/16 MiB. Required incomplete shapes cannot yield
partial clone success. All 29 native tests and scoped Clippy/fmt passed; the
`semantic_oracle.py` script verifies original evidence without rewriting it.

`FunctionFact.entry_line` separately retains the first decorator line for
coverage code-object exits. `requires_python(request)` exposes the same strict
immutable metadata parser directly to runtime compatibility consumers.

## Unicode 16 archive identity and parser inventory

Official Unicode 16 full case-fold and XID property data are retained with their
source URLs, SHA-256 digests and Unicode LICENSE V3. Generated static tables
avoid inheriting a future Unicode inventory from a transitive parser dependency.
The complete set of 1,112,064 Unicode scalar values was compared to CPython
3.14.7/Unicode 16 for full folding and identifier start/continuation. Native tests
verify those digests and representative NFC/portable archive-name collisions.
Unknown future identifier characters fail with inspected locations. The parser
identity now includes `unicode16`; no Unicode data is downloaded at runtime.
The normalization dependency is exactly 0.1.24, whose tables are Unicode 16.
Thirty native tests and scoped Clippy/fmt passed after this unit.

## Source package and wheel contracts

`package.json` preserves 105 frozen-source cases with complete findings,
fingerprints, target locations, statuses and measured archive metadata. The
native implementation inspects source declarations, callable/imported entry
points, dynamic versions, package files, wheel identities/tags, metadata headers,
case-sensitive INI entry points, strict RECORD CSV, SHA-256/384/512 integrity and
pure-wheel policy. It never imports project code, extracts archives or starts a
Python worker. Actual STORE, DEFLATE, BZIP2, LZMA and Zstandard archives are
included; their passing Linux tests do not establish other deployment platforms.
INI defaults are compared directly without copying inherited large values into
every section; a 512-section/512 KiB default regression covers that bound.

ZIP/ZIP64 directories are bounded before the archive library allocates or builds
its name map. Raw UTF-8/CP437 identities, valid Unicode-path extra overrides,
portable NFC/case-fold collisions, local-header identity, overlapping compressed
ranges, duplicate members, encryption, links/special members, CRC failures and
declared expansion sizes are checked. Four explicit correction records retain
the old observations while requiring safe rejection of contradictory directory
counts, local names, overlapping ranges and a raw UTF-8 decode exception.
Configured defaults/upper bounds remain 32 wheels, 8,192 members, 64 MiB declared
uncompressed content and 1 MiB per metadata member. Every hash/read loop checks
cancellation between 64 KiB chunks. The cancellation regression retains a
completed source metadata target and refuses to mark a partially hashed wheel
complete.

Modern `python.compat` inspects syntax/API floors by default. Source package and
wheel inspection is selected by `package_inspection=true`, the legacy projection,
nonempty wheel globs or immutable wheel inputs. An explicit package/wheel request
without metadata is incomplete. Legacy projection with absent metadata retains
its original static-only behavior. `package_evidence` and the legacy `wheel`
alias contain identical measured summaries when all selected inspection work
finishes; incomplete work cannot synthesize a successful summary.

`metadata.json` adds 25 PEP 440 selector/floor cases (including arbitrary equality,
empty sets, pre/dev/local versions and invalid selectors) and 14 metadata/import
discovery cases. Component metadata takes precedence over workspace fallback.
The public `python_version_allowed` helper shares this parser with project
runtime consumers. Both free and session-based `source_facts_cancellable` APIs
propagate active cancellation during enrichment.

Unicode evidence now also compares every scalar's lowercase mapping, Python
`repr`, decimal value and Python regular-expression word membership, plus all
256 CP437 bytes. Contextual final sigma uses the retained Unicode 16 Cased and
Case_Ignorable properties; lowercase processing remains linear. Security
allowlists and `nosec` comments use full case folding, with two frozen-source
regressions. The oracle scripts verify the preserved old results without
rewriting evidence:

```sh
python3.14 package_oracle.py /path/to/frozen-checkout
python3.14 metadata_oracle.py /path/to/frozen-checkout
python3.14 unicode_oracle.py
```

These commands require the recorded CPython 3.14.7 / Unicode 16 development
oracle, packaging 26.3 and tomli 2.4.1. They are comparison tools only and are not
product runtime dependencies.

## Source character encodings

The native decoder now uses 87 pinned strict character-codec tables: 70
single-byte maps, ASCII and 16 stateless CJK codecs. This includes CP949/UHC,
EUC-KR, Shift-JIS/CP932, Big5/HKSCS, EUC-JP/JIS variants, GBK/GB2312/GB18030 and
Johab. Actual CPython mappings preserve differences such as Shift-JIS versus
CP932 wave dash, CP932 private-use bytes and CP864's non-identity ASCII percent.

The native corpus compares 7,322,676 byte-input observations against CPython
3.14.7: every single/double-byte combination per table, every GB18030 four-byte
pointer and all EUC-KR Hangul composition forms. Fifty frozen source fixtures
also preserve original UTF-8-only intake/metrics observations and independent
CPython byte-source AST semantics. They verify strict failures, BOM/cookie
normalization, encoded identifiers, Unicode columns and original byte
boundaries. Eight frozen ownership cases cover full Unicode casefold; the
shared `source_exclusion_reasons` API supplies that exact classifier to C++.

The decoder retains original bytes and handles two-scalar character mappings
explicitly: an interior boundary without a unique raw offset is unrepresentable.
Analysis requires proven token boundaries. Changed raw bytes/encoding headers
invalidate the parser cache; mixed valid/invalid input cannot authorize a complete
source-facts result. Active multibyte decoding cancellation preserves already
completed line evidence. Parser identity now records the codec revision.

```sh
python3.14 -I -B tests/rust-parity/python/generate_codec_tables.py --check
python3.14 -I -B tests/rust-parity/python/encoding_oracle.py FROZEN_CHECKOUT \
  --dependencies .venv/lib/python3.10/site-packages
```

The generator writes only with explicit `--write`; verification keeps original
observations unchanged. Numeric mapping data and the Rust decoder enter the
product. Full upstream PSF terms and CJK mapping notes are retained beside the
data and included by the build-time license collector.

Stateful ISO-2022/HZ and UTF-7/escape/IDNA source semantics remain under a separate
audit. This milestone does not close the complete codec, per-engine option or
Python feature ledger, and unknown encodings remain explicit incomplete input.
