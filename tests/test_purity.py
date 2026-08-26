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
    assert re.search(r"(?m)^    needs: verify\n", publish)
    assert re.search(r"(?m)^    permissions:\n      contents: write\n", publish)
    assert not re.search(r"(?m)^      (?:issues|pull-requests|checks):\s*write\n", publish)
    assert "--publish" in publish
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


def test_all_ci_and_release_checkouts_disable_credential_persistence():
    for workflow_name in ("ci.yml", "release.yml"):
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
    """--publish/GITHUB_TOKEN appear only inside the privileged publish jobs."""
    workflow = _workflow("ci.yml")
    privileged = _job_block(workflow, "publish-main") + _job_block(workflow, "report-pr")
    assert workflow.count("--publish") == privileged.count("--publish")
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


def test_ci_and_release_actions_are_immutable_node24_pins():
    expected = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
        "softprops/action-gh-release": "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
    }

    for workflow_name in ("ci.yml", "release.yml"):
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


def test_release_workflow_requires_explicit_version_tag_without_stale_fallback():
    workflow = _workflow("release.yml")
    dispatch = workflow.split("  workflow_dispatch:\n", 1)[1].split("\n\npermissions:", 1)[0]
    version_input = dispatch.split("      version_tag:\n", 1)[1].split("      draft:\n", 1)[0]

    assert re.search(r"(?m)^        required: true$", version_input)
    assert not re.search(r"(?m)^        default:", version_input)
    assert "v0.3.3" not in workflow

    tag_step = workflow.split("      - name: Determine Target Tag\n", 1)[1].split(
        "\n      - name: Run Test Suite", 1
    )[0]
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
    assert "GITHUB_OUTPUT" in run_script
