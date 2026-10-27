"""Static analysis entry point sharing the next application and wire contracts."""

from pathlib import Path

from ici.cli.next_common import (
    _BASELINE_OPTION,
    _COMPONENT_OPTION,
    _CONFIG_OPTION,
    _CPP_OPTION,
    _EVENTS_OPTION,
    _JSON_OPTION,
    _LOCAL_CONFIG_OPTION,
    _NO_CACHE_OPTION,
    _PROFILE_OPTION,
    _PYTHON_OPTION,
    _REQUIRE_FULL_OPTION,
    _RESULT_OPTION,
)
from ici.cli.next_path import run_checks
from ici.domain.enums import Profile


def analyze(
    component: list[str] = _COMPONENT_OPTION,
    python: bool = _PYTHON_OPTION,
    cpp: bool = _CPP_OPTION,
    profile: Profile | None = _PROFILE_OPTION,
    require_full: bool = _REQUIRE_FULL_OPTION,
    result: Path = _RESULT_OPTION,
    no_cache: bool = _NO_CACHE_OPTION,
    json_mode: bool = _JSON_OPTION,
    events: Path | None = _EVENTS_OPTION,
    baseline: Path | None = _BASELINE_OPTION,
    config_path: Path | None = _CONFIG_OPTION,
    local_config: Path | None = _LOCAL_CONFIG_OPTION,
) -> None:
    """Analyze source without running tests; ici.toml is optional."""
    run_checks(
        component=component,
        python=python,
        cpp=cpp,
        profile=profile,
        require_full=require_full,
        result=result,
        no_cache=no_cache,
        json_mode=json_mode,
        events=events,
        baseline=baseline,
        config_path=config_path,
        local_config=local_config,
        static_only=True,
    )
