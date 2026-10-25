"""Ghost Data Still Exists - Main Entry Point.

HackerRank Orchestrate October 2026.
Deletion Audit Engineering System.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from code.evaluation.evaluator import SampleEvaluator
from code.output.validator import OutputValidator


def inspect_dataset() -> None:
    """Print an overview of the challenge dataset files."""
    dataset_dir = repo_root / "dataset"
    print("=" * 60)
    print("        GHOST DATA STILL EXISTS — DATASET SUMMARY           ")
    print("=" * 60)
    if not dataset_dir.exists():
        print(f"Dataset directory not found at {dataset_dir}")
        return

    csv_files = sorted(dataset_dir.glob("*.csv"))
    for cf in csv_files:
        try:
            with open(cf, "r", encoding="utf-8") as f:
                lines = f.readlines()
            header = lines[0].strip() if lines else "(empty)"
            row_count = max(0, len(lines) - 1)
            print(f"- {cf.name:<32} {row_count:>5} rows | Header: {header}")
        except Exception as exc:
            print(f"- {cf.name:<32} (error reading: {exc})")

    evidence_dir = dataset_dir / "evidence"
    if evidence_dir.exists():
        print("-" * 60)
        print("Evidence Categories:")
        for cat in sorted([d for d in evidence_dir.iterdir() if d.is_dir()]):
            files = list(cat.glob("*"))
            print(f"- {cat.name:<32} {len(files):>5} files")
    print("=" * 60)


def run_pipeline(
    input_path: Union[str, Path] = "dataset/deletion_requests.csv",
    output_path: Union[str, Path] = "output.csv",
    dataset_dir: Optional[Union[str, Path]] = None,
) -> int:
    """Execute end-to-end deletion audit pipeline with atomic validation."""
    import csv
    import json
    import shutil
    import time
    from collections import Counter
    from code.ingestion import build_evidence_store, load_dataset, load_deletion_requests
    from code.policy import PolicyEngine
    from code.output.validator import OutputValidator

    base_dir = Path(dataset_dir).resolve() if dataset_dir else (repo_root / "dataset")
    inp_file = Path(input_path)
    if not inp_file.is_absolute():
        inp_file = repo_root / inp_file

    out_file = Path(output_path)
    if not out_file.is_absolute():
        out_file = repo_root / out_file

    if not inp_file.exists():
        print(f"Error: Input requests file not found: {inp_file}", file=sys.stderr)
        return 1

    if not base_dir.exists():
        print(f"Error: Dataset directory not found: {base_dir}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("      GHOST DATA STILL EXISTS — FULL DELETION AUDIT PIPELINE        ")
    print("=" * 70)
    print(f"Input Requests:   {inp_file}")
    print(f"Output Target:    {out_file}")
    print(f"Dataset Dir:      {base_dir}")
    print("-" * 70)

    t0_start = time.perf_counter()
    print("[1/5] Ingesting global dataset and building evidence index...")
    bundle = load_dataset(base_dir)
    if inp_file.resolve() != (base_dir / "deletion_requests.csv").resolve():
        print(f"Loading custom requests from {inp_file.name}...")
        custom_reqs, errs = load_deletion_requests(inp_file)
        if errs:
            print(f"Warning: Encountered {len(errs)} malformed rows in {inp_file.name}")
        bundle.deletion_requests = custom_reqs

    total_requests = len(bundle.deletion_requests)
    print(f"Total requests to audit: {total_requests}")

    store = build_evidence_store(base_dir, bundle)
    print(f"Evidence store ready: {len(store.documents)} documents, {len(store.claims)} claims, {len(bundle.system_activities)} activities, {len(bundle.user_audit_logs)} audit logs.")

    print("[2/5] Initializing policy decision engine...")
    engine = PolicyEngine(bundle, store)

    # Sort requests in canonical natural order
    sorted_reqs = sorted(
        bundle.deletion_requests.values(),
        key=lambda r: OutputValidator._natural_case_sort_key(r.case_id)
    )

    print(f"[3/5] Auditing {total_requests} cases against policy and evidence...")
    t0_eval = time.perf_counter()
    results = []
    status_counts: Counter[str] = Counter()
    phase_counts: Counter[str] = Counter()
    op_counts: Counter[str] = Counter()
    failures: List[Tuple[str, str]] = []

    for req in sorted_reqs:
        try:
            res, rep = engine.evaluate_case(req)
            results.append(res)
            status_counts[res.deletion_status] += 1
            for s in res.steps:
                phase_counts[s.phase] += 1
                op_counts[s.operation] += 1
        except Exception as exc:
            failures.append((req.case_id, str(exc)))
            print(f"Error evaluating {req.case_id}: {exc}", file=sys.stderr)

    eval_time = time.perf_counter() - t0_eval
    print(f"Case audit completed in {eval_time:.2f}s ({eval_time/max(1, total_requests)*1000:.1f} ms/case)")

    if failures:
        print(f"CRITICAL: {len(failures)} cases encountered execution failures!", file=sys.stderr)
        return 1

    print("[4/5] Serializing candidate predictions and validating contract...")
    temp_dir = repo_root / "code" / "evaluation" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    candidate_csv = temp_dir / "candidate_output.csv"

    with open(candidate_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for r in results:
            steps_data = [s.to_dict() for s in r.steps]
            writer.writerow([r.case_id, r.deletion_status, json.dumps(steps_data)])

    # Strict output validation
    expected_ids = [r.case_id for r in sorted_reqs]
    validator = OutputValidator(
        expected_case_ids=expected_ids,
        expected_count=len(expected_ids),
    )
    val_result = validator.validate_csv_file(candidate_csv)
    print(val_result.summary())

    if not val_result.is_valid:
        print("CRITICAL: Candidate output failed strict contract validation! Target file was preserved.", file=sys.stderr)
        return 1

    print(f"[5/5] Validation PASSED. Writing final output to {out_file}...")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_csv, out_file)
    total_elapsed = time.perf_counter() - t0_start

    print("=" * 70)
    print("              FULL PIPELINE EXECUTION SUMMARY                       ")
    print("=" * 70)
    print(f"Output File:             {out_file} ({out_file.stat().st_size:,} bytes)")
    print(f"Total Cases:             {len(results)}")
    print(f"Status Breakdown:        COMPLETE: {status_counts['COMPLETE']}, INCOMPLETE: {status_counts['INCOMPLETE']}, NOT_APPLICABLE: {status_counts['NOT_APPLICABLE']}")
    print(f"Total Steps Generated:   {sum(phase_counts.values())}")
    print(f"  - FINDING:             {phase_counts['FINDING']}")
    print(f"  - REMEDIATION:         {phase_counts['REMEDIATION']}")
    print(f"  - VERIFICATION:        {phase_counts['VERIFICATION']}")
    print(f"Total Execution Time:    {total_elapsed:.2f}s")
    print("=" * 70)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ghost Data Still Exists - Deletion Audit Pipeline"
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default="dataset/deletion_requests.csv",
        help="Path to deletion requests CSV input (default: dataset/deletion_requests.csv)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="output.csv",
        help="Path to output predictions CSV (default: output.csv)",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Execute full deletion-audit pipeline across all requests in input CSV",
    )
    parser.add_argument(
        "--validate",
        "-v",
        type=str,
        metavar="CSV_FILE",
        help="Validate an output CSV file against strict schema and contract rules",
    )
    parser.add_argument(
        "--evaluate",
        "-e",
        type=str,
        metavar="PRED_CSV",
        help="Evaluate a prediction CSV file against reference sample answers",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect dataset files and evidence directory structure",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Execute dataset ingestion and evidence indexing telemetry",
    )
    parser.add_argument(
        "--reconstruct",
        type=str,
        metavar="CASE_ID",
        help="Run temporal reconstruction diagnostics on a specific case_id",
    )
    parser.add_argument(
        "--reconstruct-all",
        action="store_true",
        help="Execute temporal state reconstruction engine across all 518 evaluation requests",
    )
    parser.add_argument(
        "--policy-case",
        type=str,
        metavar="CASE_ID",
        help="Run policy decision engine diagnostics on a specific case_id",
    )
    parser.add_argument(
        "--evaluate-samples",
        action="store_true",
        help="Run policy engine on all 32 public sample requests and compare with sample_output.csv",
    )
    args = parser.parse_args()

    if args.inspect:
        inspect_dataset()
        return 0

    if args.policy_case:
        import time
        from code.ingestion import build_evidence_store, load_dataset, load_deletion_requests
        from code.policy import PolicyEngine
        dataset_dir = repo_root / "dataset"
        bundle = load_dataset(dataset_dir)
        store = build_evidence_store(dataset_dir, bundle)

        cid = args.policy_case.strip()
        req = bundle.deletion_requests.get(cid)
        if not req:
            sample_reqs, _ = load_deletion_requests(dataset_dir / "sample_deletion_requests.csv")
            req = sample_reqs.get(cid)

        if not req:
            print(f"Error: case_id '{cid}' not found in deletion_requests or sample_deletion_requests", file=sys.stderr)
            return 1

        engine = PolicyEngine(bundle, store)
        t0 = time.perf_counter()
        result, report = engine.evaluate_case(req)
        elapsed = time.perf_counter() - t0

        print("=" * 70)
        print(f"POLICY DECISION REPORT: {report.case_id}")
        print("=" * 70)
        print(f"Request Email: {req.email}")
        print(f"Request Text:  {req.request_text}")
        print("-" * 70)
        print(f"1. Intent Classification:")
        print(f"   - Authorized: {report.intent.is_deletion_authorized}")
        print(f"   - Intent Type: {report.intent.intent_type}")
        print(f"   - Confidence: {report.intent.confidence:.2f}")
        print(f"   - Signals: {report.intent.matched_signals}")
        print(f"   - Rationale: {report.intent.rationale}")
        print("-" * 70)
        print(f"2. Evaluated Rules: {report.evaluated_rules}")
        print(f"3. Tracked Resources ({len(report.tracked_resources)}):")
        for tr in report.tracked_resources:
            print(f"   * {tr}")
        print("-" * 70)
        print(f"4. Confirmed Violations ({len(report.violations)}):")
        for v in report.violations:
            print(f"   * [{v.operation}] {v.system_id}:{v.resource_id} state={v.state} rule={v.rule_id} ev={v.evidence_id}")
            print(f"     -> Remediation: {v.required_remediation_op} ({v.required_remediation_state})")
            print(f"     -> Verification: {v.required_verification_op} ({v.required_verification_state})")
            if v.description:
                print(f"     -> Reason: {v.description}")
        print("-" * 70)
        print(f"5. Generated Steps ({len(result.steps)}):")
        for s in result.steps:
            print(f"   Step {s.step_no:>2}: [{s.phase:<12}] {s.system_id:<20} {s.resource_id:<18} {s.operation:<25} {s.state:<12} ev='{s.evidence_id}'")
        print("-" * 70)
        print(f"6. Final Internal Verdict: {result.deletion_status}")
        print(f"   Rationale: {report.rationale}")
        print(f"Execution time: {elapsed:.3f}s")
        print("=" * 70)
        return 0

    if args.evaluate_samples:
        import csv
        import json
        import time
        from code.ingestion import build_evidence_store, load_dataset
        from code.policy import PolicyEngine
        from code.evaluation.evaluator import SampleEvaluator

        dataset_dir = repo_root / "dataset"
        print("Loading sample dataset and evidence store...")
        bundle = load_dataset(dataset_dir, requests_file="sample_deletion_requests.csv")
        store = build_evidence_store(dataset_dir, bundle)

        engine = PolicyEngine(bundle, store)
        print(f"Evaluating {len(bundle.deletion_requests)} sample deletion requests...")

        t0 = time.perf_counter()
        results = []
        for req in bundle.deletion_requests.values():
            res, _ = engine.evaluate_case(req)
            results.append(res)
        elapsed = time.perf_counter() - t0

        temp_dir = repo_root / "code" / "evaluation" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_csv = temp_dir / "sample_predictions_temp.csv"

        with open(temp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "deletion_status", "steps_json"])
            for r in results:
                steps_data = [s.to_dict() for s in r.steps]
                writer.writerow([r.case_id, r.deletion_status, json.dumps(steps_data)])

        evaluator = SampleEvaluator(reference_path=dataset_dir / "sample_output.csv")
        eval_report = evaluator.evaluate_file(temp_csv)
        print(eval_report.format_console_summary())
        print(f"Total Policy Evaluation Time: {elapsed:.3f}s ({elapsed/len(results)*1000:.1f} ms/req)")
        return 0 if eval_report.contract_result.is_structurally_valid else 1

    if args.ingest:
        from code.ingestion import build_evidence_store, load_dataset, resolve_subject
        dataset_dir = repo_root / "dataset"
        print("Loading global datasets...")
        bundle = load_dataset(dataset_dir)
        print("Record counts:", bundle.record_counts)
        print("Ingesting evidence documents and building index...")
        store = build_evidence_store(dataset_dir, bundle)
        print("Store telemetry:", store.get_telemetry())
        print("Resolving 518 evaluation requests...")
        resolved = {"EXACT_CURRENT": 0, "HISTORICALLY_LINKED": 0, "STABLE_ID_LINKED": 0, "UNRESOLVED": 0, "AMBIGUOUS": 0}
        for req in bundle.deletion_requests.values():
            graph = resolve_subject(req, bundle, store)
            resolved[graph.match_classification] = resolved.get(graph.match_classification, 0) + 1
        print("Resolution distribution:", resolved)
        return 0

    if args.reconstruct:
        import time
        from code.ingestion import build_evidence_store, load_dataset, load_deletion_requests
        from code.reconstruction import reconstruct_case
        dataset_dir = repo_root / "dataset"
        bundle = load_dataset(dataset_dir)
        store = build_evidence_store(dataset_dir, bundle)

        cid = args.reconstruct.strip()
        req = bundle.deletion_requests.get(cid)
        if not req:
            sample_reqs, _ = load_deletion_requests(dataset_dir / "sample_deletion_requests.csv")
            req = sample_reqs.get(cid)

        if not req:
            print(f"Error: case_id '{cid}' not found in deletion_requests or sample_deletion_requests", file=sys.stderr)
            return 1

        t0 = time.perf_counter()
        report = reconstruct_case(req, bundle, store)
        elapsed = time.perf_counter() - t0

        print("=" * 70)
        print(f"TEMPORAL RECONSTRUCTION REPORT: {report.case_id} (audit_at: {report.audit_at})")
        print("=" * 70)
        print(f"Subject: {report.subject_id} | Identifiers: {len(report.subject_identifiers)}")
        for ident in sorted(list(report.subject_identifiers)):
            print(f"  - {ident}")
        print("-" * 70)
        print(f"Timeline Events: {len(report.events)}")
        for ev in report.events[:15]:
            print(f"  [{ev.timestamp}] {ev.event_type:<20} {ev.system_id}:{ev.resource_id:<16} result={ev.reported_outcome:<7} ev={ev.evidence_id}")
        if len(report.events) > 15:
            print(f"  ... ({len(report.events) - 15} more events)")
        print("-" * 70)
        print(f"Resource State Assessments: {len(report.resource_assessments)}")
        for key, ra in report.resource_assessments.items():
            drift_str = " [DRIFT DETECTED]" if ra.replication_drift_detected else ""
            print(f"  * {ra.system_id}:{ra.resource_id:<18} -> {ra.state:<22} conf={ra.confidence:<20} ev={ra.supporting_evidence_ids}{drift_str}")
        print("-" * 70)
        print(f"Data Flows ({len(report.data_flow_assessments)}):")
        for fid, fa in report.data_flow_assessments.items():
            print(f"  * {fa.flow_id} ({fa.source_system} -> {fa.target_system}): state={fa.operational_state} risk={fa.recreation_risk}")
        print(f"Restore Risks ({len(report.restore_risk_assessments)}):")
        for aid, ra in report.restore_risk_assessments.items():
            print(f"  * {ra.archive_id}: state={ra.operational_state} risk={ra.recreation_risk} ev={ra.supporting_evidence_ids}")
        print("-" * 70)
        print("Diagnostics:", report.diagnostics)
        print(f"Execution time: {elapsed:.3f}s")
        print("=" * 70)
        return 0

    if args.reconstruct_all:
        import time
        from collections import Counter
        from code.ingestion import build_evidence_store, load_dataset
        from code.reconstruction import ReconstructionEngine
        dataset_dir = repo_root / "dataset"
        print("Loading global datasets...")
        bundle = load_dataset(dataset_dir)
        print("Ingesting evidence store...")
        store = build_evidence_store(dataset_dir, bundle)

        engine = ReconstructionEngine(bundle, store)
        print(f"Executing ReconstructionEngine across all {len(bundle.deletion_requests)} evaluation requests...")
        t0 = time.perf_counter()

        total_assessments = 0
        total_events = 0
        total_drifts = 0
        active_flows = 0
        active_restores = 0
        unknown_states = 0
        state_distribution = Counter()
        failures = 0

        for req in bundle.deletion_requests.values():
            try:
                rep = engine.reconstruct_case(req)
                total_events += len(rep.events)
                total_assessments += len(rep.resource_assessments)
                total_drifts += rep.diagnostics.get("replica_disagreements", 0)
                active_flows += rep.diagnostics.get("active_flows_count", 0)
                active_restores += rep.diagnostics.get("active_restore_risks_count", 0)
                for a in rep.resource_assessments.values():
                    state_distribution[a.state] += 1
                    if a.state == "UNKNOWN":
                        unknown_states += 1
            except Exception as e:
                failures += 1
                print(f"Error processing {req.case_id}: {e}", file=sys.stderr)

        elapsed = time.perf_counter() - t0
        print("=" * 70)
        print("        PHASE 3 FULL RECONSTRUCTION TELEMETRY SUMMARY       ")
        print("=" * 70)
        print(f"Total Requests Processed:      {len(bundle.deletion_requests)}")
        print(f"Total Processing Failures:     {failures}")
        print(f"Total Execution Time:          {elapsed:.2f}s ({elapsed/len(bundle.deletion_requests)*1000:.2f} ms/req)")
        print(f"Total Timeline Events:         {total_events}")
        print(f"Total Resource Assessments:    {total_assessments}")
        print(f"Replication Drift Disagreements: {total_drifts}")
        print(f"Active Data Flows Detected:    {active_flows}")
        print(f"Active Restore Risks Detected: {active_restores}")
        print(f"Unknown States Preserved:      {unknown_states}")
        print("Resource State Distribution:")
        for st, cnt in sorted(state_distribution.items()):
            print(f"  - {st:<25} {cnt:>6}")
        print("=" * 70)
        return 0

    if args.validate:
        val_path = Path(args.validate)
        if not val_path.exists():
            print(f"Error: File to validate not found: {val_path}", file=sys.stderr)
            return 1
        validator = OutputValidator()
        result = validator.validate_csv_file(val_path)
        print(result.summary())
        return 0 if result.is_valid else 1

    if args.evaluate:
        ev_path = Path(args.evaluate)
        if not ev_path.exists():
            print(f"Error: Prediction file not found: {ev_path}", file=sys.stderr)
            return 1
        evaluator = SampleEvaluator()
        report = evaluator.evaluate_file(ev_path)
        print(report.format_console_summary())
        return 0 if report.contract_result.is_structurally_valid else 1

    # Default run or --run-all: execute the full deletion audit pipeline
    return run_pipeline(input_path=args.input, output_path=args.output)


if __name__ == "__main__":
    sys.exit(main())
