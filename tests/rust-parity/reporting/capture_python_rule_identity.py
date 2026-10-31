"""Development-only capture: frozen Python rule identities behind issue-group rule ids.

`IssueGroup.rule_id` (reporters/issue_view.py, python_issue_projection.py) and `HtmlIssue.rule_id`
(reporters/html/utils.py) come from frozen `canonical_python_rule_identity`. This records, for
every alias of every reviewed mapping and for canonical, unknown, engine-fallback and non-ASCII
inputs, the canonical rule id and merge group frozen derives, plus the mapping table itself.
Never called by the Rust product.

Run from a neutral directory: PYTHONPATH=<oracle>/src <venv python> -s
capture_python_rule_identity.py <out.json> (not -I, which would ignore PYTHONPATH).
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from ici.core.python_rule_registry import PYTHON_RULE_MAPPINGS
from ici.core.python_rules import canonical_python_rule_identity

EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ENGINES = ("", "lint", "complexity", "cognitive")
FALLBACK_ENGINES = ("", "lint", "complexity", "cognitive", "security", "dead", "exception")
CONTEXTS = (
    None,
    "baseexception",
    "confirmed-leak",
    "subprocess",
    "os.system",
    "os.popen",
    "cyclomatic",
    "md5",
    "SHA1",
    "eval",
    "exec",
)


def inputs():
    for mapping in PYTHON_RULE_MAPPINGS:
        for alias in mapping.aliases:
            for engine in ENGINES:
                for context in CONTEXTS:
                    yield {"rule_id": alias, "engine": engine, "context": context}
            yield {"rule_id": "ici.legacy.lint.target", "tool_rule_id": alias}
            yield {"rule_id": "ici.legacy.lint.target", "label": f"thing [{alias}]"}
            yield {"rule_id": "ici.legacy.lint.target", "tool_rule_id": f"Tool: {alias}"}
        for context in CONTEXTS:
            yield {"rule_id": mapping.canonical_rule_id, "context": context}
    for rule in ("ici.python.custom-rule", "ici.other.rule", "Weird Rule!", "", "Straße", "E9"):
        for engine in FALLBACK_ENGINES:
            yield {"rule_id": rule, "engine": engine}
    for label in ("unreachable code", "Unreachable_Code", "something else"):
        yield {"rule_id": "ici.legacy.dead.target", "engine": "dead", "label": label}


def main() -> None:
    oracle = Path(sys.modules["ici"].__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != EXPECTED:
        raise SystemExit(f"oracle must be {EXPECTED}, found {revision}")
    cases = []
    for item in inputs():
        identity = canonical_python_rule_identity(
            item["rule_id"],
            tool_rule_id=item.get("tool_rule_id", ""),
            engine_name=item.get("engine", ""),
            target_label=item.get("label", ""),
            semantic_context=item.get("context"),
        )
        cases.append(
            [
                item["rule_id"],
                item.get("tool_rule_id", ""),
                item.get("label", ""),
                item.get("engine", ""),
                item.get("context"),
                identity.canonical_rule_id,
                identity.merge_group,
            ]
        )
    document = {
        "oracle": EXPECTED,
        "mappings": [dataclasses.asdict(mapping) for mapping in PYTHON_RULE_MAPPINGS],
        "case_fields": [
            "rule_id",
            "tool_rule_id",
            "label",
            "engine",
            "context",
            "canonical_rule_id",
            "merge_group",
        ],
        "cases": cases,
    }
    Path(sys.argv[1]).write_text(
        json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
