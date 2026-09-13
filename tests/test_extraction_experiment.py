"""Synthetic model comparison; both configurations are mocked and offline."""

import json
import os
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from unittest.mock import Mock

import pytest
from evaluation.extraction_experiment import (
    ExperimentCase,
    canonical_facts,
    compare_configurations,
    format_experiment_summary,
    main,
    synthetic_cases,
)
from extraction.media_extractor import DEFAULT_MODEL

CONFIGURATION_COUNT = 2
CLI_ERROR = 2

def response(facts):
    return json.dumps({"facts": [
        {field: (str(value) if field == "amount" and value is not None
                 else value.isoformat() if field == "date" and value is not None else value)
         for field, value in vars(fact).items() if field != "source_path"}
        for fact in facts
    ]})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr("evaluation.extraction_experiment._openai_request",
                        Mock(side_effect=AssertionError("live API prohibited")))


def perfect_request(**kwargs):
    text = kwargs["content"][0]["text"]
    case = next(case for case in synthetic_cases() if case.text == text)
    return response(reversed(case.expected))


def test_perfect_both_models_and_unchanged_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-production")
    request = Mock(side_effect=perfect_request)
    report = compare_configurations("explicit-alternative", request=request)
    assert report.winner == "tie"
    assert [config.model for config in report.configurations] == ["configured-production", "explicit-alternative"]
    assert request.call_count == CONFIGURATION_COUNT * len(synthetic_cases())
    assert request.call_args_list[0].kwargs["schema"] == request.call_args_list[-1].kwargs["schema"]
    for config in report.configurations:
        assert config.case_count == len(synthetic_cases())
        assert config.case_accuracy == config.schema_valid_rate == config.mean_field_accuracy == 1
        assert config.failures == config.mismatches == ()
        assert config.unsupported_fact_count == config.malformed_response_count == 0
        assert all(metric.evaluated == sum(len(case.expected) for case in synthetic_cases())
                   for metric in config.fields.values())
    assert os.environ["OPENAI_MODEL"] == "configured-production"


def test_alternative_wins_case_accuracy_and_reports_fields():
    def request(**kwargs):
        if kwargs["model"] != DEFAULT_MODEL:
            return perfect_request(**kwargs)
        return '{"facts": []}'
    report = compare_configurations("alternative", request=request)
    assert report.winner == "B"
    a, b = report.configurations
    assert a.exact_case_count == 1
    assert a.case_accuracy == Fraction(1, 7)
    assert a.schema_valid_rate == b.schema_valid_rate == 1
    assert len(a.mismatches) == len(synthetic_cases()) - 1
    assert a.fields["amount"].matched == 0
    assert "Winner: B" in format_experiment_summary(report)


def test_amount_error_missing_null_and_extra_duplicate_penalties():
    case = synthetic_cases()[0]
    altered = replace(case.expected[0], amount=Decimal(121))
    request = Mock(side_effect=[response([altered]), response([*case.expected, *case.expected])])
    a, b = compare_configurations("alternative", cases=[case], request=request).configurations
    assert a.unsupported_fact_count == b.unsupported_fact_count == 1
    assert a.fields["amount"].accuracy == 0
    assert a.fields["kind"].accuracy == 1
    assert b.fields["kind"].accuracy == Fraction(1, 2)
    missing = synthetic_cases()[4]
    wrong = replace(missing.expected[0], amount=Decimal(0))
    report = compare_configurations("alternative", cases=[missing], request=Mock(return_value=response([wrong])))
    assert report.configurations[0].fields["amount"].accuracy == 0
    assert report.configurations[0].fields["date"].accuracy == 1


@pytest.mark.parametrize("bad", ["not JSON", '{"facts": [], "extra": 1}'])
def test_malformed_failure_safe_and_later_cases_continue(bad):
    cases = synthetic_cases()[:2]
    request = Mock(side_effect=[bad, response(cases[1].expected),
                               response(cases[0].expected), response(cases[1].expected)])
    report = compare_configurations("alternative", cases=cases, request=request)
    a, b = report.configurations
    assert a.malformed_response_count == 1
    assert a.failures[0].code == "invalid_model_output"
    assert a.schema_valid_rate == Fraction(1, 2)
    assert a.exact_case_count == 1 and b.exact_case_count == len(cases)
    assert request.call_count == CONFIGURATION_COUNT * len(cases)


