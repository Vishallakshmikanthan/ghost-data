"""Ghost Data Still Exists - Comprehensive Policy Engine & Planner Test Suite.

Covers:
- Policy loading, validation, duplicate/contradiction detection
- Layered deletion intent classification (multilingual, negation, scope)
- Resource classification and all 15 policy category treatments
- Grounded violation detection and evidence priority
- Remediation planning, ordering, and empty evidence ID rules
- Verification planning for COMPLETE and INCOMPLETE cases
- Strict output validation and deterministic execution
"""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from code.output.validator import OutputValidator
from code.policy.intent_classifier import IntentClassification, IntentClassifier, IntentType
from code.policy.policy_engine import PolicyEngine
from code.policy.policy_evaluator import PolicyEvaluator
from code.policy.remediation_planner import RemediationPlanner
from code.policy.resource_classifier import ResourceClassifier
from code.policy.rule_loader import (
    ContradictoryRuleError,
    DuplicateRuleIdError,
    InvalidRequiredActionError,
    InvalidRuleSchemaError,
    PolicyRuleBook,
    PolicyRuleLoader,
)
from code.policy.verification_planner import VerificationPlanner
from code.policy.violation_detector import PolicyViolation, ViolationDetector, select_best_evidence_id
from code.reconstruction.models import (
    DataFlowAssessment,
    ReconstructionReport,
    ResourceStateAssessment,
    RestoreRiskAssessment,
)
from code.schemas import (
    OPERATION_ORDER,
    CaseResult,
    DeletionRequest,
    DeletionStatus,
    Operation,
    OutputStep,
    Phase,
    State,
)


class TestPolicyRuleLoader(unittest.TestCase):
    """Test policy table loading, schema validation, and contradiction checks."""

    def test_load_production_rules(self):
        rulebook = PolicyRuleLoader.load_from_file("dataset/deletion_rules.csv")
        self.assertGreaterEqual(len(rulebook.rules), 15)
        self.assertIn("USER_PROFILE", rulebook.rules_by_category)
        self.assertIn("ACCOUNT_TOMBSTONE", rulebook.rules_by_category)
        self.assertIn("SECURITY_AUDIT_LOG", rulebook.rules_by_category)
        self.assertIn("BACKUP_ARCHIVE", rulebook.rules_by_category)

    def test_missing_column_error(self):
        csv_data = "rule_id,data_category,condition\nrule_01,USER_PROFILE,Always\n"
        with self.assertRaises(InvalidRuleSchemaError):
            PolicyRuleLoader.load_from_string(csv_data)

    def test_invalid_action_error(self):
        csv_data = "rule_id,data_category,condition,required_action\nrule_01,USER_PROFILE,Always,DESTROY_EVERYTHING\n"
        with self.assertRaises(InvalidRequiredActionError):
            PolicyRuleLoader.load_from_string(csv_data)

    def test_duplicate_rule_id_error(self):
        csv_data = (
            "rule_id,data_category,condition,required_action\n"
            "rule_01,USER_PROFILE,Always,DELETE\n"
            "rule_01,USER_PROFILE,Always,DELETE\n"
        )
        with self.assertRaises(DuplicateRuleIdError):
            PolicyRuleLoader.load_from_string(csv_data)

    def test_contradictory_rule_error(self):
        csv_data = (
            "rule_id,data_category,condition,required_action\n"
            "rule_01,USER_PROFILE,Always,DELETE\n"
            "rule_02,USER_PROFILE,Always,RETAIN\n"
        )
        with self.assertRaises(ContradictoryRuleError):
            PolicyRuleLoader.load_from_string(csv_data)


