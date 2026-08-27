"""Duplication policy: warn_pct decides, and the rate is a real percentage.

Two things were wrong. The engine warned whenever a single clone group existed
regardless of the configured rate, so warn_pct was unreachable — a project at
1.8% against a 5% policy was told it had a problem it had explicitly decided
not to have. And the rate itself could exceed 100%: the numerator counted every
physical line inside a clone span while the denominator counted only lines that
survived filtering, so blanks, comments and imports were weighed against a
total that excluded them.
"""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.dup import DuplicateEngine

# Two functions with the same shape and different identifiers: a Type-2 clone.
# Blank lines between them are deliberate — they are inside the clone span and
# used to inflate the numerator.
_CLONE_SOURCE = """
def describe_alpha(count):
    out = ""

    out += "alpha"
    out += ":"

    out += str(count)
    out += ";"
    out += "end"
    return out


def describe_beta(total):
    res = ""

    res += "beta"
    res += "="

    res += str(total)
    res += "|"
    res += "done"
    return res
"""


def _project(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "clone.py").write_text(_CLONE_SOURCE, encoding="utf-8")
    return tmp_path


def _run(root: Path, warn_pct: float):
    engine = DuplicateEngine(root)
    engine.config = {"engines": {"dup": {"warn_pct": warn_pct, "fail_pct": 99.0}}}
    return engine.run()


def test_rate_stays_a_percentage(tmp_path: Path):
    """Blank lines inside a clone span must not push the rate past 100%."""
    result = _run(_project(tmp_path), warn_pct=5.0)
    assert 0.0 < result.extra["duplication_pct"] <= 100.0


def test_threshold_is_actually_tunable(tmp_path: Path):
    """The same project passes or warns depending only on the configured rate."""
    root = _project(tmp_path)
    strict = _run(root, warn_pct=5.0)
    relaxed = _run(root, warn_pct=95.0)
    assert strict.status == EngineStatus.WARN
    assert relaxed.status == EngineStatus.PASS
    assert strict.extra["duplication_pct"] == relaxed.extra["duplication_pct"]


def test_findings_survive_a_passing_rate(tmp_path: Path):
    """Passing the policy must not hide where the duplication is."""
    result = _run(_project(tmp_path), warn_pct=95.0)
    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] >= 1
    assert result.targets, "the clone group must still be reported"
    # ...but not as something to act on, or the report contradicts its own status.
    assert all(t.status == EngineStatus.PASS for t in result.targets)


def test_breaching_the_policy_marks_the_findings(tmp_path: Path):
    result = _run(_project(tmp_path), warn_pct=5.0)
    assert result.status == EngineStatus.WARN
    assert any(t.status == EngineStatus.WARN for t in result.targets)
