"""Assess full payment today without choosing alternative strategies."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Optional

from models import PurchaseRequest
from models.decision_values import AFFORDABLE_NOW, FULL_PAYMENT_UNSAFE, PAY_IN_FULL
from models.money import validate_money


@dataclass(frozen=True)
class FullPaymentAssessment:
    """Intermediate result; unsafe full payment leaves recommendations unset."""

    can_pay_in_full: bool
    affordability_status: Literal["affordable_now", "full_payment_unsafe"]
    recommended_payment_method: Optional[Literal["pay_in_full"]]
    recommended_payment_amount: Optional[Decimal]


def assess_full_payment(
    purchase_request: PurchaseRequest, amount_safe_to_pay: Decimal
) -> FullPaymentAssessment:
    """Approve exactly when requested amount <= supplied safe amount.

    Both amounts must be finite, nonnegative Decimals. An approved payment is
    exactly the requested amount, including zero. Unsafe full payment does not
    select a partial payment or any later strategy. Inputs are unchanged.
    """
    requested_amount = purchase_request.amount
    for name, amount in (
        ("purchase_request.amount", requested_amount),
        ("amount_safe_to_pay", amount_safe_to_pay),
    ):
        if amount is None:
            raise TypeError(f"{name} must be a Decimal")
        validate_money(name, amount)
    if requested_amount <= amount_safe_to_pay:
        return FullPaymentAssessment(
            True, AFFORDABLE_NOW, PAY_IN_FULL, requested_amount
        )
    return FullPaymentAssessment(False, FULL_PAYMENT_UNSAFE, None, None)
