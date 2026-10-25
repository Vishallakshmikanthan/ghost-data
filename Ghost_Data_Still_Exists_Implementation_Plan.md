# Ghost Data Still Exists — Winning-Oriented Implementation Playbook
## HackerRank Orchestrate • October 2026

> **Mission:** Build an evidence-grounded deletion audit agent that reconstructs the true data state at each request’s `audit_at`, applies the supplied deletion policy, identifies every justified residual or recreation path, and emits a deterministic, correctly ordered remediation/verification plan. The CSV is the output contract, not the product.

**Repository:** https://github.com/interviewstreet/hackerrank-orchestrate-october26  
**Challenge/submission page:** https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists  
**Primary contract:** repository `AGENTS.md`, `problem_statement.md`, `README.md`, `dataset/deletion_rules.csv`, and `dataset/sample_output.csv`.

---

## 0. The non-negotiable strategy

The best use of limited time is **a deterministic evidence-processing pipeline with a constrained AI layer**, not a free-form multi-agent system that guesses. Use AI where semantic understanding is genuinely needed (multilingual deletion intent, messy PDF/image/document interpretation, ambiguous evidence linkage); use normal code for identity graph construction, time cutoff, policy enforcement, dependency ordering, step serialization, reproducibility, and validation.

### Definition of done

- [ ] Exactly one output row per request in `dataset/deletion_requests.csv` (the published dataset has 518 requests), no missing or duplicate `case_id`.
- [ ] Root-level `output.csv`, exact header `case_id,deletion_status,steps_json`, sorted by `case_id`.
- [ ] Every output `steps_json` parses as a JSON array. Each step has the exact seven required keys; `step_no` starts at 1 and is consecutive.
- [ ] Status is only `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`.
- [ ] `NOT_APPLICABLE` means the natural-language message did not authorize deletion and must have `[]`.
- [ ] `COMPLETE` contains **only** evidence-backed `VERIFICATION` steps for the compliant current state.
- [ ] `INCOMPLETE` contains supported `FINDING`s, a complete policy-driven `REMEDIATION` plan, and planned `VERIFICATION` checks.
- [ ] Findings and `COMPLETE` verification cite an allowed `file_id`, `activity_id`, or `audit_id` that exists and is available by `audit_at`. Remediation and future verification for incomplete cases use `evidence_id: ""`.
- [ ] Policy-retained records stay available; tombstones retain only the stable internal `user_id` where allowed and redact direct identifiers.
- [ ] The answer uses every relevant participant-facing evidence source; it does not infer a case solely from a filename, reported workflow status, or generic case pattern.
- [ ] `code/evaluation/evaluation_report.md`, `code/evaluation/usage_report.md`, runnable code, prompts, `README.md`, and clean packaging are complete.
- [ ] Root `log.txt` is present and correctly appended to by the coding agent per `AGENTS.md`; inspect it for secrets before uploading.
- [ ] Fresh-run reproducibility passes from a clean checkout / clean Python environment.

This plan cannot guarantee a win; it is designed to maximize correctness against the published evaluation contract and avoid known high-impact failure modes.

---

## 1. Architecture: clustered modules with explicit data contracts

Build these as **six integration clusters**, not a zoo of independent agents.

```text
                         ┌──────────────────────────────┐
                         │  C0. Contract / Run Control  │
                         │ config, paths, logs, metrics │
                         └──────────────┬───────────────┘
                                        │
                 ┌──────────────────────▼──────────────────────┐
                 │ C1. Ingestion + Evidence Normalization      │
                 │ CSV / TXT / JSON / SQL / PDF / image / logs │
                 └──────────────────────┬──────────────────────┘
                                        │ EvidenceDocument[]
                 ┌──────────────────────▼──────────────────────┐
                 │ C2. Identity Resolution + Temporal Index    │
                 │ current + historical aliases + hashes + IDs │
                 └──────────────────────┬──────────────────────┘
                                        │ SubjectIdentityGraph
                 ┌──────────────────────▼──────────────────────┐
                 │ C3. Case Reconstruction + Evidence Graph     │
                 │ cutoff at audit_at; cross-system records     │
                 └──────────────────────┬──────────────────────┘
                                        │ CandidateFindings
                 ┌──────────────────────▼──────────────────────┐
                 │ C4. Policy / Plan Compiler                   │
                 │ treatment, flows, restore, dependency order  │
                 └──────────────────────┬──────────────────────┘
                                        │ Canonical CaseResult
                 ┌──────────────────────▼──────────────────────┐
                 │ C5. Validator + Evaluator + Submission       │
                 │ schema, sequencing, sample diff, metrics     │
                 └──────────────────────────────────────────────┘
```

### Proposed repository layout

Adapt the existing starter instead of blindly replacing it. Keep the requested entry point `code/main.py` unless your team has a tested reason not to.

```text
code/
  main.py                         # CLI/orchestrator; thin entry point
  config.py                       # paths, limits, provider/model config
  schemas.py                      # typed structures / allowed enums
  ingestion/
    csv_loader.py
    file_manifest.py
    document_extractors.py
    sql_snapshots.py
    evidence_registry.py
  identity/
    resolver.py
    alias_index.py
    temporal_identity.py
  reconstruction/
    intent_classifier.py
    case_engine.py
    evidence_linker.py
    temporal_state.py
    flow_graph.py
  policy/
    rules_loader.py
    policy_engine.py
    remediation_planner.py
    ordering.py
  llm/
    client.py
    prompts.py
    structured_output.py
    usage_tracker.py
    cache.py
  output/
    serializer.py
    validator.py
  evaluation/
    main.py
    run_sample.py
    evaluation_report.md
    usage_report.md
    custom_cases.json             # team-created tests; reports document them too
  tests/
    test_contract.py
    test_intent.py
    test_identity.py
    test_temporal_cutoff.py
    test_policy.py
    test_ordering.py
    test_sqlite_evidence.py
    test_output_validation.py
  README.md
  prompts/
    intent.md
    evidence_extraction.md
    ambiguity_review.md
```

