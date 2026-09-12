# Ramtin-Orchestrate
This is my attempt in the Hanker rank Orchestrate competition in September 12th

## Buy or Wait — Milestones 1–12

A minimal Python foundation with a startup script, shared dataclass models,
and a CSV input loader with focused tests. No API integration, forecasting, affordability
decisions, or media processing is implemented.

### Setup

Use Python 3.8 or newer. From the repository root, create a virtual environment:

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in Windows PowerShell, or
`source .venv/bin/activate` on macOS/Linux. Then install the development tools:

```sh
python -m pip install -r requirements.txt
```

No environment variables are required. `.env.example` documents this;
keep any future local secrets in the ignored `.env` file.

### Run, test, and lint

Run these commands from the repository root:

```sh
python code/main.py
python -m pytest -q
python -m ruff check .
```

The application prints `Buy or Wait: ready (Milestone 1).` and exits successfully.
`code/main.py` contains the entry point; `tests/test_smoke.py` verifies startup.

### Shared models (Milestone 2)

`code/models/` exports `FinancialProfile`, `Transaction`, `RecurringExpense`,
`Income`, `PendingPayment`, `PurchaseRequest`, `PaymentPlan`, and `DecisionResult`.
Application modules can use `from models import FinancialProfile` when run from
`code/`; tests expose the same import path through `tests/conftest.py`.

Money uses finite `decimal.Decimal` values in the profile's currency. Construct
amounts from strings, such as `Decimal("12.50")`, rather than floats. Models do
not round amounts or convert currencies. Transactions use positive inflows and
negative outflows; account balances may also be negative. Other monetary fields
reject negative values. Unknown optional amounts and dates default to `None`,
and collections default to independent empty lists. Validation runs at construction.

Payment schedules, status labels, and explanations are supplied by callers;
these models perform no financial calculations or decision rules. No additional
dependency is needed beyond the existing development tools.

### Requests CSV (Milestone 3)

From application modules under `code/`, use:

```python
from loaders import load_requests_csv

requests = load_requests_csv("requests.csv")
```

The repository supplies no sample CSV/schema, so headers use the existing
`PurchaseRequest` field names: required `amount` (finite, nonnegative Decimal),
and optional `description`, `desired_date` (YYYY-MM-DD), and
`preferred_payment_method`. Missing or blank optional dates/payment methods
become `None`; missing descriptions become `""`. Headers must be unique and
nonempty; UTF-8 files with or without a BOM are supported.

Each returned `LoadedRequest` is a `PurchaseRequest` with an additional
`source_fields` dictionary retaining all CSV columns verbatim, including any
request ID columns (for example, `request.source_fields["request_id"]`).
Missing trailing cells are represented by empty strings. Invalid input raises
`RequestsCSVError` with the file path and row/field context when applicable.
This loader performs no financial calculations.

### Structured normalization (Milestone 4)

```python
from extraction import normalize_request

normalized = normalize_request(requests[0])
profile = normalized.profile
purchase = normalized.purchase
```

Purchase fields come from the loaded object. Supported scalar source columns
are `currency`, `account_balance` (or `current_balance`), `savings_balance`,
`minimum_balance`, and `preferred_balance`. Conflicting balance aliases fail.
Account balances may be negative; other amounts must be finite and nonnegative.
No currency conversion or rounding occurs.

Collection columns contain JSON arrays of objects using existing model fields:
`incomes` uses `Income`, `pending_payments` uses `PendingPayment`, `transactions`
uses `Transaction`, and `recurring_expenses`, `essential_expenses`, and
`flexible_expenses` use `RecurringExpense`. For example, an `incomes` cell can
contain `[{"amount": "250.50", "source": "work", "expected_date": "2026-09-15"}]`.
Each entry requires `amount`; unknown entry fields are rejected. Negative
transaction amounts retain their signed meaning. Expense groups stay separate,
and absent frequency/date/category information is never inferred.

Text is trimmed, money becomes `Decimal`, and dates use YYYY-MM-DD. Optional
blanks retain model defaults and blank collections become empty lists.
`normalized.request_id` is trimmed when the `request_id` column is present;
`normalized.source_fields` copies all original columns unchanged, including
other ID columns and unsupported metadata. Invalid values raise
`NormalizationError` with a field path, including collection indices.

### Balance forecast (Milestone 5)

```python
from datetime import date
from finance import forecast_balance

forecast = forecast_balance(normalized.profile, date(2026, 9, 12), date(2026, 12, 31))
```

The returned `BalanceForecast` contains an immutable tuple of `ForecastEntry`
values with `date`, `event_type`, `source`, signed `amount`, resulting `balance`,
and original collection `source_index`. A starting entry records `account_balance`
with a zero event amount before any events on `as_of_date`. An unknown starting
balance raises an error. The forecast period includes both boundary dates.

