"""Pure forecast math and event-inclusion rules, independent of decisions."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from finance.challenge_forecast import (
    amount_safe_to_pay,
    build_timeline,
    earliest_full_payment_date,
)
from loaders.dataset_schema import SourceRecord
from models import SourceProvenance

START = date(2026, 9, 1)
END = date(2026, 11, 30)


def event_record(event_id="e1", **overrides):
    values = {
        "event_id": event_id, "user_id": "u1", "event_type": "expense",
        "description": "Rent", "category": "housing", "direction": "debit",
        "amount": Decimal(100), "currency": "USD", "event_date": date(2026, 9, 10),
        "settlement_date": None, "status": "scheduled", "linked_event_id": None,
        "flexibility": "fixed", "minimum_allowed_amount": None,
    }
    values.update(overrides)
    return SourceRecord(dict(values), {k: str(v) for k, v in values.items()},
                        SourceProvenance("financial_events.csv", 2))


def converted(record, minimum_allowed=None, rate=None):
    return SimpleNamespace(event=record, exchange_rate=rate,
                           home_minimum_allowed_amount=minimum_allowed)


def profile(**overrides):
    base = {
        "current_available_balance": Decimal(1000), "minimum_balance_to_keep": Decimal(100),
        "expense_categories_to_protect": (), "expense_categories_user_is_willing_to_stop": (),
        "expense_categories_user_is_willing_to_reduce": (),
        "payment_methods_user_will_consider": ("full_payment",), "max_installment_months": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_amount_safe_to_pay_capped_at_requested_amount():
    safe = amount_safe_to_pay(Decimal(1000), [], Decimal(100), Decimal(50))
    assert safe == Decimal(50)


def test_amount_safe_to_pay_never_negative():
    safe = amount_safe_to_pay(Decimal(50), [], Decimal(100), Decimal(500))
    assert safe == Decimal(0)


def test_amount_safe_to_pay_uses_lowest_future_point():
    timeline = [(date(2026, 9, 5), Decimal(-800))]
    safe = amount_safe_to_pay(Decimal(1000), timeline, Decimal(100), Decimal(500))
    assert safe == Decimal(100)  # 1000 - 800 - 100 minimum


def test_earliest_full_payment_date_today_when_already_safe():
    assert earliest_full_payment_date(
        Decimal(1000), [], Decimal(200), Decimal(100), START, END
    ) == START


def test_earliest_full_payment_date_waits_for_a_future_credit():
    timeline = [(date(2026, 9, 10), Decimal(500))]
    result = earliest_full_payment_date(
        Decimal(100), timeline, Decimal(400), Decimal(50), START, END
    )
    assert result == date(2026, 9, 10)


def test_earliest_full_payment_date_none_when_never_safe():
    assert earliest_full_payment_date(
        Decimal(100), [], Decimal(10000), Decimal(50), START, END
    ) is None


def test_timeline_excludes_cancelled_settled_and_pending_credit():
    events = [
        converted(event_record("e1", status="cancelled", event_date=date(2026, 9, 5))),
        converted(event_record("e2", status="settled", event_date=date(2026, 9, 6))),
        converted(event_record("e3", status="pending", direction="credit",
                               event_date=date(2026, 9, 7))),
        converted(event_record("e4", status="unrealized", direction="non_cash",
                               event_date=date(2026, 9, 8))),
    ]
    timeline, flexible = build_timeline(events, profile(), START, END)
    assert timeline == []
    assert flexible == []


def test_timeline_includes_scheduled_debit_and_credit():
    events = [
        converted(event_record("e1", status="scheduled", direction="debit",
                               amount=Decimal(100), event_date=date(2026, 9, 5))),
        converted(event_record("e2", status="scheduled", direction="credit",
                               amount=Decimal(300), event_date=date(2026, 9, 6))),
        converted(event_record("e3", status="pending", direction="debit",
                               amount=Decimal(50), event_date=date(2026, 9, 7))),
    ]
    timeline, _flexible = build_timeline(events, profile(), START, END)
    assert timeline == [
        (date(2026, 9, 5), Decimal(-100)), (date(2026, 9, 6), Decimal(300)),
        (date(2026, 9, 7), Decimal(-50)),
    ]


def test_timeline_converts_foreign_currency_via_exchange_rate():
    rate = SimpleNamespace(rate=Decimal(2))
    events = [converted(event_record("e1", currency="EUR", amount=Decimal(10)), rate=rate)]
    timeline, _ = build_timeline(events, profile(), START, END)
    assert timeline == [(date(2026, 9, 10), Decimal(-20))]


def test_flexible_candidate_requires_willing_category_and_not_protected():
    record = event_record(category="dining", flexibility="stoppable")
    willing = profile(expense_categories_user_is_willing_to_stop=("dining",))
    _timeline, flexible = build_timeline([converted(record)], willing, START, END)
    assert flexible[0].event_id == "e1"
    assert flexible[0].action == "stop"

    protected = profile(expense_categories_user_is_willing_to_stop=("dining",),
                        expense_categories_to_protect=("dining",))
    _timeline, none_flexible = build_timeline([converted(record)], protected, START, END)
    assert none_flexible == []


def test_reduce_candidate_uses_minimum_allowed_amount():
    record = event_record(category="dining", flexibility="reducible", amount=Decimal(100))
    willing = profile(expense_categories_user_is_willing_to_reduce=("dining",))
    _timeline, flexible = build_timeline(
        [converted(record, minimum_allowed=Decimal(30))], willing, START, END,
    )
    candidate, = flexible
    assert candidate.action == "reduce"
    assert candidate.reduced_amount == Decimal(30)
    assert candidate.freed == Decimal(70)
