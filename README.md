# Buy or Wait

A Python implementation of the “Buy or Wait” financial-affordability challenge:
use supplied CSV data and local text/images to assess a requested purchase,
protect a required reserve, and explain a deterministic recommendation. The
writer generates the challenge's exact-column `output.csv` without hardcoded
challenge answers. See [problem_statement.md](problem_statement.md) for the
full challenge specification.

**Current input limitation:** the runner consumes the structured, model-named
CSV format below. It does not join the raw challenge requests, profiles, events,
exchange rates, payment offers, messages, or image-index CSVs. The default
`dataset/requests.csv` path is usable only if that file already has this supported
format. Matching the output schema does not imply complete challenge compliance.

## Pipeline and architecture

CSV loading → structured normalization → local media discovery → AI fact
extraction → profile merge → balance forecast → deterministic decision → template
explanation → ordered CSV output.

AI extracts source-traceable facts only. It neither chooses recommendations nor
calculates affordability. The existing financial modules calculate and validate
payments; explanations use computed values without LLM calls. Evaluation and
model experiments run separately and never modify production configuration.

```text
code/
  main.py                 CLI entry point
  pipeline.py             orchestration and request-failure records
  logging_config.py       safe, configurable stderr logging
  models/                 typed records; Decimal money
  loaders/                CSV loading and media discovery
  extraction/             normalization, AI extraction, profile merging
  finance/                forecasts, safety checks, plans, spending adjustments
  decision/               recommendation selection and template explanations
  output/                 exact-schema CSV writer
  evaluation/             reference evaluation and optional model experiment
tests/                    offline unit and integration tests
requirements.txt          pytest, Ruff, official OpenAI SDK
.env.example              placeholders; never real credentials
ruff.toml                 lint settings; Python 3.8 target
problem_statement.md      authoritative challenge specification
```

Data must be supplied locally; `data/` and `dataset/` are ignored by Git.

## Setup

Run commands from the repository root. Application code targets Python 3.8+
(the offline suite was verified on Python 3.8.2); prefer a maintained Python
version for new installations. Dependency versions are not fully pinned.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.
Upgrade pip before installing dependencies: older bundled resolvers can select
SDK dependencies incompatible with Python 3.8.
The SDK is used only for live media extraction. Tests and no-media runs do not
require credentials or network access.

## Environment configuration

Variables are read from the process environment. **`.env` is not automatically
loaded**; copying `.env.example` alone does not configure the application.
Set credentials securely in the shell or your environment manager and never
commit them.

| Variable | Purpose / default |
| --- | --- |
| `OPENAI_API_KEY` | Required for live extraction; no default |
| `OPENAI_MODEL` | Production extraction model; `gpt-4o-mini` when unset/blank |
| `OPENAI_EXPERIMENT_MODEL` | Explicit alternative for the optional experiment; no default |
| `PYTHONPATH` | Set to `code` for the evaluation module commands below |

A custom extraction model must support image input and strict JSON-schema output.
Log level is configured by the CLI, not a dedicated environment variable.

## Input and execution

The supported CSV is UTF-8 (an optional BOM is accepted), with unique headers.
Required row values are:

- `request_id`: nonempty and unique across the batch.
- `amount`: finite, nonnegative decimal purchase amount; zero is allowed.
- `account_balance` or `current_balance`: known balance; conflicting aliases fail.
- `minimum_balance` or `preferred_balance`: an explicit nonnegative reserve.
- `request_date`: `YYYY-MM-DD`, unless `--as-of-date` supplies it globally.

Optional purchase fields: `description`, `desired_date`, and
`preferred_payment_method`. Optional profile scalars: `currency`,
`savings_balance`, and either reserve field. Collection cells are JSON arrays:
`incomes`, `pending_payments`, `transactions`, `recurring_expenses`,
`essential_expenses`, and `flexible_expenses`. Every collection entry requires
`amount`; other keys use the dataclasses in `code/models/records.py`, such as
`expected_date` for income and `next_due_date` for expenses. Unknown entry fields
are rejected. Quote JSON correctly as CSV content; absent collections are empty.

For a small **synthetic no-media example**, create `data/requests.csv`:

```csv
request_id,amount,account_balance,minimum_balance,request_date,description
example-1,25,100,20,2026-09-12,Example purchase
```

Then run:

```powershell
python code/main.py --input data/requests.csv --media-root data/media --output data/output.csv
```

This executes the full pipeline and writes `data/output.csv`. The default output
location is root `output.csv`; the destination's parent directory must exist.
Existing destination files are overwritten.

| CLI option | Default / behavior |
| --- | --- |
| `--input` | `dataset/requests.csv` |
| `--media-root` / `--media` | `dataset/media` |
| `--output` | `output.csv` |
| `--as-of-date` | No override; use each row's `request_date` |
| `--horizon-days` | Positive integer; 90, ending inclusively 90 days after the start |
| `--log-level` | `INFO`; also `DEBUG`, `WARNING`, `ERROR`, `CRITICAL` |

Media references are `media_path`, `message_path`, `image_path`, or `media_paths`
(a JSON array of path strings). Paths must resolve inside the media root;
referenced directories are searched recursively. Without explicit references,
association uses an exact request-ID directory or root-level filename stem.
Text supports UTF-8 `.txt`, `.md`, `.json`; images support `.png`, `.jpg`, `.jpeg`,
`.webp`, with a 10 MiB limit per file. No OCR library is used. A supported-media
run may make billable OpenAI calls; the client is initialized lazily.

