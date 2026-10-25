"""Ghost Data Still Exists - Central Policy Decision Engine.

Orchestrates deletion-intent verification, temporal state reconstruction,
policy table rule evaluation, grounded violation detection, and canonical
remediation and verification planning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.identity.identity_resolver import IdentityResolver
from code.ingestion.csv_loader import DatasetBundle
from code.ingestion.evidence_store import EvidenceStore
from code.output.validator import OutputValidator, ValidationError
from code.policy.intent_classifier import IntentClassification, IntentClassifier
from code.policy.policy_evaluator import PolicyEvaluator
from code.policy.remediation_planner import RemediationPlanner
from code.policy.resource_classifier import ResourceClassifier
from code.policy.rule_loader import PolicyTable, load_policy_table
from code.policy.verification_planner import VerificationPlanner
from code.policy.violation_detector import PolicyViolation, ViolationDetector
from code.reconstruction.models import ReconstructionReport
from code.reconstruction.reconstruction_engine import ReconstructionEngine
from code.schemas import (
    OPERATION_ORDER,
    Operation,
    CaseResult,
    DeletionRequest,
    DeletionStatus,
    OutputStep,
    Phase,
    State,
)

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecisionReport:
    """Transparent diagnostic report explaining all policy decisions for a case."""
    case_id: str
    intent: IntentClassification
    status: str
    violations: List[PolicyViolation] = field(default_factory=list)
    remediation_steps: List[OutputStep] = field(default_factory=list)
    verification_steps: List[OutputStep] = field(default_factory=list)
    steps: List[OutputStep] = field(default_factory=list)
    rationale: str = ""
    evaluated_rules: List[str] = field(default_factory=list)
    tracked_resources: List[str] = field(default_factory=list)


class PolicyEngine:
    """Central policy engine orchestrating compliance decisions and planning."""

    def __init__(
        self,
        bundle: DatasetBundle,
        store: EvidenceStore,
        policy_table: Optional[PolicyTable] = None,
        rules_path: str = "dataset/deletion_rules.csv",
    ) -> None:
        self.bundle = bundle
        self.store = store
        if policy_table:
            self.policy_table = policy_table
        else:
            self.policy_table = load_policy_table(rules_path)

        self.intent_classifier = IntentClassifier()
        self.evaluator = PolicyEvaluator(self.policy_table)
        self.violation_detector = ViolationDetector(self.evaluator)
        self.reconstruction_engine = ReconstructionEngine(bundle, store)
        self.resolver = IdentityResolver(bundle, store)

    def evaluate_case(self, request: DeletionRequest) -> Tuple[CaseResult, PolicyDecisionReport]:
        """Evaluate a single deletion request deterministically and return typed result and report."""
        case_id = request.case_id.strip()

        # Step 1: Deletion Intent Gate
        intent = self.intent_classifier.classify(request.request_text)
        if not intent.is_deletion_authorized:
            empty_result = CaseResult(
                case_id=case_id,
                deletion_status=DeletionStatus.NOT_APPLICABLE.value,
                steps=[],
            )
            report = PolicyDecisionReport(
                case_id=case_id,
                intent=intent,
                status=DeletionStatus.NOT_APPLICABLE.value,
                violations=[],
                remediation_steps=[],
                verification_steps=[],
                steps=[],
                rationale=f"Request does not authorize deletion. Classified as {intent.intent_type}: {intent.rationale}",
            )
            return empty_result, report

        # Step 2: Temporal State Reconstruction
        recon_report = self.reconstruction_engine.reconstruct_case(request)

        # Step 3: Violation Detection
        violations = self.violation_detector.detect_violations(recon_report)

        # Collect tracked resources for reporting
        tracked_resources = list(recon_report.resource_assessments.keys())
        tracked_resources.extend(list(recon_report.data_flow_assessments.keys()))
        tracked_resources.extend(list(recon_report.restore_risk_assessments.keys()))

        # Collect evaluated rule IDs
        evaluated_rules = sorted(list({v.rule_id for v in violations}))

        # Step 4: Status Determination and Step Construction
        if violations:
            status = DeletionStatus.INCOMPLETE.value

            # Phase 1: Findings
            finding_steps: List[OutputStep] = []
            for v in violations:
                fs = OutputStep(
                    step_no=0,
                    phase=Phase.FINDING.value,
                    system_id=ResourceClassifier.normalize_system(v.system_id),
                    resource_id=v.resource_id,
                    operation=v.operation,
                    state=v.state,
                    evidence_id=v.evidence_id,
                )
                finding_steps.append(fs)

            # Canonical finding order: Operation rank -> system_id -> resource_id
            finding_steps.sort(
                key=lambda s: (
                    OPERATION_ORDER.get(s.operation, 99),
                    s.system_id,
                    s.resource_id,
                )
            )

            # Phase 2: Remediation
            remediation_steps = RemediationPlanner.plan_remediation(violations)

            # Phase 3: Verification
            # Also include already-compliant resources that must be verified (e.g. absent accounts, retained security audits)
            compliant_resources: List[Dict[str, str]] = []
            absent_accounts: List[Tuple[str, str, str]] = []
            for r_key, assess in recon_report.resource_assessments.items():
                res_id = assess.resource_id
                if not ResourceClassifier.is_platform_resource(res_id):
                    continue
                sys_id = ResourceClassifier.normalize_system(assess.system_id)
                if assess.state == "ABSENT" and res_id not in {v.resource_id for v in violations}:
                    if res_id.startswith("account_"):
                        latest_ts = ""
                        for ev_id in assess.supporting_evidence_ids:
                            doc = self.store.documents.get(ev_id)
                            if doc and doc.captured_at and doc.captured_at > latest_ts:
                                latest_ts = doc.captured_at
                        for ev_str in assess.event_chain:
                            if ev_str.startswith("[") and "]" in ev_str:
                                ts = ev_str[1:ev_str.index("]")]
                                if ts > latest_ts:
                                    latest_ts = ts
                        absent_accounts.append((latest_ts, sys_id, res_id))
                    else:
                        compliant_resources.append({
                            "system_id": sys_id,
                            "resource_id": res_id,
                            "operation": Operation.VERIFY_RECORD_ABSENT.value,
                            "state": State.ABSENT.value,
                        })
                elif assess.state == "PRESENT" and sys_id == "security_service":
                    compliant_resources.append({
                        "system_id": sys_id,
                        "resource_id": res_id,
                        "operation": Operation.VERIFY_RECORD_PRESENT.value,
                        "state": State.PRESENT.value,
                    })

            if absent_accounts:
                absent_accounts.sort(key=lambda x: x[0], reverse=True)
                _, best_sys, best_res = absent_accounts[0]
                compliant_resources.append({
                    "system_id": best_sys,
                    "resource_id": best_res,
                    "operation": Operation.VERIFY_RECORD_ABSENT.value,
                    "state": State.ABSENT.value,
                })

            verification_steps = VerificationPlanner.plan_future_verification(violations, compliant_resources)

            all_steps = finding_steps + remediation_steps + verification_steps
            rationale = f"Deletion is INCOMPLETE: {len(violations)} compliance violation(s) identified across {len(tracked_resources)} resource(s)."

        else:
            status = DeletionStatus.COMPLETE.value
            finding_steps = []
            remediation_steps = []
            verification_steps = VerificationPlanner.plan_complete_verification(recon_report)
            all_steps = verification_steps
            rationale = f"Deletion is COMPLETE: All {len(verification_steps)} verified resource(s) and flow(s) established compliant at audit cutoff."

        # Assign consecutive step numbers
        for idx, step in enumerate(all_steps, start=1):
            step.step_no = idx

        # Step 5: Strict Output Validation
        validator = OutputValidator()
        steps_json_str = json.dumps([s.to_dict() for s in all_steps])
        val_errors = validator._validate_case(case_id=case_id, deletion_status=status, steps_json_str=steps_json_str)
        if val_errors:
            error_msgs = "; ".join(str(e) for e in val_errors)
            logger.warning("Step validation warnings for %s: %s", case_id, error_msgs)

        result = CaseResult(
            case_id=case_id,
            deletion_status=status,
            steps=all_steps,
        )

        decision_report = PolicyDecisionReport(
            case_id=case_id,
            intent=intent,
            status=status,
            violations=violations,
            remediation_steps=remediation_steps,
            verification_steps=verification_steps,
            steps=all_steps,
            rationale=rationale,
            evaluated_rules=evaluated_rules,
            tracked_resources=tracked_resources,
        )

        return result, decision_report

    def evaluate_batch(
        self,
        requests: List[DeletionRequest],
    ) -> List[Tuple[CaseResult, PolicyDecisionReport]]:
        """Evaluate a list of deletion requests deterministically."""
        results: List[Tuple[CaseResult, PolicyDecisionReport]] = []
        for req in requests:
            results.append(self.evaluate_case(req))
        return results
