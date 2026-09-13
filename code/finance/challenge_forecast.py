"""90-day cash-flow forecast over reconciled challenge financial events.

Only CSV facts and confirmed reconciled facts drive the timeline; nothing is
invented. Settled events are assumed already reflected in
`current_available_balance` and are not replayed forward. Pending credits,
cancelled/failed/unrealized events and non-cash events never count (Sec.
"90-Day Safety Check" in problem_statement.md). A blank amount that no
confirmed fact resolves is excluded from the timeline rather than guessed.
"""

from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from extraction.event_reconciliation import find_superseded_events, reconcile_event

_EXCLUDED_STATUSES = ("cancelled", "failed", "unrealized", "settled")
STOP_FLEXIBILITY = ("stoppable", "reducible_or_stoppable")
REDUCE_FLEXIBILITY = ("reducible", "reducible_or_stoppable")


@dataclass(frozen=True)
class FlexibleCandidate:
    """One CSV pending/scheduled event that may be stopped or reduced."""

    event_id: str
    date: date_cls
    amount: Decimal
    category: Optional[str]
    action: str  # "stop" or "reduce"
    reduced_amount: Optional[Decimal] = None

    @property
    def freed(self) -> Decimal:
        return self.amount if self.action == "stop" else self.amount - self.reduced_amount


def _home_amount(converted_event, reconciled) -> Optional[Decimal]:
    if reconciled.amount is None:
        return None
    if converted_event.exchange_rate is None:
        return reconciled.amount
    return reconciled.amount * converted_event.exchange_rate.rate


def build_timeline(
    events, profile, start: date_cls, end: date_cls, facts_by_event: Optional[Dict] = None,
) -> Tuple[List[Tuple[date_cls, Decimal]], List[FlexibleCandidate]]:
    """Reconcile every event and return (timeline, flexible_candidates).

    `timeline` is a list of (date, signed home-currency delta) for events
    that must be reserved or counted within [start, end]. `facts_by_event`
    optionally maps event_id -> iterable of EventFact for AI-assisted
    reconciliation; omit it to reconcile against the CSV facts alone.
    """
    facts_by_event = facts_by_event or {}
    superseded = find_superseded_events(events)
    protect = set(profile.expense_categories_to_protect)
    willing_stop = set(profile.expense_categories_user_is_willing_to_stop)
    willing_reduce = set(profile.expense_categories_user_is_willing_to_reduce)
    timeline = []
    flexible = []
    for converted in events:
        record = converted.event
        if record.event_id in superseded or record.direction == "non_cash":
            continue
        reconciled = reconcile_event(converted, facts_by_event.get(record.event_id, ()))
        if reconciled.status in _EXCLUDED_STATUSES:
            continue
        if reconciled.status == "pending" and record.direction == "credit":
            continue
        when = reconciled.effective_date
        amount = _home_amount(converted, reconciled)
        if when is None or amount is None or when < start or when > end:
            continue
        sign = Decimal(-1) if record.direction == "debit" else Decimal(1)
        timeline.append((when, sign * amount))
        category = record.category
        if record.direction != "debit" or category in protect:
            continue
        if reconciled.flexibility in STOP_FLEXIBILITY and category in willing_stop:
            flexible.append(FlexibleCandidate(record.event_id, when, amount, category, "stop"))
        elif (reconciled.flexibility in REDUCE_FLEXIBILITY and category in willing_reduce
              and converted.home_minimum_allowed_amount is not None
              and converted.home_minimum_allowed_amount < amount):
            flexible.append(FlexibleCandidate(
                record.event_id, when, amount, category, "reduce",
                converted.home_minimum_allowed_amount,
            ))
    timeline.sort(key=lambda item: item[0])
    flexible.sort(key=lambda item: item.freed, reverse=True)
    return timeline, flexible


def _aggregate_by_date(timeline) -> List[Tuple[date_cls, Decimal]]:
    """Net same-date deltas first so intraday ordering never creates a false dip."""
    totals: Dict[date_cls, Decimal] = {}
    for when, delta in timeline:
        totals[when] = totals.get(when, Decimal(0)) + delta
    return sorted(totals.items())


def lowest_balance(balance0: Decimal, timeline) -> Decimal:
    running = balance0
    lowest = running
    for _, delta in _aggregate_by_date(timeline):
        running += delta
        lowest = min(lowest, running)
    return lowest


def amount_safe_to_pay(balance0: Decimal, timeline, minimum_balance: Decimal, requested_amount: Decimal) -> Decimal:
    """Largest amount payable today without breaching minimum_balance, capped at the request."""
    headroom = lowest_balance(balance0, timeline) - minimum_balance
    return max(Decimal(0), min(requested_amount, headroom))


def _safe_paying_on(balance0, timeline, amount, minimum_balance, payment_date) -> bool:
    totals = dict(_aggregate_by_date(timeline))
    totals[payment_date] = totals.get(payment_date, Decimal(0)) - amount
    running = balance0
    for _when, delta in sorted(totals.items()):
        running += delta
        if running < minimum_balance:
            return False
    return True


def earliest_full_payment_date(  # noqa: PLR0913, PLR0917 -- one forecast-query boundary
    balance0: Decimal, timeline, requested_amount: Decimal, minimum_balance: Decimal,
    start: date_cls, end: date_cls,
) -> Optional[date_cls]:
    """First date a single full payment stays safe through the forecast end."""
    candidates = sorted({start, *(when for when, _ in timeline if start <= when <= end)})
    for candidate in candidates:
        if _safe_paying_on(balance0, timeline, requested_amount, minimum_balance, candidate):
            return candidate
    return None


def apply_candidate(timeline, candidate: FlexibleCandidate):
    """Return a new timeline with one flexible event stopped or reduced."""
    adjusted = []
    for when, delta in timeline:
        if when == candidate.date and delta == -candidate.amount:
            if candidate.action == "reduce":
                adjusted.append((when, -candidate.reduced_amount))
            continue
        adjusted.append((when, delta))
    return adjusted
