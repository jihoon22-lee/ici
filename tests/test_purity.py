"""AST test to ensure pure Python compliance and no banned imports."""

import ast
import re
from pathlib import Path

BANNED_MODULES = {"tomllib", "requests", "httpx", "certifi"}


def test_no_banned_imports():
    src_dir = Path(__file__).resolve().parent.parent / "src"

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in BANNED_MODULES, (
                        f"Banned import '{root_mod}' in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                assert root_mod not in BANNED_MODULES, f"Banned import '{root_mod}' in {py_file}"


WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def _job_block(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"workflow job not found: {job_name}"
    return match.group("body")


def _step_block(job: str, step_name: str) -> str:
    match = re.search(
        rf"(?ms)^      - name: {re.escape(step_name)}\n(?P<body>.*?)(?=^      - name:|\Z)",
        job,
    )
    assert match is not None, f"workflow step not found: {step_name}"
    return match.group("body")


# Matches either way ici can be told to publish, so the security assertions
# below track the behaviour rather than one command-line spelling.
_PUBLISH_INVOCATION = re.compile(r"ici\.pyz (?:verify [^\n]*--publish|publish\b)")


def _uses_lines(workflow: str) -> list[str]:
    return re.findall(r"(?m)^\s+uses:\s*(\S+)", workflow)


def test_ci_permissions_are_read_only_except_trusted_publish_job():
    workflow = _workflow("ci.yml")
    assert re.search(r"(?m)^permissions:\n  contents: read\n", workflow)
    assert not re.search(r"(?m)^  (?:contents|pull-requests|issues|checks): write\n", workflow)

    verify = _job_block(workflow, "verify")
    assert re.search(r"(?m)^    permissions:\n      contents: read\n", verify)
    assert "write" not in verify
    assert "--publish" not in verify
    assert "GITHUB_TOKEN" not in verify
    assert "github.token" not in verify

    publish = _job_block(workflow, "publish-main")
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in publish
    assert re.search(r"(?m)^    needs: \[verify, viewer-gui\]\n", publish)
    assert re.search(r"(?m)^    permissions:\n      contents: write\n", publish)
    assert not re.search(r"(?m)^      (?:issues|pull-requests|checks):\s*write\n", publish)
    # Either publishing form counts. What this test guards is that publish-main
    # is the job doing it, that it is gated to main, and — the security-critical
    # part above — that it cannot comment. Pinning one CLI spelling made the
    # assertion break when publishing moved from `verify --publish` to
    # `publish --report-dir`, which says nothing about permissions.
    assert _PUBLISH_INVOCATION.search(publish)
    assert "GITHUB_TOKEN" in publish


def test_ci_verify_keeps_reports_summary_and_artifacts_without_publish():
    workflow = _workflow("ci.yml")
    verify = _job_block(workflow, "verify")
    assert "verify --report --html verify_report.html --github-summary" in verify
    assert "GITHUB_STEP_SUMMARY:" not in verify

    upload = re.search(
        r"(?ms)^      - name: Upload Verification Reports\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        verify,
    )
    assert upload is not None
    assert "actions/upload-artifact@" in upload.group("body")
    path = re.search(
        r"(?ms)^          path: \|\n(?P<body>(?:            [^\n]*\n)+)", upload.group("body")
    )
    assert path is not None
    uploaded = {line.strip() for line in path.group("body").splitlines() if line.strip()}
    # The repository-root reports must always ship. Additional reports (the
    # viewer/ C++ gate, and whatever comes next) are expected, so this asserts
    # the invariant rather than pinning the exact list — the property this test
    # guards is "verify uploads and does not publish", not the file count.
    assert {"verify_report.html", "verify_report.json"} <= uploaded


def test_ci_self_verification_persists_project_python_and_bin_path():
    """The standalone dogfood step must resolve the project's installed tools."""
    verify = _job_block(_workflow("ci.yml"), "verify")
    self_verify_marker = "- name: 🐶 Dogfooding — Self-Verification Gate via ici"
    self_verify_index = verify.index(self_verify_marker)
    setup_prefix = verify[:self_verify_index]

    project_python = 'project_python="$GITHUB_WORKSPACE/.venv/bin/python"'
    assert project_python in setup_prefix
    assert 'test -x "$project_python"' in setup_prefix
    assert 'printf \'ICI_PYTHON=%s\\n\' "$project_python" >> "$GITHUB_ENV"' in setup_prefix
    assert 'printf \'%s\\n\' "$(dirname "$project_python")" >> "$GITHUB_PATH"' in setup_prefix

    self_verify = verify[self_verify_index:]
    assert "dist/ici.pyz verify" in self_verify


def test_ci_builds_pyz_twice_and_rejects_project_mutation():
    verify = _job_block(_workflow("ci.yml"), "verify")
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify-reproducibility.sh"
    script = script_path.read_text(encoding="utf-8")

    assert "./scripts/verify-reproducibility.sh" in verify
    assert script.count("./scripts/build-pyz.sh") == 2
    assert "first_sha256=$(sha256sum dist/ici.pyz" in script
    assert "second_sha256=$(sha256sum dist/ici.pyz" in script
    assert "git status --porcelain=v1 --untracked-files=all" in script
    assert '[[ "$status_before" != "$status_after" ]]' in script
    assert "umask 077" in script
    assert "umask 002" in script
    assert "SOURCE_DATE_EPOCH=1" in script
    assert "SOURCE_DATE_EPOCH=4102444800" in script
    assert "ZipApp members do not use the canonical archive timestamp" in script


def test_pyz_build_is_hermetic_against_dependency_and_permission_drift():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build-pyz.sh").read_text(
        encoding="utf-8"
    )

    assert "readonly CANONICAL_SOURCE_DATE_EPOCH=1700000000" in script
    assert 'export SOURCE_DATE_EPOCH="$CANONICAL_SOURCE_DATE_EPOCH"' in script
    assert 'readonly EXPECTED_UV_VERSION="0.12.5"' in script
    assert "umask 022" in script
    assert script.count("uv export --quiet --frozen") == 2
    assert script.count("--require-hashes") == 2
    assert script.count("--link-mode copy") == 3
    assert script.count("--only-binary :all:") == 3
    assert "--only-group package" in script
    assert "--with shiv" not in script
    assert "path.chmod(0o644)" in script
    assert "path.chmod(0o755)" in script
    assert "symbolic link is not allowed in the ZipApp" in script
    assert '"$build_python" - "$SITE" <<\'PY\'' in script
    assert '"$build_python" - "$TOOLS" <<\'PY\'' in script
    assert '"$build_python" scripts/assemble_pyz.py' in script

    resolved_build = script.split('build_python="$(uv python find "$PY_TARGET")"', 1)[1]
    assert "python3" not in resolved_build

    published_checks = resolved_build.split('"$build_python" scripts/assemble_pyz.py', 1)[1]
    assert 'cmp -s "$OUT" "$ROOT/dist/ici"' in published_checks
    assert "stat" in published_checks
    assert re.search(r"(?:0o755|0755|755)", published_checks)


def test_pyz_build_uses_the_selected_python_for_ordered_shiv_bootstrap():
    """Keep the shiv ordering shim inside the hermetic helper interpreter."""
    root = Path(__file__).resolve().parents[1]
    build_script = (root / "scripts" / "build-pyz.sh").read_text(encoding="utf-8")
    wrapper = (root / "scripts" / "run_shiv.py").read_text(encoding="utf-8")

    assert 'PYTHONPATH="$TOOLS" "$build_python" scripts/run_shiv.py' in build_script
    assert 'PYTHONPATH="$TOOLS" "$build_python" -m shiv' not in build_script
    assert "resources.sort(key=lambda item: item[1])" in wrapper
    assert "builder.iter_package_files = _sorted_package_files" in wrapper
    assert 'runpy.run_module("shiv", run_name="__main__")' in wrapper


def test_reproducibility_verifier_rejects_duplicate_and_noncanonical_member_order():
    """Archive verification must pin entry order as well as metadata."""
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify-reproducibility.sh"
    ).read_text(encoding="utf-8")

    assert "names = [member.filename for member in members]" in script
    assert "if len(names) != len(set(names))" in script
    assert 'site_members = [name for name in names if name.startswith("site-packages/")]' in script
    assert (
        'bootstrap_members = [name for name in names if name.startswith("_bootstrap/")]' in script
    )
    assert "expected_order = sorted(site_members) + sorted(bootstrap_members) + [" in script
    assert '"environment.json",' in script
    assert '"__main__.py",' in script
    assert "if names != expected_order" in script