**Ownership rule:** one agent owns one cluster; only the integrator edits `main.py`, shared schemas, and output contracts. Other agents must produce modules and tests against the shared schema—not independently invent interfaces.

---

## 2. The shared schema — define this before parallel work

Create `code/schemas.py` first. Use dataclasses or Pydantic if already installed; do not add a large dependency without reason.

### Evidence record

```python
EvidenceDocument(
    file_id: str,
    relative_path: str,
    media_type: str,
    captured_at: datetime | None,
    extracted_text: str,
    structured_records: list[dict],
    extraction_method: str,
    extraction_warnings: list[str],
    content_hash: str,
)
```

### Subject identity graph

```python
SubjectIdentityGraph(
    requested_email: str,
    stable_user_ids: set[str],
    current_identifiers: set[str],
    historical_identifiers: set[str],
    email_hashes: set[str],
    alias_validity: list[dict],    # identifier + valid_from/valid_to where known
    resolution_evidence: list[str]
)
```

No fuzzy name-only match should automatically join a record to a subject. Evidence links should be strength-ranked:
1. exact stable `user_id` or exact unique resource linkage;
2. exact current/historical email or email hash with time-consistent history;
3. a corroborated combination of identifier + system/resource relationship + valid timestamp;
4. ambiguous/partial evidence: hold for review, do not assert as a fact.

### Case result and step schema

```python
AllowedPhase = {"FINDING", "REMEDIATION", "VERIFICATION"}
AllowedStatus = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}

OutputStep = {
    "step_no": int,
    "phase": str,
    "system_id": str,
    "resource_id": str,
    "operation": str,
    "state": str,
    "evidence_id": str,
}
CaseResult = {
    "case_id": str,
    "deletion_status": str,
    "steps": list[OutputStep],
}
```

Enforce the problem statement's exact operation/state pairs; do not invent new operation names, phases, or states.

### Allowed operation/state registry

| Phase | Operation | State |
|---|---|---|
| FINDING | `REPORT_REMAINING_RECORD` | `PRESENT` |
| FINDING | `REPORT_EXPOSED_PII` | `PRESENT` |
| FINDING | `REPORT_ACTIVE_DATA_FLOW` | `ACTIVE` |
| FINDING | `REPORT_RESTORE_RISK` | `ACTIVE` |
| REMEDIATION | `DISABLE_DATA_FLOW` | `DISABLED` |
| REMEDIATION | `BLOCK_RESTORE` | `BLOCKED` |
| REMEDIATION | `DELETE_RECORD` | `ABSENT` |
| REMEDIATION | `REDACT_RECORD` | `REDACTED` |
| REMEDIATION | `ANONYMIZE_RECORD` | `ANONYMIZED` |
| VERIFICATION | `VERIFY_RECORD_ABSENT` | `ABSENT` |
| VERIFICATION | `VERIFY_RECORD_PRESENT` | `PRESENT` |
| VERIFICATION | `VERIFY_RECORD_REDACTED` | `REDACTED` |
| VERIFICATION | `VERIFY_RECORD_ANONYMIZED` | `ANONYMIZED` |
| VERIFICATION | `VERIFY_DATA_FLOW_DISABLED` | `DISABLED` |
| VERIFICATION | `VERIFY_RESTORE_BLOCKED` | `BLOCKED` |

---

## 3. Cluster C0 — Contract, run control, and agent coordination

### Goals
- Preserve the challenge contract verbatim in tests and validators.
- Make execution reproducible, observable, and restartable.
- Prevent agents from racing over shared files and from producing incompatible interfaces.
- Honour root `AGENTS.md` logging: append only; never replace existing `log.txt`; do not log API keys, tokens, credentials, or sensitive data.

### Implementation
1. Read repository `AGENTS.md` in full before changing code. Start the coding IDE/agent from the repo root so the transcript logging instruction is active.
2. Create `code/config.py`: repository-relative paths, input files, evidence root, model provider/model names from environment/config, batch size, timeout, retry ceiling, concurrency limit, cache path.
3. Create an execution manifest recording version, timestamp, config fingerprint, model settings (no secrets), input-file hashes, prompt hashes, and output hash.
4. Make every module deterministic where possible. Sort all externally visible collections.
5. Use structured logging to `stderr` or a log file in `code/`; keep root `log.txt` reserved for the agent transcript required by the repo instructions.
6. Establish a short integration heartbeat: after every cluster merges, run unit tests + a tiny end-to-end run before the next cluster starts.

### Acceptance tests
- Program works from repository root and from another working directory (paths resolved from source/repo root, not current directory).
- No secrets in source, output, logs, usage report, or zip.
- Re-running with the same inputs/config produces byte-identical `output.csv` (except explicitly separate timestamped run metadata).
- Running twice does not duplicate agent log entries for the same turn; append only as specified.

---

## 4. Cluster C1 — Ingestion, evidence inventory, and extraction

**Highest-risk failure:** relying on only `system_activity.csv` and ignoring unstructured evidence or SQL replicas.

### Agent responsibilities
- Load all CSVs with explicit encoding, predictable NA handling, and preserved string identifiers (leading zeros can matter).
- Read `evidence_files.csv`; resolve each path under `dataset/evidence/`; fail visibly on missing files rather than silently skipping.
- Enumerate all evidence recursively and calculate SHA-256 content hashes.
- Extract evidence into one common registry while retaining file provenance, timestamp, parser/version and warnings.
- Handle plain text, JSON, CSV, logs, PDF, images, and SQLite-compatible `.sql` snapshot exports.

