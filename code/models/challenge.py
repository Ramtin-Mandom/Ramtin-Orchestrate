"""Authoritative request schema from problem_statement.md.

Supporting CSV schemas are deliberately not guessed when files are absent.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Mapping, Optional

from .money import validate_money
from .validation import calendar_date, identifier


class RequestType(str, Enum):
    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


@dataclass(frozen=True)
class SourceProvenance:
    """Local source location; IDs remain verbatim, never synthesized."""

    path: str
    row_number: int

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be nonempty text")
        if type(self.row_number) is not int or self.row_number < 2:  # noqa: PLR2004
            raise ValueError("row_number must be an integer >= 2")


@dataclass(frozen=True)
class ChallengeRequest:
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    source_fields: Mapping[str, str] = field(default_factory=dict, compare=False)
    provenance: Optional[SourceProvenance] = field(default=None, compare=False)

    def __post_init__(self):
        identifier("request_id", self.request_id)
        identifier("user_id", self.user_id)
        calendar_date("request_date", self.request_date)
        calendar_date("desired_completion_date", self.desired_completion_date)
        if not isinstance(self.request_type, RequestType):
            raise TypeError("request_type must be a RequestType")
        validate_money("requested_amount", self.requested_amount)
        if self.requested_amount is None:
            raise TypeError("requested_amount must be a Decimal")
        if type(self.allows_partial_payment) is not bool:
            raise TypeError("allows_partial_payment must be a boolean")
        if not isinstance(self.request_text, str) or not self.request_text.strip():
            raise ValueError("request_text must be nonempty text")
        if self.desired_completion_date < self.request_date:
            raise ValueError("desired_completion_date must not precede request_date")
        if not isinstance(self.source_fields, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in self.source_fields.items()
        ):
            raise TypeError("source_fields must map text to text")
        object.__setattr__(self, "source_fields", dict(self.source_fields))
        if self.provenance is not None and not isinstance(self.provenance, SourceProvenance):
            raise TypeError("provenance must be SourceProvenance")
