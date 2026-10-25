"""Ghost Data Still Exists - Evidence Ingestion Package & Phase 3 Handoff Interfaces."""

from pathlib import Path
from typing import List, Optional


from code.ingestion.csv_loader import (
    DatasetBundle,
    load_dataset,
    load_deletion_requests,
    load_deletion_rules,
    load_evidence_manifest_records,
    load_system_activity,
    load_user_audit_log,
    load_user_identities,
)
from code.ingestion.document_extractors import (
    ImageExtractor,
    JsonExtractor,
    LogExtractor,
    PdfExtractor,
    TextRecordExtractor,
)
from code.ingestion.evidence_store import EvidenceStore
from code.ingestion.manifest import (
    SUPPORTED_EXTENSIONS,
    compute_file_hash,
    load_manifest,
    resolve_evidence_path,
)
from code.ingestion.normalization import (
    compare_timestamps,
    format_iso_timestamp,
    is_at_or_before,
    normalize_email,
    normalize_email_hash,
    normalize_name,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
    parse_iso_timestamp,
)
from code.ingestion.sql_snapshots import SqlSnapshotExtractor
from code.schemas import (
    DeletionRequest,
    EvidenceClaim,
    EvidenceDocument,
    SubjectIdentityGraph,
)


def build_evidence_store(
    dataset_dir: str | Path,
    bundle: Optional[DatasetBundle] = None,
) -> EvidenceStore:
    """Build, populate, and index the EvidenceStore from the manifest and dataset records.

    Args:
        dataset_dir: Directory containing evidence_files.csv and evidence/ subdirectories.
        bundle: Optional pre-loaded DatasetBundle to also ingest system activity and audit logs.

    Returns:
        Fully indexed EvidenceStore instance.
    """
    base = Path(dataset_dir)
    store = EvidenceStore(dataset_dir=base)
    manifest_csv = base / "evidence_files.csv"
    if manifest_csv.exists():
        manifest_docs, _ = load_manifest(manifest_csv, base)
        store.ingest_evidence_documents(manifest_docs, base)

    if bundle is not None:
        if bundle.system_activities:
            store.ingest_system_activity(bundle.system_activities)
        if bundle.user_audit_logs:
            store.ingest_user_audit_logs(bundle.user_audit_logs)

    return store


def resolve_subject(
    request: DeletionRequest,
    bundle: DatasetBundle,
    evidence_store: Optional[EvidenceStore] = None,
) -> SubjectIdentityGraph:
    """Resolve a deletion request to a temporal SubjectIdentityGraph."""
    from code.identity.identity_resolver import IdentityResolver
    resolver = IdentityResolver(bundle, evidence_store)
    return resolver.resolve_subject(request)


def __getattr__(name: str):
    if name in ("IdentityResolver", "TemporalIdentityGraph", "IdentityNode", "IdentityEdge"):
        import code.identity.identity_graph as ig
        import code.identity.identity_resolver as ir
        mapping = {
            "IdentityResolver": ir.IdentityResolver,
            "TemporalIdentityGraph": ig.TemporalIdentityGraph,
            "IdentityNode": ig.IdentityNode,
            "IdentityEdge": ig.IdentityEdge,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def query_evidence(
    identity_graph: SubjectIdentityGraph,
    audit_at: str,
    evidence_store: EvidenceStore,
) -> List[EvidenceClaim]:
    """Retrieve temporally valid candidate evidence claims for a subject's identifiers at audit_at."""
    candidates = evidence_store.find_candidates_for_identifiers(identity_graph.all_identifiers())
    raw_claims = [c[0] for c in candidates]
    return evidence_store.filter_temporally(raw_claims, audit_at)


def get_temporally_valid_evidence(
    evidence_store: EvidenceStore,
    file_id: str,
    audit_at: str,
) -> Optional[EvidenceDocument]:
    """Retrieve an evidence file document only if it was captured at or before audit_at."""
    return evidence_store.get_temporally_valid_document(file_id, audit_at)


__all__ = [
    # Top-level Phase 3 handoff contract
    "build_evidence_store",
    "resolve_subject",
    "query_evidence",
    "get_temporally_valid_evidence",
    # Datasets and loaders
    "DatasetBundle",
    "load_dataset",
    "load_deletion_requests",
    "load_deletion_rules",
    "load_evidence_manifest_records",
    "load_system_activity",
    "load_user_audit_log",
    "load_user_identities",
    # Evidence storage and extraction
    "EvidenceStore",
    "LogExtractor",
    "TextRecordExtractor",
    "JsonExtractor",
    "PdfExtractor",
    "ImageExtractor",
    "SqlSnapshotExtractor",
    "resolve_evidence_path",
    "compute_file_hash",
    "load_manifest",
    "SUPPORTED_EXTENSIONS",
    # Identity
    "IdentityResolver",
    "TemporalIdentityGraph",
    "IdentityNode",
    "IdentityEdge",
    # Normalization
    "normalize_email",
    "normalize_phone",
    "normalize_name",
    "normalize_email_hash",
    "normalize_user_id",
    "normalize_resource_id",
    "parse_iso_timestamp",
    "format_iso_timestamp",
    "compare_timestamps",
    "is_at_or_before",
]
