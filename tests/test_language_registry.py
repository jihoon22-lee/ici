"""The pack registry — #208 item 1.

The registry exists to answer one question without surprises: *which checks and
providers could this scope possibly need?* The rules it is tested against are
the issue's own: selecting Python must not surface a C++ tool requirement, and
nothing here may look at the machine.
"""

from __future__ import annotations

from ici.languages.cpp.checks import CPP_CHECKS
from ici.languages.python.checks import PYTHON_CHECKS
from ici.languages.registry import builtin


def test_a_python_scope_never_sees_cpp_tools() -> None:
    registry = builtin()

    checks = registry.checks_for(("python",))

    assert {check.id for check in checks} == {"python.line", "python.lint"}
    assert registry.providers_for(("python",)) == ("ici.line", "ruff")


def test_a_cpp_scope_never_sees_python_tools() -> None:
    registry = builtin()

    checks = registry.checks_for(("cpp",))

    assert {check.id for check in checks} == {"cpp.line", "cpp.compile"}
    assert "ruff" not in registry.providers_for(("cpp",))


def test_a_hybrid_scope_gets_both_packs_but_no_strays() -> None:
    registry = builtin()

    ids = {check.id for check in registry.checks_for(("python", "cpp"))}

    assert ids == {"python.line", "python.lint", "cpp.line", "cpp.compile"}


def test_an_unknown_language_gets_nothing() -> None:
    registry = builtin()

    assert registry.checks_for(("qml",)) == ()
    assert registry.pack_of("qml") is None


def test_qt_is_an_extension_not_a_language() -> None:
    registry = builtin()

    assert registry.qt.extends == "cpp"
    assert registry.pack_of("qt") is None
    # moc/uic/rcc are the capability — generated inputs a C++ scope must own.
    assert {"moc", "uic", "rcc"} <= set(registry.qt.generated_inputs)


def test_pack_contents_are_the_check_modules() -> None:
    registry = builtin()

    assert registry.pack_of("python").checks == PYTHON_CHECKS
    assert registry.pack_of("cpp").checks == CPP_CHECKS
