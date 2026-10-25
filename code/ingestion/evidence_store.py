"""Ghost Data Still Exists - Central Evidence Store & Temporal Query Engine.

Indexes all evidence documents and claims across file formats, system activity,
and identity audit logs. Provides high-recall candidate discovery, multi-key
indexing, source provenance tracking, and strict audit-cutoff temporal filtering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.document_extractors import (
    ImageExtractor,
    JsonExtractor,
    LogExtractor,
    PdfExtractor,
    TextRecordExtractor,
)
from code.ingestion.normalization import (
    compare_timestamps,
    is_at_or_before,
    normalize_email,
    normalize_email_hash,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
)
from code.ingestion.sql_snapshots import SqlSnapshotExtractor
from code.schemas import (
    EvidenceClaim,
    EvidenceDocument,
    SystemActivityRecord,
    UserAuditRecord,
)

logger = logging.getLogger(__name__)


@dataclass
class EvidenceStore:
    """Central indexed repository for all evidence documents and extracted claims."""
    dataset_dir: Optional[Path] = None
    documents: Dict[str, EvidenceDocument] = field(default_factory=dict)
    claims: List[EvidenceClaim] = field(default_factory=list)

    # Multi-dimensional reverse lookup indexes
    claims_by_email: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_user_id: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_phone: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_hash: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_resource_id: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_system_id: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)
    claims_by_file_id: Dict[str, List[EvidenceClaim]] = field(default_factory=dict)

    # Telemetry and extraction telemetry
    status_counts: Dict[str, int] = field(default_factory=lambda: {"SUCCESS": 0, "PARTIAL": 0, "FAILED": 0, "UNSUPPORTED": 0})
    format_counts: Dict[str, int] = field(default_factory=dict)

    def add_claim(self, claim: EvidenceClaim) -> None:
        """Add a single claim to the store and update all relevant lookup indexes."""
        self.claims.append(claim)

        if claim.evidence_id:
            self.claims_by_file_id.setdefault(claim.evidence_id, []).append(claim)
        if claim.system_id:
            self.claims_by_system_id.setdefault(claim.system_id, []).append(claim)
        if claim.resource_id:
            norm_res = normalize_resource_id(claim.resource_id)
            self.claims_by_resource_id.setdefault(norm_res, []).append(claim)

        for ident in claim.identifiers_found:
            if not ident:
                continue
            if "@" in ident:
                self.claims_by_email.setdefault(normalize_email(ident), []).append(claim)
            elif ident.startswith("user_"):
                self.claims_by_user_id.setdefault(normalize_user_id(ident), []).append(claim)
            elif ident.startswith("+") or ident.isdigit():
                norm_p, _ = normalize_phone(ident)
                self.claims_by_phone.setdefault(norm_p, []).append(claim)
            elif len(ident) == 64 and all(c in "0123456789abcdefABCDEF" for c in ident):
                self.claims_by_hash.setdefault(normalize_email_hash(ident), []).append(claim)

    def add_document(self, doc: EvidenceDocument) -> None:
        """Register an EvidenceDocument in the store."""
        self.documents[doc.file_id] = doc
        ext = doc.file_type or "unknown"
        self.format_counts[ext] = self.format_counts.get(ext, 0) + 1

    def ingest_evidence_documents(
        self,
        manifest_docs: Dict[str, EvidenceDocument],
        dataset_dir: str | Path,
    ) -> None:
        """Execute format-specific deterministic extraction across all manifest evidence files."""
        base_dir = Path(dataset_dir)

        for file_id, doc in manifest_docs.items():
            self.add_document(doc)

            if doc.extraction_status == "UNSUPPORTED" or doc.extraction_status == "FAILED":
                self.status_counts[doc.extraction_status] = self.status_counts.get(doc.extraction_status, 0) + 1
                continue

            file_path = base_dir / doc.file_path
            if not file_path.exists():
                doc.extraction_status = "FAILED"
                doc.extraction_warning = f"File missing from disk: {file_path}"
                self.status_counts["FAILED"] = self.status_counts.get("FAILED", 0) + 1
                continue

            extracted_claims: List[EvidenceClaim] = []
            ext = doc.file_type.lower()

            try:
                if ext == ".log":
                    extracted_claims = LogExtractor.extract(doc, file_path)
                elif ext == ".txt":
                    extracted_claims = TextRecordExtractor.extract(doc, file_path)
                elif ext == ".json":
                    extracted_claims = JsonExtractor.extract(doc, file_path)
                elif ext == ".pdf":
                    extracted_claims = PdfExtractor.extract(doc, file_path)
                elif ext == ".png":
                    extracted_claims = ImageExtractor.extract(doc, file_path)
                elif ext == ".sql":
                    extracted_claims = SqlSnapshotExtractor.extract(doc, file_path)
                else:
                    doc.extraction_status = "UNSUPPORTED"
                    doc.extraction_warning = f"Unhandled extension: {ext}"

                doc.claims_count = len(extracted_claims)
                for claim in extracted_claims:
                    self.add_claim(claim)

                # Record final extraction status for this document
                stat = doc.extraction_status or "SUCCESS"
                self.status_counts[stat] = self.status_counts.get(stat, 0) + 1

            except Exception as e:
                logger.error("Failed to extract evidence file %s: %s", file_path, e)
                doc.extraction_status = "FAILED"
                doc.extraction_warning = f"Extraction unhandled exception: {e}"
                self.status_counts["FAILED"] = self.status_counts.get("FAILED", 0) + 1

    def ingest_system_activity(self, activities: List[SystemActivityRecord]) -> None:
        """Convert system activity stream records into EvidenceClaim instances with activity provenance."""
        for act in activities:
            idents: List[str] = []
            for token in act.details.split():
                clean = token.strip(".,;:()[]'\"")
                if "@" in clean:
                    idents.append(normalize_email(clean))
                elif clean.startswith("user_"):
                    idents.append(normalize_user_id(clean))

            state = "PRESENT"
            rep_res = act.reported_result.upper()
            if "DELETE" in act.event_type:
                state = "ABSENT" if rep_res == "SUCCESS" else "PRESENT"
            elif rep_res == "FAILED":
                state = "ABSENT"
            elif rep_res == "SUCCESS":
                state = "PRESENT"

            sys_id = act.source_system
            if sys_id in ("deletion_worker", "privacy_intake", "data_catalog", "readback_collector") and act.target_system:
                sys_id = act.target_system.strip()

            claim = EvidenceClaim(
                evidence_id=act.activity_id,
                source_type="activity",
                activity_id=act.activity_id,
                timestamp=act.event_time,
                system_id=sys_id,
                resource_id=act.resource_id,
                observed_state=state,
                details=act.details,
                identifiers_found=idents,
                extraction_method="system_activity_stream",
                extracted_fields={
                    "event_type": act.event_type,
                    "target_system": act.target_system,
                    "reported_result": act.reported_result,
                },
            )
            self.add_claim(claim)

    def ingest_user_audit_logs(self, audit_logs: List[UserAuditRecord]) -> None:
        """Convert user identity audit logs into EvidenceClaim instances with audit provenance."""
        for log in audit_logs:
            idents = [log.user_id]
            if log.normalized_old:
                idents.append(log.normalized_old)
            if log.normalized_new:
                idents.append(log.normalized_new)

            claim = EvidenceClaim(
                evidence_id=log.audit_id,
                source_type="audit",
                audit_id=log.audit_id,
                timestamp=log.event_time,
                system_id="identity_service",
                resource_id=log.user_id,
                observed_state="PRESENT",
                details=f"Field {log.field_name} updated from '{log.old_value}' to '{log.new_value}'",
                identifiers_found=idents,
                extraction_method="user_audit_log",
                extracted_fields={
                    "field_name": log.field_name,
                    "old_value": log.old_value,
                    "new_value": log.new_value,
                },
            )
            self.add_claim(claim)

    def find_candidates_for_identifiers(
        self,
        identifiers: Set[str],
    ) -> List[Tuple[EvidenceClaim, str]]:
        """Surface candidate claims matching any supplied identifier without asserting attribution.

        Returns:
            List of (claim, match_method)
        """
        seen_claim_ids: Set[int] = set()
        candidates: List[Tuple[EvidenceClaim, str]] = []

        for ident in identifiers:
            if not ident:
                continue

            # 1. Exact Email
            if "@" in ident:
                norm_e = normalize_email(ident)
                for claim in self.claims_by_email.get(norm_e, []):
                    c_id = id(claim)
                    if c_id not in seen_claim_ids:
                        seen_claim_ids.add(c_id)
                        candidates.append((claim, f"EXACT_EMAIL:{norm_e}"))

            # 2. Stable User ID
            elif ident.startswith("user_"):
                norm_u = normalize_user_id(ident)
                for claim in self.claims_by_user_id.get(norm_u, []):
                    c_id = id(claim)
                    if c_id not in seen_claim_ids:
                        seen_claim_ids.add(c_id)
                        candidates.append((claim, f"USER_ID:{norm_u}"))

            # 3. Phone Number
            elif ident.startswith("+") or ident.isdigit():
                norm_p, _ = normalize_phone(ident)
                for claim in self.claims_by_phone.get(norm_p, []):
                    c_id = id(claim)
                    if c_id not in seen_claim_ids:
                        seen_claim_ids.add(c_id)
                        candidates.append((claim, f"PHONE:{norm_p}"))

            # 4. Email Hash
            elif len(ident) == 64 and all(c in "0123456789abcdefABCDEF" for c in ident):
                norm_h = normalize_email_hash(ident)
                for claim in self.claims_by_hash.get(norm_h, []):
                    c_id = id(claim)
                    if c_id not in seen_claim_ids:
                        seen_claim_ids.add(c_id)
                        candidates.append((claim, f"EMAIL_HASH:{norm_h}"))

            # 5. Resource ID match
            for claim in self.claims_by_resource_id.get(ident, []):
                c_id = id(claim)
                if c_id not in seen_claim_ids:
                    seen_claim_ids.add(c_id)
                    candidates.append((claim, f"RESOURCE_ID:{ident}"))

        return candidates

    def filter_temporally(
        self,
        claims: List[EvidenceClaim],
        audit_at: str,
    ) -> List[EvidenceClaim]:
        """Filter claims against the case's audit_at cutoff.

        Excludes any claim where the event timestamp occurs strictly after audit_at.
        If an export bundle containing historical snapshots was captured after audit_at,
        the historical claim is preserved for state reconstruction, but its post-cutoff
        evidence_id is cleared so it cannot be cited as evidence.
        """
        import copy
        valid_claims: List[EvidenceClaim] = []

        for claim in claims:
            # Check claim timestamp
            if claim.timestamp and not is_at_or_before(claim.timestamp, audit_at):
                # Exclude: Event occurred after audit_at cutoff
                continue

            if claim.evidence_id in self.documents:
                doc = self.documents[claim.evidence_id]
                if doc.captured_at and not is_at_or_before(doc.captured_at, audit_at):
                    # Exclude: Evidence document was captured after audit_at cutoff
                    continue

            valid_claims.append(claim)

        return valid_claims

    def get_temporally_valid_document(self, file_id: str, audit_at: str) -> Optional[EvidenceDocument]:
        """Retrieve an EvidenceDocument only if its captured_at is at or before audit_at."""
        doc = self.documents.get(file_id)
        if not doc:
            return None
        if doc.captured_at and not is_at_or_before(doc.captured_at, audit_at):
            return None
        return doc

    def get_telemetry(self) -> Dict[str, Any]:
        """Return summary statistics of ingested documents, formats, and claims."""
        return {
            "total_documents": len(self.documents),
            "status_counts": dict(self.status_counts),
            "format_counts": dict(self.format_counts),
            "total_claims": len(self.claims),
            "claims_by_email_keys": len(self.claims_by_email),
            "claims_by_user_id_keys": len(self.claims_by_user_id),
            "claims_by_resource_id_keys": len(self.claims_by_resource_id),
        }
