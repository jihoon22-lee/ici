import pytest

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    VerificationSuiteResult,
    aggregate_suite_status,
)
from ici.engines.base import BaseEngine
from ici.reporters.console import format_status_badge


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


def test_error_is_counted_and_rendered_as_error():
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.ERROR,
        summary="verification failed to execute",
    )
    suite = VerificationSuiteResult(suite_status=EngineStatus.ERROR, results=[result])

    assert suite.failed_count == 1
    assert format_status_badge(EngineStatus.ERROR) == "[bold red] ERROR [/]"


@pytest.mark.parametrize(
    ("status", "evidence"),
    [
        (EngineStatus.ERROR, EvidenceState.NOT_RUN),
        (EngineStatus.FAIL, EvidenceState.MEASURED),
        (EngineStatus.SKIP, EvidenceState.ESTIMATED),
        (EngineStatus.PASS, EvidenceState.ESTIMATED),
    ],
)
def test_optional_non_pass_or_non_measured_result_degrades_suite_to_warn(status, evidence):
    result = EngineResult(
        engine_name="optional",
        status=status,
        summary="optional result",
        required=False,
        evidence=evidence,
    )

    assert aggregate_suite_status([result]) == EngineStatus.WARN


def test_optional_measured_pass_does_not_degrade_suite():
    result = EngineResult(
        engine_name="optional",
        status=EngineStatus.PASS,
        summary="optional result",
        required=False,
        evidence=EvidenceState.MEASURED,
    )

    assert aggregate_suite_status([result]) == EngineStatus.PASS


def test_required_precedence_is_preserved_over_optional_warnings():
    optional_error = EngineResult(
        engine_name="optional",
        status=EngineStatus.ERROR,
        summary="optional execution error",
        required=False,
        evidence=EvidenceState.NOT_RUN,
    )
    required_failure = EngineResult(
        engine_name="required",
        status=EngineStatus.FAIL,
        summary="required policy failure",
        required=True,
        evidence=EvidenceState.MEASURED,
    )

    assert aggregate_suite_status([optional_error, required_failure]) == EngineStatus.FAIL


def test_required_error_still_precedes_optional_failures():
    optional_failure = EngineResult(
        engine_name="optional",
        status=EngineStatus.FAIL,
        summary="optional policy failure",
        required=False,
        evidence=EvidenceState.MEASURED,
    )
    required_error = EngineResult(
        engine_name="required",
        status=EngineStatus.ERROR,
        summary="required execution error",
        required=True,
        evidence=EvidenceState.NOT_RUN,
    )

    assert aggregate_suite_status([optional_failure, required_error]) == EngineStatus.ERROR
