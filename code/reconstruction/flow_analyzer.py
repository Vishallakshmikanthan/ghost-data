"""Ghost Data Still Exists - Data Flow & Restore Risk Analysis Layer.

Analyzes data synchronization pipelines, indexing connectors, export jobs,
and backup archive restore mechanisms to identify active data recreation pathways
and restore risks at the audit cutoff timestamp.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import is_at_or_before
from code.reconstruction.models import (
    DataFlowAssessment,
    FlowState,
    NormalizedEvent,
    RestoreRiskAssessment,
    RestoreState,
)

logger = logging.getLogger(__name__)


class FlowAnalyzer:
    """Analyzes active integration flows and backup restore access."""

    @staticmethod
    def analyze_data_flow(
        flow_id: str,
        events: List[NormalizedEvent],
        audit_at: str,
        source_system: str = "account_service",
        target_system: str = "search_service",
        subject_ref: Optional[str] = None,
        initial_state: str = FlowState.ACTIVE.value,
        evidence_id: str = "",
    ) -> DataFlowAssessment:
        """Determine whether a synchronization data flow is ACTIVE or DISABLED at audit_at."""
        # Order admissible events
        admissible_events = [
            e for e in events
            if is_at_or_before(e.timestamp, audit_at)
        ]
        admissible_events.sort(key=lambda x: x.timestamp)

        current_state = initial_state
        activation_evs: List[str] = [evidence_id] if evidence_id else []
        deactivation_evs: List[str] = []
        notes: Optional[str] = None

        for ev in admissible_events:
            if ev.event_type == "DISABLE_DATA_FLOW":
                if ev.reported_outcome == "SUCCESS":
                    current_state = FlowState.DISABLED.value
                    if ev.evidence_id and ev.evidence_id not in deactivation_evs:
                        deactivation_evs.append(ev.evidence_id)
                elif ev.reported_outcome == "FAILED":
                    notes = f"Attempted flow disable failed at {ev.timestamp}"
            elif ev.event_type in ("SYNC_RECORD", "INDEX_RECORD", "CHECK_DATA_FLOW"):
                if ev.reported_outcome == "SUCCESS":
                    # Ongoing activity confirms flow is active
                    if current_state != FlowState.DISABLED.value:
                        current_state = FlowState.ACTIVE.value
                    if ev.evidence_id and ev.evidence_id not in activation_evs:
                        activation_evs.append(ev.evidence_id)

        risk = "HIGH" if current_state == FlowState.ACTIVE.value else "NONE"

        return DataFlowAssessment(
            flow_id=flow_id,
            source_system=source_system,
            target_system=target_system,
            operational_state=current_state,
            recreation_risk=risk,
            pathway_type="SYNC",
            subject_reference=subject_ref,
            activation_evidence_ids=activation_evs,
            deactivation_evidence_ids=deactivation_evs,
            temporal_cutoff=audit_at,
            notes=notes,
        )

    @staticmethod
    def analyze_restore_risk(
        archive_id: str,
        events: List[NormalizedEvent],
        audit_at: str,
        subject_ref: Optional[str] = None,
        archive_path: str = "",
        restore_allowed: bool = True,
        evidence_id: str = "",
    ) -> RestoreRiskAssessment:
        """Determine whether a backup archive presents an active restore risk at audit_at."""
        admissible_events = [
            e for e in events
            if is_at_or_before(e.timestamp, audit_at)
        ]
        admissible_events.sort(key=lambda x: x.timestamp)

        current_state = RestoreState.ACTIVE.value if restore_allowed else RestoreState.BLOCKED.value
        supporting_evs: List[str] = [evidence_id] if evidence_id else []
        contradicting_evs: List[str] = []
        notes: Optional[str] = None

        for ev in admissible_events:
            if ev.event_type == "BLOCK_RESTORE":
                if ev.reported_outcome == "SUCCESS":
                    current_state = RestoreState.BLOCKED.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evs:
                        supporting_evs.append(ev.evidence_id)
                elif ev.reported_outcome == "FAILED":
                    notes = f"Block restore attempted but reported FAILED at {ev.timestamp}"
                    if ev.evidence_id and ev.evidence_id not in contradicting_evs:
                        contradicting_evs.append(ev.evidence_id)
            elif ev.event_type == "CHECK_RESTORE_ACCESS":
                if ev.reported_outcome == "SUCCESS" and "blocked" in ev.attributes.get("details", "").lower():
                    current_state = RestoreState.BLOCKED.value
                elif ev.reported_outcome == "SUCCESS" and ("active" in ev.attributes.get("details", "").lower() or "restore" in ev.attributes.get("details", "").lower()):
                    current_state = RestoreState.ACTIVE.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evs:
                        supporting_evs.append(ev.evidence_id)

        risk = "ACTIVE_RESTORE_RISK" if current_state == RestoreState.ACTIVE.value else "RESTORE_BLOCKED"

        return RestoreRiskAssessment(
            archive_id=archive_id,
            system_id="backup_service",
            operational_state=current_state,
            recreation_risk=risk,
            subject_reference=subject_ref,
            archive_path=archive_path,
            restore_allowed=(current_state == RestoreState.ACTIVE.value),
            supporting_evidence_ids=supporting_evs,
            contradicting_evidence_ids=contradicting_evs,
            temporal_cutoff=audit_at,
            notes=notes,
        )
