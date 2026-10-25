"""Ghost Data Still Exists - Remediation Planner.

Constructs safe, complete, dependency-aware remediation action plans for INCOMPLETE cases.
Ensures active data flows and restore risks are mitigated before modifying dependent records.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from code.policy.violation_detector import PolicyViolation
from code.schemas import OPERATION_ORDER, Operation, OutputStep, Phase, State


class RemediationPlanner:
    """Plans ordered corrective remediation steps from established violations."""

    @classmethod
    def plan_remediation(
        cls,
        violations: List[PolicyViolation],
    ) -> List[OutputStep]:
        """Construct a deduplicated, ordered list of remediation steps with empty evidence_ids."""
        seen_actions: Set[tuple] = set()
        candidate_steps: List[OutputStep] = []

        for v in violations:
            action_key = (v.system_id, v.resource_id, v.required_remediation_op)
            if action_key in seen_actions:
                continue
            seen_actions.add(action_key)

            step = OutputStep(
                step_no=0,  # Will be assigned canonically later
                phase=Phase.REMEDIATION.value,
                system_id=v.system_id,
                resource_id=v.resource_id,
                operation=v.required_remediation_op,
                state=v.required_remediation_state,
                evidence_id="",  # Mandatory empty evidence_id for proposed remediation
            )
            candidate_steps.append(step)

        # Canonical remediation ordering:
        # 1. Operation rank (DISABLE_DATA_FLOW: 1, BLOCK_RESTORE: 2, DELETE_RECORD: 3, REDACT_RECORD: 4, ANONYMIZE_RECORD: 5)
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
