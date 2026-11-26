# Accounting Agent for Small Businesses and Multi-Client Accountants


## 1. Overview

**Problem.** Independent accountants and small-business owners spend much of each month chasing receipts, keying invoice data, categorizing transactions, and matching them to bank lines. The work is repetitive, error-prone, and scales linearly with client count.

**Solution.** A multi-client agent that ingests invoices and receipts, extracts and categorizes them, reconciles them against bank transactions, proposes balanced journal entries, and queues only exceptions for the accountant. It also chases missing documents and tracks client readiness against filing deadlines.

**Portfolio goal.** Show a multi-tenant, auditable finance agent end to end, for a fictional practice (Ledgerline Accounting, one accountant serving 25 small-business clients).

**Non-goals (MVP).** Tax advice or filing, payroll, real bank connections, real client data, autonomous posting to a live ledger.

## 2. Scope

| Module | Function | Phase |
|---|---|---|
| Intake and extraction | Per-client inbox and upload; invoice and receipt fields with evidence | MVP |
| Categorization | Map documents and lines to the client's chart of accounts | MVP |
| Bank reconciliation | Match bank transactions to documents, suggest or flag | MVP |
| Validation and journal entries | Rule checks, balanced draft entries, period locks | MVP |
| Review UI and export | Multi-client dashboard, exception queue, CSV/API export | MVP |
| Onboarding checklist chaser | Track required items per new client, draft reminders | v1.1 |
| Deadline tracker | Per-client filing calendar with readiness score | v1.1 |
| Month-end close pack | Summary of open items and draft client email | v1.1 |

## 3. Success Metrics

Measured on synthetic clients with known ground truth (section 9). Targets:

- Extraction accuracy on key fields (vendor, date, invoice number, net, tax, total, currency) ≥ 97%
- Categorization top-1 accuracy ≥ 85% at cold start and ≥ 93% with client history
- Auto-match precision ≥ 99% (a wrong match is costly); recall ≥ 85%
- Duplicate detection recall ≥ 95%
- Unbalanced journal entries: 0 (enforced in code)
- Cross-client data leakage: 0 (tested)
- Cost ≤ $0.03 per document
- Time-saving claim: time yourself processing one month of one synthetic client by hand, then compare with reviewer time in the UI

## 4. Architecture

**Flow:** Intake → docpipe (classify, extract) → normalize → deduplicate → categorize → reconcile with bank lines → validate → journal draft → review queue → export, with an audit log throughout.

| Component | Choice |
|---|---|
| Intake | Per-client email alias (IMAP), upload, watched folder; CSV/OFX bank statement import (mock bank feed) |
| Shared core | docpipe package from the freight agent: file handling, page rendering, classifier, extraction framework, evidence grounding, document viewer. If not yet built, build it here first |
| LLM | User-configurable
| Backend | Python 3.12, FastAPI, Pydantic, async worker |
| Database | Postgres with row-level security (client_id on every row) |
| Matching | RapidFuzz for names, deterministic scoring for amounts and dates |
| Config | Per-jurisdiction files: tax rates and rules, chart-of-accounts templates, filing calendar |
| UI | Streamlit or React: multi-client dashboard, exception queue, viewer beside fields |
| Observability | Structured logs, trace ID per document, token and cost counters per client |
| Deployment | Docker Compose; hosted demo with synthetic data only |

**Design principles**

1. The model reads and suggests; code computes and decides. Totals, tax math, balancing, and matching scores are code, never LLM output.
2. Strict tenant isolation: one client per LLM call, no cross-client context.
3. Learn from corrections: each approved entry becomes client history.
4. Draft first: nothing posts without approval until a client-level auto-approve is earned by eval results.
5. Every change is audited with reviewer, time, and reason. On any failure, fail closed to review.

## 5. Agent Workflow

1. **Ingest.** Store the original under the client, render pages, detect text layer or scan, classify (invoice, receipt, credit note, bank statement, other).
2. **Extract** (vision, no tools): vendor, invoice number, dates, currency, line items, net, tax breakdown, gross, payment terms; each with confidence, page, and evidence snippet. The evidence must be found in the text layer or the field is flagged.
3. **Normalize and check math.** ISO dates, currency codes, amounts as decimals. Verify net + tax = gross and line sums in code.
4. **Deduplicate.** Key on vendor, invoice number, amount, and date; also near-duplicate fuzzy match for re-sent or re-photographed documents.
5. **Categorize**, in order: (a) client vendor rules; (b) learned mapping from past approved entries; (c) LLM with the client's chart of accounts and the line descriptions, returning account, confidence, and a one-line rationale. Below threshold goes to review.
6. **Reconcile.** Match bank lines to documents (section 6).
7. **Validate.** Run rules (section 7) and build the draft journal entry; confirm it balances and the period is open.
8. **Review.** Accountant sees exceptions ranked by severity, per client, with source highlights; approves, corrects, or overrides with a reason. Corrections update vendor mappings.
9. **Export.** Approved entries to CSV and a mock accounting-software API with idempotency keys.

