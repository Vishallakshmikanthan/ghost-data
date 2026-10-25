"""Ghost Data Still Exists - Policy Rule Loader.

Loads, validates, and provides indexed access to the policy rules defined in
dataset/deletion_rules.csv at runtime.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from code.schemas import DeletionRuleRecord

logger = logging.getLogger(__name__)

REQUIRED_RULE_COLUMNS = ("rule_id", "data_category", "condition", "required_action")

ALLOWED_ACTIONS: Set[str] = {
    "DELETE",
    "REDACT",
    "ANONYMIZE",
    "RETAIN",
}

KNOWN_DATA_CATEGORIES: Set[str] = {
    "USER_PROFILE",
    "SECONDARY_IDENTIFIER",
    "SEARCH_INDEX_ENTRY",
    "CACHE_ENTRY",
    "SUPPORT_MESSAGE",
    "SUPPORT_ATTACHMENT",
    "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY",
    "USER_EXPORT",
    "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG",
    "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE",
    "ACCOUNT_TOMBSTONE",
}


@dataclass
class PolicyRuleBook:
    """Indexed container for validated deletion policy rules."""
    rules: List[DeletionRuleRecord] = field(default_factory=list)
    rules_by_id: Dict[str, DeletionRuleRecord] = field(default_factory=dict)
    rules_by_category: Dict[str, List[DeletionRuleRecord]] = field(default_factory=dict)

    def get_rule_for_category(self, category: str) -> Optional[DeletionRuleRecord]:
        """Return the primary rule governing the specified data category."""
        category_rules = self.rules_by_category.get(category.strip().upper(), [])
        return category_rules[0] if category_rules else None

    def get_rule(self, rule_id: str) -> Optional[DeletionRuleRecord]:
        """Return rule by exact rule ID."""
        return self.rules_by_id.get(rule_id.strip())

    def categories(self) -> Set[str]:
        """Return all distinct categories present in the loaded rulebook."""
        return set(self.rules_by_category.keys())


class PolicyRuleError(ValueError):
    """Base exception for policy rule validation errors."""
    pass


class InvalidRuleSchemaError(PolicyRuleError):
    """Raised when deletion_rules.csv is missing required columns."""
    pass


class InvalidRequiredActionError(PolicyRuleError):
    """Raised when an unsupported required_action is encountered."""
    pass


class DuplicateRuleIdError(PolicyRuleError):
    """Raised when duplicate rule_id entries exist in deletion_rules.csv."""
    pass


class ContradictoryRuleError(PolicyRuleError):
    """Raised when contradictory rule definitions exist for the same category and condition."""
    pass


class PolicyRuleLoader:
    """Loads and strictly validates deletion_rules.csv."""

    @classmethod
    def load_from_string(cls, csv_text: str) -> PolicyRuleBook:
        """Parse and validate policy rules from raw CSV string content."""
        f = io.StringIO(csv_text)
        return cls._parse_reader(csv.DictReader(f))

    @classmethod
    def load_from_file(cls, file_path: str | Path) -> PolicyRuleBook:
        """Load and validate policy rules from the given CSV path."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Policy rules file not found: {p.resolve()}")

        with open(p, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return cls._parse_reader(reader, source_name=p.name)

    @classmethod
    def _parse_reader(cls, reader: csv.DictReader, source_name: str = "CSV") -> PolicyRuleBook:
        """Internal helper to validate and parse a DictReader of rules."""
        rules: List[DeletionRuleRecord] = []
        rules_by_id: Dict[str, DeletionRuleRecord] = {}
        rules_by_category: Dict[str, List[DeletionRuleRecord]] = {}

        fieldnames = [fn.strip() for fn in (reader.fieldnames or [])]
        for col in REQUIRED_RULE_COLUMNS:
            if col not in fieldnames:
                raise InvalidRuleSchemaError(f"Policy rules CSV missing required column '{col}'. Header: {fieldnames}")

        for row_no, row in enumerate(reader, start=2):
            rule_id = (row.get("rule_id") or "").strip()
            data_category = (row.get("data_category") or "").strip().upper()
            condition = (row.get("condition") or "").strip()
            required_action = (row.get("required_action") or "").strip().upper()

            if not rule_id:
                raise InvalidRuleSchemaError(f"Line {row_no}: Empty rule_id in policy rules CSV")
            if not data_category:
                raise InvalidRuleSchemaError(f"Line {row_no}: Empty data_category for rule '{rule_id}'")
            if not required_action:
                raise InvalidRuleSchemaError(f"Line {row_no}: Empty required_action for rule '{rule_id}'")

            if required_action not in ALLOWED_ACTIONS:
                raise InvalidRequiredActionError(
                    f"Line {row_no}: Unsupported required_action '{required_action}' for rule '{rule_id}'. "
                    f"Must be one of {sorted(list(ALLOWED_ACTIONS))}"
                )

            if rule_id in rules_by_id:
                raise DuplicateRuleIdError(f"Line {row_no}: Duplicate rule_id '{rule_id}' found in policy CSV")

            rule = DeletionRuleRecord(
                rule_id=rule_id,
                data_category=data_category,
                condition=condition,
                required_action=required_action,
            )

            # Check for contradictory rules: same category with identical condition but different required_action
            existing_for_cat = rules_by_category.get(data_category, [])
            for prior_rule in existing_for_cat:
                if prior_rule.condition.lower() == condition.lower() and prior_rule.required_action != required_action:
                    raise ContradictoryRuleError(
                        f"Line {row_no}: Contradictory rule '{rule_id}' conflicts with '{prior_rule.rule_id}' "
                        f"for category '{data_category}' and condition '{condition}'"
                    )

            rules.append(rule)
            rules_by_id[rule_id] = rule
            rules_by_category.setdefault(data_category, []).append(rule)

        logger.debug(f"Loaded {len(rules)} policy rules across {len(rules_by_category)} categories from {source_name}")
        return PolicyRuleBook(
            rules=rules,
            rules_by_id=rules_by_id,
            rules_by_category=rules_by_category,
        )


# Convenient aliases
PolicyTable = PolicyRuleBook
load_policy_table = PolicyRuleLoader.load_from_file
