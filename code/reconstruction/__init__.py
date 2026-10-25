"""Ghost Data Still Exists - Temporal Reconstruction & Evidence Correlation Package.

Exports the public Phase 3 reconstruction and correlation interfaces:
- normalize_events
- build_timeline
- reconstruct_resource_state
- reconcile_snapshots
- analyze_data_flows
- analyze_restore_risks
- build_dependency_graph
- reconstruct_case
- reconstruct_batch
"""

from typing import Any, Dict, List, Optional, Set

from code.ingestion.csv_loader import DatasetBundle
from code.ingestion.evidence_store import EvidenceStore
from code.reconstruction.dependency_graph import (
    DependencyEdge,
    DependencyNode,
    ResourceDependencyGraph,
)
from code.reconstruction.event_normalizer import EventNormalizer
from code.reconstruction.flow_analyzer import FlowAnalyzer
from code.reconstruction.models import (
    DataFlowAssessment,
    EvidenceStrength,
    FlowState,
    NormalizedEvent,
    ReconstructedState,
    ReconstructionReport,
    ResourceSnapshot,
    ResourceStateAssessment,
    RestoreRiskAssessment,
    RestoreState,
)
from code.reconstruction.reconstruction_engine import ReconstructionEngine
from code.reconstruction.resource_ledger import LedgerEntry, ResourceLedger
from code.reconstruction.snapshot_reconciler import (
    SnapshotReconciliationResult,
    SnapshotReconciler,
)
from code.reconstruction.state_machine import ResourceStateMachine
from code.reconstruction.timeline_builder import TimelineBuilder
from code.schemas import DeletionRequest, EvidenceClaim, SystemActivityRecord, UserAuditRecord


def normalize_events(
    claims: Optional[List[EvidenceClaim]] = None,
    activities: Optional[List[SystemActivityRecord]] = None,
    audits: Optional[List[UserAuditRecord]] = None,
    audit_at: Optional[str] = None,
    evidence_store: Optional[EvidenceStore] = None,
) -> List[NormalizedEvent]:
    """Convert heterogeneous evidence claims, system activities, and audit records into NormalizedEvents."""
    events: List[NormalizedEvent] = []

    if claims:
        for cl in claims:
            doc = evidence_store.documents.get(cl.evidence_id) if evidence_store else None
            events.append(EventNormalizer.normalize_claim(cl, doc, audit_at))

    if activities:
        for act in activities:
            events.append(EventNormalizer.normalize_activity(act, audit_at))

    if audits:
        for aud in audits:
            events.append(EventNormalizer.normalize_audit_record(aud, audit_at))

    return events


def build_timeline(
    events: List[NormalizedEvent],
    audit_at: Optional[str] = None,
) -> List[NormalizedEvent]:
    """Filter and sort normalized events canonically by (timestamp, source_precedence, event_id)."""
    return TimelineBuilder.filter_and_order(events, audit_at)


def reconstruct_resource_state(
    resource_id: str,
    system_id: str,
    subject_id: str,
    timeline: List[NormalizedEvent],
    audit_at: str,
    resource_category: Optional[str] = None,
) -> ResourceStateAssessment:
    """Execute the resource state machine along an ordered event timeline to determine state at audit_at."""
    return ResourceStateMachine.reconstruct_resource_state(
        resource_id=resource_id,
        system_id=system_id,
        subject_id=subject_id,
        timeline=timeline,
        audit_at=audit_at,
        resource_category=resource_category,
    )


def reconcile_snapshots(
    resource_id: str,
    system_id: str,
    snapshots: List[ResourceSnapshot],
    subsequent_events: List[NormalizedEvent],
    audit_at: str,
) -> SnapshotReconciliationResult:
    """Reconcile observations across primary and replica database snapshots to detect replication drift."""
    return SnapshotReconciler.reconcile_resource_snapshots(
        resource_id=resource_id,
        system_id=system_id,
        snapshots=snapshots,
        subsequent_events=subsequent_events,
        audit_at=audit_at,
    )


