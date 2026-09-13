"""No network and no installer on the run path (#202 step 6, criterion 5).

The WP01 spike could only unset proxy variables, and said so in its own comment:
removing them takes away ici's *ability* to reach the network, it does not show
that the process never tried. Two checks close that here.

The static one guards the whole tree, including code no test happens to
exercise. The runtime one watches a real analysis through CPython's audit hooks,
which see a connection attempt whether it came from urllib, a bare socket, or a
library imported three levels down.

Neither replaces the other. Static analysis cannot see a call assembled at
runtime; the audit hook only sees the process it runs in, and says nothing about
code that never ran. Together they cover both gaps well enough to state the
claim #202 asks for.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ici"

# Modules that can open a connection. urllib.parse is deliberately absent: it
# only manipulates strings, and four reporters use it to build file:// links.
NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "ftplib",
        "http.client",
        "http.server",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib.error",
        "urllib.request",
        "urllib3",
        "xmlrpc.client",
    }
)

# Where a network call is the command's purpose rather than a leak. Keep this
# list short and keep the reason with it: an entry added without one is how a
# guard stops meaning anything.
ALLOWED = {
    "engines/publish.py": (
        "publish uploads a report to gh-pages. It is a transport the user asks "
        "for by name, not something an analysis does on its way past."
    ),
}

# Anything that would install software during a user run.
INSTALLERS = frozenset({"pip", "pip3", "uv", "easy_install", "poetry", "pdm"})

# Naming an installer is not the offence; making it do something is. doctor
# probes for uv with --version, which is exactly the kind of call that must stay
# legal, so an installer invocation counts only when it asks for more than its
# own version.
INTROSPECTION_ONLY = frozenset({"--version", "-V", "-VV", "-v", "--help", "-h"})


def _python_sources() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _network_importers() -> dict[str, set[str]]:
    importers: dict[str, set[str]] = {}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = {name for name in _imported_modules(tree) if _is_network(name)}
        if hits:
            importers[path.relative_to(SRC).as_posix()] = hits
    return importers


def _is_network(name: str) -> bool:
    if name in NETWORK_MODULES:
        return True
    # urllib.request.urlopen imported directly, http.client.HTTPConnection, ...
    return any(name.startswith(f"{module}.") for module in NETWORK_MODULES)


class TestTheTreeOpensNoConnections:
    def test_only_the_allowed_modules_import_a_network_api(self) -> None:
        importers = _network_importers()
        unexpected = {path: sorted(hits) for path, hits in importers.items() if path not in ALLOWED}
        assert unexpected == {}, (
            "these modules import a network API and are not on the allowlist in "
            f"{Path(__file__).name}: {unexpected}. If the call belongs on the run "
            "path, #202 criterion 5 no longer holds; if it does not, remove it."
        )

    def test_every_allowlist_entry_is_still_a_real_importer(self) -> None:
        # A stale entry silently widens the guard for whatever takes that path
        # next, which is the same defect as never having written the guard.
        importers = _network_importers()
        stale = sorted(set(ALLOWED) - set(importers))
        assert stale == [], f"allowlist entries that no longer import anything: {stale}"

    def test_every_allowlist_entry_carries_a_reason(self) -> None:
        assert all(reason.strip() for reason in ALLOWED.values())

    def test_nothing_runs_an_installer(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names = sorted(_installer_commands(tree))
            if names:
                offenders[path.relative_to(SRC).as_posix()] = names
        assert offenders == {}, (
            f"these modules would install software during a run: {offenders}. "
            "Downloading belongs to the build path only (#202 step 6)."
        )


# The call sites that actually start a process. Looking at every call instead
# would flag the ToolProbe("uv", ("uv",), ("--version",)) declaration doctor uses
# to detect uv, which starts nothing by itself.
PROCESS_LAUNCHERS = frozenset({"run_process", "run", "Popen", "call", "check_call", "check_output"})


def _launcher_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _installer_commands(tree: ast.AST) -> set[str]:
    """Installer programs passed to a process launcher as a literal argv.

    Only literal argument lists, because that is what an accidental install
    looks like. A name assembled at runtime would slip past this, which is why
    the audit-hook test below exists as well.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _launcher_name(node) not in PROCESS_LAUNCHERS:
            continue
        for argument in node.args:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            parts = [
                element.value
                for element in argument.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if not parts:
                continue
            program = Path(parts[0]).name
            arguments = parts[1:]
            if parts[:2] == ["-m", "pip"] or arguments[:2] == ["-m", "pip"]:
                found.add(" ".join(parts[:4]))
            elif program in INSTALLERS and not _introspection_only(arguments):
                found.add(" ".join(parts[:3]))
    return found


def _introspection_only(arguments: list[str]) -> bool:
    return bool(arguments) and all(argument in INTROSPECTION_ONLY for argument in arguments)


# Run in a subprocess: an audit hook cannot be removed once installed, and the
# test session itself legitimately talks to the network (coverage plugins, the
# terminal). Only this child needs to be under the hook.
_AUDITED_RUN = r"""
import json
import sys

WATCHED = ("socket.connect", "socket.getaddrinfo", "urllib.Request", "http.client.connect")
attempts = []

def hook(event, args):
    if event in WATCHED:
        attempts.append({"event": event, "args": [repr(a)[:120] for a in args]})

sys.addaudithook(hook)

from typer.testing import CliRunner
from ici.__main__ import app

runner = CliRunner()
codes = {}
for command in sys.argv[2:]:
    result = runner.invoke(app, [command, "--path", sys.argv[1]])
    codes[command] = result.exit_code

sys.stdout.write(json.dumps({"attempts": attempts, "exit_codes": codes}))
"""


class TestAnAnalysisOpensNoConnections:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        source = tmp_path / "src"
        source.mkdir()
        (source / "module.py").write_text(
            "def add(left, right):\n"
            "    if left > right:\n"
            "        return left - right\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_the_analysis_engines_attempt_no_connection(self, project: Path) -> None:
        commands = ["line", "complexity", "cognitive", "dup"]
        completed = subprocess.run(
            [sys.executable, "-c", _AUDITED_RUN, str(project), *commands],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)

        # The engines must actually have run: an audit hook sees nothing at all
        # when nothing happened, and that is not the same as seeing no traffic.
        assert sorted(report["exit_codes"]) == sorted(commands), report["exit_codes"]
        assert report["attempts"] == [], (
            "an analysis tried to reach the network: "
            f"{report['attempts']}. #202 criterion 5 says a user run does not."
        )
