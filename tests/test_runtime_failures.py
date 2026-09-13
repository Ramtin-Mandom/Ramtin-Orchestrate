"""Expected failures are isolated without fabricated output or live API calls."""

import csv
import logging
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pipeline
import pytest
from extraction.media_extractor import ExtractedFact, FileExtraction, extract_media
from pipeline import PipelineError, run_pipeline


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    request = Mock(side_effect=AssertionError("live API forbidden"))
    monkeypatch.setattr("extraction.media_extractor._openai_request", request)
    monkeypatch.setattr(logging.getLogger("buy_or_wait"), "propagate", True)
    yield
    request.assert_not_called()


def source_csv(tmp_path, changes):
    defaults = {"amount": "80", "account_balance": "100", "minimum_balance": "20",
                "request_date": "2026-09-12"}
    rows = [{**defaults, "request_id": f"synthetic-{index}", **row}
            for index, row in enumerate(changes)]
    path = tmp_path / "requests.csv"
    headers = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return path


def output_ids(tmp_path):
    with (tmp_path / "output.csv").open(encoding="utf-8", newline="") as stream:
        return [row["request_id"] for row in csv.DictReader(stream)]


@pytest.mark.parametrize("invalid,stage", [
    ({"amount": "bad"}, "load"), ({"amount": "-1"}, "load"),
    ({"account_balance": ""}, "prepare"), ({"minimum_balance": "bad"}, "prepare"),
    ({"request_date": "bad"}, "prepare"), ({"desired_date": "bad"}, "load"),
    ({"pending_payments": '[{"amount":null}]'}, "prepare"),
])
def test_invalid_request_does_not_stop_later_request(tmp_path, invalid, stage):
    source = source_csv(tmp_path, [invalid, {}])
    results = run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", extractor=Mock())
    assert len(results) == 1
    failure, = results.failures
    assert failure.request_id == "synthetic-0"
    assert failure.number == 1
    assert failure.stage == stage
    assert output_ids(tmp_path) == ["synthetic-1"]


def test_malformed_unused_savings_is_unknown_with_warning(tmp_path, monkeypatch, caplog):
    source = source_csv(tmp_path, [{"savings_balance": "malformed private text"}])
    forecast = Mock(wraps=pipeline.forecast_balance)
    monkeypatch.setattr(pipeline, "forecast_balance", forecast)
    results = run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", extractor=Mock())
    assert not results.failures
    assert forecast.call_args.args[0].savings_balance is None
    assert "Malformed optional field skipped: savings_balance" in caplog.text
    assert "malformed private text" not in caplog.text


@pytest.mark.parametrize("reference", ["missing.txt", "unsupported.bin", "invalid.txt"])
def test_required_media_discovery_or_read_failure_is_not_empty_context(tmp_path, reference):
    if reference == "unsupported.bin":
        (tmp_path / reference).write_bytes(b"unsupported")
    if reference == "invalid.txt":
        (tmp_path / reference).write_bytes(b"\xff")
    source = source_csv(tmp_path, [{"media_path": reference}, {}])

    def extractor(media):
        return extract_media(media, request=Mock())

    results = run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=extractor)
    assert len(results) == 1
    assert len(results.failures) == 1
    assert "required media" in results.failures[0].message
    assert output_ids(tmp_path) == ["synthetic-1"]


def test_required_missing_api_key_fails_only_that_request(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "required.png").write_bytes(b"image not transmitted")
    source = source_csv(tmp_path, [{"image_path": "required.png"}, {}])
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv")
    assert len(results) == 1
    assert results.failures[0].request_id == "synthetic-0"
    assert "missing_api_key" in results.failures[0].message
    assert output_ids(tmp_path) == ["synthetic-1"]


@pytest.mark.parametrize("failure", [TimeoutError("private timeout payload"),
                                      ConnectionError("private temporary payload")])
@pytest.mark.parametrize("required", [False, True])
def test_expected_api_failure_optional_fallback_required_failure(tmp_path, failure, required, caplog):
    (tmp_path / "synthetic-0.txt").write_text("private message", encoding="utf-8")
    source = source_csv(tmp_path, [{"media_path": "synthetic-0.txt"} if required else {}, {}])
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=Mock(side_effect=failure))
    assert len(results) == (1 if required else 2)
    assert bool(results.failures) == required
    assert "private" not in caplog.text
    assert "private" not in repr(results.failures)


