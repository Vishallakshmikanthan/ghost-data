"""Ghost Data Still Exists - Format-Specific Evidence Extractors.

Deterministic content extractors for:
- Plain-text logs (.log)
- Structured plain-text records (.txt)
- JSON snapshot bundles and configurations (.json)
- PDF documents (.pdf)
- PNG application screenshots (.png)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.ingestion.normalization import (
    normalize_email,
    normalize_email_hash,
    normalize_phone,
    normalize_user_id,
)
from code.schemas import EvidenceClaim, EvidenceDocument

logger = logging.getLogger(__name__)

# Regex patterns for identifier discovery in unstructured text
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
USER_ID_REGEX = re.compile(r"\buser_\d+\b")
RESOURCE_ID_REGEX = re.compile(r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|email_delivery)_\d+\b")
KV_LOG_REGEX = re.compile(r'([a-zA-Z0-9_]+)=(?:"([^"]*)"|\'([^\']*)\'|(\S+))')
PIPE_LINE_REGEX = re.compile(r"^[-*]?\s*(\d{4}-\d{2}-\d{2}T[^\s|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(.*)$")


def extract_identifiers_from_text(text: str) -> List[str]:
    """Scan text and return normalized unique email, user_id, phone, and hash candidates."""
    found: Set[str] = set()

    for m in EMAIL_REGEX.findall(text):
        found.add(normalize_email(m))

    for m in USER_ID_REGEX.findall(text):
        found.add(normalize_user_id(m))

    return sorted(list(found))


class LogExtractor:
    """Extractor for key-value and semi-structured .log files."""

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        trace_claims: Dict[str, List[EvidenceClaim]] = {}
        trace_idents: Dict[str, Set[str]] = {}
        trace_resources: Dict[str, Set[str]] = {}

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                # Extract line timestamp if present at beginning
                ts_match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?)", line)
                line_ts = ts_match.group(1) if ts_match else doc.captured_at

                # Parse key=value tokens or embedded JSON
                kv_pairs: Dict[str, str] = {}
                if "{" in line and "}" in line:
                    try:
                        j_part = line[line.index("{"):line.rindex("}") + 1]
                        loaded = json.loads(j_part)
                        if isinstance(loaded, dict):
                            kv_pairs = {k: str(v) for k, v in loaded.items()}
                    except Exception:
                        pass

                if not kv_pairs:
                    for match in KV_LOG_REGEX.finditer(line):
                        k = match.group(1)
                        v = match.group(2) or match.group(3) or match.group(4) or ""
                        kv_pairs[k] = v

                # Determine system / service
                system_id = kv_pairs.get("service") or kv_pairs.get("logger") or kv_pairs.get("system") or doc.origin
                resource_id = kv_pairs.get("resource_id") or ""

                # Look for identifiers
                identifiers: Set[str] = set()
                for key in (
                    "matched_value",
                    "subject_key",
                    "subject_ref",
                    "lookup_key",
                    "match_key",
                    "correlation_key",
                    "user_id",
                    "email",
                    "phone",
                    "user_reference",
                    "customer_reference",
                ):
                    val = kv_pairs.get(key)
                    if val and val.lower() not in ("null", "none", "nil"):
                        if "@" in val:
                            identifiers.add(normalize_email(val))
                        elif val.startswith("user_"):
                            identifiers.add(normalize_user_id(val))
                        elif len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val):
                            identifiers.add(normalize_email_hash(val))
                        elif val.startswith("+") or (not any(c.isalpha() for c in val) and re.match(r"^\d{7,}$", re.sub(r"\D", "", val))):
                            cleaned, _ = normalize_phone(val)
                            identifiers.add(cleaned)
                        else:
                            identifiers.add(val)

                # Also search text for emails and user_ids
                for ident in extract_identifiers_from_text(line):
                    identifiers.add(ident)

                trace_id = kv_pairs.get("trace_id")
                if not resource_id and not identifiers and not trace_id:
                    # Skip noise lines without resource ID, subject identifiers, or correlation key
                    continue

                # Infer observed state from event context
                event_name = kv_pairs.get("event", "").lower()
                http_status = kv_pairs.get("http_status", "")
                rows_str = kv_pairs.get("rows", "")

                observed_state = "PRESENT"
                if http_status == "404" or rows_str == "0" or "not_found" in event_name or "cache_miss" in event_name:
                    observed_state = "ABSENT"
                elif "active" in event_name or kv_pairs.get("state") == "ACTIVE":
                    observed_state = "ACTIVE"
                elif "blocked" in event_name or kv_pairs.get("state") == "BLOCKED":
                    observed_state = "BLOCKED"
                elif "disabled" in event_name or kv_pairs.get("state") == "DISABLED":
                    observed_state = "DISABLED"
                elif "anonymized" in event_name or kv_pairs.get("state") == "ANONYMIZED":
                    observed_state = "ANONYMIZED"
                elif "redacted" in event_name or kv_pairs.get("state") == "REDACTED":
                    observed_state = "REDACTED"
                elif (system_id in ("analytics_platform", "analytics_service") or resource_id.startswith("analytics_")) and not identifiers:
                    observed_state = "ANONYMIZED"

                cat = None
                if "email_opt_in" in kv_pairs or "opt_in" in kv_pairs or "marketing" in event_name:
                    cat = "MARKETING_PROFILE"
                elif resource_id.startswith("audit_record_") or system_id == "security_service":
                    cat = "SECURITY_AUDIT_LOG"
                elif resource_id.startswith("tombstone_"):
                    cat = "ACCOUNT_TOMBSTONE"
                elif resource_id.startswith("search_"):
                    cat = "SEARCH_INDEX_ENTRY"
                elif resource_id.startswith("data_flow_"):
                    cat = "DATA_FLOW"
                elif resource_id.startswith("backup_"):
                    cat = "BACKUP_ARCHIVE"
                elif resource_id.startswith("export_"):
                    cat = "USER_EXPORT"
                elif "legacy_contact_index" in str(kv_pairs) or "contact" in event_name or "attachment" in str(kv_pairs):
                    cat = "SUPPORT_ATTACHMENT"
                elif resource_id.startswith("support_") or system_id in ("support_platform", "support_replica"):
                    cat = "SUPPORT_MESSAGE"

                claim = EvidenceClaim(
                    evidence_id=doc.file_id,
                    source_type="file",
                    source_file=doc.file_path,
                    timestamp=line_ts,
                    system_id=system_id,
                    resource_id=resource_id,
                    observed_state=observed_state,
                    details=line,
                    identifiers_found=sorted(list(identifiers)),
                    extraction_method="log_kv_parser",
                    extracted_fields=kv_pairs,
                    data_category=cat,
                )
                claims.append(claim)

                if trace_id:
                    trace_claims.setdefault(trace_id, []).append(claim)
                    trace_idents.setdefault(trace_id, set()).update(identifiers)
                    if resource_id:
                        trace_resources.setdefault(trace_id, set()).add(resource_id)

        # Correlate identifiers and resources across lines in the same trace_id
        for tid, claims_list in trace_claims.items():
            shared_idents = trace_idents.get(tid, set())
            shared_resources = trace_resources.get(tid, set())
            for c in claims_list:
                if shared_idents:
                    c.identifiers_found = sorted(list(set(c.identifiers_found) | shared_idents))
                if not c.resource_id and len(shared_resources) == 1:
                    c.resource_id = next(iter(shared_resources))

        return [c for c in claims if c.resource_id or c.identifiers_found]


class TextRecordExtractor:
    """Extractor for pipe-delimited and structured plain-text handover logs (.txt)."""

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                pipe_match = PIPE_LINE_REGEX.match(line)
                if pipe_match:
                    ts = pipe_match.group(1)
                    system_id = pipe_match.group(2).strip()
                    resource_id = pipe_match.group(3).strip()
                    data_category = pipe_match.group(4).strip()
                    status_raw = pipe_match.group(5).strip().upper()
                    ident_col = pipe_match.group(6).strip()

                    # Map status string to canonical state
                    state = "PRESENT"
                    if status_raw in ("NOT_FOUND", "ABSENT", "DELETED"):
                        state = "ABSENT"
                    elif status_raw in ("FOUND", "IDENTIFIABLE", "PRESENT"):
                        state = "PRESENT"
                    elif status_raw in ("ANONYMIZED",):
                        state = "ANONYMIZED"
                    elif status_raw in ("REDACTED",):
                        state = "REDACTED"
                    elif status_raw in ("ACTIVE",):
                        state = "ACTIVE"
                    elif status_raw in ("BLOCKED",):
                        state = "BLOCKED"
                    elif status_raw in ("DISABLED",):
                        state = "DISABLED"

                    identifiers: Set[str] = set()
                    if ident_col and ident_col.lower() != "no direct identifier":
                        if "@" in ident_col:
                            identifiers.add(normalize_email(ident_col))
                        elif ident_col.startswith("user_"):
                            identifiers.add(normalize_user_id(ident_col))
                        elif ident_col.startswith("+") or re.match(r"^\d{7,}$", re.sub(r"\D", "", ident_col)):
                            cleaned, _ = normalize_phone(ident_col)
                            identifiers.add(cleaned)
                        else:
                            identifiers.add(ident_col)

                    # Also scan for secondary identifiers
                    for ident in extract_identifiers_from_text(ident_col):
                        identifiers.add(ident)

                    claim = EvidenceClaim(
                        evidence_id=doc.file_id,
                        source_type="file",
                        source_file=doc.file_path,
                        timestamp=ts,
                        system_id=system_id,
                        resource_id=resource_id,
                        observed_state=state,
                        details=line,
                        identifiers_found=sorted(list(identifiers)),
                        extraction_method="txt_pipe_parser",
                        data_category=data_category,
                        extracted_fields={
                            "system_id": system_id,
                            "resource_id": resource_id,
                            "data_category": data_category,
                            "status": status_raw,
                            "identifier_col": ident_col,
                        },
                    )
                    claims.append(claim)
                else:
                    # Generic line: check if it contains identifiers
                    line_idents = extract_identifiers_from_text(line)
                    res_matches = RESOURCE_ID_REGEX.findall(line)
                    if line_idents or res_matches:
                        claim = EvidenceClaim(
                            evidence_id=doc.file_id,
                            source_type="file",
                            source_file=doc.file_path,
                            timestamp=doc.captured_at,
                            system_id=doc.origin,
                            resource_id=res_matches[0] if res_matches else "",
                            observed_state="PRESENT",
                            details=line,
                            identifiers_found=line_idents,
                            extraction_method="txt_unstructured_parser",
                        )
                        claims.append(claim)

        return claims


class JsonExtractor:
    """Extractor for structured JSON snapshot bundles and configurations (.json)."""

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        exported_at = data.get("exported_at") or doc.captured_at
        origin = data.get("origin") or data.get("source") or doc.origin

        # Pattern A: snapshots array (customer_data_snapshot_bundle_*.json)
        if "snapshots" in data and isinstance(data["snapshots"], list):
            for snapshot in data["snapshots"]:
                snap_ts = snapshot.get("captured_at") or exported_at
                data_store = snapshot.get("data_store") or origin
                collection = snapshot.get("collection") or ""

                for record in snapshot.get("records", []):
                    resource_id = record.get("resource_id") or ""
                    identifiers: Set[str] = set()

                    subj = record.get("subject_reference") or record.get("customer_reference") or record.get("user_id")
                    if subj:
                        if "@" in subj:
                            identifiers.add(normalize_email(subj))
                        elif subj.startswith("user_"):
                            identifiers.add(normalize_user_id(subj))
                        else:
                            identifiers.add(subj)
                    if record.get("email"):
                        identifiers.add(normalize_email(record["email"]))
                    if record.get("phone"):
                        p, _ = normalize_phone(record["phone"])
                        identifiers.add(p)
                    if record.get("name"):
                        identifiers.add(record["name"])
                    if record.get("full_name"):
                        identifiers.add(record["full_name"])
                    if record.get("email_hash"):
                        identifiers.add(normalize_email_hash(record["email_hash"]))

                    # Inspect nested document object if present
                    doc_obj = record.get("document")
                    if isinstance(doc_obj, dict):
                        if doc_obj.get("email"):
                            identifiers.add(normalize_email(doc_obj["email"]))
                        if doc_obj.get("customer_reference"):
                            identifiers.add(normalize_user_id(doc_obj["customer_reference"]))
                        if doc_obj.get("subject_reference"):
                            identifiers.add(normalize_user_id(doc_obj["subject_reference"]))
                        if doc_obj.get("display_name"):
                            identifiers.add(doc_obj["display_name"])

                    account_status = record.get("account_status", "ACTIVE")
                    observed_state = "PRESENT"
                    category: Optional[str] = None
                    if collection == "deleted_customer_references" or resource_id.startswith("tombstone_"):
                        category = "ACCOUNT_TOMBSTONE"
                        has_direct_pii = any(bool(record.get(k)) for k in ("name", "full_name", "email", "phone", "email_hash"))
                        observed_state = "PRESENT" if has_direct_pii else "REDACTED"
                    elif data_store in ("support_platform", "support_replica") or collection == "support_tickets" or resource_id.startswith("support_"):
                        category = "SUPPORT_MESSAGE"
                        has_direct_pii = any(bool(record.get(k)) for k in ("requester_name", "requester_email", "requester_phone", "name", "email", "phone"))
                        is_redacted_text = "[REDACTED]" in str(record.get("message_text") or "")
                        observed_state = "REDACTED" if (is_redacted_text or not has_direct_pii) else "PRESENT"
                    elif collection == "security_audit_events" or resource_id.startswith("audit_record_"):
                        category = "SECURITY_AUDIT_LOG"
                        observed_state = "PRESENT"
                    elif collection == "customer_analytics_reports" or resource_id.startswith("analytics_"):
                        category = "ANALYTICS_PROFILE"
                        has_direct_pii = any(bool(record.get(k)) for k in ("user_id", "email", "phone", "email_hash", "customer_reference"))
                        observed_state = "PRESENT" if has_direct_pii else "ANONYMIZED"
                    elif account_status in ("DELETED", "INACTIVE_PURGED"):
                        observed_state = "ABSENT"

                    claim = EvidenceClaim(
                        evidence_id=doc.file_id,
                        source_type="file",
                        source_file=doc.file_path,
                        timestamp=snap_ts,
                        system_id=data_store,
                        resource_id=resource_id,
                        observed_state=observed_state,
                        details=f"Collection {collection}: {json.dumps(record)}",
                        identifiers_found=sorted(list(identifiers)),
                        extraction_method="json_snapshot_bundle",
                        extracted_fields=record,
                        data_category=category,
                    )
                    claims.append(claim)

        # Pattern B: records array (connector_configurations_*.json, customer_export_jobs_*.json, etc.)
        elif "records" in data and isinstance(data["records"], list):
            for record in data["records"]:
                rec_ts = record.get("recorded_at") or record.get("captured_at") or exported_at
                system = record.get("system") or record.get("origin_system") or origin
                resource_id = record.get("resource_id") or ""

                identifiers: Set[str] = set()
                subj = record.get("subject_reference") or record.get("customer_reference") or record.get("subject_selector")
                if subj:
                    if "@" in subj:
                        identifiers.add(normalize_email(subj))
                    elif subj.startswith("user_"):
                        identifiers.add(normalize_user_id(subj))
                    else:
                        identifiers.add(subj)

                if record.get("email"):
                    identifiers.add(normalize_email(record["email"]))
                if record.get("user_id"):
                    identifiers.add(normalize_user_id(record["user_id"]))
                if record.get("phone"):
                    p, _ = normalize_phone(record["phone"])
                    identifiers.add(p)

                # Also scan any field containing email or user_id (e.g. owner_email, customer_email, requester_email)
                for k, v in record.items():
                    if isinstance(v, str):
                        if "@" in v:
                            identifiers.add(normalize_email(v))
                        elif v.startswith("user_"):
                            identifiers.add(normalize_user_id(v))

                result_val = str(record.get("result") or record.get("status") or "").upper()
                observed_state = "PRESENT"
                if result_val in ("NOT_FOUND", "ABSENT", "DELETED"):
                    observed_state = "ABSENT"
                elif result_val in ("ACTIVE", "ENABLED"):
                    observed_state = "ACTIVE"
                elif result_val in ("DISABLED",):
                    observed_state = "DISABLED"
                elif result_val in ("BLOCKED",):
                    observed_state = "BLOCKED"
                elif result_val in ("ANONYMIZED",):
                    observed_state = "ANONYMIZED"
                elif result_val in ("REDACTED",):
                    observed_state = "REDACTED"

                claim = EvidenceClaim(
                    evidence_id=doc.file_id,
                    source_type="file",
                    source_file=doc.file_path,
                    timestamp=rec_ts,
                    system_id=system,
                    resource_id=resource_id,
                    observed_state=observed_state,
                    details=json.dumps(record),
                    identifiers_found=sorted(list(identifiers)),
                    extraction_method="json_records_array",
                    extracted_fields=record,
                )
                claims.append(claim)

        # Pattern C: service_exports mapping (service_resource_export_*.json)
        elif "service_exports" in data and isinstance(data["service_exports"], dict):
            for sname, sdata in data["service_exports"].items():
                if not isinstance(sdata, dict):
                    continue
                for col_name, items in sdata.items():
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        rec_ts = item.get("observed_at") or exported_at
                        resource_id = item.get("resource_id") or ""
                        identifiers: Set[str] = set()
                        category: Optional[str] = None
                        observed_state = "PRESENT"

                        if sname == "account_service":
                            category = "USER_PROFILE"
                            profile = item.get("profile")
                            if item.get("status_code") == 404 or profile is None or profile == {}:
                                observed_state = "ABSENT"
                            else:
                                observed_state = "PRESENT"
                                if isinstance(profile, dict):
                                    if profile.get("email"):
                                        identifiers.add(normalize_email(profile["email"]))
                                    if profile.get("user_id"):
                                        identifiers.add(normalize_user_id(profile["user_id"]))
                                    if profile.get("customer_reference"):
                                        identifiers.add(normalize_user_id(profile["customer_reference"]))
                                    if profile.get("phone"):
                                        p, _ = normalize_phone(profile["phone"])
                                        identifiers.add(p)
                                    if profile.get("email_hash"):
                                        identifiers.add(normalize_email_hash(profile["email_hash"]))
                                    if profile.get("full_name"):
                                        identifiers.add(profile["full_name"])

                        elif sname == "analytics_platform":
                            category = "ANALYTICS_PROFILE"
                            if item.get("email"):
                                identifiers.add(normalize_email(item["email"]))
                            if item.get("customer_reference"):
                                identifiers.add(normalize_user_id(item["customer_reference"]))
                            if item.get("phone"):
                                p, _ = normalize_phone(item["phone"])
                                identifiers.add(p)
                            if item.get("email_hash"):
                                identifiers.add(normalize_email_hash(item["email_hash"]))
                            observed_state = "PRESENT" if identifiers else "ANONYMIZED"

                        elif sname == "backup_service":
                            category = "BACKUP_ARCHIVE"
                            if item.get("customer_email"):
                                identifiers.add(normalize_email(item["customer_email"]))
                            if item.get("customer_reference"):
                                identifiers.add(normalize_user_id(item["customer_reference"]))
                            restore_allowed = item.get("restore_allowed", True)
                            observed_state = "ACTIVE" if restore_allowed else "BLOCKED"

                        elif sname == "export_service":
                            category = "USER_EXPORT"
                            if item.get("owner_email"):
                                identifiers.add(normalize_email(item["owner_email"]))
                            if item.get("customer_reference"):
                                identifiers.add(normalize_user_id(item["customer_reference"]))
                            if item.get("owner_phone"):
                                p, _ = normalize_phone(item["owner_phone"])
                                identifiers.add(p)
                            observed_state = "PRESENT"

                        elif sname == "integration_service":
                            category = "DATA_FLOW"
                            if item.get("customer_email"):
                                identifiers.add(normalize_email(item["customer_email"]))
                            if item.get("customer_reference"):
                                identifiers.add(normalize_user_id(item["customer_reference"]))
                            enabled = item.get("enabled", True)
                            observed_state = "ACTIVE" if enabled else "DISABLED"

                        elif sname == "search_service":
                            category = "SEARCH_INDEX_ENTRY"
                            query = item.get("query", "")
                            if "@" in query:
                                identifiers.add(normalize_email(query))
                            elif query.startswith("user_"):
                                identifiers.add(normalize_user_id(query))
                            hits = item.get("hits", {})
                            total = hits.get("total", 0) if isinstance(hits, dict) else 0
                            docs = hits.get("documents", []) if isinstance(hits, dict) else []
                            for d in docs:
                                if isinstance(d, dict):
                                    if d.get("email"):
                                        identifiers.add(normalize_email(d["email"]))
                                    if d.get("customer_reference"):
                                        identifiers.add(normalize_user_id(d["customer_reference"]))
                                    if d.get("phone"):
                                        p, _ = normalize_phone(d["phone"])
                                        identifiers.add(p)
                                    if d.get("email_hash"):
                                        identifiers.add(normalize_email_hash(d["email_hash"]))
                                    if d.get("display_name"):
                                        identifiers.add(d["display_name"])
                            observed_state = "PRESENT" if total > 0 else "ABSENT"

                        elif sname == "security_service":
                            category = "SECURITY_AUDIT_LOG"
                            subj = item.get("subject_reference", "")
                            if "@" in subj:
                                identifiers.add(normalize_email(subj))
                            elif subj.startswith("user_"):
                                identifiers.add(normalize_user_id(subj))
                            observed_state = "PRESENT"

                        elif sname == "support_platform":
                            category = "SUPPORT_MESSAGE"
                            if item.get("customer_reference"):
                                identifiers.add(normalize_user_id(item["customer_reference"]))
                            direct_found = False
                            if item.get("requester_email"):
                                identifiers.add(normalize_email(item["requester_email"]))
                                direct_found = True
                            if item.get("requester_phone"):
                                p, _ = normalize_phone(item["requester_phone"])
                                identifiers.add(p)
                                direct_found = True
                            if item.get("requester_name"):
                                identifiers.add(item["requester_name"])
                                direct_found = True
                            observed_state = "PRESENT" if direct_found else "REDACTED"

                        claim = EvidenceClaim(
                            evidence_id=doc.file_id,
                            source_type="file",
                            source_file=doc.file_path,
                            timestamp=rec_ts,
                            system_id=sname,
                            resource_id=resource_id,
                            observed_state=observed_state,
                            details=json.dumps(item),
                            identifiers_found=sorted(list(identifiers)),
                            extraction_method="json_service_exports",
                            extracted_fields=item,
                            data_category=category,
                        )
                        claims.append(claim)

        return claims


class PdfExtractor:
    """Extractor for text-based PDF documents (.pdf) using pypdf."""

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            doc.metadata["page_count"] = len(reader.pages)

            full_text_pages: List[str] = []
            for page_idx, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                full_text_pages.append(page_text)

                # Scan page for identifiers
                page_idents = extract_identifiers_from_text(page_text)
                res_matches = RESOURCE_ID_REGEX.findall(page_text)

                # Look for order or support references (e.g. CS-4827 / support_001)
                support_ref = re.search(r"\b(support_\d+)\b", page_text)
                audit_ref = re.search(r"\b(audit_record_\d+)\b", page_text)
                order_ref = re.search(r"\b(ORD-\d+)\b", page_text)
                statement_ref = re.search(r"\b(NST-\d+-\d+-\d+)\b", page_text)

                resource_id = ""
                if support_ref:
                    resource_id = support_ref.group(1)
                elif audit_ref:
                    resource_id = audit_ref.group(1)
                elif res_matches:
                    resource_id = res_matches[0]
                elif order_ref:
                    resource_id = order_ref.group(1)

                if page_idents or resource_id:
                    cat = None
                    if "attachment" in doc.file_path.lower() or doc.origin in ("support_platform", "support_replica", "object_storage"):
                        cat = "SUPPORT_ATTACHMENT"

                    claim = EvidenceClaim(
                        evidence_id=doc.file_id,
                        source_type="file",
                        source_file=doc.file_path,
                        timestamp=doc.captured_at,
                        system_id=doc.origin,
                        resource_id=resource_id,
                        observed_state="PRESENT",
                        details=f"Page {page_idx}: {page_text[:300].strip()}...",
                        identifiers_found=page_idents,
                        extraction_method="pypdf_page_text",
                        data_category=cat,
                        extracted_fields={
                            "page": page_idx,
                            "order_ref": order_ref.group(1) if order_ref else None,
                            "statement_ref": statement_ref.group(1) if statement_ref else None,
                            "is_attachment": bool(cat == "SUPPORT_ATTACHMENT"),
                        },
                    )
                    claims.append(claim)

            doc.content = "\n---PAGE---\n".join(full_text_pages)
        except Exception as e:
            logger.warning("Failed to extract PDF %s: %s", file_path, e)
            doc.extraction_status = "FAILED"
            doc.extraction_warning = f"PDF extraction error: {e}"

        return claims


class ImageExtractor:
    """Extractor for image screenshots (.png).

    Extracts file dimensions and image properties via PIL.
    If OCR libraries (tesseract/easyocr) are unavailable, records an explicit
    diagnostic warning rather than claiming complete textual analysis.
    """

    @staticmethod
    def extract(doc: EvidenceDocument, file_path: Path) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                doc.metadata["image_size"] = img.size  # (width, height)
                doc.metadata["image_format"] = img.format
                doc.metadata["image_mode"] = img.mode

            # Check for OCR availability
            ocr_text = None
            try:
                import pytesseract
                ocr_text = pytesseract.image_to_string(str(file_path))
                doc.metadata["ocr_method"] = "pytesseract"
            except (ImportError, Exception):
                pass

            if ocr_text:
                doc.content = ocr_text
                doc.extraction_status = "SUCCESS"
                idents = extract_identifiers_from_text(ocr_text)
                res_matches = RESOURCE_ID_REGEX.findall(ocr_text)
                if idents or res_matches:
                    claim = EvidenceClaim(
                        evidence_id=doc.file_id,
                        source_type="file",
                        source_file=doc.file_path,
                        timestamp=doc.captured_at,
                        system_id=doc.origin,
                        resource_id=res_matches[0] if res_matches else "",
                        observed_state="PRESENT",
                        details=f"OCR text: {ocr_text[:200]}...",
                        identifiers_found=idents,
                        extraction_method="ocr_text",
                    )
                    claims.append(claim)
            else:
                doc.extraction_status = "PARTIAL"
                doc.extraction_warning = (
                    "OCR engine (tesseract/easyocr) not installed or configured; image text extraction unavailable"
                )
        except Exception as e:
            doc.extraction_status = "FAILED"
            doc.extraction_warning = f"Image inspection error: {e}"

        return claims
