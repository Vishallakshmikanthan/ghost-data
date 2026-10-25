"""Ghost Data Still Exists - Resource Category Classifier.

Maps reconstructed resources, systems, and evidence collections to the 15
data categories defined in deletion_rules.csv.
"""

from __future__ import annotations

import re
from typing import Optional


class ResourceClassifier:
    """Classifies resources into the 15 policy data categories."""

    CATEGORY_USER_PROFILE = "USER_PROFILE"
    CATEGORY_SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
    CATEGORY_SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
    CATEGORY_CACHE_ENTRY = "CACHE_ENTRY"
    CATEGORY_SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
    CATEGORY_SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
    CATEGORY_ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
    CATEGORY_PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
    CATEGORY_USER_EXPORT = "USER_EXPORT"
    CATEGORY_MARKETING_PROFILE = "MARKETING_PROFILE"
    CATEGORY_EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
    CATEGORY_SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
    CATEGORY_LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
    CATEGORY_BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
    CATEGORY_ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
    CATEGORY_DATA_FLOW = "DATA_FLOW"

    ALL_CATEGORIES = {
        CATEGORY_USER_PROFILE,
        CATEGORY_SECONDARY_IDENTIFIER,
        CATEGORY_SEARCH_INDEX_ENTRY,
        CATEGORY_CACHE_ENTRY,
        CATEGORY_SUPPORT_MESSAGE,
        CATEGORY_SUPPORT_ATTACHMENT,
        CATEGORY_ANALYTICS_PROFILE,
        CATEGORY_PRODUCT_TELEMETRY,
        CATEGORY_USER_EXPORT,
        CATEGORY_MARKETING_PROFILE,
        CATEGORY_EMAIL_DELIVERY_LOG,
        CATEGORY_SECURITY_AUDIT_LOG,
        CATEGORY_LEGAL_HOLD_RECORD,
        CATEGORY_BACKUP_ARCHIVE,
        CATEGORY_ACCOUNT_TOMBSTONE,
    }

    VALID_RESOURCE_PREFIXES = (
        "account_",
        "analytics_",
        "audit_record_",
        "backup_",
        "data_flow_",
        "export_",
        "search_",
        "support_",
        "tombstone_",
    )

    CANONICAL_SYSTEMS = {
        "account_service",
        "analytics_platform",
        "backup_service",
        "export_service",
        "identity_service",
        "integration_service",
        "search_service",
        "security_service",
        "support_platform",
    }

    SYSTEM_NORMALIZATION = {
        "support_replica": "support_platform",
        "customer_account_failover_replica": "account_service",
        "customer_account_primary": "account_service",
        "account_failover_replica": "account_service",
        "analytics_service": "analytics_platform",
        "user_export_store": "export_service",
        "object_storage": "support_platform",
        "marketing_platform": "analytics_platform",
        "marketing_service": "analytics_platform",
    }

    @classmethod
    def normalize_system(cls, system_id: str) -> str:
        s_id = (system_id or "").strip()
        return cls.SYSTEM_NORMALIZATION.get(s_id, s_id)

    @classmethod
    def is_platform_resource(cls, resource_id: str) -> bool:
        if not resource_id:
            return False
        r_id = resource_id.lower()
        if r_id.startswith("user_") or r_id.startswith("ord-") or r_id.startswith("object_"):
            return False
        return any(r_id.startswith(p) for p in cls.VALID_RESOURCE_PREFIXES)

    @classmethod
    def classify(
        cls,
        system_id: str,
        resource_id: str,
        hint_category: Optional[str] = None,
        raw_record: Optional[dict] = None,
    ) -> str:
        """Classify a system and resource into an authoritative policy data category."""
        s_id = (system_id or "").lower()
        r_id = (resource_id or "").lower()

        # Marketing profile check (e.g. email_opt_in=true or marketing console)
        if raw_record and ("email_opt_in" in str(raw_record).lower() or "opt_in" in str(raw_record).lower()):
            return cls.CATEGORY_MARKETING_PROFILE

        if hint_category and hint_category in cls.ALL_CATEGORIES:
            if hint_category == cls.CATEGORY_USER_PROFILE and not (
                s_id in ("account_service", "profile_service", "identity_service") or r_id.startswith("account_")
            ):
                pass
            else:
                return hint_category

        # Tombstone
        if "tombstone" in r_id or (s_id == "identity_service" and "tombstone" in r_id):
            return cls.CATEGORY_ACCOUNT_TOMBSTONE

        # Backup / Restore
        if s_id == "backup_service" or r_id.startswith("backup_"):
            return cls.CATEGORY_BACKUP_ARCHIVE

        # Integration / Data Flow
        if s_id == "integration_service" or r_id.startswith("data_flow_"):
            return cls.CATEGORY_DATA_FLOW

        # Security Audit
        if s_id == "security_service" or r_id.startswith("audit_record_"):
            return cls.CATEGORY_SECURITY_AUDIT_LOG

        # Legal Hold
        if s_id == "legal_retention_service" or r_id.startswith("ord-") or "legal" in s_id:
            return cls.CATEGORY_LEGAL_HOLD_RECORD

        # User Export
        if s_id in ("export_service", "user_export_store") or r_id.startswith("export_"):
            return cls.CATEGORY_USER_EXPORT

        # Search Index
        if s_id == "search_service" or r_id.startswith("search_"):
            return cls.CATEGORY_SEARCH_INDEX_ENTRY

        # Support Attachment vs Support Message
        if s_id in ("support_platform", "support_replica", "object_storage") or r_id.startswith("support_") or r_id.startswith("object_"):
            # Check if this support resource is an attachment or subject-only index (.pdf, .png, contact index, attachment in id/raw_record)
            if "attachment" in r_id or r_id.startswith("object_") or (raw_record and "attachment" in str(raw_record).lower()):
                return cls.CATEGORY_SUPPORT_ATTACHMENT
            if raw_record and ("contact" in str(raw_record).lower() or "legacy_contact_index" in str(raw_record).lower()):
                return cls.CATEGORY_SUPPORT_ATTACHMENT
            if raw_record and raw_record.get("file_type") in ("image/png", "application/pdf"):
                return cls.CATEGORY_SUPPORT_ATTACHMENT
            return cls.CATEGORY_SUPPORT_MESSAGE

        # Analytics vs Telemetry
        if s_id in ("analytics_platform", "analytics_service") or r_id.startswith("analytics_"):
            if "telemetry" in r_id:
                return cls.CATEGORY_PRODUCT_TELEMETRY
            return cls.CATEGORY_ANALYTICS_PROFILE

        # Secondary Identifier
        if "secondary" in r_id or "alt_email" in r_id:
            return cls.CATEGORY_SECONDARY_IDENTIFIER

        # Cache Entry
        if "cache" in s_id or r_id.startswith("cache_") or "session" in r_id:
            return cls.CATEGORY_CACHE_ENTRY

        # Marketing Profile
        if "marketing" in s_id or r_id.startswith("mkt_"):
            return cls.CATEGORY_MARKETING_PROFILE

        # Email Delivery Log
        if "delivery" in s_id or "notification" in s_id:
            return cls.CATEGORY_EMAIL_DELIVERY_LOG

        # User Profile
        if s_id in ("account_service", "profile_service", "identity_service") or r_id.startswith("account_") or r_id.startswith("user_"):
            return cls.CATEGORY_USER_PROFILE

        return cls.CATEGORY_USER_PROFILE