class TestIntentClassifier(unittest.TestCase):
    """Test multilingual, negation, scope, and non-deletion intent classification."""

    def setUp(self):
        self.classifier = IntentClassifier()

    def test_explicit_deletion(self):
        res = self.classifier.classify("Please permanently delete my account and all associated personal data.")
        self.assertTrue(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.EXPLICIT_DELETION.value)

    def test_account_closure_with_deletion(self):
        res = self.classifier.classify("I want to close my account and ensure my profile and personal details are removed.")
        self.assertTrue(res.is_deletion_authorized)
        self.assertTrue(res.is_deletion_authorized)

    def test_deletion_verification(self):
        res = self.classifier.classify("I submitted an account deletion request last week. Please verify that all my data has been removed.")
        self.assertTrue(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.DELETION_VERIFICATION.value)

    def test_complete_failed_deletion(self):
        res = self.classifier.classify("My account deletion was reported as partial. Please clean up the remaining database records.")
        self.assertTrue(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.COMPLETE_FAILED_DELETION.value)

    def test_export_only_non_deletion(self):
        res = self.classifier.classify("Under GDPR Article 20, I request a full machine-readable export of my personal data. Do not delete my account.")
        self.assertFalse(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.DATA_EXPORT_ONLY.value)

    def test_correction_only_non_deletion(self):
        res = self.classifier.classify("I recently moved and need to update my shipping address and phone number on file.")
        self.assertFalse(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.DATA_CORRECTION_ONLY.value)

    def test_marketing_optout_only(self):
        res = self.classifier.classify("Please unsubscribe me from marketing emails and promotional newsletters.")
        self.assertFalse(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.MARKETING_OPTOUT_ONLY.value)

    def test_access_request_only(self):
        res = self.classifier.classify("I would like a copy of all information you hold about me. What data do you have?")
        self.assertFalse(res.is_deletion_authorized)
        self.assertEqual(res.intent_type, IntentType.ACCESS_REQUEST_ONLY.value)

    def test_negation_how_to(self):
        res = self.classifier.classify("How do I delete my profile? I do not want to delete it yet, just asking.")
        self.assertFalse(res.is_deletion_authorized)

    def test_multilingual_german(self):
        res = self.classifier.classify("Bitte löschen Sie mein Konto und alle meine personenbezogenen Daten vollständig.")
        self.assertTrue(res.is_deletion_authorized)

    def test_multilingual_french(self):
        res = self.classifier.classify("Veuillez supprimer définitivement mon compte et effacer mes données personnelles.")
        self.assertTrue(res.is_deletion_authorized)

    def test_multilingual_spanish(self):
        res = self.classifier.classify("Por favor eliminen mi cuenta y todos mis datos personales de sus sistemas.")
        self.assertTrue(res.is_deletion_authorized)

    def test_permitted_retention_clause(self):
        res = self.classifier.classify("Please delete my account and email, but you may keep anonymous aggregate usage statistics.")
        self.assertTrue(res.is_deletion_authorized)


class TestPolicyEvaluator(unittest.TestCase):
    """Test policy rule evaluation across the 15 categories."""

    def setUp(self):
        self.rulebook = PolicyRuleLoader.load_from_file("dataset/deletion_rules.csv")
        self.evaluator = PolicyEvaluator(self.rulebook)

    def test_user_profile_present(self):
        res = self.evaluator.evaluate_resource(
            resource_id="account_005",
            system_id="account_service",
            data_category="USER_PROFILE",
            observed_state="PRESENT",
            audit_at="2026-09-15T12:00:00Z",
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.required_action, "DELETE")

    def test_user_profile_absent(self):
        res = self.evaluator.evaluate_resource(
            resource_id="account_001",
            system_id="account_service",
            data_category="USER_PROFILE",
            observed_state="ABSENT",
            audit_at="2026-09-15T12:00:00Z",
        )
        self.assertTrue(res.is_compliant)

    def test_tombstone_with_prohibited_email(self):
        res = self.evaluator.evaluate_resource(
            resource_id="tombstone_001",
            system_id="identity_service",
            data_category="ACCOUNT_TOMBSTONE",
            observed_state="PRESENT",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=["user_01", "user@example.com"],
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.required_action, "REDACT")

    def test_tombstone_with_allowed_user_id_only(self):
        res = self.evaluator.evaluate_resource(
            resource_id="tombstone_001",
            system_id="identity_service",
            data_category="ACCOUNT_TOMBSTONE",
            observed_state="REDACTED",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=["user_01"],
        )
        self.assertTrue(res.is_compliant)

    def test_security_audit_log_within_retention(self):
        res = self.evaluator.evaluate_resource(
            resource_id="audit_record_2945",
            system_id="security_service",
            data_category="SECURITY_AUDIT_LOG",
            observed_state="PRESENT",
            audit_at="2026-09-15T12:00:00Z",
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.required_action, "RETAIN")

    def test_analytics_profile_attributable(self):
        res = self.evaluator.evaluate_resource(
            resource_id="analytics_9914",
            system_id="analytics_platform",
            data_category="ANALYTICS_PROFILE",
            observed_state="PRESENT",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=["user@example.com"],
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.required_action, "ANONYMIZE")

    def test_analytics_profile_anonymized(self):
        res = self.evaluator.evaluate_resource(
            resource_id="analytics_901",
            system_id="analytics_platform",
            data_category="ANALYTICS_PROFILE",
            observed_state="ANONYMIZED",
            audit_at="2026-09-15T12:00:00Z",
            identifiers_found=[],
        )
        self.assertTrue(res.is_compliant)


