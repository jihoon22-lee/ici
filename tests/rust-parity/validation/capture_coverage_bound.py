"""Development-only capture: frozen legacy coverage-policy row bound (`ici test --report`).

    python -I capture_coverage_bound.py --oracle <oracle> --python <interpreter> --out <json>

A legacy project whose single source file defines N one-line functions (one test calls one of
them) is run by the immutable v0.13.0 stable `TestEngine` with an explicit interpreter that has
pytest and coverage.py. With N = 10 001 the function inventory exceeds frozen
`evaluate_coverage_policy`'s 10 000-row bound; N = 10 000 is the accepted edge. The capture
records the engine verdict and the policy targets (`Coverage:*`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
CONFIG = (
    '[ici]\nprofile = "standard"\n[project]\nsource_dirs = ["src"]\n'
    '[engines.test]\npython = "<PYTHON>"\n'
)
TEST = "from many import f0\n\n\ndef test_f0():\n    assert f0() == 0\n"


def source(count: int) -> str:
    return "".join(f"def f{index}():\n    return {index}\n\n\n" for index in range(count))


def capture(oracle: Path, python: Path, count: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ici-coverage-bound-") as temporary:
        root = Path(temporary)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src/many.py").write_text(source(count), encoding="utf-8")
        (root / "tests/test_many.py").write_text(TEST, encoding="utf-8")
        (root / "ici.toml").write_text(CONFIG.replace("<PYTHON>", str(python)), encoding="utf-8")
        process = subprocess.run(
            [str(python), "-m", "ici", "test", "--report"],
            cwd=root,
            env=dict(
                os.environ,
                PYTHONPATH=str(oracle / "src"),
                PATH="/usr/bin:/bin",
                PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            ),
            capture_output=True,
            check=False,
            timeout=1800,
        )
        engine = json.loads((root / "test_report.json").read_text(encoding="utf-8"))
    policy = [
        {key: target.get(key) for key in ("file_path", "target_name", "status", "message")}
        for target in engine["targets"]
        if target["target_name"].startswith("Coverage:")
    ]
    return {
        "functions": count,
        "exit_code": process.returncode,
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "policy_targets": policy if count > 10_000 else policy[:3],
        "policy_target_count": len(policy),
        "function_rows": len(engine["extra"].get("function_rows") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "source": SOURCE,
        "config": CONFIG,
        "test": TEST,
        "runs": {
            str(count): capture(args.oracle, args.python, count) for count in (10_001, 10_000)
        },
    }
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, run in document["runs"].items():
        print(name, run["exit_code"], run["status"], run["summary"], run["policy_target_count"])


def _private_home():
    """The shared private HOME/XDG context (tests/rust-parity/private_home.py), loaded by path."""
    path = Path(__file__).resolve().parents[1] / "private_home.py"
    spec = importlib.util.spec_from_file_location("rust_parity_private_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.private_home()


if __name__ == "__main__":
    with _private_home():
        main()
