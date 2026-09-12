"""Find a full-payment date using only the existing forecast timeline."""

from datetime import date
from decimal import Decimal, localcontext
from typing import Optional

from models.money import validate_money

from .forecast import BalanceForecast


def find_earliest_full_payment_date(
    forecast: BalanceForecast,
    requested_amount: Decimal,
    required_minimum_balance: Decimal,
) -> Optional[date]:
    """Return the first safe candidate date, or None within this horizon.

    Candidates are today (as_of_date), then event dates chronologically. On
    each candidate, payment occurs after all its known events. Safety includes
    that closing balance and every later event balance, preserving later
    same-date intermediate dips. Days without events cannot improve safety.
    No events are generated or reordered and inputs remain unchanged.
    """
    for name, amount in (
        ("requested_amount", requested_amount),
        ("required_minimum_balance", required_minimum_balance),
    ):
        if amount is None:
            raise TypeError(f"{name} must be a Decimal")
        validate_money(name, amount)
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
            raise ValueError(f"forecast.entries[{index}].balance is required")
        validate_money(
            f"forecast.entries[{index}].balance", entry.balance, allow_negative=True
        )
        previous_date = entry.date

    # Comparing with purchase + reserve is equivalent to subtracting the
    # purchase from each remaining minimum; retain all Decimal digits.
    exponent = min(
        requested_amount.as_tuple().exponent,
        required_minimum_balance.as_tuple().exponent,
    )
    with localcontext() as context:
        context.prec = (
            max(requested_amount.adjusted(), required_minimum_balance.adjusted())
            - exponent
            + 2
        )
        threshold = requested_amount + required_minimum_balance
    remaining_minima = [entry.balance for entry in entries]
    for index in range(len(entries) - 2, -1, -1):
        remaining_minima[index] = min(
            remaining_minima[index], remaining_minima[index + 1]
        )
    for index, entry in enumerate(entries):
        if index + 1 < len(entries) and entries[index + 1].date == entry.date:
            continue
        if remaining_minima[index] >= threshold:
            return entry.date
    return None
