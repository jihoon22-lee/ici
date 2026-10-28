"""Development-only CPython comparison. Never shipped as an ici runtime."""

import ast
import json
import platform
import sys
from pathlib import Path


def observe(case):
    source = (
        bytes.fromhex(case["source_hex"])
        if "source_hex" in case
        else case["source"].encode("utf-8")
    )
    source *= case.get("repeat", 1)
    record = {"id": case["id"]}
    if len(source) > 1024 * 1024:
        return {**record, "not_run": "probe-resource-limit"}
    try:
        tree = ast.parse(source, filename=case["id"])
    except (SyntaxError, ValueError) as error:
        record["ast_valid"] = False
        record["ast_error"] = str(error)
    else:
        record["ast_valid"] = True
        record["names"] = [
            {
                "id": node.id,
                "line": node.lineno,
                "byte_column": node.col_offset,
                "end_line": node.end_lineno,
                "end_byte_column": node.end_col_offset,
            }
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        ][:512]
    try:
        compile(source, case["id"], "exec")
    except (SyntaxError, ValueError) as error:
        record["compile_valid"] = False
        record["compile_error"] = str(error)
    else:
        record["compile_valid"] = True
    return record


def main():
    cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
    print(
        json.dumps(
            {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "results": [observe(case) for case in cases],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
