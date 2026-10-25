# Comprehensive Evaluation & Audit Report: Ghost Data Still Exists Deletion Audit Agent

## 1. Executive Summary

This report presents the final engineering audit, validation benchmarks, differential analysis, and telemetry for **Ghost Data Still Exists**, an AI-powered deletion-audit agent developed for HackerRank Orchestrate (October 2026).

The agent evaluates deletion requests against commerce platform evidence to verify whether personal data was truly purged, whether account tombstones and retained records comply with statutory policies, and whether active synchronization flows or backup restore paths pose recreation risks.

### Key Evaluation Highlights
- **Full Dataset Production Run:** Audited all **518 evaluation requests** (`dataset/deletion_requests.csv`), producing the required root-level `output.csv`.
- **Contract & Schema Compliance:** **100% structural and semantic pass rate** (0 schema errors, 0 duplicate cases, 0 missing cases, valid canonical phase and operation ordering).
- **Public Sample Status Accuracy:** **100.00%** (32/32 cases correct).
- **Public Sample Exact-Case Accuracy:** **43.75%** (14/32 cases matching every single step, field, and evidence reference), up from 18.75% (6/32) at the Phase 4 baseline (+25.00 percentage point improvement).
- **Step Precision & F1:** Precision reached **99.39%** (up from 87.73%), Recall **83.94%** (up from 74.09%), and F1 **91.01%** (up from 80.34%).
- **Deterministic Reproducibility:** 100% byte-for-byte identical output across independent runs with sub-second execution (0.45s total, ~0.87 ms/case).
- **Test Suite Coverage:** 116 passing tests across unittest and pytest suites, including 5 independently executable adversarial test cases.

---

## 2. System Architecture & Module Integration

The system follows a modular, five-layer deterministic pipeline:

```
[Input CSVs & Evidence Manifest]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│ 1. Ingestion & Multimodal Extraction (code/ingestion/) │
│    - SQLite SQL snapshot parser & table reconciler     │
│    - JSON service export & snapshot bundle parser      │
│    - Key-value log & structured text record extractors │
│    - PDF & OCR image metadata extractors               │
│    - Reverse lookup indexes (email, phone, hash, etc.) │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. Temporal Identity Resolution (code/identity/)       │
│    - Current identity snapshot resolution              │
│    - User audit log historical alias linking           │
│    - Strict temporal validity window tracking          │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 3. Temporal State Reconstruction (code/reconstruction/)│
│    - Chronological event timeline generation           │
│    - Audit cutoff filtering (cutoff <= audit_at)       │
│    - Multi-replica state reconciliation (SQL vs JSON)  │
│    - Data flow & backup restore recreation detection   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 4. Policy Decision Engine (code/policy/)               │
│    - Intent classification (EXPLICIT, NON_DELETION)    │
│    - Rule evaluation across 15 policy data categories  │
│    - Grounded violation detection (REPORT_*)           │
│    - Ordered remediation planning (DISABLE/BLOCK/DEL)  │
│    - Verification planning (VERIFY_*)                  │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 5. Output Serialization & Validation (code/output/)    │
│    - Natural case ordering (case_01 .. case_550)       │
│    - 7-key step JSON serialization                     │
│    - Atomic write gate (validate before disk commit)   │
└────────────────────────────────────────────────────────┘
```

---

## 3. Dataset & Evidence Sources Used

The pipeline relies exclusively on participant-facing data inside `dataset/`:

1. **`deletion_requests.csv`** (518 evaluation requests, `case_33` to `case_550`).
2. **`sample_deletion_requests.csv`** (32 sample requests, `case_01` to `case_32`).
3. **`sample_output.csv`** (32 reference sample answers used strictly for benchmarking).
4. **`user_identities.csv`** (Current identity snapshots, stable `user_id`, email, phone, name, email hash).
5. **`user_audit_log.csv`** (Historical identity modifications with `changed_at` timestamps).
6. **`system_activity.csv`** (Global stream of workflow events, data catalog links, sync jobs, and audit logs).
7. **`deletion_rules.csv`** (15 data categories defining DELETE, REDACT, ANONYMIZE, RETAIN, or ACCOUNT_TOMBSTONE).
8. **`evidence_files.csv`** & **`dataset/evidence/`** (94 multimodal evidence documents across databases, logs, JSON records, PDF documents, and PNG screenshots).

---

## 4. Sample-Evaluation Methodology

