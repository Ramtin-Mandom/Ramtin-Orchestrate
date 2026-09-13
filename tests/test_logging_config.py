"""Application logging is explicit, private and observational; all AI is mocked."""

import logging
from unittest.mock import Mock

import pytest
from logging_config import APP_LOGGER, configure_logging, request_logger
from main import main
from pipeline import run_pipeline


@pytest.fixture(autouse=True)
def isolated_application_logger(caplog):
    logger = logging.getLogger(APP_LOGGER)
    previous = logger.level, logger.propagate, list(logger.handlers)
    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield logger
    for handler in logger.handlers:
        if handler not in previous[2] and handler is not caplog.handler:
            handler.close()
    logger.level, logger.propagate, logger.handlers = previous


def capture_configured(caplog, level="INFO"):
    logger = configure_logging(level)
    logger.addHandler(caplog.handler)
    caplog.handler.setLevel(logging.NOTSET)
    return logger


def input_csv(tmp_path, identifier="synthetic-request"):
    source = tmp_path / "requests.csv"
    source.write_text("request_id,amount,account_balance,minimum_balance,request_date\n"
                      f"{identifier},80,100,20,2026-09-12\n", encoding="utf-8")
    return source


def test_configuration_reuses_handler_and_preserves_root(caplog, capsys):
    root_handlers = list(logging.getLogger().handlers)
    logger = configure_logging()
    handler, = logger.handlers
    assert logger.level == logging.INFO
    assert not logger.propagate
    assert configure_logging("DEBUG") is logger
    assert logger.handlers == [handler]
    logger.addHandler(caplog.handler)
    logger.info("single application message")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("single application message") == 1
    assert "request_id=-" in captured.err
    assert caplog.records[-1].message == "single application message"
    assert logging.getLogger().handlers == root_handlers


def test_request_stage_trace_with_no_media(caplog, tmp_path):
    capture_configured(caplog)
    extractor = Mock(side_effect=AssertionError("no extraction expected"))
    run_pipeline(input_csv(tmp_path), tmp_path / "media", tmp_path / "output.csv", extractor=extractor)
    records = [record for record in caplog.records if record.request_id == "synthetic-request"]
    messages = [record.getMessage() for record in records]
    assert messages == [
        "Request processing started",
        "Media discovered: supported=0 text=0 image=0 unsupported=0 missing=0 issues=0",
        "Extraction skipped: no supported media",
        "Profile merge completed: conflicts=0 uncertain=0 incomplete=0",
        "Forecast completed: entries=1",
        "Decision completed: status=affordable_now method=pay_in_full",
        "Request processing completed",
    ]
    assert all(record.levelno == logging.INFO for record in records)
    assert caplog.records[-1].getMessage() == "Output writing completed: rows=1"
    extractor.assert_not_called()


def test_recoverable_warning_context_type_and_payload_privacy(caplog, tmp_path, monkeypatch):
    capture_configured(caplog)
    secret = "synthetic-private-key"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    source = input_csv(tmp_path)
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "synthetic-request.txt").write_text("private message and financial details", encoding="utf-8")
    warnings = []
    extractor = Mock(side_effect=TimeoutError(f"Authorization: Bearer {secret}; full API response"))
    result, = run_pipeline(source, media_root, tmp_path / "output.csv", extractor=extractor,
                           on_warning=warnings.append)
    warning, = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warning.request_id == "synthetic-request"
    assert "using structured data (TimeoutError)" in warning.getMessage()
    assert warnings == ["request 1: media extraction failed; using structured data"]
    assert result.recommended_payment_method == "pay_in_full"
    for private in (secret, "Authorization", "full API response", "private message", "financial details"):
        assert private not in caplog.text


def test_defensive_filter_redacts_credentials_and_discards_tracebacks(caplog, capsys, monkeypatch):
    logger = capture_configured(caplog)
    key, token = "synthetic-secret-key", "synthetic-secret-token"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("TEST_TOKEN", token)
    logger.info("credential=%s token=%s", key, token)
    try:
        raise RuntimeError("private exception body")
    except RuntimeError:
        logger.exception("safe error summary")
    captured = capsys.readouterr().err
    for private in (key, token, "private exception body", "Traceback"):
        assert private not in captured
        assert private not in caplog.text
    assert "[REDACTED]" in captured
    assert all(record.exc_info is None for record in caplog.records)


def test_identifier_redaction_without_configuration(caplog, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    with caplog.at_level(logging.INFO):
        request_logger("synthetic-key\ninjected-line").info("safe stage")
    record, = caplog.records
    assert record.request_id == "[REDACTED] injected-line"
    assert "synthetic-key" not in str(record.__dict__)


def test_log_level_is_observational_and_cli_output_unchanged(caplog, tmp_path, capsys):
    source = input_csv(tmp_path)
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    capture_configured(caplog, "INFO")
    baseline = run_pipeline(source, tmp_path / "media", first, extractor=Mock())
    configure_logging("ERROR")
    caplog.clear()
    compared = run_pipeline(source, tmp_path / "media", second, extractor=Mock())
    assert compared == baseline
    assert first.read_bytes() == second.read_bytes()
    assert not caplog.records
    capsys.readouterr()
    main(["--input", str(source), "--media-root", str(tmp_path / "media"),
          "--output", str(second), "--log-level", "error"])
    captured = capsys.readouterr()
    assert captured.out == f"Wrote 1 decisions to {second}.\n"
    assert captured.err == ""


def test_successful_extraction_logs_counts_not_contents(caplog, tmp_path):
    capture_configured(caplog)
    source = input_csv(tmp_path)
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "synthetic-request.png").write_bytes(b"private image bytes")
    run_pipeline(source, media_root, tmp_path / "output.csv", extractor=Mock(return_value=()))
    assert "text=0 image=1" in caplog.text
    assert "Extraction completed: facts=0 failed_files=0" in caplog.text
    assert "private image bytes" not in caplog.text


def test_invalid_level_does_not_create_handlers(isolated_application_logger):
    with pytest.raises(ValueError, match="log level"):
        configure_logging("invalid")
    assert isolated_application_logger.handlers == []


def test_request_context_does_not_configure_logging(isolated_application_logger):
    original_level = isolated_application_logger.level
    request_logger("synthetic-request")
    assert isolated_application_logger.handlers == []
    assert isolated_application_logger.level == original_level
