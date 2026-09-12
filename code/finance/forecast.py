"""Forecast only explicit dated income, payments, and expense schedules."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Iterator, Optional, Tuple

from models import FinancialProfile, RecurringExpense
from models.money import validate_money


@dataclass(frozen=True)
class ForecastEntry:
    date: date
    event_type: str
    source: str
    amount: Decimal
    balance: Decimal
    source_index: Optional[int] = None


@dataclass(frozen=True)
class BalanceForecast:
    as_of_date: date
    end_date: date
    currency: Optional[str]
    entries: Tuple[ForecastEntry, ...]


def _validate_date(value: date, name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a date")


def _expense_dates(
    expense: RecurringExpense, start: date, end: date, name: str
) -> Iterator[date]:
    anchor = expense.next_due_date
    if anchor is None:
        return
    _validate_date(anchor, f"{name}.next_due_date")
    frequency = (expense.frequency or "").strip().lower()
    if frequency in {"daily", "weekly"}:
        step = 1 if frequency == "daily" else 7
        offset = max(0, (start - anchor).days)
        offset = ((offset + step - 1) // step) * step
        while offset <= (end - anchor).days:
            yield anchor + timedelta(days=offset)
            offset += step
    elif frequency in {"monthly", "yearly"}:
        step = 1 if frequency == "monthly" else 12
        anchor_month = (anchor.year - 1) * 12 + anchor.month - 1
        offset = max(0, (start.year - anchor.year) * 12 + start.month - anchor.month)
        offset = (offset // step) * step
        end_month = (end.year - 1) * 12 + end.month - 1
        while anchor_month + offset <= end_month:
            year, month = divmod(anchor_month + offset, 12)
            year += 1
            month += 1
            occurrence = date(year, month, min(anchor.day, monthrange(year, month)[1]))
            if start <= occurrence <= end:
                yield occurrence
            offset += step
    elif start <= anchor <= end:
        # The explicit next due date is known even if recurrence is not.
        yield anchor


def _exact_add(balance: Decimal, amount: Decimal) -> Decimal:
    """Avoid rounding long Decimal inputs under the ambient precision."""
    exponent = min(balance.as_tuple().exponent, amount.as_tuple().exponent)
    with localcontext() as context:
        context.prec = max(balance.adjusted(), amount.adjusted()) - exponent + 2
        return balance + amount


def forecast_balance(
    profile: FinancialProfile, as_of_date: date, end_date: date
) -> BalanceForecast:
    """Build an inclusive timeline, starting before events on as_of_date.

    Dated Income entries are caller-confirmed and occur once; income frequency
    alone does not confirm further income. Expense schedules support daily,
    weekly, monthly and yearly. Calendar recurrence retains the original day,
    clamping to the last day of shorter months. Unknown schedules never expand.
    Same-date events sort by outflows first, then event type, then source index.
    Historical transactions, savings, flexible expenses and purchases are not
    future events. A known account_balance is required.
    """
    _validate_date(as_of_date, "as_of_date")
    _validate_date(end_date, "end_date")
    if end_date < as_of_date:
        raise ValueError("end_date must be on or after as_of_date")
    balance = profile.account_balance
    if balance is None:
        raise ValueError("account_balance is required for forecasting")
    validate_money("account_balance", balance, allow_negative=True)
    events = []
    for event_type, records, date_field, source_field in (
        ("income", profile.incomes, "expected_date", "source"),
        ("pending_payment", profile.pending_payments, "due_date", "description"),
    ):
        for index, record in enumerate(records):
            event_date = getattr(record, date_field)
            if event_date is None:
                continue
            _validate_date(event_date, f"{event_type}[{index}].{date_field}")
            if as_of_date <= event_date <= end_date:
                validate_money(f"{event_type}[{index}].amount", record.amount)
                amount = (
                    record.amount
                    if event_type == "income"
                    else record.amount.copy_negate()
                )
                events.append(
                    (
                        event_date,
                        event_type,
                        index,
                        getattr(record, source_field),
                        amount,
                    )
                )
    for event_type, expenses in (
        ("recurring_expense", profile.recurring_expenses),
        ("essential_expense", profile.essential_expenses),
    ):
        for index, expense in enumerate(expenses):
            for event_date in _expense_dates(
                expense, as_of_date, end_date, f"{event_type}[{index}]"
            ):
                validate_money(f"{event_type}[{index}].amount", expense.amount)
                events.append(
                    (
                        event_date,
                        event_type,
                        index,
                        expense.description,
                        expense.amount.copy_negate(),
                    )
                )
    events.sort(key=lambda event: (event[0], event[4] >= 0, event[1], event[2]))
    entries = [
        ForecastEntry(
            as_of_date, "starting_balance", "account_balance", Decimal(0), balance
        )
    ]
    for event_date, event_type, index, source, amount in events:
        balance = _exact_add(balance, amount)
        entries.append(
            ForecastEntry(event_date, event_type, source, amount, balance, index)
        )
    return BalanceForecast(as_of_date, end_date, profile.currency, tuple(entries))