### Recommended extractor order
1. Plain text / logs / JSON / CSV using standard library.
2. SQL exports: load a copy into isolated temporary SQLite or parse safely; never execute arbitrary non-dataset SQL against the machine.
3. PDFs using installed local libraries if present; inspect extraction quality.
4. Images: use available OCR only when needed; inspect low-confidence output. Do not make up OCR content.
5. Preserve raw snippets/line numbers and originating `file_id` so evidence references can be traced.

### Key data design
`EvidenceRegistry` should support:
- exact identifier lookup;
- system/resource lookup;
- temporal query by capture time / event time;
- category and file type filters;
- query for all records that include a subject's identity-graph tokens;
- SQL table/row snapshots with explicit source replica and snapshot timestamp.

Do not bind a whole file to a case just because the manifest timestamp looks relevant. The manifest is a file locator, not case-level ground truth.

### SQL / replica handling
- Create a safe snapshot reader: each `.sql` file is loaded into an isolated temp SQLite database if compatible.
- Enumerate tables and columns before querying. Use parameterized queries for values.
- Distinguish primary, regional, reporting, and failover replicas from grounded table/metadata evidence.
- For a state query, capture `(source_file_id, replica/system, resource_id, row contents/fields, snapshot_at)`.
- Compare state across replicas; an account missing in the primary but present in a reporting/failover replica is residual data, not successful deletion.
- If parsing/execution fails, retain a warning and try textual extraction; never silently equate an unreadable file with no evidence.

### Tests
- Every manifest entry resolves to a file or triggers a clearly recorded error.
- A known test SQL file can be loaded and queried; duplicate rows / table names are handled.
- JSON/log lines parse independently; one malformed line cannot destroy an entire file.
- Each extracted claim can map back to its `file_id`.
- A later evidence capture cannot be used as the state at an earlier audit cutoff.

---

## 5. Cluster C2 — Deletion intent, identity resolution, and temporal aliases

These are separate decisions: **does this message authorize deletion?** and **which subject/identifiers does it refer to?** Do not conflate them.

### C2A. Intent classifier
Use a hybrid:
- deterministic patterns for obvious intent/non-intent;
- one low-cost LLM structured classifier for ambiguous/multilingual language;
- strict JSON/schema validation;
- a second review call only for low-confidence/contradictory outcomes, never for all 518 by default.

The response should be limited to fields like:

```json
{
  "authorizes_deletion": true,
  "intent_type": "ACCOUNT_DELETION",
  "confidence": 0.97,
  "evidence_spans": ["..."],
  "ambiguity_reason": ""
}
```

The LLM labels intent; it does **not** determine deletion status or select the final steps. Access, correction, export, marketing opt-out, and ambiguous non-deletion requests must not be treated as deletion authorization. Interpret the request’s language rather than relying only on English keywords.

### C2B. Identity graph
- Build indices from `user_identities.csv` and `user_audit_log.csv`.
- Start with the request email; resolve it as either current or historical.
- Recover all current and past emails, phone numbers, email hashes, stable `user_id`, relevant alias validity intervals, and audit evidence IDs.
- Normalize emails safely (trim whitespace and case-fold); do not perform unsafe fuzzy email matches.
- Maintain a bidirectional mapping from alias → candidate `user_id`, and user → alias history.
- Check for collisions: if an alias maps to multiple users across time, require timestamp-consistent evidence and corroboration.
- Use `requested_at` and identity history to avoid associating a historical identifier with the wrong user.
- The identity graph must store provenance: each alias is backed by an audit or identity row, not simply generated from text.

### Acceptance tests
1. Current email resolves to the right stable subject.
2. Historical email resolves to the same subject and discovers both old/current aliases.
3. Hash-only record is found when the hash is supported by source data and the hash scheme is understood.
4. Same/similar email belonging to a different user does not get included.
5. Alias collision is not resolved by fuzzy score alone.
6. A non-deletion request yields `NOT_APPLICABLE` even when a user exists.
7. A multilingual delete request is correctly recognized.
8. When evidence is newer than `audit_at`, it is not used to rewrite the historical answer.

---

## 6. Cluster C3 — Case reconstruction and evidence graph

This is the core of the challenge. Avoid “search for email and count hits” as the entire algorithm.

### Per-case state machine
1. Parse `audit_at`; set it as the hard cutoff.
2. Determine deletion intent. If not authorized, return `NOT_APPLICABLE` / `[]` immediately.
3. Resolve subject identity graph from request email and history.
4. Gather all system-activity rows related to stable user IDs, current/historical aliases, hashes, and evidence-derived resource IDs.
5. Gather evidence-file claims matching subject identifiers and relevant system/resource IDs.
6. Remove evidence events/captures that occur after `audit_at`. For activities, use event timestamps; distinguish `occurred_at` from `recorded_at` when both exist.
7. Reconstruct the likely current state of each resource as-of the cutoff. A reported workflow status or activity row’s `result` is an assertion, not proof.
8. Build data-flow edges among sources and destinations: sync, import, index, export, replica, backup, restore, and similar processes.
9. Apply policy to each relevant record, active flow, restore capability, and retained record.
10. Produce candidate findings plus evidence-backed verification candidates.
11. Feed candidates through policy planner and canonical ordering.
12. Validate; if uncertain or internally contradictory, run the ambiguity resolver and then revalidate.

### Evidence graph representation
Nodes:
- subject / alias;
- system;
- resource / record;
- activity event;
- evidence document / SQL row;
- policy rule;
- data-flow process;
- backup/restore artifact.

Edges:
- `IDENTIFIER_OF`, `ALIAS_OF`, `OBSERVED_IN`, `PROVES_STATE`,
- `COPIES_TO`, `RESTORES_TO`, `INDEXES_FROM`, `EXPORTS_FROM`,
- `GOVERNED_BY`, `RETAINED_BY_POLICY`.