def test_every_setup_uv_step_pins_the_build_tool_version():
    for workflow_name in ("ci.yml", "release.yml", "candidate-artifact.yml"):
        workflow = _workflow(workflow_name)
        setup_steps = re.findall(
            r"(?ms)^      - name: Install uv\n(?P<body>.*?)(?=^      - name:|\Z)", workflow
        )
        assert setup_steps, f"no setup-uv step found in {workflow_name}"
        for step in setup_steps:
            assert "astral-sh/setup-uv@" in step
            assert 'version: "0.12.5"' in step


def test_pyz_build_requires_every_public_json_schema():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build-pyz.sh").read_text(
        encoding="utf-8"
    )

    assert "ici-result-v3.schema.json" in script
    assert "ici-compilation-export-v1.schema.json" in script
    assert 'if [ ! -f "$SITE/ici/schemas/$schema" ]' in script


def test_all_ci_release_and_candidate_checkouts_disable_credential_persistence():
    for workflow_name in (
        "ci.yml",
        "release.yml",
        "candidate-artifact.yml",
        "candidate-quality-zoo.yml",
    ):
        workflow = _workflow(workflow_name)
        checkouts = re.findall(
            r"(?ms)^      - name: [^\n]*Checkout[^\n]*\n(?P<body>.*?)(?=^      - name:|\Z)",
            workflow,
        )
        assert checkouts, f"no checkout step found in {workflow_name}"
        assert all("persist-credentials: false" in block for block in checkouts)


