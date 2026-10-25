"""Ghost Data Still Exists - Comprehensive Phase 3 Reconstruction Test Suite.

Adversarial and scenario tests covering:
- Event sequencing, recreation, and failed operations
- Snapshot reconciliation and multi-replica drift detection
- Strict audit-cutoff temporal filtering and source capture boundaries
- Data-flow recreation risks and backup restore pathways
- Cyclic dependency graph handling
- Attribution, provenance, determinism, and contract preservation
"""

import unittest
from pathlib import Path
from typing import Dict, List

from code.reconstruction.dependency_graph import ResourceDependencyGraph
from code.reconstruction.event_normalizer import EventNormalizer
from code.reconstruction.flow_analyzer import FlowAnalyzer
from code.reconstruction.models import (
    DataFlowAssessment,
    EvidenceStrength,
    FlowState,
    NormalizedEvent,
    ReconstructedState,
    ReconstructionReport,
    ResourceSnapshot,
    ResourceStateAssessment,
    RestoreRiskAssessment,
    RestoreState,
)
from code.reconstruction.resource_ledger import ResourceLedger
from code.reconstruction.snapshot_reconciler import SnapshotReconciler
from code.reconstruction.state_machine import ResourceStateMachine
from code.reconstruction.timeline_builder import TimelineBuilder
from code.schemas import EvidenceClaim, EvidenceDocument, SystemActivityRecord, UserAuditRecord