def test_timeout_is_failure_not_malformed_or_raw_payload():
    request = Mock(side_effect=TimeoutError("authorization sensitive-payload"))
    report = compare_configurations("alternative", cases=synthetic_cases()[:1], request=request)
    for config in report.configurations:
        assert config.failures[0].code == "api_error"
        assert config.malformed_response_count == 0
        assert config.case_accuracy == config.schema_valid_rate == 0
    assert "sensitive-payload" not in format_experiment_summary(report)


def test_field_accuracy_breaks_equal_case_scores():
    case = synthetic_cases()[0]
    slightly_wrong = replace(case.expected[0], amount=Decimal(121))
    very_wrong = replace(slightly_wrong, certainty="uncertain", kind="purchase")
    report = compare_configurations("alternative", cases=[case], request=Mock(side_effect=[
        response([very_wrong]), response([slightly_wrong])]))
    assert report.winner == "B"
    a, b = report.configurations
    assert a.case_accuracy == b.case_accuracy == 0
    assert a.mean_field_accuracy < b.mean_field_accuracy


def test_schema_rate_breaks_equal_case_and_field_scores():
    case = synthetic_cases()[0]
    report = compare_configurations("alternative", cases=[case], request=Mock(side_effect=[
        "bad JSON", '{"facts": []}']))
    a, b = report.configurations
    assert a.case_accuracy == b.case_accuracy == a.mean_field_accuracy == b.mean_field_accuracy == 0
    assert report.winner == "B"


def test_canonical_sort_preserves_precision_duplicates_and_inputs():
    facts = synthetic_cases()[-1].expected
    equivalent = replace(facts[0], amount=Decimal("120.000"), evidence="different excerpt")
    assert canonical_facts([facts[1], equivalent]) == canonical_facts(facts)
    duplicated = [facts[0], facts[0]]
    assert len(canonical_facts(duplicated)) == len(duplicated)
    assert facts == synthetic_cases()[-1].expected


def test_empty_and_duplicate_cases():
    report = compare_configurations("alternative", cases=[], request=Mock())
    assert report.winner == "tie"
    assert report.configurations[0].case_count == 0
    case = synthetic_cases()[0]
    with pytest.raises(ValueError, match="duplicate"):
        compare_configurations("alternative", cases=[case, case], request=Mock())
    assert isinstance(case, ExperimentCase)


@pytest.mark.parametrize("alternative", [None, "", " "])
def test_missing_alternative_never_calls_provider(alternative):
    with pytest.raises(ValueError, match="alternative"):
        compare_configurations(alternative, live=True)


def test_live_guard_key_and_same_model():
    with pytest.raises(ValueError, match="opt-in"):
        compare_configurations("alternative")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        compare_configurations("alternative", live=True)
    with pytest.raises(ValueError, match="differ"):
        compare_configurations(DEFAULT_MODEL, request=Mock())


def test_cli_no_live_default_and_help(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_EXPERIMENT_MODEL", "alternative")
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == CLI_ERROR
    assert "--live" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--alternative-model" in capsys.readouterr().out


def test_classification_certainty_and_date_metrics_and_a_wins():
    cases = synthetic_cases()[2:4]
    wrong = [replace(case.expected[0], category="unknown", certainty="uncertain", date=None)
             for case in cases]
    report = compare_configurations("alternative", cases=cases, request=Mock(side_effect=[
        response(cases[0].expected), response(cases[1].expected),
        response([wrong[0]]), response([wrong[1]])]))
    a, b = report.configurations
    assert report.winner == "A"
    assert a.case_accuracy == 1
    assert b.fields["category"].accuracy == b.fields["certainty"].accuracy == 0
    assert b.fields["date"].accuracy == Fraction(1, 2)
    assert b.mismatches[0].case_id == cases[0].case_id


def test_cli_opt_in_environment_model_and_secret_redaction(monkeypatch, capsys):
    # CLI orchestration is mocked even when --live is specified.
    report = compare_configurations("alternative", request=perfect_request)
    runner = Mock(return_value=report)
    monkeypatch.setattr("evaluation.extraction_experiment.compare_configurations", runner)
    monkeypatch.setenv("OPENAI_EXPERIMENT_MODEL", "alternative")
    main(["--live"])
    runner.assert_called_once_with("alternative", live=True)
    assert "Winner: tie" in capsys.readouterr().out
    monkeypatch.setenv("OPENAI_API_KEY", "representative-test-secret")
    secret_fact = replace(synthetic_cases()[0].expected[0], description="representative-test-secret")
    secret_report = compare_configurations("alternative", cases=synthetic_cases()[:1],
                                           request=Mock(return_value=response([secret_fact])))
    assert "representative-test-secret" not in format_experiment_summary(secret_report)
