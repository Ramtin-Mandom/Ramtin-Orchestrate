"""Offline, isolated integration of existing loaders, finance and CSV output."""

import csv
import json
import logging
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pipeline
import pytest
from extraction.media_extractor import ExtractedFact, FileExtraction
from loaders import load_requests_csv
from main import main
from models import DecisionResult
from models.decision_values import INSTALLMENTS, PAY_IN_FULL
from output import OUTPUT_COLUMNS
from pipeline import PipelineError, run_pipeline

TODAY = date(2026, 9, 12)
LATER = date(2026, 9, 20)
CLI_ERROR_EXIT_CODE = 2


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    request = Mock(side_effect=AssertionError("live requests are forbidden"))
    monkeypatch.setattr("extraction.media_extractor._openai_request", request)
    yield
    request.assert_not_called()


def write_input(tmp_path, rows):
    path = tmp_path / "requests.csv"
    defaults = {"request_id": "0001", "amount": "80", "request_date": TODAY.isoformat(),
                "account_balance": "100", "minimum_balance": "20", "currency": "CAD"}
    records = [{**defaults, **row} for row in rows]
    headers = list(defaults) + sorted({key for row in records for key in row} - set(defaults))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)
    return path


def read_output(path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(OUTPUT_COLUMNS)
        return list(reader)


def test_one_request_end_to_end(tmp_path):
    source = write_input(tmp_path, [{}])
    output = tmp_path / "output.csv"
    extractor = Mock(side_effect=AssertionError("no media"))
    results = run_pipeline(source, tmp_path / "media", output, extractor=extractor)
    result, = results
    assert isinstance(result, DecisionResult)
    assert result.recommended_payment_method == PAY_IN_FULL
    assert result.earliest_date_for_full_payment == TODAY
    assert result.decision_explanation
    row, = read_output(output)
    assert row["request_id"] == "0001"
    assert row["amount_safe_to_pay"] == "80"
    assert row["recommended_payment_method"] == "full_payment"
    assert row["decision_explanation"] == result.decision_explanation
    extractor.assert_not_called()


def test_multiple_requests_in_order_and_row_dates(tmp_path):
    source = write_input(tmp_path, [{"request_id": "003"},
                                    {"request_id": "001", "request_date": LATER.isoformat()}])
    output = tmp_path / "output.csv"
    results = run_pipeline(source, tmp_path / "media", output, extractor=Mock())
    assert [row["request_id"] for row in read_output(output)] == ["003", "001"]
    assert [result.earliest_date_for_full_payment for result in results] == [TODAY, LATER]


def test_no_media_never_initializes_default_extractor(tmp_path, monkeypatch):
    source = write_input(tmp_path, [{}])
    extractor = Mock(side_effect=AssertionError("should never initialize"))
    monkeypatch.setattr(pipeline, "extract_media", extractor)
    run_pipeline(source, tmp_path / "missing-media", tmp_path / "output.csv")
    extractor.assert_not_called()


def test_mocked_income_is_merged_before_forecasting(tmp_path, monkeypatch):
    source = write_input(tmp_path, [{"amount": "100"}])
    media_root = tmp_path / "media"
    media_root.mkdir()
    text = media_root / "0001.txt"
    text.write_text("Confirmed salary 25", encoding="utf-8")
    fact = ExtractedFact(text.resolve(), "income", "confirmed", "Confirmed salary 25",
                         Decimal(25), "work", LATER, None, None, None)
    extractor = Mock(return_value=(FileExtraction(text.resolve(), (fact,)),))
    forecast = Mock(wraps=pipeline.forecast_balance)
    monkeypatch.setattr(pipeline, "forecast_balance", forecast)
    output = tmp_path / "output.csv"
    result, = run_pipeline(source, media_root, output, extractor=extractor)
    merged_profile = forecast.call_args.args[0]
    assert merged_profile.incomes == [fact.record]
    assert forecast.call_args.args[2] == date(2026, 12, 11)
    assert result.recommended_payment_method == INSTALLMENTS
    assert read_output(output)[0]["payment_plan"] == "2026-09-12:80|2026-09-20:20"
    extractor.assert_called_once()
    assert extractor.call_args.args[0].files[0].path == text.resolve()


@pytest.mark.parametrize("raises", [False, True])
def test_api_failure_recovers_and_continues_batch(tmp_path, raises, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("buy_or_wait"), "propagate", True)
    source = write_input(tmp_path, [{"request_id": "0001"}, {"request_id": "0002"}])
    media_root = tmp_path / "media"
    media_root.mkdir()
    text = media_root / "0001.txt"
    text.write_text("media", encoding="utf-8")
    extractor = Mock(side_effect=TimeoutError("secret authorization details")) if raises else Mock(
        return_value=(FileExtraction(text, error="api_error"),))
    output = tmp_path / "output.csv"
    results = run_pipeline(source, media_root, output, extractor=extractor)
    assert [result.recommended_payment_method for result in results] == [PAY_IN_FULL, PAY_IN_FULL]
    assert [row["request_id"] for row in read_output(output)] == ["0001", "0002"]
    stderr = caplog.text
    assert "request 1:" in stderr
    assert "secret" not in stderr
    assert "authorization" not in stderr


def test_partial_media_failure_retains_successful_facts(tmp_path):
    source = write_input(tmp_path, [{"amount": "100"}])
    media_root = tmp_path / "media"
    media_root.mkdir()
    text = media_root / "0001.txt"
    text.write_text("income", encoding="utf-8")
    fact = ExtractedFact(text, "income", "confirmed", "income", Decimal(25), "work",
                         LATER, None, None, None)
    extractor = Mock(return_value=[FileExtraction(text, error="invalid_model_output"),
                                  FileExtraction(text, (fact,))])
    warnings = []
    result, = run_pipeline(source, media_root, tmp_path / "output.csv", extractor=extractor,
                           on_warning=warnings.append)
    assert result.recommended_payment_method == INSTALLMENTS
    assert warnings


def test_missing_key_with_media_is_recoverable_without_live_call(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(logging.getLogger("buy_or_wait"), "propagate", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = write_input(tmp_path, [{}])
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "0001.png").write_bytes(b"not sent to API")
    results = run_pipeline(source, media_root, tmp_path / "output.csv")
    assert results[0].recommended_payment_method == PAY_IN_FULL
    assert "media extraction failed" in caplog.text


def test_missing_and_unsupported_media_warn_and_continue(tmp_path):
    source = write_input(tmp_path, [{"media_paths": '["missing.txt", "file.bin"]'}])
    (tmp_path / "file.bin").write_bytes(b"unsupported")
    extractor = Mock()
    warnings = []
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=extractor,
                           on_warning=warnings.append)
    assert results == []
    assert "unsupported" in results.failures[0].message
    extractor.assert_not_called()


@pytest.mark.parametrize("row,message", [
    ({"amount": "invalid"}, "invalid required purchase data"),
    ({"account_balance": ""}, "account_balance is required"),
    ({"minimum_balance": ""}, "explicit minimum or preferred"),
    ({"request_id": ""}, "request_id is required"),
    ({"request_date": ""}, "request_date"),
    ({"incomes": "not JSON"}, "invalid structured financial data"),
])
def test_invalid_required_data_is_omitted_without_ai_or_fabricated_output(tmp_path, row, message):
    source = write_input(tmp_path, [{}, {"request_id": "0002", **row}])
    output = tmp_path / "output.csv"
    output.write_text("existing", encoding="utf-8")
    extractor = Mock()
    results = run_pipeline(source, tmp_path, output, extractor=extractor)
    assert len(results) == 1
    assert message in results.failures[0].message
    extractor.assert_not_called()
    assert len(read_output(output)) == 1


def test_explicit_date_and_horizon_are_configurable(tmp_path, monkeypatch):
    source = write_input(tmp_path, [{"request_date": ""}])
    forecast = Mock(wraps=pipeline.forecast_balance)
    monkeypatch.setattr(pipeline, "forecast_balance", forecast)
    run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", as_of_date=LATER,
                 horizon_days=10, extractor=Mock())
    assert forecast.call_args.args[1:] == (LATER, date(2026, 9, 30))