class TestEventSequencing(unittest.TestCase):
    """Test timeline event sequencing and state transitions."""

    def test_record_present_then_deleted(self):
        timeline = [
            NormalizedEvent(
                event_id="e1",
                event_type="OBSERVE_RECORD",
                system_id="account_service",
                resource_id="account_100",
                timestamp="2026-08-01T10:00:00Z",
                attributes={"observed_state": "PRESENT"},
                evidence_id="ev_01",
            ),
            NormalizedEvent(
                event_id="e2",
                event_type="DELETE_RECORD",
                system_id="account_service",
                resource_id="account_100",
                timestamp="2026-08-02T12:00:00Z",
                reported_outcome="SUCCESS",
                evidence_id="act_01",
            ),
        ]
        assess = ResourceStateMachine.reconstruct_resource_state(
            resource_id="account_100",
            system_id="account_service",
            subject_id="user_10",
            timeline=timeline,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertEqual(assess.state, ReconstructedState.ABSENT.value)
        self.assertEqual(assess.confidence, EvidenceStrength.DERIVED_EVENT_CHAIN.value)
        self.assertIn("act_01", assess.supporting_evidence_ids)

    def test_record_deleted_then_recreated(self):
        timeline = [
            NormalizedEvent(
                event_id="e1",
                event_type="DELETE_RECORD",
                system_id="account_service",
                resource_id="account_101",
                timestamp="2026-08-01T10:00:00Z",
                reported_outcome="SUCCESS",
                evidence_id="act_01",
            ),
            NormalizedEvent(
                event_id="e2",
                event_type="SYNC_RECORD",
                system_id="account_service",
                resource_id="account_101",
                timestamp="2026-08-03T14:00:00Z",
                reported_outcome="SUCCESS",
                evidence_id="act_02",
            ),
        ]
        assess = ResourceStateMachine.reconstruct_resource_state(
            resource_id="account_101",
            system_id="account_service",
            subject_id="user_11",
            timeline=timeline,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertEqual(assess.state, ReconstructedState.PRESENT.value)
        self.assertTrue(any("recreated" in note.lower() for note in assess.uncertainty_notes))

    def test_deletion_attempted_but_failed(self):
        timeline = [
            NormalizedEvent(
                event_id="e1",
                event_type="OBSERVE_RECORD",
                system_id="search_service",
                resource_id="search_200",
                timestamp="2026-08-01T10:00:00Z",
                attributes={"observed_state": "PRESENT"},
                evidence_id="ev_01",
            ),
            NormalizedEvent(
                event_id="e2",
                event_type="DELETE_RECORD",
                system_id="search_service",
                resource_id="search_200",
                timestamp="2026-08-02T10:00:00Z",
                reported_outcome="FAILED",
                evidence_id="act_failed",
            ),
        ]
        assess = ResourceStateMachine.reconstruct_resource_state(
            resource_id="search_200",
            system_id="search_service",
            subject_id="user_12",
            timeline=timeline,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertEqual(assess.state, ReconstructedState.PRESENT.value)
        self.assertIn("act_failed", assess.contradicting_evidence_ids)

    def test_timestamp_collision_detection(self):
        events = [
            NormalizedEvent(
                event_id="e1",
                event_type="DELETE_RECORD",
                system_id="account_service",
                resource_id="account_300",
                timestamp="2026-08-01T10:00:00Z",
            ),
            NormalizedEvent(
                event_id="e2",
                event_type="OBSERVE_RECORD",
                system_id="account_service",
                resource_id="account_300",
                timestamp="2026-08-01T10:00:00Z",
            ),
        ]
        conflicts = TimelineBuilder.detect_timestamp_conflicts(events)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("Timestamp collision", conflicts[0])


class TestSnapshotReconciliation(unittest.TestCase):
    """Test multi-replica reconciliation and replication drift detection."""

    def test_primary_absent_replica_present_drift(self):
        snaps = [
            ResourceSnapshot(
                resource_id="account_500",
                system_id="account_service",
                snapshot_time="2026-08-01T10:00:00Z",
                evidence_id="ev_primary",
                observed_state="ABSENT",
                is_replica=False,
                replica_type="primary",
            ),
            ResourceSnapshot(
                resource_id="account_500",
                system_id="account_service",
                snapshot_time="2026-08-01T10:00:00Z",
                evidence_id="ev_replica",
                observed_state="PRESENT",
                is_replica=True,
                replica_type="regional_replica",
            ),
        ]
        result = SnapshotReconciler.reconcile_resource_snapshots(
            resource_id="account_500",
            system_id="account_service",
            snapshots=snaps,
            subsequent_events=[],
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertTrue(result.drift_detected)
        self.assertEqual(result.reconciled_state, ReconstructedState.PRESENT.value)
        self.assertIn("regional_replica", result.replica_states)
        self.assertIn("ev_replica", result.supporting_evidence_ids)

    def test_stale_replica_subsequently_deleted(self):
        snaps = [
            ResourceSnapshot(
                resource_id="account_501",
                system_id="account_service",
                snapshot_time="2026-08-01T10:00:00Z",
                evidence_id="ev_snap",
                observed_state="PRESENT",
                is_replica=False,
                replica_type="primary",
            ),
        ]
        events = [
            NormalizedEvent(
                event_id="del_ev",
                event_type="DELETE_RECORD",
                system_id="account_service",
                resource_id="account_501",
                timestamp="2026-08-02T12:00:00Z",
                reported_outcome="SUCCESS",
            ),
        ]
        result = SnapshotReconciler.reconcile_resource_snapshots(
            resource_id="account_501",
            system_id="account_service",
            snapshots=snaps,
            subsequent_events=events,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertFalse(result.drift_detected)
        self.assertEqual(result.reconciled_state, ReconstructedState.ABSENT.value)
        self.assertEqual(result.confidence, EvidenceStrength.DERIVED_EVENT_CHAIN.value)


class TestTemporalAdmissibility(unittest.TestCase):
    """Test strict audit cutoff enforcement and post-cutoff exclusion."""

    def test_post_cutoff_event_excluded(self):
        events = [
            NormalizedEvent(
                event_id="e_valid",
                event_type="OBSERVE_RECORD",
                system_id="account_service",
                resource_id="acc_1",
                timestamp="2026-08-01T10:00:00Z",
            ),
            NormalizedEvent(
                event_id="e_late",
                event_type="DELETE_RECORD",
                system_id="account_service",
                resource_id="acc_1",
                timestamp="2026-08-10T10:00:00Z",  # After cutoff
            ),
        ]
        ordered = TimelineBuilder.filter_and_order(events, audit_at="2026-08-05T00:00:00Z")
        self.assertEqual(len(ordered), 1)
        self.assertEqual(ordered[0].event_id, "e_valid")

    def test_file_captured_after_cutoff_excluded(self):
        claim = EvidenceClaim(
            evidence_id="ev_late_doc",
            source_type="file",
            timestamp="2026-08-01T10:00:00Z",  # Event claimed early
            system_id="account_service",
            resource_id="acc_2",
            observed_state="PRESENT",
        )
        late_doc = EvidenceDocument(
            file_id="ev_late_doc",
            file_path="evidence/records/late.json",
            origin="test",
            captured_at="2026-08-15T12:00:00Z",  # File captured late!
        )
        norm_ev = EventNormalizer.normalize_claim(claim, late_doc, audit_at="2026-08-05T00:00:00Z")
        self.assertFalse(norm_ev.is_temporally_eligible)

        ordered = TimelineBuilder.filter_and_order([norm_ev], audit_at="2026-08-05T00:00:00Z")
        self.assertEqual(len(ordered), 0)


class TestDataFlowAndRestoreRisks(unittest.TestCase):
    """Test data flow and backup restore risk analysis."""

    def test_active_flow_and_deactivation(self):
        events = [
            NormalizedEvent(
                event_id="e_sync",
                event_type="SYNC_RECORD",
                system_id="integration_service",
                resource_id="data_flow_1",
                timestamp="2026-08-01T10:00:00Z",
                reported_outcome="SUCCESS",
                evidence_id="ev_sync",
            ),
            NormalizedEvent(
                event_id="e_disable",
                event_type="DISABLE_DATA_FLOW",
                system_id="integration_service",
                resource_id="data_flow_1",
                timestamp="2026-08-02T10:00:00Z",
                reported_outcome="SUCCESS",
                evidence_id="ev_dis",
            ),
        ]
        flow_assess = FlowAnalyzer.analyze_data_flow(
            flow_id="data_flow_1",
            events=events,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertEqual(flow_assess.operational_state, FlowState.DISABLED.value)
        self.assertEqual(flow_assess.recreation_risk, "NONE")
        self.assertIn("ev_dis", flow_assess.deactivation_evidence_ids)

    def test_backup_restore_risk(self):
        events = []
        restore_assess = FlowAnalyzer.analyze_restore_risk(
            archive_id="backup_100",
            events=events,
            audit_at="2026-08-05T00:00:00Z",
            restore_allowed=True,
            evidence_id="ev_sql",
        )
        self.assertEqual(restore_assess.operational_state, RestoreState.ACTIVE.value)
        self.assertEqual(restore_assess.recreation_risk, "ACTIVE_RESTORE_RISK")
        self.assertIn("ev_sql", restore_assess.supporting_evidence_ids)

    def test_cyclic_dependency_graph(self):
        graph = ResourceDependencyGraph()
        # Create cycle: A -> B -> C -> A
        graph.add_edge("sys_a", "res_a", "sys_b", "res_b", "SYNC_DESTINATION")
        graph.add_edge("sys_b", "res_b", "sys_c", "res_c", "SYNC_DESTINATION")
        graph.add_edge("sys_c", "res_c", "sys_a", "res_a", "SYNC_DESTINATION")

        cycles = graph.detect_cycles()
        self.assertTrue(len(cycles) > 0)

        # Ensure downstream traversal terminates despite cycle
        downstream = graph.get_downstream_resources("sys_a:res_a")
        self.assertIn("sys_b:res_b", downstream)
        self.assertIn("sys_c:res_c", downstream)


class TestReconstructionDeterminism(unittest.TestCase):
    """Test deterministic execution, reproducible outputs, and schema contracts."""

    def test_repeated_reconstruction_is_identical(self):
        timeline = [
            NormalizedEvent(
                event_id="e1",
                event_type="OBSERVE_RECORD",
                system_id="account_service",
                resource_id="acc_99",
                timestamp="2026-08-01T10:00:00Z",
                attributes={"observed_state": "PRESENT"},
                evidence_id="ev_01",
            ),
        ]
        r1 = ResourceStateMachine.reconstruct_resource_state(
            resource_id="acc_99",
            system_id="account_service",
            subject_id="u1",
            timeline=timeline,
            audit_at="2026-08-05T00:00:00Z",
        )
        r2 = ResourceStateMachine.reconstruct_resource_state(
            resource_id="acc_99",
            system_id="account_service",
            subject_id="u1",
            timeline=timeline,
            audit_at="2026-08-05T00:00:00Z",
        )
        self.assertEqual(r1.to_dict(), r2.to_dict())


if __name__ == "__main__":
    unittest.main()
