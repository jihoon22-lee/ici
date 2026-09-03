"""Reviewed equivalence registry for Python analyzer rule families."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PythonRuleMapping:
    """Documented aliases for one canonical Python rule family."""

    canonical_rule_id: str
    aliases: tuple[str, ...]
    contexts: tuple[str, ...] = ()
    always_mergeable: bool = False


# Adding an alias is a code-review decision, not a user-controlled equivalence
# relation. Contextual aliases remain non-mergeable until a trusted projection
# supplies the named semantic context.
PYTHON_RULE_MAPPINGS: tuple[PythonRuleMapping, ...] = (
    PythonRuleMapping(
        "ici.python.exception.bare-except",
        ("BareExcept", "Exception:BareExcept", "E722", "Ruff:E722"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.exception.base-exception",
        ("BaseException", "Exception:BaseException", "BLE001", "Ruff:BLE001"),
        contexts=("baseexception",),
    ),
    PythonRuleMapping(
        "ici.python.exception.lost-traceback",
        ("LostTraceback", "Exception:LostTraceback"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.exception.raise-without-from",
        ("B904", "Ruff:B904"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.correctness.mutable-default",
        (
            "MutableDefault",
            "Correctness:MutableDefault",
            "Resource:MutableDefault",
            "B006",
            "Ruff:B006",
        ),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.resource.open-without-context",
        ("OpenWithoutWith", "Resource:OpenWithoutWith", "SIM115", "Ruff:SIM115"),
        contexts=("confirmed-leak",),
    ),
    PythonRuleMapping(
        "ici.python.security.weak-md5",
        ("WeakCryptoMD5", "Security:WeakCryptoMD5"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.security.weak-sha1",
        ("WeakCryptoSHA1", "Security:WeakCryptoSHA1"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.security.weak-hash",
        ("S324", "Ruff:S324"),
        contexts=("md5", "sha1"),
    ),
    PythonRuleMapping(
        "ici.python.security.weak-random",
        ("WeakRandom", "Security:WeakRandom", "S311", "Ruff:S311"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.security.eval",
        ("S307", "Ruff:S307"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.security.dynamic-execution",
        ("EvalExec", "Security:EvalExec"),
        contexts=("eval", "exec"),
    ),
    PythonRuleMapping(
        "ici.python.security.pickle-load",
        ("PickleLoad", "Security:PickleLoad", "S301", "Ruff:S301"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.security.shell-true",
        ("ShellTrue", "Security:ShellTrue", "S602", "Ruff:S602", "S604", "Ruff:S604"),
        contexts=("subprocess",),
    ),
    PythonRuleMapping(
        "ici.python.security.command-processor",
        ("CommandProcessor", "Security:CommandProcessor", "S605", "Ruff:S605"),
        contexts=("os.system", "os.popen"),
    ),
    PythonRuleMapping(
        "ici.python.security.hardcoded-secret",
        ("HardcodedSecret", "Security:HardcodedSecret", "S105", "Ruff:S105"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.complexity.cyclomatic",
        ("C901", "Ruff:C901"),
        contexts=("cyclomatic",),
    ),
    PythonRuleMapping(
        "ici.python.dead.unreachable-statement",
        ("UnreachableCode", "DeadCode:UnreachableCode"),
        always_mergeable=True,
    ),
    PythonRuleMapping(
        "ici.python.type.undefined-name",
        ("F821", "Ruff:F821", "name-defined", "Mypy:name-defined"),
        always_mergeable=True,
    ),
)
