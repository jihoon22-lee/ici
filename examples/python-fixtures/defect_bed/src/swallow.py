"""A swallowed exception — the handler body is only ``pass``."""


def risky():
    try:
        int("not a number")
    except ValueError:
        pass
