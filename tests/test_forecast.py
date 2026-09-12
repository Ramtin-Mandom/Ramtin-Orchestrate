"""Dated deterministic timelines without decisions or inferred events."""

from datetime import date, datetime, timezone
from decimal import Decimal, localcontext

import pytest
from extraction import normalize_request
from finance import ForecastEntry, forecast_balance
from loaders import LoadedRequest
from models import (
    FinancialProfile,
    Income,
    PendingPayment,
    RecurringExpense,
    Transaction,
)


def test_one_income_from_normalized_profile():
    normalized = normalize_request(
        LoadedRequest(
            Decimal(1),
            source_fields={
                "account_balance": "100",
                "currency": "CAD",
                "incomes": '[{"amount": "25.01", "source": "work", "expected_date": "2026-09-15"}]',
            },
        )
    )
    result = forecast_balance(normalized.profile, date(2026, 9, 12), date(2026, 9, 30))
    assert result.currency == "CAD"
    assert result.as_of_date == date(2026, 9, 12)
    assert result.end_date == date(2026, 9, 30)
    assert result.entries == (
        ForecastEntry(
            date(2026, 9, 12),
            "starting_balance",
            "account_balance",
            Decimal(0),
            Decimal(100),
        ),
        ForecastEntry(
            date(2026, 9, 15), "income", "work", Decimal("25.01"), Decimal("125.01"), 0
        ),
    )


def test_one_expense():
    profile = FinancialProfile(
        account_balance=Decimal(10),
        recurring_expenses=[
            RecurringExpense(Decimal(15), "bill", next_due_date=date(2026, 9, 15))
        ],
    )
    result = forecast_balance(profile, date(2026, 9, 12), date(2026, 9, 30))
    assert result.entries[-1] == ForecastEntry(
        date(2026, 9, 15), "recurring_expense", "bill", Decimal(-15), Decimal(-5), 0
    )


def test_multiple_dates_and_inclusive_boundaries():
    start, end = date(2026, 9, 12), date(2026, 9, 30)
    profile = FinancialProfile(
        account_balance=Decimal(100),
        incomes=[
            Income(Decimal(20), "later", expected_date=end),
            Income(Decimal(30), "first", expected_date=start),
        ],
        essential_expenses=[
            RecurringExpense(Decimal(5), "food", next_due_date=date(2026, 9, 20))
        ],
        pending_payments=[PendingPayment(Decimal(10), date(2026, 9, 15), "bill")],
    )
    result = forecast_balance(profile, start, end)
    assert [entry.date for entry in result.entries] == [
        start,
        start,
        date(2026, 9, 15),
        date(2026, 9, 20),
        end,
    ]
    assert [entry.balance for entry in result.entries] == [
        Decimal(100),
        Decimal(130),
        Decimal(120),
        Decimal(115),
        Decimal(135),
    ]
    assert profile.account_balance == Decimal(100)
    assert profile.incomes[0].source == "later"


def test_same_day_outflows_type_and_original_position():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(10),
        incomes=[Income(Decimal(50), "work", expected_date=day)],
        pending_payments=[
            PendingPayment(Decimal(4), day, "z"),
            PendingPayment(Decimal(3), day, "a"),
        ],
        recurring_expenses=[RecurringExpense(Decimal(2), "bill", next_due_date=day)],
        essential_expenses=[RecurringExpense(Decimal(1), "food", next_due_date=day)],
    )
    result = forecast_balance(profile, day, day)
    assert [entry.event_type for entry in result.entries] == [
        "starting_balance",
        "essential_expense",
        "pending_payment",
        "pending_payment",
        "recurring_expense",
        "income",
    ]
    assert [entry.source for entry in result.entries[2:4]] == ["z", "a"]
    assert [entry.balance for entry in result.entries] == [
        Decimal(10),
        Decimal(9),
        Decimal(5),
        Decimal(2),
        Decimal(0),
        Decimal(50),
    ]
    assert forecast_balance(profile, day, day) == result


def test_no_events_and_ignored_information():
    start, end = date(2026, 9, 12), date(2026, 9, 30)
    profile = FinancialProfile(
        account_balance=Decimal(-10),
        savings_balance=Decimal(100),
        incomes=[
            Income(Decimal(10)),
            Income(Decimal(10), expected_date=date(2026, 10, 1)),
        ],
        pending_payments=[
            PendingPayment(Decimal(10)),
            PendingPayment(Decimal(10), date(2026, 9, 11)),
        ],
        recurring_expenses=[RecurringExpense(Decimal(10), frequency="weekly")],
        essential_expenses=[RecurringExpense(Decimal(10))],
        flexible_expenses=[RecurringExpense(Decimal(10), next_due_date=start)],
        transactions=[Transaction(Decimal(100), start)],
    )
    result = forecast_balance(profile, start, end)
    assert result.entries == (
        ForecastEntry(
            start, "starting_balance", "account_balance", Decimal(0), Decimal(-10)
        ),
    )
    assert (
        len(
            forecast_balance(
                FinancialProfile(account_balance=Decimal(0)), start, end
            ).entries
        )
        == 1
    )


