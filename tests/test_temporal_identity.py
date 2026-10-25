"""Tests for audit_at temporal filtering, cutoff boundaries, and integration flows."""

import unittest
from pathlib import Path

from code.ingestion import (
    EvidenceStore,
    build_evidence_store,
    get_temporally_valid_evidence,
    load_dataset,
    query_evidence,
    resolve_subject,
)
from code.schemas import DeletionRequest, EvidenceClaim, EvidenceDocument


class TestTemporalIdentity(unittest.TestCase):
    """Test strict temporal cutoff enforcement and candidate query integration."""

    def setUp(self):
        self.store = EvidenceStore()

        # Document 1: captured 2026-08-01 (before cutoff)
        self.doc_early = EvidenceDocument(
            file_id="ev_early",
            file_path="evidence/logs/early.log",
            origin="log_service",
            captured_at="2026-08-01T12:00:00Z",
            file_type=".log",
        )
        self.store.add_document(self.doc_early)

        # Document 2: captured 2026-09-01 (after cutoff)
        self.doc_late = EvidenceDocument(
            file_id="ev_late",
            file_path="evidence/logs/late.log",
            origin="log_service",
            captured_at="2026-09-01T12:00:00Z",
            file_type=".log",
        )
        self.store.add_document(self.doc_late)

        # Claim 1: in early document, event timestamp 2026-08-01
        self.claim1 = EvidenceClaim(
            evidence_id="ev_early",
            source_type="file",
            timestamp="2026-08-01T12:00:00Z",
            system_id="account_service",
            resource_id="account_101",
            observed_state="PRESENT",
            identifiers_found=["user_10", "alice@example.com"],
        )
        self.store.add_claim(self.claim1)

        # Claim 2: in late document, event timestamp 2026-09-01
        self.claim2 = EvidenceClaim(
            evidence_id="ev_late",
            source_type="file",
            timestamp="2026-09-01T12:00:00Z",
            system_id="account_service",
            resource_id="account_102",
            observed_state="PRESENT",
            identifiers_found=["user_10", "alice@example.com"],
        )
        self.store.add_claim(self.claim2)

        # Cutoff: 2026-08-15
        self.audit_at = "2026-08-15T00:00:00Z"

    def test_claims_at_or_before_cutoff_eligible(self):
        filtered = self.store.filter_temporally([self.claim1, self.claim2], self.audit_at)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].resource_id, "account_101")

    def test_claims_after_cutoff_excluded(self):
        filtered = self.store.filter_temporally([self.claim2], self.audit_at)
        self.assertEqual(len(filtered), 0)

    def test_document_captured_after_cutoff_ineligible(self):
        # Even if claim timestamp claimed an earlier date, if the file was captured after cutoff, it's excluded
        late_file_claim = EvidenceClaim(
            evidence_id="ev_late",
            source_type="file",
            timestamp="2026-08-01T00:00:00Z",  # claimed earlier date
            system_id="account_service",
            resource_id="account_103",
            observed_state="PRESENT",
            identifiers_found=["user_10"],
        )
        self.store.add_claim(late_file_claim)

        filtered = self.store.filter_temporally([late_file_claim], self.audit_at)
        self.assertEqual(len(filtered), 0)

    def test_get_temporally_valid_document(self):
        doc1 = get_temporally_valid_evidence(self.store, "ev_early", self.audit_at)
        self.assertIsNotNone(doc1)
        self.assertEqual(doc1.file_id, "ev_early")

        doc2 = get_temporally_valid_evidence(self.store, "ev_late", self.audit_at)
        self.assertIsNone(doc2)


class TestRealDatasetIntegration(unittest.TestCase):
    """Integration test loading participant-facing sample data without generating predictions."""

    def test_sample_dataset_resolution_and_evidence_query(self):
        dataset_dir = Path("dataset")
        if not dataset_dir.exists():
            self.skipTest("dataset/ directory not present in current working environment")

        # 1. Load dataset bundle
        bundle = load_dataset(dataset_dir, requests_file="sample_deletion_requests.csv")
        self.assertGreater(len(bundle.deletion_requests), 0)
        self.assertGreater(len(bundle.user_identities), 0)

        # 2. Pick a sample request: e.g. first request
        req = list(bundle.deletion_requests.values())[0]

        # 3. Resolve subject identity graph
        graph = resolve_subject(req, bundle)
        self.assertIn(graph.match_classification, ("EXACT_CURRENT", "HISTORICALLY_LINKED", "UNRESOLVED", "AMBIGUOUS"))
        self.assertIn(req.email.lower(), graph.all_identifiers())

        # 4. Check candidate discovery on EvidenceStore
        store = EvidenceStore()
        # Ingest activity and audit logs
        store.ingest_system_activity(bundle.system_activities)
        store.ingest_user_audit_logs(bundle.user_audit_logs)

        candidates = query_evidence(graph, req.audit_at, store)
        # Should return a list of EvidenceClaim instances filtered by audit_at
        self.assertIsInstance(candidates, list)
        for c in candidates:
            self.assertIsInstance(c, EvidenceClaim)


if __name__ == "__main__":
    unittest.main()
