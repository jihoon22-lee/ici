"""The launcher's two-sided contract (#202 step 3, acceptance criteria 2 and 3).

Preserve the environment the user invoked ici in, and isolate core's imports.
Those pull against each other, and having both is the point: a project child
must be handed what the user actually had, while an inherited PYTHONPATH must
not be able to shadow core's own modules.

These run against a synthetic bundle whose "runtime" is the interpreter running
the tests, so they need no python-build-standalone download and skip nowhere.
What they cannot check is the real runtime; that is WP01's measurement and the
bundle smoke in PR C.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "bundle" / "launcher.sh"

# A stand-in for ici core. It reports what core actually sees, which is the only
# way to check isolation from outside the process.
CORE_MAIN = """
import json
import os
import sys


def app():
    if "--report-env" in sys.argv:
        sys.stdout.write(
            json.dumps(
                {
                    "env": {
                        name: os.environ.get(name)
                        for name in (
                            "PYTHONPATH",
                            "PYTHONHOME",
                            "VIRTUAL_ENV",
                            "QTDIR",
                            "LD_LIBRARY_PATH",
                            "SSL_CERT_FILE",
                            "ICI_ENTRY_PATH",
                            "ICI_ENTRY_PYTHONPATH",
                            "ICI_ENTRY_VIRTUAL_ENV",
                            "ICI_BUNDLE_ROOT",
                        )
                    },
                    "vendor_marker": _vendor_marker(),
                }
            )
        )
        return
    sys.stdout.write("ici-stub 1.0\\n")


def _vendor_marker():
    try:
        import vendored_marker
    except ImportError:
        return None
    return vendored_marker.ORIGIN
"""


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A bundle with the real launcher and a stub core."""

    return _make_bundle(tmp_path / "bundle")


def _make_bundle(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "runtime" / "python" / "bin").mkdir(parents=True)
    (root / "app" / "ici").mkdir(parents=True)
    (root / "app" / "vendor").mkdir(parents=True)

    shutil.copy2(LAUNCHER, root / "bin" / "ici")
    (root / "runtime" / "python" / "bin" / "python3").symlink_to(sys.executable)
    (root / "app" / "ici" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "ici" / "__main__.py").write_text(CORE_MAIN, encoding="utf-8")
    (root / "app" / "vendor" / "vendored_marker.py").write_text(
        'ORIGIN = "bundle"\n', encoding="utf-8"
    )
    return root


