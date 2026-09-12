"""Decision output containers; callers supply all decisions and schedules."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Optional

from .money import validate_money
from .records import PendingPayment


@dataclass
class PaymentPlan:
    """An explicitly supplied schedule; totals are not calculated or reconciled."""

    payments: List[PendingPayment] = field(default_factory=list)
    total_amount: Optional[Decimal] = None
    payment_method: Optional[str] = None

    def __post_init__(self) -> None:
        validate_money("total_amount", self.total_amount)


@dataclass
class DecisionResult:
    amount_safe_to_pay: Optional[Decimal] = None
    affordability_status: Optional[str] = None
    recommended_payment_method: Optional[str] = None
    payment_plan: Optional[PaymentPlan] = None
    earliest_date_for_full_payment: Optional[date] = None
    spending_changes_needed: List[str] = field(default_factory=list)
    decision_explanation: str = ""

    def __post_init__(self) -> None:
        validate_money("amount_safe_to_pay", self.amount_safe_to_pay)
