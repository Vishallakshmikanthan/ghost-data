"""Ghost Data Still Exists - Strict Output Contract Validator.

Validates output CSV submissions and in-memory predictions against all
syntactic, structural, canonical ordering, and semantic rules defined in
problem_statement.md and AGENTS.md.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from code.schemas import (
    OPERATION_ORDER,
    OPERATION_STATE,
    OUTPUT_COLUMNS,
    PHASE_OPERATIONS,
    PHASE_ORDER,
    REQUIRED_STEP_KEYS,
    CaseResult,
    DeletionStatus,
    OutputStep,
    Phase,
)

EVIDENCE_ID_PATTERN = re.compile(r"^(evidence|activity|audit)_\d+$")


@dataclass
class ValidationError:
    """An actionable, structured error returned by the validator."""
    rule_code: str
    message: str
    case_id: Optional[str] = None
    step_no: Optional[int] = None
    field: Optional[str] = None
    is_semantic: bool = False

    def __str__(self) -> str:
        ctx_parts = []
        if self.case_id:
            ctx_parts.append(f"case={self.case_id}")
        if self.step_no is not None:
            ctx_parts.append(f"step={self.step_no}")
        if self.field:
            ctx_parts.append(f"field={self.field}")
        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""
        err_type = "SEMANTIC" if self.is_semantic else "STRUCTURAL"
        return f"[{err_type}:{self.rule_code}]{ctx_str} {self.message}"


@dataclass
class ValidationResult:
    """Complete summary of a validation run."""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    case_count: int = 0

    @property
    def structural_errors(self) -> List[ValidationError]:
        return [e for e in self.errors if not e.is_semantic]

    @property
    def semantic_errors(self) -> List[ValidationError]:
        return [e for e in self.errors if e.is_semantic]

    @property
    def is_structurally_valid(self) -> bool:
        return len(self.structural_errors) == 0

    def summary(self) -> str:
        lines = [
            f"Validation Result: {'PASS' if self.is_valid else 'FAIL'}",
            f"Total cases evaluated: {self.case_count}",
            f"Structural errors: {len(self.structural_errors)}",
            f"Semantic errors: {len(self.semantic_errors)}",
            f"Warnings: {len(self.warnings)}",
        ]
        if self.errors:
            lines.append("Errors (first 25 shown):")
            for err in self.errors[:25]:
                lines.append(f"  - {err}")
            if len(self.errors) > 25:
                lines.append(f"  ... and {len(self.errors) - 25} more errors.")
        return "\n".join(lines)


class OutputValidator:
    """Strict validator for deletion-audit output files and records."""

    def __init__(
        self,
        expected_case_ids: Optional[Sequence[str]] = None,
        expected_count: Optional[int] = None,
        valid_evidence_ids: Optional[Set[str]] = None,
        case_cutoffs: Optional[Dict[str, str]] = None,
        evidence_timestamps: Optional[Dict[str, str]] = None,
        expected_requirements: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Configure validation parameters.

        Args:
            expected_case_ids: Ordered sequence of expected case IDs.
            expected_count: Expected number of case rows.
            valid_evidence_ids: Known valid evidence, activity, or audit IDs.
            case_cutoffs: Map of case_id -> audit_at timestamp (ISO-8601).
            evidence_timestamps: Map of evidence_id -> captured_at timestamp (ISO-8601).
            expected_requirements: Map of case_id -> expected resources/coverage for semantic verification.
        """
        self.expected_case_ids = list(expected_case_ids) if expected_case_ids else None
        self.expected_count = expected_count or (len(self.expected_case_ids) if self.expected_case_ids else None)
        self.valid_evidence_ids = valid_evidence_ids
        self.case_cutoffs = case_cutoffs or {}
        self.evidence_timestamps = evidence_timestamps or {}
        self.expected_requirements = expected_requirements or {}

    @staticmethod
    def _natural_case_sort_key(case_id: str) -> Any:
        if "_" in case_id:
            prefix, num_part = case_id.split("_", 1)
            if num_part.isdigit():
                return (prefix, int(num_part))
        return case_id

    def validate_csv_file(self, file_path: Union[str, Path]) -> ValidationResult:
        """Validate a CSV file path."""
        p = Path(file_path)
        if not p.exists():
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        rule_code="ERR_FILE_NOT_FOUND",
                        message=f"File not found: {file_path}",
                    )
                ],
            )
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        rule_code="ERR_ENCODING",
                        message=f"File is not valid UTF-8: {exc}",
                    )
                ],
            )
        return self.validate_csv_content(content)

    def validate_csv_content(self, csv_content: str) -> ValidationResult:
        """Validate raw CSV text content."""
        errors: List[ValidationError] = []
        warnings: List[str] = []

        f = io.StringIO(csv_content.strip())
        reader = csv.reader(f)

        # 1. Header validation
        try:
            header = next(reader)
        except StopIteration:
            errors.append(
                ValidationError(
                    rule_code="ERR_EMPTY_FILE",
                    message="CSV file is completely empty",
                )
            )
            return ValidationResult(is_valid=False, errors=errors)

        if tuple(header) != OUTPUT_COLUMNS:
            errors.append(
                ValidationError(
                    rule_code="ERR_HEADER_MISMATCH",
                    field="header",
                    message=f"Invalid CSV header. Expected {list(OUTPUT_COLUMNS)}, got {header}",
                )
            )

        # 2. Row parsing
        rows: List[Tuple[str, str, str]] = []
        seen_case_ids: Set[str] = set()
        case_ids_in_order: List[str] = []

        for row_idx, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != 3:
                errors.append(
                    ValidationError(
                        rule_code="ERR_COLUMN_COUNT",
                        message=f"Row {row_idx} has {len(row)} columns, expected 3",
                    )
                )
                continue

            case_id, deletion_status, steps_json_str = row[0].strip(), row[1].strip(), row[2].strip()

            if case_id in seen_case_ids:
                errors.append(
                    ValidationError(
                        rule_code="ERR_DUPLICATE_CASE_ID",
                        case_id=case_id,
                        field="case_id",
                        message=f"Duplicate case_id found: '{case_id}'",
                    )
                )
            else:
                seen_case_ids.add(case_id)
                case_ids_in_order.append(case_id)

            rows.append((case_id, deletion_status, steps_json_str))

        # 3. Row count and coverage validation
        if self.expected_count is not None and len(rows) != self.expected_count:
            errors.append(
                ValidationError(
                    rule_code="ERR_CASE_COUNT",
                    field="case_id",
                    message=f"Expected exactly {self.expected_count} rows, found {len(rows)}",
                )
            )

        if self.expected_case_ids is not None:
            expected_set = set(self.expected_case_ids)
            missing = expected_set - seen_case_ids
            unexpected = seen_case_ids - expected_set
            if missing:
                errors.append(
                    ValidationError(
                        rule_code="ERR_MISSING_CASES",
                        field="case_id",
                        message=f"Missing {len(missing)} expected cases: {sorted(list(missing))[:10]}",
                    )
                )
            if unexpected:
                errors.append(
                    ValidationError(
                        rule_code="ERR_UNEXPECTED_CASES",
                        field="case_id",
                        message=f"Found {len(unexpected)} unexpected cases: {sorted(list(unexpected))[:10]}",
                    )
                )
            # Check ordering against expected sequence
            if case_ids_in_order != [cid for cid in self.expected_case_ids if cid in seen_case_ids]:
                errors.append(
                    ValidationError(
                        rule_code="ERR_CASE_ORDERING",
                        field="case_id",
                        message="Cases are not sorted in the expected order.",
                    )
                )
        else:
            # Check natural sort order
            sorted_case_ids = sorted(case_ids_in_order, key=self._natural_case_sort_key)
            if case_ids_in_order != sorted_case_ids:
                errors.append(
                    ValidationError(
                        rule_code="ERR_CASE_ORDERING",
                        field="case_id",
                        message="Cases are not sorted in ascending case_id order.",
                    )
                )

        # 4. Per-case step and status validation
        for case_id, deletion_status, steps_json_str in rows:
            case_errors = self._validate_case(case_id, deletion_status, steps_json_str)
            errors.extend(case_errors)

        return ValidationResult(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings,
            case_count=len(rows),
        )

    @staticmethod
    def _natural_case_sort_key(case_id: str) -> Tuple[int, str]:
        """Compute sorting key for natural case ordering (e.g. case_1, case_2 ... case_550)."""
        parts = case_id.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            return (int(parts[1]), case_id)
        return (999999, case_id)

    def _validate_case(
        self, case_id: str, deletion_status: str, steps_json_str: str
    ) -> List[ValidationError]:
        """Validate a single case row."""
        errors: List[ValidationError] = []

        # Validate deletion_status enum
        if deletion_status not in DeletionStatus.values():
            errors.append(
                ValidationError(
                    rule_code="ERR_INVALID_STATUS",
                    case_id=case_id,
                    field="deletion_status",
                    message=f"Invalid status '{deletion_status}'. Must be one of {sorted(list(DeletionStatus.values()))}",
                )
            )

        # Parse JSON
        try:
            steps_data = json.loads(steps_json_str)
        except Exception as exc:
            errors.append(
                ValidationError(
                    rule_code="ERR_INVALID_JSON",
                    case_id=case_id,
                    field="steps_json",
                    message=f"Failed to parse steps_json as valid JSON: {exc}",
                )
            )
            return errors

        if not isinstance(steps_data, list):
            errors.append(
                ValidationError(
                    rule_code="ERR_STEPS_NOT_ARRAY",
                    case_id=case_id,
                    field="steps_json",
                    message="steps_json must be a JSON array",
                )
            )
            return errors

        # NOT_APPLICABLE rule: steps array must be empty
        if deletion_status == DeletionStatus.NOT_APPLICABLE.value:
            if len(steps_data) != 0:
                errors.append(
                    ValidationError(
                        rule_code="ERR_NOT_APPLICABLE_NONEMPTY",
                        case_id=case_id,
                        field="steps_json",
                        message=f"NOT_APPLICABLE status must have empty steps array [], but got {len(steps_data)} steps",
                    )
                )
            return errors

        # For COMPLETE or INCOMPLETE, validate individual steps
        parsed_steps: List[OutputStep] = []
        for idx, step_dict in enumerate(steps_data):
            step_errors, parsed_step = self._validate_step_structure(case_id, idx, step_dict)
            errors.extend(step_errors)
            if parsed_step:
                parsed_steps.append(parsed_step)

        if len(parsed_steps) != len(steps_data):
            # Structural failures in step dictionaries prevent full sequence validation
            return errors

        # Step numbering validation: must start at 1 and be consecutive
        for idx, s in enumerate(parsed_steps, start=1):
            if s.step_no != idx:
                errors.append(
                    ValidationError(
                        rule_code="ERR_STEP_NUMBERING",
                        case_id=case_id,
                        step_no=s.step_no,
                        field="step_no",
                        message=f"Step numbers must start at 1 and increase consecutively. Expected {idx}, got {s.step_no}",
                    )
                )

        # Status-specific phase requirements
        phases_present = [s.phase for s in parsed_steps]

        if deletion_status == DeletionStatus.COMPLETE.value:
            # COMPLETE must have ONLY VERIFICATION steps
            non_verif = [s for s in parsed_steps if s.phase != Phase.VERIFICATION.value]
            if non_verif:
                errors.append(
                    ValidationError(
                        rule_code="ERR_COMPLETE_NON_VERIFICATION",
                        case_id=case_id,
                        field="phase",
                        message=f"COMPLETE case must contain only VERIFICATION steps, found {len(non_verif)} other phase steps",
                    )
                )
            # All steps in COMPLETE must cite non-empty evidence
            for s in parsed_steps:
                if not s.evidence_id:
                    errors.append(
                        ValidationError(
                            rule_code="ERR_COMPLETE_EMPTY_EVIDENCE",
                            case_id=case_id,
                            step_no=s.step_no,
                            field="evidence_id",
                            message="All verification steps in COMPLETE cases must cite non-empty evidence proving current state.",
                        )
                    )

        elif deletion_status == DeletionStatus.INCOMPLETE.value:
            # INCOMPLETE must contain FINDING, REMEDIATION, and VERIFICATION phases
            has_finding = Phase.FINDING.value in phases_present
            has_remediation = Phase.REMEDIATION.value in phases_present
            has_verification = Phase.VERIFICATION.value in phases_present

            if not has_finding:
                errors.append(
                    ValidationError(
                        rule_code="ERR_INCOMPLETE_MISSING_FINDING",
                        case_id=case_id,
                        field="phase",
                        message="INCOMPLETE case must contain at least one FINDING step.",
                    )
                )
            if not has_remediation:
                errors.append(
                    ValidationError(
                        rule_code="ERR_INCOMPLETE_MISSING_REMEDIATION",
                        case_id=case_id,
                        field="phase",
                        message="INCOMPLETE case must contain at least one REMEDIATION step.",
                    )
                )
            if not has_verification:
                errors.append(
                    ValidationError(
                        rule_code="ERR_INCOMPLETE_MISSING_VERIFICATION",
                        case_id=case_id,
                        field="phase",
                        message="INCOMPLETE case must contain future VERIFICATION steps.",
                    )
                )

            # Evidence rules for INCOMPLETE:
            # Findings must have evidence_id != ""
            # Remediation and post-remediation verification must have evidence_id == ""
            for s in parsed_steps:
                if s.phase == Phase.FINDING.value and not s.evidence_id:
                    errors.append(
                        ValidationError(
                            rule_code="ERR_FINDING_EMPTY_EVIDENCE",
                            case_id=case_id,
                            step_no=s.step_no,
                            field="evidence_id",
                            message="FINDING steps must cite non-empty evidence_id.",
                        )
                    )
                elif s.phase == Phase.REMEDIATION.value and s.evidence_id != "":
                    errors.append(
                        ValidationError(
                            rule_code="ERR_REMEDIATION_NONEMPTY_EVIDENCE",
                            case_id=case_id,
                            step_no=s.step_no,
                            field="evidence_id",
                            message=f"REMEDIATION steps must have empty evidence_id (''), got '{s.evidence_id}'",
                        )
                    )
                elif s.phase == Phase.VERIFICATION.value and s.evidence_id != "":
                    errors.append(
                        ValidationError(
                            rule_code="ERR_FUTURE_VERIFICATION_NONEMPTY_EVIDENCE",
                            case_id=case_id,
                            step_no=s.step_no,
                            field="evidence_id",
                            message=f"Future VERIFICATION steps in INCOMPLETE cases must have empty evidence_id (''), got '{s.evidence_id}'",
                        )
                    )

        # Ordering validation
        # 1. Phase order: FINDING -> REMEDIATION -> VERIFICATION
        phase_ranks = [PHASE_ORDER.get(s.phase, 99) for s in parsed_steps]
        if phase_ranks != sorted(phase_ranks):
            errors.append(
                ValidationError(
                    rule_code="ERR_PHASE_ORDER",
                    case_id=case_id,
                    field="phase",
                    message="Steps violate phase ordering (FINDING -> REMEDIATION -> VERIFICATION).",
                )
            )

        # 2. Canonical step ordering:
        # Within each phase: operation rank -> system_id -> resource_id
        canonical_keys = [s.canonical_sort_key() for s in parsed_steps]
        if canonical_keys != sorted(canonical_keys):
            errors.append(
                ValidationError(
                    rule_code="ERR_STEP_ORDERING",
                    case_id=case_id,
                    field="steps_json",
                    message="Steps are not sorted in canonical order (phase -> operation -> system_id -> resource_id).",
                )
            )

        # Semantic checks: evidence validity and cutoff
        semantic_errors = self._validate_step_semantics(case_id, parsed_steps)
        errors.extend(semantic_errors)

        return errors

    def _validate_step_structure(
        self, case_id: str, array_idx: int, step_data: Any
    ) -> Tuple[List[ValidationError], Optional[OutputStep]]:
        """Validate structure and allowed values for a single step dict."""
        errors: List[ValidationError] = []

        if not isinstance(step_data, dict):
            errors.append(
                ValidationError(
                    rule_code="ERR_STEP_NOT_OBJECT",
                    case_id=case_id,
                    step_no=array_idx + 1,
                    message=f"Step at index {array_idx} must be a JSON object",
                )
            )
            return errors, None

        # Check required keys
        missing_keys = [k for k in REQUIRED_STEP_KEYS if k not in step_data]
        if missing_keys:
            errors.append(
                ValidationError(
                    rule_code="ERR_MISSING_STEP_KEY",
                    case_id=case_id,
                    step_no=step_data.get("step_no", array_idx + 1),
                    message=f"Step missing required keys: {missing_keys}",
                )
            )

        extra_keys = [k for k in step_data if k not in REQUIRED_STEP_KEYS]
        if extra_keys:
            errors.append(
                ValidationError(
                    rule_code="ERR_EXTRA_STEP_KEY",
                    case_id=case_id,
                    step_no=step_data.get("step_no", array_idx + 1),
                    message=f"Step contains unexpected keys: {extra_keys}",
                )
            )

        if missing_keys:
            return errors, None

        # Extract values
        step_no = step_data["step_no"]
        phase = str(step_data["phase"])
        system_id = str(step_data["system_id"])
        resource_id = str(step_data["resource_id"])
        operation = str(step_data["operation"])
        state = str(step_data["state"])
        evidence_id = str(step_data["evidence_id"])

        if not isinstance(step_no, int):
            errors.append(
                ValidationError(
                    rule_code="ERR_STEP_NO_TYPE",
                    case_id=case_id,
                    step_no=step_no,
                    field="step_no",
                    message=f"step_no must be an integer, got {type(step_no).__name__}",
                )
            )

        # Validate Phase
        if phase not in Phase.values():
            errors.append(
                ValidationError(
                    rule_code="ERR_INVALID_PHASE",
                    case_id=case_id,
                    step_no=step_no,
                    field="phase",
                    message=f"Invalid phase '{phase}'. Must be one of {sorted(list(Phase.values()))}",
                )
            )

        # Validate Operation
        allowed_ops_for_phase = PHASE_OPERATIONS.get(phase, set())
        if operation not in allowed_ops_for_phase:
            errors.append(
                ValidationError(
                    rule_code="ERR_INVALID_OPERATION",
                    case_id=case_id,
                    step_no=step_no,
                    field="operation",
                    message=f"Operation '{operation}' is not allowed for phase '{phase}'. Allowed: {sorted(list(allowed_ops_for_phase))}",
                )
            )

        # Validate State pairing
        expected_state = OPERATION_STATE.get(operation)
        if expected_state and state != expected_state:
            errors.append(
                ValidationError(
                    rule_code="ERR_INVALID_STATE",
                    case_id=case_id,
                    step_no=step_no,
                    field="state",
                    message=f"Invalid state '{state}' for operation '{operation}'. Expected '{expected_state}'",
                )
            )

        if errors:
            return errors, None

        return errors, OutputStep(
            step_no=step_no,
            phase=phase,
            system_id=system_id,
            resource_id=resource_id,
            operation=operation,
            state=state,
            evidence_id=evidence_id,
        )

    def _validate_step_semantics(
        self, case_id: str, steps: List[OutputStep]
    ) -> List[ValidationError]:
        """Perform semantic validation on evidence resolution and temporal cutoff."""
        errors: List[ValidationError] = []
        audit_at = self.case_cutoffs.get(case_id)

        for s in steps:
            if not s.evidence_id:
                continue

            # Format check
            if not EVIDENCE_ID_PATTERN.match(s.evidence_id):
                errors.append(
                    ValidationError(
                        rule_code="ERR_EVIDENCE_FORMAT",
                        case_id=case_id,
                        step_no=s.step_no,
                        field="evidence_id",
                        message=f"Evidence ID '{s.evidence_id}' does not match expected format (evidence_XX, activity_XX, audit_XX)",
                        is_semantic=True,
                    )
                )

            # Resolution check against known evidence registry
            if self.valid_evidence_ids is not None and s.evidence_id not in self.valid_evidence_ids:
                errors.append(
                    ValidationError(
                        rule_code="ERR_UNRESOLVED_EVIDENCE",
                        case_id=case_id,
                        step_no=s.step_no,
                        field="evidence_id",
                        message=f"Evidence ID '{s.evidence_id}' does not resolve to any known evidence file, activity, or audit record",
                        is_semantic=True,
                    )
                )

            # Temporal cutoff validation: captured_at <= audit_at
            if audit_at and s.evidence_id in self.evidence_timestamps:
                captured_at = self.evidence_timestamps[s.evidence_id]
                if captured_at > audit_at:
                    errors.append(
                        ValidationError(
                            rule_code="ERR_POST_CUTOFF_EVIDENCE",
                            case_id=case_id,
                            step_no=s.step_no,
                            field="evidence_id",
                            message=f"Evidence '{s.evidence_id}' captured at {captured_at} is strictly after case audit_at {audit_at}",
                            is_semantic=True,
                        )
                    )

        # Missing remediation / verification coverage check
        reqs = self.expected_requirements.get(case_id)
        if reqs:
            expected_resources = set(reqs.get("resources", []))
            reported_resources = {s.resource_id for s in steps}
            missing = expected_resources - reported_resources
            if missing:
                errors.append(
                    ValidationError(
                        rule_code="ERR_INCOMPLETE_COVERAGE",
                        case_id=case_id,
                        message=f"Missing expected resource coverage for: {sorted(list(missing))}",
                        is_semantic=True,
                    )
                )

        return errors
