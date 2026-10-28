"""R0 native tool proof; this developer harness is not a product resolver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ruff", required=True, type=Path)
    parser.add_argument("--ty", required=True, type=Path)
    parser.add_argument("--strace", type=Path, default=Path("/usr/bin/strace"))
    args = parser.parse_args()
    paths = {"ruff": args.ruff.resolve(), "ty": args.ty.resolve()}
    results = []
    with tempfile.TemporaryDirectory(prefix="ici-native-tools-") as temporary:
        root = Path(temporary)
        (root / "empty-path").mkdir()
        env = dict(os.environ, PATH=str(root / "empty-path"), NO_COLOR="1")
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "TY_CONFIG_FILE"):
            env.pop(name, None)

        def run(name: str, tool: str, argv: list[str], expected: int):
            trace = root / (name + ".trace")
            completed = subprocess.run(
                [
                    str(args.strace),
                    "-f",
                    "-e",
                    "trace=process,network",
                    "-o",
                    str(trace),
                    str(paths[tool]),
                    *argv,
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert completed.returncode == expected, (name, completed.stdout, completed.stderr)
            execution = [line for line in trace.read_text().splitlines() if "execve(" in line]
            assert len(execution) == 1 and str(paths[tool]) in execution[0], execution
            # Threads are allowed; no child process may invoke a Python or another tool.
            assert not any("connect(" in line for line in trace.read_text().splitlines())
            result = {
                "case": name,
                "exit": expected,
                "execve_count": len(execution),
                "network_connect_count": 0,
            }
            results.append(result)
            return completed

        for tool, version in (("ruff", "0.16.4"), ("ty", "0.0.93")):
            output = run(tool + "_version", tool, ["--version"], 0)
            assert output.stdout.strip() == f"{tool} {version}"
        (root / "main.py").write_text("undefined_name\n")
        (root / "ignored.py").write_text("another_undefined_name\n")
        (root / "ruff.toml").write_text(
            'required-version="==0.16.4"\nexclude=["ignored.py"]\n[lint]\nselect=["F821"]\n'
        )
        output = run(
            "ruff_native_exclude",
            "ruff",
            [
                "check",
                "--no-cache",
                "--force-exclude",
                "--output-format",
                "json",
                "main.py",
                "ignored.py",
            ],
            1,
        )
        findings = json.loads(output.stdout)
        assert len(findings) == 1 and findings[0]["code"] == "F821", findings
        assert findings[0]["location"] == {"row": 1, "column": 1}
        (root / "ruff.toml").write_text('required-version="==0.0.1"\n')
        mismatch = run("ruff_version_constraint", "ruff", ["check", "main.py"], 2)
        assert "required" in mismatch.stderr.lower()
        (root / "ruff.toml").unlink()
        (root / "ignored.py").unlink()
        (root / "main.py").write_text('value: int = "bad"\n')
        command = [
            "check",
            "--python-version",
            "3.10",
            "--output-format",
            "sarif",
            "--no-progress",
            "main.py",
        ]
        output = run("ty_no_python_type_diagnostic", "ty", command, 1)
        finding = json.loads(output.stdout)["runs"][0]["results"][0]
        assert finding["ruleId"] == "invalid-assignment"
        assert finding["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
        (root / "pyproject.toml").write_text('[tool.ty.rules]\ninvalid-assignment="ignore"\n')
        output = run("ty_native_pyproject_rules", "ty", command, 0)
        assert json.loads(output.stdout)["runs"][0]["results"] == []
        (root / "pyproject.toml").write_text(
            '[tool.ty.rules]\ninvalid-assignment="not-a-severity"\n'
        )
        run("ty_invalid_config", "ty", command, 2)
        (root / "pyproject.toml").unlink()
        (root / "main.py").write_text("import deliberately_missing_dependency\n")
        output = run("ty_unavailable_dependency", "ty", command, 1)
        finding = json.loads(output.stdout)["runs"][0]["results"][0]
        assert finding["ruleId"] == "unresolved-import" and finding["level"] == "error"
    print(
        json.dumps(
            {
                "cases": results,
                "count": len(results),
                "binaries": {
                    name: {
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "path": str(path),
                    }
                    for name, path in paths.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
