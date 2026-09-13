"""Write ChallengeDecision rows using the exact problem_statement.md schema."""

import csv
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Union

from decision.challenge_decision import ChallengeDecision
from loaders.dataset_schema import HEADERS

OUTPUT_COLUMNS = HEADERS["output"]


def _money(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _plan(payments) -> str:
    if not payments:
        return "none"
    ordered = sorted(payments, key=lambda item: item[0])
    return "|".join(f"{when.isoformat()}:{_money(amount)}" for when, amount in ordered)


def _changes(tokens) -> str:
    return "|".join(tokens) if tokens else "none"


def challenge_row(decision: ChallengeDecision) -> tuple:
    earliest = decision.earliest_date_for_full_payment
    return (
        decision.request_id, _money(decision.amount_safe_to_pay), decision.affordability_status,
        decision.recommended_payment_method, _plan(decision.payment_plan),
        "" if earliest is None else earliest.isoformat(),
        _changes(decision.spending_changes), decision.decision_explanation,
    )


def write_challenge_csv(
    decisions: Iterable[ChallengeDecision], output_path: Union[str, Path],
) -> None:
    """Write ordered ChallengeDecision rows as the exact challenge output.csv."""
    rows = [challenge_row(decision) for decision in decisions]
    with Path(output_path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)
