"""In-memory configuration for ordinary source trees; never writes ici.toml."""

from pathlib import Path

from ici.config.composition import EffectiveConfig, compose
from ici.config.scaffold import propose
from ici.config.schema import read_root


def automatic(start: Path) -> tuple[Path, EffectiveConfig] | None:
    """Use an existing ici config, otherwise inspect the checkout (or cwd).

    Existing malformed or legacy configuration is never silently ignored.
    Ecosystem settings remain owned and read by their respective tools.
    """
    start = start.resolve()
    root = start
    for directory in (start, *start.parents):
        if (directory / "ici.toml").exists():
            return None
        if any((directory / marker).exists() for marker in (".git", ".hg", ".svn")):
            root = directory
            break
    proposal = propose(root)
    if not proposal.candidates:
        raise ValueError(f"no supported sources discovered in {root}")
    document = read_root(proposal.text, path=str(root / "<automatic>"))
    return root, compose(document)
