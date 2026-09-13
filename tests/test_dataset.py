"""Small complete participant datasets exercise deterministic joins offline."""

import csv
from decimal import Decimal, localcontext

import pytest
from loaders import DatasetValidationError, load_dataset
from loaders.dataset_schema import HEADERS
from main import main


def write(root, name, rows):
    with (root / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS[name])
        writer.writeheader()
        writer.writerows({**dict.fromkeys(HEADERS[name], ""), **row} for row in rows)


def change(root, name, **updates):
    with (root / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0].update(updates)
    write(root, name, rows)


@pytest.fixture
def dataset(tmp_path):
    request = {"user_id": "u1", "request_date": "2026-09-01", "request_type": "travel",
                   "requested_amount": "10.25", "desired_completion_date": "2026-10-01",
                   "allows_partial_payment": "false", "request_text": "Can I travel?"}
    write(tmp_path, "requests", [{**request, "request_id": "r2"}, {**request, "request_id": "r1"}])
    write(tmp_path, "sample_requests", [{**request, "request_id": "s1",
        "amount_safe_to_pay": "10.25", "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment", "payment_plan": "2026-09-01:10.25",
        "earliest_date_for_full_payment": "2026-09-01", "spending_changes_needed": "none",
        "decision_explanation": "Example."}])
    write(tmp_path, "financial_profiles", [{"user_id": "u1", "home_currency": "EUR",
        "current_available_balance": "100", "minimum_balance_to_keep": "10",
        "financial_priorities": "travel", "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_reduce": "dining",
        "expense_categories_user_is_willing_to_stop": "subscription",
        "payment_methods_user_will_consider": "full_payment|installments", "max_installment_months": "3"}])
    event = {"user_id": "u1", "event_type": "expense", "description": "Food", "category": "dining",
                 "direction": "debit", "amount": "12.34", "currency": "USD", "event_date": "2026-08-30",
                 "settlement_date": "2026-09-01", "status": "settled", "flexibility": "reducible",
                 "minimum_allowed_amount": "5"}
    write(tmp_path, "financial_events", [{**event, "event_id": "e1"},
        {**event, "event_id": "e2", "linked_event_id": "e1", "amount": "", "currency": "EUR"}])
    write(tmp_path, "exchange_rates", [{"rate_date": "2026-09-01", "from_currency": "USD", "to_currency": "EUR", "rate": "0.92"},
        {"rate_date": "2026-08-30", "from_currency": "USD", "to_currency": "EUR", "rate": "0.5"}])
    write(tmp_path, "request_payment_options", [{"payment_option_id": "p1", "request_id": "r2",
        "payment_method": "installments", "payment_amount": "5.25", "number_of_payments": "2",
        "first_payment_date": "2026-09-01", "payment_frequency_days": "30", "financing_fee": "0.25",
        "total_payable_amount": "10.50"}])
    message = {"user_id": "u1", "sent_at": "2026-09-01T09:30:00Z", "source_type": "bank", "message_text": "Evidence"}
    write(tmp_path, "messages", [{**message, "message_id": "m1", "related_event_id": "e1"},
        {**message, "message_id": "m2", "request_id": "r2", "related_event_id": "e2"}])
    write(tmp_path, "images", [{"image_id": "i1", "user_id": "u1", "request_id": "r2", "related_event_id": "e2"}])
    image_dir = tmp_path / "media" / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "i1.png").write_bytes(b"image contents are not interpreted")
    write(tmp_path, "output", [{"request_id": "r2"}, {"request_id": "r1"}])
    return tmp_path


def test_exact_joins_order_and_conversion(dataset):
    with localcontext() as context:
        context.prec = 2
        loaded = load_dataset(dataset)
    first, second = loaded.contexts
    assert [c.request.request_id for c in loaded.contexts] == ["r2", "r1"]
    assert first.profile is second.profile
    assert first.profile.payment_methods_user_will_consider == ("full_payment", "installments")
    assert first.events[0].home_amount == Decimal("11.3528")
    assert first.events[0].home_minimum_allowed_amount == Decimal("4.60")
    assert first.events[0].event.amount == Decimal("12.34")
    assert first.events[0].exchange_rate.rate == Decimal("0.92")
    assert first.events[1].event.linked_event_id == "e1"
    assert first.events[1].home_amount is None
    assert [m.message_id for m in first.messages] == ["m1", "m2"]
    assert [m.message_id for m in second.messages] == ["m1"]
    assert first.events[0].messages[0].message_id == "m1"
    assert first.events[1].images[0].image_id == "i1"
    assert first.images[0].path == dataset / "media" / "images" / "i1.png"
    assert not second.images and not second.payment_options
    assert first.payment_options[0].payment_option_id == "p1"
    assert loaded.sample_requests[0].request_id == "s1"
    assert set(loaded.tables) == set(HEADERS) - {"requests", "sample_requests"}


