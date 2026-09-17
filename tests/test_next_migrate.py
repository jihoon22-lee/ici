"""``ici next migrate`` — the stable-to-next conversion, #225.

The contract being tested is the one the issue states: a dry run changes
nothing, applying leaves a backup or a new file, and no key vanishes without
a note saying where it went or why it did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli
from typer.testing import CliRunner

from ici.__main__ import app
from ici.config.convert import convert_document

runner = CliRunner()

STABLE = """\
[ici]
version = "0.11.0"
profile = "standard"

[project]
name = "demo"
source_dirs = ["src"]

[engines.line]
enabled = true
required = true
warn_limit = 500

[engines.lint]
enabled = true
mode = "strict"

[engines.security]
enabled = false
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (root / "ici.toml").write_text(STABLE, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _notes(text: str) -> str:
    return text  # stderr is folded into CliRunner output


class TestConvert:
    """The pure function: every stable key reaches the document or a note."""

    def test_enabled_and_required_become_check_settings(self) -> None:
        text, _ = convert_document(tomli.loads(STABLE), workspace_name="demo", component_root=".")
        document = tomli.loads(text)

        assert document["checks"]["python.line"] == {"enabled": True, "required": True}
        assert document["checks"]["cpp.line"] == {"enabled": True, "required": True}
        assert document["checks"]["python.lint"] == {"enabled": True}
        assert document["checks"]["cpp.tidy"] == {"enabled": True}
        assert document["checks"]["python.security"] == {"enabled": False}

    def test_every_key_is_accounted_for(self) -> None:
        """No silent drops: each stable key is converted, confirmed, or refused."""
        _, notes = convert_document(tomli.loads(STABLE), workspace_name="demo", component_root=".")
        covered = {note.key for note in notes}

        for key in (
            "ici.profile",
            "ici.version",
            "project.source_dirs",
            "engines.line.enabled",
            "engines.line.required",
            "engines.line.warn_limit",
            "engines.lint.enabled",
            "engines.lint.mode",
            "engines.security.enabled",
        ):
            assert key in covered, key

    def test_a_threshold_is_confirmed_not_carried(self) -> None:
        _, notes = convert_document(tomli.loads(STABLE), workspace_name="demo", component_root=".")
        note = next(item for item in notes if item.key == "engines.line.warn_limit")

        assert note.disposition == "confirm"
        assert "no per-check" in note.detail

    def test_languages_come_from_the_tree(self, tmp_path: Path) -> None:
        (tmp_path / "mod").mkdir()
        (tmp_path / "mod" / "a.py").write_text("")
        (tmp_path / "mod" / "b.cpp").write_text("")

        text, _ = convert_document(
            tomli.loads(STABLE),
            workspace_name="demo",
            component_root=".",
            workspace_root=tmp_path,
        )
        assert tomli.loads(text)["components"][0]["languages"] == ["cpp", "python"]

    def test_without_a_tree_the_language_choice_is_marked(self) -> None:
        _, notes = convert_document(tomli.loads(STABLE), workspace_name="demo", component_root=".")
        note = next(item for item in notes if item.key == "components[].languages")

        assert note.disposition == "confirm"


class TestCommand:
    def test_a_dry_run_changes_nothing(self, project: Path) -> None:
        before = (project / "ici.toml").read_text(encoding="utf-8")

        result = runner.invoke(app, ["next", "migrate"])

        assert result.exit_code == 0, result.output
        assert (project / "ici.toml").read_text(encoding="utf-8") == before
        assert "schema_version = 1" in result.output
        assert "dry run" in result.output
        assert not (project / "ici.toml.stable").exists()

    def test_output_writes_a_new_file_and_refuses_an_existing_one(self, project: Path) -> None:
        result = runner.invoke(app, ["next", "migrate", "--output", "ici.next.toml"])
        assert result.exit_code == 0, result.output

        written = tomli.loads((project / "ici.next.toml").read_text(encoding="utf-8"))
        assert written["workspace"]["name"] == "project"
        assert (project / "ici.toml").read_text(encoding="utf-8") == STABLE

        again = runner.invoke(app, ["next", "migrate", "--output", "ici.next.toml"])
        assert again.exit_code == 2
        assert "refusing to overwrite" in again.output

    def test_write_replaces_the_source_and_keeps_the_original(self, project: Path) -> None:
        result = runner.invoke(app, ["next", "migrate", "--write"])

        assert result.exit_code == 0, result.output
        backup = project / "ici.toml.stable"
        assert backup.is_file()
        assert backup.read_text(encoding="utf-8") == STABLE

        converted = tomli.loads((project / "ici.toml").read_text(encoding="utf-8"))
        assert converted["schema_version"] == 1
        assert converted["components"][0]["languages"] == ["python"]

    def test_a_converted_file_is_one_the_next_path_reads(self, project: Path) -> None:
        """The proof of a migration is that the new loader accepts the file."""
        runner.invoke(app, ["next", "migrate", "--write"])

        result = runner.invoke(app, ["next", "plan"])

        assert result.exit_code == 0, result.output
        assert "python.line" in result.output

    def test_a_next_schema_source_is_not_reconverted(self, project: Path) -> None:
        (project / "ici.toml").write_text(
            'schema_version = 1\n[workspace]\nname = "x"\n', encoding="utf-8"
        )

        result = runner.invoke(app, ["next", "migrate"])

        assert result.exit_code == 2
        assert "nothing to convert" in result.output

    def test_a_missing_source_is_an_error(self, project: Path) -> None:
        (project / "ici.toml").unlink()

        result = runner.invoke(app, ["next", "migrate"])

        assert result.exit_code == 2
        assert "no such file" in result.output

    def test_a_file_that_is_neither_schema_is_refused(self, project: Path) -> None:
        (project / "ici.toml").write_text("[unrelated]\nthing = 1\n", encoding="utf-8")

        result = runner.invoke(app, ["next", "migrate"])

        assert result.exit_code == 2
        assert "does not look like a stable config" in result.output

    def test_contested_layers_are_named_before_the_preview(
        self, project: Path, monkeypatch
    ) -> None:
        """A dev.toml that overrides the converted key is not silently lost."""
        (project / "dev.toml").write_text(
            "[engines]\n[engines.line]\nrequired = false\n", encoding="utf-8"
        )

        result = runner.invoke(app, ["next", "migrate"])

        assert result.exit_code == 0
        assert "dev.toml" in result.output
        assert "engines.line.required" in result.output
