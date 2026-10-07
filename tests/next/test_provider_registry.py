"""The provider dispatch map is one statement, not a hand-kept list.

``run_verification`` routes a planned task to a provider by the name the
task declares. If the registry and the class names can drift apart, a
provider ships unreachable — a check whose tasks never find a provider is
one that reports "no provider registered" forever after.
"""

from pathlib import Path

from ici.adapters.providers import builtin_providers

EXPECTED_PROVIDERS = frozenset(
    {
        "binary-compat",
        "clang-tidy",
        "compiler",
        "coverage",
        "ctest",
        "gcov",
        "integration",
        "mypy",
        "pytest",
        "python-compat-compileall",
        "python-compat-version",
        "qtest",
        "ruff",
        "sanitize",
        "thread-sanitize",
        "ty",
    }
)


def test_every_builtin_provider_is_keyed_by_its_own_name(tmp_path: Path) -> None:
    providers = builtin_providers(tmp_path)

    assert set(providers) == EXPECTED_PROVIDERS
    for name, provider in providers.items():
        assert provider.name == name
        assert callable(provider.plan)
        assert callable(provider.parse)
