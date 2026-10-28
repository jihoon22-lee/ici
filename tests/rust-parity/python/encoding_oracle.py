"""Verify frozen UTF-8 intake and independent CPython byte-source semantics.

This is a development oracle; no Python decoder or backend enters the product.
"""

import argparse
import ast
import base64
import codecs
import dataclasses
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def location(text, line, byte_column):
    original = text.splitlines(keepends=True)[line - 1]
    return len(original.encode("utf-8")[:byte_column].decode("utf-8")) + 1


def shapes(tree, text):
    result = {"functions": [], "names": []}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Name)):
            result["names" if isinstance(node, ast.Name) else "functions"].append(
                {
                    "name": node.id if isinstance(node, ast.Name) else node.name,
                    "start_line": node.lineno,
                    "start_column": location(text, node.lineno, node.col_offset),
                    "end_line": node.end_lineno,
                    "end_column": location(text, node.end_lineno, node.end_col_offset) - 1,
                }
            )
    for rows in result.values():
        rows.sort(
            key=lambda row: (
                row["start_line"],
                row["start_column"],
                row["end_line"],
                row["end_column"],
                row["name"],
            )
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frozen", type=Path)
    parser.add_argument("--dependencies", type=Path)
    args = parser.parse_args()
    evidence = json.loads(
        Path(__file__).with_name("source-encoding.json").read_text(encoding="utf-8")
    )
    assert sys.version.split()[0] == evidence["reference_python"]
    frozen = args.frozen.resolve()
    for name, expected in evidence["oracle_files"].items():
        assert sha((frozen / name).read_bytes()) == expected, name
    sys.path.insert(0, str(frozen / "src"))
    if args.dependencies:
        sys.path.append(str(args.dependencies.resolve()))
    metrics = importlib.import_module("ici.languages.metrics")
    intake = importlib.import_module("ici.analysis._source_inputs")
    assert Path(metrics.__file__).is_relative_to(frozen)
    for case in evidence["exclusions"]:
        assert list(intake.analysis_exclusion_reasons(case["path"])) == case["original_reasons"]
    for case in evidence["cases"]:
        raw = base64.b64decode(case["bytes_base64"], validate=True)
        assert sha(raw) == case["raw_sha256"], case["id"]
        if "codec_text_sha256" in case:
            assert sha(raw.decode(case["codec"]).encode()) == case["codec_text_sha256"]
        try:
            tree = ast.parse(raw, filename="main.py")
            actual = {"accepted": True}
        except Exception as error:
            tree = None
            actual = {
                "accepted": False,
                "error": type(error).__name__,
                "message": str(error),
                "line": getattr(error, "lineno", None),
                "offset": getattr(error, "offset", None),
            }
        assert actual == case["cpython_bytes_ast"], case["id"]
        with tempfile.TemporaryDirectory(prefix="frozen-source-") as temporary:
            root = Path(temporary)
            source = root / "main.py"
            source.write_bytes(raw)
            try:
                snapshot = intake.read_analysis_sources(root, [source]).sources[0]
                actual = {
                    "accepted": True,
                    "text_sha256": sha(snapshot.text.encode()),
                    "bytes": snapshot.byte_size,
                }
            except Exception as error:
                actual = {
                    "accepted": False,
                    "error": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "message": str(error),
                }
            assert actual == case["original_intake"], case["id"]
            try:
                rows = metrics.scan_functions(source, "python", root)
                actual = {"accepted": True, "functions": [dataclasses.asdict(row) for row in rows]}
            except Exception as error:
                actual = {"accepted": False, "error": type(error).__name__}
            assert actual == case["original_metrics"], case["id"]
        expected = case["expected"]
        if expected["source_accepted"]:
            text = raw.decode(case["codec"])
            bom = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
            if bom:
                text = text.removeprefix("\ufeff")
            assert text == expected["text"], case["id"]
            decoder = codecs.getincrementaldecoder(case["codec"])(errors="strict")
            boundaries = [[0, bom]]
            offset = 0
            for index, byte in enumerate(raw[bom:], start=bom):
                output = decoder.decode(bytes([byte]), final=False)
                for which, scalar in enumerate(output):
                    offset += len(scalar.encode())
                    boundaries.append([offset, index + 1 if which == len(output) - 1 else None])
            assert decoder.decode(b"", final=True) == ""
            # Trailing state changes consume bytes without emitting text.
            # The terminal span includes that original suffix.
            boundaries[-1][1] = len(raw)
            assert boundaries == expected["raw_boundaries"], case["id"]
            if tree:
                actual = shapes(tree, text)
                assert actual["functions"] == expected["functions"], case["id"]
                assert actual["names"] == expected["names"], case["id"]
        else:
            assert tree is None and case["difference_reason"], case["id"]
    print(f"{len(evidence['cases'])} frozen source-encoding fixtures match")


if __name__ == "__main__":
    main()
