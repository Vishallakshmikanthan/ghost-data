"""Ghost Data Still Exists - Reconstruction Models & Data Contracts.

Defines typed, reusable structures for temporal state reconstruction:
- Normalized operational events
- Point-in-time resource snapshots
- Reconstructed resource state assessments
- Active data flow and recreation pathway assessments
- Backup archive and restore risk assessments
- Comprehensive per-case reconstruction reports
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class ReconstructedState(str, Enum):
    """Internal reconstructed states for resources at audit cutoff."""
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    PRESENT_WITH_EXPOSED_PII = "PRESENT_WITH_EXPOSED_PII"
    REDACTED = "REDACTED"
    ANONYMIZED = "ANONYMIZED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class EvidenceStrength(str, Enum):
    """Classification of evidence strength and grounding for reconstructed states."""
    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"      # Direct snapshot / dump / scan at or near cutoff
    DERIVED_EVENT_CHAIN = "DERIVED_EVENT_CHAIN"    # Verified progression from baseline via admissible events
    REPORTED_UNCONFIRMED = "REPORTED_UNCONFIRMED"  # Claimed in log/status without independent verification
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE" # Stale snapshot without subsequent coverage
    AMBIGUOUS = "AMBIGUOUS"                        # Irreconcilable conflicting claims


class FlowState(str, Enum):
    """Operational state of an integration or synchronization data flow."""
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class RestoreState(str, Enum):
    """Operational state of a backup restore pathway."""
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass
class NormalizedEvent:
    """An operational event normalized from logs, activities, audits, or document claims."""
    event_id: str
    event_type: str
    system_id: str
    resource_id: str
    timestamp: str
    subject_ids: List[str] = field(default_factory=list)
    capture_timestamp: Optional[str] = None
    reported_outcome: str = "UNKNOWN"  # SUCCESS, FAILED, PARTIAL, UNKNOWN
    evidence_id: str = ""
    source_type: str = "activity"  # activity, file, audit, sql_snapshot
    source_origin: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    is_temporally_eligible: bool = True
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceSnapshot:
    """A resource observation in a database snapshot, export, or point-in-time capture."""
    resource_id: str
    system_id: str
    snapshot_time: str
    evidence_id: str
    observed_state: str  # PRESENT, ABSENT, REDACTED, ANONYMIZED, ACTIVE, BLOCKED, DISABLED
    subject_reference: Optional[str] = None
    resource_category: Optional[str] = None
    identifiers_present: List[str] = field(default_factory=list)
    source_type: str = "sql_snapshot"  # sql_snapshot, json_bundle, txt_record, log, pdf
    is_replica: bool = False
    replica_type: Optional[str] = None  # primary, failover_replica, regional_replica, read_replica, reporting_replica
    raw_record: Dict[str, Any] = field(default_factory=dict)
    limitations: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceStateAssessment:
    """Reconstructed state of a single resource at a given case's audit cutoff."""
    subject_id: str
    system_id: str
    resource_id: str
    state: str  # PRESENT, ABSENT, PRESENT_WITH_EXPOSED_PII, REDACTED, ANONYMIZED, UNKNOWN, CONFLICTING
    resource_category: Optional[str] = None
    exposed_identifiers: List[str] = field(default_factory=list)
    supporting_evidence_ids: List[str] = field(default_factory=list)
    contradicting_evidence_ids: List[str] = field(default_factory=list)
    temporal_cutoff: str = ""
    confidence: str = EvidenceStrength.DIRECT_OBSERVATION.value
    uncertainty_notes: List[str] = field(default_factory=list)
    event_chain: List[str] = field(default_factory=list)
    replica_observations: Dict[str, str] = field(default_factory=dict)
    replication_drift_detected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DataFlowAssessment:
    """Reconstructed operational state and recreation risk of a data flow / connector."""
    flow_id: str
    source_system: str
    target_system: str
    operational_state: str  # ACTIVE, DISABLED, UNKNOWN
    recreation_risk: str  # HIGH, MEDIUM, LOW, NONE
    pathway_type: str  # SYNC, INDEXING, IMPORT, REPLICATION, EXPORT
    subject_reference: Optional[str] = None
    affected_resources: List[str] = field(default_factory=list)
    activation_evidence_ids: List[str] = field(default_factory=list)
    deactivation_evidence_ids: List[str] = field(default_factory=list)
    temporal_cutoff: str = ""
    uncertainty: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RestoreRiskAssessment:
    """Reconstructed assessment of backup archive retention and restore pathways."""
    archive_id: str
    system_id: str = "backup_service"
    operational_state: str = RestoreState.ACTIVE.value  # ACTIVE, BLOCKED, UNKNOWN
    recreation_risk: str = "ACTIVE_RESTORE_RISK"  # ACTIVE_RESTORE_RISK, RESTORE_BLOCKED, NO_RISK, UNKNOWN
    subject_reference: Optional[str] = None
    archive_path: str = ""
    restore_allowed: bool = True
    supporting_evidence_ids: List[str] = field(default_factory=list)
    contradicting_evidence_ids: List[str] = field(default_factory=list)
    temporal_cutoff: str = ""
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconstructionReport:
    """Typed audit report capturing the reconstructed state of the world at audit_at."""
    case_id: str
    audit_at: str
    subject_id: Optional[str] = None
    subject_identifiers: Set[str] = field(default_factory=set)
    events: List[NormalizedEvent] = field(default_factory=list)
    resource_assessments: Dict[str, ResourceStateAssessment] = field(default_factory=dict)
    data_flow_assessments: Dict[str, DataFlowAssessment] = field(default_factory=dict)
    restore_risk_assessments: Dict[str, RestoreRiskAssessment] = field(default_factory=dict)
    dependency_edges: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        # Convert set of subject_identifiers to sorted list for JSON serialization
        res["subject_identifiers"] = sorted(list(self.subject_identifiers))
        return res