Each edge should have provenance, timestamp, and match strength. The graph is internal; output only the allowed steps.

### Temporal state policy
Evidence has different semantics:
- a record snapshot proves presence/absence at capture time within scope;
- an operation log shows an action was attempted/reported;
- a completion dashboard shows a workflow claim;
- a replication/sync activity can show a possible data path but not necessarily prove a resource’s final state;
- a backup artifact indicates restoration risk only if available evidence supports a usable restoration path;
- an attachment/document may identify a subject indirectly and must be joined carefully.

Implement a temporal resolver that asks “what evidence proves the state at `audit_at`?” not “what is the latest thing I found?” Newer evidence is excluded; older evidence may become stale if intervening events contradict it. Make assumptions explicit and conservative.

### Evidence resolution fallback
- Exact, direct evidence → deterministic.
- Evidence references a resource but identity link is indirect → corroborate with identity history, event sequence, and time.
- Conflicting sources/replicas → preserve both claims, establish timestamps/authority, then mark incomplete if a prohibited copy is demonstrably present at cutoff.
- Insufficient evidence to prove a state must not be presented as proven absence. Use an appropriately conservative status and only steps the contract can represent; don't fabricate evidence IDs.
- Global unrelated records are decoys: inclusion requires a justified identity relationship, not merely an approximate name match.

### Tests
- Activity with `event_time > audit_at` has no influence on the result.
- Earlier delete event plus later pre-cutoff sync/import/restore correctly reopens risk.
- A successful primary delete does not hide a present replica.
- A dashboard `COMPLETE` never overrides a present record.
- A log of an attempted deletion does not by itself prove absence.
- A record with another user's similar email is not claimed as subject data.
- A file that mixes multiple customers yields only the matching record/resource, not every row in the file.

---

## 7. Cluster C4 — Policy engine and remediation-plan compiler

Load `dataset/deletion_rules.csv` as data. Do not hardcode policy outcomes in case-number branches. Policy is a decision table and must be auditable.

### Policy map from the supplied rules

| Data category | Condition | Required treatment | Planning implication |
|---|---|---|---|
| `USER_PROFILE` | Always | DELETE | Delete if present |
| `SECONDARY_IDENTIFIER` | Always | DELETE | Remove old identifiers/copies |
| `SEARCH_INDEX_ENTRY` | Always | DELETE | Remove matching index entry and verify |
| `CACHE_ENTRY` | Always | DELETE | Remove cache entry and verify |
| `SUPPORT_MESSAGE` | Shared customer/agent/order context must remain | REDACT | Preserve conversation, redact subject PII |
| `SUPPORT_ATTACHMENT` | Belongs only to subject | DELETE | Remove attachment and verify |
| `ANALYTICS_PROFILE` | Still attributable | ANONYMIZE | Anonymize and verify unlinkability |
| `PRODUCT_TELEMETRY` | Still attributable | ANONYMIZE | Anonymize and verify unlinkability |
| `USER_EXPORT` | Always | DELETE | Delete stale download/export |
| `MARKETING_PROFILE` | Always | DELETE | Delete profile |
| `EMAIL_DELIVERY_LOG` | Contains subject email | REDACT | Keep permissible log but redact email |
| `SECURITY_AUDIT_LOG` | Within retention period | RETAIN | Verify required record remains |
| `LEGAL_HOLD_RECORD` | Supplied legal hold active | RETAIN | Verify record remains; never delete against active hold |
| `BACKUP_ARCHIVE` | Restore access can be blocked | RETAIN | Block restore path; verify blocked |
| `ACCOUNT_TOMBSTONE` | Only stable internal `user_id` needed | REDACT | Keep user_id; remove name/email/phone/email hash/direct identifiers |

### Planning algorithm
- Convert every supported residual/risk into a `Finding`.
- Convert its data category + policy condition into exactly one required treatment.
- For incomplete cases, construct a dependency graph; actions to stop copying/restoring must precede downstream cleanup:
  1. disable relevant active data flows;
  2. block affected restore paths;
  3. process downstream copies before source records where dependency information supports that order;
  4. delete/redact/anonymize remaining resources per the rule;
  5. append verification checks for every changed/resource/flow/restore/policy-retained state required to establish completion.
- Do not delete a policy-required retained record. Include `VERIFY_RECORD_PRESENT` for such a record when it is part of the relevant audit plan.
- For `ACCOUNT_TOMBSTONE`, use `REDACT_RECORD` and verify `VERIFY_RECORD_REDACTED` unless evidence shows the tombstone already meets policy; stable `user_id` may remain, direct identifiers must not.
- Plan should not say a future step has already succeeded. `INCOMPLETE` remediation and future verification use empty `evidence_id`.

### Completion predicate
Mark `COMPLETE` only if all are true:
1. Every relevant resource’s observed state matches its required policy treatment.
2. No prohibited direct identifiers remain in retained/redacted records.
3. Required records remain available.
4. No active sync/import/replication path can recreate prohibited data.
5. No usable restore route can reintroduce prohibited data (or the supplied rule explicitly allows retained backup with restore blocked).
6. Evidence supports the required claims at or before `audit_at`.

If any policy violation or active recreation/restore risk is proven, status is `INCOMPLETE`. Do not treat a reported workflow status as the predicate.

### Step semantics
- Findings cite the evidence record that proves the issue.
- For a `COMPLETE` case, include only verification steps with real evidence IDs.
- For an `INCOMPLETE` case, findings cite real evidence; all proposed remediation and future verification steps use `""`.
- Add verification of the **subject account/profile absence** where required by the plan, not only the residual records.
- Verification must cover each planned remediation and any flow/restore disabled, and required retained records.
- Do not fabricate `evidence_id` strings just because the dataset has similarly named evidence files.

