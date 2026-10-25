"""Ghost Data Still Exists - Per-Case Resource State Ledger.

Maintains an indexed per-case ledger of relevant subject-linked resources across
all systems, storing snapshot history, event timelines, category classifications,
and reconstructed states.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from code.reconstruction.models import (
    NormalizedEvent,
    ReconstructedState,
    ResourceSnapshot,
    ResourceStateAssessment,
)

logger = logging.getLogger(__name__)

# Canonical resource category inference from system ID and resource ID prefixes
SYSTEM_CATEGORY_MAP: Dict[str, str] = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "object_store": "USER_EXPORT",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "security_service": "SECURITY_AUDIT_LOG",
    "backup_service": "BACKUP_ARCHIVE",
    "integration_service": "INTEGRATION_CONNECTOR",
}


@dataclass
class LedgerEntry:
    """An entry in the resource ledger tracking all historical and temporal observations for a resource."""
    resource_id: str
    system_id: str
    subject_id: str
    resource_category: str
    attribution_method: str
    snapshots: List[ResourceSnapshot] = field(default_factory=list)
    events: List[NormalizedEvent] = field(default_factory=list)
    assessment: Optional[ResourceStateAssessment] = None
    exposed_fields: Set[str] = field(default_factory=set)
    diagnostics: List[str] = field(default_factory=list)


class ResourceLedger:
    """Per-case ledger tracking candidate and confirmed resources associated with a deletion subject."""

    def __init__(self, subject_id: str, primary_email: str) -> None:
        self.subject_id = subject_id
        self.primary_email = primary_email
        self.entries: Dict[str, LedgerEntry] = {}  # Key: "system_id:resource_id"

    def _entry_key(self, system_id: str, resource_id: str) -> str:
        return f"{system_id.strip()}:{resource_id.strip()}"

    def get_or_create_entry(
        self,
        system_id: str,
        resource_id: str,
        attribution_method: str = "EXACT_IDENTIFIER",
        category: Optional[str] = None,
    ) -> LedgerEntry:
        """Retrieve existing ledger entry or initialize a new one with inferred category."""
        key = self._entry_key(system_id, resource_id)
        if key not in self.entries:
            if not category:
                if resource_id.startswith("tombstone_"):
                    category = "ACCOUNT_TOMBSTONE"
                elif resource_id.startswith("data_flow_"):
                    category = "DATA_FLOW"
                elif resource_id.startswith("audit_record_"):
                    category = "SECURITY_AUDIT_LOG"
                elif resource_id.startswith("backup_"):
                    category = "BACKUP_ARCHIVE"
                elif resource_id.startswith("export_"):
                    category = "USER_EXPORT"
                elif resource_id.startswith("support_"):
                    category = "SUPPORT_MESSAGE"
                elif resource_id.startswith("search_"):
                    category = "SEARCH_INDEX_ENTRY"
                elif resource_id.startswith("analytics_"):
                    category = "ANALYTICS_PROFILE"
                elif resource_id.startswith("account_"):
                    category = "USER_PROFILE"
                else:
                    category = SYSTEM_CATEGORY_MAP.get(system_id, "USER_PROFILE")

            self.entries[key] = LedgerEntry(
                resource_id=resource_id,
                system_id=system_id,
                subject_id=self.subject_id,
                resource_category=category,
                attribution_method=attribution_method,
            )

        return self.entries[key]

    def add_snapshot(self, snapshot: ResourceSnapshot, attribution_method: str = "IDENTIFIER_MATCH") -> None:
        """Register a point-in-time snapshot observation in the ledger."""
        entry = self.get_or_create_entry(
            snapshot.system_id,
            snapshot.resource_id,
            attribution_method,
            snapshot.resource_category,
        )
        entry.snapshots.append(snapshot)
        for ident in snapshot.identifiers_present:
            entry.exposed_fields.add(ident)

    def add_event(self, event: NormalizedEvent, attribution_method: str = "IDENTIFIER_MATCH") -> None:
        """Register an operational event in the ledger."""
        entry = self.get_or_create_entry(
            event.system_id,
            event.resource_id,
            attribution_method,
        )
        entry.events.append(event)
        for sid in event.subject_ids:
            entry.exposed_fields.add(sid)

    def set_assessment(self, system_id: str, resource_id: str, assessment: ResourceStateAssessment) -> None:
        """Attach the final reconstructed state assessment to the ledger entry."""
        key = self._entry_key(system_id, resource_id)
        if key in self.entries:
            self.entries[key].assessment = assessment

    def all_assessments(self) -> Dict[str, ResourceStateAssessment]:
        """Return all computed assessments indexed by resource_id."""
        return {
            e.resource_id: e.assessment
            for e in self.entries.values()
            if e.assessment is not None
        }