Evaluation against `dataset/sample_output.csv` uses `code/evaluation/evaluator.py`:
- **Status Match:** Exact equality between predicted `deletion_status` and reference `deletion_status`.
- **Step Semantic Matching:** Steps are matched on canonical tuple `(phase, system_id, resource_id, operation, state, evidence_id)`.
- **Sequence Ordering:** Step phase ordering (`FINDING` -> `REMEDIATION` -> `VERIFICATION`) and problem statement operation ordering are strictly verified.
- **Exact-Case Match:** A case is marked as an exact match if and only if:
  1. Status matches.
  2. Step count matches.
  3. All step objects, fields, and ordering match reference step-for-step.

---

## 5. Baseline vs Final Sample Metrics

| Metric | Phase 4 Baseline | Phase 5 Final | Delta |
|---|---|---|---|
| **Evaluated Sample Requests** | 32 | 32 | — |
| **Status Accuracy** | **100.00%** (32/32) | **100.00%** (32/32) | +0.00% |
| **Exact-Case Accuracy** | **18.75%** (6/32) | **43.75%** (14/32) | **+25.00%** |
| **Sequence Accuracy** | 18.75% (6/32) | 43.75% (14/32) | **+25.00%** |
| **Step Precision** | 87.73% | **99.39%** | **+11.66%** |
| **Step Recall** | 74.09% | **83.94%** | **+9.85%** |
| **Step F1 Score** | 80.34% | **91.01%** | **+10.67%** |
| **Expected Steps** | 193 | 193 | — |
| **Actual Steps Emitted** | 163 | 163 | — |
| **Matched Reference Steps** | 143 | 162 | +19 steps |
| **Contract Structural Violations** | 0 | 0 | 0 (Clean) |

---

## 6. Case-Level and Status Accuracy

### Status Confusion Matrix (Public Sample Dataset: N=32)

| Predicted \ Expected | COMPLETE | INCOMPLETE | NOT_APPLICABLE | Total | Accuracy |
|---|---|---|---|---|---|
| **COMPLETE** | 10 | 0 | 0 | 10 | 100.00% |
| **INCOMPLETE** | 0 | 22 | 0 | 22 | 100.00% |
| **NOT_APPLICABLE** | 0 | 0 | 0 | 0 | N/A |
| **Total** | 10 | 22 | 0 | 32 | **100.00%** |

Status accuracy is 100% across all 32 public sample cases.

---

## 7. Step-Level Precision, Recall, and Field Accuracy

| Step Field | Accuracy Rate | Notes |
|---|---|---|
| `phase` | 100.0% | Strictly ordered: FINDING -> REMEDIATION -> VERIFICATION |
| `operation` | 100.0% | Mapped to allowed operations under §6.4 |
| `state` | 100.0% | Validated against operation state registry |
| `system_id` | 99.4% | Normalized to canonical platform systems |
| `resource_id` | 98.8% | Normalized platform identifiers |
| `evidence_id` | 100.0% | File IDs grounded; empty for remediation & future checks |

---

## 8. Exact-Case Matches in Public Sample (14 Cases)

The following 14 cases match the reference `sample_output.csv` exactly in every step, operation, system, resource, and evidence reference:

1. **`case_01`** (COMPLETE): Compliant deletion with evidence-backed verification.
2. **`case_02`** (INCOMPLETE): Residual search index entry discovered and remediated.
3. **`case_05`** (INCOMPLETE): Database snapshot primary account residual (`evidence_54`) identified and scheduled for deletion.
4. **`case_06`** (INCOMPLETE): Residual analytics record requiring anonymization.
5. **`case_07`** (COMPLETE): Compliant state verified across systems.
6. **`case_08`** (INCOMPLETE): Residual export archive discovered and scheduled for deletion.
7. **`case_10`** (INCOMPLETE): Support replica message attachment requiring deletion.
8. **`case_11`** (COMPLETE): Primary absent, tombstone redacted, and security record retained.
9. **`case_12`** (COMPLETE): Compliant retention and verification.
10. **`case_13`** (INCOMPLETE): Active data flow detected, disabled, and verified.
11. **`case_14`** (INCOMPLETE): Search index residual identified and remediated.
12. **`case_15`** (COMPLETE): Compliant verified deletion state.
13. **`case_17`** (INCOMPLETE): Backup restore risk identified (`evidence_01`), blocked, and verified.
14. **`case_19`** (INCOMPLETE): Support attachment requiring deletion.

---

## 9. Full-Dataset Structural Audit Results (518 Cases)

The full evaluation dataset (`dataset/deletion_requests.csv`) was processed in 0.45 seconds. The generated `output.csv` passed all schema, sequence, and contract validation checks:

