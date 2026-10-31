"""Verify token/region inputs and shared matching/clustering evidence."""

import hashlib
import importlib
import json
import sys
from pathlib import Path


def main():
    fixture = json.loads(Path(__file__).with_name("type2.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    for name, digest in fixture["oracle_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    sys.path.insert(0, str(root / "src"))
    matching = importlib.import_module("ici.analysis._dup_matching")
    clustering = importlib.import_module("ici.analysis._dup_clustering")
    tokens = importlib.import_module("ici.analysis._python_dup_tokenization")
    regions = importlib.import_module("ici.analysis._dup_regions")
    assert Path(matching.__file__).is_relative_to(root)
    limits = matching.DuplicateMatchLimits(2048, 100000, 20000, 250000, 5000000, 10000)
    for case in fixture["cases"]:
        files = []
        for file in case["files"]:
            indexed = list(tokens.tokenize_python_lines(file["raw_text"]))
            scopes = regions.python_duplicate_regions(
                file["raw_text"], (line for line, _ in indexed)
            )
            assert json.loads(json.dumps(indexed)) == file["indexed"], case["id"]
            assert json.loads(json.dumps(scopes)) == file["regions"], case["id"]
            files.append(
                matching.DuplicateFileData(
                    file["file_path"],
                    file["language"],
                    file["raw_text"].splitlines(keepends=True),
                    indexed,
                    scopes,
                )
            )
        matches = matching.filter_subsumed_matches(
            matching.find_raw_matches(files, case["window_size"], limits)
        )
        result = clustering.cluster_matches(matches, files)
        actual = []
        for group in result.groups:
            actual.append(
                {
                    "id": group.index,
                    "fingerprint": group.fingerprint,
                    "fingerprint_algorithm": "sha256/type2-region-v2",
                    "detection": "type2-lexical-region",
                    "language": group.language,
                    "lines_count": group.line_count,
                    "occurrences_count": len(group.locations),
                    "occurrences": [
                        {
                            "file_path": files[index].file_path,
                            "start_line": start,
                            "end_line": end,
                            "loc": f"{files[index].file_path}:{start}-{end}",
                        }
                        for index, start, end in group.locations
                    ],
                    "snippet": group.snippet,
                }
            )
        assert actual == case["expected_groups"], case["id"]
        assert len(result.duplicated_positions) == case["duplicated_lines"], case["id"]
        assert sum(len(file.indexed) for file in files) == case["total_code_lines"], case["id"]
    print(f"{len(fixture['cases'])} fixed shared Type-2 fixtures match")


if __name__ == "__main__":
    main()
