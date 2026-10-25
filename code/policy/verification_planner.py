"""Ghost Data Still Exists - Verification Planner.

Constructs post-remediation verification plans for INCOMPLETE cases (with empty evidence_ids)
and evidence-backed verification proofs for COMPLETE cases (with valid evidence_ids).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from code.policy.resource_classifier import ResourceClassifier
from code.policy.violation_detector import PolicyViolation, select_best_evidence_id
from code.reconstruction.models import (
    DataFlowAssessment,
    ReconstructionReport,
    ResourceStateAssessment,
    RestoreRiskAssessment,
)
from code.schemas import OPERATION_ORDER, Operation, OutputStep, Phase, State


class VerificationPlanner:
    """Plans verification checks for both INCOMPLETE and COMPLETE cases."""

    @classmethod
    def plan_future_verification(
        cls,
        violations: List[PolicyViolation],
        compliant_resources: Optional[List[Dict[str, str]]] = None,
    ) -> List[OutputStep]:
        """Construct verification checks for INCOMPLETE cases.
        
        All future verification steps use evidence_id="".
        """
        seen_checks: Set[tuple] = set()
        candidate_steps: List[OutputStep] = []

        # 1. Checks corresponding to remediated violations
        for v in violations:
            sys_id = ResourceClassifier.normalize_system(v.system_id)
            if not ResourceClassifier.is_platform_resource(v.resource_id):
                continue

            check_key = (sys_id, v.resource_id, v.required_verification_op)
            if check_key in seen_checks:
                continue
            seen_checks.add(check_key)

            step = OutputStep(
                step_no=0,
                phase=Phase.VERIFICATION.value,
                system_id=sys_id,
                resource_id=v.resource_id,
                operation=v.required_verification_op,
                state=v.required_verification_state,
                evidence_id="",  # Future verification must use empty evidence_id
            )
            candidate_steps.append(step)

        # 2. Checks for already-compliant resources (e.g. primary account absent, audit record present)
        if compliant_resources:
            for item in compliant_resources:
                sys_id = ResourceClassifier.normalize_system(item["system_id"])
                res_id = item["resource_id"]
                if not ResourceClassifier.is_platform_resource(res_id):
                    continue

                op = item["operation"]
                st = item["state"]
                check_key = (sys_id, res_id, op)
                if check_key in seen_checks:
                    continue
                seen_checks.add(check_key)

                step = OutputStep(
                    step_no=0,
                    phase=Phase.VERIFICATION.value,
                    system_id=sys_id,
                    resource_id=res_id,
                    operation=op,
                    state=st,
                    evidence_id="",  # In INCOMPLETE cases, even compliant checks use empty evidence_id
                )
                candidate_steps.append(step)

        # Canonical ordering within VERIFICATION:
        # 1. Operation rank (ABSENT: 1, PRESENT: 2, REDACTED: 3, ANONYMIZED: 4, FLOW_DISABLED: 5, RESTORE_BLOCKED: 6)
        # 2. System ID (alphabetical)
        # 3. Resource ID (alphabetical)
        candidate_steps.sort(
            key=lambda s: (
                OPERATION_ORDER.get(s.operation, 99),
                s.system_id,
                s.resource_id,
            )
        )

        return candidate_steps

    @classmethod
    def plan_complete_verification(
        cls,
        report: ReconstructionReport,
    ) -> List[OutputStep]:
        """Construct evidence-backed verification proofs for COMPLETE cases.
        
        Every step must cite participant-visible evidence establishing compliant state at audit_at.
        """
        seen_checks: Set[tuple] = set()
        candidate_steps: List[OutputStep] = []

        # 1. Resource assessments (accounts, audit logs, analytics, support, tombstones)
        for entry_key, assess in report.resource_assessments.items():
            sys_id = ResourceClassifier.normalize_system(assess.system_id)
            res_id = assess.resource_id
            state = assess.state

            if not ResourceClassifier.is_platform_resource(res_id):
                continue

            if res_id.startswith("backup_") or res_id.startswith("data_flow_"):
                continue

            ev_id = select_best_evidence_id(assess.supporting_evidence_ids)
            if not ev_id:
                continue

            op = None
            out_state = state

            if state == "ABSENT":
                op = Operation.VERIFY_RECORD_ABSENT.value
                out_state = State.ABSENT.value
            elif state == "PRESENT":
                # Compliant retention (e.g. SECURITY_AUDIT_LOG or LEGAL_HOLD)
                op = Operation.VERIFY_RECORD_PRESENT.value
                out_state = State.PRESENT.value
            elif state == "REDACTED":
                op = Operation.VERIFY_RECORD_REDACTED.value
                out_state = State.REDACTED.value
            elif state == "ANONYMIZED":
                op = Operation.VERIFY_RECORD_ANONYMIZED.value
                out_state = State.ANONYMIZED.value

            if op:
                check_key = (sys_id, res_id, op)
                if check_key not in seen_checks:
                    seen_checks.add(check_key)
                    step = OutputStep(
                        step_no=0,
                        phase=Phase.VERIFICATION.value,
                        system_id=sys_id,
                        resource_id=res_id,
                        operation=op,
                        state=out_state,
                        evidence_id=ev_id,
                    )
                    candidate_steps.append(step)

        # 2. Data flows
        for flow_id, fa in report.data_flow_assessments.items():
            if fa.operational_state == "DISABLED":
                ev_id = select_best_evidence_id(fa.activation_evidence_ids)
                if ev_id:
                    check_key = ("integration_service", flow_id, Operation.VERIFY_DATA_FLOW_DISABLED.value)
                    if check_key not in seen_checks:
                        seen_checks.add(check_key)
                        step = OutputStep(
                            step_no=0,
                            phase=Phase.VERIFICATION.value,
                            system_id="integration_service",
                            resource_id=flow_id,
                            operation=Operation.VERIFY_DATA_FLOW_DISABLED.value,
                            state=State.DISABLED.value,
                            evidence_id=ev_id,
                        )
                        candidate_steps.append(step)

        # 3. Restore risks
        for archive_id, ra in report.restore_risk_assessments.items():
            if ra.operational_state == "BLOCKED":
                ev_id = select_best_evidence_id(ra.supporting_evidence_ids)
                if ev_id:
                    check_key = ("backup_service", archive_id, Operation.VERIFY_RESTORE_BLOCKED.value)
                    if check_key not in seen_checks:
                        seen_checks.add(check_key)
                        step = OutputStep(
                            step_no=0,
                            phase=Phase.VERIFICATION.value,
                            system_id="backup_service",
                            resource_id=archive_id,
                            operation=Operation.VERIFY_RESTORE_BLOCKED.value,
                            state=State.BLOCKED.value,
                            evidence_id=ev_id,
                        )
                        candidate_steps.append(step)

        # Canonical ordering
        candidate_steps.sort(
            key=lambda s: (
                OPERATION_ORDER.get(s.operation, 99),
                s.system_id,
                s.resource_id,
            )
        )

        return candidate_steps
