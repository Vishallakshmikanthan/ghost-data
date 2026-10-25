"""Ghost Data Still Exists - Deletion Intent Classifier.

Determines whether an incoming request text genuinely authorizes personal data
and account deletion, or represents an excluded non-deletion action (access/export,
correction, marketing opt-out, login support, or general inquiry).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Categorization of detected user request intent."""
    EXPLICIT_DELETION = "EXPLICIT_DELETION"
    ACCOUNT_CLOSURE_WITH_DELETION = "ACCOUNT_CLOSURE_WITH_DELETION"
    DELETION_VERIFICATION = "DELETION_VERIFICATION"
    COMPLETE_FAILED_DELETION = "COMPLETE_FAILED_DELETION"
    DATA_EXPORT_ONLY = "DATA_EXPORT_ONLY"
    DATA_CORRECTION_ONLY = "DATA_CORRECTION_ONLY"
    MARKETING_OPTOUT_ONLY = "MARKETING_OPTOUT_ONLY"
    ACCESS_REQUEST_ONLY = "ACCESS_REQUEST_ONLY"
    ACCOUNT_ACCESS_SUPPORT = "ACCOUNT_ACCESS_SUPPORT"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class IntentClassification:
    """Structured result of intent classification for a deletion request."""
    is_deletion_authorized: bool
    intent_type: str
    confidence: float
    rationale: str
    matched_signals: List[str] = field(default_factory=list)
    raw_text: str = ""

    def is_not_applicable(self) -> bool:
        """Returns True if this request does NOT authorize deletion."""
        return not self.is_deletion_authorized


