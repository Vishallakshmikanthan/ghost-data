"""Tests for dataset CSV loaders, manifest path validation, and normalization."""

import tempfile
import unittest
from pathlib import Path

from code.ingestion.csv_loader import (
    load_dataset,
    load_deletion_requests,
    load_deletion_rules,
    load_evidence_manifest_records,
    load_system_activity,
    load_user_audit_log,
    load_user_identities,
)
from code.ingestion.manifest import (
    compute_file_hash,
    load_manifest,
    resolve_evidence_path,
)
from code.ingestion.normalization import (
    compare_timestamps,
    format_iso_timestamp,
    is_at_or_before,
    normalize_email,
    normalize_email_hash,
    normalize_name,
    normalize_phone,
    normalize_resource_id,
    normalize_user_id,
    parse_iso_timestamp,
)


class TestNormalization(unittest.TestCase):
    """Test identifier and timestamp normalization functions."""

    def test_normalize_email(self):
        self.assertEqual(normalize_email("  User.Name@Example.COM  "), "user.name@example.com")
        self.assertEqual(normalize_email(""), "")
        self.assertEqual(normalize_email(None), "")
        # Preserve distinct plus addresses
        self.assertEqual(normalize_email("test+tag@domain.com"), "test+tag@domain.com")

    def test_normalize_phone(self):
        # International formatting
        p1, amb1 = normalize_phone("+1 (202) 555-0103")
        self.assertEqual(p1, "+12025550103")
        self.assertFalse(amb1)

        # Ambiguous short digit string
        p2, amb2 = normalize_phone("1234")
        self.assertTrue(amb2)

        # None / empty
        p3, amb3 = normalize_phone(None)
        self.assertEqual(p3, "")
        self.assertFalse(amb3)

    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Jane    Doe  "), "Jane Doe")
        self.assertEqual(normalize_name("José   Silva"), "José Silva")
        self.assertEqual(normalize_name(None), "")

    def test_normalize_email_hash(self):
        h = "  D4E1F59ABCF9A1E0CFDC7C27AD6C07ED4FF88B326A16ED5EED5EC6BC31618C93  "
        self.assertEqual(normalize_email_hash(h), h.strip().lower())
        self.assertEqual(normalize_email_hash(""), "")

    def test_normalize_user_id_and_resource(self):
        self.assertEqual(normalize_user_id("  user_42  "), "user_42")
        self.assertEqual(normalize_resource_id("  account_101  "), "account_101")

    def test_parse_iso_timestamp(self):
        dt = parse_iso_timestamp("2026-09-15T11:59:39Z")
        self.assertIsNotNone(dt)
        self.assertEqual(format_iso_timestamp(dt), "2026-09-15T11:59:39Z")

        # Malformed timestamp returns None (no silent fallback to now)
        self.assertIsNone(parse_iso_timestamp("invalid-date"))
        self.assertIsNone(parse_iso_timestamp(""))
        self.assertIsNone(parse_iso_timestamp(None))

    def test_compare_and_filter_timestamps(self):
        ts_early = "2026-08-01T10:00:00Z"
        ts_late = "2026-09-01T10:00:00Z"

        self.assertEqual(compare_timestamps(ts_early, ts_late), -1)
        self.assertEqual(compare_timestamps(ts_late, ts_early), 1)
        self.assertEqual(compare_timestamps(ts_early, ts_early), 0)
        self.assertIsNone(compare_timestamps("bad", ts_late))

        self.assertTrue(is_at_or_before(ts_early, ts_late))
        self.assertFalse(is_at_or_before(ts_late, ts_early))
        self.assertTrue(is_at_or_before(ts_early, ts_early))


class TestCsvLoaders(unittest.TestCase):
    """Test CSV loading with quoted strings, Unicode, missing columns, and multiline content."""

    def test_load_deletion_requests_with_multiline_unicode(self):
        csv_content = (
            "case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
            'case_01,josé@domain.org,2026-07-01T00:00:00Z,COMPLETE,2026-07-02T00:00:00Z,2026-07-03T00:00:00Z,"Please delete\nmy data, thank you!"\n'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            requests, errors = load_deletion_requests(temp_path)
            self.assertEqual(len(requests), 1)
            self.assertEqual(len(errors), 0)
            req = requests["case_01"]
            self.assertEqual(req.email, "josé@domain.org")
            self.assertIn("Please delete\nmy data", req.request_text)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_load_deletion_requests_missing_required_column(self):
        csv_content = "case_id,email,requested_at\ncase_01,test@domain.com,2026-01-01T00:00:00Z\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                load_deletion_requests(temp_path)
            self.assertIn("missing required columns", str(ctx.exception))
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_load_user_identities_and_indexing(self):
        csv_content = (
            "user_id,name,email,phone,email_hash,updated_at\n"
            "user_10,Alice Smith,Alice.S@Example.com,+1 202 555 0199,d4e1f59abcf9a1e0cfdc7c27ad6c07ed4ff88b326a16ed5eed5ec6bc31618c93,2026-05-01T12:00:00Z\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            identities, errors = load_user_identities(temp_path)
            self.assertEqual(len(identities), 1)
            self.assertEqual(len(errors), 0)
            u = identities["user_10"]
            self.assertEqual(u.normalized_email, "alice.s@example.com")
            self.assertEqual(u.normalized_phone, "+12025550199")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_load_user_audit_log(self):
        csv_content = (
            "audit_id,user_id,event_time,field_name,old_value,new_value\n"
            "audit_01,user_10,2025-06-01T10:00:00Z,email,old.alice@example.com,alice.s@example.com\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            records, errors = load_user_audit_log(temp_path)
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec.normalized_old, "old.alice@example.com")
            self.assertEqual(rec.normalized_new, "alice.s@example.com")
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestManifestResolution(unittest.TestCase):
    """Test evidence manifest resolution, path security, and integrity hashing."""

    def test_resolve_evidence_path_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target = base / "evidence" / "logs" / "sample.log"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("sample content", encoding="utf-8")

            resolved = resolve_evidence_path(base, "evidence/logs/sample.log")
            self.assertEqual(resolved, target.resolve())

    def test_resolve_evidence_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaises(ValueError) as ctx:
                resolve_evidence_path(base, "../../../secret.txt")
            self.assertIn("Path traversal detected", str(ctx.exception))

    def test_load_manifest_with_missing_and_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            manifest_csv = base / "evidence_files.csv"
            manifest_csv.write_text(
                "file_id,file_path,origin,captured_at\n"
                "ev_01,evidence/missing.log,test_source,2026-09-01T00:00:00Z\n"
                "ev_01,evidence/duplicate.log,test_source,2026-09-01T00:00:00Z\n",
                encoding="utf-8",
            )

            docs, errors = load_manifest(manifest_csv, base)
            # ev_01 is registered but marked FAILED due to missing file
            self.assertIn("ev_01", docs)
            self.assertEqual(docs["ev_01"].extraction_status, "FAILED")
            # Duplicate error reported
            dup_errs = [e for e in errors if "Duplicate file_id" in e["error"]]
            self.assertEqual(len(dup_errs), 1)


if __name__ == "__main__":
    unittest.main()