@pytest.mark.parametrize("raw", ["invalid JSON", '{"facts":[{}]}'])
def test_required_malformed_ai_output_cannot_create_an_optimistic_result(tmp_path, raw):
    (tmp_path / "required.txt").write_text("unknown obligations", encoding="utf-8")
    source = source_csv(tmp_path, [{"media_path": "required.txt"}, {}])

    def extractor(media):
        return extract_media(media, request=Mock(return_value=raw))

    results = run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=extractor)
    assert output_ids(tmp_path) == ["synthetic-1"]
    assert len(results.failures) == 1


@pytest.mark.parametrize("required", [False, True])
def test_incomplete_media_amount_stays_unknown_without_invented_operational_fact(tmp_path, required, monkeypatch):
    path = tmp_path / "synthetic-0.txt"
    path.write_text("amount illegible", encoding="utf-8")
    source = source_csv(tmp_path, [{"media_path": path.name} if required else {}, {}])
    fact = ExtractedFact(path.resolve(), "pending_payment", "confirmed", "amount illegible",
                         None, "bill", None, None, None, None)
    extractor = Mock(return_value=(FileExtraction(path.resolve(), (fact,)),))
    forecast = Mock(wraps=pipeline.forecast_balance)
    monkeypatch.setattr(pipeline, "forecast_balance", forecast)
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=extractor)
    assert len(results) == (1 if required else 2)
    assert all(not call.args[0].pending_payments for call in forecast.call_args_list)
    assert fact.amount is None and fact.date is None


def test_missing_optional_dates_remain_none(tmp_path, monkeypatch):
    source = source_csv(tmp_path, [{"incomes": '[{"amount":"25","source":"work"}]'}])
    forecast = Mock(wraps=pipeline.forecast_balance)
    monkeypatch.setattr(pipeline, "forecast_balance", forecast)
    results = run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", extractor=Mock())
    assert not results.failures
    assert forecast.call_args.args[0].incomes[0].expected_date is None
    assert results[0].amount_safe_to_pay == Decimal(80)


def test_unexpected_exception_recorded_at_request_boundary_without_payload(tmp_path, caplog):
    (tmp_path / "synthetic-0.txt").write_text("untrusted", encoding="utf-8")
    source = source_csv(tmp_path, [{}, {}])
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv",
                           extractor=Mock(side_effect=RuntimeError("Authorization: private secret")))
    failure, = results.failures
    assert failure.exception_type == "RuntimeError"
    assert failure.message == "request processing failed"
    assert output_ids(tmp_path) == ["synthetic-1"]
    assert "Request failed: stage=process type=RuntimeError" in caplog.text
    assert "private secret" not in caplog.text


@pytest.mark.parametrize("headers", ["request_id", "amount", "request_id,amount,amount"])
def test_invalid_required_headers_are_fatal(tmp_path, headers):
    source = tmp_path / "requests.csv"
    source.write_text(headers + "\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="cannot load requests"):
        run_pipeline(source, tmp_path, tmp_path / "output.csv", extractor=Mock())
    assert not (tmp_path / "output.csv").exists()


def test_unreadable_input_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "load_requests_csv", Mock(side_effect=PermissionError("private path")))
    with pytest.raises(PipelineError, match="cannot load requests"):
        run_pipeline(tmp_path / "requests.csv", tmp_path, tmp_path / "output.csv", extractor=Mock())


def test_output_validation_failure_does_not_stop_other_requests(tmp_path, monkeypatch):
    source = source_csv(tmp_path, [{}, {}])
    validate = pipeline.validate_decision_output

    def first_invalid(identifier, decision):
        if identifier == "synthetic-0":
            raise ValueError("private serialization payload")
        validate(identifier, decision)

    monkeypatch.setattr(pipeline, "validate_decision_output", first_invalid)
    results = run_pipeline(source, tmp_path / "media", tmp_path / "output.csv", extractor=Mock())
    assert results.failures[0].stage == "output_validation"
    assert output_ids(tmp_path) == ["synthetic-1"]


def test_required_confirmed_media_conflict_is_not_silently_omitted(tmp_path):
    path = tmp_path / "required.txt"
    path.write_text("conflicting bills", encoding="utf-8")
    source = source_csv(tmp_path, [{"media_path": path.name}, {}])
    first = ExtractedFact(path.resolve(), "pending_payment", "confirmed", "bill 10",
                          Decimal(10), "bill", date(2026, 9, 15), None, None, None)
    second = ExtractedFact(path.resolve(), "pending_payment", "confirmed", "bill 20",
                           Decimal(20), "bill", date(2026, 9, 15), None, None, None)
    results = run_pipeline(source, tmp_path, tmp_path / "output.csv",
                           extractor=Mock(return_value=[FileExtraction(path.resolve(), (first, second))]))
    assert "unresolved merge conflicts" in results.failures[0].message
    assert output_ids(tmp_path) == ["synthetic-1"]
