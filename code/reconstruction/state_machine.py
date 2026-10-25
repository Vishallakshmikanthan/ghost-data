"""Ghost Data Still Exists - Deterministic Resource State Machine.

Executes formal state transitions along chronologically ordered timelines
to determine resource states at audit_at, distinguishing established presence,
established absence, unconfirmed claims, and replication residuals.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from code.reconstruction.models import (
    EvidenceStrength,
    NormalizedEvent,
    ReconstructedState,
    ResourceStateAssessment,
)

logger = logging.getLogger(__name__)


class ResourceStateMachine:
    """Reconstructs the state of a resource at audit_at by executing valid timeline transitions."""

    @staticmethod
    def reconstruct_resource_state(
        resource_id: str,
        system_id: str,
        subject_id: str,
        timeline: List[NormalizedEvent],
        audit_at: str,
        resource_category: Optional[str] = None,
    ) -> ResourceStateAssessment:
        """Derive the state of a resource by walking its ordered event timeline."""
        if not timeline:
            return ResourceStateAssessment(
                subject_id=subject_id,
                system_id=system_id,
                resource_id=resource_id,
                state=ReconstructedState.UNKNOWN.value,
                resource_category=resource_category,
                temporal_cutoff=audit_at,
                confidence=EvidenceStrength.INSUFFICIENT_EVIDENCE.value,
                uncertainty_notes=["No admissible timeline events found for resource before audit cutoff"],
            )

        current_state = ReconstructedState.UNKNOWN.value
        confidence = EvidenceStrength.INSUFFICIENT_EVIDENCE.value
        exposed_identifiers: Set[str] = set()
        supporting_evidence_ids: List[str] = []
        contradicting_evidence_ids: List[str] = []
        uncertainty_notes: List[str] = []
        event_chain: List[str] = []

        last_observed_state: Optional[str] = None

        for ev in timeline:
            ev_desc = f"[{ev.timestamp}] {ev.event_type} on {ev.system_id}:{ev.resource_id} (result={ev.reported_outcome}, src={ev.source_type}, ev={ev.evidence_id})"
            event_chain.append(ev_desc)

            obs_state = ev.attributes.get("observed_state")
            cat = ev.attributes.get("data_category") or resource_category
            if cat and not resource_category:
                resource_category = cat

            # Collect any direct identifiers observed
            for sid in ev.subject_ids:
                exposed_identifiers.add(sid)

            # 1. Direct observations (snapshots, file claims, logs)
            if ev.event_type in ("OBSERVE_RECORD", "SNAPSHOT_OBSERVATION"):
                if obs_state == "ABSENT":
                    current_state = ReconstructedState.ABSENT.value
                    confidence = EvidenceStrength.DIRECT_OBSERVATION.value
                    last_observed_state = "ABSENT"
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                elif obs_state == "PRESENT":
                    # Record is directly observed as present
                    current_state = ReconstructedState.PRESENT.value
                    confidence = EvidenceStrength.DIRECT_OBSERVATION.value
                    last_observed_state = "PRESENT"
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                elif obs_state == "REDACTED":
                    current_state = ReconstructedState.REDACTED.value
                    confidence = EvidenceStrength.DIRECT_OBSERVATION.value
                    last_observed_state = "REDACTED"
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                elif obs_state == "ANONYMIZED":
                    current_state = ReconstructedState.ANONYMIZED.value
                    confidence = EvidenceStrength.DIRECT_OBSERVATION.value
                    last_observed_state = "ANONYMIZED"
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                elif obs_state in ("ACTIVE", "DISABLED", "BLOCKED"):
                    current_state = obs_state
                    confidence = EvidenceStrength.DIRECT_OBSERVATION.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)

            # 2. Deletion operations
            elif ev.event_type == "DELETE_RECORD":
                if ev.reported_outcome == "SUCCESS":
                    # If this was reported success, but earlier we saw present
                    if current_state == ReconstructedState.PRESENT.value:
                        current_state = ReconstructedState.ABSENT.value
                        confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                        if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ev.evidence_id)
                    else:
                        current_state = ReconstructedState.ABSENT.value
                        confidence = EvidenceStrength.REPORTED_UNCONFIRMED.value
                        if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ev.evidence_id)
                elif ev.reported_outcome in ("FAILED", "PARTIAL"):
                    # Failed or partial deletion keeps record PRESENT
                    current_state = ReconstructedState.PRESENT.value
                    confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                    uncertainty_notes.append(f"Deletion operation reported {ev.reported_outcome}")
                    if ev.evidence_id and ev.evidence_id not in contradicting_evidence_ids:
                        contradicting_evidence_ids.append(ev.evidence_id)

            # 3. Anonymization operations
            elif ev.event_type == "ANONYMIZE_RECORD":
                if ev.reported_outcome == "SUCCESS":
                    current_state = ReconstructedState.ANONYMIZED.value
                    confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                else:
                    current_state = ReconstructedState.PRESENT.value
                    uncertainty_notes.append(f"Anonymization operation reported {ev.reported_outcome}")

            # 4. Redaction operations
            elif ev.event_type == "REDACT_RECORD":
                if ev.reported_outcome == "SUCCESS":
                    current_state = ReconstructedState.REDACTED.value
                    confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
                else:
                    current_state = ReconstructedState.PRESENT.value
                    uncertainty_notes.append(f"Redaction operation reported {ev.reported_outcome}")

            # 5. Data Flow or Restore operations
            elif ev.event_type == "DISABLE_DATA_FLOW":
                if ev.reported_outcome == "SUCCESS":
                    current_state = ReconstructedState.DISABLED.value
                    confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)
            elif ev.event_type == "BLOCK_RESTORE":
                if ev.reported_outcome == "SUCCESS":
                    current_state = ReconstructedState.BLOCKED.value
                    confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                    if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                        supporting_evidence_ids.append(ev.evidence_id)

            # 6. Recreation pathways (sync / index / restore after prior deletion)
            elif ev.event_type in ("SYNC_RECORD", "INDEX_RECORD", "RESTORE_BACKUP"):
                if ev.reported_outcome == "SUCCESS":
                    if current_state == ReconstructedState.ABSENT.value:
                        # Recreation occurred! Record brought back to PRESENT
                        current_state = ReconstructedState.PRESENT.value
                        confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                        uncertainty_notes.append(
                            f"Record recreated after prior deletion by {ev.event_type} at {ev.timestamp}"
                        )
                        if ev.evidence_id and ev.evidence_id not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ev.evidence_id)

        # Distinguish PRESENT vs PRESENT_WITH_EXPOSED_PII if applicable
        final_state = current_state
        if final_state == ReconstructedState.PRESENT.value:
            # Check if DIRECT prohibited PII (name, email, phone, hash) is exposed
            direct_pii = [
                i for i in exposed_identifiers
                if ("@" in i or i.startswith("+") or len(i) == 64 or (not i.startswith("user_") and len(i.split()) >= 2))
            ]
            if resource_category == "ACCOUNT_TOMBSTONE" and direct_pii:
                final_state = ReconstructedState.PRESENT_WITH_EXPOSED_PII.value
            elif resource_category in ("SUPPORT_MESSAGE", "ANALYTICS_PROFILE") and direct_pii:
                final_state = ReconstructedState.PRESENT_WITH_EXPOSED_PII.value

        return ResourceStateAssessment(
            subject_id=subject_id,
            system_id=system_id,
            resource_id=resource_id,
            state=final_state,
            resource_category=resource_category,
            exposed_identifiers=sorted(list(exposed_identifiers)),
            supporting_evidence_ids=supporting_evidence_ids,
            contradicting_evidence_ids=contradicting_evidence_ids,
            temporal_cutoff=audit_at,
            confidence=confidence,
            uncertainty_notes=uncertainty_notes,
            event_chain=event_chain,
        )
