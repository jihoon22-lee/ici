"""The built-in language packs, declared without touching the machine.

#208 item 1 asks for the pack registry and one rule above all: **importing a
pack must not probe a tool or install a package.** Everything here is data —
check definitions, provider *names*, capability flags. A provider name is a
string the resolver will look for later; holding the name is not holding the
tool, and the difference is what lets ``plan`` describe a run on a machine that
has none of them.

Qt is an extension on the C++ pack, not a language and not qmake. A CMake+Qt
project needs the same generated-input awareness a qmake one does, so the
capability hangs off the language it extends rather than off any build system.

The registry is deliberately not the old ``ENGINE_DESCRIPTORS`` in new clothes.
That table describes the stable path's engines — *implementations* with
factories — while this one declares *checks* and the providers that could
satisfy them. Keeping them in one table would fuse two things that change for
different reasons; the correspondence between them is documented, once, in
``docs/design/ici-next/inventory/current-engines.md`` (#208's own rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.languages.checks import CheckDefinition
from ici.languages.cpp.checks import CPP_CHECKS
from ici.languages.integration import INTEGRATION_CASES_CHECK
from ici.languages.python.checks import PYTHON_CHECKS

__all__ = [
    "CPP_PACK",
    "INTEGRATION_PACK",
    "PYTHON_PACK",
    "QT_EXTENSION",
    "LanguagePack",
    "PackRegistry",
    "QtExtension",
    "builtin",
]


@dataclass(frozen=True)
class LanguagePack:
    """One language's declared checks and the providers that can run them.

    ``providers`` are names, not imports — resolving a name into a usable
    executable is the caller's decision, made when a run actually asks for it.
    """

    id: str
    languages: tuple[str, ...]
    checks: tuple[CheckDefinition, ...]
    providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a pack must have an id")
        object.__setattr__(self, "languages", tuple(self.languages))
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "providers", tuple(self.providers))
        if not self.languages:
            raise ValueError(f"pack {self.id} must declare at least one language")
        foreign = [check.id for check in self.checks if check.language not in self.languages]
        if foreign:
            raise ValueError(
                f"pack {self.id} declares checks outside its languages: {sorted(foreign)}"
            )
        seen: set[str] = set()
        for check in self.checks:
            if check.id in seen:
                raise ValueError(f"pack {self.id} declares {check.id} twice")
            seen.add(check.id)


@dataclass(frozen=True)
class QtExtension:
    """The Qt capability layered onto the C++ pack.

    The generated-input kinds are the capability: moc/uic/rcc outputs are real
    sources a C++ analysis must count as inputs, and a pack that pretends they
    do not exist reports a partial compile scope as complete. No providers are
    claimed yet — clazy and friends land with #214.
    """

    extends: str = "cpp"
    generated_inputs: tuple[str, ...] = ("moc", "uic", "rcc")
    providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_inputs", tuple(self.generated_inputs))
        object.__setattr__(self, "providers", tuple(self.providers))


@dataclass(frozen=True)
class PackRegistry:
    """The packs this build of ici knows, and the extensions they accept."""

    packs: tuple[LanguagePack, ...]
    qt: QtExtension = QtExtension()

    def __post_init__(self) -> None:
        object.__setattr__(self, "packs", tuple(self.packs))
        owners: dict[str, str] = {}
        for pack in self.packs:
            for language in pack.languages:
                previous = owners.get(language)
                if previous is not None:
                    raise ValueError(
                        f"language {language} is claimed by both {previous} and {pack.id}"
                    )
                owners[language] = pack.id
            for check in pack.checks:
                if sum(check.id == other.id for other in self.checks()) > 1:
                    raise ValueError(f"check {check.id} is declared by more than one pack")

    def checks(self) -> tuple[CheckDefinition, ...]:
        return tuple(check for pack in self.packs for check in pack.checks)

    def checks_for(self, languages: tuple[str, ...]) -> tuple[CheckDefinition, ...]:
        """The checks that apply to these languages — and only these.

        This is the boundary that keeps a Python-only workspace free of C++
        tool requirements: a component never sees a check its languages do not
        own, so it cannot be asked for a tool it has no use for.
        """

        wanted = set(languages)
        return tuple(check for check in self.checks() if check.language in wanted)

    def pack_of(self, language: str) -> LanguagePack | None:
        for pack in self.packs:
            if language in pack.languages:
                return pack
        return None

    def providers_for(self, languages: tuple[str, ...]) -> tuple[str, ...]:
        """Provider names the scope could conceivably need — still no probing."""

        wanted = set(languages)
        names: list[str] = []
        for pack in self.packs:
            if set(pack.languages) & wanted:
                names.extend(pack.providers)
        if "cpp" in wanted:
            names.extend(self.qt.providers)
        return tuple(dict.fromkeys(names))


PYTHON_PACK = LanguagePack(
    id="python",
    languages=("python",),
    checks=PYTHON_CHECKS,
    providers=("ici.line", "ruff"),
)

CPP_PACK = LanguagePack(
    id="cpp",
    languages=("cpp",),
    checks=CPP_CHECKS,
    providers=("ici.line",),
)

#: ``integration`` is a domain, not a language — ``checks_for`` never returns
#: this pack's checks because no component declares ``"integration"`` as a
#: language. The check is offered to a component by ``next_common`` only when
#: the component declares cases, which is the declaration-driven opt-in #220
#: item 6 requires; the pack exists so the check is still registry-declared
#: data rather than a check the planner invents (#208 item 1's rule).
INTEGRATION_PACK = LanguagePack(
    id="integration",
    languages=("integration",),
    checks=(INTEGRATION_CASES_CHECK,),
)

QT_EXTENSION = QtExtension()


def builtin() -> PackRegistry:
    """The registry this release ships — one instance, same release as ici."""

    return PackRegistry(packs=(PYTHON_PACK, CPP_PACK, INTEGRATION_PACK), qt=QT_EXTENSION)
