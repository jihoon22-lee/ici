def _increment(value: int) -> int:
    return value + 1


def public_api(value: int) -> int:
    return _increment(value)