---

## 8. Canonical ordering — critical for sequence score

Sequence accuracy is explicitly evaluated. Make ordering code deterministic; never trust LLM-generated ordering.

### Phase order
`FINDING` → `REMEDIATION` → `VERIFICATION`.

### Finding order
Sort by:
1. operation order in the problem statement:
   1. `REPORT_REMAINING_RECORD`
   2. `REPORT_EXPOSED_PII`
   3. `REPORT_ACTIVE_DATA_FLOW`
   4. `REPORT_RESTORE_RISK`
2. `system_id` ascending
3. `resource_id` ascending

### Remediation order
The problem states both that ordering follows the operation table and that data flows/restores must be neutralized before dependent resource changes; respect both by applying **explicit dependency precedence first**, then use operation/system/resource tie-breakers only among steps that are dependency-compatible:
1. `DISABLE_DATA_FLOW`
2. `BLOCK_RESTORE`
3. downstream record actions before upstream sources where the evidence graph provides that dependency
4. `DELETE_RECORD`
5. `REDACT_RECORD`
6. `ANONYMIZE_RECORD`
7. `system_id`, then `resource_id`

If multiple non-dependent record actions exist, follow the operation ordering required by the contract, then alphabetical system/resource. Document this as a topological sort with deterministic tie-breaking.

### Verification order
Use the problem statement's operation order, followed by `system_id`, `resource_id`:
1. `VERIFY_RECORD_ABSENT`
2. `VERIFY_RECORD_PRESENT`
3. `VERIFY_RECORD_REDACTED`
4. `VERIFY_RECORD_ANONYMIZED`
5. `VERIFY_DATA_FLOW_DISABLED`
6. `VERIFY_RESTORE_BLOCKED`
7. `system_id`
8. `resource_id`

### Step-number assignment
Only after all candidate steps are finalized and sorted:
```python
for index, step in enumerate(steps, start=1):
    step["step_no"] = index
```
Never assign step numbers before sort, deduplication, or final validation.

---

## 9. Structured AI use — where models help and where they must not decide

### Recommended bounded roles
1. **Intent model:** classify authorization and language only.
2. **Document evidence extractor:** convert unstructured artifacts into cited factual claims; each claim must include `file_id`, quote/snippet or row locator, timestamp if present, system/resource/category, and confidence.
3. **Ambiguity reviewer:** review only cases with conflicting evidence, weak identity resolution, low OCR quality, or uncertain policy conditions.
4. **Optional auditor / critic:** independently inspect a proposed case graph and flag omitted relevant sources or invalid claims; cannot rewrite results without a structured justification and validator pass.

Avoid a generic "manager agent + researcher agent + judge agent" loop for every request. It burns time/tokens and creates disagreement. Prefer deterministic first pass, cached extraction, and selective escalation.

### LLM contract
- Structured JSON output only; validate against schema.
- The model must never produce arbitrary `system_id`, `resource_id`, `file_id`, `activity_id`, or `audit_id` unless found in retrieved evidence.
- Each claim includes exact evidence provenance; discard claims whose references do not resolve.
- Keep evidence snippets short and relevant; no huge dumps of every file per request.
- Retry once on malformed JSON using a repair prompt containing validation errors; after that mark the extraction uncertain and do not invent facts.
- Cache on `(model, prompt_version, normalized_input_hash, relevant_evidence_hash)`.
- Do not send secrets or credentials to model prompts.
- Set per-request timeouts and bounded concurrency. Handle rate limits with exponential backoff plus jitter.
- Keep real usage telemetry. Never fabricate token counts or cost estimates; if provider usage fields are unavailable, report that clearly and estimate with a declared tokenizer/method.

### Prompt 1 — intent classifier
Save to `code/prompts/intent.md`:

```text
You classify whether the user's message authorizes deletion of their account/personal data.
Do not assess whether deletion succeeded. Do not infer authorization from reported workflow status.
Access, correction, portability/export, marketing opt-out, and general questions are not deletion requests.
Requests may be multilingual or indirect; interpret the actual message.
Return strict JSON only:
{"authorizes_deletion": boolean, "intent_type": "ACCOUNT_DELETION"|"OTHER"|"AMBIGUOUS",
 "confidence": number, "evidence_spans": [string], "ambiguity_reason": string}
Quote minimal spans from the provided message. Do not add prose or fields.
Message:
{{request_text}}
```

### Prompt 2 — evidence extraction
Save to `code/prompts/evidence_extraction.md`:

```text
You are an evidence extraction component, not the final decision-maker.
Extract only claims explicitly supported by the supplied excerpt. Treat workflow status
and operation result strings as claims, not proof of final state. Do not infer that a record
is absent merely because it is not mentioned. Respect the case audit cutoff:
{{audit_at}}. Ignore evidence recorded after that instant.
Return strict JSON:
{"claims":[{"claim_type":"RECORD_STATE|PII_EXPOSURE|DATA_FLOW|RESTORE_PATH|RETAINED_RECORD|IDENTITY_LINK",
"system_id":"", "resource_id":"", "data_category":"", "observed_state":"",
"event_at":null, "captured_at":null, "evidence_id":"", "locator":"",
"matched_identifier":"", "confidence":0.0, "supporting_excerpt":""}],
"warnings":[]}
Evidence IDs must be copied exactly from the supplied participant-facing metadata.
If no supported claim exists, return an empty claims array.
Excerpt:
{{evidence_excerpt}}
Subject identity tokens:
{{resolved_identifiers}}
```

### Prompt 3 — ambiguity reviewer
Save to `code/prompts/ambiguity_review.md`:

