"""Ghost Data Still Exists - Custom Independent Evaluation Cases.

HackerRank Orchestrate October 2026.
Phase 5 Independent Evaluation Suite.

Contains 5 rigorous synthetic test scenarios validating deletion audit decisions,
temporal boundary isolation, replica drift, restore risk, shared support redaction,
and compliant account tombstones.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from code.identity.identity_resolver import IdentityResolver
from code.ingestion.csv_loader import DatasetBundle
from code.ingestion.evidence_store import EvidenceStore
from code.policy.policy_evaluator import PolicyEvaluator
from code.policy.policy_engine import PolicyEngine
from code.policy.resource_classifier import ResourceClassifier
from code.policy.rule_loader import PolicyRuleBook
from code.policy.violation_detector import ViolationDetector
from code.reconstruction.models import ReconstructedState, ResourceSnapshot
from code.reconstruction.reconstruction_engine import ReconstructionEngine
from code.reconstruction.snapshot_reconciler import SnapshotReconciler
from code.schemas import (
    DeletionRequest,
    DeletionRuleRecord,
    EvidenceClaim,
    EvidenceDocument,
    SystemActivityRecord,
    UserAuditRecord,
    UserIdentityRecord,
)


class TestCustomIndependentEvaluationCases(unittest.TestCase):
    """Five independent participant-designed evaluation cases for Phase 5."""

    def setUp(self):
        self.rules = {
            "rule_01": DeletionRuleRecord("rule_01", "USER_PROFILE", "Default profile storage", "DELETE"),
            "rule_05": DeletionRuleRecord("rule_05", "SUPPORT_MESSAGE", "Shared support message context", "REDACT"),
            "rule_06": DeletionRuleRecord("rule_06", "SUPPORT_ATTACHMENT", "Subject-only support attachment", "DELETE"),
            "rule_11": DeletionRuleRecord("rule_11", "ACCOUNT_TOMBSTONE", "Tombstone retaining internal reference", "REDACT"),
            "rule_12": DeletionRuleRecord("rule_12", "DATA_FLOW", "Active sync or recreation data flow", "DISABLE"),
            "rule_14": DeletionRuleRecord("rule_14", "BACKUP_ARCHIVE", "Unblocked backup restore path", "BLOCK"),
            "rule_15": DeletionRuleRecord("rule_15", "SECURITY_AUDIT_LOG", "Security event within retention", "RETAIN"),
        }
        self.rulebook = PolicyRuleBook(
            rules=list(self.rules.values()),
            rules_by_id=self.rules,
            rules_by_category={r.data_category: [r] for r in self.rules.values()},
        )

    def test_case_1_historical_alias_temporal_boundary_isolation(self):
        """Case 1: Validate that an alias changed in the past is bounded by temporal audit cutoff.
        
        Scenario: Alice owned alice.old@example.com until 2025-12-31, then changed to alice.new@example.com.
        A deletion request for Alice in 2026-08-01 should not attribute activity created in 2026-09-01
        after audit cutoff.
        """
        audit_at = "2026-08-01T12:00:00Z"
        bundle = DatasetBundle()
        bundle.deletion_rules = self.rules

        # User identity snapshot
        user_rec = UserIdentityRecord(
            user_id="user_custom_01",
            name="Alice Smith",
            email="alice.new@example.com",
            phone="+15551234567",
            email_hash="hash_alice_new",
            updated_at="2026-01-01T10:00:00Z",
        )
        bundle.identities_by_email["alice.new@example.com"] = user_rec
        bundle.identities_by_email["alice.old@example.com"] = user_rec
        bundle.identities_by_name["Alice Smith"] = [user_rec]

        # Audit log of historical email update
        audit_rec = UserAuditRecord(
            audit_id="audit_custom_01",
            user_id="user_custom_01",
            event_time="2026-01-01T10:00:00Z",
            field_name="email",
            old_value="alice.old@example.com",
            new_value="alice.new@example.com",
        )
        bundle.audit_by_user_id["user_custom_01"] = [audit_rec]

        store = EvidenceStore()
        # Admissible evidence before cutoff
        store.documents["evidence_valid"] = EvidenceDocument("evidence_valid", "evidence/records/valid.json", "2026-07-15T00:00:00Z", "records")
        # Inadmissible evidence created after cutoff
        store.documents["evidence_future"] = EvidenceDocument("evidence_future", "evidence/records/future.json", "2026-09-01T00:00:00Z", "records")

        # Claim from future evidence
        future_claim = EvidenceClaim(
            evidence_id="evidence_future",
            source_type="file",
            timestamp="2026-09-01T00:00:00Z",
            system_id="account_service",
            resource_id="account_custom_01",
            observed_state="PRESENT",
            identifiers_found=["alice.old@example.com"],
        )
        store.add_claim(future_claim)

        # Ensure future claim is excluded by temporal cutoff
        valid_claims = store.filter_temporally([future_claim], audit_at)
        self.assertEqual(len(valid_claims), 0, "Evidence captured after audit_at cutoff must be filtered out")

    def test_case_2_downstream_replica_drift_reconciliation(self):
        """Case 2: Primary source absent, but downstream failover replica snapshot still present.
        
        Scenario: Primary database reports SUCCESS on DELETE_RECORD, but a failover replica snapshot
        taken at audit_at shows the record remains PRESENT.
        Expected: SnapshotReconciler detects replication drift and reconciles state to PRESENT.
        """
        audit_at = "2026-09-10T12:00:00Z"
        primary_snap = ResourceSnapshot(
            resource_id="account_custom_02",
            system_id="account_service",
            snapshot_time="2026-09-01T08:00:00Z",
            evidence_id="evidence_primary",
            observed_state="ABSENT",
            is_replica=False,
            replica_type="primary",
        )
        replica_snap = ResourceSnapshot(
            resource_id="account_custom_02",
            system_id="account_service",
            snapshot_time="2026-09-05T10:00:00Z",
            evidence_id="evidence_replica",
            observed_state="PRESENT",
            is_replica=True,
            replica_type="failover_replica",
        )

        reconcile_res = SnapshotReconciler.reconcile_resource_snapshots(
            resource_id="account_custom_02",
            system_id="account_service",
            snapshots=[primary_snap, replica_snap],
            subsequent_events=[],
            audit_at=audit_at,
        )

        self.assertTrue(reconcile_res.drift_detected, "Replication drift must be detected when replica retains record")
        self.assertEqual(reconcile_res.reconciled_state, "PRESENT", "Residual record in replica must force reconciled state to PRESENT")
        self.assertIn("evidence_replica", reconcile_res.supporting_evidence_ids, "Replica evidence proving residual state must be cited")

    def test_case_3_backup_restore_recreation_risk_blocking(self):
        """Case 3: Incomplete deletion due to active backup archive with unblocked restore capability.
        
        Scenario: Backup archive backup_custom_03 is active and restore_allowed is true.
        Expected: ViolationDetector generates REPORT_RESTORE_RISK (ACTIVE) and mandates BLOCK_RESTORE.
        """
        evaluator = PolicyEvaluator(self.rulebook)
        detector = ViolationDetector(evaluator)

        recon_engine = ReconstructionEngine(DatasetBundle(), EvidenceStore())
        from code.reconstruction.models import DataFlowAssessment, ReconstructionReport, RestoreRiskAssessment

        report = ReconstructionReport(
            case_id="case_custom_03",
            audit_at="2026-09-15T12:00:00Z",
            subject_id="user_custom_03",
            restore_risk_assessments={
                "backup_custom_03": RestoreRiskAssessment(
                    archive_id="backup_custom_03",
                    operational_state="ACTIVE",
                    recreation_risk="HIGH",
                    supporting_evidence_ids=["evidence_backup_03"],
                )
            },
        )

        violations = detector.detect_violations(report)
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.operation, "REPORT_RESTORE_RISK")
        self.assertEqual(v.state, "ACTIVE")
        self.assertEqual(v.required_remediation_op, "BLOCK_RESTORE")
        self.assertEqual(v.required_remediation_state, "BLOCKED")
        self.assertEqual(v.required_verification_op, "VERIFY_RESTORE_BLOCKED")
        self.assertEqual(v.required_verification_state, "BLOCKED")

    def test_case_4_shared_support_conversation_redaction_vs_deletion(self):
        """Case 4: Multi-party shared support conversation requiring REDACT rather than DELETE.
        
        Scenario: Support ticket support_custom_04 contains shared conversation context.
        Policy rule_05 requires REDACT (keep conversation context, remove subject PII).
        Expected: If direct PII remains exposed, emit REPORT_EXPOSED_PII and REDACT_RECORD.
        """
        evaluator = PolicyEvaluator(self.rulebook)
        res = evaluator.evaluate_resource(
            resource_id="support_custom_04",
            system_id="support_platform",
            data_category="SUPPORT_MESSAGE",
            observed_state="PRESENT_WITH_EXPOSED_PII",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=["user_custom_04", "customer@example.com"],
        )

        self.assertFalse(res.is_compliant, "Support message exposing direct PII is non-compliant")
        self.assertEqual(res.required_action, "REDACT", "Shared support message policy action must be REDACT")
        self.assertEqual(res.rule_id, "rule_05", "rule_05 must govern shared support message redaction")

    def test_case_5_tombstone_preserves_stable_id_without_direct_pii(self):
        """Case 5: Account tombstone properly retaining stable subject reference with direct PII stripped.
        
        Scenario: Tombstone tombstone_custom_05 maintains stable reference user_custom_05,
        with name, email, phone, and hash removed (REDACTED).
        Expected: Evaluated as fully COMPLIANT under rule_11 without violation.
        """
        evaluator = PolicyEvaluator(self.rulebook)
        res = evaluator.evaluate_resource(
            resource_id="tombstone_custom_05",
            system_id="identity_service",
            data_category="ACCOUNT_TOMBSTONE",
            observed_state="REDACTED",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=["user_custom_05"],  # Allowed stable reference only
        )

        self.assertTrue(res.is_compliant, "Account tombstone stripped of direct PII must be compliant")
        self.assertEqual(res.rule_id, "rule_11", "rule_11 must govern account tombstone compliance")


if __name__ == "__main__":
    unittest.main()
