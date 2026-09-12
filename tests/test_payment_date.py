"""Remaining-horizon safety checks without generating purchase strategies."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest
from finance import (
    BalanceForecast,
    ForecastEntry,
    find_earliest_full_payment_date,
    forecast_balance,
)
from models import FinancialProfile, Income, PendingPayment


def test_affordable_today_and_inputs_unchanged():
    today = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(200),
        pending_payments=[PendingPayment(Decimal(50), date(2026, 9, 20))],
    )
    forecast = forecast_balance(profile, today, date(2026, 9, 30))
    original = deepcopy(forecast)
    assert find_earliest_full_payment_date(forecast, Decimal(100), Decimal(50)) == today
    assert forecast == original


def test_only_after_confirmed_future_income():
    income_date = date(2026, 9, 15)
    profile = FinancialProfile(
        account_balance=Decimal(20),
        incomes=[
            Income(Decimal(100), "work", expected_date=income_date),
            Income(Decimal(1000)),
        ],
    )
    forecast = forecast_balance(profile, date(2026, 9, 12), date(2026, 9, 30))
    assert (
        find_earliest_full_payment_date(forecast, Decimal(100), Decimal(20))
        == income_date
    )


def test_temporary_sufficiency_followed_by_obligation_requires_later_income():
    profile = FinancialProfile(
        account_balance=Decimal(20),
        incomes=[
            Income(Decimal(100), expected_date=date(2026, 9, 15)),
            Income(Decimal(20), expected_date=date(2026, 9, 16)),
            Income(Decimal(100), expected_date=date(2026, 9, 25)),
        ],
        pending_payments=[PendingPayment(Decimal(80), date(2026, 9, 20))],
    )
    forecast = forecast_balance(profile, date(2026, 9, 12), date(2026, 9, 30))
    assert find_earliest_full_payment_date(forecast, Decimal(100), Decimal(20)) == date(
        2026, 9, 25
    )
    without_recovery = replace(profile, incomes=profile.incomes[:2])
    assert (
        find_earliest_full_payment_date(
            forecast_balance(without_recovery, date(2026, 9, 12), date(2026, 9, 30)),
            Decimal(100),
            Decimal(20),
        )
        is None
    )


@pytest.mark.parametrize("purchase, reserve", [(101, 0), (80, 30)])
def test_never_safe_or_reserve_prevents_purchase(purchase, reserve):
    today = date(2026, 9, 12)
    profile = FinancialProfile(account_balance=Decimal(100))
    forecast = forecast_balance(profile, today, date(2026, 9, 30))
    assert (
        find_earliest_full_payment_date(forecast, Decimal(purchase), Decimal(reserve))
        is None
    )


def test_starting_only_exact_threshold_returns_today():
    today = date(2026, 9, 12)
    forecast = forecast_balance(
        FinancialProfile(account_balance=Decimal(100)), today, today
    )
    assert find_earliest_full_payment_date(forecast, Decimal(80), Decimal(20)) == today


@pytest.mark.parametrize("event_date", [date(2026, 9, 12), date(2026, 9, 15)])
def test_candidate_uses_balance_after_all_events_on_its_date(event_date):
    today = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(10),
        pending_payments=[PendingPayment(Decimal(5), event_date)],
        incomes=[Income(Decimal(100), expected_date=event_date)],
    )
    forecast = forecast_balance(profile, today, date(2026, 9, 30))
    assert (
        find_earliest_full_payment_date(forecast, Decimal(80), Decimal(20))
        == event_date
    )


def test_later_same_day_intermediate_dip_still_blocks_earlier_candidate():
    today, later = date(2026, 9, 12), date(2026, 9, 20)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        pending_payments=[PendingPayment(Decimal(80), later)],
        incomes=[Income(Decimal(100), expected_date=later)],
    )
    forecast = forecast_balance(profile, today, date(2026, 9, 30))
    assert find_earliest_full_payment_date(forecast, Decimal(50), Decimal(10)) == later


def test_income_on_end_date_can_make_purchase_safe():
    today, end = date(2026, 9, 12), date(2026, 9, 30)
    profile = FinancialProfile(
        account_balance=Decimal(0), incomes=[Income(Decimal(50), expected_date=end)]
    )
    assert (
        find_earliest_full_payment_date(
            forecast_balance(profile, today, end), Decimal(50), Decimal(0)
        )
        == end
    )


def test_exact_money_threshold_under_low_precision():
    today = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal("123456789012345678901234567890.12")
    )
    forecast = forecast_balance(profile, today, today)
    with localcontext() as context:
        context.prec = 2
        assert (
            find_earliest_full_payment_date(
                forecast, Decimal("123456789012345678901234567890.11"), Decimal("0.02")
            )
            is None
        )
        assert (
            find_earliest_full_payment_date(
                forecast, Decimal("123456789012345678901234567890.11"), Decimal("0.01")
            )
            == today
        )


@pytest.mark.parametrize("field", ["requested_amount", "required_minimum_balance"])
@pytest.mark.parametrize(
    "invalid", [None, "1", 1.0, Decimal(-1), Decimal("NaN"), Decimal("Infinity")]
)
def test_invalid_money_has_field_context(field, invalid):
    today = date(2026, 9, 12)
    forecast = forecast_balance(
        FinancialProfile(account_balance=Decimal(100)), today, today
    )
    values = {"requested_amount": Decimal(1), "required_minimum_balance": Decimal(0)}
    values[field] = invalid
    with pytest.raises((ValueError, TypeError), match=field):
        find_earliest_full_payment_date(forecast, **values)


def test_empty_forecast_is_invalid():
    today = date(2026, 9, 12)
    with pytest.raises(ValueError, match="starting_balance"):
        find_earliest_full_payment_date(
            BalanceForecast(today, today, None, ()), Decimal(1), Decimal(0)
        )


def test_unsorted_forecast_is_not_reordered():
    today, end = date(2026, 9, 12), date(2026, 9, 30)
    entries = (
        ForecastEntry(
            today, "starting_balance", "account_balance", Decimal(0), Decimal(100)
        ),
        ForecastEntry(end, "income", "work", Decimal(10), Decimal(110)),
        ForecastEntry(today, "income", "work", Decimal(10), Decimal(120)),
    )
    with pytest.raises(ValueError, match="chronological"):
        find_earliest_full_payment_date(
            BalanceForecast(today, end, None, entries), Decimal(1), Decimal(0)
        )
