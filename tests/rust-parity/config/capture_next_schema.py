"""Capture frozen v0.13.0 ici-next (schema_version = 1) verdicts for the native replay.

    PYTHONPATH=<oracle>/src python -I capture_next_schema.py <oracle-root> <output.json>

Each case is a small project tree (root ``ici.toml``, optional child component
files and an optional ``--local-config`` overlay). The frozen
``ici.config.discovery.load`` result is projected to flat keys (only the keys a
case selects) or recorded as every reported problem with its file and key.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = 'schema_version = 1\n[workspace]\nname = "demo"\n'
APP = '[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n'
LIB = '[[components]]\nid = "lib"\nroot = "pkg"\nlanguages = ["python"]\n'
CASE = (
    '[[components.integrations]]\nname = "smoke"\nargv = ["{python:py}", "-V"]\n'
    '[components.integrations.python_targets]\npy = "python3"\n'
)
FILES = {"app.py": "x = 1\n", "pkg/mod.py": "y = 2\n"}


def case(identifier, key, root, select=(), files=None, local=None, explicit=None):
    return {
        "id": identifier,
        "key": key,
        "files": {"ici.toml": root, **FILES, **(files or {})},
        "local": local,
        "explicit": explicit,
        "select": list(select),
    }


def integration(field, value, select=True):
    """APP with one integration case whose field is set to a TOML value spelling."""
    text = (
        ROOT
        + APP
        + CASE.replace(
            "[components.integrations.python_targets]",
            f"{field} = {value}\n[components.integrations.python_targets]",
        )
    )
    return text, [f"components.app.integrations[0].{field}"] if select else []


def cases():
    out = []
    add = out.append
    k = "components[].integrations[]"
    # workspace / schema
    add(case("workspace:positive", "workspace", ROOT + APP, ["workspace.name"]))
    add(case("workspace:failure0", "workspace", "schema_version = 1\nworkspace = 5\n" + APP))
    add(
        case(
            "workspace:failure1", "workspace", "schema_version = 1\n[workspace]\nbogus = 1\n" + APP
        )
    )
    add(case("workspace.name:positive", "workspace.name", ROOT + APP, ["workspace.name"]))
    add(
        case(
            "workspace.name:empty",
            "workspace.name",
            ROOT.replace('"demo"', '""') + APP,
            ["workspace.name"],
        )
    )
    add(case("workspace.name:failure0", "workspace.name", ROOT.replace('"demo"', "3") + APP))
    add(
        case(
            "workspace.profile:positive",
            "workspace.profile",
            ROOT + 'profile = "deep"\n' + APP,
            ["workspace.profile"],
        )
    )
    add(
        case(
            "workspace.profile:unknown_value",
            "workspace.profile",
            ROOT + 'profile = "slow"\n' + APP,
            ["workspace.profile"],
        )
    )
    add(case("workspace.profile:failure0", "workspace.profile", ROOT + "profile = 2\n" + APP))
    add(case("schema_version:positive", "schema_version", ROOT + APP, ["workspace.name"]))
    add(case("schema_version:failure0", "schema_version", ROOT.replace("= 1", "= 3") + APP))
    add(case("schema_version:failure1", "schema_version", ROOT.replace("= 1", '= "1"') + APP))
    add(
        case(
            "schema_version:failure2",
            "schema_version",
            ROOT.replace("schema_version = 1\n", "") + APP,
        )
    )
    add(case("<top>:unknown", "", ROOT + "bogus = 1\n" + APP))
    # checks
    checks = ROOT + "[checks.lint]\nenabled = false\nrequired = true\n" + APP
    add(
        case(
            "checks:positive",
            "checks",
            checks,
            ["checks.lint.enabled", "checks.lint.required", "components.app.checks.lint.enabled"],
        )
    )
    add(case("checks:failure0", "checks", ROOT + "checks = 5\n" + APP))
    add(case("checks:failure1", "checks", ROOT + "[checks]\nlint = 5\n" + APP))
    add(case("checks.*.enabled:positive", "checks.*.enabled", checks, ["checks.lint.enabled"]))
    add(
        case(
            "checks.*.enabled:failure0",
            "checks.*.enabled",
            ROOT + '[checks.lint]\nenabled = "no"\n' + APP,
        )
    )
    add(case("checks.*.required:positive", "checks.*.required", checks, ["checks.lint.required"]))
    add(
        case(
            "checks.*.required:failure0",
            "checks.*.required",
            ROOT + "[checks.lint]\nrequired = 1\n" + APP,
        )
    )
    add(case("checks.*:unknown", "checks", ROOT + "[checks.lint]\nbogus = 1\n" + APP))
    exempt = (
        ROOT
        + '[checks.lint]\nrequired = true\n[checks.lint.exemptions.app]\nreason = "legacy module"\n'
        + APP
        + "[components.checks.lint]\nrequired = false\n"
    )
    add(
        case(
            "checks.*.exemptions:positive",
            "checks.*.exemptions",
            exempt,
            ["components.app.checks.lint.required"],
        )
    )
    add(
        case(
            "checks.*.exemptions:failure0",
            "checks.*.exemptions",
            ROOT + "[checks.lint]\nexemptions = 5\n" + APP,
        )
    )
    add(
        case(
            "checks.*.exemptions.*.reason:positive",
            "checks.*.exemptions.*.reason",
            exempt,
            ["components.app.checks.lint.required"],
        )
    )
    add(
        case(
            "checks.*.exemptions.*.reason:failure0",
            "checks.*.exemptions.*.reason",
            exempt.replace('reason = "legacy module"\n', ""),
        )
    )
    add(
        case(
            "checks.*.exemptions.*.reason:failure1",
            "checks.*.exemptions.*.reason",
            exempt.replace('"legacy module"', "3"),
        )
    )
    add(
        case(
            "checks.*.exemptions:unknown",
            "checks.*.exemptions",
            exempt.replace('reason = "legacy module"\n', 'reason = "x"\nbogus = 1\n'),
        )
    )
    weaken = (
        ROOT
        + "[checks.lint]\nrequired = true\n"
        + APP
        + "[components.checks.lint]\nrequired = false\n"
    )
    add(case("components[].checks:weaken_without_exemption", "components[].checks", weaken))
    # tools
    tools = (
        ROOT + '[tools.ruff]\nsource = "project"\npath = "bin/ruff"\nconfig = "ruff.toml"\n' + APP
    )
    add(case("tools:positive", "tools", tools))
    add(case("tools:failure0", "tools", ROOT + "tools = 5\n" + APP))
    add(case("tools:failure1", "tools", ROOT + "[tools]\nruff = 5\n" + APP))
    add(case("tools.*:unknown", "tools", ROOT + "[tools.ruff]\nbogus = 1\n" + APP))
    for field, bad in (("source", "3"), ("path", "3"), ("config", "true")):
        add(case(f"tools.*.{field}:positive", f"tools.*.{field}", tools))
        add(
            case(
                f"tools.*.{field}:failure0",
                f"tools.*.{field}",
                ROOT + f"[tools.ruff]\n{field} = {bad}\n" + APP,
            )
        )
    add(
        case(
            "tools.*.path:absolute",
            "tools.*.path",
            ROOT + '[tools.ruff]\npath = "/usr/bin/ruff"\n' + APP,
        )
    )
    add(
        case(
            "tools.*.config:escape",
            "tools.*.config",
            ROOT + '[tools.ruff]\nconfig = "../ruff.toml"\n' + APP,
        )
    )
    # builds
    build = '[builds.native]\nsystem = "cmake"\ndirectory = "out"\nproject = "CMakeLists.txt"\nvariant = "release"\nprepare = "explicit"\nartifacts = ["bin/*"]\n'
    builds = ROOT + build + APP + 'build = "native"\n'
    selected = [
        f"builds.native.{f}"
        for f in ("system", "directory", "project", "variant", "prepare", "artifacts")
    ]
    add(case("builds:positive", "builds", builds, [*selected, "components.app.build"]))
    add(case("builds:failure0", "builds", ROOT + "builds = 5\n" + APP))
    add(case("builds:failure1", "builds", ROOT + "[builds]\nnative = 5\n" + APP))
    add(case("builds.*:unknown", "builds", ROOT + "[builds.native]\nbogus = 1\n" + APP))
    root_dir = ROOT + build.replace('directory = "out"', 'directory = "."') + APP
    add(
        case("builds.*.directory:root", "builds.*.directory", root_dir, ["builds.native.directory"])
    )
    tools_value = ROOT + APP + '[components.python]\ntest_tools = "elsewhere"\n'
    add(
        case(
            "components[].python.test_tools:unknown_value",
            "components[].python.test_tools",
            tools_value,
        )
    )
    add(
        case(
            "builds.*.variant:default",
            "builds.*.variant",
            ROOT + '[builds.native]\nsystem = "make"\ndirectory = "out"\n' + APP,
            ["builds.native.variant"],
        )
    )
    for field, bads in (
        ("system", ["3", '"CMake"']),
        ("directory", ['"../x"', '"/abs"', "3", '"."']),
        ("project", ['"../x"', "4", '"."']),
        ("variant", ["3", '"Release"']),
        ("prepare", ['"auto"', "true"]),
        ("artifacts", ['["/abs/*"]', '["../x"]', '["./x"]', '[""]', '"bin/*"']),
    ):
        add(
            case(
                f"builds.*.{field}:positive",
                f"builds.*.{field}",
                builds,
                [f"builds.native.{field}"],
            )
        )
        for index, bad in enumerate(bads):
            text = ROOT + build.replace(build.split(f"{field} = ")[1].split("\n")[0], bad, 1) + APP
            add(case(f"builds.*.{field}:failure{index}", f"builds.*.{field}", text))
    model = [
        (
            "builds:uppercase_id",
            "builds",
            ROOT + build.replace("[builds.native]", "[builds.Native]") + APP,
        ),
        ("builds:missing_system", "builds", ROOT + '[builds.native]\ndirectory = "out"\n' + APP),
        ("builds:missing_directory", "builds", ROOT + '[builds.native]\nsystem = "make"\n' + APP),
        (
            "builds:shared_directory",
            "builds",
            ROOT + build + build.replace("[builds.native]", "[builds.other]") + APP,
        ),
        (
            "components[].needs:ambiguous",
            "components[].needs",
            ROOT + build + APP + 'needs = ["native"]\n' + APP.replace('"app"', '"native"'),
        ),
        ("components[].needs:self", "components[].needs", ROOT + APP + 'needs = ["app"]\n'),
        (
            "components[].needs:cycle",
            "components[].needs",
            ROOT + APP + 'needs = ["lib"]\n' + LIB + 'needs = ["app"]\n',
        ),
        ("components[].id:uppercase", "components[].id", ROOT + APP.replace('"app"', '"App"')),
        ("workspace.name:padded", "workspace.name", ROOT.replace('"demo"', '" demo "') + APP),
    ]
    for identifier, key, text in model:
        add(case(identifier, key, text))
    # components
    add(
        case(
            "components:positive",
            "components",
            ROOT + APP + LIB,
            ["components.app.root", "components.lib.root"],
        )
    )
    add(case("components:failure0", "components", ROOT + "components = 5\n"))
    add(case("components:failure1", "components", ROOT + "components = [1]\n"))
    add(case("components:empty", "components", ROOT + "components = []\n"))
    add(case("components[]:unknown", "components", ROOT + APP + "bogus = 1\n"))
    add(case("components[].id:positive", "components[].id", ROOT + APP, ["components.app.root"]))
    add(case("components[].id:failure0", "components[].id", ROOT + APP.replace('id = "app"\n', "")))
    add(case("components[].id:failure1", "components[].id", ROOT + APP.replace('"app"', "3")))
    add(case("components[].id:duplicate", "components[].id", ROOT + APP + APP))
    add(case("components[].id:empty", "components[].id", ROOT + APP.replace('"app"', '""')))
    add(
        case(
            "components[].id:slash",
            "components[].id",
            ROOT + APP.replace('"app"', '"svc/api"'),
            ["components.svc/api.root"],
        )
    )
    add(
        case("components[].root:positive", "components[].root", ROOT + LIB, ["components.lib.root"])
    )
    for index, bad in enumerate(["", '"../x"', '"/abs"', "3"]):
        text = ROOT + (
            APP.replace('root = "."\n', f"root = {bad}\n")
            if bad
            else APP.replace('root = "."\n', "")
        )
        add(case(f"components[].root:failure{index}", "components[].root", text))
    add(
        case(
            "components[].languages:positive",
            "components[].languages",
            ROOT + APP.replace('["python"]', '["python", "cpp"]'),
            ["components.app.languages"],
        )
    )
    add(
        case(
            "components[].languages:failure0",
            "components[].languages",
            ROOT + APP.replace('languages = ["python"]\n', ""),
        )
    )
    add(
        case(
            "components[].languages:failure1",
            "components[].languages",
            ROOT + APP.replace('["python"]', '"python"'),
        )
    )
    add(
        case(
            "components[].languages:unknown_value",
            "components[].languages",
            ROOT + APP.replace('["python"]', '["cobol"]'),
            ["components.app.languages"],
        )
    )
    add(case("components[].build:positive", "components[].build", builds, ["components.app.build"]))
    add(case("components[].build:failure0", "components[].build", ROOT + APP + 'build = "ghost"\n'))
    add(case("components[].build:failure1", "components[].build", ROOT + APP + "build = 3\n"))
    for field in ("sources", "include", "exclude", "vendor"):
        text = ROOT + LIB + f'{field} = ["src/**/*.py"]\n'
        add(
            case(
                f"components[].{field}:positive",
                f"components[].{field}",
                text,
                [f"components.lib.{field}"],
            )
        )
        add(
            case(
                f"components[].{field}:failure0",
                f"components[].{field}",
                ROOT + LIB + f'{field} = [""]\n',
            )
        )
        add(
            case(
                f"components[].{field}:failure1",
                f"components[].{field}",
                ROOT + LIB + f'{field} = "src"\n',
            )
        )
    add(
        case(
            "components[].needs:positive",
            "components[].needs",
            ROOT + APP + 'needs = ["lib"]\n' + LIB,
            ["components.app.needs"],
        )
    )
    add(
        case(
            "components[].needs:unknown_target",
            "components[].needs",
            ROOT + APP + 'needs = ["ghost"]\n',
            ["components.app.needs"],
        )
    )
    add(case("components[].needs:failure0", "components[].needs", ROOT + APP + 'needs = "lib"\n'))
    add(
        case(
            "components[].external:positive",
            "components[].external",
            ROOT + APP + 'external = ["third_party/sdk"]\n',
            ["components.app.external"],
        )
    )
    add(
        case(
            "components[].external:failure0", "components[].external", ROOT + APP + "external = 3\n"
        )
    )
    py = '[components.python]\nexecutable = ".venv/bin/python"\ntype_provider = "ty"\ntest_tools = "project"\ntest_paths = ["tests"]\n'
    pysel = [
        "components.app.python.executable",
        "components.app.python.type_provider",
        "components.app.test_paths",
    ]
    add(case("components[].python:positive", "components[].python", ROOT + APP + py, pysel))
    add(case("components[].python:failure0", "components[].python", ROOT + APP + "python = 5\n"))
    add(
        case(
            "components[].python:unknown",
            "components[].python",
            ROOT + APP + "[components.python]\nbogus = 1\n",
        )
    )
    for field, select, bads in (
        ("executable", "python.executable", ['""', "3"]),
        ("type_provider", "python.type_provider", ['"pyright"', "3"]),
        ("test_tools", None, ["3"]),
        ("test_paths", "test_paths", ['[""]', '"tests"']),
    ):
        sel = [f"components.app.{select}"] if select else []
        add(
            case(
                f"components[].python.{field}:positive",
                f"components[].python.{field}",
                ROOT + APP + py,
                sel,
            )
        )
        for index, bad in enumerate(bads):
            add(
                case(
                    f"components[].python.{field}:failure{index}",
                    f"components[].python.{field}",
                    ROOT + APP + f"[components.python]\n{field} = {bad}\n",
                )
            )
    add(
        case(
            "components[].python.executable:bare",
            "components[].python.executable",
            ROOT + APP + '[components.python]\nexecutable = "python3"\n',
            ["components.app.python.executable"],
        )
    )
    add(
        case(
            "components[].cpp:positive",
            "components[].cpp",
            ROOT + APP + "[components.cpp]\nqt = true\n",
        )
    )
    add(case("components[].cpp:failure0", "components[].cpp", ROOT + APP + "cpp = 5\n"))
    add(
        case(
            "components[].cpp:unknown",
            "components[].cpp",
            ROOT + APP + "[components.cpp]\nbogus = 1\n",
        )
    )
    add(
        case(
            "components[].cpp.qt:positive",
            "components[].cpp.qt",
            ROOT + APP + "[components.cpp]\nqt = true\n",
        )
    )
    add(
        case(
            "components[].cpp.qt:failure0",
            "components[].cpp.qt",
            ROOT + APP + '[components.cpp]\nqt = "yes"\n',
        )
    )
    cchk = ROOT + APP + "[components.checks.lint]\nenabled = false\nrequired = true\n"
    add(
        case(
            "components[].checks:positive",
            "components[].checks",
            cchk,
            ["components.app.checks.lint.enabled", "components.app.checks.lint.required"],
        )
    )
    add(case("components[].checks:failure0", "components[].checks", ROOT + APP + "checks = 5\n"))
    add(
        case(
            "components[].checks.*.enabled:positive",
            "components[].checks.*.enabled",
            cchk,
            ["components.app.checks.lint.enabled"],
        )
    )
    add(
        case(
            "components[].checks.*.enabled:failure0",
            "components[].checks.*.enabled",
            ROOT + APP + '[components.checks.lint]\nenabled = "x"\n',
        )
    )
    add(
        case(
            "components[].checks.*.required:positive",
            "components[].checks.*.required",
            cchk,
            ["components.app.checks.lint.required"],
        )
    )
    add(
        case(
            "components[].checks.*.required:failure0",
            "components[].checks.*.required",
            ROOT + APP + "[components.checks.lint]\nrequired = 2\n",
        )
    )
    cex = ROOT + APP + '[components.checks.lint.exemptions.app]\nreason = "nested"\n'
    add(
        case(
            "components[].checks.*.exemptions:positive",
            "components[].checks.*.exemptions",
            cex,
            ["components.app.checks.lint.enabled"],
        )
    )
    add(
        case(
            "components[].checks.*.exemptions:failure0",
            "components[].checks.*.exemptions",
            ROOT + APP + "[components.checks.lint]\nexemptions = 3\n",
        )
    )
    # references and child component files
    child = 'schema_version = 1\n[component]\nroot = "."\nlanguages = ["python"]\n[python]\nexecutable = "venv/bin/python"\n[cpp]\nqt = true\n'
    ref = ROOT + '[[components]]\nid = "lib"\nconfig = "pkg/ici.toml"\n'
    csel = ["components.lib.root", "components.lib.python.executable"]
    add(
        case(
            "components[].config:positive",
            "components[].config",
            ref,
            csel,
            files={"pkg/ici.toml": child},
        )
    )
    add(
        case(
            "components[].config:failure0",
            "components[].config",
            ref + 'root = "pkg"\n',
            files={"pkg/ici.toml": child},
        )
    )
    add(case("components[].config:failure1", "components[].config", ref, files={}))
    add(
        case(
            "components[].config:failure2",
            "components[].config",
            ROOT + '[[components]]\nid = "lib"\nconfig = 3\n',
        )
    )
    add(
        case(
            "components[].config:failure3",
            "components[].config",
            ROOT + '[[components]]\nconfig = "pkg/ici.toml"\n',
            files={"pkg/ici.toml": child},
        )
    )
    add(
        case(
            "components[].config:escape",
            "components[].config",
            ROOT + '[[components]]\nid = "lib"\nconfig = "../ici.toml"\n',
        )
    )
    add(
        case(
            "components[].config:child_workspace",
            "components[].config",
            ref,
            files={"pkg/ici.toml": ROOT + child},
        )
    )
    add(
        case(
            "components[].config:child_unknown",
            "components[].config",
            ref,
            files={"pkg/ici.toml": child + "bogus = 1\n"},
        )
    )
    add(
        case(
            "python.executable:positive",
            "python.executable",
            ref,
            ["components.lib.python.executable"],
            files={"pkg/ici.toml": child},
        )
    )
    add(
        case(
            "python.executable:failure0",
            "python.executable",
            ref,
            files={"pkg/ici.toml": child.replace('"venv/bin/python"', '""')},
        )
    )
    add(
        case(
            "python.type_provider:positive",
            "python.type_provider",
            ref,
            ["components.lib.python.type_provider"],
            files={
                "pkg/ici.toml": child.replace("[python]\n", '[python]\ntype_provider = "mypy"\n')
            },
        )
    )
    add(
        case(
            "python.type_provider:failure0",
            "python.type_provider",
            ref,
            files={"pkg/ici.toml": child.replace("[python]\n", '[python]\ntype_provider = "x"\n')},
        )
    )
    add(
        case(
            "python.test_tools:positive",
            "python.test_tools",
            ref,
            files={
                "pkg/ici.toml": child.replace("[python]\n", '[python]\ntest_tools = "bundle"\n')
            },
        )
    )
    add(
        case(
            "python.test_tools:failure0",
            "python.test_tools",
            ref,
            files={"pkg/ici.toml": child.replace("[python]\n", "[python]\ntest_tools = 1\n")},
        )
    )
    add(
        case(
            "python.test_paths:positive",
            "python.test_paths",
            ref,
            ["components.lib.test_paths"],
            files={
                "pkg/ici.toml": child.replace("[python]\n", '[python]\ntest_paths = ["tests"]\n')
            },
        )
    )
    add(
        case(
            "python.test_paths:failure0",
            "python.test_paths",
            ref,
            files={"pkg/ici.toml": child.replace("[python]\n", '[python]\ntest_paths = [""]\n')},
        )
    )
    add(case("cpp.qt:positive", "cpp.qt", ref, files={"pkg/ici.toml": child}))
    add(
        case(
            "cpp.qt:failure0",
            "cpp.qt",
            ref,
            files={"pkg/ici.toml": child.replace("qt = true", 'qt = "on"')},
        )
    )
    # standalone component files (explicit --config)
    standalone = 'schema_version = 1\n[component]\nroot = "."\nlanguages = ["python"]\n'
    ssel = ["components.<standalone>.root", "components.<standalone>.languages", "scope_kind"]
    add(case("component:positive", "component", standalone, ssel, explicit="ici.toml"))
    add(
        case(
            "component:failure0",
            "component",
            "schema_version = 1\ncomponent = 5\n",
            explicit="ici.toml",
        )
    )
    add(case("component:not_promoted", "component", standalone))
    add(
        case(
            "component:with_workspace",
            "component",
            standalone + '[workspace]\nname = "x"\n',
            explicit="ici.toml",
        )
    )
    add(case("component:unknown", "component", standalone + "bogus = 1\n", explicit="ici.toml"))
    for field, value, sel, bads in (
        ("id", '"named"', [], ["3"]),
        ("root", '"pkg"', ["components.<standalone>.root"], ['"../x"', "3"]),
        ("languages", '["python", "cpp"]', ["components.<standalone>.languages"], ['"python"']),
        ("build", '"native"', [], ["3"]),
        ("sources", '["**/*.py"]', ["components.<standalone>.sources"], ['[""]']),
        ("include", '["**/*.py"]', ["components.<standalone>.include"], ["3"]),
        ("exclude", '["gen/**"]', ["components.<standalone>.exclude"], ['[""]']),
        ("vendor", '["third/**"]', ["components.<standalone>.vendor"], ["3"]),
        ("needs", '["other"]', [], ["3"]),
        ("external", '["sdk"]', ["components.<standalone>.external"], ["3"]),
    ):
        base = (
            standalone.replace("[component]\n", "[component]\n")
            if field != "root"
            else standalone.replace('root = "."\n', "")
        )
        if field == "languages":
            base = standalone.replace('languages = ["python"]\n', "")
        add(
            case(
                f"component.{field}:positive",
                f"component.{field}",
                base + f"{field} = {value}\n",
                sel,
                explicit="ici.toml",
            )
        )
        for index, bad in enumerate(bads):
            add(
                case(
                    f"component.{field}:failure{index}",
                    f"component.{field}",
                    base + f"{field} = {bad}\n",
                    explicit="ici.toml",
                )
            )
    add(
        case(
            "component.python:positive",
            "component.python",
            standalone + '[python]\nexecutable = "python3"\n',
            ["components.<standalone>.python.executable"],
            explicit="ici.toml",
        )
    )
    add(
        case(
            "component.python:failure0",
            "component.python",
            standalone + "python = 3\n",
            explicit="ici.toml",
        )
    )
    add(
        case(
            "component.cpp:positive",
            "component.cpp",
            standalone + "[cpp]\nqt = false\n",
            explicit="ici.toml",
        )
    )
    add(
        case(
            "component.cpp:failure0", "component.cpp", standalone + "cpp = 3\n", explicit="ici.toml"
        )
    )
    add(
        case(
            "component.checks:positive",
            "component.checks",
            standalone + "[checks.lint]\nenabled = false\n",
            ["components.<standalone>.checks.lint.enabled"],
            explicit="ici.toml",
        )
    )
    add(
        case(
            "component.checks:failure0",
            "component.checks",
            standalone + "checks = 3\n",
            explicit="ici.toml",
        )
    )
    sint = (
        standalone.replace("[component]\n", "[component]\n")
        + '[[component.integrations]]\nname = "s"\nargv = ["{python:py}"]\n[component.integrations.python_targets]\npy = "python3"\n'
    )
    add(
        case(
            "component.integrations:positive",
            "component.integrations",
            sint,
            ["components.<standalone>.integrations[0].name"],
            explicit="ici.toml",
        )
    )
    add(
        case(
            "component.integrations:failure0",
            "component.integrations",
            standalone + "integrations = 3\n",
            explicit="ici.toml",
        )
    )
    # a registered child [component] may name its workspace's builds and siblings
    child_build = child.replace("[python]", 'build = "native"\nneeds = ["app"]\n[python]')
    child_root = ROOT + build + APP + '[[components]]\nid = "lib"\nconfig = "pkg/ici.toml"\n'
    for field in ("build", "needs"):
        add(
            case(
                f"component.{field}:child",
                f"component.{field}",
                child_root,
                [f"components.lib.{field}"],
                files={"pkg/ici.toml": child_build},
            )
        )
    # integrations
    add(
        case(
            "components[].integrations:positive",
            "components[].integrations",
            ROOT + APP + CASE,
            ["components.app.integrations[0].name", "components.app.integrations[0].argv"],
        )
    )
    add(
        case(
            "components[].integrations:failure0",
            "components[].integrations",
            ROOT + APP + "integrations = 3\n",
        )
    )
    add(
        case(
            "components[].integrations:too_many",
            "components[].integrations",
            ROOT + APP + "".join(CASE.replace('"smoke"', f'"c{i}"') for i in range(33)),
        )
    )
    add(case(f"{k}:unknown", f"{k}", *integration("bogus", "1", select=False)))
    add(case(f"{k}.name:failure0", f"{k}.name", ROOT + APP + CASE.replace('name = "smoke"\n', "")))
    add(
        case(
            f"{k}.name:failure1",
            f"{k}.name",
            ROOT + APP + CASE.replace('"smoke"', '"' + "n" * 129 + '"'),
        )
    )
    add(case(f"{k}.name:duplicate", f"{k}.name", ROOT + APP + CASE + CASE))
    add(
        case(
            f"{k}.name:positive",
            f"{k}.name",
            ROOT + APP + CASE,
            ["components.app.integrations[0].name"],
        )
    )
    add(
        case(
            f"{k}.argv:positive",
            f"{k}.argv",
            ROOT + APP + CASE,
            ["components.app.integrations[0].argv"],
        )
    )
    for index, bad in enumerate(
        [
            "[]",
            '["python3", "-V"]',
            '["{python:py}", "{bad"]',
            '["{python:py}", "--x={python:py}"]',
            "3",
            '["{python:py}", "' + "a" * 1025 + '"]',
        ]
    ):
        add(
            case(
                f"{k}.argv:failure{index}",
                f"{k}.argv",
                ROOT + APP + CASE.replace('["{python:py}", "-V"]', bad),
            )
        )
    add(
        case(
            f"{k}.argv:failure6",
            f"{k}.argv",
            ROOT + APP + CASE.replace('argv = ["{python:py}", "-V"]\n', ""),
        )
    )
    specs = {
        "expected_exit": ("3", ["2147483648", '"1"']),
        "timeout_seconds": ("5.5", ["0.05", "301", '"5"']),
        "required": ("false", ['"yes"']),
        "stdout_contains": (
            '["ok", "ok"]',
            ['[""]'.replace('""', '"a\\nb"'), '"ok"', "[" + ", ".join(['"x"'] * 33) + "]"],
        ),
        "stderr_contains": ('["warn"]', ['"x"']),
        "stdout_not_contains": ('["Traceback"]', ["[1]"]),
        "stderr_not_contains": ('["error"]', ['["a\\u0001"]']),
        "requires": ('["network"]', ["3", '["' + "r" * 129 + '"]']),
        "env": (
            '{ FOO = "1" }',
            ['{ 1BAD = "x" }', '{ ICI_MODE = "x" }', "{ FOO = 1 }", '{ FOO = "a\\u0001" }', "3"],
        ),
    }
    for field, (positive, bads) in specs.items():
        text, sel = integration(field, positive)
        add(case(f"{k}.{field}:positive", f"{k}.{field}", text, sel))
        for index, bad in enumerate(bads):
            add(case(f"{k}.{field}:failure{index}", f"{k}.{field}", integration(field, bad)[0]))
    targets = ROOT + APP + CASE
    add(
        case(
            f"{k}.python_targets:positive",
            f"{k}.python_targets",
            targets,
            ["components.app.integrations[0].python_targets"],
        )
    )
    for index, bad in enumerate(
        ['"-py" = "python3"', "py = 3", 'py = "a\\u0001"', '"bad name" = "x"']
    ):
        add(
            case(
                f"{k}.python_targets:failure{index}",
                f"{k}.python_targets",
                targets.replace('py = "python3"', bad),
            )
        )
    add(
        case(
            f"{k}.python_targets:failure4",
            f"{k}.python_targets",
            ROOT
            + APP
            + CASE.replace(
                '[components.integrations.python_targets]\npy = "python3"\n', "python_targets = 3\n"
            ),
        )
    )
    out_k = f"{k}.output_artifacts"
    out_base = ROOT + APP + CASE + "[[components.integrations.output_artifacts]]\n"
    add(
        case(
            f"{out_k}:positive",
            out_k,
            out_base + 'path = "reports/out.json"\nkind = "json"\nmin_size = 4\n',
            [
                f"components.app.integrations[0].output_artifacts[0].{f}"
                for f in ("path", "kind", "min_size")
            ],
        )
    )
    add(
        case(
            f"{out_k}:failure0",
            out_k,
            ROOT
            + APP
            + CASE.replace(
                "[components.integrations.python_targets]",
                "output_artifacts = 3\n[components.integrations.python_targets]",
            ),
        )
    )
    add(case(f"{out_k}:unknown", out_k, out_base + 'path = "a"\nbogus = 1\n'))
    for field, positive, bads in (
        ("path", '"reports/out.json"', ['"../x"', '"/abs"', '"./x"', '"a\\\\b"', "3", None]),
        ("kind", '"json"', ["3"]),
        ("min_size", "4", ["-1", "67108865", '"4"']),
    ):
        lines = 'path = "reports/out.json"\n' if field != "path" else ""
        add(
            case(
                f"{out_k}[].{field}:positive",
                f"{out_k}[].{field}",
                out_base + lines + f"{field} = {positive}\n",
                [f"components.app.integrations[0].output_artifacts[0].{field}"],
            )
        )
        for index, bad in enumerate(bads):
            body = (
                out_base + lines + (f"{field} = {bad}\n" if bad is not None else 'kind = "json"\n')
            )
            add(case(f"{out_k}[].{field}:failure{index}", f"{out_k}[].{field}", body))
    # suppressions
    sup = '[[suppressions]]\nrule = "python.dead"\nfingerprint = "abc"\npath = "src/**"\ncomponent = "app"\nreason = "legacy"\n'
    ssel = [f"suppressions[0].{f}" for f in ("rule", "fingerprint", "path", "component", "reason")]
    add(case("suppressions:positive", "suppressions", ROOT + APP + sup, ssel))
    add(case("suppressions:failure0", "suppressions", ROOT + "suppressions = 3\n" + APP))
    add(case("suppressions:failure1", "suppressions", ROOT + "suppressions = [1]\n" + APP))
    add(case("suppressions[]:unknown", "suppressions", ROOT + APP + sup + "bogus = 1\n"))
    add(
        case(
            "suppressions[]:selects_nothing",
            "suppressions",
            ROOT + APP + '[[suppressions]]\nreason = "x"\n',
        )
    )
    for field, bads in (
        ("reason", ["", '" "', "3"]),
        ("rule", ['" "', "3"]),
        ("fingerprint", ['""', '"' + "f" * 1025 + '"']),
        ("path", ['"../x"', '"/abs"', "3"]),
        ("component", ['" "', "3"]),
    ):
        add(
            case(
                f"suppressions[].{field}:positive",
                f"suppressions[].{field}",
                ROOT + APP + sup,
                [f"suppressions[0].{field}"],
            )
        )
        for index, bad in enumerate(bads):
            line = next(x for x in sup.splitlines() if x.startswith(field + " ="))
            text = ROOT + APP + sup.replace(line + "\n", f"{field} = {bad}\n" if bad else "")
            add(case(f"suppressions[].{field}:failure{index}", f"suppressions[].{field}", text))
    add(
        case(
            "suppressions[].path:normalised",
            "suppressions[].path",
            ROOT + APP + sup.replace('"src/**"', '"./src/**"'),
            ["suppressions[0].path"],
        )
    )
    # publish
    pub = '[publish]\nrepo = "owner/name"\napi_url = "https://ghe.example/api/v3/"\nserver_url = "https://ghe.example"\nbranch = "pages"\ntoken_env = "ICI_TOKEN"\n'
    psel = [f"publish.{f}" for f in ("repo", "api_url", "server_url", "branch", "token_env")]
    add(case("publish:positive", "publish", ROOT + pub + APP, psel))
    add(case("publish:defaults", "publish", ROOT + "[publish]\n" + APP, psel))
    add(case("publish:failure0", "publish", ROOT + "publish = 3\n" + APP))
    add(case("publish:unknown", "publish", ROOT + pub + "bogus = 1\n" + APP))
    add(case("publish:token", "publish", ROOT + pub + 'token = "secret"\n' + APP))
    for field, bads in (
        ("repo", ['"owner"', '"o/r/x"', '"o w/r"', "3"]),
        ("api_url", ['"http://x"', "3"]),
        ("server_url", ['"ghe.example"']),
        ("branch", ["3"]),
        ("token_env", ['"lower_case"', '"1ABC"', "3"]),
    ):
        add(
            case(
                f"publish.{field}:positive",
                f"publish.{field}",
                ROOT + pub + APP,
                [f"publish.{field}"],
            )
        )
        for index, bad in enumerate(bads):
            line = next(x for x in pub.splitlines() if x.startswith(field + " ="))
            add(
                case(
                    f"publish.{field}:failure{index}",
                    f"publish.{field}",
                    ROOT + pub.replace(line, f"{field} = {bad}") + APP,
                )
            )
    add(
        case(
            "publish.branch:blank",
            "publish.branch",
            ROOT + pub.replace('"pages"', '" "') + APP,
            ["publish.branch"],
        )
    )
    # local overlay
    lb = ROOT + build + APP + 'build = "native"\n' + '[components.python]\nexecutable = "python3"\n'
    lsel = ["builds.native.directory", "builds.native.project", "components.app.python.executable"]
    overlay = 'builds.native.directory = "local-out"\nbuilds.native.project = "pkg"\ncomponents.app.python.executable = ".venv/bin/python"\ntools.ruff.path = "bin/ruff"\noutput = "results"\ncache = "cache"\n'
    add(
        case(
            "local:positive",
            "local",
            lb + '[tools.ruff]\nsource = "project"\n',
            lsel,
            local=overlay,
        )
    )
    for key, line in (
        ("builds.*.directory", 'builds.native.directory = "local-out"\n'),
        ("builds.*.project", 'builds.native.project = "pkg"\n'),
        (
            "components.*.python.executable",
            'components.app.python.executable = ".venv/bin/python"\n',
        ),
        ("tools.*.path", 'tools.ruff.path = "bin/ruff"\n'),
        ("output", 'output = "results"\n'),
        ("cache", 'cache = "cache"\n'),
    ):
        sel = [x for x in lsel if x.endswith(key.split(".")[-1]) and key.split(".")[0] in x]
        add(
            case(
                f"local.{key}:positive",
                f"local.{key}",
                lb + '[tools.ruff]\nsource = "project"\n',
                sel,
                local=line,
            )
        )
        bad = line.split(" = ")[0] + " = 3\n"
        add(case(f"local.{key}:failure0", f"local.{key}", lb, local=bad))
    add(case("local:policy", "local", lb, local="checks.lint.enabled = false\n"))
    add(case("local:deep_wildcard", "local", lb, local='builds.a.b.directory = "x"\n'))
    add(
        case(
            "local.components.*.python.executable:empty",
            "local.components.*.python.executable",
            lb,
            local='components.app.python.executable = ""\n',
        )
    )
    add(
        case(
            "local.builds.*.directory:undeclared",
            "local.builds.*.directory",
            lb,
            ["builds.ghost.directory"],
            local='builds.ghost.directory = "x"\n',
        )
    )
    add(
        case(
            "local.components.*.python.executable:undeclared",
            "local.components.*.python.executable",
            lb,
            local='components.ghost.python.executable = "python3"\n',
        )
    )
    return out


def project(effective, select, standalone_id):
    flat = {}
    if effective.workspace_name is not None:
        flat["workspace.name"] = effective.workspace_name.value
    if effective.profile is not None:
        flat["workspace.profile"] = effective.profile.value
    flat["scope_kind"] = effective.scope_kind.value
    for check in effective.checks:
        flat[f"checks.{check.id}.enabled"] = check.enabled.value
        flat[f"checks.{check.id}.required"] = check.required.value
    for component in effective.components:
        name = "<standalone>" if component.id == standalone_id else component.id
        prefix = f"components.{name}"
        flat[f"{prefix}.root"] = component.root.value
        flat[f"{prefix}.languages"] = list(component.languages.value)
        for field in ("sources", "include", "exclude", "vendor", "test_paths"):
            flat[f"{prefix}.{field}"] = list(getattr(component, field))
        if component.build is not None:
            flat[f"{prefix}.build"] = component.build.value
        if component.needs is not None:
            flat[f"{prefix}.needs"] = list(component.needs.value)
        if component.external is not None:
            flat[f"{prefix}.external"] = list(component.external.value)
        if component.python_executable is not None:
            flat[f"{prefix}.python.executable"] = component.python_executable.value
        if component.python_type_provider is not None:
            flat[f"{prefix}.python.type_provider"] = component.python_type_provider.value
        for check in component.checks:
            flat[f"{prefix}.checks.{check.id}.enabled"] = check.enabled.value
            flat[f"{prefix}.checks.{check.id}.required"] = check.required.value
        for index, item in enumerate(component.integrations):
            base = f"{prefix}.integrations[{index}]"
            for field in ("name", "expected_exit", "timeout_seconds", "required"):
                flat[f"{base}.{field}"] = getattr(item, field)
            for field in (
                "argv",
                "stdout_contains",
                "stderr_contains",
                "stdout_not_contains",
                "stderr_not_contains",
                "requires",
            ):
                flat[f"{base}.{field}"] = list(getattr(item, field))
            flat[f"{base}.env"] = dict(item.env)
            flat[f"{base}.python_targets"] = dict(item.python_targets)
            for position, output in enumerate(item.output_artifacts):
                for field in ("path", "kind", "min_size"):
                    flat[f"{base}.output_artifacts[{position}].{field}"] = getattr(output, field)
    for build in effective.builds:
        prefix = f"builds.{build.id}"
        for field, value in (
            ("system", build.system),
            ("directory", build.directory),
            ("project", build.definition),
            ("variant", build.variant),
            ("prepare", build.prepare),
        ):
            if value is not None:
                flat[f"{prefix}.{field}"] = value.value
        flat[f"{prefix}.artifacts"] = list(build.artifacts)
    for index, item in enumerate(effective.suppressions):
        for field in ("fingerprint", "rule", "path", "component", "reason"):
            flat[f"suppressions[{index}].{field}"] = getattr(item, field)
    if effective.publish is not None:
        for field in ("repo", "api_url", "server_url", "branch", "token_env"):
            flat[f"publish.{field}"] = getattr(effective.publish, field)
    return {key: flat.get(key) for key in select}


def run(oracle, cases):
    sys.path.insert(0, str(oracle / "src"))
    import ici
    from ici.config.discovery import load
    from ici.config.errors import NextConfigError
    from ici.workspace import build as build_workspace

    assert Path(ici.__file__).resolve().is_relative_to(oracle.resolve())
    results = []
    seen = set()
    for item in cases:
        assert item["id"] not in seen, item["id"]
        seen.add(item["id"])
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "project"
            for name, text in item["files"].items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            (base / ".git").mkdir()
            local = None
            if item["local"] is not None:
                local = Path(temporary) / "local.toml"
                local.write_text(item["local"], encoding="utf-8")
            explicit = base / item["explicit"] if item["explicit"] else None
            entry = dict(item)
            # `ici next` commands load and then build the workspace model; both refusals exit 2.
            stage = "load"
            try:
                effective = load(base, explicit=explicit, local=local)
                stage = "model"
                build_workspace(effective)
            except NextConfigError as error:
                entry["outcome"] = "reject"
                entry["stage"] = stage
                entry["problems"] = [
                    {
                        "file": Path(problem.origin.file).name if problem.origin.file else "",
                        "key": problem.origin.key,
                        "message": problem.message,
                    }
                    for problem in error.problems
                ]
            except ValueError as error:
                # The CLI prints `config: <error>` and exits 2 for domain refusals too.
                assert stage == "model", error
                entry["outcome"] = "reject"
                entry["stage"] = stage
                entry["problems"] = [{"file": "<model>", "key": "", "message": str(error)}]
            else:
                entry["outcome"] = "accept"
                values = json.dumps(project(effective, item["select"], base.name))
                for prefix in {str(Path(temporary).resolve()), temporary}:
                    values = values.replace(prefix, "<tmp>")
                entry["values"] = json.loads(values)
            if item["id"] in CLI_SAMPLES:
                argv = ["next", "plan", "--json"]
                if explicit is not None:
                    argv += ["--config", str(explicit)]
                if local is not None:
                    argv += ["--local-config", str(local)]
                completed = subprocess.run(
                    [sys.executable, "-I", "-c", CLI_ENTRY, str(oracle / "src"), *argv],
                    cwd=base,
                    capture_output=True,
                    text=True,
                    env={"HOME": temporary, "XDG_CONFIG_HOME": temporary, "PATH": os.defpath},
                    check=False,
                )
                entry["cli"] = {"argv": ["ici", *argv[:3]], "exit_code": completed.returncode}
        results.append(entry)
    return results


CLI_SAMPLES = ("<top>:unknown", "builds:shared_directory", "local:policy", "workspace:positive")
CLI_ENTRY = "import sys; sys.path.insert(0, sys.argv.pop(1)); from ici.__main__ import app; app()"


def main():
    oracle, output = Path(sys.argv[1]), Path(sys.argv[2])
    os.environ.pop("ICI_CONFIG", None)
    results = run(oracle, cases())
    document = {
        "oracle": "v0.13.0 6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "python": sys.version.split()[0],
        "reader": "ici.config.discovery.load(start, explicit=, local=)",
        "cases": results,
    }
    with output.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1, sort_keys=True)
        stream.write("\n")
    accepted = sum(entry["outcome"] == "accept" for entry in results)
    print(f"{len(results)} frozen next-v1 cases ({accepted} accepted)")


if __name__ == "__main__":
    main()
