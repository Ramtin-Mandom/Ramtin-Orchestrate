"""Pure, conservative merging of CSV records and confirmed media facts."""

from copy import deepcopy
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from models import (
    FinancialProfile,
    Income,
    PendingPayment,
    RecurringExpense,
    Transaction,
)

from .media_extractor import ExtractedFact

Record = Union[Income, PendingPayment, RecurringExpense, Transaction]
GROUPS = (
    "transactions", "incomes", "recurring_expenses", "essential_expenses",
    "flexible_expenses", "pending_payments",
)
MEDIA_GROUPS = {
    "income": "incomes", "pending_payment": "pending_payments",
    "recurring_expense": "recurring_expenses", "essential_expense": "essential_expenses",
    "flexible_expense": "flexible_expenses",
}
ALIASES = {
    "source": "description", "expected_date": "date", "next_due_date": "date",
    "due_date": "date",
}


@dataclass(frozen=True)
class FactSource:
    """CSV positions are the only provenance supplied by FinancialProfile."""

    origin: str
    group: Optional[str] = None
    index: Optional[int] = None
    path: Optional[Path] = None
    evidence: Optional[str] = None


@dataclass(frozen=True)
class MergeConflict:
    reason: str
    fields: Tuple[str, ...]
    sources: Tuple[FactSource, ...]
    records: Tuple[Record, ...]


@dataclass(frozen=True)
class MergeResult:
    profile: FinancialProfile
    conflicts: Tuple[MergeConflict, ...]
    ignored_uncertain_facts: Tuple[ExtractedFact, ...]
    ignored_incomplete_facts: Tuple[ExtractedFact, ...]
    warnings: Tuple[str, ...]
    provenance: Dict[Tuple[str, int], Tuple[FactSource, ...]]


@dataclass
class _Entry:
    group: str
    record: Record
    sources: List[FactSource]
    csv: bool


def _normalized(value):
    return " ".join(value.split()).casefold() if isinstance(value, str) else value


def _values(entry):
    return {
        ALIASES.get(item.name, item.name): _normalized(getattr(entry.record, item.name))
        for item in fields(entry.record)
    }


def _family(entry):
    if entry.group in {"recurring_expenses", "essential_expenses", "flexible_expenses",
                       "pending_payments"}:
        return "expense"
    return entry.group


def _exact(left, right):
    return _family(left) == _family(right) and _values(left) == _values(right)


def _related(left, right):
    """No fuzzy names or inferred dates; missing anchors are conservative bridges."""
    if _exact(left, right):
        return True
    if _family(left) != _family(right) or left.group == "transactions":
        return False
    a, b = _values(left), _values(right)
    if a.get("date") and b.get("date") and a["date"] != b["date"]:
        return False
    names = a.get("description"), b.get("description")
    if all(names):
        return names[0] == names[1]
    # An unnamed event needs both date and amount to match another record.
    return bool(a.get("date") and a.get("date") == b.get("date")
                and a["amount"] == b["amount"])


def _components(entries):
    remaining = set(range(len(entries)))
    while remaining:
        pending = [min(remaining)]
        component = set()
        while pending:
            index = pending.pop()
            if index not in remaining:
                continue
            remaining.remove(index)
            component.add(index)
            pending.extend(other for other in sorted(remaining)
                           if _related(entries[index], entries[other]))
        yield [entries[index] for index in sorted(component)]


def _disagreements(entries):
    names = sorted({name for entry in entries for name in _values(entry)})
    return tuple(name for name in names if len({
        _values(entry).get(name) for entry in entries
        if _values(entry).get(name) not in (None, "")
    }) > 1)


def _sources(entries):
    return tuple(dict.fromkeys(source for entry in entries for source in entry.sources))


def _conflict(reason, names, entries):
    return MergeConflict(reason, names, _sources(entries),
                         tuple(deepcopy(entry.record) for entry in entries))


def _fill(base, candidates):
    """Fill only fields on the retained record, with unanimously supported values."""
    updates = {}
    for item in fields(base.record):
        if getattr(base.record, item.name) not in (None, ""):
            continue
        canonical = ALIASES.get(item.name, item.name)
        choices = {}
        for entry in candidates:
            for candidate_field in fields(entry.record):
                if ALIASES.get(candidate_field.name, candidate_field.name) != canonical:
                    continue
                value = getattr(entry.record, candidate_field.name)
                if value not in (None, ""):
                    choices.setdefault(_normalized(value), value)
        if len(choices) == 1:
            updates[item.name] = next(iter(choices.values()))
    return _Entry(base.group, replace(base.record, **updates),
                  list(_sources([base, *candidates])), base.csv)


