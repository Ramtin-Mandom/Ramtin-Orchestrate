"""Isolated CSV conversion and error reporting checks."""

from datetime import date
from decimal import Decimal

import pytest
from loaders import RequestsCSVError, load_requests_csv
from models import PurchaseRequest


def write_csv(tmp_path, content):
    path = tmp_path / "requests.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_row_preserves_source(tmp_path):
    path = write_csv(
        tmp_path,
        '\ufeffrequest_id,amount,description,desired_date,preferred_payment_method,source\n'
        '0007,12.50,"Book, hardcover",2026-09-12,debit,original\n',
    )
    request, = load_requests_csv(path)
    assert isinstance(request, PurchaseRequest)
    assert request.amount == Decimal("12.50")
    assert request.description == "Book, hardcover"
    assert request.desired_date == date(2026, 9, 12)
    assert request.preferred_payment_method == "debit"
    assert request.source_fields == {
        "request_id": "0007", "amount": "12.50", "description": "Book, hardcover",
        "desired_date": "2026-09-12", "preferred_payment_method": "debit",
        "source": "original",
    }


def test_multiple_rows_preserve_order(tmp_path):
    path = write_csv(tmp_path, "request_id,amount\n001,0\n002,100.01\n")
    requests = load_requests_csv(str(path))
    assert [row.amount for row in requests] == [Decimal(0), Decimal("100.01")]
    assert [row.source_fields["request_id"] for row in requests] == ["001", "002"]


@pytest.mark.parametrize("content", [
    "amount\n1\n",
    "amount,description,desired_date,preferred_payment_method\n1,,,\n",
    "amount,description,desired_date,preferred_payment_method\n1\n",
])
def test_optional_defaults(tmp_path, content):
    request, = load_requests_csv(write_csv(tmp_path, content))
    assert request.description == ""
    assert request.desired_date is None
    assert request.preferred_payment_method is None


@pytest.mark.parametrize("amount", ["", "abc", "NaN", "Infinity", "-1"])
def test_invalid_amount_has_context(tmp_path, amount):
    path = write_csv(tmp_path, f"request_id,amount\n001,{amount}\n")
    with pytest.raises(RequestsCSVError, match="row 2: field 'amount'") as error:
        load_requests_csv(path)
    assert str(path) in str(error.value)


def test_invalid_date_has_context(tmp_path):
    path = write_csv(tmp_path, "amount,desired_date\n1,2026-02-30\n")
    with pytest.raises(RequestsCSVError, match="row 2: field 'desired_date'"):
        load_requests_csv(path)


@pytest.mark.parametrize("content, message", [
    ("", "header is missing"),
    ("description\nitem\n", "missing required field 'amount'"),
    ("amount,amount\n1,2\n", "duplicate field names"),
    ("amount,\n1,2\n", "empty field name"),
    ("amount\n1,2\n", "row 2: too many fields"),
    ('amount,description\n1,"unfinished\n', "malformed CSV"),
])
def test_invalid_csv_structure(tmp_path, content, message):
    with pytest.raises(RequestsCSVError, match=message):
        load_requests_csv(write_csv(tmp_path, content))


def test_header_only_returns_empty_list(tmp_path):
    assert load_requests_csv(write_csv(tmp_path, "amount\n")) == []