```text
Audit the proposed case reconstruction for false joins, temporal leakage, policy mistakes,
missing dependent systems, and unsupported claims. You are a critic, not an output generator.
For each proposed claim, classify KEEP / REJECT / NEEDS_CORROBORATION and explain which
supplied evidence supports that action. Never invent evidence IDs or identifiers.
Check: subject identity, audit_at cutoff, reported-status-as-claim, policy treatment,
flow/restore risk, allowed operation/state, and phase ordering.
Return strict JSON:
{"review":[{"claim_key":"","decision":"KEEP|REJECT|NEEDS_CORROBORATION",
"reason":"","evidence_ids":[]}],"missing_checks":[]}
Inputs:
{{case_graph}}
{{candidate_plan}}
```

---

## 10. Cluster C5 — output compiler, validation, evaluation, and usage report

### CSV generation
- Use Python `csv.DictWriter` with `newline=""` and `encoding="utf-8"`.
- Use `json.dumps(steps, ensure_ascii=False, separators=(",", ":"))`.
- Let `csv` quote embedded commas/quotes in JSON correctly; do not hand-build CSV strings.
- Sort output by `case_id` as strings unless the dataset/test proves a different explicit canonical ordering is expected.
- Never write predictions into `dataset/output.csv`; write root `output.csv` per instructions.

### Validator must fail closed
Before submission, verify:
- exact column header and order;
- exactly 518 output data rows;
- no duplicate/missing/unexpected case IDs;
- each status is in the allowed set;
- every `steps_json` parses and is an array;
- each step contains exactly the required seven keys;
- `step_no` equals its 1-based index;
- phase order is monotonically nondecreasing;
- every phase/operation/state combination is in the allowed registry;
- `NOT_APPLICABLE` has zero steps;
- `COMPLETE` has only verification steps and non-empty valid evidence IDs;
- `INCOMPLETE` has findings if evidence-based issues were detected, remediation for each violation, and verification coverage;
- evidence IDs resolve to manifest/activity/audit IDs as appropriate;
- findings cite evidence and are at or before `audit_at`;
- all proposed remediation and future verification steps in incomplete cases have empty `evidence_id`;
- steps are sorted by canonical ordering rules and respect flow/restore dependency precedence;
- JSON is serialized inside valid CSV quoting.

Output validation is a correctness gate, not the evaluation report. A structurally valid file can still be semantically wrong.

### Evaluation workflow
1. Run `python3 code/main.py --sample` or the chosen equivalent on the 32 sample requests.
2. Compare against `dataset/sample_output.csv` with normalized JSON objects but **do not** ignore step-array order.
3. Report:
   - case-level status accuracy;
   - exact-case accuracy (status + full ordered steps);
   - step-level precision/recall/F1 if you implement a reliable matching definition;
   - field-level accuracy by field (`phase`, `system_id`, `resource_id`, `operation`, `state`, `evidence_id`);
   - sequence accuracy;
   - invalid JSON/contract error count;
   - missing/extra findings and unsupported evidence references.
4. Create at least three custom evaluation cases (preferably five):
   - non-deletion request in another language → `NOT_APPLICABLE`, `[]`;
   - old email mapped to a current identity, with a residual historical alias copy;
   - primary deleted but reporting/failover replica still present;
   - active sync recreates deleted profile after deletion event but before audit time;
   - tombstone with only `user_id` retained is compliant, while direct PII in tombstone is incomplete.
5. Test temporal edge cases (event exactly at `audit_at`, event just after, evidence captured later than cutoff).
6. Run a complete 518-case output only after the sample gate passes.
7. When fixing a bug, rerun both sample and custom tests; don't only patch the case that exposed it.
8. Fill `evaluation_report.md` with actual numbers and examples, failed cases, root causes, and improvements. Do not claim perfect metrics unless measured.
9. Fill `usage_report.md` from actual provider telemetry for the final full-dataset run. Required: model provider/name, call count, input/output/total tokens, average tokens per request, estimated overall/per-request cost, and per-model totals. Explain pricing date/source and estimation method. If no model/API was used for the run, state that plainly and record zero calls/tokens/cost rather than inventing measurements.

### Useful metrics
Let `N = number of cases`, `correct_status` count statuses that match, `exact_case` count cases whose status and full ordered `steps_json` match.

- `status_accuracy = correct_status / N`
- `exact_case_accuracy = exact_case / N`
- `sequence_accuracy = cases with exact expected step sequence / N` (for public sample/custom truth only)
- `invalid_output_rate = invalid cases / N`
- `evidence_grounding_rate = output evidence references valid and timely / all cited output evidence references`

For hidden cases no oracle labels are available locally; do not claim accuracy from self-consistency checks alone. Use sample/custom ground truth plus honest structural and evidence-grounding diagnostics.

---

## 11. Critical integration workflow under time pressure

Use short, hard-gated milestones instead of sequentially polishing everything.

### Gate A — 10% of remaining time: contract + sample
- Read `AGENTS.md`, problem statement, rules, sample output.
- Create schemas/enums and a tiny output validator immediately.
- Prove the CSV/JSON contract on `sample_output.csv`.
- Commit only after tests pass.

### Gate B — 25%: deterministic data foundation
- Load every CSV and index identities, activity, manifest.
- Inventory all evidence file types/counts.
- Extract text and inspect SQL snapshots.
- Build exact identity alias indices and cutoff filtering.
- Produce a per-case evidence inventory for a small sample of cases.

### Gate C — 50%: evidence reconstruction + policy
- Implement request intent, identity linking, state reconstruction.
- Apply policy to every relevant residual/resource/flow/backup/retained record.
- Build canonical plan and ordering.
- Compare a representative sample including all major scenarios.

### Gate D — 70%: full-dataset first run
- Generate predictions for all requests.
- Validate all structure/evidence constraints.
- Prioritize errors by likely hidden-test impact: intent, false identity join, time leakage, missing evidence types, policy/tombstone/retain handling, operation order, invalid IDs.

