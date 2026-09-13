# Token usage and cost report

This report covers the run that produced the submitted `output.csv`:

```powershell
python code/main.py --dataset dataset --output output.csv
```

## Model calls

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- | --- | --- |
| — | — | 0 | 0 | 0 | 0 |

**No model calls were made for this run.** AI extraction
(`extraction.event_extractor.extract_event_facts`) is implemented and tested
(see `tests/test_event_extractor.py`), but it is opt-in via `--use-ai-extraction`
and stays off by default so that a submission never makes billable calls
without an explicit choice, and no `OPENAI_API_KEY` is configured in this
environment. Every decision in the submitted `output.csv` was produced
deterministically from `dataset/*.csv` alone: `extraction.event_reconciliation`
reconciles each event against zero AI facts, which is a no-op identity pass
over the CSV record (see `challenge_pipeline._collect_facts`, only invoked
when `use_ai_extraction=True`).

Total / average tokens per request: **N/A (0 requests used a model call)**.
Estimated total and per-request cost: **$0.00** for this run.

## Effect of skipping AI extraction

Scanning `dataset/financial_events.csv`, `dataset/messages.csv` and
`dataset/images.csv` directly:

- 25,342 financial events total; **16** have a blank `amount`.
- 215 messages and 16 images; **39** messages and **16** images carry a
  `related_event_id` (i.e. describe one specific event).
- **19** of those event-linked messages/images point at one of the 16
  blank-amount events (the rest describe lifecycle facts — cancellations,
  settlements, amendments, delays — for events that already have an amount).

Without AI extraction, those 16 blank-amount events are excluded from every
affected user's 90-day cash-flow timeline (`finance.challenge_forecast` never
invents an amount), and any cancellation/settlement/amendment described only
in a message or image is not applied — the CSV `status`/`amount` is used
as-is. This is a conservative, documented simplification, not a silent gap.

## Running with AI extraction enabled

```powershell
$env:OPENAI_API_KEY = "sk-..."
python code/main.py --dataset dataset --output output.csv --use-ai-extraction
```

This calls `extract_event_facts` once per (event, evidence-set) — the pipeline
caches by event_id plus the exact visible message/image IDs, so the same
event is never re-extracted across a user's multiple requests
(`challenge_pipeline._collect_facts`). With `OPENAI_MODEL` unset, the default
production model is `gpt-4o-mini`. Based on the ~55 unique event/evidence
pairs above (upper bound before caching collapses repeats), expect on the
order of 55 calls; re-run with `--use-ai-extraction` and re-generate this
table with the true `UsageRecord` totals from
`ChallengePipelineResult.usage` (provider, model, input/output tokens, and
call success are recorded for every call, successful or not) before
resubmitting if you choose to enable it.