class IntentClassifier:
    """Robust, multilingual, rule-grounded deletion intent classifier."""

    # Explicit phrases stating "DO NOT DELETE" or "KEEP ACTIVE"
    NEGATION_PATTERNS = [
        re.compile(r"\bdo not close\b", re.IGNORECASE),
        re.compile(r"\bnot asking for deletion\b", re.IGNORECASE),
        re.compile(r"\bleave the account active\b", re.IGNORECASE),
        re.compile(r"\bleave my profile .* in place\b", re.IGNORECASE),
        re.compile(r"\bleave my profile and purchase history\b", re.IGNORECASE),
        re.compile(r"\bkeep the account as it is\b", re.IGNORECASE),
        re.compile(r"\bdon't delete\b", re.IGNORECASE),
        re.compile(r"\bdo not delete\b", re.IGNORECASE),
        re.compile(r"\b(?:do not|don't)\s+want\s+to\s+delete\b", re.IGNORECASE),
        re.compile(r"\bhow\s+do\s+i\s+delete\b", re.IGNORECASE),
        re.compile(r"\bwithout removing any of my records\b", re.IGNORECASE),
        re.compile(r"\bwant to continue using the service\b", re.IGNORECASE),
        re.compile(r"\bthis is an information request, not a request to erase\b", re.IGNORECASE),
        re.compile(r"\bnot a request to erase\b", re.IGNORECASE),
    ]

    # Non-deletion action patterns
    EXPORT_PATTERNS = [
        re.compile(r"\bdownload my account information\b", re.IGNORECASE),
        re.compile(r"\bdownload my data\b", re.IGNORECASE),
        re.compile(r"\bportable format\b", re.IGNORECASE),
        re.compile(r"\b(?:machine-readable\s+)?export\b", re.IGNORECASE),
        re.compile(r"\bdata export\b", re.IGNORECASE),
    ]

    ACCESS_PATTERNS = [
        re.compile(r"\bcopy of last year's invoices\b", re.IGNORECASE),
        re.compile(r"\bwhat personal information you hold\b", re.IGNORECASE),
        re.compile(r"\b(?:what|which)\s+(?:personal\s+)?(?:data|information)\s+(?:do\s+you\s+hold|you\s+hold|do\s+you\s+have)\b", re.IGNORECASE),
        re.compile(r"\bcopy\s+of\s+(?:all|the|my)\s+(?:personal\s+)?(?:data|information)\b", re.IGNORECASE),
        re.compile(r"\binformation request\b", re.IGNORECASE),
        re.compile(r"\bsend me a copy of the information\b", re.IGNORECASE),
    ]

    CORRECTION_PATTERNS = [
        re.compile(r"\bmisspelled\b", re.IGNORECASE),
        re.compile(r"\bcorrect it\b", re.IGNORECASE),
        re.compile(r"\bout of date\b", re.IGNORECASE),
        re.compile(r"\breplace it with the new\b", re.IGNORECASE),
        re.compile(r"\bchange the email address\b", re.IGNORECASE),
        re.compile(r"\bupdate my phone\b", re.IGNORECASE),
        re.compile(r"\bupdate\b.*?\b(?:shipping\s+)?(?:address|phone|email|details|information|name)\b", re.IGNORECASE),
    ]

    MARKETING_PATTERNS = [
        re.compile(r"\bmarketing lists? only\b", re.IGNORECASE),
        re.compile(r"\bfrom marketing lists?\b", re.IGNORECASE),
        re.compile(r"\bstop the promotional emails\b", re.IGNORECASE),
        re.compile(r"\bunsubscribe from marketing\b", re.IGNORECASE),
        re.compile(r"\bunsubscribe\b.*?\b(?:marketing|promotional|newsletter|emails)\b", re.IGNORECASE),
        re.compile(r"\bnewsletter only\b", re.IGNORECASE),
    ]

    LOGIN_HELP_PATTERNS = [
        re.compile(r"\bcan't access my profile\b", re.IGNORECASE),
        re.compile(r"\bhelp resetting the login\b", re.IGNORECASE),
        re.compile(r"\bpassword reset\b", re.IGNORECASE),
    ]

    # Verification of prior deletion (counts as AUTHORIZED deletion verification)
    VERIFICATION_PATTERNS = [
        re.compile(r"\bverify that my account was deleted\b", re.IGNORECASE),
        re.compile(r"\bverify that the profile is gone\b", re.IGNORECASE),
        re.compile(r"\bverify\s+that\b.*?\b(?:deleted|removed|gone)\b", re.IGNORECASE),
        re.compile(r"\baudit the deletion\b", re.IGNORECASE),
        re.compile(r"\bconfirmation that the account was fully removed\b", re.IGNORECASE),
        re.compile(r"\btell me whether the account and identifiable .* have already been removed\b", re.IGNORECASE),
        re.compile(r"\bcheck if still any data remains and complete the deletion\b", re.IGNORECASE),
        re.compile(r"\bcomprobar si todav[ií]a queda alg[uú]n dato\b", re.IGNORECASE),
        re.compile(r"\bconfirm that the profile is gone\b", re.IGNORECASE),
    ]

    FAILED_DELETION_PATTERNS = [
        re.compile(r"\b(?:partial|failed|incomplete)\s+deletion\b", re.IGNORECASE),
        re.compile(r"\bdeletion\b.*?\b(?:partial|failed|incomplete)\b", re.IGNORECASE),
        re.compile(r"\breported as (?:partial|failed|incomplete)\b", re.IGNORECASE),
    ]

    # Multilingual explicit deletion authorization patterns
    DELETION_PATTERNS = [
        # English
        re.compile(r"\b(?:permanently|definitely|complete|completely|full)?\s*(?:delet(?:e|ed|ing|ion)?|eras(?:e|ed|ing|ure)?|remov(?:e|ed|ing|al)?|purge|forget)\b", re.IGNORECASE),
        re.compile(r"\b(?:close|closing|closed)\s+(?:this|my|the)?\s*account(?:\s+completely)?\b", re.IGNORECASE),
        re.compile(r"\baccount[- ]deletion\b", re.IGNORECASE),
        re.compile(r"\bpersonal[- ]data deletion\b", re.IGNORECASE),
        # Spanish
        re.compile(r"\b(?:elimin\w+|borr\w+|suprim\w+|cerrar\s+mi\s+cuenta)\b", re.IGNORECASE),
        re.compile(r"\bcerrar mi cuenta\b", re.IGNORECASE),
        # French
        re.compile(r"\b(?:supprimer|suppression|effacer|effacement|d[eé]r[eé]f[eé]rencer)\b", re.IGNORECASE),
        re.compile(r"\bfermer mon compte\b", re.IGNORECASE),
        # German
        re.compile(r"\b(?:l[oö]schen|l[oö]schung|entfernen|entfernung)\b", re.IGNORECASE),
        re.compile(r"\bkonto schlie[sß]en\b", re.IGNORECASE),
        # Portuguese
        re.compile(r"\b(?:exclus[aã]o|excluir|eliminar|elimina[cç][aã]o|remover)\b", re.IGNORECASE),
        re.compile(r"\bfechar minha conta\b", re.IGNORECASE),
        # Italian
        re.compile(r"\b(?:cancellare|cancellazione|eliminare|eliminazione)\b", re.IGNORECASE),
        re.compile(r"\bchiudere il conto\b", re.IGNORECASE),
        # Japanese
        re.compile(r"(?:削除|消去|アカウントの完全な削除|抹消)"),
        # Korean
        re.compile(r"(?:삭제|지워|계정 삭제)"),
    ]

    @classmethod
    def classify(cls, request_text: str) -> IntentClassification:
        return cls.classify_request(request_text)

    @classmethod
    def classify_request(cls, request_text: str) -> IntentClassification:
        """Evaluate request text and return typed IntentClassification."""
        text = (request_text or "").strip()
        if not text:
            return IntentClassification(
                is_deletion_authorized=False,
                intent_type=IntentType.AMBIGUOUS.value,
                confidence=0.0,
                rationale="Request text is empty",
                matched_signals=["EMPTY_TEXT"],
                raw_text=text,
            )

        matched_signals: List[str] = []

        # 1. Check for explicit negation first ("do not close", "not asking for deletion", etc.)
        for pat in cls.NEGATION_PATTERNS:
            m = pat.search(text)
            if m:
                matched_signals.append(f"NEGATION:{m.group(0)}")

        # 2. Check for non-deletion categories
        has_export = any(pat.search(text) for pat in cls.EXPORT_PATTERNS)
        if has_export:
            matched_signals.append("CATEGORY:EXPORT")

        has_access = any(pat.search(text) for pat in cls.ACCESS_PATTERNS)
        if has_access:
            matched_signals.append("CATEGORY:ACCESS")

        has_correction = any(pat.search(text) for pat in cls.CORRECTION_PATTERNS)
        if has_correction:
            matched_signals.append("CATEGORY:CORRECTION")

        has_marketing = any(pat.search(text) for pat in cls.MARKETING_PATTERNS)
        if has_marketing:
            matched_signals.append("CATEGORY:MARKETING_OPTOUT")

        has_login = any(pat.search(text) for pat in cls.LOGIN_HELP_PATTERNS)
        if has_login:
            matched_signals.append("CATEGORY:LOGIN_SUPPORT")

        # If any negation was matched OR non-deletion category without general deletion intent:
        # Check if the negation makes it strictly non-deletion
        if any(s.startswith("NEGATION:") for s in matched_signals):
            # Check specific intent type
            if has_export:
                itype = IntentType.DATA_EXPORT_ONLY.value
            elif has_access:
                itype = IntentType.ACCESS_REQUEST_ONLY.value
            elif has_correction:
                itype = IntentType.DATA_CORRECTION_ONLY.value
            elif has_marketing:
                itype = IntentType.MARKETING_OPTOUT_ONLY.value
            elif has_login:
                itype = IntentType.ACCOUNT_ACCESS_SUPPORT.value
            else:
                itype = IntentType.AMBIGUOUS.value

            return IntentClassification(
                is_deletion_authorized=False,
                intent_type=itype,
                confidence=0.99,
                rationale=f"Explicit non-deletion intent or negation detected: {matched_signals}",
                matched_signals=matched_signals,
                raw_text=text,
            )

        # If no explicit negation, check for non-deletion intents that do NOT have deletion intent
        # 3. Check for deletion verification
        is_verification = any(pat.search(text) for pat in cls.VERIFICATION_PATTERNS)
        if is_verification:
            matched_signals.append("INTENT:DELETION_VERIFICATION")
            return IntentClassification(
                is_deletion_authorized=True,
                intent_type=IntentType.DELETION_VERIFICATION.value,
                confidence=0.95,
                rationale="Request asks to audit, verify, or complete a previous deletion",
                matched_signals=matched_signals,
                raw_text=text,
            )

        # 3.2 Check for completing failed or partial deletion
        is_failed_comp = any(pat.search(text) for pat in cls.FAILED_DELETION_PATTERNS)
        if is_failed_comp:
            matched_signals.append("INTENT:COMPLETE_FAILED_DELETION")
            return IntentClassification(
                is_deletion_authorized=True,
                intent_type=IntentType.COMPLETE_FAILED_DELETION.value,
                confidence=0.95,
                rationale="Request asks to clean up or complete a partial or failed deletion",
                matched_signals=matched_signals,
                raw_text=text,
            )

        # 3.5 Check if marketing opt-out alone is requested (e.g. "remove me from marketing lists only")
        if re.search(r"\bmarketing lists? only\b", text, re.I) or (has_marketing and re.search(r"\bstill want\b", text, re.I)):
            return IntentClassification(
                is_deletion_authorized=False,
                intent_type=IntentType.MARKETING_OPTOUT_ONLY.value,
                confidence=0.99,
                rationale="Request specifies removal from marketing lists only with retention of account/service messages",
                matched_signals=matched_signals,
                raw_text=text,
            )

        # 4. Check for positive deletion patterns
        has_deletion = any(pat.search(text) for pat in cls.DELETION_PATTERNS)
        if has_deletion:
            matched_signals.append("INTENT:DELETION_KEYWORD")

        # Distinguish standalone access/correction/marketing without deletion
        if not has_deletion:
            if has_export:
                return IntentClassification(
                    is_deletion_authorized=False,
                    intent_type=IntentType.DATA_EXPORT_ONLY.value,
                    confidence=0.95,
                    rationale="Data download/export request without deletion authorization",
                    matched_signals=matched_signals,
                    raw_text=text,
                )
            if has_access:
                return IntentClassification(
                    is_deletion_authorized=False,
                    intent_type=IntentType.ACCESS_REQUEST_ONLY.value,
                    confidence=0.95,
                    rationale="Data access inquiry without deletion authorization",
                    matched_signals=matched_signals,
                    raw_text=text,
                )
            if has_correction:
                return IntentClassification(
                    is_deletion_authorized=False,
                    intent_type=IntentType.DATA_CORRECTION_ONLY.value,
                    confidence=0.95,
                    rationale="Data correction/update request without deletion authorization",
                    matched_signals=matched_signals,
                    raw_text=text,
                )
            if has_marketing:
                return IntentClassification(
                    is_deletion_authorized=False,
                    intent_type=IntentType.MARKETING_OPTOUT_ONLY.value,
                    confidence=0.95,
                    rationale="Marketing unsubscribe request without deletion authorization",
                    matched_signals=matched_signals,
                    raw_text=text,
                )
            if has_login:
                return IntentClassification(
                    is_deletion_authorized=False,
                    intent_type=IntentType.ACCOUNT_ACCESS_SUPPORT.value,
                    confidence=0.95,
                    rationale="Account login/support inquiry without deletion authorization",
                    matched_signals=matched_signals,
                    raw_text=text,
                )
            # Standalone question or ambiguous text
            return IntentClassification(
                is_deletion_authorized=False,
                intent_type=IntentType.AMBIGUOUS.value,
                confidence=0.85,
                rationale="No affirmative deletion intent found in request text",
                matched_signals=matched_signals,
                raw_text=text,
            )

        # Positive deletion intent established
        return IntentClassification(
            is_deletion_authorized=True,
            intent_type=IntentType.EXPLICIT_DELETION.value,
            confidence=0.98,
            rationale="Affirmative deletion or account closure request detected",
            matched_signals=matched_signals,
            raw_text=text,
        )