def test_ci_verification_checkout_does_not_persist_credentials():
    verify = _job_block(_workflow("ci.yml"), "verify")
    checkout = re.search(
        r"(?ms)^      - name: Checkout Source Code\n(?P<body>.*?)(?=^      - name:|\Z)",
        verify,
    )
    assert checkout is not None
    assert "persist-credentials: false" in checkout.group("body")


def test_ci_privileged_tokens_confined_to_publish_jobs():
    """Publishing and GITHUB_TOKEN appear only inside the privileged jobs."""
    workflow = _workflow("ci.yml")
    privileged = _job_block(workflow, "publish-main") + _job_block(workflow, "report-pr")
    all_calls = _PUBLISH_INVOCATION.findall(workflow)
    privileged_calls = _PUBLISH_INVOCATION.findall(privileged)
    # Also asserts there is at least one: a workflow that stopped publishing
    # entirely would otherwise satisfy an equality of two zeroes.
    assert all_calls and len(all_calls) == len(privileged_calls)
    assert workflow.count("GITHUB_TOKEN") == privileged.count("GITHUB_TOKEN")
    # verify job must stay read-only: no token, no publish flag in its block
    verify_block = _job_block(workflow, "verify")
    assert "--publish" not in verify_block
    assert "GITHUB_TOKEN" not in verify_block
    assert workflow.count("github.token") == 0


def test_report_pr_job_consumes_artifact_not_pr_code():
    """report-pr downloads artifacts and never checks out the PR ref with creds."""
    workflow = _workflow("ci.yml")
    report_job = _job_block(workflow, "report-pr")
    assert "actions/download-artifact@" in report_job
    assert "ici-verification-report" in report_job
    assert "persist-credentials: false" in report_job
    assert "dist/ici.pyz publish" in report_job
    # the comment path needs pull-requests write, declared only in this job
    assert "pull-requests: write" in report_job
    assert "needs: [verify, viewer-gui]" in report_job
    assert "always() && github.event_name == 'pull_request'" in report_job
    assert "Verify Sticky Comment & Published HTML" in report_job
    assert "<!-- ici-report -->" in report_job
    assert 'EXPECTED_REPORTS: "2"' in report_job
    assert "run_path = f\"/actions/runs/{os.environ['GITHUB_RUN_ID']}\"" in report_job
    assert "for page in range(1, 21)" in report_job
    assert "for attempt in range(120)" in report_job
    assert "time.sleep(5)" in report_job


def test_merge_gate_requires_every_pr_quality_job():
    workflow = _workflow("ci.yml")
    merge_gate = _job_block(workflow, "merge-gate")

    assert "if: ${{ always() }}" in merge_gate
    assert "needs: [verify, viewer-gui, report-pr]" in merge_gate
    assert "VERIFY_RESULT: ${{ needs.verify.result }}" in merge_gate
    assert "VIEWER_GUI_RESULT: ${{ needs.viewer-gui.result }}" in merge_gate
    assert "REPORT_RESULT: ${{ needs.report-pr.result }}" in merge_gate
    assert 'test "$VERIFY_RESULT" = success' in merge_gate
    assert 'test "$VIEWER_GUI_RESULT" = success' in merge_gate
    assert 'test "$REPORT_RESULT" = success' in merge_gate


def test_ci_release_and_candidate_actions_are_immutable_node24_pins():
    expected = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
        "softprops/action-gh-release": "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
    }

    for workflow_name in (
        "ci.yml",
        "release.yml",
        "candidate-artifact.yml",
        "candidate-quality-zoo.yml",
    ):
        workflow = _workflow(workflow_name)
        for uses in _uses_lines(workflow):
            action, _, ref = uses.partition("@")
            assert action in expected, f"unapproved action in {workflow_name}: {uses}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"mutable action ref in {workflow_name}: {uses}"
            )
            assert ref == expected[action], f"unexpected action pin in {workflow_name}: {uses}"
        assert "node20" not in workflow.lower()
        assert not re.search(r"@[vV][0-9]+(?:\.[0-9]+){0,2}\b", workflow)


