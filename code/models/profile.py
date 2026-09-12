"""Shared financial context; absent balances remain unknown."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .money import validate_money
from .records import Income, PendingPayment, RecurringExpense, Transaction


@dataclass
class FinancialProfile:
    currency: Optional[str] = None
    account_balance: Optional[Decimal] = None
    savings_balance: Optional[Decimal] = None
    transactions: List[Transaction] = field(default_factory=list)
    recurring_expenses: List[RecurringExpense] = field(default_factory=list)
    incomes: List[Income] = field(default_factory=list)
    pending_payments: List[PendingPayment] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_money("account_balance", self.account_balance, allow_negative=True)
        validate_money("savings_balance", self.savings_balance)
