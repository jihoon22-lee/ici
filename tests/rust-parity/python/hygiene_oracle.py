"""Verify frozen security/resource oracle evidence, preserving explicit correction rows."""

import hashlib
import importlib
import json
import sys
from pathlib import Path


def main():
    fixtures = json.loads(Path(__file__).with_name("hygiene.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    for relative, expected in fixtures["oracle_hashes"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
    sys.path.insert(0, str(root / "src"))
    security = importlib.import_module("ici.analysis._python_security")
    resources = importlib.import_module("ici.analysis._python_resources")
    assert Path(security.__file__).is_relative_to(root)
    for case in fixtures["cases"]:
        if case["kind"] == "security":
            result = security.analyze_python_security("main.py", case["source"])
            targets = result.findings
            counters = {
                "calls_checked": result.checked_calls,
                "secret_literals_checked": result.secret_literals_checked,
            }
        else:
            result = resources.analyze_python_resources("main.py", case["source"])
            targets = [issue.target for issue in result.issues]
            counters = {
                "acquisitions_checked": result.acquisitions_checked,
                "mutable_defaults_checked": result.mutable_defaults_checked,
                "ast_nodes": result.ast_nodes,
            }
        observed = [
            {
                "name": target.target_name,
                "line": target.start_line,
                "end_line": target.end_line,
                "message": target.message,
                "snippet": target.snippet,
                "metrics": target.metrics,
                "status": target.status.value,
            }
            for target in targets
        ]
        assert observed == case["oracle_targets"], case["id"]
        assert counters == case["oracle_counters"], case["id"]
    print(f"{len(fixtures['cases'])} fixed-source hygiene fixtures match; corrections stay separate")


if __name__ == "__main__":
    main()