def test_public_api_call_order(tmp_path, monkeypatch):
    source = write_input(tmp_path, [{}])
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "0001.txt").write_text("unrelated", encoding="utf-8")
    order = []

    def traced(name, function):
        def call(*args, **kwargs):
            order.append(name)
            return function(*args, **kwargs)
        return call

    names = ["load_requests_csv", "normalize_request", "discover_request_media", "merge_profile",
             "forecast_balance", "decide_purchase", "attach_decision_explanation", "write_decisions_csv"]
    for name in names:
        monkeypatch.setattr(pipeline, name, traced(name, getattr(pipeline, name)))
    extractor = traced("extract_media", Mock(return_value=()))
    run_pipeline(source, media_root, tmp_path / "output.csv", extractor=extractor)
    assert order == [*names[:3], "extract_media", *names[3:]]


def test_empty_input_writes_header_only(tmp_path):
    source = write_input(tmp_path, [])
    output = tmp_path / "output.csv"
    assert run_pipeline(source, tmp_path, output, extractor=Mock()) == []
    assert read_output(output) == []


def test_cli_run_and_invalid_args(tmp_path, capsys):
    source = write_input(tmp_path, [{"request_date": ""}])
    output = tmp_path / "output.csv"
    main(["--mode", "legacy", "--input", str(source), "--media-root", str(tmp_path / "media"),
          "--output", str(output), "--as-of-date", "2026-09-12", "--horizon-days", "90"])
    assert "Wrote 1 decisions" in capsys.readouterr().out
    assert read_output(output)[0]["request_id"] == "0001"
    with pytest.raises(SystemExit) as error:
        main(["--as-of-date", "invalid"])
    assert error.value.code == CLI_ERROR_EXIT_CODE


