"""Providers: running one analysis tool and reading what it said.

The architecture gives a provider two jobs and no others — say what to run,
and turn what came back into observations. It does not choose the tool (that
is ``toolchain``), it does not start the process (that is ``execution``), and
it does not decide whether the run passes (that is ``policy``).

Keeping those apart is what lets #205's distinction survive the trip: the
executor already knows that a tool which exited 1 because it found violations
is a tool that worked, and a provider that re-derived that from its own exit
code would be free to disagree.
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.base import Provider, ProviderPlan, unavailable
from ici.adapters.providers.binarycompat import BinaryCompatProvider
from ici.adapters.providers.compiler import CompilerDiagnosticsProvider
from ici.adapters.providers.coverage import CoverageProvider
from ici.adapters.providers.cpptest import CtestProvider, QtestProvider
from ici.adapters.providers.gcov import GcovProvider
from ici.adapters.providers.integration import IntegrationCaseProvider
from ici.adapters.providers.mypy import MypyProvider
from ici.adapters.providers.pycompat import CompileallProvider, PythonVersionProvider
from ici.adapters.providers.pytest import PytestProvider
from ici.adapters.providers.ruff import RuffProvider
from ici.adapters.providers.sanitize import SanitizeProvider
from ici.adapters.providers.tidy import ClangTidyProvider
from ici.adapters.providers.ty import TyProvider

__all__ = ["Provider", "ProviderPlan", "builtin_providers", "unavailable"]


def builtin_providers(root: Path, *, build_roots: tuple[Path, ...] = ()) -> dict[str, Provider]:
    """Every provider this build ships, keyed by the name tasks declare.

    The dispatch map used to be written out by hand where the run was
    assembled — sixteen names that had to spell themselves the same way the
    provider classes did, with nothing to notice when they drifted apart.
    Keying by each instance's own ``name`` makes the map and the providers it
    routes to one statement.
    """

    built: tuple[Provider, ...] = (
        RuffProvider(),
        CompilerDiagnosticsProvider(),
        ClangTidyProvider(),
        MypyProvider(),
        TyProvider(),
        PytestProvider(),
        CoverageProvider(),
        CtestProvider(),
        QtestProvider(),
        GcovProvider(),
        SanitizeProvider("sanitize", project_root=root),
        SanitizeProvider("thread-sanitize", project_root=root),
        PythonVersionProvider(),
        CompileallProvider(),
        IntegrationCaseProvider(),
        BinaryCompatProvider(project_root=root, build_roots=build_roots),
    )
    return {provider.name: provider for provider in built}