@pytest.mark.parametrize("name,updates", [
    ("requests", {"user_id": "missing"}),
    ("messages", {"related_event_id": "missing"}),
    ("images", {"request_id": "missing"}),
    ("financial_events", {"linked_event_id": "missing"}),
    ("request_payment_options", {"request_id": "missing"}),
    ("financial_events", {"amount": "NaN"}),
    ("financial_events", {"status": "unknown"}),
    ("financial_profiles", {"home_currency": "XYZ"}),
    ("financial_profiles", {"payment_methods_user_will_consider": "wait"}),
    ("messages", {"sent_at": "invalid"}),
    ("requests", {"allows_partial_payment": "yes"}),
    ("request_payment_options", {"number_of_payments": "2.5"}),
    ("output", {"amount_safe_to_pay": "1"}),
    ("sample_requests", {"amount_safe_to_pay": "NaN"}),
    ("sample_requests", {"payment_plan": "not a schedule"}),
])
def test_invalid_rows_and_references(dataset, name, updates):
    change(dataset, name, **updates)
    with pytest.raises(DatasetValidationError):
        load_dataset(dataset)


@pytest.mark.parametrize("name", ["requests", "sample_requests", "financial_profiles", "financial_events",
                                    "exchange_rates", "request_payment_options", "messages", "images", "output"])
def test_duplicate_ids(dataset, name):
    path = dataset / f"{name}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    write(dataset, name, [*rows, rows[0]])
    with pytest.raises(DatasetValidationError, match="duplicate"):
        load_dataset(dataset)


def test_missing_exact_rate_cannot_use_earlier_or_inverse(dataset):
    write(dataset, "exchange_rates", [{"rate_date": "2026-08-30", "from_currency": "USD", "to_currency": "EUR", "rate": "0.92"},
        {"rate_date": "2026-09-01", "from_currency": "EUR", "to_currency": "USD", "rate": "1.1"}])
    with pytest.raises(DatasetValidationError, match="no supplied exchange rate"):
        load_dataset(dataset)


def test_unsettled_event_uses_event_date(dataset):
    change(dataset, "financial_events", settlement_date="", status="pending")
    assert load_dataset(dataset).contexts[0].events[0].home_amount == Decimal("6.170")


def test_missing_image(dataset):
    (dataset / "media" / "images" / "i1.png").unlink()
    with pytest.raises(DatasetValidationError, match="image file missing"):
        load_dataset(dataset)


def test_malformed_headers_and_short_record(dataset):
    (dataset / "images.csv").write_text("image_id,user_id,request_id,related_event_id\ni1,u1\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="malformed record"):
        load_dataset(dataset)


def test_challenge_cli(dataset, capsys, tmp_path):
    output = tmp_path / "cli_output.csv"
    result = main(["--dataset", str(dataset), "--output", str(output)])
    assert [decision.request_id for decision in result.decisions] == ["r2", "r1"]
    assert "Wrote 2 challenge decisions" in capsys.readouterr().out
    assert output.is_file()


@pytest.mark.parametrize("name", list(HEADERS))
def test_exact_header_validation(dataset, name):
    (dataset / f"{name}.csv").write_text("invented_header\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="expected headers"):
        load_dataset(dataset)


@pytest.mark.parametrize("currency", ["EUR", "USD", "INR", "IDR", "ZAR"])
def test_home_currency_conversion(dataset, currency):
    change(dataset, "financial_profiles", home_currency=currency)
    write(dataset, "exchange_rates", [
        {"rate_date": "2026-09-01", "from_currency": "USD", "to_currency": currency, "rate": "2"},
        {"rate_date": "2026-09-01", "from_currency": "EUR", "to_currency": currency, "rate": "3"},
    ])
    event = load_dataset(dataset).contexts[0].events[0]
    assert event.home_currency == currency
    assert event.home_amount == Decimal("12.34" if currency == "USD" else "24.68")


def test_cross_user_reference_rejected(dataset):
    with (dataset / "financial_profiles.csv").open(encoding="utf-8", newline="") as stream:
        profiles = list(csv.DictReader(stream))
    write(dataset, "financial_profiles", [profiles[0], {**profiles[0], "user_id": "u2"}])
    change(dataset, "messages", user_id="u2")
    with pytest.raises(DatasetValidationError, match="mismatched event reference"):
        load_dataset(dataset)


def test_challenge_cli_never_calls_ai_extraction_by_default(dataset, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("billable AI extraction must be opt-in")
    monkeypatch.setattr("challenge_pipeline.extract_event_facts", forbidden)
    output = tmp_path / "no_ai_output.csv"
    result = main(["--dataset", str(dataset), "--output", str(output)])
    assert result.decisions
    assert result.usage == ()
