"""Ghost Data Still Exists - Deterministic Event Normalization Layer.

Normalizes heterogeneous operational streams (system activity, evidence claims,
user audit logs) into a uniform NormalizedEvent representation with strict
temporal semantics, provenance tracking, and separation between claimed vs
established outcomes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from code.ingestion.normalization import (
    compare_timestamps,
    is_at_or_before,
    normalize_email,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
    parse_iso_timestamp,
)
from code.reconstruction.models import NormalizedEvent
from code.schemas import EvidenceClaim, EvidenceDocument, SystemActivityRecord, UserAuditRecord

logger = logging.getLogger(__name__)


class EventNormalizer:
    """Normalizes claims, activity records, and audit events into standardized NormalizedEvent instances."""

    @staticmethod
    def normalize_activity(
        activity: SystemActivityRecord,
        audit_at: Optional[str] = None,
    ) -> NormalizedEvent:
        """Convert a SystemActivityRecord into a NormalizedEvent."""
        norm_res = normalize_resource_id(activity.resource_id)
        norm_ts = activity.event_time.strip()

        # Extract subject identifiers from details
        subject_ids: Set[str] = set()
        for token in activity.details.split():
            clean = token.strip(".,;:()[]'\"")
            if "@" in clean:
                subject_ids.add(normalize_email(clean))
            elif clean.startswith("user_"):
                subject_ids.add(normalize_user_id(clean))

        # Check temporal eligibility
        is_eligible = True
        warnings: List[str] = []
        if norm_ts and audit_at:
            if not is_at_or_before(norm_ts, audit_at):
                is_eligible = False

        if parse_iso_timestamp(norm_ts) is None:
            warnings.append(f"Malformed activity event_time: {norm_ts}")

        rep_out = activity.reported_result.upper() if activity.reported_result else "UNKNOWN"
        if rep_out not in ("SUCCESS", "FAILED", "PARTIAL"):
            rep_out = "UNKNOWN"

        system_id = activity.source_system.strip()
        # If the source system is an automated agent/worker, target_system is where the resource resides
        if system_id in ("deletion_worker", "privacy_intake", "data_catalog") and activity.target_system:
            system_id = activity.target_system.strip()

        return NormalizedEvent(
            event_id=f"act:{activity.activity_id}",
            event_type=activity.event_type.strip(),
            system_id=system_id,
            resource_id=norm_res,
            timestamp=norm_ts,
            subject_ids=sorted(list(subject_ids)),
            capture_timestamp=None,
            reported_outcome=rep_out,
            evidence_id=activity.activity_id,
            source_type="activity",
            source_origin="system_activity_stream",
            attributes={
                "target_system": activity.target_system.strip(),
                "details": activity.details,
            },
            is_temporally_eligible=is_eligible,
            warnings=warnings,
        )

    @staticmethod
    def normalize_claim(
        claim: EvidenceClaim,
        evidence_doc: Optional[EvidenceDocument] = None,
        audit_at: Optional[str] = None,
    ) -> NormalizedEvent:
        """Convert an EvidenceClaim from an evidence file into a NormalizedEvent."""
        norm_res = normalize_resource_id(claim.resource_id)
        norm_ts = claim.timestamp.strip() if claim.timestamp else ""
        capture_ts = evidence_doc.captured_at.strip() if evidence_doc and evidence_doc.captured_at else None

        is_eligible = True
        warnings: List[str] = []

        if audit_at:
            # Source file capture time must not exceed audit_at
            if capture_ts and not is_at_or_before(capture_ts, audit_at):
                is_eligible = False
            # Event timestamp must not exceed audit_at
            if norm_ts and not is_at_or_before(norm_ts, audit_at):
                is_eligible = False

        if not norm_ts:
            norm_ts = capture_ts or ""
            warnings.append("Missing event timestamp; defaulted to source capture timestamp")

        # Map observed_state to operational event type
        event_type = "OBSERVE_RECORD"
        if claim.source_type == "sql_snapshot":
            event_type = "SNAPSHOT_OBSERVATION"
        elif claim.extracted_fields.get("event"):
            ev = str(claim.extracted_fields["event"]).upper()
            if "DELETE" in ev or "PURGE" in ev:
                event_type = "DELETE_RECORD"
            elif "ANONYMIZE" in ev:
                event_type = "ANONYMIZE_RECORD"
            elif "REDACT" in ev:
                event_type = "REDACT_RECORD"
            elif "SYNC" in ev:
                event_type = "SYNC_RECORD"
            elif "RESTORE" in ev:
                event_type = "RESTORE_BACKUP"
            elif "BLOCK" in ev:
                event_type = "BLOCK_RESTORE"
            elif "DISABLE" in ev:
                event_type = "DISABLE_DATA_FLOW"

        reported_outcome = "SUCCESS"
        if claim.observed_state == "ABSENT":
            reported_outcome = "SUCCESS"
        elif claim.observed_state == "BLOCKED":
            reported_outcome = "SUCCESS"
        elif claim.observed_state == "DISABLED":
            reported_outcome = "SUCCESS"

        origin = evidence_doc.origin if evidence_doc else claim.system_id

        return NormalizedEvent(
            event_id=f"claim:{claim.evidence_id}:{norm_res or 'claim'}_{id(claim)}",
            event_type=event_type,
            system_id=claim.system_id,
            resource_id=norm_res,
            timestamp=norm_ts,
            subject_ids=sorted(list(set(claim.identifiers_found))),
            capture_timestamp=capture_ts,
            reported_outcome=reported_outcome,
            evidence_id=claim.evidence_id,
            source_type=claim.source_type,
            source_origin=origin,
            attributes={
                "observed_state": claim.observed_state,
                "details": claim.details,
                "extracted_fields": claim.extracted_fields,
                "data_category": claim.data_category,
            },
            is_temporally_eligible=is_eligible,
            warnings=warnings,
        )

    @staticmethod
    def normalize_audit_record(
        audit: UserAuditRecord,
        audit_at: Optional[str] = None,
    ) -> NormalizedEvent:
        """Convert a UserAuditRecord into a NormalizedEvent."""
        norm_ts = audit.event_time.strip()
        is_eligible = True
        warnings: List[str] = []

        if norm_ts and audit_at:
            if not is_at_or_before(norm_ts, audit_at):
                is_eligible = False

        subject_ids = [audit.user_id]
        if audit.normalized_old:
            subject_ids.append(audit.normalized_old)
        if audit.normalized_new:
            subject_ids.append(audit.normalized_new)

        return NormalizedEvent(
            event_id=f"audit:{audit.audit_id}",
            event_type="UPDATE_IDENTITY",
            system_id="identity_service",
            resource_id=audit.user_id,
            timestamp=norm_ts,
            subject_ids=sorted(list(set(subject_ids))),
            capture_timestamp=None,
            reported_outcome="SUCCESS",
            evidence_id=audit.audit_id,
            source_type="audit",
            source_origin="user_audit_log",
            attributes={
                "field_name": audit.field_name,
                "old_value": audit.old_value,
                "new_value": audit.new_value,
            },
            is_temporally_eligible=is_eligible,
            warnings=warnings,
        )
