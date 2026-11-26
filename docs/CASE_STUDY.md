# Ledgerline: synthetic accounting case study

**Problem.** A fictional accountant needs reliable source-to-review workflows across a café, designer and construction business without leaking context between clients or posting an uncertain transaction.

**Build.** Ledgerline reuses the freight project's versioned document pipeline, then adds Decimal accounting rules, client-scoped categorization, precision-first matching, balanced journals and reviewed idempotent mock exports. A 412-document synthetic corpus spans 36 client-months, including degraded scans, duplicate copies, payment noise and adversarial pages. New York is the requested starting jurisdiction; sample tax allowlists are illustrative and never decide taxability.

**Evidence.** Automated tests exercise tenant grants, source access, malformed money, validation rules, refund direction, duplicate handling, partial allocations, concurrent reuse rejection, correction-driven learning invalidation, stale versions and period locks. Runtime smoke verifies API/Streamlit startup; PostgreSQL policy code is present but its optional real-database test is skipped in the build environment. See `reports/` for actual metrics, denominators and limits.

**Limitations matter.** Offline extraction is a labeled parser, not vision AI, and abstains on scans. Cold-start category accuracy is not met without a configured model. Matching changes followed diagnostic heldout feedback, so later scores are not independent validation. A tiny host-model smoke does not establish production provider performance or cost. No human baseline timing was measured; therefore **no percentage time-saving or return-on-investment claim is made**.

**Next acceptance gates.** Run a configured provider on fresh untouched documents under measured cost caps; evaluate screenshots and human review timing; execute PostgreSQL RLS tests on the actual target deployment; implement production identity, encrypted storage and retention; validate tax and FX policies with the accountant. Only after MVP gates pass should onboarding reminders, filing calendars and close packs be added.
