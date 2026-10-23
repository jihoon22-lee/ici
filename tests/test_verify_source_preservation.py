"""Real Ruff configuration must never turn verification into a source edit."""

from pathlib import Path

import pytest

from ici.adapters.providers.base import observe
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.core.models import EngineStatus
from ici.engines.lint import LintEngine
from ici.execution.process import ProcessSpec, run_task


@pytest.mark.parametrize("route", ["stable", "next"])
@pytest.mark.parametrize(
    "settings",
    ["fix = true", "fix-only = true", "fix = true\nfix-only = true\nunsafe-fixes = true"],
)
def test_ruff_settings_cannot_edit_verified_sources(tmp_path, monkeypatch, route, settings):
    executable = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "ruff"
    if not executable.is_file():
        import shutil

        found = shutil.which("ruff")
        assert found, "the locked development Ruff must be installed"
        executable = Path(found)
    source = tmp_path / "main.py"
    original = b"import os\n"
    source.write_bytes(original)
    (tmp_path / "ruff.toml").write_text(f'{settings}\n[lint]\nselect = ["F401"]\n')

    if route == "stable":
        monkeypatch.setattr(LintEngine, "_find_ruff_command", lambda self: [str(executable)])
        result = LintEngine(tmp_path, {"project": {"source_dirs": ["."]}}).run()
        assert source.read_bytes() == original
        assert result.status is EngineStatus.FAIL
        assert any("F401" in target.target_name for target in result.targets)
    else:
        provider = RuffProvider()
        plan = provider.plan(
            RuffRequest(executable=str(executable), project_root=tmp_path, targets=("main.py",))
        )
        result = observe(
            provider,
            plan,
            run_task(ProcessSpec(argv=plan.task.argv, cwd=tmp_path, name=plan.task.id)),
        )
        assert source.read_bytes() == original
        assert result.evidence_is_complete
        assert [finding.native_rule_id for finding in result.findings] == ["F401"]
