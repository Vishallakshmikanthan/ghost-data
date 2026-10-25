"""Ghost Data Still Exists - Shared Data Contracts and Enums.

This module serves as the single source of truth for all data structures,
allowed enum values, phase/operation/state pairings, and canonical ordering
rules for the deletion-audit system.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class DeletionStatus(str, Enum):
    """Allowed top-level deletion statuses."""
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @classmethod
    def values(cls) -> Set[str]:
        return {item.value for item in cls}


class Phase(str, Enum):
    """Allowed workflow phases in strictly ordered execution."""
    FINDING = "FINDING"
    REMEDIATION = "REMEDIATION"
    VERIFICATION = "VERIFICATION"

    @classmethod
    def values(cls) -> Set[str]:
        return {item.value for item in cls}


class Operation(str, Enum):
    """Allowed operations across all phases as defined in problem_statement.md."""
    # Findings
    REPORT_REMAINING_RECORD = "REPORT_REMAINING_RECORD"
    REPORT_EXPOSED_PII = "REPORT_EXPOSED_PII"
    REPORT_ACTIVE_DATA_FLOW = "REPORT_ACTIVE_DATA_FLOW"
    REPORT_RESTORE_RISK = "REPORT_RESTORE_RISK"

    # Remediation
    DISABLE_DATA_FLOW = "DISABLE_DATA_FLOW"
    BLOCK_RESTORE = "BLOCK_RESTORE"
    DELETE_RECORD = "DELETE_RECORD"
    REDACT_RECORD = "REDACT_RECORD"
    ANONYMIZE_RECORD = "ANONYMIZE_RECORD"

    # Verification
    VERIFY_RECORD_ABSENT = "VERIFY_RECORD_ABSENT"
    VERIFY_RECORD_PRESENT = "VERIFY_RECORD_PRESENT"
    VERIFY_RECORD_REDACTED = "VERIFY_RECORD_REDACTED"
    VERIFY_RECORD_ANONYMIZED = "VERIFY_RECORD_ANONYMIZED"
    VERIFY_DATA_FLOW_DISABLED = "VERIFY_DATA_FLOW_DISABLED"
    VERIFY_RESTORE_BLOCKED = "VERIFY_RESTORE_BLOCKED"

    @classmethod
    def values(cls) -> Set[str]:
        return {item.value for item in cls}


class State(str, Enum):
    """Allowed states for operations."""
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    REDACTED = "REDACTED"
    ANONYMIZED = "ANONYMIZED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"

    @classmethod
    def values(cls) -> Set[str]:
        return {item.value for item in cls}


# Exact mapping of Phase -> Allowed Operations
PHASE_OPERATIONS: Dict[str, Set[str]] = {
    Phase.FINDING.value: {
        Operation.REPORT_REMAINING_RECORD.value,
        Operation.REPORT_EXPOSED_PII.value,
        Operation.REPORT_ACTIVE_DATA_FLOW.value,
        Operation.REPORT_RESTORE_RISK.value,
    },
    Phase.REMEDIATION.value: {
        Operation.DISABLE_DATA_FLOW.value,
        Operation.BLOCK_RESTORE.value,
        Operation.DELETE_RECORD.value,
        Operation.REDACT_RECORD.value,
        Operation.ANONYMIZE_RECORD.value,
    },
    Phase.VERIFICATION.value: {
        Operation.VERIFY_RECORD_ABSENT.value,
        Operation.VERIFY_RECORD_PRESENT.value,
        Operation.VERIFY_RECORD_REDACTED.value,
        Operation.VERIFY_RECORD_ANONYMIZED.value,
        Operation.VERIFY_DATA_FLOW_DISABLED.value,
        Operation.VERIFY_RESTORE_BLOCKED.value,
    },
}

# Exact mapping of Operation -> Required State
OPERATION_STATE: Dict[str, str] = {
    # Findings
    Operation.REPORT_REMAINING_RECORD.value: State.PRESENT.value,
    Operation.REPORT_EXPOSED_PII.value: State.PRESENT.value,
    Operation.REPORT_ACTIVE_DATA_FLOW.value: State.ACTIVE.value,
    Operation.REPORT_RESTORE_RISK.value: State.ACTIVE.value,

    # Remediation
    Operation.DISABLE_DATA_FLOW.value: State.DISABLED.value,
    Operation.BLOCK_RESTORE.value: State.BLOCKED.value,
    Operation.DELETE_RECORD.value: State.ABSENT.value,
    Operation.REDACT_RECORD.value: State.REDACTED.value,
    Operation.ANONYMIZE_RECORD.value: State.ANONYMIZED.value,

    # Verification
    Operation.VERIFY_RECORD_ABSENT.value: State.ABSENT.value,
    Operation.VERIFY_RECORD_PRESENT.value: State.PRESENT.value,
    Operation.VERIFY_RECORD_REDACTED.value: State.REDACTED.value,
    Operation.VERIFY_RECORD_ANONYMIZED.value: State.ANONYMIZED.value,
    Operation.VERIFY_DATA_FLOW_DISABLED.value: State.DISABLED.value,
    Operation.VERIFY_RESTORE_BLOCKED.value: State.BLOCKED.value,
}

# Strict phase ordering: FINDING (1) -> REMEDIATION (2) -> VERIFICATION (3)
PHASE_ORDER: Dict[str, int] = {
    Phase.FINDING.value: 1,
    Phase.REMEDIATION.value: 2,
    Phase.VERIFICATION.value: 3,
}

# Strict operation ordering within each phase
OPERATION_ORDER: Dict[str, int] = {
    # FINDING
    Operation.REPORT_REMAINING_RECORD.value: 1,
    Operation.REPORT_EXPOSED_PII.value: 2,
    Operation.REPORT_ACTIVE_DATA_FLOW.value: 3,
    Operation.REPORT_RESTORE_RISK.value: 4,

    # REMEDIATION
    Operation.DISABLE_DATA_FLOW.value: 1,
    Operation.BLOCK_RESTORE.value: 2,
    Operation.DELETE_RECORD.value: 3,
    Operation.REDACT_RECORD.value: 4,
    Operation.ANONYMIZE_RECORD.value: 5,

    # VERIFICATION
    Operation.VERIFY_RECORD_ABSENT.value: 1,
    Operation.VERIFY_RECORD_PRESENT.value: 2,
    Operation.VERIFY_RECORD_REDACTED.value: 3,
    Operation.VERIFY_RECORD_ANONYMIZED.value: 4,
    Operation.VERIFY_DATA_FLOW_DISABLED.value: 5,
    Operation.VERIFY_RESTORE_BLOCKED.value: 6,
}

# Exactly seven required keys per step
REQUIRED_STEP_KEYS = (
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)

# Standard CSV column order
OUTPUT_COLUMNS = ("case_id", "deletion_status", "steps_json")


@dataclass
class OutputStep:
    """A single step in the deletion audit steps_json array.

    Must contain exactly the seven required fields.
    """
    step_no: int
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Return exactly the 7 keys required by the output contract."""
        return {
            "step_no": self.step_no,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OutputStep:
        """Construct an OutputStep from a dictionary with strict key validation."""
        missing = [k for k in REQUIRED_STEP_KEYS if k not in data]
        if missing:
            raise ValueError(f"Step missing required keys: {missing}")
        extra = [k for k in data if k not in REQUIRED_STEP_KEYS]
        if extra:
            raise ValueError(f"Step contains unexpected keys: {extra}")

        return cls(
            step_no=int(data["step_no"]),
            phase=str(data["phase"]),
            system_id=str(data["system_id"]),
            resource_id=str(data["resource_id"]),
            operation=str(data["operation"]),
            state=str(data["state"]),
            evidence_id=str(data["evidence_id"]),
        )

    def canonical_sort_key(self) -> Tuple[int, int, str, str]:
        """Compute the canonical tuple for sorting steps:

        (phase_rank, operation_rank, system_id, resource_id)
        """
        phase_rank = PHASE_ORDER.get(self.phase, 99)
        op_rank = OPERATION_ORDER.get(self.operation, 99)
        return (phase_rank, op_rank, self.system_id, self.resource_id)


@dataclass
class DeletionRequest:
    """An incoming deletion request from deletion_requests.csv or sample_deletion_requests.csv."""
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class UserIdentityRecord:
    """User profile record from user_identities.csv."""
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str
    normalized_email: str = ""
    normalized_phone: str = ""


@dataclass
class UserAuditRecord:
    """Audit log entry from user_audit_log.csv."""
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str
    normalized_old: str = ""
    normalized_new: str = ""


@dataclass
class SystemActivityRecord:
    """System activity entry from system_activity.csv."""
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class DeletionRuleRecord:
    """A policy rule from deletion_rules.csv."""
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass
class EvidenceManifestRecord:
    """Manifest record from evidence_files.csv."""
    file_id: str
    file_path: str
    origin: str
    captured_at: str


@dataclass
class EvidenceDocument:
    """Manifest record of an evidence file and its loaded payload."""
    file_id: str
    file_path: str
    origin: str
    captured_at: str
    file_type: str = ""
    content: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_size_bytes: int = 0
    content_hash: str = ""
    extraction_status: str = "SUCCESS"  # SUCCESS, PARTIAL, FAILED, UNSUPPORTED
    extraction_warning: Optional[str] = None
    claims_count: int = 0


@dataclass
class EvidenceClaim:
    """A claim or record observation extracted from an evidence file or activity log."""
    evidence_id: str
    source_type: str  # "file", "activity", "audit", "sql_snapshot"
    timestamp: str
    system_id: str
    resource_id: str
    observed_state: str
    details: str = ""
    identifiers_found: List[str] = field(default_factory=list)
    activity_id: Optional[str] = None
    audit_id: Optional[str] = None
    source_file: Optional[str] = None
    extraction_method: str = "structured"
    parsing_limitations: Optional[str] = None
    extracted_fields: Dict[str, Any] = field(default_factory=dict)
    raw_record: Optional[Any] = None
    data_category: Optional[str] = None


@dataclass
class SubjectIdentityGraph:
    """Identity resolution graph containing current and historical identifiers."""
    user_id: Optional[str] = None
    primary_email: str = ""
    emails: Set[str] = field(default_factory=set)
    names: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    email_hashes: Set[str] = field(default_factory=set)
    historical_changes: List[Dict[str, Any]] = field(default_factory=list)
    tombstone_ids: Set[str] = field(default_factory=set)
    match_classification: str = "exact"  # exact, historically_linked, stable_id_linked, ambiguous, unresolved
    resolution_paths: Dict[str, List[str]] = field(default_factory=dict)
    ambiguities: List[str] = field(default_factory=list)
    identity_edges: List[Dict[str, Any]] = field(default_factory=list)
    validity_intervals: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)

    def all_identifiers(self) -> Set[str]:
        """Return all known direct and hashed identifier strings."""
        identifiers = set()
        if self.user_id:
            identifiers.add(self.user_id)
        if self.primary_email:
            identifiers.add(self.primary_email.lower())
        for e in self.emails:
            identifiers.add(e.lower())
        for n in self.names:
            identifiers.add(n)
        for p in self.phones:
            identifiers.add(p)
        for h in self.email_hashes:
            identifiers.add(h)
        for t in self.tombstone_ids:
            identifiers.add(t)
        return identifiers


