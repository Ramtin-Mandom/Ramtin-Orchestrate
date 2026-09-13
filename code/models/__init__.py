"""Shared dataclasses for Buy or Wait. All money uses Decimal, never float."""

from .challenge import ChallengeRequest, RequestType, SourceProvenance
from .decision import DecisionResult, PaymentPlan
from .profile import FinancialProfile
from .records import (
    Income,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
    Transaction,
)
from .spending import SpendingChange

__all__ = [
    "ChallengeRequest",
    "DecisionResult",
    "FinancialProfile",
    "Income",
    "PaymentPlan",
    "PendingPayment",
    "PurchaseRequest",
    "RecurringExpense",
    "RequestType",
    "SourceProvenance",
    "SpendingChange",
    "Transaction",
]
