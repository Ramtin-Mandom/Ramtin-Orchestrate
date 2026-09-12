"""Only already-budgeted, explicitly flexible outflows can yield savings."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

import pytest
from finance import (
    BalanceForecast,
    ForecastEntry,
    evaluate_spending_adjustments,
    forecast_balance,
)
from models import (
    FinancialProfile,
    PaymentPlan,
    PendingPayment,
    PurchaseRequest,
    RecurringExpense,
)

TODAY = date(2026, 9, 12)
FIRST = date(2026, 9, 15)
LATER = date(2026, 9, 20)


def budgeted_forecast(starting, expenses, obligation=0):
    """Explicit preexisting timeline; production forecast excludes flexible expenses."""
    balance = Decimal(starting)
    entries = [
        ForecastEntry(TODAY, "starting_balance", "account_balance", Decimal(0), balance)
    ]
    for index, expense in enumerate(expenses):
        balance -= expense.amount
        entries.append(
            ForecastEntry(
                expense.next_due_date,
                "flexible_expense",
                expense.description,
                expense.amount.copy_negate(),
                balance,
                index,
            )
        )
    if obligation:
        balance -= Decimal(obligation)
        entries.append(
            ForecastEntry(
                LATER,
                "pending_payment",
                "required bill",
                Decimal(-obligation),
                balance,
                0,
            )
        )
    return BalanceForecast(TODAY, LATER, None, tuple(entries))


def test_no_changes_required():
    profile = FinancialProfile(account_balance=Decimal(100))
    forecast = forecast_balance(profile, TODAY, LATER)
    result = evaluate_spending_adjustments(
        profile, forecast, Decimal(20), PurchaseRequest(Decimal(50))
    )
    assert result.feasible is True
    assert result.spending_changes_needed == ()
    assert result.adjusted_balances == (Decimal(50),)


def test_one_exact_reduction_and_input_purity():
    expense = RecurringExpense(Decimal(60), "entertainment", next_due_date=FIRST)
    profile = FinancialProfile(
        account_balance=Decimal(150), flexible_expenses=[expense]
    )
    forecast = budgeted_forecast(150, [expense])
    candidate = PurchaseRequest(Decimal(80))
    original = deepcopy((profile, forecast, candidate))
    result = evaluate_spending_adjustments(profile, forecast, Decimal(20), candidate)
    assert result.feasible is True
    (change,) = result.spending_changes_needed
    assert change.expense_name == "entertainment"
    assert change.affected_date == FIRST
    assert change.original_amount == Decimal(60)
    assert change.reduction_amount == Decimal(10)
    assert change.remaining_amount == Decimal(50)
    assert result.adjusted_balances == (Decimal(70), Decimal(20))
    assert (profile, forecast, candidate) == original


def test_largest_first_uses_partial_final_reduction():
    expenses = [
        RecurringExpense(Decimal(20), "small", next_due_date=FIRST),
        RecurringExpense(Decimal(50), "large", next_due_date=FIRST),
    ]
    profile = FinancialProfile(flexible_expenses=expenses)
    forecast = budgeted_forecast(200, expenses, obligation=40)
    result = evaluate_spending_adjustments(
        profile, forecast, Decimal(20), PurchaseRequest(Decimal(100))
    )
    assert result.feasible is True
    (change,) = result.spending_changes_needed
    assert change.expense_name == "large"
    assert change.reduction_amount == Decimal(30)
    assert change.remaining_amount == Decimal(20)


def test_multiple_reductions_and_final_validation():
    expenses = [
        RecurringExpense(Decimal(30), "first", next_due_date=FIRST),
        RecurringExpense(Decimal(40), "second", next_due_date=FIRST),
    ]
    profile = FinancialProfile(flexible_expenses=expenses)
    forecast = budgeted_forecast(200, expenses, obligation=80)
    result = evaluate_spending_adjustments(
        profile, forecast, Decimal(20), PurchaseRequest(Decimal(100))
    )
    assert result.feasible is True
    assert {
        change.expense_name: change.reduction_amount
        for change in result.spending_changes_needed
    } == {"first": Decimal(30), "second": Decimal(40)}
    assert all(balance >= Decimal(20) for balance in result.adjusted_balances)
    assert all(
        change.remaining_amount == 0 for change in result.spending_changes_needed
    )


def test_insufficient_flexible_spending_returns_no_partial_proposal():
    expense = RecurringExpense(Decimal(20), "fun", next_due_date=FIRST)
    result = evaluate_spending_adjustments(
        FinancialProfile(flexible_expenses=[expense]),
        budgeted_forecast(150, [expense], obligation=70),
        Decimal(20),
        PurchaseRequest(Decimal(80)),
    )
    assert result.feasible is False
    assert result.spending_changes_needed == ()
    assert result.adjusted_balances == ()


def test_essential_and_pending_expenses_are_never_reduced():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        essential_expenses=[
            RecurringExpense(
                Decimal(50), "rent", category="flexible", next_due_date=FIRST
            )
        ],
        pending_payments=[PendingPayment(Decimal(20), LATER)],
    )
    result = evaluate_spending_adjustments(
        profile,
        forecast_balance(profile, TODAY, LATER),
        Decimal(20),
        PurchaseRequest(Decimal(50)),
    )
    assert result.feasible is False
    assert result.spending_changes_needed == ()


@pytest.mark.parametrize(
    "description, category",
    [("rent", None), ("utilities", "flexible"), ("debt", "required_debt")],
)
def test_protected_expenses_do_not_qualify_even_in_flexible_collection(
    description, category
):
    expense = RecurringExpense(
        Decimal(60), description, category=category, next_due_date=FIRST
    )
    result = evaluate_spending_adjustments(
        FinancialProfile(flexible_expenses=[expense]),
        budgeted_forecast(150, [expense]),
        Decimal(20),
        PurchaseRequest(Decimal(80)),
    )
    assert result.feasible is False


def test_later_expense_cannot_repair_starting_shortfall():
    expense = RecurringExpense(Decimal(60), "fun", next_due_date=LATER)
    result = evaluate_spending_adjustments(
        FinancialProfile(flexible_expenses=[expense]),
        budgeted_forecast(100, [expense]),
        Decimal(20),
        PurchaseRequest(Decimal(90)),
    )
    assert result.feasible is False


def test_same_day_later_expense_cannot_repair_earlier_event():
    expense = RecurringExpense(Decimal(30), "fun", next_due_date=FIRST)
    forecast = BalanceForecast(
        TODAY,
        LATER,
        None,
        (
            ForecastEntry(
                TODAY, "starting_balance", "account_balance", Decimal(0), Decimal(100)
            ),
            ForecastEntry(
                FIRST, "pending_payment", "bill", Decimal(-60), Decimal(40), 0
            ),
            ForecastEntry(
                FIRST, "flexible_expense", "fun", Decimal(-30), Decimal(10), 0
            ),
        ),
    )
    result = evaluate_spending_adjustments(
        FinancialProfile(flexible_expenses=[expense]),
        forecast,
        Decimal(20),
        PurchaseRequest(Decimal(30)),
    )
    assert result.feasible is False


def test_existing_plan_future_payment_occurs_after_events():
    expense = RecurringExpense(Decimal(60), "fun", next_due_date=FIRST)
    forecast = budgeted_forecast(100, [expense])
    plan = PaymentPlan([PendingPayment(Decimal(50), FIRST)], Decimal(50))
    result = evaluate_spending_adjustments(
        FinancialProfile(flexible_expenses=[expense]), forecast, Decimal(20), plan
    )
    assert result.feasible is True
    assert result.spending_changes_needed[0].reduction_amount == Decimal(30)
    assert result.adjusted_balances == (Decimal(100), Decimal(20))


def test_unrepresented_flexible_expenses_never_create_savings():
    profile = FinancialProfile(
        account_balance=Decimal(100),
        flexible_expenses=[RecurringExpense(Decimal(100), "fun", next_due_date=FIRST)],
        pending_payments=[PendingPayment(Decimal(60), LATER)],
    )
    result = evaluate_spending_adjustments(
        profile,
        forecast_balance(profile, TODAY, LATER),
        Decimal(20),
        PurchaseRequest(Decimal(50)),
    )
    assert result.feasible is False


def test_explicit_discretionary_recurring_category_qualifies():
    profile = FinancialProfile(
        account_balance=Decimal(150),
        recurring_expenses=[
            RecurringExpense(
                Decimal(60), "fun", category="discretionary", next_due_date=FIRST
            )
        ],
    )
    result = evaluate_spending_adjustments(
        profile,
        forecast_balance(profile, TODAY, LATER),
        Decimal(20),
        PurchaseRequest(Decimal(80)),
    )
    assert result.feasible is True
    assert result.spending_changes_needed[0].reduction_amount == Decimal(10)


def test_stable_name_tie_breaker():
    expenses = [
        RecurringExpense(Decimal(20), "z", next_due_date=FIRST),
        RecurringExpense(Decimal(20), "a", next_due_date=FIRST),
    ]
    profile = FinancialProfile(flexible_expenses=expenses)
    forecast = budgeted_forecast(150, expenses, obligation=30)
    candidate = PurchaseRequest(Decimal(70))
    result = evaluate_spending_adjustments(profile, forecast, Decimal(20), candidate)
    assert result.feasible is True
    assert result.spending_changes_needed[0].expense_name == "a"
    assert result.spending_changes_needed[0].reduction_amount == Decimal(10)
    assert (
        evaluate_spending_adjustments(profile, forecast, Decimal(20), candidate)
        == result
    )


def test_exact_decimal_reduction_under_low_precision():
    expense = RecurringExpense(Decimal("60.01"), "fun", next_due_date=FIRST)
    profile = FinancialProfile(flexible_expenses=[expense])
    forecast = budgeted_forecast(150, [expense])
    with localcontext() as context:
        context.prec = 2
        result = evaluate_spending_adjustments(
            profile, forecast, Decimal(20), PurchaseRequest(Decimal(80))
        )
    assert result.spending_changes_needed[0].reduction_amount == Decimal("10.01")


def test_invalid_plan_total_is_rejected():
    profile = FinancialProfile(account_balance=Decimal(100))
    with pytest.raises(ValueError, match="total_amount"):
        evaluate_spending_adjustments(
            profile,
            forecast_balance(profile, TODAY, LATER),
            Decimal(20),
            PaymentPlan([PendingPayment(Decimal(10), TODAY)], Decimal(20)),
        )
