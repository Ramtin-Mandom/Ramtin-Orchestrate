"""Load purchase requests using the existing model's field names."""

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Union

from models import PurchaseRequest


class RequestsCSVError(ValueError):
    """A CSV header or row cannot be converted to a purchase request."""


@dataclass
class LoadedRequest(PurchaseRequest):
    """A PurchaseRequest retaining all original CSV values, including IDs."""

    source_fields: Dict[str, str] = field(default_factory=dict)


def _parse_row(source: Dict[str, str]) -> LoadedRequest:
    amount_text = source["amount"].strip()
    if not amount_text:
        raise ValueError("field 'amount' is required")
    try:
        amount = Decimal(amount_text)
    except InvalidOperation as exc:
        raise ValueError("field 'amount' must be a decimal number") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("field 'amount' must be finite and nonnegative")

    desired_text = source.get("desired_date", "").strip()
    desired_date = None
    if desired_text:
        try:
            desired_date = date.fromisoformat(desired_text)
        except ValueError as exc:
            raise ValueError("field 'desired_date' must be an ISO date (YYYY-MM-DD)") from exc
    return LoadedRequest(
        amount=amount,
        description=source.get("description", ""),
        desired_date=desired_date,
        preferred_payment_method=source.get("preferred_payment_method", "").strip() or None,
        source_fields=source,
    )


def load_requests_csv(path: Union[str, Path]) -> List[LoadedRequest]:
    """Read UTF-8 CSV; amount is required, other model columns are optional.

    Extra columns are retained verbatim in each request's source_fields.
    Error row numbers are physical CSV line numbers (the header is line 1).
    """
    requests = []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            headers = reader.fieldnames
            if not headers:
                raise RequestsCSVError(f"{path}: header is missing; required field 'amount'")
            if any(not name.strip() for name in headers):
                raise RequestsCSVError(f"{path}: header contains an empty field name")
            if len(headers) != len(set(headers)):
                raise RequestsCSVError(f"{path}: header contains duplicate field names")
            if "amount" not in headers:
                raise RequestsCSVError(f"{path}: header missing required field 'amount'")
            for row in reader:
                if None in row:
                    raise RequestsCSVError(f"{path}: row {reader.line_num}: too many fields")
                source = {name: value if value is not None else "" for name, value in row.items()}
                try:
                    requests.append(_parse_row(source))
                except ValueError as exc:
                    raise RequestsCSVError(f"{path}: row {reader.line_num}: {exc}") from exc
        except csv.Error as exc:
            raise RequestsCSVError(f"{path}: row {reader.line_num}: malformed CSV: {exc}") from exc
    return requests