def test_candidate_workflow_is_manual_and_requires_exact_target_sha():
    workflow = _workflow("candidate-artifact.yml")
    event_block = workflow.split("\n\npermissions:", 1)[0]
    dispatch = event_block.split("  workflow_dispatch:\n", 1)[1]
    target_input = dispatch.split("      target_sha:\n", 1)[1]

    assert "  workflow_dispatch:" in event_block
    assert not re.search(r"(?m)^  (?!workflow_dispatch:)[A-Za-z0-9_-]+:", event_block)
    assert re.search(r"(?m)^        required: true$", target_input)
    assert re.search(r"(?m)^        type: string$", target_input)
    assert not re.search(r"(?m)^        default:", target_input)


def test_candidate_quality_zoo_workflow_is_manual_and_requires_exact_coordinates():
    workflow = _workflow("candidate-quality-zoo.yml")
    event_block = workflow.split("\n\npermissions:", 1)[0]
    dispatch = event_block.split("  workflow_dispatch:\n", 1)[1]

    assert not re.search(r"(?m)^  (?!workflow_dispatch:)[A-Za-z0-9_-]+:", event_block)
    for name in (
        "ici_target_sha",
        "candidate_artifact_id",
        "candidate_archive_sha256",
        "toy_target_sha",
    ):
        value = re.split(
            r"\n      [A-Za-z0-9_-]+:\n",
            dispatch.split(f"      {name}:\n", 1)[1],
            maxsplit=1,
        )[0]
        assert re.search(r"(?m)^        required: true$", value)
        assert re.search(r"(?m)^        type: string$", value)
        assert not re.search(r"(?m)^        default:", value)

    mode = dispatch.split("      toy_revision_mode:\n", 1)[1]
    assert re.search(r"(?m)^        required: false$", mode)
    assert re.search(r"(?m)^        default: main$", mode)
    assert re.search(r"(?m)^        type: choice$", mode)
    assert "          - main" in mode
    assert "          - pull_request" in mode

    pr_number = dispatch.split("      toy_pr_number:\n", 1)[1]
    assert re.search(r"(?m)^        required: false$", pr_number)
    assert re.search(r"(?m)^        type: string$", pr_number)


def test_candidate_quality_zoo_job_is_read_only_and_exact_main_bound():
    workflow = _workflow("candidate-quality-zoo.yml")
    accept = _job_block(workflow, "accept")
    permission_block = re.search(r"(?ms)^    permissions:\n(?P<body>(?:      [^\n]+\n)+)", accept)
    assert permission_block is not None
    assert {
        line.strip() for line in permission_block.group("body").splitlines() if line.strip()
    } == {"actions: read", "checks: read", "contents: read", "pull-requests: read"}
    assert "write" not in permission_block.group("body")
    assert "permissions: {}" in workflow.split("jobs:", 1)[0]
    assert "--publish" not in workflow
    assert "<!-- ici-report -->" not in workflow
    assert "pull-requests: write" not in workflow
    assert "pages:" not in workflow

    checkout = _step_block(accept, "Checkout Exact Quality Zoo Commit")
    assert "repository: jihoon22-lee/toy-projects" in checkout
    assert "ref: ${{ inputs.toy_target_sha }}" in checkout
    assert "persist-credentials: false" in checkout

    validate = _step_block(accept, "Validate Workflow Inputs and Exact Revisions")
    run_script = validate.split("        run: |\n", 1)[1]
    assert "${{ inputs." not in run_script
    assert 'test "$GITHUB_REPOSITORY" = "jihoon22-lee/ici"' in run_script
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in run_script
    assert 'test "$GITHUB_SHA" = "$ici_main"' in run_script
    assert 'test "$TOY_TARGET_SHA" = "$toy_main"' in run_script
    assert "grep -Eq '^(main|pull_request)$'" in run_script
    assert "candidate_merge_gate.py verify-toy-pr" in run_script
    assert '"repos/${toy_repository}/pulls/${TOY_PR_NUMBER}"' in run_script
    assert '"$RUNNER_TEMP/toy-revision.json"' in run_script
    assert run_script.count("^[0-9a-f]{40}$") == 2
    assert "^[0-9a-f]{64}$" in run_script
    assert "^[1-9][0-9]*$" in run_script

    stage = _step_block(accept, "Stage Candidate Acceptance Evidence")
    assert '"$destination/toy-revision.json"' in stage


