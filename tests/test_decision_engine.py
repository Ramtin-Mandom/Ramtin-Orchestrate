"""Final option priority with real deterministic finance modules."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from decision import decide_purchase, decision_engine
from finance import evaluate_spending_adjustments, forecast_balance
from models import (
    FinancialProfile,
    Income,
    PaymentPlan,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)
from models.decision_values import (
    AFFORDABLE_LATER,
    AFFORDABLE_NOW,
    AFFORDABLE_WITH_PLAN,
    DO_NOT_PROCEED,
    INSTALLMENTS,
    NOT_AFFORDABLE,
    PAY_IN_FULL,
    WAIT,
)
from models.spending import SpendingChange

TODAY = date(2026, 9, 12)
FIRST = date(2026, 9, 15)
LATER = date(2026, 9, 20)


def test_full_now_priority_and_inputs_unchanged(monkeypatch):
    profile = FinancialProfile(
        account_balance=Decimal(200), minimum_balance=Decimal(20)
    )
    purchase = PurchaseRequest(Decimal(100))
    forecast = forecast_balance(profile, TODAY, LATER)
    original = deepcopy((profile, purchase, forecast))

    def unexpected_plan(*args):
        pytest.fail("full now must be selected before evaluating plans")

    monkeypatch.setattr(decision_engine, "plan_payments", unexpected_plan)
    result = decide_purchase(profile, purchase, forecast)
    assert result.affordability_status == AFFORDABLE_NOW
    assert result.recommended_payment_method == PAY_IN_FULL
    assert result.amount_safe_to_pay == Decimal(180)
    assert result.earliest_date_for_full_payment == TODAY
    assert result.payment_plan is None
    assert result.spending_changes_needed == []
    assert result.decision_explanation == ""
    assert (profile, purchase, forecast) == original


def test_safe_plan_selected_before_wait():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(20),
        incomes=[Income(Decimal(100), expected_date=LATER)],
    )
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(150)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.affordability_status == AFFORDABLE_WITH_PLAN
    assert result.recommended_payment_method == INSTALLMENTS
    assert result.amount_safe_to_pay == Decimal(80)
    assert result.earliest_date_for_full_payment == LATER
    assert [payment.amount for payment in result.payment_plan.payments] == [
        Decimal(80),
        Decimal(70),
    ]
    assert result.payment_plan.total_amount == Decimal(150)
    assert result.spending_changes_needed == []
    assert result.decision_explanation == ""


def test_wait_when_baseline_plan_is_unsafe():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(20),
        pending_payments=[PendingPayment(Decimal(90), FIRST)],
        incomes=[Income(Decimal(100), expected_date=LATER)],
    )
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(50)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.affordability_status == AFFORDABLE_LATER
    assert result.recommended_payment_method == WAIT
    assert result.earliest_date_for_full_payment == LATER
    assert result.amount_safe_to_pay == Decimal(0)
    assert result.payment_plan is None
    assert result.spending_changes_needed == []


def test_full_payment_requires_structured_flexible_changes():
    profile = FinancialProfile(
        account_balance=Decimal(150),
        minimum_balance=Decimal(20),
        recurring_expenses=[
            RecurringExpense(
                Decimal(60), "fun", category="discretionary", next_due_date=FIRST
            )
        ],
    )
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(100)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.affordability_status == AFFORDABLE_NOW
    assert result.recommended_payment_method == PAY_IN_FULL
    assert result.amount_safe_to_pay == Decimal(70)
    assert result.payment_plan is None
    assert result.earliest_date_for_full_payment is None
    (change,) = result.spending_changes_needed
    assert isinstance(change, SpendingChange)
    assert change.reduction_amount == Decimal(30)
    assert result.decision_explanation == ""


def test_wait_precedes_feasible_flexible_changes():
    profile = FinancialProfile(
        account_balance=Decimal(150),
        minimum_balance=Decimal(20),
        recurring_expenses=[
            RecurringExpense(
                Decimal(140), "fun", category="discretionary", next_due_date=FIRST
            )
        ],
        incomes=[Income(Decimal(100), expected_date=LATER)],
    )
    purchase = PurchaseRequest(Decimal(80))
    forecast = forecast_balance(profile, TODAY, LATER)
    assert evaluate_spending_adjustments(
        profile, forecast, Decimal(20), purchase
    ).feasible
    result = decide_purchase(profile, purchase, forecast)
    assert result.affordability_status == AFFORDABLE_LATER
    assert result.recommended_payment_method == WAIT
    assert result.earliest_date_for_full_payment == LATER
    assert result.spending_changes_needed == []


def test_existing_adjustment_plan_requires_validated_changes():
    profile = FinancialProfile(
        account_balance=Decimal(50),
        minimum_balance=Decimal(20),
        recurring_expenses=[
            RecurringExpense(
                Decimal(40), "fun", category="flexible", next_due_date=FIRST
            )
        ],
        incomes=[Income(Decimal(100), expected_date=LATER)],
    )
    forecast = forecast_balance(profile, TODAY, LATER)
    candidate = PaymentPlan(
        [PendingPayment(Decimal(30), FIRST), PendingPayment(Decimal(100), LATER)],
        Decimal(130),
    )
    original = deepcopy(candidate)
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(130)), forecast, adjustment_plan=candidate
    )
    assert result.affordability_status == AFFORDABLE_WITH_PLAN
    assert result.recommended_payment_method == INSTALLMENTS
    assert result.amount_safe_to_pay == Decimal(0)
    assert result.payment_plan == candidate
    assert result.earliest_date_for_full_payment is None
    assert result.spending_changes_needed[0].reduction_amount == Decimal(40)
    assert candidate == original
    result.payment_plan.payments.clear()
    assert candidate == original


def test_no_safe_option():
    profile = FinancialProfile(
        account_balance=Decimal(100), minimum_balance=Decimal(20)
    )
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(1000)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.affordability_status == NOT_AFFORDABLE
    assert result.recommended_payment_method == DO_NOT_PROCEED
    assert result.amount_safe_to_pay == Decimal(80)
    assert result.payment_plan is None
    assert result.earliest_date_for_full_payment is None
    assert result.spending_changes_needed == []
    assert result.decision_explanation == ""


def test_unsafe_adjustment_plan_is_not_selected():
    profile = FinancialProfile(account_balance=Decimal(50), minimum_balance=Decimal(20))
    candidate = PaymentPlan([PendingPayment(Decimal(100), TODAY)], Decimal(100))
    result = decide_purchase(
        profile,
        PurchaseRequest(Decimal(100)),
        forecast_balance(profile, TODAY, LATER),
        adjustment_plan=candidate,
    )
    assert result.affordability_status == NOT_AFFORDABLE
    assert result.payment_plan is None


def test_higher_reserve_and_missing_reserve():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(80),
        preferred_balance=Decimal(20),
    )
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(30)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.amount_safe_to_pay == Decimal(20)
    assert result.recommended_payment_method == DO_NOT_PROCEED
    profile.minimum_balance = None
    profile.preferred_balance = None
    with pytest.raises(ValueError, match="no default"):
        decide_purchase(
            profile,
            PurchaseRequest(Decimal(30)),
            forecast_balance(profile, TODAY, LATER),
        )


def test_zero_purchase_cannot_approve_existing_reserve_violation():
    profile = FinancialProfile(account_balance=Decimal(0), minimum_balance=Decimal(20))
    result = decide_purchase(
        profile, PurchaseRequest(Decimal(0)), forecast_balance(profile, TODAY, LATER)
    )
    assert result.recommended_payment_method == DO_NOT_PROCEED
