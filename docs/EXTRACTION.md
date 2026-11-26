# Extraction and bank import boundaries

`extract_document(path, client_id, backend='offline', provider=None)` uses the
unchanged freight `docpipe` snapshot for PDF resource limits, rendering and
full-source-line grounding. SHA-256 hashes are in `DOCPIPE_PROVENANCE.json`.
Accounting-specific normalization/classification is an adapter, not a replacement
copy of the freight parser. PDF, UTF-8 text, PNG and JPEG intake is supported.

The default `offline-template-parser-v1` is a labeled deterministic parser of
`Label: Value` documents, **not an LLM accuracy demonstration**. It does not read
images. Scans and unknown documents fail closed to review. LLM mode sends page
images and available text through a tool-free OpenAI-compatible chat-completions
API. Extracted scan fields remain ungrounded until human evidence review because
this version has no OCR text-verification service. A substring of a field value
is not accepted as grounded evidence. Conflicting repeated fields are flagged.

Configure `ACCOUNTING_LLM_BASE_URL` (HTTPS), `ACCOUNTING_LLM_API_KEY`,
`ACCOUNTING_LLM_MODEL`, `ACCOUNTING_INPUT_PRICE`, `ACCOUNTING_OUTPUT_PRICE` (USD
per million tokens), and `ACCOUNTING_CLIENT_COST_CAP`. Prices are illustrative
configuration, not current provider quotes. Supply a retained `Provider` instance
to extraction/categorization. Reservations are conservative and failures are
charged; usage over a reservation fails closed. Set `ACCOUNTING_BUDGET_DB` to a SQLite file in an existing writable data
directory for durable, atomic per-client reservations across workers. Without
that setting the cap is **process-local**, not a durable billing or cross-worker
limit. Reservations are lifetime cumulative until an administrator deliberately
rotates the local budget ledger; there is no silent daily reset. Provider metadata logs contain
client ID, model, tokens, estimated cost and latency, not document content or
credentials. Runtime integrations must persist and aggregate these records.
A provider call never receives other clients' history or chart of accounts.

Categorization order: explicit tenant vendor rule → consistent approved,
non-invalidated tenant history → configured LLM → abstention. Offline cold-start
categorization is deliberately zero coverage, not a fabricated model score.
Provider errors and out-of-COA suggestions return review-required abstentions.

Bank CSV is UTF-8 with exact headers `date,amount,currency,description` plus
optional `reference,id`. Amounts are signed decimal strings, at most two fraction
digits. `date` is ISO YYYY-MM-DD. Currency must be uppercase three-letter code;
allowed currencies are separately validated by jurisdiction configuration.
Imports reject malformed rows, nonfinite numbers and duplicate identifiers.
OFX accepts strict XML only, one statement currency, FITID per transaction;
legacy OFX SGML and namespaces are intentionally unsupported. XML entities and
DTDs are prohibited with `defusedxml`; import files are limited to 5 MB and
20,000 transactions. Matching interprets negative bank amounts as outflows.
