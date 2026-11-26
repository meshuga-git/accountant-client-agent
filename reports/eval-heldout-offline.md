# Synthetic accounting evaluation
Backend: `offline`. Split: `heldout` (reused development diagnostics, not untouched). Documents: 112.

## Measured stage metrics
```json
{
  "extraction_per_field": {
    "vendor": 0.911765,
    "date": 0.911765,
    "invoice_number": 0.911765,
    "net": 0.911765,
    "tax": 0.911765,
    "total": 0.911765,
    "currency": 0.911765
  },
  "extraction_micro_accuracy": 0.911765,
  "degraded_extraction_per_field": {
    "vendor": 0.0,
    "date": 0.0,
    "invoice_number": 0.0,
    "net": 0.0,
    "tax": 0.0,
    "total": 0.0,
    "currency": 0.0
  },
  "category_cold_accuracy": 0.0,
  "category_warm_accuracy": 1.0,
  "category_count": 99,
  "category_cold_abstentions": 99,
  "auto_match_precision": 1.0,
  "auto_match_recall_exact_eligible": 1.0,
  "auto_matches": 84,
  "exact_eligible_bank_lines": 84,
  "bank_lines": 99,
  "duplicate_recall": 1.0,
  "duplicate_cases": 3,
  "balanced_drafts": 99,
  "unbalanced_drafts": 0,
  "invalid_totals_rejected": 0,
  "adversarial_mandatory_field_abstention": 1.0,
  "adversarial_cases": 10,
  "cost_usd": "0",
  "cost_per_document_usd": "0",
  "total_tokens": 0,
  "latency_seconds_mean": 0.02025,
  "latency_seconds_p95": 0.0448
}
```
## Target checks
```json
{
  "extraction_97pct": false,
  "category_cold_85pct": false,
  "category_warm_93pct": true,
  "match_precision_99pct": true,
  "match_recall_85pct": true,
  "duplicate_recall_95pct": true,
  "zero_unbalanced": true
}
```
## Rule probes
A01–A12: all 12 positive/negative fixture probes passed. These are unit-like branch probes, not corpus sensitivity estimates; see JSON for per-rule outcomes.

## Limits
- Synthetic templated English corpus only; not a real-world accuracy claim.
- Matching score weights were adjusted after observing this held-out split: these are reused development diagnostics, NOT untouched held-out validation. A fresh independent corpus is required.
- Offline extraction reads PDF text layers. Raster receipts require configured vision provider and conservatively abstain offline.
- Cold category and matching stage metrics use labeled normalized fields; they are not end-to-end OCR pipeline metrics.
- Warm category history is simulated accountant-approved dev labels strictly earlier than the evaluated document; no held-out labels enter history.
- Exact-eligible recall excludes compound, fee, partial and missing-document cases; these require reviewed settlement.
- Duplicate metric uses labeled normalized fields, not degraded extraction outputs.
- Offline provider cost is zero; live LLM cost and extraction target unverified without configured model run.
- Rule branch tests are in the test suite; no corpus-wide A01-A12 sensitivity claim or manual time-saving claim.
- Precision is undefined (null) if no auto matches; no result is silently converted into a pass.

## Failures
63 field/stage failures; 0 wrong automatic matches. Full detail in companion JSON.