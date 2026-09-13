import pytest
from calc.rates import apply_rate


def test_a_rate_scales_the_amount():
    assert apply_rate(100.0, 0.2) == 20.0


def test_a_negative_rate_is_refused():
    with pytest.raises(ValueError):
        apply_rate(100.0, -1.0)
