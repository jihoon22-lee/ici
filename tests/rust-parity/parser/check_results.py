"""Check native parser evidence without treating counts as analysis parity."""

import json
import sys
from pathlib import Path


def main():
    corpus = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    results = {item["id"]: item for item in output["results"]}
    assert set(results) == {case["id"] for case in corpus["cases"]}
    for case in corpus["cases"]:
        result = results[case["id"]]
        assert result["grammar_complete"] == case["expected_grammar_complete"], case["id"]
        assert result.get("syntax_valid") == case.get("expected_syntax_valid"), case["id"]
        assert result.get("failure") == case.get("expected_failure"), case["id"]

    def names(case):
        return [token for token in results[case]["tokens"] if token["kind"] == "Name"]

    unicode_names = names("unicode_columns")
    assert [item["start"] for item in unicode_names] == [0, 15, 21]
    assert [item["end"] for item in unicode_names] == [9, 18, 30]
    assert [item["position"]["byte_column"] for item in unicode_names] == [0, 15, 21]
    assert [item["position"]["character_column"] for item in unicode_names] == [0, 9, 13]
    assert names("crlf")[1]["start"] == 12
    assert names("lone_cr")[1]["start"] == 11
    assert names("crlf")[1]["position"]["line"] == 2
    assert names("lone_cr")[1]["position"]["line"] == 2
    assert results["bom_raw"]["bom_bytes"] == 3
    assert names("bom_raw")[0]["start"] == 0
    normalized = [
        node["name"] for node in results["unicode_combining"]["nodes"] if node["kind"] == "ExprName"
    ]
    assert normalized == ["café", "print", "café"]
    assert names("unicode_combining")[0]["text"] == "cafe\u0301"
    scope = results["scope_forms"]
    for kind, count in {
        "StmtGlobal": 1,
        "StmtNonlocal": 1,
        "StmtFunctionDef": 3,
        "Comprehension": 1,
        "ExprAwait": 1,
    }.items():
        assert scope["node_counts"][kind] == count, kind
    assert [
        (node["name"], node["asname"]) for node in scope["nodes"] if node["kind"] == "Alias"
    ] == [("subprocess", "sp"), ("Path", "P")]
    assert any(node.get("is_async") for node in scope["nodes"])
    assert results["malformed_recovery"]["node_counts"]["StmtAssign"] == 2
    assert results["malformed_recovery"]["errors"]
    assert not results["malformed_recovery"]["grammar_complete"]
    assert results["type_alias_old"]["syntax_valid"]
    assert results["type_alias_old"]["unsupported"]
    assert not results["type_alias_old"]["grammar_complete"]
    trivia = {token["kind"] for token in results["token_trivia"]["tokens"]}
    assert {"Comment", "NonLogicalNewline"} <= trivia
    print(f"{len(results)} native parser/decode fixtures and location/scope-shape checks passed")


if __name__ == "__main__":
    main()
