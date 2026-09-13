"""Pure merge precedence, traceability and downstream compatibility."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from decision import decide_purchase
from extraction import normalize_request
from extraction.media_extractor import ExtractedFact
from extraction.profile_merger import merge_profile
from finance import forecast_balance
from loaders import LoadedRequest
from models import (
    FinancialProfile,
    Income,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)

DAY = date(2026, 9, 15)


def fact(kind="income", **kwargs):
    result = ExtractedFact(Path("salary.txt"), kind, "confirmed", "salary 25",
                           Decimal(25), "work", DAY, None, None, None)
    return replace(result, **kwargs)


def test_csv_only_preserves_values_and_independent_objects():
    profile = FinancialProfile(
        currency="CAD", account_balance=Decimal(100), savings_balance=Decimal(50),
        minimum_balance=Decimal(10), preferred_balance=Decimal(20),
        incomes=[Income(Decimal(25), "work", expected_date=DAY)],
        pending_payments=[PendingPayment(Decimal(10), DAY, "bill")],
    )
    original = deepcopy(profile)
    result = merge_profile(profile)
    assert result.profile == profile
    assert result.profile is not profile
    assert result.profile.incomes[0] is not profile.incomes[0]
    result.profile.incomes[0].source = "changed"
    assert profile == original
    assert not result.conflicts
    assert result.provenance[("incomes", 0)][0].origin == "csv"


@pytest.mark.parametrize("kind,group,model", [
    ("income", "incomes", Income),
    ("pending_payment", "pending_payments", PendingPayment),
    ("recurring_expense", "recurring_expenses", RecurringExpense),
    ("essential_expense", "essential_expenses", RecurringExpense),
    ("flexible_expense", "flexible_expenses", RecurringExpense),
])
def test_media_only_additions(kind, group, model):
    incoming = fact(kind)
    original = deepcopy(incoming)
    result = merge_profile(FinancialProfile(), [incoming])
    records = getattr(result.profile, group)
    assert len(records) == 1
    assert isinstance(records[0], model)
    assert result.provenance[(group, 0)][0].path == incoming.source_path
    assert result.provenance[(group, 0)][0].evidence == incoming.evidence
    assert incoming == original


def test_combined_normalization_forecast_and_decision():
    normalized = normalize_request(LoadedRequest(Decimal(5), source_fields={
        "account_balance": "100", "minimum_balance": "10",
        "incomes": '[{"amount":"25", "source":"work", "expected_date":"2026-09-15"}]',
    }))
    facts = [fact(), fact("pending_payment", description="bill", amount=Decimal(10))]
    original = deepcopy((normalized, facts))
    result = merge_profile(normalized.profile, facts)
    forecast = forecast_balance(result.profile, DAY, DAY)
    assert forecast.entries[-1].balance == Decimal(115)
    decision = decide_purchase(result.profile, PurchaseRequest(Decimal(5)), forecast)
    assert decision.amount_safe_to_pay == Decimal(80)
    assert (normalized, facts) == original


def test_exact_duplicates_combine_csv_and_media_provenance():
    profile = FinancialProfile(incomes=[
        Income(Decimal(25), " Work ", expected_date=DAY),
        Income(Decimal("25.00"), "work", expected_date=DAY),
    ])
    facts = [fact(), fact(source_path=Path("image.png"))]
    result = merge_profile(profile, facts)
    assert len(result.profile.incomes) == 1
    assert result.profile.incomes[0].source == " Work "
    assert len(result.provenance[("incomes", 0)]) == len(profile.incomes) + len(facts)
    assert not result.conflicts


def test_media_duplicates_only():
    result = merge_profile(FinancialProfile(), [fact(), fact(source_path=Path("other.txt"))])
    assert result.profile.incomes == [Income(Decimal(25), "work", expected_date=DAY)]
    assert [source.path for source in result.provenance[("incomes", 0)]] == [
        Path("salary.txt"), Path("other.txt")]


@pytest.mark.parametrize("changed,field", [
    ({"amount": Decimal(30)}, "amount"), ({"frequency": "weekly"}, "frequency"),
])
def test_csv_conflict_keeps_explicit_value(changed, field):
    profile = FinancialProfile(incomes=[Income(Decimal(25), "work", "monthly", DAY)])
    result = merge_profile(profile, [fact(frequency="monthly", **changed)]
                           if field == "amount" else [fact(**changed)])
    assert result.profile == profile
    conflict, = result.conflicts
    assert conflict.reason == "csv_precedence"
    assert field in conflict.fields
    assert conflict.sources[0].origin == "csv"
    assert conflict.sources[1].path == Path("salary.txt")


def test_optional_csv_fields_filled_without_overwriting():
    profile = FinancialProfile(pending_payments=[PendingPayment(Decimal(25), None, "work")])
    result = merge_profile(profile, [fact("pending_payment", payment_method="debit")])
    assert result.profile.pending_payments == [PendingPayment(Decimal(25), DAY, "work", "debit")]
    assert profile.pending_payments[0].due_date is None
    assert not result.conflicts


def test_missing_description_filled_by_known_date_and_amount():
    profile = FinancialProfile(incomes=[Income(Decimal(25), expected_date=DAY)])
    result = merge_profile(profile, [fact()])
    assert len(result.profile.incomes) == 1
    assert result.profile.incomes[0].source == "work"


def test_unresolved_confirmed_media_omitted_independent_of_order():
    facts = [fact(), fact(amount=Decimal(30)), fact(source_path=Path("duplicate.txt"))]
    for ordered in (facts, list(reversed(facts))):
        result = merge_profile(FinancialProfile(), ordered)
        assert not result.profile.incomes
        assert result.conflicts[0].reason == "unresolved_media_conflict"
        assert result.conflicts[0].fields == ("amount",)
        assert len(result.conflicts[0].sources) == len(facts)


def test_media_can_supply_complementary_fields():
    facts = [fact(date=None), fact(frequency="monthly")]
    result = merge_profile(FinancialProfile(), facts)
    assert result.profile.incomes == [Income(Decimal(25), "work", "monthly", DAY)]
    assert not result.conflicts


def test_conflicting_media_optional_field_not_filled_in_csv():
    profile = FinancialProfile(incomes=[Income(Decimal(25), "work", expected_date=DAY)])
    result = merge_profile(profile, [fact(frequency="weekly"), fact(frequency="monthly")])
    assert result.profile == profile
    assert result.conflicts[0].reason == "unresolved_media_fields"
    assert result.conflicts[0].fields == ("frequency",)


def test_uncertain_incomplete_and_purchase_do_not_enter_profile():
    profile = FinancialProfile(account_balance=Decimal(100))
    uncertain = fact(certainty="uncertain")
    incomplete = fact(amount=None)
    result = merge_profile(profile, [uncertain, incomplete, fact("purchase")])
    assert result.profile == profile
    assert result.ignored_uncertain_facts == (uncertain,)
    assert result.ignored_incomplete_facts == (incomplete,)
    assert len(result.warnings) == 1
    assert forecast_balance(result.profile, DAY, DAY).entries[-1].balance == Decimal(100)


def test_distinct_dates_and_names_are_new_events():
    facts = [fact(), fact(date=date(2026, 9, 16)), fact(description="other job")]
    result = merge_profile(FinancialProfile(), facts)
    assert len(result.profile.incomes) == len(facts)
    assert not result.conflicts


def test_ambiguous_missing_date_does_not_bridge_two_csv_events():
    profile = FinancialProfile(incomes=[
        Income(Decimal(25), "work", expected_date=DAY),
        Income(Decimal(25), "work", expected_date=date(2026, 9, 16)),
    ])
    result = merge_profile(profile, [fact(date=None)])
    assert result.profile == profile
    assert result.conflicts[0].reason == "ambiguous_csv_match"


def test_cross_expense_groups_do_not_double_count():
    profile = FinancialProfile(recurring_expenses=[
        RecurringExpense(Decimal(25), "work", next_due_date=DAY)],
        essential_expenses=[RecurringExpense(Decimal(25), "work", next_due_date=DAY)])
    result = merge_profile(profile, [fact("pending_payment")])
    assert len(result.profile.recurring_expenses) == 1
    assert result.profile.essential_expenses == []
    assert result.profile.pending_payments == []
    assert result.conflicts[0].reason == "csv_precedence"
    assert forecast_balance(replace(result.profile, account_balance=Decimal(100)),
                            DAY, DAY).entries[-1].balance == Decimal(75)


def test_classification_conflict_in_media_omits_addition():
    result = merge_profile(FinancialProfile(),
                           [fact("essential_expense"), fact("flexible_expense")])
    assert not result.profile.essential_expenses
    assert not result.profile.flexible_expenses
    assert "expense_group" in result.conflicts[0].fields


def test_csv_order_precedes_new_media_and_is_repeatable():
    profile = FinancialProfile(incomes=[Income(Decimal(25), "work"),
                                       Income(Decimal(50), "second")])
    incoming = [fact(), fact(description="third"), fact(description="fourth")]
    result = merge_profile(profile, iter(incoming))
    assert [record.source for record in result.profile.incomes] == [
        "work", "second", "third", "fourth"]
    assert result == merge_profile(profile, incoming)


def test_csv_same_event_conflict_is_reported_and_counted_once():
    profile = FinancialProfile(pending_payments=[
        PendingPayment(Decimal(25), DAY, "bill"),
        PendingPayment(Decimal(30), DAY, "bill"),
    ])
    result = merge_profile(profile)
    assert result.profile.pending_payments == [profile.pending_payments[0]]
    assert result.conflicts[0].reason == "csv_first_occurrence"
    assert result.conflicts[0].fields == ("amount",)
    assert len(profile.pending_payments) == len(result.conflicts[0].records)


def test_csv_cross_payment_expense_match_is_not_counted_twice():
    profile = FinancialProfile(recurring_expenses=[
        RecurringExpense(Decimal(25), "bill", next_due_date=DAY)],
        pending_payments=[PendingPayment(Decimal(25), DAY, "bill")])
    result = merge_profile(profile)
    assert len(result.profile.recurring_expenses) == 1
    assert not result.profile.pending_payments
    assert result.conflicts[0].fields == ("expense_group",)


def test_csv_interleaved_components_retain_original_order():
    profile = FinancialProfile(incomes=[
        Income(Decimal(25), "work", expected_date=DAY),
        Income(Decimal(5), "middle", expected_date=DAY),
        Income(Decimal(25), "work", expected_date=date(2026, 9, 16)),
        Income(Decimal(25), "work"),
    ])
    assert merge_profile(profile).profile == profile


def test_anonymous_exact_duplicates_but_no_guess_by_amount_only():
    anonymous = fact(description=None, date=None)
    result = merge_profile(FinancialProfile(), [anonymous, anonymous, fact()])
    assert [record.source for record in result.profile.incomes] == ["", "work"]


def test_transaction_duplicates_remain_historical_records():
    from models import Transaction  # noqa: PLC0415 -- local test fixture

    profile = FinancialProfile(transactions=[
        Transaction(Decimal(25), DAY, "work"), Transaction(Decimal(25), DAY, " WORK ")])
    result = merge_profile(profile, [fact()])
    assert result.profile.transactions == [profile.transactions[0]]
    assert len(result.profile.incomes) == 1


def test_missing_media_description_uses_existing_model_default_downstream():
    incoming = fact("recurring_expense", description=None, category="flexible")
    result = merge_profile(FinancialProfile(account_balance=Decimal(100),
                                          minimum_balance=Decimal(10)), [incoming])
    forecast = forecast_balance(result.profile, DAY, DAY)
    assert result.profile.recurring_expenses[0].description == ""
    assert incoming.description is None
    assert decide_purchase(result.profile, PurchaseRequest(Decimal(5)),
                           forecast).amount_safe_to_pay == Decimal(65)
