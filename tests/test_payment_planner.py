"""Greedy plans preserve reserves at every original forecast event."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

import pytest
from finance import forecast_balance, plan_payments
from models import FinancialProfile, Income, PendingPayment, PurchaseRequest


def test_two_payments_exact_total_and_inputs_unchanged():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        incomes=[Income(Decimal(100), expected_date=later)],
    )
    forecast = forecast_balance(profile, today, date(2026, 9, 30))
    purchase = PurchaseRequest(Decimal(150), "item", preferred_payment_method="debit")
    original = deepcopy((purchase, forecast))
    plan = plan_payments(purchase, forecast, Decimal(20), Decimal(80))
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (today, Decimal(80)),
        (later, Decimal(70)),
    ]
    assert (
        plan.total_amount
        == sum((payment.amount for payment in plan.payments), Decimal(0))
        == purchase.amount
    )
    assert all(payment.amount > 0 for payment in plan.payments)
    assert plan.payment_method == "debit"
    assert (purchase, forecast) == original
    assert plan_payments(purchase, forecast, Decimal(20), Decimal(80)) == plan


def test_multi_payment_plan_and_remaining_amount_cap():
    today, first, second = date(2026, 9, 12), date(2026, 9, 15), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        incomes=[
            Income(Decimal(50), expected_date=first),
            Income(Decimal(50), expected_date=second),
        ],
    )
    plan = plan_payments(
        PurchaseRequest(Decimal(160)),
        forecast_balance(profile, today, second),
        Decimal(20),
        Decimal(80),
    )
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (today, Decimal(80)),
        (first, Decimal(50)),
        (second, Decimal(30)),
    ]
    assert plan.total_amount == Decimal(160)


def test_later_obligation_caps_an_overestimated_today_amount():
    today, bill_date, income_date = (
        date(2026, 9, 12),
        date(2026, 9, 15),
        date(2026, 9, 20),
    )
    profile = FinancialProfile(
        account_balance=Decimal(100),
        pending_payments=[PendingPayment(Decimal(70), bill_date)],
        incomes=[Income(Decimal(100), expected_date=income_date)],
    )
    forecast = forecast_balance(profile, today, income_date)
    plan = plan_payments(
        PurchaseRequest(Decimal(50)), forecast, Decimal(20), Decimal(100)
    )
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (today, Decimal(10)),
        (income_date, Decimal(40)),
    ]
    assert forecast.entries[1].balance - plan.payments[0].amount == Decimal(20)


def test_future_payment_after_all_same_day_events():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(30),
        pending_payments=[PendingPayment(Decimal(10), later)],
        incomes=[Income(Decimal(100), expected_date=later)],
    )
    plan = plan_payments(
        PurchaseRequest(Decimal(100)),
        forecast_balance(profile, today, later),
        Decimal(20),
        Decimal(0),
    )
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (later, Decimal(100))
    ]


def test_today_cap_is_respected_even_when_today_income_is_large():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        incomes=[
            Income(Decimal(100), expected_date=today),
            Income(Decimal(1), expected_date=later),
        ],
    )
    plan = plan_payments(
        PurchaseRequest(Decimal(100)),
        forecast_balance(profile, today, later),
        Decimal(20),
        Decimal(10),
    )
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (today, Decimal(10)),
        (later, Decimal(90)),
    ]


def test_future_capacity_keeps_later_same_day_intermediate_dips():
    today, first, later = date(2026, 9, 12), date(2026, 9, 15), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(20),
        incomes=[
            Income(Decimal(100), expected_date=first),
            Income(Decimal(100), expected_date=later),
        ],
        pending_payments=[PendingPayment(Decimal(80), later)],
    )
    plan = plan_payments(
        PurchaseRequest(Decimal(100)),
        forecast_balance(profile, today, later),
        Decimal(20),
        Decimal(0),
    )
    assert plan is not None
    assert [(payment.due_date, payment.amount) for payment in plan.payments] == [
        (first, Decimal(20)),
        (later, Decimal(80)),
    ]


@pytest.mark.parametrize("starting, payment", [(100, 0), (100, 90)])
def test_no_complete_or_reserve_preserving_plan(starting, payment):
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(starting),
        pending_payments=[PendingPayment(Decimal(payment), later)],
        incomes=[Income(Decimal(1000))],
    )
    assert (
        plan_payments(
            PurchaseRequest(Decimal(150)),
            forecast_balance(profile, today, later),
            Decimal(20),
            Decimal(80),
        )
        is None
    )


def test_initially_safe_looking_payment_cannot_ignore_later_expense():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        pending_payments=[PendingPayment(Decimal(70), later)],
    )
    assert (
        plan_payments(
            PurchaseRequest(Decimal(50)),
            forecast_balance(profile, today, later),
            Decimal(20),
            Decimal(80),
        )
        is None
    )


def test_exact_decimal_total_under_low_precision():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal("123456789012345678901234567890.12"),
        incomes=[Income(Decimal("1.01"), expected_date=later)],
    )
    forecast = forecast_balance(profile, today, later)
    purchase = PurchaseRequest(Decimal("123456789012345678901234567891.11"))
    with localcontext() as context:
        context.prec = 2
        plan = plan_payments(
            purchase,
            forecast,
            Decimal("0.01"),
            Decimal("123456789012345678901234567890.11"),
        )
    assert plan is not None
    assert [payment.amount for payment in plan.payments] == [
        Decimal("123456789012345678901234567890.11"),
        Decimal(1),
    ]
    assert plan.total_amount == purchase.amount


def test_zero_purchase_skips_zero_payments():
    today = date(2026, 9, 12)
    forecast = forecast_balance(
        FinancialProfile(account_balance=Decimal(20)), today, today
    )
    plan = plan_payments(PurchaseRequest(Decimal(0)), forecast, Decimal(20), Decimal(0))
    assert plan is not None
    assert plan.payments == []
    assert plan.total_amount == Decimal(0)


@pytest.mark.parametrize("field", ["required_minimum_balance", "amount_safe_to_pay"])
@pytest.mark.parametrize("invalid", [None, "1", Decimal(-1), Decimal("NaN")])
def test_invalid_money_has_context(field, invalid):
    today = date(2026, 9, 12)
    forecast = forecast_balance(
        FinancialProfile(account_balance=Decimal(100)), today, today
    )
    values = {"required_minimum_balance": Decimal(0), "amount_safe_to_pay": Decimal(50)}
    values[field] = invalid
    with pytest.raises((ValueError, TypeError), match=field):
        plan_payments(PurchaseRequest(Decimal(50)), forecast, **values)
