"""Load challenge requests or the separate legacy model-named input format."""

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Union

from models import ChallengeRequest, PurchaseRequest, RequestType, SourceProvenance
from models.validation import parse_boolean, parse_date

CHALLENGE_HEADERS = (
    "request_id", "user_id", "request_date", "request_type", "requested_amount",
    "desired_completion_date", "allows_partial_payment", "request_text",
)


def _parse_challenge_row(source, path, row_number):
    try:
        amount = Decimal(source["requested_amount"])
    except InvalidOperation as exc:
        raise ValueError("requested_amount must be a decimal number") from exc
    return ChallengeRequest(
        request_id=source["request_id"], user_id=source["user_id"],
        request_date=parse_date("request_date", source["request_date"]),
        request_type=RequestType(source["request_type"]), requested_amount=amount,
        desired_completion_date=parse_date(
            "desired_completion_date", source["desired_completion_date"]
        ),
        allows_partial_payment=parse_boolean(
            "allows_partial_payment", source["allows_partial_payment"]
        ),
        request_text=source["request_text"], source_fields=source,
        provenance=SourceProvenance(str(Path(path).resolve()), row_number),
    )


class RequestsCSVError(ValueError):
    """A CSV header or row cannot be converted to a request."""


@dataclass(frozen=True)
class RequestRowFailure:
    number: int
    request_id: str
    exception_type: str


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


def load_requests_csv(path: Union[str, Path], *, on_row_error=None, required_headers=()) -> List[Union[LoadedRequest, ChallengeRequest]]:
    """Read UTF-8 CSV with strict challenge fields or legacy amount fields.

    Any challenge-specific header selects the complete challenge schema.
    Challenge records carry typed IDs, dates, booleans and source provenance.
    Legacy inputs remain supported as a separate format for existing callers.

    Extra columns are retained verbatim in each request's source_fields.
    Error row numbers are physical CSV line numbers (the header is line 1).
    With on_row_error, invalid purchase rows are reported as RequestRowFailure
    and omitted; structural CSV/header errors still raise RequestsCSVError.
    The default remains strict for existing standalone callers.
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
            challenge = any(name in headers for name in (
                "requested_amount", "request_type", "allows_partial_payment",
                "desired_completion_date", "user_id", "request_text",
            ))
            if challenge and any(name not in headers for name in CHALLENGE_HEADERS):
                raise RequestsCSVError("missing required challenge CSV headers")
            if not challenge and "amount" not in headers:
                raise RequestsCSVError(f"{path}: header missing required field 'amount'")
            if any(header not in headers for header in required_headers):
                raise RequestsCSVError("missing required CSV headers")
            for number, row in enumerate(reader, start=1):
                if None in row:
                    raise RequestsCSVError(f"{path}: row {reader.line_num}: too many fields")
                source = {name: value if value is not None else "" for name, value in row.items()}
                try:
                    if challenge and any(value is None for value in row.values()):
                        raise ValueError("record has missing fields")
                    requests.append(
                        _parse_challenge_row(source, path, reader.line_num)
                        if challenge else _parse_row(source)
                    )
                except (ValueError, TypeError) as exc:
                    if on_row_error is not None:
                        on_row_error(RequestRowFailure(number, source.get("request_id", ""), type(exc).__name__))
                        continue
                    raise RequestsCSVError(f"{path}: row {reader.line_num}: {exc}") from exc
        except csv.Error as exc:
            raise RequestsCSVError(f"{path}: row {reader.line_num}: malformed CSV: {exc}") from exc
    return requests
