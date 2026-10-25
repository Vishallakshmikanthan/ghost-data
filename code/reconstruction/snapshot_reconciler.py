"""Ghost Data Still Exists - Replica & Snapshot Reconciliation Layer.

Reconciles observations across primary database snapshots, read replicas,
failover replicas, regional replicas, and reporting replicas to detect
replication drift, stale residual copies, and unpropagated deletions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import compare_timestamps, is_at_or_before
from code.reconstruction.models import (
    EvidenceStrength,
    NormalizedEvent,
    ReconstructedState,
    ResourceSnapshot,
    ResourceStateAssessment,
)

logger = logging.getLogger(__name__)


@dataclass
class SnapshotReconciliationResult:
    """Detailed reconciliation analysis across primary and replica observations."""
    resource_id: str
    primary_state: Optional[str] = None
    replica_states: Dict[str, str] = field(default_factory=dict)
    primary_evidence_id: Optional[str] = None
    replica_evidence_ids: Dict[str, str] = field(default_factory=dict)
    drift_detected: bool = False
    drift_summary: Optional[str] = None
    reconciled_state: str = ReconstructedState.UNKNOWN.value
    confidence: str = EvidenceStrength.DIRECT_OBSERVATION.value
    supporting_evidence_ids: List[str] = field(default_factory=list)
    contradicting_evidence_ids: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)


class SnapshotReconciler:
    """Reconciles multi-database snapshot observations and detects replication drift."""

    @staticmethod
    def reconcile_resource_snapshots(
        resource_id: str,
        system_id: str,
        snapshots: List[ResourceSnapshot],
        subsequent_events: List[NormalizedEvent],
        audit_at: str,
    ) -> SnapshotReconciliationResult:
        """Reconcile all admissible snapshots for a resource against subsequent operational events."""
        result = SnapshotReconciliationResult(resource_id=resource_id)

        if not snapshots:
            result.diagnostics.append("No snapshots available for resource")
            return result

        # Filter snapshots strictly against audit_at
        admissible_snaps = [
            s for s in snapshots if is_at_or_before(s.snapshot_time, audit_at)
        ]
        if not admissible_snaps:
            result.diagnostics.append("All snapshots occurred after audit cutoff")
            return result

        # Group by replica type or source table
        snaps_by_role: Dict[str, List[ResourceSnapshot]] = {}
        for s in admissible_snaps:
            role = s.replica_type or ("replica" if s.is_replica else "primary")
            snaps_by_role.setdefault(role, []).append(s)

        # 1. Determine primary state
        latest_primary: Optional[ResourceSnapshot] = None
        if "primary" in snaps_by_role:
            primary_list = sorted(snaps_by_role["primary"], key=lambda x: x.snapshot_time)
            latest_primary = primary_list[-1]
            result.primary_state = latest_primary.observed_state
            result.primary_evidence_id = latest_primary.evidence_id
            if latest_primary.evidence_id:
                result.supporting_evidence_ids.append(latest_primary.evidence_id)

        # 2. Determine replica states
        for role, role_snaps in snaps_by_role.items():
            if role == "primary":
                continue
            sorted_role = sorted(role_snaps, key=lambda x: x.snapshot_time)
            latest_role = sorted_role[-1]
            result.replica_states[role] = latest_role.observed_state
            result.replica_evidence_ids[role] = latest_role.evidence_id
            if latest_role.evidence_id and latest_role.evidence_id not in result.supporting_evidence_ids:
                result.supporting_evidence_ids.append(latest_role.evidence_id)

        # 3. Check subsequent intervening events between snapshots and audit_at
        ordered_events = sorted(subsequent_events, key=lambda x: x.timestamp)
        post_snap_deletions = [
            e for e in ordered_events
            if e.event_type == "DELETE_RECORD" and e.reported_outcome == "SUCCESS"
        ]

        # 4. Check for replication drift
        # Drift occurs if primary is ABSENT (or deleted), but a replica still contains it as PRESENT
        residual_replicas = [
            role for role, st in result.replica_states.items() if st == "PRESENT"
        ]

        if result.primary_state in ("ABSENT", None) and residual_replicas:
            result.drift_detected = True
            result.drift_summary = (
                f"Replication drift: primary is {result.primary_state or 'absent'}, "
                f"but {residual_replicas} still retain record as PRESENT"
            )
            result.reconciled_state = ReconstructedState.PRESENT.value
            result.confidence = EvidenceStrength.DIRECT_OBSERVATION.value
            result.diagnostics.append(result.drift_summary)
            # The residual replica evidence proves presence
            for r in residual_replicas:
                ev_id = result.replica_evidence_ids.get(r)
                if ev_id and ev_id not in result.supporting_evidence_ids:
                    result.supporting_evidence_ids.append(ev_id)

        elif result.primary_state == "PRESENT":
            # Primary itself still has the record
            # Check if an intervening deletion occurred AFTER the snapshot
            latest_snap_ts = latest_primary.snapshot_time if latest_primary else ""
            intervening = [
                d for d in post_snap_deletions
                if compare_timestamps(d.timestamp, latest_snap_ts) > 0
            ]
            if intervening:
                result.reconciled_state = ReconstructedState.ABSENT.value
                result.confidence = EvidenceStrength.DERIVED_EVENT_CHAIN.value
                result.diagnostics.append(
                    f"Record in primary snapshot at {latest_snap_ts} subsequently deleted at {intervening[-1].timestamp}"
                )
            else:
                result.reconciled_state = ReconstructedState.PRESENT.value
                result.confidence = EvidenceStrength.DIRECT_OBSERVATION.value

        elif result.primary_state == "ABSENT" and not residual_replicas:
            result.reconciled_state = ReconstructedState.ABSENT.value
            result.confidence = EvidenceStrength.DIRECT_OBSERVATION.value

        else:
            # Replicas observed, but primary not present in snapshots
            if residual_replicas:
                result.reconciled_state = ReconstructedState.PRESENT.value
                result.confidence = EvidenceStrength.DIRECT_OBSERVATION.value
            else:
                result.reconciled_state = ReconstructedState.ABSENT.value
                result.confidence = EvidenceStrength.DIRECT_OBSERVATION.value

        return result
