"""Evaluate explicit flexible reductions against an existing payment candidate."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Dict, Tuple, Union

from models import FinancialProfile, PaymentPlan, PurchaseRequest
from models.money import validate_money
from models.spending import SpendingChange

from .forecast import BalanceForecast


@dataclass(frozen=True)
class SpendingAdjustmentResult:
    feasible: bool
    spending_changes_needed: Tuple[SpendingChange, ...] = ()
    adjusted_balances: Tuple[Decimal, ...] = ()


def _required_money(name, amount):
    if amount is None:
        raise TypeError(f"{name} must be a Decimal")
    validate_money(name, amount)


def _payment_positions(candidate, forecast) -> Dict[int, Decimal]:
    if isinstance(candidate, PurchaseRequest):
        _required_money("purchase_request.amount", candidate.amount)
        return {0: candidate.amount}
    if not isinstance(candidate, PaymentPlan):
        raise TypeError("candidate must be a PurchaseRequest or PaymentPlan")
    positions = {entry.date: index for index, entry in enumerate(forecast.entries)}
    positions[forecast.as_of_date] = 0
    scheduled = {}
    total = Decimal(0)
    for index, payment in enumerate(candidate.payments):
        _required_money(f"payments[{index}].amount", payment.amount)
        if payment.due_date not in positions:
            raise ValueError(
                f"payments[{index}].due_date must be today or an existing forecast date"
            )
        position = positions[payment.due_date]
        scheduled[position] = scheduled.get(position, Decimal(0)) + payment.amount
        total += payment.amount
    if candidate.total_amount is not None:
        _required_money("payment_plan.total_amount", candidate.total_amount)
        if candidate.total_amount != total:
            raise ValueError("payment_plan.total_amount must equal its payments")
    return scheduled


def _flexible_events(profile, forecast):
    eligible = {}
    protected = {
        "essential",
        "required",
        "rent",
        "utilities",
        "required debt payments",
        "required_debt",
        "debt_payment",
    }
    for index, entry in enumerate(forecast.entries):
        if entry.event_type == "flexible_expense":
            expenses = profile.flexible_expenses
        elif entry.event_type == "recurring_expense":
            expenses = profile.recurring_expenses
        else:
            continue
        source_index = entry.source_index
        if source_index is None or not 0 <= source_index < len(expenses):
            raise ValueError(
                f"forecast.entries[{index}].source_index must identify its expense"
            )
        expense = expenses[source_index]
        category = (expense.category or "").strip().lower()
        if entry.event_type == "recurring_expense" and category not in {
            "flexible",
            "discretionary",
        }:
            continue
        if category in protected or expense.description.strip().lower() in protected:
            continue
        _required_money(f"{entry.event_type}[{source_index}].amount", expense.amount)
        if (
            entry.source != expense.description
            or entry.amount.copy_abs() != expense.amount
            or entry.amount >= 0
        ):
            raise ValueError(
                f"forecast.entries[{index}] does not match its flexible outflow"
            )
        identifier = f"{entry.event_type}:{source_index}:{index}"
        eligible[index] = (identifier, expense.description, entry.date, expense.amount)
    return eligible


def _simulate(forecast, scheduled, reductions):
    paid = Decimal(0)
    saved = Decimal(0)
    balances = []
    for index, entry in enumerate(forecast.entries):
        paid += scheduled.get(index, Decimal(0))
        saved += reductions.get(index, Decimal(0))
        balances.append(entry.balance - paid + saved)
    return balances


def _evaluate(profile, forecast, reserve, candidate):
    scheduled = _payment_positions(candidate, forecast)
    eligible = _flexible_events(profile, forecast)
    balances = _simulate(forecast, scheduled, {})
    reductions = {}
    for index, balance in enumerate(balances):
        shortfall = reserve - balance
        if shortfall <= 0:
            continue
        available = [
            position
            for position in eligible
            if position <= index
            and eligible[position][3] > reductions.get(position, Decimal(0))
        ]
        available.sort(
            key=lambda position: (
                -(eligible[position][3] - reductions.get(position, Decimal(0))),
                eligible[position][2],
                eligible[position][1],
                eligible[position][0],
            )
        )
        for position in available:
            reduction = min(
                shortfall, eligible[position][3] - reductions.get(position, Decimal(0))
            )
            reductions[position] = reductions.get(position, Decimal(0)) + reduction
            for affected in range(position, len(balances)):
                balances[affected] += reduction
            shortfall -= reduction
            if shortfall == 0:
                break
        if shortfall > 0:
            return SpendingAdjustmentResult(False)
    validated = _simulate(forecast, scheduled, reductions)
    if any(balance < reserve for balance in validated):
        return SpendingAdjustmentResult(False)
    changes = tuple(
        SpendingChange(
            *eligible[position], reduction, eligible[position][3] - reduction
        )
        for position, reduction in sorted(reductions.items())
    )
    return SpendingAdjustmentResult(True, changes, tuple(validated))


def evaluate_spending_adjustments(
    profile: FinancialProfile,
    forecast: BalanceForecast,
    required_minimum_balance: Decimal,
    candidate: Union[PurchaseRequest, PaymentPlan],
) -> SpendingAdjustmentResult:
    """Greedily reduce only matching, explicitly flexible forecast outflows.

    Today payments precede all events; future plan payments follow that date's
    final event. Reductions take effect at the original expense event, never
    earlier. Largest available amount wins, then date/name/stable identifier.
    There are no model-defined reduction limits, so each occurrence is capped
    at its original amount. Unrepresented expenses cannot produce savings.
    Infeasible results contain no proposed changes or adjusted timeline.
    """
    _required_money("required_minimum_balance", required_minimum_balance)
    if not forecast.entries or forecast.entries[0].event_type != "starting_balance":
        raise ValueError("forecast must include a starting_balance entry first")
    if forecast.entries[0].date != forecast.as_of_date:
        raise ValueError("starting_balance date must equal forecast.as_of_date")
    previous = forecast.as_of_date
    values = [required_minimum_balance]
    for index, entry in enumerate(forecast.entries):
        if not previous <= entry.date <= forecast.end_date:
            raise ValueError(
                f"forecast.entries[{index}].date must be chronological and within the horizon"
            )
        if entry.balance is None:
            raise TypeError(f"forecast.entries[{index}].balance must be a Decimal")
        if entry.amount is None:
            raise TypeError(f"forecast.entries[{index}].amount must be a Decimal")
        validate_money(
            f"forecast.entries[{index}].balance", entry.balance, allow_negative=True
        )
        validate_money(
            f"forecast.entries[{index}].amount", entry.amount, allow_negative=True
        )
        values.extend((entry.balance, entry.amount))
        previous = entry.date
    if isinstance(candidate, PurchaseRequest):
        _required_money("purchase_request.amount", candidate.amount)
        values.append(candidate.amount)
    elif isinstance(candidate, PaymentPlan):
        for index, payment in enumerate(candidate.payments):
            _required_money(f"payments[{index}].amount", payment.amount)
            values.append(payment.amount)
    else:
        raise TypeError("candidate must be a PurchaseRequest or PaymentPlan")
    with localcontext() as context:
        context.prec = (
            max(value.adjusted() for value in values)
            - min(value.as_tuple().exponent for value in values)
            + len(str(len(values)))
            + 3
        )
        return _evaluate(profile, forecast, required_minimum_balance, candidate)
