# ici user guide

ici is an offline, fail-closed quality gate for Python and C++/Qt projects.
It measures what a workspace declares and refuses to call a run `PASS` when
required checks did not complete — partial evidence reports `INCOMPLETE`.

## Install & run

The distribution is a single `ici.pyz` (system Python ≥ 3.10) or a
self-contained bundle. From a source checkout: `python -m ici …`.

```
ici doctor     # what the scope's checks need, and whether each is available
ici plan       # what a run would do — starts nothing, builds nothing
ici verify     # run the selected checks; writes .ici/next/result.json
ici report     # render the saved result (offline HTML, optional SARIF)
```

`ici next …` spells the same commands and stays as a compatibility alias.

## Configuration — `ici.toml` (schema v1)

```toml
schema_version = 1

[workspace]
name = "myproject"
profile = "standard"          # fast | standard | deep

[[components]]
id = "app"
root = "."
languages = ["python"]
sources = ["src/**/*.py"]

[components.python]
executable = ".venv/bin/python"   # workspace-relative, verified — never falls
                                  # back to the interpreter running ici
test_tools = "project"
test_paths = ["tests"]
```

C++ components declare a build tree — **ici never builds**; CI owns it:

```toml
[builds.main]
system = "cmake"
project = "CMakeLists.txt"
directory = "build/main"
variant = "default"
prepare = "explicit"
artifacts = ["icirv"]

[[components]]
id = "viewer"
languages = ["cpp"]
build = "main"
sources = ["src/**/*.cpp", "src/**/*.h"]
```

### Check policy

```toml
# Advisory: still measured and reported, findings never fail the gate.
[checks."python.dup"]
required = false
```

`[[suppressions]]` carries a mandatory `reason`. A component may not relax a
check the root required — relaxation is a root decision with a named
exemption (`docs/design/ici-next/config-origin.md`).

## Commands

| Command | Purpose |
|---|---|
| `ici init` | Write a starter `ici.toml` (`--preview`, `--force`) |
| `ici migrate [src]` | Preview a stable config as next schema (`--write` keeps `ici.toml.stable`) |
| `ici doctor` | Tool/interpreter availability per selected check |
| `ici plan` | Selected checks, resolved tools, blocked reasons (`--require-full` makes partial scope a failure) |
| `ici verify` | Execute; `--result` path, `--baseline` compare, `--no-cache`, `--events` event stream, `--profile`, `--python`/`--cpp`/`--component` scope |
| `ici report` | `--result` in, `--out` HTML, `--sarif` file |
| `ici diff <baseline>` | New/resolved/carried findings between two stored results |
| `ici publish` | Push the result's page to the configured backend (gh-pages / GHES); the only network path besides the GHES adapter |

Exit codes: `0` pass · `1` violations (`FAIL`) · `2` request rejected
(no workspace, unreadable config) · `3` `INCOMPLETE` — required checks could
not run or did not finish.

## Reading a result

`.ici/next/result.json` carries `schema_id="ici.next.run"` and keeps five
axes separate: execution completion, evidence level, selected scope, code
findings, publication status. `metrics[]` hold measured values
(`coverage.lines`, `pytest.cases`, `tem.<component>`, `duplicated_lines`);
`limitations[]` hold what could not be judged. A limitation is reported —
never silently graded.

## Offline guarantees

`verify`/`plan`/`doctor`/`report` open no sockets and run no installer. When
running from a bundle (`ICI_BUNDLE_ROOT`), analyzers resolve to the bundle's
copies only — a tool that is absent reports `UNAVAILABLE` instead of falling
back to whatever PATH happens to hold.
