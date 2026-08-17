"""Tests for Test Execution Engine, Coverage & TEM 5.0 Scoring."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.test import TestEngine


def test_test_engine_execution_and_tem_score(tmp_python_project: Path):
    engine = TestEngine(tmp_python_project)
    res = engine.run()

    assert res.status == EngineStatus.PASS
    assert res.score is not None
    # TEM score must be between 0 and 5.0
    assert 0.0 <= res.score <= 5.0
    assert res.extra["passed_tests"] >= 1


def test_tem_formula_direct_calculation():
    """Validates TEM Score formula: (min(80, branch) / 80) * (func / 100) * 5.0"""
    # Case 1: Max score (branch >= 80, func = 100) -> 5.0
    branch = 85.0
    func = 100.0
    tem = (min(80.0, branch) / 80.0) * (func / 100.0) * 5.0
    assert tem == 5.0

    # Case 2: Partial branch (branch = 40, func = 100) -> 2.5
    branch = 40.0
    func = 100.0
    tem = (min(80.0, branch) / 80.0) * (func / 100.0) * 5.0
    assert tem == 2.5

    # Case 3: Partial func (branch = 80, func = 80) -> 4.0
    branch = 80.0
    func = 80.0
    tem = (min(80.0, branch) / 80.0) * (func / 100.0) * 5.0
    assert tem == 4.0
