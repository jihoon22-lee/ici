"""Explaining the legacy configuration rather than absorbing it (#203 item 7).

The report exists because the stable loader's output cannot answer the question
a migrating user actually has: *which of my four files decided this?* After
``_deep_merge`` that information is gone. The report reads the same files in the
same order and names the winner and the losers.

It converts nothing and writes nothing. Converting from inside a report would be
the silent mixing item 7 forbids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.config.migration import report


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    return tmp_path


def _env(project: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(project / "home"), **extra}


class TestItNamesTheWinnerAndTheLosers:
    def test_a_contested_key_reports_both_sides(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("[engines.lint]\nrequired = false\n", encoding="utf-8")
        contested = report(project, environment=_env(project)).contested
        assert len(contested) == 1
        assert contested[0].key == "engines.lint.required"
        assert contested[0].winner == "dev.toml"
        assert contested[0].winning_value is False
        assert contested[0].overridden == (("project ici.toml", True),)

    def test_a_key_only_one_file_sets_is_not_contested(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("[engines.line]\nmax = 500\n", encoding="utf-8")
        assert report(project, environment=_env(project)).contested == ()

    def test_two_files_agreeing_is_not_contested(self, project: Path) -> None:
        # Nothing was lost, so there is nothing for a migrating user to learn.
        for name in ("ici.toml", "dev.toml"):
            (project / name).write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        assert report(project, environment=_env(project)).contested == ()

    def test_it_marks_a_contest_that_can_change_a_verdict(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("[engines.lint]\nrequired = false\n", encoding="utf-8")
        assert report(project, environment=_env(project)).policy_is_contested

    def test_a_path_being_overridden_is_not_a_policy_contest(self, project: Path) -> None:
        # A build directory differing per machine is a convenience. A required
        # differing per machine is the defect SPEC-01 section 3 names.
        (project / "ici.toml").write_text('[build]\ndirectory = "a"\n', encoding="utf-8")
        (project / "dev.toml").write_text('[build]\ndirectory = "b"\n', encoding="utf-8")
        result = report(project, environment=_env(project))
        assert result.contested
        assert not result.policy_is_contested


class TestItReadsTheSameFilesTheLoaderDoes:
    def test_it_finds_an_explicit_ici_config(self, project: Path) -> None:
        explicit = project / "elsewhere.toml"
        explicit.write_text("[engines.lint]\nrequired = false\n", encoding="utf-8")
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        result = report(project, environment=_env(project, ICI_CONFIG=str(explicit)))
        assert [label for label, _ in result.sources] == ["project ici.toml", "ICI_CONFIG"]
        assert result.contested[0].winner == "ICI_CONFIG"

    def test_it_finds_the_xdg_global_file(self, project: Path) -> None:
        xdg = project / "xdg"
        (xdg / "ici").mkdir(parents=True)
        (xdg / "ici" / "config.toml").write_text(
            "[engines.lint]\nrequired = false\n", encoding="utf-8"
        )
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        result = report(project, environment=_env(project, XDG_CONFIG_HOME=str(xdg)))
        assert [label for label, _ in result.sources] == ["XDG global", "project ici.toml"]
        # The project file is read after the global one, so it wins.
        assert result.contested[0].winner == "project ici.toml"

    def test_a_project_with_no_legacy_files_says_so(self, project: Path) -> None:
        result = report(project, environment=_env(project))
        assert result.sources == ()
        assert result.lines() == ("No legacy configuration files were found.",)

    def test_a_malformed_legacy_file_wins_nothing(self, project: Path) -> None:
        # Reporting its parse error is the stable loader's job; this report is
        # about what wins, and an unreadable file wins nothing.
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("this is not toml\n", encoding="utf-8")
        assert report(project, environment=_env(project)).contested == ()


class TestItChangesNothing:
    def test_reporting_writes_no_file(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("[engines.lint]\nrequired = false\n", encoding="utf-8")
        before = {
            (str(p.relative_to(project)), p.stat().st_mtime)
            for p in project.rglob("*")
            if p.is_file()
        }
        report(project, environment=_env(project))
        after = {
            (str(p.relative_to(project)), p.stat().st_mtime)
            for p in project.rglob("*")
            if p.is_file()
        }
        assert after == before

    def test_it_produces_no_next_path_config(self, project: Path) -> None:
        # Converting is a separate decision. A report that quietly produced a
        # new config would be the silent mixing item 7 forbids.
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        result = report(project, environment=_env(project))
        assert not hasattr(result, "text")
        assert not hasattr(result, "converted")


class TestTheReportReadsAsText:
    def test_it_lists_the_files_it_found(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        assert any(
            "project ici.toml" in line
            for line in report(project, environment=_env(project)).lines()
        )

    def test_it_says_when_nothing_is_contested(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        lines = report(project, environment=_env(project)).lines()
        assert any("No key is set by more than one" in line for line in lines)

    def test_a_contest_renders_with_both_values(self, project: Path) -> None:
        (project / "ici.toml").write_text("[engines.lint]\nrequired = true\n", encoding="utf-8")
        (project / "dev.toml").write_text("[engines.lint]\nrequired = false\n", encoding="utf-8")
        rendered = "\n".join(report(project, environment=_env(project)).lines())
        assert "engines.lint.required" in rendered
        assert "dev.toml=False" in rendered
        assert "project ici.toml=True" in rendered
