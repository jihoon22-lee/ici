# Quality Zoo

Quality Zoo is ici's own known-answer corpus. Each scenario is a small project
that is deliberately defective (or deliberately clean), and ici must report the
expected finding at the expected location. A scenario is a test asset, not an
application to install or a product release.

The corpus was migrated into this repository from
`jihoon22-lee/toy-projects` (commit `195de9b`), where it previously lived so
that ici could be exercised against an external consumer. It is now owned here
per the [ici-next ownership plan](../docs/design/ici-next/quality-zoo-ownership.md),
and every scenario is a row in the [fixture register](../tests/fixtures/manifest.toml).

## The contract

Scenarios pin the `ici.next.run` result contract — the flat CLI's stored
verdict — not the legacy `ici.result/v3` engine reports the corpus was written
against. The legacy runner (`runner/run.py`) and its v3 expectation format were
removed with the stable cutover; `runner/next_contract.py` evaluates gate,
findings, limitations and metrics on the new shape.

## Layout

```text
manifest.next.json         the next-contract scenario registry
runner/                    stdlib-only runner: next_run.py, next_contract.py,
                           candidate_intake.py, common.py
tests/                     corpus unit tests (unittest; run from this directory)
scenarios/<lang>/<name>/
    ici.toml               scenario workspace config ([workspace]/[[components]])
    scenario.json          {"schema": 3, "expectation": "expectations/next.json"}
                           — or schema 2, digest-keyed for per-artifact answers
    expectations/next.json strict known answers against the ici.next.run shape
    src/, tests/           the miniature project under analysis
```

## Building is the scenario's job

`ici` never builds. A C++ scenario that needs artifacts or instrumented
binaries declares them in `expectations/*.json` under `prepare` — a list of
argv steps the runner executes in the sandboxed project copy *before* ici runs.
That mirrors how a consumer works: the project owns its build trees; ici reads
them.

## Running it

The candidate workflow drives the corpus; locally the same entry point is:

```sh
cd quality-zoo
ICI_BIN=/path/to/ici.pyz python3.10 -m runner.next_run \
    --manifest manifest.next.json \
    --output-dir /tmp/quality-zoo-results \
    --timeout-seconds 300
```

`runner.candidate_intake` verifies a candidate archive's provenance evidence
before any scenario runs; see `runner/candidate_intake.py`.

Corpus unit tests (they guard the runner and the manifest/disk consistency):

```sh
cd quality-zoo
python3.10 -m unittest discover -s tests -v
```

## Known gaps against the stable corpus

- `cpp.qt-missing-parent-constructor` is retained on disk but is not in
  `manifest.next.json`: the stable lint engine had a Qt lifetime convention
  rule, and no next check answers it — a compiler emits nothing for a missing
  `QObject` parent. Pinning "no findings" would be a vacuous known answer, so
  the scenario is parked until a `cpp` lifetime check exists.
- `cpp.quality-coverage` loses the per-file coverage floor findings the stable
  `test` engine reported. Next reports coverage as metrics, so the scenario
  pins `coverage.cpp.lines` bounds instead of per-file findings; aggregate
  floors live in `scripts/check_next_floors.py` for the repo's own gate.
- `cpp.static-build-context` no longer pins the include-cycle finding: the
  next include resolver reports the ambiguous headers as limitations rather
  than naming a cycle. The compiler-error findings it does pin are the ones
  stable also caught.

## Hygiene note

`python.security-resource-correctness` embeds a credential-shaped assignment
on purpose — it is the bait the security check must flag. The fixture register
marks it with `hygiene_allow = ["credential-shape"]`; every other hygiene
pattern still applies to it, and every pattern still applies to every other
scenario.
