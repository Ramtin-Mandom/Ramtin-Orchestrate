"""Safe amounts depend on the full supplied forecast and explicit reserves."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest
from extraction import normalize_request
from finance import (
    BalanceForecast,
    ForecastEntry,
    calculate_amount_safe_to_pay,
    forecast_balance,
)
from loaders import LoadedRequest
from models import FinancialProfile, Income, PendingPayment, RecurringExpense


def test_affordable_amount_is_not_capped_at_purchase_price_and_inputs_are_pure():
    normalized = normalize_request(
        LoadedRequest(
            Decimal(10),
            source_fields={
                "account_balance": "1000",
                "minimum_balance": "100",
                "pending_payments": '[{"amount": "200", "due_date": "2026-09-20"}]',
            },
        )
    )
    forecast = forecast_balance(
        normalized.profile, date(2026, 9, 12), date(2026, 9, 30)
    )
    original = deepcopy((normalized, forecast))
    result = calculate_amount_safe_to_pay(normalized.profile, forecast)
    assert result == Decimal(700)
    assert isinstance(result, Decimal)
    assert result > normalized.purchase.amount
    assert (normalized, forecast) == original


@pytest.mark.parametrize(
    "starting, payment, reserve, expected",
    [
        (100, 50, 50, 0),
        (100, 150, 0, 0),
        (100, 80, 50, 0),
        (1000, 800, 100, 100),
    ],
)
def test_equal_insufficient_negative_and_large_expense(
    starting, payment, reserve, expected
):
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(starting),
        minimum_balance=Decimal(reserve),
        pending_payments=[PendingPayment(Decimal(payment), day)],
    )
    assert calculate_amount_safe_to_pay(
        profile, forecast_balance(profile, day, day)
    ) == Decimal(expected)


def test_confirmed_future_income_covers_later_obligation_but_not_starting_balance():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(20),
        incomes=[Income(Decimal(100), "work", expected_date=date(2026, 9, 15))],
        pending_payments=[PendingPayment(Decimal(150), date(2026, 9, 20))],
    )
    forecast = forecast_balance(profile, date(2026, 9, 12), date(2026, 9, 30))
    assert calculate_amount_safe_to_pay(profile, forecast) == Decimal(30)
    without_income = replace(profile, incomes=[])
    assert calculate_amount_safe_to_pay(
        without_income,
        forecast_balance(without_income, date(2026, 9, 12), date(2026, 9, 30)),
    ) == Decimal(0)
    only_income = replace(profile, pending_payments=[])
    assert calculate_amount_safe_to_pay(
        only_income, forecast_balance(only_income, date(2026, 9, 12), date(2026, 9, 30))
    ) == Decimal(80)


def test_intermediate_same_day_low_is_used_even_after_recovery():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(10),
        pending_payments=[PendingPayment(Decimal(80), day)],
        incomes=[Income(Decimal(200), expected_date=day)],
    )
    forecast = forecast_balance(profile, day, day)
    assert forecast.entries[-1].balance == Decimal(220)
    assert calculate_amount_safe_to_pay(profile, forecast) == Decimal(10)


@pytest.mark.parametrize(
    "minimum, preferred, expected",
    [
        (None, 50, 150),
        (50, None, 150),
        (50, 80, 120),
        (80, 50, 120),
        (0, None, 200),
    ],
)
def test_starting_only_and_reserve_selection(minimum, preferred, expected):
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(200),
        minimum_balance=None if minimum is None else Decimal(minimum),
        preferred_balance=None if preferred is None else Decimal(preferred),
    )
    assert calculate_amount_safe_to_pay(
        profile, forecast_balance(profile, day, day)
    ) == Decimal(expected)


def test_unconfirmed_income_and_flexible_changes_are_not_counted():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        minimum_balance=Decimal(20),
        incomes=[Income(Decimal(1000))],
        flexible_expenses=[RecurringExpense(Decimal(50), next_due_date=day)],
    )
    forecast = forecast_balance(profile, day, day)
    assert calculate_amount_safe_to_pay(profile, forecast) == Decimal(80)


def test_missing_reserve_has_no_invented_default():
    day = date(2026, 9, 12)
    profile = FinancialProfile(account_balance=Decimal(100))
    with pytest.raises(
        ValueError, match="minimum_balance or preferred_balance is required"
    ):
        calculate_amount_safe_to_pay(profile, forecast_balance(profile, day, day))


def test_exact_subtraction_under_low_precision():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal("123456789012345678901234567890.12"),
        preferred_balance=Decimal("0.01"),
    )
    forecast = forecast_balance(profile, day, day)
    with localcontext() as context:
        context.prec = 2
        result = calculate_amount_safe_to_pay(profile, forecast)
    assert result == Decimal("123456789012345678901234567890.11")


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), None, "100", 100.0]
)
def test_unusable_forecast_balances_fail_with_context(value):
    day = date(2026, 9, 12)
    forecast = BalanceForecast(
        day,
        day,
        None,
        (ForecastEntry(day, "starting_balance", "account_balance", Decimal(0), value),),
    )
    with pytest.raises((ValueError, TypeError), match=r"forecast.entries\[0\].balance"):
        calculate_amount_safe_to_pay(
            FinancialProfile(minimum_balance=Decimal(0)), forecast
        )


def test_empty_forecast_fails_clearly():
    day = date(2026, 9, 12)
    with pytest.raises(ValueError, match="starting_balance"):
        calculate_amount_safe_to_pay(
            FinancialProfile(minimum_balance=Decimal(0)),
            BalanceForecast(day, day, None, ()),
        )
