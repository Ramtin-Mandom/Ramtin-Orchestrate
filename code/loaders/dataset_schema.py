"""Exact participant CSV headers and scalar validation rules."""

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from models import SourceProvenance
from models.validation import identifier, parse_date

OUTPUT = ("request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation")
REQUEST = ("request_id", "user_id", "request_date", "request_type", "requested_amount", "desired_completion_date", "allows_partial_payment", "request_text")
HEADERS = {
    "requests": REQUEST,
    "sample_requests": REQUEST + OUTPUT[1:],
    "financial_profiles": ("user_id", "home_currency", "current_available_balance", "minimum_balance_to_keep", "financial_priorities", "expense_categories_to_protect", "expense_categories_user_is_willing_to_reduce", "expense_categories_user_is_willing_to_stop", "payment_methods_user_will_consider", "max_installment_months"),
    "financial_events": ("event_id", "user_id", "event_type", "description", "category", "direction", "amount", "currency", "event_date", "settlement_date", "status", "linked_event_id", "flexibility", "minimum_allowed_amount"),
    "exchange_rates": ("rate_date", "from_currency", "to_currency", "rate"),
    "request_payment_options": ("payment_option_id", "request_id", "payment_method", "payment_amount", "number_of_payments", "first_payment_date", "payment_frequency_days", "financing_fee", "total_payable_amount"),
    "messages": ("message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text"),
    "images": ("image_id", "user_id", "request_id", "related_event_id"),
    "output": OUTPUT,
}
CURRENCIES = frozenset(("EUR", "IDR", "INR", "USD", "ZAR"))
ENUMS = {
    "event_type": frozenset(["debt_payment", "expense", "income", "investment_purchase", "investment_sale", "investment_valuation", "refund", "subscription"]),
    "direction": frozenset(["credit", "debit", "non_cash"]),
    "status": frozenset(["cancelled", "failed", "pending", "scheduled", "settled", "unrealized"]),
    "flexibility": frozenset(["fixed", "reducible", "reducible_or_stoppable", "stoppable"]),
    "payment_method": frozenset(("full_payment", "installments")),
    "source_type": frozenset(["bank", "employer", "financial_service", "merchant", "service_provider"]),
}
MONEY = frozenset(["current_available_balance", "minimum_balance_to_keep", "amount", "minimum_allowed_amount", "rate", "payment_amount", "financing_fee", "total_payable_amount"])
INTEGERS = frozenset(["number_of_payments", "payment_frequency_days", "max_installment_months"])
OPTIONAL = frozenset(["request_id", "related_event_id", "linked_event_id", "settlement_date", "amount", "minimum_allowed_amount", "payment_frequency_days", "max_installment_months"])
LISTS = frozenset(["financial_priorities", "expense_categories_to_protect", "expense_categories_user_is_willing_to_reduce", "expense_categories_user_is_willing_to_stop", "payment_methods_user_will_consider"])


class DatasetValidationError(ValueError):
    """Invalid source schema, record, reference, or conversion."""


@dataclass(frozen=True)
class SourceRecord:
    """Exact source fields with typed scalar values and original provenance."""

    values: Mapping
    source_fields: Mapping[str, str]
    provenance: SourceProvenance

    def __getattr__(self, name):
        values = object.__getattribute__(self, "values")
        if name not in values:
            raise AttributeError(name)
        return values[name]


def read_csv(root, name):
    path = Path(root) / f"{name}.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if tuple(reader.fieldnames or ()) != HEADERS[name]:
                raise DatasetValidationError(f"{path.name}: expected headers {HEADERS[name]}")
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise DatasetValidationError(f"{path.name}: row {reader.line_num}: malformed record")
                rows.append((row, SourceProvenance(str(path.resolve()), reader.line_num)))
            return rows
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DatasetValidationError(f"cannot read {path.name}: {type(exc).__name__}") from exc


def scalar(name, text):  # noqa: PLR0911, PLR0912 -- explicit CSV scalar types
    if text == "" and name in OPTIONAL:
        return None
    if name.endswith("_id"):
        identifier(name, text)
    if name in MONEY:
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{name}: invalid decimal") from exc
        if not value.is_finite() or value < 0 or (name == "rate" and value == 0):
            raise ValueError(f"{name}: expected finite nonnegative money or positive rate")
        return value
    if name.endswith("currency") and text not in CURRENCIES:
        raise ValueError(f"{name}: unsupported currency")
    if name.endswith("_date"):
        return parse_date(name, text)
    if name == "sent_at":
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("sent_at: timezone required")
        return value
    if name in INTEGERS:
        if not text.isascii() or not text.isdigit() or int(text) <= 0:
            raise ValueError(f"{name}: positive integer required")
        return int(text)
    if name in ENUMS and text not in ENUMS[name]:
        raise ValueError(f"{name}: invalid enum value")
    if name in LISTS:
        parts = tuple(text.split("|")) if text else ()
        if len(parts) != len(set(parts)) or any(not p.strip() or p != p.strip() for p in parts):
            raise ValueError(f"{name}: malformed preference list")
        if name == "payment_methods_user_will_consider" and (
            not parts or set(parts) - {"full_payment", "partial_payment", "installments"}
        ):
            raise ValueError(f"{name}: invalid payment preferences")
        return parts
    if not text and name not in LISTS:
        raise ValueError(f"{name}: required value missing")
    return text


def records(rows):
    result = []
    for row, provenance in rows:
        try:
            result.append(SourceRecord({k: scalar(k, v) for k, v in row.items()}, dict(row), provenance))
        except (TypeError, ValueError) as exc:
            raise DatasetValidationError(f"{Path(provenance.path).name}: row {provenance.row_number}: {exc}") from exc
    return tuple(result)
