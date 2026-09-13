"""Convert explicit scalar fields and JSON collections; infer no values."""

import json
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from loaders import LoadedRequest
from models import (
    FinancialProfile,
    Income,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
    Transaction,
)
from models.money import validate_money


class NormalizationError(ValueError):
    """An explicit input field cannot be normalized."""


@dataclass
class NormalizedRequest:
    """Container for existing financial models and untouched source metadata."""

    profile: FinancialProfile
    purchase: PurchaseRequest
    source_fields: Dict[str, str]
    request_id: Optional[str] = None


def _text(value, name: str, default=None):
    if value is None:
        return default
    if not isinstance(value, str):
        raise NormalizationError(f"{name}: expected text")
    return value.strip() or default


def _money(value, name: str, *, required=False, allow_negative=False):
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        if required:
            raise NormalizationError(f"{name}: amount is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise NormalizationError(f"{name}: expected a decimal number")
    try:
        amount = Decimal(value)
        validate_money(name, amount, allow_negative=allow_negative)
    except (InvalidOperation, ValueError) as exc:
        raise NormalizationError(
            f"{name}: expected a finite decimal number"
            + ("" if allow_negative else " that is nonnegative")
        ) from exc
    return amount


def _date(value, name: str):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = _text(value, name)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise NormalizationError(f"{name}: expected ISO date YYYY-MM-DD") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate entry field '{key}'")
        result[key] = value
    return result


def _entries(value, name: str, model) -> List:
    text = _text(value, name)
    if text is None:
        return []
    try:
        entries = json.loads(
            text, parse_float=Decimal, object_pairs_hook=_unique_object
        )
    except ValueError as exc:
        raise NormalizationError(f"{name}: invalid JSON collection ({exc})") from exc
    if not isinstance(entries, list):
        raise NormalizationError(f"{name}: expected a JSON array of objects")
    allowed = {item.name for item in fields(model)}
    result = []
    for index, entry in enumerate(entries):
        prefix = f"{name}[{index}]"
        if not isinstance(entry, dict):
            raise NormalizationError(f"{prefix}: expected an object")
        unknown = set(entry) - allowed
        if unknown:
            raise NormalizationError(f"{prefix}.{min(unknown)}: unsupported field")
        values = {
            "amount": _money(
                entry.get("amount"),
                f"{prefix}.amount",
                required=True,
                allow_negative=model is Transaction,
            )
        }
        for key, entry_value in entry.items():
            if key == "amount":
                continue
            field_name = f"{prefix}.{key}"
            if key == "date" or key.endswith("_date"):
                values[key] = _date(entry_value, field_name)
            else:
                default = "" if key in {"description", "source"} else None
                values[key] = _text(entry_value, field_name, default)
        result.append(model(**values))
    return result


def normalize_request(request: LoadedRequest, *, on_optional_warning=None) -> NormalizedRequest:
    """Normalize model-named source fields and JSON arrays of model fields.

    Purchase fields come from the loaded object. All metadata is copied raw.
    current_balance is an explicit alias for account_balance; conflicting
    values fail. No dates, frequencies, categories or balances are inferred.
    """
    source = dict(request.source_fields)
    balance = _money(
        source.get("account_balance"), "account_balance", allow_negative=True
    )
    current = _money(
        source.get("current_balance"), "current_balance", allow_negative=True
    )
    if balance is not None and current is not None and balance != current:
        raise NormalizationError("current_balance: conflicts with account_balance")
    try:
        savings = _money(source.get("savings_balance"), "savings_balance")
    except NormalizationError:
        if on_optional_warning is None:
            raise
        on_optional_warning("savings_balance")
        savings = None
    profile = FinancialProfile(
        currency=_text(source.get("currency"), "currency"),
        account_balance=balance if balance is not None else current,
        savings_balance=savings,
        minimum_balance=_money(source.get("minimum_balance"), "minimum_balance"),
        preferred_balance=_money(source.get("preferred_balance"), "preferred_balance"),
        transactions=_entries(source.get("transactions"), "transactions", Transaction),
        incomes=_entries(source.get("incomes"), "incomes", Income),
        recurring_expenses=_entries(
            source.get("recurring_expenses"), "recurring_expenses", RecurringExpense
        ),
        essential_expenses=_entries(
            source.get("essential_expenses"), "essential_expenses", RecurringExpense
        ),
        flexible_expenses=_entries(
            source.get("flexible_expenses"), "flexible_expenses", RecurringExpense
        ),
        pending_payments=_entries(
            source.get("pending_payments"), "pending_payments", PendingPayment
        ),
    )
    purchase = PurchaseRequest(
        amount=_money(request.amount, "amount", required=True),
        description=_text(request.description, "description", ""),
        desired_date=_date(request.desired_date, "desired_date"),
        preferred_payment_method=_text(
            request.preferred_payment_method, "preferred_payment_method"
        ),
    )
    return NormalizedRequest(
        profile, purchase, source, _text(source.get("request_id"), "request_id")
    )
