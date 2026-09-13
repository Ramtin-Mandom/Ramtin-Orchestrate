"""Orchestrate the challenge dataset into a completed output.csv.

AI extraction is opt-in (`use_ai_extraction=True`): it costs real API calls
and time, so a default run stays fully deterministic and free, reconciling
each event against its CSV facts alone (facts_by_event is then empty, which
`reconcile_event` treats as a no-op identity pass). Enable it once an
OPENAI_API_KEY is configured to also resolve blank amounts and status
changes described only in messages/images.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from decision.challenge_decision import ChallengeDecision, decide_request
from extraction.event_extractor import UsageRecord, extract_event_facts
from loaders import load_dataset
from output.challenge_writer import write_challenge_csv

PathLike = Union[str, Path]


@dataclass(frozen=True)
class ChallengePipelineResult:
    decisions: Tuple[ChallengeDecision, ...]
    usage: Tuple[UsageRecord, ...]


def _cache_key(event):
    """Same event_id plus the exact evidence visible in this request's scope.

    A message/image can be scoped to one request_id, so the same event_id may
    expose different evidence across a user's requests; keying on the visible
    IDs (not just event_id) still skips a repeat call whenever the evidence is
    identical, without ever reusing facts extracted from different media.
    """
    return (
        event.event.event_id,
        tuple(sorted(item.message_id for item in event.messages)),
        tuple(sorted(item.image_id for item in event.images)),
    )


def _collect_facts(context, extractor: Optional[Callable], cache: dict, usage: list):
    """Extract once per (event, visible evidence); cache across a user's requests."""
    facts_by_event = defaultdict(list)
    for event in context.events:
        key = _cache_key(event)
        if key not in cache:
            confirmed = []
            for source in extract_event_facts(event, context, request=extractor):
                if source.usage is not None:
                    usage.append(source.usage)
                confirmed.extend(fact for fact in source.facts if fact.certainty == "confirmed")
            cache[key] = confirmed
        facts_by_event[event.event.event_id].extend(cache[key])
    return facts_by_event


def run_challenge_pipeline(
    dataset_root: PathLike, output_path: PathLike, *,
    use_ai_extraction: bool = False, extractor: Optional[Callable] = None,
    horizon_days: int = 90,
) -> ChallengePipelineResult:
    """Load the challenge dataset, decide every request, and write output_path.

    Requests are processed in `requests.csv` order; every request_id gets
    exactly one row. With use_ai_extraction, each event's messages/images are
    sent through extraction.event_extractor before reconciliation, cached by
    event_id since the same event recurs across every request for its user;
    otherwise reconciliation runs against the CSV facts only (still fully
    correct for events with no lifecycle changes described in media).
    """
    dataset = load_dataset(dataset_root)
    decisions = []
    usage: list = []
    cache: dict = {}
    for context in dataset.contexts:
        facts_by_event = {}
        if use_ai_extraction:
            facts_by_event = _collect_facts(context, extractor, cache, usage)
        decisions.append(decide_request(context, facts_by_event, horizon_days))
    write_challenge_csv(decisions, output_path)
    return ChallengePipelineResult(tuple(decisions), tuple(usage))
