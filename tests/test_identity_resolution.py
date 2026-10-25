"""Tests for temporal identity resolution, graph construction, and ambiguity handling."""

import unittest

from code.identity.identity_graph import TemporalIdentityGraph
from code.identity.identity_resolver import IdentityResolver
from code.ingestion.csv_loader import DatasetBundle
from code.schemas import (
    DeletionRequest,
    UserAuditRecord,
    UserIdentityRecord,
)


class TestIdentityResolution(unittest.TestCase):
    """Test identity resolution against snapshots, historical logs, and conflicts."""

    def setUp(self):
        self.bundle = DatasetBundle()

        # Alice: user_01 (current: alice.smith@gmail.com, historical: alice.s@yahoo.com)
        self.u1 = UserIdentityRecord(
            user_id="user_01",
            name="Alice Smith",
            email="alice.smith@gmail.com",
            phone="+1 202 555 0101",
            email_hash="hash_alice_01",
            updated_at="2026-05-01T00:00:00Z",
            normalized_email="alice.smith@gmail.com",
            normalized_phone="+12025550101",
        )
        self.bundle.user_identities["user_01"] = self.u1
        self.bundle.identities_by_email["alice.smith@gmail.com"] = [self.u1]

        # Audit log for user_01: changed email from alice.s@yahoo.com to alice.smith@gmail.com
        self.audit1 = UserAuditRecord(
            audit_id="audit_101",
            user_id="user_01",
            event_time="2025-06-15T10:00:00Z",
            field_name="email",
            old_value="alice.s@yahoo.com",
            new_value="alice.smith@gmail.com",
            normalized_old="alice.s@yahoo.com",
            normalized_new="alice.smith@gmail.com",
        )
        self.bundle.user_audit_logs.append(self.audit1)
        self.bundle.audit_by_user_id["user_01"] = [self.audit1]
        self.bundle.audit_by_old_value["alice.s@yahoo.com"] = [self.audit1]

        # Bob: user_02 (shares first name, different user)
        self.u2 = UserIdentityRecord(
            user_id="user_02",
            name="Alice Jones",
            email="alice.jones@outlook.com",
            phone="+1 202 555 0102",
            email_hash="hash_alice_02",
            updated_at="2026-04-01T00:00:00Z",
            normalized_email="alice.jones@outlook.com",
            normalized_phone="+12025550102",
        )
        self.bundle.user_identities["user_02"] = self.u2
        self.bundle.identities_by_email["alice.jones@outlook.com"] = [self.u2]

        self.resolver = IdentityResolver(self.bundle)

    def test_current_email_resolves_exact(self):
        req = DeletionRequest(
            case_id="case_01",
            email="Alice.Smith@Gmail.com",
            requested_at="2026-07-01T00:00:00Z",
            reported_status="COMPLETE",
            reported_at="2026-07-02T00:00:00Z",
            audit_at="2026-07-03T00:00:00Z",
            request_text="Delete my data",
        )
        graph = self.resolver.resolve_subject(req)
        self.assertEqual(graph.match_classification, "EXACT_CURRENT")
        self.assertEqual(graph.user_id, "user_01")
        self.assertIn("alice.smith@gmail.com", graph.emails)
        self.assertIn("alice.s@yahoo.com", graph.emails)  # Historical email included
        self.assertIn("user_01", graph.tombstone_ids)      # Tombstone ID included

    def test_historical_email_resolves_to_same_subject(self):
        req = DeletionRequest(
            case_id="case_02",
            email="alice.s@yahoo.com",  # Old historical email
            requested_at="2026-07-01T00:00:00Z",
            reported_status="COMPLETE",
            reported_at="2026-07-02T00:00:00Z",
            audit_at="2026-07-03T00:00:00Z",
            request_text="Delete my account",
        )
        graph = self.resolver.resolve_subject(req)
        self.assertEqual(graph.match_classification, "HISTORICALLY_LINKED")
        self.assertEqual(graph.user_id, "user_01")
        self.assertEqual(graph.primary_email, "alice.smith@gmail.com")
        self.assertIn("audit_id:audit_101", str(graph.identity_edges))

    def test_unlinked_email_remains_unresolved(self):
        req = DeletionRequest(
            case_id="case_03",
            email="stranger@unknown.com",
            requested_at="2026-07-01T00:00:00Z",
            reported_status="COMPLETE",
            reported_at="2026-07-02T00:00:00Z",
            audit_at="2026-07-03T00:00:00Z",
            request_text="Delete my account",
        )
        graph = self.resolver.resolve_subject(req)
        self.assertEqual(graph.match_classification, "UNRESOLVED")
        self.assertIsNone(graph.user_id)
        self.assertIn("stranger@unknown.com", graph.emails)

    def test_different_users_with_similar_names_remain_separate(self):
        # Alice Smith (user_01) and Alice Jones (user_02) both have first name Alice
        req1 = DeletionRequest(
            case_id="c1", email="alice.smith@gmail.com", requested_at="",
            reported_status="", reported_at="", audit_at="", request_text=""
        )
        req2 = DeletionRequest(
            case_id="c2", email="alice.jones@outlook.com", requested_at="",
            reported_status="", reported_at="", audit_at="", request_text=""
        )
        g1 = self.resolver.resolve_subject(req1)
        g2 = self.resolver.resolve_subject(req2)

        self.assertEqual(g1.user_id, "user_01")
        self.assertEqual(g2.user_id, "user_02")
        self.assertNotIn("alice.jones@outlook.com", g1.emails)
        self.assertNotIn("alice.smith@gmail.com", g2.emails)

    def test_conflicting_identities_report_ambiguity(self):
        # Add conflict: shared email between two different users
        shared_email = "shared.clash@domain.com"
        conflict_u1 = UserIdentityRecord(
            user_id="user_99", name="Conf 1", email=shared_email, phone="",
            email_hash="", updated_at="", normalized_email=shared_email
        )
        conflict_u2 = UserIdentityRecord(
            user_id="user_100", name="Conf 2", email=shared_email, phone="",
            email_hash="", updated_at="", normalized_email=shared_email
        )
        self.bundle.identities_by_email[shared_email] = [conflict_u1, conflict_u2]

        req = DeletionRequest(
            case_id="c_clash", email=shared_email, requested_at="",
            reported_status="", reported_at="", audit_at="", request_text=""
        )
        graph = self.resolver.resolve_subject(req)
        self.assertEqual(graph.match_classification, "AMBIGUOUS")
        self.assertIsNone(graph.user_id)
        self.assertGreater(len(graph.ambiguities), 0)


if __name__ == "__main__":
    unittest.main()