Dated income entries are treated as caller-confirmed and included once. Pending
payments are included on their due dates. Recurring and essential expenses use
their explicit next due dates; daily, weekly, monthly, and yearly schedules
expand through the end date. Monthly/yearly schedules retain the original day
and clamp it to the last day of shorter months, recovering the original day
in later months. Missing or unsupported frequencies include only the explicit
due-date occurrence; missing dates produce no events. Events outside the period
are excluded, while a past recurrence anchor can generate future occurrences.

Events sort chronologically, then by outflows before inflows, then event type
alphabetically and original collection position. All arithmetic uses Decimal
without rounding to cents or depending on ambient precision. Savings, historical
transactions, flexible expenses, and requested purchases do not affect this
timeline. No forecasting rule infers further income or missing schedules, and
the input profile is unchanged.

### Safe amount (Milestone 6)

```python
from finance import calculate_amount_safe_to_pay

amount_safe_to_pay = calculate_amount_safe_to_pay(normalized.profile, forecast)
```

The pure function returns a Decimal using
`max(0, min(entry.balance for entry in forecast.entries) - required_reserve)`.
Every balance counts, including the starting balance and intermediate same-date
balances. The reserve is the higher of the explicit `minimum_balance` and
`preferred_balance`, or the single supplied value. If both are unknown, a clear
validation error is raised; there is no numeric default. The forecast must
contain its starting entry and finite Decimal balances.

The result is never negative and is not capped at the purchase amount. The
function consumes only the existing forecast, does not generate events or
assume spending reductions, and leaves both inputs unchanged. It does not
return a purchase decision or affordability status.

### Full payment assessment (Milestone 7)

```python
from finance import assess_full_payment

assessment = assess_full_payment(normalized.purchase, amount_safe_to_pay)
```

The pure function approves full payment exactly when the requested amount is
less than or equal to the supplied safe amount. Both inputs must be finite,
nonnegative Decimals. The frozen `FullPaymentAssessment` uses explicit Literal
values: approval sets `can_pay_in_full=True`, `affordability_status="affordable_now"`,
`recommended_payment_method="pay_in_full"`, and `recommended_payment_amount`
to exactly the requested amount. A zero purchase is approved even with zero
safe amount.

Unsafe full payment sets `can_pay_in_full=False` and
`affordability_status="full_payment_unsafe"`; both recommendation fields are
`None`. This is an intermediate assessment, not a final alternative selection.
No forecast or safe amount is recalculated, and inputs remain unchanged.

### Earliest full-payment date (Milestone 8)

```python
from finance import find_earliest_full_payment_date

earliest_date = find_earliest_full_payment_date(
    forecast, normalized.purchase.amount, required_minimum_balance
)
```

Supply an explicit finite, nonnegative Decimal reserve and purchase amount.
The function returns `forecast.as_of_date` if safe, the earliest safe later
event date otherwise, or `None` within the forecast horizon. It evaluates
each candidate after all known events on that date, using the forecast's
existing order. With no events today, today's candidate uses the starting
balance. Safety requires the lowest balance from that candidate's closing
entry through all remaining events, minus the purchase amount, to stay at
or above the reserve. Intermediate dips on later dates still count, even
when later same-day income restores the balance. This after-events convention
also applies to today; Milestone 6's spend-today amount retains its original
before-events convention.

Dates without events cannot improve safety, so only today and event dates
need evaluation. A chronological forecast with its starting entry is required.
The function returns an existing `date` value and leaves the timeline unchanged;
it generates no events or alternative recommendations.

### Payment planner (Milestone 9)

```python
from finance import plan_payments

plan = plan_payments(
    normalized.purchase, forecast, required_minimum_balance, amount_safe_to_pay
)
```

The pure planner returns the existing `PaymentPlan` with dated `PendingPayment`
entries, or `None` if the purchase cannot be completed safely within the horizon.
Today is before all events, capped by the purchase amount, supplied safe amount,
and actual remaining-horizon capacity. Later event dates are considered after
their final event. Greedy capacity is the minimum remaining forecast balance
minus the reserve and all previously scheduled payments, floored at zero. Each
payment is capped by the unpaid purchase amount; zero payments are skipped.

The completed plan is validated against every original event balance: today's
payment affects the starting entry, while future payments affect their date's
closing entry and all later entries. Earlier same-day events still include
prior payments. Every adjusted balance must preserve the reserve and payments
must total exactly the purchase amount. A timeline already violating the reserve
returns `None`. A zero purchase returns an empty zero-total plan on a safe timeline.
Decimal arithmetic remains exact even under low ambient precision. Existing
purchase descriptions/payment methods are retained, and inputs and forecasts
are unchanged. No fees, loans, or alternative option selection are added.

