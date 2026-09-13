"""Explicit, stderr-only application logging with lightweight secret protection."""

import logging
import os
import sys

APP_LOGGER = "buy_or_wait"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def safe_log_value(value):
    """Redact environment credentials and flatten untrusted identifiers."""
    text = str(value)
    secrets = {value for name, value in os.environ.items() if value and (
        name.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
    )}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return "".join(char if char.isprintable() else " " for char in text)


class _PrivacyFilter(logging.Filter):
    def filter(self, record):
        record.msg = safe_log_value(record.getMessage())
        record.args = ()
        record.request_id = safe_log_value(getattr(record, "request_id", "-"))
        record.exc_info = record.exc_text = record.stack_info = None
        return True


class _ConsoleHandler(logging.StreamHandler):
    """Identify only the handler owned by this application."""

    def emit(self, record):
        self.stream = sys.stderr
        super().emit(record)


def configure_logging(level="INFO"):
    """Configure once from main; repeated calls reuse one owned handler.

    Only the application namespace is configured, leaving third-party logging
    and root handlers alone. No files, handlers or configuration exist on import.
    """
    if not isinstance(level, str) or level.upper() not in LOG_LEVELS:
        raise ValueError("unsupported application log level")
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(level.upper())
    logger.propagate = False
    handler = next((item for item in logger.handlers if isinstance(item, _ConsoleHandler)), None)
    if handler is None:
        handler = _ConsoleHandler(sys.stderr)
        handler.addFilter(_PrivacyFilter())
        logger.addHandler(handler)
    else:
        handler.stream = sys.stderr
    handler.setFormatter(logging.Formatter(
        "%(levelname)s %(name)s request_id=%(request_id)s %(message)s"
    ))
    return logger


def request_logger(identifier=None):
    """Attach sanitized request context without configuring logging."""
    return logging.LoggerAdapter(logging.getLogger(APP_LOGGER + ".pipeline"), {
        "request_id": safe_log_value(identifier if identifier is not None else "-"),
    })
