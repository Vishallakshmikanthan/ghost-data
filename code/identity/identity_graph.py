"""Ghost Data Still Exists - Typed Subject Identity Graph & Temporal Intervals.

Models a subject's identity as a temporal graph of evidence-supported
identifier relationships, validity intervals, and attribution provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import (
    compare_timestamps,
    is_at_or_before,
    normalize_email,
    normalize_email_hash,
    normalize_phone,
    normalize_user_id,
)
from code.schemas import SubjectIdentityGraph


@dataclass
class IdentityNode:
    """A node in the subject identity graph representing an identifier."""
    identifier: str
    kind: str  # USER_ID, PRIMARY_EMAIL, HISTORICAL_EMAIL, PHONE, HISTORICAL_PHONE, EMAIL_HASH, NAME, TOMBSTONE_ID
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_valid_at(self, query_ts: Optional[str]) -> bool:
        """Check if this identifier was valid at query_ts."""
        if not query_ts:
            return True
        if self.valid_from and not is_at_or_before(self.valid_from, query_ts):
            return False
        if self.valid_to and is_at_or_before(self.valid_to, query_ts):
            return False
        return True


@dataclass
class IdentityEdge:
    """A directed evidence-supported relationship between two identifiers."""
    source: str
    target: str
    relationship: str  # SNAPSHOT_CURRENT, AUDIT_UPDATE, EVIDENCE_LINK, TOMBSTONE_REDACTION
    source_reference: str  # e.g., "user_identities:user_18", "audit_id:audit_59"
    timestamp: Optional[str] = None
    notes: str = ""


class TemporalIdentityGraph:
    """Graph structure maintaining nodes, edges, validity intervals, and resolution explanations."""

    def __init__(self, user_id: Optional[str] = None, primary_email: str = "") -> None:
        self.user_id = user_id
        self.primary_email = primary_email
        self.nodes: Dict[str, IdentityNode] = {}
        self.edges: List[IdentityEdge] = []
        self.match_classification: str = "exact"
        self.resolution_paths: Dict[str, List[str]] = {}
        self.ambiguities: List[str] = []

    def add_node(
        self,
        identifier: str,
        kind: str,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IdentityNode:
        """Add or update an identifier node in the graph."""
        clean_id = identifier.strip()
        if kind in ("PRIMARY_EMAIL", "HISTORICAL_EMAIL"):
            clean_id = normalize_email(clean_id)
        elif kind == "USER_ID":
            clean_id = normalize_user_id(clean_id)
        elif kind in ("PHONE", "HISTORICAL_PHONE"):
            clean_id, _ = normalize_phone(clean_id)
        elif kind == "EMAIL_HASH":
            clean_id = normalize_email_hash(clean_id)

        node = IdentityNode(
            identifier=clean_id,
            kind=kind,
            valid_from=valid_from,
            valid_to=valid_to,
            metadata=metadata or {},
        )
        self.nodes[clean_id] = node
        return node

    def add_edge(
        self,
        source: str,
        target: str,
        relationship: str,
        source_reference: str,
        timestamp: Optional[str] = None,
        notes: str = "",
    ) -> None:
        """Add an evidence-backed relationship edge between two identifiers."""
        edge = IdentityEdge(
            source=source,
            target=target,
            relationship=relationship,
            source_reference=source_reference,
            timestamp=timestamp,
            notes=notes,
        )
        self.edges.append(edge)

    def to_schema_graph(self) -> SubjectIdentityGraph:
        """Export to the shared SubjectIdentityGraph dataclass contract."""
        emails: Set[str] = set()
        phones: Set[str] = set()
        names: Set[str] = set()
        hashes: Set[str] = set()
        tombstones: Set[str] = set()
        intervals: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

        for ident, node in self.nodes.items():
            intervals[ident] = (node.valid_from, node.valid_to)
            if node.kind in ("PRIMARY_EMAIL", "HISTORICAL_EMAIL"):
                emails.add(ident)
            elif node.kind in ("PHONE", "HISTORICAL_PHONE"):
                phones.add(ident)
            elif node.kind == "NAME":
                names.add(ident)
            elif node.kind == "EMAIL_HASH":
                hashes.add(ident)
            elif node.kind == "TOMBSTONE_ID":
                tombstones.add(ident)

        if self.user_id:
            tombstones.add(self.user_id)

        edge_dicts = [
            {
                "source": e.source,
                "target": e.target,
                "relationship": e.relationship,
                "source_reference": e.source_reference,
                "timestamp": e.timestamp,
                "notes": e.notes,
            }
            for e in self.edges
        ]

        return SubjectIdentityGraph(
            user_id=self.user_id,
            primary_email=self.primary_email,
            emails=emails,
            names=names,
            phones=phones,
            email_hashes=hashes,
            tombstone_ids=tombstones,
            match_classification=self.match_classification,
            resolution_paths=self.resolution_paths,
            ambiguities=self.ambiguities,
            identity_edges=edge_dicts,
            validity_intervals=intervals,
        )
