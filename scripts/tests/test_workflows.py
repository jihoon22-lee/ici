"""Shape of the CI, candidate and release workflows after the R9 Rust cutover.

Ported from ``tests/test_purity.py`` when the Python product tests were removed.
Kept: the permission split, the trusted-base publication path, the Merge Gate
needs, credential handling, immutable action pins, manual-only candidate and
release workflows, and the native release guards. Dropped with what they
checked: the ``src/`` banned-import walk (the Python product is gone; Ruff's
banned-api still covers the harness) and every pyz build, launcher, smoke,
package and candidate-bundle assertion.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# Publishing can be spelled as `publish` or `verify --publish`; the security
# assertions track the behaviour rather than one spelling.
PUBLISH_INVOCATION = re.compile(r"ici\"? (?:verify [^\n]*--publish|publish\b)")

EXPECTED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
    "softprops/action-gh-release": "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
}


def workflow(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def all_workflows() -> list[Path]:
    # Every workflow, not a list written down here: the one that is forgotten
    # is the one that would carry the unpinned action.
    return sorted(WORKFLOW_DIR.glob("*.yml"))


def job_block(text: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise AssertionError(f"workflow job not found: {job_name}")
    return match.group("body")


def step_block(job: str, step_name: str) -> str:
    match = re.search(
        rf"(?ms)^      - name: {re.escape(step_name)}\n(?P<body>.*?)(?=^      - name:|\Z)",
        job,
    )
    if match is None:
        raise AssertionError(f"workflow step not found: {step_name}")
    return match.group("body")


def step_blocks(text: str) -> list[str]:
    return re.findall(r"(?ms)^      - (?:name|uses):.*?(?=^      - (?:name|uses):|^  \S|\Z)", text)


def permissions(job: str) -> set[str]:
    match = re.search(r"(?ms)^    permissions:\n(?P<body>(?:      [^\n]+\n)+)", job)
    if match is None:
        raise AssertionError("job has no permissions block")
    return {line.split("#", 1)[0].strip() for line in match.group("body").splitlines()} - {""}


def event_block(text: str) -> str:
    return text.split("\n\npermissions:", 1)[0]


class WorkflowSetTest(unittest.TestCase):
    def test_no_early_exit_grep_reads_a_streamed_file_under_pipefail(self) -> None:
        # `producer | grep -q` makes the producer die of SIGPIPE when grep stops at the first match,
        # and pipefail turns that into a failure; for a large file (the CHANGELOG release section
        # check) the release job then refuses a release that has the section.
        streamed = re.compile(r"(?:git show|cat|curl|gh api)[^\n|]*\|\s*grep\s+-[A-Za-z]*q")
        for path in all_workflows():
            with self.subTest(workflow=path.name):
                self.assertIsNone(streamed.search(path.read_text(encoding="utf-8")))

    def test_only_the_native_workflows_remain(self) -> None:
        names = {path.name for path in all_workflows()}
        self.assertEqual(
            names,
            {"ci.yml", "candidate-artifact.yml", "candidate-quality-zoo.yml", "release-native.yml"},
        )

    def test_no_workflow_references_the_python_product_build(self) -> None:
        for path in all_workflows():
            text = path.read_text(encoding="utf-8")
            for marker in ("dist/ici.pyz", "build-pyz.sh", "launcher.sh", "candidate_bundle.py"):
                self.assertNotIn(marker, text, f"{path.name} still references {marker}")

    def test_every_action_is_an_immutable_allowlisted_pin(self) -> None:
        for path in all_workflows():
            text = path.read_text(encoding="utf-8")
            for uses in re.findall(r"(?m)^\s+uses:\s*(\S+)", text):
                action, _, ref = uses.partition("@")
                self.assertIn(action, EXPECTED_ACTIONS, f"unapproved action in {path.name}: {uses}")
                self.assertRegex(ref, r"^[0-9a-f]{40}$", f"mutable ref in {path.name}: {uses}")
                self.assertEqual(ref, EXPECTED_ACTIONS[action], f"{path.name}: {uses}")
            self.assertNotIn("node20", text.lower())
            self.assertIsNone(re.search(r"@[vV][0-9]+(?:\.[0-9]+){0,2}\b", text))

    def test_every_checkout_disables_credential_persistence(self) -> None:
        for path in all_workflows():
            text = path.read_text(encoding="utf-8")
            checkouts = [block for block in step_blocks(text) if "actions/checkout@" in block]
            self.assertTrue(checkouts, f"no checkout step in {path.name}")
            for block in checkouts:
                self.assertIn("persist-credentials: false", block, path.name)

    def test_every_setup_uv_step_pins_the_tool_version(self) -> None:
        found = 0
        for path in all_workflows():
            for block in step_blocks(path.read_text(encoding="utf-8")):
                if "astral-sh/setup-uv@" in block:
                    found += 1
                    self.assertIn('version: "0.12.5"', block, path.name)
        self.assertTrue(found, "no setup-uv step found in any workflow")

    def test_top_level_permissions_are_empty_outside_ci(self) -> None:
        for name in ("candidate-artifact.yml", "candidate-quality-zoo.yml", "release-native.yml"):
            self.assertIn("permissions: {}", workflow(name).split("jobs:", 1)[0], name)


class CiWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = workflow("ci.yml")

    def test_permissions_are_read_only_except_the_publish_jobs(self) -> None:
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read\n")
        self.assertIsNone(
            re.search(r"(?m)^  (?:contents|pull-requests|issues|checks): write\n", self.text)
        )
        for name in ("verify", "rust", "viewer-gui"):
            job = job_block(self.text, name)
            self.assertEqual(permissions(job), {"contents: read"}, name)
            self.assertIsNone(re.search(r"(?m)^\s+[a-z-]+:\s*write(?:-all)?\s*$", job), name)
        self.assertEqual(
            permissions(job_block(self.text, "publish-main")), {"contents: write", "pages: read"}
        )
        self.assertEqual(
            permissions(job_block(self.text, "report-pr")),
            {"contents: write", "pull-requests: write", "pages: read"},
        )
        self.assertIn("permissions: {}", job_block(self.text, "merge-gate"))

    def test_tokens_and_publication_stay_in_the_publish_jobs(self) -> None:
        privileged = job_block(self.text, "publish-main") + job_block(self.text, "report-pr")
        calls = PUBLISH_INVOCATION.findall(self.text)
        self.assertTrue(calls)
        self.assertEqual(len(calls), len(PUBLISH_INVOCATION.findall(privileged)))
        self.assertEqual(self.text.count("GITHUB_TOKEN"), privileged.count("GITHUB_TOKEN"))
        self.assertEqual(self.text.count("github.token"), 0)
        verify = job_block(self.text, "verify")
        self.assertNotIn("--publish", verify)
        self.assertNotIn("GITHUB_TOKEN", verify)

    def test_verify_builds_and_dogfoods_the_native_binary(self) -> None:
        verify = job_block(self.text, "verify")
        build = step_block(verify, "Build the native ici")
        self.assertIn("cargo build --release --locked -p ici-cli", build)
        self.assertIn('"$ici" --version', build)
        root = step_block(verify, "🐶 Dogfooding — Self-Verification Gate via native ici")
        self.assertIn('"$ICI_BIN" verify --report --html verify_report.html --github-summary', root)
        static = step_block(
            verify, "🐶 Dogfooding — static CLI artifact contract via native ici (viewer/)"
        )
        self.assertIn("working-directory: viewer", static)
        self.assertIn("ICI_CONFIG: ici-static-cli.toml", static)
        self.assertIn('"$ICI_BIN" verify --profile deep --report', static)
        cpp = step_block(verify, "🐶 Dogfooding — C++ Gate via native ici (viewer/)")
        self.assertIn("working-directory: viewer", cpp)
        self.assertIn('"$ICI_BIN" verify --report --html verify_report.html --github-summary', cpp)
        self.assertLess(verify.index("Build the native ici"), verify.index("Self-Verification"))

    def test_verify_keeps_the_harness_suites_and_ruff(self) -> None:
        verify = job_block(self.text, "verify")
        self.assertIn("uv run --frozen --python 3.10 ruff check .", verify)
        self.assertIn("uv run --frozen --python 3.10 ruff format --check .", verify)
        self.assertIn("-m unittest discover -s scripts/tests", verify)
        quality_zoo = step_block(verify, "Quality Zoo corpus unit tests")
        self.assertIn("working-directory: quality-zoo", quality_zoo)
        self.assertIn("python3.10 -m unittest discover -s tests", quality_zoo)

    def test_verify_uploads_the_reports(self) -> None:
        upload = step_block(job_block(self.text, "verify"), "Upload Verification Reports")
        self.assertIn("actions/upload-artifact@", upload)
        self.assertIn("name: ici-verification-report", upload)
        for path in (
            "verify_report.html",
            "verify_report.json",
            "viewer/verify_report.html",
            "viewer/verify_report.json",
            "viewer/static_cli_report.json",
        ):
            self.assertIn(path, upload)

    def test_publish_jobs_build_on_trusted_code_and_consume_only_the_artifact(self) -> None:
        report = job_block(self.text, "report-pr")
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", report)
        self.assertIn("always() && github.event_name == 'pull_request'", report)
        main = job_block(self.text, "publish-main")
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", main)
        for job in (report, main):
            self.assertIn("needs: [verify, viewer-gui]", job)
            self.assertNotIn("github.event.pull_request.head", job)
            self.assertIn("cargo build --release --locked -p ici-cli", job)
            self.assertIn("actions/download-artifact@", job)
            self.assertIn("name: ici-verification-report", job)
            # The binary leaves the checkout before the artifact is unpacked into it.
            self.assertLess(job.index("ici-trusted/ici"), job.index("actions/download-artifact@"))
            self.assertIn(
                '"$RUNNER_TEMP/ici-trusted/ici" publish --report-dir ici=. --report-dir viewer',
                job,
            )
        for fragment in (
            "Verify Sticky Comment & Published HTML",
            "<!-- ici-report -->",
            'EXPECTED_REPORTS: "2"',
            "for page in range(1, 21)",
            "for attempt in range(120)",
        ):
            self.assertIn(fragment, report)

    def test_pre_cutover_base_guard_never_runs_pr_head_code(self) -> None:
        guard_step = "Check the Trusted Base Has the Rust Product"
        condition = "if: steps.base.outputs.rust == 'true'"
        for name in ("report-pr", "publish-main"):
            job = job_block(self.text, name)
            guard = step_block(job, guard_step)
            self.assertIn("id: base", guard)
            self.assertIn("if [ -f crates/ici-cli/Cargo.toml ]; then", guard)
            self.assertIn("::notice::The trusted base predates the Rust product", guard)
            # The guard only inspects the trusted checkout: it builds, runs, fetches
            # and checks out nothing.
            for forbidden in ("cargo", "ici ", "uses:", "github.event.pull_request.head", "git "):
                self.assertNotIn(forbidden, guard, name)
            # Exactly one checkout per job, and it is the trusted ref, before the guard.
            checkouts = [block for block in step_blocks(job) if "actions/checkout@" in block]
            self.assertEqual(len(checkouts), 1, name)
            self.assertNotIn("github.event.pull_request.head", job)
            self.assertNotIn("github.head_ref", job)
            self.assertLess(job.index("actions/checkout@"), job.index(guard_step))
            # Every later step is gated on the guard.
            later = job[job.index(guard_step) :]
            steps = step_blocks(later)[1:]
            self.assertTrue(steps, name)
            for block in steps:
                self.assertIn(condition, block, f"{name}: ungated step after the guard")
        self.assertIn(
            "ref: ${{ github.event.pull_request.base.sha }}",
            step_blocks(job_block(self.text, "report-pr"))[0],
        )

    def test_merge_gate_requires_every_quality_job(self) -> None:
        gate = job_block(self.text, "merge-gate")
        self.assertIn("if: ${{ always() }}", gate)
        self.assertIn("needs: [verify, rust, viewer-gui, report-pr]", gate)
        for name, variable in (
            ("verify", "VERIFY_RESULT"),
            ("rust", "RUST_RESULT"),
            ("viewer-gui", "VIEWER_GUI_RESULT"),
            ("report-pr", "REPORT_RESULT"),
        ):
            self.assertIn(f"{variable}: ${{{{ needs.{name}.result }}}}", gate)
            self.assertIn(f'test "${variable}" = success', gate)

    def test_rust_job_runs_the_product_gate_and_the_ledger(self) -> None:
        rust = job_block(self.text, "rust")
        for command in (
            "cargo fmt --all -- --check",
            "cargo clippy --workspace --all-targets --locked -- -D warnings",
            "cargo test --workspace --locked",
            "python3 scripts/rust_migration_inventory.py --self-test",
            "python3 scripts/rust_migration_inventory.py --check --require-closed",
            "uv sync --frozen --python 3.10",
        ):
            self.assertIn(command, rust)


class CandidateWorkflowsTest(unittest.TestCase):
    def test_candidate_artifact_is_manual_and_requires_an_exact_target(self) -> None:
        text = workflow("candidate-artifact.yml")
        events = event_block(text)
        self.assertIsNone(re.search(r"(?m)^  (?!workflow_dispatch:)[A-Za-z0-9_-]+:", events))
        target = events.split("      target_sha:\n", 1)[1]
        self.assertRegex(target, r"(?m)^        required: true$")
        self.assertIsNone(re.search(r"(?m)^        default:", target))
        self.assertEqual(
            set(re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\n", text.split("jobs:", 1)[1])),
            {"validate", "native"},
        )

    def test_candidate_validate_is_read_only_and_main_bound(self) -> None:
        validate = job_block(workflow("candidate-artifact.yml"), "validate")
        self.assertEqual(permissions(validate), {"actions: read", "checks: read", "contents: read"})
        script = step_block(validate, "Validate Exact Main Commit and Merge Gate").split(
            "        run: |\n", 1
        )[1]
        self.assertNotIn("${{ inputs.target_sha }}", script)
        for fragment in (
            'test "$GITHUB_REF" = "refs/heads/main"',
            '[ "$REQUESTED_TARGET_SHA" != "$GITHUB_SHA" ]',
            'git merge-base --is-ancestor "$REQUESTED_TARGET_SHA" refs/remotes/origin/main',
            "python3 scripts/candidate_merge_gate.py page-count",
            "python3 scripts/candidate_merge_gate.py select-pages",
            "python3 scripts/candidate_merge_gate.py verify-job",
        ):
            self.assertIn(fragment, script)
        self.assertNotIn("gh api --paginate", script)

    def test_native_candidate_builds_twice_without_credentials(self) -> None:
        native = job_block(workflow("candidate-artifact.yml"), "native")
        self.assertEqual(permissions(native), {"contents: read"})
        self.assertIn("ref: ${{ needs.validate.outputs.target_sha }}", native)
        self.assertIn("for build in a b; do", native)
        self.assertIn('--compare "$RUNNER_TEMP/native-b/$name"', native)
        self.assertIn("tests/rust-parity/distribution/verify_artifact.py", native)
        token_lines = [
            line.strip()
            for line in native.splitlines()
            if re.search(r"\b(?:GH_TOKEN|GITHUB_TOKEN|ACTIONS_\w+TOKEN)\b", line)
        ]
        self.assertTrue(token_lines)
        self.assertTrue(all(line.startswith("unset ") for line in token_lines))

    def test_quality_zoo_is_manual_native_only_and_read_only(self) -> None:
        text = workflow("candidate-quality-zoo.yml")
        events = event_block(text)
        self.assertIsNone(re.search(r"(?m)^  (?!workflow_dispatch:)[A-Za-z0-9_-]+:", events))
        self.assertNotIn("candidate_format", text)
        self.assertEqual(
            set(re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\n", text.split("jobs:", 1)[1])),
            {"accept-native"},
        )
        accept = job_block(text, "accept-native")
        self.assertNotRegex(accept, r"(?m)^    if:")
        self.assertEqual(
            permissions(accept),
            {"actions: read", "checks: read", "contents: read", "pull-requests: read"},
        )
        for forbidden in ("--publish", "<!-- ici-report -->", "pull-requests: write", "pages:"):
            self.assertNotIn(forbidden, text)
        for step in (
            "Intake and Bind Native Candidate without Credentials",
            "Run Quality Zoo against the Native Candidate",
        ):
            body = step_block(accept, step)
            self.assertNotIn("GH_TOKEN:", body)
            self.assertIn("unset GH_TOKEN GITHUB_TOKEN ACTIONS_RUNTIME_TOKEN", body)
        self.assertIn(
            'payload.get("contract_verdict") != "PASS"',
            step_block(accept, "Run Quality Zoo against the Native Candidate"),
        )


class ReleaseNativeWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = workflow("release-native.yml")

    def test_release_is_manual_only(self) -> None:
        events = event_block(self.text)
        self.assertIsNone(re.search(r"(?m)^  (?!workflow_dispatch:)[A-Za-z0-9_-]+:", events))
        self.assertNotIn("tags:", events)

    def test_release_guards_the_tag_version_and_cutover_state(self) -> None:
        validate = job_block(self.text, "validate")
        for fragment in (
            "grep -Eq '^v[0-9]+\\.[0-9]+\\.[0-9]+$'",
            'git cat-file -e "$TARGET_SHA:src/ici"',
            'test "$TAG" = "v$VERSION"',
            'grep -Fq "## [$VERSION]"',
            'select(.name == "Merge Gate")',
            '.path == ".github/workflows/candidate-artifact.yml"',
            'git cat-file -e "$TARGET_SHA:docs/release-notes/$VERSION.md"',
        ):
            self.assertIn(fragment, validate)

    def test_release_body_is_the_concise_notes_file_with_checksums(self) -> None:
        publish = job_block(self.text, "publish")
        self.assertIn("sparse-checkout: docs/release-notes", publish)
        self.assertIn('notes="docs/release-notes/$VERSION.md"', publish)
        self.assertIn('test "$chars" -le 8192', publish)
        self.assertIn("### SHA-256", publish)
        self.assertIn("body_path: RELEASE_NOTES.md", publish)
        self.assertNotIn("CHANGELOG.md", publish)
        version = re.search(
            r'(?m)^version = "([^"]+)"$',
            (WORKFLOW_DIR.parents[1] / "Cargo.toml").read_text(encoding="utf-8"),
        ).group(1)
        notes = WORKFLOW_DIR.parents[1] / "docs" / "release-notes" / f"{version}.md"
        self.assertTrue(notes.is_file(), notes)
        # Leaves room for the appended checksum block under the 8192 limit.
        self.assertLess(len(notes.read_text(encoding="utf-8")), 7000)

    def test_only_publish_can_write_and_it_never_builds(self) -> None:
        self.assertEqual(
            permissions(job_block(self.text, "validate")),
            {"actions: read", "checks: read", "contents: read"},
        )
        self.assertEqual(
            permissions(job_block(self.text, "verify")), {"actions: read", "contents: read"}
        )
        self.assertEqual(permissions(job_block(self.text, "viewer")), {"contents: read"})
        publish = job_block(self.text, "publish")
        self.assertEqual(permissions(publish), {"contents: write"})
        self.assertIn("needs: [validate, verify, viewer]", publish)
        for forbidden in ("cargo ", "cmake ", "build-native.sh"):
            self.assertNotIn(forbidden, publish)
        self.assertIn("fail_on_unmatched_files: true", publish)
        self.assertIn("sha256sum --check --strict", publish)

    def test_release_checks_run_without_credentials(self) -> None:
        verify = job_block(self.text, "verify")
        checks = step_block(verify, "Run Native Release Checks without Credentials")
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN ACTIONS_RUNTIME_TOKEN", checks)
        self.assertIn("scripts/check-native-release.sh --archive", checks)
        self.assertNotIn("--allow-prerelease-version", checks)

    def test_viewer_assets_come_from_the_validated_commit(self) -> None:
        viewer = job_block(self.text, "viewer")
        self.assertIn("ref: ${{ needs.validate.outputs.target_sha }}", viewer)
        self.assertIn("-DICIRV_BUILD_GUI=OFF", viewer)
        self.assertIn("not statically linked", viewer)
        publish = job_block(self.text, "publish")
        for asset in ("icirv", "icirv-gui", "icirv-gui.README.txt", "viewer-assets.sha256"):
            self.assertIn(f"viewer-release-assets/{asset}\n", publish)


if __name__ == "__main__":
    unittest.main()
