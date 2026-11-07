"""Capture frozen v0.13.0 legacy configuration layering (config/__init__.py).

    PYTHONPATH=<oracle>/src python -I capture_legacy_layers.py <oracle-root> <output.json>

Observes the historical precedence (XDG global, project ici.toml, dev.toml,
ICI_CONFIG), the first-run global default that ``load_config`` writes, the
missing explicit file refusal and ``get_engine_config``.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

LAYERS = {
    "xdg/ici/ici.toml": '[ici]\npolicy_name = "Global policy"\n[engines.line]\nwarn_limit = 600\n',
    "project/ici.toml": "[engines.line]\nwarn_limit = 700\n",
    "project/dev.toml": "[engines.line]\nwarn_limit = 800\nfail_limit = 2000\n",
    "explicit.toml": "[engines.line]\nwarn_limit = 900\n",
}


def write(base, files):
    for name, text in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(oracle / "src"))
    import ici
    from ici.config import ConfigError, get_engine_config, load_config

    assert Path(ici.__file__).resolve().is_relative_to(oracle.resolve())
    observations = {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        write(base, {"project/main.py": "x = 1\n"})
        (base / "xdg").mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(base / "xdg")
        os.environ.pop("ICI_CONFIG", None)
        config = load_config(base / "project", create_global_default=True)
        written = base / "xdg/ici/ici.toml"
        observations["first_run_global_default"] = {
            "global_file_written": written.is_file(),
            "global_file_has_default_policy": "[engines.line]" in written.read_text(),
            "warn_limit": config["engines"]["line"]["warn_limit"],
        }
        observations["get_engine_config"] = {
            "line": get_engine_config(config, "line"),
            "missing": get_engine_config(config, "no-such-engine"),
        }
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        write(base, LAYERS)
        os.environ["XDG_CONFIG_HOME"] = str(base / "xdg")
        merged = {}
        for label, explicit in (("without_explicit", None), ("with_explicit", "explicit.toml")):
            if explicit:
                os.environ["ICI_CONFIG"] = str(base / explicit)
            else:
                os.environ.pop("ICI_CONFIG", None)
            config = load_config(base / "project", create_global_default=False)
            merged[label] = {
                "warn_limit": config["engines"]["line"]["warn_limit"],
                "fail_limit": config["engines"]["line"]["fail_limit"],
                "policy_name": config["ici"]["policy_name"],
            }
        observations["layers"] = {"files": LAYERS, **merged}
        os.environ["ICI_CONFIG"] = str(base / "missing.toml")
        try:
            load_config(base / "project", create_global_default=False)
        except ConfigError as error:
            observations["missing_explicit"] = {
                "outcome": "reject",
                "message": str(error).replace(temporary, "<tmp>"),
            }
        os.environ.pop("ICI_CONFIG", None)
    from ici.config.automatic import automatic

    trees = {
        "python": {"app.py": "x = 1\n", "pkg/mod.py": "y = 2\n"},
        "hybrid": {"app.py": "x = 1\n", "src/main.cpp": "int main() {}\n"},
        "empty": {"README.md": "docs\n"},
        "configured": {"app.py": "x = 1\n", "ici.toml": "[engines.line]\nwarn_limit = 600\n"},
        "subdirectory": {"app.py": "x = 1\n", "sub/inner.py": "z = 3\n"},
    }
    observations["automatic"] = {}
    for name, files in trees.items():
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "checkout"
            write(base, files)
            (base / ".git").mkdir()
            start = base / "sub" if name == "subdirectory" else base
            try:
                found = automatic(start)
            except ValueError as error:
                observed = {"outcome": "reject", "message": str(error).replace(temporary, "<tmp>")}
            else:
                if found is None:
                    observed = {"outcome": "existing_config"}
                else:
                    root, config = found
                    component = config.components[0]
                    observed = {
                        "outcome": "accept",
                        "root_is_checkout": root == base.resolve(),
                        "components": [item.id for item in config.components],
                        "component_root": component.root.value,
                        "languages": list(component.languages.value),
                        "exclude": list(component.exclude),
                        "profile": config.profile.value,
                    }
            observations["automatic"][name] = {"files": files, **observed}
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "reader": "ici.config.load_config / get_engine_config",
        "observations": observations,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True)
        stream.write("\n")
    print(json.dumps(observations["layers"], sort_keys=True))


if __name__ == "__main__":
    main()
