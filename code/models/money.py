"""Structural checks for money, represented as Decimal in one currency."""

from decimal import Decimal
from typing import Optional


def validate_money(
    name: str, amount: Optional[Decimal], *, allow_negative: bool = False
) -> None:
    if amount is None:
        return
    if not isinstance(amount, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    if not allow_negative and amount < 0:
        raise ValueError(f"{name} must not be negative")
