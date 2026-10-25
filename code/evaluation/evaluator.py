"""Ghost Data Still Exists - Evaluation Utility.

Compares deletion-audit predictions against reference answers semantically:
- Evaluates deletion status accuracy and confusion matrix.
- Evaluates exact-case match (status + steps in exact sequence).
- Evaluates sequence accuracy across matched step sets.
- Computes step-level precision, recall, and F1.
- Identifies missing/extra steps and field-level differences.
- Separates structural/contract errors from semantic comparison.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from code.output.validator import OutputValidator, ValidationError, ValidationResult
from code.schemas import DeletionStatus, OutputStep


@dataclass
class StepDiff:
    """Detailed difference between predicted and expected steps for a case."""
    step_no: int
    field_name: str
    expected_value: Any
    actual_value: Any


@dataclass
class CaseEvaluation:
    """Evaluation result for an individual case."""
    case_id: str
    status_match: bool
    expected_status: str
    actual_status: str
    steps_exact_match: bool
    sequence_match: bool
    expected_step_count: int
    actual_step_count: int
    missing_steps: List[Dict[str, Any]] = field(default_factory=list)
    extra_steps: List[Dict[str, Any]] = field(default_factory=list)
    field_differences: List[StepDiff] = field(default_factory=list)


@dataclass
class EvaluationReport:
    """Comprehensive evaluation metrics and summary."""
    total_cases: int
    status_matches: int
    status_accuracy: float
    exact_case_matches: int
    exact_case_accuracy: float
    sequence_matches: int
    sequence_accuracy: float
    total_expected_steps: int
    total_actual_steps: int
    matched_steps: int
    step_precision: float
    step_recall: float
    step_f1: float
    status_confusion_matrix: Dict[str, Dict[str, int]]
    contract_result: ValidationResult
    case_evaluations: List[CaseEvaluation] = field(default_factory=list)

    def format_console_summary(self) -> str:
        """Format a human-readable console summary."""
        lines = [
            "============================================================",
            "        DELETION AUDIT EVALUATION SUMMARY REPORT            ",
            "============================================================",
            f"Total Evaluated Cases:       {self.total_cases}",
            f"Status Accuracy:             {self.status_accuracy * 100:.2f}% ({self.status_matches}/{self.total_cases})",
            f"Exact Case Accuracy:         {self.exact_case_accuracy * 100:.2f}% ({self.exact_case_matches}/{self.total_cases})",
            f"Sequence Accuracy:           {self.sequence_accuracy * 100:.2f}% ({self.sequence_matches}/{self.total_cases})",
            "------------------------------------------------------------",
            "Step-Level Metrics:",
            f"  Expected Steps:            {self.total_expected_steps}",
            f"  Actual Steps:              {self.total_actual_steps}",
            f"  Matched Steps:             {self.matched_steps}",
            f"  Step Precision:            {self.step_precision * 100:.2f}%",
            f"  Step Recall:               {self.step_recall * 100:.2f}%",
            f"  Step F1 Score:             {self.step_f1 * 100:.2f}%",
            "------------------------------------------------------------",
            "Status Confusion Matrix (Actual \\ Expected):",
        ]

        all_statuses = sorted(list(DeletionStatus.values()))
        header_row = f"  {'':<16} " + " ".join(f"{s:<16}" for s in all_statuses)
        lines.append(header_row)
        for act in all_statuses:
            row_vals = [f"{self.status_confusion_matrix.get(act, {}).get(exp, 0):<16}" for exp in all_statuses]
            lines.append(f"  {act:<16} " + " ".join(row_vals))

        lines.append("------------------------------------------------------------")
        lines.append(f"Contract Structural Validity: {'PASS' if self.contract_result.is_structurally_valid else 'FAIL'}")
        if not self.contract_result.is_structurally_valid:
            lines.append(f"Contract Violations:         {len(self.contract_result.structural_errors)}")
            for err in self.contract_result.structural_errors[:5]:
                lines.append(f"  * {err}")

        # List failures if any
        failures = [c for c in self.case_evaluations if not c.steps_exact_match or not c.status_match]
        if failures:
            lines.append("------------------------------------------------------------")
            lines.append(f"Discrepancies in {len(failures)} cases (first 5 shown):")
            for c in failures[:5]:
                lines.append(f"  Case {c.case_id}:")
                if not c.status_match:
                    lines.append(f"    - Status: expected '{c.expected_status}', got '{c.actual_status}'")
                if c.missing_steps:
                    lines.append(f"    - Missing {len(c.missing_steps)} expected step(s)")
                if c.extra_steps:
                    lines.append(f"    - Extra {len(c.extra_steps)} unexpected step(s)")
                if c.field_differences:
                    lines.append(f"    - {len(c.field_differences)} field differences (e.g., {c.field_differences[0]})")
        else:
            lines.append("All cases matched reference answers with 100% precision and recall.")

        lines.append("============================================================")
        return "\n".join(lines)


class SampleEvaluator:
    """Evaluates prediction CSV files or dictionaries against ground-truth references."""

    def __init__(self, reference_path: Optional[Union[str, Path]] = None):
        if reference_path is None:
            # Default to repo sample output
            repo_root = Path(__file__).resolve().parent.parent.parent
            default_path = repo_root / "dataset" / "sample_output.csv"
            self.reference_path = default_path if default_path.exists() else None
        else:
            self.reference_path = Path(reference_path)

        self.reference_data: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}
        if self.reference_path and self.reference_path.exists():
            self.reference_data = self._load_csv(self.reference_path)

    @staticmethod
    def _load_csv(file_path: Path) -> Dict[str, Tuple[str, List[Dict[str, Any]]]]:
        """Load and parse case_id -> (status, steps) from CSV."""
        results: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if not row or len(row) < 3:
                    continue
                case_id, status, steps_str = row[0].strip(), row[1].strip(), row[2].strip()
                try:
                    steps = json.loads(steps_str)
                except Exception:
                    steps = []
                results[case_id] = (status, steps)
        return results

    def evaluate_file(
        self,
        predictions_path: Union[str, Path],
        reference_path: Optional[Union[str, Path]] = None,
    ) -> EvaluationReport:
        """Evaluate a prediction CSV file against reference."""
        pred_path = Path(predictions_path)
        ref_data = self.reference_data
        if reference_path is not None:
            ref_data = self._load_csv(Path(reference_path))

        if not ref_data:
            raise ValueError("No reference data available for evaluation.")

        # Run strict contract validation on prediction file
        validator = OutputValidator(
            expected_case_ids=list(ref_data.keys()),
            expected_count=len(ref_data),
        )
        contract_result = validator.validate_csv_file(pred_path)

        pred_data = self._load_csv(pred_path)
        return self._compare(pred_data, ref_data, contract_result)

    def _compare(
        self,
        pred_data: Dict[str, Tuple[str, List[Dict[str, Any]]]],
        ref_data: Dict[str, Tuple[str, List[Dict[str, Any]]]],
        contract_result: ValidationResult,
    ) -> EvaluationReport:
        """Compare predicted data against reference data."""
        total_cases = len(ref_data)
        status_matches = 0
        exact_case_matches = 0
        sequence_matches = 0

        total_expected_steps = 0
        total_actual_steps = 0
        matched_steps = 0

        all_statuses = sorted(list(DeletionStatus.values()))
        confusion_matrix: Dict[str, Dict[str, int]] = {
            act: {exp: 0 for exp in all_statuses} for act in all_statuses
        }

        case_evaluations: List[CaseEvaluation] = []

        for case_id, (ref_status, ref_steps) in ref_data.items():
            total_expected_steps += len(ref_steps)

            if case_id not in pred_data:
                case_evaluations.append(
                    CaseEvaluation(
                        case_id=case_id,
                        status_match=False,
                        expected_status=ref_status,
                        actual_status="MISSING",
                        steps_exact_match=False,
                        sequence_match=False,
                        expected_step_count=len(ref_steps),
                        actual_step_count=0,
                        missing_steps=ref_steps,
                    )
                )
                continue

            pred_status, pred_steps = pred_data[case_id]
            total_actual_steps += len(pred_steps)

            # Record confusion matrix
            if pred_status in confusion_matrix and ref_status in confusion_matrix[pred_status]:
                confusion_matrix[pred_status][ref_status] += 1

            status_match = (pred_status == ref_status)
            if status_match:
                status_matches += 1

            # Step comparison
            case_eval = self._compare_case_steps(case_id, ref_status, pred_status, ref_steps, pred_steps)
            case_evaluations.append(case_eval)

            if case_eval.steps_exact_match:
                exact_case_matches += 1

            if case_eval.sequence_match:
                sequence_matches += 1

            # Count multiset matched steps for precision/recall
            matched_steps += self._count_step_matches(ref_steps, pred_steps)

        status_acc = status_matches / total_cases if total_cases > 0 else 0.0
        exact_acc = exact_case_matches / total_cases if total_cases > 0 else 0.0
        seq_acc = sequence_matches / total_cases if total_cases > 0 else 0.0

        precision = matched_steps / total_actual_steps if total_actual_steps > 0 else 0.0
        recall = matched_steps / total_expected_steps if total_expected_steps > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        return EvaluationReport(
            total_cases=total_cases,
            status_matches=status_matches,
            status_accuracy=status_acc,
            exact_case_matches=exact_case_matches,
            exact_case_accuracy=exact_acc,
            sequence_matches=sequence_matches,
            sequence_accuracy=seq_acc,
            total_expected_steps=total_expected_steps,
            total_actual_steps=total_actual_steps,
            matched_steps=matched_steps,
            step_precision=precision,
            step_recall=recall,
            step_f1=f1,
            status_confusion_matrix=confusion_matrix,
            contract_result=contract_result,
            case_evaluations=case_evaluations,
        )

    @staticmethod
    def _step_semantic_key(step: Dict[str, Any]) -> Tuple[Any, ...]:
        """Key used for semantic step equivalence (excluding step_no)."""
        return (
            step.get("phase"),
            step.get("system_id"),
            step.get("resource_id"),
            step.get("operation"),
            step.get("state"),
            step.get("evidence_id"),
        )

    def _count_step_matches(
        self, ref_steps: List[Dict[str, Any]], pred_steps: List[Dict[str, Any]]
    ) -> int:
        """Count matched steps between reference and prediction using multiset intersection."""
        ref_counts: Dict[Tuple[Any, ...], int] = {}
        for s in ref_steps:
            k = self._step_semantic_key(s)
            ref_counts[k] = ref_counts.get(k, 0) + 1

        matches = 0
        for s in pred_steps:
            k = self._step_semantic_key(s)
            if ref_counts.get(k, 0) > 0:
                matches += 1
                ref_counts[k] -= 1
        return matches

    def _compare_case_steps(
        self,
        case_id: str,
        ref_status: str,
        pred_status: str,
        ref_steps: List[Dict[str, Any]],
        pred_steps: List[Dict[str, Any]],
    ) -> CaseEvaluation:
        """Compare steps for a single case in detail."""
        status_match = (ref_status == pred_status)
        field_diffs: List[StepDiff] = []

        # Check sequence match:
        # Same length, and step_semantic_key matches at every index
        sequence_match = (len(ref_steps) == len(pred_steps)) and all(
            self._step_semantic_key(ref_steps[i]) == self._step_semantic_key(pred_steps[i])
            for i in range(len(ref_steps))
        )

        # Check exact match including step_no
        exact_match = status_match and (len(ref_steps) == len(pred_steps))
        if exact_match:
            for idx in range(len(ref_steps)):
                r_s = ref_steps[idx]
                p_s = pred_steps[idx]
                for k in ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"):
                    if r_s.get(k) != p_s.get(k):
                        exact_match = False
                        field_diffs.append(
                            StepDiff(
                                step_no=idx + 1,
                                field_name=k,
                                expected_value=r_s.get(k),
                                actual_value=p_s.get(k),
                            )
                        )

        # Missing and extra steps
        ref_keys = [self._step_semantic_key(s) for s in ref_steps]
        pred_keys = [self._step_semantic_key(s) for s in pred_steps]

        missing_steps: List[Dict[str, Any]] = []
        extra_steps: List[Dict[str, Any]] = []

        rem_pred_keys = list(pred_keys)
        for s in ref_steps:
            k = self._step_semantic_key(s)
            if k in rem_pred_keys:
                rem_pred_keys.remove(k)
            else:
                missing_steps.append(s)

        rem_ref_keys = list(ref_keys)
        for s in pred_steps:
            k = self._step_semantic_key(s)
            if k in rem_ref_keys:
                rem_ref_keys.remove(k)
            else:
                extra_steps.append(s)

        return CaseEvaluation(
            case_id=case_id,
            status_match=status_match,
            expected_status=ref_status,
            actual_status=pred_status,
            steps_exact_match=exact_match,
            sequence_match=sequence_match,
            expected_step_count=len(ref_steps),
            actual_step_count=len(pred_steps),
            missing_steps=missing_steps,
            extra_steps=extra_steps,
            field_differences=field_diffs,
        )
