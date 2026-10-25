# Token Usage & Cost Report: Ghost Data Still Exists Deletion Audit Engine

## 1. Execution Overview

| Metric | Measured Value |
|---|---|
| **Challenge** | HackerRank Orchestrate (October 2026) — Ghost Data Still Exists |
| **Pipeline Run Mode** | Full Dataset Production Inference (`dataset/deletion_requests.csv`) |
| **Execution Timestamp** | 2026-10-25T17:51:53Z |
| **Execution Platform** | Local Python 3.14.6 (Windows x86_64) |
| **Total Requests Audited** | 518 requests (`case_33` through `case_550`) |
| **Primary Output File** | `output.csv` (Root directory, 511,395 bytes, 519 lines) |
| **Total Wall-Clock Runtime** | 0.45 seconds (~0.87 ms per request) |
| **Overall Processing Failures** | 0 (100% completion rate) |

---

## 2. Model & Token Accounting

The final deletion-audit architecture operates as a **100% deterministic, high-throughput local rule, graph, and evidence correlation engine**. All deletion intent classification, temporal identity graph reconstruction, evidence ingestion, replica drift reconciliation, policy rule evaluation, remediation planning, and verification sequencing are executed locally using native Python algorithms without external LLM API calls.

| Metric | Accounted Value |
|---|---|
| **Model Provider** | None (Local Rule Engine) |
| **Model Identifier / Runtime Name** | None (`local_rule_engine_v1`) |
| **Total Model API Calls** | 0 calls |
| **Prompt / Input Tokens** | 0 tokens |
| **Completion / Output Tokens** | 0 tokens |
| **Total Model Tokens** | 0 tokens |
| **Average Input Tokens Per Request** | 0.00 |
| **Average Output Tokens Per Request** | 0.00 |
| **Average Total Tokens Per Request** | 0.00 |
| **Cache Read / Write Tokens** | 0 tokens |
| **Failed / Retried Model Calls** | 0 calls |

---

## 3. Financial Cost Breakdown

Because all 518 evaluation cases are audited locally without third-party model inference endpoints (e.g. OpenAI, Anthropic, or Google Cloud Vertex AI API fees), the API inference cost for the final production run is exactly zero.

| Cost Dimension | Measured / Estimated Value | Notes |
|---|---|---|
| **LLM Provider API Cost** | **$0.0000** | Zero API calls dispatched during production inference |
| **Input Token Charges** | $0.0000 | 0 input tokens billed |
| **Output Token Charges** | $0.0000 | 0 output tokens billed |
| **Average Cost Per Request** | **$0.0000** | 0.87 ms local CPU time per case |
| **Infrastructure / Compute Cost** | Negligible (<$0.0001) | Sub-second execution on commodity workstation CPU |
| **Total Production Inference Cost** | **$0.0000** | Zero monetary cost |

---

## 4. Accounting Methodology & Reproducibility

1. **Deterministic Rule Engine:** Deletion intent is evaluated using token-boundary regular expressions and intent dictionaries across English and multilingual keywords. Policy decisions are derived by matching reconstructed resource states against the rules table (`dataset/deletion_rules.csv`).
2. **Local Multi-Modal Ingestion:** Unstructured log files, plain-text tables, JSON snapshot bundles, PDF documents, and SQLite snapshots are parsed locally using Python's standard library and local dependencies (`pypdf`, `Pillow`, `sqlite3`).
3. **Reproducibility Guarantee:** Any user or automated grader executing `python code/main.py` incurs zero API charges, requires no API credentials or `.env` configuration, and produces the exact same byte-for-byte `output.csv` in under 1 second.