def test_incompatible_spending_identifiers_fail_without_inventing_source_ids(tmp_path):
    source = write_input(tmp_path, [{"account_balance": "150", "amount": "100",
                                   "recurring_expenses": json.dumps([{
                                       "amount": "60", "description": "fun",
                                       "category": "discretionary", "next_due_date": "2026-09-15",
                                   }])}])
    results = run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", extractor=Mock())
    assert results == []
    assert results.failures[0].stage == "output_validation"


def test_duplicate_ids_and_same_input_output_fail(tmp_path):
    source = write_input(tmp_path, [{}, {}])
    with pytest.raises(PipelineError, match="unique"):
        run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=Mock())
    with pytest.raises(PipelineError, match="differ"):
        run_pipeline(source, tmp_path, source, extractor=Mock())


@pytest.mark.parametrize("days", [0, -1, True, 1.5])
def test_invalid_horizon_fails_before_extraction(tmp_path, days):
    source = write_input(tmp_path, [{}])
    extractor = Mock()
    with pytest.raises(PipelineError, match="positive integer"):
        run_pipeline(source, tmp_path, tmp_path / "output.csv", horizon_days=days, extractor=extractor)
    extractor.assert_not_called()


def test_cli_required_data_error_is_clear_and_safe(tmp_path, capsys):
    source = write_input(tmp_path, [{"amount": "private invalid value"}])
    with pytest.raises(SystemExit) as error:
        main(["--mode", "legacy", "--input", str(source), "--output", str(tmp_path / "output.csv")])
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "failed requests=1" in stderr
    assert "private invalid value" not in stderr


def test_challenge_request_loader_accepts_authoritative_schema(tmp_path):
    source = tmp_path / "requests.csv"
    source.write_text("request_id,user_id,request_date,request_type,requested_amount,"
                      "desired_completion_date,allows_partial_payment,request_text\n"
                      "0001,user_1,2026-09-12,travel,100,2026-10-01,false,Can I travel?\n",
                      encoding="utf-8")
    request, = load_requests_csv(source)
    assert request.requested_amount == Decimal(100)
    assert request.user_id == "user_1"
