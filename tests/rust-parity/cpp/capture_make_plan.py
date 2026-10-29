"""Development-only frozen Make plan oracle capture; never called by the Rust product."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))
from ici.core.context import BuildVariant  # noqa: E402
from ici.core.make import MakeConfigError, make_plan  # noqa: E402

BASE = {
    "enabled": True,
    "workdir": ".",
    "shadow_dir": "out",
    "out_of_tree": "allow",
    "configure_argv": [],
    "build_argv": ["make", "--jobs", "{jobs}", "all"],
    "test_argv": ["make", "check"],
    "clean_argv": ["make", "clean"],
    "coverage_build_argv": ["make", "coverage"],
    "jobs": 3,
}
DROP = object()
CASES = [
    ("base", {}),
    ("defaults", {"workdir": DROP, "shadow_dir": DROP, "jobs": DROP, "out_of_tree": DROP}),
    ("jobs-one", {"jobs": 1}),
    ("jobs-zero", {"jobs": 0}),
    ("jobs-65", {"jobs": 65}),
    ("jobs-bool", {"jobs": True}),
    ("jobs-float", {"jobs": 3.0}),
    ("out-of-tree-required-root", {"out_of_tree": "required"}),
    ("out-of-tree-required-absent", {"out_of_tree": "required", "workdir": DROP}),
    ("out-of-tree-required-sub", {"out_of_tree": "required", "workdir": "sub"}),
    ("out-of-tree-other", {"out_of_tree": "never"}),
    ("shadow-root", {"shadow_dir": "."}),
    ("workdir-parent", {"workdir": "../x"}),
    ("workdir-absolute", {"workdir": "/tmp"}),
    ("workdir-backslash", {"workdir": "a\\b"}),
    ("workdir-drive", {"workdir": "C:x"}),
    ("shadow-parent", {"shadow_dir": "a/../.."}),
    ("upper-c", {"build_argv": ["make", "-C", "sub", "all"]}),
    ("attached-jobs", {"build_argv": ["make", "-j{jobs}"]}),
    ("equals-jobs", {"build_argv": ["make", "--jobs={jobs}"]}),
    ("unknown-placeholder", {"build_argv": ["make", "{foo}"]}),
    ("brace", {"build_argv": ["make", "a}"]}),
    ("dollar", {"build_argv": ["make", "$(touch nope)"]}),
    ("backtick", {"build_argv": ["make", "`id`"]}),
    ("control", {"build_argv": ["make", "a\tb"]}),
    ("empty-token", {"build_argv": ["make", ""]}),
    ("tokens-64", {"build_argv": ["make"] + ["x"] * 63}),
    ("tokens-65", {"build_argv": ["make"] + ["x"] * 64}),
    ("token-1024", {"build_argv": ["make", "x" * 1024]}),
    ("token-1025", {"build_argv": ["make", "x" * 1025]}),
    ("aggregate", {"build_argv": ["make"] + ["x" * 1000] * 33}),
    ("shell-upper", {"build_argv": ["BASH", "x"]}),
    ("shell-path", {"build_argv": ["/bin/fish", "x"]}),
    ("shell-pwsh", {"build_argv": ["pwsh"]}),
    ("shell-exe", {"build_argv": ["bash.exe", "x"]}),
    ("flag-slash-c", {"build_argv": ["make", "/c"]}),
    ("flag-command", {"build_argv": ["make", "-Command"]}),
    ("flag-first", {"build_argv": ["-c", "make"]}),
    ("build-empty", {"build_argv": []}),
    ("clean-shell", {"clean_argv": ["sh", "-c", "rm"]}),
    ("configure-jobs", {"configure_argv": ["./configure", "{jobs}"]}),
    ("test-dollar", {"test_argv": ["make", "$X"]}),
]
VARIANT_CASES = [
    ("coverage", "coverage", {}),
    ("coverage-test", "coverage", {"coverage_test_argv": ["make", "covcheck"]}),
    ("sanitize-missing", "sanitize", {}),
    ("thread-sanitize", "thread-sanitize", {"thread_sanitize_build_argv": ["make", "tsan"]}),
]


def table(overrides):
    value = dict(BASE)
    for key, item in overrides.items():
        if item is DROP:
            value.pop(key, None)
        else:
            value[key] = item
    return value


def capture(name, variant, overrides, root):
    config = {"build": {"make": table(overrides)}}
    try:
        plan = make_plan(root, config, BuildVariant(variant))
    except MakeConfigError as err:
        return {
            "name": name,
            "variant": variant,
            "make": config["build"]["make"],
            "error": str(err),
        }
    return {
        "name": name,
        "variant": variant,
        "make": config["build"]["make"],
        "workdir": plan.workdir.relative_to(root).as_posix() or ".",
        "shadow": plan.shadow.relative_to(root).as_posix(),
        "configure_argv": list(plan.configure_argv),
        "clean_argv": list(plan.clean_argv),
        "build_argv": list(plan.build_argv),
        "test_argv": list(plan.test_argv),
        "jobs": plan.jobs,
        "out_of_tree": plan.out_of_tree,
    }


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    (root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    records = [capture(name, "release", overrides, root) for name, overrides in CASES]
    records += [
        capture(name, variant, overrides, root) for name, variant, overrides in VARIANT_CASES
    ]

output = ROOT / "tests/rust-parity/cpp/make-plan-oracle.json"
output.write_text(
    json.dumps({"oracle": EXPECTED, "cases": records}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"{len(records)} cases -> {output}")
