"""Earliest-date greedy payments over an unchanged balance forecast."""

from decimal import Decimal, localcontext
from typing import Dict, List, Optional

from models import PaymentPlan, PendingPayment, PurchaseRequest
from models.money import validate_money

from .forecast import BalanceForecast


def _remaining_minima(forecast: BalanceForecast) -> List[Decimal]:
    entries = forecast.entries
    if not entries or entries[0].event_type != "starting_balance":
        raise ValueError("forecast must include a starting_balance entry first")
    if entries[0].date != forecast.as_of_date:
        raise ValueError("starting_balance date must equal forecast.as_of_date")
    previous_date = forecast.as_of_date
    for index, entry in enumerate(entries):
        if not previous_date <= entry.date <= forecast.end_date:
            raise ValueError(
                f"forecast.entries[{index}].date must be chronological and within the horizon"
            )
        if entry.balance is None:
            raise TypeError(f"forecast.entries[{index}].balance must be a Decimal")
        validate_money(
            f"forecast.entries[{index}].balance", entry.balance, allow_negative=True
        )
        previous_date = entry.date
    minima = [entry.balance for entry in entries]
    for index in range(len(entries) - 2, -1, -1):
        minima[index] = min(minima[index], minima[index + 1])
    return minima


def _valid_plan(
    forecast: BalanceForecast,
    scheduled: Dict[int, Decimal],
    reserve: Decimal,
    requested: Decimal,
) -> bool:
    paid = Decimal(0)
    for index, entry in enumerate(forecast.entries):
        amount = scheduled.get(index, Decimal(0))
        if index in scheduled and amount <= 0:
            return False
        paid += amount
        if paid > requested or entry.balance - paid < reserve:
            return False
    return paid == requested


def _build_plan(
    purchase, forecast, reserve, safe_today, minima
) -> Optional[PaymentPlan]:
    if minima[0] < reserve:
        return None
    paid = Decimal(0)
    scheduled = {}
    payments = []
    for index, entry in enumerate(forecast.entries):
        if index != 0:
            if entry.date == forecast.as_of_date:
                continue
            if (
                index + 1 < len(forecast.entries)
                and forecast.entries[index + 1].date == entry.date
            ):
                continue
        capacity = max(Decimal(0), minima[index] - reserve - paid)
        if index == 0:
            capacity = min(capacity, safe_today)
        amount = min(purchase.amount - paid, capacity)
        if amount > 0:
            payments.append(
                PendingPayment(
                    amount,
                    entry.date,
                    purchase.description,
                    purchase.preferred_payment_method,
                )
            )
            scheduled[index] = amount
            paid += amount
        if paid == purchase.amount:
            break
    if not _valid_plan(forecast, scheduled, reserve, purchase.amount):
        return None
    return PaymentPlan(payments, paid, purchase.preferred_payment_method)


def plan_payments(
    purchase_request: PurchaseRequest,
    forecast: BalanceForecast,
    required_minimum_balance: Decimal,
    amount_safe_to_pay: Decimal,
) -> Optional[PaymentPlan]:
    """Schedule positive payments greedily, or return None if incomplete/unsafe.

    Today is before all events and is capped by the supplied safe amount and
    actual remaining-horizon capacity. Later dates are after their final event.
    Capacity is remaining minimum minus reserve minus all scheduled payments.
    Each adjusted event balance must preserve the reserve. No events, payment
    methods, fees or financial values are inferred; inputs remain unchanged.
    A zero purchase returns an empty, zero-total plan if the timeline is safe.
    """
    for name, amount in (
        ("purchase_request.amount", purchase_request.amount),
        ("required_minimum_balance", required_minimum_balance),
        ("amount_safe_to_pay", amount_safe_to_pay),
    ):
        if amount is None:
            raise TypeError(f"{name} must be a Decimal")
        validate_money(name, amount)
    minima = _remaining_minima(forecast)
    values = [
        purchase_request.amount,
        required_minimum_balance,
        amount_safe_to_pay,
        *(entry.balance for entry in forecast.entries),
    ]
    exponent = min(value.as_tuple().exponent for value in values)
    with localcontext() as context:
        context.prec = max(value.adjusted() for value in values) - exponent + 3
        return _build_plan(
            purchase_request,
            forecast,
            required_minimum_balance,
            amount_safe_to_pay,
            minima,
        )
