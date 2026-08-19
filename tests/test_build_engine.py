"""Tests for build tool failure handling."""

from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.build import BuildEngine


def test_build_truncated_compile_output_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        'name = "sample"\ntype = "cpp"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "ici.engines.build.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.build.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", "", 0.01, truncated=True),
    )

    result = BuildEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
