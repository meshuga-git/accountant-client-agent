"""Review-first workflow. All writes, locks, allocations and exports are atomic."""

import csv
import hashlib
import io
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

from .core import (
    assert_balanced,
    document_status,
    draft_journal,
    money,
    norm,
    reconcile,
    settlement_proposal,
    validate_document,
)
from .store import Store, encoded

COA = [
    {"code": "1000", "name": "Cash", "type": "asset"},
    {"code": "1200", "name": "Receivables", "type": "asset"},
    {"code": "2100", "name": "Payables", "type": "liability"},
    {"code": "2200", "name": "Sales tax payable", "type": "liability"},
    {"code": "4000", "name": "Sales revenue", "type": "income"},
    {"code": "5000", "name": "Materials and supplies", "type": "expense"},
    {"code": "5100", "name": "Software subscriptions", "type": "expense"},
    {"code": "5200", "name": "Rent and utilities", "type": "expense"},
    {"code": "5300", "name": "Professional services", "type": "expense"},
    {"code": "5400", "name": "Travel and transport", "type": "expense"},
    {"code": "5500", "name": "Bank fees and rounding", "type": "expense"},
    {"code": "5600", "name": "Meals and food supplies", "type": "expense"},
]
DEFAULT_CONFIG = {
    "base_currency": "USD",
    "supported_currencies": ["USD", "EUR"],
    "allowed_tax_rates": ["0", "0.04", "0.08", "0.08875"],
    "fx_rates": {},
    "tax_treatment": "nonrecoverable",
    "category_threshold": "0.85",
}
CLIENTS = {
    "cafe": "Harbor Cafe (fictional)",
    "designer": "Maple Design (fictional)",
    "construction": "Rivet Construction (fictional)",
}


