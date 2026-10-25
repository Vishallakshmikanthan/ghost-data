"""Ghost Data Still Exists - Policy Condition & Treatment Evaluator.

Evaluates rule conditions against reconstructed resource states and determines
whether each resource satisfies the supplied policy table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from code.ingestion.normalization import is_at_or_before
from code.policy.rule_loader import DeletionRuleRecord, PolicyTable


@dataclass
class PolicyEvaluationResult:
    """Evaluation result for a single resource against applicable policy."""
    resource_id: str
    system_id: str
    data_category: str
    rule_id: str
    condition: str
    required_action: str
    required_state: str
    condition_satisfied: bool
    is_compliant: bool
    observed_state: str
    exposed_identifiers: List[str] = field(default_factory=list)
    rationale: str = ""


class PolicyEvaluator:
    """Evaluates policy rules and conditions against reconstructed resources."""

    def __init__(self, policy_table: PolicyTable) -> None:
        self.policy_table = policy_table

    def evaluate_resource(
        self,
        resource_id: str,
        system_id: str,
        data_category: str,
        observed_state: str,
        audit_at: str,
        identifiers_found: Optional[List[str]] = None,
        raw_record: Optional[Dict[str, Any]] = None,
        retention_until: Optional[str] = None,
        is_legal_hold_active: bool = False,
    ) -> PolicyEvaluationResult:
        """Evaluate a resource against the policy table rule for its category."""
        rule = self.policy_table.get_rule_for_category(data_category)
        if not rule:
            # Fallback if category is DATA_FLOW or unmapped
            if data_category == "DATA_FLOW" or system_id == "integration_service":
                is_comp = (observed_state == "DISABLED")
                return PolicyEvaluationResult(
                    resource_id=resource_id,
                    system_id=system_id,
                    data_category="DATA_FLOW",
                    rule_id="data_flow_policy",
                    condition="Active data flow must be disabled",
                    required_action="DISABLE",
                    required_state="DISABLED",
                    condition_satisfied=True,
                    is_compliant=is_comp,
                    observed_state=observed_state,
                    rationale="Data flow must be disabled to prevent recreation.",
                )
            raise ValueError(f"No policy rule defined for category {data_category}")

        condition = rule.condition
        req_action = rule.required_action.upper()
        raw = raw_record or {}
        idents = identifiers_found or []

        # Map required_action to canonical required_state
        req_state_map = {
            "DELETE": "ABSENT",
            "REDACT": "REDACTED",
            "ANONYMIZE": "ANONYMIZED",
            "RETAIN": "PRESENT",
        }
        required_state = req_state_map.get(req_action, "ABSENT")

        condition_satisfied = True
        is_compliant = False
        exposed_idents: List[str] = []
        rationale = ""

        # Evaluate by Category & Condition
        if data_category in ("USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "USER_EXPORT", "MARKETING_PROFILE"):
            # Condition: Always -> DELETE -> ABSENT
            condition_satisfied = True
            is_compliant = (observed_state == "ABSENT")
            if not is_compliant:
                rationale = f"{data_category} {resource_id} is still {observed_state}; policy requires deletion."

        elif data_category == "SUPPORT_ATTACHMENT":
            # Condition: Attachment belongs only to the deletion subject -> DELETE -> ABSENT
            condition_satisfied = True
            is_compliant = (observed_state == "ABSENT")
            if not is_compliant:
                rationale = f"Subject-only support attachment {resource_id} is still {observed_state}; policy requires deletion."

        elif data_category == "SUPPORT_MESSAGE":
            # Condition: Conversation contains shared context -> REDACT -> REDACTED
            # Must remove email, phone, name, but can keep subject_id or order context
            condition_satisfied = True
            # Check if direct PII is exposed
            has_direct_pii = any(
                ("@" in i or i.startswith("+") or (not i.startswith("user_") and len(i.split()) >= 2))
                for i in idents
            )
            if observed_state == "PRESENT" or has_direct_pii:
                is_compliant = False
                exposed_idents = idents
                rationale = f"Support message {resource_id} retains exposed personal identifiers {idents}."
            elif observed_state == "REDACTED":
                is_compliant = True
                rationale = f"Support message {resource_id} is properly redacted."
            else:
                is_compliant = (observed_state == "ABSENT")

        elif data_category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
            # Condition: Record is still attributable to a user -> ANONYMIZE -> ANONYMIZED
            condition_satisfied = True
            if observed_state == "PRESENT" or len(idents) > 0:
                is_compliant = False
                exposed_idents = idents
                rationale = f"Analytics profile {resource_id} remains attributable to subject with identifiers {idents}."
            elif observed_state == "ANONYMIZED":
                is_compliant = True
                rationale = f"Analytics profile {resource_id} has been anonymized."
            else:
                is_compliant = (observed_state == "ABSENT")

        elif data_category == "EMAIL_DELIVERY_LOG":
            # Condition: Log contains a user email address -> REDACT -> REDACTED
            has_email = any("@" in i for i in idents)
            condition_satisfied = has_email
            if has_email:
                is_compliant = (observed_state == "REDACTED" or observed_state == "ABSENT")
                if not is_compliant:
                    exposed_idents = [i for i in idents if "@" in i]
                    rationale = f"Email delivery log {resource_id} contains unredacted email {exposed_idents}."
            else:
                is_compliant = True

        elif data_category == "SECURITY_AUDIT_LOG":
            # Condition: Record is within the supplied retention period -> RETAIN -> PRESENT
            ret_ts = retention_until or raw.get("retention_until")
            if ret_ts:
                # If retention_until is at or after audit_at, retention is mandatory
                is_within_retention = not is_at_or_before(ret_ts, audit_at)
                condition_satisfied = is_within_retention
                if is_within_retention:
                    is_compliant = (observed_state == "PRESENT")
                    rationale = f"Security audit log {resource_id} retained until {ret_ts} within retention period."
                else:
                    # Retention expired
                    is_compliant = (observed_state == "ABSENT")
            else:
                # Default retain if present
                condition_satisfied = True
                is_compliant = (observed_state == "PRESENT")
                rationale = f"Security audit log {resource_id} retained per policy."

        elif data_category == "LEGAL_HOLD_RECORD":
            # Condition: A supplied legal hold is active -> RETAIN -> PRESENT
            condition_satisfied = is_legal_hold_active or raw.get("hold_status") == "ACTIVE"
            if condition_satisfied:
                is_compliant = (observed_state == "PRESENT")
                rationale = f"Legal hold record {resource_id} must be retained under active legal hold."
            else:
                is_compliant = (observed_state == "ABSENT")

        elif data_category == "BACKUP_ARCHIVE":
            # Condition: Restore access can be blocked -> RETAIN -> BLOCKED (for restore)
            condition_satisfied = True
            is_compliant = (observed_state == "BLOCKED")
            required_state = "BLOCKED"
            if not is_compliant:
                rationale = f"Backup archive {resource_id} has active restore access (not blocked)."

        elif data_category == "ACCOUNT_TOMBSTONE":
            # Condition: Account is deleted and only a stable internal user_id is needed -> REDACT -> REDACTED
            condition_satisfied = True
            # Tombstone must only preserve stable internal user_id; prohibited direct PII must be absent
            prohibited_pii = [
                i for i in idents
                if "@" in i or i.startswith("+") or (not i.startswith("user_") and not i.startswith("tombstone_"))
            ]
            if observed_state == "PRESENT" or prohibited_pii:
                is_compliant = False
                exposed_idents = prohibited_pii
                rationale = f"Account tombstone {resource_id} exposes prohibited direct identifiers {prohibited_pii}."
            elif observed_state in ("REDACTED", "ABSENT"):
                is_compliant = True
                rationale = f"Account tombstone {resource_id} properly preserves only stable reference."

        return PolicyEvaluationResult(
            resource_id=resource_id,
            system_id=system_id,
            data_category=data_category,
            rule_id=rule.rule_id,
            condition=condition,
            required_action=req_action,
            required_state=required_state,
            condition_satisfied=condition_satisfied,
            is_compliant=is_compliant,
            observed_state=observed_state,
            exposed_identifiers=exposed_idents,
            rationale=rationale,
        )
