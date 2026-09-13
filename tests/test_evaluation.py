"""Synthetic evaluator cases only; no invented challenge reference files."""

import csv
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

import pytest
from evaluation import evaluate_predictions, format_summary
from evaluation.__main__ import main
from models import DecisionResult
from output import write_decisions_csv


def row(identifier="synthetic-a", **values):
    return {"request_id": identifier, "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment", "amount_safe_to_pay": "100",
            "earliest_date_for_full_payment": "2026-09-12", **values}


def test_perfect_predictions_and_input_preservation():
    expected = [row(), row("synthetic-b", amount_safe_to_pay="12.50")]
    predictions = [row("synthetic-b", amount_safe_to_pay=Decimal("12.5")), row()]
    original = deepcopy((predictions, expected))
    report = evaluate_predictions(predictions, expected)
    assert report.case_count == len(expected)
    assert report.evaluated_case_count == len(expected)
    assert report.skipped_case_count == 0
    assert report.mismatches == ()
    for metric in report.fields.values():
        assert metric.accuracy == Decimal(1)
        assert metric.evaluated_count == len(expected)
    assert report.fields["amount_safe_to_pay"].mean_absolute_error == Decimal(0)
    assert (predictions, expected) == original


def test_categorical_mismatch_details():
    report = evaluate_predictions([row(affordability_status="not_affordable")], [row()])
    mismatch, = report.mismatches
    assert mismatch.request_id == "synthetic-a"
    assert mismatch.field == "affordability_status"
    assert mismatch.expected_value == "affordable_now"
    assert mismatch.predicted_value == "not_affordable"
    assert mismatch.numeric_error is None
    assert report.fields[mismatch.field].accuracy == Decimal(0)


def test_exact_monetary_error_and_mean_independent_of_precision():
    expected = [row(amount_safe_to_pay="1234567890.12345"), row("synthetic-b", amount_safe_to_pay="10")]
    predictions = [row(amount_safe_to_pay="1234567890.12344"), row("synthetic-b", amount_safe_to_pay="8")]
    with localcontext() as context:
        context.prec = 2
        report = evaluate_predictions(predictions, expected)
    metric = report.fields["amount_safe_to_pay"]
    assert metric.mean_absolute_error == Decimal("1.000005")
    assert metric.numeric_error_count == len(expected)
    assert [mismatch.numeric_error for mismatch in report.mismatches] == [Decimal("0.00001"), Decimal(2)]


def test_missing_expected_skipped_even_if_prediction_is_invalid():
    expected = [{"request_id": "synthetic-a", "amount_safe_to_pay": "", "affordability_status": None}]
    report = evaluate_predictions([row(amount_safe_to_pay="invalid")], expected)
    assert report.skipped_case_count == 1
    assert report.mismatches == ()
    for metric in report.fields.values():
        assert metric.evaluated_count == 0
        assert metric.skipped_count == 1
        assert metric.accuracy is None
    assert report.fields["amount_safe_to_pay"].mean_absolute_error is None
    assert "n/a" in format_summary(report)


def test_missing_predictions_are_mismatches_not_skips():
    report = evaluate_predictions([], [row()])
    assert report.missing_prediction_ids == ("synthetic-a",)
    assert len(report.mismatches) == len(report.fields)
    for metric in report.fields.values():
        assert metric.evaluated_count == 1
        assert metric.skipped_count == 0
        assert metric.missing_prediction_count == 1
        assert metric.accuracy == Decimal(0)
    assert report.fields["amount_safe_to_pay"].numeric_error_count == 0
    assert report.fields["amount_safe_to_pay"].mean_absolute_error is None
    assert all(mismatch.predicted_value is None for mismatch in report.mismatches)


def test_field_specific_denominators_and_extra_predictions():
    expected = [row(), {"request_id": "synthetic-b", "recommended_payment_method": "wait"}]
    predictions = [row("extra"), row("synthetic-b", recommended_payment_method="full_payment"), row()]
    report = evaluate_predictions(predictions, expected)
    assert report.unexpected_prediction_ids == ("extra",)
    assert report.case_count == len(expected)
    metric = report.fields["recommended_payment_method"]
    assert metric.evaluated_count == len(expected)
    assert metric.accuracy == Decimal("0.5")
    assert report.fields["amount_safe_to_pay"].evaluated_count == 1
    assert report.fields["amount_safe_to_pay"].skipped_count == 1


def test_dates_exact_match_and_missing_date():
    report = evaluate_predictions([row(earliest_date_for_full_payment=date(2026, 9, 12))], [row()])
    assert report.fields["earliest_date_for_full_payment"].accuracy == Decimal(1)
    report = evaluate_predictions([row(earliest_date_for_full_payment="")], [row()])
    assert report.mismatches[0].field == "earliest_date_for_full_payment"
    report = evaluate_predictions([row(earliest_date_for_full_payment="2026-09-13")], [row()])
    assert report.fields["earliest_date_for_full_payment"].accuracy == Decimal(0)


@pytest.mark.parametrize("side", ["predicted", "expected"])
def test_duplicate_ids_raise_clear_error(side):
    predicted = [row(), row()] if side == "predicted" else [row()]
    expected = [row(), row()] if side == "expected" else [row()]
    with pytest.raises(ValueError, match="duplicate request_id"):
        evaluate_predictions(predicted, expected)


def test_empty_inputs_and_deterministic_summary():
    report = evaluate_predictions([], [])
    assert report.case_count == 0
    assert report.evaluated_case_count == 0
    assert report.mismatches == ()
    assert format_summary(report).startswith("Cases: 0; evaluated: 0; skipped: 0")
    expected = [row(), row("synthetic-b")]
    predictions = [row("synthetic-b", amount_safe_to_pay="90"), row()]
    assert evaluate_predictions(predictions, expected) == evaluate_predictions(
        list(reversed(predictions)), list(reversed(expected)))
    assert format_summary(evaluate_predictions(predictions, expected)) == format_summary(
        evaluate_predictions(predictions, expected))


def test_existing_writer_format_and_standalone_cli(tmp_path, capsys):
    decision = DecisionResult(Decimal(100), "affordable_now", "pay_in_full",
                              earliest_date_for_full_payment=date(2026, 9, 12))
    predicted = tmp_path / "predictions.csv"
    expected = tmp_path / "reference.csv"
    write_decisions_csv([("synthetic-a", decision)], predicted)
    expected.write_bytes(predicted.read_bytes())
    with predicted.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert evaluate_predictions(rows, rows).mismatches == ()
    main(["--predictions", str(predicted), "--expected", str(expected)])
    summary = capsys.readouterr().out
    assert "accuracy 1.0000 (1/1)" in summary
    assert "MAE 0 (numeric pairs 1)" in summary


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", 1.5])
def test_invalid_money_is_validation_error(value):
    with pytest.raises(ValueError, match="money"):
        evaluate_predictions([row(amount_safe_to_pay=value)], [row()])


def test_missing_amount_does_not_artificially_improve_exact_accuracy():
    report = evaluate_predictions([row(), row("synthetic-b", amount_safe_to_pay=None)],
                                  [row(), row("synthetic-b")])
    metric = report.fields["amount_safe_to_pay"]
    assert metric.accuracy == Decimal("0.5")
    assert metric.mean_absolute_error == Decimal(0)
    assert metric.numeric_error_count == 1
    assert metric.missing_prediction_count == 1
