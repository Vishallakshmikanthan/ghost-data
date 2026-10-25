"""Unit tests for the sample-output evaluation utility."""

import json
import tempfile
import unittest
from pathlib import Path

from code.evaluation.evaluator import SampleEvaluator


class TestEvaluator(unittest.TestCase):
    """Test suite for SampleEvaluator precision/recall, sequence, and diff reporting."""

    def setUp(self):
        self.evaluator = SampleEvaluator()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_temp_csv(self, content: str) -> Path:
        p = Path(self.temp_dir.name) / "test_pred.csv"
        p.write_text(content, encoding="utf-8")
        return p

    def test_evaluate_sample_against_itself(self):
        sample_path = Path("dataset/sample_output.csv")
        report = self.evaluator.evaluate_file(sample_path)

        self.assertEqual(report.total_cases, 32)
        self.assertEqual(report.status_accuracy, 1.0)
        self.assertEqual(report.exact_case_accuracy, 1.0)
        self.assertEqual(report.sequence_accuracy, 1.0)
        self.assertEqual(report.step_precision, 1.0)
        self.assertEqual(report.step_recall, 1.0)
        self.assertEqual(report.step_f1, 1.0)
        self.assertTrue(report.contract_result.is_structurally_valid)

    def test_status_mismatch_detection(self):
        ref_path = self._write_temp_csv(
            "case_id,deletion_status,steps_json\n"
            "case_01,COMPLETE,[]\n"
            "case_02,INCOMPLETE,[]\n"
        )
        pred_path = Path(self.temp_dir.name) / "pred.csv"
        # case_02 status inverted
        pred_path.write_text(
            "case_id,deletion_status,steps_json\n"
            "case_01,COMPLETE,[]\n"
            "case_02,COMPLETE,[]\n",
            encoding="utf-8",
        )

        ev = SampleEvaluator(reference_path=ref_path)
        report = ev.evaluate_file(pred_path, reference_path=ref_path)

        self.assertEqual(report.total_cases, 2)
        self.assertEqual(report.status_matches, 1)
        self.assertEqual(report.status_accuracy, 0.5)
        self.assertEqual(report.status_confusion_matrix["COMPLETE"]["INCOMPLETE"], 1)

    def test_missing_and_extra_steps_detection(self):
        s1 = {
            "step_no": 1,
            "phase": "FINDING",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT",
            "evidence_id": "evidence_01",
        }
        s2 = {
            "step_no": 2,
            "phase": "REMEDIATION",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "DELETE_RECORD",
            "state": "ABSENT",
            "evidence_id": "",
        }

        # Reference has s1 and s2
        ref_csv = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,INCOMPLETE,\"{json.dumps([s1, s2]).replace('\"', '\"\"')}\"\n"
        )
        ref_path = self._write_temp_csv(ref_csv)

        # Prediction has only s1
        pred_path = Path(self.temp_dir.name) / "pred2.csv"
        pred_csv = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,INCOMPLETE,\"{json.dumps([s1]).replace('\"', '\"\"')}\"\n"
        )
        pred_path.write_text(pred_csv, encoding="utf-8")

        ev = SampleEvaluator(reference_path=ref_path)
        report = ev.evaluate_file(pred_path, reference_path=ref_path)

        # Expected: 2 steps, actual: 1 step, matched: 1 step
        self.assertEqual(report.total_expected_steps, 2)
        self.assertEqual(report.total_actual_steps, 1)
        self.assertEqual(report.matched_steps, 1)
        self.assertEqual(report.step_precision, 1.0)
        self.assertEqual(report.step_recall, 0.5)
        self.assertAlmostEqual(report.step_f1, 2 * (1.0 * 0.5) / 1.5)

    def test_reordered_steps_sequence_mismatch(self):
        s1 = {
            "step_no": 1,
            "phase": "FINDING",
            "system_id": "account_service",
            "resource_id": "account_001",
            "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT",
            "evidence_id": "evidence_01",
        }
        s2 = {
            "step_no": 2,
            "phase": "FINDING",
            "system_id": "search_service",
            "resource_id": "search_002",
            "operation": "REPORT_REMAINING_RECORD",
            "state": "PRESENT",
            "evidence_id": "evidence_01",
        }

        ref_csv = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,INCOMPLETE,\"{json.dumps([s1, s2]).replace('\"', '\"\"')}\"\n"
        )
        ref_path = self._write_temp_csv(ref_csv)

        # Swap steps in prediction: s2 first, s1 second
        s2_swapped = dict(s2, step_no=1)
        s1_swapped = dict(s1, step_no=2)
        pred_path = Path(self.temp_dir.name) / "pred_swap.csv"
        pred_csv = (
            "case_id,deletion_status,steps_json\n"
            f"case_01,INCOMPLETE,\"{json.dumps([s2_swapped, s1_swapped]).replace('\"', '\"\"')}\"\n"
        )
        pred_path.write_text(pred_csv, encoding="utf-8")

        ev = SampleEvaluator(reference_path=ref_path)
        report = ev.evaluate_file(pred_path, reference_path=ref_path)

        # Multiset precision and recall are 1.0, but sequence accuracy is 0.0!
        self.assertEqual(report.step_precision, 1.0)
        self.assertEqual(report.step_recall, 1.0)
        self.assertEqual(report.sequence_matches, 0)
        self.assertEqual(report.sequence_accuracy, 0.0)


if __name__ == "__main__":
    unittest.main()
