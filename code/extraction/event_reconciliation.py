"""Deterministic reconciliation of one financial event against extracted facts.

Applies the fixed precedence order from problem_statement.md:
1. An explicit cancellation, settlement or amendment.
2. A newer record from the same source.
3. A settled event over an estimate or forecast.
4. The financially safer interpretation when still unresolved.

Only confirmed facts can change a deterministic field; uncertain facts are
kept for provenance but never override the CSV record. Nothing here invents
an amount, date, currency or event; a field that cannot be resolved from the
supplied facts is left as the CSV value (or None) and reported in
`unresolved` instead of guessed.
"""

from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal
from typing import FrozenSet, Iterable, List, Optional, Tuple

from .event_extractor import EventFact

TERMINAL_STATUSES = ("cancelled", "settled", "failed")
CASH_EXCLUDED_STATUSES = ("cancelled", "failed")


class ReconciliationError(ValueError):
    """The supplied event or facts cannot be reconciled deterministically."""


@dataclass(frozen=True)
class ReconciledEvent:
    """Effective view of one event after applying confirmed facts."""

    event_id: str
    status: str
    amount: Optional[Decimal]
    currency: str
    effective_date: Optional[date_cls]
    is_cancelled: bool
    is_settled: bool
    is_recurring: Optional[bool]
    flexibility: str
    amount_source: str
    applied_facts: Tuple[EventFact, ...]
    ignored_facts: Tuple[EventFact, ...]
    unresolved: Tuple[str, ...]

    @property
    def counts_toward_cash_flow(self) -> bool:
        return self.status not in CASH_EXCLUDED_STATUSES


def _select(candidates: Iterable[EventFact]) -> List[EventFact]:
    """Narrow tied candidates by recency, then by settled-over-estimate."""
    candidates = list(candidates)
    if len(candidates) <= 1:
        return candidates
    if all(fact.sent_at is not None for fact in candidates):
        newest = max(fact.sent_at for fact in candidates)
        newest_group = [fact for fact in candidates if fact.sent_at == newest]
        if len(newest_group) == 1:
            return newest_group
        candidates = newest_group
    settled = [fact for fact in candidates if fact.is_settlement]
    if settled and len(settled) < len(candidates):
        return settled
    return candidates


def _safer_prefers_cancelled(base) -> bool:
    """Conservative default: distrust uncertain credits, keep debits reserved."""
    return base.direction == "credit"


def _terminal_groups(confirmed: List[EventFact]):
    groups = {"cancelled": [], "settled": [], "failed": []}
    for fact in confirmed:
        if fact.is_cancellation or fact.status == "cancelled":
            groups["cancelled"].append(fact)
        if fact.is_settlement or fact.status == "settled":
            groups["settled"].append(fact)
        if fact.status == "failed":
            groups["failed"].append(fact)
    return {name: facts for name, facts in groups.items() if facts}


def _resolve_terminal_status(base, groups, unresolved: List[str]):
    if not groups:
        return base.status, [], []
    if len(groups) == 1:
        (name, facts), = groups.items()
        return name, _select(facts), []
    best_per_group = {name: _select(facts) for name, facts in groups.items()}
    timed = {
        name: max((fact.sent_at for fact in facts if fact.sent_at is not None), default=None)
        for name, facts in best_per_group.items()
    }
    known = {name: value for name, value in timed.items() if value is not None}
    if known:
        winner = max(known, key=known.get)
        if list(known.values()).count(known[winner]) == 1:
            ignored = [fact for name, facts in groups.items() if name != winner for fact in facts]
            return winner, best_per_group[winner], ignored
    unresolved.append(
        f"{base.event_id}: conflicting explicit status facts {sorted(groups)} could not be "
        "ranked by recency; applied the safer interpretation"
    )
    prefer_cancelled = _safer_prefers_cancelled(base)
    priority = ("cancelled", "failed", "settled") if prefer_cancelled else ("settled", "failed", "cancelled")
    winner = next(name for name in priority if name in groups)
    ignored = [fact for name, facts in groups.items() if name != winner for fact in facts]
    return winner, best_per_group[winner], ignored


def _fill_amount(base, candidates: List[EventFact]):
    money_facts = [fact for fact in candidates if fact.amount is not None]
    if not money_facts:
        return None, "csv", [], []
    matching = [fact for fact in money_facts if fact.currency in (None, base.currency)]
    mismatched = [fact for fact in money_facts if fact not in matching]
    if not matching:
        note = (f"{base.event_id}: extracted amount currency conflicts with the event's "
                "declared currency; amount left unresolved")
        return None, "csv", mismatched, [note]
    winners = _select(matching)
    if len({fact.amount for fact in winners}) > 1:
        note = (f"{base.event_id}: multiple conflicting confirmed amounts could not be ranked "
                "by recency or settlement; amount left unresolved")
        return None, "csv", mismatched + winners, [note]
    chosen = winners[0]
    ignored = mismatched + [fact for fact in money_facts if fact is not chosen]
    return chosen.amount, f"fact:{chosen.source_type}:{chosen.source_id}", ignored, []


