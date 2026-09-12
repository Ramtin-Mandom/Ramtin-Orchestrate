"""Structured expense changes shared by evaluations and final results."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class SpendingChange:
    expense_identifier: str
    expense_name: str
    affected_date: date
    original_amount: Decimal
    reduction_amount: Decimal
    remaining_amount: Decimal
