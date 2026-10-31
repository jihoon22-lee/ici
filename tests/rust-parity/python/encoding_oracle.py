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
    original = text.split("\n")[line - 1]
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


def normalize(data):
    """CPython byte-source newline translation with original boundary indices."""
    normalized = bytearray()
    boundaries = [0]
    index = 0
    while index < len(data):
        byte = data[index]
        index += 1
        if byte == 13:
            byte = 10
            if data[index : index + 1] == b"\n":
                index += 1
        normalized.append(byte)
        boundaries.append(index)
    if normalized and normalized[-1] != 10:
        normalized.append(10)
        boundaries.append(len(data))
    return bytes(normalized), boundaries


def decoded_source(raw, codec):
    bom = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
    prepared, prepared_boundaries = normalize(raw[bom:])
    text = prepared.decode(codec)
    canonical = codecs.lookup(codec).name
    incremental_codec = codec
    if canonical in ("utf-16", "utf-32") and not prepared.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")
    ):
        incremental_codec = canonical + ("-le" if sys.byteorder == "little" else "-be")
    decoder = codecs.getincrementaldecoder(incremental_codec)(errors="strict")
    decoded_boundaries = {0: bom}
    offset = 0
    if canonical in ("unicode-escape", "raw-unicode-escape"):
        # CPython's incremental unicode_escape releases octal digits eagerly,
        # so one-byte feeds differ from full strict decoding (\777 vs \7,7,7).
        # Feed complete greedy atoms to the actual full CPython codec instead.
        index = 0
        pieces = []
        while index < len(prepared):
            begin = index
            index += 1
            if prepared[begin] == 92 and index < len(prepared):
                escape = prepared[index]
                index += 1
                if escape in (ord("u"), ord("U")) or (
                    escape == ord("x") and canonical == "unicode-escape"
                ):
                    index += {ord("x"): 2, ord("u"): 4, ord("U"): 8}[escape]
                elif canonical == "unicode-escape" and ord("0") <= escape <= ord("7"):
                    for _ in range(2):
                        if index < len(prepared) and ord("0") <= prepared[index] <= ord("7"):
                            index += 1
                        else:
                            break
                elif escape == ord("N") and canonical == "unicode-escape":
                    index = prepared.index(b"}", index) + 1
            output = prepared[begin:index].decode(codec)
            pieces.append(output)
            for which, scalar in enumerate(output):
                offset += len(scalar.encode())
                decoded_boundaries[offset] = (
                    prepared_boundaries[index] + bom if which == len(output) - 1 else None
                )
        assert "".join(pieces) == text
        decoded_boundaries[len(text.encode())] = len(raw)
        normalized, final_boundaries = normalize(text.encode())
        text = normalized.decode()
        boundaries = [[0, decoded_boundaries[final_boundaries[0]]]]
        offset = 0
        for scalar in text:
            offset += len(scalar.encode())
            boundaries.append([offset, decoded_boundaries[final_boundaries[offset]]])
        return text, boundaries
    for index, byte in enumerate(prepared):
        buffered = decoder.getstate()[0] if canonical == "utf-7" else b""
        output = decoder.decode(bytes([byte]), final=False)
        if buffered.startswith(b"+") and output and buffered + bytes([byte]) != b"+-":
            segment = buffered + bytes([byte])
            start = index - len(buffered)
            size = 0
            alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
            while size + 1 < len(segment) and segment[size + 1] in alphabet:
                size += 1
            terminated = segment[1 + size :] == b"-"
            shifted = output if terminated else output[:-1]
            units = 0
            for which, scalar in enumerate(shifted):
                units += len(scalar.encode("utf-16-be")) // 2
                offset += len(scalar.encode())
                end = start + 1 + units * 16 // 6
                if which == len(shifted) - 1:
                    end = start + 1 + size + int(terminated)
                    raw_boundary = prepared_boundaries[end] + bom
                else:
                    raw_boundary = prepared_boundaries[end] + bom if units * 16 % 6 == 0 else None
                decoded_boundaries[offset] = raw_boundary
            if not terminated:
                offset += len(output[-1].encode())
                decoded_boundaries[offset] = prepared_boundaries[index + 1] + bom
            continue
        for which, scalar in enumerate(output):
            offset += len(scalar.encode())
            decoded_boundaries[offset] = (
                prepared_boundaries[index + 1] + bom if which == len(output) - 1 else None
            )
    assert decoder.decode(b"", final=True) == ""
    decoded_boundaries[len(text.encode())] = len(raw)
    normalized, final_boundaries = normalize(text.encode())
    text = normalized.decode()
    boundaries = [[0, decoded_boundaries[final_boundaries[0]]]]
    offset = 0
    for scalar in text:
        offset += len(scalar.encode())
        boundaries.append([offset, decoded_boundaries[final_boundaries[offset]]])
    return text, boundaries


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
        if "codec_text_error" in case:
            try:
                raw.decode(case["codec"])
            except UnicodeError as error:
                assert type(error).__name__ == case["codec_text_error"], case["id"]
            else:
                raise AssertionError(case["id"])
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
            text, boundaries = decoded_source(raw, case["codec"])
            assert text == expected["text"], case["id"]
            assert boundaries == expected["raw_boundaries"], case["id"]
            if tree:
                actual = shapes(tree, text)
                assert actual["functions"] == expected["functions"], case["id"]
                assert actual["names"] == expected["names"], case["id"]
        else:
            assert case["difference_reason"], case["id"]
            if tree is not None:
                assert case.get("strict_source_rejection") == "decoded-nul", case["id"]
                prepared, _ = normalize(raw)
                assert "\0" in prepared.decode(case["codec"]), case["id"]
    print(f"{len(evidence['cases'])} frozen source-encoding fixtures match")


if __name__ == "__main__":
    main()