@dataclass
class _State:
    """Mutable working fields threaded through the amendment/delay passes."""

    currency: str
    amount: Optional[Decimal]
    amount_source: str
    effective_date: Optional[date_cls]
    flexibility: str
    applied: List[EventFact]
    ignored: List[EventFact]
    unresolved: List[str]


def _apply_amendment(base, explicit, state: _State) -> None:
    winners = _select(fact for fact in explicit if fact.is_amendment)
    if len(winners) > 1:
        state.unresolved.append(
            f"{base.event_id}: multiple conflicting amendments could not be ranked from the "
            "supplied facts"
        )
        state.ignored.extend(winners)
        return
    if not winners:
        return
    winner = winners[0]
    state.applied.append(winner)
    if winner.amount is not None and winner.currency in (None, state.currency):
        state.amount = winner.amount
        state.amount_source = f"fact:{winner.source_type}:{winner.source_id}"
    elif winner.amount is not None:
        state.unresolved.append(
            f"{base.event_id}: amendment amount currency conflicts with the event's declared "
            "currency; amount left unresolved"
        )
    if winner.date is not None:
        state.effective_date = winner.date
    if winner.flexibility is not None:
        state.flexibility = winner.flexibility


def _apply_delay(base, explicit, state: _State) -> None:
    winners = _select(fact for fact in explicit if fact.is_delay)
    if len(winners) > 1:
        state.unresolved.append(
            f"{base.event_id}: conflicting delay facts could not be ranked from the supplied facts"
        )
        state.ignored.extend(winners)
        return
    if not winners:
        return
    winner = winners[0]
    if winner.date is None:
        state.ignored.append(winner)
        return
    state.applied.append(winner)
    state.effective_date = winner.date


def _recurrence(confirmed, unresolved):
    votes = {fact.is_recurring for fact in confirmed if fact.is_recurring is not None}
    if len(votes) > 1:
        unresolved.append("conflicting recurrence facts could not be ranked from the supplied facts")
        return None
    return next(iter(votes), None)


def reconcile_event(event, facts: Iterable[EventFact]) -> ReconciledEvent:
    """Reconcile one loaders.dataset.ConvertedEvent against its extracted facts."""
    base = event.event
    facts = tuple(facts)
    confirmed = [fact for fact in facts if fact.certainty == "confirmed"]
    ignored = [fact for fact in facts if fact.certainty != "confirmed"]
    unresolved: List[str] = []
    applied: List[EventFact] = []

    groups = _terminal_groups(confirmed)
    status, terminal_applied, terminal_ignored = _resolve_terminal_status(base, groups, unresolved)
    applied.extend(terminal_applied)
    ignored.extend(terminal_ignored)

    explicit = [
        fact for fact in confirmed
        if (fact.is_amendment or fact.is_delay) and fact not in applied
    ]
    passive = [fact for fact in confirmed if fact not in explicit and fact not in applied]

    state = _State(
        currency=base.currency, amount=base.amount, amount_source="csv",
        effective_date=base.settlement_date or base.event_date, flexibility=base.flexibility,
        applied=applied, ignored=ignored, unresolved=unresolved,
    )
    _apply_amendment(base, explicit, state)
    _apply_delay(base, explicit, state)

    if state.amount is None:
        fill_candidates = [fact for fact in explicit + passive if fact not in state.applied]
        filled_amount, filled_source, fill_ignored, fill_unresolved = _fill_amount(base, fill_candidates)
        if filled_amount is not None:
            state.amount, state.amount_source = filled_amount, filled_source
            state.applied.append(next(
                fact for fact in fill_candidates
                if filled_source == f"fact:{fact.source_type}:{fact.source_id}"
            ))
        state.ignored.extend(fill_ignored)
        state.unresolved.extend(fill_unresolved)

    is_recurring = _recurrence(confirmed, state.unresolved)

    return ReconciledEvent(
        event_id=base.event_id, status=status, amount=state.amount, currency=state.currency,
        effective_date=state.effective_date, is_cancelled=status == "cancelled",
        is_settled=status == "settled", is_recurring=is_recurring, flexibility=state.flexibility,
        amount_source=state.amount_source, applied_facts=tuple(state.applied),
        ignored_facts=tuple(fact for fact in state.ignored if fact not in state.applied),
        unresolved=tuple(state.unresolved),
    )


def find_superseded_events(events) -> FrozenSet[str]:
    """Return event_ids superseded by a later linked_event_id lifecycle record.

    Conservative: a record only supersedes the event it links to when both
    share the same cash direction and event_type and the predecessor was
    still pending/scheduled while the successor is settled, cancelled or
    failed. The link alone never implies duplication (AGENTS.md Sec. 6.1);
    non-cash and investment lifecycle links (purchase -> valuation -> sale)
    are always kept as distinct events.
    """
    by_id = {converted.event.event_id: converted.event for converted in events}
    superseded = set()
    for converted in events:
        record = converted.event
        parent_id = record.linked_event_id
        if not parent_id or parent_id not in by_id:
            continue
        parent = by_id[parent_id]
        if record.direction != parent.direction or record.direction == "non_cash":
            continue
        if record.event_type != parent.event_type:
            continue
        if parent.status in ("pending", "scheduled") and record.status in TERMINAL_STATUSES:
            superseded.add(parent.event_id)
    return frozenset(superseded)