### Gate E — 85%: targeted improvement
- Add only bounded model escalation for genuinely ambiguous cases.
- Cache every extraction.
- Run custom cases + full structural validator.
- Refresh evaluation report and usage report from real run.

### Gate F — 95%: freeze and package
- Freeze prompts/config/model version.
- Run full job from clean state.
- Hash input, prompt and output; inspect diff.
- Build `code.zip`, inspect its contents, and ensure it contains `README.md`, `code/`, `code/evaluation/evaluation_report.md`, `code/evaluation/usage_report.md`.
- Exclude dataset, `.venv`, `node_modules`, build caches and nested archives.
- Upload the exact root `output.csv`, `code.zip`, and root `log.txt` in their respective slots.
- Submit before the deadline; leave time for upload/AI interview.

### Stop-doing list
- No polished frontend/dashboard unless the challenge explicitly asks for one (it does not).
- No elaborate autonomous multi-agent orchestration for every case.
- No fine-tuning or dataset synthesis that consumes the remaining time.
- No manually hardcoded case IDs or inferred case families.
- No giant prompts containing every evidence file for every request.
- No “absence from search results means deleted.”
- No success claims based only on the workflow's `COMPLETE` field.
- No fake evaluation/token counts.

---

## 12. Parallel agent deployment plan

Run agents in parallel only after agreeing on the schema above. All should work in the same repository only if edits are disjoint; otherwise use separate branches/worktrees. Integrator owns merges and can reject interface drift.

### Agent A — Evidence ingestion
**Deliver:** `ingestion/*`, `tests/test_sqlite_evidence.py`, a file inventory report.  
**Do not:** edit `main.py`, output serializer, identity policy, or final status logic.

**Agent prompt**
```text
Read AGENTS.md fully and follow its append-only root log rules. Implement Cluster C1 in
the plan. Inspect the actual repository and evidence tree before coding. Create robust
CSV/text/JSON/log/PDF/image/SQL evidence ingestion with provenance, content hashes,
warnings for missing/unreadable evidence, and safe SQLite snapshot loading. Do not assume
the manifest implies case membership. Preserve file_id and row/line locators. Add tests.
Use the shared schemas.py contract; if it is absent, propose an interface in a report but
do not independently invent a conflicting schema. Do not touch main.py or output logic.
Report exact files changed, test commands/results, extraction gaps, and TODOs.
```

### Agent B — Identity and temporal resolver
**Deliver:** `identity/*`, `reconstruction/temporal_state.py`, tests.  
**Do not:** create final output steps or alter intent/policy.

**Agent prompt**
```text
Read AGENTS.md fully. Implement identity resolution and temporal cutoff using supplied
user_identities.csv, user_audit_log.csv, and schema contract. Recover current/historical
identifiers and evidence provenance, handle alias collisions, and exclude any activity or
capture after each case's audit_at. No fuzzy-name-only identity joins. Add tests for old
email, collision, exact cutoff, and later evidence. Do not hardcode case IDs or infer labels.
Do not edit main.py, policy planner, or serializer. Report tests and uncertainty behavior.
```

### Agent C — Policy + plan compiler
**Deliver:** `policy/*`, ordering tests.  
**Do not:** parse raw evidence or produce final CSV.

**Agent prompt**
```text
Read AGENTS.md and problem_statement.md. Implement a rules-driven policy engine from the
actual dataset/deletion_rules.csv. Support DELETE, REDACT, ANONYMIZE, RETAIN, tombstones,
active flows, backup restore blocking, and required verification. Implement deterministic
phase/operation/system/resource ordering with explicit dependency constraints; number steps
only after final sort. Enforce exact allowed operation/state pairs. Add tests. Never use
case-number branches or fabricate evidence IDs. Shared schema only; do not edit main.py.
```

### Agent D — Intent + extraction prompts / bounded LLM wrapper
**Deliver:** `reconstruction/intent_classifier.py`, `llm/*`, `prompts/*`, usage instrumentation.  
**Do not:** make final status decisions or invent evidence.

**Agent prompt**
```text
Read AGENTS.md first. Implement a bounded, optional LLM layer for deletion-intent classification
and unstructured evidence extraction. Require strict validated JSON, provenance IDs copied from
input evidence, caching, timeout/retry limits, model-call/token telemetry, and deterministic
fallbacks. Model output cannot decide final deletion status or invent identifiers/evidence IDs.
Keep prompts in source Markdown. Avoid new dependencies unless needed and justified. Add tests
using mocked provider responses, malformed JSON, no credentials, retries, and cache hits.
Do not edit main.py or serializer.
```

### Agent E — Integration QA and evaluator
**Deliver:** `output/validator.py`, tests and evaluator/report templates.  
**Do not:** make semantic assumptions to force sample score.

**Agent prompt**
```text
Read AGENTS.md, README.md, problem_statement.md, and sample_output.csv. Implement a strict
validator and sample evaluator that checks row coverage/order, CSV/JSON schema, allowed enums,
consecutive step numbers, phase and operation ordering, evidence references, COMPLETE/INCOMPLETE/
NOT_APPLICABLE contracts, and empty evidence IDs on future actions. Compare JSON semantically
but preserve array order. Add at least five custom test scenarios and evaluation-report tooling.
Do not hardcode case labels or weaken validations to make output pass. Do not change main.py.
```

### Integrator / lead agent
**Deliver:** `main.py`, `schemas.py`, integration, full run, reports, final zip.
- Define schema and operation enums before assigning other agents.
- Ask agents for test results and interface signatures.
- Merge one cluster at a time; resolve conflicts explicitly.
- Keep a runnable baseline after every merge.
- Do not let multiple agents write `main.py`, `schemas.py`, or `log.txt` simultaneously.
- The lead alone signs off final semantic review and packages the submission.

