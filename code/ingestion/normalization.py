"""Ghost Data Still Exists - Identifier Normalization & Timestamp Utilities.

Provides conservative normalization for emails, phone numbers, names,
hashes, resource IDs, and timezone-aware ISO-8601 timestamps.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Tuple


def normalize_email(email: Optional[str]) -> str:
    """Normalize an email address conservatively.

    Strips whitespace and converts ASCII to lowercase.
    Does not drop dots or plus-aliases, preserving distinct addresses.
    """
    if not email:
        return ""
    return email.strip().lower()


def normalize_phone(phone: Optional[str]) -> Tuple[str, bool]:
    """Normalize a phone number while tracking ambiguity.

    Removes formatting delimiters (spaces, hyphens, parentheses, dots),
    preserves leading '+', and checks whether the resulting string represents
    a plausible international or national phone number.

    Returns:
        (normalized_phone, is_ambiguous)
    """
    if not phone:
        return "", False

    raw = phone.strip()
    if not raw:
        return "", False

    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return raw, True

    is_ambiguous = False
    # Standard phone numbers globally have between 7 and 15 digits (E.164 recommendation)
    if len(digits) < 7 or len(digits) > 15:
        is_ambiguous = True

    cleaned = f"+{digits}" if has_plus else digits
    return cleaned, is_ambiguous


def normalize_name(name: Optional[str]) -> str:
    """Normalize a person or account name.

    Strips leading and trailing whitespace and collapses repeated internal spaces.
    Preserves case and special characters (accents, hyphens).
    """
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip())


def normalize_email_hash(hash_val: Optional[str]) -> str:
    """Normalize an email hash value.

    Strips whitespace and lowercases hex characters.
    """
    if not hash_val:
        return ""
    return hash_val.strip().lower()


def normalize_user_id(user_id: Optional[str]) -> str:
    """Normalize a stable internal user identifier.

    Strips surrounding whitespace, preserving exact case.
    """
    if not user_id:
        return ""
    return user_id.strip()


def normalize_resource_id(resource_id: Optional[str]) -> str:
    """Normalize a resource or record identifier (e.g., account_005, search_123)."""
    if not resource_id:
        return ""
    return resource_id.strip()


def parse_iso_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime.

    Handles 'Z' suffixes and offset strings. Returns None if input is missing or malformed.
    Does NOT silently substitute current time.
    """
    if not ts:
        return None
    raw = ts.strip()
    if not raw:
        return None

    try:
        # Replace terminal 'Z' with '+00:00' for standard fromisoformat compatibility
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            # Assume UTC if timezone was omitted, but make it explicitly aware
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def format_iso_timestamp(dt: Optional[datetime]) -> str:
    """Format a timezone-aware UTC datetime into a standard ISO-8601 string ending in 'Z'."""
    if dt is None:
        return ""
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def compare_timestamps(ts1: Optional[str], ts2: Optional[str]) -> Optional[int]:
    """Compare two ISO timestamp strings.

    Returns:
        -1 if ts1 < ts2
         0 if ts1 == ts2
         1 if ts1 > ts2
        None if either timestamp is missing or malformed (preserving uncertainty)
    """
    dt1 = parse_iso_timestamp(ts1)
    dt2 = parse_iso_timestamp(ts2)
    if dt1 is None or dt2 is None:
        return None
    if dt1 < dt2:
        return -1
    elif dt1 > dt2:
        return 1
    return 0


def is_at_or_before(candidate_ts: Optional[str], cutoff_ts: Optional[str], default_if_missing: bool = False) -> bool:
    """Check if candidate_ts is chronologically at or before cutoff_ts.

    If candidate_ts or cutoff_ts is invalid/missing, returns default_if_missing.
    """
    cmp = compare_timestamps(candidate_ts, cutoff_ts)
    if cmp is None:
        return default_if_missing
    return cmp <= 0
