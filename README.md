# Ledgerline — review-first accounting agent

A runnable **synthetic-only** workbench for a New York accounting practice: three fictional clients, document evidence, client-scoped categorization, conservative bank matching, balanced draft journals, a review desk, and confirmed idempotent **local mock** exports. No real bank connections, tax advice, filings, messages, spending, live ledger posting, or hosted deployment.

## Start locally (Python 3.12 recommended)

From this folder on macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
ledgerline demo --limit 33
uvicorn accounting_agent.api:app --host 127.0.0.1 --port 8000
# In a second terminal, activate the same environment:
streamlit run src/accounting_agent/ui.py --server.address 127.0.0.1
```

Open **http://localhost:8501**; API documentation is **http://localhost:8000/docs**. The included `demo_data/` corpus is ready to use; `ledgerline generate --out demo_data` recreates it. Demo loading creates drafts, **not approvals**. Review a category, save the revision with a reason, inspect evidence and exceptions, approve, explicitly confirm mock export, then download CSV. Bank allocations require separate explicit review.

### Windows / PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\ledgerline.exe demo --limit 33
.\.venv\Scripts\python.exe -m uvicorn accounting_agent.api:app --host 127.0.0.1 --port 8000
# Second PowerShell window in this folder:
.\.venv\Scripts\python.exe -m streamlit run src/accounting_agent/ui.py --server.address 127.0.0.1
```

No activation-policy change needed. Windows startup commands are supplied but were **not executed on Windows**. Exact versions tested on Linux/Python 3.12 are in `requirements-tested.txt` (not a cross-platform lockfile).

### Local demo credentials

`demo-accountant-token` grants the three demo clients; `demo-cafe-token` grants only `cafe`. The server derives reviewer identity and permitted clients from the token, never from a submitted reviewer/client claim. Tokens are intentionally public **demo credentials**, not production authentication. Bind to loopback. Override `LEDGERLINE_TOKENS` with a JSON token-to-grant map if testing additional identities; do not publish credentials or real data.

## What is implemented

- PDF/image/text intake, bounded background jobs, source retention, rendered pages and field-level evidence beside editable fields. **Unchanged shared `docpipe` is genuinely used** for PDF rendering/grounding; provenance hashes in `DOCPIPE_PROVENANCE.json`.
- Offline labeled-template parser **explicitly not an LLM**. Optional tool-free OpenAI-compatible text/vision provider with configurable model/prices, timeout, retries, bounded low-confidence reread, tenant-scoped payloads and Decimal budget reservations. `ACCOUNTING_BUDGET_DB` makes caps durable across restarts/workers.
- Categorization order: manual vendor rule → consistent currently approved client history → configured LLM → abstain. Client-only COA; corrections revoke the old approval and automatically remove its learned mapping.
- Decimal amounts only; A01–A12 validations, exact/fuzzy duplicate review, gross-expense treatment of nonrecoverable purchase sales tax, reversed credit notes, balanced accrual journals. No auto-approval.
- Strict bounded CSV and XML OFX import; SGML OFX, entities, ambiguous rows and duplicate IDs rejected. Unique deterministic matching checks currency, payment direction, exact amount, date, vendor and reference. Top-three suggestions; **even high-confidence matches do not post or allocate**.
- Explicit reviewed one-to-many, partial and difference-account settlements. Atomic bank/document allocations prevent reuse and overpayment. Correction invalidates affected settlements. Settlement journals are stored/audited but **not included in document export**; export covers approved accrual journals only.
- Versioned corrections, stale-version protection, mandatory review reasons, immutable old audit/export records, period locks rechecked at approval and export. Financial config/COA changes revoke approvals and settlements. Confirmed mock exports use a tenant+document+revision+journal idempotency key.
- Multi-client Streamlit dashboard, severity queue, rendered evidence, correction/approval/export, advanced settlement JSON, period lock and audit trail. API supplies manual vendor rules and advanced financial configuration.
- SQLite local demonstration and real PostgreSQL FORCE RLS migration/non-owner-role enforcement. These are **different isolation guarantees**; see below.

## New York configuration and currency

`config/new_york_demo.json` documents USD base and an illustrative USD/EUR support profile. The sample allowlist (`0`, `4%`, `8%`, `8.875%`) is **not a legal tax table**. NY state tax is one component; local rates, taxability and exemptions require reviewer determination. NYC's 8.875% is an example, not a statewide default. Purchases default to tax included in expense, **not input VAT recovery**. Official context: [NY rates](https://www.tax.ny.gov/bus/st/rates.htm), [NYC sales tax](https://portal.311.nyc.gov/article/?kanumber=KA-01675), [NY exemption certificates](https://www.tax.ny.gov/pubs_and_bulls/tg_bulletins/st/exemption_certificates_for_sales_tax.htm).

