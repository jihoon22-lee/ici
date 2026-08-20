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
    verify = _job_block(_workflow("ci.yml"), "verify")
    assert "verify --report --html verify_report.html --github-summary" in verify
    assert "verify_report.html" in verify
    assert "verify_report.json" in verify
    assert "actions/upload-artifact@" in verify
    assert "GITHUB_STEP_SUMMARY" in verify


def test_ci_verification_checkout_does_not_persist_credentials():
    verify = _job_block(_workflow("ci.yml"), "verify")
    checkout = re.search(
        r"(?ms)^      - name: Checkout Source Code\n(?P<body>.*?)(?=^      - name:|\Z)",
        verify,
    )
    assert checkout is not None
    assert "persist-credentials: false" in checkout.group("body")


def test_ci_only_trusted_publish_job_uses_publish_and_token():
    workflow = _workflow("ci.yml")
    publish = _job_block(workflow, "publish-main")
    assert workflow.count("--publish") == publish.count("--publish")
    assert workflow.count("GITHUB_TOKEN") == publish.count("GITHUB_TOKEN")
    assert workflow.count("github.token") == 0


def test_ci_and_release_actions_are_immutable_node24_pins():
    expected = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
        "softprops/action-gh-release": "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
    }

    for workflow_name in ("ci.yml", "release.yml"):
        workflow = _workflow(workflow_name)
        for uses in _uses_lines(workflow):
            action, _, ref = uses.partition("@")
            assert action in expected, f"unapproved action in {workflow_name}: {uses}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"mutable action ref in {workflow_name}: {uses}"
            assert ref == expected[action], f"unexpected action pin in {workflow_name}: {uses}"
        assert "node20" not in workflow.lower()
        assert not re.search(r"@[vV][0-9]+(?:\.[0-9]+){0,2}\b", workflow)
