"""One small, obvious function. Nothing for a detector to find."""


def apply_rate(amount: float, rate: float) -> float:
    """Return ``amount`` scaled by ``rate``."""

    if rate < 0:
        raise ValueError("rate must not be negative")
    return amount * rate
