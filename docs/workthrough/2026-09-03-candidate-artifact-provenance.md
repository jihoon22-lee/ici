# Candidate Artifact Provenance — 2026-09-03

## Overview

This workthrough records the candidate-artifact producer contract currently present in the ici
worktree. It is a non-release path for producing a short-lived `ici.pyz` bundle from an exact
protected-main dispatch commit. The producer slice, its local contract, and its remote producer
evidence are complete; consumer injection and quality-zoo acceptance remain pending.

## Context

Stable `v0.10.2` remains the released ici artifact and its version, tag, and release are not
changed by this path. The normal toy-project PR gate must continue using the released `v0.10.2`.
The manifest's `package_version` is read from the selected target commit; it may differ from the
stable version in a future candidate, but it never authorizes a version bump, tag, or release.
Candidate validation is intentionally a separate manual workflow so a candidate can be consumed by
a later quality-zoo runner without replacing the normal gate.

## Changes Made

### Files changed

The implementation and documentation changes covered these repository-root paths:

- `.github/workflows/candidate-artifact.yml`
- `scripts/candidate_bundle.py`
- `scripts/candidate_merge_gate.py`
- `tests/test_candidate_bundle.py`
- `tests/test_candidate_merge_gate.py`
- `tests/test_purity.py`
- `CHANGELOG.md`
- `README.md`
- `docs/superpowers/2026-08-30-handover.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `docs/workthrough/2026-09-03-candidate-artifact-provenance.md`

### Main-only workflow and exact dispatch commit

`.github/workflows/candidate-artifact.yml` declares only `workflow_dispatch` and requires
`target_sha`. Runtime guards require repository `jihoon22-lee/ici`, `refs/heads/main`, trusted
workflow `HEAD == GITHUB_SHA`, and a full lowercase 40-character SHA. The requested target must
equal that `GITHUB_SHA`, ensuring that the helper and workflow contract come from the same protected
commit. The workflow also proves the target is an exact commit and that it satisfies
`git merge-base --is-ancestor target_sha refs/remotes/origin/main`, then the build job checks out
that exact SHA.

### Merge Gate provenance auditor

`scripts/candidate_merge_gate.py` reads the first Checks page, derives the exact required page
count, and refuses more than ten pages before the workflow fetches the remainder. Each explicit
request asks for at most 100 checks, and each response is parsed with a 20 MB file and bounded JSON
shape contract. It selects the largest check-run ID matching `Merge Gate`, the target SHA, and the
GitHub Actions app. A newest exact check that is not completed/successful fails closed; an older
successful check is never used as a fallback. Its canonical details URL supplies both the
`workflow_run_id` and `workflow_job_id`. The workflow independently fetches the run from the
Actions Runs API and the job from the Actions Jobs API. The run must be the successful
`CI Quality Gate (Dogfooding)` `push` run on `main`, with exact target SHA, positive run attempt,
canonical run URL, and matching repository/head repository. The job must bind the selected check
and run attempt while matching job/run IDs, target SHA, `Merge Gate` name, workflow name, main
branch, completed/success status, and canonical job/run/check URLs.

### Reproducible build and smoke

The target checkout runs `scripts/verify-reproducibility.sh`, which builds twice, compares the
`dist/ici.pyz` SHA-256 values, and rejects any git source-status mutation. `scripts/smoke.sh`
checks standalone version/help, doctor, shell environment generation, report generation, and
rejects external executable/display asset references covered by its `src`, stylesheet `href`, and
CSS `url(...)` patterns in the HTML report. The smoke script permits a verify
finding exit status when the report is still generated, but fails if the report is missing or
violates the Zero-CDN check.

The validate job has only Actions/Checks/Contents read permissions. The build job has Contents read
only; before candidate-controlled build and bundle commands it unsets GitHub/publication tokens,
and all checkouts disable credential persistence. The producer has no tag, release, Pages, or PR
comment write authority.

### Exact bundle and provenance

The upload directory contract contains exactly:

- `ici.pyz`, a descriptor-checked copy of the built pyz with executable mode `0755`.
- `ici.pyz.sha256`, containing `<sha256>  ici.pyz` and a final newline.
- `candidate-provenance.json`, canonical UTF-8 JSON with a final newline.

The manifest is schema `ici.candidate/v1`, channel `candidate`, and `stable: false`. This remains a
candidate-only contract rather than a stable-release compatibility promise. Its exact fields are
`schema`, `channel`, `stable`,
`repository`, `target_sha`, `package_version`, `candidate_workflow`,
`candidate_workflow_definition_sha`, `candidate_run_id`, `candidate_run_attempt`,
`merge_gate_check_run_id`, `merge_gate_job_id`, `merge_gate_run_id`, `merge_gate_run_attempt`,
`merge_gate_job_url`, `merge_gate_url`, `artifact_file`, `artifact_file_sha256`,
`artifact_file_size`, and `retention_days`. The helper
bounds a non-empty source pyz at 64 MiB and metadata at 64 KiB, rejects
symlinks/non-regular files/pre-existing output/source mutation, and requires the exact three-file
directory. Actions upload is configured with the target SHA in its name, `overwrite: false`,
compression level 0, and 14-day retention. Artifact ID, digest, and authenticated download URL
are recorded in the Actions step summary rather than added to the three-file bundle.

The remote v7 archive audit below confirms that the uploaded ZIP preserved these required modes;
there was no mode-loss exception for this artifact. The consumer must still verify the modes after
download rather than assuming any archive implementation's behavior.

## Remote producer evidence

The producer was dispatched from protected `main` with exact source SHA
`7872a7b80899cbd3d40d92d18e7920cd7e2283e7`. The remote evidence is complete for the producer
boundary, but it does not close candidate consumer or quality-zoo acceptance.

### Main source and report evidence

[Main run `33688279264`](https://github.com/jihoon22-lee/ici/actions/runs/33688279264) was successful
with every required job green. Its selected Merge Gate was check/job `100442919168`, run attempt 1.
Independent [ici main Pages](https://jihoon22-lee.github.io/ici/ici/main/) and [viewer main Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
matched the corresponding extracted main artifact bytes, retained the exact source SHA, used the
correct titles `ici Verification Report — ici` and `ici Verification Report — viewer`, and passed
the Zero-CDN audit.

The independently verified check-to-job-to-run identity chain was:

| Object | Bound identity and canonical URL |
| --- | --- |
| Check run | ID `100442919168`, target SHA above, [Checks API URL](https://api.github.com/repos/jihoon22-lee/ici/check-runs/100442919168) |
| Workflow job | ID `100442919168`, `run_id=33688279264`, `run_attempt=1`, name `Merge Gate`, `workflow_name=CI Quality Gate (Dogfooding)`, `head_branch=main`, [job HTML URL](https://github.com/jihoon22-lee/ici/actions/runs/33688279264/job/100442919168), [job API URL](https://api.github.com/repos/jihoon22-lee/ici/actions/jobs/100442919168) |
| Workflow run | ID `33688279264`, `run_attempt=1`, `name=CI Quality Gate (Dogfooding)`, `head_branch=main`, [run HTML URL](https://github.com/jihoon22-lee/ici/actions/runs/33688279264), [run API URL](https://api.github.com/repos/jihoon22-lee/ici/actions/runs/33688279264) |

The job's canonical `run_url` matched the run API URL and its canonical `check_run_url` matched the
Checks API URL; all check/job/run IDs, attempts, target SHA, workflow name, branch, status/conclusion,
and canonical URLs matched independently fetched API responses.

### Candidate run and archive evidence

[Candidate run `33689056008`](https://github.com/jihoon22-lee/ici/actions/runs/33689056008) completed
successfully. It published [artifact ID `9869395069`](https://github.com/jihoon22-lee/ici/actions/artifacts/9869395069)
with name `ici-candidate-7872a7b80899cbd3d40d92d18e7920cd7e2283e7`. The [artifact API metadata](https://api.github.com/repos/jihoon22-lee/ici/actions/artifacts/9869395069)
and the authenticated [raw ZIP download](https://api.github.com/repos/jihoon22-lee/ici/actions/artifacts/9869395069/zip)
have digest `sha256:640e50ecf5b099174c16f1ef5d2b5b87945329711e96f926d94c3cc04109081e`, ZIP size
`2,277,109` bytes, and expiry `2026-09-16T22:14:38Z`.

The downloaded v7 ZIP contained exactly these three regular files:

| Entry | Mode | Bytes |
| --- | ---: | ---: |
| `candidate-provenance.json` | `0644` | 859 |
| `ici.pyz.sha256` | `0644` | 74 |
| `ici.pyz` | `0755` | 2,275,786 |

The pyz SHA-256 was
`53fc75f0a073a74689babfe9ef8a4b2378995002d7d563bdc52da548fdbb9ee8`, and the bundled executable
reported `ici 0.10.2`. The candidate manifest bytes matched the independent verifier's canonical
output. The observed v7 ZIP preserved all three required modes, correcting the earlier generic
assumption that an upload ZIP loses executable/file modes.

### Sticky-comment boundary

The candidate workflow does not publish reports or comments. The existing `ci.yml` PR path remains
the sole `<!-- ici-report -->` upsert path. If quality-zoo output is later surfaced there, it must
be an additional section/link in that same body and retain exactly one sticky comment/marker.

## Code Examples

The producer input is a full SHA, not a branch or abbreviated revision:

```text
# .github/workflows/candidate-artifact.yml
workflow_dispatch(target_sha=<40 lowercase hex characters>)
target_sha == candidate_workflow_definition_sha == GITHUB_SHA
target_sha ∈ ancestry(refs/remotes/origin/main)
```

The Merge Gate selection and independent run/job checks are equivalent to:

```text
# scripts/candidate_merge_gate.py
check.name == "Merge Gate"
check.head_sha == target_sha
check.app.slug == "github-actions"
newest_check = max(check.id)
newest_check.status == "completed" and newest_check.conclusion == "success"

