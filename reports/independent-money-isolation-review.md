# Independent money and tenant-isolation review

Reviewed 2026-10-23. Scope: local, synthetic-only accounting prototype, not production financial software certification.

## Result

**15 independent regressions passed** using:

```sh
.venv/bin/python -m pytest -q tests/test_review_money_isolation.py
```

Verified behavior:
- Purchase sales tax is included in expense; no automatic NY input-VAT asset.
- Expense credit-note refund clears the same payable as its reversal; inconsistent credit-note direction cannot be approved after correction.
- Already consumed bank lines and ambiguous exact candidates do not auto-match.
- Duplicate-source documents do not receive automatic match suggestions.
- Foreign-currency validation requires a rate for the document's exact date.
- SQLite application reads/writes are tenant scoped; foreign writes are rejected.
- API credential grants determine accessible clients; request-supplied reviewer/client fields do not override the authenticated identity or tenant. Cross-tenant document, source and export routes are denied.
- Export requires confirmation and current approved version, is idempotent for the same journal/version, and rechecks period locks.
- Corrections revoke approval and old approved-history learning, and invalidate associated settlements.
- Partial settlements cannot reuse a bank transaction or exceed remaining document balances.
- Financial configuration changes revoke approvals and invalidate settlements.
- Manual vendor mappings survive end-to-end ingestion.

## Defects identified and fixed by implementers

Wrong refund clearing account, already-consumed bank auto-match, vendor-rule list/dict integration failure, duplicate-source suggestion eligibility, and credit-note direction defaults/correction consistency. Independent regressions cover these cases.

## Verification limit

**PostgreSQL RLS runtime: not verified in this environment.** Neither PostgreSQL server tools nor Docker are installed here. Static inspection found FORCE ROW LEVEL SECURITY, a non-owner/NOBYPASSRLS application role, tenant USING/WITH CHECK policy, and application refusal to operate as owner/superuser/BYPASSRLS. This is not a substitute for executing a cross-tenant SQL test under that role. SQLite tests demonstrate application scoping only, not database RLS.

No external financial actions were performed. Exports remain local mocks. No remaining blocker was found for the scoped local synthetic demonstration; production authentication, live posting, and production database validation remain outside this result.