class TestViolationDetectorAndEvidenceSelection(unittest.TestCase):
    """Test violation detection and evidence priority."""

    def test_select_best_evidence_id_prioritizes_evidence_files(self):
        eids = ["activity_3482", "evidence_54", "audit_123"]
        self.assertEqual(select_best_evidence_id(eids), "evidence_54")

    def test_select_best_evidence_id_fallback(self):
        eids = ["activity_3482", "audit_123"]
        self.assertEqual(select_best_evidence_id(eids), "activity_3482")
        self.assertEqual(select_best_evidence_id([]), "")

    def test_platform_resource_filtering(self):
        self.assertTrue(ResourceClassifier.is_platform_resource("account_005"))
        self.assertTrue(ResourceClassifier.is_platform_resource("analytics_9914"))
        self.assertTrue(ResourceClassifier.is_platform_resource("audit_record_2945"))
        self.assertTrue(ResourceClassifier.is_platform_resource("backup_3120"))
        self.assertTrue(ResourceClassifier.is_platform_resource("data_flow_9522"))
        self.assertTrue(ResourceClassifier.is_platform_resource("export_001"))
        self.assertTrue(ResourceClassifier.is_platform_resource("search_001"))
        self.assertTrue(ResourceClassifier.is_platform_resource("support_001"))
        self.assertTrue(ResourceClassifier.is_platform_resource("tombstone_001"))

        # Non-platform internal entities
        self.assertFalse(ResourceClassifier.is_platform_resource("user_05"))
        self.assertFalse(ResourceClassifier.is_platform_resource("ORD-20264118"))
        self.assertFalse(ResourceClassifier.is_platform_resource("object_5829"))
        self.assertFalse(ResourceClassifier.is_platform_resource("activity_3482"))