EUR requires an explicit positive dated rate keyed `YYYY-MM-DD:EUR` before approval/export. Journals currently remain in **source currency**; base-USD translation, FX revaluation and realized gains/losses are **not implemented**. No cross-currency automatic match. Do not treat this as a production multi-currency ledger.

## Evaluation — measured, not aspirational

```bash
pytest -q
ledgerline eval run --split dev
ledgerline eval run --split heldout
ledgerline eval run --split all
```

The supplied corpus has **412 documents, 3 clients × 12 months, 36 raster/degraded PDFs, 10 adversarial documents**, separate ground truth, and consistent noisy bank CSVs. January–September are development history; October–December are chronological test rows. Some stage metrics use normalized truth inputs to isolate category/matching logic; they are **not end-to-end extraction-to-posting metrics**. No ground truth is passed into extraction. Warm history is explicitly simulated prior approved data, not actual accountant review.

See [`reports/eval-all-offline.md`](reports/eval-all-offline.md), [`reports/eval-heldout-offline.md`](reports/eval-heldout-offline.md), and the preserved [`baseline-heldout-offline.md`](reports/baseline-heldout-offline.md). Offline extraction misses scans, cold-start categorization abstains, and no real-model corpus cost/accuracy or human time-saving claim is made. Matching weights were revised after inspecting the original held-out recall; later results on that split are **reused-split development/regression diagnostics, not pristine holdout validation**. Do not claim all specification success criteria met.

The original host-model smoke inputs and labels are separate in `reports/host-smoke/`; the parent build's two-image smoke is a tiny capability check, not a provider integration or statistically meaningful accuracy evaluation. Test and runtime reports cover accounting invariants and API/UI startup. Load safety uses 200 synthetic in-memory-style draft records across five tenants; it is not a measured human productivity study or OCR/provider throughput claim.

## PostgreSQL and Docker

```bash
docker compose up --build
# Optional populate container's RLS-backed database:
docker compose exec api ledgerline demo --limit 33
```

Postgres 16 runs the actual migration with owner credentials; the application connects as `ledgerline_app` (non-owner, NOSUPERUSER, NOBYPASSRLS). Each transaction sets tenant context locally, acquires a tenant advisory lock, and RLS checks both reads and writes. Generic `records` has tenant-scoped JSON entities plus a tenant/payload consistency check; this is a prototype schema, not every normalized table from the specification.

`tests/test_postgres_rls.py` deliberately queries without repository tenant predicates and attempts a foreign write. Set `TEST_DATABASE_URL` to a **running migrated non-owner** database to execute it. This build environment lacked PostgreSQL/Docker binaries, so this test is **skipped and Docker runtime unverified**. SQLite uses application predicates and transaction locks: **it does not implement database RLS**. MinIO/encryption-at-rest integrations are not implemented. Compose's known passwords are local synthetic defaults only; existing volumes do not rerun initialization SQL.

## Configurable LLM

Set environment variables from `.env.example` yourself; the app does not implicitly load `.env`. Model names and price estimates are supplied by the user. No model credentials are bundled. A configured provider enables LLM categories; choose `llm` or `vision` in upload for extraction. Calls have no tools and treat text as untrusted data. Cost reservations conservatively remain charged after uncertain/retried calls; actual usage metadata is logged without source text. Set `ACCOUNTING_BUDGET_DB=data/provider-budget.sqlite` for durable limits. Without it, reservations are per process only.

## Explicit remaining work

Not production-ready: no authenticated firm/user provisioning, encrypted object storage, retention/deletion enforcement, external immutable audit sink, real accounting connector, IMAP/watched inbox, durable distributed job queue, automatic OCR grounding, complete tax engine, source-to-base FX accounting, or external deployment. Restarted queued jobs are failed closed for resubmission. Source files and SQLite remain local plaintext; use **synthetic data only**. No v1.1 onboarding/deadline/close-pack modules: MVP accuracy gates have not all passed. No live demo, public repo publication, recorded demo video or measured manual-vs-review case study is claimed. A walkthrough script and honest case study are included; publication/recording would be separate work.

Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Extraction: [`docs/EXTRACTION.md`](docs/EXTRACTION.md). Review examples: [`docs/DEMO.md`](docs/DEMO.md).