If only one AI agent is available, use the same prompts sequentially in the order **E/schema → A → B → C → D → integration**, keeping the contracts fixed.

---

## 13. Suggested `main.py` execution pipeline

Keep it legible. Main should orchestrate, not contain business logic:

```python
def main():
    config = load_config()
    dataset = load_dataset(config.dataset_dir)
    rules = load_policy_rules(config.rules_path)
    registry = build_evidence_registry(config, dataset.evidence_manifest)
    identity_index = build_identity_index(dataset.identities, dataset.audit_log)

    results = []
    for request in sorted(dataset.requests, key=lambda r: r.case_id):
        if not intent_classifier.authorizes_deletion(request.request_text):
            results.append(not_applicable_result(request.case_id))
            continue

        subject = identity_resolver.resolve(request, identity_index)
        case_evidence = registry.retrieve_for_subject(
            subject=subject,
            activities=dataset.activities,
            audit_at=request.audit_at,
        )
        state_graph = reconstruct_state(
            request=request,
            subject=subject,
            evidence=case_evidence,
            audit_at=request.audit_at,
        )
        candidate = policy_engine.assess(state_graph, rules)
        result = plan_compiler.compile(candidate, request, subject)
        result = canonicalize_and_validate_case(result, registry, request.audit_at)
        if result.needs_review:
            result = bounded_ambiguity_review_and_revalidate(result, state_graph)
        results.append(result)

    output_path = config.repo_root / "output.csv"
    write_csv(results, output_path)
    validate_full_output(output_path, dataset.requests, registry, rules)
    run_or_update_evaluation(...)
    write_usage_report_from_actual_telemetry(...)
```

Treat this as an interface sketch, not literal drop-in code. Adapt signatures once actual modules are available. Important: create evidence and identity indices once; do not rescan every file from disk for every request.

---

## 14. Submission packaging and final checks

From the repository root on macOS/Linux:

```bash
python3 code/main.py
python3 -m unittest discover -s code/tests
python3 code/evaluation/main.py
python3 - <<'PY'
import csv, json
from pathlib import Path
p = Path("output.csv")
with p.open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 518, len(rows)
assert rows and list(rows[0]) == ["case_id", "deletion_status", "steps_json"]
assert [r["case_id"] for r in rows] == sorted(r["case_id"] for r in rows)
for r in rows:
    assert r["deletion_status"] in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
    steps = json.loads(r["steps_json"])
    assert isinstance(steps, list)
    assert [s["step_no"] for s in steps] == list(range(1, len(steps) + 1))
    if r["deletion_status"] == "NOT_APPLICABLE":
        assert steps == []
    if r["deletion_status"] == "COMPLETE":
        assert all(s["phase"] == "VERIFICATION" for s in steps)
print("Output contract smoke test passed.")
PY
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store" "*.zip" "*.tar" "*.gz"
unzip -l code.zip
```

Adapt the evaluator/test command to the actual repository. The `zip` command must not bundle `dataset/`, virtual environments, build caches, or nested archives. On Windows PowerShell, the equivalent basic packaging command is:

```powershell
Compress-Archive -Path .\README.md, .\code -DestinationPath .\code.zip -Force
```

Before upload:
- [ ] `output.csv` is at the repository root and passes strict validation.
- [ ] `code.zip` contains runnable source, prompts, `README.md`, both required reports under `code/evaluation/`.
- [ ] `log.txt` is the root agent trace expected by this challenge; verify format and redact secrets.
- [ ] No extra top-level dataset, `.env`, API credentials, real personal data, or nested archive in the zip.
- [ ] Reports cite actual measured runs and clearly state limitations.
- [ ] You can explain architecture, identity resolution, cutoff, false positives, policy logic, sequence ordering, and evaluation trade-offs during the AI Judge interview.

---

## 15. Interview-ready explanation (memorize the reasoning, not a script)

“Our system separates evidence extraction from the final decision. We build a temporal identity graph from current identities and audit history, then reconstruct the state at the request’s audit cutoff using structured activity and unstructured evidence, including database replicas. We treat operation logs and workflow statuses as claims rather than proof. A policy engine applies the provided treatment rules for deletion, redaction, anonymization, retention, tombstones, sync flows, and restore paths. A dependency-aware planner generates findings, then remediation, then verification, with deterministic ordering and evidence provenance. An independent validator enforces the output contract, and a sample/custom evaluation suite measures status, field, and sequence accuracy. Model calls are limited to semantic intent, messy-document extraction, and ambiguity review; deterministic code controls policy and serialization. We log actual provider usage and cost rather than estimating them from memory.”

Be candid about edge cases that remain uncertain. A technically credible explanation of measured limitations is better than claiming magical perfect accuracy.

---

## 16. Final priority stack

If time becomes severely constrained, preserve these in descending order:

1. **Contract validator + sample comparison** — catches submissions that cannot score.
2. **Identity history + hard `audit_at` cutoff** — crucial hidden-test dimensions.
3. **Evidence coverage including SQL snapshots and unstructured records** — avoids false completion and missed copies.
4. **Policy-driven residual/PII/flow/restore/tombstone/retention logic** — central business correctness.
5. **Canonical output sequencing + real evidence IDs** — explicit grading sensitivity.
6. **Three or more meaningful custom tests and honest evaluation report.**
7. **Token/cost instrumentation, clean packaging, log transcript.**
8. Only then add model ensembles, more elaborate graph analytics, or optional optimizations.

**Core principle:** evidence first, identity second, temporal reconstruction third, policy fourth, ordered plan fifth, output serialization last. A perfectly formatted `output.csv` is necessary—but evidence-grounded decisions are what make it competitive.
