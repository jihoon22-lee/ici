# Native doctor acceptance

Both command routes use the common read-only planner and the existing process runner.
The next document preserves `schema_id="ici.next.doctor"`, schema version 1, root, profile,
component check rows and recorded C++ compilation inputs. Historical `doctor --json` retains
system/tool/capability/support/profile/path fields; `--brief` keeps the compact summary.

The approved Rust replacement changes two historical assumptions explicitly: the running
runtime is reported as Rust (`running_python: null`), and doctor does not search for NAS or
shared initialization paths. Python candidates refer only to selected project runtimes.
The tool inventory follows selected checks plus explicit `doctor.required_tools`; disabled
checks do not trigger an unrelated host tool survey.

Only version arguments run, once per distinct resolved executable identity. Probes use
one-second timeouts, an 8192-byte output bound, at most two concurrent children and the
common process-group cleanup. No project tests, imports, compiler replay, configuration,
build or implicit installation runs. Missing/optional runtime axes remain separate, and
support entries retain `NOT_RUN`: version readiness does not establish analysis support.
Recorded compilation geometry does not claim a complete generated-input closure.

`doctor-gates-final.log` passed all 11 actual command cases in 1.09 seconds and strict
CLI/transitive Clippy in 6.18 seconds. Cases cover shared linter probe identity, disabled
checks, project Python absence alongside native rules, optional runtime matrix absence,
recorded C++ context without replay, required tool failure/timeout, output truncation,
verified/corrupt native tool packs, both public JSON routes and brief output, redaction,
cancellation, source-free diagnostics, invalid scope and preservation of source/config/state.
The original contracts are in `src/ici/cli/next_path.py` and `src/ici/doctor.py`.
This scoped command acceptance does not close the complete R8 product ledger.
