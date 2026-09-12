"""Pure safe-spending calculation over an existing balance forecast."""

from decimal import Decimal, localcontext

from models import FinancialProfile
from models.money import validate_money

from .forecast import BalanceForecast


def calculate_amount_safe_to_pay(
    profile: FinancialProfile, forecast: BalanceForecast
) -> Decimal:
    """Return max(0, lowest forecast balance minus the required reserve).

    At least one of minimum_balance/preferred_balance must be explicit. If
    both are present, the higher value preserves both constraints. Every
    forecast entry counts, including the starting balance and intermediate
    same-date balances. No events are generated and no purchase-price cap
    is applied. Inputs remain unchanged.
    """
    reserves = []
    for name in ("minimum_balance", "preferred_balance"):
        value = getattr(profile, name)
        if value is not None:
            validate_money(name, value)
            reserves.append(value)
    if not reserves:
        raise ValueError(
            "minimum_balance or preferred_balance is required; no default is defined"
        )
    if not forecast.entries or forecast.entries[0].event_type != "starting_balance":
        raise ValueError("forecast must include a starting_balance entry first")
    for index, entry in enumerate(forecast.entries):
        name = f"forecast.entries[{index}].balance"
        if entry.balance is None:
            raise ValueError(f"{name} is required")
        validate_money(name, entry.balance, allow_negative=True)
    lowest_balance = min(entry.balance for entry in forecast.entries)
    required_reserve = max(reserves)
    if lowest_balance <= required_reserve:
        return Decimal(0)
    # Align the operands' decimal places and retain all digits, even if the
    # caller uses a smaller Decimal precision. Do not depend on forecast internals.
    exponent = min(
        lowest_balance.as_tuple().exponent, required_reserve.as_tuple().exponent
    )
    with localcontext() as context:
        context.prec = (
            max(lowest_balance.adjusted(), required_reserve.adjusted()) - exponent + 2
        )
        return lowest_balance - required_reserve
