"""The component that carries the defect: one function over the complexity limit."""


def classify(a, b, c, d, e, f, g, h):
    """Branch far past the threshold so the complexity engine has to report it."""

    if a > 0:
        if b > 0:
            return "ab"
        if c > 0:
            return "ac"
        if d > 0:
            return "ad"
        return "a"
    if b > 0:
        if c > 0:
            return "bc"
        if d > 0:
            return "bd"
        if e > 0:
            return "be"
        return "b"
    if c > 0:
        if d > 0:
            return "cd"
        if e > 0:
            return "ce"
        return "c"
    if d > 0:
        if e > 0:
            return "de"
        return "d"
    if e > 0:
        return "e"
    if f > 0:
        return "f"
    if g > 0:
        return "g"
    if h > 0:
        return "h"
    return "none"
