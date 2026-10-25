"""Ghost Data Still Exists - Policy Violation Detector.

Identifies confirmed compliance violations, residual records, exposed identifiers,
active synchronization pathways, and backup restore risks from reconstructed state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from code.policy.policy_evaluator import PolicyEvaluationResult, PolicyEvaluator
from code.policy.resource_classifier import ResourceClassifier
from code.reconstruction.models import ReconstructionReport, ResourceStateAssessment
from code.schemas import Operation, State


@dataclass
class PolicyViolation:
    """Typed policy violation backed by admissible evidence."""
    system_id: str
    resource_id: str
    operation: str  # REPORT_REMAINING_RECORD | REPORT_EXPOSED_PII | REPORT_ACTIVE_DATA_FLOW | REPORT_RESTORE_RISK
    state: str      # PRESENT | ACTIVE
    evidence_id: str
    data_category: str
    rule_id: str
    required_remediation_op: str
    required_remediation_state: str
    required_verification_op: str
    required_verification_state: str
    exposed_identifiers: List[str] = field(default_factory=list)
    description: str = ""


def select_best_evidence_id(evidence_ids: List[str]) -> str:
    """Prioritize evidence_XX file IDs. Fall back to activity_ or audit_ IDs if no file evidence."""
    if not evidence_ids:
        return ""
    for eid in evidence_ids:
        if eid and eid.startswith("evidence_"):
            return eid
    for eid in evidence_ids:
        if eid and (eid.startswith("activity_") or eid.startswith("audit_")):
            return eid
    return ""


class ViolationDetector:
    """Detects grounded policy violations across reconstructed resources and flows."""

    def __init__(self, evaluator: PolicyEvaluator) -> None:
        self.evaluator = evaluator

    def detect_violations(
        self,
        report: ReconstructionReport,
    ) -> List[PolicyViolation]:
        """Inspect all reconstructed resources and flows in the report to find confirmed violations."""
        violations: List[PolicyViolation] = []
        seen_violations: Set[Tuple[str, str, str]] = set()

        # 1. Active Data Flows -> REPORT_ACTIVE_DATA_FLOW (ACTIVE)
        for flow_id, fa in sorted(report.data_flow_assessments.items()):
            if fa.operational_state == "ACTIVE":
                ev_id = select_best_evidence_id(fa.activation_evidence_ids)
                if not ev_id:
                    continue
                v = PolicyViolation(
                    system_id="integration_service",
                    resource_id=flow_id,
                    operation=Operation.REPORT_ACTIVE_DATA_FLOW.value,
                    state=State.ACTIVE.value,
                    evidence_id=ev_id,
                    data_category=ResourceClassifier.CATEGORY_DATA_FLOW,
                    rule_id="data_flow_policy",
                    required_remediation_op=Operation.DISABLE_DATA_FLOW.value,
                    required_remediation_state=State.DISABLED.value,
                    required_verification_op=Operation.VERIFY_DATA_FLOW_DISABLED.value,
                    required_verification_state=State.DISABLED.value,
                    description=f"Active data flow {flow_id} can recreate deleted records.",
                )
                v_key = (v.system_id, v.resource_id, v.operation)
                if v_key not in seen_violations:
                    seen_violations.add(v_key)
                    violations.append(v)

        # 2. Backup Restore Risks -> REPORT_RESTORE_RISK (ACTIVE)
        for archive_id, ra in sorted(report.restore_risk_assessments.items()):
            if ra.operational_state == "ACTIVE":
                ev_id = select_best_evidence_id(ra.supporting_evidence_ids)
                if not ev_id:
                    continue
                v = PolicyViolation(
                    system_id="backup_service",
                    resource_id=archive_id,
                    operation=Operation.REPORT_RESTORE_RISK.value,
                    state=State.ACTIVE.value,
                    evidence_id=ev_id,
                    data_category=ResourceClassifier.CATEGORY_BACKUP_ARCHIVE,
                    rule_id="rule_14",
                    required_remediation_op=Operation.BLOCK_RESTORE.value,
                    required_remediation_state=State.BLOCKED.value,
                    required_verification_op=Operation.VERIFY_RESTORE_BLOCKED.value,
                    required_verification_state=State.BLOCKED.value,
                    description=f"Backup archive {archive_id} has unblocked restore capability.",
                )
                v_key = (v.system_id, v.resource_id, v.operation)
                if v_key not in seen_violations:
                    seen_violations.add(v_key)
                    violations.append(v)

        # 3. Residual Records and Exposed Identifiers
        canonical_assessments: Dict[Tuple[str, str], ResourceStateAssessment] = {}
        for entry_key, assess in sorted(report.resource_assessments.items()):
            sys_id = ResourceClassifier.normalize_system(assess.system_id)
            res_id = assess.resource_id

            # Filter non-platform resources (e.g. user_*, ORD-*, object_*)
            if not ResourceClassifier.is_platform_resource(res_id):
                continue

            # Skip non-canonical systems (e.g. data_catalog, privacy_intake, readback_collector)
            if sys_id not in ResourceClassifier.CANONICAL_SYSTEMS:
                continue

            # Skip integration and backup if already handled above
            if sys_id == "integration_service" or sys_id == "backup_service":
                continue

            c_key = (sys_id, res_id)
            if c_key not in canonical_assessments:
                canonical_assessments[c_key] = assess
            else:
                existing = canonical_assessments[c_key]
                if assess.state in ("PRESENT", "PRESENT_WITH_EXPOSED_PII") and existing.state not in ("PRESENT", "PRESENT_WITH_EXPOSED_PII"):
                    canonical_assessments[c_key] = assess
                elif assess.resource_category == "SUPPORT_ATTACHMENT":
                    canonical_assessments[c_key] = assess

        for (sys_id, res_id), assess in sorted(canonical_assessments.items()):
            # Classify category
            category = ResourceClassifier.classify(sys_id, res_id, hint_category=assess.resource_category)

            # Evaluate policy
            eval_res = self.evaluator.evaluate_resource(
                resource_id=res_id,
                system_id=sys_id,
                data_category=category,
                observed_state=assess.state,
                audit_at=report.audit_at,
                identifiers_found=assess.exposed_identifiers,
            )

            # If not compliant -> record violation
            if not eval_res.is_compliant:
                # Account tombstones are permitted by policy; catalog link activities alone do not prove PII exposure in the tombstone
                if category == ResourceClassifier.CATEGORY_ACCOUNT_TOMBSTONE and not any(e.startswith("evidence_") for e in assess.supporting_evidence_ids):
                    continue

                ev_id = select_best_evidence_id(assess.supporting_evidence_ids)
                if not ev_id:
                    continue

                if eval_res.required_action == "DELETE":
                    op = Operation.REPORT_REMAINING_RECORD.value
                    rem_op = Operation.DELETE_RECORD.value
                    rem_st = State.ABSENT.value
                    ver_op = Operation.VERIFY_RECORD_ABSENT.value
                    ver_st = State.ABSENT.value
                elif eval_res.required_action == "REDACT":
                    op = Operation.REPORT_EXPOSED_PII.value
                    rem_op = Operation.REDACT_RECORD.value
                    rem_st = State.REDACTED.value
                    ver_op = Operation.VERIFY_RECORD_REDACTED.value
                    ver_st = State.REDACTED.value
                elif eval_res.required_action == "ANONYMIZE":
                    op = Operation.REPORT_EXPOSED_PII.value
                    rem_op = Operation.ANONYMIZE_RECORD.value
                    rem_st = State.ANONYMIZED.value
                    ver_op = Operation.VERIFY_RECORD_ANONYMIZED.value
                    ver_st = State.ANONYMIZED.value
                else:
                    op = Operation.REPORT_REMAINING_RECORD.value
                    rem_op = Operation.DELETE_RECORD.value
                    rem_st = State.ABSENT.value
                    ver_op = Operation.VERIFY_RECORD_ABSENT.value
                    ver_st = State.ABSENT.value

                v = PolicyViolation(
                    system_id=sys_id,
                    resource_id=res_id,
                    operation=op,
                    state=State.PRESENT.value,
                    evidence_id=ev_id,
                    data_category=category,
                    rule_id=eval_res.rule_id,
                    required_remediation_op=rem_op,
                    required_remediation_state=rem_st,
                    required_verification_op=ver_op,
                    required_verification_state=ver_st,
                    exposed_identifiers=eval_res.exposed_identifiers,
                    description=eval_res.rationale,
                )
                v_key = (v.system_id, v.resource_id, v.operation)
                if v_key not in seen_violations:
                    seen_violations.add(v_key)
                    violations.append(v)

        return violations
