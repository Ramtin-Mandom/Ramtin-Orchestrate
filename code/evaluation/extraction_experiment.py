"""Opt-in extraction comparison; never imported by the production pipeline."""

import argparse
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, Optional, Tuple

from extraction.media_extractor import (
    DEFAULT_MODEL,
    ExtractedFact,
    _openai_request,
    extract_media,
)
from loaders.media_loader import MediaFile
from logging_config import safe_log_value

FIELDS = ("kind", "amount", "date", "category", "certainty")
CANONICAL_FIELDS = ("description", "kind", "amount", "date", "category", "certainty",
                    "frequency", "payment_method")


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    text: str
    expected: Tuple[ExtractedFact, ...]


@dataclass(frozen=True)
class FieldAccuracy:
    matched: int
    evaluated: int

    @property
    def accuracy(self):
        return Fraction(self.matched, self.evaluated) if self.evaluated else Fraction(0)


@dataclass(frozen=True)
class CaseMismatch:
    case_id: str
    expected: tuple
    predicted: tuple


@dataclass(frozen=True)
class ExperimentFailure:
    case_id: str
    code: str


@dataclass(frozen=True)
class ConfigurationReport:
    name: str
    model: str
    case_count: int
    schema_valid_count: int
    exact_case_count: int
    fields: Dict[str, FieldAccuracy]
    malformed_response_count: int
    unsupported_fact_count: int
    failures: Tuple[ExperimentFailure, ...]
    mismatches: Tuple[CaseMismatch, ...]

    @property
    def schema_valid_rate(self):
        return Fraction(self.schema_valid_count, self.case_count) if self.case_count else Fraction(0)

    @property
    def case_accuracy(self):
        return Fraction(self.exact_case_count, self.case_count) if self.case_count else Fraction(0)

    @property
    def mean_field_accuracy(self):
        return sum((metric.accuracy for metric in self.fields.values()), Fraction(0)) / len(FIELDS)


@dataclass(frozen=True)
class ExperimentReport:
    configurations: Tuple[ConfigurationReport, ConfigurationReport]
    winner: str


def _fact(description, kind="income", amount=None, when=None, **values):
    return ExtractedFact(Path("synthetic"), kind, values.get("certainty", "confirmed"), description,
                         None if amount is None else Decimal(amount), description,
                         None if when is None else date.fromisoformat(when), None,
                         values.get("category"), None)


def synthetic_cases():
    """Public, synthetic references only; no challenge rows or inferred labels."""
    salary = _fact("Salary", amount="120", when="2026-09-15")
    bill = _fact("Utility bill", "pending_payment", "30", "2026-09-16")
    return (
        ExperimentCase("confirmed-income", "Confirmed income: Salary, 120, on 2026-09-15.", (salary,)),
        ExperimentCase("uncertain-income", "Possible income: Bonus, 50, if approved. Date unknown.",
                       (_fact("Bonus", amount="50", certainty="uncertain"),)),
        ExperimentCase("essential-expense", "Essential expense: Rent, 40, on 2026-09-16. Category: essential.",
                       (_fact("Rent", "essential_expense", "40", "2026-09-16", category="essential"),)),
        ExperimentCase("flexible-expense", "Flexible expense: Games, 10. Category: flexible. Date unknown.",
                       (_fact("Games", "flexible_expense", "10", category="flexible"),)),
        ExperimentCase("missing-values", "Confirmed income: Gift. Amount and date unknown.", (_fact("Gift"),)),
        ExperimentCase("irrelevant", "The garden flowers are blue.", ()),
        ExperimentCase("multiple-facts", "Confirmed income: Salary, 120, on 2026-09-15. "
                       "Pending payment: Utility bill, 30, due 2026-09-16.", (salary, bill)),
    )


def canonical_facts(facts):
    """Exclude evidence wording/source paths; retain every financial field.

    Amounts compare as exact Decimals, dates as ISO strings. Ordering uses
    description first, then all other values, with null distinct from text.
    Duplicates remain visible. No fuzzy matching or currency rounding.
    """
    rows = [tuple(getattr(fact, field).isoformat() if field == "date" and getattr(fact, field)
                  else getattr(fact, field) for field in CANONICAL_FIELDS) for fact in facts]
    return tuple(sorted(rows, key=lambda row: tuple(
        (value is not None, "" if value is None else value) for value in row)))