run.name == "CI Quality Gate (Dogfooding)"
run.path == ".github/workflows/ci.yml"
run.event == "push" and run.head_branch == "main"
run.head_sha == target_sha
run.status == "completed" and run.conclusion == "success"
verified_run.run_attempt == merge_gate_run_attempt

job.id == merge_gate_job_id
job.run_id == merge_gate_run_id
job.run_attempt == merge_gate_run_attempt
job.name == "Merge Gate"
job.workflow_name == "CI Quality Gate (Dogfooding)"
job.head_branch == "main" and job.head_sha == target_sha
job.status == "completed" and job.conclusion == "success"
job.html_url == canonical_job_url
job.url == canonical_job_api_url
job.run_url == canonical_run_api_url
job.check_run_url == canonical_check_run_api_url
```

The bundle boundary is:

```text
# .github/workflows/candidate-artifact.yml
ici.pyz
ici.pyz.sha256
candidate-provenance.json
```

The provenance field addition and API split are the key before/after contract changes:

```text
# scripts/candidate_merge_gate.py (before → after)
details_url → workflow_run_id
details_url → workflow_run_id + workflow_job_id
one workflow-run verification → independent run verification + verify-job binding

# scripts/candidate_bundle.py (before → after)
... merge_gate_check_run_id, merge_gate_run_id, ...
... merge_gate_check_run_id, merge_gate_job_id, merge_gate_run_id, ...
```

## Verification Results

The bounded local evidence currently is:

```text
# Focused regression suite
uv run --python 3.10 pytest -q tests/test_candidate_merge_gate.py \
  tests/test_candidate_bundle.py tests/test_purity.py
