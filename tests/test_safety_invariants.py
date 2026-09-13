"""Cross-module safety boundaries beyond existing individual-module examples."""

import csv
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest
from decision import decide_purchase
from extraction.media_extractor import extract_media
from finance import assess_full_payment, evaluate_spending_adjustments, forecast_balance
from models import (
    FinancialProfile,
    Income,
    PaymentPlan,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)
from models.decision_values import DO_NOT_PROCEED, INSTALLMENTS, PAY_IN_FULL, WAIT
from output import OUTPUT_COLUMNS
from pipeline import run_pipeline

TODAY = date(2026, 9, 12)
FIRST = date(2026, 9, 15)
LAST = date(2026, 9, 20)


def assert_safe_schedule(payments, purchase, forecast, reserve):
    """Independently replay today's before-events and future after-events timing."""
    assert all(payment.amount > 0 for payment in payments)
    assert sum((payment.amount for payment in payments), Decimal(0)) == purchase.amount
    assert all(payment.amount <= purchase.amount for payment in payments)
    paid = sum((payment.amount for payment in payments if payment.due_date == TODAY), Decimal(0))
    for index, entry in enumerate(forecast.entries):
        is_closing = index == len(forecast.entries) - 1 or forecast.entries[index + 1].date != entry.date
        if entry.date != TODAY and is_closing:
            paid += sum((payment.amount for payment in payments if payment.due_date == entry.date), Decimal(0))
        assert entry.balance - paid >= reserve, f"unsafe projected balance at forecast entry {index}"
        assert paid <= purchase.amount


@pytest.mark.parametrize("balance,reserve,amount,method", [
    (0, 0, 0, PAY_IN_FULL), (0, 0, 1, DO_NOT_PROCEED),
    (-5, 0, 0, DO_NOT_PROCEED), (10, 0, 10, PAY_IN_FULL),
    (10, 20, 0, DO_NOT_PROCEED),
])
def test_zero_boundaries_through_decision_and_payment_validation(balance, reserve, amount, method):
    profile = FinancialProfile(account_balance=Decimal(balance), minimum_balance=Decimal(reserve))
    purchase = PurchaseRequest(Decimal(amount))
    forecast = forecast_balance(profile, TODAY, LAST)
    original = deepcopy((profile, purchase, forecast))
    result = decide_purchase(profile, purchase, forecast)
    assert result.recommended_payment_method == method
    assert result.payment_plan is None
    if method == PAY_IN_FULL:
        assessment = assess_full_payment(purchase, result.amount_safe_to_pay)
        assert assessment.recommended_payment_amount == purchase.amount
        assert all(entry.balance - purchase.amount >= Decimal(reserve) for entry in forecast.entries)
    else:
        assert result.earliest_date_for_full_payment is None
    assert (profile, purchase, forecast) == original


def test_same_date_multi_events_accepted_plan_preserves_every_intermediate_balance():
    profile = FinancialProfile(account_balance=Decimal(100), minimum_balance=Decimal(20),
                               incomes=[Income(Decimal(40), "a", expected_date=FIRST),
                                        Income(Decimal(60), "b", expected_date=FIRST),
                                        Income(Decimal(50), "c", expected_date=LAST)],
                               pending_payments=[PendingPayment(Decimal(20), FIRST)],
                               essential_expenses=[RecurringExpense(Decimal(30), "rent", next_due_date=FIRST)],
                               recurring_expenses=[RecurringExpense(Decimal(10), "bill", next_due_date=FIRST)])
    purchase = PurchaseRequest(Decimal(150))
    forecast = forecast_balance(profile, TODAY, LAST)
    result = decide_purchase(profile, purchase, forecast)
    assert result.recommended_payment_method == INSTALLMENTS
    assert result.payment_plan.total_amount == purchase.amount
    assert_safe_schedule(result.payment_plan.payments, purchase, forecast, profile.minimum_balance)


def test_fractional_plan_finishes_exactly_at_reserve():
    profile = FinancialProfile(account_balance=Decimal("20.05"), minimum_balance=Decimal(20),
                               incomes=[Income(Decimal("0.05"), expected_date=FIRST)])
    purchase = PurchaseRequest(Decimal("0.10"))
    forecast = forecast_balance(profile, TODAY, LAST)
    result = decide_purchase(profile, purchase, forecast)
    assert result.recommended_payment_method == INSTALLMENTS
    assert result.payment_plan.total_amount == Decimal("0.10")
    assert_safe_schedule(result.payment_plan.payments, purchase, forecast, Decimal(20))


def test_large_late_income_cannot_justify_an_immediate_payment_before_major_expense():
    profile = FinancialProfile(account_balance=Decimal(100), minimum_balance=Decimal(20),
                               pending_payments=[PendingPayment(Decimal(90), FIRST)],
                               incomes=[Income(Decimal(1000), expected_date=LAST)])
    result = decide_purchase(profile, PurchaseRequest(Decimal(50)), forecast_balance(profile, TODAY, LAST))
    assert result.recommended_payment_method == WAIT
    assert result.amount_safe_to_pay == 0
    assert result.payment_plan is None
    assert result.earliest_date_for_full_payment == LAST


