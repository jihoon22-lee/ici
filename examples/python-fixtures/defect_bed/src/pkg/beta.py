"""beta -> alpha edge of the planted import cycle."""

import pkg.alpha


def beta_value() -> int:
    return pkg.alpha.alpha_value() + 1
