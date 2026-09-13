"""Serialize the Required output schema in problem_statement.md, without decisions."""

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Tuple, Union

from models import DecisionResult
from models.decision_values import (
    AFFORDABLE_LATER,
    AFFORDABLE_NOW,
    AFFORDABLE_WITH_PLAN,
    DO_NOT_PROCEED,
    INSTALLMENTS,
    NOT_AFFORDABLE,
    PAY_IN_FULL,
    WAIT,
)
from models.money import validate_money
from models.spending import SpendingChange

OUTPUT_COLUMNS = (
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment",
    "spending_changes_needed", "decision_explanation",
)
STATUSES = {AFFORDABLE_NOW, AFFORDABLE_WITH_PLAN, AFFORDABLE_LATER, NOT_AFFORDABLE}
METHODS = {
    PAY_IN_FULL: "full_payment", DO_NOT_PROCEED: "not_recommended",
    INSTALLMENTS: "installments", WAIT: "wait",
    "full_payment": "full_payment", "partial_payment": "partial_payment",
    "not_recommended": "not_recommended",
}
MAX_CHANGES = 3
STOP_TOKEN_FIELDS = 2
REDUCTION_TOKEN_FIELDS = 3


def _money(value):
    if not isinstance(value, Decimal):
        raise TypeError("output money must be a Decimal")
    validate_money("output money", value)
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _date(value):
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError("output dates must be date objects")
    return value.isoformat()


def _event_id(value):
    if (not isinstance(value, str) or not value.strip()
            or value != value.strip() or any(char in value for char in ":|\r\n")):
        raise ValueError("spending changes require a source event ID without delimiters")
    return value


def _change(value):
    if isinstance(value, SpendingChange):
        identifier = _event_id(value.expense_identifier)
        amount = _money(value.remaining_amount)
        return identifier, (f"stop:{identifier}" if value.remaining_amount == 0
                            else f"reduce_to:{identifier}:{amount}")
    if not isinstance(value, str):
        raise TypeError("spending changes must be structured changes or challenge tokens")
    parts = value.split(":")
    if len(parts) == STOP_TOKEN_FIELDS and parts[0] == "stop":
        identifier = _event_id(parts[1])
        return identifier, f"stop:{identifier}"
    if len(parts) == REDUCTION_TOKEN_FIELDS and parts[0] == "reduce_to":
        identifier = _event_id(parts[1])
        return identifier, f"reduce_to:{identifier}:{_money(Decimal(parts[2]))}"
    raise ValueError("free-text spending changes cannot be serialized as challenge tokens")


def _changes(values):
    if len(values) > MAX_CHANGES:
        raise ValueError("the challenge permits at most three spending changes")
    changes = [_change(value) for value in values]
    if len({identifier for identifier, _ in changes}) != len(changes):
        raise ValueError("each spending event may be changed only once")
    return "|".join(text for _, text in changes) or "none"


def _plan(plan):
    if plan is None or not plan.payments:
        return "none"
    payments = [(_date(payment.due_date), _money(payment.amount))
                for payment in plan.payments]
    payments.sort(key=lambda payment: payment[0])
    return "|".join(f"{when}:{amount}" for when, amount in payments)


def _row(request_id, decision):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("each decision requires a nonempty string request_id")
    if not isinstance(decision, DecisionResult):
        raise TypeError("each row requires a DecisionResult")
    if decision.affordability_status not in STATUSES:
        raise ValueError("a completed decision requires a challenge affordability status")
    if decision.recommended_payment_method not in METHODS:
        raise ValueError("a completed decision requires a supported payment method")
    explanation = decision.decision_explanation
    if explanation is not None and not isinstance(explanation, str):
        raise TypeError("decision_explanation must be text")
    return (
        request_id, _money(decision.amount_safe_to_pay), decision.affordability_status,
        METHODS[decision.recommended_payment_method], _plan(decision.payment_plan),
        "" if decision.earliest_date_for_full_payment is None
        else _date(decision.earliest_date_for_full_payment),
        _changes(decision.spending_changes_needed), explanation or "",
    )


def validate_decision_output(request_id: str, decision: DecisionResult) -> None:
    """Validate one completed row without writing or changing values."""
    _row(request_id, decision)


def write_decisions_csv(
    results: Iterable[Tuple[str, DecisionResult]], output_path: Union[str, Path],
) -> None:
    """Write ordered (request_id, completed DecisionResult) pairs as challenge rows.

    Validate serialization before opening the destination. No missing required
    money/status/method is fabricated. Only the optional full-payment date and
    explanation are blank; absent plans/changes are 'none'. Dates and money in
    plans must exist. SpendingChange identifiers must already be source event
    IDs, not internal forecast identifiers. No schedules or financial values
    are calculated, reconciled or capped; those belong to the decision stage.
    """
    rows = [_row(request_id, decision) for request_id, decision in results]
    with Path(output_path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)
