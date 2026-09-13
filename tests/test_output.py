"""Exact challenge CSV formatting and preservation of completed decisions."""

import csv
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

import pytest
from models import DecisionResult, PaymentPlan, PendingPayment
from models.decision_values import AFFORDABLE_NOW, PAY_IN_FULL
from models.spending import SpendingChange
from output import write_decisions_csv

HEADER = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment",
    "spending_changes_needed", "decision_explanation",
]


def decision(**kwargs):
    values = {"amount_safe_to_pay": Decimal(100), "affordability_status": AFFORDABLE_NOW,
              "recommended_payment_method": PAY_IN_FULL}
    values.update(kwargs)
    return DecisionResult(**values)


def read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


def test_one_decision_and_exact_header(tmp_path):
    path = tmp_path / "output.csv"
    write_decisions_csv([("0007", decision(earliest_date_for_full_payment=date(2026, 9, 7),
                                         decision_explanation="Pay in full."))], path)
    assert read(path) == [HEADER, ["0007", "100", "affordable_now", "full_payment",
                                 "none", "2026-09-07", "none", "Pay in full."]]


def test_order_and_one_row_per_input(tmp_path):
    path = tmp_path / "output.csv"
    results = [("03", decision()), ("01", decision()), ("02", decision())]
    original = deepcopy(results)
    write_decisions_csv(iter(results), str(path))
    rows = read(path)
    assert [row[0] for row in rows[1:]] == [identifier for identifier, _ in results]
    assert len(rows) == len(results) + 1
    assert results == original


def test_optional_fields_and_empty_input(tmp_path):
    path = tmp_path / "output.csv"
    write_decisions_csv([("1", decision(payment_plan=PaymentPlan()))], path)
    assert read(path)[1][4:] == ["none", "", "none", ""]
    write_decisions_csv([], path)
    assert read(path) == [HEADER]


def test_plan_chronological_complete_and_no_mutation(tmp_path):
    plan = PaymentPlan([PendingPayment(Decimal("300.00"), date(2026, 11, 7)),
                        PendingPayment(Decimal(300), date(2026, 9, 7)),
                        PendingPayment(Decimal(300), date(2026, 10, 7))], Decimal(900))
    result = decision(payment_plan=plan, affordability_status="affordable_with_plan",
                      recommended_payment_method="installments")
    original = deepcopy(result)
    path = tmp_path / "output.csv"
    write_decisions_csv([("1", result)], path)
    assert read(path)[1][4] == "2026-09-07:300|2026-10-07:300|2026-11-07:300"
    assert result == original


@pytest.mark.parametrize("amount,expected", [
    ("12.5000", "12.5"), ("1E+5", "100000"), ("0.00001", "0.00001"),
    ("-0.00", "0"), ("1234567890.12345", "1234567890.12345"),
])
def test_exact_money_no_locale_or_rounding(tmp_path, amount, expected):
    path = tmp_path / "output.csv"
    with localcontext() as context:
        context.prec = 2
        write_decisions_csv([("1", decision(amount_safe_to_pay=Decimal(amount)))], path)
    assert read(path)[1][1] == expected


def test_spending_tokens_and_structured_changes(tmp_path):
    changes = [SpendingChange("event_14", "fun", date(2026, 9, 7), Decimal(20), Decimal(20), Decimal(0)),
               SpendingChange("event_21", "coffee", date(2026, 9, 7), Decimal(150), Decimal(50), Decimal(100)),
               "reduce_to:event_22:12.50"]
    path = tmp_path / "output.csv"
    write_decisions_csv([("1", decision(spending_changes_needed=changes))], path)
    assert read(path)[1][6] == "stop:event_14|reduce_to:event_21:100|reduce_to:event_22:12.5"


def test_csv_escaping_and_utf8(tmp_path):
    text = 'Café, "safe"\nKeep the reserve.'
    path = tmp_path / "output.csv"
    write_decisions_csv([("1", decision(decision_explanation=text))], path)
    assert read(path)[1][-1] == text
    assert b'Caf\xc3\xa9' in path.read_bytes()


@pytest.mark.parametrize("method,expected", [
    ("pay_in_full", "full_payment"), ("do_not_proceed", "not_recommended"),
    ("full_payment", "full_payment"), ("partial_payment", "partial_payment"),
    ("installments", "installments"), ("wait", "wait"),
    ("not_recommended", "not_recommended"),
])
def test_payment_label_serialization(tmp_path, method, expected):
    path = tmp_path / "output.csv"
    result = decision(recommended_payment_method=method)
    write_decisions_csv([("1", result)], path)
    assert read(path)[1][3] == expected
    assert result.recommended_payment_method == method


@pytest.mark.parametrize("changes", [
    ["Reduce coffee"], ["stop:recurring_expense:0:1"],
    ["stop:event_1", "reduce_to:event_1:10"],
    ["stop:event_1", "stop:event_2", "stop:event_3", "stop:event_4"],
    ["reduce_to:event_1:-1"],
])
def test_invalid_changes_do_not_overwrite_output(tmp_path, changes):
    path = tmp_path / "output.csv"
    path.write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError):
        write_decisions_csv([("1", decision(spending_changes_needed=changes))], path)
    assert path.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize("kwargs", [
    {"amount_safe_to_pay": None}, {"affordability_status": None},
    {"recommended_payment_method": None},
    {"payment_plan": PaymentPlan([PendingPayment(Decimal(10))])},
])
def test_required_values_not_invented(tmp_path, kwargs):
    with pytest.raises((ValueError, TypeError)):
        write_decisions_csv([("1", decision(**kwargs))], tmp_path / "output.csv")