### Flexible spending evaluation (Milestone 10)

```python
from finance import evaluate_spending_adjustments

result = evaluate_spending_adjustments(
    normalized.profile, forecast, required_minimum_balance, candidate
)
```

`candidate` is a `PurchaseRequest` paid before today's events or an existing
`PaymentPlan`, whose future payments occur after their date's final event.
Plan dates must be today or existing forecast dates. The evaluator checks all
simulated balances and returns `SpendingAdjustmentResult` with `feasible`, a
tuple of structured `spending_changes_needed`, and `adjusted_balances` aligned
with the original entries. Already-safe candidates need no changes. Infeasible
results contain no partial proposal or simulated balances.

Only matching forecast outflows qualify: `flexible_expense` events identify
`profile.flexible_expenses` via `source_index`; `recurring_expense` events require
an explicit `flexible` or `discretionary` category. Source name and amount must
match the underlying expense. Essential expenses and pending payments never
qualify, and protected rent/utilities/required-debt labels are excluded. The
existing forecast engine still excludes the flexible collection: merely listing
an expense there cannot generate savings against a timeline that never deducted
it. No forecast generation is changed.

Shortfalls are processed chronologically. Only expense events already reached
are eligible, ordered by largest available reduction, then date, name and stable
identifier. The last selected expense is reduced only by the exact remaining
shortfall. Changes record identifier/name, date, original amount, reduction and
remaining amount. Existing models define no additional reduction limits; each
occurrence is capped at its original amount. Savings start at that expense's
event position, never before it. After choosing reductions, the complete
simulation is recomputed and every balance must preserve the reserve. All money
uses Decimal, inputs remain unchanged, and no final option is selected.

### Final decision (Milestone 11)

```python
from decision import decide_purchase

result = decide_purchase(normalized.profile, normalized.purchase, forecast)
```

The engine calls existing financial evaluators and selects the first safe option:
full payment now, validated baseline payment plan, earliest safe full-payment
date, feasible flexible-spending adjustment, then do not proceed. Canonical
typed constants in `models.decision_values` define statuses and strategies:
`affordable_now`/`pay_in_full`, `affordable_with_plan`/`installments`,
`affordable_later`/`wait`, and `not_affordable`/`do_not_proceed`. Plan strategy
labels are separate from the plan's original payment instrument, such as debit.

Every `DecisionResult` retains baseline `amount_safe_to_pay` and calculated
`earliest_date_for_full_payment` when available, including for a higher-priority
plan. The date retains Milestone 8's after-events convention. Unused plans remain
`None`; unused spending changes remain empty. Flexible-adjusted options use the
underlying full-payment or plan status and carry structured `SpendingChange`
values. This shared type is also exported by finance as before; `DecisionResult`
continues to accept existing text changes for compatibility. Explanations remain
empty, and inputs are not modified.

Full-payment adjustments are evaluated automatically. To evaluate an existing
proposed plan requiring spending changes, supply `adjustment_plan=existing_plan`.
It must total the requested purchase amount, and the spending evaluator must
validate its safety before selection. No adjustment-only payment schedule is
invented: the existing evaluator requires a candidate, and this engine does not
duplicate plan construction. Baseline options always take priority over that
proposal. Missing reserve information still raises the existing validation error.

### Local media discovery (Milestone 12)

```python
from loaders import discover_request_media

media = discover_request_media(requests[0], "media")
# Also accepts a normalized request or a string request ID.
```

No dataset or challenge media schema is supplied in this repository. The
documented fallback prefers explicit `media_path`, `message_path`, `image_path`,
or `media_paths` (a JSON array of path strings) from preserved source fields.
Paths are relative to the configured media root; contained absolute paths also
work. Explicit references suppress ID fallback, even if missing or unsafe.
Referenced directories are searched recursively. Without explicit references,
only root-level directories named exactly for the request ID and files with
exactly that filename stem qualify; ID directories are searched recursively.
No substring matching is used.

`RequestMedia` contains the request ID, supported `files`, `unsupported_files`,
`missing_paths`, and structured `issues`. Each file records its resolved Path,
category, lowercase extension, MIME type, and byte size. Text types are `.txt`,
`.md`, and `.json`; image types are `.png`, `.jpg`, `.jpeg`, and `.webp`, classified
by extension without opening contents. Results are sorted and deduplicated by
resolved path. No media or an absent fallback root returns an empty result.

Traversal, external resolved paths (including symlink targets), alternate
streams, URI paths, and unsafe IDs are reported rather than followed. Missing,
unsupported, malformed-reference, and inaccessible-path cases are reported
without crashing discovery. No financial data, message content, or image content
is extracted.
