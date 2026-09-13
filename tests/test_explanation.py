"""Template wording from computed recommendations without changing decisions."""

from copy import deepcopy
from dataclasses import fields
from datetime import date
from decimal import Decimal, localcontext

import pytest
from decision import decide_purchase
from decision.explanation import attach_decision_explanation, build_decision_explanation
from finance import forecast_balance
from models import (
    DecisionResult,
    FinancialProfile,
    Income,
    PaymentPlan,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)
from models.decision_values import DO_NOT_PROCEED, INSTALLMENTS, PAY_IN_FULL, WAIT
from models.spending import SpendingChange

TODAY = date(2026, 9, 12)
FIRST = date(2026, 9, 15)
LAST = date(2026, 9, 20)


def computed(amount, *, balance=200, incomes=(), obligations=(), expenses=(), plan=None):  # noqa: PLR0913 -- independent scenario inputs
    profile = FinancialProfile(
        currency="CAD", account_balance=Decimal(balance), minimum_balance=Decimal(20),
        incomes=list(incomes), pending_payments=list(obligations),
        recurring_expenses=list(expenses),
    )
    purchase = PurchaseRequest(Decimal(amount))
    forecast = forecast_balance(profile, TODAY, LAST)
    decision = decide_purchase(profile, purchase, forecast, adjustment_plan=plan)
    return decision, purchase, profile, forecast


def test_full_now_from_real_decision():
    context = computed(100)
    assert build_decision_explanation(*context) == (
        "The purchase of CAD 100 is within today's safe amount of CAD 180; pay in full now. "
        "The required reserve of CAD 20 remains protected.")


def test_plan_from_real_decision():
    context = computed(150, balance=100, incomes=[Income(Decimal(100), "work", expected_date=LAST)])
    text = build_decision_explanation(*context)
    assert "2-payment plan totaling CAD 150" in text
    assert "CAD 80 on 2026-09-12" in text
    assert "CAD 70 on 2026-09-20" in text
    assert "reserve of CAD 20 remains protected" in text
    assert "confirmed income of CAD 100 on 2026-09-20" in text


def test_wait_from_real_decision_mentions_relevant_events():
    context = computed(50, balance=100,
                       incomes=[Income(Decimal(100), expected_date=LAST)],
                       obligations=[PendingPayment(Decimal(90), FIRST)])
    assert build_decision_explanation(*context) == (
        "Wait to pay in full; the earliest safe date is 2026-09-20. Today's safe amount is CAD 0. "
        "The forecast includes confirmed income of CAD 100 on 2026-09-20 and "
        "an obligation of CAD 90 on 2026-09-15.")


def test_spending_changes_do_not_claim_purchase_within_original_safe_amount():
    context = computed(100, balance=150, expenses=[RecurringExpense(
        Decimal(60), "fun", category="discretionary", next_due_date=FIRST)])
    assert build_decision_explanation(*context) == (
        "Pay the purchase of CAD 100 in full after the required spending reductions. "
        "Required spending reductions: fun by CAD 30 on 2026-09-15. "
        "The required reserve of CAD 20 remains protected.")


def test_plan_requiring_spending_changes():
    plan = PaymentPlan([PendingPayment(Decimal(30), FIRST), PendingPayment(Decimal(100), LAST)],
                       Decimal(130))
    context = computed(130, balance=50, incomes=[Income(Decimal(100), expected_date=LAST)],
                       expenses=[RecurringExpense(Decimal(40), "fun", category="flexible",
                                                  next_due_date=FIRST)], plan=plan)
    text = build_decision_explanation(*context)
    assert "2-payment plan" in text
    assert "fun by CAD 40 on 2026-09-15" in text
    assert "reserve of CAD 20 remains protected" in text
    assert "forecast includes" not in text


def test_do_not_proceed_from_real_decision():
    context = computed(1000, balance=100)
    assert build_decision_explanation(*context) == (
        "Do not proceed: no safe full payment, payment plan, or future full-payment date "
        "is available within the evaluated horizon through 2026-09-20.")


@pytest.mark.parametrize("method", [PAY_IN_FULL, INSTALLMENTS, WAIT, DO_NOT_PROCEED, None])
def test_missing_optional_context(method):
    decision = DecisionResult(recommended_payment_method=method)
    text = build_decision_explanation(decision)
    assert text
    assert "None" not in text
    assert "CAD" not in text
    assert "reserve" not in text
    assert "2026" not in text
    if method == PAY_IN_FULL:
        assert "within today's safe amount" not in text
    if method == WAIT:
        assert "earliest safe date" not in text


