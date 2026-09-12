"""Normalize in-memory loader output without datasets or financial logic."""

from datetime import date
from decimal import Decimal

import pytest
from extraction import NormalizationError, normalize_request
from loaders import LoadedRequest
from models import (
    FinancialProfile,
    Income,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)


def test_complete_request():
    source = {
        "request_id": " 0007 ",
        "currency": " CAD ",
        "current_balance": " 1000.50 ",
        "savings_balance": "50",
        "minimum_balance": "100",
        "preferred_balance": "200",
        "incomes": '[{"amount": 25.01, "source": " work ", '
        '"frequency": " weekly ", "expected_date": "2026-09-15"}]',
        "recurring_expenses": '[{"amount": "10", "description": " rent ", '
        '"frequency": "monthly", "next_due_date": "2026-10-01"}]',
        "essential_expenses": '[{"amount": "5", "category": " food "}]',
        "flexible_expenses": '[{"amount": "2", "description": " snack "}]',
        "pending_payments": '[{"amount": "20", "due_date": "2026-09-20", '
        '"payment_method": " debit ", "description": " bill "}]',
        "transactions": '[{"amount": "-3.50", "date": "2026-09-10"}]',
        "other_metadata": " untouched ",
    }
    request = LoadedRequest(Decimal(30), " Book ", date(2026, 9, 12), " debit ", source)
    result = normalize_request(request)
    assert result.purchase == PurchaseRequest(
        Decimal(30), "Book", date(2026, 9, 12), "debit"
    )
    assert isinstance(result.profile, FinancialProfile)
    assert result.profile.account_balance == Decimal("1000.50")
    assert result.profile.savings_balance == Decimal(50)
    assert result.profile.minimum_balance == Decimal(100)
    assert result.profile.preferred_balance == Decimal(200)
    assert result.profile.currency == "CAD"
    assert result.profile.incomes == [
        Income(Decimal("25.01"), "work", "weekly", date(2026, 9, 15))
    ]
    assert result.profile.recurring_expenses == [
        RecurringExpense(Decimal(10), "rent", "monthly", date(2026, 10, 1))
    ]
    assert result.profile.essential_expenses == [
        RecurringExpense(Decimal(5), category="food")
    ]
    assert result.profile.flexible_expenses == [RecurringExpense(Decimal(2), "snack")]
    assert result.profile.pending_payments == [
        PendingPayment(Decimal(20), date(2026, 9, 20), "bill", "debit")
    ]
    assert result.profile.transactions[0].amount == Decimal("-3.50")
    assert result.profile.transactions[0].date == date(2026, 9, 10)
    assert result.request_id == "0007"
    assert result.source_fields == source
    result.source_fields["other_metadata"] = "changed"
    assert source["other_metadata"] == " untouched "
    assert request.description == " Book "


@pytest.mark.parametrize(
    "source",
    [
        {},
        {
            "account_balance": " ",
            "minimum_balance": "",
            "preferred_balance": "",
            "currency": " ",
            "incomes": "",
            "recurring_expenses": "[]",
            "essential_expenses": " ",
            "flexible_expenses": "",
            "pending_payments": "[]",
        },
    ],
)
def test_unknown_optional_information(source):
    result = normalize_request(LoadedRequest(Decimal(0), source_fields=source))
    assert result.profile == FinancialProfile()
    assert result.purchase == PurchaseRequest(Decimal(0))
    assert result.request_id is None
    result.profile.flexible_expenses.append(RecurringExpense(Decimal(1)))
    assert normalize_request(LoadedRequest(Decimal(0))).profile.flexible_expenses == []


def test_multiple_entries_and_optional_entry_defaults():
    result = normalize_request(
        LoadedRequest(
            Decimal(1),
            source_fields={
                "account_balance": "-1",
                "current_balance": "-1.0",
                "incomes": '[{"amount": "0", "source": " ", "expected_date": ""}, {"amount": 2}]',
                "recurring_expenses": '[{"amount": 3}, {"amount": "4", "frequency": null}]',
                "pending_payments": '[{"amount": 5}, {"amount": 6, "due_date": null}]',
            },
        )
    )
    assert result.profile.account_balance == Decimal(-1)
    assert result.profile.incomes == [Income(Decimal(0)), Income(Decimal(2))]
    assert result.profile.recurring_expenses == [
        RecurringExpense(Decimal(3)),
        RecurringExpense(Decimal(4)),
    ]
    assert result.profile.pending_payments == [
        PendingPayment(Decimal(5)),
        PendingPayment(Decimal(6)),
    ]


@pytest.mark.parametrize(
    "field, value, context",
    [
        ("account_balance", "abc", "account_balance"),
        ("minimum_balance", "-1", "minimum_balance"),
        ("preferred_balance", "NaN", "preferred_balance"),
        ("incomes", "not JSON", "incomes"),
        ("incomes", "{}", "incomes"),
        ("incomes", "[2]", r"incomes\[0\]"),
        ("incomes", "[{}]", r"incomes\[0\].amount"),
        ("incomes", '[{"amount": true}]', r"incomes\[0\].amount"),
        ("incomes", '[{"amount": "Infinity"}]', r"incomes\[0\].amount"),
        ("incomes", '[{"amount": 1, "source": 2}]', r"incomes\[0\].source"),
        ("incomes", '[{"amount": 1, "unknown": 2}]', r"incomes\[0\].unknown"),
        ("incomes", '[{"amount": 1, "amount": 2}]', "duplicate entry field 'amount'"),
        ("pending_payments", '[{"amount": -1}]', r"pending_payments\[0\].amount"),
        (
            "pending_payments",
            '[{"amount": 1, "due_date": "2026-02-30"}]',
            r"pending_payments\[0\].due_date",
        ),
        ("flexible_expenses", '[{"amount": null}]', r"flexible_expenses\[0\].amount"),
    ],
)
def test_invalid_source_has_field_context(field, value, context):
    request = LoadedRequest(Decimal(1), source_fields={field: value})
    with pytest.raises(NormalizationError, match=context):
        normalize_request(request)


@pytest.mark.parametrize(
    "amount", [None, " ", "abc", Decimal("NaN"), Decimal(-1), True, 1.5]
)
def test_invalid_mutated_purchase_amount(amount):
    request = LoadedRequest(Decimal(1))
    request.amount = amount
    with pytest.raises(NormalizationError, match="amount:"):
        normalize_request(request)


def test_conflicting_balance_aliases_fail():
    request = LoadedRequest(
        Decimal(1), source_fields={"current_balance": "1", "account_balance": "2"}
    )
    with pytest.raises(NormalizationError, match="current_balance: conflicts"):
        normalize_request(request)


@pytest.mark.parametrize("field", ["minimum_balance", "preferred_balance"])
def test_new_model_money_validation(field):
    with pytest.raises(ValueError, match=field):
        FinancialProfile(**{field: Decimal(-1)})
