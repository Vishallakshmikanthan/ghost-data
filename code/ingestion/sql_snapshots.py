"""Ghost Data Still Exists - Isolated SQL Snapshot Processor.

Processes SQLite-compatible point-in-time database snapshots in an isolated,
in-memory SQLite environment without executing against live systems or mutating
evidence files. Detects database topology, parses JSON record payloads, extracts
claims with complete provenance, and identifies replica drift.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import (
    normalize_email,
    normalize_email_hash,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
)
from code.schemas import EvidenceClaim, EvidenceDocument

logger = logging.getLogger(__name__)

# Disallowed dangerous SQL tokens for in-memory safety
DISALLOWED_SQL_PATTERNS = [
    re.compile(r"\bATTACH\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bLOAD_EXTENSION\b", re.IGNORECASE),
]

# System mapping based on table or collection naming conventions
TABLE_SYSTEM_MAP = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}


def inspect_sql_metadata(sql_text: str) -> Dict[str, Any]:
    """Parse header comments to extract snapshot capture time, dialect, and topology."""
    metadata: Dict[str, Any] = {
        "captured_at": None,
        "topology": "unknown",
        "dialect": "sqlite",
    }
    for line in sql_text.splitlines()[:20]:
        line = line.strip()
        if not line.startswith("--"):
            continue
        ts_match = re.search(r"Captured at:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?)", line, re.I)
        if ts_match:
            metadata["captured_at"] = ts_match.group(1)
        topo_match = re.search(r"Database topology:\s*([a-zA-Z0-9_-]+)", line, re.I)
        if topo_match:
            metadata["topology"] = topo_match.group(1).lower()

    return metadata


def validate_sql_script(sql_text: str) -> None:
    """Validate that the SQL script contains no prohibited or dangerous operations."""
    for pattern in DISALLOWED_SQL_PATTERNS:
        if pattern.search(sql_text):
            raise ValueError(f"Prohibited SQL operation detected matching pattern: {pattern.pattern}")


class SqlSnapshotExtractor:
    """Processes SQLite-compatible dumps in an isolated in-memory database."""

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                sql_text = f.read()

            validate_sql_script(sql_text)
            sql_meta = inspect_sql_metadata(sql_text)
            doc.metadata["sql_topology"] = sql_meta["topology"]
            if sql_meta["captured_at"]:
                doc.metadata["snapshot_time"] = sql_meta["captured_at"]

            # Execute in an isolated in-memory SQLite database
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            try:
                cursor.executescript(sql_text)
            except sqlite3.Error as e:
                doc.extraction_status = "FAILED"
                doc.extraction_warning = f"SQLite execution failed: {e}"
                return claims

            # Discover all user-created tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row["name"] for row in cursor.fetchall()]
            doc.metadata["tables"] = tables

            # Track primary vs replica rows for drift comparison
            primary_records: Dict[str, Dict[str, Any]] = {}
            replica_records: Dict[str, Dict[str, Any]] = {}

            for table in tables:
                if table == "snapshot_scope":
                    continue

                cursor.execute(f"PRAGMA table_info({table})")
                cols = [col["name"] for col in cursor.fetchall()]

                cursor.execute(f"SELECT * FROM {table}")
                rows = cursor.fetchall()

                is_replica = any(k in table.lower() for k in ("replica", "failover", "reporting"))
                default_system = TABLE_SYSTEM_MAP.get(table, doc.origin)

                for row in rows:
                    row_dict = dict(row)
                    resource_id = normalize_resource_id(str(row_dict.get("resource_id") or ""))
                    system_id = default_system
                    if "origin_system" in row_dict and row_dict["origin_system"]:
                        system_id = row_dict["origin_system"]

                    row_ts = (
                        row_dict.get("updated_at")
                        or row_dict.get("indexed_at")
                        or sql_meta["captured_at"]
                        or doc.captured_at
                    )

                    identifiers: Set[str] = set()

                    # Direct columns
                    for u_col in ("user_id", "customer_reference", "subject_reference"):
                        val = row_dict.get(u_col)
                        if val:
                            identifiers.add(normalize_user_id(str(val)))

                    for e_col in ("email", "requester_email", "customer_email"):
                        val = row_dict.get(e_col)
                        if val:
                            identifiers.add(normalize_email(str(val)))

                    for p_col in ("phone", "requester_phone"):
                        val = row_dict.get(p_col)
                        if val:
                            p, _ = normalize_phone(str(val))
                            identifiers.add(p)

                    for h_col in ("email_hash",):
                        val = row_dict.get(h_col)
                        if val:
                            identifiers.add(normalize_email_hash(str(val)))

                    for n_col in ("full_name", "display_name", "requester_name"):
                        val = row_dict.get(n_col)
                        if val:
                            identifiers.add(str(val).strip())

                    # Check record_json column
                    if "record_json" in row_dict and row_dict["record_json"]:
                        try:
                            payload = json.loads(row_dict["record_json"])
                            if isinstance(payload, dict):
                                def _extract_payload_identifiers(obj: Any) -> None:
                                    if isinstance(obj, dict):
                                        for k, v in obj.items():
                                            if k in ("user_id", "subject_reference", "customer_reference") and v:
                                                identifiers.add(normalize_user_id(str(v)))
                                            elif k in ("email", "requester_email", "customer_email") and v:
                                                identifiers.add(normalize_email(str(v)))
                                            elif k in ("phone", "requester_phone") and v:
                                                p, _ = normalize_phone(str(v))
                                                if p:
                                                    identifiers.add(p)
                                            elif k in ("email_hash",) and v:
                                                identifiers.add(normalize_email_hash(str(v)))
                                            elif k in ("display_name", "full_name", "requester_name") and v:
                                                identifiers.add(str(v).strip())
                                            else:
                                                _extract_payload_identifiers(v)
                                    elif isinstance(obj, list):
                                        for item in obj:
                                            _extract_payload_identifiers(item)
                                    elif isinstance(obj, str):
                                        if "@" in obj:
                                            identifiers.add(normalize_email(obj))
                                        elif obj.startswith("user_"):
                                            identifiers.add(normalize_user_id(obj))

                                _extract_payload_identifiers(payload)
                                if "resource_id" in payload and not resource_id:
                                    resource_id = normalize_resource_id(str(payload["resource_id"]))
                        except Exception:
                            pass

                    # Determine observed state
                    observed_state = "PRESENT"
                    acc_status = str(row_dict.get("account_status") or "").upper()
                    if acc_status in ("DELETED", "PURGED", "ABSENT"):
                        observed_state = "ABSENT"

                    if "restore_allowed" in row_dict:
                        ra = row_dict["restore_allowed"]
                        if ra is False or ra == 0 or str(ra).lower() == "false":
                            observed_state = "BLOCKED"
                        elif ra is True or ra == 1 or str(ra).lower() == "true":
                            observed_state = "ACTIVE"

                    if "enabled" in row_dict:
                        en = row_dict["enabled"]
                        if en is False or en == 0 or str(en).lower() == "false":
                            observed_state = "DISABLED"
                        elif en is True or en == 1 or str(en).lower() == "true":
                            observed_state = "ACTIVE"

                    # Track primary vs replica
                    if is_replica:
                        replica_records[resource_id] = row_dict
                    else:
                        primary_records[resource_id] = row_dict

                    claim = EvidenceClaim(
                        evidence_id=doc.file_id,
                        source_type="sql_snapshot",
                        source_file=doc.file_path,
                        timestamp=row_ts,
                        system_id=system_id,
                        resource_id=resource_id,
                        observed_state=observed_state,
                        details=f"Table {table}: {json.dumps(row_dict, default=str)}",
                        identifiers_found=sorted(list(identifiers)),
                        extraction_method="sqlite_isolated_query",
                        extracted_fields={
                            "table": table,
                            "is_replica": is_replica,
                            **row_dict,
                        },
                    )
                    claims.append(claim)

            # Detect replication drift
            drift_detected = False
            for res_id, replica_data in replica_records.items():
                if res_id not in primary_records:
                    drift_detected = True
                    break
            doc.metadata["replication_drift_detected"] = drift_detected
            doc.extraction_status = "SUCCESS"
            conn.close()

        except Exception as e:
            logger.warning("Error processing SQL snapshot %s: %s", file_path, e)
            doc.extraction_status = "FAILED"
            doc.extraction_warning = f"SQL snapshot error: {e}"

        return claims
