"""Native output attribution and honest execution effects for ecosystem tools."""

import json
import os
import shutil
import subprocess

import pytest

from ici.adapters.providers.ecosystem import EcosystemProvider
from ici.execution.process import Outcome, ProcessSpec, TaskOutcome, run_task


def _missing(message):
    if os.environ.get("ICI_REQUIRE_ECOSYSTEM_TOOLS") == "1":
        pytest.fail(message)
    pytest.skip(message)


def outcome(root, text, code=0):
    return TaskOutcome(
        spec=ProcessSpec(argv=("tool",), name="project.check", cwd=root),
        outcome=Outcome.FINISHED,
        exit_code=code,
        stdout=text,
    )


def test_eslint_native_rule_and_location(tmp_path):
    parsed = EcosystemProvider("eslint", tmp_path).parse(
        outcome(
            tmp_path,
            json.dumps(
                [
                    {
                        "filePath": str(tmp_path / "src.js"),
                        "messages": [
                            {
                                "ruleId": "no-undef",
                                "line": 3,
                                "column": 2,
                                "severity": 2,
                                "message": "unknown",
                            }
                        ],
                    }
                ]
            ),
            1,
        )
    )
    assert parsed.is_readable
    finding = parsed.findings[0]
    assert finding.native_rule_id == "no-undef"
    assert finding.primary_location.path == "src.js"
    assert finding.primary_location.start_line == 3


@pytest.mark.parametrize(
    "name,text", [("eslint", "{}"), ("clippy", ""), ("tsc", "error TS5023: bad option")]
)
def test_unreadable_never_becomes_pass(tmp_path, name, text):
    assert not EcosystemProvider(name, tmp_path).parse(outcome(tmp_path, text, 1)).is_readable


def test_cargo_prepare_and_config_inputs(tmp_path):
    plan = EcosystemProvider("clippy", tmp_path).plan(
        "cargo",
        tmp_path,
        ("src/lib.rs",),
        "project.rust.clippy",
        "project.rust",
        ("Cargo.toml", "Cargo.lock", ".cargo/config.toml"),
    )
    assert plan.task.mutating and not plan.task.cacheable
    assert "--offline" in plan.task.argv and "--locked" in plan.task.argv
    assert ".cargo/config.toml" in plan.task.input_refs


def test_real_clippy_fixture(tmp_path):
    cargo = shutil.which("cargo")
    if not cargo:
        _missing("Cargo unavailable; real Clippy fixture not executed")
    probe = subprocess.run([cargo, "clippy", "--version"], capture_output=True)
    if probe.returncode:
        _missing("Clippy unavailable; real Clippy fixture not executed")
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="ici-provider-fixture"\nversion="0.1.0"\nedition="2021"\n'
    )
    (tmp_path / "src/lib.rs").write_text("pub fn answer() -> i32 { let unused = 1; 42 }\n")
    (tmp_path / "build.rs").write_text(
        'fn main() { std::fs::write("build-script-ran", "yes").unwrap(); }'
    )
    subprocess.run(
        [cargo, "generate-lockfile", "--offline"], cwd=tmp_path, check=True, capture_output=True
    )
    provider = EcosystemProvider("clippy", tmp_path)
    plan = provider.plan(
        cargo,
        tmp_path,
        ("src/lib.rs", "build.rs"),
        "project.rust.clippy",
        "project.rust",
        ("Cargo.toml", "Cargo.lock"),
    )
    parsed = provider.parse(
        run_task(
            ProcessSpec(
                argv=plan.task.argv, cwd=tmp_path, name=plan.task.id, environment=dict(os.environ)
            )
        )
    )
    assert parsed.is_readable, parsed.failed_to_parse
    assert any(f.native_rule_id == "unused_variables" for f in parsed.findings)
    assert (tmp_path / "build-script-ran").is_file()
    assert plan.task.mutating


def test_real_typescript_fixture(tmp_path):
    tsc = shutil.which("tsc")
    if not tsc:
        _missing("TypeScript unavailable; real tsc fixture not executed")
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":true},"include":["example.ts"]}'
    )
    (tmp_path / "example.ts").write_text('const value: number = "bad";\n')
    provider = EcosystemProvider("tsc", tmp_path)
    plan = provider.plan(
        tsc,
        tmp_path,
        ("example.ts",),
        "project.typescript.type",
        "project.typescript",
        ("tsconfig.json",),
    )
    parsed = provider.parse(
        run_task(
            ProcessSpec(
                argv=plan.task.argv, cwd=tmp_path, name=plan.task.id, environment=dict(os.environ)
            )
        )
    )
    assert parsed.is_readable, parsed.failed_to_parse
    assert any(f.native_rule_id == "2322" for f in parsed.findings)
    assert not (tmp_path / "example.js").exists()
    assert "tsconfig.json" in plan.task.input_refs


def test_real_eslint_fixture(tmp_path):
    eslint = shutil.which("eslint")
    if not eslint:
        _missing("ESLint unavailable; real ESLint fixture not executed")
    (tmp_path / "eslint.config.mjs").write_text(
        'export default [{rules:{"no-unused-vars":"error"}}];'
    )
    # Older supported system ESLint uses its own native config format.
    (tmp_path / ".eslintrc.json").write_text(
        '{"root":true,"parserOptions":{"ecmaVersion":2018},"rules":{"no-unused-vars":"error"}}'
    )
    (tmp_path / "example.js").write_text("const unused = 1;\n")
    provider = EcosystemProvider("eslint", tmp_path)
    plan = provider.plan(
        eslint,
        tmp_path,
        ("example.js",),
        "project.javascript.lint",
        "project.javascript",
        ("eslint.config.mjs",),
    )
    parsed = provider.parse(
        run_task(
            ProcessSpec(
                argv=plan.task.argv, cwd=tmp_path, name=plan.task.id, environment=dict(os.environ)
            )
        )
    )
    assert parsed.is_readable, parsed.failed_to_parse
    assert parsed.findings[0].native_rule_id == "no-unused-vars"
    assert (tmp_path / "example.js").read_text() == "const unused = 1;\n"


def test_tsc_native_scope_does_not_resolve_baseline_by_inference(tmp_path, monkeypatch):
    if not shutil.which("tsc"):
        _missing("TypeScript unavailable; native scope regression not executed")
    from typer.testing import CliRunner

    from ici.__main__ import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "example.ts").write_text('const value: number = "bad";\n')
    (tmp_path / "tsconfig.json").write_text('{"include":["example.ts"]}')
    (tmp_path / "ici.toml").write_text(
        'schema_version=1\n[workspace]\nname="fixture"\n'
        '[checks."typescript.lint"]\nenabled=false\n'
        '[[components]]\nid="project"\nroot="."\nlanguages=["typescript"]\n'
    )
    result = CliRunner().invoke(app, ["analyze", "--json"])
    assert result.exit_code == 1, result.output
    record = json.loads(result.stdout)["checks"][0]
    assert record["state"] == "SUCCEEDED"
    assert record["evidence_complete"] is False