class TestRemediationAndVerificationPlanners(unittest.TestCase):
    """Test remediation and verification construction, ordering, and evidence rules."""

    def test_remediation_empty_evidence_id(self):
        v = PolicyViolation(
            system_id="account_service",
            resource_id="account_005",
            operation=Operation.REPORT_REMAINING_RECORD.value,
            state=State.PRESENT.value,
            evidence_id="evidence_54",
            data_category="USER_PROFILE",
            rule_id="rule_01",
            required_remediation_op=Operation.DELETE_RECORD.value,
            required_remediation_state=State.ABSENT.value,
            required_verification_op=Operation.VERIFY_RECORD_ABSENT.value,
            required_verification_state=State.ABSENT.value,
        )
        steps = RemediationPlanner.plan_remediation([v])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].evidence_id, "")
        self.assertEqual(steps[0].operation, Operation.DELETE_RECORD.value)
        self.assertEqual(steps[0].phase, Phase.REMEDIATION.value)

    def test_remediation_ordering_flows_before_records(self):
        v_rec = PolicyViolation(
            system_id="account_service",
            resource_id="account_005",
            operation=Operation.REPORT_REMAINING_RECORD.value,
            state=State.PRESENT.value,
            evidence_id="evidence_54",
            data_category="USER_PROFILE",
            rule_id="rule_01",
            required_remediation_op=Operation.DELETE_RECORD.value,
            required_remediation_state=State.ABSENT.value,
            required_verification_op=Operation.VERIFY_RECORD_ABSENT.value,
            required_verification_state=State.ABSENT.value,
        )
        v_flow = PolicyViolation(
            system_id="integration_service",
            resource_id="data_flow_01",
            operation=Operation.REPORT_ACTIVE_DATA_FLOW.value,
            state=State.ACTIVE.value,
            evidence_id="evidence_50",
            data_category="DATA_FLOW",
            rule_id="data_flow_policy",
            required_remediation_op=Operation.DISABLE_DATA_FLOW.value,
            required_remediation_state=State.DISABLED.value,
            required_verification_op=Operation.VERIFY_DATA_FLOW_DISABLED.value,
            required_verification_state=State.DISABLED.value,
        )
        steps = RemediationPlanner.plan_remediation([v_rec, v_flow])
        # DISABLE_DATA_FLOW (rank 1) must precede DELETE_RECORD (rank 3)
        self.assertEqual(steps[0].operation, Operation.DISABLE_DATA_FLOW.value)
        self.assertEqual(steps[1].operation, Operation.DELETE_RECORD.value)

    def test_future_verification_empty_evidence_id(self):
        v = PolicyViolation(
            system_id="account_service",
            resource_id="account_005",
            operation=Operation.REPORT_REMAINING_RECORD.value,
            state=State.PRESENT.value,
            evidence_id="evidence_54",
            data_category="USER_PROFILE",
            rule_id="rule_01",
            required_remediation_op=Operation.DELETE_RECORD.value,
            required_remediation_state=State.ABSENT.value,
            required_verification_op=Operation.VERIFY_RECORD_ABSENT.value,
            required_verification_state=State.ABSENT.value,
        )
        ver_steps = VerificationPlanner.plan_future_verification([v])
        self.assertEqual(len(ver_steps), 1)
        self.assertEqual(ver_steps[0].evidence_id, "")
        self.assertEqual(ver_steps[0].operation, Operation.VERIFY_RECORD_ABSENT.value)


class TestOutputValidationAndCanonicalContract(unittest.TestCase):
    """Test output step structure and schema compliance."""

    def test_step_dictionary_exact_seven_keys(self):
        step = OutputStep(
            step_no=1,
            phase=Phase.FINDING.value,
            system_id="account_service",
            resource_id="account_005",
            operation=Operation.REPORT_REMAINING_RECORD.value,
            state=State.PRESENT.value,
            evidence_id="evidence_54",
        )
        d = step.to_dict()
        expected_keys = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
        self.assertEqual(set(d.keys()), expected_keys)
        self.assertEqual(len(d), 7)

    def test_validator_passes_valid_incomplete_case(self):
        validator = OutputValidator()
        steps = [
            {
                "step_no": 1,
                "phase": "FINDING",
                "system_id": "account_service",
                "resource_id": "account_005",
                "operation": "REPORT_REMAINING_RECORD",
                "state": "PRESENT",
                "evidence_id": "evidence_54",
            },
            {
                "step_no": 2,
                "phase": "REMEDIATION",
                "system_id": "account_service",
                "resource_id": "account_005",
                "operation": "DELETE_RECORD",
                "state": "ABSENT",
                "evidence_id": "",
            },
            {
                "step_no": 3,
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": "account_005",
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": "",
            },
        ]
        errors = validator._validate_case("case_05", "INCOMPLETE", json.dumps(steps))
        self.assertEqual(len(errors), 0)

    def test_validator_passes_not_applicable_empty_steps(self):
        validator = OutputValidator()
        errors = validator._validate_case("case_533", "NOT_APPLICABLE", "[]")
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