## 6. Reconciliation Design

- **Candidate generation.** For each bank line, select documents with amount within tolerance (exact by default, configurable for FX or fees), date within ±5 days of due or issue date, and fuzzy vendor match against the bank description.
- **Scoring.** Weighted score of amount, date proximity, name similarity, and reference-number hit. Auto-match only if score ≥ 0.95 and the match is unique; otherwise suggest top 3.
- **Compound cases.** One payment covering several invoices, one invoice paid in parts, bank fees and rounding differences (tolerance with a booked difference line).
- **Leftovers.** Unmatched bank lines older than 14 days produce a missing-document request; unmatched documents older than 45 days are flagged as possibly unpaid.
- **No LLM in scoring.** The LLM may be used only to read an ambiguous bank description into a likely vendor name, and its output feeds the same deterministic scorer.

## 7. Validation Rules

| ID | Check | Severity |
|---|---|---|
| A01 | Duplicate document (vendor, number, amount, date) | Critical |
| A02 | Net + tax = gross; line items sum to totals | Critical |
| A03 | Journal entry balances (debits equal credits) | Critical |
| A04 | Entry date falls in an open period, not in the future | Critical |
| A05 | Tax rate belongs to the jurisdiction's allowed set | Major |
| A06 | Currency supported and exchange rate available for the date | Major |
| A07 | Amount anomaly versus vendor history (above 3× median or outside configured band) | Major |
| A08 | New vendor, needs confirmation | Major |
| A09 | Mandatory fields present (vendor, date, total; invoice number where required) | Major |
| A10 | Category confidence below threshold or rationale conflicts with vendor history | Major |
| A11 | Bank line unmatched for more than 14 days | Minor |
| A12 | Document date far from bank date (over 60 days) | Minor |

Each rule is a pure function with unit tests per branch; thresholds live in config. Status per document: Ready, Needs review (any Major), Blocked (any Critical).

## 8. Data Model

| Table | Key fields |
|---|---|
| firms, users | id, name, role |
| clients | id, firm_id, name, jurisdiction, entity_type, vat_period, lock_date |
| chart_of_accounts | id, client_id, code, name, type |
| vendors | id, client_id, name, aliases, default_account, tax_profile |
| vendor_rules | id, client_id, match, account, source (manual or learned), hit_count |
| documents | id, client_id, type, storage_ref, status, hash |
| extractions | id, document_id, field, raw_value, normalized_value, confidence, page, evidence, grounded |
| bank_transactions | id, client_id, date, amount, currency, description, status |
| matches | id, transaction_id, document_id, score, method, status |
| categorizations | id, document_id, line_ref, account, confidence, rationale, source |
| journal_entries, journal_lines | id, client_id, date, status, line: account, debit, credit |
| exceptions | id, client_id, rule_id, severity, subject_ref, status |
| reviews | id, subject_ref, action, old_value, new_value, reason, reviewer, ts |
| tasks (v1.1) | id, client_id, kind, due_date, status, last_reminder |
| filing_calendar (v1.1) | jurisdiction, entity_type, filing, rule, offset_days |
| audit_log | id, client_id, step, input_hash, output_ref, model, cost, trace_id, ts |
| eval_clients, eval_runs | id, generator_seed, ground_truth, split, metrics, ts |

## 9. Guardrails

- **Tenant isolation.** Row-level security on client_id; each LLM call receives one client's data; automated test attempts cross-client access and must fail.
- **Prompt injection.** Document text is untrusted data; extraction and categorization calls have no tools; adversarial documents are in the eval set.
- **Money safety.** All arithmetic and matching in code; decimal types only; period locks enforced; no entry without a balanced, reviewed draft.
- **Approval.** MVP has no auto-posting. Auto-approve may be enabled per client and per vendor rule only after the held-out eval meets targets and the accountant opts in.
- **Advisory limits.** The agent suggests categories and tax treatments; it does not give tax advice or file returns, and the UI says so.
- **Data handling.** Synthetic data only in the portfolio; encryption at rest, retention config, secrets in environment variables, redaction of IBANs and personal data in logs.
- **Reliability.** Retries with backoff, timeouts, per-client cost cap, idempotent exports, fail closed to review.

## 10. Evaluation

**Dataset.** Generate three fictional clients (a café, a freelance designer, a small construction firm) with 12 months of activity each:

- About 400 documents (invoices, receipts including photographed and degraded ones) rendered from templates, with ground-truth fields and correct accounts
- Bank statements (CSV) consistent with the documents, with realistic noise: date offsets, truncated descriptions, combined payments, fees, rounding
- Seeded issues: duplicates, missing receipts, wrong tax math, anomalous amounts, locked-period dates, new vendors
- 10 adversarial documents (injected instructions, blank pages, non-invoice files)

**Split.** Chronological: months 1–9 are dev and serve as client history; months 10–12 are held-out. Report categorization both cold start (no history) and warm start (with history).

**Harness.** One CLI command (eval run) reports extraction accuracy per field, categorization accuracy cold and warm, match precision and recall, duplicate recall, per-rule results, cost, and latency; it prints failures and stores results in eval_runs. Run on every prompt, model, rule, or threshold change.