def test_plan_missing_dates_and_total_and_single_payment():
    decision = DecisionResult(recommended_payment_method=INSTALLMENTS,
                              payment_plan=PaymentPlan([PendingPayment(Decimal("12.34"))]))
    assert build_decision_explanation(decision) == "Use the 1-payment plan (12.34)."
    decision.payment_plan.payments.clear()
    assert build_decision_explanation(decision) == "Use the recommended payment plan."


def test_long_plan_summarizes_endpoints_without_changing_schedule():
    payments = [PendingPayment(Decimal(10), TODAY), PendingPayment(Decimal(20), FIRST),
                PendingPayment(Decimal(30), LAST)]
    decision = DecisionResult(recommended_payment_method=INSTALLMENTS,
                              payment_plan=PaymentPlan(payments, Decimal(60)))
    text = build_decision_explanation(decision)
    assert "3-payment plan totaling 60" in text
    assert "first and last: 10 on 2026-09-12; 30 on 2026-09-20" in text
    assert "20 on 2026-09-15" not in text
    assert decision.payment_plan.payments == payments


@pytest.mark.parametrize("method", [PAY_IN_FULL, INSTALLMENTS, WAIT, DO_NOT_PROCEED])
def test_attach_preserves_every_other_field_and_input_objects(method):
    decision, purchase, profile, forecast = computed(100)
    decision.recommended_payment_method = method
    decision.payment_plan = PaymentPlan([PendingPayment(Decimal(5), FIRST)], Decimal(5), "debit")
    decision.spending_changes_needed = [SpendingChange(
        "internal-identifier", "coffee", FIRST, Decimal(10), Decimal(5), Decimal(5))]
    decision.decision_explanation = "old text"
    original = deepcopy((decision, purchase, profile, forecast))
    attached = attach_decision_explanation(decision, purchase, profile, forecast)
    assert attached is not decision
    for item in fields(decision):
        if item.name != "decision_explanation":
            assert getattr(attached, item.name) == getattr(decision, item.name)
    assert attached.decision_explanation != "old text"
    assert "internal-identifier" not in attached.decision_explanation
    assert (decision, purchase, profile, forecast) == original
    attached.payment_plan.payments.clear()
    attached.spending_changes_needed.clear()
    assert (decision, purchase, profile, forecast) == original


def test_deterministic_and_no_evaluators_called(monkeypatch):
    context = computed(100)

    def forbidden(*args, **kwargs):
        pytest.fail("Explanation must not run financial evaluators")

    monkeypatch.setattr("decision.decision_engine.decide_purchase", forbidden)
    monkeypatch.setattr("finance.forecast_balance", forbidden)
    assert build_decision_explanation(*context) == build_decision_explanation(*context)


def test_unknown_currency_exact_decimal_and_higher_reserve():
    decision = DecisionResult(amount_safe_to_pay=Decimal("100.12345"),
                              recommended_payment_method=PAY_IN_FULL)
    purchase = PurchaseRequest(Decimal("10.12345"))
    profile = FinancialProfile(minimum_balance=Decimal(10), preferred_balance=Decimal(30))
    with localcontext() as context:
        context.prec = 2
        text = build_decision_explanation(decision, purchase, profile)
    assert "10.12345" in text
    assert "100.12345" in text
    assert "reserve of 30" in text
    assert "$" not in text


def test_legacy_changes_and_missing_expense_name():
    decision = DecisionResult(recommended_payment_method=PAY_IN_FULL,
                              spending_changes_needed=["Reduce optional outings", SpendingChange(
                                  "id", "", FIRST, Decimal(10), Decimal(5), Decimal(5))])
    text = build_decision_explanation(decision)
    assert "Reduce optional outings" in text
    assert "flexible spending by 5 on 2026-09-15" in text


def test_raw_undated_income_is_not_mentioned_and_forecast_currency_fallback():
    decision, purchase, profile, forecast = computed(100)
    profile.currency = None
    profile.incomes.append(Income(Decimal(9999), "unconfirmed future income"))
    text = build_decision_explanation(decision, purchase, profile, forecast)
    assert "CAD 100" in text
    assert "9999" not in text
    assert "unconfirmed" not in text