@dataclass
class CandidateFinding:
    """A residual data finding or recreation risk discovered before remediation planning."""
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str
    data_category: Optional[str] = None
    rule_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class RemediationAction:
    """A proposed remediation step to address a finding."""
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""
    depends_on: List[str] = field(default_factory=list)
    rule_id: Optional[str] = None


@dataclass
class VerificationCheck:
    """A verification check proving deletion state (historical or future)."""
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str
    rule_id: Optional[str] = None


@dataclass
class CaseResult:
    """The audited verdict and ordered steps for a single case."""
    case_id: str
    deletion_status: str
    steps: List[OutputStep] = field(default_factory=list)
    audit_at: Optional[str] = None
    notes: Optional[str] = None

    def sort_and_renumber_steps(self) -> None:
        """Sort steps canonically and renumber them consecutively from 1."""
        self.steps.sort(key=lambda s: s.canonical_sort_key())
        for idx, s in enumerate(self.steps, start=1):
            s.step_no = idx

    def to_csv_row(self) -> Tuple[str, str, str]:
        """Return the (case_id, deletion_status, steps_json) tuple for CSV emission."""
        if self.deletion_status == DeletionStatus.NOT_APPLICABLE.value:
            steps_data = []
        else:
            steps_data = [s.to_dict() for s in self.steps]
        return (
            self.case_id,
            self.deletion_status,
            json.dumps(steps_data, separators=(",", ":")),
        )


@dataclass
class RunMetrics:
    """Execution telemetry and token accounting for evaluation/usage_report.md."""
    total_cases: int = 0
    complete_cases: int = 0
    incomplete_cases: int = 0
    not_applicable_cases: int = 0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    model_name: Optional[str] = None
    per_model_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
