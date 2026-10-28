# Native report evidence corpus

Stored-result reader tests use the published next v1 and legacy v3 fixtures in
`tests/fixtures/ici-next`. Reading preserves producer extensions and rich target,
finding, metric and context payloads. Schema families remain distinct. Duplicate
JSON keys, unsupported versions and malformed source regions are refused.

Reports are pure projections of saved data. They do not execute checks, load
source files, install tools, use the network or decide a new gate verdict.
Supporting imported evidence retains its provenance and `NOT_EVALUATED` gate.

SARIF location handling follows [SARIF 2.1.0 plus Errata 01](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
especially sections 3.14.27 (column units), 3.30.8 (exclusive end columns), and
3.4.4 (URI base identifiers). ici source spans use inclusive Unicode codepoint
endpoints. Conversion must preserve that distinction and explicit base mappings.

The native report crate owns its CSS/JavaScript and immutable Python rule alias
metadata; it does not include files from the Python runtime tree. All rendering
and legacy projection take saved data only. The caller owns filesystem writes,
source snapshots, timestamps, publication and TLS.

Frozen original-pipeline evidence, captured from `6fe3e405` during development:

- `migration-v2.json`: six exact v2-to-v3 archive migrations, including Unicode,
  duplicate symbols, normalized invalid positions, explicit roots and tool data.
- `legacy-pipeline.json`: exact `findings_for_result` output for every target
  (including PASS), overload/region fingerprints, resource/compiler native
  replacement, GNU linker native identity alongside its target adapter, and a
  package finding with no target. Historical fingerprints use
  `ici-fingerprint/v1`; modern attributed identities remain in `native_run`.
- `issue-groups.json`: fifteen exact conservative display projections. Precise
  Python aliases require overlapping columns and trusted semantic context;
  line-only evidence, different operations, C++ paths and same-producer aliases
  remain separate. Clone groups union overlapping occurrences without changing
  the saved finding inventory.
- `rich-19-families.json`: all nineteen report families with normal and warning
  targets, line scope/file inventory, tests/coverage/TEM/test quality, complexity,
  clone snippets, dependency cycles, diagnostics, generated inputs, sanitizer,
  artifact, ABI, integration, support matrix and producer extension fields.

`ProjectionContext` supplies exact required policy, additional check-family
mappings, project name and duration. Missing historical policy or identity is
left unknown; native selection/execution/evidence/code/publication axes remain
independent. `finding_identity_map` and `target_scopes` live in engine `extra`,
so strict historical finding/target objects keep their original shape.
Configuration closure uncertainty does not turn an actually successful tool
execution into `SKIP`; `evidence_complete` remains a separate recorded fact.

Input limits: supporting imports are 32 MiB / one million JSON nodes; stored
results are 64 MiB / eight million nodes. Nesting is capped at 128 levels and
finding inventories at 100,000 records. HTML refuses more than 64 MiB of actual
escaped embedded JSON or 128 MiB of expanded output. Above 2,000 actionable
findings it renders fifty initial cards and pages/filters the complete saved
inventory locally. A 100,000-record regression exercises the actual path.

Console output defaults to five groups per engine, four locations, and eighty
Unicode display cells; verbose mode retains all groups. Markdown has a 900,000
UTF-8-byte summary budget with an explicit truncation notice; annotations are
bounded, escaped, and prioritize new/regressed findings before severity.

Intentional correctness changes are pinned by native tests:

- SARIF declares Unicode code points and converts ici's inclusive end column
  to SARIF's exclusive end; it emits no undefined `SRCROOT` base.
- Imported `uriBaseId` requires an explicit mapping. Missing producer column
  units remain unspecified; UTF-16 conversion requires supplied source text.
- A machine-usable fix is emitted only when every replacement explicitly
  confirms Unicode code points and exclusive ends. Unconfirmed historical
  edits remain literal properties with a limitation; a multi-file fix is never
  partially offered. HTML displays suggested text without applying it.
- Imported SARIF/JUnit/LCOV always remains supporting `NOT_EVALUATED` evidence.
- HTML/Markdown/console output redacts credentials at the rendering boundary;
  the stored reader's lossless archive round-trip remains separate.

Verification uses locked Rust tests, strict scoped Clippy and crate formatting,
with cargo jobs and test threads capped at two under the shared heavy lock.
Development-only Python captured the immutable fixtures; it is never invoked
by the native report implementation or its Rust tests.

Source navigation uses only explicit caller context. `source_root` enables the
VS Code/Cursor/PyCharm/Sublime/browser-file picker. `repository_url` and
`commit_ref` together enable encoded source links; credentials, query/fragment,
control characters and traversal are refused. `[external]` stays literal.
Default reports remain portable with location-copy controls. The development
DOM harness is runnable with
`node tests/rust-parity/reporting/navigation-dom-smoke.cjs` and verifies actual
inline JavaScript dispatch/encoding without installing a browser or reading
project sources.

The application can use the pure APIs with its existing atomic storage:

```rust,ignore
let bytes = ici_execution::storage::read_bounded_regular(input, 64 * 1024 * 1024)?;
let stored = ici_reporting::StoredResult::from_bytes(&bytes)?;
let options = ici_reporting::RenderOptions {
    title: project_name.to_string(),
    source_root: Some(project_root.to_string_lossy().into_owned()),
    ..Default::default()
};
let html = ici_reporting::render_html(&stored, &options)?;
ici_execution::storage::atomic_write(output, html.as_bytes(), write_mode)?;
```

For a new native run, call
`legacy_projection(&run, &outputs_by_logical_task_id, &ProjectionContext { ... })`
then wrap it in `StoredResult::Legacy`. Supply exact `required_checks`,
`engine_names`, `raw_fingerprints`, project name and duration from execution.
Historical `analysis_metadata` / `legacy_baseline` require their own proven
compatibility; otherwise leave them `None` and retain modern facts in
`native_run`. The modern envelope never receives report-only fields.

`StoredResult::to_json` is the lossless archive round-trip. Use `render_json`
for a redacted JSON output boundary. `render_sarif`, `render_markdown`,
`render_console` and `github_annotations` likewise project saved facts only.
`import_report` and `load_auxiliary` handle supporting evidence; attach it via
`RenderOptions::auxiliary` without changing the run's gate or selection.
