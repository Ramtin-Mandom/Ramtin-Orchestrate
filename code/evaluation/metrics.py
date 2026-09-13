"""Compare only supplied references, matching challenge CSV rows by request ID."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Dict, Iterable, Mapping, Optional, Tuple

from models.money import validate_money

FIELDS = (
    "affordability_status", "recommended_payment_method", "amount_safe_to_pay",
    "earliest_date_for_full_payment",
)
MONEY_FIELD = "amount_safe_to_pay"
DATE_FIELD = "earliest_date_for_full_payment"


@dataclass(frozen=True)
class Mismatch:
    request_id: str
    field: str
    expected_value: object
    predicted_value: object
    numeric_error: Optional[Decimal] = None


@dataclass(frozen=True)
class FieldMetrics:
    evaluated_count: int
    skipped_count: int
    exact_match_count: int
    missing_prediction_count: int
    accuracy: Optional[Decimal]
    numeric_error_count: int = 0
    mean_absolute_error: Optional[Decimal] = None


@dataclass(frozen=True)
class EvaluationReport:
    case_count: int
    evaluated_case_count: int
    skipped_case_count: int
    fields: Dict[str, FieldMetrics]
    mismatches: Tuple[Mismatch, ...]
    missing_prediction_ids: Tuple[str, ...]
    unexpected_prediction_ids: Tuple[str, ...]


def _absent(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _index(rows):
    indexed = {}
    for row in rows:
        identifier = row.get("request_id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("each evaluation row requires a nonempty string request_id")
        if identifier in indexed:
            raise ValueError("duplicate request_id in evaluation input")
        indexed[identifier] = dict(row)
    return indexed


def _value(value, field):
    if field == MONEY_FIELD:
        if not isinstance(value, (str, Decimal)):
            raise ValueError("evaluation money must be a decimal string or Decimal")
        try:
            amount = Decimal(value)
            validate_money("evaluation money", amount)
        except (InvalidOperation, ValueError):
            raise ValueError("evaluation money must be finite and nonnegative") from None
        return amount
    if field == DATE_FIELD:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() == value:
                    return parsed
            except ValueError:
                pass
        raise ValueError("evaluation dates must use YYYY-MM-DD or date objects")
    if not isinstance(value, str):
        raise TypeError("evaluation categorical values must be strings")
    return value


def _error(expected, predicted):
    """Exact subtraction, independent of the caller's Decimal context."""
    exponent = min(expected.as_tuple().exponent, predicted.as_tuple().exponent)
    precision = max(expected.adjusted(), predicted.adjusted()) - exponent + 2
    with localcontext(Context(prec=precision)):
        return (expected - predicted).copy_abs()


def _ratio(numerator, denominator):
    if not denominator:
        return None
    with localcontext(Context(prec=28)):
        return Decimal(numerator) / Decimal(denominator)


def _mean(errors):
    if not errors:
        return None
    exponent = min(error.as_tuple().exponent for error in errors)
    precision = max(error.adjusted() for error in errors) - exponent + len(str(len(errors))) + 2
    with localcontext(Context(prec=max(28, precision))):
        return sum(errors, Decimal(0)) / Decimal(len(errors))


def _field_metrics(field, predicted, expected):
    evaluated = skipped = matched = missing = 0
    errors, mismatches = [], []
    for identifier in sorted(expected):
        reference = expected[identifier].get(field)
        if _absent(reference):
            skipped += 1
            continue
        evaluated += 1
        supplied = predicted.get(identifier, {}).get(field)
        reference_value = _value(reference, field)
        error = None
        if _absent(supplied):
            missing += 1
            predicted_value = None
        else:
            predicted_value = _value(supplied, field)
            if field == MONEY_FIELD:
                error = _error(reference_value, predicted_value)
                errors.append(error)
        if predicted_value == reference_value:
            matched += 1
        else:
            mismatches.append(Mismatch(identifier, field, reference, supplied, error))
    metrics = FieldMetrics(evaluated, skipped, matched, missing, _ratio(matched, evaluated),
                           len(errors), _mean(errors))
    return metrics, mismatches


def evaluate_predictions(
    predictions: Iterable[Mapping[str, object]], expected_results: Iterable[Mapping[str, object]],
) -> EvaluationReport:
    """Evaluate supplied CSV-shaped row mappings without modifying them.

    None, omitted keys, and blank strings are absent; literal 'none' is not an
    absent date. References define the case set. Extra predictions are listed
    separately and cannot affect metrics. Missing predicted fields/rows are
    mismatches, not skips. Money MAE uses only numeric pairs, explicitly counted;
    exact-match accuracy still penalizes missing amounts. Decimal division uses
    deterministic precision (at least 28 digits), without currency rounding.
    """
    predicted, expected = _index(predictions), _index(expected_results)
    metrics, mismatches = {}, []
    for field in FIELDS:
        metrics[field], differences = _field_metrics(field, predicted, expected)
        mismatches.extend(differences)
    evaluated_cases = sum(any(not _absent(row.get(field)) for field in FIELDS)
                          for row in expected.values())
    return EvaluationReport(
        len(expected), evaluated_cases, len(expected) - evaluated_cases, metrics,
        tuple(mismatches), tuple(sorted(set(expected) - set(predicted))),
        tuple(sorted(set(predicted) - set(expected))),
    )


def format_summary(report: EvaluationReport) -> str:
    """Stable text with explicit field denominators and missing-value counts."""
    lines = [(f"Cases: {report.case_count}; evaluated: {report.evaluated_case_count}; "
              f"skipped: {report.skipped_case_count}")]
    for field in FIELDS:
        metric = report.fields[field]
        with localcontext(Context(prec=28)):
            accuracy = "n/a" if metric.accuracy is None else format(metric.accuracy, ".4f")
        text = (f"{field}: accuracy {accuracy} ({metric.exact_match_count}/{metric.evaluated_count}); "
                f"skipped {metric.skipped_count}; missing predictions {metric.missing_prediction_count}")
        if field == MONEY_FIELD:
            mae = "n/a" if metric.mean_absolute_error is None else format(metric.mean_absolute_error, "f")
            text += f"; MAE {mae} (numeric pairs {metric.numeric_error_count})"
        lines.append(text)
    lines.append(f"Mismatches: {len(report.mismatches)}; missing prediction rows: "
                 f"{len(report.missing_prediction_ids)}; extra prediction rows: "
                 f"{len(report.unexpected_prediction_ids)}")
    return "\n".join(lines)