def test_candidate_quality_zoo_separates_tokens_from_candidate_execution():
    accept = _job_block(_workflow("candidate-quality-zoo.yml"), "accept")
    download = _step_block(accept, "Download Exact Candidate Archive and Metadata")
    preflight = _step_block(accept, "Preflight Candidate without Credentials")
    evidence = _step_block(accept, "Fetch Authenticated GitHub Evidence")
    execute = _step_block(accept, "Verify Candidate Provenance and Run Quality Zoo")

    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in download
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in evidence
    for candidate_step in (preflight, execute):
        assert "GH_TOKEN:" not in candidate_step
        assert "GITHUB_TOKEN:" not in candidate_step
        assert "unset GH_TOKEN GITHUB_TOKEN ACTIONS_RUNTIME_TOKEN" in candidate_step
        assert "unset ACTIONS_ID_TOKEN_REQUEST_TOKEN ACTIONS_ID_TOKEN_REQUEST_URL" in candidate_step
    assert (
        accept.index("Preflight Candidate without Credentials")
        < accept.index("Fetch Authenticated GitHub Evidence")
        < accept.index("Verify Candidate Provenance and Run Quality Zoo")
    )


def test_candidate_quality_zoo_prefers_candidate_manifest_with_stable_fallback():
    accept = _job_block(_workflow("candidate-quality-zoo.yml"), "accept")
    select = _step_block(accept, "Select Candidate Quality Zoo Manifest")
    execute = _step_block(accept, "Verify Candidate Provenance and Run Quality Zoo")

    assert "id: select-manifest" in select
    assert 'candidate_manifest="candidate-manifest.json"' in select
    assert 'stable_manifest="manifest.json"' in select
    assert select.index('selected_manifest="$candidate_manifest"') < select.index(
        'selected_manifest="$stable_manifest"'
    )
    assert 'selected_source="candidate"' in select
    assert 'selected_source="stable-fallback"' in select
    assert "must be a regular non-symlink file" in select
    assert 'sha256sum -- "$selected_manifest"' in select

    assert "MANIFEST_PATH: ${{ steps.select-manifest.outputs.path }}" in execute
    assert "MANIFEST_SOURCE: ${{ steps.select-manifest.outputs.source }}" in execute
    assert "MANIFEST_SHA256: ${{ steps.select-manifest.outputs.sha256 }}" in execute
    assert "candidate:candidate-manifest.json|stable-fallback:manifest.json" in execute
    assert '--manifest "$MANIFEST_PATH"' in execute
    assert 'sha256sum -- "$MANIFEST_PATH"' in execute
    assert "quality-zoo.manifest-selection/v1" in execute


def test_candidate_quality_zoo_installs_cpp_qt_analysis_tools_without_credentials():
    accept = _job_block(_workflow("candidate-quality-zoo.yml"), "accept")
    install = _step_block(accept, "Install C++ and Qt analysis tools")

    assert "sudo apt-get update" in install
    assert "--no-install-recommends" in install
    for package in ("clang", "clang-tidy", "clazy", "cmake", "g++", "pkg-config", "qt6-base-dev"):
        assert re.search(rf"(?:^|\s){re.escape(package)}(?:\s|$)", install)
    assert "GH_TOKEN" not in install
    assert "GITHUB_TOKEN" not in install


def test_candidate_quality_zoo_verifies_provenance_and_uploads_separate_evidence():
    accept = _job_block(_workflow("candidate-quality-zoo.yml"), "accept")
    evidence = _step_block(accept, "Fetch Authenticated GitHub Evidence")
    execute = _step_block(accept, "Verify Candidate Provenance and Run Quality Zoo")
    upload = _step_block(accept, "Upload Candidate Acceptance Evidence")

    for suffix in (
        "actions/runs/${CANDIDATE_RUN_ID}",
        "check-runs/${MERGE_GATE_CHECK_RUN_ID}",
        "actions/jobs/${MERGE_GATE_JOB_ID}",
        "actions/runs/${MERGE_GATE_RUN_ID}",
    ):
        assert suffix in evidence
    assert '--github-evidence "$RUNNER_TEMP/candidate-evidence"' in execute
    assert "python3.10 -m runner.run" in execute
    assert 'payload.get("contract_verdict") != "PASS"' in execute
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in upload
    assert (
        "name: quality-zoo-candidate-${{ inputs.ici_target_sha }}-${{ inputs.toy_target_sha }}"
        in upload
    )
    assert "ici-report-" not in upload
    assert "if-no-files-found: error" in upload
    assert "compression-level: 0" in upload


