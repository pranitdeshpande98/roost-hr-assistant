"""Hours as people write them, in confirmations and the console."""

from decimal import Decimal

import pytest

from roost.leave.repository import format_hours


@pytest.mark.parametrize(
    ("hours", "text"),
    [
        (Decimal("8.00"), "8"),
        (Decimal("40.00"), "40"),
        (Decimal("4.50"), "4.5"),
        (Decimal("0.00"), "0"),
        (Decimal("-8.00"), "-8"),
        (Decimal("100"), "100"),
    ],
)
def test_format_hours(hours: Decimal, text: str) -> None:
    assert format_hours(hours) == text