class Workflow:
    def __init__(self, store=None, provider=None):
        self.store = store or Store()
        self.provider = provider

    def seed(self, clients=None):
        for cid, name in (clients or CLIENTS).items():
            with self.store.transaction(cid) as tx:
                try:
                    tx.get("client", cid)
                except KeyError:
                    tx.put(
                        "client",
                        cid,
                        {
                            "id": cid,
                            "client_id": cid,
                            "name": name,
                            "jurisdiction": "NYS_DEMO",
                            "lock_date": None,
                            "config": deepcopy(DEFAULT_CONFIG),
                            "coa": deepcopy(COA),
                        },
                    )
                    tx.audit("client_created", cid, "system", "Synthetic demo seed")

    def client(self, cid):
        with self.store.transaction(cid) as tx:
            return tx.get("client", cid)

    def list(self, cid, kind):
        with self.store.transaction(cid) as tx:
            return tx.list(kind)

    def get(self, cid, kind, identifier):
        with self.store.transaction(cid) as tx:
            return tx.get(kind, identifier)

    def _history(self, tx):
        # No detached learned cache: current approved revisions only. Corrections
        # immediately remove the old decision from subsequent model context.
        return [
            d
            for d in tx.list("document")
            if d.get("status") in ("approved", "exported")
            and d.get("approved_version") == d.get("version")
        ]

    def _refresh(self, tx, doc):
        client = tx.get("client", tx.client_id)
        history = self._history(tx)
        vendor_history = [
            x
            for x in history
            if norm(x.get("vendor")) == norm(doc.get("vendor")) and x["id"] != doc["id"]
        ]
        ctx = {
            **client["config"],
            "lock_date": client.get("lock_date"),
            "existing_documents": [
                x
                for x in tx.list("document")
                if x["id"] != doc["id"] and not x.get("excluded_duplicate")
            ],
            "known_vendors": [x.get("vendor") for x in history],
            "vendor_history": [x["total"] for x in vendor_history],
            "category": doc.get("category", {}),
        }
        account = doc.get("category", {}).get("account")
        coa = {x["code"] for x in client["coa"]}
        try:
            if doc.get("type") not in ("invoice", "receipt", "credit_note"):
                raise ValueError("Unsupported accounting document type")
            if account not in coa:
                raise ValueError("Category not in client chart of accounts")
            doc["journal"] = draft_journal(doc, account)
            ctx["journal"] = doc["journal"]
        except (ValueError, KeyError, TypeError):
            doc["journal"] = None
            ctx["journal"] = []
        try:
            issues = validate_document(doc, ctx)
        except (ValueError, TypeError, KeyError):
            issues = [
                {
                    "rule_id": "A09",
                    "severity": "Critical",
                    "message": "Invalid normalized fields; correct before approval",
                }
            ]
        # Extraction warning is not silently lost after category review.
        for issue in doc.get("extraction_issues", []):
            issues.append(
                {"rule_id": "E01", "severity": "Major", "message": str(issue)}
            )
        if doc.get("excluded_duplicate"):
            issues.append(
                {
                    "rule_id": "A01",
                    "severity": "Critical",
                    "message": "Document excluded as duplicate; never export",
                }
            )
        doc["issues"] = issues
        doc["review_status"] = document_status(issues)
        return doc

    def ingest(self, cid, values, extraction=None, source=None, reviewer="system"):
        from .extraction import categorize

        with self.store.transaction(cid) as tx:
            client = tx.get("client", cid)
            if values.get("client_id", cid) != cid:
                raise PermissionError("Cross-client ingestion")
            identifier = str(uuid.uuid4())
            doc = {
                **deepcopy(values),
                "id": identifier,
                "client_id": cid,
                "version": 1,
                "status": "draft",
                "approved_version": None,
                "trace_id": str(uuid.uuid4()),
                "source": source,
                "extraction": extraction or {},
                "extraction_issues": (extraction or {}).get("issues", []),
            }
            doc.setdefault("type", "invoice")
            doc.setdefault(
                "direction",
                "credit_note" if doc["type"] == "credit_note" else "expense",
            )
            if doc["type"] == "credit_note" and doc["direction"] != "credit_note":
                raise ValueError("Credit note requires reversal direction")
            doc["hash"] = (extraction or {}).get(
                "hash", hashlib.sha256(encoded(values).encode()).hexdigest()
            )
            history = [
                {**d, "account": d["category"]["account"], "approved": True}
                for d in self._history(tx)
            ]
            doc["category"] = categorize(
                cid,
                doc,
                client["coa"],
                {r["vendor"]: r["account"] for r in tx.list("vendor_rule")},
                history,
                provider=self.provider,
            )
            self._refresh(tx, doc)
            tx.put("document", identifier, doc)
            tx.audit(
                "ingest",
                identifier,
                reviewer,
                "Document extracted; no posting",
                after={
                    "version": 1,
                    "hash": doc["hash"],
                    "backend": (extraction or {}).get("backend"),
                },
            )
            return doc

    def correct(self, cid, identifier, changes, version, reviewer, reason):
        self._reason(reviewer, reason)
        allowed = {
            "vendor",
            "invoice_number",
            "date",
            "due_date",
            "currency",
            "net",
            "tax",
            "total",
            "tax_rate",
            "tax_breakdown",
            "line_items",
            "type",
            "direction",
            "category",
            "excluded_duplicate",
        }
        if set(changes) - allowed:
            raise ValueError("Unsupported correction fields")
        with self.store.transaction(cid) as tx:
            doc = tx.get("document", identifier)
            self._version(doc, version)
            before = deepcopy(doc)
            doc.update(deepcopy(changes))
            doc["version"] += 1
            doc["approved_version"] = None
            doc["status"] = "draft"
            doc.pop("approval", None)
            doc.pop("export_key", None)
            # Source extraction evidence is immutable, reviewer correction recorded
            # separately; do not claim the new values were extracted/grounded.
            doc["reviewer_corrected"] = True
            for settlement in tx.list("settlement"):
                if (
                    identifier in settlement["document_ids"]
                    and settlement["status"] == "approved"
                ):
                    settlement["status"] = "invalidated"
                    settlement["reason"] = "Source corrected; re-review settlement"
                    tx.put("settlement", settlement["id"], settlement)
            self._refresh(tx, doc)
            tx.put("document", identifier, doc)
            tx.audit("correct", identifier, reviewer, reason, before, doc)
            return doc

    def approve(
        self, cid, identifier, version, reviewer, reason, acknowledge_issues=False
    ):
        self._reason(reviewer, reason)
        with self.store.transaction(cid) as tx:
            doc = tx.get("document", identifier)
            self._version(doc, version)
            self._refresh(tx, doc)
            if any(i["severity"] == "Critical" for i in doc["issues"]):
                raise ValueError(
                    "Critical exceptions block approval; correct the source draft"
                )
            if (
                any(i["severity"] == "Major" for i in doc["issues"])
                and not acknowledge_issues
            ):
                raise ValueError(
                    "Explicit acknowledgement of all major issues required"
                )
            # Missing dated FX is not a discretionary tax/rationale override.
            if any(i["rule_id"] == "A06" for i in doc["issues"]):
                raise ValueError(
                    "Configure supported currency and dated FX before approval"
                )
            assert_balanced(doc["journal"]["lines"])
            doc["approved_version"] = version
            doc["status"] = "approved"
            doc["approval"] = {
                "reviewer": reviewer,
                "reason": reason,
                "issues": deepcopy(doc["issues"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            tx.put("document", identifier, doc)
            tx.audit(
                "approve",
                identifier,
                reviewer,
                reason,
                after={"version": version, "issues": doc["issues"]},
            )
            return doc

    def configure(self, cid, changes, reviewer, reason):
        self._reason(reviewer, reason)
        if set(changes) - {"lock_date", "config", "coa"}:
            raise ValueError("Unsupported client settings")
        if changes.get("lock_date"):
            date.fromisoformat(changes["lock_date"])
        with self.store.transaction(cid) as tx:
            client = tx.get("client", cid)
            old = deepcopy(client)
            if "config" in changes:
                client["config"] = {**client["config"], **changes["config"]}
            if "coa" in changes:
                if not changes["coa"] or len(
                    {x["code"] for x in changes["coa"]}
                ) != len(changes["coa"]):
                    raise ValueError("Unique nonempty COA required")
                client["coa"] = changes["coa"]
            if "lock_date" in changes:
                client["lock_date"] = changes["lock_date"]
            tx.put("client", cid, client)
            if "config" in changes or "coa" in changes:
                for doc in tx.list("document"):
                    doc["version"] += 1
                    doc["approved_version"] = None
                    doc["status"] = "draft"
                    doc.pop("approval", None)
                    doc.pop("export_key", None)
                    self._refresh(tx, doc)
                    tx.put("document", doc["id"], doc)
                for settlement in tx.list("settlement"):
                    if settlement["status"] == "approved":
                        settlement["status"] = "invalidated"
                        settlement["reason"] = "Financial configuration changed"
                        tx.put("settlement", settlement["id"], settlement)
            tx.audit("configure", cid, reviewer, reason, old, client)
            return client

    def vendor_rule(self, cid, vendor, account, reviewer, reason):
        self._reason(reviewer, reason)
        with self.store.transaction(cid) as tx:
            if account not in {x["code"] for x in tx.get("client", cid)["coa"]}:
                raise ValueError("Account outside client COA")
            identifier = hashlib.sha256(norm(vendor).encode()).hexdigest()
            value = {
                "id": identifier,
                "client_id": cid,
                "vendor": vendor,
                "match": vendor,
                "account": account,
                "source": "manual",
                "approved": True,
            }
            tx.put("vendor_rule", identifier, value)
            tx.audit("manual_vendor_rule", identifier, reviewer, reason, after=value)
            return value

    def import_bank(self, cid, rows, reviewer="system"):
        with self.store.transaction(cid) as tx:
            tx.get("client", cid)
            for row in rows:
                if row.get("client_id") != cid:
                    raise PermissionError("Cross-client bank import")
                money(row["amount"])
                date.fromisoformat(row["date"])
                try:
                    old = tx.get("bank", row["id"])
                    if old != row:
                        raise ValueError("Bank ID conflict")
                except KeyError:
                    tx.put("bank", row["id"], row)
            tx.audit(
                "bank_import",
                cid,
                reviewer,
                "Synthetic bank import",
                after={"rows": len(rows)},
            )
        return len(rows)

    def matches(self, cid):
        with self.store.transaction(cid) as tx:
            docs = tx.list("document")
            settlements = [
                s for s in tx.list("settlement") if s["status"] == "approved"
            ]
            used = {b for s in settlements for b in s["bank_ids"]}
            allocations = {}
            for s in settlements:
                for d, a in s["allocations"].items():
                    allocations[d] = allocations.get(d, Decimal(0)) + money(a)
            eligible = []
            for d in docs:
                if (
                    d.get("excluded_duplicate")
                    or not d.get("total")
                    or any(i["severity"] == "Critical" for i in d.get("issues", []))
                ):
                    continue
                try:
                    d = deepcopy(d)
                    d["total"] = str(
                        money(d["total"]) - allocations.get(d["id"], Decimal(0))
                    )
                    d["settled"] = money(d["total"]) <= 0
                    eligible.append(d)
                except ValueError:
                    pass
            results = []
            for bank in tx.list("bank"):
                if bank["id"] in used:
                    continue
                try:
                    result = reconcile(bank, eligible)
                except (ValueError, TypeError, KeyError):
                    result = {
                        "auto_match": None,
                        "candidates": [],
                        "requires_review": True,
                    }
                age = (date.today() - date.fromisoformat(bank["date"])).days
                results.append(
                    {
                        "bank": bank,
                        **result,
                        "missing_document_request": f"Please provide documentation for bank transaction {bank['id']} dated {bank['date']}."
                        if age > 14 and not result.get("auto_match")
                        else None,
                    }
                )
            return results

    def settle(
        self,
        cid,
        bank_ids,
        document_ids,
        allocations,
        version_map,
        reviewer,
        reason,
        difference_account=None,
        confirmed=False,
    ):
        self._reason(reviewer, reason)
        if not confirmed:
            raise ValueError("Explicit settlement confirmation required")
        with self.store.transaction(cid) as tx:
            existing = [s for s in tx.list("settlement") if s["status"] == "approved"]
            if set(bank_ids) & {b for s in existing for b in s["bank_ids"]}:
                raise ValueError("Bank transaction already allocated")
            docs = [tx.get("document", i) for i in document_ids]
            banks = [tx.get("bank", i) for i in bank_ids]
            if set(allocations) != set(document_ids):
                raise ValueError("Explicit allocation per document required")
            client = tx.get("client", cid)
            if difference_account and difference_account not in {
                c["code"] for c in client["coa"]
            }:
                raise ValueError("Difference account outside chart")
            for bank in banks:
                if bank["date"] > date.today().isoformat() or (
                    client.get("lock_date") and bank["date"] <= client["lock_date"]
                ):
                    raise ValueError("Settlement date future or locked")
            for doc in docs:
                self._version(doc, version_map.get(doc["id"]))
                self._refresh(tx, doc)
                if doc.get("approved_version") != doc["version"] or any(
                    x["severity"] == "Critical" for x in doc["issues"]
                ):
                    raise ValueError(
                        "Every source must be current approved/open document"
                    )
                prior = sum(
                    (money(s["allocations"].get(doc["id"], "0")) for s in existing),
                    Decimal(0),
                )
                amount = money(allocations[doc["id"]])
                if amount <= 0 or amount + prior > money(doc["total"]):
                    raise ValueError("Allocation exceeds outstanding document balance")
                doc["total"] = str(amount)
            proposal = settlement_proposal(banks, docs, difference_account)
            if money(proposal["remaining"]) != 0:
                raise ValueError(
                    "Allocations must equal payment; leave outstanding amount on document instead"
                )
            identifier = str(uuid.uuid4())
            proposal.update(
                {
                    "id": identifier,
                    "client_id": cid,
                    "status": "approved",
                    "allocations": allocations,
                    "versions": version_map,
                    "reviewer": reviewer,
                    "reason": reason,
                    "date": max(b["date"] for b in banks),
                }
            )
            tx.put("settlement", identifier, proposal)
            tx.audit(
                "settlement_approved", identifier, reviewer, reason, after=proposal
            )
            return proposal

    def export(self, cid, identifier, version, reviewer, reason, confirmed=False):
        self._reason(reviewer, reason)
        if not confirmed:
            raise ValueError("Explicit mock export confirmation required")
        with self.store.transaction(cid) as tx:
            doc = tx.get("document", identifier)
            self._version(doc, version)
            if doc.get("approved_version") != version or doc.get("status") not in (
                "approved",
                "exported",
            ):
                raise ValueError("Current revision must be approved")
            self._refresh(tx, doc)
            if any(
                x["severity"] == "Critical" or x["rule_id"] == "A06"
                for x in doc["issues"]
            ):
                raise ValueError("Export blocked by revalidation or period lock")
            assert_balanced(doc["journal"]["lines"])
            key = hashlib.sha256(
                encoded(
                    {
                        "client": cid,
                        "document": identifier,
                        "version": version,
                        "journal": doc["journal"],
                    }
                ).encode()
            ).hexdigest()
            try:
                return tx.get("export", key)
            except KeyError:
                pass
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(
                [
                    "client_id",
                    "document_id",
                    "version",
                    "date",
                    "currency",
                    "account",
                    "debit",
                    "credit",
                ]
            )

            def safe(v):
                s = str(v)
                return "'" + s if s.startswith(("=", "+", "-", "@", "\t", "\r")) else s

            for line in doc["journal"]["lines"]:
                writer.writerow(
                    [
                        safe(v)
                        for v in [
                            cid,
                            identifier,
                            version,
                            doc["date"],
                            doc["currency"],
                            line["account"],
                            line["debit"],
                            line["credit"],
                        ]
                    ]
                )
            result = {
                "id": key,
                "client_id": cid,
                "idempotency_key": key,
                "document_id": identifier,
                "version": version,
                "target": "LOCAL MOCK ONLY",
                "status": "accepted_mock",
                "csv": out.getvalue(),
                "journal": doc["journal"],
            }
            tx.put("export", key, result)
            doc["status"] = "exported"
            doc["export_key"] = key
            tx.put("document", identifier, doc)
            tx.audit(
                "mock_export",
                identifier,
                reviewer,
                reason,
                after={"key": key, "version": version},
            )
            return result

    @staticmethod
    def _reason(reviewer, reason):
        if not reviewer or not str(reason).strip():
            raise ValueError("Reviewer and nonempty reason required")

    @staticmethod
    def _version(doc, version):
        if doc["version"] != version:
            raise ValueError("Stale revision; reload and review latest version")