def test_candidate_validate_job_has_read_only_provenance_permissions():
    workflow = _workflow("candidate-artifact.yml")
    validate = _job_block(workflow, "validate")
    permission_block = re.search(r"(?ms)^    permissions:\n(?P<body>(?:      [^\n]+\n)+)", validate)
    assert permission_block is not None
    assert {
        line.strip() for line in permission_block.group("body").splitlines() if line.strip()
    } == {"actions: read", "checks: read", "contents: read"}
    assert not re.search(r"(?m)^      \S+:\s*write$", permission_block.group("body"))
    assert "permissions: {}" in workflow.split("jobs:", 1)[0]


def test_candidate_validate_binds_input_to_env_and_validates_main_ancestry():
    validate = _job_block(_workflow("candidate-artifact.yml"), "validate")
    provenance = _step_block(validate, "Validate Exact Main Commit and Merge Gate")
    run_script = provenance.split("        run: |\n", 1)[1]

    assert "REQUESTED_TARGET_SHA: ${{ inputs.target_sha }}" in provenance
    assert "${{ inputs.target_sha }}" not in run_script
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in run_script
    assert "^[0-9a-f]{40}$" in run_script
    assert '[ "$REQUESTED_TARGET_SHA" != "$GITHUB_SHA" ]' in run_script
    assert "git fetch --force origin refs/heads/main:refs/remotes/origin/main" in run_script
    assert 'git cat-file -e "${REQUESTED_TARGET_SHA}^{commit}"' in run_script
    assert (
        'test "$(git rev-parse "${REQUESTED_TARGET_SHA}^{commit}")" = "$REQUESTED_TARGET_SHA"'
        in run_script
    )
    assert (
        'git merge-base --is-ancestor "$REQUESTED_TARGET_SHA" refs/remotes/origin/main'
        in run_script
    )


def test_candidate_validate_uses_bounded_pages_and_independent_gate_apis():
    validate = _job_block(_workflow("candidate-artifact.yml"), "validate")
    run_script = _step_block(validate, "Validate Exact Main Commit and Merge Gate").split(
        "        run: |\n", 1
    )[1]

    assert "gh api --paginate" not in run_script
    assert "python3 scripts/candidate_merge_gate.py page-count" in run_script
    assert (
        '"repos/${GITHUB_REPOSITORY}/commits/${REQUESTED_TARGET_SHA}/check-runs?'
        'per_page=100&filter=all&page=1"' in run_script
    )
    assert "for ((page = 2; page <= page_count; page++))" in run_script
    assert 'test "${#check_pages[@]}" = "$page_count"' in run_script
    select = "python3 scripts/candidate_merge_gate.py select-pages"
    verify = "python3 scripts/candidate_merge_gate.py verify"
    verify_job = "python3 scripts/candidate_merge_gate.py verify-job"
    assert select in run_script
    assert verify in run_script
    assert verify_job in run_script
    run_fetch = '"repos/${GITHUB_REPOSITORY}/actions/runs/${merge_gate_run_id}"' in run_script
    job_fetch = '"repos/${GITHUB_REPOSITORY}/actions/jobs/${merge_gate_job_id}"' in run_script
    assert run_fetch
    assert job_fetch
    assert (
        run_script.index(select)
        < run_script.index('"repos/${GITHUB_REPOSITORY}/actions/runs/${merge_gate_run_id}"')
        < run_script.index(verify)
        < run_script.index('"repos/${GITHUB_REPOSITORY}/actions/jobs/${merge_gate_job_id}"')
        < run_script.index(verify_job)
    )


def test_candidate_validate_outputs_bind_target_gate_and_workflow_definition():
    validate = _job_block(_workflow("candidate-artifact.yml"), "validate")
    for output in (
        "target_sha",
        "merge_gate_check_run_id",
        "merge_gate_job_id",
        "merge_gate_run_id",
        "merge_gate_run_attempt",
        "merge_gate_job_url",
        "merge_gate_url",
        "candidate_workflow_definition_sha",
    ):
        assert f"{output}: ${{{{ steps.provenance.outputs.{output} }}}}" in validate

    run_script = _step_block(validate, "Validate Exact Main Commit and Merge Gate").split(
        "        run: |\n", 1
    )[1]
    for output in (
        "target_sha",
        "merge_gate_check_run_id",
        "merge_gate_job_id",
        "merge_gate_run_id",
        "merge_gate_run_attempt",
        "merge_gate_job_url",
        "merge_gate_url",
    ):
        assert f"printf '{output}=%s\\n'" in run_script
    assert "printf 'candidate_workflow_definition_sha=%s\\n' \"$GITHUB_SHA\"" in run_script


