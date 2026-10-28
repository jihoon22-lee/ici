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