111 passed (41 Merge Gate, 44 bundle, 26 workflow-purity); exit code 0

# Workflow syntax
actionlint .github/workflows/candidate-artifact.yml
pass; exit code 0

# Live API provenance verifier
check→job→run/attempt binding, including canonical job/run/check URLs
pass; exit code 0

# Full Python 3.10 regression suite
.venv/bin/pytest -q
2,068 collected; 2,061 passed and 7 environment-dependent skipped; exit code 0

# Static and workflow checks
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
actionlint .github/workflows/*.yml
188 Python files formatted; 104 typed source files clean; exit code 0

# Reproducibility and standalone smoke
./scripts/verify-reproducibility.sh && ./scripts/smoke.sh
ici.pyz SHA-256 0d52861eef43a53e8b46deca397f5e75ce82b983e38c9f4085a86d0260dbece4
2,275,463 bytes; source status unchanged; Python 3.10 and Zero-CDN smoke passed

# Real built-pyz bundle round trip
candidate-provenance.json 0644 807 bytes
ici.pyz                    0755 2,275,463 bytes
ici.pyz.sha256             0644 74 bytes
create output == verify output; sidecar OK; bundled executable reports ici 0.10.2
```

The helper's `create` and `verify` subcommands match the workflow invocation; verification reopens
the exact three regular files through a directory descriptor and checks modes, bounded canonical
JSON, provenance types/constants, artifact size/hash, and the checksum sidecar. The remote producer
evidence in [Remote producer evidence](#remote-producer-evidence) records the independently checked
main and candidate runs, artifact ID/digest/expiry, exact ZIP entries, preserved modes, pyz digest,
manifest byte match, and canonical check/job/run API identities. Consumer injection and
quality-zoo acceptance are not yet evidenced.

## Next Steps

- Add the separate toy consumer/quality-zoo runner that verifies the candidate manifest before
  injecting the pyz by local path.
- Keep every toy PR's normal gate pinned to released `ici v0.10.2`; if quality-zoo output is shown
  in a PR, extend the existing single sticky comment rather than creating another one.
