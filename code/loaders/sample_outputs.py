"""Validate example output syntax without using examples as evaluation labels."""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from models.validation import identifier, parse_date

from .dataset_schema import OUTPUT, DatasetValidationError, SourceRecord


def _money(text):
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid output money") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("output money must be finite and nonnegative")
    return amount


def _schedule(text):
    if text == "none":
        return ()
    payments = []
    for entry in text.split("|"):
        day, amount = entry.split(":")
        payments.append((parse_date("payment_plan", day), _money(amount)))
    if payments != sorted(payments, key=lambda p: p[0]):
        raise ValueError("example payment_plan must be chronological")
    return tuple(payments)


def load_sample_outputs(rows):
    outputs = []
    for row, provenance in rows:
        try:
            values = {k: row[k] for k in OUTPUT}
            values["amount_safe_to_pay"] = _money(row["amount_safe_to_pay"])
            if row["affordability_status"] not in {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}:
                raise ValueError("invalid sample affordability_status")
            if row["recommended_payment_method"] not in {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}:
                raise ValueError("invalid sample payment method")
            values["payment_plan"] = _schedule(row["payment_plan"])
            values["earliest_date_for_full_payment"] = (
                parse_date("earliest_date_for_full_payment", row["earliest_date_for_full_payment"])
                if row["earliest_date_for_full_payment"] else None
            )
            changes = row["spending_changes_needed"]
            if changes != "none":
                for change in changes.split("|"):
                    pieces = change.split(":")
                    if pieces[0] == "stop" and len(pieces) == 2:  # noqa: PLR2004
                        identifier("event_id", pieces[1])
                    elif pieces[0] == "reduce_to" and len(pieces) == 3:  # noqa: PLR2004
                        identifier("event_id", pieces[1])
                        _money(pieces[2])
                    else:
                        raise ValueError("invalid sample spending change")
            if not row["decision_explanation"].strip():
                raise ValueError("sample explanation required")
            outputs.append(SourceRecord(values, dict(row), provenance))
        except (ValueError, TypeError) as exc:
            raise DatasetValidationError(f"{Path(provenance.path).name}: row {provenance.row_number}: {exc}") from exc
    return tuple(outputs)
