"""The same claims, made with real processes (#204 PR B).

PR A's rules were tested against fake executables, which is what made the matrix
of awkward cases cheap. It is also what those tests cannot prove: that launching
``.venv/bin/python`` and launching the file it points at differ *in fact*.

They do, and this is the measurement:

    .venv/bin/python      -> sys.prefix = <project>/.venv, the project's package imports
    its realpath          -> sys.prefix = /usr,            the package is not importable

The fifth acceptance criterion of #204 asks for exactly this — *"core/project 환경
분리와 symlink launch 경로가 실제 프로세스 시험을 통과한다"* — and it is the one claim
that cannot be made by any number of unit tests.

The virtual environment here is real and built offline: the module the project
"declares" is written into its site-packages rather than installed, so nothing
reaches the network and the proof is the same.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

import pytest

from ici.toolchain.environment import EnvironmentSnapshot
from ici.toolchain.launch import DEFAULT_OUTPUT_LIMIT, probe_with, run
from ici.toolchain.resolution import Availability, ResolvedTool, Role, Unresolved
from ici.toolchain.resolver import Candidate, Request, Resolver

MARKER = "ici_project_only_marker"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real virtual environment with one module only it can import."""

    root = tmp_path_factory.mktemp("project")
    venv.create(root / ".venv", with_pip=False, symlinks=True)
    site = next((root / ".venv" / "lib").glob("python*")) / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (site / f"{MARKER}.py").write_text("ORIGIN = 'project'\n", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def launch_path(project: Path) -> Path:
    return project / ".venv" / "bin" / "python"


@pytest.fixture(scope="module")
def identity_path(launch_path: Path) -> Path:
    resolved = Path(os.path.realpath(launch_path))
    if resolved == launch_path:
        pytest.skip("this platform's venv is not a symlink, so the two cannot differ")
    return resolved


def _empty_environment() -> EnvironmentSnapshot:
    # Deliberately minimal. Anything the child sees has to have been put here.
    return EnvironmentSnapshot({"PATH": "/usr/bin:/bin"})


def _imports_marker(interpreter: Path, environment: EnvironmentSnapshot):
    return run(
        [str(interpreter), "-c", f"import {MARKER}; print({MARKER}.ORIGIN)"],
        environment=environment,
    )


class TestLaunchingTheLinkIsNotLaunchingItsTarget:
    """PR A split the two paths. This is why the split is not bookkeeping."""

    def test_the_launch_path_sees_the_project_environment(self, launch_path: Path) -> None:
        result = _imports_marker(launch_path, _empty_environment())
        assert result.exit_code == 0, result.output
        assert "project" in result.output

    def test_the_identity_path_does_not(self, identity_path: Path) -> None:
        # The same file, run under its own name, and the package the project
        # declared is gone. Substituting the realpath is the substitution that
        # looks harmless and changes the answer.
        result = _imports_marker(identity_path, _empty_environment())
        assert result.exit_code != 0
        assert "No module named" in result.output

    def test_the_prefixes_differ(self, launch_path: Path, identity_path: Path) -> None:
        environment = _empty_environment()
        argv = ["-c", "import sys; print(sys.prefix)"]
        inside = run([str(launch_path), *argv], environment=environment).output.strip()
        outside = run([str(identity_path), *argv], environment=environment).output.strip()
        assert inside != outside
        assert inside.endswith(".venv")

    def test_the_resolver_hands_back_the_path_that_works(
        self, launch_path: Path, identity_path: Path
    ) -> None:
        resolver = Resolver(probe_with(_empty_environment()))
        resolved = resolver.resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate(str(launch_path), "project"),),
            )
        )
        assert isinstance(resolved, ResolvedTool)
        assert resolved.launch_path == str(launch_path)
        assert resolved.identity_path == str(identity_path)
        assert resolved.launches_through_a_link
        # And the path it hands back is the one that can import the project's
        # module — which the identity path cannot.
        assert "project" in _imports_marker(Path(resolved.launch_path), _empty_environment()).output


