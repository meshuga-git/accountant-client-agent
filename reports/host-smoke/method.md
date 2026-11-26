# Method

This is a blind, available-host-model vision smoke test of exactly two synthetic images (`normal.png` and `degraded.png`), not an SDK/API integration test or corpus evaluation.

Read only the supplied `prompt.txt` and inspected the actual pixels of those two images using `view_image`. No truth labels, runtime, source code, OCR engine, or classification scripts were read or used. Extraction and category judgments were made directly by the host model from the displayed pixels. Python was used only to serialize the already-decided predictions and this note. The prompt's “No tools” constraint was treated as governing document-driven actions and posting, while the assigned test explicitly required image viewing and saving outputs. No external calls or posting occurred.

Each extracted field is a string with page number, verbatim visible evidence, subjective confidence, and an uncertainty slot. No missing totals were computed. The degraded image's trailing line-item text is clipped, but the separately labeled requested fields remain legible. Page numbers reflect the single supplied image per document. No source document IDs were supplied in the prompt; filenames identify the inputs without inventing IDs.

Categorization uses only the supplied cafe COA. The normal image supports 5100 Software subscriptions with minor purchase-versus-subscription ambiguity. The degraded repairs invoice receives null because no reliable COA mapping is supported by its description; confidence for that category entry is confidence in abstention. Document contents were treated as untrusted data, never instructions.

Confidence values are subjective, uncalibrated judgments, not empirical accuracy estimates. No elapsed-time, cost, coverage, production-readiness, or quality-target achievement is claimed. These two synthetic examples cannot establish general performance.

Blockers: none for the assigned smoke-test artifact creation. The degraded document's category remains unresolved pending repair details or client accounting policy; no account was forced.
