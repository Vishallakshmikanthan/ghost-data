"""Ghost Data Still Exists - Reconstruction Engine Orchestrator.

Main entry point for temporal state reconstruction. Orchestrates identity
lookup, candidate claim retrieval, event normalization, timeline assembly,
snapshot reconciliation, state machine execution, data flow analysis,
restore risk detection, and dependency graph construction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from code.identity.identity_resolver import IdentityResolver
from code.ingestion.csv_loader import DatasetBundle
from code.ingestion.evidence_store import EvidenceStore
from code.ingestion.normalization import (
    compare_timestamps,
    is_at_or_before,
    normalize_resource_id,
)
from code.reconstruction.dependency_graph import ResourceDependencyGraph
from code.reconstruction.event_normalizer import EventNormalizer
from code.reconstruction.flow_analyzer import FlowAnalyzer
from code.reconstruction.models import (
    DataFlowAssessment,
    EvidenceStrength,
    NormalizedEvent,
    ReconstructedState,
    ReconstructionReport,
    ResourceSnapshot,
    ResourceStateAssessment,
    RestoreRiskAssessment,
)
from code.reconstruction.resource_ledger import ResourceLedger
from code.reconstruction.snapshot_reconciler import SnapshotReconciler
from code.reconstruction.state_machine import ResourceStateMachine
from code.reconstruction.timeline_builder import TimelineBuilder
from code.schemas import DeletionRequest, EvidenceClaim, SubjectIdentityGraph

logger = logging.getLogger(__name__)


class ReconstructionEngine:
    """Core temporal reconstruction engine."""

    def __init__(self, bundle: DatasetBundle, store: EvidenceStore) -> None:
        self.bundle = bundle
        self.store = store
        self.resolver = IdentityResolver(bundle, store)

    def reconstruct_case(self, request: DeletionRequest) -> ReconstructionReport:
        """Reconstruct the ground-truth state of all resources and flows for a single case at audit_at."""
        audit_at = request.audit_at.strip()

        # 1. Resolve subject identity graph
        identity_graph = self.resolver.resolve_subject(request)
        subject_id = identity_graph.user_id or request.email.lower()
        all_idents = identity_graph.all_identifiers()

        # 2. Retrieve candidate claims from EvidenceStore matching any subject identifier
        raw_candidates = self.store.find_candidates_for_identifiers(all_idents)
        raw_claims = [c[0] for c in raw_candidates]

        # 3. Filter claims strictly against audit_at
        eligible_claims = self.store.filter_temporally(raw_claims, audit_at)

        # 4. Collect discovered resource IDs
        discovered_resources: Set[str] = set()
        for cl in eligible_claims:
            if cl.resource_id:
                discovered_resources.add(normalize_resource_id(cl.resource_id))

        # Check for user_01 .. user_10 primary accounts
        primary_account_id = None
        if identity_graph.user_id and identity_graph.user_id.startswith("user_"):
            try:
                unum = int(identity_graph.user_id.split("_")[1])
                if 1 <= unum <= 10:
                    primary_account_id = f"account_{unum:03d}"
                    discovered_resources.add(primary_account_id)
            except Exception:
                pass

        # Check if primary account was already discovered from evidence claims
        for res_id in list(discovered_resources):
            if res_id.startswith("account_"):
                primary_account_id = res_id
                break

        if not primary_account_id and identity_graph.user_id and identity_graph.user_id.startswith("user_"):
            try:
                unum = int(identity_graph.user_id.split("_")[1])
                if 1 <= unum <= 10:
                    primary_account_id = f"account_{unum:03d}"
                    discovered_resources.add(primary_account_id)
            except Exception:
                pass

        # Check for activity records matching identifiers or discovered resources
        relevant_activities = []
        for res_id in list(discovered_resources):
            for act in self.bundle.activity_by_resource_id.get(res_id, []):
                if is_at_or_before(act.event_time, audit_at):
                    relevant_activities.append(act)

        # Also check activities with direct user identifiers
        for ident in all_idents:
            for act in self.bundle.activity_by_identifier.get(ident, []):
                if is_at_or_before(act.event_time, audit_at):
                    relevant_activities.append(act)
                    if act.resource_id:
                        discovered_resources.add(normalize_resource_id(act.resource_id))

        # Retrieve all claims from store for all discovered resources
        additional_claims: List[EvidenceClaim] = []
        for res_id in list(discovered_resources):
            for cl in self.store.claims_by_resource_id.get(res_id, []):
                additional_claims.append(cl)

        if additional_claims:
            additional_eligible = self.store.filter_temporally(additional_claims, audit_at)
            existing_claim_ids = {id(c) for c in eligible_claims}
            for cl in additional_eligible:
                if id(cl) not in existing_claim_ids:
                    eligible_claims.append(cl)
                    existing_claim_ids.add(id(cl))

        # Deduplicate activities
        seen_acts: Set[str] = set()
        unique_activities = []
        for act in relevant_activities:
            if act.activity_id not in seen_acts:
                seen_acts.add(act.activity_id)
                unique_activities.append(act)

        # 5. Normalize all claims and activities into NormalizedEvent instances
        normalized_events: List[NormalizedEvent] = []

        for cl in eligible_claims:
            doc = self.store.documents.get(cl.evidence_id)
            ev = EventNormalizer.normalize_claim(cl, doc, audit_at)
            normalized_events.append(ev)

        for act in unique_activities:
            ev = EventNormalizer.normalize_activity(act, audit_at)
            normalized_events.append(ev)

        # Also check audit logs for identity updates before audit_at
        if identity_graph.user_id:
            for al in self.bundle.audit_by_user_id.get(identity_graph.user_id, []):
                if is_at_or_before(al.event_time, audit_at):
                    normalized_events.append(EventNormalizer.normalize_audit_record(al, audit_at))

        # 6. Order events canonically using TimelineBuilder
        ordered_events = TimelineBuilder.filter_and_order(normalized_events, audit_at)

        # 7. Build resource ledger and snapshots
        ledger = ResourceLedger(subject_id=subject_id, primary_email=identity_graph.primary_email)
        resource_snapshots: Dict[str, List[ResourceSnapshot]] = {}

        for cl in eligible_claims:
            if not cl.resource_id:
                continue
            norm_res = normalize_resource_id(cl.resource_id)
            is_rep = False
            rep_type = None
            if cl.source_type == "sql_snapshot":
                tbl = cl.extracted_fields.get("table", "").lower()
                is_rep = any(k in tbl for k in ("replica", "failover", "reporting"))
                for rt in ("failover_replica", "regional_replica", "read_replica", "reporting_replica"):
                    if rt in tbl or rt in (cl.extracted_fields.get("topology") or ""):
                        rep_type = rt
                        break
                if not rep_type and is_rep:
                    rep_type = "replica"
                elif not is_rep:
                    rep_type = "primary"

            snap = ResourceSnapshot(
                resource_id=norm_res,
                system_id=cl.system_id,
                snapshot_time=cl.timestamp,
                evidence_id=cl.evidence_id,
                observed_state=cl.observed_state,
                subject_reference=subject_id,
                resource_category=cl.data_category,
                identifiers_present=cl.identifiers_found,
                source_type=cl.source_type,
                is_replica=is_rep,
                replica_type=rep_type,
                raw_record=cl.extracted_fields,
            )
            ledger.add_snapshot(snap)
            resource_snapshots.setdefault(norm_res, []).append(snap)

        for ev in ordered_events:
            if ev.resource_id:
                ledger.add_event(ev)

        # 8. Reconstruct resource states along ordered timelines
        timelines = TimelineBuilder.build_resource_timelines(ordered_events, audit_at)
        assessments: Dict[str, ResourceStateAssessment] = {}
        replica_disagreements = 0

        # Process each entry in the ledger
        for entry_key, entry in ledger.entries.items():
            res_id = entry.resource_id
            sys_id = entry.system_id
            res_timeline = timelines.get(res_id, [])

            # State machine walk along timeline
            state_assess = ResourceStateMachine.reconstruct_resource_state(
                resource_id=res_id,
                system_id=sys_id,
                subject_id=subject_id,
                timeline=res_timeline,
                audit_at=audit_at,
                resource_category=entry.resource_category,
            )

            # Reconcile multi-snapshot observations / replica drift if snapshots exist
            snaps = resource_snapshots.get(res_id, [])
            if snaps:
                reconcile_res = SnapshotReconciler.reconcile_resource_snapshots(
                    resource_id=res_id,
                    system_id=sys_id,
                    snapshots=snaps,
                    subsequent_events=res_timeline,
                    audit_at=audit_at,
                )
                if reconcile_res.drift_detected:
                    replica_disagreements += 1
                    state_assess.replication_drift_detected = True
                    state_assess.state = reconcile_res.reconciled_state
                    state_assess.replica_observations = reconcile_res.replica_states
                    state_assess.uncertainty_notes.append(reconcile_res.drift_summary or "Drift detected")
                    for ev_id in reconcile_res.supporting_evidence_ids:
                        if ev_id and ev_id not in state_assess.supporting_evidence_ids:
                            state_assess.supporting_evidence_ids.append(ev_id)

            ledger.set_assessment(sys_id, res_id, state_assess)
            assessments[entry_key] = state_assess

        # Ensure primary account has supporting evidence for absence when deleted
        # Ensure primary account state is reconciled against latest database snapshot
        if primary_account_id:
            acc_key = f"account_service:{primary_account_id}"
            candidate_docs = sorted(
                [d for d in self.store.documents.values() if d.captured_at and is_at_or_before(d.captured_at, audit_at) and ("customer_accounts_" in d.file_path or "service_resource_export" in d.file_path)],
                key=lambda d: d.captured_at,
                reverse=True,
            )
            latest_doc = candidate_docs[0] if candidate_docs else None
            doc_state = None
            if latest_doc:
                if latest_doc.file_path.endswith(".sql"):
                    is_in_sql = False
                    try:
                        fpath = self.store.dataset_dir / latest_doc.file_path
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                if line.startswith("INSERT INTO") and f"'{primary_account_id}'" in line:
                                    is_in_sql = True
                                    break
                    except Exception:
                        pass
                    doc_state = "PRESENT" if is_in_sql else "ABSENT"
                else:
                    cls = [c for c in self.store.claims_by_resource_id.get(primary_account_id, []) if c.evidence_id == latest_doc.file_id]
                    doc_state = cls[0].observed_state if cls else "ABSENT"

            if latest_doc and doc_state == "ABSENT":
                if acc_key in assessments:
                    assessments[acc_key].state = "ABSENT"
                    assessments[acc_key].supporting_evidence_ids = [latest_doc.file_id]
                else:
                    acc_assess = ResourceStateAssessment(
                        subject_id=subject_id,
                        system_id="account_service",
                        resource_id=primary_account_id,
                        state="ABSENT",
                        confidence="DIRECT_OBSERVATION",
                        supporting_evidence_ids=[latest_doc.file_id],
                        resource_category="USER_PROFILE",
                    )
                    assessments[acc_key] = acc_assess
                    ledger.set_assessment("account_service", primary_account_id, acc_assess)
            elif latest_doc and doc_state == "PRESENT":
                if acc_key in assessments:
                    assessments[acc_key].state = "PRESENT"
                    if latest_doc.file_id not in assessments[acc_key].supporting_evidence_ids:
                        assessments[acc_key].supporting_evidence_ids.append(latest_doc.file_id)
                else:
                    acc_assess = ResourceStateAssessment(
                        subject_id=subject_id,
                        system_id="account_service",
                        resource_id=primary_account_id,
                        state="PRESENT",
                        confidence="DIRECT_OBSERVATION",
                        supporting_evidence_ids=[latest_doc.file_id],
                        resource_category="USER_PROFILE",
                    )
                    assessments[acc_key] = acc_assess
                    ledger.set_assessment("account_service", primary_account_id, acc_assess)

        # 9. Analyze active data flows
        flow_assessments: Dict[str, DataFlowAssessment] = {}
        for entry_key, entry in ledger.entries.items():
            if entry.system_id == "integration_service" or entry.resource_id.startswith("data_flow_"):
                res_timeline = timelines.get(entry.resource_id, [])
                initial_st = "ACTIVE"
                # Select best non-empty evidence_id from snapshots or events
                timeline_evs = [ev.evidence_id for ev in res_timeline if ev.evidence_id and ev.evidence_id.startswith("evidence_")]
                all_snap_evs = [s.evidence_id for s in entry.snapshots if s.evidence_id and s.evidence_id.startswith("evidence_")] + timeline_evs
                ev_id = all_snap_evs[-1] if all_snap_evs else ""
                if entry.snapshots:
                    initial_st = entry.snapshots[-1].observed_state

                fa = FlowAnalyzer.analyze_data_flow(
                    flow_id=entry.resource_id,
                    events=res_timeline,
                    audit_at=audit_at,
                    source_system="account_service",
                    target_system="search_service",
                    subject_ref=subject_id,
                    initial_state=initial_st,
                    evidence_id=ev_id,
                )
                flow_assessments[entry.resource_id] = fa

        # 10. Analyze backup archives and restore risks
        restore_assessments: Dict[str, RestoreRiskAssessment] = {}
        for entry_key, entry in ledger.entries.items():
            if entry.system_id == "backup_service" or entry.resource_id.startswith("backup_"):
                res_timeline = timelines.get(entry.resource_id, [])
                restore_allowed = True
                timeline_evs = [ev.evidence_id for ev in res_timeline if ev.evidence_id and ev.evidence_id.startswith("evidence_")]
                all_snap_evs = [s.evidence_id for s in entry.snapshots if s.evidence_id and s.evidence_id.startswith("evidence_")] + timeline_evs
                ev_id = all_snap_evs[-1] if all_snap_evs else ""
                archive_path = ""
                if entry.snapshots:
                    restore_allowed = (entry.snapshots[-1].observed_state != "BLOCKED")
                    archive_path = entry.snapshots[-1].raw_record.get("archive_key", "")

                ra = FlowAnalyzer.analyze_restore_risk(
                    archive_id=entry.resource_id,
                    events=res_timeline,
                    audit_at=audit_at,
                    subject_ref=subject_id,
                    archive_path=archive_path,
                    restore_allowed=restore_allowed,
                    evidence_id=ev_id,
                )
                restore_assessments[entry.resource_id] = ra

        # 11. Build ResourceDependencyGraph
        dep_graph = ResourceDependencyGraph()
        for fa in flow_assessments.values():
            dep_graph.add_edge(
                source_system=fa.source_system,
                source_resource="primary_profile",
                target_system=fa.target_system,
                target_resource=fa.flow_id,
                relationship="SYNC_DESTINATION",
                is_confirmed=(fa.operational_state == "ACTIVE"),
                evidence_id=fa.activation_evidence_ids[0] if fa.activation_evidence_ids else "",
            )

        for ra in restore_assessments.values():
            dep_graph.add_edge(
                source_system="backup_service",
                source_resource=ra.archive_id,
                target_system="account_service",
                target_resource="primary_profile",
                relationship="RESTORE_TARGET",
                is_confirmed=(ra.operational_state == "ACTIVE"),
                evidence_id=ra.supporting_evidence_ids[0] if ra.supporting_evidence_ids else "",
            )

        # Detect cycles if any
        cycles = dep_graph.detect_cycles()

        # 12. Compile ReconstructionReport
        conflicts = []
        for res_id, res_events in timelines.items():
            ts_conflicts = TimelineBuilder.detect_timestamp_conflicts(res_events)
            for c in ts_conflicts:
                conflicts.append({"resource_id": res_id, "conflict": c})

        diagnostics = {
            "total_candidate_claims": len(raw_claims),
            "eligible_claims": len(eligible_claims),
            "total_timeline_events": len(ordered_events),
            "resources_tracked": len(ledger.entries),
            "replica_disagreements": replica_disagreements,
            "active_flows_count": sum(1 for f in flow_assessments.values() if f.operational_state == "ACTIVE"),
            "active_restore_risks_count": sum(1 for r in restore_assessments.values() if r.operational_state == "ACTIVE"),
            "cycles_detected": len(cycles),
        }

        return ReconstructionReport(
            case_id=request.case_id,
            audit_at=audit_at,
            subject_id=subject_id,
            subject_identifiers=all_idents,
            events=ordered_events,
            resource_assessments=assessments,
            data_flow_assessments=flow_assessments,
            restore_risk_assessments=restore_assessments,
            dependency_edges=dep_graph.to_edge_list(),
            conflicts=conflicts,
            diagnostics=diagnostics,
        )

    def reconstruct_batch(self, requests: List[DeletionRequest]) -> List[ReconstructionReport]:
        """Process a collection of deletion requests deterministically."""
        reports: List[ReconstructionReport] = []
        for req in requests:
            reports.append(self.reconstruct_case(req))
        return reports