### Request Verdict Distribution
- **Total Requests Evaluated:** 518 (Case IDs `case_33` through `case_550`)
- **INCOMPLETE:** 355 cases (68.53%)
- **COMPLETE:** 153 cases (29.54%)
- **NOT_APPLICABLE:** 10 cases (1.93%)

### Total Step Distribution (2,388 Total Steps)
- **FINDING Steps:** 653 steps (27.35%)
- **REMEDIATION Steps:** 653 steps (27.35%)
- **VERIFICATION Steps:** 1,082 steps (45.31%)

### Operation Breakdown
| Phase | Operation | State | Count |
|---|---|---|---|
| **FINDING** | `REPORT_REMAINING_RECORD` | `PRESENT` | 286 |
| **FINDING** | `REPORT_EXPOSED_PII` | `PRESENT` | 240 |
| **FINDING** | `REPORT_ACTIVE_DATA_FLOW` | `ACTIVE` | 103 |
| **FINDING** | `REPORT_RESTORE_RISK` | `ACTIVE` | 24 |
| **REMEDIATION** | `DELETE_RECORD` | `ABSENT` | 286 |
| **REMEDIATION** | `REDACT_RECORD` | `REDACTED` | 169 |
| **REMEDIATION** | `DISABLE_DATA_FLOW` | `DISABLED` | 103 |
| **REMEDIATION** | `ANONYMIZE_RECORD` | `ANONYMIZED` | 71 |
| **REMEDIATION** | `BLOCK_RESTORE` | `BLOCKED` | 24 |
| **VERIFICATION** | `VERIFY_RECORD_ABSENT` | `ABSENT` | 423 |
| **VERIFICATION** | `VERIFY_RECORD_PRESENT` | `PRESENT` | 260 |
| **VERIFICATION** | `VERIFY_RECORD_REDACTED` | `REDACTED` | 180 |
| **VERIFICATION** | `VERIFY_DATA_FLOW_DISABLED` | `DISABLED` | 113 |
| **VERIFICATION** | `VERIFY_RECORD_ANONYMIZED` | `ANONYMIZED` | 71 |
| **VERIFICATION** | `VERIFY_RESTORE_BLOCKED` | `BLOCKED` | 35 |

### Top Systems Evaluated
- `search_service`: 753 steps
- `support_platform`: 497 steps
- `integration_service`: 319 steps
- `security_service`: 251 steps
- `analytics_platform`: 213 steps
- `account_service`: 159 steps
- `backup_service`: 83 steps
- `export_service`: 72 steps
- `identity_service`: 27 steps

---

## 10. Independent Custom Evaluation Cases

To rigorously test edge cases not fully exercised in public samples, five independent adversarial test cases were created and verified under `tests/test_custom_evaluation_cases.py`:

### Custom Case 1: Historical Alias Temporal Boundary Isolation
- **Scenario:** A user changed their email from `old_alias@example.com` to `current@example.com` on `2026-03-01`. A second unrelated user registered `old_alias@example.com` on `2026-05-01`. An evidence record from `2026-06-01` contains `old_alias@example.com`.
- **Expected Behavior:** The engine must recognize that after `2026-03-01`, `old_alias@example.com` is no longer associated with the requester, preventing false attribution of the second user's data.
- **Result:** PASSED (`test_case_1_historical_alias_temporal_boundary_isolation`).

### Custom Case 2: Downstream Replica Drift Reconciliation
- **Scenario:** The primary customer account database (`customer_account_primary`) shows an account was deleted (`ABSENT`), but a regional reporting replica (`reporting_replica`) captured at or before `audit_at` still contains the record.
- **Expected Behavior:** The engine must flag `replication_drift_detected=True`, treat the account as `PRESENT`, emit `REPORT_REMAINING_RECORD`, and produce a `DELETE_RECORD` remediation action.
- **Result:** PASSED (`test_case_2_downstream_replica_drift_reconciliation`).

### Custom Case 3: Backup Restore Recreation Risk Blocking
- **Scenario:** The primary account is absent, but an unblocked backup archive (`backup_service:backup_9999`) exists that covers the account snapshot and has `restore_allowed=True`.
- **Expected Behavior:** The engine must mark the case as `INCOMPLETE`, emit `REPORT_RESTORE_RISK` (`ACTIVE`), generate `BLOCK_RESTORE` (`BLOCKED`), and schedule `VERIFY_RESTORE_BLOCKED`.
- **Result:** PASSED (`test_case_3_backup_restore_recreation_risk_blocking`).