class TestCoreAndProjectEnvironmentsStayApart:
    """#204 item 5, with real processes."""

    def test_nothing_from_this_process_reaches_the_child(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The property is "nothing was inherited", not "the child's os.environ
        # equals the snapshot". A Python child adds LC_CTYPE to its own
        # environment under PEP 538 locale coercion — measured by passing env={}
        # and still seeing it — so an equality assertion would be asserting
        # something about CPython rather than about this launcher.
        monkeypatch.setenv("ICI_LEAK_SENTINEL", "should-not-travel")
        result = run(
            [sys.executable, "-c", "import os; print(sorted(os.environ))"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin", "QTDIR": "/qt"}),
        )
        assert result.exit_code == 0
        seen = set(json.loads(result.output.strip().replace("'", '"')))
        assert "ICI_LEAK_SENTINEL" not in seen
        assert {"PATH", "QTDIR"} <= seen
        # Whatever else the child has, it did not come from here.
        assert seen - {"PATH", "QTDIR"} <= {"LC_CTYPE"}, seen

    def test_an_inherited_pythonpath_does_not_reach_the_child(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The variable is set for *this* process, and must not travel.
        monkeypatch.setenv("PYTHONPATH", str(project))
        result = run(
            [sys.executable, "-c", "import os; print(os.environ.get('PYTHONPATH', '<unset>'))"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin"}),
        )
        assert result.output.strip() == "<unset>"

    def test_core_does_not_import_the_project_site_packages(self, project: Path) -> None:
        # ici's own interpreter, under the core environment, must not be able to
        # import a module that exists only in the project's environment.
        snapshot = EnvironmentSnapshot({"PATH": "/usr/bin", "PYTHONPATH": str(project)})
        result = _imports_marker(Path(sys.executable), snapshot.for_core())
        assert result.exit_code != 0
        assert "No module named" in result.output

    def test_a_project_child_keeps_what_the_project_needs(self) -> None:
        values = {
            "PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/opt/lib",
            "QTDIR": "/opt/qt",
            "SSL_CERT_FILE": "/etc/ca.pem",
        }
        child = EnvironmentSnapshot(values).for_project()
        result = run(
            [
                sys.executable,
                "-c",
                "import os; print(','.join(f'{k}={os.environ[k]}' for k in sorted(os.environ)))",
            ],
            environment=child,
        )
        for name, value in values.items():
            assert f"{name}={value}" in result.output

    def test_no_bundle_path_is_injected_globally(self) -> None:
        # Whatever ici needs for itself must not appear in a project child.
        child = EnvironmentSnapshot({"PATH": "/usr/bin"}).for_project()
        result = run(
            [sys.executable, "-c", "import os; print(os.environ.get('PATH'))"],
            environment=child,
        )
        assert result.output.strip() == "/usr/bin"


class TestTheFailuresComeBackAsFacts:
    def test_a_timeout_is_reported_not_raised(self) -> None:
        result = run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin"}),
            timeout=0.5,
        )
        assert result.timed_out
        assert not result.usable

    def test_too_much_output_is_truncated_and_says_so(self) -> None:
        # Reading the first part of a flood as a version is how a broken tool
        # passes for a working one.
        result = run(
            [sys.executable, "-c", f"print('x' * {DEFAULT_OUTPUT_LIMIT * 2})"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin"}),
        )
        assert result.truncated
        assert not result.usable
        assert len(result.output) == DEFAULT_OUTPUT_LIMIT

    def test_a_missing_executable_is_reported_not_raised(self) -> None:
        result = run(
            ["/definitely/not/here"], environment=EnvironmentSnapshot({"PATH": "/usr/bin"})
        )
        assert not result.usable
        assert result.exit_code != 0

    def test_a_nonzero_exit_is_not_usable(self) -> None:
        result = run(
            [sys.executable, "-c", "raise SystemExit(3)"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin"}),
        )
        assert result.exit_code == 3
        assert not result.usable


class TestTheRefusalsHoldAgainstRealProcesses:
    def test_a_missing_explicit_interpreter_does_not_fall_back(self, project: Path) -> None:
        # The defect being replaced: this is where _resolve_python returns
        # sys.executable and the project is tested with ici's interpreter.
        resolver = Resolver(probe_with(_empty_environment()))
        resolved = resolver.resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(
                    Candidate(
                        str(project / "missing" / "python"), "config", "python.executable", True
                    ),
                    Candidate(sys.executable, "ici itself"),
                ),
            )
        )
        assert isinstance(resolved, Unresolved)
        assert resolved.availability is Availability.UNAVAILABLE
        assert sys.executable not in resolver.probed

    def test_a_path_with_a_space_runs(self, tmp_path: Path) -> None:
        home = tmp_path / "my tools"
        home.mkdir()
        script = home / "tool"
        script.write_text("#!/bin/sh\necho 'tool 1.2.3'\n", encoding="utf-8")
        script.chmod(0o755)
        resolved = Resolver(probe_with(EnvironmentSnapshot({"PATH": "/usr/bin:/bin"}))).resolve(
            Request(role=Role.ANALYZER, name="tool", candidates=(Candidate(str(script), "config"),))
        )
        assert isinstance(resolved, ResolvedTool)
        assert resolved.version == "1.2.3"

    def test_a_tool_that_hangs_is_broken_not_missing(self, tmp_path: Path) -> None:
        script = tmp_path / "hangs"
        script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        script.chmod(0o755)
        resolver = Resolver(probe_with(EnvironmentSnapshot({"PATH": "/usr/bin:/bin"}), timeout=0.5))
        resolved = resolver.resolve(
            Request(role=Role.ANALYZER, name="hangs", candidates=(Candidate(str(script), "PATH"),))
        )
        assert isinstance(resolved, Unresolved)
        assert resolved.availability is Availability.BROKEN
        assert "timed out" in resolved.detail


class TestTheMeasurementThisPrExistsFor:
    def test_the_current_path_still_falls_back_to_icis_own_interpreter(
        self, tmp_path: Path
    ) -> None:
        """The defect, pinned so its removal in PR C is visible as a change.

        Not a test of new code: a record of what the stable path does today, so
        that when PR C cuts an engine over, the diff shows the behaviour
        changing rather than a claim that it did.
        """

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'src');"
                "from ici.engines.test_interpreter import TestInterpreterMixin;"
                "m = TestInterpreterMixin();"
                "m.project_root = __import__('pathlib').Path(sys.argv[1]);"
                "m.get_config = lambda name: {};"
                "print(m._resolve_python()[0])",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == sys.executable


class TestTheProbeIsBoundedInFactAndNotOnlyInItsReport:
    """#205 item 5. Two things the old probe path got wrong while looking right.

    Both were invisible in the result. ``truncated`` was set and a tidy 1 KB
    came back — after the whole flood had been read into memory. ``timed_out``
    was set and the probe's own child was still running. A tool that "answers"
    while leaving that behind has not told you what it did.
    """

    @pytest.mark.skipif(
        not Path("/proc/self/status").exists(), reason="no /proc to read VmHWM from"
    )
    def test_a_flood_is_not_read_into_memory_before_it_is_trimmed(self) -> None:
        # Measured in a child, and *not* with getrusage. ru_maxrss carries the
        # forking parent's high-water mark across exec, so the first version of
        # this test read pytest's size: it passed on its own and failed in the
        # full suite, having measured the wrong process either way. VmHWM in
        # /proc is the figure for this process alone.
        flood = 200 * 1024 * 1024
        chunk = 64 * 1024
        source = str(Path(__file__).resolve().parents[1] / "src")
        writer = f"import sys\nfor _ in range({flood // chunk}): sys.stdout.write('x' * {chunk})"
        probe = (
            "import sys\n"
            f"sys.path.insert(0, {source!r})\n"
            "def hwm():\n"
            "    for line in open('/proc/self/status'):\n"
            "        if line.startswith('VmHWM:'):\n"
            "            return int(line.split()[1])\n"
            "    raise SystemExit('no VmHWM')\n"
            "from ici.toolchain.environment import EnvironmentSnapshot\n"
            "from ici.toolchain.launch import run\n"
            f"r = run([sys.executable, '-c', {writer!r}],\n"
            "        environment=EnvironmentSnapshot({}), output_limit=1024)\n"
            "print(len(r.output), int(r.truncated), hwm())\n"
        )

        done = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=300
        )
        assert done.returncode == 0, done.stderr
        length, truncated, peak_kib = (int(v) for v in done.stdout.split())

        assert (length, truncated) == (1024, 1), "the probe stopped reporting the flood"
        # A quarter of the flood is a wide margin around the ~13 MiB this takes:
        # the old path peaked past the whole 200 MiB, having read all of it.
        assert peak_kib * 1024 < flood // 4, (
            f"peak resident memory was {peak_kib // 1024} MiB for a "
            f"{flood // 1024 // 1024} MiB flood, so the output was read before it was trimmed"
        )

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
    def test_a_probe_that_times_out_does_not_leave_its_child_running(self) -> None:
        result = run(
            [
                sys.executable,
                "-c",
                "import subprocess, sys, time\n"
                "k = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                "print(k.pid, flush=True)\n"
                "time.sleep(120)\n",
            ],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin:/bin"}),
            timeout=1.0,
        )

        assert result.timed_out
        child = int(result.output.split()[0])
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        pytest.fail(f"the probe's child {child} outlived the probe that timed out")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
    def test_a_probe_does_not_run_in_icis_own_process_group(self) -> None:
        # The child being in our group is what made the leak reach us: a group
        # cleanup could not touch it without taking ici down too.
        result = run(
            [sys.executable, "-c", "import os; print(os.getpgrp())"],
            environment=EnvironmentSnapshot({"PATH": "/usr/bin:/bin"}),
        )

        assert result.usable
        assert int(result.output.strip()) != os.getpgrp()