Output columns, exactly in this order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

Money is serialized as plain decimal text without locale formatting; dates use
ISO format. Plans use `date:amount|...`, absent plans/changes use `none`, and absent
optional dates/explanations are blank. Spending changes require actual source event
IDs and use `stop:event_id` or `reduce_to:event_id:amount` (at most three). CSV
quoting handles commas, quotes, and newlines. Internal strategy names map to the
required output labels, such as `pay_in_full` → `full_payment`.

## Testing and help

Tests mock AI/API behavior and use synthetic temporary inputs:

```powershell
python -m pytest -q
python -m ruff check .
python code/main.py --help
$env:PYTHONPATH = 'code'
python -m evaluation --help
python -m evaluation.extraction_experiment --help
```

## Reference evaluation (offline)

With `PYTHONPATH=code`, evaluate an existing prediction CSV against **provided**
reference values (replace `data/expected.csv` with your reference path):

```powershell
python -m evaluation --predictions data/output.csv --expected data/expected.csv
```

Rows match by `request_id`; duplicate IDs are validation errors. The report shows
exact accuracy for status, payment method and earliest date, plus Decimal mean
absolute error and exact accuracy for safe amount. Missing expected fields are
skipped with visible denominators; missing predictions are mismatches. Money MAE
uses numeric pairs only. Typed reports contain field-level mismatch details.
No hidden answers are supplied, and this evaluator does not score plans, spending
changes or explanation quality.

## Extraction experiment (optional, live and billable)

The independent experiment compares A (current production model) with B (an
explicit different model), using the same extractor, prompt and schema on seven
synthetic text cases. It reports schema validity, complete-case accuracy, per-field
accuracy, malformed responses, unsupported facts, failures and mismatches.
Canonical sorting ignores source paths/evidence wording; field scoring aligns
sorted facts without fuzzy matching. Winner priority is case accuracy, mean field
accuracy, then schema-valid rate, otherwise a tie. It never changes production.

Only run this command if you want live, potentially billable API requests and have
`OPENAI_API_KEY` configured; replace the placeholder with your chosen model:

```powershell
python -m evaluation.extraction_experiment --live --alternative-model YOUR_MODEL_ID
```

Alternatively set `OPENAI_EXPERIMENT_MODEL`. The CLI refuses execution without
`--live`, a key, and a different explicit alternative. Offline callers can use
`compare_configurations(model, request=mock_request)`; tests mock both models.

## Safety and failures

- Payment validation protects the higher supplied minimum/preferred reserve across
  the computed timeline. Successful plans have positive payments totaling the
  purchase amount; essential expenses and pending payments are never reduced.
- AI media is untrusted data. Extraction requests strict schema-only JSON and
  validates it locally; missing fields stay unknown. Uncertain/incomplete facts
  are excluded from the operational profile. CSV income entries are caller-confirmed.
- Merge precedence is explicit CSV over confirmed media. Exact duplicates combine
  provenance; unresolved equal-priority media conflicts are reported and omitted.
- Missing optional media can continue with structured data. Failed optional files
  warn; successful extracted facts remain available. Explicit nonblank media
  references are required context: missing/unsupported/unreadable files, missing
  keys, API failures, malformed/incomplete/uncertain output or unresolved media
  conflicts reject that request. A valid empty irrelevant response is permitted.
- Required-data failures never fabricate decisions. Later independent requests
  continue; successful rows retain input order. `run_pipeline(...).failures`
  records rejected requests. The CLI exits 1 for a partial batch, so its CSV may
  have fewer rows than the input. File/header, duplicate-ID and output-access
  failures are fatal (CLI exit 2).
- Only malformed unused `savings_balance` can be skipped with a warning; other
  malformed constraints/obligations reject the request. Logs use stderr, safe
  summaries, stages, types and IDs; no raw API bodies, keys or media are logged.

## Limitations and assumptions

- This is not a complete raw-dataset challenge runner: no joins, exchange-rate
  conversion, lifecycle cancellation/amendment reconstruction, supplied payment
  offer matching, partial-payment eligibility or user-method ranking is implemented.
  Purchase deadlines are not enforced by the current financial algorithms.
- Safe amount is baseline horizon capacity and is **not capped at the purchase
  amount**, unlike the challenge requirement. Selection uses the existing fixed
  priority: full now, baseline plan, wait, flexible adjustment, then no purchase.
- Forecasts include dated income once, pending payments, and explicitly dated
  recurring/essential expenses. Income frequency does not generate further income.
  Expenses support daily/weekly/monthly/yearly recurrence; unknown schedules never
  expand. Savings, history, purchases and the separate flexible-expense collection
  do not add baseline future events; flexible recurring events may be reduced.
- Undated optional events are not forecast. Today's safe amount includes the
  starting balance and intermediate events; future payment/date candidates use
  their date's closing balance while protecting later balances.
- Money assumes one currency. Extraction has no currency field: explicit currency
  and signs remain in evidence; unrepresentable negative amounts stay null and
  uncertain. No trusted reference date is supplied, so relative media dates stay
  unknown. Source event IDs are not reconstructed for spending-change export.
- Offline tests verify interfaces and safety invariants, not live model accuracy,
  prompt-injection resistance or a full-dataset run. No token/cost usage report or
  fabricated transcript is generated by these workflows.
