"""Orchestrate existing APIs without implementing financial or extraction rules."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from decision import attach_decision_explanation, decide_purchase
from extraction import NormalizedRequest, merge_profile, normalize_request
from extraction.media_extractor import ExtractionAPIError, FileExtraction, extract_media
from finance import forecast_balance
from loaders import (
    RequestMedia,
    RequestsCSVError,
    discover_request_media,
    load_requests_csv,
)
from logging_config import request_logger, safe_log_value
from models import ChallengeRequest
from output import validate_decision_output, write_decisions_csv

DEFAULT_HORIZON_DAYS = 90
PathLike = Union[str, Path]
Extractor = Callable[[RequestMedia], Iterable[FileExtraction]]


class PipelineError(ValueError):
    """Required inputs or computed outputs cannot complete the pipeline safely."""


class RequestError(PipelineError):
    """Safe request-level input/context failure; other requests may proceed."""


@dataclass(frozen=True)
class RequestFailure:
    number: int
    request_id: Optional[str]
    stage: str
    exception_type: str
    message: str


class PipelineResults(list):
    """Successful decisions with separately recorded failures, preserving list use."""

    def __init__(self, decisions, failures):
        super().__init__(decisions)
        self.failures = tuple(failures)


@dataclass(frozen=True)
class _PreparedRequest:
    number: int
    request_id: str
    normalized: NormalizedRequest
    start: date
    end: date


def _prepare(loaded, number, as_of_date, horizon_days, warn):
    identifier = loaded.source_fields.get("request_id", "")
    logger = request_logger(identifier or None)
    if not identifier.strip():
        logger.error("Input rejected: missing request ID")
        raise RequestError(f"request {number}: request_id is required")
    def optional_warning(field):
        logger.warning("Malformed optional field skipped: %s", field)
        if warn is not None:
            warn(f"request {number}: malformed optional savings_balance skipped")
    try:
        normalized = normalize_request(loaded, on_optional_warning=optional_warning)
    except (ValueError, TypeError) as exc:
        logger.error("Structured input rejected (%s)", safe_log_value(type(exc).__name__))
        raise RequestError(f"request {number}: invalid structured financial data") from None
    profile = normalized.profile
    if isinstance(loaded, ChallengeRequest):
        raise RequestError(
            f"request {number}: supporting CSV context is required; "
            "use loaders.load_dataset for challenge loading"
        )
    if profile.account_balance is None:
        logger.error("Input rejected: missing account balance")
        raise RequestError(f"request {number}: account_balance is required")
    if profile.minimum_balance is None and profile.preferred_balance is None:
        logger.error("Input rejected: missing required reserve")
        raise RequestError(f"request {number}: an explicit minimum or preferred balance is required")
    start = as_of_date
    if start is None:
        try:
            start = date.fromisoformat(loaded.source_fields.get("request_date", "").strip())
        except ValueError:
            logger.error("Input rejected: missing or invalid evaluation date (ValueError)")
            raise RequestError(
                f"request {number}: request_date (YYYY-MM-DD) or --as-of-date is required"
            ) from None
    try:
        end = start + timedelta(days=horizon_days)
    except OverflowError:
        logger.error("Input rejected: unsupported horizon (OverflowError)")
        raise RequestError(f"request {number}: forecast horizon exceeds supported dates") from None
    return _PreparedRequest(number, identifier, normalized, start, end)


def _media_facts(media, extractor, warn, number, required=False):
    logger = request_logger(media.request_id)
    if media.issues or media.missing_paths or media.unsupported_files:
        if required:
            raise RequestError("required media is missing, unreadable or unsupported")
        warn(f"request {number}: some media could not be discovered or is unsupported")
    if not media.files:
        if required:
            raise RequestError("required media contains no supported files")
        logger.info("Extraction skipped: no supported media")
        return ()
    try:
        extracted = tuple(extractor(media))
        facts = []
        failed = False
        for result in extracted:
            if result.error:
                failed = True
            else:
                facts.extend(result.facts)
    except (ExtractionAPIError, OSError, TimeoutError) as exc:
        if required:
            raise RequestError("required media extraction failed") from None
        warn(f"request {number}: media extraction failed; using structured data",
             exception_type=safe_log_value(type(exc).__name__))
        return ()
    if failed:
        if required:
            known_errors = {"missing_api_key", "file_read_error", "api_error", "invalid_model_output"}
            codes = sorted({result.error if result.error in known_errors else "extraction_failure"
                            for result in extracted if result.error})
            raise RequestError("required media extraction failed: " + ", ".join(codes))
        warn(f"request {number}: some media extraction failed; retaining structured data")
    if required and ({result.source_path for result in extracted} != {item.path for item in media.files}
                     or any(fact.amount is None or fact.certainty != "confirmed" for fact in facts)):
        raise RequestError("required media output is incomplete or uncertain")
    logger.info("Extraction completed: facts=%d failed_files=%d", len(facts),
                sum(bool(result.error) for result in extracted))
    return tuple(facts)


def _process(prepared, media_root, extractor, warn):
    logger = request_logger(prepared.request_id)
    logger.info("Request processing started")
    callback = warn

    def notify(message, *, exception_type=None):
        if exception_type is None:
            logger.warning("%s", message)
        else:
            logger.warning("%s (%s)", message, exception_type)
        if callback is not None:
            callback(message)

    warn = notify
    normalized = prepared.normalized
    media = discover_request_media(normalized, media_root)
    logger.info("Media discovered: supported=%d text=%d image=%d unsupported=%d missing=%d issues=%d",
                len(media.files), sum(item.category == "text" for item in media.files),
                sum(item.category == "image" for item in media.files), len(media.unsupported_files),
                len(media.missing_paths), len(media.issues))
    required = any(normalized.source_fields.get(field, "").strip()
                   for field in ("media_path", "message_path", "image_path", "media_paths"))
    facts = _media_facts(media, extractor, warn, prepared.number, required)
    merged = merge_profile(normalized.profile, facts)
    logger.info("Profile merge completed: conflicts=%d uncertain=%d incomplete=%d",
                len(merged.conflicts), len(merged.ignored_uncertain_facts),
                len(merged.ignored_incomplete_facts))
    if required and any(conflict.reason in {
        "unresolved_media_conflict", "unresolved_media_fields", "ambiguous_csv_match",
    } for conflict in merged.conflicts):
        raise RequestError("required media facts have unresolved merge conflicts")
    if (merged.conflicts or merged.ignored_uncertain_facts
            or merged.ignored_incomplete_facts or merged.warnings):
        warn(f"request {prepared.number}: merge reported conflicts or excluded media facts")
    try:
        forecast = forecast_balance(merged.profile, prepared.start, prepared.end)
        logger.info("Forecast completed: entries=%d", len(forecast.entries))
        decision = decide_purchase(merged.profile, normalized.purchase, forecast)
        logger.info("Decision completed: status=%s method=%s",
                    safe_log_value(decision.affordability_status),
                    safe_log_value(decision.recommended_payment_method))
        result = attach_decision_explanation(decision, normalized.purchase, merged.profile, forecast)
        logger.info("Request processing completed")
        return result
    except (ValueError, TypeError, ArithmeticError) as exc:
        logger.error("Forecast or decision failed (%s)", safe_log_value(type(exc).__name__))
        raise RequestError(
            f"request {prepared.number}: forecasting or decision failed; check financial inputs and dates"
        ) from None


def run_pipeline(  # noqa: PLR0913 -- explicit orchestration dependencies and settings
    input_path: PathLike, media_root: PathLike, output_path: PathLike, *,
    as_of_date: Optional[date] = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
    extractor: Optional[Extractor] = None, on_warning: Optional[Callable[[str], None]] = None,
) -> PipelineResults:
    """Run normalized structured requests in order and write one final CSV batch.

    Uses the existing model-named CSV schema. Raw challenge supporting datasets
    are not joined here. A row request_date is required unless explicitly
    overridden; the default inclusive forecast ends 90 days after that date.
    Each request's required inputs are checked before its extraction or output.
    Successful decisions retain list behavior; .failures records rejected rows.
    Explicit media references are required context; unusable referenced media
    fails its request. Only malformed unused savings_balance may be skipped.
    Extractor injection uses the existing RequestMedia -> FileExtraction API;
    without supported media it is never called, so no SDK client is initialized.
    Warning/error messages deliberately exclude external exception bodies.
    File-level loading/configuration/output I/O failures remain fatal.
    """
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days <= 0:
        raise PipelineError("horizon_days must be a positive integer")
    if as_of_date is not None and (
        not isinstance(as_of_date, date) or isinstance(as_of_date, datetime)
    ):
        raise PipelineError("as_of_date must be a date")
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise PipelineError("output_path must differ from input_path")
    failures = []
    def row_error(failure):
        failures.append(RequestFailure(failure.number, failure.request_id or None, "load",
                                       safe_log_value(failure.exception_type), "invalid required purchase data"))
        request_logger(failure.request_id or None).error("Request rejected: invalid purchase data (%s)",
                                                        safe_log_value(failure.exception_type))
    try:
        loaded = load_requests_csv(input_path, on_row_error=row_error, required_headers=("request_id",))
    except (OSError, RequestsCSVError, UnicodeError) as exc:
        request_logger().error("Request loading failed (%s)", safe_log_value(type(exc).__name__))
        raise PipelineError(
            "cannot load requests: check the input path, UTF-8 CSV structure and required amount column"
        ) from None
    identifiers = [request.source_fields.get("request_id", "").strip() for request in loaded]
    identifiers.extend(failure.request_id.strip() for failure in failures if failure.request_id)
    if len({identifier for identifier in identifiers if identifier}) != sum(bool(identifier) for identifier in identifiers):
        request_logger().error("Input rejected: duplicate request IDs")
        raise PipelineError("request_id values must be unique")
    extract = extractor if extractor is not None else extract_media
    warn = on_warning
    results, rows = [], []
    rejected_numbers = {failure.number for failure in failures}
    number = 0
    for request in loaded:
        number += 1
        while number in rejected_numbers:
            number += 1
        identifier = request.source_fields.get("request_id") or None
        stage = "prepare"
        try:
            prepared = _prepare(request, number, as_of_date, horizon_days, warn)
            stage = "process"
            result = _process(prepared, media_root, extract, warn)
            stage = "output_validation"
            validate_decision_output(prepared.request_id, result)
        except Exception as exc:  # noqa: BLE001 -- recorded per-request batch boundary
            message = str(exc) if isinstance(exc, RequestError) else "request processing failed"
            failures.append(RequestFailure(number, identifier, stage,
                                           safe_log_value(type(exc).__name__), message))
            request_logger(identifier).error("Request failed: stage=%s type=%s", stage,
                                             safe_log_value(type(exc).__name__))
            continue
        results.append(result)
        rows.append((prepared.request_id, result))
    try:
        write_decisions_csv(rows, output_path)
    except (OSError, ValueError, TypeError, ArithmeticError) as exc:
        request_logger().error("Output writing failed (%s)", safe_log_value(type(exc).__name__))
        raise PipelineError(
            "cannot write output: check the output path and completed decision serialization "
            "(spending changes require source event IDs)"
        ) from None
    request_logger().info("Output writing completed: rows=%d", len(results))
    return PipelineResults(results, sorted(failures, key=lambda failure: failure.number))
