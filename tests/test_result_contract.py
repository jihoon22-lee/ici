from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    aggregate_suite_status,
)
from ici.engines.base import BaseEngine


class DummyEngine(BaseEngine):
    def run(self):
        raise NotImplementedError


def test_required_not_run_result_blocks_suite():
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.SKIP,
        summary="pytest was not executed",
        required=True,
        evidence=EvidenceState.NOT_RUN,
    )
    assert aggregate_suite_status([result]) == EngineStatus.ERROR


def test_empty_suite_is_error():
    assert aggregate_suite_status([]) == EngineStatus.ERROR


def test_pass_fail_promotes_warning_to_failure():
    engine = DummyEngine()
    assert engine.evaluate_status(False, True, "pass_fail") == EngineStatus.FAIL
