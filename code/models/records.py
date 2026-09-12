"""Financial records and purchase input, without calculation rules."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .money import validate_money


@dataclass
class Transaction:
    """Signed amount: positive inflow, negative outflow."""

    amount: Decimal
    date: Optional[date] = None
    description: str = ""
    category: Optional[str] = None

    def __post_init__(self) -> None:
        validate_money("amount", self.amount, allow_negative=True)
        if self.amount is None:
            raise TypeError("amount must be a Decimal")


@dataclass
class RecurringExpense:
    amount: Decimal
    description: str = ""
    frequency: Optional[str] = None
    next_due_date: Optional[date] = None
    category: Optional[str] = None

    def __post_init__(self) -> None:
        validate_money("amount", self.amount)
        if self.amount is None:
            raise TypeError("amount must be a Decimal")


@dataclass
class Income:
    amount: Decimal
    source: str = ""
    frequency: Optional[str] = None
    expected_date: Optional[date] = None

    def __post_init__(self) -> None:
        validate_money("amount", self.amount)
        if self.amount is None:
            raise TypeError("amount must be a Decimal")


@dataclass
class PendingPayment:
    amount: Decimal
    due_date: Optional[date] = None
    description: str = ""
    payment_method: Optional[str] = None

    def __post_init__(self) -> None:
        validate_money("amount", self.amount)
        if self.amount is None:
            raise TypeError("amount must be a Decimal")


@dataclass
class PurchaseRequest:
    amount: Decimal
    description: str = ""
    desired_date: Optional[date] = None
    preferred_payment_method: Optional[str] = None

    def __post_init__(self) -> None:
        validate_money("amount", self.amount)
        if self.amount is None:
            raise TypeError("amount must be a Decimal")
