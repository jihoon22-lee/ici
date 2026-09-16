"""WP28 #226 item 4 — hostile-input and credential edges on the next path.

Exercised here, at the CLI boundary rather than by inspection:

- a component ``root`` that walks out of the workspace is refused at config
  load — the run does not start, and nothing outside is explored;
- an observation-cache entry that was corrupted or rewritten after storing
  is evicted and re-run — never adopted into the result;
- the publish token travels in the Authorization header only — not in
  stdout, not in ``publish.json``, and not even when the server echoes it
  back in an error body;
- every finding path a stored result carries is workspace-relative — the
  result is the artifact that may leave the network (R15), so it must not
  leak the machine's layout.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.domain.serialization import dumps, run_result_to_dict
from test_next_serialization import minimal_result

runner = CliRunner()

RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")

HEADER = 'schema_version = 1\n[workspace]\nname = "ws"\n'

PUBLISH_CONFIG = HEADER + (
    '[publish]\nrepo = "org/repo"\n'
    'api_url = "https://ghes.internal/api/v3"\n'
    'server_url = "https://ghes.internal"\n'
)


def _project(root: Path, *, defect: bool = True) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text(
        "import os\nvalue = 1\n" if defect else "value = 1\n", encoding="utf-8"
    )
    (root / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "."\nlanguages = ["python"]\n'
        + '[checks."python.test"]\nenabled = false\n'
        + '[checks."python.coverage"]\nenabled = false\n'
        + '[checks."python.type"]\nenabled = false\n'
        + '[checks."python.format"]\nenabled = false\n'
        + '[checks."python.compat-runtime"]\nenabled = false\n',
        encoding="utf-8",
    )
    return root


def _cache_dir(root: Path) -> Path:
    return root / ".ici" / "cache" / "observations"


# --- declared paths stay inside the workspace -------------------------------


def test_a_component_root_escaping_the_workspace_is_refused(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.py").write_text("value = 1\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "../outside"\nlanguages = ["python"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(ws)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 2, result.output
    assert "escapes" in result.output


# --- the observation cache is evidence, not authority -----------------------


@needs_ruff
def test_a_corrupted_cache_still_reports_the_defect(tmp_path: Path, monkeypatch) -> None:
    """Corrupt stored bytes are a miss, and the defect is re-detected (#226)."""
    root = _project(tmp_path / "ws")
    monkeypatch.chdir(root)
    result_path = tmp_path / "r1.json"

    first = runner.invoke(app, ["next", "verify", "--result", str(result_path)])
    assert first.exit_code == 1, first.output
    entries = list(_cache_dir(root).glob("*.json"))
    assert entries, "the tool tasks must have cached their observations"

    for entry in entries:
        entry.write_text("{ corrupted", encoding="utf-8")

    second = runner.invoke(app, ["next", "verify", "--result", str(result_path)])

    assert second.exit_code == 1, second.output
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["findings"], "the defect must be re-detected, not forgotten"


# --- the publish token is a header, not content ------------------------------


class _TokenEchoingGhes:
    """A hostile edge: the server echoes the credential back in an error body.

    publish must still keep it out of output and the record — a log that
    leaks the token because a server repeated it is still a leak.
    """

    TOKEN = "ghs_l1tteral-t0ken"

    def __call__(self, method, url, token, payload):
        assert token == self.TOKEN, "the credential must travel in the header"
        if method == "GET" and "/git/ref/" in url:
            return 404, {}
        if method == "POST" and url.endswith("/git/refs"):
            return 201, {}
        return 500, {"error": f"rejected: bearer {self.TOKEN} was refused"}


def test_the_publish_token_never_reaches_output_or_records(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "ici.toml").write_text(PUBLISH_CONFIG, encoding="utf-8")
    out = tmp_path / ".ici" / "next"
    out.mkdir(parents=True)
    (out / "result.json").write_text(dumps(run_result_to_dict(minimal_result())), encoding="utf-8")
    (out / "result.html").write_text("<html>report</html>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ici.adapters.ghes._urllib_transport", _TokenEchoingGhes())
    monkeypatch.setenv("GITHUB_TOKEN", _TokenEchoingGhes.TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_API_URL", "https://ghes.internal/api/v3")

    result = runner.invoke(app, ["next", "publish"])

    assert result.exit_code in (0, 1), result.output  # server behaviour, not the point
    surface = result.output + (result.stderr or "")
    record_path = out / "publish.json"
    if record_path.is_file():
        surface += record_path.read_text(encoding="utf-8")
    assert _TokenEchoingGhes.TOKEN not in surface


# --- stored results carry no machine layout ---------------------------------


@needs_ruff
def test_finding_paths_stay_workspace_relative(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path / "ws")
    monkeypatch.chdir(root)
    result_path = tmp_path / "result.json"

    result = runner.invoke(app, ["next", "verify", "--result", str(result_path)])
    assert result.exit_code == 1, result.output

    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["findings"]
    for finding in document["findings"]:
        spans = [finding["primary_location"], *finding.get("related_locations", [])]
        for span in spans:
            path = Path(span["path"])
            assert not path.is_absolute(), span["path"]
            assert ".." not in path.parts, span["path"]
