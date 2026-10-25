"""Tests for isolated SQL snapshot extraction, security validation, and replication drift."""

import tempfile
import unittest
from pathlib import Path

from code.ingestion.sql_snapshots import (
    SqlSnapshotExtractor,
    inspect_sql_metadata,
    validate_sql_script,
)
from code.schemas import EvidenceDocument


class TestSqlSnapshots(unittest.TestCase):
    """Test SQL snapshot processing in an isolated in-memory environment."""

    def test_inspect_sql_metadata(self):
        sql = (
            "-- Ghost Data Still Exists: point-in-time database snapshot\n"
            "-- Dialect: SQLite-compatible SQL\n"
            "-- Captured at: 2026-09-21T10:00:00Z\n"
            "-- Database topology: read_replica\n"
        )
        meta = inspect_sql_metadata(sql)
        self.assertEqual(meta["captured_at"], "2026-09-21T10:00:00Z")
        self.assertEqual(meta["topology"], "read_replica")

    def test_dangerous_sql_rejection(self):
        dangerous_sql = "ATTACH DATABASE 'malicious.db' AS evil; CREATE TABLE evil.t(x);"
        with self.assertRaises(ValueError) as ctx:
            validate_sql_script(dangerous_sql)
        self.assertIn("Prohibited SQL operation", str(ctx.exception))

    def test_isolated_sql_extraction_and_drift_detection(self):
        sql_content = (
            "-- Captured at: 2026-09-20T12:00:00Z\n"
            "-- Database topology: read_replica\n"
            "CREATE TABLE customer_profiles_primary (resource_id TEXT PRIMARY KEY, user_id TEXT, email TEXT, updated_at TEXT);\n"
            "CREATE TABLE customer_profiles_replica (resource_id TEXT PRIMARY KEY, user_id TEXT, email TEXT, updated_at TEXT);\n"
            "INSERT INTO customer_profiles_primary VALUES ('account_01', 'user_10', 'alice@test.com', '2026-09-20T12:00:00Z');\n"
            "INSERT INTO customer_profiles_replica VALUES ('account_01', 'user_10', 'alice@test.com', '2026-09-20T12:00:00Z');\n"
            "-- Stale replica row that was deleted from primary (drift!)\n"
            "INSERT INTO customer_profiles_replica VALUES ('account_02', 'user_20', 'bob@test.com', '2026-09-18T10:00:00Z');\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".sql") as f:
            f.write(sql_content)
            temp_path = f.name

        try:
            doc = EvidenceDocument(
                file_id="ev_sql",
                file_path="evidence/databases/replica.sql",
                origin="database_service",
                captured_at="2026-09-20T12:00:00Z",
                file_type=".sql",
            )
            claims = SqlSnapshotExtractor.extract(doc, Path(temp_path))
            self.assertEqual(len(claims), 3)

            # Drift detected because account_02 is in replica but absent from primary
            self.assertTrue(doc.metadata.get("replication_drift_detected"))
            self.assertEqual(doc.extraction_status, "SUCCESS")

            # Check claim fields
            c2 = [c for c in claims if c.resource_id == "account_02"][0]
            self.assertIn("user_20", c2.identifiers_found)
            self.assertIn("bob@test.com", c2.identifiers_found)
            self.assertEqual(c2.extraction_method, "sqlite_isolated_query")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_file_remains_unmutated(self):
        sql_content = "CREATE TABLE t(x TEXT); INSERT INTO t VALUES('test');"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".sql") as f:
            f.write(sql_content)
            temp_path = f.name

        try:
            path = Path(temp_path)
            content_before = path.read_bytes()
            doc = EvidenceDocument(
                file_id="ev_test",
                file_path="evidence/databases/test.sql",
                origin="test",
                captured_at="2026-09-01T00:00:00Z",
                file_type=".sql",
            )
            SqlSnapshotExtractor.extract(doc, path)
            content_after = path.read_bytes()
            self.assertEqual(content_before, content_after)
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