def test_candidate_build_is_read_only_and_uses_validated_commit():
    workflow = _workflow("candidate-artifact.yml")
    build = _job_block(workflow, "build")
    permission_block = re.search(r"(?ms)^    permissions:\n(?P<body>(?:      [^\n]+\n)+)", build)
    assert permission_block is not None
    assert [
        line.strip() for line in permission_block.group("body").splitlines() if line.strip()
    ] == ["contents: read"]
    assert "needs: validate" in build

    forbidden_capability = re.compile(
        r"(?i)(?:secrets\.|\bgithub\.token\b|"
        r"\b(?:GH_TOKEN|GITHUB_TOKEN)\s*:|--publish|\bpublish\b|\bcomment\b|"
        r"\bpages\b|softprops/action-gh-release|gh\s+release|release-action)"
    )
    assert not forbidden_capability.search(build)
    token_lines = [
        line.strip()
        for line in build.splitlines()
        if re.search(r"(?i)\b(?:GH_TOKEN|GITHUB_TOKEN|ACTIONS_\w+TOKEN)\b", line)
    ]
    assert token_lines
    assert all(line.startswith("unset ") for line in token_lines)

    checkout = _step_block(build, "Checkout Validated Candidate Commit")
    assert "ref: ${{ needs.validate.outputs.target_sha }}" in checkout
    assert "${{ inputs.target_sha }}" not in checkout
    assert "persist-credentials: false" in checkout


def test_candidate_build_confirms_head_and_repeats_reproducibility_smoke_gates():
    build = _job_block(_workflow("candidate-artifact.yml"), "build")
    confirm = _step_block(build, "Confirm Exact Candidate Commit")
    assert "TARGET_SHA: ${{ needs.validate.outputs.target_sha }}" in confirm
    assert 'test "$(git rev-parse HEAD)" = "$TARGET_SHA"' in confirm
    assert "./scripts/verify-reproducibility.sh" in build
    assert "./scripts/smoke.sh" in build


def test_candidate_bundle_is_created_and_verified_before_upload():
    build = _job_block(_workflow("candidate-artifact.yml"), "build")
    bundle = _step_block(build, "Stage Exact Candidate Bundle")
    create = "python3 scripts/candidate_bundle.py create"
    verify = 'python3 scripts/candidate_bundle.py verify "$bundle_dir"'
    assert create in bundle
    assert verify in bundle
    assert bundle.index(create) < bundle.index(verify)
    assert "MERGE_GATE_JOB_ID: ${{ needs.validate.outputs.merge_gate_job_id }}" in bundle
    assert '--merge-gate-job-id "$MERGE_GATE_JOB_ID"' in bundle


def test_candidate_upload_is_immutable_and_named_for_validated_target():
    build = _job_block(_workflow("candidate-artifact.yml"), "build")
    upload = _step_block(build, "Upload Immutable Candidate Bundle")
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in upload
    assert "name: ici-candidate-${{ needs.validate.outputs.target_sha }}" in upload
    assert "if-no-files-found: error" in upload
    assert "overwrite: false" in upload
    assert "retention-days: 14" in upload
    assert "path: ${{ runner.temp }}/ici-candidate-bundle" in upload


def test_candidate_summary_keeps_artifact_coordinates_separate():
    build = _job_block(_workflow("candidate-artifact.yml"), "build")
    summary = _step_block(build, "Record Candidate Coordinates")
    for variable, output in (
        ("ARTIFACT_ID", "artifact-id"),
        ("ARTIFACT_DIGEST", "artifact-digest"),
        ("ARTIFACT_URL", "artifact-url"),
    ):
        assert f"{variable}: ${{{{ steps.upload.outputs.{output} }}}}" in summary
        assert f"${variable}" in summary
    assert "- artifact ID:" in summary
    assert "- artifact digest:" in summary
    assert "- authenticated download:" in summary