def analyze_data_flows(
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
    return FlowAnalyzer.analyze_data_flow(
        flow_id=flow_id,
        events=events,
        audit_at=audit_at,
        source_system=source_system,
        target_system=target_system,
        subject_ref=subject_ref,
        initial_state=initial_state,
        evidence_id=evidence_id,
    )


def analyze_restore_risks(
    archive_id: str,
    events: List[NormalizedEvent],
    audit_at: str,
    subject_ref: Optional[str] = None,
    archive_path: str = "",
    restore_allowed: bool = True,
    evidence_id: str = "",
) -> RestoreRiskAssessment:
    """Determine whether a backup archive presents an active restore risk at audit_at."""
    return FlowAnalyzer.analyze_restore_risk(
        archive_id=archive_id,
        events=events,
        audit_at=audit_at,
        subject_ref=subject_ref,
        archive_path=archive_path,
        restore_allowed=restore_allowed,
        evidence_id=evidence_id,
    )


def build_dependency_graph(
    flow_assessments: Optional[List[DataFlowAssessment]] = None,
    restore_assessments: Optional[List[RestoreRiskAssessment]] = None,
) -> ResourceDependencyGraph:
    """Construct a directed dependency graph from analyzed flows and restore mechanisms."""
    graph = ResourceDependencyGraph()
    if flow_assessments:
        for fa in flow_assessments:
            graph.add_edge(
                source_system=fa.source_system,
                source_resource="primary_profile",
                target_system=fa.target_system,
                target_resource=fa.flow_id,
                relationship="SYNC_DESTINATION",
                is_confirmed=(fa.operational_state == "ACTIVE"),
                evidence_id=fa.activation_evidence_ids[0] if fa.activation_evidence_ids else "",
            )
    if restore_assessments:
        for ra in restore_assessments:
            graph.add_edge(
                source_system="backup_service",
                source_resource=ra.archive_id,
                target_system="account_service",
                target_resource="primary_profile",
                relationship="RESTORE_TARGET",
                is_confirmed=(ra.operational_state == "ACTIVE"),
                evidence_id=ra.supporting_evidence_ids[0] if ra.supporting_evidence_ids else "",
            )
    return graph


def reconstruct_case(
    request: DeletionRequest,
    bundle: DatasetBundle,
    store: EvidenceStore,
) -> ReconstructionReport:
    """Orchestrate temporal state reconstruction for a single DeletionRequest."""
    engine = ReconstructionEngine(bundle, store)
    return engine.reconstruct_case(request)


def reconstruct_batch(
    requests: List[DeletionRequest],
    bundle: DatasetBundle,
    store: EvidenceStore,
) -> List[ReconstructionReport]:
    """Orchestrate temporal state reconstruction for a batch of DeletionRequests."""
    engine = ReconstructionEngine(bundle, store)
    return engine.reconstruct_batch(requests)


__all__ = [
    # Top-level reconstruction handoff functions
    "normalize_events",
    "build_timeline",
    "reconstruct_resource_state",
    "reconcile_snapshots",
    "analyze_data_flows",
    "analyze_restore_risks",
    "build_dependency_graph",
    "reconstruct_case",
    "reconstruct_batch",
    # Core Classes
    "ReconstructionEngine",
    "ResourceStateMachine",
    "SnapshotReconciler",
    "FlowAnalyzer",
    "TimelineBuilder",
    "EventNormalizer",
    "ResourceLedger",
    "ResourceDependencyGraph",
    "LedgerEntry",
    "SnapshotReconciliationResult",
    "DependencyNode",
    "DependencyEdge",
    # Models & Enums
    "NormalizedEvent",
    "ResourceSnapshot",
    "ResourceStateAssessment",
    "DataFlowAssessment",
    "RestoreRiskAssessment",
    "ReconstructionReport",
    "ReconstructedState",
    "EvidenceStrength",
    "FlowState",
    "RestoreState",
]
