"""Ghost Data Still Exists - Temporal Identity Resolver.

Resolves incoming deletion requests to stable subject identity graphs by
integrating current profile snapshots, historical audit log changes, and
temporal validity intervals. Handles historical emails, tombstone representations,
and conservatively reports ambiguities without unsound transitive merging.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Dict, List, Optional, Set

from code.identity.identity_graph import TemporalIdentityGraph
from code.ingestion.csv_loader import DatasetBundle
from code.ingestion.evidence_store import EvidenceStore
from code.ingestion.normalization import (
    normalize_email,
    normalize_email_hash,
    normalize_phone,
    normalize_user_id,
)
from code.schemas import DeletionRequest, SubjectIdentityGraph

logger = logging.getLogger(__name__)


class IdentityResolver:
    """Resolves deletion requests to evidence-supported subject identity graphs."""

    def __init__(
        self,
        dataset_bundle: DatasetBundle,
        evidence_store: Optional[EvidenceStore] = None,
    ) -> None:
        self.bundle = dataset_bundle
        self.evidence_store = evidence_store

    def resolve_subject(self, request: DeletionRequest) -> SubjectIdentityGraph:
        """Resolve a DeletionRequest into a SubjectIdentityGraph.

        Follows conservative resolution:
        1. Check exact match in current snapshot (user_identities.csv).
        2. Check historical identity updates in user_audit_log.csv.
        3. Check stable internal user ID match.
        4. Guard against conflicting identities: never merge conflicting users.
        5. Build temporal intervals for each identifier.
        """
        raw_email = request.email.strip()
        norm_email = normalize_email(raw_email)
        graph = TemporalIdentityGraph(primary_email=norm_email)

        # Step 1: Check current snapshot
        current_matches = self.bundle.identities_by_email.get(norm_email, [])

        if current_matches:
            user_ids = {m.user_id for m in current_matches}
            if len(user_ids) > 1:
                # Ambiguity: Multiple users in snapshot share this email
                graph.match_classification = "AMBIGUOUS"
                graph.ambiguities.append(
                    f"Conflicting identities: email '{norm_email}' matches multiple current users: {sorted(list(user_ids))}"
                )
                graph.add_node(norm_email, kind="PRIMARY_EMAIL")
                return graph.to_schema_graph()

            resolved_user_id = current_matches[0].user_id
            graph.match_classification = "EXACT_CURRENT"
            graph.resolution_paths[norm_email] = [
                norm_email,
                f"user_identities:{resolved_user_id}",
                resolved_user_id,
            ]
            self._populate_subject_graph(graph, resolved_user_id, norm_email)
            return graph.to_schema_graph()

        # Step 2: Check historical audit logs
        historical_matches = self.bundle.audit_by_old_value.get(norm_email, [])
        email_audit_matches = [m for m in historical_matches if m.field_name == "email"]

        if email_audit_matches:
            user_ids = {m.user_id for m in email_audit_matches}
            if len(user_ids) > 1:
                # Ambiguity: Historical email associated with multiple users across time
                graph.match_classification = "AMBIGUOUS"
                graph.ambiguities.append(
                    f"Conflicting historical audit: email '{norm_email}' is linked to multiple users: {sorted(list(user_ids))}"
                )
                graph.add_node(norm_email, kind="HISTORICAL_EMAIL")
                return graph.to_schema_graph()

            resolved_user_id = email_audit_matches[0].user_id
            graph.match_classification = "HISTORICALLY_LINKED"
            graph.resolution_paths[norm_email] = [
                norm_email,
                f"audit_id:{email_audit_matches[0].audit_id}",
                resolved_user_id,
            ]
            self._populate_subject_graph(graph, resolved_user_id, norm_email)
            return graph.to_schema_graph()

        # Step 3: Check if request identifier is directly a user_id
        if raw_email in self.bundle.user_identities or norm_email in self.bundle.user_identities:
            resolved_user_id = raw_email if raw_email in self.bundle.user_identities else norm_email
            graph.match_classification = "STABLE_ID_LINKED"
            graph.resolution_paths[raw_email] = [raw_email, resolved_user_id]
            self._populate_subject_graph(graph, resolved_user_id, "")
            return graph.to_schema_graph()

        # Step 4: Unresolved request email
        graph.match_classification = "UNRESOLVED"
        graph.add_node(norm_email, kind="PRIMARY_EMAIL")
        return graph.to_schema_graph()

    def _populate_subject_graph(
        self,
        graph: TemporalIdentityGraph,
        user_id: str,
        request_email: str,
    ) -> None:
        """Populate current and historical attributes, validity intervals, and tombstone for user_id."""
        graph.user_id = user_id
        graph.add_node(user_id, kind="USER_ID")

        # Tombstone reference (rule_15: preserve user_id, redact direct identifiers)
        graph.add_node(user_id, kind="TOMBSTONE_ID")
        graph.add_edge(
            source=user_id,
            target=user_id,
            relationship="TOMBSTONE_REDACTION",
            source_reference=f"rule_15:user_id:{user_id}",
            notes="Account tombstone preserving internal user reference with direct identifiers redacted",
        )

        # Current profile snapshot
        profile = self.bundle.user_identities.get(user_id)
        if profile:
            if profile.normalized_email:
                graph.primary_email = profile.normalized_email
                graph.add_node(profile.normalized_email, kind="PRIMARY_EMAIL", valid_from=profile.updated_at)
                graph.add_edge(
                    source=user_id,
                    target=profile.normalized_email,
                    relationship="SNAPSHOT_CURRENT",
                    source_reference=f"user_identities:{user_id}",
                    timestamp=profile.updated_at,
                )
                calc_hash = hashlib.sha256(profile.normalized_email.encode("utf-8")).hexdigest()
                graph.add_node(calc_hash, kind="EMAIL_HASH", valid_from=profile.updated_at)
                graph.add_edge(
                    source=user_id,
                    target=calc_hash,
                    relationship="SNAPSHOT_CURRENT",
                    source_reference=f"user_identities:{user_id}",
                    timestamp=profile.updated_at,
                )

            if profile.name:
                graph.add_node(profile.name, kind="NAME")
                graph.add_edge(
                    source=user_id,
                    target=profile.name,
                    relationship="SNAPSHOT_CURRENT",
                    source_reference=f"user_identities:{user_id}",
                    timestamp=profile.updated_at,
                )

            if profile.normalized_phone:
                graph.add_node(profile.normalized_phone, kind="PHONE", valid_from=profile.updated_at)
                graph.add_edge(
                    source=user_id,
                    target=profile.normalized_phone,
                    relationship="SNAPSHOT_CURRENT",
                    source_reference=f"user_identities:{user_id}",
                    timestamp=profile.updated_at,
                )

            if profile.email_hash:
                graph.add_node(profile.email_hash, kind="EMAIL_HASH", valid_from=profile.updated_at)
                graph.add_edge(
                    source=user_id,
                    target=profile.email_hash,
                    relationship="SNAPSHOT_CURRENT",
                    source_reference=f"user_identities:{user_id}",
                    timestamp=profile.updated_at,
                )

        # Historical audit logs
        audit_records = self.bundle.audit_by_user_id.get(user_id, [])
        # Sort chronologically by event_time
        sorted_audits = sorted(audit_records, key=lambda r: r.event_time)

        for audit in sorted_audits:
            if audit.field_name == "email":
                if audit.normalized_old:
                    graph.add_node(
                        audit.normalized_old,
                        kind="HISTORICAL_EMAIL",
                        valid_to=audit.event_time,
                    )
                    graph.add_edge(
                        source=user_id,
                        target=audit.normalized_old,
                        relationship="AUDIT_UPDATE",
                        source_reference=f"audit_id:{audit.audit_id}",
                        timestamp=audit.event_time,
                        notes=f"Changed to {audit.new_value}",
                    )
                    old_hash = hashlib.sha256(audit.normalized_old.encode("utf-8")).hexdigest()
                    graph.add_node(
                        old_hash,
                        kind="EMAIL_HASH",
                        valid_to=audit.event_time,
                    )
                    graph.add_edge(
                        source=user_id,
                        target=old_hash,
                        relationship="HISTORICAL_EMAIL_HASH",
                        source_reference=f"audit_id:{audit.audit_id}",
                        timestamp=audit.event_time,
                    )
                if audit.normalized_new:
                    graph.add_node(
                        audit.normalized_new,
                        kind="PRIMARY_EMAIL",
                        valid_from=audit.event_time,
                    )
                    new_hash = hashlib.sha256(audit.normalized_new.encode("utf-8")).hexdigest()
                    graph.add_node(
                        new_hash,
                        kind="EMAIL_HASH",
                        valid_from=audit.event_time,
                    )

            elif audit.field_name == "phone":
                if audit.normalized_old:
                    graph.add_node(
                        audit.normalized_old,
                        kind="HISTORICAL_PHONE",
                        valid_to=audit.event_time,
                    )
                    graph.add_edge(
                        source=user_id,
                        target=audit.normalized_old,
                        relationship="AUDIT_UPDATE",
                        source_reference=f"audit_id:{audit.audit_id}",
                        timestamp=audit.event_time,
                    )

            elif audit.field_name == "email_hash":
                if audit.normalized_old:
                    graph.add_node(
                        audit.normalized_old,
                        kind="EMAIL_HASH",
                        valid_to=audit.event_time,
                    )
                    graph.add_edge(
                        source=user_id,
                        target=audit.normalized_old,
                        relationship="AUDIT_UPDATE",
                        source_reference=f"audit_id:{audit.audit_id}",
                        timestamp=audit.event_time,
                    )

        # If request_email was used and differs from primary, ensure node exists
        if request_email and request_email != graph.primary_email:
            if request_email not in graph.nodes:
                graph.add_node(request_email, kind="HISTORICAL_EMAIL")
                graph.add_edge(
                    source=user_id,
                    target=request_email,
                    relationship="AUDIT_UPDATE",
                    source_reference=f"request_context:{request_email}",
                )
