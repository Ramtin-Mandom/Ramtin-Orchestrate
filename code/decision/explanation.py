"""Deterministic wording for decisions already computed by the finance modules."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from typing import Optional

from finance import BalanceForecast
from models import DecisionResult, FinancialProfile, PurchaseRequest
from models.decision_values import DO_NOT_PROCEED, INSTALLMENTS, PAY_IN_FULL, WAIT
from models.spending import SpendingChange

MAX_SENTENCES = 3


def _money(amount: Decimal, currency: Optional[str]) -> str:
    """Keep Decimal precision and explicit currency codes; never assume dollars."""
    value = format(amount, "f")
    return f"{currency} {value}" if currency else value


def _reserve(profile, currency):
    if profile is None:
        return ""
    reserves = [value for value in (profile.minimum_balance, profile.preferred_balance)
                if value is not None]
    if not reserves:
        return ""
    return f"The required reserve of {_money(max(reserves), currency)} remains protected."


def _changes(decision, currency):
    details = []
    for change in decision.spending_changes_needed:
        if isinstance(change, SpendingChange):
            name = change.expense_name.strip() or "flexible spending"
            details.append(f"{name} by {_money(change.reduction_amount, currency)}"
                           f" on {change.affected_date.isoformat()}")
        elif isinstance(change, str) and change.strip():
            details.append(change.strip())
    return "Required spending reductions: " + "; ".join(details) + "." if details else ""


def _plan(decision, currency):
    plan = decision.payment_plan
    if plan is None or not plan.payments:
        return "Use the recommended payment plan."
    # Summarize endpoints rather than expanding a potentially long schedule.
    payments = plan.payments
    endpoints = payments if len(payments) == 1 else [payments[0], payments[-1]]
    details = []
    for payment in endpoints:
        text = _money(payment.amount, currency)
        if payment.due_date is not None:
            text += f" on {payment.due_date.isoformat()}"
        details.append(text)
    count = len(payments)
    total = f" totaling {_money(plan.total_amount, currency)}" if plan.total_amount is not None else ""
    label = "" if count == 1 else "first and last: "
    return f"Use the {count}-payment plan{total} ({label}{'; '.join(details)})."


def _recommendation(decision, purchase, profile, forecast, currency):
    method = decision.recommended_payment_method
    if method == DO_NOT_PROCEED:
        horizon = f" through {forecast.end_date.isoformat()}" if forecast is not None else ""
        return [("Do not proceed: no safe full payment, payment plan, or future full-payment"
                 f" date is available within the evaluated horizon{horizon}.")]
    if method == WAIT:
        text = "Wait to pay in full"
        if decision.earliest_date_for_full_payment is not None:
            text += f"; the earliest safe date is {decision.earliest_date_for_full_payment.isoformat()}"
        text += "."
        if decision.amount_safe_to_pay is not None:
            text += f" Today's safe amount is {_money(decision.amount_safe_to_pay, currency)}."
        return [text]
    if method == INSTALLMENTS:
        return [_plan(decision, currency), _changes(decision, currency), _reserve(profile, currency)]
    if method == PAY_IN_FULL:
        amount = f" of {_money(purchase.amount, currency)}" if purchase is not None else ""
        text = f"Pay the purchase{amount} in full"
        if decision.spending_changes_needed:
            text += " after the required spending reductions."
        else:
            text += " now."
            if (purchase is not None and decision.amount_safe_to_pay is not None
                    and purchase.amount <= decision.amount_safe_to_pay):
                text = (f"The purchase{amount} is within today's safe amount of "
                        f"{_money(decision.amount_safe_to_pay, currency)}; pay in full now.")
        return [text, _changes(decision, currency), _reserve(profile, currency)]
    return ["No recommendation is available."]


def _context(decision, forecast, currency):
    """Mention dated forecast facts without claiming they caused the recommendation."""
    if forecast is None or decision.recommended_payment_method not in {WAIT, INSTALLMENTS, PAY_IN_FULL}:
        return ""
    focus = decision.earliest_date_for_full_payment
    details = []
    entries = sorted(forecast.entries, key=lambda entry: entry.date)
    for entry in entries:
        if (entry.event_type == "income" and entry.amount > 0 and focus is not None
                and entry.date == focus and forecast.as_of_date <= entry.date <= forecast.end_date):
            details.append(f"confirmed income of {_money(entry.amount, currency)} on {entry.date.isoformat()}")
            break
    for entry in entries:
        if (entry.event_type in {"pending_payment", "recurring_expense", "essential_expense"}
                and entry.amount < 0 and forecast.as_of_date <= entry.date <= forecast.end_date
                and (focus is None or entry.date <= focus)):
            details.append(f"an obligation of {_money(entry.amount.copy_abs(), currency)} on {entry.date.isoformat()}")
            break
    return "The forecast includes " + " and ".join(details) + "." if details else ""


def build_decision_explanation(
    decision: DecisionResult, purchase_request: Optional[PurchaseRequest] = None,
    profile: Optional[FinancialProfile] = None, forecast: Optional[BalanceForecast] = None,
) -> str:
    """Describe supplied results only; never call evaluators or revise a decision.

    Missing context is omitted. Forecast income entries are caller-confirmed;
    raw profile income is not assumed relevant. At most three template sentences
    are produced (legacy free-text spending changes are retained verbatim).
    """
    currency = (profile.currency if profile is not None else None) or (
        forecast.currency if forecast is not None else None
    )
    parts = [part for part in _recommendation(
        decision, purchase_request, profile, forecast, currency,
    ) if part]
    # WAIT already supplies two sentences when a safe amount is available.
    sentence_count = len(parts) + int(decision.recommended_payment_method == WAIT
                                    and decision.amount_safe_to_pay is not None)
    if sentence_count < MAX_SENTENCES:
        context = _context(decision, forecast, currency)
        if context:
            parts.append(context)
    return " ".join(parts)


def attach_decision_explanation(
    decision: DecisionResult, purchase_request: Optional[PurchaseRequest] = None,
    profile: Optional[FinancialProfile] = None, forecast: Optional[BalanceForecast] = None,
) -> DecisionResult:
    """Return an independent copy; preserve all fields except explanation text."""
    return replace(deepcopy(decision), decision_explanation=build_decision_explanation(
        decision, purchase_request, profile, forecast,
    ))
