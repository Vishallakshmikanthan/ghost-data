"""Ghost Data Still Exists - Deterministic Timeline Builder.

Builds chronologically ordered, temporally admissible timelines for subjects,
resources, and processes with stable tie-breaking, source precedence, and
strict audit-cutoff enforcement.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import compare_timestamps, is_at_or_before
from code.reconstruction.models import NormalizedEvent

logger = logging.getLogger(__name__)

# Precedence for resolving same-timestamp tie-breaking:
# Grounded direct observations supersede reported activities or generic claims
SOURCE_PRECEDENCE: Dict[str, int] = {
    "sql_snapshot": 1,
    "file": 2,
    "activity": 3,
    "audit": 4,
}


def event_sort_key(event: NormalizedEvent) -> Tuple[str, int, str]:
    """Canonical stable sort key: (timestamp, source_precedence, event_id)."""
    prec = SOURCE_PRECEDENCE.get(event.source_type, 9)
    return (event.timestamp, prec, event.event_id)


class TimelineBuilder:
    """Assembles and orders temporal event streams with strict audit cutoff filtering."""

    @staticmethod
    def filter_and_order(
        events: List[NormalizedEvent],
        audit_at: Optional[str] = None,
    ) -> List[NormalizedEvent]:
        """Filter out temporally ineligible events and return canonically ordered list."""
        valid_events: List[NormalizedEvent] = []

        for ev in events:
            # Enforce audit_at cutoff on event timestamp
            if audit_at and ev.timestamp and not is_at_or_before(ev.timestamp, audit_at):
                continue

            # Enforce audit_at cutoff on source capture timestamp if present
            if audit_at and ev.capture_timestamp and not is_at_or_before(ev.capture_timestamp, audit_at):
                continue

            valid_events.append(ev)

        valid_events.sort(key=event_sort_key)
        return valid_events

    @staticmethod
    def build_resource_timelines(
        events: List[NormalizedEvent],
        audit_at: Optional[str] = None,
    ) -> Dict[str, List[NormalizedEvent]]:
        """Group events by resource_id and order each resource's timeline deterministically."""
        filtered = TimelineBuilder.filter_and_order(events, audit_at)
        timelines: Dict[str, List[NormalizedEvent]] = {}

        for ev in filtered:
            if not ev.resource_id:
                continue
            timelines.setdefault(ev.resource_id, []).append(ev)

        return timelines

    @staticmethod
    def detect_timestamp_conflicts(timeline: List[NormalizedEvent]) -> List[str]:
        """Detect cases where multiple contradictory events share identical timestamps."""
        conflicts: List[str] = []
        seen_ts: Dict[str, List[NormalizedEvent]] = {}

        for ev in timeline:
            if ev.timestamp:
                seen_ts.setdefault(ev.timestamp, []).append(ev)

        for ts, ev_list in seen_ts.items():
            if len(ev_list) > 1:
                types = {e.event_type for e in ev_list}
                if ("DELETE_RECORD" in types and "OBSERVE_RECORD" in types) or ("DELETE_RECORD" in types and "UPDATE_RECORD" in types):
                    conflicts.append(
                        f"Timestamp collision at {ts}: contradictory events {[e.event_id for e in ev_list]}"
                    )

        return conflicts