def test_release_workflow_requires_validated_tag_without_stale_fallback():
    workflow = _workflow("release.yml")
    dispatch = workflow.split("  workflow_dispatch:\n", 1)[1].split("\n\npermissions:", 1)[0]
    version_input = dispatch.split("      version_tag:\n", 1)[1].split("      draft:\n", 1)[0]

    assert re.search(r"(?m)^        required: true$", version_input)
    assert not re.search(r"(?m)^        default:", version_input)
    assert "v0.3.3" not in workflow

    tag_step = workflow.split("      - name: Validate Tag, Main Ancestry, and Merge Gate\n", 1)[
        1
    ].split("\n  build-release:", 1)[0]
    run_script = tag_step.split("        run: |\n", 1)[1]
    assert "MANUAL_VERSION_TAG: ${{ inputs.version_tag }}" in tag_step
    assert "${{ inputs.version_tag }}" not in run_script
    assert 'if [ "$GITHUB_EVENT_NAME" = "push" ]; then' in run_script
    assert 'TAG="$GITHUB_REF_NAME"' in run_script
    assert 'TAG="$MANUAL_VERSION_TAG"' in run_script
    assert 'TAG="v0.3.3"' not in run_script
    assert "PACKAGE_VERSION" in run_script
    assert "src/ici/__init__.py" in run_script
    assert '"$TAG" != "v$PACKAGE_VERSION"' in run_script
    assert 'git rev-parse --verify "$TAG^{commit}"' in run_script
    assert 'git checkout --detach "$TARGET_SHA"' in run_script
    assert 'git merge-base --is-ancestor "$TARGET_SHA" refs/remotes/origin/main' in run_script
    assert "commits/${TARGET_SHA}/check-runs" in run_script
    assert 'select(.name == "Merge Gate")' in run_script
    assert '[ "$gate_conclusion" = success ]' in run_script
    assert "GITHUB_OUTPUT" in run_script


def test_release_write_permission_follows_read_only_provenance_gate():
    workflow = _workflow("release.yml")
    validate = _job_block(workflow, "validate-release")
    build = _job_block(workflow, "build-release")

    assert "permissions: {}" in workflow.split("jobs:", 1)[0]
    assert "contents: read" in validate
    assert "checks: read" in validate
    assert "contents: write" not in validate
    assert "needs: validate-release" in build
    assert "contents: write" in build
    assert "ref: ${{ needs.validate-release.outputs.target_sha }}" in build
    assert 'test "$(git rev-parse HEAD)" = "$TARGET_SHA"' in build


def test_release_repeats_quality_and_dogfood_gates_on_the_candidate():
    build = _job_block(_workflow("release.yml"), "build-release")

    assert "ruff check ." in build
    assert "ruff format --check ." in build
    assert "pytest -v" in build
    assert "./scripts/build-pyz.sh" in build
    assert "(cd dist && sha256sum ici.pyz > ici.pyz.sha256)" in build
    assert "sha256sum dist/ici.pyz > dist/ici.pyz.sha256" not in build
    assert "./scripts/smoke.sh" in build
    assert "dist/ici.pyz verify" in build
    assert "../dist/ici.pyz verify" in build
    assert "QT_QPA_PLATFORM: offscreen" in build
    assert "cmake --build viewer/build/gui --parallel" in build
    assert "cmake --build viewer/build/gui --target icirv-gui" not in build
    assert "ctest --test-dir viewer/build/gui --output-on-failure" in build
    for report in (
        "dist/ici-self-report.html",
        "dist/ici-self-report.json",
        "dist/viewer-report.html",
        "dist/viewer-report.json",
    ):
        assert build.count(report) >= 2


def test_gate_reason_matches_the_aggregation_rule():
    """The stated reason must agree with the status it explains.

    The console prints a Pass/Warn/Fail/Error tally alongside the suite status,
    but the two come from different rules — a required engine that skipped
    escalates everything — so a report could read "Error: 0" while the suite was
    ERROR with nothing saying why.
    """
    from ici.core.models import (
        EngineResult,
        EngineStatus,
        EvidenceState,
        aggregate_suite_status,
        gate_reason,
    )

    def engine(name, status, required=True, evidence=EvidenceState.MEASURED):
        return EngineResult(
            engine_name=name, status=status, summary="", required=required, evidence=evidence
        )

    cases = [
        # A required skip escalates, and the reason names the engine that did it.
        (
            [engine("lint", EngineStatus.PASS), engine("dead", EngineStatus.SKIP)],
            EngineStatus.ERROR,
            "dead",
        ),
        # Not applicable is invisible to the gate, so it must not be blamed.
        (
            [
                engine("lint", EngineStatus.PASS),
                engine("dead", EngineStatus.SKIP, evidence=EvidenceState.NOT_APPLICABLE),
            ],
            EngineStatus.PASS,
            "passed",
        ),
        (
            [engine("lint", EngineStatus.WARN, evidence=EvidenceState.NOT_RUN)],
            EngineStatus.ERROR,
            "did not run",
        ),
        ([engine("test", EngineStatus.FAIL)], EngineStatus.FAIL, "test"),
        ([engine("dup", EngineStatus.WARN)], EngineStatus.WARN, "dup"),
    ]
    for results, expected_status, expected_fragment in cases:
        status = aggregate_suite_status(results)
        assert status == expected_status, f"{results} -> {status}"
        reason = gate_reason(results, status)
        assert expected_fragment in reason, reason