**Reporting.** Publish failures and limits in the README, including that results come from synthetic data.

## 11. Implementation Plan


**Phase 0: Foundation**

1. Set up repo, environment, Docker Compose (app, Postgres, MinIO), linting, CI with tests; import docpipe or scaffold it. *Exit:* docker compose up serves a health endpoint.
2. Build a SDK wrapper with retries, timeouts, token and cost logging per client, and model names from config. *Exit:* a test call logs its cost.

**Phase 1: Tenancy and configuration**

3. Create the schema with client_id on all tables and Postgres row-level security; write the cross-client access test. *Exit:* the test fails to read another client's rows.
4. Add jurisdiction config (tax rates, rules), chart-of-accounts templates, and the filing calendar; seed three clients.

**Phase 2: Synthetic data generator**

5. Build the ledger simulator: 12 months of transactions per client with known categories.
6. Render invoices and receipts from templates; degrade a share as photos.
7. Generate consistent bank statements with noise, then the seeded-issue injector. *Exit:* ground truth verified by reconciling three sample months by hand.

**Phase 3: Extraction**

8. Define invoice and receipt schemas with line items and tax breakdowns; write prompts with evidence and confidence.
9. Implement normalization, math checks, and the grounding check with unit tests.
10. Run the extraction eval; add low-confidence retry. *Exit:* key-field accuracy ≥ 97% on dev set.

**Phase 4: Categorization**

11. Implement vendor rules and learned mappings from approved entries.
12. Implement LLM categorization with the client's chart of accounts and rationale output.
13. Implement the correction feedback loop; run cold and warm evals. *Exit:* ≥ 85% cold, ≥ 93% warm on dev set.

**Phase 5: Reconciliation**

14. Implement bank import (CSV, OFX) and normalization.
15. Implement candidate generation, scoring, and compound-case handling.
16. Implement leftovers handling and missing-document requests. *Exit:* auto-match precision ≥ 99%, recall ≥ 85% on dev set.

**Phase 6: Validation and journal entries**

17. Implement rules A01–A12 as pure functions with unit tests per branch.
18. Implement draft journal entries, balance enforcement, and period locks. *Exit:* 0 unbalanced entries across the full dev set; duplicate recall ≥ 95%.

**Phase 7: Review UI and export**

19. Build the multi-client dashboard: open items per client, exceptions by severity, readiness at a glance.
20. Build the exception queue with viewer, evidence highlights, approve, correct, and override with reason.
21. Implement CSV and mock API export with idempotency keys. *Exit:* a clean month for one client goes from upload to export with only confirmation clicks.

**Phase 8: Hardening (Day 21)**

22. Run the held-out eval and adversarial documents; fix failures; freeze and tag prompt, rule, and threshold versions.
23. Load test with 200 documents across 5 clients; verify retries, cost caps, and isolation. *Exit:* section 3 targets met, or gaps documented.

**Phase 9: v1.1 modules**

24. Onboarding checklist chaser: per-client required items (registration documents, bank access, prior-period statements, authorizations), status tracking, reminder drafts shown for approval, never auto-sent.
25. Deadline tracker: compute due dates from the filing calendar per client; readiness score based on open exceptions, unmatched lines, and missing documents; alerts at 14, 7, and 2 days.
26. Month-end close pack: summary of open items per client and a draft client email. Give each module a 20-case mini eval.

**Phase 10: Packaging**

27. Deploy the demo with seeded synthetic clients and a read-only guest login; no real credentials.
28. Produce the architecture diagram, README (problem, results, limitations), and a 3-minute demo video showing one client's month closing end to end.
29. Write a one-page case study with the measured time comparison, stating that it uses simulated data.

## 12. Portfolio Deliverables

- Public repo with README and architecture diagram
- Live demo with guest access
- 3-minute demo video of a month-end close
- Eval report (extraction, categorization cold and warm, matching, duplicates, failure analysis)
- One-page case study
- Shared docpipe package, reused from the freight agent

## 13. Risks

| Risk | Mitigation |
|---|---|
| Synthetic data too easy, inflating results | Photo-style degradation, bank noise, adversarial cases, chronological held-out split, disclose limits |
| Wrong auto-match or category posted | Precision-first thresholds, no auto-posting in MVP, review queue, audit trail |
| Cross-client leakage | Row-level security, single-client LLM calls, automated isolation test |
| Jurisdiction differences | Tax and calendar rules in config, one jurisdiction implemented, others documented as extension |
| LLM arithmetic errors | All math in code with decimal types |
| Cost on photo receipts | Retry only when needed, per-client cost cap |
| Scope creep | v1.1 modules start only after MVP exit criteria |

## 14. Assumptions and Open Decisions

- One jurisdiction and currency pair implemented first; others via config.
- English-language documents only in MVP.
- Streamlit is the default UI.
- Bank data is imported from files; bank feeds, accounting-software exports, and reminder emails are mocked in the public demo.