def _run(bundle: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    if env:
        environment.update(env)
    return subprocess.run(
        [str(bundle / "bin" / "ici"), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def _reported_env(bundle: Path, **env: str) -> dict[str, str | None]:
    completed = _run(bundle, "--report-env", env=env)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)["env"]


class TestCoreIsIsolated:
    def test_an_inherited_pythonpath_cannot_shadow_core(self, bundle, tmp_path):
        """Measured against a PYTHONPATH holding a module that raises on import.

        If isolation failed the launcher would not reach the stub at all, so a
        clean run is the assertion.
        """

        poison = tmp_path / "poison"
        poison.mkdir()
        (poison / "vendored_marker.py").write_text(
            'raise RuntimeError("project path shadowed the bundle")\n', encoding="utf-8"
        )

        completed = _run(bundle, "--report-env", env={"PYTHONPATH": str(poison)})

        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["vendor_marker"] == "bundle"

    @pytest.mark.parametrize("name", ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"])
    def test_the_python_variables_are_cleared_inside_core(self, bundle, name):
        assert _reported_env(bundle, **{name: "/somewhere"})[name] is None

    def test_the_bundle_path_is_not_exported(self, bundle):
        """It goes on sys.path inside -c, so a project child never inherits it.

        Exporting PYTHONPATH instead would push the bundle's vendored packages
        into every tool ici runs.
        """

        assert _reported_env(bundle)["PYTHONPATH"] is None


class TestTheEntryEnvironmentIsPreserved:
    def test_the_original_pythonpath_survives_under_its_entry_name(self, bundle):
        """Both halves at once: cleared for core, kept for the child."""

        reported = _reported_env(bundle, PYTHONPATH="/project/libs")

        assert reported["PYTHONPATH"] is None
        assert reported["ICI_ENTRY_PYTHONPATH"] == "/project/libs"

    def test_the_original_virtualenv_survives(self, bundle):
        reported = _reported_env(bundle, VIRTUAL_ENV="/project/.venv")

        assert reported["VIRTUAL_ENV"] is None
        assert reported["ICI_ENTRY_VIRTUAL_ENV"] == "/project/.venv"

    def test_an_unset_variable_is_preserved_as_empty_not_missing(self, bundle):
        """The entry value has to be reportable even when there was none, or a
        child cannot tell "the user had nothing" from "we lost it"."""

        assert _reported_env(bundle)["ICI_ENTRY_PYTHONPATH"] == ""

    @pytest.mark.parametrize("name,value", [("QTDIR", "/opt/qt6"), ("SSL_CERT_FILE", "/ca.pem")])
    def test_everything_else_reaches_core_untouched(self, bundle, name, value):
        """Only the three Python variables are cleared.

        Qt and the toolchain have to arrive intact for a C++ analysis to inherit
        what the user configured, and SSL_CERT_FILE matters because WP01 found
        this runtime links OpenSSL statically — clearing it loses the system CA.
        """

        assert _reported_env(bundle, **{name: value})[name] == value

    def test_the_bundle_root_is_published_for_core(self, bundle):
        assert _reported_env(bundle)["ICI_BUNDLE_ROOT"] == str(bundle)


class TestRelocation:
    def test_a_moved_bundle_still_runs(self, tmp_path):
        bundle = _make_bundle(tmp_path / "first")
        moved = tmp_path / "second"
        shutil.move(str(bundle), str(moved))

        assert _run(moved).stdout.strip() == "ici-stub 1.0"

    def test_a_path_with_spaces_still_runs(self, tmp_path):
        bundle = _make_bundle(tmp_path / "has space" / "ici bundle")

        assert _run(bundle).stdout.strip() == "ici-stub 1.0"

    def test_invocation_through_a_symlink_finds_the_bundle(self, tmp_path, bundle):
        """readlink -f is why this works; without it BUNDLE would resolve to the
        symlink's directory and the runtime would not be there."""

        elsewhere = tmp_path / "bin"
        elsewhere.mkdir()
        link = elsewhere / "ici"
        link.symlink_to(bundle / "bin" / "ici")

        completed = subprocess.run([str(link)], capture_output=True, text=True, check=False)

        assert completed.stdout.strip() == "ici-stub 1.0", completed.stderr

    def test_two_bundles_side_by_side_stay_independent(self, tmp_path):
        """Each resolves its own root, so installing a second version does not
        redirect the first."""

        first = _make_bundle(tmp_path / "v1")
        second = _make_bundle(tmp_path / "v2")
        (second / "app" / "ici" / "__main__.py").write_text(
            CORE_MAIN.replace("ici-stub 1.0", "ici-stub 2.0"), encoding="utf-8"
        )

        assert _run(first).stdout.strip() == "ici-stub 1.0"
        assert _run(second).stdout.strip() == "ici-stub 2.0"
        assert _reported_env(first)["ICI_BUNDLE_ROOT"] == str(first)


class TestTheInstallDirectoryIsNotWrittenTo:
    """#202 step 5. Measured on a real bundle too: running an analysis left zero
    new files under it."""

    def test_running_writes_nothing_into_the_bundle(self, bundle, tmp_path):
        """Precompiling is what buys this, which is why PR A does it.

        Without bytecode already in place the first run writes __pycache__ into
        the install directory — harmless here, impossible on the read-only mount
        WP01 measured. This asserts the state a built bundle actually ships in.
        """

        subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(bundle / "app")],
            check=True,
            capture_output=True,
        )
        project = tmp_path / "project"
        project.mkdir()
        before = {path: path.stat().st_mtime_ns for path in bundle.rglob("*") if path.is_file()}

        _run(bundle, cwd=project)

        after = {path: path.stat().st_mtime_ns for path in bundle.rglob("*") if path.is_file()}
        assert after == before

    def test_running_writes_nothing_into_the_project(self, bundle, tmp_path):
        project = tmp_path / "project"
        project.mkdir()

        _run(bundle, cwd=project)

        assert list(project.iterdir()) == []


class TestTheLauncherDoesNotPinOneRuntimeRelease:
    def test_it_invokes_the_unversioned_interpreter(self):
        """assemble_bundle.py reads bin/python3 while the spike launcher ran
        bin/python3.13. The two had drifted, so a runtime upgrade would have
        broken the launcher alone.
        """

        code = "\n".join(
            line
            for line in LAUNCHER.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

        assert 'runtime/python/bin/python3"' in code
        assert "python3.13" not in code
