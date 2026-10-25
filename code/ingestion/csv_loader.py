"""Ghost Data Still Exists - CSV Dataset Loaders & Fast Lookup Indexer.

Loads all participant-facing CSV files with strict schema validation,
Unicode and multiline support, timezone-aware timestamp parsing,
non-destructive normalization, and precomputed reverse indexes.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import (
    normalize_email,
    normalize_email_hash,
    normalize_name,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
    parse_iso_timestamp,
)
from code.schemas import (
    DeletionRequest,
    DeletionRuleRecord,
    EvidenceManifestRecord,
    SystemActivityRecord,
    UserAuditRecord,
    UserIdentityRecord,
)

logger = logging.getLogger(__name__)


@dataclass
class DatasetBundle:
    """In-memory typed container for all global dataset records and lookup indexes."""
    deletion_requests: Dict[str, DeletionRequest] = field(default_factory=dict)
    user_identities: Dict[str, UserIdentityRecord] = field(default_factory=dict)
    user_audit_logs: List[UserAuditRecord] = field(default_factory=list)
    system_activities: List[SystemActivityRecord] = field(default_factory=list)
    deletion_rules: Dict[str, DeletionRuleRecord] = field(default_factory=dict)
    evidence_manifest: Dict[str, EvidenceManifestRecord] = field(default_factory=dict)

    # Fast reverse lookup indexes
    identities_by_email: Dict[str, List[UserIdentityRecord]] = field(default_factory=dict)
    identities_by_phone: Dict[str, List[UserIdentityRecord]] = field(default_factory=dict)
    identities_by_email_hash: Dict[str, List[UserIdentityRecord]] = field(default_factory=dict)
    identities_by_name: Dict[str, List[UserIdentityRecord]] = field(default_factory=dict)

    audit_by_user_id: Dict[str, List[UserAuditRecord]] = field(default_factory=dict)
    audit_by_old_value: Dict[str, List[UserAuditRecord]] = field(default_factory=dict)
    audit_by_new_value: Dict[str, List[UserAuditRecord]] = field(default_factory=dict)

    activity_by_resource_id: Dict[str, List[SystemActivityRecord]] = field(default_factory=dict)
    activity_by_identifier: Dict[str, List[SystemActivityRecord]] = field(default_factory=dict)

    # Ingestion audit telemetry
    record_counts: Dict[str, int] = field(default_factory=dict)
    malformed_rows: List[Dict[str, Any]] = field(default_factory=list)
    missing_fields: List[Dict[str, Any]] = field(default_factory=list)


def load_deletion_requests(file_path: str | Path) -> Tuple[Dict[str, DeletionRequest], List[Dict[str, Any]]]:
    """Load deletion_requests.csv or sample_deletion_requests.csv."""
    path = Path(file_path)
    requests: Dict[str, DeletionRequest] = {}
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"Deletion requests file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            case_id = (row.get("case_id") or "").strip()
            email = (row.get("email") or "").strip()
            req_at = (row.get("requested_at") or "").strip()
            rep_status = (row.get("reported_status") or "").strip()
            rep_at = (row.get("reported_at") or "").strip()
            audit_at = (row.get("audit_at") or "").strip()
            req_text = row.get("request_text") or ""

            if not case_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty case_id"})
                continue

            # Validate ISO timestamp formats
            if audit_at and parse_iso_timestamp(audit_at) is None:
                errors.append({"file": path.name, "row": row_idx, "case_id": case_id, "error": f"Malformed audit_at: {audit_at}"})

            req = DeletionRequest(
                case_id=case_id,
                email=email,
                requested_at=req_at,
                reported_status=rep_status,
                reported_at=rep_at,
                audit_at=audit_at,
                request_text=req_text,
            )
            requests[case_id] = req

    return requests, errors


def load_user_identities(file_path: str | Path) -> Tuple[Dict[str, UserIdentityRecord], List[Dict[str, Any]]]:
    """Load user_identities.csv with non-destructive normalization."""
    path = Path(file_path)
    identities: Dict[str, UserIdentityRecord] = {}
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"User identities file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"user_id", "name", "email", "phone", "email_hash", "updated_at"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            user_id = normalize_user_id(row.get("user_id"))
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip()
            phone = (row.get("phone") or "").strip()
            email_hash = (row.get("email_hash") or "").strip()
            updated_at = (row.get("updated_at") or "").strip()

            if not user_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty user_id"})
                continue

            norm_email = normalize_email(email)
            norm_phone, _ = normalize_phone(phone)
            norm_hash = normalize_email_hash(email_hash)

            rec = UserIdentityRecord(
                user_id=user_id,
                name=name,
                email=email,
                phone=phone,
                email_hash=norm_hash,
                updated_at=updated_at,
                normalized_email=norm_email,
                normalized_phone=norm_phone,
            )
            identities[user_id] = rec

    return identities, errors


def load_user_audit_log(file_path: str | Path) -> Tuple[List[UserAuditRecord], List[Dict[str, Any]]]:
    """Load user_audit_log.csv tracking historical identity modifications."""
    path = Path(file_path)
    records: List[UserAuditRecord] = []
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"User audit log file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            audit_id = (row.get("audit_id") or "").strip()
            user_id = normalize_user_id(row.get("user_id"))
            event_time = (row.get("event_time") or "").strip()
            field_name = (row.get("field_name") or "").strip().lower()
            old_val = (row.get("old_value") or "").strip()
            new_val = (row.get("new_value") or "").strip()

            if not audit_id or not user_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty audit_id or user_id"})
                continue

            norm_old = old_val
            norm_new = new_val
            if field_name == "email":
                norm_old = normalize_email(old_val)
                norm_new = normalize_email(new_val)
            elif field_name == "phone":
                norm_old, _ = normalize_phone(old_val)
                norm_new, _ = normalize_phone(new_val)
            elif field_name == "email_hash":
                norm_old = normalize_email_hash(old_val)
                norm_new = normalize_email_hash(new_val)

            rec = UserAuditRecord(
                audit_id=audit_id,
                user_id=user_id,
                event_time=event_time,
                field_name=field_name,
                old_value=old_val,
                new_value=new_val,
                normalized_old=norm_old,
                normalized_new=norm_new,
            )
            records.append(rec)

    return records, errors


def load_system_activity(file_path: str | Path) -> Tuple[List[SystemActivityRecord], List[Dict[str, Any]]]:
    """Load system_activity.csv recording global asynchronous operations."""
    path = Path(file_path)
    records: List[SystemActivityRecord] = []
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"System activity file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            activity_id = (row.get("activity_id") or "").strip()
            event_time = (row.get("event_time") or "").strip()
            event_type = (row.get("event_type") or "").strip()
            source_system = (row.get("source_system") or "").strip()
            target_system = (row.get("target_system") or "").strip()
            resource_id = normalize_resource_id(row.get("resource_id"))
            reported_result = (row.get("reported_result") or "").strip()
            details = row.get("details") or ""

            if not activity_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty activity_id"})
                continue

            rec = SystemActivityRecord(
                activity_id=activity_id,
                event_time=event_time,
                event_type=event_type,
                source_system=source_system,
                target_system=target_system,
                resource_id=resource_id,
                reported_result=reported_result,
                details=details,
            )
            records.append(rec)

    return records, errors


def load_deletion_rules(file_path: str | Path) -> Tuple[Dict[str, DeletionRuleRecord], List[Dict[str, Any]]]:
    """Load deletion_rules.csv specifying required actions per data category."""
    path = Path(file_path)
    rules: Dict[str, DeletionRuleRecord] = {}
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"Deletion rules file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"rule_id", "data_category", "condition", "required_action"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            rule_id = (row.get("rule_id") or "").strip()
            data_category = (row.get("data_category") or "").strip()
            condition = (row.get("condition") or "").strip()
            required_action = (row.get("required_action") or "").strip()

            if not rule_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty rule_id"})
                continue

            rec = DeletionRuleRecord(
                rule_id=rule_id,
                data_category=data_category,
                condition=condition,
                required_action=required_action,
            )
            rules[rule_id] = rec

    return rules, errors


def load_evidence_manifest_records(file_path: str | Path) -> Tuple[Dict[str, EvidenceManifestRecord], List[Dict[str, Any]]]:
    """Load evidence_files.csv manifest rows."""
    path = Path(file_path)
    manifest: Dict[str, EvidenceManifestRecord] = {}
    errors: List[Dict[str, Any]] = []

    if not path.exists():
        raise FileNotFoundError(f"Evidence files manifest not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        expected = {"file_id", "file_path", "origin", "captured_at"}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise ValueError(f"{path.name} missing required columns: {expected - actual}")

        for row_idx, row in enumerate(reader, start=2):
            file_id = (row.get("file_id") or "").strip()
            rel_path = (row.get("file_path") or "").strip()
            origin = (row.get("origin") or "").strip()
            captured_at = (row.get("captured_at") or "").strip()

            if not file_id:
                errors.append({"file": path.name, "row": row_idx, "error": "Empty file_id"})
                continue

            rec = EvidenceManifestRecord(
                file_id=file_id,
                file_path=rel_path,
                origin=origin,
                captured_at=captured_at,
            )
            manifest[file_id] = rec

    return manifest, errors


def load_dataset(dataset_dir: str | Path, requests_file: str = "deletion_requests.csv") -> DatasetBundle:
    """Unified entry point to load and index the complete participant dataset.

    Args:
        dataset_dir: Base directory containing CSV files and evidence/
        requests_file: Filename for deletion requests (default: deletion_requests.csv)

    Returns:
        DatasetBundle with typed records and multi-index lookups.
    """
    base = Path(dataset_dir)
    bundle = DatasetBundle()

    # 1. Deletion Requests
    req_path = base / requests_file
    if req_path.exists():
        bundle.deletion_requests, errs = load_deletion_requests(req_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["deletion_requests"] = len(bundle.deletion_requests)

    # 2. User Identities
    ident_path = base / "user_identities.csv"
    if ident_path.exists():
        bundle.user_identities, errs = load_user_identities(ident_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["user_identities"] = len(bundle.user_identities)

        # Index identities
        for uid, ident in bundle.user_identities.items():
            if ident.normalized_email:
                bundle.identities_by_email.setdefault(ident.normalized_email, []).append(ident)
            if ident.normalized_phone:
                bundle.identities_by_phone.setdefault(ident.normalized_phone, []).append(ident)
            if ident.email_hash:
                bundle.identities_by_email_hash.setdefault(ident.email_hash, []).append(ident)
            if ident.name:
                bundle.identities_by_name.setdefault(normalize_name(ident.name), []).append(ident)

    # 3. User Audit Log
    audit_path = base / "user_audit_log.csv"
    if audit_path.exists():
        bundle.user_audit_logs, errs = load_user_audit_log(audit_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["user_audit_log"] = len(bundle.user_audit_logs)

        # Index audit logs
        for log in bundle.user_audit_logs:
            bundle.audit_by_user_id.setdefault(log.user_id, []).append(log)
            if log.normalized_old:
                bundle.audit_by_old_value.setdefault(log.normalized_old, []).append(log)
            if log.normalized_new:
                bundle.audit_by_new_value.setdefault(log.normalized_new, []).append(log)

    # 4. System Activity
    act_path = base / "system_activity.csv"
    if act_path.exists():
        bundle.system_activities, errs = load_system_activity(act_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["system_activity"] = len(bundle.system_activities)

        # Index system activity
        for act in bundle.system_activities:
            if act.resource_id:
                bundle.activity_by_resource_id.setdefault(act.resource_id, []).append(act)

            # Discover identifier references in details
            for token in act.details.split():
                clean_token = token.strip(".,;:()[]'\"")
                if "@" in clean_token:
                    bundle.activity_by_identifier.setdefault(normalize_email(clean_token), []).append(act)
                elif clean_token.startswith("user_"):
                    bundle.activity_by_identifier.setdefault(normalize_user_id(clean_token), []).append(act)

    # 5. Deletion Rules
    rules_path = base / "deletion_rules.csv"
    if rules_path.exists():
        bundle.deletion_rules, errs = load_deletion_rules(rules_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["deletion_rules"] = len(bundle.deletion_rules)

    # 6. Evidence Manifest
    ev_path = base / "evidence_files.csv"
    if ev_path.exists():
        bundle.evidence_manifest, errs = load_evidence_manifest_records(ev_path)
        bundle.malformed_rows.extend(errs)
        bundle.record_counts["evidence_files"] = len(bundle.evidence_manifest)

    return bundle
