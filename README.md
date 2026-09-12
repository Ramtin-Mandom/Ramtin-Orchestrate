# Ramtin-Orchestrate
This is my attempt in the Hanker rank Orchestrate competition in September 12th

## Buy or Wait — Milestones 1 and 2

A minimal Python foundation with a startup script, shared dataclass models,
and focused tests. No CSV loading, API integration, forecasting, affordability
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