def _resolve(component, conflicts):
    csv = [entry for entry in component if entry.csv]
    media = [entry for entry in component if not entry.csv]
    if not media:
        return csv
    if len(csv) > 1:
        conflicts.append(_conflict("ambiguous_csv_match", (), component))
        return csv
    if not csv:
        disagreement = _disagreements(media)
        if len({entry.group for entry in media}) > 1:
            disagreement += ("expense_group",)
        if disagreement:
            conflicts.append(_conflict("unresolved_media_conflict", disagreement, media))
            return []
        return [_fill(media[0], media[1:])]
    base = csv[0]
    compatible = []
    for entry in media:
        disagreement = _disagreements([base, entry])
        if base.group != entry.group:
            disagreement += ("expense_group",)
        if disagreement:
            conflicts.append(_conflict("csv_precedence", disagreement, [base, entry]))
        else:
            compatible.append(entry)
    disagreement = _disagreements(compatible)
    if disagreement:
        conflicts.append(_conflict("unresolved_media_fields", disagreement,
                                   [base, *compatible]))
    return [_fill(base, compatible)]


def _csv_entries(profile, conflicts):
    entries = []
    for group in GROUPS:
        for index, record in enumerate(getattr(profile, group)):
            entry = _Entry(group, deepcopy(record), [FactSource("csv", group, index)], True)
            duplicate = next((old for old in entries if _exact(old, entry)), None)
            if duplicate is None:
                entries.append(entry)
            else:
                duplicate.sources.extend(entry.sources)
    # Resolve same-anchor CSV repetitions before media matching. Distinct
    # known dates remain independent, even when an undated row bridges them.
    resolved = []
    for component in _components(entries):
        dates = {_values(entry).get("date") for entry in component}
        dates.discard(None)
        if len(component) == 1 or len(dates) > 1:
            resolved.extend(component)
            continue
        disagreement = _disagreements(component)
        if len({entry.group for entry in component}) > 1:
            disagreement += ("expense_group",)
        if disagreement:
            conflicts.append(_conflict("csv_first_occurrence", disagreement, component))
            resolved.append(component[0])
        else:
            resolved.append(_fill(component[0], component[1:]))
    return sorted(resolved, key=lambda entry: (
        GROUPS.index(entry.sources[0].group), entry.sources[0].index,
    ))


def merge_profile(
    csv_profile: FinancialProfile, media_facts: Iterable[ExtractedFact] = (),
) -> MergeResult:
    """Return an independent existing FinancialProfile with merge diagnostics.

    CSV order is retained, then media input order. Exact normalized duplicates
    are combined, including expense groups. Logical identity is family plus
    exact normalized name and compatible date; unnamed records require equal
    known date and amount. Distinct known dates are distinct events. Ambiguous
    bridges to multiple CSV records omit media. Purchase facts have no profile
    collection and are warned about rather than converted into expenses.
    Conflicting same-anchor CSV repetitions keep the first explicit occurrence
    with a conflict report. Scalar profile values are copied without inference.
    """
    profile = deepcopy(csv_profile)
    conflicts = []
    entries = _csv_entries(csv_profile, conflicts)
    uncertain, incomplete, warnings = [], [], []
    for index, fact in enumerate(media_facts):
        if fact.certainty != "confirmed":
            uncertain.append(fact)
            continue
        if fact.amount is None:
            incomplete.append(fact)
            continue
        if fact.kind not in MEDIA_GROUPS:
            warnings.append(f"media[{index}]: {fact.kind} has no profile collection")
            continue
        record = deepcopy(fact.record)
        text_field = "source" if isinstance(record, Income) else "description"
        # Existing models use an empty string for missing display text, and
        # spending adjustment code calls description.strip(). The original
        # extracted wrapper continues to retain None without modification.
        if getattr(record, text_field) is None:
            record = replace(record, **{text_field: ""})
        entries.append(_Entry(MEDIA_GROUPS[fact.kind], record,
                              [FactSource("media", index=index, path=fact.source_path,
                                          evidence=fact.evidence)], False))
    resolved = []
    for component in _components(entries):
        resolved.extend(_resolve(component, conflicts))
    # Components can interleave CSV positions: restore all original positions.
    positions = {id(entry): index for index, entry in enumerate(entries)}
    source_positions = {source: positions[id(entry)]
                        for entry in entries for source in entry.sources}
    resolved.sort(key=lambda entry: min(source_positions[source] for source in entry.sources))
    provenance = {}
    for group in GROUPS:
        setattr(profile, group, [])
    for entry in resolved:
        records = getattr(profile, entry.group)
        provenance[(entry.group, len(records))] = tuple(entry.sources)
        records.append(entry.record)
    return MergeResult(profile, tuple(conflicts), tuple(uncertain), tuple(incomplete),
                       tuple(warnings), provenance)
