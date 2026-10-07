"""A handle acquired and never closed — resource defect on both paths."""


def read_all(path: str) -> str:
    handle = open(path)
    data = handle.read()
    return data
