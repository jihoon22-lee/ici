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

from ici.adapters.providers.base import Provider, ProviderPlan, unavailable

__all__ = ["Provider", "ProviderPlan", "unavailable"]
