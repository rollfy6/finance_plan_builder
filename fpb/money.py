"""Money helpers. All engine math is Decimal and rounded to cents at each posting."""

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
TWELVE = Decimal("12")
DAYS_PER_YEAR = Decimal("365")
_CENT = Decimal("0.01")


def q(x: Decimal) -> Decimal:
    """Round to whole cents, half up."""
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)
