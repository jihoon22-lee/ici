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
