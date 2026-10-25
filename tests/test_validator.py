"""Unit tests for the strict output contract validator."""

import json
import unittest

from code.output.validator import OutputValidator


class TestOutputValidator(unittest.TestCase):
    """Test suite for OutputValidator covering all required edge cases and contract rules."""

    def setUp(self):
        self.validator = OutputValidator()

    def test_valid_not_applicable_result(self):
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            "case_01,NOT_APPLICABLE,[]\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)

    def test_invalid_not_applicable_with_steps(self):
        step = {
            "step_no": 1,
            "phase": "VERIFICATION",
            "system_id": "account_service",
            "resource_id": "account_01",
            "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT",
            "evidence_id": "evidence_01",
        }
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,NOT_APPLICABLE,\"{json.dumps([step]).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_NOT_APPLICABLE_NONEMPTY", rule_codes)

    def test_valid_complete_result(self):
        steps = [
            {
                "step_no": 1,
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": "account_001",
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": "evidence_54",
            }
        ]
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)

    def test_complete_result_with_empty_evidence_fails(self):
        steps = [
            {
                "step_no": 1,
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": "account_001",
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": "",
            }
        ]
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_COMPLETE_EMPTY_EVIDENCE", rule_codes)

    def test_complete_result_with_finding_fails(self):
        steps = [
            {
                "step_no": 1,
                "phase": "FINDING",
                "system_id": "account_service",
                "resource_id": "account_001",
                "operation": "REPORT_REMAINING_RECORD",
                "state": "PRESENT",
                "evidence_id": "evidence_54",
            }
        ]
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_COMPLETE_NON_VERIFICATION", rule_codes)

    def test_valid_incomplete_result(self):
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
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_05,INCOMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)

    def test_invalid_json(self):
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            "case_01,COMPLETE,\"{invalid_json: 123]\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_INVALID_JSON", rule_codes)

    def test_missing_required_keys(self):
        step_missing_evidence = {
            "step_no": 1,
            "phase": "VERIFICATION",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT",
            # missing "evidence_id"
        }
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps([step_missing_evidence]).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_MISSING_STEP_KEY", rule_codes)

    def test_invalid_operation_state_combination(self):
        step_invalid_state = {
            "step_no": 1,
            "phase": "VERIFICATION",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "VERIFY_RECORD_ABSENT",
            "state": "PRESENT",  # Invalid! Must be ABSENT for VERIFY_RECORD_ABSENT
            "evidence_id": "evidence_54",
        }
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps([step_invalid_state]).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_INVALID_STATE", rule_codes)

    def test_duplicate_case_ids(self):
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            "case_01,NOT_APPLICABLE,[]\n"
            "case_01,NOT_APPLICABLE,[]\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_DUPLICATE_CASE_ID", rule_codes)

    def test_missing_expected_case_ids(self):
        validator = OutputValidator(expected_case_ids=["case_01", "case_02"])
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            "case_01,NOT_APPLICABLE,[]\n"
        )
        res = validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_MISSING_CASES", rule_codes)

    def test_incorrect_step_numbering(self):
        steps = [
            {
                "step_no": 2,  # Starts at 2 instead of 1
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": "account_001",
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": "evidence_54",
            }
        ]
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_STEP_NUMBERING", rule_codes)

    def test_incorrect_phase_ordering(self):
        steps = [
            {
                "step_no": 1,
                "phase": "REMEDIATION",  # REMEDIATION before FINDING
                "system_id": "account_service",
                "resource_id": "account_005",
                "operation": "DELETE_RECORD",
                "state": "ABSENT",
                "evidence_id": "",
            },
            {
                "step_no": 2,
                "phase": "FINDING",
                "system_id": "account_service",
                "resource_id": "account_005",
                "operation": "REPORT_REMAINING_RECORD",
                "state": "PRESENT",
                "evidence_id": "evidence_54",
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
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_05,INCOMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_PHASE_ORDER", rule_codes)

    def test_invalid_evidence_reference(self):
        # Format violation
        step = {
            "step_no": 1,
            "phase": "VERIFICATION",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT",
            "evidence_id": "random_file_id.txt",  # Invalid pattern!
        }
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps([step]).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_EVIDENCE_FORMAT", rule_codes)

    def test_unresolved_evidence_reference(self):
        validator = OutputValidator(valid_evidence_ids={"evidence_01", "evidence_02"})
        step = {
            "step_no": 1,
            "phase": "VERIFICATION",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "VERIFY_RECORD_ABSENT",
            "state": "ABSENT",
            "evidence_id": "evidence_999",  # Not in valid set
        }
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps([step]).replace('\"', '\"\"')}\"\n"
        )
        res = validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_UNRESOLVED_EVIDENCE", rule_codes)

    def test_remediation_with_nonempty_evidence_id_fails(self):
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
                "evidence_id": "evidence_54",  # Violates rule! Remediation must have empty evidence_id
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
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_05,INCOMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = self.validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_REMEDIATION_NONEMPTY_EVIDENCE", rule_codes)

    def test_post_cutoff_finding_rejected_by_temporal_validation(self):
        case_cutoffs = {"case_01": "2026-09-01T12:00:00Z"}
        evidence_timestamps = {"evidence_54": "2026-09-05T12:00:00Z"}  # Captured after cutoff!

        validator = OutputValidator(
            case_cutoffs=case_cutoffs,
            evidence_timestamps=evidence_timestamps,
        )
        steps = [
            {
                "step_no": 1,
                "phase": "VERIFICATION",
                "system_id": "account_service",
                "resource_id": "account_001",
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT",
                "evidence_id": "evidence_54",
            }
        ]
        csv_text = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,COMPLETE,\"{json.dumps(steps).replace('\"', '\"\"')}\"\n"
        )
        res = validator.validate_csv_content(csv_text)
        self.assertFalse(res.is_valid)
        rule_codes = [e.rule_code for e in res.errors]
        self.assertIn("ERR_POST_CUTOFF_EVIDENCE", rule_codes)

    def test_sample_output_csv_passes_all_checks(self):
        res = self.validator.validate_csv_file("dataset/sample_output.csv")
        self.assertTrue(res.is_valid, f"sample_output.csv failed: {res.summary()}")
        self.assertEqual(res.case_count, 32)
        self.assertEqual(len(res.errors), 0)


if __name__ == "__main__":
    unittest.main()
