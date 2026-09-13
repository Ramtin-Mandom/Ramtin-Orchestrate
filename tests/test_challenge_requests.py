"""Challenge request boundaries, with no supporting-data inference."""

import csv
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from extraction import normalize_request
from loaders import RequestsCSVError, load_requests_csv
from loaders.requests import CHALLENGE_HEADERS
from models import ChallengeRequest, RequestType
from pipeline import run_pipeline


def request():
    return ChallengeRequest(
        "0007", "user_01", date(2026, 9, 7), RequestType.TRAVEL,
        Decimal("123.45"), date(2026, 10, 7), True, "Can I travel?",
    )


def write_request(tmp_path, **updates):
    row = {name: getattr(request(), name) for name in CHALLENGE_HEADERS}
    row["request_type"] = "travel"
    row.update(updates)
    path = tmp_path / "requests.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


@pytest.mark.parametrize("kind", list(RequestType))
def test_all_request_types(tmp_path, kind):
    loaded, = load_requests_csv(write_request(tmp_path, request_type=kind.value))
    assert loaded.request_type is kind
    assert loaded.request_id == "0007"
    assert loaded.user_id == "user_01"
    assert loaded.requested_amount == Decimal("123.45")
    assert type(loaded.request_date) is date
    assert loaded.allows_partial_payment is True
    assert loaded.provenance.path == str((tmp_path / "requests.csv").resolve())
    assert loaded.provenance.row_number == 2  # noqa: PLR2004


@pytest.mark.parametrize("field,value", [
    ("request_id", ""), ("user_id", "../user"),
    ("requested_amount", "NaN"), ("requested_amount", "Infinity"),
    ("requested_amount", "-1"), ("requested_amount", "money"),
    ("request_date", "20260907"), ("request_date", "2026-02-30"),
    ("desired_completion_date", "2026-01-01"),
    ("allows_partial_payment", "yes"), ("allows_partial_payment", "1"),
    ("request_type", "unknown"), ("request_text", ""),
])
def test_malformed_csv_values(tmp_path, field, value):
    with pytest.raises(RequestsCSVError, match="row 2"):
        load_requests_csv(write_request(tmp_path, **{field: value}))


@pytest.mark.parametrize("field,value", [
    ("requested_amount", 1.5), ("requested_amount", None),
    ("request_date", datetime(2026, 9, 7, tzinfo=timezone.utc)),
    ("allows_partial_payment", 1), ("request_type", "purchase"),
])
def test_direct_model_does_not_coerce(field, value):
    with pytest.raises(TypeError):
        replace(request(), **{field: value})


def test_challenge_finances_are_not_embedded_json(tmp_path):
    loaded, = load_requests_csv(write_request(
        tmp_path, account_balance="999999", incomes="malformed JSON",
    ))
    normalized = normalize_request(loaded)
    assert normalized.purchase is loaded
    assert normalized.profile.account_balance is None
    assert normalized.profile.incomes == []
    results = run_pipeline(tmp_path / "requests.csv", tmp_path, tmp_path / "output.csv")
    assert not results
    assert results.failures[0].request_id == "0007"
    assert "supporting CSV context is required" in results.failures[0].message
