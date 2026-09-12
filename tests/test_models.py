"""Focused checks for model construction and structural validation."""

from datetime import date
from decimal import Decimal

import pytest
from models import (
    DecisionResult,
    FinancialProfile,
    Income,
    PaymentPlan,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
    Transaction,
)


def test_construct_financial_inputs():
    transaction = Transaction(Decimal("-12.50"), date(2026, 1, 1))
    expense = RecurringExpense(Decimal(10), frequency="monthly")
    income = Income(Decimal(100), source="work")
    payment = PendingPayment(Decimal(20), due_date=date(2026, 2, 1))
    purchase = PurchaseRequest(Decimal(30), description="item")
    profile = FinancialProfile(
        currency="CAD",
        account_balance=Decimal(-5),
        savings_balance=Decimal(0),
        transactions=[transaction],
        recurring_expenses=[expense],
        incomes=[income],
        pending_payments=[payment],
    )
    assert profile.transactions[0].amount == Decimal("-12.50")
    assert profile.recurring_expenses == [expense]
    assert profile.incomes == [income]
    assert profile.pending_payments == [payment]
    assert purchase.amount == Decimal(30)


def test_defaults_are_unknown_and_collections_are_independent():
    profile = FinancialProfile()
    assert profile.account_balance is None
    assert profile.savings_balance is None
    assert profile.currency is None
    profile.transactions.append(Transaction(Decimal(0)))
    assert FinancialProfile().transactions == []
    assert Income(Decimal(0)).expected_date is None
    assert RecurringExpense(Decimal(0)).frequency is None
    assert PendingPayment(Decimal(0)).due_date is None
    assert PurchaseRequest(Decimal(0)).desired_date is None
    plan = PaymentPlan()
    assert plan.total_amount is None
    plan.payments.append(PendingPayment(Decimal(0)))
    assert PaymentPlan().payments == []
    result = DecisionResult()
    assert result.amount_safe_to_pay is None
    assert result.affordability_status is None
    assert result.recommended_payment_method is None
    assert result.payment_plan is None
    assert result.earliest_date_for_full_payment is None
    assert result.decision_explanation == ""
    result.spending_changes_needed.append("Review subscriptions")
    assert DecisionResult().spending_changes_needed == []


@pytest.mark.parametrize(
    "model, field",
    [
        (RecurringExpense, "amount"),
        (Income, "amount"),
        (PendingPayment, "amount"),
        (PurchaseRequest, "amount"),
        (FinancialProfile, "savings_balance"),
        (PaymentPlan, "total_amount"),
        (DecisionResult, "amount_safe_to_pay"),
    ],
)
def test_negative_amounts_are_rejected(model, field):
    with pytest.raises(ValueError, match="must not be negative"):
        model(**{field: Decimal("-0.01")})


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_money_is_rejected(amount):
    with pytest.raises(ValueError, match="must be finite"):
        Transaction(amount)


@pytest.mark.parametrize("amount", [1.5, "1.50", None])
def test_required_money_must_be_decimal(amount):
    with pytest.raises(TypeError, match="must be a Decimal"):
        PurchaseRequest(amount)


def test_complete_decision_result():
    full_payment_date = date(2026, 2, 1)
    plan = PaymentPlan(
        payments=[PendingPayment(Decimal(25), full_payment_date)],
        total_amount=Decimal(25),
        payment_method="debit",
    )
    result = DecisionResult(
        amount_safe_to_pay=Decimal(25),
        affordability_status="supplied status",
        recommended_payment_method="debit",
        payment_plan=plan,
        earliest_date_for_full_payment=full_payment_date,
        spending_changes_needed=["Review subscriptions"],
        decision_explanation="An explanation supplied by the caller.",
    )
    assert result.amount_safe_to_pay == Decimal(25)
    assert result.affordability_status == "supplied status"
    assert result.recommended_payment_method == "debit"
    assert result.payment_plan is plan
    assert result.earliest_date_for_full_payment == full_payment_date
    assert result.spending_changes_needed == ["Review subscriptions"]
    assert result.decision_explanation == "An explanation supplied by the caller."
