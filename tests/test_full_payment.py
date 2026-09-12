"""Full payment uses only the requested amount and supplied safe amount."""

from copy import deepcopy
from decimal import Decimal, localcontext

import pytest
from finance import assess_full_payment
from models import PurchaseRequest


@pytest.mark.parametrize(
    "requested, safe", [("25.01", "50"), ("25.01", "25.01"), ("0", "0")]
)
def test_approved_payment_is_exactly_requested_and_inputs_unchanged(requested, safe):
    purchase = PurchaseRequest(
        Decimal(requested), "item", preferred_payment_method="debit"
    )
    safe_amount = Decimal(safe)
    original = deepcopy((purchase, safe_amount))
    assessment = assess_full_payment(purchase, safe_amount)
    assert assessment.can_pay_in_full is True
    assert assessment.affordability_status == "affordable_now"
    assert assessment.recommended_payment_method == "pay_in_full"
    assert assessment.recommended_payment_amount == purchase.amount
    assert assessment.recommended_payment_amount <= purchase.amount
    assert (purchase, safe_amount) == original


@pytest.mark.parametrize("safe", [Decimal(10), Decimal(0)])
def test_unsafe_full_payment_has_no_alternative_or_partial_recommendation(safe):
    purchase = PurchaseRequest(Decimal(20))
    original = deepcopy(purchase)
    assessment = assess_full_payment(purchase, safe)
    assert assessment.can_pay_in_full is False
    assert assessment.affordability_status == "full_payment_unsafe"
    assert assessment.recommended_payment_method is None
    assert assessment.recommended_payment_amount is None
    assert purchase == original


def test_decimal_comparison_does_not_round():
    purchase = PurchaseRequest(Decimal("123456789012345678901234567890.12"))
    with localcontext() as context:
        context.prec = 2
        assert (
            assess_full_payment(
                purchase, Decimal("123456789012345678901234567890.11")
            ).can_pay_in_full
            is False
        )
        assessment = assess_full_payment(purchase, purchase.amount)
    assert assessment.recommended_payment_amount == purchase.amount


@pytest.mark.parametrize("field", ["purchase_request.amount", "amount_safe_to_pay"])
@pytest.mark.parametrize(
    "value",
    [None, "20", 20, 20.0, True, Decimal(-1), Decimal("NaN"), Decimal("Infinity")],
)
def test_invalid_amounts_have_field_context(field, value):
    purchase = PurchaseRequest(Decimal(20))
    safe = Decimal(30)
    if field == "purchase_request.amount":
        purchase.amount = value
    else:
        safe = value
    with pytest.raises((TypeError, ValueError), match=field):
        assess_full_payment(purchase, safe)