@pytest.mark.parametrize("amount,method", [(Decimal(100), PAY_IN_FULL), (Decimal("100.01"), DO_NOT_PROCEED)])
def test_exact_flexible_budget_threshold_never_borrows_from_essential_expenses(amount, method):
    profile = FinancialProfile(account_balance=Decimal(150), minimum_balance=Decimal(20),
                               essential_expenses=[RecurringExpense(Decimal(20), "rent", category="flexible",
                                                                    next_due_date=FIRST)],
                               recurring_expenses=[RecurringExpense(Decimal(30), "fun", category="flexible",
                                                                    next_due_date=FIRST)],
                               pending_payments=[PendingPayment(Decimal(10), LAST)])
    purchase = PurchaseRequest(amount)
    forecast = forecast_balance(profile, TODAY, LAST)
    original = deepcopy(profile)
    result = decide_purchase(profile, purchase, forecast)
    assert result.recommended_payment_method == method
    adjusted = evaluate_spending_adjustments(profile, forecast, Decimal(20), purchase)
    if method == PAY_IN_FULL:
        assert adjusted.feasible
        assert all(balance >= Decimal(20) for balance in adjusted.adjusted_balances)
        assert [change.expense_name for change in result.spending_changes_needed] == ["fun"]
        assert result.spending_changes_needed[0].remaining_amount == 0
    else:
        assert not adjusted.feasible
        assert adjusted.spending_changes_needed == ()
        assert result.spending_changes_needed == []
    assert profile == original


def test_supplied_later_installment_failure_rejects_the_complete_plan():
    profile = FinancialProfile(account_balance=Decimal(100), minimum_balance=Decimal(20),
                               pending_payments=[PendingPayment(Decimal(50), LAST)])
    forecast = forecast_balance(profile, TODAY, LAST)
    plan = PaymentPlan([PendingPayment(Decimal(10), TODAY), PendingPayment(Decimal(40), LAST)], Decimal(50))
    assert forecast.entries[0].balance - plan.payments[0].amount >= Decimal(20)
    assert forecast.entries[-1].balance - plan.total_amount < Decimal(20)
    result = evaluate_spending_adjustments(profile, forecast, Decimal(20), plan)
    assert not result.feasible
    assert result.spending_changes_needed == ()
    assert result.adjusted_balances == ()


@pytest.mark.parametrize("zero_date", [FIRST, LAST])
def test_zero_installment_cannot_be_accepted_in_an_adjusted_recommendation(zero_date):
    profile = FinancialProfile(account_balance=Decimal(50), minimum_balance=Decimal(20),
                               incomes=[Income(Decimal(100), expected_date=LAST)],
                               recurring_expenses=[RecurringExpense(Decimal(40), "fun", category="flexible",
                                                                    next_due_date=FIRST)])
    plan = PaymentPlan([PendingPayment(Decimal(30), FIRST), PendingPayment(Decimal(0), zero_date),
                        PendingPayment(Decimal(100), LAST)], Decimal(130))
    forecast = forecast_balance(profile, TODAY, LAST)
    with pytest.raises(ValueError, match="positive"):
        decide_purchase(profile, PurchaseRequest(Decimal(130)), forecast, adjustment_plan=plan)


def test_empty_zero_total_adjustment_plan_remains_valid():
    profile = FinancialProfile(account_balance=Decimal(0), minimum_balance=Decimal(0))
    forecast = forecast_balance(profile, TODAY, LAST)
    result = evaluate_spending_adjustments(profile, forecast, Decimal(0), PaymentPlan([], Decimal(0)))
    assert result.feasible
    assert result.spending_changes_needed == ()
    assert result.adjusted_balances == (Decimal(0),)


def test_mixed_recoverable_media_failures_write_every_row_without_invented_income(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    identifiers = ["no-media", "unsupported", "malformed", "api-failure"]
    (media_root / "unsupported.bin").write_bytes(b"unsupported")
    (media_root / "malformed.txt").write_text("untrusted text", encoding="utf-8")
    (media_root / "api-failure.txt").write_text("untrusted text", encoding="utf-8")
    source = tmp_path / "requests.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["request_id", "amount", "account_balance", "minimum_balance", "request_date"])
        writer.writerows((identifier, "80", "100", "20", TODAY.isoformat()) for identifier in identifiers)
    request = Mock(side_effect=["not JSON", TimeoutError("private authorization details")])

    def extractor(media):
        return extract_media(media, request=request)

    warnings = []
    output = tmp_path / "output.csv"
    results = run_pipeline(source, media_root, output, extractor=extractor, on_warning=warnings.append)
    assert all(result.recommended_payment_method == PAY_IN_FULL for result in results)
    assert all(result.payment_plan is None for result in results)
    with output.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(OUTPUT_COLUMNS)
        rows = list(reader)
    assert [row["request_id"] for row in rows] == identifiers
    assert all(row["amount_safe_to_pay"] == "80" and row["payment_plan"] == "none" for row in rows)
    assert all("income" not in row["decision_explanation"] for row in rows)
    assert "private" not in " ".join(warnings)
    assert request.call_count == len(identifiers) - len(["no-media", "unsupported"])
