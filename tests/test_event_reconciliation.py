"""Deterministic reconciliation, independent of any AI extraction call."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from extraction.event_extractor import EventFact
from extraction.event_reconciliation import (
    find_superseded_events,
    reconcile_event,
)
from loaders.dataset_schema import SourceRecord
from models import SourceProvenance


def event_record(event_id="e1", **overrides):
    values = {
        "event_id": event_id, "user_id": "u1", "event_type": "expense",
        "description": "Groceries", "category": "food", "direction": "debit",
        "amount": Decimal("50.00"), "currency": "USD", "event_date": date(2026, 9, 1),
        "settlement_date": None, "status": "pending", "linked_event_id": None,
        "flexibility": "reducible", "minimum_allowed_amount": None,
    }
    values.update(overrides)
    return SourceRecord(dict(values), {k: str(v) for k, v in values.items()},
                        SourceProvenance("financial_events.csv", 2))


def converted(record):
    return SimpleNamespace(event=record)


def event_fact(source_id="m1", source_type="message", sent_at=None, **overrides):
    base = {
        "event_id": "e1", "request_id": "r1", "user_id": "u1", "home_currency": "USD",
        "source_type": source_type, "source_id": source_id, "sent_at": sent_at,
        "certainty": "confirmed", "evidence": "evidence", "currency": "USD", "amount": None,
        "date": None, "status": None, "is_confirmation": False, "is_cancellation": False,
        "is_settlement": False, "is_amendment": False, "is_delay": False, "is_recurring": None,
        "flexibility": None,
    }
    base.update(overrides)
    return EventFact(**base)


def test_blank_amount_filled_by_confirmed_fact():
    record = event_record(amount=None)
    fact = event_fact(source_type="image", source_id="i1", amount=Decimal("899.00"))
    result = reconcile_event(converted(record), [fact])
    assert result.amount == Decimal("899.00")
    assert result.amount_source == "fact:image:i1"
    assert fact in result.applied_facts


def test_blank_amount_never_defaults_to_zero_without_facts():
    record = event_record(amount=None)
    result = reconcile_event(converted(record), [])
    assert result.amount is None
    assert result.unresolved == ()


def test_amount_currency_mismatch_left_unresolved():
    record = event_record(amount=None, currency="USD")
    fact = event_fact(currency="EUR", amount=Decimal("40.00"))
    result = reconcile_event(converted(record), [fact])
    assert result.amount is None
    assert result.unresolved
    assert fact in result.ignored_facts


def test_conflicting_amounts_without_recency_left_unresolved():
    record = event_record(amount=None)
    a = event_fact(source_id="m1", amount=Decimal("40.00"))
    b = event_fact(source_id="m2", amount=Decimal("45.00"))
    result = reconcile_event(converted(record), [a, b])
    assert result.amount is None
    assert result.unresolved


def test_conflicting_amounts_resolved_by_recency():
    record = event_record(amount=None)
    older = event_fact(source_id="m1", amount=Decimal("40.00"),
                       sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    newer = event_fact(source_id="m2", amount=Decimal("45.00"),
                       sent_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    result = reconcile_event(converted(record), [older, newer])
    assert result.amount == Decimal("45.00")
    assert result.amount_source == "fact:message:m2"
    assert older in result.ignored_facts


def test_uncertain_amount_never_fills_blank():
    record = event_record(amount=None)
    fact = event_fact(amount=Decimal("899.00"), certainty="uncertain")
    result = reconcile_event(converted(record), [fact])
    assert result.amount is None


def test_explicit_cancellation_applied():
    record = event_record(status="scheduled")
    fact = event_fact(is_cancellation=True)
    result = reconcile_event(converted(record), [fact])
    assert result.status == "cancelled"
    assert result.is_cancelled is True
    assert result.counts_toward_cash_flow is False


def test_explicit_settlement_applied():
    record = event_record(status="pending")
    fact = event_fact(is_settlement=True)
    result = reconcile_event(converted(record), [fact])
    assert result.status == "settled"
    assert result.is_settled is True
    assert result.counts_toward_cash_flow is True


def test_failed_transaction_status_applied():
    record = event_record(status="pending")
    fact = event_fact(status="failed")
    result = reconcile_event(converted(record), [fact])
    assert result.status == "failed"
    assert result.counts_toward_cash_flow is False


def test_cancellation_vs_settlement_resolved_by_recency():
    record = event_record(status="pending")
    cancel = event_fact(source_id="m1", is_cancellation=True,
                        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    settle = event_fact(source_id="m2", is_settlement=True,
                        sent_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
    result = reconcile_event(converted(record), [cancel, settle])
    assert result.status == "settled"


def test_cancellation_vs_settlement_unresolved_prefers_safer_for_credit():
    record = event_record(status="pending", direction="credit")
    cancel = event_fact(source_id="m1", is_cancellation=True)
    settle = event_fact(source_id="m2", is_settlement=True)
    result = reconcile_event(converted(record), [cancel, settle])
    assert result.status == "cancelled"
    assert result.unresolved


def test_cancellation_vs_settlement_unresolved_prefers_safer_for_debit():
    record = event_record(status="pending", direction="debit")
    cancel = event_fact(source_id="m1", is_cancellation=True)
    settle = event_fact(source_id="m2", is_settlement=True)
    result = reconcile_event(converted(record), [cancel, settle])
    assert result.status == "settled"
    assert result.unresolved


def test_amendment_overrides_amount_and_date():
    record = event_record(amount=Decimal("50.00"), currency="USD",
                          event_date=date(2026, 9, 1))
    fact = event_fact(is_amendment=True, amount=Decimal("45.00"), date=date(2026, 9, 20))
    result = reconcile_event(converted(record), [fact])
    assert result.amount == Decimal("45.00")
    assert result.effective_date == date(2026, 9, 20)
    assert fact in result.applied_facts


def test_conflicting_amendments_left_unresolved():
    record = event_record(amount=Decimal("50.00"))
    a = event_fact(source_id="m1", is_amendment=True, amount=Decimal("45.00"))
    b = event_fact(source_id="m2", is_amendment=True, amount=Decimal("60.00"))
    result = reconcile_event(converted(record), [a, b])
    assert result.amount == Decimal("50.00")
    assert result.unresolved


def test_delay_shifts_effective_date_only():
    record = event_record(amount=Decimal("50.00"), event_date=date(2026, 9, 1))
    fact = event_fact(is_delay=True, date=date(2026, 10, 5))
    result = reconcile_event(converted(record), [fact])
    assert result.effective_date == date(2026, 10, 5)
    assert result.amount == Decimal("50.00")


def test_delay_without_date_is_ignored():
    record = event_record(event_date=date(2026, 9, 1))
    fact = event_fact(is_delay=True, date=None)
    result = reconcile_event(converted(record), [fact])
    assert result.effective_date == date(2026, 9, 1)
    assert fact in result.ignored_facts


def test_settlement_date_preferred_over_event_date_by_default():
    record = event_record(event_date=date(2026, 9, 1), settlement_date=date(2026, 9, 3))
    result = reconcile_event(converted(record), [])
    assert result.effective_date == date(2026, 9, 3)


def test_recurrence_agreement():
    facts = [event_fact(source_id="m1", is_recurring=True),
             event_fact(source_id="m2", is_recurring=True)]
    result = reconcile_event(converted(event_record()), facts)
    assert result.is_recurring is True
    assert result.unresolved == ()


def test_recurrence_conflict_left_unresolved():
    facts = [event_fact(source_id="m1", is_recurring=True),
             event_fact(source_id="m2", is_recurring=False)]
    result = reconcile_event(converted(event_record()), facts)
    assert result.is_recurring is None
    assert result.unresolved


def test_find_superseded_events_pending_to_settled_lifecycle():
    parent = event_record(event_id="e1", status="pending", direction="debit")
    child = event_record(event_id="e2", status="settled", direction="debit",
                         linked_event_id="e1")
    superseded = find_superseded_events([converted(parent), converted(child)])
    assert superseded == frozenset({"e1"})


def test_investment_lifecycle_never_superseded():
    purchase = event_record(event_id="e1", event_type="investment_purchase",
                            direction="debit", status="settled")
    valuation = event_record(event_id="e2", event_type="investment_valuation",
                             direction="non_cash", status="unrealized",
                             linked_event_id="e1")
    superseded = find_superseded_events([converted(purchase), converted(valuation)])
    assert superseded == frozenset()


def test_still_pending_link_not_superseded():
    parent = event_record(event_id="e1", status="pending", direction="debit")
    child = event_record(event_id="e2", status="pending", direction="debit",
                         linked_event_id="e1")
    superseded = find_superseded_events([converted(parent), converted(child)])
    assert superseded == frozenset()


def test_unlinked_events_never_superseded():
    a = event_record(event_id="e1", status="settled")
    b = event_record(event_id="e2", status="settled")
    assert find_superseded_events([converted(a), converted(b)]) == frozenset()