def _evaluate(name, model, cases, root, request):
    matched = {field: 0 for field in FIELDS}
    evaluated = {field: 0 for field in FIELDS}
    valid = exact = malformed = unsupported = 0
    failures, mismatches = [], []

    def configured_request(**kwargs):
        return request(**{**kwargs, "model": model})
    for index, case in enumerate(cases):
        path = root / f"case-{index}.txt"
        path.write_text(case.text, encoding="utf-8")
        descriptor = MediaFile(path, "text", ".txt", "text/plain", path.stat().st_size)
        result, = extract_media([descriptor], request=configured_request)
        expected, predicted = canonical_facts(case.expected), canonical_facts(result.facts)
        if result.error:
            failures.append(ExperimentFailure(case.case_id, result.error))
            malformed += result.error == "invalid_model_output"
        else:
            valid += 1
            exact += expected == predicted
        if result.error or expected != predicted:
            mismatches.append(CaseMismatch(case.case_id, expected, predicted))
        # Unsupported includes altered, invented and duplicate extra financial facts.
        unsupported += sum((Counter(predicted) - Counter(expected)).values())
        # Canonical positional comparison: missing/extra facts penalize every field.
        # Null is an evaluated expected value, not a skip. Failed empty cases also
        # receive one missing slot, so malformed irrelevant output earns no credit.
        slots = max(len(expected), len(predicted), int(bool(result.error)))
        for field in FIELDS:
            position = CANONICAL_FIELDS.index(field)
            evaluated[field] += slots
            matched[field] += sum(
                expected[i][position] == predicted[i][position]
                for i in range(min(len(expected), len(predicted)))
            )
    return ConfigurationReport(name, model, len(cases), valid, exact,
                               {field: FieldAccuracy(matched[field], evaluated[field]) for field in FIELDS},
                               malformed, unsupported, tuple(failures), tuple(mismatches))


def compare_configurations(alternative_model: str, *, cases=None,
                           request: Optional[Callable] = None, live: bool = False) -> ExperimentReport:
    """Inject one request(model, content, schema) callable for both configurations.

    Without an injected request, live=True and an environment key are mandatory.
    Neither OPENAI_MODEL nor any production setting is modified.
    """
    if not isinstance(alternative_model, str) or not alternative_model.strip():
        raise ValueError("an explicit alternative model is required")
    if request is None:
        if not live:
            raise ValueError("live execution requires explicit opt-in or an injected request")
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise ValueError("OPENAI_API_KEY is required for live execution")
        request = _openai_request
    selected = tuple(synthetic_cases() if cases is None else cases)
    if len({case.case_id for case in selected}) != len(selected):
        raise ValueError("duplicate experiment case IDs")
    production = os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL
    alternative = alternative_model.strip()
    if alternative == production:
        raise ValueError("alternative model must differ from production model")
    with TemporaryDirectory(prefix="extraction-experiment-") as directory:
        root = Path(directory)
        reports = (_evaluate("A", production, selected, root, request),
                   _evaluate("B", alternative, selected, root, request))
    ranks = [(r.case_accuracy, r.mean_field_accuracy, r.schema_valid_rate) for r in reports]
    winner = "tie" if ranks[0] == ranks[1] else reports[int(ranks[1] > ranks[0])].name
    return ExperimentReport(reports, winner)


def format_experiment_summary(report):
    lines = []
    for config in report.configurations:
        lines.append(f"{config.name} model={config.model}: cases={config.case_count}; "
                     f"schema-valid={float(config.schema_valid_rate):.4f}; "
                     f"case accuracy={float(config.case_accuracy):.4f}; "
                     f"mean field accuracy={float(config.mean_field_accuracy):.4f}; "
                     f"malformed={config.malformed_response_count}; unsupported={config.unsupported_fact_count}")
        for field, metric in config.fields.items():
            lines.append(f"  {field}: {metric.matched}/{metric.evaluated} ({float(metric.accuracy):.4f})")
        for failure in config.failures:
            lines.append(f"  failure {failure.case_id}: {failure.code}")
        for mismatch in config.mismatches:
            lines.append(f"  mismatch {mismatch.case_id}: expected={mismatch.expected!r}; predicted={mismatch.predicted!r}")
    lines.append(f"Winner: {report.winner}; production configuration unchanged")
    return "\n".join(safe_log_value(line) for line in lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in synthetic extraction model comparison")
    parser.add_argument("--live", action="store_true", help="explicitly enable paid API requests")
    parser.add_argument("--alternative-model", default=os.environ.get("OPENAI_EXPERIMENT_MODEL"))
    args = parser.parse_args(argv)
    try:
        report = compare_configurations(args.alternative_model, live=args.live)
    except ValueError:
        parser.error("require --live, OPENAI_API_KEY, and an explicit alternative model different from production")
    print(format_experiment_summary(report))


if __name__ == "__main__":
    main()
