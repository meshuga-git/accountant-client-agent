# Three-minute review walkthrough (script, not a recorded video)

1. **0:00–0:30** Start API/UI, select the café. State “synthetic New York illustration, not tax advice.” Show document and attention counts.
2. **0:30–1:15** Open a normal invoice. Compare page evidence with vendor, date, net, tax and total. Select a reviewed supplies category, explain nonrecoverable purchase tax, save with a reason. Show the revision increment and balanced journal.
3. **1:15–1:45** Acknowledge source and new-vendor exception; approve this exact revision. Explicitly confirm LOCAL MOCK export and download CSV. Repeating the same export returns the same key.
4. **1:45–2:15** Show bank suggestions, missing-document draft text, and a partial/compound proposal. Explain explicit allocations, bank reuse prevention and no automatic posting.
5. **2:15–2:45** Change a field with a reason: approval is revoked. Show audit trail and source evidence unchanged. Lock the date and demonstrate approval/export rejection.
6. **2:45–3:00** Open reports. State that offline scans abstain, cold-start categories require review, revised matching results reuse a diagnostic split, and production/security work remains.

## Settlement API example

Use IDs and current versions returned from your own client's API; the values below are illustrative.

```json
{
  "bank_ids": ["bank-id"],
  "document_ids": ["document-id"],
  "allocations": {"document-id": "50.00"},
  "version_map": {"document-id": 2},
  "reason": "Reviewed partial payment against invoice and statement",
  "confirmed": true,
  "difference_account": null
}
```

This leaves the invoice's outstanding balance available for a second distinct bank line. For fees/rounding, specify a reviewed account in the client's COA; the difference is explicit in a balanced settlement journal. Do not mix currencies or income/refund clearing accounts. Settlement journals are not part of document mock CSV export.