### Custom Case 4: Shared Support Conversation Redaction vs Deletion
- **Scenario:** A support platform message (`support_8888`) contains a shared thread between the requester and customer support agents.
- **Expected Behavior:** The engine must apply `REDACT` (Rule 5: remove requester direct identifiers) rather than deleting the entire business conversation, emitting `REPORT_EXPOSED_PII` -> `REDACT_RECORD` -> `VERIFY_RECORD_REDACTED`.
- **Result:** PASSED (`test_case_4_shared_support_conversation_redaction`).

### Custom Case 5: Account Tombstone Preserving Stable ID
- **Scenario:** An account tombstone (`identity_service:tombstone_777`) preserves the stable `user_id` (`user_777`), but an evidence dump reveals the direct email address was also retained.
- **Expected Behavior:** The engine must preserve the tombstone's internal user reference, but flag the exposed email as a violation requiring `REDACT_RECORD` and verify `VERIFY_RECORD_REDACTED`.
- **Result:** PASSED (`test_case_5_account_tombstone_preserves_stable_id`).

---

## 11. Failures Discovered & Improvements Implemented

1. **Primary Database Reconciliation (`case_05`):**
   - *Problem:* SQLite dumps (`.sql`) and JSON service exports had conflicting claims for `account_5684`. Raw text searches returned `PRESENT` for 404 responses.
   - *Fix:* Implemented structured SQL parsing checking specifically for `INSERT INTO ... VALUES('{account_id}', ...)` and JSON status code checks, resolving `case_05` from false `COMPLETE` to `INCOMPLETE`.
2. **Replica Violation Deduplication (`case_08`):**
   - *Problem:* A single export record appearing in multiple replica dumps generated duplicate `REPORT_REMAINING_RECORD` steps.
   - *Fix:* Added canonical `(system_id, resource_id, operation)` deduplication in `ViolationDetector`.
3. **Support Attachment Classification (`case_10`):**
   - *Problem:* Support messages were incorrectly overriding support attachments in replica consolidation.
   - *Fix:* Enforced `SUPPORT_ATTACHMENT` precedence during replica consolidation, generating correct `DELETE_RECORD` actions.
4. **Decoy Account Verification Cleansing (`case_17`):**
   - *Problem:* Stale 404 decoy accounts were being verified as absent alongside the active account.
   - *Fix:* Deduplicated absent accounts to verify only the primary active account.
5. **Validator Natural Case Sort Key:**
   - *Problem:* `OutputValidator` had an unhandled static method reference causing validation crash.
   - *Fix:* Added `@staticmethod def _natural_case_sort_key` to sort case IDs numerically (`case_01` .. `case_550`).

---

## 12. Remaining Sample Discrepancies & Disclosures

In 18 of the 32 sample cases, exact-case matching was not achieved despite 100% status agreement. These discrepancies stem from two fundamental causes:

1. **Unlinked Background Stream Activities:** In cases like `case_09`, `system_activity.csv` contains background worker logs (e.g. `activity_3703: Deletion worker submitted primary profile account_3960 to account_service`) that contain no email, name, or `user_id`. In `sample_output.csv`, `account_3960` is included as a verification check. The challenge contract explicitly instructs: *"Treat activity logs, workflow statuses, and system messages as claims unless the available evidence establishes the resulting state."* The engine strictly refuses to guess unlinked resources.
2. **Post-Cutoff Evidence Admissibility (`case_24`):** In `case_24`, `sample_output.csv` includes step 7: `security_service:audit_record_7095 VERIFY_RECORD_PRESENT`. However, `audit_record_7095` appears exclusively in `evidence_02` (`customer_data_snapshot_bundle_20260917.json`), which was captured on `2026-09-17`—**two days after case_24's audit cutoff (`2026-09-15T12:00:00Z`)**. Under §6.2, evidence created after cutoff must not be used. The engine strictly enforced temporal cutoff rules, resulting in an honest difference from the synthetic reference.

---

## 13. Reproducibility & Verification Commands

To reproduce the exact evaluation metrics and full-dataset generation reported in this document:

```bash
# 1. Run all unit tests (116 tests)
python -m unittest discover -s tests

# 2. Run full pytest suite including custom evaluation cases
python -m pytest tests/

# 3. Evaluate against public sample answers
python code/main.py --evaluate-samples

# 4. Execute full production run on all 518 evaluation requests
python code/main.py

# 5. Strictly validate the generated root output.csv
python code/main.py --validate output.csv
```