@pytest.mark.parametrize(
    "frequency, expected",
    [
        (
            "daily",
            [
                date(2026, 9, 12),
                date(2026, 9, 13),
                date(2026, 9, 14),
                date(2026, 9, 15),
            ],
        ),
        (" Weekly ", [date(2026, 9, 15)]),
        (None, []),
        ("unsupported", []),
    ],
)
def test_daily_weekly_and_unknown_schedules(frequency, expected):
    profile = FinancialProfile(
        account_balance=Decimal(100),
        recurring_expenses=[
            RecurringExpense(
                Decimal(1), frequency=frequency, next_due_date=date(2026, 9, 1)
            )
        ],
    )
    result = forecast_balance(profile, date(2026, 9, 12), date(2026, 9, 15))
    assert [entry.date for entry in result.entries[1:]] == expected


def test_calendar_recurrence_retains_anchor_day():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        recurring_expenses=[
            RecurringExpense(
                Decimal(10), frequency="monthly", next_due_date=date(2024, 1, 31)
            )
        ],
    )
    result = forecast_balance(profile, date(2024, 2, 1), date(2024, 4, 30))
    assert [entry.date for entry in result.entries[1:]] == [
        date(2024, 2, 29),
        date(2024, 3, 31),
        date(2024, 4, 30),
    ]
    assert result.entries[-1].balance == Decimal(70)


def test_yearly_essential_expense_leap_day():
    profile = FinancialProfile(
        account_balance=Decimal(10),
        essential_expenses=[
            RecurringExpense(
                Decimal(1), frequency="yearly", next_due_date=date(2024, 2, 29)
            )
        ],
    )
    result = forecast_balance(profile, date(2025, 1, 1), date(2028, 2, 29))
    assert [entry.date for entry in result.entries[1:]] == [
        date(2025, 2, 28),
        date(2026, 2, 28),
        date(2027, 2, 28),
        date(2028, 2, 29),
    ]


def test_unknown_schedule_keeps_known_due_date_and_income_occurs_once():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal(0),
        incomes=[Income(Decimal(10), frequency="daily", expected_date=day)],
        recurring_expenses=[
            RecurringExpense(Decimal(1), frequency="unknown", next_due_date=day)
        ],
    )
    assert [
        entry.event_type
        for entry in forecast_balance(profile, day, date(2026, 9, 30)).entries
    ] == ["starting_balance", "recurring_expense", "income"]


def test_decimal_arithmetic_is_exact_under_low_precision():
    day = date(2026, 9, 12)
    profile = FinancialProfile(
        account_balance=Decimal("123456789012345678901234567890.12"),
        pending_payments=[PendingPayment(Decimal("0.01"), day)],
    )
    with localcontext() as context:
        context.prec = 2
        result = forecast_balance(profile, day, day)
    assert result.entries[-1].balance == Decimal("123456789012345678901234567890.11")


def test_date_max_boundary_does_not_overflow():
    profile = FinancialProfile(
        account_balance=Decimal(0),
        recurring_expenses=[
            RecurringExpense(Decimal(1), frequency="daily", next_due_date=date.max),
            RecurringExpense(Decimal(1), frequency="monthly", next_due_date=date.max),
        ],
    )
    assert forecast_balance(profile, date.max, date.max).entries[-1].balance == Decimal(
        -2
    )


def test_missing_balance_and_reversed_period_fail():
    day = date(2026, 9, 12)
    with pytest.raises(ValueError, match="account_balance is required"):
        forecast_balance(FinancialProfile(), day, day)
    with pytest.raises(ValueError, match="end_date"):
        forecast_balance(
            FinancialProfile(account_balance=Decimal(0)), day, date(2026, 9, 11)
        )


@pytest.mark.parametrize(
    "invalid", ["2026-09-12", None, datetime(2026, 9, 12, tzinfo=timezone.utc)]
)
def test_invalid_boundary_date(invalid):
    with pytest.raises(TypeError, match="as_of_date"):
        forecast_balance(
            FinancialProfile(account_balance=Decimal(0)), invalid, date(2026, 9, 12)
        )
