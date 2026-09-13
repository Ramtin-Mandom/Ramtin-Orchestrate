"""End-to-end decision selection over a synthetic RequestContext."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from decision.challenge_decision import decide_request
from loaders.dataset_schema import SourceRecord
from models import SourceProvenance

START = date(2026, 9, 1)


def request(**overrides):
    base = {
        "request_id": "r1", "user_id": "u1", "request_date": START,
        "requested_amount": Decimal(200), "desired_completion_date": date(2026, 10, 1),
        "allows_partial_payment": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def profile(**overrides):
    base = {
        "current_available_balance": Decimal(1000), "minimum_balance_to_keep": Decimal(100),
        "expense_categories_to_protect": (), "expense_categories_user_is_willing_to_stop": (),
        "expense_categories_user_is_willing_to_reduce": (),
        "payment_methods_user_will_consider": ("full_payment", "partial_payment", "installments"),
        "max_installment_months": 6,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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
                           home_minimum_allowed_amount=minimum_allowed,
                           messages=(), images=())


def option(**overrides):
    base = {
        "payment_option_id": "p1", "payment_method": "installments",
        "payment_amount": Decimal(70), "number_of_payments": 3,
        "first_payment_date": date(2026, 9, 1), "payment_frequency_days": 30,
        "financing_fee": Decimal(10), "total_payable_amount": Decimal(210),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def context(events=(), options=(), **request_overrides):
    return SimpleNamespace(request=request(**request_overrides), profile=profile(),
                           events=tuple(events), payment_options=tuple(options))


def test_full_payment_today_when_already_safe():
    decision = decide_request(context())
    assert decision.affordability_status == "affordable_now"
    assert decision.recommended_payment_method == "full_payment"
    assert decision.amount_safe_to_pay == Decimal(200)
    assert decision.payment_plan == ((START, Decimal(200)),)
    assert decision.earliest_date_for_full_payment == START
    assert decision.spending_changes == ()


def test_amount_safe_to_pay_capped_and_not_zeroed_when_low_balance():
    ctx = context()
    ctx.profile.current_available_balance = Decimal(150)
    decision = decide_request(ctx)
    assert decision.amount_safe_to_pay == Decimal(50)
    assert Decimal(0) <= decision.amount_safe_to_pay <= ctx.request.requested_amount


def test_wait_when_full_payment_becomes_safe_later():
    ctx = context()
    ctx.profile.current_available_balance = Decimal(150)
    ctx.profile.payment_methods_user_will_consider = ("full_payment",)
    events = [converted(event_record(status="scheduled", direction="credit",
                                     amount=Decimal(500), event_date=date(2026, 9, 15)))]
    ctx.events = tuple(events)
    decision = decide_request(ctx)
    assert decision.affordability_status == "affordable_later"
    assert decision.recommended_payment_method == "wait"
    assert decision.earliest_date_for_full_payment == date(2026, 9, 15)
    assert decision.payment_plan == ((date(2026, 9, 15), Decimal(200)),)


def test_partial_payment_when_only_part_is_safe_today():
    ctx = context()
    ctx.profile.current_available_balance = Decimal(150)
    events = [converted(event_record(status="scheduled", direction="credit",
                                     amount=Decimal(500), event_date=date(2026, 9, 15)))]
    ctx.events = tuple(events)
    ctx.profile.payment_methods_user_will_consider = ("partial_payment",)
    decision = decide_request(ctx)
    assert decision.affordability_status == "affordable_with_plan"
    assert decision.recommended_payment_method == "partial_payment"
    safe = decision.amount_safe_to_pay
    assert decision.payment_plan == ((START, safe), (date(2026, 9, 15), Decimal(200) - safe))


def test_installments_matching_a_supplied_payment_option():
    ctx = context()
    ctx.profile.payment_methods_user_will_consider = ("installments",)
    ctx.payment_options = (option(),)
    decision = decide_request(ctx)
    assert decision.affordability_status == "affordable_with_plan"
    assert decision.recommended_payment_method == "installments"
    assert decision.payment_plan == (
        (date(2026, 9, 1), Decimal(70)), (date(2026, 10, 1), Decimal(70)),
        (date(2026, 10, 31), Decimal(70)),
    )


def test_installments_rejected_when_not_in_accepted_methods():
    ctx = context()
    ctx.profile.current_available_balance = Decimal(150)
    ctx.profile.payment_methods_user_will_consider = ("full_payment",)
    ctx.payment_options = (option(),)
    decision = decide_request(ctx)
    assert decision.recommended_payment_method != "installments"


def test_installments_rejected_beyond_max_installment_months():
    ctx = context()
    ctx.profile.current_available_balance = Decimal(150)
    ctx.profile.payment_methods_user_will_consider = ("installments",)
    ctx.profile.max_installment_months = 1
    ctx.payment_options = (option(number_of_payments=6, payment_frequency_days=30),)
    decision = decide_request(ctx)
    assert decision.recommended_payment_method != "installments"


def test_not_affordable_when_nothing_is_safe():
    ctx = context(requested_amount=Decimal(100000))
    ctx.profile.payment_methods_user_will_consider = ("full_payment",)
    decision = decide_request(ctx)
    assert decision.affordability_status == "not_affordable"
    assert decision.recommended_payment_method == "not_recommended"
    assert decision.payment_plan == ()
    assert decision.spending_changes == ()


def test_spending_change_unlocks_full_payment_today():
    ctx = context(requested_amount=Decimal(900))
    ctx.profile.current_available_balance = Decimal(1000)
    ctx.profile.expense_categories_user_is_willing_to_stop = ("dining",)
    ctx.profile.payment_methods_user_will_consider = ("full_payment",)
    events = [converted(event_record(
        event_id="e_flex", category="dining", flexibility="stoppable",
        amount=Decimal(300), event_date=date(2026, 9, 3),
    ))]
    ctx.events = tuple(events)
    decision = decide_request(ctx)
    assert decision.recommended_payment_method == "full_payment"
    assert decision.affordability_status == "affordable_with_plan"
    assert decision.spending_changes == ("stop:e_flex",)


def test_decision_explanation_is_nonempty_text():
    decision = decide_request(context())
    assert isinstance(decision.decision_explanation, str)
    assert decision.decision_explanation
