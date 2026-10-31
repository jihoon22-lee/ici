"""Development-only frozen Python comparison of an actually captured native GCOV text file."""

import argparse
import hashlib
import importlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[3]
REVISION = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tool-sources", type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.input.read_text())
    archive = subprocess.check_output(["git", "archive", REVISION, "src/ici"], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="ici-gcov-text-reference-") as temporary:
        reference = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle.getmembers():
                name = PurePosixPath(member.name)
                if not member.isfile():
                    continue
                if name.is_absolute() or ".." in name.parts or name.parts[:2] != ("src", "ici"):
                    raise ValueError("reference archive contains an unsafe member")
                target = reference.joinpath(*name.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                assert source is not None
                target.write_bytes(source.read())
        sys.path.insert(0, str(reference / "src"))
        coverage = importlib.import_module("ici.analysis.coverage_support")
        project = reference / "project"
        reports = project / "reports"
        reports.mkdir(parents=True)
        (project / "main.c").write_text(document["source"])
        (reports / "main.c.gcov").write_text(document["report"].replace("<ROOT>", str(project)))
        rows = coverage.parse_gcov_dir(reports, {"main.c"}, project)
        functions = coverage.parse_gcov_functions(reports, {"main.c"}, project)
        assert len(rows) == 1
        counts = document["counts"]
        assert all(rows[0][key] == counts[key] for key in ("stmts", "covered", "miss", "nb", "cb"))
        assert len(functions) == counts["functions"]
        assert sum(row["covered"] for row in functions) == counts["covered_functions"]
        names = ("src/ici/analysis/coverage_support.py", "src/ici/application/tem.py")
        document["oracle"] = {
            "source_sha": REVISION,
            "file_sha256": {
                name: hashlib.sha256((reference / name).read_bytes()).hexdigest() for name in names
            },
            "coverage_rows": rows,
            "function_rows": functions,
        }
    document["tool_sources"] = json.loads(args.tool_sources.read_text())
    document["capture_command"] = (
        "cargo test -p ici-validation --test native_gcov_text --locked -- "
        "--include-ignored --test-threads=1; explicit GCC/gcov7.4.0 and15.2.0 paths"
    )
    args.output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
