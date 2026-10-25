"""Unit tests for shared schemas and data contracts."""

import json
import unittest

from code.schemas import (
    OPERATION_ORDER,
    OPERATION_STATE,
    PHASE_OPERATIONS,
    PHASE_ORDER,
    REQUIRED_STEP_KEYS,
    CandidateFinding,
    CaseResult,
    DeletionRequest,
    DeletionStatus,
    EvidenceClaim,
    EvidenceDocument,
    Operation,
    OutputStep,
    Phase,
    RemediationAction,
    RunMetrics,
    State,
    SubjectIdentityGraph,
    VerificationCheck,
)


class TestSchemas(unittest.TestCase):
    """Test schemas, enums, dataclasses, and canonical ordering."""

    def test_deletion_status_values(self):
        expected = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
        self.assertEqual(DeletionStatus.values(), expected)

    def test_phase_values(self):
        expected = {"FINDING", "REMEDIATION", "VERIFICATION"}
        self.assertEqual(Phase.values(), expected)

    def test_operation_and_state_registry_integrity(self):
        """Ensure all operations have a defined phase and state."""
        all_ops = Operation.values()
        # All operations mapped to a state
        for op in all_ops:
            self.assertIn(op, OPERATION_STATE, f"Operation {op} missing from OPERATION_STATE")
            self.assertIn(OPERATION_STATE[op], State.values())

        # All operations mapped to a phase
        phase_mapped_ops = set()
        for p, ops in PHASE_OPERATIONS.items():
            self.assertIn(p, Phase.values())
            for op in ops:
                self.assertIn(op, all_ops)
                phase_mapped_ops.add(op)
        self.assertEqual(phase_mapped_ops, all_ops)

    def test_phase_and_operation_order(self):
        """Ensure phase and operation orderings have unique ranks per phase."""
        self.assertEqual(PHASE_ORDER[Phase.FINDING.value], 1)
        self.assertEqual(PHASE_ORDER[Phase.REMEDIATION.value], 2)
        self.assertEqual(PHASE_ORDER[Phase.VERIFICATION.value], 3)

        for phase, ops in PHASE_OPERATIONS.items():
            ranks = [OPERATION_ORDER[op] for op in ops]
            self.assertEqual(len(ranks), len(set(ranks)), f"Duplicate op ranks in phase {phase}")

    def test_output_step_exact_keys(self):
        step_dict = {
            "step_no": 1,
            "phase": "FINDING",
            "system_id": "account_service",
            "resource_id": "account_101",
            "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT",
            "evidence_id": "evidence_01",
        }
        step = OutputStep.from_dict(step_dict)
        self.assertEqual(step.to_dict(), step_dict)
        self.assertEqual(tuple(step.to_dict().keys()), REQUIRED_STEP_KEYS)

    def test_output_step_missing_key_raises(self):
        step_dict = {
            "step_no": 1,
            "phase": "FINDING",
            "system_id": "account_service",
            "resource_id": "account_101",
            # missing operation, state, evidence_id
        }
        with self.assertRaises(ValueError):
            OutputStep.from_dict(step_dict)

    def test_output_step_extra_key_raises(self):
        step_dict = {
            "step_no": 1,
            "phase": "FINDING",
            "system_id": "account_service",
            "resource_id": "account_101",
            "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT",
            "evidence_id": "evidence_01",
            "extra_field": "disallowed",
        }
        with self.assertRaises(ValueError):
            OutputStep.from_dict(step_dict)

    def test_case_result_sorting_and_renumbering(self):
        # Steps out of order
        s_verif = OutputStep(
            step_no=1,
            phase="VERIFICATION",
            system_id="account_service",
            resource_id="account_101",
            operation="VERIFY_RECORD_ABSENT",
            state="ABSENT",
            evidence_id="",
        )
        s_finding = OutputStep(
            step_no=2,
            phase="FINDING",
            system_id="search_service",
            resource_id="search_202",
            operation="REPORT_REMAINING_RECORD",
            state="PRESENT",
            evidence_id="evidence_05",
        )
        s_remed = OutputStep(
            step_no=3,
            phase="REMEDIATION",
            system_id="search_service",
            resource_id="search_202",
            operation="DELETE_RECORD",
            state="ABSENT",
            evidence_id="",
        )

        cr = CaseResult(
            case_id="case_999",
            deletion_status=DeletionStatus.INCOMPLETE.value,
            steps=[s_verif, s_finding, s_remed],
        )
        cr.sort_and_renumber_steps()

        # Should be FINDING (1) -> REMEDIATION (2) -> VERIFICATION (3)
        self.assertEqual(cr.steps[0].phase, "FINDING")
        self.assertEqual(cr.steps[0].step_no, 1)
        self.assertEqual(cr.steps[1].phase, "REMEDIATION")
        self.assertEqual(cr.steps[1].step_no, 2)
        self.assertEqual(cr.steps[2].phase, "VERIFICATION")
        self.assertEqual(cr.steps[2].step_no, 3)

    def test_not_applicable_csv_row(self):
        cr = CaseResult(
            case_id="case_000",
            deletion_status=DeletionStatus.NOT_APPLICABLE.value,
            steps=[],
        )
        cid, status, steps_json = cr.to_csv_row()
        self.assertEqual(cid, "case_000")
        self.assertEqual(status, "NOT_APPLICABLE")
        self.assertEqual(steps_json, "[]")

    def test_subject_identity_graph_resolution(self):
        graph = SubjectIdentityGraph(
            user_id="user_123",
            primary_email="john.doe@example.com",
            emails={"old.john@example.com"},
            names={"John Doe"},
            phones={"+1 555-0199"},
            email_hashes={"hash_abc123"},
            tombstone_ids={"tombstone_99"},
        )
        all_ids = graph.all_identifiers()
        self.assertIn("user_123", all_ids)
        self.assertIn("john.doe@example.com", all_ids)
        self.assertIn("old.john@example.com", all_ids)
        self.assertIn("hash_abc123", all_ids)
        self.assertIn("tombstone_99", all_ids)


if __name__ == "__main__":
    unittest.main()
