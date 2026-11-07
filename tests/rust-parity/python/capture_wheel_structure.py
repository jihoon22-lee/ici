"""Development-only capture: frozen wheel structure refusals package.json does not cover (F3).

    python -I capture_wheel_structure.py <v0.13.0 checkout>

Builds three wheels from the `pure-wheel` case of package.json with one structural change each
(non-UTF-8 METADATA, a second `.dist-info` directory, the only `.dist-info` directory nested
below the archive root), plus five refusals frozen raises as PythonPackagingError
(`[project]` not a table, a non-string version, absolute or `..` wheel globs, more wheels than
`max_wheels`), runs frozen `analyze_python_packaging` and writes `wheel-structure.json` in the
package.json case shape (`original` = frozen result, `original_error` = frozen refusal).
"""

import base64
import hashlib
import importlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from package_oracle import finding, target

HERE = Path(__file__).resolve().parent


def record(members: dict[str, bytes], name: str) -> bytes:
    rows = []
    for member, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        rows.append(f"{member},sha256={digest.decode()},{len(payload)}")
    rows.append(f"{name},,")
    return ("\n".join(rows) + "\n").encode()


def wheel(members: dict[str, bytes], record_name: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
        archive.writestr(record_name, record(members, record_name))
    return base64.b64encode(buffer.getvalue()).decode()


def cases(base: dict) -> list[dict]:
    wheel_text = (
        b"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    package = {"demo/__init__.py": b"VALUE = 1\n", "demo/cli.py": b"def main():\n    return 0\n"}

    def members(directory: str, metadata: bytes) -> dict[str, bytes]:
        return {
            f"{directory}/WHEEL": wheel_text,
            f"{directory}/METADATA": metadata,
            **package,
            f"{directory}/entry_points.txt": b"[console_scripts]\ndemo = demo.cli:main\n",
        }

    good = b"Name: demo\nVersion: 1.2.3\n"
    second = {**members("demo-1.2.3.dist-info", good), "extra-1.0.dist-info/NOTES": b"x\n"}
    shapes = {
        "wheel-metadata-not-utf8": (
            members("demo-1.2.3.dist-info", b"Name: demo\xff\nVersion: 1.2.3\n"),
            "demo-1.2.3.dist-info/RECORD",
        ),
        "wheel-two-dist-info-directories": (second, "demo-1.2.3.dist-info/RECORD"),
        "wheel-nested-dist-info-directory": (
            members("lib/demo-1.2.3.dist-info", good),
            "lib/demo-1.2.3.dist-info/RECORD",
        ),
    }
    structure = [
        {
            "id": name,
            "sources": base["sources"],
            "pyproject": base["pyproject"],
            "options": base["options"],
            "artifacts": {"dist/demo-1.2.3-py3-none-any.whl": wheel(entries, record_name)},
        }
        for name, (entries, record_name) in shapes.items()
    ]
    pure = base["artifacts"]["dist/demo-1.2.3-py3-none-any.whl"]
    refusals = {
        "pyproject-project-not-table": ("project = 1\n", base["options"], {}),
        "pyproject-version-not-string": (
            '[project]\nname = "demo"\nversion = 1\n',
            base["options"],
            {},
        ),
        "wheel-glob-absolute": (
            base["pyproject"],
            {**base["options"], "wheel_globs": ["/abs/*.whl"]},
            {},
        ),
        "wheel-glob-parent": (
            base["pyproject"],
            {**base["options"], "wheel_globs": ["../*.whl"]},
            {},
        ),
        "wheel-match-count-bound": (
            base["pyproject"],
            {**base["options"], "max_wheels": 1},
            {
                "dist/demo-1.2.3-py3-none-any.whl": pure,
                "dist/demo-1.2.4-py3-none-any.whl": pure,
            },
        ),
    }
    return structure + [
        {
            "id": name,
            "sources": base["sources"],
            "pyproject": pyproject,
            "options": options,
            "artifacts": artifacts,
            "refusal": True,
        }
        for name, (pyproject, options, artifacts) in refusals.items()
    ]


def main() -> None:
    fixture = json.loads((HERE / "package.json").read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve()
    for name, digest in fixture["oracle_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    sys.path.insert(0, str(root / "src"))
    module = importlib.import_module("ici.analysis._python_packaging")
    assert Path(module.__file__).is_relative_to(root)
    base = next(case for case in fixture["cases"] if case["id"] == "pure-wheel")
    captured = []
    for case in cases(base):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, text in case["sources"].items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            (directory / "pyproject.toml").write_text(case["pyproject"], encoding="utf-8")
            for name, encoded in case["artifacts"].items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(encoded, validate=True))
            options = {**case["options"], "wheel_globs": tuple(case["options"]["wheel_globs"])}
            if case.pop("refusal", False):
                try:
                    module.analyze_python_packaging(
                        directory,
                        [directory / name for name in case["sources"]],
                        module.PackagingPolicy(**options),
                    )
                except module.PythonPackagingError as error:
                    case["original_error"] = {"type": type(error).__name__, "message": str(error)}
                captured.append(case)
                continue
            result = module.analyze_python_packaging(
                directory,
                [directory / name for name in case["sources"]],
                module.PackagingPolicy(**options),
            )
            case["original"] = json.loads(
                json.dumps(
                    {
                        "targets": [target(value) for value in result.targets],
                        "findings": [finding(value) for value in result.findings],
                        "metadata": result.metadata,
                        "failures": result.failures,
                        "warnings": result.warnings,
                    }
                )
            )
        captured.append(case)
    payload = {"oracle_files": fixture["oracle_files"], "cases": captured}
    out = HERE / "wheel-structure.json"
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for case in captured:
        if "original_error" in case:
            print(case["id"], "refused:", case["original_error"]["message"])
            continue
        print(case["id"], json.dumps(case["original"]["targets"][-1]["message"]))
        print("  findings:", [item["message"] for item in case["original"]["findings"]])


if __name__ == "__main__":
    main()
