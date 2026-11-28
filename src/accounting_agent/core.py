"""Pure, conservative accounting rules. Decimal strings are the public money format.

Illustrative rules only: no tax advice, automatic posting, or automatic settlement
of partial/compound payments. Call validation again at approval and export.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from statistics import median

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    if isinstance(value, (float, bool)) or value is None:
        raise ValueError("Money requires a decimal string, integer, or Decimal")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid money") from exc
    try:
        if not result.is_finite() or result != result.quantize(CENT):
            raise ValueError("Money must be finite with at most two decimal places")
        return result.quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError("Money exceeds supported decimal precision") from exc


def iso_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _same_tenant(a, b):
    if not a.get("client_id") or a.get("client_id") != b.get("client_id"):
        raise ValueError("Cross-client or missing-client data refused")


def duplicate_candidates(doc, existing):
    result = []
    for other in existing:
        _same_tenant(doc, other)
        if doc.get("id") == other.get("id") and doc.get("id"):
            continue
        same_hash = bool(doc.get("hash")) and doc.get("hash") == other.get("hash")
        same_number = bool(norm(doc.get("invoice_number"))) and norm(
            doc.get("invoice_number")
        ) == norm(other.get("invoice_number"))
        vendor_similarity = SequenceMatcher(
            None, norm(doc.get("vendor")), norm(other.get("vendor"))
        ).ratio()
        try:
            same_total = money(doc.get("total")) == money(other.get("total"))
            close_date = (
                abs((iso_date(doc.get("date")) - iso_date(other.get("date"))).days) <= 2
            )
        except (ValueError, TypeError):
            same_total, close_date = False, False
        if same_hash or (
            same_number and same_total and close_date and vendor_similarity >= 0.85
        ):
            result.append(
                {
                    "document_id": other.get("id"),
                    "kind": "exact" if same_hash or vendor_similarity == 1 else "near",
                    "requires_review": True,
                }
            )
    return result


def assert_balanced(lines):
    if not lines:
        raise ValueError("Journal requires lines")
    debit = credit = ZERO
    for line in lines:
        d, c = money(line.get("debit", "0")), money(line.get("credit", "0"))
        if d < 0 or c < 0 or (d and c) or not line.get("account"):
            raise ValueError("Invalid journal line")
        debit += d
        credit += c
    if debit != credit or debit <= 0:
        raise ValueError("Journal is unbalanced or empty")
    return True


def draft_journal(doc, account, bank_account="2100", recoverable_tax=False):
    """Accrual expense draft (2100 payable), income receivable, or expense refund.

    Purchase sales tax is nonrecoverable gross expense by default (NY example).
    Recoverable tax must be explicitly authorized for the configured jurisdiction.
    The historical bank_account parameter is the balancing account, not proof of payment.
    """
    net, tax, total = (money(doc[k]) for k in ("net", "tax", "total"))
    if min(net, tax, total) < 0 or net + tax != total:
        raise ValueError("Invalid document totals")
    direction = doc.get("direction", "expense")
    if doc.get("type") == "credit_note" and direction != "credit_note":
        raise ValueError("Credit-note document must retain credit-note direction")
    if direction not in ("expense", "income", "credit_note"):
        raise ValueError("Unsupported document direction")
    if not doc.get("client_id") or not account:
        raise ValueError("Client and account required")
    reverse = direction in ("income", "credit_note")

    def line(code, value, debit):
        return {
            "account": code,
            "debit": str(value if debit else ZERO),
            "credit": str(ZERO if debit else value),
        }

    expense_value = net if recoverable_tax or direction == "income" else total
    lines = [line(account, expense_value, not reverse)] if expense_value else []
    if tax and (recoverable_tax or direction == "income"):
        lines.append(
            line("2200" if direction == "income" else "1400", tax, not reverse)
        )
    lines.append(
        line("1200" if direction == "income" else bank_account, total, reverse)
    )
    assert_balanced(lines)
    return {
        "client_id": doc["client_id"],
        "document_id": doc.get("id"),
        "date": doc["date"],
        "currency": doc["currency"],
        "status": "draft",
        "lines": lines,
        "requires_approval": True,
    }


def validate_document(doc, context=None):
    """All A01-A12 rules; context is caller-owned, scoped to the same client.

    Context: existing_documents, journal, today, lock_date, allowed_tax_rates,
    supported_currencies, base_currency, fx_rates {'YYYY-MM-DD:CCY': 'rate'},
    vendor_history amounts, known_vendors, category, bank, unmatched_bank_lines.
    """
    context = context or {}
    issues = []

    def add(rule, severity, message):
        issues.append({"rule_id": rule, "severity": severity, "message": message})

    today = iso_date(context.get("today", date.today()))
    existing = context.get("existing_documents", [])
    if duplicate_candidates(doc, existing) or context.get("duplicate"):
        add("A01", "Critical", "Potential duplicate: reviewer must resolve")
    try:
        net, tax, total = (money(doc[k]) for k in ("net", "tax", "total"))
        if net + tax != total or min(net, tax, total) < 0:
            raise ValueError("Totals disagree")
        items = doc.get("line_items", doc.get("lines", []))
        if (
            items
            and sum((money(i.get("net", i.get("amount"))) for i in items), ZERO) != net
        ):
            raise ValueError("Line net sum disagrees")
        if (
            items
            and all("tax" in i for i in items)
            and sum((money(i["tax"]) for i in items), ZERO) != tax
        ):
            raise ValueError("Line tax sum disagrees")
        breakdown = doc.get("tax_breakdown", [])
        if breakdown and all("amount" in row for row in breakdown):
            if sum((money(row["amount"]) for row in breakdown), ZERO) != tax:
                raise ValueError("Tax breakdown sum disagrees")
            for row in breakdown:
                if "taxable_amount" in row:
                    computed = (
                        money(row["taxable_amount"]) * Decimal(str(row["rate"]))
                    ).quantize(CENT, rounding=ROUND_HALF_UP)
                    if computed != money(row["amount"]):
                        raise ValueError("Tax breakdown calculation disagrees")
    except (KeyError, ValueError, TypeError, InvalidOperation):
        add(
            "A02",
            "Critical",
            "Missing/invalid amounts or net, tax, total and line sums disagree",
        )
    if "journal" in context:
        try:
            journal = context["journal"]
            if isinstance(journal, dict):
                _same_tenant(doc, journal)
                journal = journal["lines"]
            assert_balanced(journal)
        except (ValueError, KeyError, TypeError):
            add(
                "A03",
                "Critical",
                "Journal must balance with valid nonnegative debit/credit lines",
            )
    try:
        entry_date = iso_date(doc["date"])
        if entry_date > today or (
            context.get("lock_date") and entry_date <= iso_date(context["lock_date"])
        ):
            raise ValueError("Period not open")
    except (ValueError, KeyError, TypeError):
        add("A04", "Critical", "Invalid, future, or locked entry date")
    allowed = {
        Decimal(str(x))
        for x in context.get("allowed_tax_rates", ["0", "0.04", "0.08", "0.08875"])
    }
    rates = [r.get("rate") for r in doc.get("tax_breakdown", [])] or [
        doc.get("tax_rate")
    ]
    try:
        if any(r is None or Decimal(str(r)) not in allowed for r in rates):
            raise ValueError("Tax rate not configured")
    except (ValueError, InvalidOperation, TypeError):
        add(
            "A05",
            "Major",
            "Tax rate missing or outside illustrative jurisdiction allowed set",
        )
    currency = doc.get("currency")
    supported = context.get("supported_currencies", ["USD"])
    fx = context.get("fx_rates", {})
    valid_fx = True
    if currency != context.get("base_currency", "USD"):
        try:
            rate = Decimal(str(fx[f"{doc.get('date')}:{currency}"]))
            valid_fx = rate.is_finite() and rate > 0
        except (KeyError, InvalidOperation):
            valid_fx = False
    if currency not in supported or not valid_fx:
        add("A06", "Major", "Unsupported currency or missing positive dated FX rate")
    history = context.get("vendor_history", [])
    try:
        amounts = [money(x) for x in history]
        if amounts and money(doc["total"]) > median(amounts) * Decimal(
            str(context.get("anomaly_multiple", "3"))
        ):
            add("A07", "Major", "Amount exceeds configured vendor-history threshold")
    except (ValueError, KeyError, TypeError):
        add("A07", "Major", "Invalid vendor history or total; anomaly review required")
    if norm(doc.get("vendor")) not in {
        norm(v) for v in context.get("known_vendors", [])
    }:
        add("A08", "Major", "New vendor requires confirmation")
    mandatory = ["client_id", "vendor", "date", "total", "currency"]
    if doc.get("type", "invoice") == "invoice":
        mandatory.append("invoice_number")
    if any(doc.get(k) is None or doc.get(k) == "" for k in mandatory):
        add("A09", "Major", "Mandatory document fields missing")
    category = context.get("category", doc.get("category", {}))
    try:
        confidence = Decimal(str(category.get("confidence", 0)))
        if (
            not category.get("account")
            or not confidence.is_finite()
            or confidence < Decimal(str(context.get("category_threshold", ".85")))
            or category.get("conflicts_history")
        ):
            raise ValueError("Category uncertain")
    except (ValueError, InvalidOperation, AttributeError):
        add(
            "A10",
            "Major",
            "Category missing, uncertain, or conflicts with approved history",
        )
    for bank in context.get("unmatched_bank_lines", []):
        _same_tenant(doc, bank)
        if (today - iso_date(bank["date"])).days > 14:
            add(
                "A11",
                "Minor",
                f"Unmatched bank line {bank.get('id')} older than 14 days",
            )
    bank = context.get("bank")
    if bank:
        _same_tenant(doc, bank)
        try:
            if abs((iso_date(doc["date"]) - iso_date(bank["date"])).days) > 60:
                add("A12", "Minor", "Document and bank dates differ by over 60 days")
        except (KeyError, TypeError, ValueError):
            add("A12", "Minor", "Date distance could not be verified")
    return issues


def document_status(issues):
    severities = {i["severity"] for i in issues}
    return (
        "Blocked"
        if "Critical" in severities
        else "Needs review"
        if "Major" in severities
        else "Ready"
    )


def _direction(doc):
    return (
        "inflow"
        if doc.get("direction", "expense") in ("income", "credit_note")
        else "outflow"
    )


def _bank_direction(bank):
    amount = money(bank["amount"])
    return "inflow" if amount > 0 else "outflow"


def reconcile(bank, documents, days=5, threshold=Decimal(".95")):
    """Unique high-confidence exact match only; no FX conversion or tolerance auto-match.

    A second plausible exact candidate prevents auto-match regardless of ranking.
    Bank amounts are signed: negative outflow and positive inflow.
    """
    candidates = []
    if bank.get("matched"):
        return {
            "auto_match": None,
            "candidates": [],
            "candidate_count": 0,
            "requires_review": True,
        }
    for doc in documents:
        _same_tenant(bank, doc)
        if (
            doc.get("settled")
            or doc.get("duplicate")
            or doc.get("currency") != bank.get("currency")
            or _direction(doc) != _bank_direction(bank)
        ):
            continue
        amount = abs(money(bank["amount"]))
        if amount != money(doc["total"]) or amount == 0:
            continue
        delta = min(
            abs((iso_date(bank["date"]) - iso_date(doc[d])).days)
            for d in ("date", "due_date")
            if doc.get(d)
        )
        if delta > days:
            continue
        vendor, description = norm(doc.get("vendor")), norm(bank.get("description", ""))
        reference_text = norm(
            str(bank.get("description", "")) + " " + str(bank.get("reference", ""))
        )
        similarity = Decimal(str(SequenceMatcher(None, vendor, description).ratio()))
        if vendor and vendor in description:
            similarity = Decimal("1")
        reference = norm(doc.get("invoice_number"))
        hit = bool(reference) and f" {reference} " in f" {reference_text} "
        if similarity < Decimal(".65") and not hit:
            continue
        score = (
            Decimal(".70")
            + Decimal(".05") * (Decimal(1) - Decimal(delta) / Decimal(max(days, 1)))
            + Decimal(".20") * similarity
            + Decimal(".05") * int(hit)
        )
        candidates.append(
            {
                "document_id": doc["id"],
                "score": str(score.quantize(Decimal(".0001"))),
                "date_distance": delta,
                "reference_hit": hit,
                "method": "exact_deterministic",
            }
        )
    candidates.sort(key=lambda x: Decimal(x["score"]), reverse=True)
    auto = (
        candidates[0]
        if len(candidates) == 1 and Decimal(candidates[0]["score"]) >= threshold
        else None
    )
    return {
        "auto_match": auto,
        "candidates": candidates[:3],
        "candidate_count": len(candidates),
        "requires_review": auto is None,
    }


def settlement_proposal(bank_lines, documents, difference_account=None):
    """One/many matching proposal with explicit difference line; never auto approved.

    Partial payment records remaining payable, not a fictional fee. Caller must pass
    a difference account only for a reviewer-authorized fee/rounding adjustment.
    """
    if not bank_lines or not documents:
        raise ValueError("Bank lines and documents required")
    anchor = documents[0]
    for item in [*bank_lines, *documents]:
        _same_tenant(anchor, item)
        if item.get("currency") != anchor.get("currency"):
            raise ValueError("Cross-currency settlement requires reviewed FX journal")
    if len({x["id"] for x in bank_lines}) != len(bank_lines) or len(
        {x["id"] for x in documents}
    ) != len(documents):
        raise ValueError("Repeated settlement item")
    direction = _direction(anchor)
    if any(
        d.get("direction", "expense") != anchor.get("direction", "expense")
        for d in documents
    ):
        raise ValueError("Mixed document directions require separate clearing journals")
    if any(_direction(d) != direction or d.get("settled") for d in documents) or any(
        _bank_direction(b) != direction or b.get("matched") for b in bank_lines
    ):
        raise ValueError("Direction mismatch or already settled item")
    due = sum((money(d["total"]) for d in documents), ZERO)
    paid = sum((abs(money(b["amount"])) for b in bank_lines), ZERO)
    difference = paid - due
    allocated = due if difference_account else min(due, paid)
    if paid > due and not difference_account:
        raise ValueError("Overpayment requires explicit reviewed difference account")

    def line(account, debit, credit):
        return {"account": account, "debit": str(debit), "credit": str(credit)}

    if direction == "outflow":
        lines = [line("2100", allocated, ZERO), line("1000", ZERO, paid)]
    else:
        lines = [
            line("1000", paid, ZERO),
            line(
                "2100" if anchor.get("direction") == "credit_note" else "1200",
                ZERO,
                allocated,
            ),
        ]
    if difference_account and difference:
        debit_diff = difference > 0 if direction == "outflow" else difference < 0
        lines.append(
            line(
                difference_account,
                abs(difference) if debit_diff else ZERO,
                ZERO if debit_diff else abs(difference),
            )
        )
    assert_balanced(lines)
    return {
        "client_id": anchor["client_id"],
        "currency": anchor["currency"],
        "document_ids": [d["id"] for d in documents],
        "bank_ids": [b["id"] for b in bank_lines],
        "paid": str(paid),
        "due": str(due),
        "remaining": str(ZERO if difference_account else due - allocated),
        "difference": str(difference if difference_account else ZERO),
        "lines": lines,
        "status": "review_required",
        "requires_approval": True,
        "method": "compound"
        if len(documents) > 1
        else "partial"
        if paid < due and not difference_account
        else "settlement",
    }
