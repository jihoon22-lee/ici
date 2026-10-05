# ici Architecture

> The stable v0.11 engine/reporter shell described by the previous revision of
> this document was removed in the ici-next cutover. The normative design
> record is [`design/ici-next/`](design/ici-next/README.md) — this page is the
> map of the code as it stands.

## Layers

`src/ici/` is arranged so each layer only depends on the ones below it:

| Layer | Contents |
|---|---|
| `domain/` | `Workspace`, `Component`, `BuildUnit`, `Check`, `Task`, `Observation`, `Finding`, verdict/state enums. No IO. |
| `config/` | `ici.toml` reader, schema validation, layer composition, provenance (`Decided` values carry which file set them). |
| `workspace/` | Project inventory: source discovery, CMake/qmake/compile-DB readers, test-suite discovery. |
| `toolchain/` | Deterministic tool and interpreter resolution — `Request`/`Candidate`/`Resolver`; bundle mode never falls back to PATH. |
| `analysis/` | Pure analysis cores: line counting, cycles, duplication, coverage parsing, dead code, ELF/ABI. No engine shells. |
| `languages/` | `check` descriptors and `measure_*` entry points per language (`python/`, `cpp/`, plus shared `cycles`, `deadcode`, `duplicates`, `metrics`, `hygiene`). |
| `application/` | Orchestration: `plan` (check→task graph), `verify` (execution + gate), `baseline`, `report`, `publish`, `tem`, `gates`, `tooling` glue. |
| `adapters/` | External-tool providers (`ruff`, `mypy`, `pytest`, `gcov`, `clang-tidy`, `ctest`, `readelf`…) and the GHES client. |
| `execution/` | Task runner: scheduling, cancellation, caching, event stream, result assembly (`schema_id="ici.next.run"`). |
| `reporting/` | Offline HTML page, SARIF, view models — reporters never re-analyse. |
| `cli/` + `__main__.py` | Typer surface. `ici verify|plan|doctor|report|publish|diff|migrate|init`; `ici next …` is the same commands under an alias. |
| `core/` | Shared substrate kept by both paths: process runner, env, compile-db readers, path utilities. Not a stable namespace. |

## Contract points worth knowing

- **ici never builds.** `verify` reads declared build trees (`[builds.*]` in
  `ici.toml`); producing them is CI's job. A missing build tree is a blocked
  check, which the gate reports as `INCOMPLETE`, never `PASS`.
- **Gate order**: required-but-incomplete checks → `INCOMPLETE`; blocking
  measured findings → `FAIL`; otherwise `PASS`. Advisory checks
  (`required = false`) still measure and report — their findings never fail
  the gate.
- **Offline**: `verify`/`plan`/`doctor`/`report` open no sockets and install
  nothing. The only network paths are `publish` (`application/publish.py`)
  and the GHES adapter — both opt-in.
- **Interpreters**: a component's `[components.*.python] executable` is
  anchored at the workspace root and checked. `sys.executable` is never a
  project-interpreter fallback.
