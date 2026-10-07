"""A private module-level function nothing references."""


def used_entry() -> int:
    return 1


def _orphan_helper() -> int:
    total = 0
    for index in range(10):
        total += index
    return total


ALIVE = used_entry()
