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

## Layout

```text
manifest.json              stable known-answer slice (released-artifact contract)
candidate-manifest.json    full candidate set (adds deep/candidate-only scenarios)
runner/                    stdlib-only runner: run.py, candidate_intake.py, contract checks
tests/                     corpus unit tests (unittest; run from this directory)
scenarios/<lang>/<name>/
    ici.toml               scenario-local ici configuration (enabled engines, profile)
    scenario.json          schema-2 selector: ici artifact SHA-256 -> expectation file
    expectations/*.json    strict known answers, keyed by exact artifact digest
    src/, tests/           the miniature project under analysis
```

## The selector contract

`scenario.json` does not select expectations by version string. It maps the
exact SHA-256 of the `ici.pyz` being tested to one expectation file, so a
candidate and a released artifact can carry different known answers for the
same scenario and neither can silently reuse the other's. Adding a candidate
entry never rewrites the released manifest's answers.

## Running it

The candidate workflow drives the corpus; locally the same entry point is:

```sh
cd quality-zoo
ICI_PYTHON=python3.10 python3.10 -m runner.run \
    --manifest manifest.json \
    --ici-bin /path/to/ici.pyz \
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

## Hygiene note

`python.security-resource-correctness` embeds a credential-shaped assignment
on purpose — it is the bait the security engine must flag. The fixture register
marks it with `hygiene_allow = ["credential-shape"]`; every other hygiene
pattern still applies to it, and every pattern still applies to every other
scenario.
